import json
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from rag.api.app_context import (
    VALIDATION_VERSION,
    build_complete_prompt,
    build_requirement_questions,
    model_to_dict,
    prepare_recommendation_context,
    validate_goal,
)
from rag.api.attachments import prepare_attachments_for_recommendation
from rag.api.request_models import GoalRequest, PromptFinalizeRequest
from rag.api.runtime_context import apply_runtime_trace, build_adaptive_runtime_context, runtime_event_payload
from rag.api.sse import sse_event
from rag.recommendation import InvalidGoalError, parse_requirement, parse_requirement_rule_based, recommend_shopping_products
from rag.recommendation.input_preprocessor import preprocess_user_input
from rag.recommendation.recommendation_graph import stream_recommendation_graph
from rag.recommendation.session_state import get_session
from rag.recommendation.tool_router import route_shopping_tool_call
from rag.utils.runtime_errors import public_error, sanitize_report, sanitize_result_for_response


router = APIRouter()


def stream_llm_enabled() -> bool:
    from rag.api import recommendation_app

    return recommendation_app.is_llm_configured() and recommendation_app.STREAM_LLM_ENABLED


@router.post("/api/analyze-intent")
def analyze_intent(request: GoalRequest) -> Dict[str, Any]:
    session = get_session(request.session_id) if request.session_id else None
    goal, attachments, attachment_report = prepare_recommendation_context(request.goal, request.attachments, session)
    try:
        validate_goal(goal)
        requirement = parse_requirement_rule_based(goal)
    except InvalidGoalError as exc:
        raise HTTPException(status_code=400, detail=public_error(exc)) from exc
    payload = model_to_dict(requirement)
    payload["attachments"] = attachments
    payload["attachment_analysis"] = sanitize_report(attachment_report)
    return sanitize_result_for_response(payload)


@router.post("/api/review-requirement")
def review_requirement(request: GoalRequest) -> Dict[str, Any]:
    session = get_session(request.session_id) if request.session_id else None
    goal, attachments, attachment_report = prepare_recommendation_context(request.goal, request.attachments, session)
    try:
        validate_goal(goal)
        requirement = parse_requirement(goal, use_llm=True)
    except InvalidGoalError as exc:
        raise HTTPException(status_code=400, detail=public_error(exc)) from exc
    questions = build_requirement_questions(requirement, attachments)
    return {
        "requirement": model_to_dict(requirement),
        "attachments": attachments,
        "attachment_analysis": sanitize_report(attachment_report),
        "questions": questions,
        "prompt": build_complete_prompt(request.goal, attachments, []),
    }


@router.post("/api/finalize-prompt")
def finalize_prompt(request: PromptFinalizeRequest) -> Dict[str, Any]:
    attachments, attachment_report = prepare_attachments_for_recommendation(request.attachments)
    prompt = build_complete_prompt(request.goal, attachments, request.answers)
    return {"prompt": prompt, "attachments": attachments, "attachment_analysis": sanitize_report(attachment_report)}


@router.post("/api/recommend")
def recommend(request: GoalRequest) -> Dict[str, Any]:
    """Non-streaming test endpoint for the recommendation pipeline.

    The production chat flow is /api/chat/stream; this route is kept for tests,
    smoke checks, and clients that need one complete recommendation payload.
    """

    session = get_session(request.session_id) if request.session_id else None
    llm_configured = stream_llm_enabled()
    route_session = session or get_session(None)
    runtime_context = build_adaptive_runtime_context(
        request.goal,
        route_session,
        requested_mode=getattr(request, "mode", None) or "auto",
        has_attachments=bool(request.attachments),
        has_image_data=_has_image_data(request.attachments),
        llm_configured=llm_configured,
        is_test_env=_is_test_env(),
        system_degraded=_system_degraded(),
    )
    decision = runtime_context["decision"]
    policy = runtime_context["policy"]
    if session is not None:
        session.runtime_mode = decision.selected_mode
    goal, attachments, attachment_report = prepare_recommendation_context(
        request.goal,
        request.attachments,
        session,
        use_vision_llm=policy.use_vision_llm,
    )
    try:
        validate_goal(goal)
        catalog_scope = infer_recommend_catalog_scope(request.goal, session, local_route=runtime_context["local_route"])
        result = recommend_shopping_products(
            goal,
            use_llm=policy.use_requirement_llm,
            use_llm_guidance=policy.use_guidance_llm,
            catalog_scope=catalog_scope,
            use_milvus_retrieval=policy.use_milvus_retrieval,
            use_rag_query_expansion=policy.use_rag_query_expansion,
        )
    except InvalidGoalError as exc:
        raise HTTPException(status_code=400, detail=public_error(exc)) from exc
    payload = model_to_dict(result)
    trace = payload.setdefault("trace", {})
    trace["attachments"] = attachments
    trace["attachment_analysis"] = sanitize_report(attachment_report)
    apply_runtime_trace(
        trace,
        decision=decision,
        policy=policy,
        requested_mode=getattr(request, "mode", None) or "auto",
        llm_configured=llm_configured,
        has_attachments=bool(request.attachments),
        has_image_data=_has_image_data(request.attachments),
        is_test_env=_is_test_env(),
        system_degraded=_system_degraded(),
    )
    trace["llm_used_for_parse"] = bool(trace.get("llm_requirement_parse_used"))
    trace.setdefault("llm_used_for_explanation", False)
    return payload


def infer_recommend_catalog_scope(goal: str, session: Any = None, *, local_route: Optional[Dict[str, Any]] = None) -> str:
    """Use the same router rules as chat to choose the recommendation catalog."""

    route_session = session or get_session(None)
    tool_call = local_route or route_shopping_tool_call(goal, route_session, use_llm=False)
    if tool_call.get("name") != "recommend_shopping_products":
        return "ecommerce"
    scope = (tool_call.get("arguments") or {}).get("catalog_scope") or "ecommerce"
    return scope if scope in {"ecommerce", "pc_parts", "combined"} else "ecommerce"


def _has_image_data(value: Any) -> bool:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return False
    if not isinstance(value, list):
        return False
    return any(isinstance(item, dict) and (item.get("data_url") or item.get("dataUrl")) for item in value)


def _is_test_env() -> bool:
    return os.getenv("APP_ENV", "").strip().lower() in {"test", "testing", "ci"}


def _system_degraded() -> bool:
    return os.getenv("SYSTEM_DEGRADED", "").strip().lower() in {"1", "true", "yes", "on"}


@router.get("/api/stream-recommend")
def stream_recommend(
    goal: str = Query(...),
    attachments: Optional[str] = Query(default=None),
    session_id: Optional[str] = Query(default=None),
    mode: Optional[str] = Query(default="fast"),
) -> StreamingResponse:
    """Debug endpoint for the graph-style recommendation demo stream.

    This exposes RecommendationGraph events for inspection and should not be
    treated as the main conversational business entrypoint.
    """

    session = get_session(session_id) if session_id else None
    llm_configured = stream_llm_enabled()
    route_session = session or get_session(None)
    runtime_context = build_adaptive_runtime_context(
        goal,
        route_session,
        requested_mode=mode,
        has_attachments=bool(attachments),
        has_image_data=_has_image_data(attachments),
        llm_configured=llm_configured,
        is_test_env=_is_test_env(),
        system_degraded=_system_degraded(),
    )
    decision = runtime_context["decision"]
    policy = runtime_context["policy"]
    if session is not None:
        session.runtime_mode = decision.selected_mode
    parse_goal, attachment_items, attachment_report = prepare_recommendation_context(
        goal,
        attachments,
        session,
        use_vision_llm=policy.use_vision_llm,
    )
    try:
        validate_goal(parse_goal)
    except InvalidGoalError as exc:
        error_detail = public_error(exc)

        def validation_error_stream():
            yield sse_event("validation_error", {"label": "需求无法识别", "detail": error_detail, "validation_version": VALIDATION_VERSION})
            yield sse_event("done", {"label": "推荐已停止"})

        return StreamingResponse(validation_error_stream(), media_type="text/event-stream", headers={"content-type": "text/event-stream"})

    def generate():
        yield sse_event(
            "runtime_mode",
            runtime_event_payload(
                decision=decision,
                policy=policy,
                requested_mode=mode,
                llm_configured=llm_configured,
                has_attachments=bool(attachments),
                has_image_data=_has_image_data(attachments),
                is_test_env=_is_test_env(),
                system_degraded=_system_degraded(),
            ),
        )
        yield sse_event(
            "step",
            {
                "label": "正在调用生成式模型" if policy.use_requirement_llm else "使用快速规则解析",
                "detail": "按 runtime policy 增强需求理解" if policy.use_requirement_llm else "调试流式接口默认不等待外部模型，优先保证演示稳定",
            },
        )
        for item in stream_recommendation_graph(
            parse_goal,
            attachments=attachment_items,
            use_llm=policy.use_requirement_llm,
            use_guidance_llm=policy.use_guidance_llm,
            use_milvus_retrieval=policy.use_milvus_retrieval,
            use_rag_query_expansion=policy.use_rag_query_expansion,
        ):
            payload = dict(item.data or {})
            if item.event in {"plans", "result"}:
                trace = dict(payload.get("trace") or {})
                trace["attachments"] = attachment_items
                trace["attachment_analysis"] = sanitize_report(attachment_report)
                trace["preprocessed_input"] = preprocess_user_input(goal, attachment_items).to_trace()
                apply_runtime_trace(
                    trace,
                    decision=decision,
                    policy=policy,
                    requested_mode=mode,
                    llm_configured=llm_configured,
                    has_attachments=bool(attachments),
                    has_image_data=_has_image_data(attachments),
                    is_test_env=_is_test_env(),
                    system_degraded=_system_degraded(),
                )
                payload["trace"] = trace
                payload = sanitize_result_for_response(payload)
            yield sse_event(item.event, payload)

    return StreamingResponse(generate(), media_type="text/event-stream")
