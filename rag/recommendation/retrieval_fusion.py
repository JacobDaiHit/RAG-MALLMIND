"""Hybrid retrieval fusion: combine rule-filtered candidates with vector recall.

Integrates vector search (via Milvus) as a supplementary recall path alongside
the deterministic filter chain.  Results are merged with Reciprocal Rank Fusion
(RRF) to produce a unified, deduplicated candidate list before scoring.

Design principles:
- Vector retrieval is **additive**: it supplements, never replaces, deterministic filtering.
- All failures are caught and gracefully degraded: on any error, the rule-filtered
  list is returned unchanged.
- Trace metadata records fusion statistics for observability.
"""
from __future__ import annotations

import logging
import os
import socket
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from rag.schemas import ApiProduct, ComponentCategory, RequirementSpec

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

VECTOR_RECALL_ENABLED = os.getenv(
    "RECOMMENDATION_VECTOR_RECALL",
    os.getenv("RECOMMENDATION_ENABLE_MILVUS", "false"),
).lower() == "true"

VECTOR_RECALL_TOP_K = int(os.getenv("RECOMMENDATION_VECTOR_RECALL_TOP_K", "20"))
RRF_K = int(os.getenv("RECOMMENDATION_RRF_K", "60"))
VECTOR_RECALL_WEIGHT = float(os.getenv("RECOMMENDATION_VECTOR_RECALL_WEIGHT", "0.4"))
RULE_FILTER_WEIGHT = float(os.getenv("RECOMMENDATION_RULE_FILTER_WEIGHT", "0.6"))
MILVUS_CONNECT_TIMEOUT_SECONDS = float(os.getenv("MILVUS_CONNECT_TIMEOUT_SECONDS", "0.75"))


@dataclass(frozen=True)
class FusionResult:
    """Result of fusing rule-filtered and vector-retrieved candidates."""

    fused_products: List[ApiProduct]
    rule_only_ids: Set[str] = field(default_factory=set)
    vector_only_ids: Set[str] = field(default_factory=set)
    overlap_ids: Set[str] = field(default_factory=set)
    rrf_scores: Dict[str, float] = field(default_factory=dict)
    vector_candidates_count: int = 0
    rule_candidates_count: int = 0
    status: str = "ok"
    error: str = ""

    def to_trace(self) -> Dict[str, Any]:
        return {
            "fusion_status": self.status,
            "fusion_error": self.error,
            "rule_candidates_count": self.rule_candidates_count,
            "vector_candidates_count": self.vector_candidates_count,
            "overlap_count": len(self.overlap_ids),
            "vector_only_count": len(self.vector_only_ids),
            "rule_only_count": len(self.rule_only_ids),
            "fused_count": len(self.fused_products),
            "rrf_k": RRF_K,
            "vector_recall_weight": VECTOR_RECALL_WEIGHT,
            "rule_filter_weight": RULE_FILTER_WEIGHT,
            "top_rrf_scores": {
                pid: round(score, 4)
                for pid, score in sorted(
                    self.rrf_scores.items(), key=lambda x: x[1], reverse=True
                )[:10]
            },
        }


def fuse_candidates(
    rule_filtered: List[ApiProduct],
    requirement: RequirementSpec,
    category: ComponentCategory,
    catalog_products: List[ApiProduct],
    *,
    enabled: bool = True,
    retrieved_product_ids: Optional[List[str]] = None,
) -> FusionResult:
    """Fuse rule-filtered candidates with vector-retrieved candidates.

    Parameters
    ----------
    rule_filtered:
        Products that passed the deterministic filter chain.
    requirement:
        The parsed shopping requirement.
    category:
        The target component category.
    catalog_products:
        Full catalog product list (for fetching vector-recalled products by ID).
    enabled:
        Master toggle; when False, returns rule-filtered unchanged.

    Returns
    -------
    FusionResult with fused product list and trace metadata.
    """
    if not enabled or not VECTOR_RECALL_ENABLED:
        return FusionResult(
            fused_products=rule_filtered,
            rule_only_ids={p.product_id for p in rule_filtered},
            rule_candidates_count=len(rule_filtered),
            status="disabled",
        )

    # ── Step 1: Vector recall ──
    if retrieved_product_ids is None:
        vector_products = _vector_recall(
            requirement=requirement,
            category=category,
            catalog_products=catalog_products,
        )
    else:
        catalog_index = {product.product_id: product for product in catalog_products}
        vector_products = [
            catalog_index[product_id]
            for product_id in retrieved_product_ids
            if product_id in catalog_index
        ]

    # Vector retrieval may improve ordering, but it must never bypass the
    # deterministic hard-constraint filter. Restrict recalled products to the
    # already validated candidate set before fusion.
    allowed_ids = {product.product_id for product in rule_filtered}
    vector_products = [product for product in vector_products if product.product_id in allowed_ids]

    if not vector_products:
        return FusionResult(
            fused_products=rule_filtered,
            rule_only_ids={p.product_id for p in rule_filtered},
            rule_candidates_count=len(rule_filtered),
            vector_candidates_count=0,
            status="vector_empty",
        )

    # ── Step 2: RRF fusion ──
    return _rrf_fuse(rule_filtered, vector_products)


def _vector_recall(
    requirement: RequirementSpec,
    category: ComponentCategory,
    catalog_products: List[ApiProduct],
) -> List[ApiProduct]:
    """Retrieve candidate products from Milvus vector search.

    Uses the existing Milvus infrastructure (milvus_client.py) and embedding
    service to search for product chunks matching the query, then maps
    retrieved product_ids back to catalog products.
    """
    try:
        from rag.storage.milvus_client import MilvusManager
        from rag.ingestion.embedding import embedding_service
    except ImportError:
        logger.debug("Milvus or embedding service not available for vector recall")
        return []

    # Quick connectivity check
    manager = MilvusManager()
    if not _milvus_port_available(manager):
        return []

    if not manager.has_collection():
        return []

    # Build retrieval query from requirement
    query = _build_vector_query(requirement, category)

    # Build Milvus filter expression
    filter_expr = _build_vector_filter(category, requirement)

    try:
        dense_embedding = embedding_service.get_embeddings([query])[0]
        sparse_embedding = embedding_service.get_sparse_embedding(query)

        hits = manager.hybrid_retrieve(
            dense_embedding=dense_embedding,
            sparse_embedding=sparse_embedding,
            top_k=VECTOR_RECALL_TOP_K * 2,
            filter_expr=filter_expr,
        )
    except Exception as hybrid_exc:
        logger.warning("Vector recall hybrid search failed, trying dense fallback: %s", hybrid_exc)
        try:
            dense_embedding = embedding_service.get_embeddings([query])[0]
            hits = manager.dense_retrieve(
                dense_embedding=dense_embedding,
                top_k=VECTOR_RECALL_TOP_K * 2,
                filter_expr=filter_expr,
            )
        except Exception as dense_exc:
            logger.warning("Vector recall dense fallback failed: %s", dense_exc)
            return []

    # Extract unique product_ids from hits, preserving rank order
    seen_ids: Set[str] = set()
    ranked_product_ids: List[str] = []
    for hit in hits:
        pid = (hit.get("product_id") or "").strip()
        if pid and pid not in seen_ids:
            seen_ids.add(pid)
            ranked_product_ids.append(pid)

    if not ranked_product_ids:
        return []

    # Map product_ids to actual catalog products
    catalog_index = {p.product_id: p for p in catalog_products}
    vector_products: List[ApiProduct] = []
    for pid in ranked_product_ids:
        product = catalog_index.get(pid)
        if product:
            vector_products.append(product)

    return vector_products[:VECTOR_RECALL_TOP_K]


def _build_vector_query(requirement: RequirementSpec, category: ComponentCategory) -> str:
    """Construct a retrieval query combining requirement fields."""
    parts = [requirement.raw_query]
    if requirement.brands:
        parts.append(" ".join(requirement.brands))
    if requirement.target_sub_categories:
        parts.append(" ".join(requirement.target_sub_categories))
    if requirement.must_have_terms:
        parts.append(" ".join(requirement.must_have_terms[:5]))
    return " ".join(parts)


def _build_vector_filter(category: ComponentCategory, requirement: RequirementSpec) -> str:
    """Build Milvus boolean filter for vector retrieval."""
    leaf_level = int(os.getenv("LEAF_RETRIEVE_LEVEL", "3"))
    conditions = [f'chunk_level == {leaf_level}']
    conditions.append(f'category == "{category.value}"')

    # Add brand filter if specified (narrow vector search scope)
    if requirement.brands and len(requirement.brands) <= 3:
        brand_conditions = []
        for brand in requirement.brands:
            brand_conditions.append(f'brand == "{brand}"')
        if brand_conditions:
            conditions.append("(" + " || ".join(brand_conditions) + ")")

    return " && ".join(conditions)


def _rrf_fuse(
    rule_filtered: List[ApiProduct],
    vector_products: List[ApiProduct],
) -> FusionResult:
    """Merge two ranked lists using Reciprocal Rank Fusion.

    RRF formula: score(d) = Σ 1 / (k + rank_i(d))
    where rank starts at 1 for the first item in each list.
    """
    rrf_scores: Dict[str, float] = {}
    rule_ids = {p.product_id for p in rule_filtered}
    vector_ids = {p.product_id for p in vector_products}

    # Score rule-filtered products (rank 1 = best, as they passed all hard filters)
    for rank, product in enumerate(rule_filtered, 1):
        pid = product.product_id
        score = RULE_FILTER_WEIGHT / (RRF_K + rank)
        rrf_scores[pid] = rrf_scores.get(pid, 0.0) + score

    # Score vector-recalled products
    for rank, product in enumerate(vector_products, 1):
        pid = product.product_id
        score = VECTOR_RECALL_WEIGHT / (RRF_K + rank)
        rrf_scores[pid] = rrf_scores.get(pid, 0.0) + score

    # Partition into overlap / rule-only / vector-only
    overlap_ids = rule_ids & vector_ids
    rule_only_ids = rule_ids - vector_ids
    vector_only_ids = vector_ids - rule_ids

    # Sort all candidates by RRF score (descending)
    all_ids_by_score = sorted(rrf_scores.keys(), key=lambda pid: rrf_scores[pid], reverse=True)

    # Build product lookup from both lists
    product_lookup: Dict[str, ApiProduct] = {}
    for product in rule_filtered:
        product_lookup[product.product_id] = product
    for product in vector_products:
        product_lookup[product.product_id] = product

    fused_products = [product_lookup[pid] for pid in all_ids_by_score if pid in product_lookup]

    return FusionResult(
        fused_products=fused_products,
        rule_only_ids=rule_only_ids,
        vector_only_ids=vector_only_ids,
        overlap_ids=overlap_ids,
        rrf_scores=rrf_scores,
        vector_candidates_count=len(vector_products),
        rule_candidates_count=len(rule_filtered),
        status="ok" if fused_products else "empty",
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

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
