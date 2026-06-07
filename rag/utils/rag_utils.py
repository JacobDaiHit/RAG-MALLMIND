from collections import defaultdict
from typing import List, Tuple, Dict, Any
import os
import json
import logging
import requests

from langchain.chat_models import init_chat_model
from rag.utils.runtime_errors import is_debug_mode, public_error, sanitize_report

# Legacy compatibility: shared post-processing helpers are now canonical in
# rag.utils.retrieval_postprocess. Re-export them so older callers that still
# import from rag.utils.rag_utils keep working without duplicating code.

logger = logging.getLogger(__name__)

ARK_API_KEY = os.getenv("ARK_API_KEY")
MODEL = os.getenv("MODEL")
BASE_URL = os.getenv("BASE_URL")
RERANK_MODEL = os.getenv("RERANK_MODEL")
RERANK_BINDING_HOST = os.getenv("RERANK_BINDING_HOST")
RERANK_API_KEY = os.getenv("RERANK_API_KEY")
AUTO_MERGE_ENABLED = os.getenv("AUTO_MERGE_ENABLED", "true").lower() != "false"
AUTO_MERGE_THRESHOLD = int(os.getenv("AUTO_MERGE_THRESHOLD", "2"))
LEAF_RETRIEVE_LEVEL = int(os.getenv("LEAF_RETRIEVE_LEVEL", "3"))

# ── Delegate shared post-processing to the canonical module ──
from rag.utils.retrieval_postprocess import (  # noqa: E402
    _auto_merge_documents,
    _get_rerank_endpoint,
    _merge_to_parent_level,
    _rerank_documents,
    get_parent_chunk_store,
)

# 全局初始化检索依赖（与 api 共用 embedding_service，保证 BM25 状态一致）
_milvus_manager = None
_embedding_service = None
_stepback_model = None


def get_milvus_manager():
    global _milvus_manager
    if _milvus_manager is None:
        from rag.storage.milvus_client import MilvusManager

        _milvus_manager = MilvusManager()
    return _milvus_manager


def get_embedding_service():
    global _embedding_service
    if _embedding_service is None:
        from rag.ingestion.embedding import embedding_service

        _embedding_service = embedding_service
    return _embedding_service


def _get_stepback_model():
    """RAG 工具函数：获取 stepback model，屏蔽配置、缓存或外部依赖细节。"""
    global _stepback_model
    if not ARK_API_KEY or not MODEL:
        return None
    if _stepback_model is None:
        _stepback_model = init_chat_model(
            model=MODEL,
            model_provider="openai",
            api_key=ARK_API_KEY,
            base_url=BASE_URL,
            temperature=0.2,
        )
    return _stepback_model


def _generate_step_back_question(query: str) -> str:
    """RAG 工具函数：封装 generate step back question 相关逻辑，供上层流程复用。"""
    model = _get_stepback_model()
    if not model:
        return ""
    prompt = (
        "请将用户的具体问题抽象成更高层次、更概括的‘退步问题’，"
        "用于探寻背后的通用原理或核心概念。只输出退步问题一句话，不要解释。\n"
        f"用户问题：{query}"
    )
    try:
        return (model.invoke(prompt).content or "").strip()
    except Exception as exc:
        logger.warning("Step-back question generation failed: %s", exc)
        return ""


def _answer_step_back_question(step_back_question: str) -> str:
    """RAG 工具函数：封装 answer step back question 相关逻辑，供上层流程复用。"""
    model = _get_stepback_model()
    if not model or not step_back_question:
        return ""
    prompt = (
        "请简要回答以下退步问题，提供通用原理/背景知识，"
        "控制在120字以内。只输出答案，不要列出推理过程。\n"
        f"退步问题：{step_back_question}"
    )
    try:
        return (model.invoke(prompt).content or "").strip()
    except Exception as exc:
        logger.warning("Step-back answer generation failed: %s", exc)
        return ""


def generate_hypothetical_document(query: str) -> str:
    """RAG 工具函数：封装 generate hypothetical document 相关逻辑，供上层流程复用。"""
    model = _get_stepback_model()
    if not model:
        return ""
    prompt = (
        "请基于用户问题生成一段‘假设性文档’，内容应像真实资料片段，"
        "用于帮助检索相关信息。文档可以包含合理推测，但需与问题语义相关。"
        "只输出文档正文，不要标题或解释。\n"
        f"用户问题：{query}"
    )
    try:
        return (model.invoke(prompt).content or "").strip()
    except Exception as exc:
        logger.warning("HyDE document generation failed: %s", exc)
        return ""


def step_back_expand(query: str) -> dict:
    """RAG 工具函数：封装 step back expand 相关逻辑，供上层流程复用。"""
    step_back_question = _generate_step_back_question(query)
    step_back_answer = _answer_step_back_question(step_back_question)
    if step_back_question or step_back_answer:
        expanded_query = (
            f"{query}\n\n"
            f"退步问题：{step_back_question}\n"
            f"退步问题答案：{step_back_answer}"
        )
    else:
        expanded_query = query
    return {
        "step_back_question": step_back_question,
        "step_back_answer": step_back_answer,
        "expanded_query": expanded_query,
    }


def retrieve_documents(query: str, top_k: int = 5) -> Dict[str, Any]:
    """RAG 工具函数：检索 documents，为回答或推荐提供上下文证据。"""
    candidate_k = max(top_k * 3, top_k)
    filter_expr = f"chunk_level == {LEAF_RETRIEVE_LEVEL}"
    try:
        embedding_service = get_embedding_service()
        manager = get_milvus_manager()
        dense_embeddings = embedding_service.get_embeddings([query])
        dense_embedding = dense_embeddings[0]
        sparse_embedding = embedding_service.get_sparse_embedding(query)

        retrieved = manager.hybrid_retrieve(
            dense_embedding=dense_embedding,
            sparse_embedding=sparse_embedding,
            top_k=candidate_k,
            filter_expr=filter_expr,
        )
        reranked, rerank_meta = _rerank_documents(query=query, docs=retrieved, top_k=top_k)
        merged_docs, merge_meta = _auto_merge_documents(docs=reranked, top_k=top_k)
        rerank_meta["retrieval_mode"] = "hybrid"
        rerank_meta["candidate_k"] = candidate_k
        rerank_meta["leaf_retrieve_level"] = LEAF_RETRIEVE_LEVEL
        rerank_meta.update(merge_meta)
        return {"docs": merged_docs, "meta": sanitize_report(rerank_meta)}
    except Exception as exc:
        logger.warning("Hybrid document retrieval failed; trying dense fallback: %s", exc)
        try:
            embedding_service = get_embedding_service()
            manager = get_milvus_manager()
            dense_embeddings = embedding_service.get_embeddings([query])
            dense_embedding = dense_embeddings[0]
            retrieved = manager.dense_retrieve(
                dense_embedding=dense_embedding,
                top_k=candidate_k,
                filter_expr=filter_expr,
            )
            reranked, rerank_meta = _rerank_documents(query=query, docs=retrieved, top_k=top_k)
            merged_docs, merge_meta = _auto_merge_documents(docs=reranked, top_k=top_k)
            rerank_meta["retrieval_mode"] = "dense_fallback"
            rerank_meta["candidate_k"] = candidate_k
            rerank_meta["leaf_retrieve_level"] = LEAF_RETRIEVE_LEVEL
            rerank_meta.update(merge_meta)
            return {"docs": merged_docs, "meta": sanitize_report(rerank_meta)}
        except Exception as fallback_exc:
            logger.exception("Document retrieval failed")
            return {
                "docs": [],
                "meta": sanitize_report({
                    "rerank_enabled": bool(RERANK_MODEL and RERANK_API_KEY and RERANK_BINDING_HOST),
                    "rerank_applied": False,
                    "rerank_error": public_error(fallback_exc, fallback="retrieve_failed"),
                    "retrieval_mode": "failed",
                    "candidate_k": candidate_k,
                    "leaf_retrieve_level": LEAF_RETRIEVE_LEVEL,
                    "auto_merge_enabled": AUTO_MERGE_ENABLED,
                    "auto_merge_applied": False,
                    "auto_merge_threshold": AUTO_MERGE_THRESHOLD,
                    "auto_merge_replaced_chunks": 0,
                    "auto_merge_steps": 0,
                    "candidate_count": 0,
                }),
            }
