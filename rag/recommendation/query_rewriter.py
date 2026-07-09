"""Query rewriter for multi-turn shopping conversations.

Upgrades the naive string-concatenation in ``build_contextual_goal()`` with
pronoun resolution, attribute inheritance, and implicit condition completion.
Operates as "rule-first, LLM-fallback" to keep latency low.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from rag.security.prompt_guard import defense_prefix, defense_suffix, wrap_user_input

logger = logging.getLogger(__name__)


# ── Pronoun / deictic patterns ──────────────────────────────────────────────

_PRONOUN_PATTERNS = [
    # "这个怎么样" / "这款好吗" / "这两个对比一下"
    re.compile(r"^(?:这[个款两几](?:产品|商品|款|个)?(?:怎么样|好吗|好不好|如何|值不值|优缺点|对比|比较))"),
    # "它" / "他们"
    re.compile(r"^(?:它|他们|她们)(?:怎么样|好吗|好不好|如何|值不值|有什么优缺点)"),
    # "第一个" / "第二个"
    re.compile(r"^第[一二三四五六七八九十\d]+[个款]"),
]

# Short follow-up queries that almost certainly refer to previous context
_SHORT_FOLLOWUP_RE = re.compile(
    r"^(?:还有吗|再看看|再推荐|换一批|换几个|便宜点|贵一点|贵些|便宜些|便宜点的|贵点的|有货吗|库存多少|有白色的吗|有黑色的吗|有蓝色的吗|有粉色的吗|其他颜色|有没有其他)"
)

# Constraint modification signals → LLM rewrite may be needed
_CONSTRAINT_MOD_RE = re.compile(
    r"(?:换成|改成|不要|去掉|加上|换成|排除|除了|只要|只看)"
)

# Price-relative adjustment
_PRICE_ADJUST_RE = re.compile(
    r"(?:便宜[点些的]|贵[点些的]|再便宜|再贵|降[点些]|加点预算|预算[高低]|省[点些]钱)"
)

# Attribute-only follow-up (very short, likely inheriting context)
_ATTRIBUTE_ONLY_RE = re.compile(
    r"^(?:白色的?|黑色的?|蓝色的?|粉色的?|红色的?|绿色的?|银色的?|金色的?|灰色的?|紫色的?|橙色的?|大[一点小些]|小[一点小些]|轻[一点些]|重[一点些])[\s?？。！]*$"
)


def rewrite_query(
    message: str,
    session: Any,
    *,
    use_llm: bool = True,
) -> RewriteResult:
    """Rewrite user query with multi-turn context resolution.

    Returns a ``RewriteResult`` with the rewritten query and trace metadata.
    The caller should use ``result.query`` as the search query for retrieval
    and routing, while keeping the original ``message`` for display.
    """
    clean = " ".join(str(message or "").split())
    if not clean:
        return RewriteResult(query=clean, mode="empty")

    session_current: Dict[str, Any] = getattr(session, "current", {}) if session else {}
    last_result: Any = getattr(session, "last_result", None) if session else None
    last_goal: str = getattr(session, "last_goal", "") if session else ""

    # Fast path: clearly a new topic, no rewriting needed
    if not last_goal and not session_current:
        return RewriteResult(query=clean, mode="new_topic")

    # Fast path: long enough and self-contained
    if len(clean) > 20 and not _has_unresolved_pronoun(clean):
        return RewriteResult(query=clean, mode="self_contained")

    # ── Rule-based rewriting ──
    rule_result = _rule_based_rewrite(clean, session_current, last_result, last_goal)
    if rule_result and rule_result.query != clean:
        return rule_result

    # ── LLM rewriting (for complex cases) ──
    if use_llm and _needs_llm_rewrite(clean, session_current, last_result):
        llm_result = _llm_rewrite(clean, session_current, last_result, last_goal)
        if llm_result and llm_result.query != clean:
            return llm_result

    return RewriteResult(query=clean, mode="no_rewrite_needed")


class RewriteResult:
    """Result of query rewriting with trace metadata."""

    __slots__ = ("query", "mode", "rewrites_applied", "original")

    def __init__(
        self,
        query: str,
        mode: str = "unknown",
        rewrites_applied: Optional[List[str]] = None,
        original: str = "",
    ) -> None:
        self.query = query
        self.mode = mode
        self.rewrites_applied = rewrites_applied or []
        self.original = original

    def to_trace(self) -> Dict[str, Any]:
        return {
            "rewrite_mode": self.mode,
            "rewrites_applied": self.rewrites_applied,
            "rewritten_query": self.query,
            "original_query": self.original,
        }


# ── Rule-based rewriting ──────────────────────────────────────────────────

def _rule_based_rewrite(
    message: str,
    session_current: Dict[str, Any],
    last_result: Any,
    last_goal: str,
) -> Optional[RewriteResult]:
    """Apply deterministic rewrites for common follow-up patterns."""

    rewrites: List[str] = []
    rewritten = message

    # 1. Pronoun resolution: "这个怎么样" → "[product title] 怎么样"
    pronoun_resolved = _resolve_pronouns(rewritten, last_result)
    if pronoun_resolved != rewritten:
        rewrites.append("pronoun_resolution")
        rewritten = pronoun_resolved

    # 2. Attribute inheritance: "白色的" → "[category] [brand] 白色"
    if _ATTRIBUTE_ONLY_RE.match(rewritten):
        inherited = _inherit_attributes(rewritten, session_current, last_goal)
        if inherited != rewritten:
            rewrites.append("attribute_inheritance")
            rewritten = inherited

    # 3. Short follow-up: "还有吗" / "再推荐几个"
    if _SHORT_FOLLOWUP_RE.match(rewritten) and (session_current or last_goal):
        expanded = _expand_followup(rewritten, session_current, last_goal)
        if expanded != rewritten:
            rewrites.append("followup_expansion")
            rewritten = expanded

    # 4. Price-relative adjustment: "便宜点的" → inherit + price hint
    if _PRICE_ADJUST_RE.search(rewritten) and session_current:
        price_adjusted = _adjust_price_context(rewritten, session_current)
        if price_adjusted != rewritten:
            rewrites.append("price_adjustment")
            rewritten = price_adjusted

    if not rewrites:
        return None

    return RewriteResult(
        query=rewritten,
        mode="rule",
        rewrites_applied=rewrites,
        original=message,
    )


def _resolve_pronouns(message: str, last_result: Any) -> str:
    """Replace pronouns with actual product references from last result."""
    if not last_result:
        return message

    for pattern in _PRONOUN_PATTERNS:
        if not pattern.match(message):
            continue

        product_titles = _extract_last_product_titles(last_result)
        if not product_titles:
            return message

        # "这两个对比" → use first two titles
        if "两" in message[:6] or "几" in message[:6]:
            titles_text = "和".join(product_titles[:2])
        else:
            titles_text = product_titles[0]

        # Replace the pronoun prefix with the product title
        resolved = pattern.sub(lambda m: f"{titles_text} {m.group(0)[-3:]}", message)
        return resolved.strip()

    # Index-based: "第一个怎么样"
    idx_match = re.match(r"^第([一二三四五六七八九十\d]+)[个款]", message)
    if idx_match:
        idx = _cn_to_int(idx_match.group(1))
        product_titles = _extract_last_product_titles(last_result)
        if 0 < idx <= len(product_titles):
            title = product_titles[idx - 1]
            return f"{title} {message[idx_match.end():]}".strip()

    return message


def _inherit_attributes(
    message: str,
    session_current: Dict[str, Any],
    last_goal: str,
) -> str:
    """Inherit category, brand, and constraints from session for short queries."""
    parts: List[str] = []

    # Inherit sub_category / category context
    sub_cat = session_current.get("sub_category") or ""
    category = session_current.get("category") or ""
    if sub_cat:
        parts.append(sub_cat)
    elif category:
        parts.append(str(category))

    # Inherit brands
    brands = session_current.get("brands") or []
    if brands:
        parts.append(" ".join(brands))

    # The user's attribute query itself
    parts.append(message)

    # Inherit key constraints from last_goal (e.g. "预算500以内")
    if last_goal:
        budget_match = re.search(r"\d+(?:\.\d+)?(?:元|块|以内|以下|左右)", last_goal)
        if budget_match:
            parts.append(budget_match.group(0))

    return " ".join(p for p in parts if p)


def _expand_followup(
    message: str,
    session_current: Dict[str, Any],
    last_goal: str,
) -> str:
    """Expand short follow-up queries with inherited context."""
    base = last_goal.split(". User added constraints:")[0].strip() if last_goal else ""
    if not base:
        # Try to reconstruct from session_current
        base_parts: List[str] = []
        sub_cat = session_current.get("sub_category") or ""
        if sub_cat:
            base_parts.append(sub_cat)
        brands = session_current.get("brands") or []
        if brands:
            base_parts.append(" ".join(brands))
        base = " ".join(base_parts) if base_parts else ""

    if not base:
        return message

    return f"{base} {message}".strip()


def _adjust_price_context(
    message: str,
    session_current: Dict[str, Any],
) -> str:
    """Add inherited category/brand context to price-relative queries."""
    parts: List[str] = []
    sub_cat = session_current.get("sub_category") or ""
    if sub_cat:
        parts.append(sub_cat)
    brands = session_current.get("brands") or []
    if brands:
        parts.append(" ".join(brands))
    parts.append(message)
    return " ".join(parts)


# ── LLM rewriting ──────────────────────────────────────────────────────────

def _needs_llm_rewrite(
    message: str,
    session_current: Dict[str, Any],
    last_result: Any,
) -> bool:
    """Determine whether the query needs LLM-based rewriting."""
    if not session_current and not last_result:
        return False
    # Very short follow-up with pronouns that rules couldn't resolve
    if len(message) <= 10 and _has_unresolved_pronoun(message):
        return True
    # Constraint modification signals
    if _CONSTRAINT_MOD_RE.search(message):
        return True
    return False


def _llm_rewrite(
    message: str,
    session_current: Dict[str, Any],
    last_result: Any,
    last_goal: str,
) -> Optional[RewriteResult]:
    """Use LLM to rewrite ambiguous follow-up queries."""
    try:
        from rag.recommendation.llm_client import (
            OpenAICompatibleChatClient,
            run_with_hard_timeout,
        )
    except ImportError:
        return None

    client = OpenAICompatibleChatClient()
    if not client.configured:
        return None

    # Build context summary for the LLM
    context_lines: List[str] = []
    if last_goal:
        context_lines.append(f"上一轮用户查询: {last_goal}")
    if session_current:
        if session_current.get("sub_category"):
            context_lines.append(f"当前品类: {session_current['sub_category']}")
        if session_current.get("brands"):
            context_lines.append(f"当前品牌: {', '.join(session_current['brands'])}")
        price_max = session_current.get("price_max")
        if price_max is not None:
            context_lines.append(f"当前预算上限: {price_max}元")
    product_titles = _extract_last_product_titles(last_result)
    if product_titles:
        context_lines.append(f"上一轮推荐商品: {', '.join(product_titles[:3])}")

    context_text = "\n".join(context_lines) if context_lines else "无历史上下文"

    prompt = (
        f"{defense_prefix()}\n\n"
        "你是电商导购查询改写器。根据对话上下文，将用户的追问改写为一个完整、"
        "明确的搜索查询，用于商品检索。只输出改写后的查询文本，不要解释。\n\n"
        f"【对话上下文】\n{context_text}\n\n"
        f"{wrap_user_input(message, max_len=500)}\n\n"
        "改写要求：\n"
        "1. 将代词（这个、它、第一个等）替换为具体商品名称\n"
        "2. 补全继承的品类、品牌、预算等约束\n"
        "3. 保留用户本轮新增的条件（颜色、价格调整、排除等）\n"
        "4. 只输出一行改写文本\n\n"
        f"{defense_suffix()}"
    )

    try:
        _timeout = float(os.getenv("RECOMMENDATION_QUERY_REWRITE_TIMEOUT_SECONDS", "5"))
        payload = run_with_hard_timeout(
            lambda: client.chat_completion(
                [{"role": "user", "content": prompt}],
                model=os.getenv("MALLMIND_REWRITE_MODEL") or client.config.fast_model,
                temperature=0.1,
                max_tokens=200,
            ),
            _timeout,
            "query_rewrite",
        )
        # Extract text from the response
        rewritten = ""
        if isinstance(payload, str):
            rewritten = payload.strip()
        elif isinstance(payload, dict):
            choices = payload.get("choices") or []
            if choices:
                msg = choices[0].get("message") or {}
                rewritten = (msg.get("content") or "").strip()

        if rewritten and rewritten != message and len(rewritten) > len(message):
            return RewriteResult(
                query=rewritten,
                mode="llm",
                rewrites_applied=["llm_context_rewrite"],
                original=message,
            )
    except Exception as exc:
        logger.warning("LLM query rewrite failed: %s", exc)

    return None


# ── Shared helpers ──────────────────────────────────────────────────────────

def _has_unresolved_pronoun(message: str) -> bool:
    """Check if the message contains pronouns that likely need resolution."""
    pronouns = ["这个", "这款", "这两个", "这几个", "它", "他们", "第一个", "第二个", "第三个"]
    return any(p in message for p in pronouns)


def _extract_last_product_titles(last_result: Any) -> List[str]:
    """Extract product titles from the last recommendation result."""
    if not last_result:
        return []

    titles: List[str] = []

    # last_result might be a dict or a RecommendationResult
    cards: List[Dict[str, Any]] = []
    if isinstance(last_result, dict):
        cards = last_result.get("product_cards") or []
    elif hasattr(last_result, "product_cards"):
        cards = last_result.product_cards or []

    for card in cards[:5]:
        title = ""
        if isinstance(card, dict):
            title = card.get("title") or card.get("name") or ""
        elif hasattr(card, "title"):
            title = card.title or ""
        if title:
            titles.append(title)

    return titles


def _cn_to_int(value: str) -> int:
    """Convert Chinese number string to int."""
    mapping = {
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    }
    if value in mapping:
        return mapping[value]
    try:
        return int(value)
    except ValueError:
        return 0
