"""Tests for local product image-vector retrieval."""

import base64
from pathlib import Path

from rag.recommendation.image_retrieval import (
    ProductImageVectorIndex,
    retrieve_image_evidence,
    resolve_product_image_path,
)
from rag.recommendation.product_loader import load_product_catalog


def _data_url_from_file(path: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def test_product_image_index_retrieves_identical_image_first(tmp_path):
    catalog = load_product_catalog(use_cache=False)
    product = catalog.products[0]
    image_path = resolve_product_image_path(product)
    assert image_path is not None

    index = ProductImageVectorIndex(index_path=tmp_path / "image_vectors.json")
    evidence = retrieve_image_evidence(
        attachments=[
            {
                "name": image_path.name,
                "type": "image/jpeg",
                "size": image_path.stat().st_size,
                "data_url": _data_url_from_file(image_path),
            }
        ],
        catalog=catalog,
        top_k=3,
        index=index,
    )

    assert evidence.status == "ok"
    assert evidence.total_hits == 3
    assert product.product_id in evidence.by_product_id
    assert evidence.by_product_id[product.product_id][0]["score"] > 0.99


def test_product_image_index_persists_vectors(tmp_path):
    catalog = load_product_catalog(use_cache=False)
    index_path = tmp_path / "image_vectors.json"
    index = ProductImageVectorIndex(index_path=index_path)

    payload = index.build(catalog)

    assert index_path.is_file()
    assert payload["count"] == 100
    assert payload["entries"][0]["vector"]
    assert payload["entries"][0]["product_id"].startswith("p_")
