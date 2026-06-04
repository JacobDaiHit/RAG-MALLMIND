"""Offline retrieval evaluation for MallMind product recommendations."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rag.recommendation.package_builder import build_recommendation_result
from rag.recommendation.product_loader import load_combined_product_catalog, load_pc_parts_product_catalog, load_product_catalog
from rag.recommendation.query_guards import is_pc_query
from rag.recommendation.retrieval import retrieve_requirement_evidence
from rag.recommendation.recommendation_pipeline import parse_requirement


Case = Dict[str, Any]
MAIN_CASE_SCOPES = {"in_catalog_exact", "in_catalog_attribute_gap"}
AMBIGUOUS_CASE_SCOPE = "in_catalog_ambiguous"
CATALOG_GAP_CASE_SCOPE = "catalog_gap"
NEGATIVE_CASE_SCOPE = "negative_or_impossible"


def precision_at_k(ranked_ids: Sequence[str], relevant_ids: Iterable[str], k: int) -> float:
    if k <= 0:
        return 0.0
    return len(set(ranked_ids[:k]) & set(relevant_ids)) / float(k)


def recall_at_k(ranked_ids: Sequence[str], relevant_ids: Iterable[str], k: int) -> float:
    relevant = set(relevant_ids)
    return len(set(ranked_ids[:k]) & relevant) / float(max(1, len(relevant)))


def hit_at_k(ranked_ids: Sequence[str], relevant_ids: Iterable[str], k: int) -> int:
    return int(bool(set(ranked_ids[:k]) & set(relevant_ids)))


def mrr(ranked_ids: Sequence[str], relevant_ids: Iterable[str]) -> float:
    relevant = set(relevant_ids)
    for index, product_id in enumerate(ranked_ids, 1):
        if product_id in relevant:
            return 1.0 / index
    return 0.0


def constraint_violation(
    ranked_ids: Sequence[str],
    case: Case,
    *,
    catalog_by_id: Optional[Dict[str, Any]] = None,
    k: Optional[int] = None,
) -> Tuple[bool, List[str]]:
    top_ids = list(ranked_ids[:k] if k is not None else ranked_ids)
    excluded = set(case.get("excluded_product_ids") or [])
    reasons: List[str] = []
    excluded_hits = [product_id for product_id in top_ids if product_id in excluded]
    if excluded_hits:
        reasons.append("excluded_product_ids: " + ", ".join(excluded_hits))

    terms = [str(term).lower() for term in (case.get("must_not_contain_terms") or []) if str(term).strip()]
    if terms and catalog_by_id:
        for product_id in top_ids:
            product = catalog_by_id.get(product_id)
            text = _product_text(product).lower() if product is not None else product_id.lower()
            matched = [term for term in terms if term in text]
            if matched:
                reasons.append(f"must_not_contain_terms({product_id}): " + ", ".join(matched))

    if catalog_by_id and (case.get("price_min") is not None or case.get("price_max") is not None):
        price_min = case.get("price_min")
        price_max = case.get("price_max")
        for product_id in top_ids:
            product = catalog_by_id.get(product_id)
            price = _product_price(product)
            if price is None:
                continue
            if price_min is not None and price < float(price_min):
                reasons.append(f"price_below_min({product_id}): {price}")
            if price_max is not None and price > float(price_max):
                reasons.append(f"price_above_max({product_id}): {price}")

    return bool(reasons), reasons


def load_cases(path: Path, *, limit: Optional[int] = None, case_id: Optional[str] = None) -> List[Case]:
    cases: List[Case] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                case = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            _validate_case_shape(case, path, line_number)
            if case_id and case.get("id") != case_id:
                continue
            cases.append(case)
            if limit is not None and len(cases) >= limit:
                break
    return cases


def validate_product_ids(cases: Sequence[Case], catalog_by_id: Dict[str, Any], *, strict: bool = False) -> Dict[str, List[str]]:
    invalid: Dict[str, List[str]] = {}
    fields = ("relevant_product_ids", "acceptable_product_ids", "excluded_product_ids")
    for case in cases:
        missing: List[str] = []
        for field in fields:
            for product_id in case.get(field) or []:
                if product_id not in catalog_by_id:
                    missing.append(product_id)
        if missing:
            invalid[str(case["id"])] = sorted(set(missing))
    if strict and invalid:
        raise ValueError(f"Golden cases reference product_ids missing from catalog: {invalid}")
    return invalid


def is_negative_case(case: Case) -> bool:
    return case_scope(case) == NEGATIVE_CASE_SCOPE


def case_scope(case: Case) -> str:
    scope = str(case.get("case_scope") or "").strip()
    if scope:
        return scope
    if not (case.get("relevant_product_ids") or []) and not (case.get("acceptable_product_ids") or []):
        return NEGATIVE_CASE_SCOPE
    return "in_catalog_exact"


def classify_case_group(case: Case) -> str:
    scope = case_scope(case)
    if scope == NEGATIVE_CASE_SCOPE:
        return "negative"
    if scope == CATALOG_GAP_CASE_SCOPE:
        expected_categories = [str(item) for item in (case.get("expected_categories") or [])]
        if any(category.startswith("pc_") for category in expected_categories):
            return "pc"
        if expected_categories:
            return "ecommerce"
        return "unknown"
    expected_categories = [str(item) for item in (case.get("expected_categories") or [])]
    product_ids = list(case.get("relevant_product_ids") or []) + list(case.get("acceptable_product_ids") or [])
    if any(category.startswith("pc_") for category in expected_categories) or any(product_id.startswith("pc_") for product_id in product_ids):
        return "pc"
    if expected_categories or any(product_id.startswith("p_") for product_id in product_ids):
        return "ecommerce"
    return "unknown"


def extract_ranked_product_ids(result: Any) -> List[str]:
    ids: List[str] = []

    cards = _as_list(_get(result, "product_cards"))
    if isinstance(_get(result, "product_cards"), dict):
        cards = _as_list(_get(_get(result, "product_cards"), "products"))
    for card in cards:
        _append_id(ids, _get(card, "product_id") or _get(card, "id"))

    for plan in _as_list(_get(result, "plans")):
        for key in ("items", "products"):
            for item in _as_list(_get(plan, key)):
                _append_id(ids, _get(item, "product_id") or _get(item, "id"))
                product = _get(item, "product")
                _append_id(ids, _get(product, "product_id") or _get(product, "id"))
        for component in _as_list(_get(plan, "components")):
            product = _get(component, "product")
            _append_id(ids, _get(component, "product_id") or _get(product, "product_id") or _get(product, "id"))

    trace = _get(result, "trace") or {}
    for container in (_get(result, "candidate_scope"), _get(trace, "candidate_scope"), trace):
        for key in ("top_candidates", "candidate_scope"):
            value = _get(container, key)
            if key == "candidate_scope":
                value = _get(value, "top_candidates")
            for item in _as_list(value):
                _append_id(ids, _get(item, "product_id") or _get(item, "id"))

    return _dedupe(ids)


def evaluate_cases(
    cases: Sequence[Case],
    *,
    top_ks: Sequence[int],
    catalog_by_id: Dict[str, Any],
    recommend_fn: Callable[[Case], Tuple[Any, float]],
    with_milvus: bool = False,
    requirement_fn: Optional[Callable[[str], Any]] = None,
    exclude_pc_from_main: bool = True,
    exclude_negative_from_main: bool = True,
    group_by: str = "case_group,expected_categories",
) -> Dict[str, Any]:
    per_case: List[Dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        error = ""
        result: Any = {}
        latency_ms = 0.0
        try:
            result, latency_ms = recommend_fn(case)
        except Exception as exc:  # keep offline eval reportable
            error = str(exc)
            latency_ms = (time.perf_counter() - started) * 1000.0

        ranked_ids = extract_ranked_product_ids(result)
        strict_ids = set(case.get("relevant_product_ids") or [])
        relaxed_ids = strict_ids | set(case.get("acceptable_product_ids") or [])
        case_group = classify_case_group(case)
        scope = case_scope(case)
        metric_eligible = scope not in {NEGATIVE_CASE_SCOPE, CATALOG_GAP_CASE_SCOPE}
        negative = scope == NEGATIVE_CASE_SCOPE
        catalog_gap = scope == CATALOG_GAP_CASE_SCOPE
        returned_titles = product_titles(ranked_ids, catalog_by_id)
        returned_categories = product_categories(ranked_ids, catalog_by_id)
        expected_ids = list(case.get("relevant_product_ids") or [])
        expected_titles = product_titles(expected_ids, catalog_by_id)
        expected_categories = list(case.get("expected_categories") or [])
        top1_id = ranked_ids[0] if ranked_ids else None
        top1_title = returned_titles[0] if returned_titles else None
        top1_category = returned_categories[0] if returned_categories else None
        metrics: Dict[str, Any] = {
            "mrr": None if not metric_eligible else mrr(ranked_ids, relaxed_ids),
            "empty_result": not bool(ranked_ids),
            "latency_ms": round(latency_ms, 2),
            "metric_eligible": metric_eligible,
        }
        violation_details: Dict[str, List[str]] = {}
        category_details: Dict[str, bool] = {}
        for k in top_ks:
            metrics[f"precision@{k}"] = None if not metric_eligible else precision_at_k(ranked_ids, relaxed_ids, k)
            metrics[f"strict_recall@{k}"] = None if not metric_eligible else recall_at_k(ranked_ids, strict_ids, k)
            metrics[f"relaxed_recall@{k}"] = None if not metric_eligible else recall_at_k(ranked_ids, relaxed_ids, k)
            metrics[f"hit@{k}"] = None if not metric_eligible else hit_at_k(ranked_ids, relaxed_ids, k)
            violated, reasons = constraint_violation(ranked_ids, case, catalog_by_id=catalog_by_id, k=k)
            metrics[f"constraint_violation@{k}"] = int(violated)
            if reasons:
                violation_details[str(k)] = reasons
            category_ok = category_accuracy_at_k(ranked_ids, case.get("expected_categories") or [], catalog_by_id, k)
            metrics[f"category_accuracy@{k}"] = int(category_ok)
            category_details[str(k)] = category_ok
        metrics["category_accuracy_top1"] = int(
            category_accuracy_at_k(ranked_ids, case.get("expected_categories") or [], catalog_by_id, 1)
        )
        metrics["constraint_violation"] = int(any(metrics[f"constraint_violation@{k}"] for k in top_ks))
        negative_violation, negative_reasons = negative_case_violation(case, ranked_ids)
        metrics["negative_violation"] = int(negative_violation)
        metrics["negative_returned_any"] = int(bool(ranked_ids)) if negative else None
        metrics["negative_no_recommendation"] = int(not bool(ranked_ids)) if negative else None
        catalog_gap_violation, catalog_gap_reasons = catalog_gap_violation_check(case, ranked_ids)
        metrics["catalog_gap_violation"] = int(catalog_gap_violation)
        metrics["catalog_gap_returned_any"] = int(bool(ranked_ids)) if catalog_gap else None
        metrics["catalog_gap_no_recommendation"] = int(not bool(ranked_ids)) if catalog_gap else None
        if negative_reasons:
            violation_details["negative"] = negative_reasons
        if catalog_gap_reasons:
            violation_details["catalog_gap"] = catalog_gap_reasons

        evidence: Dict[str, Any] = {"retrieval_status": "not_requested"}
        if with_milvus and not error and requirement_fn is not None:
            evidence = evaluate_milvus_evidence(case, requirement_fn, relaxed_ids, top_ks)

        trace_debug = extract_trace_debug(result)
        stage_guess = miss_stage_guess(
            case=case,
            ranked_ids=ranked_ids,
            metrics=metrics,
            catalog_by_id=catalog_by_id,
            case_group=case_group,
        )
        reasons = miss_reasons(case, ranked_ids, metrics, top_ks, case_group=case_group)
        per_case.append(
            {
                "id": case["id"],
                "query": case["query"],
                "case_group": case_group,
                "case_scope": scope,
                "scope_note": case.get("scope_note"),
                "expected_behavior": case.get("expected_behavior"),
                "expected_relevant_product_ids": list(case.get("relevant_product_ids") or []),
                "acceptable_product_ids": list(case.get("acceptable_product_ids") or []),
                "returned_ids": ranked_ids,
                "returned_product_ids": ranked_ids,
                "returned_titles": returned_titles,
                "returned_categories": returned_categories,
                "expected_titles": expected_titles,
                "expected_categories": expected_categories,
                "top1_id": top1_id,
                "top1_title": top1_title,
                "top1_category": top1_category,
                "miss_reasons": reasons,
                "miss_stage_guess": stage_guess,
                "latency_ms": round(latency_ms, 2),
                "trace_debug": trace_debug,
                "metrics": metrics,
                "constraint_violation_reasons": violation_details,
                "category_match_by_k": category_details,
                "miss_reason": "; ".join(reasons),
                "error": error,
                **evidence,
            }
        )
    main_cases = [
        case
        for case in per_case
        if case.get("case_scope") in MAIN_CASE_SCOPES
        and not (
            exclude_pc_from_main
            and case.get("case_group") == "pc"
            and case.get("case_scope") != "in_catalog_attribute_gap"
        )
        and not (exclude_negative_from_main and case.get("case_group") == "negative")
    ]
    grouped = grouped_summaries(per_case, top_ks, group_by=group_by)
    return {
        "per_case": per_case,
        "aggregate": aggregate_metrics(main_cases, top_ks),
        "main_summary": aggregate_metrics(main_cases, top_ks),
        "global_all_cases_summary": aggregate_metrics(per_case, top_ks),
        "all_cases_summary": aggregate_metrics(per_case, top_ks),
        "ecommerce_summary": aggregate_metrics([case for case in per_case if case.get("case_group") == "ecommerce"], top_ks),
        "pc_summary": aggregate_metrics([case for case in per_case if case.get("case_group") == "pc"], top_ks),
        "ambiguous_summary": aggregate_metrics([case for case in per_case if case.get("case_scope") == AMBIGUOUS_CASE_SCOPE], top_ks),
        "catalog_gap_summary": aggregate_catalog_gap_cases([case for case in per_case if case.get("case_scope") == CATALOG_GAP_CASE_SCOPE], top_ks),
        "negative_summary": aggregate_negative_cases([case for case in per_case if case.get("case_group") == "negative"], top_ks),
        "grouped_summaries": grouped,
    }


def aggregate_metrics(per_case: Sequence[Dict[str, Any]], top_ks: Sequence[int]) -> Dict[str, Any]:
    count = len(per_case)
    successful = [case for case in per_case if not case.get("error")]
    eligible = [case for case in per_case if case["metrics"].get("metric_eligible", True)]
    metrics = {
        "case_count": count,
        "success_count": len(successful),
        "empty_result_count": sum(1 for case in per_case if case["metrics"].get("empty_result")),
        "empty_result_rate": _mean(case["metrics"].get("empty_result", 0) for case in per_case),
        "latency_ms": round(_mean(case["metrics"].get("latency_ms", 0.0) for case in per_case), 2),
        "avg_latency_ms": round(_mean(case["metrics"].get("latency_ms", 0.0) for case in per_case), 2),
        "metric_eligible_count": len(eligible),
        "mrr": _mean(case["metrics"].get("mrr", 0.0) for case in eligible),
        "constraint_violation_rate": _mean(case["metrics"].get("constraint_violation", 0) for case in per_case),
        "category_accuracy_top1": _mean(case["metrics"].get("category_accuracy_top1", 0) for case in per_case),
    }
    for k in top_ks:
        for name in ("precision", "strict_recall", "relaxed_recall", "hit"):
            metrics[f"{name}@{k}"] = _mean(case["metrics"].get(f"{name}@{k}", 0.0) for case in eligible)
        for name in ("category_accuracy", "constraint_violation"):
            metrics[f"{name}@{k}"] = _mean(case["metrics"].get(f"{name}@{k}", 0.0) for case in per_case)
    return metrics


def aggregate_negative_cases(per_case: Sequence[Dict[str, Any]], top_ks: Sequence[int]) -> Dict[str, Any]:
    return {
        "case_count": len(per_case),
        "negative_case_count": len(per_case),
        "success_count": sum(1 for case in per_case if not case.get("error")),
        "negative_empty_result_rate": _mean(case["metrics"].get("empty_result", 0) for case in per_case),
        "negative_no_recommendation_rate": _mean(case["metrics"].get("negative_no_recommendation", 0) for case in per_case),
        "negative_violation_rate": _mean(case["metrics"].get("negative_violation", 0) for case in per_case),
        "negative_returned_any_rate": _mean(case["metrics"].get("negative_returned_any", 0) for case in per_case),
        "constraint_violation_rate": _mean(case["metrics"].get("constraint_violation", 0) for case in per_case),
        "category_accuracy@5": _mean(case["metrics"].get("category_accuracy@5", 0) for case in per_case),
        "avg_latency_ms": round(_mean(case["metrics"].get("latency_ms", 0.0) for case in per_case), 2),
        "latency_ms": round(_mean(case["metrics"].get("latency_ms", 0.0) for case in per_case), 2),
    }


def aggregate_catalog_gap_cases(per_case: Sequence[Dict[str, Any]], top_ks: Sequence[int]) -> Dict[str, Any]:
    return {
        "case_count": len(per_case),
        "catalog_gap_case_count": len(per_case),
        "success_count": sum(1 for case in per_case if not case.get("error")),
        "catalog_gap_empty_result_rate": _mean(case["metrics"].get("empty_result", 0) for case in per_case),
        "catalog_gap_no_recommendation_rate": _mean(case["metrics"].get("catalog_gap_no_recommendation", 0) for case in per_case),
        "catalog_gap_violation_rate": _mean(case["metrics"].get("catalog_gap_violation", 0) for case in per_case),
        "catalog_gap_returned_any_rate": _mean(case["metrics"].get("catalog_gap_returned_any", 0) for case in per_case),
        "constraint_violation_rate": _mean(case["metrics"].get("constraint_violation", 0) for case in per_case),
        "category_accuracy@5": _mean(case["metrics"].get("category_accuracy@5", 0) for case in per_case),
        "avg_latency_ms": round(_mean(case["metrics"].get("latency_ms", 0.0) for case in per_case), 2),
        "latency_ms": round(_mean(case["metrics"].get("latency_ms", 0.0) for case in per_case), 2),
    }


def grouped_summaries(per_case: Sequence[Dict[str, Any]], top_ks: Sequence[int], *, group_by: str) -> Dict[str, Any]:
    fields = [field.strip() for field in (group_by or "").split(",") if field.strip()]
    summaries: Dict[str, Any] = {}
    for field in fields:
        buckets: Dict[str, List[Dict[str, Any]]] = {}
        for case in per_case:
            if field == "expected_categories":
                values = case.get("expected_categories") or ["<none>"]
                key = ",".join(str(value) for value in values)
            else:
                key = str(case.get(field) or "<none>")
            buckets.setdefault(key, []).append(case)
        summaries[field] = {key: aggregate_metrics(items, top_ks) for key, items in buckets.items()}
    return summaries


def _summary_lines(summary: Dict[str, Any], top_ks: Sequence[int]) -> List[str]:
    return [
        f"- Total cases: {summary.get('case_count', 0)}",
        f"- Success cases: {summary.get('success_count', 0)}",
        f"- Metric-eligible cases: {summary.get('metric_eligible_count', 0)}",
        f"- Empty results: {summary.get('empty_result_count', 0)} ({summary.get('empty_result_rate', 0):.3f})",
        f"- Average latency: {summary.get('avg_latency_ms', summary.get('latency_ms', 0)):.2f} ms",
        _metric_row("precision", summary, top_ks),
        _metric_row("strict_recall", summary, top_ks),
        _metric_row("relaxed_recall", summary, top_ks),
        _metric_row("hit", summary, top_ks),
        f"- MRR: {summary.get('mrr', 0):.3f}",
        f"- constraint_violation_rate: {summary.get('constraint_violation_rate', 0):.3f}",
        f"- category_accuracy_top1: {summary.get('category_accuracy_top1', 0):.3f}",
        _metric_row("category_accuracy", summary, top_ks),
    ]


def _compact_group_lines(summary: Dict[str, Any], label: str) -> List[str]:
    return [
        f"- {label}_cases: {summary.get('case_count', 0)}",
        f"- hit@5: {summary.get('hit@5', 0):.3f}",
        f"- relaxed_recall@5: {summary.get('relaxed_recall@5', 0):.3f}",
        f"- constraint_violation_rate: {summary.get('constraint_violation_rate', 0):.3f}",
        f"- category_accuracy@5: {summary.get('category_accuracy@5', 0):.3f}",
        f"- avg_latency_ms: {summary.get('avg_latency_ms', summary.get('latency_ms', 0)):.2f}",
    ]


def _negative_summary_lines(summary: Dict[str, Any]) -> List[str]:
    return [
        f"- negative_cases: {summary.get('negative_case_count', 0)}",
        f"- negative_empty_result_rate: {summary.get('negative_empty_result_rate', 0):.3f}",
        f"- negative_no_recommendation_rate: {summary.get('negative_no_recommendation_rate', 0):.3f}",
        f"- negative_violation_rate: {summary.get('negative_violation_rate', 0):.3f}",
        f"- negative_returned_any_rate: {summary.get('negative_returned_any_rate', 0):.3f}",
        f"- constraint_violation_rate: {summary.get('constraint_violation_rate', 0):.3f}",
        f"- category_accuracy@5: {summary.get('category_accuracy@5', 0):.3f}",
        f"- avg_latency_ms: {summary.get('avg_latency_ms', summary.get('latency_ms', 0)):.2f}",
    ]


def _catalog_gap_summary_lines(summary: Dict[str, Any]) -> List[str]:
    return [
        f"- catalog_gap_cases: {summary.get('catalog_gap_case_count', 0)}",
        f"- catalog_gap_empty_result_rate: {summary.get('catalog_gap_empty_result_rate', 0):.3f}",
        f"- catalog_gap_no_recommendation_rate: {summary.get('catalog_gap_no_recommendation_rate', 0):.3f}",
        f"- catalog_gap_violation_rate: {summary.get('catalog_gap_violation_rate', 0):.3f}",
        f"- catalog_gap_returned_any_rate: {summary.get('catalog_gap_returned_any_rate', 0):.3f}",
        f"- constraint_violation_rate: {summary.get('constraint_violation_rate', 0):.3f}",
        f"- category_accuracy@5: {summary.get('category_accuracy@5', 0):.3f}",
        f"- avg_latency_ms: {summary.get('avg_latency_ms', summary.get('latency_ms', 0)):.2f}",
    ]


def render_markdown(report: Dict[str, Any], top_ks: Sequence[int]) -> str:
    main = report.get("main_summary") or report["aggregate"]
    all_cases = report.get("all_cases_summary") or report["aggregate"]
    lines = [
        "# Retrieval Evaluation",
        "",
        "## Main Summary",
        "",
        "_Default main summary includes in_catalog_exact and in_catalog_attribute_gap cases. Ambiguous, catalog gap, and negative/impossible cases are reported separately._",
        "",
        *_summary_lines(main, top_ks),
        "",
        "## All Cases Summary",
        "",
        "_Mixed summary across all cases; retrieval metrics exclude negative cases but include PC cases._",
        "",
        *_summary_lines(all_cases, top_ks),
        "",
        "## Ecommerce Summary",
        "",
        *_compact_group_lines(report.get("ecommerce_summary") or {}, "ecommerce"),
        "",
        "## PC Summary",
        "",
        *_compact_group_lines(report.get("pc_summary") or {}, "pc"),
        "",
        "## Ambiguous Summary",
        "",
        *_compact_group_lines(report.get("ambiguous_summary") or {}, "ambiguous"),
        "",
        "## Catalog Gap Summary",
        "",
        *_catalog_gap_summary_lines(report.get("catalog_gap_summary") or {}),
        "",
        "## Negative Summary",
        "",
        *_negative_summary_lines(report.get("negative_summary") or {}),
        "",
        "## Worst Cases",
        "",
        "| case | group | scope | query | expected ids | expected titles | returned ids | returned titles | top1 title | expected category | returned category | miss_stage_guess | miss reason |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for case in worst_cases(report["per_case"]):
        expected = ", ".join(case.get("expected_relevant_product_ids") or [])
        returned = ", ".join((case.get("returned_product_ids") or [])[:10])
        expected_titles = ", ".join((case.get("expected_titles") or [])[:5])
        returned_titles = ", ".join((case.get("returned_titles") or [])[:5])
        expected_category = ", ".join(case.get("expected_categories") or [])
        returned_category = ", ".join((case.get("returned_categories") or [])[:5])
        lines.append(
            f"| {case['id']} | {case.get('case_group')} | {case.get('case_scope')} | {_md_cell(case['query'])} | {_md_cell(expected)} | {_md_cell(expected_titles)} | {_md_cell(returned)} | {_md_cell(returned_titles)} | {_md_cell(case.get('top1_title') or '')} | {_md_cell(expected_category)} | {_md_cell(returned_category)} | {_md_cell(case.get('miss_stage_guess') or '')} | {_md_cell(case.get('miss_reason') or '')} |"
        )
    return "\n".join(lines) + "\n"


def worst_cases(per_case: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    for case in per_case:
        metrics = case["metrics"]
        if (
            metrics.get("strict_recall@5", 0) == 0
            or metrics.get("constraint_violation")
            or metrics.get("negative_violation")
            or metrics.get("empty_result")
            or (case.get("category_match_by_k") and not case["category_match_by_k"].get("5", True))
        ):
            selected.append(case)
    return selected[:20]


def category_accuracy_at_k(
    ranked_ids: Sequence[str],
    expected_categories: Sequence[str],
    catalog_by_id: Dict[str, Any],
    k: int,
) -> bool:
    if not expected_categories:
        return True
    expected = {str(item).lower() for item in expected_categories}
    for product_id in ranked_ids[:k]:
        product = catalog_by_id.get(product_id)
        values = {
            str(_get(product, "category") or "").lower(),
            str(getattr(_get(product, "category"), "value", "")).lower(),
            str(_get(product, "category_name") or "").lower(),
            str(_get(product, "sub_category") or "").lower(),
        }
        if values & expected:
            return True
    return False


def product_title(product: Any) -> str:
    if product is None:
        return ""
    for field in ("name", "title", "product_name", "api_name", "id", "product_id"):
        value = _get(product, field)
        if value:
            return str(value)
    return ""


def product_titles(product_ids: Sequence[str], catalog_by_id: Dict[str, Any]) -> List[str]:
    return [product_title(catalog_by_id.get(product_id)) or product_id for product_id in product_ids]


def product_category(product: Any) -> str:
    if product is None:
        return ""
    category = _get(product, "category")
    return str(getattr(category, "value", category) or _get(product, "category_name") or "")


def product_categories(product_ids: Sequence[str], catalog_by_id: Dict[str, Any]) -> List[str]:
    return [product_category(catalog_by_id.get(product_id)) or "unknown" for product_id in product_ids]


def negative_case_violation(case: Case, ranked_ids: Sequence[str]) -> Tuple[bool, List[str]]:
    if case_scope(case) != NEGATIVE_CASE_SCOPE:
        return False, []
    if not ranked_ids:
        return False, []
    if bool(case.get("allow_fallback")):
        return False, []
    expected_behavior = str(case.get("expected_behavior") or "").strip().lower()
    if expected_behavior in {"no_recommendation", "clarify"} or not expected_behavior:
        return True, ["fallback_violation: negative case returned products"]
    return False, []


def catalog_gap_violation_check(case: Case, ranked_ids: Sequence[str]) -> Tuple[bool, List[str]]:
    if case_scope(case) != CATALOG_GAP_CASE_SCOPE:
        return False, []
    if not ranked_ids:
        return False, []
    if bool(case.get("allow_fallback")):
        return False, []
    expected_behavior = str(case.get("expected_behavior") or "").strip().lower()
    if expected_behavior in {"no_recommendation", "clarify"} or not expected_behavior:
        return True, ["fallback_violation: catalog gap case returned products"]
    return False, []


def miss_stage_guess(
    *,
    case: Case,
    ranked_ids: Sequence[str],
    metrics: Dict[str, Any],
    catalog_by_id: Dict[str, Any],
    case_group: str,
) -> str:
    if not ranked_ids:
        return "empty_result"
    if case_scope(case) == CATALOG_GAP_CASE_SCOPE:
        violated, _ = catalog_gap_violation_check(case, ranked_ids)
        if violated:
            return "catalog_gap_fallback_error"
    if case_group == "negative":
        violated, _ = negative_case_violation(case, ranked_ids)
        if violated:
            return "negative_fallback_error"
    returned_top1_category = product_category(catalog_by_id.get(ranked_ids[0]))
    if case_group == "pc" and returned_top1_category in {"digital", "beauty", "food", "clothing"}:
        return "pc_route_not_used"
    if metrics.get("constraint_violation"):
        return "constraint_filter_error"
    expected_categories = case.get("expected_categories") or []
    if expected_categories and not category_matches(returned_top1_category, expected_categories):
        return "category_routing_or_filter_error"
    relevant_ids = set(case.get("relevant_product_ids") or [])
    if relevant_ids:
        top10 = set(ranked_ids[:10])
        top5 = set(ranked_ids[:5])
        if relevant_ids & top10 and not relevant_ids & top5:
            return "ranking_error"
        relevant_categories = {product_category(catalog_by_id.get(product_id)) for product_id in relevant_ids}
        returned_categories = set(product_categories(ranked_ids[:10], catalog_by_id))
        if relevant_categories & returned_categories and not relevant_ids & top10:
            return "recall_or_candidate_generation_error"
    return "ok_or_label_review"


def category_matches(category: str, expected_categories: Sequence[str]) -> bool:
    normalized = str(category).lower()
    return normalized in {str(item).lower() for item in expected_categories}


def miss_reasons(
    case: Case,
    ranked_ids: Sequence[str],
    metrics: Dict[str, Any],
    top_ks: Sequence[int],
    *,
    case_group: str,
) -> List[str]:
    reasons: List[str] = []
    if metrics.get("empty_result"):
        reasons.append("empty result")
    if case_scope(case) not in {NEGATIVE_CASE_SCOPE, CATALOG_GAP_CASE_SCOPE} and metrics.get("strict_recall@5", 0) == 0:
        reasons.append("strict recall@5 is 0")
    if metrics.get("constraint_violation"):
        reasons.append("constraint violation")
    if metrics.get("negative_violation"):
        reasons.append("negative fallback violation")
    if metrics.get("catalog_gap_violation"):
        reasons.append("catalog gap fallback violation")
    if case.get("expected_categories") and not metrics.get("category_accuracy@5", 1):
        reasons.append("category mismatch")
    if not reasons and ranked_ids:
        reasons.append("low rank or partial match")
    return reasons


def extract_trace_debug(result: Any) -> Dict[str, Any]:
    trace = _get(result, "trace") or {}
    candidate_scope = _get(result, "candidate_scope") or _get(trace, "candidate_scope") or {}
    structured_filter = _get(trace, "structured_filter") or {}
    filtered_counts: List[Any] = []
    if isinstance(structured_filter, dict):
        for diagnostics in structured_filter.values():
            if isinstance(diagnostics, dict):
                filtered_counts.append(
                    diagnostics.get("filtered_count")
                    or diagnostics.get("candidate_count")
                    or diagnostics.get("matched_count")
                )
    return {
        "route_tool_name": _get(_get(trace, "intent_route") or {}, "tool_name")
        or _get(_get(result, "intent_route") or {}, "tool_name")
        or _get(_get(trace, "intent_route") or {}, "route"),
        "parsed_category": _get(trace, "desired_categories"),
        "candidate_count": _get(result, "candidate_count") or _get(trace, "catalog_product_count"),
        "filtered_count": next((item for item in filtered_counts if item is not None), None),
        "final_count": len(extract_ranked_product_ids(result)),
        "inferred_product_type": _get(trace, "inferred_product_type"),
        "product_type_filter_applied": _trace_any(structured_filter, "product_type_filter_applied"),
        "product_type_candidate_count": _trace_any(structured_filter, "product_type_candidate_count"),
        "pc_part_constraints": _trace_any(structured_filter, "pc_part_constraints"),
        "pc_constraint_filter_applied": _trace_any(structured_filter, "pc_constraint_filter_applied"),
        "pc_constraint_candidate_count": _trace_any(structured_filter, "pc_constraint_candidate_count"),
        "pc_constraint_relaxed": _trace_any(structured_filter, "pc_constraint_relaxed"),
        "no_match_reason": _get(trace, "no_match_reason"),
        "fallback_blocked_reason": _get(trace, "fallback_blocked_reason"),
        "requested_product_type": _get(trace, "requested_product_type"),
        "available_neighbor_types": _get(trace, "available_neighbor_types"),
        "pc_route_detected": _get(trace, "pc_route_detected"),
    }


def evaluate_milvus_evidence(
    case: Case,
    requirement_fn: Callable[[str], Any],
    relaxed_ids: Set[str],
    top_ks: Sequence[int],
) -> Dict[str, Any]:
    try:
        requirement = requirement_fn(case["query"])
        categories = getattr(requirement, "desired_categories", []) or getattr(requirement, "required_components", [])
        evidence = retrieve_requirement_evidence(requirement, categories)
        trace = evidence.to_trace()
        matched_ids = list(trace.get("matched_product_ids") or [])
        status = trace.get("status") or "unknown"
    except Exception as exc:
        return {
            "retrieval_status": "skipped",
            "retrieval_error": str(exc),
            "evidence_matched_product_ids": [],
        }
    payload: Dict[str, Any] = {
        "retrieval_status": status if status == "ok" else "skipped",
        "evidence_raw_status": status,
        "evidence_matched_product_ids": matched_ids,
    }
    for k in top_ks:
        payload[f"evidence_recall@{k}"] = recall_at_k(matched_ids, relaxed_ids, k)
    return payload


def miss_reason(case: Case, ranked_ids: Sequence[str], metrics: Dict[str, Any], top_ks: Sequence[int]) -> str:
    reasons: List[str] = []
    if metrics.get("empty_result"):
        reasons.append("empty result")
    if metrics.get("strict_recall@5", 0) == 0:
        reasons.append("strict recall@5 is 0")
    if metrics.get("constraint_violation"):
        reasons.append("constraint violation")
    if case.get("expected_categories") and not metrics.get("category_accuracy@5", 1):
        reasons.append("category mismatch")
    if not reasons and ranked_ids:
        reasons.append("low rank or partial match")
    return "; ".join(reasons)


def build_default_recommend_fn(*, use_llm: bool, catalog_name: str, with_milvus: bool) -> Tuple[Callable[[Case], Tuple[Any, float]], Dict[str, Any], Callable[[str], Any]]:
    catalog = load_combined_product_catalog(use_cache=True) if catalog_name == "combined" else load_product_catalog(use_cache=True)
    pc_catalog = load_pc_parts_product_catalog(use_cache=True)
    catalog_by_id = catalog.by_id

    def requirement_fn(query: str) -> Any:
        return parse_requirement(query, use_llm=use_llm)

    def recommend_fn(case: Case) -> Tuple[Any, float]:
        started = time.perf_counter()
        requirement = requirement_fn(case["query"])
        scope = catalog_name
        active_catalog = catalog
        if classify_case_group(case) == "pc" or is_pc_query(case["query"]):
            scope = "pc_parts"
            active_catalog = pc_catalog
        result = build_recommendation_result(
            requirement,
            catalog=active_catalog,
            catalog_scope=scope,
            use_milvus_retrieval=False,
        )
        return result, (time.perf_counter() - started) * 1000.0

    return recommend_fn, catalog_by_id, requirement_fn


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT_DIR / "tests" / "eval" / "golden_retrieval_cases.jsonl")
    parser.add_argument("--top-k", default="1,3,5,10")
    parser.add_argument("--output", type=Path, default=ROOT_DIR / "reports" / "retrieval_eval.json")
    parser.add_argument("--markdown", type=Path, default=ROOT_DIR / "reports" / "retrieval_eval.md")
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument("--catalog", choices=("combined", "ecommerce"), default="combined")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--case-id")
    parser.add_argument("--with-milvus", action="store_true")
    parser.add_argument("--exclude-pc-from-main", nargs="?", const=True, default=True, type=parse_bool)
    parser.add_argument("--exclude-negative-from-main", nargs="?", const=True, default=True, type=parse_bool)
    parser.add_argument("--group-by", default="case_scope,case_group,expected_categories")
    args = parser.parse_args(argv)

    top_ks = parse_top_ks(args.top_k)
    cases = load_cases(args.cases, limit=args.limit, case_id=args.case_id)
    recommend_fn, catalog_by_id, requirement_fn = build_default_recommend_fn(
        use_llm=args.use_llm,
        catalog_name=args.catalog,
        with_milvus=args.with_milvus,
    )
    invalid_ids = validate_product_ids(cases, catalog_by_id, strict=args.strict)
    report = evaluate_cases(
        cases,
        top_ks=top_ks,
        catalog_by_id=catalog_by_id,
        recommend_fn=recommend_fn,
        with_milvus=args.with_milvus,
        requirement_fn=requirement_fn,
        exclude_pc_from_main=args.exclude_pc_from_main,
        exclude_negative_from_main=args.exclude_negative_from_main,
        group_by=args.group_by,
    )
    report["validation_warnings"] = {"invalid_product_ids": invalid_ids}
    report["config"] = {
        "cases": str(args.cases),
        "top_k": top_ks,
        "use_llm": args.use_llm,
        "catalog": args.catalog,
        "with_milvus": args.with_milvus,
        "exclude_pc_from_main": args.exclude_pc_from_main,
        "exclude_negative_from_main": args.exclude_negative_from_main,
        "group_by": args.group_by,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.write_text(render_markdown(report, top_ks), encoding="utf-8")
    if invalid_ids:
        print(f"validation warning: invalid product ids found: {invalid_ids}")
    print(f"wrote {args.output}")
    print(f"wrote {args.markdown}")
    return 0


def parse_top_ks(raw: str) -> List[int]:
    values = sorted({int(item.strip()) for item in raw.split(",") if item.strip()})
    if not values or any(value <= 0 for value in values):
        raise ValueError("--top-k must contain positive integers")
    return values


def parse_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean value: {raw}")


def _validate_case_shape(case: Case, path: Path, line_number: int) -> None:
    for field in ("id", "query", "relevant_product_ids"):
        if field not in case:
            raise ValueError(f"Missing required field {field!r} at {path}:{line_number}")
    if not isinstance(case["id"], str) or not isinstance(case["query"], str):
        raise ValueError(f"id and query must be strings at {path}:{line_number}")
    for field in ("relevant_product_ids", "acceptable_product_ids", "excluded_product_ids", "expected_categories", "must_not_contain_terms"):
        if field in case and case[field] is not None and not isinstance(case[field], list):
            raise ValueError(f"{field} must be a list at {path}:{line_number}")


def _get(value: Any, key: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _append_id(ids: List[str], value: Any) -> None:
    if value is not None:
        text = str(value).strip()
        if text:
            ids.append(text)


def _dedupe(values: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    result: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _product_text(product: Any) -> str:
    if product is None:
        return ""
    fields = [
        _get(product, "product_id"),
        _get(product, "title"),
        _get(product, "brand"),
        _get(product, "category"),
        _get(product, "category_name"),
        _get(product, "sub_category"),
        _get(product, "description"),
        " ".join(str(item) for item in (_get(product, "tags") or [])),
        " ".join(str(item) for item in (_get(product, "best_for") or [])),
    ]
    return " ".join(str(item) for item in fields if item)


def _product_price(product: Any) -> Optional[float]:
    if product is None:
        return None
    for field in ("base_price", "min_price", "price_cny", "price"):
        value = _get(product, field)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _trace_any(container: Any, key: str) -> Any:
    if not isinstance(container, dict):
        return None
    for value in container.values():
        if isinstance(value, dict) and key in value:
            return value.get(key)
    return None


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return float(statistics.mean(items)) if items else 0.0


def _metric_row(name: str, aggregate: Dict[str, Any], top_ks: Sequence[int]) -> str:
    return "- " + " / ".join(f"{name}@{k}: {aggregate.get(f'{name}@{k}', 0):.3f}" for k in top_ks)


def _md_cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")[:240]


if __name__ == "__main__":
    raise SystemExit(main())
