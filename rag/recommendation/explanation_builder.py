"""Evidence-grounded explanation helpers for selected catalog products."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Optional

from rag.recommendation.llm_client import LLMClientError, OpenAICompatibleChatClient, run_with_hard_timeout
from rag.security.prompt_guard import defense_prefix, defense_suffix
from rag.utils.runtime_errors import public_error


ALLOWED_LLM_INPUT_FIELDS = {
    "user_need",
    "parsed_requirement",
    "selected_products",
    "title",
    "brand",
    "category",
    "sub_category",
    "price",
    "tags",
    "best_for",
    "not_good_for",
    "faq_summary",
    "review_summary",
    "score_breakdown",
    "evidence_chunks",
    "constraints_satisfied",
    "constraints_relaxed",
    "comparison_table",
}

EXPLANATION_OUTPUT_FIELDS = {
    "why_recommended",
    "evidence_points",
    "constraint_explanation",
    "tradeoff",
    "caveat",
}


def build_evidence_grounded_explanation(
    *,
    user_need: str,
    parsed_requirement: Dict[str, Any],
    selected_products: Iterable[Dict[str, Any]],
    comparison_table: Optional[List[Dict[str, Any]]] = None,
    use_llm: bool = False,
    timeout_seconds: float = 8.0,
) -> Dict[str, Any]:
    llm_input = build_llm_explanation_input(
        user_need=user_need,
        parsed_requirement=parsed_requirement,
        selected_products=selected_products,
        comparison_table=comparison_table,
    )
    # ── 标准化 explanation trace 字段 ──
    trace = {
        "llm_explanation_attempted": bool(use_llm),
        "llm_explanation_success": False,
        "llm_explanation_failure_reason": "",
    }
    if not use_llm:
        trace["llm_explanation_failure_reason"] = "llm_disabled"
        return {"mode": "template", "llm_input": llm_input, "explanation": template_explanation(llm_input), "_trace": trace}

    client = OpenAICompatibleChatClient()
    if not client.configured:
        trace["llm_explanation_failure_reason"] = "llm_not_configured"
        return {"mode": "fallback", "fallback_reason": "llm_not_configured", "llm_input": llm_input, "explanation": template_explanation(llm_input), "_trace": trace}

    try:
        payload = run_with_hard_timeout(
            lambda: client.chat_json(
                [
                    {"role": "system", "content": f"{defense_prefix('en')}\nOnly output strict JSON. Explain only from the provided evidence. Do not invent product facts.\n{defense_suffix('en')}"},
                    {"role": "user", "content": json.dumps(llm_input, ensure_ascii=False)},
                ],
                model=os.getenv("MALLMIND_GUIDANCE_MODEL") or client.config.fast_model,
                temperature=0.1,
                max_tokens=700,
            ),
            timeout_seconds,
            "evidence_explanation",
        )
        explanation = validate_explanation_output(payload)
        trace["llm_explanation_success"] = True
        return {"mode": "llm_evidence_grounded", "llm_input": llm_input, "explanation": explanation, "_trace": trace}
    except TimeoutError:
        trace["llm_explanation_failure_reason"] = "llm_timeout"
        return {"mode": "fallback", "fallback_reason": "llm_timeout", "llm_input": llm_input, "explanation": template_explanation(llm_input), "_trace": trace}
    except (LLMClientError, ValueError, TypeError, ConnectionError, PermissionError, OSError) as exc:
        text = str(exc).lower()
        if "timeout" in text or "timed out" in text:
            trace["llm_explanation_failure_reason"] = "llm_timeout"
        elif isinstance(exc, (ConnectionError, PermissionError, OSError)):
            trace["llm_explanation_failure_reason"] = "network_error"
        elif isinstance(exc, (ValueError, TypeError)):
            trace["llm_explanation_failure_reason"] = "llm_json_invalid"
        else:
            trace["llm_explanation_failure_reason"] = "llm_provider_error"
        return {"mode": "fallback", "fallback_reason": public_error(exc), "llm_input": llm_input, "explanation": template_explanation(llm_input), "_trace": trace}


def build_llm_explanation_input(
    *,
    user_need: str,
    parsed_requirement: Dict[str, Any],
    selected_products: Iterable[Dict[str, Any]],
    comparison_table: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    payload = {
        "user_need": str(user_need or ""),
        "parsed_requirement": _whitelist_requirement(parsed_requirement),
        "selected_products": [_whitelist_product(product) for product in selected_products],
    }
    if comparison_table:
        payload["comparison_table"] = [_whitelist_comparison_row(row) for row in comparison_table]
    return _drop_unknown_fields(payload)


def validate_explanation_output(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("explanation payload must be a JSON object")
    result = {
        "why_recommended": _string_list(payload.get("why_recommended")),
        "evidence_points": _string_list(payload.get("evidence_points")),
        "constraint_explanation": str(payload.get("constraint_explanation") or ""),
        "tradeoff": str(payload.get("tradeoff") or ""),
        "caveat": str(payload.get("caveat") or ""),
    }
    extra = set(payload) - EXPLANATION_OUTPUT_FIELDS
    if extra:
        result["ignored_fields"] = sorted(extra)
    return result


def template_explanation(llm_input: Dict[str, Any]) -> Dict[str, Any]:
    products = list(llm_input.get("selected_products") or [])
    names = [str(item.get("title") or item.get("product_id") or "") for item in products if item]
    evidence_points = []
    for item in products[:3]:
        bits = []
        if item.get("best_for"):
            bits.append("适合：" + "、".join(item["best_for"][:2]))
        if item.get("tags"):
            bits.append("标签：" + "、".join(item["tags"][:3]))
        if item.get("price") is not None:
            bits.append(f"价格：{item['price']}")
        if bits:
            evidence_points.append(f"{item.get('title', '候选商品')} - " + "；".join(bits))
    return {
        "why_recommended": [f"已从商品库候选中选择：{name}" for name in names[:3]],
        "evidence_points": evidence_points,
        "constraint_explanation": "商品卡片与解释均基于 catalog、结构化筛选和评分结果。",
        "tradeoff": "具体取舍请结合价格、适用场景和不适合场景查看。",
        "caveat": "未使用未提供的商品事实生成解释。",
    }


def _whitelist_requirement(requirement: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {
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
    }
    return {key: requirement.get(key) for key in allowed if requirement.get(key) not in (None, "", [], {})}


def _whitelist_product(product: Dict[str, Any]) -> Dict[str, Any]:
    score = product.get("score_breakdown") or product.get("score") or {}
    return {
        key: value
        for key, value in {
            "title": product.get("title"),
            "brand": product.get("brand"),
            "category": product.get("category"),
            "sub_category": product.get("sub_category"),
            "price": product.get("price") or product.get("min_price") or product.get("base_price"),
            "tags": product.get("tags") or [],
            "best_for": product.get("best_for") or [],
            "not_good_for": product.get("not_good_for") or [],
            "faq_summary": product.get("faq_summary") or product.get("faqs") or [],
            "review_summary": product.get("review_summary") or product.get("reviews") or [],
            "score_breakdown": score,
            "evidence_chunks": product.get("evidence_chunks") or product.get("evidence") or [],
            "constraints_satisfied": product.get("constraints_satisfied") or [],
            "constraints_relaxed": product.get("constraints_relaxed") or [],
        }.items()
        if value not in (None, "", [], {})
    }


def _whitelist_comparison_row(row: Dict[str, Any]) -> Dict[str, Any]:
    keys = ["title", "brand", "category", "sub_category", "price", "best_for", "not_good_for", "evidence"]
    return {key: row.get(key) for key in keys if row.get(key) not in (None, "", [], {})}


def _drop_unknown_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in payload.items() if key in ALLOWED_LLM_INPUT_FIELDS}


def _string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []
