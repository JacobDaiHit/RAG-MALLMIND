import inspect
import logging
from dataclasses import asdict
from typing import Any, Dict, Iterable, List

from rag.api.app_context import VALIDATION_VERSION, model_to_dict, validate_goal
from rag.api.sse import sse_event
from rag.recommendation import InvalidGoalError, recommend_shopping_products
from rag.recommendation.comparison import compare_products
from rag.recommendation.image_retrieval import retrieve_image_evidence
from rag.recommendation.input_preprocessor import preprocess_user_input
from rag.recommendation.pc_session_flow import build_pc_plan_for_message, format_pc_plan_comparison_text
from rag.recommendation.product_loader import load_catalog_for_scope, load_combined_product_catalog
from rag.recommendation.session_state import (
    apply_cart_instruction,
    last_recommended_product_ids,
    remember_pc_build_plan,
    remember_recommendation,
    update_topic_memory,
)
from rag.utils.catalog_scope import normalize_catalog_scope
from rag.utils.runtime_errors import public_error, sanitize_result_for_response


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


def handle_general_chat(session: Any, tool_call: Dict[str, Any]) -> Iterable[str]:
    update_topic_memory(session, tool_call, result_type="general_chat")
    query = str((tool_call.get("arguments") or {}).get("query") or "")
    if any(term in query for term in ("天气", "写一首诗", "排序算法", "国际局势")):
        text = (
            "我是智能导购助手，主要帮你挑选商品、做商品对比和处理购物车。"
            "这个问题和购物无关，我就不展开了；如果你有购物需求，可以告诉我品类、预算和偏好。"
        )
    elif query.strip().isdigit() or not any(ch.isalnum() for ch in query):
        text = (
            "我是智能导购助手。请告诉我你想买什么商品、预算多少、有什么偏好，"
            "我可以帮你搜索、推荐、对比，或加入购物车。"
        )
    else:
        text = (
            "你好，我是智能导购助手，可以帮你搜索商品、推荐合适款式、对比商品、"
            "生成整机方案，也可以处理购物车。请告诉我你想买什么。"
        )
    yield sse_event("delta", {"text": text})
    yield sse_event("done", {"session_id": session.session_id})


def handle_compare(session: Any, product_ids: List[str], tool_call: Dict[str, Any]) -> Iterable[str]:
    if not product_ids:
        product_ids = last_recommended_product_ids(session)
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
    runtime_mode_decision: Any = None,
    runtime_mode_policy: Any = None,
) -> Iterable[str]:
    recommendation_fn = recommendation_fn or recommend_shopping_products
    image_retrieval_fn = image_retrieval_fn or retrieve_image_evidence
    catalog_scope = normalize_catalog_scope((tool_call.get("arguments") or {}).get("catalog_scope"))
    recommendation_domain = "single_pc_part" if catalog_scope == "pc_parts" else "ecommerce"
    try:
        validate_goal(contextual_goal)
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
        )
    except InvalidGoalError as exc:
        logger.warning("Recommendation goal validation failed: %s", exc)
        yield sse_event("validation_error", {"label": "需求无法识别", "detail": public_error(exc), "validation_version": VALIDATION_VERSION})
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
    if runtime_mode_decision is not None:
        result.trace["runtime_mode"] = runtime_mode_decision.mode
        result.trace["requested_mode"] = (runtime_mode_decision.signals or {}).get("requested_mode", "auto")
        result.trace["selected_mode"] = runtime_mode_decision.mode
        result.trace["llm_configured"] = bool((runtime_mode_decision.signals or {}).get("llm_configured", llm_stream_enabled))
        result.trace["runtime_mode_decision"] = {
            "mode": runtime_mode_decision.mode,
            "reason": runtime_mode_decision.reason,
            "signals": runtime_mode_decision.signals,
        }
    if runtime_mode_policy is not None:
        result.trace["runtime_policy"] = asdict(runtime_mode_policy)
        result.trace["use_milvus_retrieval"] = runtime_mode_policy.use_milvus_retrieval
        result.trace["use_rag_query_expansion"] = runtime_mode_policy.use_rag_query_expansion
    result.trace["runtime_mode_policy"] = {
        "use_requirement_llm": llm_stream_enabled,
        "use_guidance_llm": use_llm_guidance,
        "use_milvus_retrieval": use_milvus_retrieval,
        "use_rag_query_expansion": use_rag_query_expansion,
    }
    payload = model_to_dict(result)
    remember_recommendation(session, contextual_goal, payload)
    topic_memory = update_topic_memory(session, tool_call, result_type=payload.get("type") or "")
    payload.setdefault("trace", {})["topic_memory"] = topic_memory
    response_payload = sanitize_result_for_response(payload)

    yield sse_event("intent_route", response_payload.get("intent_route") or {})
    for item in build_chat_progress_events(payload):
        yield sse_event("progress", item)
    for text in build_chat_delta_lines(payload):
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
    yield sse_event("done", {"session_id": session.session_id})


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
    if "catalog_scope" in parameters:
        kwargs["catalog_scope"] = catalog_scope
    if "use_milvus_retrieval" in parameters:
        kwargs["use_milvus_retrieval"] = use_milvus_retrieval
    if "use_rag_query_expansion" in parameters:
        kwargs["use_rag_query_expansion"] = use_rag_query_expansion
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

    if cards:
        lead = cards[0]
        lead_title = lead.get("title") or lead.get("name") or "候选商品"
        lead_price = lead.get("price")
        price_max = requirement.get("price_max")
        if price_max is not None and lead_price and lead_price > price_max:
            lines.append(f"商品库里没有找到 {price_max:g} CNY 内且足够相关的候选，下面给出同类最近备选。")
        lines.append(f"我优先推荐 {lead_title}，参考价约 {lead_price:g} CNY。" if lead_price else f"我优先推荐 {lead_title}。")
        lines.append("下面保留了候选商品卡片，你可以继续对比或加入购物车。")
    elif plans:
        summary = str((plans[0] or {}).get("summary") or "").strip()
        lines.append(summary or "已生成一组推荐方案。")
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
