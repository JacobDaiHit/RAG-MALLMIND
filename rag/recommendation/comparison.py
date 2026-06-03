"""Product comparison helpers for 2-3 grounded ecommerce products."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from rag.recommendation.product_loader import ProductCatalog
from rag.schemas import ApiProduct


def compare_products(catalog: ProductCatalog, product_ids: Iterable[str]) -> Dict[str, Any]:
    """Return a structured comparison table for real products from the catalog."""

    products: List[ApiProduct] = []
    missing: List[str] = []
    seen = set()
    for product_id in product_ids:
        key = str(product_id or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        product = catalog.get(key)
        if product is None:
            missing.append(key)
            continue
        products.append(product)
        if len(products) >= 3:
            break

    rows = [product_to_comparison_row(product) for product in products]
    return {
        "count": len(rows),
        "missing_product_ids": missing,
        "rows": rows,
        "recommendation": choose_comparison_winner(rows),
    }


def product_to_comparison_row(product: ApiProduct) -> Dict[str, Any]:
    row = {
        "product_id": product.product_id,
        "title": product.title,
        "brand": product.brand,
        "category": product.category.value,
        "category_name": product.category_name,
        "sub_category": product.sub_category,
        "price": product.min_price or product.base_price,
        "price_range": [product.min_price, product.max_price],
        "currency": product.currency,
        "rating_avg": product.rating_avg,
        "review_count": product.review_count,
        "image_url": product.image_url,
        "stock_status": product.stock_status,
        "best_for": product.best_for[:4],
        "not_good_for": product.not_good_for[:4],
        "key_skus": [
            {
                "sku_id": sku.sku_id,
                "properties": sku.properties,
                "price": sku.price,
            }
            for sku in product.skus[:3]
        ],
        "evidence": [product.description[:180]] + [faq.answer[:120] for faq in product.faqs[:2]],
    }
    if product.category.value.startswith("pc_"):
        row.pop("image_url", None)
    return row


def choose_comparison_winner(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"product_id": None, "reason": "没有可对比的真实商品。"}

    def score(row: Dict[str, Any]) -> float:
        rating = float(row.get("rating_avg") or 3.0) / 5
        reviews = min(float(row.get("review_count") or 0), 20) / 20
        price = float(row.get("price") or 0)
        price_score = 1.0 / max(price, 1)
        return rating * 0.55 + reviews * 0.25 + price_score * 20

    winner = sorted(rows, key=score, reverse=True)[0]
    return {
        "product_id": winner["product_id"],
        "title": winner["title"],
        "reason": "默认按评价均分、评价数量和价格压力综合建议；最终仍应结合用户偏好。"
    }
