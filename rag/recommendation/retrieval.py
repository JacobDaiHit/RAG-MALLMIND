"""Milvus-backed product evidence retrieval for shopping recommendation scoring.

The recommender stays deterministic around catalog products, prices, and cards.
Milvus is only an optional evidence layer over ecommerce product chunks.
"""
from __future__ import annotations

import os
import logging
import socket
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Tuple

from dotenv import load_dotenv

from rag.schemas import ComponentCategory, RequirementSpec
from rag.utils.runtime_errors import is_debug_mode, public_error, sanitize_report

load_dotenv()

DEFAULT_TOP_K_PER_COMPONENT = int(os.getenv("RECOMMENDATION_RETRIEVAL_TOP_K", "12"))
DEFAULT_MAX_QUERY_VARIANTS = int(os.getenv("RECOMMENDATION_QUERY_VARIANTS", "3"))
MILVUS_CONNECT_TIMEOUT_SECONDS = float(os.getenv("MILVUS_CONNECT_TIMEOUT_SECONDS", "0.75"))
LEAF_RETRIEVE_LEVEL = int(os.getenv("LEAF_RETRIEVE_LEVEL", "3"))
QUERY_EXPANSION_ENABLED = os.getenv("RECOMMENDATION_QUERY_EXPANSION", "true").lower() != "false"
RAG_POSTPROCESS_ENABLED = os.getenv("RECOMMENDATION_RAG_POSTPROCESS", "true").lower() != "false"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueryVariant:
    """One retrieval query generated for a component."""

    kind: str
    query: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalEvidence:
    """Retrieved product snippets grouped by product id."""

    by_product_id: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    by_category: Dict[str, int] = field(default_factory=dict)
    total_hits: int = 0
    status: str = "disabled"
    error: str = ""
    query_variants: List[Dict[str, Any]] = field(default_factory=list)
    postprocess: List[Dict[str, Any]] = field(default_factory=list)
    query_expansion_enabled: bool = False

    def snippets_for(self, product_id: str) -> List[Dict[str, Any]]:
        """Return retrieved snippets for one catalog product."""
        return list(self.by_product_id.get(product_id, []))

    def to_trace(self) -> Dict[str, Any]:
        provider = os.getenv("EMBEDDING_PROVIDER", "local")
        model = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
        dim = os.getenv("EMBEDDING_DIM") or os.getenv("DENSE_EMBEDDING_DIM", "1024")
        collection = os.getenv("MILVUS_COLLECTION", "embeddings_collection")
        raw_hit_count = sum(_safe_int(item.get("raw_hits")) or 0 for item in self.postprocess)
        before_postprocess = sum(_safe_int(item.get("retrieved_chunk_count_before_postprocess") or item.get("raw_hits")) or 0 for item in self.postprocess)
        after_postprocess = sum(_safe_int(item.get("retrieved_chunk_count_after_postprocess") or item.get("final_hits")) or 0 for item in self.postprocess)
        filters = _dedupe_strings(str(item.get("retrieval_filter") or item.get("filter_expr") or "") for item in self.postprocess)
        retrieval_queries = _dedupe_strings(str(item.get("retrieval_query") or "") for item in self.postprocess)
        postprocess_errors = [
            str(item.get("rag_postprocess_error") or item.get("rerank_error") or "")
            for item in self.postprocess
            if item.get("rag_postprocess_error") or item.get("rerank_error")
        ]
        return sanitize_report({
            "status": self.status,
            "rag_used": self.status in {"ok", "empty", "partial"},
            "retrieval_backend": "milvus" if self.status in {"ok", "empty", "partial", "failed", "timeout", "no_collection"} else "structured_catalog",
            "retrieval_query": retrieval_queries[0] if retrieval_queries else "",
            "retrieval_queries": retrieval_queries,
            "retrieval_filters": filters,
            "embedding_provider": provider,
            "embedding_model": model,
            "embedding_dim": _safe_int(dim),
            "milvus_collection": collection,
            "milvus_raw_hit_count": raw_hit_count,
            "retrieved_chunk_count_before_postprocess": before_postprocess,
            "retrieved_chunk_count_after_postprocess": after_postprocess,
            "retrieved_chunk_count": self.total_hits,
            "total_hits": self.total_hits,
            "hits_by_category": self.by_category,
            "matched_product_ids": sorted(self.by_product_id.keys()),
            "retrieved_product_ids": sorted(self.by_product_id.keys()),
            "error": self.error,
            "retrieval_error": self.error,
            "retrieval_timeout": self.status == "timeout",
            "postprocess_error": "; ".join(postprocess_errors),
            "auto_merge_status": _auto_merge_status(self.postprocess),
            "query_expansion_enabled": self.query_expansion_enabled,
            "rag_postprocess_enabled": RAG_POSTPROCESS_ENABLED,
            "query_variants": self.query_variants,
            "postprocess": self.postprocess,
        })


class EvidenceRetriever:
    """Retrieve grounded product evidence for recommendation scoring."""

    def __init__(
        self,
        *,
        manager: Any | None = None,
        embedding_service: Any | None = None,
        top_k_per_component: int = DEFAULT_TOP_K_PER_COMPONENT,
        max_query_variants: int = DEFAULT_MAX_QUERY_VARIANTS,
        use_query_expansion: bool = False,
        use_rag_postprocess: bool = RAG_POSTPROCESS_ENABLED,
    ) -> None:
        self.manager = manager
        self.embedding_service = embedding_service
        self.top_k_per_component = top_k_per_component
        self.max_query_variants = max(1, max_query_variants)
        self.use_query_expansion = use_query_expansion
        self.use_rag_postprocess = use_rag_postprocess

    def retrieve(
        self,
        requirement: RequirementSpec,
        categories: Iterable[ComponentCategory],
    ) -> RetrievalEvidence:
        """Retrieve optional Milvus evidence for the requested categories."""
        category_list = list(categories)
        if not category_list:
            return RetrievalEvidence(status="skipped", query_expansion_enabled=self.use_query_expansion)

        by_product_id: Dict[str, List[Dict[str, Any]]] = {}
        by_category: Dict[str, int] = {}
        query_variants_trace: List[Dict[str, Any]] = []
        postprocess_trace: List[Dict[str, Any]] = []
        total_hits = 0

        try:
            manager = self.manager
            if manager is None:
                from rag.storage.milvus_client import MilvusManager

                manager = MilvusManager()

            if not _milvus_port_available(manager):
                return RetrievalEvidence(
                    status="unavailable",
                    error=(
                        f"Milvus {getattr(manager, 'host', 'localhost')}:{getattr(manager, 'port', '19530')} is not reachable."
                        if is_debug_mode()
                        else "milvus_unavailable"
                    ),
                    query_expansion_enabled=self.use_query_expansion,
                )

            if not manager.has_collection():
                return RetrievalEvidence(status="no_collection", query_expansion_enabled=self.use_query_expansion)

            embedding_service = self.embedding_service
            if embedding_service is None:
                from rag.ingestion.embedding import embedding_service as default_embedding_service

                embedding_service = default_embedding_service

            for category in category_list:
                category_hits: List[Dict[str, Any]] = []
                for variant in self._build_query_variants(requirement, category):
                    query_variants_trace.append(
                        {
                            "component": category.value,
                            "kind": variant.kind,
                            "retrieval_query": variant.query,
                            "query_preview": variant.query[:240],
                            **variant.metadata,
                        }
                    )
                    hits, meta = self._retrieve_variant(
                        manager=manager,
                        embedding_service=embedding_service,
                        category=category,
                        variant=variant,
                    )
                    postprocess_trace.append(meta)
                    category_hits.extend(hits)

                compacted = _dedupe_hits(category_hits)
                by_category[category.value] = len(compacted)
                total_hits += len(compacted)
                for hit in compacted:
                    product_id = (hit.get("product_id") or "").strip()
                    if not product_id:
                        continue
                    by_product_id.setdefault(product_id, []).append(hit)
        except Exception as exc:
            logger.exception("Product evidence retrieval failed")
            return RetrievalEvidence(
                by_product_id=by_product_id,
                by_category=by_category,
                total_hits=total_hits,
                status="failed" if total_hits == 0 else "partial",
                error=public_error(exc, fallback="retrieval_failed"),
                query_variants=query_variants_trace,
                postprocess=postprocess_trace,
                query_expansion_enabled=self.use_query_expansion,
            )

        return RetrievalEvidence(
            by_product_id=by_product_id,
            by_category=by_category,
            total_hits=total_hits,
            status="ok" if total_hits else "empty",
            query_variants=query_variants_trace,
            postprocess=postprocess_trace,
            query_expansion_enabled=self.use_query_expansion,
        )

    def _build_query_variants(
        self,
        requirement: RequirementSpec,
        category: ComponentCategory,
    ) -> List[QueryVariant]:
        """推荐证据检索器：构造 query variants，把分散数据组织成可复用结果。"""
        base_query = _build_component_query(requirement, category)
        variants = [QueryVariant(kind="base", query=base_query)]
        if not self.use_query_expansion:
            return variants

        step_back = _safe_step_back_expand(base_query)
        expanded_query = (step_back.get("expanded_query") or "").strip()
        if expanded_query and expanded_query != base_query:
            variants.append(
                QueryVariant(
                    kind="step_back",
                    query=expanded_query,
                    metadata={
                        "step_back_question": step_back.get("step_back_question", ""),
                    },
                )
            )

        hypothetical_doc = _safe_generate_hypothetical_document(base_query)
        if hypothetical_doc:
            variants.append(
                QueryVariant(
                    kind="hyde",
                    query=hypothetical_doc,
                    metadata={"hypothetical_doc_preview": hypothetical_doc[:240]},
                )
            )

        return variants[: self.max_query_variants]

    def _retrieve_variant(
        self,
        *,
        manager: Any,
        embedding_service: Any,
        category: ComponentCategory,
        variant: QueryVariant,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """推荐证据检索器：检索 variant，为回答或推荐提供上下文证据。"""
        candidate_k = max(self.top_k_per_component * 3, self.top_k_per_component)
        filter_expr = _build_component_filter(category)
        meta: Dict[str, Any] = {
            "component": category.value,
            "query_kind": variant.kind,
            "retrieval_query": variant.query,
            "candidate_k": candidate_k,
            "filter_expr": filter_expr,
            "retrieval_filter": filter_expr,
            "retrieval_mode": "unknown",
            "raw_hits": 0,
            "retrieved_chunk_count_before_postprocess": 0,
            "retrieved_chunk_count_after_postprocess": 0,
        }

        dense_embedding = embedding_service.get_embeddings([variant.query])[0]
        sparse_embedding = embedding_service.get_sparse_embedding(variant.query)
        try:
            hits = manager.hybrid_retrieve(
                dense_embedding=dense_embedding,
                sparse_embedding=sparse_embedding,
                top_k=candidate_k,
                filter_expr=filter_expr,
            )
            meta["retrieval_mode"] = "hybrid"
        except Exception as exc:
            logger.warning("Hybrid retrieval failed; falling back to dense retrieval: %s", exc)
            meta["hybrid_error"] = public_error(exc, fallback="dense_fallback")
            try:
                hits = manager.dense_retrieve(
                    dense_embedding=dense_embedding,
                    top_k=candidate_k,
                    filter_expr=filter_expr,
                )
                meta["retrieval_mode"] = "dense_fallback"
            except Exception as dense_exc:
                logger.exception("Dense fallback retrieval failed")
                meta["dense_error"] = public_error(dense_exc, fallback="dense_fallback_failed")
                meta["retrieval_mode"] = "failed"
                meta["retrieval_error"] = meta["dense_error"]
                return [], meta

        meta["raw_hits"] = len(hits)
        meta["milvus_raw_hit_count"] = len(hits)
        meta["retrieved_chunk_count_before_postprocess"] = len(hits)
        processed, post_meta = self._apply_rag_postprocess(variant.query, hits)
        meta.update(post_meta)

        compacted = []
        for hit in processed:
            item = _compact_hit(hit, fallback_category=category.value)
            item["query_kind"] = variant.kind
            item["retrieval_mode"] = meta["retrieval_mode"]
            compacted.append(item)
        meta["final_hits"] = len(compacted)
        meta["retrieved_chunk_count_after_postprocess"] = len(compacted)
        return compacted, meta

    def _apply_rag_postprocess(
        self,
        query: str,
        hits: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """推荐证据检索器：应用 rag postprocess 规则或后处理，更新中间结果。"""
        meta: Dict[str, Any] = {
            "rag_postprocess_applied": False,
            "rag_postprocess_error": "",
        }
        if not hits or not self.use_rag_postprocess:
            return hits[: self.top_k_per_component], meta

        try:
            from rag.utils.retrieval_postprocess import _auto_merge_documents, _rerank_documents

            reranked, rerank_meta = _rerank_documents(
                query=query,
                docs=hits,
                top_k=self.top_k_per_component,
            )
            merged, merge_meta = _auto_merge_documents(
                docs=reranked,
                top_k=self.top_k_per_component,
            )
            meta.update(rerank_meta)
            meta.update(merge_meta)
            meta["rag_postprocess_applied"] = bool(
                rerank_meta.get("rerank_applied") or merge_meta.get("auto_merge_applied")
            )
            return merged, meta
        except Exception as exc:
            logger.warning("RAG postprocess failed; using raw hits: %s", exc)
            meta["rag_postprocess_error"] = public_error(exc, fallback="rag_postprocess_failed")
            return hits[: self.top_k_per_component], meta


def retrieve_requirement_evidence(
    requirement: RequirementSpec,
    categories: Iterable[ComponentCategory],
    top_k_per_component: int = DEFAULT_TOP_K_PER_COMPONENT,
    manager: Any | None = None,
    use_query_expansion: bool = False,
) -> RetrievalEvidence:
    """Retrieve product evidence snippets from Milvus for each requested category.

    The recommender still works when Milvus is unavailable: failures are captured in
    trace metadata and the static product scorer remains the fallback.
    """

    return EvidenceRetriever(
        manager=manager,
        top_k_per_component=top_k_per_component,
        use_query_expansion=use_query_expansion,
    ).retrieve(requirement, categories)


def _build_component_query(requirement: RequirementSpec, category: ComponentCategory) -> str:
    """推荐证据检索器：构造 component query，把分散数据组织成可复用结果。"""
    parts = [
        requirement.raw_query,
        requirement.scenario,
        requirement.task_type,
        category.value,
        " ".join(requirement.languages),
        " ".join(requirement.input_modalities),
        " ".join(requirement.output_modalities),
    ]
    if requirement.budget_level:
        parts.append(f"budget {requirement.budget_level.value}")
    return "\n".join(item for item in parts if item)


def _build_component_filter(category: ComponentCategory) -> str:
    # Milvus boolean expressions support C-style conjunctions. Dynamic metadata
    # fields are available because the collection schema enables dynamic fields.
    """推荐证据检索器：构造 component filter，把分散数据组织成可复用结果。"""
    return f'chunk_level == {LEAF_RETRIEVE_LEVEL} && category == "{category.value}"'


def _compact_hit(hit: Dict[str, Any], fallback_category: str = "") -> Dict[str, Any]:
    """Keep only the hit fields that are useful to scoring and tracing."""
    text = (hit.get("text") or "").strip()
    filename = hit.get("filename", "")
    product_id = (
        hit.get("product_id")
        or _infer_product_id_from_filename(filename)
    ).strip()
    return {
        "product_id": product_id,
        "filename": filename,
        "chunk_type": hit.get("chunk_type") or hit.get("doc_type", ""),
        "doc_type": hit.get("doc_type") or hit.get("chunk_type", ""),
        "category": hit.get("category", "") or fallback_category,
        "brand": hit.get("brand", ""),
        "title": hit.get("title", ""),
        "chunk_id": hit.get("chunk_id", ""),
        "score": float(hit.get("score") or 0.0),
        "text": text[:500],
    }


def evidence_summary(snippets: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """Return compact evidence ids and human-readable evidence lines."""

    evidence_ids: List[str] = []
    evidence_lines: List[str] = []
    seen = set()
    for snippet in snippets:
        doc_id = (
            snippet.get("chunk_id")
            or f"{snippet.get('filename', '')}:{snippet.get('doc_type', '')}"
            or snippet.get("product_id", "")
        )
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        evidence_ids.append(doc_id)
        label = snippet.get("title") or snippet.get("filename") or snippet.get("product_id") or "商品证据"
        chunk_type = snippet.get("chunk_type") or snippet.get("doc_type") or "chunk"
        score = snippet.get("score", 0.0)
        evidence_lines.append(f"{label}#{chunk_type} 召回分 {score:.4f}")
        if len(evidence_ids) >= 3:
            break
    return evidence_ids, evidence_lines


def _safe_step_back_expand(query: str) -> Dict[str, Any]:
    try:
        from rag.utils.rag_utils import step_back_expand

        return step_back_expand(query)
    except Exception:
        logger.warning("Step-back query expansion failed", exc_info=True)
        return {}


def _safe_generate_hypothetical_document(query: str) -> str:
    try:
        from rag.utils.rag_utils import generate_hypothetical_document

        return (generate_hypothetical_document(query) or "").strip()
    except Exception:
        logger.warning("HyDE query expansion failed", exc_info=True)
        return ""


def _dedupe_hits(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for hit in hits:
        key = (
            hit.get("product_id"),
            hit.get("chunk_id"),
            hit.get("filename"),
            hit.get("doc_type"),
            hit.get("chunk_type"),
            hit.get("text"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(hit)
    return deduped


def _infer_product_id_from_filename(filename: str) -> str:
    """Infer product id from legacy filename layouts."""
    normalized = str(filename or "").replace("\\", "/")
    if "/" not in normalized:
        return normalized.strip()
    return normalized.split("/", 1)[0].strip()


def _milvus_port_available(manager: Any) -> bool:
    host = str(getattr(manager, "host", "") or "localhost")
    raw_port = str(getattr(manager, "port", "") or "19530")
    try:
        port = int(raw_port)
    except ValueError:
        return True

    try:
        with socket.create_connection((host, port), timeout=MILVUS_CONNECT_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def _safe_int(value: Any) -> int | None:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _dedupe_strings(items: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _auto_merge_status(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "not_run"
    if any(item.get("rag_postprocess_error") for item in items):
        return "error"
    if any(item.get("auto_merge_applied") for item in items):
        return "applied"
    if any("auto_merge_applied" in item for item in items):
        return "not_applied"
    return "unknown"
