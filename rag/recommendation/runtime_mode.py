from __future__ import annotations

from dataclasses import dataclass


VALID_RUNTIME_MODES = {"auto", "fast", "balanced", "full"}


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


def runtime_policy_for_mode(mode: str | None, *, llm_configured: bool = True) -> RuntimeModePolicy:
    normalized = normalize_runtime_mode(mode)

    if normalized == "auto":
        normalized = "balanced"

    if normalized == "fast":
        return RuntimeModePolicy(
            mode="fast",
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
            use_router_llm=False,
            use_requirement_llm=llm_configured,
            use_guidance_llm=False,
            use_vision_llm=False,
            use_rag_query_expansion=False,
            use_milvus_retrieval=True,
        )

    return RuntimeModePolicy(
        mode="full",
        use_router_llm=llm_configured,
        use_requirement_llm=llm_configured,
        use_guidance_llm=llm_configured,
        use_vision_llm=llm_configured,
        use_rag_query_expansion=llm_configured,
        use_milvus_retrieval=True,
    )
