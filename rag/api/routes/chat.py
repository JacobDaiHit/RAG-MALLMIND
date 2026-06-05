import os
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from rag.api.app_context import prepare_recommendation_context
from rag.api.request_models import CartActionRequest, ChatStreamRequest, ProductCompareRequest
from rag.api.runtime_context import build_adaptive_runtime_context, decision_reason, decision_signals, runtime_event_payload
from rag.api.routes.common import request_product_ids
from rag.api.routes.legacy_chat_compat import chat_compat_response
from rag.api.sse import safe_stream, sse_event
from rag.recommendation.comparison import compare_products
from rag.recommendation.product_loader import load_combined_product_catalog
from rag.recommendation.session_state import (
    apply_cart_instruction,
    current_topic_json,
    get_session,
    remember_tool_call,
)
from rag.recommendation.tool_handlers import (
    build_chat_opening,
    handle_cart,
    handle_compare,
    handle_general_chat,
    handle_pc_build,
    handle_recommend,
)
from rag.recommendation.runtime_mode_selector import RuntimeModeDecision
from rag.recommendation.tool_router import route_shopping_tool_call
from rag.utils.runtime_errors import sanitize_report


router = APIRouter()


def stream_llm_enabled() -> bool:
    from rag.api import recommendation_app

    return recommendation_app.is_llm_configured() and recommendation_app.STREAM_LLM_ENABLED


def recommendation_fn():
    from rag.api import recommendation_app

    return recommendation_app.recommend_shopping_products


def image_retrieval_fn():
    from rag.api import recommendation_app

    return recommendation_app.retrieve_image_evidence


@router.post("/api/chat")
def chat_compat(request: ChatStreamRequest) -> Dict[str, Any]:
    """Legacy compatibility endpoint; /api/chat/stream is the main entrypoint."""

    return chat_compat_response(request)


@router.post("/api/chat/stream")
def chat_stream(request: ChatStreamRequest) -> StreamingResponse:
    raw_attachments = [*request.attachments, *request.images]
    session = get_session(request.session_id)
    raw_message = request.message.strip()
    if not raw_message:
        raise HTTPException(status_code=400, detail="message cannot be empty")

    def unsafe_generate():
        llm_configured = stream_llm_enabled()
        runtime_context = build_adaptive_runtime_context(
            raw_message,
            session,
            requested_mode=getattr(request, "mode", None),
            has_attachments=bool(raw_attachments),
            has_image_data=_has_image_data(raw_attachments),
            llm_configured=llm_configured,
            is_test_env=_is_test_env(),
            system_degraded=_system_degraded(),
        )
        adaptive_decision = runtime_context["decision"]
        policy = runtime_context["policy"]
        session.runtime_mode = adaptive_decision.selected_mode
        decision = RuntimeModeDecision(
            mode=adaptive_decision.selected_mode,  # type: ignore[arg-type]
            reason=decision_reason(adaptive_decision),
            signals=decision_signals(
                adaptive_decision,
                requested_mode=getattr(request, "mode", None),
                llm_configured=llm_configured,
                has_attachments=bool(raw_attachments),
                has_image_data=_has_image_data(raw_attachments),
                is_test_env=_is_test_env(),
                system_degraded=_system_degraded(),
            ),
        )
        yield sse_event(
            "runtime_mode",
            runtime_event_payload(
                decision=adaptive_decision,
                policy=policy,
                requested_mode=getattr(request, "mode", None),
                llm_configured=llm_configured,
                has_attachments=bool(raw_attachments),
                has_image_data=_has_image_data(raw_attachments),
                is_test_env=_is_test_env(),
                system_degraded=_system_degraded(),
            ),
        )
        tool_call = route_shopping_tool_call(raw_message, session, use_llm=policy.use_router_llm)
        remember_tool_call(session, tool_call)
        yield sse_event(
            "tool_call",
            {
                "name": tool_call.get("name"),
                "arguments": tool_call.get("arguments") or {},
                "confidence": tool_call.get("confidence"),
                "reason": tool_call.get("reason"),
                "source": tool_call.get("source"),
                "routing_trace": tool_call.get("routing_trace") or {},
                "topic_memory": current_topic_json(session),
            },
        )

        if tool_call.get("name") == "apply_cart_instruction":
            yield from handle_cart(session, raw_message, request_product_ids(request), tool_call)
            return

        if tool_call.get("name") == "general_chat":
            yield from handle_general_chat(session, tool_call)
            return

        if tool_call.get("name") == "compare_products":
            product_ids = list((tool_call.get("arguments") or {}).get("product_ids") or request_product_ids(request))
            if not product_ids:
                product_ids = request_product_ids(request)
            yield from handle_compare(session, product_ids, tool_call)
            return

        contextual_goal, attachments, attachment_report = prepare_recommendation_context(
            raw_message,
            raw_attachments,
            session,
            use_vision_llm=policy.use_vision_llm,
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
                "detail": "大模型会参与需求理解。" if policy.use_requirement_llm else "当前使用规则解析需求。",
            },
        )

        if tool_call.get("name") == "generate_pc_build_plan":
            yield from handle_pc_build(session, raw_message, contextual_goal, tool_call)
            return

        yield from handle_recommend(
            session,
            raw_message,
            raw_attachments,
            contextual_goal,
            attachments,
            attachment_report,
            policy.use_requirement_llm,
            tool_call,
            recommendation_fn=recommendation_fn(),
            image_retrieval_fn=image_retrieval_fn(),
            use_llm_guidance=policy.use_guidance_llm,
            use_milvus_retrieval=policy.use_milvus_retrieval,
            use_rag_query_expansion=policy.use_rag_query_expansion,
            runtime_mode_decision=decision,
            runtime_mode_policy=policy,
        )

    return StreamingResponse(
        safe_stream(unsafe_generate, {"session_id": session.session_id}),
        media_type="text/event-stream",
        headers={"content-type": "text/event-stream"},
    )


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


@router.post("/api/products/compare")
def compare_product_cards(request: ProductCompareRequest) -> Dict[str, Any]:
    if not request.product_ids:
        raise HTTPException(status_code=400, detail="product_ids cannot be empty")
    return compare_products(load_combined_product_catalog(), request.product_ids)


def _chat_mode(request: ChatStreamRequest) -> str:
    mode = str(getattr(request, "mode", "") or "").strip().lower()
    if mode in {"auto", "fast", "balanced", "full"}:
        return mode
    return "auto"


def _has_image_data(items: List[Dict[str, Any]]) -> bool:
    return any(isinstance(item, dict) and (item.get("data_url") or item.get("dataUrl")) for item in items)


def _is_test_env() -> bool:
    return os.getenv("APP_ENV", "").strip().lower() in {"test", "testing", "ci"}


def _system_degraded() -> bool:
    return os.getenv("SYSTEM_DEGRADED", "").strip().lower() in {"1", "true", "yes", "on"}
