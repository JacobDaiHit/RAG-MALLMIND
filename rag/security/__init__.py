"""Public prompt-injection safeguards for HTTP and model-prompt boundaries.

The exported functions are used by the chat input guard and dormant multimodal
observer. They block or wrap untrusted text but do not make routing, catalog,
or execution decisions.
"""

from rag.security.prompt_guard import (
    INJECTION_DEFENSE_PREFIX,
    INJECTION_DEFENSE_SUFFIX,
    detect_injection,
    sanitize_for_prompt,
    wrap_user_input,
)

__all__ = [
    "INJECTION_DEFENSE_PREFIX",
    "INJECTION_DEFENSE_SUFFIX",
    "detect_injection",
    "sanitize_for_prompt",
    "wrap_user_input",
]
