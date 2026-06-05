from __future__ import annotations

import os
from dataclasses import dataclass


VALID_RUNTIME_MODES = {"auto", "fast", "balanced", "full", "degraded_fast"}


@dataclass(frozen=True)
class RuntimeModePolicy:
    mode: str
    use_router_llm: bool
    use_requirement_llm: bool
    use_guidance_llm: bool
    use_vision_llm: bool
    use_rag_query_expansion: bool
    use_milvus_retrieval: bool


def normalize_runtime_mode(mode: str | None) -> str:
    value = str(mode or "").strip().lower()
    return value if value in VALID_RUNTIME_MODES else "auto"


def env_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def global_llm_enabled() -> bool:
    return env_flag("MALLMIND_LLM_ENABLED", True)


def runtime_policy_for_mode(mode: str | None, *, llm_configured: bool = True) -> RuntimeModePolicy:
    normalized = normalize_runtime_mode(mode)
    effective_llm = bool(llm_configured and global_llm_enabled())

    if normalized == "auto":
        normalized = "balanced"

    if normalized in {"fast", "degraded_fast"}:
        return RuntimeModePolicy(
            mode=normalized,
            use_router_llm=False,
            use_requirement_llm=False,
            use_guidance_llm=False,
            use_vision_llm=False,
            use_rag_query_expansion=False,
            use_milvus_retrieval=False,
        )

    if normalized == "balanced":
        return RuntimeModePolicy(
            mode="balanced",
            use_router_llm=effective_llm,
            use_requirement_llm=effective_llm,
            use_guidance_llm=False,
            use_vision_llm=False,
            use_rag_query_expansion=False,
            use_milvus_retrieval=True,
        )

    return RuntimeModePolicy(
        mode="full",
        use_router_llm=effective_llm,
        use_requirement_llm=effective_llm,
        use_guidance_llm=effective_llm,
        use_vision_llm=effective_llm,
        use_rag_query_expansion=effective_llm,
        use_milvus_retrieval=True,
    )
