"""Deterministic product filtering before scoring.

The recommender should narrow candidates with database fields first, then let
semantic scoring rank the survivors. When a hard budget removes every product
in a category, the filter relaxes only that budget constraint so the system can
still explain the nearest alternatives instead of hallucinating products.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List

from rag.schemas import ApiProduct, ComponentCategory, RequirementSpec


@dataclass(frozen=True)
class FilterDiagnostics:
    category: ComponentCategory
    raw_count: int = 0
    after_stock_count: int = 0
    after_exclusion_count: int = 0
    after_target_count: int = 0
    after_must_have_count: int = 0
    after_budget_count: int = 0
    returned_count: int = 0
    relaxed_constraints: List[str] = field(default_factory=list)

    def to_trace(self) -> Dict[str, object]:
        return {
            "category": self.category.value,
            "raw_count": self.raw_count,
            "after_stock_count": self.after_stock_count,
            "after_exclusion_count": self.after_exclusion_count,
            "after_target_count": self.after_target_count,
            "after_must_have_count": self.after_must_have_count,
            "after_budget_count": self.after_budget_count,
            "returned_count": self.returned_count,
            "relaxed_constraints": list(self.relaxed_constraints),
        }


def filter_products_for_requirement(
    requirement: RequirementSpec,
    products: Iterable[ApiProduct],
    category: ComponentCategory,
) -> tuple[List[ApiProduct], FilterDiagnostics]:
    """Apply structured constraints for one category with safe fallback."""

    raw = [product for product in products if product.category == category]
    stock_filtered = [product for product in raw if is_available(product)]
    exclusion_filtered = [
        product
        for product in stock_filtered
        if not violates_brand_or_text_exclusion(requirement, product)
    ]
    target_filtered = [
        product
        for product in exclusion_filtered
        if matches_target_sub_category(requirement, product)
    ]
    if not target_filtered:
        target_filtered = exclusion_filtered

    must_have_filtered = [
        product
        for product in target_filtered
        if matches_all_required_terms(requirement, product)
    ]
    if not must_have_filtered:
        must_have_filtered = target_filtered

    budget_filtered = [
        product
        for product in must_have_filtered
        if matches_budget(requirement, product)
    ]
    relaxed: List[str] = []
    returned = budget_filtered
    if requirement.price_max is not None and not budget_filtered and must_have_filtered:
        returned = must_have_filtered
        relaxed.append("price_max")

    diagnostics = FilterDiagnostics(
        category=category,
        raw_count=len(raw),
        after_stock_count=len(stock_filtered),
        after_exclusion_count=len(exclusion_filtered),
        after_target_count=len(target_filtered),
        after_must_have_count=len(must_have_filtered),
        after_budget_count=len(budget_filtered),
        returned_count=len(returned),
        relaxed_constraints=relaxed,
    )
    return returned, diagnostics


def is_available(product: ApiProduct) -> bool:
    if product.stock_quantity is not None and product.stock_quantity <= 0:
        return False
    status = (product.stock_status or "").lower()
    return status not in {"sold_out", "out_of_stock", "unavailable"}


def violates_brand_or_text_exclusion(requirement: RequirementSpec, product: ApiProduct) -> bool:
    text = collect_product_text(product)
    if product.brand and normalize(product.brand) in {normalize(item) for item in requirement.excluded_brands}:
        return True
    for term in requirement.excluded_terms:
        key = normalize(term)
        if key and key in text:
            return True
    return False


def matches_all_required_terms(requirement: RequirementSpec, product: ApiProduct) -> bool:
    terms = [term for term in requirement.must_have_terms if term]
    if not terms:
        return True
    text = collect_product_text(product)
    return all(normalize(term) in text for term in terms)


def matches_target_sub_category(requirement: RequirementSpec, product: ApiProduct) -> bool:
    terms = [term for term in requirement.target_sub_categories if term]
    if not terms:
        return True
    text = collect_product_text(product)
    return any(normalize(term) in text for term in terms)


def matches_budget(requirement: RequirementSpec, product: ApiProduct) -> bool:
    price = product.min_price or product.base_price
    if requirement.price_min is not None and price < requirement.price_min:
        return False
    if requirement.price_max is not None and price > requirement.price_max:
        return False
    return True


def collect_product_text(product: ApiProduct) -> str:
    values = [
        product.product_id,
        product.title,
        product.brand,
        product.category.value,
        product.category_name,
        product.sub_category,
        product.description,
        " ".join(product.tags),
        " ".join(product.best_for),
        " ".join(product.not_good_for),
        " ".join(product.supported_scenarios),
        " ".join(f"{sku.sku_id} {' '.join(sku.properties.values())}" for sku in product.skus),
    ]
    return normalize(" ".join(values))


def normalize(value: object) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
