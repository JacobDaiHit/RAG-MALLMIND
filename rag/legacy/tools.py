"""Legacy LangChain-style product evidence tools.

.. deprecated::
    This module is **deprecated** and will be removed in a future release.
    The production shopping flow does not use this module. The current main routing
    path is rag.recommendation.tool_router -> rag.recommendation.tool_handlers.
    Keep this file only for old experiments/tests.

    Known issues:
    - Uses unsynchronised global mutable state (_LAST_RAG_CONTEXT, etc.)
    - Not thread-safe under concurrent FastAPI requests.
"""

import warnings as _warnings

_warnings.warn(
    "rag.legacy.tools is deprecated; use rag.recommendation.tool_router instead.",
    DeprecationWarning,
    stacklevel=2,
)

from typing import Optional

from langchain_core.tools import tool

_LAST_RAG_CONTEXT = None
_PRODUCT_EVIDENCE_TOOL_CALLS_THIS_TURN = 0
_RAG_STEP_QUEUE = None  # asyncio.Queue, set by agent before streaming
_RAG_STEP_LOOP = None   # asyncio loop, captured when setting queue


def _set_last_rag_context(context: dict):
    """Agent 工具函数：设置 last rag context 状态，让后续调用共享该上下文。"""
    global _LAST_RAG_CONTEXT
    _LAST_RAG_CONTEXT = context


def get_last_rag_context(clear: bool = True) -> Optional[dict]:
    """获取最近一次 RAG 检索上下文，默认读取后清空。"""
    global _LAST_RAG_CONTEXT
    context = _LAST_RAG_CONTEXT
    if clear:
        _LAST_RAG_CONTEXT = None
    return context


def reset_tool_call_guards():
    """每轮对话开始时重置工具调用计数。"""
    global _PRODUCT_EVIDENCE_TOOL_CALLS_THIS_TURN
    _PRODUCT_EVIDENCE_TOOL_CALLS_THIS_TURN = 0


def set_rag_step_queue(queue):
    """设置 RAG 步骤队列，并捕获当前事件循环以便跨线程调度。"""
    global _RAG_STEP_QUEUE, _RAG_STEP_LOOP
    _RAG_STEP_QUEUE = queue
    if queue:
        import asyncio
        try:
            _RAG_STEP_LOOP = asyncio.get_running_loop()
        except RuntimeError:
            _RAG_STEP_LOOP = asyncio.get_event_loop()
    else:
        _RAG_STEP_LOOP = None


def emit_rag_step(icon: str, label: str, detail: str = ""):
    """向队列发送一个 RAG 检索步骤。支持跨线程安全调用。"""
    global _RAG_STEP_QUEUE, _RAG_STEP_LOOP
    if _RAG_STEP_QUEUE is not None and _RAG_STEP_LOOP is not None:
        step = {"icon": icon, "label": label, "detail": detail}
        try:
            if not _RAG_STEP_LOOP.is_closed():
                _RAG_STEP_LOOP.call_soon_threadsafe(_RAG_STEP_QUEUE.put_nowait, step)
        except Exception:
            pass


@tool("search_product_evidence")
def search_product_evidence(query: str) -> str:
    """Search the local ecommerce catalog for grounded product evidence."""

    global _PRODUCT_EVIDENCE_TOOL_CALLS_THIS_TURN
    if _PRODUCT_EVIDENCE_TOOL_CALLS_THIS_TURN >= 1:
        return (
            "TOOL_CALL_LIMIT_REACHED: product evidence search has already been called once in this turn. "
            "Use the existing product evidence and provide the final answer directly."
        )
    _PRODUCT_EVIDENCE_TOOL_CALLS_THIS_TURN += 1

    from rag.recommendation.product_loader import load_product_catalog

    emit_rag_step("🔎", "正在检索商品证据", query[:80])
    catalog = load_product_catalog()
    query_key = query.lower().strip()
    products = catalog.search_tags([query_key])[:6] if query_key else catalog.products[:6]

    _set_last_rag_context(
        {
            "product_evidence": {
                "query": query,
                "matched_product_ids": [product.product_id for product in products],
            }
        }
    )
    if not products:
        return "No matching products found in the local ecommerce catalog."

    formatted = []
    for i, product in enumerate(products, 1):
        formatted.append(
            "\n".join(
                [
                    f"[{i}] {product.product_id} {product.title}",
                    f"品牌: {product.brand}",
                    f"类目: {product.category_name} / {product.sub_category}",
                    f"价格: {product.min_price:g}-{product.max_price:g} {product.currency}",
                    f"标签: {'、'.join(product.tags[:8])}",
                    f"详情: {product.description[:260]}",
                ]
            )
        )

    emit_rag_step("✅", f"商品证据检索完成，命中 {len(products)} 个商品")
    return "Retrieved Product Evidence:\n" + "\n\n---\n\n".join(formatted)
