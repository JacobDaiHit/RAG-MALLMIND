from __future__ import annotations

import re
from typing import List

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


def stream_llm_enabled() -> bool:
    """Return True when the LLM is configured *and* the stream LLM flag is on."""
    from rag.api import recommendation_app

    return recommendation_app.is_llm_configured() and recommendation_app.STREAM_LLM_ENABLED
