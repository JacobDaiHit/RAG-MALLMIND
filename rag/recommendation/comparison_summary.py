"""Natural-language summaries for grounded product comparisons."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from rag.recommendation.llm_client import LLMClientError, OpenAICompatibleChatClient
from rag.security.prompt_guard import defense_prefix, defense_suffix, wrap_user_input


logger = logging.getLogger(__name__)


def summarize_comparison(query: str, compare_result: Dict[str, Any]) -> str:
    rows = compare_result.get("rows") or []
    if not rows:
        return ""

    client = OpenAICompatibleChatClient()
    if client.configured:
        prompt = {
            "query": query,
            "rows": rows,
            "recommendation": compare_result.get("recommendation") or {},
            "fact_check_issues": compare_result.get("fact_check_issues") or [],
        }
        messages = [
            {
                "role": "system",
                "content": (
                    f"{defense_prefix()}\n"
                    "你是电商导购比较助手。只能基于 rows 里的商品信息总结，不要编造跑分、官网价或未提供参数。"
                    "如果用户问性能但 rows 缺少直接性能参数，就用芯片、定位、价格、描述等已给证据谨慎比较。"
                    "输出 2-4 句中文结论。"
                    f"\n{defense_suffix()}"
                ),
            },
            {"role": "user", "content": wrap_user_input(json.dumps(prompt, ensure_ascii=False), max_len=6000)},
        ]
        try:
            text = client.chat_text(messages, temperature=0.1, max_tokens=500).strip()
            if text:
                return text
        except LLMClientError as exc:
            logger.debug("comparison summary LLM failed: %s", exc)

    return _fallback_summary(rows, compare_result.get("recommendation") or {})


def _fallback_summary(rows: List[Dict[str, Any]], recommendation: Dict[str, Any]) -> str:
    names = "、".join(str(row.get("title") or row.get("product_id")) for row in rows[:3])
    winner = recommendation.get("title") or recommendation.get("product_id")
    if winner:
        return f"已为你对比 {names}。综合商品库里的价格、评价和详情完整度，当前更倾向 {winner}；具体差异可以看下方对比表。"
    return f"已为你对比 {names}，具体差异可以看下方对比表。"

