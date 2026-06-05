"""Evaluate MallMind typical user scenarios against the current catalog.

This script intentionally treats catalog and capability boundaries as first
class outcomes. It does not change recommendation logic, and it does not
special-case product IDs for individual cases.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rag.api.app_context import model_to_dict
from rag.recommendation.comparison import compare_products
from rag.recommendation.product_loader import ProductCatalog, load_combined_product_catalog, load_product_catalog
from rag.recommendation.recommendation_pipeline import recommend_shopping_products
from rag.recommendation.session_state import (
    ShoppingSession,
    apply_cart_instruction,
    build_contextual_goal,
    remember_recommendation,
    update_topic_memory,
)
from rag.recommendation.tool_router import route_shopping_tool_call
from rag.schemas import ApiProduct, ComponentCategory


DEFAULT_JSON = ROOT_DIR / "reports" / "user_scenarios_eval.json"
DEFAULT_MD = ROOT_DIR / "reports" / "user_scenarios_eval.md"
STATUSES = ("ok", "failed", "suspicious", "not_applicable")
FAILURE_TYPES = (
    "none",
    "business_failed",
    "catalog_gap",
    "budget_catalog_gap",
    "capability_gap",
    "capability_partial",
    "negative_guard",
    "eval_design_gap",
    "not_applicable",
    "script_error",
)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--use-llm", action="store_true", help="允许需求解析/路由使用 LLM。默认关闭，适合 CI。")
    parser.add_argument("--runtime-mode", choices=("fast", "balanced", "full"), default="balanced")
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MD)
    args = parser.parse_args(argv)

    started = time.perf_counter()
    catalog = load_product_catalog(use_cache=False)
    combined_catalog = load_combined_product_catalog(use_cache=False)
    catalog_summary = build_catalog_summary(catalog, combined_catalog)
    cases = user_scenario_cases()
    rows = [
        run_case(case, catalog, combined_catalog, use_llm=args.use_llm, runtime_mode=args.runtime_mode)
        for case in cases
    ]
    report = build_report(
        rows,
        catalog_summary,
        config={
            "use_llm": bool(args.use_llm),
            "runtime_mode": args.runtime_mode,
            "output_json": str(args.output_json),
            "output_md": str(args.output_md),
            "case_count": len(cases),
        },
        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
    )

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(render_markdown(report), encoding="utf-8")

    print(f"典型用户场景评估完成: {report['summary']['overall_status']}")
    print(f"JSON 报告: {args.output_json}")
    print(f"Markdown 报告: {args.output_md}")
    return 0


def user_scenario_cases() -> List[Dict[str, Any]]:
    return [
        {
            "case_id": "basic_oily_skin_sunscreen",
            "difficulty": "basic",
            "scenario_type": "single_turn_fuzzy_recommendation",
            "expected_type": "in_catalog_positive",
            "turns": ["推荐一款适合油皮夏天用的防晒"],
            "expected_tool": "recommend_shopping_products",
            "expected_categories": ["beauty"],
            "expected_product_predicates": ['category == beauty', '文本命中 "防晒"/"油皮"/"清爽"'],
            "catalog_probe": {
                "required_category": "beauty",
                "required_keywords_any": ["防晒", "油皮", "清爽"],
                "required_subcategory_keywords_any": ["防晒"],
            },
            "requires_rag": True,
        },
        {
            "case_id": "basic_under_200_sunscreen",
            "difficulty": "basic",
            "scenario_type": "conditional_filter",
            "expected_type": "auto",
            "turns": ["200 元以内适合夏天用的防晒有哪些？"],
            "expected_tool": "recommend_shopping_products",
            "expected_categories": ["beauty"],
            "expected_price_max": 200,
            "expected_product_predicates": ["category == beauty", "min_price/base_price <= 200", "文本命中防晒或夏季护肤词"],
            "catalog_probe": {
                "required_category": "beauty",
                "required_keywords_any": ["防晒", "夏天", "夏季"],
                "required_subcategory_keywords_any": ["防晒"],
                "price_max": 200,
            },
            "requires_rag": True,
        },
        {
            "case_id": "basic_pdf_example_cleanser",
            "difficulty": "basic",
            "scenario_type": "single_turn_fuzzy_recommendation",
            "expected_type": "auto",
            "turns": ["推荐一款适合油皮的洗面奶"],
            "expected_tool": "recommend_shopping_products",
            "expected_categories": ["beauty"],
            "expected_no_match_reason": "missing_subcategory",
            "catalog_probe": {
                "required_category": "beauty",
                "required_keywords_any": ["洗面奶", "洁面", "cleanser", "facial cleanser"],
                "required_subcategory_keywords_any": ["洗面奶", "洁面", "cleanser", "facial cleanser"],
            },
            "requires_rag": True,
        },
        {
            "case_id": "basic_pdf_example_under_200_earphones",
            "difficulty": "basic",
            "scenario_type": "conditional_filter",
            "expected_type": "auto",
            "turns": ["200 元以下的蓝牙耳机有哪些？"],
            "expected_tool": "recommend_shopping_products",
            "expected_categories": ["digital"],
            "expected_price_max": 200,
            "expected_no_match_reason": "missing_subcategory",
            "catalog_probe": {
                "required_category": "digital",
                "required_keywords_any": ["蓝牙耳机", "耳机"],
                "required_subcategory_keywords_any": ["蓝牙耳机", "耳机"],
                "price_max": 200,
            },
            "requires_rag": True,
        },
        {
            "case_id": "intermediate_running_shoes_multiturn",
            "difficulty": "intermediate",
            "scenario_type": "multiturn_refinement",
            "expected_type": "auto",
            "turns": ["帮我推荐跑鞋", "要轻量的", "预算 500 以内"],
            "expected_tool": "recommend_shopping_products",
            "expected_categories": ["clothing"],
            "expected_session_carryover": True,
            "expected_price_max": 500,
            "expected_product_predicates": ["category == clothing", "文本命中跑步鞋/跑鞋/轻量/缓震/透气"],
            "catalog_probe": {
                "required_category": "clothing",
                "required_keywords_any": ["跑步鞋", "跑鞋", "轻量", "缓震", "透气"],
                "required_subcategory_keywords_any": ["跑步鞋", "跑鞋"],
                "price_max": 500,
            },
            "requires_rag": True,
        },
        {
            "case_id": "intermediate_compare_cream",
            "difficulty": "intermediate",
            "scenario_type": "product_comparison",
            "expected_type": "in_catalog_or_route_positive",
            "turns": ["帮我比较两款面霜哪个更保湿"],
            "expected_tool": "compare_products",
            "expected_categories": ["beauty"],
            "catalog_probe": {
                "required_category": "beauty",
                "required_keywords_any": ["面霜", "保湿"],
                "required_subcategory_keywords_any": ["面霜"],
            },
            "requires_rag": True,
        },
        {
            "case_id": "intermediate_clarify_phone",
            "difficulty": "intermediate",
            "scenario_type": "proactive_clarification",
            "expected_type": "llm_needed_or_design_choice",
            "turns": ["推荐一款手机"],
            "expected_tool": "recommend_shopping_products",
            "expected_categories": ["digital"],
            "expected_clarification_required": True,
            "catalog_probe": {
                "required_category": "digital",
                "required_keywords_any": ["手机", "智能手机"],
                "required_subcategory_keywords_any": ["智能手机", "手机"],
            },
            "requires_llm": True,
        },
        {
            "case_id": "advanced_negative_sunscreen",
            "difficulty": "advanced",
            "scenario_type": "negative_constraints",
            "expected_type": "in_catalog_positive_or_filtered_empty",
            "turns": ["推荐防晒霜，但我不要含酒精的，也不要日系品牌"],
            "expected_tool": "recommend_shopping_products",
            "expected_categories": ["beauty"],
            "excluded_terms": ["酒精"],
            "excluded_brands_or_regions": ["日本", "日系"],
            "catalog_probe": {
                "required_category": "beauty",
                "required_keywords_any": ["防晒"],
                "required_subcategory_keywords_any": ["防晒"],
                "excluded_terms": ["酒精", "日本", "日系"],
            },
            "requires_rag": True,
        },
        {
            "case_id": "advanced_sanya_bundle",
            "difficulty": "advanced",
            "scenario_type": "scenario_bundle_recommendation",
            "expected_type": "in_catalog_positive",
            "turns": ["下周去三亚度假，帮我搭配一套从防晒到穿搭的方案，预算800以内"],
            "expected_tool": "recommend_shopping_products",
            "expected_component_roles": ["beauty", "clothing"],
            "expected_price_max": 800,
            "catalog_probe": {
                "required_category": "beauty",
                "required_keywords_any": ["防晒", "三亚", "度假", "穿搭", "帽子", "T恤", "背包"],
                "required_subcategory_keywords_any": ["防晒", "帽子", "T恤", "背包"],
                "price_max": 800,
            },
            "requires_rag": True,
        },
        {
            "case_id": "advanced_cart_crud",
            "difficulty": "advanced",
            "scenario_type": "cart_crud",
            "expected_type": "state_positive",
            "turns": ["推荐一款适合油皮夏天用的防晒", "把刚才那款加到购物车", "删掉第一个", "清空购物车"],
            "expected_tool": "apply_cart_instruction",
            "expected_categories": ["beauty"],
            "expected_cart_action": "add/remove/clear",
            "expected_cart_actions": ["add", "remove", "clear"],
            "catalog_probe": {
                "required_category": "beauty",
                "required_keywords_any": ["防晒", "油皮", "清爽"],
                "required_subcategory_keywords_any": ["防晒"],
            },
            "requires_rag": True,
        },
        {
            "case_id": "advanced_photo_same_jacket",
            "difficulty": "advanced",
            "scenario_type": "multimodal_photo_search",
            "expected_type": "capability_gap_or_catalog_gap",
            "turns": ["[image]", "我想要同款外套"],
            "expected_tool": "recommend_shopping_products",
            "expected_categories": ["clothing"],
            "expected_no_match_reason": "missing_subcategory",
            "catalog_probe": {
                "required_category": "clothing",
                "required_keywords_any": ["外套", "冲锋衣", "防风外套"],
                "required_subcategory_keywords_any": ["外套", "冲锋衣", "防风外套"],
            },
            "requires_multimodal": True,
        },
    ]


def run_case(
    case: Dict[str, Any],
    catalog: ProductCatalog,
    combined_catalog: ProductCatalog,
    *,
    use_llm: bool,
    runtime_mode: str,
) -> Dict[str, Any]:
    probe = probe_catalog(catalog, case.get("catalog_probe") or {})
    row = base_case_row(case, probe)
    row["effective_expected_type"] = effective_expected_type(case, probe)
    use_milvus = runtime_mode == "full"
    try:
        with stable_eval_environment(runtime_mode):
            if case["scenario_type"] == "cart_crud":
                execution = run_cart_case(case, catalog, use_llm=use_llm, use_milvus=use_milvus)
            elif case["scenario_type"] == "multiturn_refinement":
                execution = run_multiturn_case(case, use_llm=use_llm, use_milvus=use_milvus)
            elif case["scenario_type"] == "product_comparison":
                execution = run_comparison_case(case, combined_catalog, use_llm=use_llm)
            elif case["scenario_type"] == "multimodal_photo_search":
                execution = run_multimodal_boundary_case(case, probe)
            else:
                execution = run_recommendation_case(case, use_llm=use_llm, use_milvus=use_milvus)
    except Exception as exc:  # keep the matrix reportable
        execution = {
            "error": f"{type(exc).__name__}: {exc}",
            "route_result": {},
            "recommended_products": [],
            "component_roles": [],
            "candidate_count": 0,
            "trace_summary": {},
        }

    row.update(execution)
    status, failure_type, reason = judge_case(case, row, probe, use_llm=use_llm)
    row["status"] = status
    row["failure_type"] = failure_type
    row["failure_reason"] = reason
    return row


def base_case_row(case: Dict[str, Any], probe: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "difficulty": case["difficulty"],
        "scenario_type": case["scenario_type"],
        "expected_type": case.get("expected_type", "auto"),
        "turns": list(case.get("turns") or []),
        "expected_tool": case.get("expected_tool", ""),
        "expected_categories": list(case.get("expected_categories") or []),
        "expected_component_roles": list(case.get("expected_component_roles") or []),
        "expected_product_ids": list(case.get("expected_product_ids") or []),
        "expected_product_predicates": list(case.get("expected_product_predicates") or []),
        "expected_no_match_reason": case.get("expected_no_match_reason", ""),
        "expected_clarification_required": bool(case.get("expected_clarification_required", False)),
        "expected_cart_action": case.get("expected_cart_action", ""),
        "expected_price_max": case.get("expected_price_max"),
        "excluded_terms": list(case.get("excluded_terms") or []),
        "excluded_brands_or_regions": list(case.get("excluded_brands_or_regions") or []),
        "requires_rag": bool(case.get("requires_rag", False)),
        "requires_llm": bool(case.get("requires_llm", False)),
        "requires_multimodal": bool(case.get("requires_multimodal", False)),
        "status": "not_applicable",
        "failure_type": "not_applicable",
        "failure_reason": "",
        "route_result": {},
        "recommended_products": [],
        "component_roles": [],
        "candidate_count": 0,
        "trace_summary": {},
        "catalog_probe": probe,
    }


def run_recommendation_case(case: Dict[str, Any], *, use_llm: bool, use_milvus: bool) -> Dict[str, Any]:
    query = case["turns"][-1]
    result = recommend_shopping_products(
        query,
        use_llm=use_llm,
        use_llm_guidance=False,
        use_milvus_retrieval=use_milvus,
    )
    payload = model_to_dict(result)
    return result_execution_payload(payload)


def run_multiturn_case(case: Dict[str, Any], *, use_llm: bool, use_milvus: bool) -> Dict[str, Any]:
    session = ShoppingSession(session_id=f"eval-{case['case_id']}")
    final_payload: Dict[str, Any] = {}
    turn_summaries = []
    for turn in case["turns"]:
        contextual_goal = build_contextual_goal(session, turn)
        result = recommend_shopping_products(
            contextual_goal,
            use_llm=use_llm,
            use_llm_guidance=False,
            use_milvus_retrieval=use_milvus,
        )
        final_payload = model_to_dict(result)
        remember_recommendation(session, contextual_goal, final_payload)
        turn_summaries.append(
            {
                "turn": turn,
                "contextual_goal": contextual_goal,
                "desired_categories": normalize_category_values(nested_get(final_payload, ["requirement", "desired_categories"], []) or []),
                "price_max": nested_get(final_payload, ["requirement", "price_max"]),
            }
        )
    payload = result_execution_payload(final_payload)
    payload["session_state"] = {
        "last_goal": session.last_goal,
        "turn_count": len(session.messages),
        "turn_summaries": turn_summaries,
    }
    return payload


def run_comparison_case(case: Dict[str, Any], combined_catalog: ProductCatalog, *, use_llm: bool) -> Dict[str, Any]:
    query = case["turns"][-1]
    session = ShoppingSession(session_id=f"eval-{case['case_id']}")
    tool_call = route_shopping_tool_call(query, session, use_llm=use_llm)
    route_result = compact_route(tool_call)
    product_ids = [str(item) for item in (tool_call.get("arguments") or {}).get("product_ids") or [] if str(item)]
    compare_result: Dict[str, Any] = {}
    if tool_call.get("name") == "compare_products" and product_ids:
        compare_result = compare_products(combined_catalog, product_ids)
        update_topic_memory(session, tool_call, result_type="comparison")
    elif tool_call.get("name") == "compare_products":
        compare_result = {"rows": [], "clarification_required": True, "reason": "未给出明确 A/B 商品。"}
    else:
        result = recommend_shopping_products(query, use_llm=use_llm, use_llm_guidance=False, use_milvus_retrieval=False)
        payload = model_to_dict(result)
        out = result_execution_payload(payload)
        out["route_result"] = route_result
        return out

    rows = compare_result.get("rows") or []
    products = []
    for row in rows:
        product_id = row.get("product_id") or row.get("id") or ""
        product = combined_catalog.get(product_id) if product_id else None
        products.append(product_brief(product) if product else {"product_id": product_id})
    return {
        "route_result": route_result,
        "recommended_products": products,
        "component_roles": [],
        "candidate_count": len(rows),
        "trace_summary": {"comparison_rows": len(rows), "clarification_required": bool(compare_result.get("clarification_required"))},
        "comparison_table": rows,
        "clarification_required": bool(compare_result.get("clarification_required")),
    }


def run_cart_case(case: Dict[str, Any], catalog: ProductCatalog, *, use_llm: bool, use_milvus: bool) -> Dict[str, Any]:
    session = ShoppingSession(session_id=f"eval-{case['case_id']}")
    first = recommend_shopping_products(
        case["turns"][0],
        use_llm=use_llm,
        use_llm_guidance=False,
        use_milvus_retrieval=use_milvus,
    )
    first_payload = model_to_dict(first)
    remember_recommendation(session, case["turns"][0], first_payload)
    selected_ids = [item["product_id"] for item in extract_recommended_products(first_payload)[:1] if item.get("product_id")]

    actions = []
    for turn in case["turns"][1:]:
        before = sum(item.quantity for item in session.cart.values())
        result = apply_cart_instruction(session, turn, catalog, selected_ids if "加" in turn else None)
        after = int((result.get("cart") or {}).get("count") or 0)
        actions.append({"turn": turn, "action": result.get("action"), "before_count": before, "after_count": after, "messages": result.get("messages") or []})
        selected_ids = []

    payload = result_execution_payload(first_payload)
    payload["cart_trace"] = actions
    payload["route_result"] = {"name": "apply_cart_instruction", "source": "local_session_eval"}
    return payload


def run_multimodal_boundary_case(case: Dict[str, Any], probe: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "route_result": {"name": "recommend_shopping_products", "source": "eval_boundary"},
        "recommended_products": [],
        "component_roles": [],
        "candidate_count": 0,
        "trace_summary": {
            "multimodal_policy": "当前评估不伪造真实图像语义理解；仅记录能力边界。",
            "catalog_probe_result": probe.get("probe_status"),
        },
        "capability_boundary": "capability_partial",
    }


def result_execution_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    requirement = payload.get("requirement") or {}
    return {
        "route_result": payload.get("intent_route") or {"route": requirement.get("scenario"), "task_type": requirement.get("task_type")},
        "recommended_products": extract_recommended_products(payload),
        "component_roles": extract_component_roles(payload),
        "candidate_count": int(payload.get("candidate_count") or 0),
        "trace_summary": compact_trace(payload.get("trace") or {}),
        "comparison_table": payload.get("comparison_table") or [],
        "missing_fields": payload.get("missing_fields") or [],
        "requirement": {
            "desired_categories": normalize_category_values(requirement.get("desired_categories") or []),
            "required_components": normalize_category_values(requirement.get("required_components") or []),
            "target_sub_categories": requirement.get("target_sub_categories") or [],
            "must_have_terms": requirement.get("must_have_terms") or [],
            "excluded_terms": requirement.get("excluded_terms") or [],
            "excluded_brands": requirement.get("excluded_brands") or [],
            "price_max": requirement.get("price_max"),
            "need_bundle": bool(requirement.get("need_bundle")),
            "need_comparison": bool(requirement.get("need_comparison")),
            "need_cart_action": bool(requirement.get("need_cart_action")),
            "need_multimodal": bool(requirement.get("need_multimodal")),
            "missing_fields": requirement.get("missing_fields") or [],
        },
    }


def judge_case(case: Dict[str, Any], row: Dict[str, Any], probe: Dict[str, Any], *, use_llm: bool) -> Tuple[str, str, str]:
    if row.get("error"):
        return "failed", "script_error", row["error"]
    scenario = case["scenario_type"]
    if scenario == "multimodal_photo_search":
        if probe.get("probe_status") in {"catalog_gap", "unsupported_category"}:
            return "not_applicable", "capability_gap", "当前没有真实图片语义理解，且 catalog 中没有明确外套/冲锋衣；这是能力/数据边界，不算业务回归。"
        return "not_applicable", "capability_partial", "当前评估仅确认多模态边界，没有伪造图像理解成功。"

    if probe.get("probe_status") == "unsupported_category":
        return "not_applicable", "catalog_gap", "当前 catalog 不包含所需品类。"
    if probe.get("probe_status") == "catalog_gap":
        if recommends_forbidden_substitute(row, probe):
            return "failed", "business_failed", "catalog 缺少目标子类时仍推荐了不匹配商品。"
        return "not_applicable", "catalog_gap", "当前 catalog 缺少目标子类/关键词命中商品，不作为业务失败。"
    if probe.get("probe_status") == "budget_catalog_gap":
        if row.get("recommended_products") and any(product_price(p) <= float(probe.get("price_max") or 0) for p in row["recommended_products"]):
            return "suspicious", "eval_design_gap", "catalog probe 判定预算缺口，但线上结果出现预算内商品，需复核 probe 关键词。"
        return "not_applicable", "budget_catalog_gap", "目标商品存在，但预算过滤后为空，不作为业务失败。"

    if scenario == "single_turn_fuzzy_recommendation":
        return judge_single_turn(case, row)
    if scenario == "conditional_filter":
        return judge_conditional_filter(case, row)
    if scenario == "multiturn_refinement":
        return judge_multiturn(case, row)
    if scenario == "product_comparison":
        return judge_comparison(case, row)
    if scenario == "proactive_clarification":
        return judge_clarification(case, row, use_llm=use_llm)
    if scenario == "negative_constraints":
        return judge_negative_constraints(case, row)
    if scenario == "scenario_bundle_recommendation":
        return judge_bundle(case, row)
    if scenario == "cart_crud":
        return judge_cart(case, row)
    return "suspicious", "eval_design_gap", f"未知场景类型: {scenario}"


def judge_single_turn(case: Dict[str, Any], row: Dict[str, Any]) -> Tuple[str, str, str]:
    category_ok = expected_categories_present(case, row)
    relevant = any(product_matches_keywords(item, row["catalog_probe"].get("required_keywords_any") or []) for item in row.get("recommended_products") or [])
    if category_ok and row.get("recommended_products") and relevant:
        return "ok", "none", ""
    return "failed", "business_failed", "单轮推荐未同时满足类目正确、至少 1 个相关商品。"


def judge_conditional_filter(case: Dict[str, Any], row: Dict[str, Any]) -> Tuple[str, str, str]:
    status, failure_type, reason = judge_single_turn(case, row)
    if status != "ok":
        return status, failure_type, reason
    price_max = case.get("expected_price_max")
    over = [item for item in row.get("recommended_products") or [] if price_max is not None and product_price(item) > float(price_max)]
    if over:
        return "failed", "business_failed", f"推荐结果包含超过预算 {price_max} 的商品: {[item.get('product_id') for item in over]}"
    return "ok", "none", ""


def judge_multiturn(case: Dict[str, Any], row: Dict[str, Any]) -> Tuple[str, str, str]:
    req = row.get("requirement") or {}
    categories = set(req.get("desired_categories") or [])
    price_ok = req.get("price_max") == case.get("expected_price_max")
    text = joined_products_text(row.get("recommended_products") or [])
    keyword_ok = any(term in text for term in ["跑步鞋", "跑鞋", "轻量", "缓震", "透气"])
    if "clothing" in categories and price_ok and row.get("recommended_products") and keyword_ok:
        return "ok", "none", ""
    return "failed", "business_failed", "多轮最终结果未完整继承 clothing/跑鞋/轻量/500 元以内约束。"


def judge_comparison(case: Dict[str, Any], row: Dict[str, Any]) -> Tuple[str, str, str]:
    route_name = (row.get("route_result") or {}).get("name") or (row.get("route_result") or {}).get("route")
    if route_name == "compare_products" and (row.get("comparison_table") or row.get("clarification_required")):
        return "ok", "none", ""
    if row.get("clarification_required"):
        return "suspicious", "eval_design_gap", "未给明确 A/B 商品时返回澄清，当前产品设计可接受但需持续观察。"
    return "failed", "business_failed", "对比请求未走 compare_products，也没有对比结构或澄清。"


def judge_clarification(case: Dict[str, Any], row: Dict[str, Any], *, use_llm: bool) -> Tuple[str, str, str]:
    has_question = bool(row.get("clarification_required") or row.get("missing_fields") or nested_get(row, ["requirement", "missing_fields"], []))
    questions = " ".join(str(item) for item in nested_get(row, ["trace_summary", "follow_up_questions"], []) or [])
    if has_question or any(term in questions for term in ["拍照", "续航", "性能", "性价比", "预算"]):
        return "ok", "none", ""
    if not use_llm:
        return "suspicious", "eval_design_gap", "use_llm=False 时规则链路直接推荐手机，缺少主动澄清；本轮不阻塞 CI。"
    return "failed", "business_failed", "use_llm=True 时仍未出现主动澄清。"


def judge_negative_constraints(case: Dict[str, Any], row: Dict[str, Any]) -> Tuple[str, str, str]:
    products = row.get("recommended_products") or []
    if not products:
        return "ok", "none", "过滤后无候选，未忽略否定条件。"
    forbidden = [*(case.get("excluded_terms") or []), *(case.get("excluded_brands_or_regions") or [])]
    violations = [item.get("product_id") for item in products if product_matches_keywords(item, forbidden)]
    if violations:
        return "failed", "business_failed", f"推荐结果包含用户排除词或品牌/地区: {violations}"
    return "ok", "none", ""


def judge_bundle(case: Dict[str, Any], row: Dict[str, Any]) -> Tuple[str, str, str]:
    roles = set(row.get("component_roles") or [])
    expected = set(case.get("expected_component_roles") or [])
    if expected.issubset(roles):
        return "ok", "none", ""
    return "failed", "business_failed", f"跨类目组合缺少组件: {sorted(expected - roles)}"


def judge_cart(case: Dict[str, Any], row: Dict[str, Any]) -> Tuple[str, str, str]:
    actions = row.get("cart_trace") or []
    by_action = {item.get("action"): item for item in actions}
    add_ok = by_action.get("add", {}).get("after_count", 0) > by_action.get("add", {}).get("before_count", 0)
    remove_ok = by_action.get("remove", {}).get("after_count", 999) < by_action.get("remove", {}).get("before_count", 0)
    clear_ok = by_action.get("clear", {}).get("after_count") == 0
    if add_ok and remove_ok and clear_ok:
        return "ok", "none", ""
    return "failed", "business_failed", "购物车 add/remove/clear 状态变化未全部满足。"


def probe_catalog(catalog: ProductCatalog, spec: Dict[str, Any]) -> Dict[str, Any]:
    required_category = spec.get("required_category") or ""
    keywords = list(spec.get("required_keywords_any") or [])
    sub_keywords = list(spec.get("required_subcategory_keywords_any") or [])
    price_max = spec.get("price_max")
    excluded = list(spec.get("excluded_terms") or [])
    category_products = products_for_category(catalog, required_category)
    keyword_hits = [p for p in category_products if product_has_any(p, keywords)]
    sub_hits = [p for p in category_products if product_has_any_subcategory(p, sub_keywords)]
    combined_hits = [p for p in category_products if product_has_any(p, keywords + sub_keywords)]
    base_hits = sub_hits or keyword_hits or combined_hits
    budget_hits = [p for p in base_hits if price_max is None or product_model_price(p) <= float(price_max)]
    after_exclusion_hits = [p for p in budget_hits if not product_has_any(p, excluded)]

    if required_category and not category_products:
        probe_status = "unsupported_category"
    elif required_category and sub_keywords and not sub_hits:
        probe_status = "catalog_gap"
    elif required_category and keywords and not (keyword_hits or combined_hits):
        probe_status = "catalog_gap"
    elif price_max is not None and base_hits and not budget_hits:
        probe_status = "budget_catalog_gap"
    else:
        probe_status = "in_catalog_positive"

    return {
        "required_category": required_category,
        "required_keywords_any": keywords,
        "required_subcategory_keywords_any": sub_keywords,
        "price_max": price_max,
        "excluded_terms": excluded,
        "category_product_count": len(category_products),
        "keyword_hit_count": len(keyword_hits),
        "subcategory_hit_count": len(sub_hits),
        "combined_hit_count": len(combined_hits),
        "budget_hit_count": len(budget_hits),
        "after_exclusion_hit_count": len(after_exclusion_hits),
        "probe_status": probe_status,
        "sample_product_ids": [p.product_id for p in (after_exclusion_hits or budget_hits or base_hits)[:5]],
    }


def build_catalog_summary(catalog: ProductCatalog, combined_catalog: ProductCatalog) -> Dict[str, Any]:
    category_counts = Counter(product.category.value for product in catalog.products)
    combined_category_counts = Counter(product.category.value for product in combined_catalog.products)
    sub_counts: Dict[str, Counter] = defaultdict(Counter)
    price_ranges: Dict[str, Dict[str, Optional[float]]] = {}
    keyword_terms = [
        "防晒", "油皮", "清爽", "洗面奶", "面霜", "保湿", "蓝牙耳机", "耳机", "跑步鞋", "跑鞋",
        "轻量", "篮球鞋", "帽子", "外套", "冲锋衣", "咖啡", "方便食品", "功能饮料", "B760", "DDR5",
        "750W", "2TB", "NVMe",
    ]
    coverage = {term: Counter() for term in keyword_terms}
    for product in combined_catalog.products:
        category = product.category.value
        sub_counts[category][product.sub_category or ""] += 1
        for term in keyword_terms:
            if product_has_any(product, [term]):
                coverage[term][category] += 1
    for category, products in combined_catalog.by_category.items():
        prices = [product_model_price(product) for product in products if product_model_price(product) > 0]
        price_ranges[category.value] = {
            "min": min(prices) if prices else None,
            "max": max(prices) if prices else None,
            "count": len(prices),
        }
    return {
        "ecommerce_total": len(catalog.products),
        "combined_total": len(combined_catalog.products),
        "category_counts": dict(sorted(category_counts.items())),
        "combined_category_counts": dict(sorted(combined_category_counts.items())),
        "sub_category_counts": {key: dict(counter.most_common()) for key, counter in sorted(sub_counts.items())},
        "keyword_coverage": {term: dict(counter) for term, counter in coverage.items()},
        "price_ranges": price_ranges,
    }


def effective_expected_type(case: Dict[str, Any], probe: Dict[str, Any]) -> str:
    expected = case.get("expected_type") or "auto"
    if expected not in {"auto", "llm_needed_or_design_choice", "capability_gap_or_catalog_gap"}:
        return expected
    if case.get("requires_multimodal"):
        return "capability_gap" if probe.get("probe_status") == "in_catalog_positive" else "capability_gap_or_catalog_gap"
    return probe.get("probe_status") or expected


def build_report(rows: List[Dict[str, Any]], catalog_summary: Dict[str, Any], *, config: Dict[str, Any], elapsed_ms: float) -> Dict[str, Any]:
    summary = aggregate(rows)
    summary["overall_status"] = "failed" if any(row["failure_type"] in {"business_failed", "script_error"} for row in rows) else "ok"
    summary["elapsed_ms"] = elapsed_ms
    report = {
        "summary": summary,
        "by_difficulty": group_aggregate(rows, "difficulty"),
        "by_scenario_type": group_aggregate(rows, "scenario_type"),
        "cases": rows,
        "catalog_summary": catalog_summary,
        "failure_breakdown": group_aggregate(rows, "failure_type"),
        "recommendations": build_recommendations(rows),
        "config": config,
    }
    report["summary"].update(rate_summary(rows))
    return report


def aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts = Counter(row.get("status", "not_applicable") for row in rows)
    failure_counts = Counter(row.get("failure_type", "none") for row in rows)
    return {
        "total_cases": len(rows),
        **{status: counts.get(status, 0) for status in STATUSES},
        "catalog_gap": failure_counts.get("catalog_gap", 0) + failure_counts.get("budget_catalog_gap", 0),
        "capability_gap": failure_counts.get("capability_gap", 0) + failure_counts.get("capability_partial", 0),
        "business_failed": failure_counts.get("business_failed", 0),
        "failure_types": dict(failure_counts),
    }


def group_aggregate(rows: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "unknown")].append(row)
    return {name: aggregate(items) for name, items in sorted(groups.items())}


def rate_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "rag_applicable_pass_rate": pass_rate([row for row in rows if row.get("requires_rag") and row.get("failure_type") not in {"catalog_gap", "budget_catalog_gap", "capability_gap", "capability_partial"}]),
        "multiturn_state_pass_rate": pass_rate([row for row in rows if row.get("scenario_type") == "multiturn_refinement"]),
        "negative_constraint_pass_rate": pass_rate([row for row in rows if row.get("scenario_type") == "negative_constraints"]),
        "cross_category_bundle_pass_rate": pass_rate([row for row in rows if row.get("scenario_type") == "scenario_bundle_recommendation"]),
        "cart_state_pass_rate": pass_rate([row for row in rows if row.get("scenario_type") == "cart_crud"]),
    }


def pass_rate(rows: List[Dict[str, Any]]) -> Optional[float]:
    if not rows:
        return None
    return round(sum(1 for row in rows if row.get("status") == "ok") / len(rows), 4)


def build_recommendations(rows: List[Dict[str, Any]]) -> List[str]:
    recs = []
    failed = [row for row in rows if row.get("failure_type") == "business_failed"]
    if failed:
        recs.append("优先修复 business_failed case：这些是当前业务逻辑或路由行为与验收口径不一致。")
        for row in failed[:8]:
            recs.append(f"- {row['case_id']}: {row['failure_reason']}")
    if any(row.get("failure_type") in {"catalog_gap", "budget_catalog_gap"} for row in rows):
        recs.append("catalog_gap / budget_catalog_gap 反映数据集覆盖不足，不应算代码错误；后续可补洗面奶、蓝牙耳机、预算内跑鞋、外套/冲锋衣等商品。")
    if any(row.get("failure_type") in {"capability_gap", "capability_partial"} for row in rows):
        recs.append("多模态同款识别目前是能力边界，应先接入真实视觉语义理解，再把该类 case 转为业务通过项。")
    if any(row.get("status") == "suspicious" for row in rows):
        recs.append("suspicious case 不阻塞 CI，但建议产品侧确认主动澄清和模糊需求策略。")
    return recs


def render_markdown(report: Dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# MallMind 典型用户场景评估报告",
        "",
        "## 总览",
        "",
        f"- 总 case 数：{summary['total_cases']}",
        f"- ok：{summary['ok']}",
        f"- failed：{summary['failed']}",
        f"- suspicious：{summary['suspicious']}",
        f"- not_applicable：{summary['not_applicable']}",
        f"- catalog_gap：{summary['catalog_gap']}",
        f"- capability_gap：{summary['capability_gap']}",
        f"- business_failed：{summary['business_failed']}",
        "",
        "## 关键通过率",
        "",
        f"- RAG 适用场景通过率：{format_rate(summary.get('rag_applicable_pass_rate'))}",
        f"- 多轮状态通过率：{format_rate(summary.get('multiturn_state_pass_rate'))}",
        f"- 反选约束通过率：{format_rate(summary.get('negative_constraint_pass_rate'))}",
        f"- 跨类目组合通过率：{format_rate(summary.get('cross_category_bundle_pass_rate'))}",
        f"- 购物车状态通过率：{format_rate(summary.get('cart_state_pass_rate'))}",
        "",
        "## 按难度汇总",
        "",
        render_group_table(report["by_difficulty"]),
        "",
        "## 按场景类型汇总",
        "",
        render_group_table(report["by_scenario_type"]),
        "",
    ]
    lines.extend(render_case_section("failed case 明细", [row for row in report["cases"] if row["status"] == "failed"]))
    lines.extend(render_case_section("suspicious case 明细", [row for row in report["cases"] if row["status"] == "suspicious"]))
    lines.extend(
        render_case_section(
            "catalog_gap 明细",
            [
                row
                for row in report["cases"]
                if row["failure_type"] in {"catalog_gap", "budget_catalog_gap"}
                or (row.get("catalog_probe") or {}).get("probe_status") in {"catalog_gap", "budget_catalog_gap", "unsupported_category"}
            ],
        )
    )
    lines.extend(render_case_section("capability_gap 明细", [row for row in report["cases"] if row["failure_type"] in {"capability_gap", "capability_partial"}]))
    lines.extend([
        "## 多模态能力边界说明",
        "",
        "当前评估不把 `[image]` 视为真实视觉语义输入，也不伪造“同款外套”识别成功。若 catalog 没有外套/冲锋衣，合格输出应是 catalog_gap / capability_gap，而不是用户外裤、背包、帽子冒充外套。",
        "",
        "## Catalog 摘要",
        "",
        f"- ecommerce 商品数：{report['catalog_summary']['ecommerce_total']}",
        f"- combined 商品数：{report['catalog_summary']['combined_total']}",
        f"- ecommerce 类目分布：{json.dumps(report['catalog_summary']['category_counts'], ensure_ascii=False)}",
        "",
        "## 下一步建议",
        "",
    ])
    for item in report.get("recommendations") or ["当前没有额外建议。"]:
        lines.append(item)
    lines.append("")
    return "\n".join(lines)


def render_group_table(groups: Dict[str, Any]) -> str:
    lines = ["| 分组 | total | ok | failed | suspicious | not_applicable | catalog_gap | capability_gap | business_failed |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, item in groups.items():
        lines.append(
            f"| {name} | {item['total_cases']} | {item['ok']} | {item['failed']} | {item['suspicious']} | "
            f"{item['not_applicable']} | {item['catalog_gap']} | {item['capability_gap']} | {item['business_failed']} |"
        )
    return "\n".join(lines)


def render_case_section(title: str, rows: List[Dict[str, Any]]) -> List[str]:
    lines = [f"## {title}", ""]
    if not rows:
        lines.extend(["无。", ""])
        return lines
    lines.extend(["| case_id | status | failure_type | reason | probe | recommended |", "| --- | --- | --- | --- | --- | --- |"])
    for row in rows:
        products = ", ".join(item.get("product_id", "") for item in (row.get("recommended_products") or [])[:5])
        lines.append(
            f"| {row['case_id']} | {row['status']} | {row['failure_type']} | {escape_md(row.get('failure_reason') or '')} | "
            f"{row.get('catalog_probe', {}).get('probe_status')} | {products or '-'} |"
        )
    lines.append("")
    return lines


def extract_recommended_products(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    products: List[Dict[str, Any]] = []
    seen = set()
    for card in payload.get("product_cards") or []:
        add_product_brief(products, seen, card)
    for plan in payload.get("plans") or []:
        for component in plan.get("components") or []:
            product = component.get("product") or {}
            item = product_brief_dict(product)
            item["component_role"] = normalize_category_value(component.get("role"))
            add_product_brief(products, seen, item)
    return products


def add_product_brief(products: List[Dict[str, Any]], seen: set, item: Dict[str, Any]) -> None:
    product_id = item.get("product_id") or item.get("id") or ""
    if not product_id or product_id in seen:
        return
    seen.add(product_id)
    products.append(product_brief_dict(item))


def product_brief(product: Optional[ApiProduct]) -> Dict[str, Any]:
    if product is None:
        return {}
    return {
        "product_id": product.product_id,
        "title": product.title,
        "brand": product.brand,
        "category": product.category.value,
        "sub_category": product.sub_category,
        "base_price": product.base_price,
        "min_price": product.min_price,
        "max_price": product.max_price,
        "text": product_text(product),
    }


def product_brief_dict(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "product_id": item.get("product_id") or item.get("id") or "",
        "title": item.get("title") or item.get("name") or "",
        "brand": item.get("brand") or "",
        "category": normalize_category_value(item.get("category") or item.get("category_key") or item.get("role")),
        "sub_category": item.get("sub_category") or "",
        "base_price": item.get("base_price") or item.get("price") or item.get("min_price") or 0,
        "min_price": item.get("min_price") or item.get("base_price") or item.get("price") or 0,
        "max_price": item.get("max_price") or item.get("base_price") or item.get("price") or 0,
        "component_role": normalize_category_value(item.get("component_role") or item.get("role")),
        "text": " ".join(str(item.get(key) or "") for key in ("title", "brand", "category", "sub_category", "description", "reason")),
    }


def extract_component_roles(payload: Dict[str, Any]) -> List[str]:
    roles = []
    for plan in payload.get("plans") or []:
        for component in plan.get("components") or []:
            role = normalize_category_value(component.get("role"))
            if role and role not in roles:
                roles.append(role)
    return roles


def compact_trace(trace: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "selected_runtime_mode": trace.get("selected_runtime_mode") or trace.get("selected_mode") or trace.get("runtime_mode"),
        "explanation_mode": trace.get("explanation_mode"),
        "fallback_used": bool(trace.get("fallback_used")),
        "llm_used_for_explanation": bool(trace.get("llm_used_for_explanation")),
        "adaptive_reason_codes": (trace.get("adaptive_decision") or {}).get("reason_codes") or trace.get("reason_codes") or [],
        "desired_categories": trace.get("desired_categories"),
        "candidate_counts_by_category": trace.get("candidate_counts_by_category"),
        "structured_filter": trace.get("structured_filter"),
        "intent_route": trace.get("intent_route"),
        "retrieval_status": nested_get(trace, ["retrieval", "status"]),
        "milvus_status": nested_get(trace, ["milvus_retrieval", "status"]),
        "requirement_parsing": trace.get("requirement_parsing"),
    }


def compact_route(tool_call: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": tool_call.get("name"),
        "confidence": tool_call.get("confidence"),
        "source": tool_call.get("source"),
        "reason": tool_call.get("reason"),
        "arguments": tool_call.get("arguments") or {},
    }


def products_for_category(catalog: ProductCatalog, category: str) -> List[ApiProduct]:
    try:
        return catalog.filter_by_category(ComponentCategory(category))
    except ValueError:
        return []


def product_has_any(product: ApiProduct, terms: Iterable[str]) -> bool:
    terms = [term for term in terms if term]
    if not terms:
        return True
    text = product_text(product)
    return any(term.lower() in text.lower() for term in terms)


def product_has_any_subcategory(product: ApiProduct, terms: Iterable[str]) -> bool:
    terms = [term for term in terms if term]
    if not terms:
        return True
    text = f"{product.sub_category} {product.title} {' '.join(product.tags)}".lower()
    return any(term.lower() in text for term in terms)


def product_text(product: ApiProduct) -> str:
    faq_text = " ".join(f"{faq.question} {faq.answer}" for faq in product.faqs)
    review_text = " ".join(review.content for review in product.reviews)
    return " ".join(
        [
            product.product_id,
            product.title,
            product.brand,
            product.category.value,
            product.category_name,
            product.sub_category,
            product.description,
            " ".join(product.tags),
            " ".join(product.best_for),
            " ".join(product.not_good_for),
            " ".join(product.supported_scenarios),
            faq_text,
            review_text,
            json.dumps(product.metadata, ensure_ascii=False),
        ]
    )


def product_matches_keywords(item: Dict[str, Any], terms: Iterable[str]) -> bool:
    text = " ".join(str(item.get(key) or "") for key in ("title", "brand", "category", "sub_category", "text"))
    return any(str(term).lower() in text.lower() for term in terms if str(term).strip())


def joined_products_text(items: List[Dict[str, Any]]) -> str:
    return " ".join(" ".join(str(item.get(key) or "") for key in ("title", "brand", "category", "sub_category", "text")) for item in items)


def product_model_price(product: ApiProduct) -> float:
    return float(product.min_price or product.base_price or 0)


def product_price(item: Dict[str, Any]) -> float:
    try:
        return float(item.get("min_price") or item.get("base_price") or item.get("price") or 0)
    except (TypeError, ValueError):
        return 0.0


def expected_categories_present(case: Dict[str, Any], row: Dict[str, Any]) -> bool:
    expected = set(case.get("expected_categories") or [])
    if not expected:
        return True
    product_categories = {item.get("category") for item in row.get("recommended_products") or [] if item.get("category")}
    req_categories = set(nested_get(row, ["requirement", "desired_categories"], []) or [])
    return bool(expected & (product_categories | req_categories))


def recommends_forbidden_substitute(row: Dict[str, Any], probe: Dict[str, Any]) -> bool:
    sub_terms = probe.get("required_subcategory_keywords_any") or []
    products = row.get("recommended_products") or []
    return bool(products) and not any(product_matches_keywords(item, sub_terms) for item in products)


def normalize_category_values(values: Iterable[Any]) -> List[str]:
    out = []
    for value in values:
        normalized = normalize_category_value(value)
        if normalized and normalized not in out:
            out.append(normalized)
    return out


def normalize_category_value(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("value") or value.get("category") or value.get("role") or ""
    if hasattr(value, "value"):
        value = value.value
    return str(value or "")


def nested_get(data: Any, path: List[str], default: Any = None) -> Any:
    current = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def format_rate(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"{value:.4f}"


def escape_md(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


@contextmanager
def stable_eval_environment(runtime_mode: str):
    old_values = {
        "RECOMMENDATION_LLM_GUIDANCE": os.getenv("RECOMMENDATION_LLM_GUIDANCE"),
        "RECOMMENDATION_ENABLE_MILVUS": os.getenv("RECOMMENDATION_ENABLE_MILVUS"),
        "RECOMMENDATION_USE_MILVUS": os.getenv("RECOMMENDATION_USE_MILVUS"),
        "RECOMMENDATION_RUNTIME_MODE": os.getenv("RECOMMENDATION_RUNTIME_MODE"),
    }
    os.environ["RECOMMENDATION_LLM_GUIDANCE"] = "false"
    os.environ["RECOMMENDATION_RUNTIME_MODE"] = runtime_mode
    if runtime_mode != "full":
        os.environ["RECOMMENDATION_ENABLE_MILVUS"] = "false"
        os.environ["RECOMMENDATION_USE_MILVUS"] = "false"
    try:
        yield
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    raise SystemExit(main())
