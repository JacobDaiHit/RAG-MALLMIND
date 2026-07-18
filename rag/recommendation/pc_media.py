"""Resolve optional local PC product images into browser-safe media metadata.

``resolve_pc_product_media`` is a presentation helper used when loading and
rendering PC directory facts. It does not perform image understanding or affect
compatibility, routing, retrieval, or product eligibility.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Mapping
from urllib.parse import quote


ROOT_DIR = Path(__file__).resolve().parents[2]
PC_IMAGES_DIR = ROOT_DIR / "data" / "jd_pc_products"
PC_MEDIA_CATALOG_PATH = ROOT_DIR / "data" / "parts.json"


def resolve_pc_product_media(
    *,
    title: str = "",
    brand: str = "",
    model: str = "",
    source: Mapping[str, Any] | None = None,
) -> Dict[str, str]:
    """Return a local image path and its mounted URL for one PC product."""

    direct = _media_from_source(source or {})
    if direct:
        return direct

    media = _pc_media_index()
    for key in _media_keys(title=title, brand=brand, model=model):
        if key in media:
            return dict(media[key])
    return {"image_path": "", "image_url": ""}


@lru_cache(maxsize=1)
def _pc_media_index() -> Dict[str, Dict[str, str]]:
    if not PC_MEDIA_CATALOG_PATH.is_file():
        return {}
    try:
        rows = json.loads(PC_MEDIA_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(rows, list):
        return {}

    index: Dict[str, Dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        media = _media_from_source(row.get("source") or {})
        if not media:
            continue
        for key in _media_keys(
            title=str(row.get("title") or ""),
            brand=str(row.get("brand") or ""),
            model=str(row.get("model") or ""),
        ):
            index[key] = media
    return index


def _media_from_source(source: Mapping[str, Any]) -> Dict[str, str]:
    raw_path = source.get("screenshot_path") or source.get("image_path") or source.get("image_url")
    if not raw_path:
        return {}

    normalized = str(raw_path).strip().replace("\\", "/")
    prefix = "data/jd_pc_products/"
    url_prefix = "/pc-images/"
    if normalized.startswith(prefix):
        relative_text = normalized[len(prefix):]
    elif normalized.startswith(url_prefix):
        relative_text = normalized[len(url_prefix):]
    else:
        return {}

    candidate = (PC_IMAGES_DIR / relative_text).resolve()
    try:
        relative = candidate.relative_to(PC_IMAGES_DIR.resolve())
    except ValueError:
        return {}
    if not candidate.is_file():
        return {}

    relative_posix = relative.as_posix()
    return {
        "image_path": f"data/jd_pc_products/{relative_posix}",
        "image_url": f"/pc-images/{quote(relative_posix, safe='/')}",
    }


def _media_keys(*, title: str, brand: str, model: str) -> list[str]:
    keys = []
    normalized_title = " ".join(title.split()).casefold()
    normalized_brand = " ".join(brand.split()).casefold()
    normalized_model = " ".join(model.split()).casefold()
    if normalized_title:
        keys.append(f"title:{normalized_title}")
    if normalized_brand and normalized_model:
        keys.append(f"model:{normalized_brand}|{normalized_model}")
    return keys
