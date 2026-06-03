"""In-memory demo session state for multi-turn shopping, topic memory, and cart actions."""
from __future__ import annotations

import re
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from rag.recommendation.product_loader import ProductCatalog


DEFAULT_SESSION_TTL_SECONDS = 7200
DEFAULT_MAX_IN_MEMORY_SESSIONS = 500
SESSION_CLEANUP_INTERVAL_SECONDS = 60


def _now_seconds() -> float:
    return time.time()


@dataclass
class CartItem:
    product_id: str
    quantity: int = 1


@dataclass
class ShoppingSession:
    session_id: str
    updated_at: float = field(default_factory=_now_seconds)
    messages: List[str] = field(default_factory=list)
    last_goal: str = ""
    last_result: Dict[str, Any] = field(default_factory=dict)
    pc_build_history: List[Dict[str, Any]] = field(default_factory=list)
    cart: Dict[str, CartItem] = field(default_factory=dict)
    topic_memory: Dict[str, Any] = field(default_factory=dict)
    tool_history: List[Dict[str, Any]] = field(default_factory=list)


class SessionStore:
    def get(self, session_id: str) -> Optional[ShoppingSession]:
        raise NotImplementedError

    def set(self, session: ShoppingSession) -> None:
        raise NotImplementedError

    def cleanup(self, *, ttl_seconds: int, max_sessions: int, now: Optional[float] = None) -> int:
        raise NotImplementedError


class InMemorySessionStore(SessionStore):
    def __init__(self, sessions: Optional[Dict[str, ShoppingSession]] = None) -> None:
        self.sessions = sessions if sessions is not None else {}

    def get(self, session_id: str) -> Optional[ShoppingSession]:
        return self.sessions.get(session_id)

    def set(self, session: ShoppingSession) -> None:
        self.sessions[session.session_id] = session

    def cleanup(self, *, ttl_seconds: int, max_sessions: int, now: Optional[float] = None) -> int:
        current = _now_seconds() if now is None else now
        removed = 0
        if ttl_seconds > 0:
            expired = [
                session_id
                for session_id, session in self.sessions.items()
                if current - float(getattr(session, "updated_at", 0.0) or 0.0) > ttl_seconds
            ]
            for session_id in expired:
                self.sessions.pop(session_id, None)
                removed += 1

        if max_sessions > 0 and len(self.sessions) > max_sessions:
            overflow = len(self.sessions) - max_sessions
            oldest = sorted(
                self.sessions.items(),
                key=lambda item: float(getattr(item[1], "updated_at", 0.0) or 0.0),
            )[:overflow]
            for session_id, _session in oldest:
                self.sessions.pop(session_id, None)
                removed += 1
        return removed


_SESSIONS: Dict[str, ShoppingSession] = {}
_SESSION_STORE = InMemorySessionStore(_SESSIONS)
_LAST_SESSION_CLEANUP_AT = 0.0


def get_session(session_id: Optional[str]) -> ShoppingSession:
    _maybe_cleanup_sessions()
    key = (session_id or "default").strip() or "default"
    session = _SESSION_STORE.get(key)
    if session is None:
        session = ShoppingSession(session_id=key, topic_memory=default_topic_memory())
        _SESSION_STORE.set(session)
    if not session.topic_memory:
        session.topic_memory = default_topic_memory()
    session.updated_at = _now_seconds()
    if len(_SESSIONS) > _int_env("MAX_IN_MEMORY_SESSIONS", DEFAULT_MAX_IN_MEMORY_SESSIONS):
        cleanup_expired_sessions(now=session.updated_at)
    return session


def cleanup_expired_sessions(now: Optional[float] = None) -> int:
    return _SESSION_STORE.cleanup(
        ttl_seconds=_int_env("SESSION_TTL_SECONDS", DEFAULT_SESSION_TTL_SECONDS),
        max_sessions=_int_env("MAX_IN_MEMORY_SESSIONS", DEFAULT_MAX_IN_MEMORY_SESSIONS),
        now=now,
    )


def _maybe_cleanup_sessions() -> None:
    global _LAST_SESSION_CLEANUP_AT
    now = _now_seconds()
    if now - _LAST_SESSION_CLEANUP_AT < SESSION_CLEANUP_INTERVAL_SECONDS:
        return
    cleanup_expired_sessions(now=now)
    _LAST_SESSION_CLEANUP_AT = now


def _int_env(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        return default
    return max(value, 0)


def default_topic_memory() -> Dict[str, Any]:
    return {
        "topic_id": "",
        "topic_type": "unknown",
        "subject": "",
        "route": "",
        "category": "",
        "slots": {
            "budget": None,
            "usage": [],
            "preferences": {},
            "product_ids": [],
        },
        "confidence": 0.0,
        "source": "init",
        "reason": "",
        "updated_at": "",
        "history": [],
    }


def current_topic_json(session: ShoppingSession) -> Dict[str, Any]:
    if not session.topic_memory:
        session.topic_memory = default_topic_memory()
    return dict(session.topic_memory)


def update_topic_memory(session: ShoppingSession, tool_call: Dict[str, Any], *, result_type: str = "") -> Dict[str, Any]:
    """Update the short-term topic JSON after a validated tool call."""

    previous = current_topic_json(session)
    arguments = dict(tool_call.get("arguments") or {})
    route = str(tool_call.get("name") or "")
    topic_type = _topic_type_for_tool(route, result_type, arguments)
    subject = _subject_for_tool(route, arguments, previous)
    slots = _merge_slots(previous.get("slots") or {}, arguments)
    now = datetime.now(timezone.utc).isoformat()
    history = list(previous.get("history") or [])
    if previous.get("topic_type") != "unknown" or previous.get("subject"):
        history.append(
            {
                "topic_type": previous.get("topic_type"),
                "subject": previous.get("subject"),
                "route": previous.get("route"),
                "slots": previous.get("slots") or {},
                "updated_at": previous.get("updated_at"),
            }
        )
    del history[:-8]
    session.topic_memory = {
        "topic_id": previous.get("topic_id") or f"{session.session_id}-{len(history) + 1}",
        "topic_type": topic_type,
        "subject": subject,
        "route": route,
        "category": arguments.get("category") or ("pc_build" if topic_type == "pc_build" else previous.get("category") or ""),
        "slots": slots,
        "confidence": float(tool_call.get("confidence") or 0.0),
        "source": tool_call.get("source") or "rules",
        "reason": tool_call.get("reason") or "",
        "updated_at": now,
        "history": history,
    }
    return current_topic_json(session)


def remember_tool_call(session: ShoppingSession, tool_call: Dict[str, Any], *, result_status: str = "ok") -> None:
    entry = {
        "name": tool_call.get("name"),
        "arguments": tool_call.get("arguments") or {},
        "confidence": tool_call.get("confidence"),
        "source": tool_call.get("source"),
        "reason": tool_call.get("reason"),
        "routing_trace": tool_call.get("routing_trace") or {},
        "result_status": result_status,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    session.tool_history.append(entry)
    del session.tool_history[:-12]


def build_contextual_goal(session: ShoppingSession, message: str) -> str:
    clean = " ".join(str(message or "").split())
    if not session.last_goal:
        return clean
    if should_start_new_product_topic(session, clean):
        return clean
    if looks_like_followup(clean):
        return f"{session.last_goal}. User added constraints: {clean}"
    return clean


def should_start_new_product_topic(session: ShoppingSession, message: str) -> bool:
    topic = current_topic_json(session).get("topic_type")
    if topic not in {"pc_build", "single_pc_part"}:
        return False
    text = message or ""
    product_terms = [
        "手机",
        "耳机",
        "护肤",
        "面膜",
        "咖啡",
        "零食",
        "衣服",
        "外套",
        "食品",
        "键盘",
        "鼠标",
        "显示器",
        "笔记本",
        "推荐个",
        "推荐一款",
    ]
    pc_build_terms = ["整机", "主机", "装机", "配置单", "配电脑", "配一台", "游戏主机"]
    return any(term in text for term in product_terms) and not any(term in text for term in pc_build_terms)


def remember_recommendation(session: ShoppingSession, goal: str, result_payload: Dict[str, Any]) -> None:
    session.last_goal = goal
    session.last_result = result_payload
    session.messages.append(goal)
    del session.messages[:-12]


def remember_pc_build_plan(session: ShoppingSession, goal: str, plan: Dict[str, Any]) -> None:
    remember_recommendation(session, goal, plan)
    session.pc_build_history.append(plan)
    del session.pc_build_history[:-6]


def has_last_pc_build_plan(session: ShoppingSession) -> bool:
    return (session.last_result or {}).get("type") == "pc_build_plan" or current_topic_json(session).get("topic_type") == "pc_build"


def get_previous_pc_build_plan(session: ShoppingSession, offset: int = 1) -> Optional[Dict[str, Any]]:
    """Return a previous PC build plan, where offset=1 means the last plan."""

    if offset <= 0 or len(session.pc_build_history) < offset:
        return None
    return session.pc_build_history[-offset]


def looks_like_followup(message: str) -> bool:
    if len(message) <= 12:
        return True
    return any(
        keyword in message
        for keyword in [
            "再",
            "换",
            "不要",
            "改成",
            "改为",
            "便宜",
            "贵",
            "第二个",
            "这个",
            "刚才",
            "加购",
            "加入购物车",
            "对比",
            "比较",
            "显卡",
            "机箱",
            "预算",
            "颜色",
            "色系",
            "黑色",
            "白色",
            "低噪",
            "静音",
            "降",
            "换",
            "预算",
            "显卡",
            "机箱",
        ]
    )


def apply_cart_instruction(
    session: ShoppingSession,
    instruction: str,
    catalog: ProductCatalog,
    product_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    instruction = " ".join(str(instruction or "").split())
    ids = product_ids or extract_product_ids(instruction) or last_recommended_product_ids(session)
    action = infer_cart_action(instruction)
    quantity = extract_quantity(instruction) or 1
    changed: List[str] = []

    if action == "clear":
        session.cart.clear()
        changed.append("已清空购物车。")
    elif action == "remove":
        for product_id in ids:
            if product_id in session.cart:
                product = catalog.get(product_id)
                title = product.title if product is not None else product_id
                session.cart.pop(product_id, None)
                changed.append(f"已从购物车移除 {title}。")
    elif action == "set_quantity":
        for product_id in ids:
            product = catalog.get(product_id)
            if product is None:
                continue
            session.cart[product_id] = CartItem(product_id=product_id, quantity=max(quantity, 1))
            changed.append(f"已将 {product.title} 的数量修改为 {max(quantity, 1)}。")
    else:
        for product_id in ids:
            product = catalog.get(product_id)
            if product is None:
                continue
            item = session.cart.get(product_id) or CartItem(product_id=product_id, quantity=0)
            item.quantity += max(quantity, 1)
            session.cart[product_id] = item
            changed.append(f"已将 {product.title} 加入购物车，数量 {max(quantity, 1)}。")

    return {
        "session_id": session.session_id,
        "action": action,
        "messages": changed or ["没有找到可操作的商品，请先推荐商品或指定 product_id。"],
        "cart": cart_snapshot(session, catalog),
    }


def cart_snapshot(session: ShoppingSession, catalog: ProductCatalog) -> Dict[str, Any]:
    items = []
    total = 0.0
    for item in session.cart.values():
        product = catalog.get(item.product_id)
        if product is None:
            continue
        price = product.min_price or product.base_price
        total += price * item.quantity
        payload = {
            "product_id": product.product_id,
            "title": product.title,
            "brand": product.brand,
            "price": price,
            "currency": product.currency,
            "quantity": item.quantity,
            "line_total": round(price * item.quantity, 2),
        }
        if not product.category.value.startswith("pc_"):
            payload["image_url"] = product.image_url
        items.append(payload)
    return {
        "items": items,
        "total_price": round(total, 2),
        "currency": "CNY",
        "count": sum(item["quantity"] for item in items),
    }


def infer_cart_action(instruction: str) -> str:
    if any(keyword in instruction for keyword in ["清空", "全部删除"]):
        return "clear"
    if any(keyword in instruction for keyword in ["删除", "移除", "不要了"]):
        return "remove"
    if any(keyword in instruction for keyword in ["数量", "改成", "改为", "修改"]):
        return "set_quantity"
    return "add"


def extract_quantity(instruction: str) -> Optional[int]:
    match = re.search(r"(?:数量|改成|改为|修改为|x|X)\s*(\d+)", instruction)
    if match:
        return int(match.group(1))
    return None


def extract_product_ids(text: str) -> List[str]:
    pattern = r"(?:p_(?:beauty|digital|clothes|food)_\d{3}|pc_[A-Za-z0-9_]+)"
    return re.findall(pattern, text)


def last_recommended_product_ids(session: ShoppingSession) -> List[str]:
    result = session.last_result or {}
    if result.get("type") == "pc_build_plan":
        return [item.get("product_id") for item in result.get("parts") or result.get("items") or [] if item.get("product_id")]

    plans = result.get("plans") or []
    selected_plan = plans[0] if plans else None
    if selected_plan:
        return [
            ((component.get("product") or {}).get("product_id") or "")
            for component in selected_plan.get("components") or []
            if (component.get("product") or {}).get("product_id")
        ]
    cards = result.get("product_cards") or []
    return [card.get("product_id") for card in cards[:3] if card.get("product_id")]


def _topic_type_for_tool(route: str, result_type: str, arguments: Optional[Dict[str, Any]] = None) -> str:
    arguments = arguments or {}
    if route == "generate_pc_build_plan" or result_type == "pc_build_plan":
        return "pc_build"
    if route == "recommend_shopping_products":
        return "single_pc_part" if arguments.get("catalog_scope") == "pc_parts" else "ecommerce_recommendation"
    if route == "compare_products":
        return "comparison"
    if route == "apply_cart_instruction":
        return "cart"
    if route == "general_chat":
        return "general_chat"
    return "unknown"


def _subject_for_tool(route: str, arguments: Dict[str, Any], previous: Dict[str, Any]) -> str:
    if route == "generate_pc_build_plan":
        return "PC整机方案"
    if route == "recommend_shopping_products":
        return str(arguments.get("category") or arguments.get("query") or previous.get("subject") or "商品推荐")
    if route == "compare_products":
        return "商品对比"
    if route == "apply_cart_instruction":
        return previous.get("subject") or "购物车"
    return previous.get("subject") or ""


def _merge_slots(previous: Dict[str, Any], arguments: Dict[str, Any]) -> Dict[str, Any]:
    slots = {
        "budget": previous.get("budget"),
        "usage": list(previous.get("usage") or []),
        "preferences": dict(previous.get("preferences") or {}),
        "product_ids": list(previous.get("product_ids") or []),
        "catalog_scope": previous.get("catalog_scope") or "ecommerce",
    }
    if arguments.get("budget") is not None:
        slots["budget"] = arguments.get("budget")
    usage = arguments.get("usage")
    if isinstance(usage, list):
        slots["usage"] = [str(item) for item in usage if str(item).strip()]
    elif usage:
        slots["usage"] = [str(usage)]
    preferences = arguments.get("preferences")
    if isinstance(preferences, dict):
        slots["preferences"].update(preferences)
    for key in ("color", "noise", "usage", "category"):
        if arguments.get(key):
            slots["preferences"][key] = arguments.get(key)
    product_ids = arguments.get("product_ids")
    if isinstance(product_ids, list):
        slots["product_ids"] = [str(item) for item in product_ids if str(item).strip()]
    if arguments.get("catalog_scope"):
        slots["catalog_scope"] = str(arguments.get("catalog_scope"))
    return slots
