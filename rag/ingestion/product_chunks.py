"""Build ecommerce product evidence chunks for optional vector indexing."""
from __future__ import annotations

from typing import Iterable, List

from rag.recommendation.product_loader import ProductCatalog, load_combined_product_catalog, load_product_catalog
from rag.recommendation.v3.registry import CatalogNormalizationRegistry
from rag.recommendation.v3.pc_catalog import canonical_product_key
from rag.schemas import ApiProduct


def build_product_chunks(products: Iterable[ApiProduct], *, registry: CatalogNormalizationRegistry | None = None) -> List[dict]:
    """Convert catalog products into compact RAG evidence chunks.

    The current shopping loop works without Milvus, but when vector retrieval is
    enabled it should index product knowledge, not old PDF/API-document chunks.
    """

    products = list(products)
    chunks: List[dict] = []
    for product in products:
        chunks.extend(_chunks_for_product(product, registry=registry))
    return chunks


def build_catalog_chunks(catalog: ProductCatalog | None = None) -> List[dict]:
    """Load the ecommerce catalog and build product evidence chunks."""

    catalog = catalog or load_product_catalog()
    return build_product_chunks(catalog.products, registry=CatalogNormalizationRegistry.from_catalog(catalog))


def build_all_catalog_chunks(catalog: ProductCatalog | None = None) -> List[dict]:
    """Load ecommerce and JD PC products and build evidence chunks."""

    catalog = catalog or load_combined_product_catalog()
    return build_product_chunks(catalog.products, registry=CatalogNormalizationRegistry.from_catalog(catalog))


def _chunks_for_product(product: ApiProduct, *, registry: CatalogNormalizationRegistry | None = None) -> List[dict]:
    root_chunk_id = f"{product.product_id}::root"
    is_pc_part = product.category.value.startswith("pc_")
    specs = product.metadata.get("specs") if isinstance(product.metadata.get("specs"), dict) else {}
    brand_entity = registry.brand_by_surface(product.brand) if registry is not None else None
    stock_status = str(product.stock_status or "").lower()
    base = {
        "product_id": product.product_id,
        "part_id": product.product_id if is_pc_part else "",
        "title": product.title,
        "brand": product.brand,
        "brand_family_id": brand_entity.canonical_id if brand_entity else "",
        "category": product.category.value,
        "component_type": product.category.value if is_pc_part else "",
        "category_name": product.category_name,
        "sub_category": product.sub_category,
        "base_price": float(product.base_price),
        "is_active": stock_status not in {"inactive", "off_shelf"},
        "in_stock": stock_status not in {"sold_out", "out_of_stock", "inactive", "off_shelf"},
        "filename": product.product_id,
        "file_type": str(product.metadata.get("source_type") or "ecommerce_product"),
        "file_path": str(product.metadata.get("source") or "data/ecommerce_products/products.json"),
        "page_number": 0,
        "root_chunk_id": root_chunk_id,
        "parent_chunk_id": root_chunk_id,
        "chunk_level": 3,
        "structured_compatibility_fields": specs if is_pc_part else {},
        "metadata": {"canonical_product_key": canonical_product_key(product)} if is_pc_part else {},
    }

    texts = [
        ("profile", _product_profile_text(product)),
        ("sku", _sku_text(product)),
        ("faq", _faq_text(product)),
        ("review", _review_text(product)),
    ]

    chunks = []
    for index, (chunk_type, text) in enumerate(texts):
        if not text:
            continue
        chunk_id = f"{product.product_id}::{chunk_type}::{index}"
        chunks.append(
            {
                **base,
                "text": text[:2000],
                "chunk_id": chunk_id,
                "chunk_idx": index,
                "chunk_type": chunk_type,
                "doc_type": chunk_type,
            }
        )
    return chunks


def _product_profile_text(product: ApiProduct) -> str:
    is_pc_part = product.category.value.startswith("pc_")
    specs = product.metadata.get("specs") if isinstance(product.metadata.get("specs"), dict) else {}
    spec_lines = [f"{key}: {value}" for key, value in sorted(specs.items()) if value not in ("", None, [])][:18] if is_pc_part else []
    values = [
        f"商品ID: {product.product_id}",
        f"标题: {product.title}",
        f"品牌: {product.brand}",
        f"PC component type: {product.category.value}" if is_pc_part else "",
        f"类目: {product.category_name} / {product.sub_category}",
        f"价格: {product.min_price:g}-{product.max_price:g} {product.currency}",
        f"Key specs: {'; '.join(spec_lines)}" if spec_lines else "",
        f"评分: {product.rating_avg if product.rating_avg is not None else '暂无'}",
        f"适合: {'、'.join(product.best_for)}",
        f"不适合: {'、'.join(product.not_good_for)}",
        f"场景: {'、'.join(product.supported_scenarios)}",
        f"标签: {'、'.join(product.tags)}",
        f"详情: {product.description}",
    ]
    return "\n".join(item for item in values if item and not item.endswith(": "))


def _sku_text(product: ApiProduct) -> str:
    if not product.skus:
        return ""
    lines = [f"{product.title} SKU 和规格:"]
    for sku in product.skus[:12]:
        props = "，".join(f"{key}: {value}" for key, value in sku.properties.items())
        price = f"{sku.price:g} {product.currency}" if sku.price is not None else "价格缺失"
        lines.append(f"- {sku.sku_id}: {props or '默认规格'}，价格 {price}")
    return "\n".join(lines)


def _faq_text(product: ApiProduct) -> str:
    if not product.faqs:
        return ""
    lines = [f"{product.title} 官方 FAQ:"]
    for item in product.faqs[:8]:
        lines.append(f"Q: {item.question}\nA: {item.answer}")
    return "\n".join(lines)


def _review_text(product: ApiProduct) -> str:
    if not product.reviews:
        return ""
    lines = [f"{product.title} 用户评价摘要:"]
    for review in product.reviews[:10]:
        rating = f"{review.rating}星" if review.rating is not None else "未评分"
        lines.append(f"- {rating}: {review.content}")
    return "\n".join(lines)
