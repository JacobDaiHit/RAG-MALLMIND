"""Build the local product image-vector index."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.recommendation.image_retrieval import ProductImageVectorIndex, resolve_product_image_path
from rag.recommendation.product_loader import load_product_catalog


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Count indexable product images without writing the image index.")
    parser.add_argument("--output", type=Path, help="Override the image index output path.")
    args = parser.parse_args(argv)

    catalog = load_product_catalog(use_cache=False)
    index = ProductImageVectorIndex(index_path=args.output) if args.output else ProductImageVectorIndex()
    products_with_images = [
        product
        for product in catalog.products
        if resolve_product_image_path(product) is not None
    ]
    report = {
        "status": "dry_run" if args.dry_run else "pending",
        "catalog_products": len(catalog.products),
        "indexable_images": len(products_with_images),
        "index_path": str(index.index_path),
        "embedding_version": index.embedding_service.version,
        "embedding_dim": index.embedding_service.dim,
    }
    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    payload = index.build(catalog)
    report.update(
        {
            "status": "ok",
            "indexed_images": payload["count"],
        }
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Indexed {payload['count']} product images into {index.index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
