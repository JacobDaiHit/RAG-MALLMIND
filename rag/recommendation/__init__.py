"""Public catalog-access surface used by API, ingestion, and V3 execution.

Routing, requirement parsing, retrieval and state changes live in
``rag.recommendation.v3``.  This package intentionally no longer re-exports
the removed legacy recommendation pipeline.
"""

from rag.recommendation.product_loader import (
    ProductCatalog,
    ProductCatalogError,
    load_catalog_for_scope,
    load_combined_product_catalog,
    load_pc_parts_product_catalog,
    load_product_catalog,
    load_products,
    upsert_product,
)

__all__ = [
    "ProductCatalog",
    "ProductCatalogError",
    "load_catalog_for_scope",
    "load_combined_product_catalog",
    "load_pc_parts_product_catalog",
    "load_product_catalog",
    "load_products",
    "upsert_product",
]
