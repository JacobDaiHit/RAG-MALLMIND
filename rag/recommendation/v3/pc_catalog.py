"""Canonical PC product identity shared by ingestion and recommendation.

``canonical_product_key`` removes dataset revision suffix noise from stable
brand/category/model facts. CandidateGate and evidence-chunk creation use the
same key so duplicate PC records cannot become duplicate recommendation cards.
"""
from __future__ import annotations

from rag.recommendation.pc_types import base_model_key


def canonical_product_key(product) -> str:
    """Group data-production revision variants without guessing across product families."""

    category = str(getattr(getattr(product, "category", None), "value", ""))
    if not category.startswith("pc_"):
        return str(getattr(product, "product_id", ""))
    metadata = getattr(product, "metadata", {}) or {}
    model = str(metadata.get("model") or getattr(product, "sub_category", "") or "")
    return base_model_key(getattr(product, "brand", ""), category, model, getattr(product, "title", ""))
