"""Deterministic adaptive runtime selection for recommendation turns."""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, Optional


ADAPTIVE_RUNTIME_MODES = {"fast", "balanced", "full", "degraded_fast"}


@dataclass(frozen=True)
class AdaptiveRuntimeDecision:
    selected_mode: str
    reason_codes: list[str] = field(default_factory=list)
    route_confidence: float = 0.0
    route_margin: float = 0.0
    requirement_completeness: float = 0.0
    query_complexity: float = 0.0
    history_dependency: float = 0.0
    llm_allowed: bool = True
    llm_available: bool = True
    fallback_used: bool = False
    fallback_reason: Optional[str] = None

    def to_trace(self) -> Dict[str, Any]:
        return asdict(self)


def select_adaptive_runtime(
    message: str,
    *,
    session: Any = None,
    local_route: Optional[Dict[str, Any]] = None,
    route_scores: Optional[Dict[str, Any]] = None,
    requirement: Any = None,
    requested_mode: Optional[str] = None,
    llm_configured: bool = True,
    llm_enabled: Optional[bool] = None,
    llm_failure_reason: Optional[str] = None,
    has_attachments: bool = False,
    has_image_data: bool = False,
    is_test_env: bool = False,
    system_degraded: bool = False,
) -> AdaptiveRuntimeDecision:
    """Choose a runtime mode using only local deterministic signals."""

    text = str(message or "")
    requested = _normalize_requested(requested_mode)
    route_scores = route_scores or (local_route or {}).get("route_scores") or {}
    route_confidence = max(
        _clamp_float(route_scores.get("confidence")),
        _clamp_float((local_route or {}).get("confidence")),
    )
    route_margin = _clamp_float(route_scores.get("margin", route_scores.get("score_margin", 0.0)))
    completeness = _requirement_completeness(requirement, local_route)
    complexity = _query_complexity(text, has_attachments=has_attachments, has_image_data=has_image_data)
    history = _history_dependency(text, session)
    route_name = str((local_route or {}).get("name") or "")
    llm_allowed = _env_bool("MALLMIND_LLM_ENABLED", True) if llm_enabled is None else bool(llm_enabled)
    llm_available = bool(llm_configured and llm_allowed and not system_degraded and not is_test_env)
    reason_codes: list[str] = []

    if not llm_allowed:
        return _decision("degraded_fast", ["llm_global_disabled"], route_confidence, route_margin, completeness, complexity, history, llm_allowed, False, "llm_global_disabled")
    if not llm_configured:
        return _decision("degraded_fast", ["llm_not_configured"], route_confidence, route_margin, completeness, complexity, history, llm_allowed, False, "llm_not_configured")
    if is_test_env:
        return _decision("degraded_fast", ["test_offline_mode"], route_confidence, route_margin, completeness, complexity, history, llm_allowed, False, "test_offline_mode")
    if system_degraded:
        return _decision("degraded_fast", ["system_degraded"], route_confidence, route_margin, completeness, complexity, history, llm_allowed, False, "system_degraded")
    if llm_failure_reason:
        return _decision("degraded_fast", ["llm_fallback"], route_confidence, route_margin, completeness, complexity, history, llm_allowed, False, _sanitize_reason(llm_failure_reason))

    if requested in {"fast", "balanced", "full"}:
        return AdaptiveRuntimeDecision(
            selected_mode=requested,
            reason_codes=[f"requested_{requested}"],
            route_confidence=route_confidence,
            route_margin=route_margin,
            requirement_completeness=completeness,
            query_complexity=complexity,
            history_dependency=history,
            llm_allowed=llm_allowed,
            llm_available=llm_available,
        )

    if has_image_data or has_attachments:
        reason_codes.append("multimodal_input")
    if _needs_full(text, complexity, route_name):
        reason_codes.append("complex_or_detailed_query")
    if reason_codes:
        return AdaptiveRuntimeDecision("full", reason_codes, route_confidence, route_margin, completeness, complexity, history, llm_allowed, llm_available)

    if route_name == "compare_products" or _looks_like_compare(text):
        reason_codes.append("comparison_request")
    if _looks_like_bundle(text):
        reason_codes.append("bundle_or_scenario")
    if history >= 0.45:
        reason_codes.append("session_followup")
    if completeness < 0.75:
        reason_codes.append("incomplete_requirement")
    if _needs_explanation(text):
        reason_codes.append("needs_explanation")
    if route_confidence < 0.85 or route_margin < 0.25:
        reason_codes.append("medium_route_confidence")
    if reason_codes:
        return AdaptiveRuntimeDecision("balanced", _dedupe(reason_codes), route_confidence, route_margin, completeness, complexity, history, llm_allowed, llm_available)

    if is_fast_safe_case(
        text,
        session=session,
        local_route=local_route,
        requirement=requirement,
        route_confidence=route_confidence,
        route_margin=route_margin,
        requirement_completeness=completeness,
        query_complexity=complexity,
        history_dependency=history,
        has_attachments=has_attachments,
        has_image_data=has_image_data,
    ):
        return AdaptiveRuntimeDecision("fast", ["fast_safe_case"], route_confidence, route_margin, completeness, complexity, history, llm_allowed, llm_available)

    return AdaptiveRuntimeDecision("balanced", ["default_balanced"], route_confidence, route_margin, completeness, complexity, history, llm_allowed, llm_available)


def is_fast_safe_case(
    message: str,
    *,
    session: Any = None,
    local_route: Optional[Dict[str, Any]] = None,
    requirement: Any = None,
    route_confidence: float = 0.0,
    route_margin: float = 0.0,
    requirement_completeness: float = 0.0,
    query_complexity: float = 0.0,
    history_dependency: float = 0.0,
    has_attachments: bool = False,
    has_image_data: bool = False,
) -> bool:
    """Allow fast only for narrow, single-turn, explicit ecommerce product asks."""

    text = str(message or "")
    route_name = str((local_route or {}).get("name") or "")
    args = dict((local_route or {}).get("arguments") or {})
    if route_name != "recommend_shopping_products":
        return False
    if has_attachments or has_image_data:
        return False
    if history_dependency > 0.0 or _has_session_context(session):
        return False
    if _looks_like_compare(text) or _looks_like_bundle(text) or _looks_like_pc(text):
        return False
    if _needs_explanation(text) or _looks_like_multimodal(text):
        return False
    if _looks_like_catalog_gap_or_safety(text):
        return False
    if _looks_like_broad_category(text):
        return False
    if route_confidence < 0.93 or route_margin < 0.45:
        return False
    if requirement_completeness < 0.88 or query_complexity > 0.30:
        return False
    if not _has_explicit_product_type(text, requirement, args):
        return False
    return True


def _decision(
    mode: str,
    reasons: list[str],
    confidence: float,
    margin: float,
    completeness: float,
    complexity: float,
    history: float,
    llm_allowed: bool,
    llm_available: bool,
    fallback_reason: str,
) -> AdaptiveRuntimeDecision:
    return AdaptiveRuntimeDecision(
        selected_mode=mode,
        reason_codes=reasons,
        route_confidence=confidence,
        route_margin=margin,
        requirement_completeness=completeness,
        query_complexity=complexity,
        history_dependency=history,
        llm_allowed=llm_allowed,
        llm_available=llm_available,
        fallback_used=True,
        fallback_reason=_sanitize_reason(fallback_reason),
    )


def _normalize_requested(value: Optional[str]) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in {"auto", "fast", "balanced", "full"} else "auto"


def _requirement_completeness(requirement: Any, local_route: Optional[Dict[str, Any]]) -> float:
    args = dict((local_route or {}).get("arguments") or {})
    missing = list(getattr(requirement, "missing_fields", []) or [])
    score = 1.0
    if missing:
        score -= min(len(missing) * 0.18, 0.54)
    if not (getattr(requirement, "desired_categories", None) or args.get("category") or args.get("catalog_scope") == "pc_parts"):
        score -= 0.22
    if getattr(requirement, "price_max", None) is None and args.get("budget") is None:
        score -= 0.08
    return _clamp_float(score)


def _query_complexity(text: str, *, has_attachments: bool, has_image_data: bool) -> float:
    clean = "".join(str(text or "").split())
    score = min(len(clean) / 120.0, 0.55)
    if has_attachments or has_image_data:
        score += 0.35
    if _looks_like_compare(text):
        score += 0.18
    if _looks_like_bundle(text):
        score += 0.18
    if len(re.findall(r"[，,。；;]|然后|同时|另外|并且", text)) >= 2:
        score += 0.2
    return _clamp_float(score)


def _history_dependency(text: str, session: Any) -> float:
    raw = str(text or "")
    lowered = raw.lower()
    score = 0.0
    followup_terms = [
        "\u521a\u624d",
        "\u4e4b\u524d",
        "\u4e0a\u4e00",
        "\u90a3\u6b3e",
        "\u8fd9\u4e2a",
        "\u7b2c\u4e00\u4e2a",
        "\u7b2c\u4e8c\u4e2a",
        "\u7ee7\u7eed",
        "\u6362\u6210",
        "\u964d\u5230",
    ]
    if any(term in raw for term in followup_terms) or any(
        term in lowered for term in ["continue", "followup", "follow-up", "previous", "last one", "change to"]
    ):
        score += 0.55
    has_prior_context = bool(getattr(session, "last_result", None) or getattr(session, "last_requirement", None))
    if "\u9884\u7b97" in raw and has_prior_context:
        score += 0.35
    if getattr(session, "last_result", None):
        score += 0.18
    if getattr(session, "topic_memory", None):
        score += 0.12
    if getattr(session, "last_requirement", None):
        score += 0.12
    return _clamp_float(score)


def _needs_full(text: str, complexity: float, route_name: str) -> bool:
    return (
        complexity >= 0.72
        or route_name == "generate_pc_build_plan" and any(term in text for term in ["完整", "详细", "分析", "调整", "换成"])
        or any(term in text for term in ["详细解释", "完整分析", "深入分析", "图片", "图里", "多模态", "query expansion"])
    )


def _looks_like_compare(text: str) -> bool:
    return any(term.lower() in str(text or "").lower() for term in ["比较", "对比", "哪个更", "哪款更", "vs", "pk"])


def _looks_like_bundle(text: str) -> bool:
    return any(term in str(text or "") for term in ["一套", "全套", "组合", "搭配", "方案", "套装", "场景"])


def _needs_explanation(text: str) -> bool:
    return any(term in str(text or "") for term in ["为什么", "理由", "解释", "取舍", "优缺点", "详细说明"])


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _clamp_float(value: Any) -> float:
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0


def _sanitize_reason(value: str) -> str:
    text = re.sub(r"[A-Za-z]:\\[^\\s]+|/[^\\s]+", "[path]", str(value or ""))
    text = re.sub(r"(api[_-]?key|token|secret|endpoint)=[^\\s]+", r"\1=[redacted]", text, flags=re.I)
    return text[:120]


def _dedupe(items: Iterable[str]) -> list[str]:
    result = []
    seen = set()
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _needs_full(text: str, complexity: float, route_name: str) -> bool:
    raw = str(text or "")
    return (
        complexity >= 0.72
        or route_name == "generate_pc_build_plan" and any(term in raw for term in ["完整", "详细", "分析", "调整", "换成"])
        or any(term in raw for term in ["详细解释", "完整分析", "深入分析", "图片", "图里", "同款", "拍照找", "多模态"])
    )


def _looks_like_compare(text: str) -> bool:
    return any(term.lower() in str(text or "").lower() for term in ["比较", "对比", "哪个更", "哪款更", "vs", "pk"])


def _looks_like_bundle(text: str) -> bool:
    return any(term in str(text or "") for term in ["一套", "全套", "组合", "搭配", "方案", "套装", "场景"])


def _needs_explanation(text: str) -> bool:
    return any(term in str(text or "") for term in ["为什么", "理由", "解释", "取舍", "优缺点", "详细说明"])


def _has_session_context(session: Any) -> bool:
    return bool(
        getattr(session, "last_result", None)
        or getattr(session, "last_requirement", None)
        or getattr(session, "pc_build_history", None)
    )


def _looks_like_pc(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(term in lowered for term in ["pc", "cpu", "gpu", "rtx", "显卡", "主板", "内存", "ssd", "电源", "机箱", "装机", "整机", "主机"])


def _looks_like_multimodal(text: str) -> bool:
    return any(term in str(text or "") for term in ["图片", "图里", "同款", "拍照", "上传", "照片", "image", "photo"])


def _looks_like_catalog_gap_or_safety(text: str) -> bool:
    lowered = str(text or "").lower()
    terms = ["药", "处方", "医药", "宠物", "猫粮", "狗粮", "家电", "冰箱", "汽车", "摩托", "unsupported"]
    return any(term in lowered for term in terms)


def _looks_like_broad_category(text: str) -> bool:
    compact = "".join(str(text or "").split())
    broad_terms = ["商品", "礼物", "数码", "食品", "饮料", "衣服", "护肤", "美妆", "有啥", "有哪些", "随便推荐"]
    return any(term in compact for term in broad_terms) and not _has_specific_product_word(compact)


def _has_explicit_product_type(text: str, requirement: Any, args: Dict[str, Any]) -> bool:
    if args.get("category") in {"pc_part", "pc_build"}:
        return False
    if list(getattr(requirement, "target_sub_categories", []) or []):
        return True
    return _has_specific_product_word("".join(str(text or "").split()))


def _has_specific_product_word(text: str) -> bool:
    lowered = str(text or "").lower()
    specific_terms = [
        "蓝牙耳机",
        "耳机",
        "手机壳",
        "键盘",
        "鼠标",
        "防晒霜",
        "洗面奶",
        "面霜",
        "精华",
        "咖啡",
        "坚果",
        "t恤",
        "跑鞋",
        "篮球鞋",
        "背包",
    ]
    return any(term in lowered for term in specific_terms)
