import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query

from rag.api.model_utils import parse_model_payload
from rag.api.request_models import ProductUpsertRequest
from rag.api.text_utils import normalize_lookup_text
from rag.recommendation import load_combined_product_catalog, load_product_catalog, upsert_product
from rag.schemas import ApiProduct, ProductFAQ, ProductReview, ProductSku
from rag.utils.runtime_errors import is_debug_mode, public_error

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/api/products")
def list_products(
    category: Optional[str] = Query(default=None),
    brand: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    """Return ecommerce products filtered by category, brand, and keyword."""

    catalog = load_combined_product_catalog()
    products = catalog.products
    if category:
        products = [product for product in products if product.category.value == category]
    if brand:
        brand_key = normalize_lookup_text(brand)
        products = [product for product in products if normalize_lookup_text(product.brand) == brand_key]
    if q:
        query = normalize_lookup_text(q)
        products = [product for product in products if product_matches_query(product, query)]
    return {
        "count": len(products),
        "products": [product_to_response(product) for product in products],
        "categories": build_product_facets(catalog.products, "category"),
        "brands": build_product_facets(catalog.products, "brand"),
    }


@router.get("/api/products/{product_id}")
def get_product(product_id: str) -> Dict[str, Any]:
    """Return one ecommerce product by product_id."""

    catalog = load_combined_product_catalog()
    product = catalog.get(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product not found: {product_id}")
    return product_to_response(product)


@router.post("/api/products")
def save_product(request: ProductUpsertRequest, x_admin_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """Validate and persist a product into the JSON catalog."""

    require_product_admin(x_admin_token)
    product = parse_model_payload(ApiProduct, request.product)
    try:
        catalog = upsert_product(product)
    except Exception as exc:
        logger.exception("Product upsert failed")
        raise HTTPException(status_code=400, detail=public_error(exc)) from exc
    saved = catalog.require(product.product_id)
    return {
        "status": "saved",
        "count": len(catalog.products),
        "product": product_to_response(saved),
    }


@router.put("/api/products/{product_id}")
def update_product(product_id: str, request: ProductUpsertRequest, x_admin_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    """Update a product when the path product_id matches the payload product_id."""

    require_product_admin(x_admin_token)
    product = parse_model_payload(ApiProduct, request.product)
    if product.product_id != product_id:
        raise HTTPException(status_code=400, detail="product_id in path and product payload must match")
    payload = product.model_dump(mode="json") if hasattr(product, "model_dump") else product.dict()
    return save_product(ProductUpsertRequest(product=payload), x_admin_token=x_admin_token)


def require_product_admin(x_admin_token: Optional[str]) -> None:
    enabled = os.getenv("ENABLE_PRODUCT_ADMIN_API", "false").strip().lower() == "true"
    if not enabled and not is_debug_mode():
        raise HTTPException(status_code=403, detail="product admin API is disabled")
    if not enabled and is_debug_mode():
        return
    expected = os.getenv("ADMIN_TOKEN", "").strip()
    if expected and x_admin_token != expected:
        raise HTTPException(status_code=403, detail="invalid admin token")


def product_matches_query(product: ApiProduct, query: str) -> bool:
    """Return whether a product matches a normalized search query."""

    if not query:
        return True
    haystack = normalize_lookup_text(
        " ".join(
            [
                product.product_id,
                product.brand,
                product.title,
                product.category.value,
                product.category_name,
                product.sub_category,
                product.description,
                " ".join(product.best_for),
                " ".join(product.not_good_for),
                " ".join(product.supported_scenarios),
                " ".join(product.tags),
            ]
        )
    )
    return query in haystack


def build_product_facets(products: List[ApiProduct], field: str) -> List[Dict[str, Any]]:
    """Build product facet values and counts for frontend filters."""

    counts: Dict[str, int] = {}
    for product in products:
        key = product.category.value if field == "category" else product.brand
        counts[key] = counts.get(key, 0) + 1
    return [
        {"value": value, "count": count}
        for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))
    ]


def product_to_response(product: ApiProduct) -> Dict[str, Any]:
    """Return the ecommerce-facing product payload without legacy API-stack fields."""

    payload = {
        "product_id": product.product_id,
        "title": product.title,
        "brand": product.brand,
        "category": product.category.value,
        "category_name": product.category_name,
        "sub_category": product.sub_category,
        "base_price": product.base_price,
        "min_price": product.min_price,
        "max_price": product.max_price,
        "currency": product.currency,
        "stock_status": product.stock_status,
        "stock_quantity": product.stock_quantity,
        "image_path": product.image_path,
        "image_url": product.image_url,
        "skus": [sku_to_response(sku) for sku in product.skus],
        "description": product.description,
        "faqs": [faq_to_response(item) for item in product.faqs],
        "reviews": [review_to_response(item) for item in product.reviews],
        "review_count": product.review_count,
        "rating_avg": product.rating_avg,
        "best_for": product.best_for,
        "not_good_for": product.not_good_for,
        "supported_scenarios": product.supported_scenarios,
        "tags": product.tags,
        "metadata": product.metadata,
    }
    if product.category.value.startswith("pc_"):
        payload.pop("image_path", None)
        payload.pop("image_url", None)
    return payload


def sku_to_response(sku: ProductSku) -> Dict[str, Any]:
    return {
        "sku_id": sku.sku_id,
        "properties": sku.properties,
        "price": sku.price,
    }


def faq_to_response(item: ProductFAQ) -> Dict[str, str]:
    return {
        "question": item.question,
        "answer": item.answer,
    }


def review_to_response(item: ProductReview) -> Dict[str, Any]:
    return {
        "nickname": item.nickname,
        "rating": item.rating,
        "content": item.content,
    }
