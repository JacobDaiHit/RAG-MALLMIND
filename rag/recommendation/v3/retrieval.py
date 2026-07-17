"""V3 Milvus retrieval constrained by catalog-validated RetrievalFilters."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from rag.ingestion.embedding import EmbeddingService
from rag.storage.milvus_client import MilvusManager

from .types import RetrievalEvidenceV3, RetrievalFilters


class V3EvidenceRetriever:
    """Retrieve evidence only inside the CandidateGate allowlist."""

    def __init__(self, *, manager: Any | None = None, embedding_service: Any | None = None, top_k: int = 18) -> None:
        self._manager = manager
        self._embedding_service = embedding_service
        self._top_k = top_k

    def retrieve(self, *, query: str, filters: RetrievalFilters) -> RetrievalEvidenceV3:
        if not filters.product_ids:
            return RetrievalEvidenceV3("empty", (), 0, "", "candidate_gate_empty")
        expression = _filter_expression(filters.product_ids)
        try:
            manager = self._manager or MilvusManager(collection_name=os.getenv("MILVUS_V3_COLLECTION", "mallmind_product_evidence_v3"))
            if not manager.has_collection():
                return RetrievalEvidenceV3("unavailable", (), 0, expression, "v3_collection_missing")
            service = self._embedding_service or EmbeddingService(state_path=_v3_bm25_state_path())
            dense, sparse = service.get_all_embeddings([query])
            hits = manager.hybrid_retrieve(dense[0], sparse[0], top_k=self._top_k, filter_expr=expression)
        except (RuntimeError, ValueError, OSError, ConnectionError, TimeoutError):
            return RetrievalEvidenceV3("unavailable", (), 0, expression, "v3_retrieval_failed")
        ordered = []
        for hit in hits:
            product_id = str(hit.get("product_id") or "")
            if product_id and product_id in filters.product_ids and product_id not in ordered:
                ordered.append(product_id)
        return RetrievalEvidenceV3("ok" if ordered else "empty", tuple(ordered), len(hits), expression)


def _v3_bm25_state_path() -> Path:
    value = Path(os.getenv("MILVUS_V3_BM25_STATE_PATH", "data/bm25_state_v3.json"))
    return value if value.is_absolute() else Path(__file__).resolve().parents[3] / value


def _filter_expression(product_ids: tuple[str, ...]) -> str:
    quoted = ", ".join(f'"{product_id}"' for product_id in product_ids)
    return f"chunk_level == 3 && product_id in [{quoted}]"
