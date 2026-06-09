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
from rag.api.routes.common import stream_llm_enabled
from rag.api.sse import sse_event
from rag.recommendation import InvalidGoalError, parse_requirement, parse_requirement_rule_based, recommend_shopping_products
from rag.recommendation.input_preprocessor import preprocess_user_input
from rag.recommendation.recommendation_graph import stream_recommendation_graph
from rag.recommendation.session_state import get_session
from rag.recommendation.tool_router import route_shopping_tool_call
from rag.utils.runtime_errors import public_error, sanitize_report, sanitize_result_for_response


router = APIRouter()


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
    """Non-streaming test endpoint for the recommendation pipeline."""

    session = get_session(request.session_id) if request.session_id else None
    use_llm = stream_llm_enabled()
    goal, attachments, attachment_report = prepare_recommendation_context(
        request.goal,
        request.attachments,
        session,
        use_vision_llm=True,
    )
    try:
        validate_goal(goal)
        catalog_scope = infer_recommend_catalog_scope(request.goal, session)
        result = recommend_shopping_products(
            goal,
            use_llm=use_llm,
            use_llm_guidance=_env_bool("MALLMIND_GUIDANCE_LLM", False),
            catalog_scope=catalog_scope,
            use_milvus_retrieval=_env_bool("MALLMIND_MILVUS_RETRIEVAL", True),
            use_rag_query_expansion=_env_bool("MALLMIND_RAG_QUERY_EXPANSION", False),
        )
    except InvalidGoalError as exc:
        raise HTTPException(status_code=400, detail=public_error(exc)) from exc
    payload = model_to_dict(result)
    trace = payload.setdefault("trace", {})
    trace["attachments"] = attachments
    trace["attachment_analysis"] = sanitize_report(attachment_report)
    trace["runtime_mode"] = "balanced"
    trace["llm_configured"] = use_llm
    trace["llm_used_for_parse"] = bool(trace.get("llm_requirement_parse_used"))
    trace.setdefault("llm_used_for_explanation", False)
    return payload


def infer_recommend_catalog_scope(goal: str, session: Any = None) -> str:
    """Use the same router rules as chat to choose the recommendation catalog."""

    route_session = session or get_session(None)
    tool_call = route_shopping_tool_call(goal, route_session, use_llm=False)
    if tool_call.get("name") != "recommend_shopping_products":
        return "ecommerce"
    scope = (tool_call.get("arguments") or {}).get("catalog_scope") or "ecommerce"
    return scope if scope in {"ecommerce", "pc_parts", "combined"} else "ecommerce"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@router.get("/api/stream-recommend")
def stream_recommend(
    goal: str = Query(...),
    attachments: Optional[str] = Query(default=None),
    session_id: Optional[str] = Query(default=None),
) -> StreamingResponse:
    """Debug endpoint for the graph-style recommendation demo stream."""

    session = get_session(session_id) if session_id else None
    use_llm = stream_llm_enabled()
    parse_goal, attachment_items, attachment_report = prepare_recommendation_context(
        goal,
        attachments,
        session,
        use_vision_llm=True,
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
        yield sse_event("runtime_mode", {"mode": "balanced", "use_llm": use_llm})
        yield sse_event(
            "step",
            {
                "label": "正在调用生成式模型" if use_llm else "使用快速规则解析",
                "detail": "大模型参与需求理解" if use_llm else "规则解析",
            },
        )
        for item in stream_recommendation_graph(
            parse_goal,
            attachments=attachment_items,
            use_llm=use_llm,
            use_guidance_llm=_env_bool("MALLMIND_GUIDANCE_LLM", False),
            use_milvus_retrieval=_env_bool("MALLMIND_MILVUS_RETRIEVAL", True),
            use_rag_query_expansion=_env_bool("MALLMIND_RAG_QUERY_EXPANSION", False),
        ):
            payload = dict(item.data or {})
            if item.event in {"plans", "result"}:
                trace = dict(payload.get("trace") or {})
                trace["attachments"] = attachment_items
                trace["attachment_analysis"] = sanitize_report(attachment_report)
                trace["preprocessed_input"] = preprocess_user_input(goal, attachment_items).to_trace()
                trace["runtime_mode"] = "balanced"
                trace["llm_configured"] = use_llm
                payload["trace"] = trace
                payload = sanitize_result_for_response(payload)
            yield sse_event(item.event, payload)

    return StreamingResponse(generate(), media_type="text/event-stream")
