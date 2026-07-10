"""Brand and product-line normalization helpers for catalog filtering.

The router may output a product line ("iPhone") while the catalog stores the
manufacturer brand ("Apple 苹果").  Keep that translation in one place so the
router, rule parser, and structured filters do not each grow their own rules.
"""
from __future__ import annotations

from typing import Iterable, List, Sequence, Set


BRAND_ALIAS_GROUPS: Sequence[Sequence[str]] = (
    ("Apple 苹果", "Apple", "苹果", "iPhone", "iPad", "MacBook", "Mac"),
    ("HUAWEI 华为", "HUAWEI", "华为"),
    ("Xiaomi 小米", "Xiaomi", "小米", "Redmi", "红米"),
)


def normalize_brand_text(value: object) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def expand_brand_terms(terms: Iterable[object]) -> List[str]:
    """Return input terms plus aliases from the same canonical brand group."""

    normalized_inputs = {normalize_brand_text(term) for term in terms if str(term or "").strip()}
    if not normalized_inputs:
        return []

    expanded: List[str] = []
    seen: Set[str] = set()
    for group in BRAND_ALIAS_GROUPS:
        normalized_group = {normalize_brand_text(item) for item in group}
        if normalized_inputs & normalized_group:
            for item in group:
                key = normalize_brand_text(item)
                if key and key not in seen:
                    seen.add(key)
                    expanded.append(item)

    for term in terms:
        text = str(term or "").strip()
        key = normalize_brand_text(text)
        if text and key not in seen:
            seen.add(key)
            expanded.append(text)
    return expanded


def canonicalize_brand_terms(terms: Iterable[object]) -> List[str]:
    """Prefer the canonical catalog-facing brand name when an alias is known."""

    result: List[str] = []
    seen: Set[str] = set()
    for term in terms:
        text = str(term or "").strip()
        if not text:
            continue
        normalized = normalize_brand_text(text)
        canonical = text
        for group in BRAND_ALIAS_GROUPS:
            if normalized in {normalize_brand_text(item) for item in group}:
                canonical = group[0]
                break
        key = normalize_brand_text(canonical)
        if key not in seen:
            seen.add(key)
            result.append(canonical)
    return result

