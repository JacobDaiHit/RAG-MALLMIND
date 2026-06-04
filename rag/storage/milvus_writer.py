"""Vectorize ecommerce evidence chunks and write them into Milvus."""
from __future__ import annotations

import logging
import os

from rag.ingestion.embedding import EmbeddingService, embedding_service as _default_embedding_service
from rag.storage.milvus_client import MilvusManager


logger = logging.getLogger(__name__)


class MilvusWriter:
    """Batch writer for product chunks, with dense and BM25 sparse vectors."""

    def __init__(self, embedding_service: EmbeddingService = None, milvus_manager: MilvusManager = None):
        """初始化对象状态，保存后续方法会复用的配置、连接或依赖实例。"""
        self.embedding_service = embedding_service or _default_embedding_service
        self.milvus_manager = milvus_manager or MilvusManager()

    def write_documents(
        self,
        documents: list[dict],
        batch_size: int = 50,
        progress_callback=None,
        *,
        reset_bm25: bool = False,
        drop_collection: bool = False,
    ):
        """
        批量写入商品证据片段到 Milvus（同时生成密集和稀疏向量）
        :param documents: product evidence chunks
        :param batch_size: 批次大小
        """
        if not documents:
            return

        try:
            dense_dim = _embedding_service_dim(self.embedding_service)
            configured_dim = _parse_positive_int(os.getenv("DENSE_EMBEDDING_DIM", "1024"), default=1024)
            if dense_dim is not None and dense_dim != configured_dim:
                raise ValueError(
                    f"Embedding dimension mismatch: provider dim is {dense_dim}, "
                    f"but DENSE_EMBEDDING_DIM is {configured_dim}. Align the settings before indexing."
                )
            if drop_collection:
                self.milvus_manager.drop_collection()

            _init_collection(self.milvus_manager, dense_dim=dense_dim or configured_dim)

            all_texts = [doc["text"] for doc in documents]
            if reset_bm25:
                self.embedding_service.reset_state()
            self.embedding_service.increment_add_documents(all_texts)

            total = len(documents)
            for i in range(0, total, batch_size):
                batch = documents[i:i + batch_size]
                texts = [doc["text"] for doc in batch]

                # 同时生成密集向量和稀疏向量
                dense_embeddings, sparse_embeddings = self.embedding_service.get_all_embeddings(texts)

                insert_data = [
                    {
                        "dense_embedding": dense_emb,
                        "sparse_embedding": sparse_emb,
                        "text": doc["text"],
                        "filename": doc["filename"],
                        "file_type": doc["file_type"],
                        "file_path": doc.get("file_path", ""),
                        "page_number": doc.get("page_number", 0),
                        "chunk_idx": doc.get("chunk_idx", 0),
                        "chunk_id": doc.get("chunk_id", ""),
                        "parent_chunk_id": doc.get("parent_chunk_id", ""),
                        "root_chunk_id": doc.get("root_chunk_id", ""),
                        "chunk_level": doc.get("chunk_level", 0),
                        "product_id": doc.get("product_id", ""),
                        "title": doc.get("title", ""),
                        "brand": doc.get("brand", ""),
                        "chunk_type": doc.get("chunk_type") or doc.get("doc_type", ""),
                        "doc_type": doc.get("doc_type") or doc.get("chunk_type", ""),
                        "category": doc.get("category", ""),
                        "component_type": doc.get("component_type", ""),
                        "metadata": doc.get("metadata") or {},
                    }
                    for doc, dense_emb, sparse_emb in zip(batch, dense_embeddings, sparse_embeddings)
                ]

                self.milvus_manager.insert(insert_data)

                # 每个批次写入后更新进度，前端据此展示“向量化入库 xx%”。
                if progress_callback:
                    processed = min(i + batch_size, total)
                    progress_callback(processed, total)

            self.milvus_manager.flush()
        except Exception as exc:
            logger.exception("Milvus indexing failed")
            if "dimension mismatch" in str(exc).lower() or "dense_embedding dim" in str(exc):
                raise RuntimeError(str(exc)) from exc
            raise RuntimeError(
                "Milvus indexing failed after BM25 state may have been updated. "
                "Please rerun a full rebuild with reset_bm25=True and drop_collection=True."
            ) from exc


def _embedding_service_dim(embedding_service) -> int | None:
    dim = getattr(embedding_service, "dim", None)
    if dim is None:
        provider = getattr(embedding_service, "provider", None)
        dim = getattr(provider, "dim", None)
    if dim is None:
        return None
    try:
        return int(dim)
    except (TypeError, ValueError):
        return None


def _init_collection(milvus_manager, *, dense_dim: int) -> None:
    try:
        milvus_manager.init_collection(dense_dim=dense_dim)
    except TypeError as exc:
        if "dense_dim" not in str(exc):
            raise
        milvus_manager.init_collection()


def _parse_positive_int(value: object, default: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
