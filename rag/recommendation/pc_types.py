"""Shared PC component type helpers."""
from __future__ import annotations

import re
from typing import Optional


PC_COMPONENT_TYPE_ALIASES = {
    "cpu": "pc_cpu",
    "pc_cpu": "pc_cpu",
    "gpu": "pc_gpu",
    "pc_gpu": "pc_gpu",
    "motherboard": "pc_motherboard",
    "pc_motherboard": "pc_motherboard",
    "memory": "pc_memory",
    "pc_memory": "pc_memory",
    "ssd": "pc_storage",
    "hdd": "pc_storage",
    "storage": "pc_storage",
    "pc_storage": "pc_storage",
    "psu": "pc_psu",
    "pc_psu": "pc_psu",
    "case": "pc_case",
    "pc_case": "pc_case",
    "cooler": "pc_cooler",
    "cpu_cooler": "pc_cooler",
    "pc_cooler": "pc_cooler",
}

PC_ROLE_TO_LEGACY = {
    "pc_cpu": "cpu",
    "pc_gpu": "gpu",
    "pc_motherboard": "motherboard",
    "pc_memory": "memory",
    "pc_storage": "ssd",
    "pc_psu": "psu",
    "pc_case": "case",
    "pc_cooler": "cpu_cooler",
}

PC_ROLE_NAMES_ZH = {
    "pc_cpu": "处理器",
    "pc_gpu": "显卡",
    "pc_motherboard": "主板",
    "pc_memory": "内存",
    "pc_storage": "固态硬盘",
    "pc_psu": "电源",
    "pc_case": "机箱",
    "pc_cooler": "散热器",
}

REQUIRED_PC_ROLES = [
    "pc_cpu",
    "pc_motherboard",
    "pc_gpu",
    "pc_memory",
    "pc_storage",
    "pc_psu",
    "pc_case",
    "pc_cooler",
]


def normalize_pc_component_type(value: object) -> str:
    """Normalize PC component aliases to the internal pc_* enum."""

    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if key in PC_COMPONENT_TYPE_ALIASES:
        return PC_COMPONENT_TYPE_ALIASES[key]
    raise ValueError(f"Unknown PC component type: {value!r}")


def maybe_normalize_pc_component_type(value: object) -> Optional[str]:
    try:
        return normalize_pc_component_type(value)
    except ValueError:
        return None


def legacy_pc_component_type(value: object) -> str:
    normalized = normalize_pc_component_type(value)
    return PC_ROLE_TO_LEGACY[normalized]


def pc_component_name_zh(value: object) -> str:
    normalized = normalize_pc_component_type(value)
    return PC_ROLE_NAMES_ZH.get(normalized, normalized)


def base_model_key(brand: object, component_type: object, model: object, title: object = "") -> str:
    """Build a near-duplicate grouping key for V2/V3/revision variants."""

    normalized_type = maybe_normalize_pc_component_type(component_type) or str(component_type or "")
    text = str(model or title or "").lower()
    text = re.sub(r"\b(v|ver|version|rev|revision|edition)[\s._-]*\d+\b", "", text)
    text = re.sub(r"\b\d+(?:st|nd|rd|th)?\s*edition\b", "", text)
    text = re.sub(r"\boc\b", "", text)
    text = re.sub(r"[_\W]+", " ", text, flags=re.ASCII).strip()
    text = re.sub(r"\s+", " ", text)
    return "|".join([str(brand or "").lower().strip(), normalized_type, text])
