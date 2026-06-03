from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


RuntimeMode = Literal["fast", "balanced", "full"]


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

    if is_test_env:
        return RuntimeModeDecision(
            mode="fast",
            reason="测试环境强制使用 fast，避免外部依赖影响回归测试。",
            signals={"requested_mode": requested, "llm_configured": llm_configured, "is_test_env": True},
        )

    if not llm_configured:
        return RuntimeModeDecision(
            mode="fast",
            reason="LLM 未配置，自动降级到 fast。",
            signals={"requested_mode": requested, "llm_configured": False},
        )

    if system_degraded:
        return RuntimeModeDecision(
            mode="fast",
            reason="系统处于降级状态，优先保证响应速度和稳定性。",
            signals={"requested_mode": requested, "llm_configured": llm_configured, "system_degraded": True},
        )

    if requested in {"fast", "balanced", "full"}:
        return RuntimeModeDecision(
            mode=requested,  # type: ignore[arg-type]
            reason="请求显式指定运行模式。",
            signals={"requested_mode": requested, "llm_configured": llm_configured},
        )

    return RuntimeModeDecision(
        mode="balanced",
        reason="auto 默认映射到 balanced。",
        signals={
            "requested_mode": "auto",
            "llm_configured": llm_configured,
            "has_attachments": has_attachments,
            "has_image_data": has_image_data,
        },
    )
