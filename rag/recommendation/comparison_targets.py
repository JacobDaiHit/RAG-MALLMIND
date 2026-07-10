"""Resolve concrete catalog products for comparison requests."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence

from rag.recommendation.brand_normalizer import canonicalize_brand_terms, expand_brand_terms, normalize_brand_text
from rag.recommendation.product_reference import product_identity_text
from rag.recommendation.recommendation_pipeline import parse_requirement_rule_based
from rag.recommendation.session_state import last_recommended_product_ids
from rag.schemas import ApiProduct


def resolve_comparison_product_ids(catalog: Any, session: Any, tool_call: Dict[str, Any], initial_ids: Iterable[str]) -> List[str]:
    args = dict(tool_call.get("arguments") or {})
    explicit = _valid_ids(catalog, initial_ids)
    if explicit:
        return explicit[:3]

    query = str(args.get("query") or "").strip()
    brands = canonicalize_brand_terms(args.get("brands") or [])
    sub_category = str(args.get("sub_category") or "").strip()
    category = str(args.get("category") or "").strip()

    if not brands and query:
        try:
            parsed = parse_requirement_rule_based(query, skip_keyword_check=True)
            brands = canonicalize_brand_terms(parsed.brands)
            sub_category = sub_category or (parsed.target_sub_categories[0] if parsed.target_sub_categories else "")
            category = category or (parsed.desired_categories[0].value if parsed.desired_categories else "")
        except Exception:
            pass

    if brands:
        selected = _select_one_product_per_brand(catalog.products, brands, category, sub_category)
        if selected:
            return [product.product_id for product in selected[:3]]

    contextual = _valid_ids(catalog, last_recommended_product_ids(session))
    return contextual[:3]


def _select_one_product_per_brand(
    products: Sequence[ApiProduct],
    brands: Sequence[str],
    category: str,
    sub_category: str,
) -> List[ApiProduct]:
    selected: List[ApiProduct] = []
    selected_ids = set()
    for brand in brands:
        brand_terms = expand_brand_terms([brand])
        candidates = [
            product for product in products
            if product.product_id not in selected_ids
            and _matches_category(product, category, sub_category)
            and _matches_brand(product, brand_terms)
        ]
        if not candidates:
            continue
        best = sorted(candidates, key=_comparison_candidate_score, reverse=True)[0]
        selected.append(best)
        selected_ids.add(best.product_id)
    return selected


def _matches_category(product: ApiProduct, category: str, sub_category: str) -> bool:
    category_ok = not category or product.category.value == category or product.category_name == category
    sub_category_ok = not sub_category or normalize_brand_text(sub_category) in normalize_brand_text(
        f"{product.sub_category} {product.title} {' '.join(product.tags)}"
    )
    return category_ok and sub_category_ok


def _matches_brand(product: ApiProduct, brand_terms: Sequence[str]) -> bool:
    identity = normalize_brand_text(product_identity_text(product))
    return any(normalize_brand_text(term) in identity for term in brand_terms)


def _comparison_candidate_score(product: ApiProduct) -> float:
    rating = float(product.rating_avg or 0)
    reviews = min(float(product.review_count or 0), 1000) / 1000
    detail = 1.0 if product.description else 0.0
    sku = min(len(product.skus), 5) / 5
    return rating * 2 + reviews + detail + sku


def _valid_ids(catalog: Any, ids: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for product_id in ids or []:
        key = str(product_id or "").strip()
        if key and key not in seen and catalog.get(key):
            seen.add(key)
            result.append(key)
    return result

