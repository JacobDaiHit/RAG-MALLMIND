"""Build the catalog-backed canonical vocabulary shared by all V3 modules.

``CatalogNormalizationRegistry.from_catalog`` derives product types, PC part
roles, and brand families from current catalog facts, then supplies exact unique
lookup to grammar, type candidates, PromotionGate, and CandidateGate. It is not
a fuzzy synonym engine: high-frequency forms such as ``Huawei/华为`` live in one
reviewable registry path, while open natural language must pass SemanticParse
and TypeResolutionGate.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, Tuple

from rag.recommendation.product_loader import ProductCatalog

from .config import PC_COMPONENT_ALIASES, PRODUCT_TYPE_ALIASES, PRODUCT_TYPE_SUB_CATEGORIES, REGISTRY_VERSION
from .types import CanonicalEntity, EntityType


def _surface_key(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).lower().split())


def _normalize_brand_text(value: object) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _brand_id(value: str) -> str:
    normalized = _normalize_brand_text(value)
    if normalized in {"xiaomi", "小米", "redmi", "红米"}:
        return "xiaomi"
    if normalized in {"huawei", "华为"}:
        return "huawei"
    if normalized in {"apple", "苹果", "apple苹果", "iphone", "ipad"}:
        return "apple"
    return normalized


@dataclass(frozen=True)
class CatalogNormalizationRegistry:
    version: str
    product_types: Dict[str, CanonicalEntity]
    brands: Dict[str, CanonicalEntity]

    @classmethod
    def from_catalog(cls, catalog: ProductCatalog) -> "CatalogNormalizationRegistry":
        product_types = {
            canonical_id: CanonicalEntity(
                entity_type=EntityType.PRODUCT_TYPE,
                canonical_id=canonical_id,
                display_name=aliases[0],
                aliases=tuple(aliases),
                catalog_values=PRODUCT_TYPE_SUB_CATEGORIES[canonical_id],
            )
            for canonical_id, aliases in PRODUCT_TYPE_ALIASES.items()
        }
        # Every catalog sub-category is an addressable V3 type.  Static aliases
        # above remain for high-frequency user wording such as "手机"; dynamic
        # entries cover catalog domains such as 咖啡、防晒、篮球鞋 without a
        # second hand-maintained taxonomy.
        for product in catalog.products:
            if str(product.category.value).startswith("pc_"):
                # PC part model names are products, not recommendation types.
                # Their stable type is added below as pc_category:<role>.
                continue
            surface = str(product.sub_category or "").strip()
            if not surface:
                continue
            canonical_id = f"sub_category:{_surface_key(surface)}"
            if canonical_id in product_types or any(
                _surface_key(surface) in {_surface_key(alias) for alias in entity.aliases}
                for entity in product_types.values()
            ):
                continue
            product_types[canonical_id] = CanonicalEntity(
                entity_type=EntityType.PRODUCT_TYPE,
                canonical_id=canonical_id,
                display_name=surface,
                aliases=(surface,),
                catalog_values=(surface,),
            )
        for category, aliases in PC_COMPONENT_ALIASES.items():
            values = tuple(sorted({str(product.sub_category) for product in catalog.products if product.category.value == category}))
            if values:
                product_types[f"pc_category:{category}"] = CanonicalEntity(
                    entity_type=EntityType.PRODUCT_TYPE,
                    canonical_id=f"pc_category:{category}",
                    display_name=aliases[0],
                    aliases=tuple(aliases),
                    catalog_values=values,
                )

        grouped: Dict[str, set[str]] = {}
        for product in catalog.products:
            canonical_id = _brand_id(product.brand)
            if not canonical_id:
                continue
            grouped.setdefault(canonical_id, set()).add(product.brand)
        brands = {
            canonical_id: CanonicalEntity(
                entity_type=EntityType.BRAND_FAMILY,
                canonical_id=canonical_id,
                display_name=sorted(values)[0],
                aliases=tuple(sorted(values | _brand_aliases(canonical_id))),
                catalog_values=tuple(sorted(values)),
            )
            for canonical_id, values in grouped.items()
        }
        return cls(version=REGISTRY_VERSION, product_types=product_types, brands=brands)

    def product_type_by_surface(self, surface: str) -> CanonicalEntity | None:
        """Resolve exact catalog spelling for certified grammar and PC components.

        Natural-language recommendation types must instead use
        TypeResolutionGate.  In particular, this method deliberately has no
        hand-maintained semantic alias table such as ``篮球实战鞋 -> 篮球鞋``.
        """
        key = _surface_key(surface)
        exact_matches = [
            entity
            for entity in self.product_types.values()
            if key in {_surface_key(alias) for alias in entity.aliases}
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]

        return None

    def brand_by_surface(self, surface: str) -> CanonicalEntity | None:
        key = _surface_key(surface)
        matches = [entity for entity in self.brands.values() if key in {_surface_key(alias) for alias in entity.aliases}]
        return matches[0] if len(matches) == 1 else None

    def product_type_aliases(self) -> Iterable[Tuple[str, CanonicalEntity]]:
        for entity in self.product_types.values():
            for alias in entity.aliases:
                yield alias, entity

    def brand_aliases(self) -> Iterable[Tuple[str, CanonicalEntity]]:
        for entity in self.brands.values():
            for alias in entity.aliases:
                yield alias, entity


def _brand_aliases(canonical_id: str) -> set[str]:
    aliases = {
        "xiaomi": {"小米", "Xiaomi", "MI", "Redmi", "红米"},
        "huawei": {"华为", "HUAWEI", "Huawei"},
        "apple": {"Apple", "苹果", "iPhone", "iPad"},
    }
    return aliases.get(canonical_id, set())
