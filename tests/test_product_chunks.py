from rag.ingestion.product_chunks import build_catalog_chunks


def test_product_chunks_are_built_from_ecommerce_catalog():
    chunks = build_catalog_chunks()

    assert chunks
    assert {chunk["file_type"] for chunk in chunks} == {"ecommerce_product"}
    assert all(chunk["product_id"].startswith("p_") for chunk in chunks)
    assert all(chunk["text"] for chunk in chunks)


def test_product_chunks_include_sku_faq_and_review_evidence():
    chunks = build_catalog_chunks()
    chunk_types = {chunk["chunk_type"] for chunk in chunks}

    assert {"profile", "sku", "faq", "review"} <= chunk_types
