from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

from rag.api.app_context import dedupe_strings
from rag.api.request_models import ChatStreamRequest
from rag.utils.catalog_scope import normalize_catalog_scope


def request_product_ids(request: ChatStreamRequest) -> List[str]:
    ids = []
    for item in request.attachments + request.images:
        if isinstance(item, dict) and item.get("product_id"):
            ids.append(str(item["product_id"]))
    ids.extend(re.findall(r"(?:p_(?:beauty|digital|clothes|food)_\d{3}|pc_[A-Za-z0-9_]+)", request.message))
    return dedupe_strings(ids)


# ── Shared environment helpers (extracted to avoid duplication across routes) ──


def has_image_data(value: Any) -> bool:
    """Return True when *value* contains at least one item with inline image data.

    Accepts a list of dicts or a JSON-encoded string of such a list.
    """

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return False
    if not isinstance(value, list):
        return False
    return any(isinstance(item, dict) and (item.get("data_url") or item.get("dataUrl")) for item in value)


def is_test_env() -> bool:
    """Return True when APP_ENV indicates a CI / test environment."""
    return os.getenv("APP_ENV", "").strip().lower() in {"test", "testing", "ci"}


def system_degraded() -> bool:
    """Return True when SYSTEM_DEGRADED is explicitly enabled."""
    return os.getenv("SYSTEM_DEGRADED", "").strip().lower() in {"1", "true", "yes", "on"}


def stream_llm_enabled() -> bool:
    """Return True when the LLM is configured *and* the stream LLM flag is on."""
    from rag.api import recommendation_app

    return recommendation_app.is_llm_configured() and recommendation_app.STREAM_LLM_ENABLED
