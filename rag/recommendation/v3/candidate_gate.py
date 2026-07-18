"""Build the catalog allowlist before any product retrieval.

``CatalogCandidateGate.evaluate`` applies certified category, brand, price,
stock, type-exclusion, and PC duplicate rules to real products. Its
``RetrievalFilters`` is the only product-level input accepted by V3 Milvus, and
``rejected_by_reason`` makes each removed candidate traceable.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .registry import CatalogNormalizationRegistry
from .pc_catalog import canonical_product_key
from .types import CandidateGateResult, RequirementSpecV3, RetrievalFilters, V3Action


_UNAVAILABLE_STOCK = frozenset({"sold_out", "out_of_stock", "inactive", "off_shelf"})


class CatalogCandidateGate:
    """Derive a verified allowlist before retrieval or ranking can run."""

    def evaluate(self, requirement: RequirementSpecV3, *, catalog: Any) -> CandidateGateResult:
        if requirement.action is not V3Action.RECOMMEND:
            raise ValueError("CandidateGate only accepts recommendation requirements")
        registry = CatalogNormalizationRegistry.from_catalog(catalog)
        sub_categories = tuple(sorted({
            sub
            for product_type in requirement.product_type_ids
            for sub in (registry.product_types.get(product_type).catalog_values if registry.product_types.get(product_type) else ())
        }))
        pc_categories = tuple(sorted({
            product_type[len("pc_category:"):]
            for product_type in requirement.product_type_ids
            if product_type.startswith("pc_category:")
        }))
        excluded_sub_categories = tuple(sorted({
            sub
            for product_type in requirement.exclude_product_type_ids
            for sub in (registry.product_types.get(product_type).catalog_values if registry.product_types.get(product_type) else ())
        }))
        excluded_pc_categories = tuple(sorted({
            product_type[len("pc_category:"):]
            for product_type in requirement.exclude_product_type_ids
            if product_type.startswith("pc_category:")
        }))
        rejected: dict[str, list[str]] = defaultdict(list)
        allowed: list[str] = []
        allowed_pc_keys: dict[str, tuple[float, str]] = {}
        for product in catalog.products:
            product_id = str(product.product_id)
            if sub_categories and product.sub_category not in sub_categories:
                rejected["sub_category"].append(product_id)
                continue
            if pc_categories and product.category.value not in pc_categories:
                rejected["pc_category"].append(product_id)
                continue
            if product.sub_category in excluded_sub_categories or product.category.value in excluded_pc_categories:
                rejected["excluded_product_type"].append(product_id)
                continue
            if str(product.stock_status or "").lower() in _UNAVAILABLE_STOCK:
                rejected["stock"].append(product_id)
                continue
            if requirement.price_max is not None and float(product.base_price) > requirement.price_max:
                rejected["price_max"].append(product_id)
                continue
            if requirement.price_min is not None and float(product.base_price) < requirement.price_min:
                rejected["price_min"].append(product_id)
                continue
            brand = registry.brand_by_surface(str(product.brand))
            if brand is None:
                rejected["unknown_brand_registry"].append(product_id)
                continue
            if brand.canonical_id in requirement.exclude_brand_family_ids:
                rejected["excluded_brand"].append(product_id)
                continue
            if requirement.include_brand_family_ids and brand.canonical_id not in requirement.include_brand_family_ids:
                rejected["included_brand_mismatch"].append(product_id)
                continue
            canonical_key = canonical_product_key(product)
            if str(product.category.value).startswith("pc_"):
                preference = (float(product.base_price), product_id)
                existing = allowed_pc_keys.get(canonical_key)
                if existing is not None and existing <= preference:
                    rejected["duplicate_canonical_product"].append(product_id)
                    continue
                if existing is not None:
                    prior_id = existing[1]
                    allowed.remove(prior_id)
                    rejected["duplicate_canonical_product"].append(prior_id)
                allowed_pc_keys[canonical_key] = preference
            allowed.append(product_id)
        filters = RetrievalFilters(
            product_ids=tuple(allowed),
            sub_categories=sub_categories,
            exclude_brand_family_ids=requirement.exclude_brand_family_ids,
            price_max=requirement.price_max,
        )
        return CandidateGateResult(
            filters=filters,
            rejected_by_reason={key: tuple(value) for key, value in sorted(rejected.items())},
        )
