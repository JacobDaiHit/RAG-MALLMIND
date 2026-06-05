"""Compact session memory helpers for multi-turn recommendation context."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


RECENT_TURN_LIMIT = 8


def record_turn(
    session: Any,
    *,
    role: str,
    content: str,
    tool_name: str = "",
    selected_runtime_mode: str = "",
    requirement_delta: Optional[Dict[str, Any]] = None,
    selected_product_ids: Optional[List[str]] = None,
    cart_delta: Optional[Dict[str, Any]] = None,
    failure_type: str = "",
) -> Dict[str, Any]:
    entry = {
        "role": role,
        "content": str(content or ""),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool_name": tool_name,
        "selected_runtime_mode": selected_runtime_mode,
        "requirement_delta": dict(requirement_delta or {}),
        "selected_product_ids": [str(item) for item in (selected_product_ids or []) if str(item).strip()],
        "cart_delta": dict(cart_delta or {}),
        "failure_type": failure_type,
    }
    turns = list(getattr(session, "recent_turns", []) or [])
    turns.append(entry)
    overflow = turns[:-RECENT_TURN_LIMIT]
    session.recent_turns = turns[-RECENT_TURN_LIMIT:]
    if overflow:
        session.recent_turns_summary = compact_turns(getattr(session, "recent_turns_summary", ""), overflow)
    if failure_type:
        session.failure_state = {"type": failure_type, "timestamp": entry["timestamp"], "content": entry["content"][:160]}
    return entry


def compact_turns(existing_summary: str, turns: Iterable[Dict[str, Any]]) -> str:
    parts = [existing_summary.strip()] if existing_summary else []
    for turn in turns:
        tool = turn.get("tool_name") or "unknown"
        req = turn.get("requirement_delta") or {}
        ids = turn.get("selected_product_ids") or []
        fragment = f"{turn.get('role', '')}:{tool}"
        if req:
            fragment += f" req={_short_dict(req)}"
        if ids:
            fragment += f" ids={','.join(ids[:3])}"
        if turn.get("failure_type"):
            fragment += f" failure={turn.get('failure_type')}"
        parts.append(fragment)
    summary = " | ".join(part for part in parts if part)
    return summary[-1200:]


def requirement_to_delta(requirement: Any) -> Dict[str, Any]:
    if requirement is None:
        return {}
    data = requirement.model_dump(mode="json") if hasattr(requirement, "model_dump") else dict(requirement or {})
    keep = [
        "raw_query",
        "scenario",
        "task_type",
        "desired_categories",
        "target_sub_categories",
        "brands",
        "must_have_terms",
        "preferences",
        "price_min",
        "price_max",
        "need_bundle",
        "need_comparison",
        "need_multimodal",
    ]
    return {key: data.get(key) for key in keep if data.get(key) not in (None, "", [], {})}


def merge_requirement_memory(session: Any, requirement: Any, message: str) -> Dict[str, Any]:
    current = requirement_to_delta(requirement)
    previous = dict(getattr(session, "last_requirement", {}) or {})
    if _starts_new_topic(message, current, previous):
        merged = current
    else:
        merged = {**previous, **current}
        for key in ("desired_categories", "target_sub_categories", "brands", "must_have_terms", "preferences"):
            merged[key] = _dedupe([*(previous.get(key) or []), *(current.get(key) or [])])
        if current.get("price_max") is None and previous.get("price_max") is not None:
            merged["price_max"] = previous["price_max"]
    session.last_requirement = merged
    return merged


def session_context_for_llm(session: Any) -> Dict[str, Any]:
    """Return compact, bounded memory safe to pass to LLM prompts."""

    return {
        "last_requirement": dict(getattr(session, "last_requirement", {}) or {}),
        "recent_turns_summary": str(getattr(session, "recent_turns_summary", "") or ""),
        "recent_turns": list(getattr(session, "recent_turns", []) or [])[-RECENT_TURN_LIMIT:],
        "last_result_product_ids": _last_result_ids(getattr(session, "last_result", {}) or {}),
        "failure_state": dict(getattr(session, "failure_state", {}) or {}),
    }


def _starts_new_topic(message: str, current: Dict[str, Any], previous: Dict[str, Any]) -> bool:
    text = str(message or "")
    if not previous:
        return True
    old_categories = set(previous.get("desired_categories") or [])
    new_categories = set(current.get("desired_categories") or [])
    if old_categories and new_categories and old_categories.isdisjoint(new_categories):
        return True
    switch_terms = ["再推荐一款手机", "换个品类", "重新推荐", "另一个品类"]
    return any(term in text for term in switch_terms)


def _last_result_ids(result: Dict[str, Any]) -> List[str]:
    cards = result.get("product_cards") or []
    if cards:
        return [str(card.get("product_id")) for card in cards if card.get("product_id")][:3]
    plans = result.get("plans") or []
    ids: List[str] = []
    for plan in plans:
        for component in plan.get("components") or []:
            product = component.get("product") or {}
            if product.get("product_id"):
                ids.append(str(product["product_id"]))
    return ids[:3]


def _short_dict(value: Dict[str, Any]) -> str:
    return ",".join(f"{key}={value[key]}" for key in sorted(value)[:5])[:240]


def _dedupe(items: Iterable[Any]) -> List[Any]:
    result: List[Any] = []
    seen = set()
    for item in items:
        key = str(item)
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result
