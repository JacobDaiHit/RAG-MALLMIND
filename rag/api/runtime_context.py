"""Shared runtime-mode selection and resolved feature policy."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class RuntimeDecision:
    requested_mode: str
    selected_mode: str
    reason_codes: List[str]
    route_confidence: float
    route_margin: float
    requirement_completeness: float
    query_complexity: float
    history_dependency: float


def build_adaptive_runtime_context(
    message: str,
    session: Any,
    *,
    llm_configured: bool,
    has_attachments: bool = False,
    has_image_data: bool = False,
    requested_mode: str = "auto",
) -> Dict[str, Any]:
    """Select a truthful mode and expose the signals behind the decision."""

    text = str(message or "").strip()
    requested = str(requested_mode or "auto").strip().lower()
    if requested not in {"auto", "fast", "balanced", "full"}:
        requested = "auto"

    lowered = text.lower()
    has_category = any(
        term in lowered
        for term in ("面霜", "护肤", "手机", "耳机", "鞋", "外套", "咖啡", "显卡", "cpu", "ssd")
    )
    has_budget = bool(re.search(r"\d+\s*(?:元|块|以内|以下|预算)", lowered)) or "预算" in text
    is_comparison = any(term in text for term in ("比较", "对比", "哪个更", "哪款更"))
    is_detailed = any(term in text for term in ("详细分析", "深度分析", "完整分析"))
    has_history = bool(
        getattr(session, "last_requirement", None)
        or getattr(session, "last_goal", "")
        or getattr(session, "recent_turns", None)
    )

    route_confidence = 0.92 if has_category or is_comparison else 0.72
    route_margin = 0.35 if has_category or is_comparison else 0.2
    requirement_completeness = 0.85 if has_category and has_budget else (0.7 if has_category else 0.45)
    query_complexity = min(1.0, 0.2 + (0.12 if is_comparison else 0.0) + (0.22 if is_detailed else 0.0))
    history_dependency = 0.65 if has_history else 0.1
    reason_codes: List[str] = []
    if is_comparison:
        reason_codes.append("comparison_request")
    if has_attachments or has_image_data:
        reason_codes.append("multimodal_input")

    if requested != "auto":
        selected = requested
        reason_codes.append(f"explicit_{requested}")
    elif has_attachments or has_image_data or is_detailed:
        selected = "full"
    else:
        selected = "balanced"
        reason_codes.append("default_balanced")

    if selected != "fast" and not llm_configured:
        selected = "degraded_fast"
        reason_codes.append("llm_unavailable")

    decision = RuntimeDecision(
        requested_mode=requested,
        selected_mode=selected,
        reason_codes=reason_codes,
        route_confidence=route_confidence,
        route_margin=route_margin,
        requirement_completeness=requirement_completeness,
        query_complexity=query_complexity,
        history_dependency=history_dependency,
    )
    return {"decision": decision, "llm_configured": bool(llm_configured)}


def build_runtime_policy(
    requested_mode: str,
    message: str,
    session: Any,
    *,
    llm_configured: bool,
    has_attachments: bool = False,
    has_image_data: bool = False,
) -> Dict[str, Any]:
    context = build_adaptive_runtime_context(
        message,
        session,
        llm_configured=llm_configured,
        has_attachments=has_attachments,
        has_image_data=has_image_data,
        requested_mode=requested_mode,
    )
    decision: RuntimeDecision = context["decision"]
    external_enabled = decision.selected_mode in {"balanced", "full"} and llm_configured
    policy = {
        "use_requirement_llm": external_enabled,
        "use_guidance_llm": external_enabled and _env_bool(
            "RECOMMENDATION_LLM_GUIDANCE", _env_bool("MALLMIND_GUIDANCE_LLM", False)
        ),
        "use_vision_llm": external_enabled,
        "use_milvus_retrieval": decision.selected_mode in {"balanced", "full"}
        and _env_bool("RECOMMENDATION_ENABLE_MILVUS", _env_bool("MALLMIND_MILVUS_RETRIEVAL", False)),
        "use_rag_query_expansion": decision.selected_mode == "full"
        and _env_bool("RECOMMENDATION_QUERY_EXPANSION", _env_bool("MALLMIND_RAG_QUERY_EXPANSION", False)),
    }
    reason = ",".join(decision.reason_codes) or "runtime_policy_resolved"
    return {
        "mode": decision.selected_mode,
        "runtime_mode": decision.selected_mode,
        "selected_mode": decision.selected_mode,
        "selected_runtime_mode": decision.selected_mode,
        "requested_mode": decision.requested_mode,
        "reason": reason,
        "reason_codes": list(decision.reason_codes),
        "route_confidence": decision.route_confidence,
        "route_margin": decision.route_margin,
        "requirement_completeness": decision.requirement_completeness,
        "query_complexity": decision.query_complexity,
        "history_dependency": decision.history_dependency,
        "llm_configured": bool(llm_configured),
        "use_llm": policy["use_requirement_llm"],
        "use_llm_guidance": policy["use_guidance_llm"],
        "use_vision_llm": policy["use_vision_llm"],
        "use_milvus_retrieval": policy["use_milvus_retrieval"],
        "use_rag_query_expansion": policy["use_rag_query_expansion"],
        "policy": policy,
        "runtime_policy": policy,
    }


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
