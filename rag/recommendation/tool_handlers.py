import inspect
import logging
from typing import Any, Dict, Iterable, List, Optional

from rag.api.app_context import VALIDATION_VERSION, model_to_dict, validate_goal
from rag.api.sse import sse_event
from rag.recommendation import InvalidGoalError, recommend_shopping_products
from rag.recommendation.comparison import compare_products
from rag.recommendation.image_retrieval import retrieve_image_evidence
from rag.recommendation.input_preprocessor import preprocess_user_input
from rag.recommendation.recommendation_pipeline import fact_check_result  # 🟢 新增
from rag.recommendation.response_generator import generate_natural_response  # 🟢 新增
from rag.recommendation.pc_session_flow import build_pc_plan_for_message, format_pc_plan_comparison_text
from rag.recommendation.product_loader import load_catalog_for_scope, load_combined_product_catalog
from rag.recommendation.session_state import (
    apply_cart_instruction,
    current_topic_json,
    extract_item_index,
    extract_quantity,
    fuzzy_match_cart_item,
    infer_cart_action,
    last_recommended_product_ids,
    references_previous_item,
    remember_pc_build_plan,
    remember_recommendation,
    save_pc_build_to_session,
    save_session,
    update_topic_memory,
)
from rag.security.prompt_guard import defense_prefix, defense_suffix, wrap_user_input
from rag.utils.catalog_scope import normalize_catalog_scope
from rag.utils.runtime_errors import public_error, sanitize_result_for_response
from rag.recommendation.llm_client import LLMClientError, OpenAICompatibleChatClient


logger = logging.getLogger(__name__)


def product_cards_payload(cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the versioned product-card SSE payload.

    ``products`` remains as a temporary compatibility alias for older clients;
    new consumers must use ``cards``.
    """

    normalized = list(cards or [])
    return {
        "schema_version": "product_cards.v2",
        "cards": normalized,
        "products": normalized,
    }


# ── 🟢 新增: 购物车 v2（计划+确认模式） ──

_CART_CONFIRM_TTL_SECONDS = 60


def handle_cart_v2(session: Any, message: str, product_ids: List[str], tool_call: Dict[str, Any]) -> Iterable[str]:
    """🟣 v4: 购物车 v2 统一入口——解析操作类型后分流到子处理器。

    支持 add / remove / set_quantity / clear 四种操作，
    每种操作都走 plan → confirm 模式（clear 直接执行）。
    """
    args = dict(tool_call.get("arguments") or {})
    catalog = load_combined_product_catalog()
    action = _resolve_cart_action(args, message)

    if action == "clear":
        yield from _handle_cart_clear(session, catalog)
        return

    if action in ("remove", "set_quantity"):
        yield from _handle_cart_modify(session, message, catalog, action, product_ids, tool_call)
        return

    # 默认: add
    yield from _handle_cart_add(session, message, product_ids, catalog, tool_call)


# ── 🟣 v4: 内部子处理器 ──


def _resolve_cart_action(args: Dict[str, Any], message: str) -> str:
    """从 tool_call arguments 或用户消息中推断操作类型。"""
    # 优先用 router 传入的 operation 参数
    op = args.get("operation")
    if op and op in ("add", "remove", "set_quantity", "clear"):
        return op
    # 其次从消息文本推断
    return infer_cart_action(message)


def _match_recommended_by_name(
    message: str,
    recommended_ids: List[str],
    catalog: Any,
) -> Optional[str]:
    """Match a user message against recommended product titles and brands.

    Returns the product_id if exactly one recommended product's brand or title
    keywords appear in the message.  Returns None when zero or multiple products
    match (ambiguous — requires LLM disambiguation).
    """
    if not recommended_ids:
        return None

    msg_lower = message.lower()
    brand_hits: List[str] = []
    title_hits: List[str] = []

    for pid in recommended_ids:
        product = catalog.by_id.get(pid) if hasattr(catalog, "by_id") else None
        if product is None:
            continue

        brand = (getattr(product, "brand", None) or "").strip().lower()
        title = (getattr(product, "title", None) or "").strip().lower()

        # Brand match: the user mentioned the brand name
        if brand and brand in msg_lower:
            brand_hits.append(pid)
            continue

        # Title keyword match: only if no brand was matched
        if title and _title_keyword_hit(title, msg_lower):
            title_hits.append(pid)

    # Brand matches are more reliable than title keyword matches.
    # When any brand is matched, ignore ambiguous title hits to avoid
    # false positives from generic terms like "手机"/"耳机" appearing in
    # multiple product titles.
    if brand_hits:
        if len(brand_hits) == 1:
            return brand_hits[0]
        return None  # multiple brands matched → ambiguous

    # No brand matched — use title keyword hits.
    # If multiple products matched the same keyword (e.g. "平板" matching
    # two tablet models), return the first match rather than falling back
    # to recommended_ids[0] which could be an entirely different product type.
    if title_hits:
        return title_hits[0]
    return None


def _title_keyword_hit(title: str, msg_lower: str) -> bool:
    """Check if any meaningful keyword from the title appears in the message.

    Handles both space-separated (English) and unsegmented (Chinese) text.
    """
    # 1) Space-separated tokens (English titles like "OPPO Find X9 Ultra")
    tokens = [t for t in title.split() if len(t) >= 2]
    if any(token.lower() in msg_lower for token in tokens if len(token) >= 3):
        return True

    # 2) Character-window substrings for Chinese/unsegmented text.
    # Extract 2-5 char windows from compact regions (no spaces, no digits-only)
    compact = "".join(ch for ch in title if ch.isalpha() or "一" <= ch <= "鿿")
    if len(compact) >= 2:
        for window_len in (5, 4, 3, 2):
            for i in range(len(compact) - window_len + 1):
                sub = compact[i:i + window_len]
                if sub.lower() in msg_lower:
                    # Avoid matching trivial bigrams that happen to collide
                    if window_len >= 3 or not sub.isascii():
                        return True
    return False


def _llm_resolve_cart_product(
    session: Any,
    message: str,
    recommended_ids: List[str],
    catalog: Any,
) -> Optional[str]:
    """Use LLM to disambiguate which recommended product the user wants.

    Called when rule-based matching (_match_recommended_by_name) can't determine
    the target product.  The LLM receives the last recommendation's product
    titles and the user message, and returns the 1-based index of the target.
    """
    if len(recommended_ids) <= 1:
        return recommended_ids[0] if recommended_ids else None

    # Build product list for the LLM prompt
    product_lines: List[str] = []
    for i, pid in enumerate(recommended_ids, 1):
        product = catalog.by_id.get(pid) if hasattr(catalog, "by_id") else None
        title = getattr(product, "title", pid) if product else pid
        brand = getattr(product, "brand", "") if product else ""
        price = getattr(product, "base_price", "") if product else ""
        label = f"{title}"
        if brand:
            label = f"{brand} {label}"
        if price:
            label = f"{label} (¥{price})"
        product_lines.append(f"{i}. {label}")

    prompt = (
        f"{defense_prefix()}\n\n"
        f"用户上一轮看到了以下商品：\n"
        + "\n".join(product_lines)
        + f"\n\n{wrap_user_input(message, max_len=300)}\n\n"
        + "请返回用户想加入购物车的商品编号（1/2/3等）。\n"
        + "注意：如果品牌名同时匹配多个商品（如\"华为\"同时匹配手机和耳机），"
        + "且用户未指定类别，必须返回 0。\n"
        + "如果无法确定就返回 0，宁可追问也不要猜。\n"
        + "只输出数字。\n\n"
        + f"{defense_suffix()}"
    )

    try:
        from rag.recommendation.llm_client import OpenAICompatibleChatClient
        client = OpenAICompatibleChatClient()
        if not client.configured:
            return None

        payload, _report = client.chat_json_with_report(
            [
                {"role": "system", "content": f"{defense_prefix()}\n你是购物车助手，只输出数字。\n{defense_suffix()}"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=8,
        )
        # Extract the first integer from the response
        import re
        response_text = str(payload) if not isinstance(payload, str) else payload
        match = re.search(r"\d+", response_text)
        if match:
            index = int(match.group()) - 1  # 1-based → 0-based
            if 0 <= index < len(recommended_ids):
                return recommended_ids[index]
    except Exception:
        pass  # LLM unavailable — fall through to [0] default

    return None


def _resolve_product_for_cart(
    session: Any,
    message: str,
    product_ids: List[str],
    args: Dict[str, Any],
    catalog: Any,
    action: str,
) -> str:
    """🟣 v4: 通用产品 ID 解析——add 从推荐结果取，remove/set_quantity 从购物车取。"""
    # 1) 显式 product_ids
    if product_ids:
        return product_ids[0]
    arg_ids = args.get("product_ids")
    if isinstance(arg_ids, list) and arg_ids:
        return str(arg_ids[0])

    cart_ids = list(session.cart.keys())

    # 2) 名称模糊匹配（从购物车中定位）
    if cart_ids:
        fuzzy_hit = fuzzy_match_cart_item(message, cart_ids, catalog)
        if fuzzy_hit:
            return fuzzy_hit

    # 3) remove/set_quantity: 优先在购物车内定位
    if action in ("remove", "set_quantity") and cart_ids:
        index = extract_item_index(message)
        if index is not None and 0 <= index < len(cart_ids):
            return cart_ids[index]
        if references_previous_item(message):
            return cart_ids[0]
        return cart_ids[0]  # 兜底：购物车第一个

    # 4) add: 从上次推荐结果中定位
    recommended_ids = last_recommended_product_ids(session)
    if recommended_ids:
        index = extract_item_index(message)
        if index is not None and 0 <= index < len(recommended_ids):
            return recommended_ids[index]
        if references_previous_item(message):
            return recommended_ids[0]
        # 4b) 品牌/产品名模糊匹配：用户提到了具体品牌或产品名
        name_match = _match_recommended_by_name(message, recommended_ids, catalog)
        if name_match:
            return name_match
        # 4c) LLM 消歧：规则无法确定时，让 LLM 从推荐列表中选出用户所指的商品
        llm_match = _llm_resolve_cart_product(session, message, recommended_ids, catalog)
        if llm_match:
            return llm_match
        return recommended_ids[0]

    return ""


def _build_confirmation_message(plan: Dict[str, Any], operation: str) -> str:
    """🟣 v4: 根据操作类型生成确认文案。"""
    title = plan.get("product_title", "")
    qty = plan.get("quantity", 1)
    price = plan.get("estimated_unit_price")

    if operation == "remove":
        return f"确认从购物车移除 {title}？"
    if operation == "set_quantity":
        return f"确认将 {title} 的数量修改为 {qty}？"
    # add
    price_hint = f"（预估 ¥{price * qty:.0f}）" if price else ""
    return f"确认将 {title} x{qty} 加入购物车{price_hint}？"


def _cart_item_list(session: Any, catalog: Any) -> List[Dict[str, Any]]:
    """🟣 v4: 构建购物车商品列表，供追问事件前端渲染。"""
    items = []
    for i, (pid, cart_item) in enumerate(session.cart.items()):
        product = catalog.get(pid) if catalog else None
        title = getattr(product, "title", pid) if product else pid
        price = getattr(product, "base_price", None) if product else None
        items.append({
            "index": i + 1,
            "product_id": pid,
            "title": title,
            "price": price,
            "quantity": getattr(cart_item, "quantity", 1),
        })
    return items


def _check_cart_ambiguity(
    session: Any, message: str, cart_ids: List[str], catalog: Any,
) -> Optional[str]:
    """🟣 v4: 检查购物车操作是否有歧义，返回追问文本或 None。

    检测场景：
    - 同品类多商品 + 品类模糊引用（"删掉那个手机"但有两个手机）
    - 序数越界（"删除第五个"但只有 3 个商品）
    """
    if not cart_ids or not catalog:
        return None

    # 序数越界检查
    index = extract_item_index(message)
    if index is not None and index >= len(cart_ids):
        items = _cart_item_list(session, catalog)
        names = "、".join(item["title"] for item in items)
        return f"购物车里只有 {len(cart_ids)} 个商品，没有第 {index + 1} 个。当前有：{names}，你要操作哪一个？"

    # 同品类歧义检查：仅当用户提到品类词但没指定名称/序数时触发
    if len(cart_ids) < 2:
        return None

    # 如果用户已指定了名称（模糊匹配能命中）或序数，则无歧义
    fuzzy_hit = fuzzy_match_cart_item(message, cart_ids, catalog)
    if fuzzy_hit:
        return None
    if index is not None:
        return None

    # 按品类分组
    category_groups: Dict[str, List[str]] = {}
    for pid in cart_ids:
        product = catalog.get(pid)
        if not product:
            continue
        cat = getattr(product, "sub_category", "") or getattr(product, "category", "") or ""
        if cat:
            category_groups.setdefault(cat, []).append(pid)

    # 检查是否有品类被模糊引用（消息包含品类关键词但没具体指定）
    for cat, group_ids in category_groups.items():
        if len(group_ids) >= 2 and cat and cat in message:
            names = "、".join(
                getattr(catalog.get(pid), "title", pid) for pid in group_ids if catalog.get(pid)
            )
            return f"购物车里有多个{cat}商品：{names}，你要操作哪一个？可以说名称或'第几个'。"

    return None


def _handle_cart_add(
    session: Any, message: str, product_ids: List[str], catalog: Any, tool_call: Dict[str, Any],
) -> Iterable[str]:
    """🟣 v4: 加入购物车——从推荐结果中定位商品。"""
    args = dict(tool_call.get("arguments") or {})
    plan_product_id = _resolve_product_for_cart(session, message, product_ids, args, catalog, "add")

    product = catalog.get(plan_product_id) if plan_product_id else None
    if not product and plan_product_id:
        yield sse_event("error", {"label": "商品不存在", "detail": f"product_id {plan_product_id} 不在商品库中。"})
        yield sse_event("done", {"session_id": session.session_id})
        return
    if not product:
        yield sse_event("error", {"label": "未找到商品", "detail": "没有找到可加入购物车的商品，请先推荐商品。"})
        yield sse_event("done", {"session_id": session.session_id})
        return

    product_title = getattr(product, "title", plan_product_id)
    unit_price = getattr(product, "base_price", None)
    raw_qty = args.get("quantity", 1)
    quantity = max(int(raw_qty) if raw_qty is not None else 1, 1)
    estimated_total = round(unit_price * quantity, 2) if unit_price is not None else None

    plan = _make_plan(plan_product_id, product_title, "add", quantity, unit_price, estimated_total)
    session.pending_cart_action = plan
    save_session(session)

    yield sse_event("cart_confirmation", {
        "plan": plan,
        "message": _build_confirmation_message(plan, "add"),
    })
    yield sse_event("done", {"session_id": session.session_id})


def _handle_cart_modify(
    session: Any, message: str, catalog: Any, action: str,
    product_ids: List[str], tool_call: Dict[str, Any],
) -> Iterable[str]:
    """🟣 v4: 删除/修改数量——从购物车中定位商品，含歧义追问。"""
    args = dict(tool_call.get("arguments") or {})
    cart_ids = list(session.cart.keys())

    if not cart_ids:
        yield sse_event("delta", {"text": "购物车是空的，没有可操作的商品。"})
        yield sse_event("done", {"session_id": session.session_id})
        return

    # 🟣 歧义追问：同品类多商品 + 模糊引用
    ambiguity = _check_cart_ambiguity(session, message, cart_ids, catalog)
    if ambiguity:
        yield sse_event("cart_clarification", {
            "text": ambiguity,
            "cart_items": _cart_item_list(session, catalog),
            "action": action,
        })
        yield sse_event("done", {"session_id": session.session_id})
        return

    plan_product_id = _resolve_product_for_cart(session, message, product_ids, args, catalog, action)

    product = catalog.get(plan_product_id) if plan_product_id else None
    if not product:
        yield sse_event("cart_clarification", {
            "text": "没找到要操作的商品。你可以说商品名称或'第几个'来指定。",
            "cart_items": _cart_item_list(session, catalog),
            "action": action,
        })
        yield sse_event("done", {"session_id": session.session_id})
        return

    product_title = getattr(product, "title", plan_product_id)
    unit_price = getattr(product, "base_price", None)

    if action == "set_quantity":
        raw_qty = extract_quantity(message) or args.get("quantity", 1)
        quantity = max(int(raw_qty) if raw_qty is not None else 1, 1)
    else:
        quantity = 1

    estimated_total = round(unit_price * quantity, 2) if unit_price is not None else None
    plan = _make_plan(plan_product_id, product_title, action, quantity, unit_price, estimated_total)
    session.pending_cart_action = plan
    save_session(session)

    yield sse_event("cart_confirmation", {
        "plan": plan,
        "message": _build_confirmation_message(plan, action),
    })
    yield sse_event("done", {"session_id": session.session_id})


def _handle_cart_clear(session: Any, catalog: Any) -> Iterable[str]:
    """🟣 v4: 清空购物车——直接执行，不走确认。"""
    count = len(session.cart)
    session.cart.clear()
    save_session(session)
    from rag.recommendation.session_state import cart_snapshot
    yield sse_event("delta", {"text": f"已清空购物车（移除了 {count} 件商品）。"})
    yield sse_event("cart", cart_snapshot(session, catalog))
    yield sse_event("done", {"session_id": session.session_id})


def _make_plan(
    product_id: str, product_title: str, operation: str,
    quantity: int, unit_price: Optional[float], estimated_total: Optional[float],
) -> Dict[str, Any]:
    """🟣 v4: 构建通用 CartActionPlan。"""
    now = _now_seconds()
    return {
        "operation": operation,
        "product_id": product_id,
        "product_title": product_title,
        "quantity": quantity,
        "estimated_unit_price": unit_price,
        "estimated_total": estimated_total,
        "created_at": now,
        "expires_at": now + _CART_CONFIRM_TTL_SECONDS,
    }


def _now_seconds() -> float:
    import time

    return time.time()


def handle_general_chat(session: Any, tool_call: Dict[str, Any]) -> Iterable[str]:
    """Handle general_chat: generate diverse responses via LLM with template fallback."""
    update_topic_memory(session, tool_call, result_type="general_chat")
    query = str((tool_call.get("arguments") or {}).get("query") or "")

    # ── LLM 生成多样化回复 ──
    # 通过 LLM 生成自然、多样的回复，避免所有 general_chat 返回相同模板。
    # 如果 LLM 不可用或失败，回退到模板回复。
    text = _generate_general_chat_llm_response(query)
    if not text:
        text = _generate_general_chat_fallback(query)

    yield sse_event("delta", {"text": text})
    yield sse_event("done", {"session_id": session.session_id})


def _generate_general_chat_llm_response(query: str) -> str:
    """Use LLM to generate a diverse general_chat response. Returns empty string on failure."""
    try:
        client = OpenAICompatibleChatClient()
        if not client.configured:
            return ""
        messages = [
            {
                "role": "system",
                "content": (
                    f"{defense_prefix()}\n\n"
                    "你是一个电商智能导购助手。用户问了一个与具体商品推荐无关的问题，请你用自然、友好、多样的方式回复。\n"
                    "回复规则：\n"
                    "1. 如果是问候（你好、hi、hello），友好回应并简短介绍自己的能力（搜索商品、推荐、对比、购物车）\n"
                    "2. 如果是身份问题（你是谁、你叫什么），介绍自己是智能导购助手\n"
                    "3. 如果是购物无关的问题（天气、写代码、新闻），委婉说明自己专注购物领域，并引导用户提出购物需求\n"
                    "4. 如果是感谢或告别，礼貌回应\n"
                    "5. 回复要简短（1-3句话），自然口语化，不要每次都一模一样\n"
                    "直接输出回复文本，不要加引号或前缀。\n\n"
                    f"{defense_suffix()}"
                ),
            },
            {"role": "user", "content": wrap_user_input(query, max_len=300)},
        ]
        result = client.chat_text(messages, temperature=0.7, max_tokens=200)
        result = result.strip().strip('"').strip("'")
        if len(result) > 5:
            return result
        return ""
    except (LLMClientError, Exception) as exc:
        logger.debug("general_chat LLM fallback failed: %s", exc)
        return ""


def _generate_general_chat_fallback(query: str) -> str:
    """Template-based fallback when LLM is unavailable."""
    lower = query.lower()
    if any(term in query for term in ("天气", "写一首诗", "排序算法", "国际局势")):
        return (
            "我是智能导购助手，主要帮你挑选商品、做商品对比和处理购物车。"
            "这个问题和购物无关，我就不展开了；如果你有购物需求，可以告诉我品类、预算和偏好。"
        )
    if query.strip().isdigit() or not any(ch.isalnum() for ch in query):
        return (
            "我是智能导购助手。请告诉我你想买什么商品、预算多少、有什么偏好，"
            "我可以帮你搜索、推荐、对比，或加入购物车。"
        )
    if any(term in lower for term in ("谢谢", "感谢", "thank", "辛苦了")):
        return "不客气！有需要随时找我，我可以帮你搜商品、做对比、处理购物车。"
    if any(term in lower for term in ("再见", "拜拜", "bye")):
        return "再见！购物有需要随时来找我。"
    return (
        "你好，我是智能导购助手，可以帮你搜索商品、推荐合适款式、对比商品、"
        "生成整机方案，也可以处理购物车。请告诉我你想买什么。"
    )


# ── 🟢 新增: 对比 v2（加入事实校验） ──

def handle_compare_v2(session: Any, product_ids: List[str], tool_call: Dict[str, Any]) -> Iterable[str]:
    """Compare products v2: with 3-level fallback chain + fact checks."""
    catalog = load_combined_product_catalog()
    fact_issues: List[Dict[str, Any]] = []
    arguments = tool_call.get("arguments") or {}
    fallback_source = "direct"  # router 直接传了 product_ids

    # ── 三级降级链 ──
    if not product_ids:
        # 降级 1：从 session 上一轮推荐结果中提取 product_ids
        product_ids = last_recommended_product_ids(session)
        if product_ids:
            fallback_source = "last_recommended"
            logger.info(
                "compare_v2: fell back to last_recommended_product_ids, count=%d, session=%s",
                len(product_ids), session.session_id,
            )

    if not product_ids:
        # 降级 2：用 query 关键词走推荐管线获取候选 ID
        query = str(arguments.get("query") or "").strip()
        if query:
            product_ids = comparison_candidate_ids(query, limit=3)
            if product_ids:
                fallback_source = "comparison_candidates"
                logger.info(
                    "compare_v2: fell back to comparison_candidate_ids, query=%r, count=%d",
                    query, len(product_ids),
                )

    if not product_ids:
        # 降级 3：PC 装机话题 → 对比最近两个方案
        topic = current_topic_json(session)
        pc_history = getattr(session, "pc_build_history", None) or []
        if topic.get("topic_type") == "pc_build" and len(pc_history) >= 2:
            yield from _emit_pc_build_comparison(session, tool_call)
            return

    # ── 校验所有 product_id 真实存在于 catalog ──
    valid_ids = []
    for pid in product_ids:
        if catalog.get(pid):
            valid_ids.append(pid)
        else:
            fact_issues.append({"product_id": pid, "issue": "not_found_in_catalog"})

    if not valid_ids:
        # 所有降级均失败 → 降级为推荐同类商品
        query = str(arguments.get("query") or arguments.get("category") or "").strip()
        if query:
            yield sse_event("delta", {
                "text": "商品库里暂时没有找到你要对比的具体型号，帮你搜了同类商品。"
            })
            try:
                result = recommend_shopping_products(
                    query,
                    use_llm=False,
                    use_llm_guidance=False,
                    catalog_scope="combined",
                    use_milvus_retrieval=False,
                    session=session,
                )
                payload = model_to_dict(result)
                cards = payload.get("product_cards") or []
                if cards:
                    yield sse_event("product_cards", product_cards_payload(cards))
                    response_text = generate_natural_response(payload, session, query)
                    for line in response_text:
                        yield sse_event("delta", {"text": line})
            except Exception:
                logger.warning("compare_v2: recommend fallback failed", exc_info=True)
                yield sse_event("error", {
                    "label": "对比失败",
                    "detail": "未能找到可对比的商品，请尝试指定具体型号。"
                })
        else:
            yield sse_event("error", {
                "label": "商品不存在",
                "detail": "所有待对比商品均未在商品库中找到。"
            })
        yield sse_event("done", {"session_id": session.session_id})
        return

    # 🟢 同品类检测
    categories = {}
    for pid in valid_ids:
        prod = catalog.get(pid)
        if prod:
            cat = getattr(prod, "sub_category", None) or getattr(prod, "category", None)
            categories[pid] = str(cat) if cat else "unknown"
    unique_cats = set(categories.values())
    if len(unique_cats) > 1:
        fact_issues.append({"issue": "cross_category_comparison", "categories": list(unique_cats)})

    # 🟢 价格区间检测
    prices = []
    for pid in valid_ids:
        prod = catalog.get(pid)
        if prod:
            p = getattr(prod, "base_price", None)
            if p is not None:
                prices.append(float(p))
    if len(prices) >= 2 and max(prices) > 0 and min(prices) / max(prices) < 0.2:
        fact_issues.append({"issue": "large_price_gap", "min": min(prices), "max": max(prices)})

    if fact_issues:
        yield sse_event("fact_check", {"passed": len(fact_issues) <= 1 and "cross_category_comparison" not in str(fact_issues), "issues": fact_issues})

    # 使用原始对比逻辑
    compare_result = compare_products(catalog, valid_ids)
    compare_result["fact_check_issues"] = fact_issues
    compare_result["missing_product_ids"] = [pid for pid in product_ids if pid not in valid_ids]

    update_topic_memory(session, tool_call, result_type="comparison")
    yield sse_event("intent_route", {"route": "comparison", "task_type": "compare_products_v2", "tool_call": tool_call})
    yield sse_event("comparison_table", {"rows": compare_result.get("rows") or []})
    yield sse_event("result", {
        "type": "comparison",
        "comparison": compare_result,
        "tool_call": tool_call,
        "fallback_source": fallback_source,
    })
    yield sse_event("done", {"session_id": session.session_id})


def _emit_pc_build_comparison(session: Any, tool_call: Dict[str, Any]) -> Iterable[str]:
    """Emit a comparison between the two most recent PC build plans."""
    pc_history = getattr(session, "pc_build_history", None) or []
    if len(pc_history) < 2:
        yield sse_event("error", {
            "label": "方案不足",
            "detail": "需要至少两个装机方案才能对比。"
        })
        yield sse_event("done", {"session_id": session.session_id})
        return

    current_plan = pc_history[-1]
    baseline_plan = pc_history[-2]
    baseline_label = baseline_plan.get("label") or "上一个方案"

    try:
        from rag.recommendation.pc_build import compare_pc_build_plans
        comparison = compare_pc_build_plans(current_plan, baseline_plan, baseline_label)
    except Exception:
        logger.warning("pc_build_comparison: compare_pc_build_plans failed", exc_info=True)
        yield sse_event("error", {
            "label": "对比失败",
            "detail": "PC 方案对比过程中出现错误。"
        })
        yield sse_event("done", {"session_id": session.session_id})
        return

    highlights = comparison.get("highlights") or []
    changes = comparison.get("changes") or []
    text_parts = list(highlights)
    for change in changes:
        role_name = change.get("role_name", "")
        from_title = change.get("from", "")
        to_title = change.get("to", "")
        reason = change.get("reason", "")
        text_parts.append(f"{role_name}：{from_title} → {to_title}。{reason}")

    yield sse_event("delta", {"text": "\n".join(text_parts)})
    yield sse_event("pc_comparison_table", {
        "comparison": comparison,
        "current_plan": current_plan.get("label", "当前方案"),
        "baseline_plan": baseline_label,
    })
    yield sse_event("done", {"session_id": session.session_id})


# ── 新增: 参数查询 / SKU 查询 / 价格比较 处理器 ──


def _resolve_product(catalog, product_mentions: List[str], session: Any):
    """Try to find a product from mentions or session last_result."""
    # 1. 从 product_mentions 匹配
    if product_mentions:
        for mention in product_mentions:
            mention_lower = mention.lower()
            for pid, product in catalog.by_id.items():
                if mention_lower in product.title.lower() or mention_lower in pid.lower():
                    return product
    # 2. 降级到 session.last_result 中最近推荐的商品
    last_ids = last_recommended_product_ids(session)
    if last_ids:
        first_id = last_ids[0]
        product = catalog.get(first_id)
        if product:
            return product
    return None


def handle_parameter_query(session: Any, tool_call: Dict[str, Any]) -> Iterable[str]:
    """Answer factual questions about specific product attributes (功耗、重量、尺寸等)."""
    arguments = tool_call.get("arguments") or {}
    product_mentions = arguments.get("product_mentions") or []
    attribute = str(arguments.get("attribute") or "").strip()

    catalog = load_combined_product_catalog()
    product = _resolve_product(catalog, product_mentions, session)

    if not product:
        yield sse_event("delta", {"text": "你想了解哪款商品的参数？可以告诉我具体型号。"})
        yield sse_event("done", {"session_id": session.session_id})
        return

    # 从产品数据中提取属性信息
    detail_parts = [f"「{product.title}」"]
    if product.brand:
        detail_parts.append(f"品牌：{product.brand}")

    # 尝试从 description 和 tags 中匹配属性
    desc = product.description or ""
    tags = " ".join(product.tags) if product.tags else ""
    all_text = f"{desc} {tags}"

    if attribute and attribute.lower() in all_text.lower():
        # 简单提取包含属性关键词的句子
        sentences = desc.replace("。", "。\n").split("\n")
        matched = [s.strip() for s in sentences if attribute in s]
        if matched:
            detail_parts.append(f"关于{attribute}：{'；'.join(matched[:2])}")
        else:
            detail_parts.append(f"关于{attribute}：商品描述中提到了相关信息，建议查看详情页。")
    elif attribute:
        detail_parts.append(f"关于{attribute}：商品库中暂未收录该参数的具体数据，建议查看商品详情页。")

    # 补充基础信息
    if product.base_price:
        detail_parts.append(f"参考价：¥{product.base_price}")

    yield sse_event("delta", {"text": "\n".join(detail_parts)})
    yield sse_event("product_cards", product_cards_payload([model_to_dict(product)]))
    yield sse_event("done", {"session_id": session.session_id})


def handle_sku_query(session: Any, tool_call: Dict[str, Any]) -> Iterable[str]:
    """Answer SKU-level queries: price differences between configurations."""
    arguments = tool_call.get("arguments") or {}
    product_mentions = arguments.get("product_mentions") or []
    sku_criteria = str(arguments.get("sku_criteria") or "").strip()

    catalog = load_combined_product_catalog()
    product = _resolve_product(catalog, product_mentions, session)

    if not product:
        yield sse_event("delta", {"text": "你想了解哪款商品的配置差异？可以告诉我具体型号。"})
        yield sse_event("done", {"session_id": session.session_id})
        return

    skus = product.skus or []
    if not skus:
        yield sse_event("delta", {"text": f"「{product.title}」暂时没有多种配置可选。"})
        yield sse_event("done", {"session_id": session.session_id})
        return

    # 如果有 sku_criteria，筛选匹配的 SKU
    matched_skus = []
    if sku_criteria:
        criteria_lower = sku_criteria.lower()
        for sku in skus:
            sku_text = " ".join(str(v) for v in sku.properties.values()).lower()
            if criteria_lower in sku_text:
                matched_skus.append(sku)

    display_skus = matched_skus if matched_skus else skus

    lines = [f"「{product.title}」的配置信息："]
    for sku in display_skus:
        props = " / ".join(str(v) for v in sku.properties.values())
        price = sku.price or product.base_price or 0
        lines.append(f"- {props}：¥{price}")

    if len(display_skus) >= 2:
        prices = [sku.price or product.base_price or 0 for sku in display_skus]
        diff = max(prices) - min(prices)
        if diff > 0:
            lines.append(f"\n最高配与最低配差价：¥{diff}")

    yield sse_event("delta", {"text": "\n".join(lines)})
    yield sse_event("done", {"session_id": session.session_id})


def handle_price_comparison(session: Any, tool_call: Dict[str, Any]) -> Iterable[str]:
    """Answer price comparison/confirmation queries."""
    arguments = tool_call.get("arguments") or {}
    product_mentions = arguments.get("product_mentions") or []

    catalog = load_combined_product_catalog()
    product = _resolve_product(catalog, product_mentions, session)

    if not product:
        yield sse_event("delta", {"text": "你想比价哪款商品？可以告诉我具体型号。"})
        yield sse_event("done", {"session_id": session.session_id})
        return

    lines = [f"「{product.title}」的价格信息："]
    if product.base_price:
        lines.append(f"参考价：¥{product.base_price}")
    if product.min_price and product.max_price and product.min_price != product.max_price:
        lines.append(f"价格区间：¥{product.min_price} ~ ¥{product.max_price}")

    skus = product.skus or []
    if skus:
        lines.append("\n各配置价格：")
        for sku in skus:
            props = " / ".join(str(v) for v in sku.properties.values())
            price = sku.price or product.base_price or 0
            lines.append(f"- {props}：¥{price}")

    lines.append("\n以上为商品库中的参考价格，实际价格请以购买页面为准。")

    yield sse_event("delta", {"text": "\n".join(lines)})
    yield sse_event("product_cards", product_cards_payload([model_to_dict(product)]))
    yield sse_event("done", {"session_id": session.session_id})


def comparison_candidate_ids(query: str, limit: int = 2) -> List[str]:
    try:
        result = recommend_shopping_products(
            query,
            use_llm=False,
            use_llm_guidance=False,
            catalog_scope="combined",
            use_milvus_retrieval=False,
        )
    except Exception:
        return []
    payload = model_to_dict(result)
    return [
        str(card.get("product_id"))
        for card in payload.get("product_cards") or []
        if card.get("product_id")
    ][:limit]


def handle_pc_build(session: Any, message: str, contextual_goal: str, tool_call: Dict[str, Any]) -> Iterable[str]:
    try:
        plan = build_pc_plan_for_message(message, session)
    except ValueError as exc:
        logger.warning("PC build plan validation failed: %s", exc)
        yield sse_event("validation_error", {"label": "PC 方案无法生成", "detail": public_error(exc)})
        yield sse_event("done", {"session_id": session.session_id})
        return

    if not plan.get("_transient_comparison"):
        remember_pc_build_plan(session, contextual_goal, plan)
        save_pc_build_to_session(session, plan)
    topic_memory = update_topic_memory(session, tool_call, result_type="pc_build_plan")
    plan["tool_call"] = tool_call
    plan["topic_memory"] = topic_memory
    yield sse_event(
        "intent_route",
        {
            "route": "pc_build_plan",
            "task_type": "pc_build_plan",
            "supported_now": True,
            "tool_call": tool_call,
            "topic_memory": topic_memory,
            "reason": "识别到电脑整机/装机方案需求，进入独立 PC 配置规划链路。",
        },
    )
    yield sse_event("delta", {"text": plan.get("summary", "已生成电脑整机方案。")})
    for reason in plan.get("recommendation_reasons") or []:
        yield sse_event("delta", {"text": f"推荐理由：{reason}"})
    if plan.get("comparison"):
        yield sse_event("delta", {"text": format_pc_plan_comparison_text(plan["comparison"])})
    yield sse_event("pc_build_plan", plan)
    yield sse_event("done", {"session_id": session.session_id})


def handle_recommend(
    session: Any,
    message: str,
    raw_attachments: List[Dict[str, Any]],
    contextual_goal: str,
    attachments: List[Dict[str, Any]],
    attachment_report: Dict[str, Any],
    llm_stream_enabled: bool,
    tool_call: Dict[str, Any],
    *,
    recommendation_fn=None,
    image_retrieval_fn=None,
    use_llm_guidance: bool = False,
    use_milvus_retrieval: bool = True,
    use_rag_query_expansion: bool = False,
    runtime_mode: str = "balanced",
) -> Iterable[str]:
    recommendation_fn = recommendation_fn or recommend_shopping_products
    image_retrieval_fn = image_retrieval_fn or retrieve_image_evidence
    catalog_scope = normalize_catalog_scope((tool_call.get("arguments") or {}).get("catalog_scope"))
    recommendation_domain = "single_pc_part" if catalog_scope == "pc_parts" else "ecommerce"
    try:
        validate_goal(contextual_goal, skip_keyword_check=True)
        yield sse_event("progress", {"label": "系统已开始检索", "detail": "正在连接本地商品库并准备结构化筛选。"})
        image_evidence = image_retrieval_fn(
            attachments=raw_attachments,
            catalog=load_catalog_for_scope(catalog_scope),
        )
        if image_evidence.status == "ok":
            yield sse_event(
                "progress",
                {
                    "label": "图片相似召回完成",
                    "detail": f"基于商品图片向量命中 {image_evidence.total_hits} 个相似商品候选。",
                },
            )
        result = call_recommendation_fn(
            recommendation_fn,
            contextual_goal,
            use_llm=llm_stream_enabled,
            image_retrieval_evidence=image_evidence,
            use_llm_guidance=use_llm_guidance,
            catalog_scope=catalog_scope,
            use_milvus_retrieval=use_milvus_retrieval,
            use_rag_query_expansion=use_rag_query_expansion,
            router_arguments=tool_call.get("arguments") or {},
            session=session,
        )
    except InvalidGoalError as exc:
        logger.warning("Recommendation goal validation failed: %s", exc)
        yield sse_event("validation_error", {"label": "需求无法识别", "detail": public_error(exc), "validation_version": VALIDATION_VERSION})
        yield sse_event("done", {"session_id": session.session_id})
        return
    except Exception as exc:
        logger.exception("Recommendation pipeline failed")
        yield sse_event("error", {"label": "推荐异常", "detail": public_error(exc)})
        yield sse_event("done", {"session_id": session.session_id})
        return

    result.trace["attachments"] = attachments
    result.trace["attachment_analysis"] = attachment_report
    result.trace["preprocessed_input"] = preprocess_user_input(message, attachments).to_trace()
    result.trace["stream_llm_enabled"] = llm_stream_enabled
    result.trace["stream_llm_reason"] = "configured_and_enabled" if llm_stream_enabled else "disabled_or_not_configured"
    result.trace["tool_call"] = tool_call
    result.trace["catalog_scope"] = catalog_scope
    result.trace["recommendation_domain"] = recommendation_domain
    result.trace["runtime_mode"] = runtime_mode
    result.trace["selected_runtime_mode"] = runtime_mode
    result.trace["llm_configured"] = llm_stream_enabled
    result.trace["llm_used_for_route"] = bool(((tool_call.get("routing_trace") or {}).get("llm") or {}).get("name"))
    result.trace["catalog_guard_result"] = result.trace.get("no_match_reason") or result.trace.get("fallback_blocked_reason") or "ok"
    result.trace["retrieval_used"] = bool((result.trace.get("retrieval") or {}).get("retrieved_chunk_count"))
    result.trace["milvus_used"] = bool((result.trace.get("milvus_retrieval") or {}).get("retrieval_backend") == "milvus")
    result.trace["candidate_count_before"] = result.trace.get("catalog_product_count")
    result.trace["candidate_count_after"] = len(result.product_cards or [])
    result.trace["selected_product_ids"] = [str(card.get("product_id")) for card in result.product_cards if card.get("product_id")]
    result.trace["llm_used_for_parse"] = bool(result.trace.get("llm_requirement_parse_used"))
    result.trace.setdefault("llm_used_for_explanation", False)
    result.trace["session_updated"] = True
    payload = model_to_dict(result)

    # 🟢 事实校验: 验证推荐结果中商品 ID / 价格 / 库存
    catalog = load_catalog_for_scope(catalog_scope)
    payload = fact_check_result(payload, catalog)
    session.last_fact_check_status = "passed" if payload.get("fact_check", {}).get("passed") else "fail"

    remember_recommendation(session, contextual_goal, payload)
    # ── PC 配件替换：当 catalog_scope=pc_parts 且有当前配置时，更新对应组件 ──
    if catalog_scope == "pc_parts" and session.current_pc_build:
        _apply_pc_component_update(session, payload, tool_call)
    topic_memory = update_topic_memory(session, tool_call, result_type=payload.get("type") or "")
    payload.setdefault("trace", {})["topic_memory"] = topic_memory
    response_payload = sanitize_result_for_response(payload)

    yield sse_event("intent_route", response_payload.get("intent_route") or {})
    for item in build_chat_progress_events(payload):
        yield sse_event("progress", item)
    # 🟢 使用响应生成器替代硬编码模板
    natural_lines = generate_natural_response(payload, session, message)
    for text in natural_lines:
        yield sse_event("delta", {"text": text})
    yield sse_event("product_cards", product_cards_payload(response_payload.get("product_cards") or []))
    yield sse_event("candidate_scope", response_payload.get("candidate_scope") or {})
    comparison_rows = payload.get("comparison_table") or []
    if not (payload.get("requirement") or {}).get("need_comparison"):
        comparison_rows = []
    yield sse_event("comparison_table", {"rows": comparison_rows})
    if response_payload.get("follow_up_questions"):
        yield sse_event("follow_up_questions", {"questions": response_payload.get("follow_up_questions")})
    yield sse_event("result", response_payload)

    # ── 组合意图：推荐后进入统一购物车确认链路 ──
    # 当路由参数带 action="add_to_cart" 时，不直接写购物车；
    # 先生成 pending_cart_action，由 /api/cart/confirm 统一执行真实变更。
    tool_args = tool_call.get("arguments") or {}
    pending_cart_action = tool_args.get("action") == "add_to_cart"
    if pending_cart_action:
        top_ids = [
            str(card.get("product_id"))
            for card in (response_payload.get("product_cards") or [])
            if card.get("product_id")
        ][:1]
        if top_ids:
            catalog = load_combined_product_catalog()
            product = catalog.get(top_ids[0])
            if product is not None:
                unit_price = getattr(product, "base_price", None)
                quantity = max(int(tool_args.get("quantity") or 1), 1)
                estimated_total = round(unit_price * quantity, 2) if unit_price is not None else None
                plan = _make_plan(top_ids[0], getattr(product, "title", top_ids[0]), "add", quantity, unit_price, estimated_total)
                session.pending_cart_action = plan
                save_session(session)
                yield sse_event("cart_confirmation", {
                    "plan": plan,
                    "message": _build_confirmation_message(plan, "add"),
                })
            else:
                yield sse_event("cart", {
                    "action": "add",
                    "items": [],
                    "total_price": 0.0,
                    "count": 0,
                    "messages": ["推荐商品不在当前商品库，无法生成购物车确认。"],
                })
        else:
            yield sse_event("cart", {
                "action": "add",
                "items": [],
                "total_price": 0.0,
                "count": 0,
                "messages": ["推荐结果为空，无法自动加入购物车。"],
            })

    yield sse_event("done", {"session_id": session.session_id})


def _apply_pc_component_update(session: Any, payload: Dict[str, Any], _tool_call: Dict[str, Any]) -> None:
    """Update session.current_pc_build when a single PC part is recommended.

    Matches the recommended product's category to the corresponding role in
    the current build and replaces that component.
    """

    cards = payload.get("product_cards") or []
    if not cards:
        return
    top_card = cards[0]
    product_id = str(top_card.get("product_id") or "")
    if not product_id:
        return

    # 从 product_id 推断角色（如 pc_gpu_xxx → pc_gpu）
    role = ""
    for prefix in ("pc_cpu", "pc_gpu", "pc_motherboard", "pc_memory", "pc_storage", "pc_psu", "pc_case", "pc_cooler"):
        if product_id.startswith(prefix):
            role = prefix
            break
    if not role:
        return

    build = dict(session.current_pc_build or {})
    build[role] = {
        "product_id": product_id,
        "title": str(top_card.get("title") or top_card.get("name") or ""),
        "price": top_card.get("price"),
    }
    # 重新计算总价
    total = 0.0
    for key in ("pc_cpu", "pc_gpu", "pc_motherboard", "pc_memory", "pc_storage", "pc_psu", "pc_case", "pc_cooler"):
        part = build.get(key) or {}
        price = part.get("price")
        if price is not None:
            total += float(price)
    build["total_price"] = round(total, 2)
    session.current_pc_build = build
    save_session(session)


def call_recommendation_fn(
    recommendation_fn: Any,
    contextual_goal: str,
    *,
    use_llm: bool,
    image_retrieval_evidence: Any,
    use_llm_guidance: bool,
    catalog_scope: str = "ecommerce",
    use_milvus_retrieval: bool = True,
    use_rag_query_expansion: bool = False,
    router_arguments: Optional[Dict[str, Any]] = None,
    session: Any = None,
) -> Any:
    kwargs = {
        "use_llm": use_llm,
        "image_retrieval_evidence": image_retrieval_evidence,
    }
    try:
        parameters = inspect.signature(recommendation_fn).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "use_llm_guidance" in parameters:
        kwargs["use_llm_guidance"] = use_llm_guidance
    if "use_llm_explanation" in parameters:
        kwargs["use_llm_explanation"] = use_llm_guidance
    if "catalog_scope" in parameters:
        kwargs["catalog_scope"] = catalog_scope
    if "use_milvus_retrieval" in parameters:
        kwargs["use_milvus_retrieval"] = use_milvus_retrieval
    if "use_rag_query_expansion" in parameters:
        kwargs["use_rag_query_expansion"] = use_rag_query_expansion
    if "skip_keyword_check" in parameters:
        kwargs["skip_keyword_check"] = True
    if "router_arguments" in parameters and router_arguments:
        kwargs["router_arguments"] = router_arguments
    if "session" in parameters and session is not None:
        kwargs["session"] = session
    return recommendation_fn(contextual_goal, **kwargs)


def build_chat_progress_events(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    from rag.api.app_context import CATEGORY_LABELS

    scope = payload.get("candidate_scope") or {}
    trace = payload.get("trace") or {}
    cards = payload.get("product_cards") or []
    events: List[Dict[str, Any]] = []

    total = scope.get("total_catalog_count") or payload.get("candidate_count")
    if total is not None:
        events.append({"label": "商品库扫描完成", "detail": f"共读取 {total} 条本地真实商品数据。"})

    retrieval = trace.get("milvus_retrieval") or trace.get("retrieval") or {}
    retrieval_status = retrieval.get("status")
    if retrieval_status and retrieval_status != "disabled":
        events.append({"label": "RAG 证据检索完成", "detail": f"检索到 {retrieval.get('total_hits', 0)} 条证据，命中 {len(retrieval.get('matched_product_ids') or [])} 个商品。"})
    else:
        events.append({"label": "结构化筛选启动", "detail": "当前使用本地商品属性、SKU、价格和评价进行评分。"})

    for category, info in (scope.get("by_category") or {}).items():
        if not isinstance(info, dict):
            continue
        category_name = CATEGORY_LABELS.get(str(category), str(category))
        events.append(
            {
                "label": f"{category_name}筛选完成",
                "detail": f"原始 {info.get('raw_count', 0)} 条，排除后 {info.get('after_exclusion_count', 0)} 条，预算内命中 {info.get('within_budget_count', 0)} 条。",
            }
        )
        for index, candidate in enumerate((info.get("top_candidates") or [])[:4], 1):
            parts = [str(candidate.get("title") or candidate.get("product_id") or "候选商品")]
            if candidate.get("price") is not None:
                parts.append(f"约 {candidate['price']:g} CNY")
            if candidate.get("score") is not None:
                parts.append(f"评分 {float(candidate['score']):.2f}")
            events.append({"label": f"命中候选 {index}", "detail": "；".join(parts)})

    if cards:
        events.append({"label": "候选卡片已准备", "detail": f"将展示 {min(len(cards), 6)} 张商品卡片，并保留可对比候选。"})
    events.append({"label": "正在生成导购回答", "detail": "正在整理推荐理由和追问。"})
    return events
