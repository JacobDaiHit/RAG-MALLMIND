from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict

from rag.recommendation.adaptive_runtime import AdaptiveRuntimeDecision, select_adaptive_runtime
from rag.recommendation.recommendation_pipeline import InvalidGoalError
from rag.recommendation.recommendation_pipeline import parse_requirement_rule_based
from rag.recommendation.runtime_mode import RuntimeModePolicy, runtime_policy_for_mode
from rag.recommendation.tool_router import local_route_tool_call
from rag.schemas import RequirementSpec


def build_adaptive_runtime_context(
    message: str,
    session: Any,
    *,
    requested_mode: str | None = "auto",
    has_attachments: bool = False,
    has_image_data: bool = False,
    llm_configured: bool = True,
    is_test_env: bool = False,
    system_degraded: bool = False,
) -> Dict[str, Any]:
    """Build local routing/parse signals before selecting runtime policy."""

    degraded_precheck = select_adaptive_runtime(
        message,
        session=session,
        requested_mode=requested_mode,
        llm_configured=llm_configured,
        has_attachments=has_attachments,
        has_image_data=has_image_data,
        is_test_env=is_test_env,
        system_degraded=system_degraded,
    )
    if degraded_precheck.selected_mode == "degraded_fast":
        local_route = local_route_tool_call(message, session)
        rule_requirement = _safe_rule_requirement(message)
        decision = select_adaptive_runtime(
            message,
            session=session,
            local_route=local_route,
            route_scores=local_route.get("route_scores") or {},
            requirement=rule_requirement,
            requested_mode=requested_mode,
            llm_configured=llm_configured,
            has_attachments=has_attachments,
            has_image_data=has_image_data,
            is_test_env=is_test_env,
            system_degraded=system_degraded,
        )
    else:
        local_route = local_route_tool_call(message, session)
        rule_requirement = _safe_rule_requirement(message)
        decision = select_adaptive_runtime(
            message,
            session=session,
            local_route=local_route,
            route_scores=local_route.get("route_scores") or {},
            requirement=rule_requirement,
            requested_mode=requested_mode,
            llm_configured=llm_configured,
            has_attachments=has_attachments,
            has_image_data=has_image_data,
            is_test_env=is_test_env,
            system_degraded=system_degraded,
        )
    policy = runtime_policy_for_mode(decision.selected_mode, llm_configured=llm_configured)
    return {
        "decision": decision,
        "policy": policy,
        "local_route": local_route,
        "route_scores": local_route.get("route_scores") or {},
        "rule_requirement": rule_requirement,
    }


def _safe_rule_requirement(message: str) -> RequirementSpec:
    try:
        return parse_requirement_rule_based(message)
    except InvalidGoalError:
        return RequirementSpec(raw_query=str(message or ""), missing_fields=["rule_parse_failed"])


def decision_reason(decision: AdaptiveRuntimeDecision) -> str:
    return "adaptive_runtime:" + ",".join(decision.reason_codes or ["default"])


def decision_signals(
    decision: AdaptiveRuntimeDecision,
    *,
    requested_mode: str | None,
    llm_configured: bool,
    has_attachments: bool,
    has_image_data: bool,
    is_test_env: bool,
    system_degraded: bool,
) -> Dict[str, Any]:
    requested = str(requested_mode or "auto").strip().lower() or "auto"
    if requested not in {"auto", "fast", "balanced", "full"}:
        requested = "auto"
    return {
        "requested_mode": requested,
        "llm_configured": llm_configured,
        "has_attachments": has_attachments,
        "has_image_data": has_image_data,
        "is_test_env": is_test_env,
        "system_degraded": system_degraded,
        "adaptive_decision": decision.to_trace(),
        "reason_codes": list(decision.reason_codes),
    }


def runtime_event_payload(
    *,
    decision: AdaptiveRuntimeDecision,
    policy: RuntimeModePolicy,
    requested_mode: str | None,
    llm_configured: bool,
    has_attachments: bool,
    has_image_data: bool,
    is_test_env: bool,
    system_degraded: bool,
) -> Dict[str, Any]:
    signals = decision_signals(
        decision,
        requested_mode=requested_mode,
        llm_configured=llm_configured,
        has_attachments=has_attachments,
        has_image_data=has_image_data,
        is_test_env=is_test_env,
        system_degraded=system_degraded,
    )
    trace = decision.to_trace()
    return {
        "requested_mode": signals["requested_mode"],
        "selected_mode": decision.selected_mode,
        "mode": decision.selected_mode,
        "reason": decision_reason(decision),
        "signals": signals,
        "llm_configured": llm_configured,
        "use_milvus_retrieval": policy.use_milvus_retrieval,
        "use_rag_query_expansion": policy.use_rag_query_expansion,
        "policy": asdict(policy),
        "route_confidence": trace["route_confidence"],
        "route_margin": trace["route_margin"],
        "requirement_completeness": trace["requirement_completeness"],
        "query_complexity": trace["query_complexity"],
        "history_dependency": trace["history_dependency"],
        "reason_codes": trace["reason_codes"],
    }


def apply_runtime_trace(
    trace: Dict[str, Any],
    *,
    decision: AdaptiveRuntimeDecision,
    policy: RuntimeModePolicy,
    requested_mode: str | None,
    llm_configured: bool,
    has_attachments: bool,
    has_image_data: bool,
    is_test_env: bool,
    system_degraded: bool,
) -> None:
    signals = decision_signals(
        decision,
        requested_mode=requested_mode,
        llm_configured=llm_configured,
        has_attachments=has_attachments,
        has_image_data=has_image_data,
        is_test_env=is_test_env,
        system_degraded=system_degraded,
    )
    adaptive = decision.to_trace()
    trace["runtime_mode"] = decision.selected_mode
    trace["requested_mode"] = signals["requested_mode"]
    trace["selected_mode"] = decision.selected_mode
    trace["requested_runtime_mode"] = signals["requested_mode"]
    trace["selected_runtime_mode"] = decision.selected_mode
    trace["llm_configured"] = llm_configured
    trace["adaptive_decision"] = adaptive
    trace["reason_codes"] = list(decision.reason_codes)
    trace["route_confidence"] = adaptive["route_confidence"]
    trace["route_margin"] = adaptive["route_margin"]
    trace["requirement_completeness"] = adaptive["requirement_completeness"]
    trace["query_complexity"] = adaptive["query_complexity"]
    trace["history_dependency"] = adaptive["history_dependency"]
    trace["fallback_used"] = bool(adaptive["fallback_used"])
    trace["fallback_reason"] = adaptive["fallback_reason"]
    trace["use_milvus_retrieval"] = policy.use_milvus_retrieval
    trace["use_rag_query_expansion"] = policy.use_rag_query_expansion
    trace["runtime_mode_decision"] = {
        "mode": decision.selected_mode,
        "reason": decision_reason(decision),
        "signals": signals,
    }
    trace["runtime_policy"] = asdict(policy)
