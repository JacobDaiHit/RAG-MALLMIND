from __future__ import annotations

from typing import Any


def normalize_catalog_scope(scope: Any) -> str:
    value = str(scope or "ecommerce").strip().lower()
    return value if value in {"ecommerce", "pc_parts", "combined"} else "ecommerce"
