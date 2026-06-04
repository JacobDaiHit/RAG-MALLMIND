from __future__ import annotations

from pathlib import Path

import pytest

from rag.ingestion.embedding import (
    EmbeddingProviderError,
    EmbeddingService,
    LocalEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    create_embedding_provider,
)
from rag.storage.milvus_client import MilvusManager
from rag.storage.milvus_writer import MilvusWriter
from scripts import check_embedding_provider


class MockEmbeddings:
    def __init__(self, dim: int, calls: list[list[str]]):
        self.dim = dim
        self.calls = calls

    def create(self, *, model: str, input: list[str], dimensions: int):
        self.calls.append(list(input))
        return {"data": [{"embedding": [0.1] * dimensions} for _ in input]}


class MockClient:
    def __init__(self, dim: int = 4):
        self.calls: list[list[str]] = []
        self.embeddings = MockEmbeddings(dim, self.calls)


def test_provider_factory_defaults_to_local(monkeypatch):
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)

    provider = create_embedding_provider()

    assert isinstance(provider, LocalEmbeddingProvider)
    assert provider.provider == "local"


def test_provider_factory_builds_dashscope(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-v4")
    monkeypatch.setenv("EMBEDDING_DIM", "1024")

    provider = create_embedding_provider()

    assert isinstance(provider, OpenAICompatibleEmbeddingProvider)
    assert provider.provider == "dashscope"
    assert provider.model == "text-embedding-v4"
    assert provider.dim == 1024


def test_dashscope_mock_client_batches_and_returns_configured_dim():
    client = MockClient()
    provider = OpenAICompatibleEmbeddingProvider(
        provider="dashscope",
        model="text-embedding-v4",
        dim=4,
        batch_size=99,
        client=client,
    )

    vectors = provider.embed_texts([f"text {index}" for index in range(12)])

    assert [len(call) for call in client.calls] == [10, 2]
    assert len(vectors) == 12
    assert all(len(vector) == 4 for vector in vectors)


def test_dashscope_provider_rejects_dimension_mismatch():
    class BadEmbeddings:
        def create(self, *, model: str, input: list[str], dimensions: int):
            return {"data": [{"embedding": [0.1, 0.2]} for _ in input]}

    class BadClient:
        embeddings = BadEmbeddings()

    provider = OpenAICompatibleEmbeddingProvider(
        provider="dashscope",
        model="text-embedding-v4",
        dim=4,
        client=BadClient(),
    )

    with pytest.raises(EmbeddingProviderError, match="returned dimension 2, expected 4"):
        provider.embed_texts(["hello"])


def test_dashscope_missing_api_key_is_lazy(monkeypatch):
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    provider = OpenAICompatibleEmbeddingProvider(provider="dashscope", model="text-embedding-v4", dim=4)

    with pytest.raises(EmbeddingProviderError, match="requires EMBEDDING_API_KEY"):
        provider.embed_texts(["hello"])


def test_milvus_writer_rejects_provider_dim_mismatch(monkeypatch):
    monkeypatch.setenv("DENSE_EMBEDDING_DIM", "1024")
    service = EmbeddingService(state_path=Path(".pytest_tmp") / "bm25_state.json", provider=LocalEmbeddingProvider(dim=768))
    writer = MilvusWriter(embedding_service=service, milvus_manager=object())

    with pytest.raises(RuntimeError, match="Embedding dimension mismatch"):
        writer.write_documents([{"text": "hello", "filename": "a.txt", "file_type": "txt"}])


def test_milvus_existing_collection_dim_mismatch_requires_rebuild():
    class FakeClient:
        def describe_collection(self, *, collection_name: str):
            return {"fields": [{"name": "dense_embedding", "params": {"dim": 768}}]}

    manager = MilvusManager()

    with pytest.raises(ValueError, match="Rebuild the collection explicitly"):
        manager._validate_existing_dense_dim(FakeClient(), 1024)


def test_check_embedding_provider_dry_run(monkeypatch, capsys):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-v4")
    monkeypatch.setenv("EMBEDDING_DIM", "1024")

    assert check_embedding_provider.main(["--provider", "dashscope", "--dry-run"]) == 0
    output = capsys.readouterr().out

    assert '"provider": "dashscope"' in output
    assert '"status": "dry_run"' in output
