"""Full-chain ablation evaluation for MallMind recommendation flows."""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rag.api.app_context import model_to_dict
from rag.recommendation.comparison import compare_products
from rag.recommendation.package_builder import build_recommendation_result
from rag.recommendation.pc_session_flow import build_pc_plan_for_message
from rag.recommendation.product_loader import load_combined_product_catalog, load_pc_parts_product_catalog
from rag.recommendation.recommendation_pipeline import parse_requirement, recommend_shopping_products
from rag.recommendation.runtime_mode import runtime_policy_for_mode
from rag.recommendation.session_state import ShoppingSession, remember_pc_build_plan, remember_recommendation
from rag.recommendation.tool_router import route_shopping_tool_call


MODES = {
    "fast_no_llm_no_rag": {"runtime_mode": "fast", "llm": False, "rag": False},
    "rag_only": {"runtime_mode": "balanced", "llm": False, "rag": True},
    "llm_only": {"runtime_mode": "full", "llm": True, "rag": False},
    "full": {"runtime_mode": "full", "llm": True, "rag": True},
}
TOP_KS = [1, 3, 5]
PC_PART_DEBUG_CASE_IDS = {
    "pc_part_b760_ddr5_motherboard",
    "pc_part_750w_gold_psu",
    "pc_part_2tb_nvme_ssd",
    "pc_part_long_gpu_case",
}
EVAL_BUCKETS = (
    "in_catalog_ecommerce_rag",
    "pc_part_rag",
    "pc_build_structured",
    "negative_guard",
    "ambiguous_llm_needed",
    "route_boundary",
    "multiturn_session",
)
PC_COMPONENT_ID_PREFIXES = (
    "pc_motherboard_",
    "pc_psu_",
    "pc_storage_",
    "pc_case_",
    "pc_cpu_",
    "pc_gpu_",
    "pc_memory_",
    "pc_cooler_",
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT_DIR / "tests" / "fixtures" / "full_chain_eval_cases.json")
    parser.add_argument("--output", type=Path, default=ROOT_DIR / "reports" / "full_chain_ablation.json")
    parser.add_argument("--markdown", type=Path, default=ROOT_DIR / "reports" / "full_chain_ablation.md")
    parser.add_argument(
        "--mode",
        choices=tuple(MODES),
        action="append",
        help="跑单个模式；可重复传入。不传 --mode/--modes 时跑全部模式。",
    )
    parser.add_argument(
        "--modes",
        help="跑多个模式，使用逗号分隔，例如 fast_no_llm_no_rag,rag_only,llm_only,full；不传则跑全部模式。",
    )
    parser.add_argument("--disable-guidance", action="store_true", help="评估时关闭最终导购文案增强 LLM。")
    parser.add_argument("--retrieval-timeout-seconds", type=float, help="覆盖本次评估的 RAG/Milvus 检索超时时间。")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--case-id")
    args = parser.parse_args(argv)

    load_dotenv(ROOT_DIR / ".env")
    cases = load_cases(args.cases, limit=args.limit, case_id=args.case_id)
    modes = resolve_modes(args)
    all_rows: List[Dict[str, Any]] = []
    for mode in modes:
        for case in cases:
            all_rows.extend(run_case(case, mode, disable_guidance=args.disable_guidance, retrieval_timeout_seconds=args.retrieval_timeout_seconds))

    report = {
        "status": "failed" if any(row["case_status"] == "failed" for row in all_rows) else "ok",
        "config": {
            "cases": str(args.cases),
            "modes": modes,
            "top_ks": TOP_KS,
            "embedding_provider": os.getenv("EMBEDDING_PROVIDER", "local"),
            "embedding_model": os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"),
            "embedding_dim": safe_int(os.getenv("EMBEDDING_DIM") or os.getenv("DENSE_EMBEDDING_DIM", "1024")),
            "milvus_collection": os.getenv("MILVUS_COLLECTION", "embeddings_collection"),
            "disable_guidance": args.disable_guidance,
            "retrieval_timeout_seconds": args.retrieval_timeout_seconds,
        },
        "summary": aggregate(all_rows),
        "mode_summaries": {mode: aggregate([row for row in all_rows if row["mode"] == mode]) for mode in modes},
        "domain_summaries": {
            name: aggregate([row for row in all_rows if row["case_group"] == name])
            for name in ("ecommerce", "pc_parts", "pc_build", "negative", "routing")
        },
        "eval_bucket_summaries": {
            name: aggregate([row for row in all_rows if row["eval_bucket"] == name])
            for name in EVAL_BUCKETS
        },
        "pc_part_diagnostics": [
            row["pc_part_diagnostic"]
            for row in all_rows
            if row.get("pc_part_diagnostic")
        ],
        "failed_or_suspicious_cases": [
            row for row in all_rows if row["case_status"] in {"failed", "suspicious"} or row.get("errors")
        ],
        "per_case": all_rows,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(f"full 链路消融评估完成: {report['status']}")
    print(f"JSON 报告: {args.output}")
    print(f"Markdown 报告: {args.markdown}")
    return 1 if report["status"] == "failed" else 0


def resolve_modes(args: argparse.Namespace) -> List[str]:
    if args.modes and args.mode:
        raise SystemExit("请不要同时传 --mode 和 --modes；--mode 跑单个模式，--modes 跑逗号分隔的多个模式。")
    if args.modes:
        modes = [item.strip() for item in str(args.modes).split(",") if item.strip()]
        invalid = [mode for mode in modes if mode not in MODES]
        if invalid:
            allowed = ", ".join(MODES)
            raise SystemExit(f"--modes 包含未知模式: {', '.join(invalid)}；可选值: {allowed}")
        return modes
    return args.mode or list(MODES)


def run_case(case: Dict[str, Any], mode: str, *, disable_guidance: bool = False, retrieval_timeout_seconds: float | None = None) -> List[Dict[str, Any]]:
    if case.get("turns"):
        session = ShoppingSession(session_id=f"eval-{case['case_id']}-{mode}")
        rows = []
        for index, turn in enumerate(case["turns"], 1):
            merged = {**case, **turn, "case_id": f"{case['case_id']}_t{index}", "query": turn["query"], "parent_case_id": case["case_id"]}
            rows.append(run_single_case(merged, mode, session=session, disable_guidance=disable_guidance, retrieval_timeout_seconds=retrieval_timeout_seconds))
        return rows
    return [run_single_case(case, mode, session=ShoppingSession(session_id=f"eval-{case['case_id']}-{mode}"), disable_guidance=disable_guidance, retrieval_timeout_seconds=retrieval_timeout_seconds)]


def run_single_case(case: Dict[str, Any], mode: str, *, session: ShoppingSession, disable_guidance: bool = False, retrieval_timeout_seconds: float | None = None) -> Dict[str, Any]:
    spec = MODES[mode]
    started = time.perf_counter()
    errors: List[str] = []
    payload: Dict[str, Any] = {}
    tool_call: Dict[str, Any] = {}
    with mode_environment(spec, disable_guidance=disable_guidance, retrieval_timeout_seconds=retrieval_timeout_seconds):
        patch_retrieval_flag(spec["rag"], retrieval_timeout_seconds=retrieval_timeout_seconds)
        try:
            session.runtime_mode = spec["runtime_mode"]
            tool_call = route_shopping_tool_call(case["query"], session, use_llm=spec["llm"])
            tool_name = tool_call.get("name") or ""
            if tool_name == "generate_pc_build_plan":
                plan = build_pc_plan_for_message(case["query"], session)
                if not plan.get("_transient_comparison"):
                    remember_pc_build_plan(session, case["query"], plan)
                payload = pc_payload(plan, tool_call)
            elif tool_name == "compare_products":
                ids = case.get("product_ids") or []
                if not ids and session.last_result:
                    ids = extract_product_ids(session.last_result)[:2]
                payload = {"type": "comparison", "comparison": compare_products(load_combined_product_catalog(), ids), "tool_call": tool_call}
            elif tool_name == "apply_cart_instruction":
                payload = {"type": "cart", "tool_call": tool_call, "trace": {"no_match_reason": "cart_action_not_recommendation"}}
            elif tool_name == "general_chat":
                payload = {"type": "general_chat", "tool_call": tool_call, "trace": {"no_match_reason": "general_chat"}}
            else:
                catalog_scope = (tool_call.get("arguments") or {}).get("catalog_scope") or case.get("catalog_scope") or "ecommerce"
                result = recommend_shopping_products(
                    case["query"],
                    use_llm=spec["llm"],
                    catalog_scope=catalog_scope,
                    use_milvus_retrieval=spec["rag"],
                    use_rag_query_expansion=spec["rag"] and spec["llm"],
                    use_llm_guidance=spec["llm"] and not disable_guidance,
                )
                payload = model_to_dict(result)
                payload.setdefault("trace", {})["tool_call"] = tool_call
                remember_recommendation(session, case["query"], payload)
        except Exception as exc:
            errors.append(sanitize_error(exc))
            payload = {"trace": {}, "product_cards": []}
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return build_row(case, mode, spec, payload, tool_call, latency_ms, errors)


def build_row(
    case: Dict[str, Any],
    mode: str,
    spec: Dict[str, Any],
    payload: Dict[str, Any],
    tool_call: Dict[str, Any],
    latency_ms: float,
    errors: List[str],
) -> Dict[str, Any]:
    trace = payload.get("trace") or {}
    retrieval = trace.get("milvus_retrieval") or trace.get("retrieval") or {}
    routing_trace = tool_call.get("routing_trace") or trace.get("routing_trace") or {}
    ranked_ids = extract_product_ids(payload)
    titles = extract_titles(payload)
    tool_name = tool_call.get("name") or payload.get("type") or ""
    expected_tool = case.get("expected_tool")
    route_correct = expected_tool is None or tool_name == expected_tool
    eval_bucket = classify_eval_bucket(case)
    requires_rag = case_requires_rag(case, spec, eval_bucket)
    applicability = "rag_applicable" if requires_rag else "rag_not_applicable"
    rag_actually_used = is_milvus_used(retrieval)
    llm_router_used = bool((routing_trace.get("llm") or {}).get("name")) and not routing_trace.get("llm_skipped")
    llm_parse_used = bool(trace.get("llm_requirement_parse_used") or (trace.get("requirement_parsing") or {}).get("llm_parse_used"))
    llm_enhancement_used = trace.get("llm_guidance") == "enabled"
    no_match_reason = trace.get("no_match_reason") or trace.get("fallback_blocked_reason")
    rag_diagnostic = diagnose_rag(
        case=case,
        spec=spec,
        retrieval=retrieval,
        rag_actually_used=rag_actually_used,
        ranked_ids=ranked_ids,
        no_match_reason=no_match_reason,
        requires_rag=requires_rag,
    )
    pc_build_quality = extract_pc_build_quality(payload, tool_name, eval_bucket)
    status, failed_reason = case_status(
        case=case,
        spec=spec,
        route_correct=route_correct,
        rag_diagnostic=rag_diagnostic,
        llm_used=llm_router_used or llm_parse_used or llm_enhancement_used,
        no_match_reason=no_match_reason,
        ranked_ids=ranked_ids,
        errors=errors,
    )
    metrics = metrics_for_case(
        case, ranked_ids, tool_name, route_correct, no_match_reason, latency_ms, status, spec,
        rag_actually_used, llm_router_used, llm_parse_used, llm_enhancement_used, trace,
        rag_diagnostic, eval_bucket, requires_rag, pc_build_quality,
    )
    row = {
        "case_id": case["case_id"],
        "query": case["query"],
        "mode": mode,
        "runtime_mode": spec["runtime_mode"],
        "case_group": case.get("case_group", "unknown"),
        "eval_bucket": eval_bucket,
        "rag_applicability": applicability,
        "requires_rag": requires_rag,
        "expected_product_ids": case.get("expected_product_ids") or [],
        "expected_category": case.get("expected_category"),
        "expected_component_type": case.get("expected_component_type"),
        "expected_tool": expected_tool,
        "recommended_product_ids": ranked_ids,
        "recommended_titles": titles,
        "tool_name": tool_name,
        "route_correct": route_correct,
        "rag_expected_enabled": spec["rag"],
        "rag_actually_used": rag_actually_used,
        "llm_expected_enabled": spec["llm"],
        "llm_router_used": llm_router_used,
        "llm_requirement_parse_used": llm_parse_used,
        "llm_enhancement_used": llm_enhancement_used,
        "retrieval_backend": retrieval.get("retrieval_backend") or ("milvus" if rag_actually_used else "structured_catalog"),
        "embedding_provider": retrieval.get("embedding_provider") or os.getenv("EMBEDDING_PROVIDER", "local"),
        "embedding_model": retrieval.get("embedding_model") or os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"),
        "embedding_dim": retrieval.get("embedding_dim") or safe_int(os.getenv("EMBEDDING_DIM") or os.getenv("DENSE_EMBEDDING_DIM", "1024")),
        "milvus_collection": retrieval.get("milvus_collection") or os.getenv("MILVUS_COLLECTION", "embeddings_collection"),
        "retrieval_query": retrieval.get("retrieval_query") or "",
        "retrieval_filters": retrieval.get("retrieval_filters") or [],
        "milvus_raw_hit_count": int(retrieval.get("milvus_raw_hit_count") or 0),
        "retrieved_chunk_count_before_postprocess": int(retrieval.get("retrieved_chunk_count_before_postprocess") or 0),
        "retrieved_chunk_count_after_postprocess": int(retrieval.get("retrieved_chunk_count_after_postprocess") or retrieval.get("retrieved_chunk_count") or retrieval.get("total_hits") or 0),
        "retrieved_chunk_count": int(retrieval.get("retrieved_chunk_count") or retrieval.get("total_hits") or 0),
        "retrieved_product_ids": retrieval.get("retrieved_product_ids") or retrieval.get("matched_product_ids") or [],
        "retrieval_error": retrieval.get("retrieval_error") or retrieval.get("error") or "",
        "retrieval_timeout": bool(retrieval.get("retrieval_timeout") or retrieval.get("status") == "timeout"),
        "postprocess_error": retrieval.get("postprocess_error") or "",
        "auto_merge_status": retrieval.get("auto_merge_status") or "unknown",
        "rag_failure_reason": rag_diagnostic["reason"],
        "rag_chain_valid": rag_diagnostic["chain_valid"],
        "pc_build_chain_valid": pc_build_quality.get("pc_build_chain_valid"),
        "pc_build_compatibility_valid": pc_build_quality.get("pc_build_compatibility_valid"),
        "budget_valid": pc_build_quality.get("budget_valid"),
        "recommendation_source": recommendation_source(ranked_ids, retrieval),
        "candidate_count_before_filter": candidate_count_before(trace, payload),
        "candidate_count_after_filter": candidate_count_after(trace, payload),
        "no_match_reason": no_match_reason,
        "latency_ms": latency_ms,
        "errors": errors,
        "failed_reason": failed_reason,
        "case_status": status,
        "metrics": metrics,
        "trace": compact_trace(trace, routing_trace),
    }
    diagnostic = build_pc_part_diagnostic(row, case, payload, trace)
    if diagnostic:
        row["pc_part_diagnostic"] = diagnostic
    return row


def classify_eval_bucket(case: Dict[str, Any]) -> str:
    case_id = str(case.get("case_id") or "")
    group = str(case.get("case_group") or "")
    if case.get("parent_case_id") or (case_id.rsplit("_t", 1)[-1].isdigit() and "_t" in case_id):
        return "multiturn_session"
    if group == "routing":
        return "route_boundary"
    if group == "negative":
        return "negative_guard"
    if group == "pc_build":
        return "pc_build_structured"
    if group == "pc_parts":
        return "pc_part_rag"
    if case_id.startswith("ambiguous_") or (group == "ecommerce" and not relevant_ids(case)):
        return "ambiguous_llm_needed"
    if group == "ecommerce":
        return "in_catalog_ecommerce_rag"
    return group or "unknown"


def case_requires_rag(case: Dict[str, Any], spec: Dict[str, Any], eval_bucket: str) -> bool:
    if not spec.get("rag"):
        return False
    explicit = case.get("requires_rag")
    if explicit is not None:
        return bool(explicit)
    return eval_bucket in {"in_catalog_ecommerce_rag", "pc_part_rag"}


def diagnose_rag(
    *,
    case: Dict[str, Any],
    spec: Dict[str, Any],
    retrieval: Dict[str, Any],
    rag_actually_used: bool,
    ranked_ids: List[str],
    no_match_reason: Any,
    requires_rag: bool,
) -> Dict[str, Any]:
    raw_hits = int(retrieval.get("milvus_raw_hit_count") or 0)
    before_postprocess = int(retrieval.get("retrieved_chunk_count_before_postprocess") or raw_hits or 0)
    after_postprocess = int(retrieval.get("retrieved_chunk_count_after_postprocess") or retrieval.get("retrieved_chunk_count") or retrieval.get("total_hits") or 0)
    retrieval_status = str(retrieval.get("status") or "")
    retrieval_error = str(retrieval.get("retrieval_error") or retrieval.get("error") or "")
    timeout = bool(retrieval.get("retrieval_timeout") or retrieval_status == "timeout")
    positive = is_positive_eval_case(case)
    final_hit = normalized_id_overlap(ranked_ids, relevant_ids(case)) or relaxed_pc_part_hit(case, ranked_ids)

    reason = ""
    message = ""
    case_status_value = "ok"
    chain_valid = False

    if not spec.get("rag") or not requires_rag:
        return {
            "reason": "" if not spec.get("rag") else "not_applicable",
            "message": "" if not spec.get("rag") else "该 case 不适用于 RAG 链路判定",
            "case_status": "ok",
            "chain_valid": None,
            "raw_hits": raw_hits,
            "before_postprocess": before_postprocess,
            "after_postprocess": after_postprocess,
            "final_hit": final_hit,
        }

    if no_match_reason and not positive:
        return {
            "reason": "",
            "message": "",
            "case_status": "ok",
            "chain_valid": None,
            "raw_hits": raw_hits,
            "before_postprocess": before_postprocess,
            "after_postprocess": after_postprocess,
            "final_hit": final_hit,
        }

    if timeout:
        reason = "retrieval_timeout"
        message = "RAG/Milvus 检索超时"
        case_status_value = "failed" if positive else "suspicious"
    elif retrieval_status == "failed" or retrieval_error:
        reason = "retrieval_error"
        message = f"RAG/Milvus 检索错误: {retrieval_error or retrieval_status}"
        case_status_value = "failed" if positive else "suspicious"
    elif not rag_actually_used:
        reason = "milvus_not_used"
        message = "配置要求启用 RAG/Milvus，但 trace 显示没有调用 Milvus"
        case_status_value = "failed" if positive else "suspicious"
    elif raw_hits == 0 and positive:
        reason = "milvus_used_but_no_raw_hits"
        message = "Milvus 已调用，但正例 query 的 raw hits 为 0"
        case_status_value = "failed"
    elif raw_hits > 0 and after_postprocess == 0:
        reason = "raw_hits_found_but_postprocess_empty"
        message = "Milvus raw hits > 0，但 RAG 后处理后 evidence 为 0"
        case_status_value = "failed" if positive and not final_hit else "suspicious"
    elif raw_hits == 0 and after_postprocess > 0:
        reason = "trace_missing_retrieved_count"
        message = "检索可能已执行，但 trace 未传出 raw hit count"
        case_status_value = "suspicious"
    elif positive and not final_hit:
        reason = "final_recommendation_miss"
        message = "RAG 已执行，但最终推荐完全未命中期望商品"
        case_status_value = "failed"
    else:
        chain_valid = bool(rag_actually_used and raw_hits > 0 and after_postprocess > 0)

    return {
        "reason": reason,
        "message": message,
        "case_status": case_status_value,
        "chain_valid": chain_valid,
        "raw_hits": raw_hits,
        "before_postprocess": before_postprocess,
        "after_postprocess": after_postprocess,
        "final_hit": final_hit,
    }


def recommendation_source(ranked_ids: Sequence[str], retrieval: Dict[str, Any]) -> str:
    retrieved_ids = set(retrieval.get("retrieved_product_ids") or retrieval.get("matched_product_ids") or [])
    if retrieved_ids and set(ranked_ids) & retrieved_ids:
        return "rag_evidence"
    if retrieval.get("status") in {"ok", "empty", "partial", "failed", "timeout", "no_collection"}:
        return "structured_catalog_fallback_after_rag"
    return "structured_catalog"


def is_positive_eval_case(case: Dict[str, Any]) -> bool:
    return not case.get("expected_no_match_reason") and bool(relevant_ids(case))


def relevant_ids(case: Dict[str, Any]) -> set:
    return set(case.get("expected_product_ids") or []) | set(case.get("acceptable_product_ids") or [])


def normalized_relevant_ids(case: Dict[str, Any]) -> set:
    return {normalize_product_id_for_eval(item) for item in relevant_ids(case)}


def case_status(
    *,
    case: Dict[str, Any],
    spec: Dict[str, Any],
    route_correct: bool,
    rag_diagnostic: Dict[str, Any],
    llm_used: bool,
    no_match_reason: str | None,
    ranked_ids: List[str],
    errors: List[str],
) -> tuple[str, str]:
    if errors:
        return "failed", "; ".join(errors)
    if not route_correct:
        return "failed", "tool route 不符合预期"
    if rag_diagnostic["case_status"] in {"failed", "suspicious"}:
        return rag_diagnostic["case_status"], rag_diagnostic["message"]
    if spec["llm"] and not llm_used:
        return "suspicious", "配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用"
    expected_no_match = case.get("expected_no_match_reason")
    if expected_no_match and ranked_ids:
        return "failed", "负例/缺失品类返回了推荐商品"
    if expected_no_match and not no_match_reason:
        return "failed", "no_match_reason 缺失"
    if expected_no_match and no_match_reason and not no_match_reason_matches(str(expected_no_match), str(no_match_reason)):
        return "failed", f"no_match_reason 不符合预期: {no_match_reason}"
    return "ok", ""


def no_match_reason_matches(expected: str, actual: str) -> bool:
    expected_norm = expected.lower()
    actual_norm = actual.lower()
    if expected_norm in actual_norm:
        return True
    if expected_norm == "unsupported" and actual_norm in {"unsupported_category", "safety_restricted_category"}:
        return True
    return False


def metrics_for_case(
    case: Dict[str, Any],
    ranked_ids: List[str],
    tool_name: str,
    route_correct: bool,
    no_match_reason: Any,
    latency_ms: float,
    status: str,
    spec: Dict[str, Any],
    rag_used: bool,
    llm_router: bool,
    llm_parse: bool,
    llm_enhance: bool,
    trace: Dict[str, Any],
    rag_diagnostic: Dict[str, Any],
    eval_bucket: str,
    requires_rag: bool,
    pc_build_quality: Dict[str, Any],
) -> Dict[str, Any]:
    expected = {normalize_product_id_for_eval(item) for item in case.get("expected_product_ids") or []}
    relaxed = expected | {normalize_product_id_for_eval(item) for item in case.get("acceptable_product_ids") or []}
    normalized_ranked_ids = [normalize_product_id_for_eval(item) for item in ranked_ids]
    expected_category = case.get("expected_category")
    expected_component = case.get("expected_component_type")
    empty = not bool(ranked_ids)
    out: Dict[str, Any] = {
        "mrr": mrr(normalized_ranked_ids, relaxed),
        "empty_result": int(empty),
        "no_match_accuracy": int(not case.get("expected_no_match_reason") or bool(no_match_reason)),
        "negative_no_recommendation_accuracy": int(not case.get("expected_no_match_reason") or empty),
        "missing_subcategory_accuracy": int(case.get("expected_no_match_reason") != "missing_subcategory" or str(no_match_reason) == "missing_subcategory"),
        "tool_route_accuracy": int(route_correct),
        "pc_build_route_accuracy": int(case.get("case_group") != "pc_build" or tool_name == "generate_pc_build_plan"),
        "ecommerce_route_accuracy": int(case.get("case_group") != "ecommerce" or tool_name == "recommend_shopping_products"),
        "general_chat_route_accuracy": int(case.get("expected_tool") != "general_chat" or tool_name == "general_chat"),
        "rag_usage_rate": int(rag_used),
        "milvus_usage_rate": int(rag_used),
        "llm_router_usage_rate": int(llm_router),
        "llm_requirement_parse_usage_rate": int(llm_parse),
        "full_chain_valid": int(spec["rag"] and spec["llm"] and rag_used and (llm_router or llm_parse or llm_enhance) and status == "ok"),
        "rag_chain_valid": int(bool(rag_diagnostic.get("chain_valid"))) if requires_rag else None,
        "rag_applicable": int(requires_rag),
        "pc_build_chain_valid": pc_build_quality.get("pc_build_chain_valid"),
        "pc_build_compatibility_valid": pc_build_quality.get("pc_build_compatibility_valid"),
        "budget_valid": pc_build_quality.get("budget_valid"),
        "latency_ms": latency_ms,
        "error": int(status == "failed"),
        "constraint_violation": int(bool(trace.get("constraint_violations"))),
        "budget_violation": int(any("预算" in str(item) for item in trace.get("risks", []) or [])),
        "excluded_brand_violation": excluded_brand_violation(case, ranked_ids),
    }
    for k in TOP_KS:
        out[f"precision@{k}"] = precision_at_k(normalized_ranked_ids, relaxed, k)
        out[f"hit@{k}"] = hit_at_k(normalized_ranked_ids, relaxed, k)
        out[f"strict_recall@{k}"] = recall_at_k(normalized_ranked_ids, expected, k)
        out[f"relaxed_recall@{k}"] = recall_at_k(normalized_ranked_ids, relaxed, k)
        out[f"category_accuracy@{k}"] = int(not expected_category or category_hit(case, ranked_ids[:k], expected_category))
        out[f"component_type_accuracy@{k}"] = int(not expected_component or category_hit(case, ranked_ids[:k], expected_component))
    return out


def extract_pc_build_quality(payload: Dict[str, Any], tool_name: str, eval_bucket: str) -> Dict[str, Any]:
    if eval_bucket != "pc_build_structured":
        return {"pc_build_chain_valid": None, "pc_build_compatibility_valid": None, "budget_valid": None}
    plan = payload.get("pc_build_plan") if isinstance(payload.get("pc_build_plan"), dict) else payload
    parts = plan.get("parts") or plan.get("items") or []
    compatibility = plan.get("compatibility") or {}
    total_price = number_or_none(plan.get("total_price") or plan.get("total_cost") or plan.get("estimated_total_price"))
    budget = number_or_none(plan.get("budget") or plan.get("budget_max") or plan.get("target_budget"))
    compatibility_ok = compatibility.get("valid")
    if compatibility_ok is None:
        violations = compatibility.get("violations") or compatibility.get("issues") or plan.get("compatibility_issues") or []
        compatibility_ok = len(violations) == 0 if isinstance(violations, list) else None
    budget_ok = None
    if total_price is not None and budget is not None and budget > 0:
        budget_ok = total_price <= budget * 1.08
    return {
        "pc_build_chain_valid": int(tool_name == "generate_pc_build_plan" and bool(parts)),
        "pc_build_compatibility_valid": int(bool(compatibility_ok)) if compatibility_ok is not None else None,
        "budget_valid": int(bool(budget_ok)) if budget_ok is not None else None,
    }


def build_pc_part_diagnostic(row: Dict[str, Any], case: Dict[str, Any], payload: Dict[str, Any], trace: Dict[str, Any]) -> Dict[str, Any] | None:
    if case.get("case_id") not in PC_PART_DEBUG_CASE_IDS:
        return None
    expected_ids = [str(item) for item in case.get("expected_product_ids") or []]
    expected_component = str(case.get("expected_component_type") or case.get("expected_category") or "")
    retrieved_ids = [str(item) for item in row.get("retrieved_product_ids") or []][:10]
    recommended_ids = [str(item) for item in row.get("recommended_product_ids") or []][:10]
    normalized_expected_ids = [normalize_product_id_for_eval(item) for item in expected_ids]
    normalized_retrieved_ids = [normalize_product_id_for_eval(item) for item in retrieved_ids]
    normalized_recommended_ids = [normalize_product_id_for_eval(item) for item in recommended_ids]
    candidate_ids = candidate_product_ids(trace)
    retrieved_contains_expected = bool(set(normalized_retrieved_ids) & set(normalized_expected_ids))
    recommended_contains_expected = bool(set(normalized_recommended_ids) & set(normalized_expected_ids))
    recommended_component_matches = category_hit(case, recommended_ids, expected_component) if expected_component else None
    recommended_specs_match = pc_part_key_specs_match(case, recommended_ids)
    candidate_contains_expected = bool({normalize_product_id_for_eval(item) for item in candidate_ids} & set(normalized_expected_ids))
    if recommended_contains_expected:
        failure_type = "normalized_id_match"
    elif recommended_component_matches and recommended_specs_match:
        failure_type = "relaxed_component_specs_match"
    elif recommended_component_matches:
        failure_type = "expected_too_strict_warning"
    elif recommended_component_matches is False and recommended_ids:
        failure_type = "component_type_mismatch"
    elif not retrieved_contains_expected:
        failure_type = "retrieval_miss_expected"
    elif candidate_contains_expected:
        failure_type = "scoring_or_filtering_issue"
    elif retrieved_contains_expected:
        failure_type = "ranking_miss_expected"
    else:
        failure_type = "component_type_mismatch"
    return {
        "case_id": case["case_id"],
        "mode": row["mode"],
        "status": row["case_status"],
        "query": case["query"],
        "expected_product_ids": expected_ids,
        "normalized_expected_product_ids": normalized_expected_ids,
        "expected_component_type": expected_component,
        "retrieved_product_ids_top10": retrieved_ids,
        "normalized_retrieved_product_ids_top10": normalized_retrieved_ids,
        "recommended_product_ids_top10": recommended_ids,
        "normalized_recommended_product_ids_top10": normalized_recommended_ids,
        "recommended_components_top10": [product_category_label(product_id) for product_id in recommended_ids],
        "retrieved_contains_expected": retrieved_contains_expected,
        "normalized_match": recommended_contains_expected,
        "recommended_component_type_matches_expected": recommended_component_matches,
        "recommended_key_specs_match_expected": recommended_specs_match,
        "candidate_contains_expected": candidate_contains_expected,
        "candidate_count_before_filter": row.get("candidate_count_before_filter"),
        "candidate_count_after_filter": row.get("candidate_count_after_filter"),
        "top_score_breakdown": top_score_breakdown(payload, trace),
        "failure_type": failure_type,
        "rag_failure_reason": row.get("rag_failure_reason"),
        "recommendation_source": row.get("recommendation_source"),
    }


def candidate_product_ids(trace: Dict[str, Any]) -> List[str]:
    scope = trace.get("candidate_scope") or {}
    ids: List[str] = []
    for item in (scope.get("by_category") or {}).values():
        if not isinstance(item, dict):
            continue
        for candidate in item.get("top_candidates") or []:
            append(ids, candidate.get("product_id"))
    return dedupe(ids)


def top_score_breakdown(payload: Dict[str, Any], trace: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = payload.get("comparison_table") or []
    if not rows:
        rows = []
        for item in (trace.get("candidate_scope") or {}).get("by_category", {}).values():
            if isinstance(item, dict):
                rows.extend(item.get("top_candidates") or [])
    out = []
    for item in rows[:5]:
        out.append({
            "product_id": item.get("product_id"),
            "score": item.get("score"),
            "category": item.get("category"),
            "strength": item.get("strength"),
        })
    return out


def product_category_label(product_id: str) -> Dict[str, str]:
    catalog = load_combined_product_catalog().by_id
    pc_catalog = load_pc_parts_product_catalog().by_id
    product = catalog.get(product_id) or pc_catalog.get(product_id)
    if product is None:
        return {"product_id": product_id, "category": "", "sub_category": ""}
    category = getattr(product, "category", "")
    return {
        "product_id": product_id,
        "category": str(getattr(category, "value", category) or ""),
        "sub_category": str(getattr(product, "sub_category", "") or ""),
    }


def normalize_product_id_for_eval(product_id: Any) -> str:
    value = str(product_id or "")
    for prefix in PC_COMPONENT_ID_PREFIXES:
        if value.startswith(prefix + "pc_seed_"):
            value = value[len(prefix):]
            break
    value = re.sub(r"(?:_v[2-9]|_rev[2-9])$", "", value)
    return value


def normalized_id_overlap(ids: Iterable[Any], expected_ids: Iterable[Any]) -> bool:
    normalized_ids = {normalize_product_id_for_eval(item) for item in ids}
    normalized_expected = {normalize_product_id_for_eval(item) for item in expected_ids}
    return bool(normalized_ids & normalized_expected)


def relaxed_pc_part_hit(case: Dict[str, Any], ranked_ids: Sequence[str]) -> bool:
    if case.get("case_group") != "pc_parts":
        return False
    expected_component = str(case.get("expected_component_type") or case.get("expected_category") or "")
    return bool(expected_component and category_hit(case, ranked_ids, expected_component) and pc_part_key_specs_match(case, ranked_ids))


def pc_part_key_specs_match(case: Dict[str, Any], recommended_ids: Sequence[str]) -> bool:
    if case.get("case_group") != "pc_parts" or not recommended_ids:
        return False
    from rag.recommendation.query_guards import parse_pc_part_constraints, product_matches_pc_constraints

    constraints = parse_pc_part_constraints(str(case.get("query") or ""))
    if not constraints:
        return False
    catalog = load_combined_product_catalog().by_id
    pc_catalog = load_pc_parts_product_catalog().by_id
    for product_id in recommended_ids:
        product = catalog.get(product_id) or pc_catalog.get(product_id)
        if product is not None and product_matches_pc_constraints(product, constraints):
            return True
    return False


def number_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def aggregate(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    latencies = [float(row.get("latency_ms") or 0.0) for row in rows]
    metrics = [row.get("metrics") or {} for row in rows]
    out = {
        "case_count": len(rows),
        "failed_count": sum(1 for row in rows if row.get("case_status") == "failed"),
        "suspicious_count": sum(1 for row in rows if row.get("case_status") == "suspicious"),
        "rag_applicable_count": sum(1 for row in rows if row.get("requires_rag")),
        "rag_not_applicable_count": sum(1 for row in rows if not row.get("requires_rag")),
        "latency_avg_ms": round(mean(latencies), 2),
        "latency_p50_ms": round(percentile(latencies, 50), 2),
        "latency_p95_ms": round(percentile(latencies, 95), 2),
        "error_rate": mean(metric.get("error", 0) for metric in metrics),
    }
    names = [
        "precision@1", "precision@3", "precision@5", "hit@1", "hit@3", "hit@5", "mrr",
        "strict_recall@1", "strict_recall@3", "strict_recall@5", "relaxed_recall@1", "relaxed_recall@3", "relaxed_recall@5",
        "category_accuracy@1", "category_accuracy@5", "component_type_accuracy@1", "component_type_accuracy@5",
        "constraint_violation", "budget_violation", "excluded_brand_violation", "empty_result",
        "no_match_accuracy", "negative_no_recommendation_accuracy", "missing_subcategory_accuracy",
        "tool_route_accuracy", "pc_build_route_accuracy", "ecommerce_route_accuracy", "general_chat_route_accuracy",
        "rag_usage_rate", "milvus_usage_rate", "llm_router_usage_rate", "llm_requirement_parse_usage_rate", "full_chain_valid", "rag_chain_valid",
        "rag_applicable", "pc_build_chain_valid", "pc_build_compatibility_valid", "budget_valid",
    ]
    for name in names:
        out[name] = round(mean(metric.get(name, 0) for metric in metrics), 4)
    out["constraint_violation_rate"] = out.pop("constraint_violation")
    out["budget_violation_rate"] = out.pop("budget_violation")
    out["excluded_brand_violation_rate"] = out.pop("excluded_brand_violation")
    out["empty_result_rate"] = out.pop("empty_result")
    out["full_chain_valid_rate"] = out.pop("full_chain_valid")
    out["rag_chain_valid_rate"] = out.pop("rag_chain_valid")
    out["rag_applicable_rate"] = out.pop("rag_applicable")
    out["pc_build_chain_valid_rate"] = out.pop("pc_build_chain_valid")
    out["pc_build_compatibility_valid_rate"] = out.pop("pc_build_compatibility_valid")
    out["budget_valid_rate"] = out.pop("budget_valid")
    return out


def render_markdown(report: Dict[str, Any]) -> str:
    lines = ["# MallMind full 链路消融评估报告", ""]
    summary = report["summary"]
    lines.extend([
        "## 总览",
        "",
        "| 指标 | 值 |",
        "| --- | ---: |",
        f"| 总 case 数 | {summary['case_count']} |",
        f"| failed | {summary['failed_count']} |",
        f"| suspicious | {summary['suspicious_count']} |",
        f"| RAG 适用 case 数 | {summary['rag_applicable_count']} |",
        f"| RAG 不适用 case 数 | {summary['rag_not_applicable_count']} |",
        f"| rag_chain_valid_rate | {summary['rag_chain_valid_rate']:.4f} |",
        f"| full_chain_valid_rate | {summary['full_chain_valid_rate']:.4f} |",
        f"| latency avg / p50 / p95 | {summary['latency_avg_ms']} / {summary['latency_p50_ms']} / {summary['latency_p95_ms']} ms |",
        "",
        "## 四组模式对比",
        "",
        "| mode | cases | P@1 | Hit@5 | route | RAG | Milvus | raw hit | LLM parse | rag valid | full valid | failed | suspicious | avg ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for mode, item in report["mode_summaries"].items():
        mode_rows = [row for row in report.get("per_case", []) if row.get("mode") == mode]
        raw_hit_rate = mean(1 if int(row.get("milvus_raw_hit_count") or 0) > 0 else 0 for row in mode_rows)
        lines.append(
            f"| {mode} | {item['case_count']} | {item['precision@1']:.3f} | {item['hit@5']:.3f} | "
            f"{item['tool_route_accuracy']:.3f} | {item['rag_usage_rate']:.3f} | {item['milvus_usage_rate']:.3f} | "
            f"{raw_hit_rate:.3f} | {item['llm_requirement_parse_usage_rate']:.3f} | {item['rag_chain_valid_rate']:.3f} | {item['full_chain_valid_rate']:.3f} | "
            f"{item['failed_count']} | {item['suspicious_count']} | {item['latency_avg_ms']} |"
        )
    lines.extend([
        "",
        "## 评估桶汇总",
        "",
        "| bucket | cases | RAG applicable | rag valid | route | failed | suspicious | note |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ])
    notes = {
        "in_catalog_ecommerce_rag": "电商正例检索链路",
        "pc_part_rag": "PC 单配件检索链路",
        "pc_build_structured": "结构化装机规划，不默认要求 Milvus",
        "negative_guard": "负例/安全/不支持品类 guard",
        "ambiguous_llm_needed": "模糊需求，rag_only 下多为口径不适用",
        "route_boundary": "路由边界与会话指令",
        "multiturn_session": "多轮会话，rag_only 下仅记录路由/澄清",
    }
    for bucket, item in report.get("eval_bucket_summaries", {}).items():
        lines.append(
            f"| {bucket} | {item.get('case_count', 0)} | {item.get('rag_applicable_count', 0)} | "
            f"{item.get('rag_chain_valid_rate', 0):.3f} | {item.get('tool_route_accuracy', 0):.3f} | "
            f"{item.get('failed_count', 0)} | {item.get('suspicious_count', 0)} | {md(notes.get(bucket, ''))} |"
        )
    for title, key in [
        ("ecommerce summary", "ecommerce"),
        ("pc_parts summary", "pc_parts"),
        ("pc_build summary", "pc_build"),
        ("negative/no-match summary", "negative"),
    ]:
        item = report["domain_summaries"].get(key) or {}
        lines.extend([
            "",
            f"## {title}",
            "",
            f"- case_count: {item.get('case_count', 0)}",
            f"- precision@1: {item.get('precision@1', 0):.3f}",
            f"- hit@5: {item.get('hit@5', 0):.3f}",
            f"- no_match_accuracy: {item.get('no_match_accuracy', 0):.3f}",
            f"- route_accuracy: {item.get('tool_route_accuracy', 0):.3f}",
            f"- latency_avg_ms: {item.get('latency_avg_ms', 0)}",
        ])
    lines.extend([
        "",
        "## failed/suspicious case 明细",
        "",
        "| case | mode | status | tool | RAG | raw | after | source | rag_reason | LLM(router/parse/enhance) | failed_reason |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |",
    ])
    for row in report["failed_or_suspicious_cases"][:80]:
        llm = f"{int(row['llm_router_used'])}/{int(row['llm_requirement_parse_used'])}/{int(row['llm_enhancement_used'])}"
        lines.append(
            f"| {md(row['case_id'])} | {row['mode']} | {row['case_status']} | {md(row['tool_name'])} | "
            f"{int(row['rag_actually_used'])} | {row.get('milvus_raw_hit_count', 0)} | {row.get('retrieved_chunk_count_after_postprocess', 0)} | "
            f"{md(row.get('recommendation_source') or '')} | {md(row.get('rag_failure_reason') or '')} | {llm} | {md(row.get('failed_reason') or '')} |"
        )
    lines.extend(render_issue_sections(report))
    lines.extend(render_pc_part_diagnostics(report))
    lines.extend(render_conclusion_section(report))
    return "\n".join(lines)


def render_issue_sections(report: Dict[str, Any]) -> List[str]:
    rows = report.get("per_case", [])
    sections = [
        ("RAG 链路问题", [row for row in rows if row.get("requires_rag") and row.get("rag_failure_reason")]),
        ("路由问题", [row for row in rows if not row.get("route_correct")]),
        ("负例 guard 问题", [row for row in rows if row.get("eval_bucket") == "negative_guard" and row.get("case_status") != "ok"]),
        ("PC build 结构化规划问题", [row for row in rows if row.get("eval_bucket") == "pc_build_structured" and row.get("case_status") != "ok"]),
        ("评估口径不适用项", [row for row in rows if row.get("rag_applicability") == "rag_not_applicable"]),
    ]
    lines: List[str] = ["", "## 问题分类", ""]
    for title, items in sections:
        lines.extend([f"### {title}", ""])
        if not items:
            lines.append("- 无")
            lines.append("")
            continue
        lines.extend([
            "| case | mode | bucket | status | tool | reason |",
            "| --- | --- | --- | --- | --- | --- |",
        ])
        for row in items[:40]:
            reason = row.get("failed_reason") or row.get("rag_failure_reason") or row.get("rag_applicability") or ""
            lines.append(
                f"| {md(row.get('case_id'))} | {md(row.get('mode'))} | {md(row.get('eval_bucket'))} | "
                f"{md(row.get('case_status'))} | {md(row.get('tool_name'))} | {md(reason)} |"
            )
        lines.append("")
    return lines


def render_pc_part_diagnostics(report: Dict[str, Any]) -> List[str]:
    diagnostics = report.get("pc_part_diagnostics") or []
    lines = ["", "## PC 单配件 final_recommendation_miss 诊断", ""]
    if not diagnostics:
        lines.append("- 本次报告没有命中指定 PC 单配件诊断 case。")
        return lines
    lines.extend([
        "| case | mode | failure_type | expected | normalized expected | retrieved top10 | normalized retrieved top10 | recommended top10 | normalized recommended top10 | normalized match | rec category | retrieved has expected | component/spec match | candidates before/after | score top |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- | ---: | --- | --- | --- |",
    ])
    for item in diagnostics:
        score_top = item.get("top_score_breakdown") or []
        score_brief = "; ".join(
            f"{row.get('product_id')}:{row.get('score')}"
            for row in score_top[:3]
            if isinstance(row, dict)
        )
        rec_categories = ", ".join(
            f"{row.get('product_id')}:{row.get('category')}/{row.get('sub_category')}"
            for row in item.get("recommended_components_top10", [])[:3]
            if isinstance(row, dict)
        )
        lines.append(
            f"| {md(item.get('case_id'))} | {md(item.get('mode'))} | {md(item.get('failure_type'))} | "
            f"{md(','.join(item.get('expected_product_ids') or []))} / {md(item.get('expected_component_type'))} | "
            f"{md(','.join(item.get('normalized_expected_product_ids') or []))} | "
            f"{md(','.join(item.get('retrieved_product_ids_top10') or []))} | "
            f"{md(','.join(item.get('normalized_retrieved_product_ids_top10') or []))} | "
            f"{md(','.join(item.get('recommended_product_ids_top10') or []))} | "
            f"{md(','.join(item.get('normalized_recommended_product_ids_top10') or []))} | "
            f"{int(bool(item.get('normalized_match')))} | {md(rec_categories)} | "
            f"{int(bool(item.get('retrieved_contains_expected')))} | "
            f"{int(bool(item.get('recommended_component_type_matches_expected')))}/{int(bool(item.get('recommended_key_specs_match_expected')))} | "
            f"{item.get('candidate_count_before_filter')}/{item.get('candidate_count_after_filter')} | {md(score_brief)} |"
        )
    return lines


def render_conclusion_section(report: Dict[str, Any]) -> List[str]:
    modes = list((report.get("config") or {}).get("modes") or report.get("mode_summaries", {}).keys())
    lines = ["", "## 链路真实性结论", ""]
    if len(modes) == 1:
        mode = modes[0]
        item = report["mode_summaries"].get(mode) or {}
        if mode == "rag_only":
            conclusion = "通过" if item.get("rag_chain_valid_rate", 0) > 0 and item.get("failed_count", 0) == 0 else "未通过"
            lines.append(
                f"RAG-only 结论：{conclusion}。Milvus 使用率 {item.get('milvus_usage_rate', 0):.3f}，"
                f"RAG 链路有效率 {item.get('rag_chain_valid_rate', 0):.3f}。LLM parse 使用率为 "
                f"{item.get('llm_requirement_parse_usage_rate', 0):.3f}，在 rag_only 模式下这是预期值。"
            )
        elif mode == "llm_only":
            conclusion = "通过" if item.get("llm_requirement_parse_usage_rate", 0) > 0 and item.get("failed_count", 0) == 0 else "需关注"
            lines.append(
                f"LLM-only 结论：{conclusion}。LLM 需求解析使用率 {item.get('llm_requirement_parse_usage_rate', 0):.3f}，"
                f"Milvus 使用率 {item.get('milvus_usage_rate', 0):.3f}，RAG 关闭符合预期。"
            )
        elif mode == "fast_no_llm_no_rag":
            lines.append(
                f"Baseline 结论：规则/结构化 baseline 已运行。Milvus 使用率 {item.get('milvus_usage_rate', 0):.3f}，"
                f"LLM parse 使用率 {item.get('llm_requirement_parse_usage_rate', 0):.3f}，均应接近 0。"
            )
        elif mode == "full":
            conclusion = "通过" if item.get("full_chain_valid_rate", 0) > 0 and item.get("failed_count", 0) == 0 else "未通过"
            lines.append(
                f"Full 链路结论：{conclusion}。Milvus 使用率 {item.get('milvus_usage_rate', 0):.3f}，"
                f"LLM 需求解析使用率 {item.get('llm_requirement_parse_usage_rate', 0):.3f}，"
                f"full_chain_valid_rate {item.get('full_chain_valid_rate', 0):.3f}。"
            )
    else:
        parts = []
        for mode in modes:
            item = report["mode_summaries"].get(mode) or {}
            parts.append(
                f"{mode}: RAG {item.get('rag_chain_valid_rate', 0):.3f}, full {item.get('full_chain_valid_rate', 0):.3f}, failed {item.get('failed_count', 0)}"
            )
        lines.append("综合消融结论：" + "；".join(parts) + "。")
    lines.append("")
    return lines


@contextmanager
def mode_environment(spec: Dict[str, Any], *, disable_guidance: bool = False, retrieval_timeout_seconds: float | None = None):
    keys = [
        "MALLMIND_LLM_ENABLED",
        "RECOMMENDATION_ENABLE_MILVUS",
        "RECOMMENDATION_USE_MILVUS",
        "RECOMMENDATION_LLM_GUIDANCE",
        "RECOMMENDATION_LLM_PARSE",
        "RECOMMENDATION_RETRIEVAL_TIMEOUT_SECONDS",
    ]
    old = {key: os.getenv(key) for key in keys}
    os.environ["MALLMIND_LLM_ENABLED"] = "true" if spec["llm"] else "false"
    os.environ["RECOMMENDATION_ENABLE_MILVUS"] = "true" if spec["rag"] else "false"
    os.environ["RECOMMENDATION_USE_MILVUS"] = "true" if spec["rag"] else "false"
    os.environ["RECOMMENDATION_LLM_GUIDANCE"] = "true" if spec["llm"] and not disable_guidance else "false"
    os.environ["RECOMMENDATION_LLM_PARSE"] = "auto"
    if retrieval_timeout_seconds is not None:
        os.environ["RECOMMENDATION_RETRIEVAL_TIMEOUT_SECONDS"] = str(retrieval_timeout_seconds)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def patch_retrieval_flag(enabled: bool, *, retrieval_timeout_seconds: float | None = None) -> None:
    import rag.recommendation.package_builder as package_builder

    package_builder.MILVUS_RETRIEVAL_ENABLED = bool(enabled)
    if retrieval_timeout_seconds is not None:
        package_builder.RETRIEVAL_TIMEOUT_SECONDS = float(retrieval_timeout_seconds)


def load_cases(path: Path, *, limit: int | None, case_id: str | None) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("cases", data) if isinstance(data, dict) else data
    out = [row for row in rows if not case_id or row.get("case_id") == case_id]
    return out[:limit] if limit else out


def pc_payload(plan: Dict[str, Any], tool_call: Dict[str, Any]) -> Dict[str, Any]:
    trace = dict(plan.get("trace") or {})
    trace["tool_call"] = tool_call
    trace["retrieval"] = {
        "status": "not_applicable_pc_build_local_catalog",
        "retrieval_backend": trace.get("retrieval_mode", "local_pc_catalog"),
        "retrieved_chunk_count": trace.get("retrieved_chunk_count", 0),
        "matched_product_ids": trace.get("matched_product_ids", []),
    }
    return {"type": "pc_build_plan", "pc_build_plan": plan, "trace": trace, "product_cards": plan.get("parts") or []}


def extract_product_ids(payload: Dict[str, Any]) -> List[str]:
    ids: List[str] = []
    for card in payload.get("product_cards") or []:
        append(ids, card.get("product_id") or card.get("part_id"))
    plan = payload.get("pc_build_plan") if isinstance(payload.get("pc_build_plan"), dict) else payload
    for item in plan.get("parts") or plan.get("items") or []:
        append(ids, item.get("product_id") or item.get("part_id"))
    for plan in payload.get("plans") or []:
        for comp in plan.get("components") or []:
            product = comp.get("product") or {}
            append(ids, comp.get("product_id") or product.get("product_id"))
    return dedupe(ids)


def extract_titles(payload: Dict[str, Any]) -> List[str]:
    titles = []
    for card in payload.get("product_cards") or []:
        if card.get("title"):
            titles.append(str(card["title"]))
    plan = payload.get("pc_build_plan") if isinstance(payload.get("pc_build_plan"), dict) else payload
    for item in plan.get("parts") or []:
        if item.get("title"):
            titles.append(str(item["title"]))
    return titles[:10]


def category_hit(case: Dict[str, Any], ids: Iterable[str], expected: str) -> bool:
    expected = str(expected or "").lower()
    catalog = load_combined_product_catalog().by_id
    pc_catalog = load_pc_parts_product_catalog().by_id
    for product_id in ids:
        product = catalog.get(product_id) or pc_catalog.get(product_id)
        if product is None:
            if expected in str(product_id).lower():
                return True
            continue
        values = [str(getattr(product, "category", "")).lower(), str(getattr(getattr(product, "category", ""), "value", "")).lower(), str(getattr(product, "sub_category", "")).lower()]
        if expected in values or any(expected in item for item in values):
            return True
    return False


def candidate_count_before(trace: Dict[str, Any], payload: Dict[str, Any]) -> int:
    return int(trace.get("catalog_product_count") or payload.get("candidate_count") or 0)


def candidate_count_after(trace: Dict[str, Any], payload: Dict[str, Any]) -> int:
    scope = trace.get("candidate_scope") or payload.get("candidate_scope") or {}
    total = 0
    for item in (scope.get("by_category") or {}).values():
        if isinstance(item, dict):
            total += int(item.get("after_exclusion_count") or 0)
    return total or len(extract_product_ids(payload))


def compact_trace(trace: Dict[str, Any], routing_trace: Dict[str, Any]) -> Dict[str, Any]:
    retrieval = trace.get("milvus_retrieval") or trace.get("retrieval") or {}
    return {
        "runtime": {
            "requested_runtime_mode": trace.get("requested_mode"),
            "resolved_runtime_mode": trace.get("runtime_mode") or trace.get("selected_mode"),
            "llm_enabled": trace.get("stream_llm_enabled"),
            "rag_enabled": (trace.get("runtime_retrieval_policy") or {}).get("use_milvus_retrieval"),
            "vision_enabled": (trace.get("runtime_policy") or {}).get("use_vision_llm"),
        },
        "router": routing_trace,
        "requirement_parsing": trace.get("requirement_parsing") or {},
        "retrieval": retrieval,
        "filtering_scoring": {
            "structured_filter": trace.get("structured_filter") or {},
            "candidate_counts_by_category": trace.get("candidate_counts_by_category") or {},
            "score_breakdown": trace.get("dynamic_weights") or {},
        },
        "response": {
            "no_match_reason": trace.get("no_match_reason"),
            "recommended_product_ids": retrieval.get("matched_product_ids") or [],
        },
    }


def is_milvus_used(retrieval: Dict[str, Any]) -> bool:
    return (retrieval.get("retrieval_backend") == "milvus" or retrieval.get("status") in {"ok", "empty", "partial"}) and retrieval.get("status") != "disabled"


def excluded_brand_violation(case: Dict[str, Any], ranked_ids: List[str]) -> int:
    excluded = [str(item).lower() for item in case.get("exclude_brands") or []]
    if not excluded:
        return 0
    catalog = load_combined_product_catalog().by_id
    for product_id in ranked_ids:
        product = catalog.get(product_id)
        brand = str(getattr(product, "brand", "")).lower() if product else ""
        if any(item in brand for item in excluded):
            return 1
    return 0


def precision_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    relevant_set = set(relevant)
    if not relevant_set:
        return 0.0
    return len(set(ranked[:k]) & relevant_set) / float(k)


def recall_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> float:
    relevant_set = set(relevant)
    if not relevant_set:
        return 0.0
    return len(set(ranked[:k]) & relevant_set) / float(len(relevant_set))


def hit_at_k(ranked: Sequence[str], relevant: Iterable[str], k: int) -> int:
    relevant_set = set(relevant)
    return int(bool(relevant_set and set(ranked[:k]) & relevant_set))


def mrr(ranked: Sequence[str], relevant: Iterable[str]) -> float:
    relevant_set = set(relevant)
    for index, product_id in enumerate(ranked, 1):
        if product_id in relevant_set:
            return 1.0 / index
    return 0.0


def mean(values: Iterable[Any]) -> float:
    nums = []
    for item in values:
        if item is None:
            continue
        nums.append(float(item))
    return float(statistics.mean(nums)) if nums else 0.0


def percentile(values: Sequence[float], pct: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((pct / 100) * (len(ordered) - 1))))
    return ordered[index]


def append(items: List[str], value: Any) -> None:
    if value:
        items.append(str(value))


def dedupe(items: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def safe_int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def sanitize_error(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    for key in ("DASHSCOPE_API_KEY", "EMBEDDING_API_KEY", "OPENAI_API_KEY"):
        secret = os.getenv(key)
        if secret:
            text = text.replace(secret, "***")
    return text


def md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")[:240]


if __name__ == "__main__":
    raise SystemExit(main())
