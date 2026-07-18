"""Compare explicitly supplied catalog product IDs using directory facts only.

``compare_catalog_products`` is the direct HTTP comparison helper. It reads
title, SKU, price, stock, and parameters from the catalog and returns no model
generated facts; conversational card-reference comparison instead enters
``fact_query_executor`` through a certified RequirementSpecV3.
"""
from __future__ import annotations

from typing import Any, Iterable


def compare_catalog_products(*, catalog: Any, product_ids: Iterable[str]) -> dict[str, object]:
    products = []
    missing = []
    seen = set()
    for raw_id in product_ids:
        product_id = str(raw_id or "").strip()
        if not product_id or product_id in seen:
            continue
        seen.add(product_id)
        product = catalog.get(product_id)
        if product is None:
            missing.append(product_id)
            continue
        products.append(product)
        if len(products) == 3:
            break
    rows = [_row(product) for product in products]
    return {"count": len(rows), "missing_product_ids": missing, "rows": rows}


def _row(product: Any) -> dict[str, object]:
    return {
        "product_id": str(product.product_id),
        "title": str(product.title),
        "brand": str(product.brand),
        "category": product.category.value,
        "sub_category": str(product.sub_category),
        "price": float(product.min_price or product.base_price),
        "price_range": [float(product.min_price), float(product.max_price)],
        "currency": str(product.currency),
        "stock_status": str(product.stock_status),
        "key_skus": [
            {"sku_id": str(sku.sku_id), "properties": dict(sku.properties or {}), "price": float(sku.price if sku.price is not None else product.base_price)}
            for sku in product.skus[:3]
        ],
        "specs": dict((product.metadata or {}).get("specs") or {}),
    }
