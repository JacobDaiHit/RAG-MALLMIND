"""Session state for multi-turn shopping, topic memory, and cart actions."""
from __future__ import annotations

import copy
import json
import logging
import re
import os
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol

from rag.recommendation.product_loader import ProductCatalog
from rag.recommendation.session_context import merge_requirement_memory, record_turn, requirement_to_delta


DEFAULT_SESSION_TTL_SECONDS = 7200
DEFAULT_MAX_IN_MEMORY_SESSIONS = 500
SESSION_CLEANUP_INTERVAL_SECONDS = 60
SESSION_KEY_PREFIX = "mallmind:session:"
SCHEMA_VERSION = 2  # incremented on breaking session shape changes

logger = logging.getLogger(__name__)


def _now_seconds() -> float:
    return time.time()


@dataclass
class CartItem:
    product_id: str
    quantity: int = 1


# ── 分层子状态（Phase 4: 架构治理） ──────────────────────────────────────
# 这些 dataclass 是 ShoppingSession 的结构化视图，方便按职责读写。
# 它们与 ShoppingSession 的扁平字段保持同步（通过 to_/from_ 方法）。


@dataclass
class ConversationState:
    """对话级状态：消息历史、查询窗口、话题标签。"""
    session_id: str = ""
    messages: List[str] = field(default_factory=list)
    recent_queries: List[Dict[str, Any]] = field(default_factory=list)
    chat_topic: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RecommendationState:
    """推荐级状态：累积路由参数、最近一次目标和结果。"""
    current: Dict[str, Any] = field(default_factory=dict)
    last_goal: str = ""
    last_result: Any = field(default_factory=dict)
    last_requirement: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CartState:
    """购物车级状态。"""
    cart: Dict[str, CartItem] = field(default_factory=dict)
    pending_cart_action: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cart": {k: asdict(v) for k, v in self.cart.items()},
            "pending_cart_action": self.pending_cart_action,
        }


@dataclass
class PCBuildState:
    """PC 构建级状态。"""
    pc_build_history: List[Dict[str, Any]] = field(default_factory=list)
    current_pc_build: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ObservabilityState:
    """可观测级状态。"""
    topic_memory: Dict[str, Any] = field(default_factory=dict)
    topic_history: List[Dict[str, Any]] = field(default_factory=list)
    llm_call_log: List[Dict[str, Any]] = field(default_factory=list)
    last_fact_check_status: str = "passed"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ShoppingSession:
    session_id: str
    updated_at: float = field(default_factory=_now_seconds)
    messages: List[str] = field(default_factory=list)
    last_goal: str = ""
    last_result: Any = field(default_factory=dict)
    pc_build_history: List[Dict[str, Any]] = field(default_factory=list)
    cart: Dict[str, CartItem] = field(default_factory=dict)
    topic_memory: Dict[str, Any] = field(default_factory=dict)
    tool_history: List[Dict[str, Any]] = field(default_factory=list)
    last_requirement: Dict[str, Any] = field(default_factory=dict)
    recent_turns: List[Dict[str, Any]] = field(default_factory=list)
    recent_turns_summary: str = ""
    failure_state: Dict[str, Any] = field(default_factory=dict)
    # ── 新版统一 session 字段 ──
    chat_topic: str = ""
    topic_history: List[Dict[str, Any]] = field(default_factory=list)
    recent_queries: List[Dict[str, Any]] = field(default_factory=list)
    current: Dict[str, Any] = field(default_factory=dict)
    current_pc_build: Dict[str, Any] = field(default_factory=dict)
    # ── 链路改造字段 ──
    pending_cart_action: Dict[str, Any] = field(default_factory=dict)
    last_fact_check_status: str = "passed"
    llm_call_log: List[Dict[str, Any]] = field(default_factory=list)
    # ── Phase 4: schema versioning ──
    schema_version: int = SCHEMA_VERSION

    # ── 分层状态视图方法 ──

    def conversation_state(self) -> ConversationState:
        """Extract a ConversationState view."""
        return ConversationState(
            session_id=self.session_id,
            messages=list(self.messages),
            recent_queries=list(self.recent_queries),
            chat_topic=self.chat_topic,
        )

    def recommendation_state(self) -> RecommendationState:
        """Extract a RecommendationState view."""
        return RecommendationState(
            current=dict(self.current or {}),
            last_goal=self.last_goal,
            last_result=self.last_result,
            last_requirement=dict(self.last_requirement or {}),
        )

    def cart_state(self) -> CartState:
        """Extract a CartState view."""
        return CartState(
            cart=dict(self.cart),
            pending_cart_action=dict(self.pending_cart_action or {}),
        )

    def pc_build_state(self) -> PCBuildState:
        """Extract a PCBuildState view."""
        return PCBuildState(
            pc_build_history=list(self.pc_build_history),
            current_pc_build=dict(self.current_pc_build or {}),
        )

    def observability_state(self) -> ObservabilityState:
        """Extract an ObservabilityState view."""
        return ObservabilityState(
            topic_memory=dict(self.topic_memory or {}),
            topic_history=list(self.topic_history),
            llm_call_log=list(self.llm_call_log),
            last_fact_check_status=self.last_fact_check_status,
        )

    def snapshot(self) -> Dict[str, Any]:
        """Return an immutable snapshot of the session for async ops and logging."""
        try:
            return copy.deepcopy(session_to_dict(self))
        except Exception:
            return {"session_id": self.session_id, "schema_version": self.schema_version}


class BaseSessionStore(Protocol):
    def get(self, session_id: str) -> Optional[ShoppingSession]:
        ...

    def save(self, session: ShoppingSession) -> None:
        ...

    def delete(self, session_id: str) -> None:
        ...

    def cleanup(self, *, ttl_seconds: int, max_sessions: int, now: Optional[float] = None) -> int:
        ...


SessionStore = BaseSessionStore


class InMemorySessionStore(SessionStore):
    def __init__(self, sessions: Optional[Dict[str, ShoppingSession]] = None) -> None:
        self.sessions = sessions if sessions is not None else {}
        self._lock = threading.RLock()

    def get(self, session_id: str) -> Optional[ShoppingSession]:
        with self._lock:
            return self.sessions.get(session_id)

    def save(self, session: ShoppingSession) -> None:
        with self._lock:
            self.sessions[session.session_id] = session

    def delete(self, session_id: str) -> None:
        with self._lock:
            self.sessions.pop(session_id, None)

    def cleanup(self, *, ttl_seconds: int, max_sessions: int, now: Optional[float] = None) -> int:
        with self._lock:
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
        if removed:
            logger.info("Cleaned up %s expired or overflow in-memory sessions", removed)
        return removed


class RedisSessionStore(SessionStore):
    def __init__(self, redis_url: str, *, ttl_seconds: int) -> None:
        import redis

        self.redis_url = redis_url
        self.ttl_seconds = ttl_seconds
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)
        self._client.ping()

    def get(self, session_id: str) -> Optional[ShoppingSession]:
        key = self._key(session_id)
        payload = self._client.get(key)
        if not payload:
            return None
        try:
            data = json.loads(payload)
            return session_from_dict(data)
        except Exception as exc:
            logger.warning("Session deserialization failed for %s: %s", session_id, exc)
            self.delete(session_id)
            return None

    def save(self, session: ShoppingSession) -> None:
        ttl = _session_ttl_seconds()
        payload = json.dumps(session_to_dict(session), ensure_ascii=False, separators=(",", ":"))
        if ttl > 0:
            self._client.setex(self._key(session.session_id), ttl, payload)
        else:
            self._client.set(self._key(session.session_id), payload)

    def delete(self, session_id: str) -> None:
        self._client.delete(self._key(session_id))

    def cleanup(self, *, ttl_seconds: int, max_sessions: int, now: Optional[float] = None) -> int:
        return 0

    def _key(self, session_id: str) -> str:
        return f"{SESSION_KEY_PREFIX}{session_id}"


_SESSIONS: Dict[str, ShoppingSession] = {}
_SESSION_STORE = InMemorySessionStore(_SESSIONS)
_SESSION_STORE_CONFIG = ("memory", "", DEFAULT_SESSION_TTL_SECONDS)
_LAST_SESSION_CLEANUP_AT = 0.0


def get_session(session_id: Optional[str]) -> ShoppingSession:
    _maybe_cleanup_sessions()
    key = _normalize_session_id(session_id)
    store = get_session_store()
    session = store.get(key)
    if session is None:
        session = ShoppingSession(session_id=key, topic_memory=default_topic_memory())
    if not session.topic_memory:
        session.topic_memory = default_topic_memory()
    session.updated_at = _now_seconds()
    save_session(session)
    if isinstance(store, InMemorySessionStore) and len(_SESSIONS) > _session_max_count():
        cleanup_expired_sessions(now=session.updated_at)
    return session


def save_session(session: ShoppingSession) -> None:
    session.updated_at = _now_seconds()
    get_session_store().save(session)


def cleanup_expired_sessions(now: Optional[float] = None) -> int:
    removed = get_session_store().cleanup(
        ttl_seconds=_session_ttl_seconds(),
        max_sessions=_session_max_count(),
        now=now,
    )
    if removed:
        logger.info("Session cleanup removed %s sessions", removed)
    return removed


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


def _session_ttl_seconds() -> int:
    return _int_env("SESSION_TTL_SECONDS", DEFAULT_SESSION_TTL_SECONDS)


def _session_max_count() -> int:
    return _int_env("SESSION_MAX_COUNT", _int_env("MAX_IN_MEMORY_SESSIONS", DEFAULT_MAX_IN_MEMORY_SESSIONS))


def _app_env() -> str:
    return os.getenv("APP_ENV", os.getenv("ENV", "development")).strip().lower()


def _is_production_env() -> bool:
    return _app_env() in {"production", "prod"}


def _normalize_session_id(session_id: Optional[str]) -> str:
    key = str(session_id or "").strip()
    if key:
        return key
    if _is_production_env():
        logger.error("Missing session_id in production environment")
        raise ValueError("session_id is required in production; refusing to use shared default session")
    return "default"


def _session_backend() -> str:
    backend = os.getenv("SESSION_BACKEND")
    if backend:
        return backend.strip().lower()
    return "redis" if _is_production_env() and os.getenv("REDIS_URL") else "memory"


def get_session_store() -> SessionStore:
    global _SESSION_STORE, _SESSION_STORE_CONFIG

    backend = _session_backend()
    redis_url = os.getenv("REDIS_URL", "").strip()
    ttl = _session_ttl_seconds()
    config = (backend, redis_url, ttl)
    if config == _SESSION_STORE_CONFIG:
        return _SESSION_STORE

    if backend not in {"memory", "redis"}:
        logger.error("Session backend initialization failed for %s: unsupported backend", backend)
        raise RuntimeError(f"Unsupported SESSION_BACKEND: {backend}")

    try:
        if backend == "memory":
            _SESSION_STORE = InMemorySessionStore(_SESSIONS)
        else:
            if not redis_url:
                raise RuntimeError("SESSION_BACKEND=redis requires REDIS_URL")
            _SESSION_STORE = RedisSessionStore(redis_url, ttl_seconds=ttl)
    except Exception as exc:
        logger.error("Session backend initialization failed for %s: %s", backend, exc)
        if _is_production_env():
            raise
        logger.warning("Falling back to in-memory session store after backend initialization failure")
        _SESSION_STORE = InMemorySessionStore(_SESSIONS)
    _SESSION_STORE_CONFIG = config
    return _SESSION_STORE


def session_to_dict(session: ShoppingSession) -> Dict[str, Any]:
    return asdict(session)


def session_from_dict(data: Any) -> ShoppingSession:
    if not isinstance(data, dict):
        raise ValueError("session payload must be a JSON object")

    known_fields = {item.name for item in fields(ShoppingSession)}
    kwargs = {key: value for key, value in data.items() if key in known_fields}
    kwargs.setdefault("session_id", "")
    if not str(kwargs["session_id"]).strip():
        raise ValueError("session payload is missing session_id")

    cart_payload = kwargs.get("cart") or {}
    cart: Dict[str, CartItem] = {}
    if isinstance(cart_payload, dict):
        for product_id, item in cart_payload.items():
            try:
                if isinstance(item, CartItem):
                    cart[str(product_id)] = item
                elif isinstance(item, dict):
                    cart[str(product_id)] = CartItem(
                        product_id=str(item.get("product_id") or product_id),
                        quantity=max(int(item.get("quantity", 1)), 1),
                    )
            except (TypeError, ValueError):
                continue
    kwargs["cart"] = cart

    for key in ("messages", "pc_build_history", "tool_history", "recent_turns"):
        if not isinstance(kwargs.get(key), list):
            kwargs[key] = []
    for key in ("topic_memory", "last_requirement", "failure_state"):
        if not isinstance(kwargs.get(key), dict):
            kwargs[key] = default_topic_memory() if key == "topic_memory" else {}
    if not isinstance(kwargs.get("recent_turns_summary"), str):
        kwargs["recent_turns_summary"] = ""
    if kwargs.get("last_result") is None:
        kwargs["last_result"] = {}
    # ── 新版统一 session 字段 ──
    if not isinstance(kwargs.get("chat_topic"), str):
        kwargs["chat_topic"] = ""
    for key in ("topic_history", "recent_queries"):
        if not isinstance(kwargs.get(key), list):
            kwargs[key] = []
    if not isinstance(kwargs.get("current"), dict):
        kwargs["current"] = {}
    if not isinstance(kwargs.get("current_pc_build"), dict):
        kwargs["current_pc_build"] = {}
    # ── 新增字段向后兼容 ──
    if not isinstance(kwargs.get("pending_cart_action"), dict):
        kwargs["pending_cart_action"] = {}
    if not isinstance(kwargs.get("last_fact_check_status"), str):
        kwargs["last_fact_check_status"] = "passed"
    if not isinstance(kwargs.get("llm_call_log"), list):
        kwargs["llm_call_log"] = []
    # ── schema_version: default to current if missing ──
    if not isinstance(kwargs.get("schema_version"), int):
        kwargs["schema_version"] = SCHEMA_VERSION
    try:
        kwargs["updated_at"] = float(kwargs.get("updated_at") or _now_seconds())
    except (TypeError, ValueError):
        kwargs["updated_at"] = _now_seconds()
    return ShoppingSession(**kwargs)


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
        "source": "init",
        "reason": "",
        "updated_at": "",
        "history": [],
    }


def current_topic_json(session: ShoppingSession) -> Dict[str, Any]:
    if not session.topic_memory:
        session.topic_memory = default_topic_memory()
        save_session(session)
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
        "source": tool_call.get("source") or "rules",
        "reason": tool_call.get("reason") or "",
        "updated_at": now,
        "history": history,
    }
    save_session(session)
    return current_topic_json(session)


def remember_tool_call(session: ShoppingSession, tool_call: Dict[str, Any], *, result_status: str = "ok") -> None:
    entry = {
        "name": tool_call.get("name"),
        "arguments": tool_call.get("arguments") or {},
        "source": tool_call.get("source"),
        "reason": tool_call.get("reason"),
        "routing_trace": tool_call.get("routing_trace") or {},
        "result_status": result_status,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    session.tool_history.append(entry)
    del session.tool_history[:-12]
    save_session(session)


def update_session_from_router(session: ShoppingSession, message: str, tool_call: Dict[str, Any]) -> None:
    """Update unified session JSON after LLM router returns.

    Updates current (accumulated state), recent_queries (sliding window of 5),
    topic_history, and chat_topic.  Handles brand conflict resolution.
    """

    args = dict(tool_call.get("arguments") or {})
    tool_name = str(tool_call.get("name") or "")

    # ── chat_topic ──
    if tool_name == "general_chat":
        session.chat_topic = "chat"
    elif tool_name in {"recommend_shopping_products", "generate_pc_build_plan", "compare_products", "apply_cart_instruction"}:
        session.chat_topic = "recommendation"
    else:
        session.chat_topic = "off_topic"

    # ── recent_queries: sliding window of 5 ──
    session.recent_queries.append({
        "turn": len(session.recent_queries) + 1,
        "query": str(message or "").strip(),
    })
    if len(session.recent_queries) > 5:
        session.recent_queries = session.recent_queries[-5:]

    # ── current: merge router arguments into accumulated state ──
    prev = dict(session.current or {})
    new_current = {
        "tool_call": tool_name,
        "query": str(args.get("query") or message or "").strip(),
        "category": str(args.get("category") or prev.get("category") or "").strip(),
        "catalog_scope": str(args.get("catalog_scope") or prev.get("catalog_scope") or "ecommerce").strip(),
    }

    # ── brands / exclude_brands ──
    # 区分"LLM 未输出"（保留累积值）和"LLM 输出空列表"（清空累积值）
    # 例如用户说"替代品"→ LLM 输出 brands=[] 表示不限品牌
    prev_brands = list(prev.get("brands") or [])
    prev_exclude = list(prev.get("exclude_brands") or [])

    if "brands" in args:
        # LLM 明确输出了 brands 字段
        new_brands_raw = args.get("brands") or []
        new_brands = [str(b) for b in new_brands_raw if str(b).strip()]
        if new_brands:
            # 有新品牌 → 累积
            prev_brands = _dedupe([*prev_brands, *new_brands])
        else:
            # brands=[] → 清空累积值（不限品牌）
            prev_brands = []
    # else: LLM 未输出 brands → 保留累积值

    if "exclude_brands" in args:
        new_exclude_raw = args.get("exclude_brands") or []
        new_exclude = [str(b) for b in new_exclude_raw if str(b).strip()]
        if new_exclude:
            prev_exclude = _dedupe([*prev_exclude, *new_exclude])
        else:
            prev_exclude = []
    # else: LLM 未输出 exclude_brands → 保留累积值

    # 品牌冲突处理：brands 与 exclude_brands 同时存在时，exclude 优先移除
    if prev_brands and prev_exclude:
        prev_brands = [b for b in prev_brands if b not in set(prev_exclude)]

    new_current["brands"] = prev_brands
    new_current["exclude_brands"] = prev_exclude

    # remove from brands if now in exclude_brands
    if new_current["exclude_brands"]:
        new_current["brands"] = [b for b in new_current["brands"] if b not in set(new_current["exclude_brands"])]

    # price: new values override old; __CLEAR__ explicitly clears
    _CLEAR = "__CLEAR__"
    for key in ("price_min", "price_max", "budget"):
        val = args.get(key)
        if val == _CLEAR:
            # 显式清除：不继承历史值（等同于该约束不存在）
            pass
        elif val is not None:
            new_current[key] = val
        elif key not in new_current:
            new_current[key] = prev.get(key)

    # preferences: merge
    prefs = dict(prev.get("preferences") or {})
    prefs.update(args.get("preferences") or {})
    new_current["preferences"] = prefs

    # ── PC 配件场景：组件级约束每轮替换，不累积 ──
    # 用户每轮修改不同配件（显卡/机箱/散热），旧配件的 sub_category 和
    # must_have_terms 不应污染新配件的搜索。
    is_pc_part = new_current.get("catalog_scope") == "pc_parts"

    # must_have_terms: PC 配件场景替换，普通商品场景累积
    new_terms = [str(t) for t in (args.get("must_have_terms") or []) if str(t).strip()]
    if is_pc_part:
        new_current["must_have_terms"] = new_terms
    else:
        new_current["must_have_terms"] = _dedupe([*list(prev.get("must_have_terms") or []), *new_terms])

    # sub_category: PC 配件场景替换（不降级到累积值），普通商品场景降级到累积值
    sub_cat = str(args.get("sub_category") or "").strip()
    if is_pc_part:
        new_current["sub_category"] = sub_cat
    else:
        new_current["sub_category"] = sub_cat if sub_cat else str(prev.get("sub_category") or "").strip()

    # product_ids: 路由器优先，降级到累积值
    pids = [str(pid) for pid in (args.get("product_ids") or []) if str(pid).strip()]
    new_current["product_ids"] = pids if pids else list(prev.get("product_ids") or [])

    # ── 品牌切换检测：用户明确更换品牌时，清除品类级累积约束 ──
    # 旧的 sub_category 和 must_have_terms 可能是针对上一个品牌的，
    # 切换品牌后继续累积会导致过滤条件过严。
    new_brands = new_current.get("brands") or []
    prev_brands_raw = list(prev.get("brands") or [])
    if new_brands and prev_brands_raw and set(new_brands) != set(prev_brands_raw):
        if not is_pc_part:
            # 品牌发生了明确变化 → 清除 sub_category 和 must_have_terms
            new_current["sub_category"] = str(args.get("sub_category") or "").strip()
            new_current["must_have_terms"] = [
                str(t) for t in (args.get("must_have_terms") or []) if str(t).strip()
            ]

    session.current = new_current

    # ── topic_history ──
    session.topic_history.append({
        "turn": len(session.recent_queries),
        "tool_call": tool_name,
        "category": new_current.get("category", ""),
        "query": new_current.get("query", ""),
    })
    if len(session.topic_history) > 20:
        session.topic_history = session.topic_history[-20:]

    save_session(session)


def save_pc_build_to_session(session: ShoppingSession, plan: Dict[str, Any]) -> None:
    """Extract components from a PC build plan and store in session.current_pc_build."""

    parts = plan.get("parts") or plan.get("items") or []
    build: Dict[str, Any] = {
        "total_price": plan.get("total_price"),
        "budget": plan.get("budget"),
        "usage": plan.get("usage") or [],
        "preferences": plan.get("preferences") or {},
    }
    for part in parts:
        role = str(part.get("role") or part.get("component_type") or "")
        if not role:
            continue
        build[role] = {
            "product_id": str(part.get("product_id") or ""),
            "title": str(part.get("title") or part.get("name") or ""),
            "price": part.get("price"),
        }
    session.current_pc_build = build
    save_session(session)


def _dedupe(items: list) -> list:
    seen = set()
    result = []
    for item in items:
        key = str(item)
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def session_to_json(session: ShoppingSession) -> Dict[str, Any]:
    """Return the unified session JSON for debugging / SSE emission."""
    return {
        "session_id": session.session_id,
        "chat_topic": session.chat_topic,
        "topic_history": session.topic_history[-5:],
        "recent_queries": session.recent_queries,
        "current": session.current,
    }


def build_contextual_goal(session: ShoppingSession, message: str) -> str:
    clean = " ".join(str(message or "").split())
    if not session.last_goal:
        return clean
    if should_start_new_product_topic(session, clean):
        return clean
    if looks_like_followup(clean):
        # 只保留最后一轮的原始用户输入，丢弃累积的 "User added constraints:" 链，
        # 避免多轮追问导致 query 被历史约束淹没。
        base_goal = session.last_goal.split(". User added constraints:")[0].strip()
        return f"{base_goal}. User added constraints: {clean}"
    return clean


def should_start_new_product_topic(session: ShoppingSession, message: str) -> bool:
    topic = current_topic_json(session).get("topic_type")
    text = message or ""

    # 🟢 显式话题切换信号优先检测
    if _has_explicit_topic_switch(text):
        return True

    # 🟢 扩展: ecommerce_recommendation 场景下的切换检测
    if topic == "ecommerce_recommendation":
        last_category = session.current.get("category", "")
        new_category = _detect_category_from_message(text)
        if new_category and new_category != last_category:
            return True
        # 包含"不要了""算了""换个话题"等 → 切换
        return False

    # 原有 PC 场景逻辑
    if topic not in {"pc_build", "single_pc_part"}:
        return False

    if topic == "pc_build" and "显示器" in text and any(term in text for term in ["再加", "加一个", "总预算", "主机", "2K", "4K"]):
        return False
    if topic == "pc_build" and any(term in text for term in ["保留显卡", "其他配件", "压低"]):
        return False
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


# ── 🟢 新增: 显式话题切换信号 ──

_EXPLICIT_SWITCH_SIGNALS = [
    "换个话题", "不要了", "算了", "不买了", "不看这个了",
    "帮我推荐", "推荐一下", "有什么推荐", "我想买",
]

_CATEGORY_SIGNALS = {
    "beauty": ["面霜", "精华", "护肤", "面膜", "防晒", "粉底", "眉笔", "口红", "化妆"],
    "digital": ["手机", "耳机", "电脑", "笔记本", "平板", "键盘", "鼠标", "显示器", "充电"],
    "clothing": ["衣服", "外套", "鞋", "裤", "裙", "帽", "包", "穿搭"],
    "food": ["饮料", "咖啡", "牛奶", "零食", "茶", "水", "食品", "糖"],
}


def _has_explicit_topic_switch(text: str) -> bool:
    """用户明确表达了切换意图."""
    # 短消息以这些开头 → 可能是新话题
    if any(text.startswith(s) for s in ["帮我", "我想", "推荐", "我要", "换个", "算了"]):
        return True
    return any(s in text for s in _EXPLICIT_SWITCH_SIGNALS)


def _detect_category_from_message(text: str) -> str:
    """从消息中检测品类，返回 ComponentCategory 值或空字符串."""
    for cat, signals in _CATEGORY_SIGNALS.items():
        if any(s in text for s in signals):
            return cat
    return ""


def remember_recommendation(session: ShoppingSession, goal: str, result_payload: Dict[str, Any]) -> None:
    session.last_goal = goal
    session.last_result = result_payload
    session.messages.append(goal)
    del session.messages[:-12]
    requirement = (result_payload or {}).get("requirement") or {}
    if requirement:
        merge_requirement_memory(session, requirement, goal)
    trace = (result_payload or {}).get("trace") or {}
    record_turn(
        session,
        role="user",
        content=goal,
        tool_name=((trace.get("tool_call") or {}).get("name") or ""),
        selected_runtime_mode=str(trace.get("selected_runtime_mode") or trace.get("selected_mode") or trace.get("runtime_mode") or ""),
        requirement_delta=requirement_to_delta(requirement),
        selected_product_ids=last_recommended_product_ids(session),
        failure_type=str(trace.get("no_match_reason") or trace.get("fallback_blocked_reason") or ""),
    )
    save_session(session)


def remember_pc_build_plan(session: ShoppingSession, goal: str, plan: Dict[str, Any]) -> None:
    remember_recommendation(session, goal, plan)
    session.pc_build_history.append(plan)
    del session.pc_build_history[:-6]
    save_session(session)


def has_last_pc_build_plan(session: ShoppingSession) -> bool:
    return (session.last_result or {}).get("type") == "pc_build_plan" or current_topic_json(session).get("topic_type") == "pc_build"


def get_previous_pc_build_plan(session: ShoppingSession, offset: int = 1) -> Optional[Dict[str, Any]]:
    """Return a previous PC build plan, where offset=1 means the last plan."""

    if offset <= 0 or len(session.pc_build_history) < offset:
        return None
    return session.pc_build_history[-offset]


def looks_like_followup(message: str) -> bool:
    # 🟢 收紧: 短消息不再一律视为 followup，只检测明确的追问模式
    text = message.strip()
    followup_patterns = [
        "还有", "另外", "再加", "顺便", "对了",
        "那", "这个", "这款",
        "能", "可以", "有没有", "能不能",
        "多", "少", "够",
    ]
    if any(text.startswith(p) for p in followup_patterns):
        return True
    if text.lower() in {"battery", "price", "budget", "color", "size", "weight", "续航", "价格", "预算", "颜色", "尺寸", "重量"}:
        return True
    # 极短消息（≤6字符）且不含新话题信号 → 很可能是追问
    if len(text) <= 6 and not _has_explicit_topic_switch(text):
        return True
    return False


def apply_cart_instruction(
    session: ShoppingSession,
    instruction: str,
    catalog: ProductCatalog,
    product_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    instruction = " ".join(str(instruction or "").split())
    action = infer_cart_action(instruction)
    index = extract_item_index(instruction)  # 🟣 v4: 所有操作均支持序数定位
    ids = resolve_cart_product_ids(session, instruction, action, product_ids=product_ids, index=index, catalog=catalog)
    quantity = extract_quantity(instruction) or 1
    changed: List[str] = []

    if action == "clear":
        session.cart.clear()
        changed.append("已清空购物车。")
    elif action == "remove":
        if index is not None and not ids:
            changed.append(f"购物车里没有第 {index + 1} 个商品，未删除任何商品。")
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

    save_session(session)
    record_turn(
        session,
        role="user",
        content=instruction,
        tool_name="apply_cart_instruction",
        cart_delta={"action": action, "product_ids": ids, "changed": changed},
        failure_type="" if changed else "cart_no_target",
    )
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
            "image_url": product.image_url,
        }
        items.append(payload)
    return {
        "items": items,
        "total_price": round(total, 2),
        "currency": "CNY",
        "count": sum(item["quantity"] for item in items),
    }


def infer_cart_action(instruction: str) -> str:
    if any(keyword in instruction for keyword in ["清空", "全部删除", "删光"]):
        return "clear"
    if any(keyword in instruction for keyword in ["删除", "删掉", "删了", "移除", "不要了"]):
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


def fuzzy_match_cart_item(instruction: str, cart_ids: List[str], catalog: ProductCatalog) -> Optional[str]:
    """🟣 v4: 从购物车中模糊匹配商品标题/品牌，返回最佳匹配的 product_id。"""
    if not cart_ids or not catalog:
        return None
    matched: List[tuple] = []
    for pid in cart_ids:
        product = catalog.get(pid)
        if not product:
            continue
        title = getattr(product, "title", "") or ""
        brand = getattr(product, "brand", "") or ""
        combined = f"{brand} {title}".strip()
        if not combined:
            continue
        # 分词：中文字符串 + ASCII 词，过滤 <2 字符的碎片
        keywords = set(re.findall(r'[\u4e00-\u9fff]{2,}|[A-Za-z]{2,}', combined))
        if not keywords:
            continue
        hits = sum(1 for kw in keywords if kw in instruction)
        if hits >= 1:
            matched.append((hits, pid))
    if matched:
        matched.sort(key=lambda x: x[0], reverse=True)
        return matched[0][1]
    return None


def resolve_cart_product_ids(
    session: ShoppingSession,
    instruction: str,
    action: str,
    *,
    product_ids: Optional[List[str]] = None,
    index: Optional[int] = None,
    catalog: Optional[ProductCatalog] = None,
) -> List[str]:
    # 1) 显式 product_id（请求参数或正则提取）
    explicit_ids = product_ids or extract_product_ids(instruction)
    if explicit_ids:
        return select_by_index(explicit_ids, index)

    cart_ids = list(session.cart.keys())

    # 2) 🟣 v4: 商品名称/品牌模糊匹配（从购物车中定位）
    if cart_ids and catalog is not None:
        fuzzy_hit = fuzzy_match_cart_item(instruction, cart_ids, catalog)
        if fuzzy_hit:
            return [fuzzy_hit]

    # 3) remove / set_quantity：优先在购物车内定位
    if action in ("remove", "set_quantity") and cart_ids:
        if index is not None:
            return [cart_ids[index]] if 0 <= index < len(cart_ids) else []
        if references_previous_item(instruction):
            return cart_ids[:1]
        # 兜底：购物车第一个商品
        return cart_ids[:1] if cart_ids else []

    # 4) add：从上次推荐结果中定位
    recommended_ids = last_recommended_product_ids(session)
    if index is not None:
        return select_by_index(recommended_ids, index)
    if references_previous_item(instruction):
        return recommended_ids[:1]
    return recommended_ids


def select_by_index(ids: List[str], index: Optional[int]) -> List[str]:
    if index is None:
        return ids
    return [ids[index]] if 0 <= index < len(ids) else []


def references_previous_item(instruction: str) -> bool:
    return any(term in instruction for term in ["刚才那款", "上一个", "上个", "这个", "这款", "第一款", "第一个", "第二款", "第二个", "第三款", "第三个"])


def extract_item_index(instruction: str) -> Optional[int]:
    text = instruction or ""
    patterns = [
        (r"(?:第\s*)?1\s*(?:个|款|号)", 0),
        (r"(?:第\s*)?2\s*(?:个|款|号)", 1),
        (r"(?:第\s*)?3\s*(?:个|款|号)", 2),
        (r"第一\s*(?:个|款|号)?", 0),
        (r"第二\s*(?:个|款|号)?", 1),
        (r"第三\s*(?:个|款|号)?", 2),
    ]
    for pattern, index in patterns:
        if re.search(pattern, text):
            return index
    return None


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
