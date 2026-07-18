"""Generate bounded non-shopping replies with no business-state write access.

``execute_general_chat`` is used only after V3 decides the turn has no commerce
operation. It calls the configured chat model with a short conversational
prompt, emits SSE text, and deliberately cannot access catalog, SessionCore,
cart, or recommendation executors.
"""
from __future__ import annotations

from typing import Any, Iterable

from rag.api.sse import sse_event
from rag.recommendation.llm_client import LLMClientError, OpenAICompatibleChatClient, run_with_hard_timeout

from .config import SEMANTIC_PARSE_TIMEOUT_SECONDS


def execute_general_chat(*, session: Any, message: str) -> Iterable[str]:
    client = OpenAICompatibleChatClient()
    if not client.configured:
        yield sse_event("error", {"label": "闲聊服务暂不可用", "detail": "当前未配置文本模型。"})
        yield sse_event("done", {"session_id": session.session_id})
        return
    try:
        text, report = run_with_hard_timeout(
            lambda: client.chat_text_with_report(
                [
                    {"role": "system", "content": "你是电商导购应用的闲聊助手。简洁回答；不要编造商品价格、库存、SKU 或下单结果。"},
                    {"role": "user", "content": message},
                ],
                model=client.config.fast_model,
                temperature=0.2,
                max_tokens=240,
            ),
            SEMANTIC_PARSE_TIMEOUT_SECONDS,
            "v3_general_chat",
        )
    except (TimeoutError, LLMClientError, OSError, ConnectionError, ValueError, TypeError):
        yield sse_event("error", {"label": "闲聊服务暂不可用", "detail": "文本模型当前没有返回可用结果。"})
        yield sse_event("done", {"session_id": session.session_id})
        return
    usage = report.usage or {}
    yield sse_event(
        "model_usage",
        {
            "purpose": "general_chat",
            "prompt_tokens": _token_count(usage, "prompt_tokens", "input_tokens"),
            "completion_tokens": _token_count(usage, "completion_tokens", "output_tokens"),
            "total_tokens": _token_count(usage, "total_tokens"),
            "elapsed_ms": report.elapsed_ms,
        },
    )
    yield sse_event("delta", {"text": text})
    yield sse_event("done", {"session_id": session.session_id})


def _token_count(usage: dict[str, Any], *names: str) -> int | None:
    """Return one non-negative provider token count without exposing payloads."""

    for name in names:
        value = usage.get(name)
        if isinstance(value, bool):
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            return parsed
    return None
