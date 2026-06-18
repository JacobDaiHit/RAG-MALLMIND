"""Natural-language response diversifier — LLM-first, template-fallback.

Inserts between ``remember_recommendation`` and SSE delta yield to replace
the hardcoded ``build_chat_delta_lines`` output with varied, human-sounding
replies while never fabricating product facts.
"""
from __future__ import annotations

import logging
import os
import random
import re
from typing import Any, Dict, List, Optional

from rag.recommendation.llm_client import (
    LLMClientError,
    OpenAICompatibleChatClient,
    get_llm_provider_trace,
    run_with_hard_timeout,
)
from rag.utils.runtime_errors import public_error

logger = logging.getLogger(__name__)

# ── 模板变体库 ──

_OPENING_VARIANTS = [
    "帮你筛了一遍商品库，",
    "我在商品库里找到了这些，",
    "按你的需求筛了一下，",
    "本地商品库里匹配到了，",
    "我从上架商品里挑了几款，",
    "【v2】我筛选了一下商品库，",
]

_LEAD_VARIANTS = [
    "最推荐 {title}，参考价约 {price:g} CNY。",
    "首推 {title}，大概 {price:g} 块。",
    "{title} 挺适合你的，{price:g} 左右。",
    "我觉得 {title} 不错，约 {price:g} CNY。",
    "优先看看 {title}，{price:g} CNY 性价比很高。",
]

_LEAD_NO_PRICE = [
    "最推荐 {title}。",
    "首推 {title}，各方面匹配度很高。",
    "我觉得 {title} 很适合你。",
]

_TAIL_VARIANTS = [
    "下面保留了候选卡片，可以对比或加购物车～",
    "还有几个备选在卡片里，你可以翻翻看。",
    "候选商品卡片就在下面，方便对比挑选。",
    "更多选择在下方卡片里，随时可以对比。",
]

_NO_MATCH_VARIANTS = [
    "这次没有找到足够匹配的商品，可以换个关键词或调一下预算再试试。",
    "商品库里暂时没有完全符合的，调整一下条件再搜搜？",
    "没找到特别贴合的，要不要放宽预算或者换个品类看看？",
]

_BUDGET_OVER_VARIANTS = [
    "商品库里没有 {budget:g} CNY 内足够相关的候选，下面给出同类最近备选。",
    "{budget:g} 以内暂时没找到合适的，看看这几款接近的吧。",
]

_BRAND_MISS_VARIANTS = [
    "没有找到 {brands} 品牌的在售商品，下面推荐了其他品牌的候选。",
    "{brands} 品牌目前缺货，先看看这些替代品吧。",
]


def _pick(variants: List[str], **kwargs: Any) -> str:
    return random.choice(variants).format(**kwargs)


# ── 主入口 ──


def generate_natural_response(
    payload: Dict[str, Any],
    session: Any = None,
    message: str = "",
) -> List[str]:
    """Return a list of natural-language lines (one per SSE delta event).

    Tries LLM first; falls back to templated variants on any failure.
    """
    # ── 0 卡片短路：直接返回 _NO_MATCH_VARIANTS 模板，不走 LLM ──
    # 防止 LLM 生成"挑了几款"之类的措辞而实际卡片数为 0。
    cards = payload.get("product_cards") or []
    if not cards and not payload.get("pc_build_plan"):
        text = random.choice(_NO_MATCH_VARIANTS)
        budget_info = payload.get("budget_info") or {}
        if budget_info.get("over_budget"):
            try:
                text = _pick(_BUDGET_OVER_VARIANTS, budget=budget_info.get("budget", 0))
            except (KeyError, ValueError):
                pass
        return [text]

    # 🟢 条件：LLM 可用 且 事实校验未降级
    fc = payload.get("fact_check") or {}
    use_llm = (
        os.getenv("RECOMMENDATION_RESPONSE_LLM", "true").strip().lower() in {"1", "true", "yes", "on"}
        and fc.get("degraded") is not True
    )

    if use_llm:
        try:
            llm_text = _llm_diverse_response(payload, message)
            if llm_text:
                return [llm_text]
        except Exception as exc:
            logger.debug("LLM response generator failed, using template: %s", exc)

    return [naturalize_response(payload)]


# ── LLM 生成 ──

_RESPONSE_PROMPT = """你是友好的电商导购助手。根据以下事实数据，用自然、有人情味的语言回复用户。

【用户需求】: {message}
【推荐商品】: {products}
【预算】: {budget}
【无匹配原因】: {no_match}

【约束】:
1. 绝对不能编造商品名称、价格、库存。所有提及的商品和价格必须来自上面的数据。
2. 2-3句话，不超过120字。
3. 语气自然，像真人导购，不用"根据你的需求""推荐理由如下""我先按你的需求"等套路句式。
4. 如果超预算或没有品牌匹配，友好提醒。
5. 如果没有匹配商品，简短说明原因并建议调整条件。

只输出回复文本，不要引号或前缀。"""


def _llm_diverse_response(payload: Dict[str, Any], message: str) -> Optional[str]:
    client = OpenAICompatibleChatClient()
    if not client.configured:
        return None

    cards = payload.get("product_cards") or []
    products_text = _format_products(cards)
    budget = _extract_budget(payload)
    no_match = _extract_no_match(payload)

    prompt = _RESPONSE_PROMPT.format(
        message=message[:300],
        products=products_text or "（无商品）",
        budget=str(budget) if budget else "未指定",
        no_match=no_match or "无",
    )

    model = os.getenv("RECOMMENDATION_RESPONSE_MODEL") or client.config.fast_model or client.config.model
    timeout_s = float(os.getenv("RECOMMENDATION_RESPONSE_TIMEOUT_SECONDS", "5.0"))

    try:
        text, _report = run_with_hard_timeout(
            lambda: client.chat_text_with_report(
                [{"role": "user", "content": prompt}],
                model=model,
                temperature=0.9,
                max_tokens=200,
            ),
            timeout_s,
            "response_generator",
        )
    except (TimeoutError, LLMClientError, Exception):
        return None

    cleaned = str(text or "").strip()[:300]
    if not cleaned or not _response_facts_are_allowed(cleaned, cards, budget):
        return None
    return cleaned


def _response_facts_are_allowed(text: str, cards: List[Dict[str, Any]], budget: Optional[float]) -> bool:
    """Reject generated claims that are not supported by the compact fact input."""

    # Stock and promotion data are deliberately absent from the response prompt.
    unsupported_claims = ("现货", "库存充足", "缺货", "售罄", "优惠券", "满减", "历史最低")
    if any(claim in text for claim in unsupported_claims):
        return False

    allowed_numbers = set()
    for card in cards:
        for key in ("price", "min_price", "max_price", "base_price"):
            value = card.get(key)
            if value is not None:
                try:
                    allowed_numbers.add(round(float(value), 2))
                except (TypeError, ValueError):
                    continue
    if budget is not None:
        allowed_numbers.add(round(float(budget), 2))

    price_patterns = re.findall(
        r"(?:[¥￥]\s*([\d,.]+)|([\d,.]+)\s*(?:元|块|CNY))",
        text,
        flags=re.IGNORECASE,
    )
    for groups in price_patterns:
        raw = next((item for item in groups if item), "").replace(",", "")
        try:
            mentioned = round(float(raw), 2)
        except ValueError:
            return False
        if mentioned not in allowed_numbers:
            return False
    return True


# ── 模板变体 ──


def naturalize_response(payload: Dict[str, Any]) -> str:
    """Build a varied template reply from payload data."""
    requirement = payload.get("requirement") or {}
    cards = payload.get("product_cards") or []
    plans = payload.get("plans") or []
    no_match = payload.get("trace", {}).get("no_match_reason", "")

    lines: List[str] = []

    # 无结果
    if not cards and not plans:
        lines.append(_pick(_NO_MATCH_VARIANTS))
        return " ".join(lines)

    # 有 PC 方案（仅当 recommendation_type 为 pc_build_plan 时）
    rec_type = payload.get("type", "")
    if rec_type == "pc_build_plan" and plans:
        summary = plans[0].get("summary", "") if plans else ""
        if summary:
            lines.append(summary)
        return " ".join(lines)

    # 有商品卡片
    lines.append(_pick(_OPENING_VARIANTS))

    # 品牌不匹配
    brands_missed = _extract_brands_missed(payload)
    if brands_missed:
        lines.append(_pick(_BRAND_MISS_VARIANTS, brands=brands_missed))

    # 预算超限
    budget_over = _extract_budget_over(payload)
    if budget_over:
        lines.append(_pick(_BUDGET_OVER_VARIANTS, budget=budget_over))

    # 主打商品
    if cards:
        lead = cards[0]
        title = lead.get("title", "")
        price = lead.get("price")
        if price is not None:
            lines.append(_pick(_LEAD_VARIANTS, title=title, price=price))
        else:
            lines.append(_pick(_LEAD_NO_PRICE, title=title))

    # 结尾
    if len(cards) > 1 or payload.get("comparison_table"):
        lines.append(_pick(_TAIL_VARIANTS))

    return " ".join(lines)


# ── 辅助函数 ──


def _format_products(cards: List[Dict[str, Any]]) -> str:
    if not cards:
        return ""
    parts = []
    for c in cards[:4]:
        title = c.get("title", "?")
        price = c.get("price")
        brand = c.get("brand", "")
        label = f"{brand} {title}" if brand else title
        if price is not None:
            label += f" (¥{price:g})"
        parts.append(label)
    return "\n".join(f"- {p}" for p in parts)


def _extract_budget(payload: Dict[str, Any]) -> Optional[float]:
    req = payload.get("requirement") or {}
    return req.get("price_max")


def _extract_no_match(payload: Dict[str, Any]) -> str:
    trace = payload.get("trace") or {}
    return trace.get("no_match_reason", "")


def _extract_brands_missed(payload: Dict[str, Any]) -> str:
    trace = payload.get("trace") or {}
    brand_list = trace.get("brands_not_found") or []
    if not brand_list:
        return ""
    return "、".join(str(b) for b in brand_list)


def _extract_budget_over(payload: Dict[str, Any]) -> Optional[float]:
    trace = payload.get("trace") or {}
    budget = trace.get("budget_gap_price_max")
    if budget is not None:
        return float(budget)
    return None
