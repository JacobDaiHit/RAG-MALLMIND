"""Load, validate, and update the local ecommerce and PC product directories.

``load_product_catalog`` and ``load_combined_product_catalog`` are the factual
source for V3 registry, CandidateGate, product cards, and ingestion chunks.
``ProductCatalog.get`` is the normal lookup path; this module reads JSON data
and never asks an LLM to fill missing product/SKU/price/inventory facts.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from pydantic import ValidationError

from rag.schemas import ApiProduct, ComponentCategory
from rag.utils.runtime_errors import public_error
from rag.recommendation.pc_media import resolve_pc_product_media
from rag.recommendation.pc_types import normalize_pc_component_type, pc_component_name_zh


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PRODUCTS_PATH = ROOT_DIR / "data" / "ecommerce_products" / "products.json"
DEFAULT_PC_PRODUCTS_PATH = ROOT_DIR / "data" / "jd_pc_products" / "products.json"
PC_PART_CATEGORIES = {
    ComponentCategory.pc_cpu,
    ComponentCategory.pc_gpu,
    ComponentCategory.pc_motherboard,
    ComponentCategory.pc_memory,
    ComponentCategory.pc_storage,
    ComponentCategory.pc_psu,
    ComponentCategory.pc_case,
    ComponentCategory.pc_cooler,
}
logger = logging.getLogger(__name__)


class ProductCatalogError(RuntimeError):
    """Raised when the ecommerce product catalog cannot be loaded safely."""


@dataclass(frozen=True)
class ProductCatalog:
    """Validated ecommerce catalog with lookup helpers."""

    products: List[ApiProduct]
    by_id: Dict[str, ApiProduct]
    by_category: Dict[ComponentCategory, List[ApiProduct]]
    source_path: Path

    def get(self, product_id: str) -> Optional[ApiProduct]:
        return self.by_id.get(product_id)

    def require(self, product_id: str) -> ApiProduct:
        product = self.get(product_id)
        if product is None:
            raise KeyError(f"Product not found: {product_id}")
        return product

    def filter_by_category(self, category: ComponentCategory) -> List[ApiProduct]:
        return list(self.by_category.get(category, []))

    def filter_by_categories(self, categories: Iterable[ComponentCategory]) -> List[ApiProduct]:
        selected: List[ApiProduct] = []
        seen = set()
        for category in categories:
            for product in self.by_category.get(category, []):
                if product.product_id in seen:
                    continue
                seen.add(product.product_id)
                selected.append(product)
        return selected

    def search_tags(self, keywords: Iterable[str]) -> List[ApiProduct]:
        normalized_keywords = [item.strip().lower() for item in keywords if item and item.strip()]
        if not normalized_keywords:
            return list(self.products)
        return [
            product
            for product in self.products
            if any(keyword in _product_search_text(product) for keyword in normalized_keywords)
        ]


def load_products(path: Optional[Path] = None) -> List[ApiProduct]:
    """Load and validate ecommerce products from JSON."""

    return list(load_product_catalog(path).products)


def upsert_product(product: ApiProduct, path: Optional[Path] = None) -> ProductCatalog:
    """Create or update one ecommerce product in a JSON catalog."""

    product_path = (path or DEFAULT_PRODUCTS_PATH).resolve()
    raw_items = _load_json(product_path) if product_path.is_file() else []
    if not isinstance(raw_items, list):
        raise ProductCatalogError("Product file must contain a JSON list.")

    payload = _product_to_json_dict(product)
    updated_items: List[Any] = []
    replaced = False
    for item in raw_items:
        existing_id = item.get("product_id") if isinstance(item, dict) else None
        if existing_id == product.product_id:
            updated_items.append(payload)
            replaced = True
        else:
            updated_items.append(item)
    if not replaced:
        updated_items.append(payload)

    _parse_products(updated_items)
    try:
        product_path.parent.mkdir(parents=True, exist_ok=True)
        product_path.write_text(
            json.dumps(updated_items, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise ProductCatalogError(f"Failed to write product file: {product_path}: {exc}") from exc

    _load_product_catalog_cached.cache_clear()
    return load_product_catalog(product_path, use_cache=False)


def load_product_catalog(path: Optional[Path] = None, use_cache: bool = True) -> ProductCatalog:
    """Load ecommerce products and build lookup indexes."""

    product_path = (path or DEFAULT_PRODUCTS_PATH).resolve()
    if use_cache:
        return _load_product_catalog_cached(str(product_path))
    return _load_product_catalog_uncached(product_path)


def load_combined_product_catalog(use_cache: bool = True) -> ProductCatalog:
    """Load ecommerce products plus JD PC parts as card/indexable products."""

    if use_cache:
        return _load_combined_product_catalog_cached()
    return _load_combined_product_catalog_uncached()


def load_pc_parts_product_catalog(use_cache: bool = True) -> ProductCatalog:
    """Load only PC parts converted to ecommerce product cards."""

    if use_cache:
        return _load_pc_parts_product_catalog_cached()
    return _load_pc_parts_product_catalog_uncached()


def filter_pc_parts_catalog(catalog: ProductCatalog) -> ProductCatalog:
    """Return a catalog view containing only PC part categories."""

    products = [product for product in catalog.products if product.category in PC_PART_CATEGORIES]
    return ProductCatalog(
        products=products,
        by_id=_build_by_id(products),
        by_category=_build_by_category(products),
        source_path=catalog.source_path,
    )


def load_catalog_for_scope(scope: str, use_cache: bool = True) -> ProductCatalog:
    """Resolve the internal recommendation catalog scope."""

    normalized = (scope or "ecommerce").strip().lower()
    if normalized == "combined":
        return load_combined_product_catalog(use_cache=use_cache)
    if normalized == "pc_parts":
        return load_pc_parts_product_catalog(use_cache=use_cache)
    return load_product_catalog(use_cache=use_cache)


@lru_cache(maxsize=8)
def _load_product_catalog_cached(path_str: str) -> ProductCatalog:
    return _load_product_catalog_uncached(Path(path_str))


@lru_cache(maxsize=1)
def _load_combined_product_catalog_cached() -> ProductCatalog:
    return _load_combined_product_catalog_uncached()


@lru_cache(maxsize=1)
def _load_pc_parts_product_catalog_cached() -> ProductCatalog:
    return _load_pc_parts_product_catalog_uncached()


def _load_combined_product_catalog_uncached() -> ProductCatalog:
    ecommerce = _load_product_catalog_uncached(DEFAULT_PRODUCTS_PATH)
    pc_products = _load_pc_products_as_api_products(DEFAULT_PC_PRODUCTS_PATH)
    products = [*ecommerce.products, *pc_products]
    return ProductCatalog(
        products=products,
        by_id=_build_by_id(products),
        by_category=_build_by_category(products),
        source_path=ROOT_DIR / "data",
    )


def _load_pc_parts_product_catalog_uncached() -> ProductCatalog:
    products = _load_pc_products_as_api_products(DEFAULT_PC_PRODUCTS_PATH)
    return ProductCatalog(
        products=products,
        by_id=_build_by_id(products),
        by_category=_build_by_category(products),
        source_path=DEFAULT_PC_PRODUCTS_PATH,
    )


def _load_product_catalog_uncached(path: Path) -> ProductCatalog:
    if not path.is_file():
        raise ProductCatalogError(f"Product catalog not found: {path}")
    raw_items = _load_json(path)
    if not isinstance(raw_items, list):
        raise ProductCatalogError("Product file must contain a JSON list.")

    products = _parse_products(raw_items)
    return ProductCatalog(
        products=products,
        by_id=_build_by_id(products),
        by_category=_build_by_category(products),
        source_path=path,
    )


def _load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise ProductCatalogError(f"Invalid product JSON: {path}: {exc}") from exc
    except OSError as exc:
        raise ProductCatalogError(f"Failed to read product file: {path}: {exc}") from exc


def _load_pc_products_as_api_products(path: Path) -> List[ApiProduct]:
    if not path.is_file():
        return []

    try:
        from rag.recommendation.pc_build import load_pc_parts
    except Exception as exc:
        logger.exception("Failed to import PC product loader")
        raise ProductCatalogError(f"Failed to import PC product loader: {public_error(exc)}") from exc

    role_category = {
        "pc_cpu": ComponentCategory.pc_cpu,
        "pc_gpu": ComponentCategory.pc_gpu,
        "pc_motherboard": ComponentCategory.pc_motherboard,
        "pc_memory": ComponentCategory.pc_memory,
        "pc_storage": ComponentCategory.pc_storage,
        "pc_psu": ComponentCategory.pc_psu,
        "pc_case": ComponentCategory.pc_case,
        "pc_cooler": ComponentCategory.pc_cooler,
    }

    products: List[ApiProduct] = []
    for part in load_pc_parts(path):
        try:
            normalized_role = normalize_pc_component_type(part.role)
        except ValueError:
            logger.warning("Unknown PC component type while loading catalog: %s", part.role)
            continue
        category = role_category.get(normalized_role)
        if category is None:
            continue
        display_name = pc_component_name_zh(normalized_role)
        spec_lines = [f"{key}: {value}" for key, value in sorted(part.specs.items()) if value not in ("", None, [])]
        description_parts = [
            part.recommendation_text,
            "；".join(part.selling_points),
            "；".join(spec_lines[:12]),
        ]
        price = max(float(part.price or 0), 0)
        media = resolve_pc_product_media(
            title=part.title,
            brand=part.brand,
            model=part.model,
            source=part.source,
        )
        products.append(
            ApiProduct(
                product_id=part.product_id,
                title=part.title,
                brand=part.brand,
                category=category,
                category_name=display_name,
                sub_category=part.model or display_name,
                base_price=price,
                min_price=price,
                max_price=price,
                currency=part.currency,
                stock_status=part.stock_status or "available_for_demo",
                stock_quantity=part.stock_quantity,
                image_path=media["image_path"],
                image_url=media["image_url"],
                skus=[
                    {
                        "sku_id": part.source.get("sku") or part.product_id,
                        "price": price,
                        "properties": {
                            "型号": part.model,
                            "平台": "JD",
                            "品类": display_name,
                        },
                    }
                ],
                description="；".join(item for item in description_parts if item),
                best_for=part.selling_points,
                not_good_for=part.limitations,
                supported_scenarios=["PC 装机", "电脑配件", normalized_role],
                tags=[*part.tags, display_name, part.model],
                metadata={
                    "source": str(path.relative_to(ROOT_DIR)),
                    "source_type": "jd_pc_product",
                    "component_type": normalized_role,
                    "model": part.model,
                    "product_url": part.source.get("product_url", ""),
                    "specs": part.specs,
                },
                risk_notes=part.limitations,
            )
        )
    return products


def _parse_products(raw_items: List[Any]) -> List[ApiProduct]:
    products: List[ApiProduct] = []
    errors: List[str] = []
    for index, item in enumerate(raw_items):
        product_id = item.get("product_id") if isinstance(item, dict) else None
        try:
            products.append(ApiProduct(**item))
        except (TypeError, ValidationError) as exc:
            errors.append(_format_product_error(index=index, product_id=product_id, error=exc))

    if errors:
        preview = "\n".join(errors[:5])
        more = f"\n... and {len(errors) - 5} more errors" if len(errors) > 5 else ""
        raise ProductCatalogError(f"Invalid ecommerce product data:\n{preview}{more}")
    return products


def _product_to_json_dict(product: ApiProduct) -> Dict[str, Any]:
    return product.model_dump(mode="json")


def _format_product_error(index: int, product_id: Optional[str], error: Exception) -> str:
    prefix = f"index={index} product_id={product_id or '<missing>'}"
    if isinstance(error, ValidationError):
        details = []
        for item in error.errors():
            loc = ".".join(str(part) for part in item.get("loc", []))
            msg = item.get("msg", "invalid value")
            details.append(f"{loc}: {msg}" if loc else msg)
        return f"{prefix}: {'; '.join(details)}"
    return f"{prefix}: {error}"


def _build_by_id(products: List[ApiProduct]) -> Dict[str, ApiProduct]:
    by_id: Dict[str, ApiProduct] = {}
    duplicates = set()
    for product in products:
        product_id = product.product_id
        if not product_id:
            continue
        if product_id in by_id:
            duplicates.add(product_id)
        by_id[product_id] = product
    if duplicates:
        raise ProductCatalogError(f"Duplicate product ids: {', '.join(sorted(duplicates))}")
    return by_id


def _build_by_category(products: List[ApiProduct]) -> Dict[ComponentCategory, List[ApiProduct]]:
    by_category: Dict[ComponentCategory, List[ApiProduct]] = {}
    for product in products:
        by_category.setdefault(product.category, []).append(product)
    return by_category


def _product_search_text(product: ApiProduct) -> str:
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
    ]
    for sku in product.skus:
        values.extend(str(value) for value in sku.properties.values())
    return " ".join(values).lower()
