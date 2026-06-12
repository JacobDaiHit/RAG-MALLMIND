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
    last_recommended_product_ids,
    references_previous_item,
    remember_pc_build_plan,
    remember_recommendation,
    save_pc_build_to_session,
    save_session,
    update_topic_memory,
)
from rag.utils.catalog_scope import normalize_catalog_scope
from rag.utils.runtime_errors import public_error, sanitize_result_for_response
from rag.recommendation.llm_client import LLMClientError, OpenAICompatibleChatClient


logger = logging.getLogger(__name__)


def handle_cart(session: Any, message: str, product_ids: List[str], tool_call: Dict[str, Any]) -> Iterable[str]:
    cart_result = apply_cart_instruction(
        session=session,
        instruction=message,
        catalog=load_combined_product_catalog(),
        product_ids=product_ids,
    )
    update_topic_memory(session, tool_call, result_type="cart")
    text = "\n".join(str(item) for item in cart_result.get("messages") or [] if str(item).strip())
    yield sse_event("delta", {"text": text or "购物车已更新。"})
    yield sse_event("cart", cart_result)
    yield sse_event("done", {"session_id": session.session_id})


# ── 🟢 新增: 购物车 v2（计划+确认模式） ──

_CART_CONFIRM_TTL_SECONDS = 60


def handle_cart_v2(session: Any, message: str, product_ids: List[str], tool_call: Dict[str, Any]) -> Iterable[str]:
    """Shopping cart v2: generate a plan first, then wait for user confirmation.

    Instead of immediately executing the cart instruction, this handler:
    1. Plans the cart action (add/remove/update/clear)
    2. Stores the plan in session.pending_cart_action
    3. Sends a ``cart_confirmation`` SSE event

    The front-end should then call ``POST /api/cart/confirm`` to execute.
    """
    args = dict(tool_call.get("arguments") or {})
    catalog = load_combined_product_catalog()

    # 1) 显式 product_ids（请求级或路由参数）
    plan_product_id = ""
    if product_ids:
        plan_product_id = product_ids[0]
    else:
        arg_ids = args.get("product_ids")
        if isinstance(arg_ids, list) and arg_ids:
            plan_product_id = str(arg_ids[0])

    # 2) 如果没有显式 id，尝试从 session 上轮推荐结果中解析（"第一部手机"、"第一款"等）
    if not plan_product_id:
        recommended_ids = last_recommended_product_ids(session)
        if recommended_ids:
            index = extract_item_index(message)
            if index is not None and 0 <= index < len(recommended_ids):
                plan_product_id = recommended_ids[index]
            elif references_previous_item(message):
                plan_product_id = recommended_ids[0]
            else:
                # 用户说"加入购物车"但没指定第几个，默认取第一个推荐
                plan_product_id = recommended_ids[0]

    # 🟢 真实 catalog 校验
    product = catalog.get(plan_product_id) if plan_product_id else None
    if not product and plan_product_id:
        yield sse_event("error", {"label": "商品不存在", "detail": f"product_id {plan_product_id} 不在商品库中。"})
        yield sse_event("done", {"session_id": session.session_id})
        return

    product_title = getattr(product, "title", plan_product_id) if product else plan_product_id
    unit_price = getattr(product, "base_price", None) if product else None

    operation = args.get("operation", "add") or "add"
    raw_qty = args.get("quantity", 1)
    quantity = max(int(raw_qty) if raw_qty is not None else 1, 1)
    estimated_total = round(unit_price * quantity, 2) if unit_price is not None else None

    now = _now_seconds()
    plan: Dict[str, Any] = {
        "operation": operation,
        "product_id": plan_product_id,
        "product_title": product_title,
        "quantity": quantity,
        "estimated_unit_price": unit_price,
        "estimated_total": estimated_total,
        "created_at": now,
        "expires_at": now + _CART_CONFIRM_TTL_SECONDS,
    }
    session.pending_cart_action = plan

    yield sse_event("cart_confirmation", {
        "plan": plan,
        "message": f"确认将 {product_title} x{quantity} {'加入' if operation == 'add' else operation}购物车{'（预估 ¥' + str(estimated_total) + '）' if estimated_total else ''}？",
    })
    yield sse_event("done", {"session_id": session.session_id})


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
                    "你是一个电商智能导购助手。用户问了一个与具体商品推荐无关的问题，请你用自然、友好、多样的方式回复。\n"
                    "回复规则：\n"
                    "1. 如果是问候（你好、hi、hello），友好回应并简短介绍自己的能力（搜索商品、推荐、对比、购物车）\n"
                    "2. 如果是身份问题（你是谁、你叫什么），介绍自己是智能导购助手\n"
                    "3. 如果是购物无关的问题（天气、写代码、新闻），委婉说明自己专注购物领域，并引导用户提出购物需求\n"
                    "4. 如果是感谢或告别，礼貌回应\n"
                    "5. 回复要简短（1-3句话），自然口语化，不要每次都一模一样\n"
                    "直接输出回复文本，不要加引号或前缀。"
                ),
            },
            {"role": "user", "content": query},
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


def handle_compare(session: Any, product_ids: List[str], tool_call: Dict[str, Any]) -> Iterable[str]:
    if not product_ids:
        product_ids = last_recommended_product_ids(session)
    if not product_ids:
        product_ids = comparison_candidate_ids((tool_call.get("arguments") or {}).get("query") or "")
    compare_result = compare_products(load_combined_product_catalog(), product_ids) if product_ids else {
        "count": 0,
        "rows": [],
        "recommendation": {},
        "message": "请指定要对比的 product_id，或先让系统推荐一组商品。",
    }
    topic_memory = update_topic_memory(session, tool_call, result_type="comparison")
    yield sse_event("intent_route", {"route": "comparison", "task_type": "compare_products", "tool_call": tool_call, "topic_memory": topic_memory})
    yield sse_event("comparison_table", {"rows": compare_result.get("rows") or []})
    yield sse_event("result", {"type": "comparison", "comparison": compare_result, "tool_call": tool_call, "topic_memory": topic_memory})
    yield sse_event("done", {"session_id": session.session_id})


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
                    yield sse_event("product_cards", {"cards": cards})
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
    yield sse_event("product_cards", {"cards": [model_to_dict(product)]})
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
    yield sse_event("product_cards", {"cards": [model_to_dict(product)]})
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
    result.trace["runtime_mode"] = "balanced"
    result.trace["llm_configured"] = llm_stream_enabled
    result.trace["llm_used_for_route"] = bool(((tool_call.get("routing_trace") or {}).get("llm") or {}).get("name"))
    result.trace["clarification_required"] = bool(result.trace.get("clarification_required"))
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
    yield sse_event("product_cards", {"products": response_payload.get("product_cards") or []})
    yield sse_event("candidate_scope", response_payload.get("candidate_scope") or {})
    comparison_rows = payload.get("comparison_table") or []
    if not (payload.get("requirement") or {}).get("need_comparison"):
        comparison_rows = []
    yield sse_event("comparison_table", {"rows": comparison_rows})
    if response_payload.get("follow_up_questions"):
        yield sse_event("follow_up_questions", {"questions": response_payload.get("follow_up_questions")})
    yield sse_event("result", response_payload)

    # ── 组合意图：推荐后自动加购物车 ──
    # 当 LLM 路由选择了 recommend_shopping_products 并附带 action="add_to_cart" 时，
    # 在推荐完成后自动将首个推荐商品加入购物车，实现"推荐并加购"的链式操作。
    tool_args = tool_call.get("arguments") or {}
    pending_cart_action = tool_args.get("action") == "add_to_cart"
    if pending_cart_action:
        top_ids = [
            str(card.get("product_id"))
            for card in (response_payload.get("product_cards") or [])
            if card.get("product_id")
        ][:1]
        if top_ids:
            cart_result = apply_cart_instruction(
                session=session,
                instruction=f"把 {top_ids[0]} 加到购物车",
                catalog=load_combined_product_catalog(),
                product_ids=top_ids,
            )
            yield sse_event("cart", cart_result)
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


def build_chat_opening(message: str, session: Any = None) -> str:
    return "我先按你的需求筛一遍商品库，优先找最相关的真实商品。"


def build_chat_delta_lines(payload: Dict[str, Any]) -> List[str]:
    requirement = payload.get("requirement") or {}
    cards = payload.get("product_cards") or []
    plans = payload.get("plans") or []
    trace = payload.get("trace") or {}
    llm_guidance = payload.get("teaching_guidance") or []
    lines: List[str] = []

    # ── clarification_question takes priority ──
    clarification_q = (
        trace.get("clarification_question")
        or requirement.get("clarification_question")
        or ""
    )
    if clarification_q:
        lines.append(str(clarification_q))

    # ── product cards ──
    if cards:
        lead = cards[0]
        lead_title = lead.get("title") or lead.get("name") or "候选商品"
        lead_price = lead.get("price")
        lead_brand = lead.get("brand") or ""
        price_max = requirement.get("price_max")

        # brand mismatch prefix
        requested_brands = requirement.get("brands") or []
        if requested_brands and lead_brand:
            _norm = lambda s: "".join(
                ch.lower() for ch in str(s or "")
                if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
            )
            if not any(_norm(b) in _norm(lead_brand) for b in requested_brands):
                brands_text = "、".join(requested_brands)
                lines.append(
                    f"没有找到 {brands_text} 品牌的在售商品，下面推荐了其他品牌的候选。"
                )

        if price_max is not None and lead_price and lead_price > price_max:
            lines.append(
                f"商品库里没有找到 {price_max:g} CNY 内且足够相关的候选，下面给出同类最近备选。"
            )
        if lead_price:
            lines.append(f"我优先推荐 {lead_title}，参考价约 {lead_price:g} CNY。")
        else:
            lines.append(f"我优先推荐 {lead_title}。")
        lines.append("下面保留了候选商品卡片，你可以继续对比或加入购物车。")
    elif plans:
        summary = str((plans[0] or {}).get("summary") or "").strip()
        lines.append(summary or "已生成一组推荐方案。")
    else:
        # ── smart no-match response using structured data ──
        price_max = requirement.get("price_max")
        price_min = requirement.get("price_min")
        category_list = requirement.get("desired_categories") or requirement.get("target_sub_categories") or []
        excluded_brands = requirement.get("excluded_brands") or []

        if price_max is not None:
            cat_hint = "该品类" if category_list else ""
            lines.append(
                f"当前商品库没有找到 {cat_hint} {price_max:g} CNY 以内的合适商品，"
                "可以试试调高预算或换个关键词。"
            )
        elif price_min is not None:
            lines.append(
                f"当前商品库没有找到 {price_min:g} CNY 以上的合适商品，可以试试调整价格区间。"
            )
        elif excluded_brands:
            brands_text = "、".join(excluded_brands)
            lines.append(
                f"排除 {brands_text} 后没有找到足够匹配的商品，可以放宽品牌限制或调整其他条件。"
            )
        else:
            lines.append("这次没有找到足够贴合的商品，可以换个预算、品类或关键词再试。")

    if trace.get("llm_guidance") == "enabled":
        lines.extend(str(item).strip() for item in llm_guidance[:2] if str(item).strip())
    if requirement.get("need_comparison"):
        lines.append("我也放了候选对比表，方便直接看价格、评分和取舍。")
    return [line for line in lines if line]


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
