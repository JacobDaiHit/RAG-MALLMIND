from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from rag.recommendation.adaptive_runtime import select_adaptive_runtime


RuntimeMode = Literal["fast", "balanced", "full", "degraded_fast"]


@dataclass(frozen=True)
class RuntimeModeDecision:
    mode: RuntimeMode
    reason: str
    signals: dict[str, Any] = field(default_factory=dict)


def choose_runtime_mode(
    message: str,
    *,
    requested_mode: str | None = None,
    has_attachments: bool = False,
    has_image_data: bool = False,
    llm_configured: bool = True,
    is_test_env: bool = False,
    system_degraded: bool = False,
) -> RuntimeModeDecision:
    raw_requested = str(requested_mode or "").strip().lower()
    requested = raw_requested if raw_requested in {"auto", "fast", "balanced", "full"} else "auto"
    adaptive = select_adaptive_runtime(
        message,
        requested_mode=requested,
        llm_configured=llm_configured,
        has_attachments=has_attachments,
        has_image_data=has_image_data,
        is_test_env=is_test_env,
        system_degraded=system_degraded,
    )
    return RuntimeModeDecision(
        mode=adaptive.selected_mode,  # type: ignore[arg-type]
        reason="adaptive_runtime:" + ",".join(adaptive.reason_codes or ["default"]),
        signals={
            "requested_mode": requested,
            "llm_configured": llm_configured,
            "has_attachments": has_attachments,
            "has_image_data": has_image_data,
            "is_test_env": is_test_env,
            "system_degraded": system_degraded,
            "adaptive_decision": adaptive.to_trace(),
            "reason_codes": list(adaptive.reason_codes),
        },
    )
