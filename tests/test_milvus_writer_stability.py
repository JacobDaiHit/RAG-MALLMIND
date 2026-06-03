import pytest

from rag.storage.milvus_writer import MilvusWriter


class FakeEmbeddingService:
    def __init__(self):
        self.reset_called = False
        self.incremented_texts = []

    def reset_state(self):
        self.reset_called = True

    def increment_add_documents(self, texts):
        self.incremented_texts.extend(texts)

    def get_all_embeddings(self, texts):
        return [[0.0, 1.0] for _ in texts], [{0: 1.0} for _ in texts]


class FailingMilvusManager:
    collection_name = "test_collection"

    def __init__(self):
        self.drop_called = False
        self.init_called = False

    def drop_collection(self):
        self.drop_called = True

    def init_collection(self):
        self.init_called = True

    def insert(self, insert_data):
        raise RuntimeError("insert failed")

    def flush(self):
        raise AssertionError("flush should not run after insert failure")


def test_milvus_writer_failure_reports_full_rebuild_required():
    embedding_service = FakeEmbeddingService()
    manager = FailingMilvusManager()
    writer = MilvusWriter(embedding_service=embedding_service, milvus_manager=manager)

    with pytest.raises(RuntimeError, match="Please rerun a full rebuild"):
        writer.write_documents(
            [{"text": "hello", "filename": "a.txt", "file_type": "txt"}],
            reset_bm25=True,
            drop_collection=True,
        )

    assert manager.drop_called is True
    assert manager.init_called is True
    assert embedding_service.reset_called is True
    assert embedding_service.incremented_texts == ["hello"]
