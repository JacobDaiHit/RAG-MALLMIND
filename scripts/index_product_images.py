"""Build the local product image-vector index."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.recommendation.image_retrieval import ProductImageVectorIndex
from rag.recommendation.product_loader import load_product_catalog


def main() -> None:
    catalog = load_product_catalog(use_cache=False)
    index = ProductImageVectorIndex()
    payload = index.build(catalog)
    print(f"Indexed {payload['count']} product images into {index.index_path}")


if __name__ == "__main__":
    main()
