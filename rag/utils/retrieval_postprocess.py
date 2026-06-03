"""Lightweight retrieval post-processing helpers.

This module intentionally avoids creating Milvus, embedding, or other networked
clients at import time. It only performs local post-processing, and lazily opens
the parent chunk store when auto-merge actually needs parent documents.
"""

from __future__ import annotations

from collections import defaultdict
import json
import logging
import os
from typing import Any, Dict, List, Tuple

import requests
from dotenv import load_dotenv

from rag.utils.runtime_errors import is_debug_mode, public_error, sanitize_report

load_dotenv()

RERANK_MODEL = os.getenv("RERANK_MODEL")
RERANK_BINDING_HOST = os.getenv("RERANK_BINDING_HOST")
RERANK_API_KEY = os.getenv("RERANK_API_KEY")
AUTO_MERGE_ENABLED = os.getenv("AUTO_MERGE_ENABLED", "true").lower() != "false"
AUTO_MERGE_THRESHOLD = int(os.getenv("AUTO_MERGE_THRESHOLD", "2"))
logger = logging.getLogger(__name__)

_parent_chunk_store = None


def get_parent_chunk_store():
    """Return the parent chunk store, creating it only when auto-merge needs it."""

    global _parent_chunk_store
    if _parent_chunk_store is None:
        from rag.storage.parent_chunk_store import ParentChunkStore

        _parent_chunk_store = ParentChunkStore()
    return _parent_chunk_store


def _get_rerank_endpoint() -> str:
    if not RERANK_BINDING_HOST:
        return ""
    host = RERANK_BINDING_HOST.strip().rstrip("/")
    return host if host.endswith("/v1/rerank") else f"{host}/v1/rerank"


def _merge_to_parent_level(docs: List[dict], threshold: int = 2) -> Tuple[List[dict], int]:
    groups: Dict[str, List[dict]] = defaultdict(list)
    for doc in docs:
        parent_id = (doc.get("parent_chunk_id") or "").strip()
        if parent_id:
            groups[parent_id].append(doc)

    merge_parent_ids = [parent_id for parent_id, children in groups.items() if len(children) >= threshold]
    if not merge_parent_ids:
        return docs, 0

    parent_docs = get_parent_chunk_store().get_documents_by_ids(merge_parent_ids)
    parent_map = {item.get("chunk_id", ""): item for item in parent_docs if item.get("chunk_id")}

    merged_docs: List[dict] = []
    merged_count = 0
    for doc in docs:
        parent_id = (doc.get("parent_chunk_id") or "").strip()
        if not parent_id or parent_id not in parent_map:
            merged_docs.append(doc)
            continue
        parent_doc = dict(parent_map[parent_id])
        score = doc.get("score")
        if score is not None:
            parent_doc["score"] = max(float(parent_doc.get("score", score)), float(score))
        parent_doc["merged_from_children"] = True
        parent_doc["merged_child_count"] = len(groups[parent_id])
        merged_docs.append(parent_doc)
        merged_count += 1

    deduped: List[dict] = []
    seen = set()
    for item in merged_docs:
        key = item.get("chunk_id") or (item.get("filename"), item.get("page_number"), item.get("text"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    return deduped, merged_count


def _auto_merge_documents(docs: List[dict], top_k: int) -> Tuple[List[dict], Dict[str, Any]]:
    if not AUTO_MERGE_ENABLED or not docs:
        return docs[:top_k], {
            "auto_merge_enabled": AUTO_MERGE_ENABLED,
            "auto_merge_applied": False,
            "auto_merge_threshold": AUTO_MERGE_THRESHOLD,
            "auto_merge_replaced_chunks": 0,
            "auto_merge_steps": 0,
        }

    merged_docs, merged_count_l3_l2 = _merge_to_parent_level(docs, threshold=AUTO_MERGE_THRESHOLD)
    merged_docs, merged_count_l2_l1 = _merge_to_parent_level(merged_docs, threshold=AUTO_MERGE_THRESHOLD)

    merged_docs.sort(key=lambda item: item.get("score", 0.0), reverse=True)
    merged_docs = merged_docs[:top_k]

    replaced_count = merged_count_l3_l2 + merged_count_l2_l1
    return merged_docs, {
        "auto_merge_enabled": AUTO_MERGE_ENABLED,
        "auto_merge_applied": replaced_count > 0,
        "auto_merge_threshold": AUTO_MERGE_THRESHOLD,
        "auto_merge_replaced_chunks": replaced_count,
        "auto_merge_steps": int(merged_count_l3_l2 > 0) + int(merged_count_l2_l1 > 0),
    }


def _rerank_documents(query: str, docs: List[dict], top_k: int) -> Tuple[List[dict], Dict[str, Any]]:
    docs_with_rank = [{**doc, "rrf_rank": i} for i, doc in enumerate(docs, 1)]
    meta: Dict[str, Any] = {
        "rerank_enabled": bool(RERANK_MODEL and RERANK_API_KEY and RERANK_BINDING_HOST),
        "rerank_applied": False,
        "rerank_error": None,
        "candidate_count": len(docs_with_rank),
    }
    if is_debug_mode():
        meta["rerank_model"] = RERANK_MODEL
        meta["rerank_endpoint"] = _get_rerank_endpoint()
    if not docs_with_rank or not meta["rerank_enabled"]:
        return docs_with_rank[:top_k], sanitize_report(meta)

    payload = {
        "model": RERANK_MODEL,
        "query": query,
        "documents": [doc.get("text", "") for doc in docs_with_rank],
        "top_n": min(top_k, len(docs_with_rank)),
        "return_documents": False,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {RERANK_API_KEY}",
    }

    try:
        meta["rerank_applied"] = True
        response = requests.post(
            _get_rerank_endpoint(),
            headers=headers,
            json=payload,
            timeout=15,
        )
        if response.status_code >= 400:
            logger.warning("Rerank API returned HTTP %s", response.status_code)
            meta["rerank_error"] = f"HTTP {response.status_code}: {response.text}" if is_debug_mode() else f"http_status_{response.status_code}"
            return docs_with_rank[:top_k], sanitize_report(meta)

        items = response.json().get("results", [])
        reranked = []
        for item in items:
            idx = item.get("index")
            if isinstance(idx, int) and 0 <= idx < len(docs_with_rank):
                doc = dict(docs_with_rank[idx])
                score = item.get("relevance_score")
                if score is not None:
                    doc["rerank_score"] = score
                reranked.append(doc)

        if reranked:
            return reranked[:top_k], sanitize_report(meta)

        meta["rerank_error"] = "empty_rerank_results"
        return docs_with_rank[:top_k], sanitize_report(meta)
    except Exception as exc:
        logger.warning("Rerank postprocess failed: %s", exc)
        meta["rerank_error"] = public_error(exc, fallback="rerank_failed")
        return docs_with_rank[:top_k], sanitize_report(meta)
