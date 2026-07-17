"""V3 Milvus document-contract tests; no external service is required."""
from __future__ import annotations

from rag.ingestion.product_chunks import build_all_catalog_chunks
from scripts.index_ecommerce_products import ROOT_DIR, resolve_index_target


def test_v3_product_chunks_carry_catalog_filter_fields():
    chunks = build_all_catalog_chunks()
    assert chunks
    for chunk in chunks:
        assert chunk["product_id"]
        assert chunk["brand_family_id"]
        assert chunk["sub_category"]
        assert isinstance(chunk["base_price"], float)
        assert isinstance(chunk["is_active"], bool)
        assert isinstance(chunk["in_stock"], bool)


def test_v3_product_chunks_use_one_canonical_brand_family_for_aliases():
    chunks = build_all_catalog_chunks()
    xiaomi = [chunk for chunk in chunks if chunk["product_id"] == "p_digital_008"]
    assert xiaomi
    assert {chunk["brand_family_id"] for chunk in xiaomi} == {"xiaomi"}


def test_v3_pc_chunks_carry_the_same_canonical_deduplication_key():
    chunks = build_all_catalog_chunks()
    pc_chunks = [chunk for chunk in chunks if str(chunk["category"]).startswith("pc_")]

    assert pc_chunks
    assert all(chunk["metadata"].get("canonical_product_key") for chunk in pc_chunks)


def test_v3_index_target_owns_a_separate_bm25_state(monkeypatch):
    monkeypatch.delenv("MILVUS_V3_BM25_STATE_PATH", raising=False)

    legacy_collection, legacy_state_path = resolve_index_target(v3=False)
    v3_collection, v3_state_path = resolve_index_target(v3=True)

    assert legacy_collection
    assert legacy_state_path is None
    assert v3_collection == "mallmind_product_evidence_v3"
    assert v3_state_path == ROOT_DIR / "data" / "bm25_state_v3.json"
