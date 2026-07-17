"""Input normalization for V3 routing; it cleans form, never rewrites intent."""
from __future__ import annotations

import re
import unicodedata
from uuid import uuid4

from .types import NormalizedTurn

_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_WHITESPACE = re.compile(r"[ \t\r\n]+")


def normalize_turn(*, session_id: str, message: str) -> NormalizedTurn:
    """Return one stable text view without deleting business-bearing tokens."""

    normalized = unicodedata.normalize("NFKC", str(message or ""))
    normalized = _ZERO_WIDTH.sub("", normalized)
    normalized = _CONTROL.sub("", normalized)
    normalized = _WHITESPACE.sub(" ", normalized).strip()
    return NormalizedTurn(
        request_id=f"req-{uuid4().hex}",
        session_id=session_id,
        text=normalized,
        input_events=(),
    )
