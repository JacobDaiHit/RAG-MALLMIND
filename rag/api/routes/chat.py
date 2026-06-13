import logging
import os
import time as _time_module
from typing import Any, Callable, Dict, Iterable, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from rag.api.app_context import prepare_recommendation_context
from rag.api.request_models import CartActionRequest, ChatStreamRequest, ProductCompareRequest
from rag.api.routes.common import request_product_ids, stream_llm_enabled
from rag.api.routes.legacy_chat_compat import chat_compat_response
from rag.api.sse import safe_stream, sse_event
from rag.recommendation.comparison import compare_products
from rag.recommendation.handler_base import generate_trace_id, trace_span
from rag.recommendation.product_loader import load_combined_product_catalog
from rag.recommendation.session_state import (
    apply_cart_instruction,
    get_session,
    save_session,
    session_to_json,
    update_session_from_router,
)
from rag.recommendation.tool_handlers import (
    build_chat_opening,
    handle_cart,
    handle_cart_v2,
    handle_compare,
    handle_compare_v2,
    handle_general_chat,
    handle_parameter_query,
    handle_pc_build,
    handle_price_comparison,
    handle_recommend,
    handle_sku_query,
)
from rag.recommendation.tool_router import route_shopping_tool_call, validate_tool_call
from rag.utils.runtime_errors import sanitize_report


router = APIRouter()

# ── 🟢 输入消毒 ──
MAX_MESSAGE_LENGTH = 2000


def sanitize_input(message: str, session_id: str) -> str:
    """Clean and validate raw user input before routing."""
    if not session_id or not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id is required")
    cleaned = message.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="message cannot be empty")
    if len(cleaned) > MAX_MESSAGE_LENGTH:
        cleaned = cleaned[:MAX_MESSAGE_LENGTH]
    return cleaned


def recommendation_fn():
    from rag.api import recommendation_app

    return recommendation_app.recommend_shopping_products


def image_retrieval_fn():
    from rag.api import recommendation_app

    return recommendation_app.retrieve_image_evidence


# ── 🟢 Handler 注册表（替代 if/elif 分发链） ──
# 轻量工具：不需要 contextual_goal / attachments 等重上下文
# 重量工具（pc_build / recommend）在注册表之外单独处理，
# 因为它们需要先执行 prepare_recommendation_context()。

_LIGHTWEIGHT_TOOLS = {
    "apply_cart_instruction",
    "general_chat",
    "compare_products",
    "parameter_query",
    "sku_detail",
    "price_comparison",
}


def _dispatch_lightweight(
    tool_name: str,
    session: Any,
    tool_call: Dict[str, Any],
    raw_message: str,
    request: ChatStreamRequest,
) -> Iterable[str]:
    """Dispatch a lightweight tool that doesn't need heavy context preparation."""
    if tool_name == "apply_cart_instruction":
        yield from handle_cart_v2(session, raw_message, request_product_ids(request), tool_call)
    elif tool_name == "general_chat":
        yield from handle_general_chat(session, tool_call)
    elif tool_name == "compare_products":
        product_ids = list((tool_call.get("arguments") or {}).get("product_ids") or request_product_ids(request))
        if not product_ids:
            product_ids = request_product_ids(request)
        yield from handle_compare_v2(session, product_ids, tool_call)
    elif tool_name == "parameter_query":
        yield from handle_parameter_query(session, tool_call)
    elif tool_name == "sku_detail":
        yield from handle_sku_query(session, tool_call)
    elif tool_name == "price_comparison":
        yield from handle_price_comparison(session, tool_call)


@router.post("/api/chat")
def chat_compat(request: ChatStreamRequest) -> Dict[str, Any]:
    """Legacy compatibility endpoint; /api/chat/stream is the main entrypoint."""

    return chat_compat_response(request)


@router.post("/api/chat/stream")
def chat_stream(request: ChatStreamRequest) -> StreamingResponse:
    raw_attachments = [*request.attachments, *request.images]
    raw_message = sanitize_input(request.message, request.session_id)
    session = get_session(request.session_id)

    def unsafe_generate():
        span_start = _time_module.time()
        trace_id = generate_trace_id(session.session_id)
        span_id = f"{session.session_id}-{int(span_start * 1000) % 100000}"
        tool_name = ""
        fact_check_passed = None
        use_llm = stream_llm_enabled()

        yield sse_event("runtime_mode", {"mode": "balanced", "use_llm": use_llm})

        with trace_span("route_tool_call", trace_id=trace_id) as route_span:
            tool_call = route_shopping_tool_call(raw_message, session, use_llm=use_llm)
            local_route = tool_call.get("routing_trace", {}).get("local", {})
            tool_call = validate_tool_call(tool_call, local_route, raw_message, session)
            route_span["source"] = tool_call.get("source", "")
            route_span["result"] = tool_call.get("name", "")

        # 争议路由或闲聊不累积 session 状态
        _should_update_session = (
            tool_call.get("name") != "apply_cart_instruction"
            and not tool_call.get("downgraded")
            and tool_call.get("name") != "general_chat"
        )
        if _should_update_session:
            update_session_from_router(session, raw_message, tool_call)
        yield sse_event(
            "tool_call",
            {
                "name": tool_call.get("name"),
                "arguments": tool_call.get("arguments") or {},
                "reason": tool_call.get("reason"),
                "source": tool_call.get("source"),
                "routing_trace": tool_call.get("routing_trace") or {},
            },
        )

        tool_name = tool_call.get("name", "")

        # ── 注册表分发：轻量工具 ──
        if tool_name in _LIGHTWEIGHT_TOOLS:
            with trace_span(f"handle_{tool_name}", trace_id=trace_id):
                yield from _dispatch_lightweight(tool_name, session, tool_call, raw_message, request)
            _end_span(session, span_start, span_id, tool_name, True, None)
            return

        # ── 重量工具：需要 prepare_recommendation_context ──
        contextual_goal, attachments, attachment_report = prepare_recommendation_context(
            raw_message,
            raw_attachments,
            session,
            use_vision_llm=True,
        )
        yield sse_event("delta", {"text": build_chat_opening(raw_message, session)})
        yield sse_event("progress", {"label": "已收到需求", "detail": "开始整理预算、品类、颜色和功能约束。"})
        if attachments:
            public_attachment_report = sanitize_report(attachment_report)
            yield sse_event(
                "attachment_analysis",
                {
                    "summary": public_attachment_report["summary"],
                    "attachments": sanitize_report(attachments),
                    "status_counts": public_attachment_report["status_counts"],
                    "vision_model": public_attachment_report.get("vision_model"),
                },
            )
            yield sse_event(
                "progress",
                {
                    "label": "图片解析完成",
                    "detail": attachment_report["summary"],
                },
            )
        yield sse_event(
            "progress",
            {
                "label": "正在解析条件",
                "detail": "大模型会参与需求理解。" if use_llm else "当前使用规则解析需求。",
            },
        )

        if tool_name == "generate_pc_build_plan":
            with trace_span("handle_pc_build", trace_id=trace_id):
                yield from handle_pc_build(session, raw_message, contextual_goal, tool_call)
            _end_span(session, span_start, span_id, tool_name, True, None)
            return

        # 默认：recommend_shopping_products
        tool_name = "recommend_shopping_products"
        with trace_span("handle_recommend", trace_id=trace_id):
            yield from handle_recommend(
                session,
                raw_message,
                raw_attachments,
                contextual_goal,
                attachments,
                attachment_report,
                use_llm,
                tool_call,
                recommendation_fn=recommendation_fn(),
                image_retrieval_fn=image_retrieval_fn(),
                use_llm_guidance=_env_bool("MALLMIND_GUIDANCE_LLM", False),
                use_milvus_retrieval=_env_bool("MALLMIND_MILVUS_RETRIEVAL", True),
                use_rag_query_expansion=_env_bool("MALLMIND_RAG_QUERY_EXPANSION", False),
            )
        fact_check_passed = getattr(session, "last_fact_check_status", "passed") == "passed"
        _end_span(session, span_start, span_id, tool_name, True, fact_check_passed)

    return StreamingResponse(
        safe_stream(unsafe_generate, {"session_id": session.session_id}),
        media_type="text/event-stream",
        headers={"content-type": "text/event-stream"},
    )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# ── 🟢 ⑱ span 结束日志 ──

_MAX_LOG_ENTRIES = 20


def _end_span(session: Any, span_start: float, span_id: str, tool_name: str, success: bool, fact_check_passed: Optional[bool]) -> None:
    """Record structured span log into session.llm_call_log."""
    elapsed_ms = int((_time_module.time() - span_start) * 1000)
    entry: Dict[str, Any] = {
        "span_id": span_id,
        "tool_name": tool_name,
        "success": success,
        "fact_check_passed": fact_check_passed,
        "elapsed_ms": elapsed_ms,
        "timestamp": int(_time_module.time()),
    }
    log = getattr(session, "llm_call_log", None)
    if isinstance(log, list):
        log.append(entry)
        # 滑动窗口 20
        while len(log) > _MAX_LOG_ENTRIES:
            log.pop(0)


@router.post("/api/cart/actions")
def cart_actions(request: CartActionRequest) -> Dict[str, Any]:
    if not request.instruction.strip():
        raise HTTPException(status_code=400, detail="instruction cannot be empty")
    return apply_cart_instruction(
        session=get_session(request.session_id),
        instruction=request.instruction,
        catalog=load_combined_product_catalog(),
        product_ids=request.product_ids,
    )


# ── 🟢 新增: 购物车确认端点 ──

_CONFIRM_TTL_SECONDS = 60


@router.post("/api/cart/confirm")
def cart_confirm(request: Dict[str, Any]) -> Dict[str, Any]:
    """Confirm or cancel a pending cart action plan."""
    session = get_session(request.get("session_id"))
    plan = getattr(session, "pending_cart_action", None) or {}
    if not plan:
        raise HTTPException(status_code=400, detail="no pending cart action to confirm")

    expires_at = plan.get("expires_at", 0)
    import time

    if time.time() > expires_at:
        session.pending_cart_action = {}
        save_session(session)
        raise HTTPException(status_code=410, detail="cart confirmation expired")

    confirmed = request.get("confirmed", False)
    if not confirmed:
        session.pending_cart_action = {}
        save_session(session)
        return {"status": "cancelled", "cart": session.cart}

    # 🟣 v4: 执行真实购物车写操作——根据 plan.operation 分支
    adjusted_qty = request.get("adjusted_quantity")
    quantity = max(int(adjusted_qty), 1) if adjusted_qty is not None else plan.get("quantity", 1)
    catalog = load_combined_product_catalog()
    pid = plan.get("product_id", "")
    product = catalog.get(pid)
    title = getattr(product, "title", plan.get("product_title", "")) if product else plan.get("product_title", "")
    operation = plan.get("operation", "add")

    if operation == "remove":
        instruction = f"删除 {pid} {title}"
    elif operation == "set_quantity":
        instruction = f"把 {pid} {title} 数量改为 {quantity}"
    else:
        instruction = f"把 {pid} {title} 加入购物车，数量 {quantity}"

    result = apply_cart_instruction(
        session=session,
        instruction=instruction,
        catalog=catalog,
        product_ids=[pid],
    )
    session.pending_cart_action = {}
    save_session(session)
    return {"status": "applied", "cart": result.get("cart", session.cart), "action": result.get("action", operation), "messages": result.get("messages", [])}


@router.post("/api/products/compare")
def compare_product_cards(request: ProductCompareRequest) -> Dict[str, Any]:
    if not request.product_ids:
        raise HTTPException(status_code=400, detail="product_ids cannot be empty")
    return compare_products(load_combined_product_catalog(), request.product_ids)
