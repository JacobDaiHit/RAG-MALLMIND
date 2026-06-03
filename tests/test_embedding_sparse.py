from pathlib import Path

from rag.ingestion import embedding


class DummyDenseEmbedder:
    def embed_documents(self, texts):
        return [[0.0, 1.0] for _ in texts]


def test_sparse_embedding_never_returns_empty_vector(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(embedding, "_create_dense_embedder", lambda: DummyDenseEmbedder())
    service = embedding.EmbeddingService(state_path=tmp_path / "bm25_state.json")

    sparse = service.get_sparse_embedding("12345 !!!")

    assert sparse
    assert all(value > 0 for value in sparse.values())


def test_tokenize_keeps_model_version_numbers(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(embedding, "_create_dense_embedder", lambda: DummyDenseEmbedder())
    service = embedding.EmbeddingService(state_path=tmp_path / "bm25_state.json")

    assert service.tokenize("Doubao Seed 2.0 Lite") == ["doubao", "seed", "2", "0", "lite"]


def test_reset_state_clears_bm25_statistics(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(embedding, "_create_dense_embedder", lambda: DummyDenseEmbedder())
    service = embedding.EmbeddingService(state_path=tmp_path / "bm25_state.json")

    service.increment_add_documents(["Milvus hybrid search"])
    service.reset_state()

    assert service._total_docs == 0
    assert service._sum_token_len == 0
    assert service._vocab == {}


def test_embedding_service_does_not_load_dense_model_on_init(monkeypatch, tmp_path: Path):
    def fail_create_dense_embedder():
        raise AssertionError("dense embedder should be loaded lazily")

    monkeypatch.setattr(embedding, "_create_dense_embedder", fail_create_dense_embedder)

    service = embedding.EmbeddingService(state_path=tmp_path / "bm25_state.json")
    sparse = service.get_sparse_embedding("only sparse")

    assert sparse


def test_query_sparse_embedding_does_not_expand_vocab(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(embedding, "_create_dense_embedder", lambda: DummyDenseEmbedder())
    service = embedding.EmbeddingService(state_path=tmp_path / "bm25_state.json")
    service.increment_add_documents(["known token"])
    vocab_before = dict(service._vocab)

    sparse = service.get_sparse_embedding("brand_new_query_term")

    assert sparse
    assert service._vocab == vocab_before
