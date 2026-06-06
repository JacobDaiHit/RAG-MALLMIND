"""Evaluate external model chain combinations for MallMind.

This script is intentionally evaluation-only. It composes existing router,
requirement parsing, RAG, query expansion, and guidance switches without
changing the production recommendation path.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import statistics
import sys
import time
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rag.api.app_context import model_to_dict
from rag.recommendation.llm_client import build_llm_provider_config
from rag.recommendation.comparison import compare_products
from rag.recommendation.package_builder import build_recommendation_result
from rag.recommendation.pc_session_flow import build_pc_plan_for_message
from rag.recommendation.product_loader import load_combined_product_catalog, load_pc_parts_product_catalog
from rag.recommendation.recommendation_pipeline import parse_requirement, recommend_shopping_products
from rag.recommendation.session_state import ShoppingSession, remember_pc_build_plan, remember_recommendation
from rag.recommendation.tool_router import route_shopping_tool_call


DEFAULT_CASES = ROOT_DIR / "tests" / "fixtures" / "full_chain_eval_cases.json"
DEFAULT_JSON = ROOT_DIR / "reports" / "model_chain_ablation_eval.json"
DEFAULT_MD = ROOT_DIR / "reports" / "model_chain_ablation_eval.md"
TOP_KS = (1, 3, 5)
PC_COMPONENT_ID_PREFIXES = (
    "pc_motherboard_",
    "pc_psu_",
    "pc_storage_",
    "pc_case_",
    "pc_gpu_",
    "pc_cpu_",
    "pc_memory_",
    "pc_cooler_",
)


@dataclass(frozen=True)
class GroupSpec:
    name: str
    label: str
    runtime_mode: str
    router_llm: bool
    requirement_llm: bool
    guidance_llm: bool
    vision_llm: bool
    query_expansion: bool
    milvus: bool
    degraded: bool = False
    note: str = ""


GROUPS: Dict[str, GroupSpec] = {
    "fast_baseline": GroupSpec(
        "fast_baseline",
        "A fast_baseline / no_llm_no_rag",
        "fast",
        False,
        False,
        False,
        False,
        False,
        False,
        note="稳定兜底 baseline：规则路由、规则解析、本地结构化过滤与 catalog scoring。",
    ),
    "rag_only": GroupSpec(
        "rag_only",
        "A2 rag_only / no_llm_milvus",
        "balanced",
        False,
        False,
        False,
        False,
        False,
        True,
        note="隔离 RAG/Milvus/Embedding 贡献；无外部 LLM，但可能有外部 embedding。",
    ),
    "balanced_demo": GroupSpec(
        "balanced_demo",
        "B balanced_demo / llm_router_parse_milvus",
        "balanced",
        True,
        True,
        False,
        False,
        False,
        True,
        note="推荐演示模式：LLM 路由 + LLM 需求解析 + Milvus，关闭重型增强。",
    ),
    "router_llm_only": GroupSpec(
        "router_llm_only",
        "B1 router_llm_only",
        "balanced",
        True,
        False,
        False,
        False,
        False,
        False,
        note="只隔离 LLM 工具路由贡献。",
    ),
    "parse_llm_only": GroupSpec(
        "parse_llm_only",
        "B2 parse_llm_only",
        "balanced",
        False,
        True,
        False,
        False,
        False,
        False,
        note="只隔离 LLM 需求解析贡献。",
    ),
    "full_llm_all": GroupSpec(
        "full_llm_all",
        "C full_llm_all",
        "full",
        True,
        True,
        True,
        True,
        True,
        True,
        note="效果上限与不稳定上限：打开 query expansion 与 guidance；vision 按当前项目能力记录。",
    ),
    "full_no_guidance": GroupSpec(
        "full_no_guidance",
        "C1 full_no_guidance",
        "full",
        True,
        True,
        False,
        True,
        True,
        True,
        note="隔离 guidance 文案增强影响。",
    ),
    "full_no_query_expansion": GroupSpec(
        "full_no_query_expansion",
        "C2 full_no_query_expansion",
        "full",
        True,
        True,
        True,
        True,
        False,
        True,
        note="隔离 query expansion / HyDE / step-back 影响。",
    ),
    "timeout_fallback": GroupSpec(
        "timeout_fallback",
        "D timeout_fallback / degraded",
        "balanced",
        True,
        True,
        True,
        False,
        False,
        True,
        degraded=True,
        note="故障注入组：极低 LLM/RAG timeout，验证 trace、降级和非 500 行为。",
    ),
}


CASE_TYPE_LABELS = {
    "basic_recommendation": "基础推荐",
    "conditional_filter": "条件筛选",
    "ambiguous_recommendation": "模糊推荐",
    "multiturn": "多轮",
    "comparison": "对比",
    "pc_part": "PC 单配件",
    "pc_build": "PC 整机",
    "negative": "负例/catalog gap",
    "query_expansion_risk": "query expansion 风险",
    "guidance_hallucination": "guidance 幻觉",
    "routing": "路由边界",
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--groups", default="all", help="逗号分隔的实验组，或 all。")
    parser.add_argument("--case-filter", help="逗号分隔 case type 或 case_group。")
    parser.add_argument("--case-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--include-degraded", action="store_true", help="包含 timeout_fallback 组。")
    parser.add_argument("--output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MD)
    parser.add_argument("--llm-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--router-timeout-seconds", type=float, default=5.0)
    parser.add_argument("--retrieval-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--llm-provider", choices=("ark", "deepseek", "mimo", "openai_compatible"))
    parser.add_argument("--llm-base-url")
    parser.add_argument("--llm-model")
    parser.add_argument("--router-model")
    parser.add_argument("--parse-model")
    parser.add_argument("--guidance-model")
    parser.add_argument("--partial-every", type=int, default=0, help="Save a partial report every N rows; 0 disables partial saves.")
    parser.add_argument("--resume", action="store_true", help="Resume from existing output JSON rows and skip completed rows.")
    parser.add_argument("--disable-router-llm", action="store_true", help="评估时强制关闭 router LLM，只测 parse/RAG/guidance 等链路。")
    parser.add_argument("--router-circuit-failures", type=int, default=5, help="router LLM 连续失败多少次后开启熔断，默认 5，避免一次失败让整组 router LLM 未被有效测到。")
    parser.add_argument("--verbose-external-errors", action="store_true", help="显示外部 LLM/embedding/Milvus 失败的完整 traceback。默认静默，报告仍记录失败状态。")
    parser.add_argument("--group-delay-seconds", type=float, default=0.0, help="每个实验组结束后等待的秒数，用于避免触发 API 限流。")
    args = parser.parse_args(argv)

    load_dotenv(ROOT_DIR / ".env")
    configure_logging(args)
    cases = load_cases(args.cases, case_id=args.case_id, limit=args.limit, case_filter=args.case_filter)
    groups = resolve_groups(args.groups, include_degraded=args.include_degraded)

    rows: List[Dict[str, Any]] = load_resume_rows(args.output) if args.resume else []
    completed = {(row.get("group"), row.get("case_id"), row.get("turn_index")) for row in rows}
    started = time.perf_counter()
    interrupted = False
    try:
        for group_index, group in enumerate(groups):
            print(f"开始实验组: {group.name}", flush=True)
            for index, case in enumerate(cases, 1):
                print(f"  [{index}/{len(cases)}] {case.get('case_id')}", flush=True)
                if case_completed(group.name, case, completed):
                    continue
                new_rows = run_case(case, group, args)
                rows.extend(new_rows)
                completed.update((row.get("group"), row.get("case_id"), row.get("turn_index")) for row in new_rows)
                if args.partial_every and len(rows) % max(args.partial_every, 1) < len(new_rows):
                    write_report(rows, groups, args, started, status="partial")
            if args.group_delay_seconds and group_index < len(groups) - 1:
                print(f"  组间等待 {args.group_delay_seconds}s ...", flush=True)
                time.sleep(args.group_delay_seconds)
    except KeyboardInterrupt:
        interrupted = True
        print("评估被中断，正在写出已完成部分报告...", flush=True)

    report = build_report(rows, groups, args, elapsed_ms=round((time.perf_counter() - started) * 1000, 2))
    if interrupted:
        report["status"] = "interrupted"
        report["interrupted"] = True
    write_report_payload(report, args)
    print(f"模型链路组合评估完成: {report['status']}")
    print(f"JSON 报告: {args.output}")
    print(f"Markdown 报告: {args.markdown}")
    return 1 if report["status"] == "failed" else 0


def load_resume_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        key = (row.get("group"), row.get("case_id"), row.get("turn_index"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def case_completed(group_name: str, case: Dict[str, Any], completed: set[tuple[Any, Any, Any]]) -> bool:
    if case.get("turns"):
        parent_id = case.get("case_id")
        return all((group_name, f"{parent_id}_t{index}", index) in completed for index, _turn in enumerate(case.get("turns") or [], 1))
    return (group_name, case.get("case_id"), 1) in completed


def write_report(rows: List[Dict[str, Any]], groups: List[GroupSpec], args: argparse.Namespace, started: float, *, status: str) -> None:
    report = build_report(rows, groups, args, elapsed_ms=round((time.perf_counter() - started) * 1000, 2))
    report["status"] = status
    report["partial"] = status == "partial"
    write_report_payload(report, args)


def write_report_payload(report: Dict[str, Any], args: argparse.Namespace) -> None:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.write_text(render_markdown(report), encoding="utf-8")


def configure_logging(args: argparse.Namespace) -> None:
    if args.verbose_external_errors:
        return
    for name in (
        "rag.recommendation.retrieval",
        "rag.ingestion.embedding",
        "rag.recommendation.llm_client",
        "rag.recommendation.recommendation_pipeline",
        "rag.utils.rag_utils",
    ):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    warnings.filterwarnings("ignore", category=UserWarning, module=r"pandas\..*")
    warnings.filterwarnings("ignore", message=r".*function `init_chat_model`.*")


def resolve_groups(raw: str, *, include_degraded: bool) -> List[GroupSpec]:
    if raw.strip().lower() == "all":
        names = [name for name in GROUPS if include_degraded or name != "timeout_fallback"]
    else:
        names = [item.strip() for item in raw.split(",") if item.strip()]
        if include_degraded and "timeout_fallback" not in names:
            names.append("timeout_fallback")
    invalid = [name for name in names if name not in GROUPS]
    if invalid:
        raise SystemExit(f"未知实验组: {', '.join(invalid)}；可选: {', '.join(GROUPS)}")
    return [GROUPS[name] for name in names]


def load_cases(path: Path, *, case_id: str | None, limit: int | None, case_filter: str | None) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("cases", data) if isinstance(data, dict) else data
    filters = {item.strip() for item in str(case_filter or "").split(",") if item.strip()}
    out = []
    for row in rows:
        ctype = classify_case_type(row)
        if case_id and row.get("case_id") != case_id:
            continue
        if filters and row.get("case_group") not in filters and ctype not in filters:
            continue
        enriched = dict(row)
        enriched["case_type"] = ctype
        out.append(enriched)
    return out[:limit] if limit else out


def run_case(case: Dict[str, Any], group: GroupSpec, args: argparse.Namespace) -> List[Dict[str, Any]]:
    if case.get("turns"):
        session = ShoppingSession(session_id=f"eval-model-chain-{case['case_id']}-{group.name}")
        rows = []
        for index, turn in enumerate(case["turns"], 1):
            merged = {**case, **turn, "case_id": f"{case['case_id']}_t{index}", "parent_case_id": case["case_id"]}
            merged["case_type"] = classify_case_type(merged)
            rows.append(run_single_case(merged, group, session=session, args=args, turn_index=index))
        return rows
    return [run_single_case(case, group, session=ShoppingSession(session_id=f"eval-model-chain-{case['case_id']}-{group.name}"), args=args)]


def run_single_case(
    case: Dict[str, Any],
    group: GroupSpec,
    *,
    session: ShoppingSession,
    args: argparse.Namespace,
    turn_index: int = 1,
) -> Dict[str, Any]:
    started = time.perf_counter()
    errors: List[str] = []
    payload: Dict[str, Any] = {}
    tool_call: Dict[str, Any] = {}
    with group_environment(group, args):
        try:
            session.runtime_mode = group.runtime_mode
            query = case["query"]
            tool_call = route_shopping_tool_call(query, session, use_llm=group.router_llm and not args.disable_router_llm)
            tool_name = tool_call.get("name") or ""
            if tool_name == "generate_pc_build_plan":
                plan = build_pc_plan_for_message(query, session)
                if not plan.get("_transient_comparison"):
                    remember_pc_build_plan(session, query, plan)
                payload = pc_payload(plan, tool_call)
            elif tool_name == "compare_products":
                ids = case.get("product_ids") or []
                if not ids and session.last_result:
                    ids = extract_product_ids_from_payload(session.last_result)[:2]
                payload = {"type": "comparison", "comparison": compare_products(load_combined_product_catalog(), ids), "tool_call": tool_call, "trace": {"tool_call": tool_call}}
            elif tool_name == "apply_cart_instruction":
                payload = {"type": "cart", "tool_call": tool_call, "trace": {"no_match_reason": "cart_action_not_recommendation", "tool_call": tool_call}, "product_cards": []}
            elif tool_name == "general_chat":
                payload = {"type": "general_chat", "tool_call": tool_call, "trace": {"no_match_reason": "general_chat", "tool_call": tool_call}, "product_cards": []}
            else:
                catalog_scope = (tool_call.get("arguments") or {}).get("catalog_scope") or case.get("catalog_scope") or "ecommerce"
                result = recommend_shopping_products(
                    query,
                    use_llm=group.requirement_llm,
                    catalog_scope=catalog_scope,
                    use_milvus_retrieval=group.milvus,
                    use_rag_query_expansion=group.query_expansion,
                    use_llm_guidance=group.guidance_llm,
                    skip_keyword_check=True,
                )
                payload = model_to_dict(result)
                payload.setdefault("trace", {})["tool_call"] = tool_call
                remember_recommendation(session, query, payload)
        except Exception as exc:
            errors.append(sanitize_error(exc))
            payload = {"trace": {}, "product_cards": []}
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return build_row(case, group, payload, tool_call, latency_ms, errors, turn_index)


@contextmanager
def group_environment(group: GroupSpec, args: argparse.Namespace):
    keys = [
        "MALLMIND_LLM_ENABLED",
        "RECOMMENDATION_ENABLE_MILVUS",
        "RECOMMENDATION_USE_MILVUS",
        "RECOMMENDATION_QUERY_EXPANSION",
        "RECOMMENDATION_LLM_PARSE",
        "RECOMMENDATION_LLM_GUIDANCE",
        "RECOMMENDATION_STREAM_USE_LLM",
        "RECOMMENDATION_LLM_ROUTER_TIMEOUT_SECONDS",
        "RECOMMENDATION_LLM_PARSE_TIMEOUT_SECONDS",
        "RECOMMENDATION_LLM_GUIDANCE_TIMEOUT_SECONDS",
        "RECOMMENDATION_RETRIEVAL_TIMEOUT_SECONDS",
        "RECOMMENDATION_ROUTER_LLM_CIRCUIT_FAILURES",
        "RECOMMENDATION_ROUTER_LLM_CIRCUIT_COOLDOWN_SECONDS",
        "LLM_TIMEOUT_SECONDS",
        "MALLMIND_LLM_PROVIDER",
        "MALLMIND_LLM_BASE_URL",
        "MALLMIND_LLM_MODEL",
        "MALLMIND_LLM_TIMEOUT_SECONDS",
        "MALLMIND_ROUTER_MODEL",
        "MALLMIND_PARSE_MODEL",
        "MALLMIND_GUIDANCE_MODEL",
    ]
    old = {key: os.getenv(key) for key in keys}
    router_llm_enabled = bool(group.router_llm and not args.disable_router_llm)
    any_llm = router_llm_enabled or group.requirement_llm or group.guidance_llm or group.vision_llm
    os.environ["MALLMIND_LLM_ENABLED"] = "true" if any_llm else "false"
    os.environ["RECOMMENDATION_ENABLE_MILVUS"] = "true" if group.milvus else "false"
    os.environ["RECOMMENDATION_USE_MILVUS"] = "true" if group.milvus else "false"
    os.environ["RECOMMENDATION_QUERY_EXPANSION"] = "true" if group.query_expansion else "false"
    os.environ["RECOMMENDATION_LLM_PARSE"] = "auto" if group.requirement_llm else "false"
    os.environ["RECOMMENDATION_LLM_GUIDANCE"] = "true" if group.guidance_llm else "false"
    os.environ["RECOMMENDATION_STREAM_USE_LLM"] = "true" if group.guidance_llm else "false"
    os.environ["LLM_TIMEOUT_SECONDS"] = str(args.llm_timeout_seconds)
    os.environ["MALLMIND_LLM_TIMEOUT_SECONDS"] = str(args.llm_timeout_seconds)
    if args.llm_provider:
        os.environ["MALLMIND_LLM_PROVIDER"] = args.llm_provider
    if args.llm_base_url:
        os.environ["MALLMIND_LLM_BASE_URL"] = args.llm_base_url
    if args.llm_model:
        os.environ["MALLMIND_LLM_MODEL"] = args.llm_model
    if args.router_model:
        os.environ["MALLMIND_ROUTER_MODEL"] = args.router_model
    if args.parse_model:
        os.environ["MALLMIND_PARSE_MODEL"] = args.parse_model
    if args.guidance_model:
        os.environ["MALLMIND_GUIDANCE_MODEL"] = args.guidance_model
    os.environ["RECOMMENDATION_LLM_ROUTER_TIMEOUT_SECONDS"] = str(args.router_timeout_seconds)
    os.environ["RECOMMENDATION_LLM_PARSE_TIMEOUT_SECONDS"] = str(args.llm_timeout_seconds)
    os.environ["RECOMMENDATION_LLM_GUIDANCE_TIMEOUT_SECONDS"] = str(args.llm_timeout_seconds)
    os.environ["RECOMMENDATION_RETRIEVAL_TIMEOUT_SECONDS"] = str(args.retrieval_timeout_seconds)
    os.environ["RECOMMENDATION_ROUTER_LLM_CIRCUIT_FAILURES"] = str(max(int(args.router_circuit_failures), 1))
    os.environ["RECOMMENDATION_ROUTER_LLM_CIRCUIT_COOLDOWN_SECONDS"] = "3600"
    if group.degraded:
        os.environ["LLM_TIMEOUT_SECONDS"] = "0.001"
        os.environ["RECOMMENDATION_LLM_ROUTER_TIMEOUT_SECONDS"] = "0.001"
        os.environ["RECOMMENDATION_LLM_PARSE_TIMEOUT_SECONDS"] = "0.001"
        os.environ["RECOMMENDATION_LLM_GUIDANCE_TIMEOUT_SECONDS"] = "0.001"
        os.environ["RECOMMENDATION_RETRIEVAL_TIMEOUT_SECONDS"] = "0.001"
    patch_package_builder(group.milvus, args.retrieval_timeout_seconds if not group.degraded else 0.001)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def patch_package_builder(enabled: bool, timeout_seconds: float) -> None:
    import rag.recommendation.package_builder as package_builder

    package_builder.MILVUS_RETRIEVAL_ENABLED = bool(enabled)
    package_builder.RETRIEVAL_TIMEOUT_SECONDS = float(timeout_seconds)


def build_row(
    case: Dict[str, Any],
    group: GroupSpec,
    payload: Dict[str, Any],
    tool_call: Dict[str, Any],
    latency_ms: float,
    errors: List[str],
    turn_index: int,
) -> Dict[str, Any]:
    trace = payload.get("trace") or {}
    retrieval = trace.get("milvus_retrieval") or trace.get("retrieval") or {}
    routing_trace = tool_call.get("routing_trace") or trace.get("routing_trace") or {}
    provider_config = build_llm_provider_config()
    ranked_ids = [normalize_product_id_for_eval(item) for item in extract_product_ids_from_payload(payload)]
    expected_ids = [normalize_product_id_for_eval(item) for item in relevant_ids(case)]
    expected_tool = case.get("expected_tool")
    tool_name = tool_call.get("name") or payload.get("type") or ""
    route_correct = expected_tool is None or tool_name == expected_tool
    no_match_reason = trace.get("no_match_reason") or trace.get("fallback_blocked_reason")
    positive = bool(expected_ids)
    final_hit = bool(set(ranked_ids) & set(expected_ids))
    negative_ok = not positive and expected_negative_ok(case, no_match_reason, ranked_ids)
    status = "ok" if (route_correct and (final_hit or negative_ok or case_not_ranked(case))) and not errors else "failed"
    if group.degraded and not errors:
        status = "ok" if payload is not None else "failed"

    retrieved_top_ids = retrieval.get("retrieved_product_ids") or retrieval.get("matched_product_ids") or []
    calls = call_counters(group, routing_trace, trace, retrieval, tool_name=tool_name)
    card_metrics = validate_cards(payload, case)
    constraint_violation = constraint_violation_count(case, payload)
    expected_recommended_rank = rank_of_any(ranked_ids, expected_ids)
    expected_retrieved_rank = rank_of_any([normalize_product_id_for_eval(item) for item in retrieved_top_ids], expected_ids)
    rag_card_used = rag_evidence_used_in_card(payload, retrieved_top_ids)
    rag_reason_used = rag_evidence_used_in_reason(payload, retrieved_top_ids)
    failure_type, failure_reason = classify_failure(case, status, route_correct, final_hit, negative_ok, no_match_reason, errors, ranked_ids, tool_name)
    return {
        "case_id": case["case_id"],
        "parent_case_id": case.get("parent_case_id"),
        "turn_index": turn_index,
        "query": case.get("query", ""),
        "group": group.name,
        "group_label": group.label,
        "runtime_mode": group.runtime_mode,
        "llm_provider": trace.get("llm_provider") or routing_trace.get("llm_provider") or provider_config.provider,
        "llm_model": trace.get("llm_model") or provider_config.model,
        "router_model": trace.get("router_model") or routing_trace.get("router_model") or os.getenv("MALLMIND_ROUTER_MODEL") or provider_config.fast_model,
        "parse_model": trace.get("parse_model") or os.getenv("MALLMIND_PARSE_MODEL") or provider_config.fast_model,
        "guidance_model": trace.get("guidance_model") or os.getenv("MALLMIND_GUIDANCE_MODEL") or provider_config.model,
        "llm_configured": bool(trace.get("llm_configured", provider_config.configured)),
        "llm_config_error_code": trace.get("llm_config_error_code") or provider_config.config_error_code,
        "llm_provider_fallback": trace.get("llm_provider_fallback") or ("" if provider_config.configured else "local_rule"),
        "llm_error_sanitized": trace.get("llm_error_sanitized") or "",
        "selected_runtime_mode": trace.get("selected_runtime_mode") or trace.get("selected_mode") or trace.get("runtime_mode") or group.runtime_mode,
        "adaptive_runtime": trace.get("adaptive_decision") or {},
        "explanation_mode": trace.get("explanation_mode") or "skipped",
        "llm_used_for_explanation": bool(trace.get("llm_used_for_explanation")),
        "case_group": case.get("case_group", "unknown"),
        "case_type": case.get("case_type") or classify_case_type(case),
        "expected_tool": expected_tool,
        "actual_tool": tool_name,
        "route_correct": route_correct,
        "expected_product_ids": expected_ids,
        "recommended_product_ids": ranked_ids,
        "retrieved_top_ids": retrieved_top_ids,
        "expected_id_recommended_rank": expected_recommended_rank,
        "expected_id_retrieved_rank": expected_retrieved_rank,
        "expected_category": case.get("expected_category"),
        "expected_category_set": case.get("expected_category_set") or case.get("expected_categories") or [],
        "forbidden_category_set": case.get("forbidden_category_set") or [],
        "no_match_reason": no_match_reason,
        "status": status,
        "failure_type": failure_type,
        "failure_reason": failure_reason,
        "latency_ms": latency_ms,
        "errors": errors,
        "constraint_violation_count": constraint_violation,
        "timeout": bool(errors and any("timeout" in item.lower() or "超时" in item for item in errors)) or bool(retrieval.get("retrieval_timeout") or retrieval.get("status") == "timeout"),
        "json_invalid": json_invalid(trace, routing_trace, errors),
        "fallback_triggered": fallback_triggered(trace, routing_trace, retrieval, errors),
        "degraded_success": bool(group.degraded and status == "ok"),
        "llm_router_used": calls["llm_router_used"],
        "llm_router_attempted": calls["llm_router_attempted"],
        "llm_router_success": calls["llm_router_success"],
        "llm_router_applied": calls["llm_router_applied"],
        "router_attempted": calls["router_attempted"],
        "router_success": calls["router_success"],
        "router_applied": calls["router_applied"],
        "router_timeout": calls["router_timeout"],
        "router_json_invalid": calls["router_json_invalid"],
        "router_skipped_by_policy": calls["router_skipped_by_policy"],
        "router_skipped_high_confidence": calls["router_skipped_high_confidence"],
        "router_final_source": calls["router_final_source"],
        "llm_parse_attempted": calls["llm_parse_attempted"],
        "llm_parse_used": calls["llm_parse_used"],
        "llm_parse_applied": calls["llm_parse_applied"],
        "llm_guidance_attempted": calls["llm_guidance_attempted"],
        "llm_guidance_used": calls["llm_guidance_used"],
        "llm_guidance_applied": calls["llm_guidance_applied"],
        "llm_attempted": calls["llm_attempted"],
        "llm_used": calls["llm_used"],
        "llm_applied": calls["llm_applied"],
        "rag_attempted": calls["rag_attempted"],
        "embedding_success": calls["embedding_success"],
        "milvus_success": calls["milvus_success"],
        "retrieval_nonempty": calls["retrieval_nonempty"],
        "retrieval_timeout": calls["retrieval_timeout"],
        "fallback_to_catalog": calls["fallback_to_catalog"],
        "rag_contribution_evaluable": calls["rag_contribution_evaluable"],
        "rag_evidence_used_in_card": rag_card_used,
        "rag_evidence_used_in_reason": rag_reason_used,
        "rag_changed_top1": False,
        "rag_changed_top3": False,
        "rag_filtered_by_structured_constraints": bool(calls["rag_contribution_evaluable"] and retrieved_top_ids and not set([normalize_product_id_for_eval(item) for item in retrieved_top_ids]) & set(ranked_ids)),
        "category_injected_to_retrieval_query": category_injected_to_retrieval_query(retrieval),
        "query_expansion_used": calls["query_expansion_used"],
        "query_expansion": query_expansion_diagnostics(retrieval, calls["query_expansion_used"]),
        "llm_calls": calls["llm_calls"],
        "embedding_calls": calls["embedding_calls"],
        "milvus_calls": calls["milvus_calls"],
        "guidance_calls": calls["guidance_calls"],
        "card_metrics": card_metrics,
        "grounded_reason_score": card_metrics.get("card_reason_grounded"),
        "hallucination_flag": bool(card_metrics.get("card_hallucination_flag")),
        "llm_changed_route": calls["llm_changed_route"],
        "llm_filled_missing_fields": calls["llm_filled_missing_fields"],
        "llm_triggered_clarification": bool(no_match_reason == "clarification_required" and calls["llm_applied"]),
        "llm_changed_recommendation": False,
        "router_diagnostics": router_diagnostics(routing_trace),
        "parse_diagnostics": parse_diagnostics(trace),
        "guidance_diagnostics": guidance_diagnostics(trace, card_metrics),
        "pc_build_metrics": pc_build_metrics(payload, case),
        "trace_summary": compact_trace(trace, routing_trace),
    }


def classify_case_type(case: Dict[str, Any]) -> str:
    case_id = str(case.get("case_id") or "")
    group = str(case.get("case_group") or "")
    query = str(case.get("query") or "")
    if case.get("parent_case_id") or case.get("turns"):
        return "multiturn"
    if group == "pc_build":
        return "pc_build"
    if group == "pc_parts":
        return "pc_part"
    if group == "negative":
        if "防风" in query or "户外" in query or "summer" in case_id:
            return "query_expansion_risk"
        return "negative"
    if group == "routing":
        if "compare" in case_id:
            return "comparison"
        return "routing"
    if case_id.startswith("ambiguous_"):
        return "ambiguous_recommendation"
    if case.get("exclude_brands") or "under" in case_id or "budget" in case_id:
        return "conditional_filter"
    if "coffee" in case_id:
        return "guidance_hallucination"
    return "basic_recommendation"


def relevant_ids(case: Dict[str, Any]) -> List[str]:
    return dedupe([*(case.get("expected_product_ids") or []), *(case.get("acceptable_product_ids") or [])])


def rank_of_any(ranked_ids: Sequence[Any], expected_ids: Sequence[Any]) -> int | None:
    expected = {normalize_product_id_for_eval(item) for item in expected_ids}
    if not expected:
        return None
    for index, product_id in enumerate(ranked_ids, 1):
        if normalize_product_id_for_eval(product_id) in expected:
            return index
    return None


def normalize_product_id_for_eval(product_id: Any) -> str:
    value = str(product_id or "")
    for prefix in PC_COMPONENT_ID_PREFIXES:
        if value.startswith(prefix + "pc_seed_"):
            value = value[len(prefix):]
            break
    return re.sub(r"(?:_v\d+|_rev\d+)$", "", value)


def case_not_ranked(case: Dict[str, Any]) -> bool:
    return (case.get("case_group") in {"routing", "pc_build"} or classify_case_type(case) in {"comparison", "multiturn"}) and not relevant_ids(case)


def expected_negative_ok(case: Dict[str, Any], no_match_reason: Any, ranked_ids: List[str]) -> bool:
    expected = str(case.get("expected_no_match_reason") or "")
    if expected:
        if expected == "unsupported" and str(no_match_reason or "") in {"unsupported_category", "safety_restricted_category"}:
            return True
        return expected in str(no_match_reason or "")
    if case.get("case_group") == "negative":
        return bool(no_match_reason) or not ranked_ids
    if not relevant_ids(case) and case.get("expected_category"):
        return bool(ranked_ids) or bool(no_match_reason)
    return not relevant_ids(case)


def classify_failure(
    case: Dict[str, Any],
    status: str,
    route_correct: bool,
    final_hit: bool,
    negative_ok: bool,
    no_match_reason: Any,
    errors: List[str],
    ranked_ids: List[str],
    tool_name: str,
) -> tuple[str, str]:
    if status == "ok":
        return "none", ""
    if errors:
        return "script_or_chain_error", "; ".join(errors)[:400]
    if not route_correct:
        return "wrong_route", f"期望路由 {case.get('expected_tool')}，实际 {tool_name}"
    if case.get("case_group") == "negative" and not negative_ok:
        return "negative_guard_failed", f"期望 no-match={case.get('expected_no_match_reason')}，实际 reason={no_match_reason}，推荐={ranked_ids[:5]}"
    if relevant_ids(case) and not final_hit:
        return "final_recommendation_miss", f"Top 结果未命中期望商品，推荐={ranked_ids[:5]}"
    return "business_failed", "结果与验收预期不一致"


def call_counters(group: GroupSpec, routing_trace: Dict[str, Any], trace: Dict[str, Any], retrieval: Dict[str, Any], *, tool_name: str = "") -> Dict[str, Any]:
    llm_payload = routing_trace.get("llm") or {}
    chosen_before_guard = routing_trace.get("chosen_before_guard") or {}
    final_route = routing_trace.get("final") or {}
    router_attempted = bool(routing_trace.get("router_attempted", group.router_llm and not routing_trace.get("llm_skipped")))
    router_success = bool(routing_trace.get("router_success", bool(llm_payload.get("name")) and router_attempted))
    router_applied_trace = routing_trace.get("router_applied")
    llm_router_attempted = router_attempted
    llm_router_success = router_success
    llm_router_applied = bool(
        router_applied_trace
        if router_applied_trace is not None
        else (
            llm_router_success
            and chosen_before_guard.get("name") == llm_payload.get("name")
            and final_route.get("name") == llm_payload.get("name")
            and not routing_trace.get("guard_overridden")
        )
    )
    llm_router_used = llm_router_success
    parse_trace = trace.get("requirement_parsing") or {}
    llm_parse_requested = bool(parse_trace.get("llm_parse_requested") or (group.requirement_llm and tool_name == "recommend_shopping_products"))
    llm_parse_used = bool(trace.get("llm_requirement_parse_used") or parse_trace.get("llm_parse_used"))
    llm_parse_applied = llm_parse_used
    llm_filled_missing_fields = bool(
        llm_parse_applied
        and (
            parse_trace.get("llm_filled_missing_fields")
            or parse_trace.get("llm_changed_category")
            or parse_trace.get("llm_changed_budget")
            or parse_trace.get("llm_changed_excluded_brands")
            or parse_trace.get("llm_changed_attributes")
        )
    )
    guidance_status = str(trace.get("llm_guidance") or "")
    guidance_attempted = bool(group.guidance_llm and guidance_status not in {"", "disabled"})
    llm_guidance_used = guidance_status == "enabled"
    llm_guidance_applied = llm_guidance_used
    query_expansion_used = bool(
        trace.get("use_rag_query_expansion")
        or (retrieval.get("query_expansion") or {}).get("enabled")
        or retrieval.get("query_expansion_enabled")
    )
    retrieval_status = str(retrieval.get("status") or "")
    retrieval_backend = str(retrieval.get("retrieval_backend") or "")
    retrieved_top_ids = retrieval.get("retrieved_product_ids") or retrieval.get("matched_product_ids") or []
    rag_attempted = bool(group.milvus and retrieval_status and retrieval_status != "disabled")
    retrieval_timeout = bool(retrieval.get("retrieval_timeout") or retrieval_status == "timeout")
    milvus_success = bool(rag_attempted and retrieval_backend == "milvus" and retrieval_status in {"ok", "empty", "partial"})
    embedding_success = bool(milvus_success or (rag_attempted and retrieved_top_ids))
    retrieval_nonempty = bool(retrieved_top_ids)
    fallback_to_catalog = bool(
        group.milvus
        and (
            not rag_attempted
            or retrieval_status in {"disabled", "timeout", "failed", "no_collection"}
            or not retrieval_nonempty
        )
    )
    rag_contribution_evaluable = bool(rag_attempted and embedding_success and milvus_success and retrieval_nonempty and not retrieval_timeout)
    llm_calls = int(llm_router_attempted) + int(llm_parse_requested) + int(guidance_attempted)
    llm_attempted = bool(llm_router_attempted or llm_parse_requested or guidance_attempted)
    llm_used = bool(llm_router_success or llm_parse_used or llm_guidance_used)
    llm_applied = bool(llm_router_applied or llm_parse_applied or llm_guidance_applied)
    llm_changed_route = bool(
        llm_router_applied
        and (routing_trace.get("local") or {}).get("name")
        and llm_payload.get("name")
        and (routing_trace.get("local") or {}).get("name") != llm_payload.get("name")
    )
    return {
        "llm_router_attempted": llm_router_attempted,
        "llm_router_success": llm_router_success,
        "llm_router_applied": llm_router_applied,
        "llm_router_used": llm_router_used,
        "router_attempted": router_attempted,
        "router_success": router_success,
        "router_applied": llm_router_applied,
        "router_timeout": bool(routing_trace.get("router_timeout")),
        "router_json_invalid": bool(routing_trace.get("router_json_invalid")),
        "router_skipped_by_policy": bool(routing_trace.get("router_skipped_by_policy")),
        "router_skipped_high_confidence": bool(routing_trace.get("router_skipped_high_confidence_local")),
        "router_final_source": routing_trace.get("router_final_source") or final_route.get("source") or "",
        "llm_parse_attempted": llm_parse_requested,
        "llm_parse_used": llm_parse_used,
        "llm_parse_applied": llm_parse_applied,
        "llm_guidance_attempted": guidance_attempted,
        "llm_guidance_used": llm_guidance_used,
        "llm_guidance_applied": llm_guidance_applied,
        "llm_attempted": llm_attempted,
        "llm_used": llm_used,
        "llm_applied": llm_applied,
        "llm_changed_route": llm_changed_route,
        "llm_filled_missing_fields": llm_filled_missing_fields,
        "rag_attempted": rag_attempted,
        "embedding_success": embedding_success,
        "milvus_success": milvus_success,
        "retrieval_nonempty": retrieval_nonempty,
        "retrieval_timeout": retrieval_timeout,
        "fallback_to_catalog": fallback_to_catalog,
        "rag_contribution_evaluable": rag_contribution_evaluable,
        "query_expansion_used": query_expansion_used,
        "llm_calls": llm_calls,
        "embedding_calls": int(rag_attempted),
        "milvus_calls": int(rag_attempted),
        "guidance_calls": int(guidance_attempted),
        # ── 标准化 LLM 诊断字段 ──
        "llm_router_failure_reason": str(routing_trace.get("llm_router_failure_reason") or routing_trace.get("router_failure_reason") or ""),
        "llm_router_error_class": str(routing_trace.get("llm_router_error_class") or ""),
        "llm_router_elapsed_ms": int(routing_trace.get("llm_router_elapsed_ms") or 0),
        "llm_router_timeout": bool(routing_trace.get("llm_router_error_class") == "timeout" or routing_trace.get("router_timeout")),
        "llm_router_provider_error": bool(routing_trace.get("llm_router_error_class") == "provider_error"),
        "llm_parse_failure_reason": str(parse_trace.get("llm_parse_failure_reason") or parse_trace.get("parse_fallback_reason") or ""),
        "llm_parse_error_class": str(parse_trace.get("llm_parse_error_class") or ""),
        "llm_parse_elapsed_ms": int(parse_trace.get("llm_parse_elapsed_ms") or 0),
        "llm_parse_timeout": bool(parse_trace.get("llm_parse_error_class") == "timeout"),
        "llm_parse_json_invalid": bool(parse_trace.get("llm_parse_error_class") == "json_invalid"),
        "llm_parse_provider_error": bool(parse_trace.get("llm_parse_error_class") == "provider_error"),
        "llm_guidance_failure_reason": str(trace.get("llm_guidance_failure_reason") or ""),
        "llm_explanation_attempted": bool(trace.get("llm_explanation_attempted")),
        "llm_explanation_success": bool(trace.get("llm_explanation_success")),
        "llm_explanation_failure_reason": str(trace.get("llm_explanation_failure_reason") or ""),
    }


def validate_cards(payload: Dict[str, Any], case: Dict[str, Any]) -> Dict[str, Any]:
    cards = payload.get("product_cards") or []
    if not cards:
        return {
            "card_count": 0,
            "card_product_id_valid": None,
            "card_category_valid": None,
            "card_price_valid": None,
            "card_sku_valid": None,
            "card_evidence_consistent": None,
            "card_reason_grounded": None,
            "card_constraint_satisfied": None,
            "card_hallucination_flag": False,
            "card_accuracy": None,
        }
    catalog = load_combined_product_catalog().by_id
    pc_catalog = load_pc_parts_product_catalog().by_id
    expected_category = str(case.get("expected_category") or "")
    valid_ids = 0
    valid_category = 0
    valid_price = 0
    sku_valid = 0
    grounded = 0
    constraints = 0
    for card in cards:
        product_id = str(card.get("product_id") or card.get("part_id") or "")
        product = catalog.get(product_id) or pc_catalog.get(product_id)
        if product:
            valid_ids += 1
        category_text = " ".join(str(value) for value in [card.get("category"), card.get("category_key"), card.get("sub_category"), product_id])
        if not expected_category or expected_category in category_text:
            valid_category += 1
        price = card.get("price") or card.get("base_price") or card.get("min_price")
        if price is None or safe_float(price) is not None:
            valid_price += 1
        if card.get("sku_id") or product:
            sku_valid += 1
        reason = str(card.get("reason") or card.get("summary") or "")
        product_text = product_to_text(product) if product else ""
        if not reason or any(token for token in tokenize(reason) if token in product_text):
            grounded += 1
        if constraint_satisfied(card, case):
            constraints += 1
    total = len(cards)
    metrics = {
        "card_count": total,
        "card_product_id_valid": valid_ids / total,
        "card_category_valid": valid_category / total,
        "card_price_valid": valid_price / total,
        "card_sku_valid": sku_valid / total,
        "card_evidence_consistent": grounded / total,
        "card_reason_grounded": grounded / total,
        "card_constraint_satisfied": constraints / total,
        "card_hallucination_flag": valid_ids < total or grounded < total,
    }
    metrics["card_accuracy"] = statistics.mean(
        value for key, value in metrics.items() if key.startswith("card_") and isinstance(value, (int, float)) and key != "card_count"
    )
    return metrics


def query_expansion_diagnostics(retrieval: Dict[str, Any], expansion_used: bool) -> Dict[str, Any]:
    variants = retrieval.get("query_variants") or []
    expanded_queries = [
        str(item.get("retrieval_query") or item.get("query") or "")
        for item in variants
        if isinstance(item, dict) and str(item.get("kind") or item.get("query_kind") or "") != "base"
    ]
    all_queries = [
        str(item.get("retrieval_query") or item.get("query") or "")
        for item in variants
        if isinstance(item, dict) and str(item.get("retrieval_query") or item.get("query") or "")
    ]
    return {
        "original_query": all_queries[0] if all_queries else retrieval.get("retrieval_query", ""),
        "expanded_queries": dedupe(expanded_queries),
        "expansion_latency_ms": retrieval.get("query_expansion_latency_ms") or retrieval.get("expansion_latency_ms"),
        "expansion_timeout": bool(retrieval.get("query_expansion_timeout") or retrieval.get("expansion_timeout")),
        "expansion_used": bool(expansion_used),
        "expansion_retrieval_nonempty": bool(expansion_used and (retrieval.get("retrieved_product_ids") or retrieval.get("matched_product_ids"))),
        "expansion_changed_top_ids": bool(retrieval.get("expansion_changed_top_ids")),
        "expansion_drift_flag": bool(retrieval.get("expansion_drift_flag")),
        "expansion_fallback_reason": retrieval.get("query_expansion_fallback_reason") or retrieval.get("expansion_fallback_reason") or "",
    }


def category_injected_to_retrieval_query(retrieval: Dict[str, Any]) -> bool:
    variants = list(retrieval.get("query_variants") or [])
    postprocess = list(retrieval.get("postprocess") or [])
    return any(
        bool(item.get("category_injected_to_retrieval_query"))
        for item in [*variants, *postprocess]
        if isinstance(item, dict)
    )


def router_diagnostics(routing_trace: Dict[str, Any]) -> Dict[str, Any]:
    local_route = routing_trace.get("local") or {}
    llm_route = routing_trace.get("llm") or {}
    final_route = routing_trace.get("final") or {}
    return {
        "local_route": local_route.get("name"),
        "llm_route": llm_route.get("name"),
        "final_route": final_route.get("name"),
        "route_changed_by_llm": bool(local_route.get("name") and llm_route.get("name") and local_route.get("name") != llm_route.get("name")),
        "route_changed_by_guard": bool(routing_trace.get("guard_overridden") or routing_trace.get("route_overridden")),
        "llm_skipped_reason": routing_trace.get("llm_skipped_reason"),
        "router_attempted": bool(routing_trace.get("router_attempted")),
        "router_timeout": bool(routing_trace.get("router_timeout")),
        "router_json_invalid": bool(routing_trace.get("router_json_invalid")),
        "router_success": bool(routing_trace.get("router_success")),
        "router_applied": bool(routing_trace.get("router_applied")),
        "router_skipped_by_policy": bool(routing_trace.get("router_skipped_by_policy")),
        "router_skipped_high_confidence": bool(routing_trace.get("router_skipped_high_confidence_local")),
        "router_final_source": routing_trace.get("router_final_source"),
        "circuit_open": bool(routing_trace.get("circuit_open")),
        "confidence": routing_trace.get("confidence") or routing_trace.get("route_confidence"),
        "margin": routing_trace.get("margin") or routing_trace.get("route_margin"),
    }


def parse_diagnostics(trace: Dict[str, Any]) -> Dict[str, Any]:
    parse_trace = trace.get("requirement_parsing") or {}
    return {
        "rule_requirement": parse_trace.get("rule_requirement") or parse_trace.get("rule_parse"),
        "llm_requirement": parse_trace.get("llm_requirement") or parse_trace.get("llm_parse"),
        "final_requirement": parse_trace.get("final_requirement") or parse_trace.get("final"),
        "llm_filled_missing_fields": bool(parse_trace.get("llm_filled_missing_fields")),
        "llm_changed_category": bool(parse_trace.get("llm_changed_category")),
        "llm_changed_budget": bool(parse_trace.get("llm_changed_budget")),
        "llm_changed_excluded_brands": bool(parse_trace.get("llm_changed_excluded_brands")),
        "llm_changed_attributes": bool(parse_trace.get("llm_changed_attributes")),
        "parse_fallback_reason": parse_trace.get("parse_fallback_reason"),
    }


def guidance_diagnostics(trace: Dict[str, Any], card_metrics: Dict[str, Any]) -> Dict[str, Any]:
    guidance = trace.get("llm_guidance")
    explanation = trace.get("grounded_explanation") or {}
    return {
        "guidance_attempted": guidance not in {None, "", "disabled"},
        "guidance_success": guidance == "enabled",
        "guidance_applied": guidance == "enabled",
        "grounded_reason_score": card_metrics.get("card_reason_grounded"),
        "hallucination_flag": bool(card_metrics.get("card_hallucination_flag")),
        "unsupported_claims": explanation.get("unsupported_claims") or [],
        "evidence_ids_used": explanation.get("evidence_ids_used") or explanation.get("evidence_ids") or [],
    }


def rag_evidence_used_in_card(payload: Dict[str, Any], retrieved_top_ids: Sequence[Any]) -> bool:
    retrieved = {normalize_product_id_for_eval(item) for item in retrieved_top_ids}
    if not retrieved:
        return False
    for card in payload.get("product_cards") or []:
        product_id = normalize_product_id_for_eval(card.get("product_id") or card.get("part_id") or card.get("id"))
        if product_id in retrieved:
            return True
        evidence_ids = card.get("evidence_ids") or card.get("evidence") or []
        if isinstance(evidence_ids, list) and evidence_ids:
            return True
    return False


def rag_evidence_used_in_reason(payload: Dict[str, Any], retrieved_top_ids: Sequence[Any]) -> bool:
    if not retrieved_top_ids:
        return False
    evidence_terms = ["evidence", "retrieval", "milvus", "rag", "证据", "召回", "知识"]
    for card in payload.get("product_cards") or []:
        reason = str(card.get("reason") or card.get("summary") or card.get("description") or "").lower()
        if any(term in reason for term in evidence_terms):
            return True
    for plan in payload.get("plans") or []:
        text = json.dumps(plan, ensure_ascii=False).lower()
        if any(term in text for term in evidence_terms):
            return True
    return False


def product_to_text(product: Any) -> str:
    if product is None:
        return ""
    data = model_to_dict(product)
    return json.dumps(data, ensure_ascii=False).lower()


def tokenize(text: str) -> List[str]:
    return [item.lower() for item in str(text).replace("，", " ").replace("。", " ").split() if len(item.strip()) >= 2]


def constraint_satisfied(card: Dict[str, Any], case: Dict[str, Any]) -> bool:
    price_max = case.get("expected_price_max")
    if price_max is None:
        return True
    price = safe_float(card.get("min_price") or card.get("base_price") or card.get("price"))
    return price is None or price <= float(price_max)


def constraint_violation_count(case: Dict[str, Any], payload: Dict[str, Any]) -> int:
    cards = payload.get("product_cards") or []
    violations = 0
    excluded = [str(item).lower() for item in case.get("exclude_brands") or []]
    expected_price = case.get("expected_price_max")
    for card in cards:
        text = json.dumps(card, ensure_ascii=False).lower()
        if excluded and any(item in text for item in excluded):
            violations += 1
        price = safe_float(card.get("min_price") or card.get("base_price") or card.get("price"))
        if expected_price is not None and price is not None and price > float(expected_price):
            violations += 1
    return violations


def pc_build_metrics(payload: Dict[str, Any], case: Dict[str, Any]) -> Dict[str, Any]:
    if case.get("case_group") != "pc_build":
        return {}
    plan = payload.get("pc_build_plan") if isinstance(payload.get("pc_build_plan"), dict) else payload
    trace = plan.get("trace") or {}
    compatibility = plan.get("compatibility") or {}
    budget = plan.get("budget") or plan.get("budget_summary") or {}
    budget_map = budget if isinstance(budget, dict) else {}
    parts = plan.get("parts") or plan.get("items") or []
    return {
        "pc_build_chain_valid": bool(parts),
        "compatibility_valid": not bool(compatibility.get("errors") or compatibility.get("fatal_errors")),
        "budget_valid": not bool(budget_map.get("over_budget") or trace.get("budget_overflow")),
    }


def fallback_triggered(trace: Dict[str, Any], routing_trace: Dict[str, Any], retrieval: Dict[str, Any], errors: List[str]) -> bool:
    values = [
        routing_trace.get("llm_skipped_reason"),
        (trace.get("requirement_parsing") or {}).get("parse_fallback_reason"),
        trace.get("llm_guidance"),
        retrieval.get("status"),
        retrieval.get("retrieval_error") or retrieval.get("error"),
        *errors,
    ]
    text = " ".join(str(item) for item in values if item)
    return any(term in text.lower() for term in ["fallback", "timeout", "failed", "not_configured", "disabled", "unavailable"])


def json_invalid(trace: Dict[str, Any], routing_trace: Dict[str, Any], errors: List[str]) -> bool:
    values = [
        routing_trace.get("llm_skipped_reason"),
        (trace.get("requirement_parsing") or {}).get("parse_fallback_reason"),
        trace.get("llm_guidance_error"),
        trace.get("explanation_fallback_reason"),
        trace.get("llm_error_sanitized"),
        *errors,
    ]
    text = " ".join(str(item) for item in values if item).lower()
    return any(term in text for term in ["json", "validation", "decode", "invalid"])


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


def extract_product_ids_from_payload(payload: Dict[str, Any]) -> List[str]:
    ids: List[str] = []
    for card in payload.get("product_cards") or []:
        append(ids, card.get("product_id") or card.get("part_id") or card.get("id"))
    plan = payload.get("pc_build_plan") if isinstance(payload.get("pc_build_plan"), dict) else payload
    for item in plan.get("parts") or plan.get("items") or []:
        append(ids, item.get("product_id") or item.get("part_id") or item.get("id"))
    for plan_item in payload.get("plans") or []:
        for comp in plan_item.get("components") or []:
            product = comp.get("product") or {}
            append(ids, comp.get("product_id") or product.get("product_id"))
    return dedupe(ids)


def compact_trace(trace: Dict[str, Any], routing_trace: Dict[str, Any]) -> Dict[str, Any]:
    retrieval = trace.get("milvus_retrieval") or trace.get("retrieval") or {}
    return {
        "llm_provider": trace.get("llm_provider") or routing_trace.get("llm_provider"),
        "llm_model": trace.get("llm_model") or routing_trace.get("llm_model"),
        "selected_runtime_mode": trace.get("selected_runtime_mode") or trace.get("selected_mode") or trace.get("runtime_mode"),
        "adaptive_runtime": trace.get("adaptive_decision") or {},
        "explanation_mode": trace.get("explanation_mode"),
        "fallback_used": bool(trace.get("fallback_used")),
        "router": {
            "llm_skipped": routing_trace.get("llm_skipped"),
            "llm_skipped_reason": routing_trace.get("llm_skipped_reason"),
            "guard_overridden": routing_trace.get("guard_overridden"),
            "route_overridden": routing_trace.get("route_overridden"),
        },
        "requirement_parsing": trace.get("requirement_parsing") or {},
        "retrieval": {
            "status": retrieval.get("status"),
            "backend": retrieval.get("retrieval_backend"),
            "raw_hits": retrieval.get("milvus_raw_hit_count"),
            "after_postprocess": retrieval.get("retrieved_chunk_count_after_postprocess") or retrieval.get("retrieved_chunk_count"),
            "error": retrieval.get("retrieval_error") or retrieval.get("error"),
            "timeout": retrieval.get("retrieval_timeout"),
        },
        "guidance": trace.get("llm_guidance"),
        "no_match_reason": trace.get("no_match_reason") or trace.get("fallback_blocked_reason"),
    }


def build_report(rows: List[Dict[str, Any]], groups: List[GroupSpec], args: argparse.Namespace, *, elapsed_ms: float) -> Dict[str, Any]:
    annotate_rows_with_fast_deltas(rows)
    provider_config = build_llm_provider_config()
    return {
        "status": "failed" if any(row["status"] == "failed" for row in rows) else "ok",
        "config": {
            "cases": str(args.cases),
            "groups": [group.name for group in groups],
            "case_count": len({(row.get("parent_case_id") or row["case_id"]) for row in rows}),
            "elapsed_ms": elapsed_ms,
            "embedding_provider": os.getenv("EMBEDDING_PROVIDER", "local"),
            "embedding_model": os.getenv("EMBEDDING_MODEL", ""),
            "milvus_collection": os.getenv("MILVUS_COLLECTION", ""),
            "llm_timeout_seconds": args.llm_timeout_seconds,
            "llm_provider": provider_config.provider,
            "llm_model": provider_config.model,
            "router_model": os.getenv("MALLMIND_ROUTER_MODEL") or provider_config.fast_model,
            "parse_model": os.getenv("MALLMIND_PARSE_MODEL") or provider_config.fast_model,
            "guidance_model": os.getenv("MALLMIND_GUIDANCE_MODEL") or provider_config.model,
            "router_timeout_seconds": args.router_timeout_seconds,
            "retrieval_timeout_seconds": args.retrieval_timeout_seconds,
            "disable_router_llm": bool(args.disable_router_llm),
            "router_circuit_failures": args.router_circuit_failures,
        },
        "group_specs": {group.name: group.__dict__ for group in groups},
        "summary_by_group": {group.name: aggregate([row for row in rows if row["group"] == group.name]) for group in groups},
        "summary_by_provider": {
            provider: aggregate([row for row in rows if row.get("llm_provider") == provider])
            for provider in sorted({row.get("llm_provider") or "unknown" for row in rows})
        },
        "stability_eval": {group.name: stability_aggregate([row for row in rows if row["group"] == group.name]) for group in groups},
        "capability_eval": {group.name: capability_aggregate([row for row in rows if row["group"] == group.name]) for group in groups},
        "summary_by_case_type": {
            case_type: aggregate([row for row in rows if row["case_type"] == case_type])
            for case_type in sorted({row["case_type"] for row in rows})
        },
        "ablation_conclusions": build_capability_ablation_conclusions(rows),
        "vs_fast_delta": build_vs_fast_delta(rows),
        "rag_diagnostics": build_rag_diagnostics(rows),
        "llm_diagnostics": build_llm_diagnostics(rows),
        "capability_challenge_assessment": build_capability_challenge_assessment(rows),
        "worst_cases": build_worst_cases(rows),
        "rows": rows,
    }


def aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return empty_aggregate()
    positive_rows = [row for row in rows if row.get("expected_product_ids") and row.get("case_group") != "pc_build"]
    route_rows = [row for row in rows if row.get("expected_tool")]
    card_values = [row["card_metrics"].get("card_accuracy") for row in rows if isinstance(row.get("card_metrics"), dict) and row["card_metrics"].get("card_accuracy") is not None]
    return {
        "case_count": len(rows),
        "success_rate": rate(row["status"] == "ok" for row in rows),
        "route_accuracy": rate(row["route_correct"] for row in route_rows),
        "tool_call_accuracy": rate(row["route_correct"] for row in route_rows),
        "hit@1": hit_at_k_rate(positive_rows, 1),
        "hit@3": hit_at_k_rate(positive_rows, 3),
        "hit@5": hit_at_k_rate(positive_rows, 5),
        "precision@1": precision_at_k_rate(positive_rows, 1),
        "precision@3": precision_at_k_rate(positive_rows, 3),
        "precision@5": precision_at_k_rate(positive_rows, 5),
        "strict_recall@5": recall_at_k_rate(positive_rows, 5),
        "relaxed_recall@5": recall_at_k_rate(positive_rows, 5),
        "MRR": mean(reciprocal_rank(row) for row in positive_rows),
        "constraint_violation_rate": rate(row.get("constraint_violation_count", 0) > 0 for row in rows),
        "empty_result_rate": rate(not row.get("recommended_product_ids") for row in rows if row.get("actual_tool") == "recommend_shopping_products"),
        "catalog_gap_correct_rate": rate(row["status"] == "ok" for row in rows if row.get("case_group") == "negative"),
        "card_accuracy": mean(card_values),
        "avg_latency_ms": mean(row["latency_ms"] for row in rows),
        "p50_latency_ms": percentile([row["latency_ms"] for row in rows], 50),
        "p95_latency_ms": percentile([row["latency_ms"] for row in rows], 95),
        "timeout_rate": rate(row["timeout"] for row in rows),
        "json_invalid_rate": rate(row.get("json_invalid") for row in rows),
        "error_rate": rate(bool(row["errors"]) for row in rows),
        "no_500_rate": rate(not any("500" in str(item) for item in (row.get("errors") or [])) for row in rows),
        "llm_calls_per_case": mean(row["llm_calls"] for row in rows),
        "llm_attempted_rate": rate(row["llm_attempted"] for row in rows),
        "llm_used_rate": rate(row["llm_used"] for row in rows),
        "llm_success_rate": rate(row["llm_used"] for row in rows),
        "llm_applied_rate": rate(row["llm_applied"] for row in rows),
        "router_success_rate": rate(row["llm_router_success"] for row in rows),
        "parse_success_rate": rate(row["llm_parse_used"] for row in rows),
        "guidance_success_rate": rate(row["llm_guidance_used"] for row in rows),
        "llm_router_attempted_rate": rate(row["llm_router_attempted"] for row in rows),
        "llm_router_success_rate": rate(row["llm_router_success"] for row in rows),
        "llm_router_applied_rate": rate(row["llm_router_applied"] for row in rows),
        "router_attempted_rate": rate(row.get("router_attempted") for row in rows),
        "router_success_rate": rate(row.get("router_success") for row in rows),
        "router_applied_rate": rate(row.get("router_applied") for row in rows),
        "router_timeout_rate": rate(row.get("router_timeout") for row in rows),
        "router_json_invalid_rate": rate(row.get("router_json_invalid") for row in rows),
        "router_skipped_by_policy_rate": rate(row.get("router_skipped_by_policy") for row in rows),
        "router_skipped_high_confidence_rate": rate(row.get("router_skipped_high_confidence") for row in rows),
        "llm_parse_attempted_rate": rate(row["llm_parse_attempted"] for row in rows),
        "llm_parse_success_rate": rate(row["llm_parse_used"] for row in rows),
        "llm_parse_applied_rate": rate(row["llm_parse_applied"] for row in rows),
        "llm_guidance_attempted_rate": rate(row["llm_guidance_attempted"] for row in rows),
        "llm_guidance_success_rate": rate(row["llm_guidance_used"] for row in rows),
        "llm_guidance_applied_rate": rate(row["llm_guidance_applied"] for row in rows),
        "rag_attempted_rate": rate(row["rag_attempted"] for row in rows),
        "embedding_success_rate": rate(row["embedding_success"] for row in rows),
        "milvus_success_rate": rate(row["milvus_success"] for row in rows),
        "retrieval_nonempty_rate": rate(row["retrieval_nonempty"] for row in rows),
        "retrieval_timeout_rate": rate(row["retrieval_timeout"] for row in rows),
        "fallback_to_catalog_rate": rate(row["fallback_to_catalog"] for row in rows),
        "rag_contribution_evaluable_rate": rate(row["rag_contribution_evaluable"] for row in rows),
        "rag_contribution_evaluable_count": sum(1 for row in rows if row["rag_contribution_evaluable"]),
        "rag_changed_top1_rate": rate(row.get("rag_changed_top1") for row in rows if row.get("rag_contribution_evaluable")),
        "rag_changed_top3_rate": rate(row.get("rag_changed_top3") for row in rows if row.get("rag_contribution_evaluable")),
        "category_injected_to_retrieval_query_rate": rate(row.get("category_injected_to_retrieval_query") for row in rows),
        "rag_evidence_used_in_reason_rate": rate(row.get("rag_evidence_used_in_reason") for row in rows if row.get("rag_contribution_evaluable")),
        "rag_evidence_used_in_card_rate": rate(row.get("rag_evidence_used_in_card") for row in rows if row.get("rag_contribution_evaluable")),
        "llm_changed_route_rate": rate(row.get("llm_changed_route") for row in rows if row.get("llm_applied")),
        "llm_filled_missing_fields_rate": rate(row.get("llm_filled_missing_fields") for row in rows if row.get("llm_applied")),
        "llm_triggered_clarification_rate": rate(row.get("llm_triggered_clarification") for row in rows if row.get("llm_applied")),
        "llm_changed_recommendation_rate": rate(row.get("llm_changed_recommendation") for row in rows if row.get("llm_applied")),
        "embedding_calls_per_case": mean(row["embedding_calls"] for row in rows),
        "milvus_calls_per_case": mean(row["milvus_calls"] for row in rows),
        "fallback_triggered_rate": rate(row["fallback_triggered"] for row in rows),
        "degraded_success_rate": rate(row["degraded_success"] for row in rows if row["group"] == "timeout_fallback"),
        "llm_route_used_rate": rate(row["llm_router_used"] for row in rows),
        "llm_parse_used_rate": rate(row["llm_parse_used"] for row in rows),
        "guidance_used_rate": rate(row["llm_guidance_used"] for row in rows),
        "query_expansion_used_rate": rate(row["query_expansion_used"] for row in rows),
        "fast_selected_rate": rate(row.get("selected_runtime_mode") == "fast" for row in rows),
        "balanced_selected_rate": rate(row.get("selected_runtime_mode") == "balanced" for row in rows),
        "full_selected_rate": rate(row.get("selected_runtime_mode") == "full" for row in rows),
        "degraded_fast_rate": rate(row.get("selected_runtime_mode") == "degraded_fast" for row in rows),
        "failed_count": sum(1 for row in rows if row["status"] == "failed"),
        # ── LLM 错误分类统计 ──
        "llm_timeout_rate": rate(
            row.get("llm_router_timeout") or row.get("llm_parse_timeout")
            for row in rows if row.get("llm_attempted")
        ),
        "llm_json_invalid_rate": rate(
            row.get("router_json_invalid") or row.get("llm_parse_json_invalid")
            for row in rows if row.get("llm_attempted")
        ),
        "llm_provider_error_rate": rate(
            row.get("llm_router_provider_error") or row.get("llm_parse_provider_error")
            for row in rows if row.get("llm_attempted")
        ),
        "llm_router_failure_rate": rate(
            bool(row.get("llm_router_failure_reason")) for row in rows if row.get("llm_router_attempted")
        ),
        "llm_parse_failure_rate": rate(
            bool(row.get("llm_parse_failure_reason")) for row in rows if row.get("llm_parse_attempted")
        ),
        "llm_guidance_failure_rate": rate(
            bool(row.get("llm_guidance_failure_reason")) for row in rows if row.get("llm_guidance_attempted")
        ),
        "llm_explanation_failure_rate": rate(
            bool(row.get("llm_explanation_failure_reason")) for row in rows if row.get("llm_explanation_attempted")
        ),
    }


def empty_aggregate() -> Dict[str, Any]:
    return {
        "case_count": 0,
        "success_rate": 0.0,
        "failed_count": 0,
        "route_accuracy": 0.0,
        "tool_call_accuracy": 0.0,
        "hit@1": 0.0,
        "hit@3": 0.0,
        "hit@5": 0.0,
        "precision@1": 0.0,
        "precision@3": 0.0,
        "precision@5": 0.0,
        "constraint_violation_rate": 0.0,
        "card_accuracy": 0.0,
        "avg_latency_ms": 0.0,
        "p95_latency_ms": 0.0,
        "timeout_rate": 0.0,
        "llm_calls_per_case": 0.0,
        "embedding_calls_per_case": 0.0,
        "milvus_calls_per_case": 0.0,
        "fallback_triggered_rate": 0.0,
        "error_rate": 0.0,
        "no_500_rate": 0.0,
        "catalog_gap_correct_rate": 0.0,
    }


def stability_aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    base = aggregate(rows)
    return {
        "case_count": base.get("case_count", 0),
        "success_rate": base.get("success_rate", 0.0),
        "route_accuracy": base.get("route_accuracy", 0.0),
        "tool_call_accuracy": base.get("tool_call_accuracy", 0.0),
        "constraint_violation_rate": base.get("constraint_violation_rate", 0.0),
        "catalog_gap_correct_rate": base.get("catalog_gap_correct_rate", 0.0),
        "error_rate": base.get("error_rate", 0.0),
        "timeout_rate": base.get("timeout_rate", 0.0),
        "fallback_triggered_rate": base.get("fallback_triggered_rate", 0.0),
        "no_500_rate": base.get("no_500_rate", 0.0),
        "failed_count": base.get("failed_count", 0),
    }


def capability_aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    base = aggregate(rows)
    rag_rows = [row for row in rows if row.get("rag_contribution_evaluable")]
    positive_rag_rows = [row for row in rag_rows if row.get("expected_product_ids") and row.get("case_group") != "pc_build"]
    return {
        "case_count": base.get("case_count", 0),
        "llm_attempted_rate": base.get("llm_attempted_rate", 0.0),
        "llm_success_rate": base.get("llm_used_rate", 0.0),
        "llm_used_rate": base.get("llm_used_rate", 0.0),
        "llm_applied_rate": base.get("llm_applied_rate", 0.0),
        "llm_router_attempted_rate": base.get("llm_router_attempted_rate", 0.0),
        "llm_router_success_rate": base.get("llm_router_success_rate", 0.0),
        "llm_router_applied_rate": base.get("llm_router_applied_rate", 0.0),
        "router_attempted_rate": base.get("router_attempted_rate", 0.0),
        "router_success_rate": base.get("router_success_rate", 0.0),
        "router_applied_rate": base.get("router_applied_rate", 0.0),
        "router_timeout_rate": base.get("router_timeout_rate", 0.0),
        "router_json_invalid_rate": base.get("router_json_invalid_rate", 0.0),
        "router_skipped_by_policy_rate": base.get("router_skipped_by_policy_rate", 0.0),
        "router_skipped_high_confidence_rate": base.get("router_skipped_high_confidence_rate", 0.0),
        "llm_parse_attempted_rate": base.get("llm_parse_attempted_rate", 0.0),
        "llm_parse_success_rate": base.get("llm_parse_success_rate", 0.0),
        "llm_parse_applied_rate": base.get("llm_parse_applied_rate", 0.0),
        "llm_guidance_attempted_rate": base.get("llm_guidance_attempted_rate", 0.0),
        "llm_guidance_success_rate": base.get("llm_guidance_success_rate", 0.0),
        "llm_guidance_applied_rate": base.get("llm_guidance_applied_rate", 0.0),
        "rag_attempted_rate": base.get("rag_attempted_rate", 0.0),
        "embedding_success_rate": base.get("embedding_success_rate", 0.0),
        "milvus_success_rate": base.get("milvus_success_rate", 0.0),
        "retrieval_nonempty_rate": base.get("retrieval_nonempty_rate", 0.0),
        "retrieval_timeout_rate": base.get("retrieval_timeout_rate", 0.0),
        "fallback_to_catalog_rate": base.get("fallback_to_catalog_rate", 0.0),
        "rag_contribution_evaluable_count": len(rag_rows),
        "rag_effective_hit@5": hit_at_k_rate(positive_rag_rows, 5),
        "rag_effective_precision@1": precision_at_k_rate(positive_rag_rows, 1),
        "rag_effective_mrr": mean(reciprocal_rank(row) for row in positive_rag_rows),
        "rag_changed_top1_rate": base.get("rag_changed_top1_rate", 0.0),
        "rag_changed_top3_rate": base.get("rag_changed_top3_rate", 0.0),
        "rag_evidence_used_in_reason_rate": base.get("rag_evidence_used_in_reason_rate", 0.0),
        "rag_evidence_used_in_card_rate": base.get("rag_evidence_used_in_card_rate", 0.0),
        "llm_changed_route_rate": base.get("llm_changed_route_rate", 0.0),
        "llm_filled_missing_fields_rate": base.get("llm_filled_missing_fields_rate", 0.0),
        "llm_triggered_clarification_rate": base.get("llm_triggered_clarification_rate", 0.0),
        "llm_changed_recommendation_rate": base.get("llm_changed_recommendation_rate", 0.0),
    }


def build_ablation_conclusions(rows: List[Dict[str, Any]]) -> Dict[str, str]:
    summaries = {name: aggregate([row for row in rows if row["group"] == name]) for name in GROUPS}

    def delta(a: str, b: str, key: str) -> float:
        return round(float(summaries.get(b, {}).get(key, 0) or 0) - float(summaries.get(a, {}).get(key, 0) or 0), 4)

    return {
        "A_vs_A2": f"RAG/Milvus 贡献：hit@5 变化 {delta('fast_baseline', 'rag_only', 'hit@5')}，precision@1 变化 {delta('fast_baseline', 'rag_only', 'precision@1')}，平均延迟变化 {delta('fast_baseline', 'rag_only', 'avg_latency_ms')}ms。",
        "A2_vs_B": f"LLM router+parse 贡献：路由准确率变化 {delta('rag_only', 'balanced_demo', 'route_accuracy')}，成功率变化 {delta('rag_only', 'balanced_demo', 'success_rate')}，LLM calls/case 变化 {delta('rag_only', 'balanced_demo', 'llm_calls_per_case')}。",
        "B_vs_C": f"full 能力上限与代价：hit@5 变化 {delta('balanced_demo', 'full_llm_all', 'hit@5')}，p95 延迟变化 {delta('balanced_demo', 'full_llm_all', 'p95_latency_ms')}ms，失败率变化 {delta('balanced_demo', 'full_llm_all', 'error_rate')}。",
        "C_vs_C1": f"guidance 贡献与风险：卡片准确率变化 {delta('full_no_guidance', 'full_llm_all', 'card_accuracy')}，guidance 使用率变化 {delta('full_no_guidance', 'full_llm_all', 'guidance_used_rate')}。",
        "C_vs_C2": f"query expansion 贡献与漂移风险：hit@5 变化 {delta('full_no_query_expansion', 'full_llm_all', 'hit@5')}，约束违规率变化 {delta('full_no_query_expansion', 'full_llm_all', 'constraint_violation_rate')}。",
        "B_vs_D": f"降级能力：degraded_success_rate={summaries.get('timeout_fallback', {}).get('degraded_success_rate', 0)}，fallback_triggered_rate={summaries.get('timeout_fallback', {}).get('fallback_triggered_rate', 0)}。",
    }

def build_capability_ablation_conclusions(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    summaries = {name: aggregate([row for row in rows if row["group"] == name]) for name in GROUPS}
    capability = {name: capability_aggregate([row for row in rows if row["group"] == name]) for name in GROUPS}

    def delta(a: str, b: str, key: str) -> float:
        return round(float(summaries.get(b, {}).get(key, 0) or 0) - float(summaries.get(a, {}).get(key, 0) or 0), 4)

    def cap_delta(a: str, b: str, key: str) -> float:
        return round(float(capability.get(b, {}).get(key, 0) or 0) - float(capability.get(a, {}).get(key, 0) or 0), 4)

    def rag_effective_text(group: str) -> str:
        item = capability.get(group, {})
        if int(item.get("rag_contribution_evaluable_count") or 0) <= 0:
            return (
                f"{group}: RAG 未有效测到，rag_attempted={item.get('rag_attempted_rate', 0)}，"
                f"embedding_success={item.get('embedding_success_rate', 0)}，milvus_success={item.get('milvus_success_rate', 0)}，"
                f"retrieval_nonempty={item.get('retrieval_nonempty_rate', 0)}，timeout={item.get('retrieval_timeout_rate', 0)}。"
            )
        return (
            f"{group}: RAG 有效样本 {item.get('rag_contribution_evaluable_count')} 个，"
            f"有效 hit@5={item.get('rag_effective_hit@5', 0)}，有效 p@1={item.get('rag_effective_precision@1', 0)}。"
        )

    def router_effective_text(group: str) -> str:
        item = capability.get(group, {})
        if GROUPS[group].router_llm and float(item.get("llm_router_success_rate") or 0) == 0:
            return f"{group}: LLM router not effectively exercised，不能据此判断 router LLM 贡献。"
        return (
            f"{group}: router attempted={item.get('llm_router_attempted_rate', 0)}，"
            f"success={item.get('llm_router_success_rate', 0)}，applied={item.get('llm_router_applied_rate', 0)}。"
        )

    return {
        "stability_eval": {
            "A_vs_A2": f"稳定性：rag_only 相对 fast_baseline 成功率变化 {delta('fast_baseline', 'rag_only', 'success_rate')}，错误率变化 {delta('fast_baseline', 'rag_only', 'error_rate')}，fallback 变化 {delta('fast_baseline', 'rag_only', 'fallback_triggered_rate')}。",
            "A2_vs_B": f"稳定性：balanced_demo 相对 rag_only 路由准确率变化 {delta('rag_only', 'balanced_demo', 'route_accuracy')}，成功率变化 {delta('rag_only', 'balanced_demo', 'success_rate')}，违规率变化 {delta('rag_only', 'balanced_demo', 'constraint_violation_rate')}。",
            "B_vs_D": f"降级稳定性：degraded_success_rate={summaries.get('timeout_fallback', {}).get('degraded_success_rate', 0)}，fallback_triggered_rate={summaries.get('timeout_fallback', {}).get('fallback_triggered_rate', 0)}。",
        },
        "capability_eval": {
            "RAG_A2": rag_effective_text("rag_only"),
            "RAG_B": rag_effective_text("balanced_demo"),
            "LLM_router_B": router_effective_text("balanced_demo"),
            "LLM_router_B1": router_effective_text("router_llm_only"),
            "LLM_overall_B_vs_C": f"能力：full_llm_all 相对 balanced_demo llm_attempted 变化 {cap_delta('balanced_demo', 'full_llm_all', 'llm_attempted_rate')}，llm_used 变化 {cap_delta('balanced_demo', 'full_llm_all', 'llm_used_rate')}，llm_applied 变化 {cap_delta('balanced_demo', 'full_llm_all', 'llm_applied_rate')}。",
            "QueryExpansion_C_vs_C2": f"能力：query expansion 只能在 RAG 有效样本上解释；full_no_query_expansion RAG 有效样本={capability.get('full_no_query_expansion', {}).get('rag_contribution_evaluable_count', 0)}，full_llm_all RAG 有效样本={capability.get('full_llm_all', {}).get('rag_contribution_evaluable_count', 0)}。",
        },
    }


def annotate_rows_with_fast_deltas(rows: List[Dict[str, Any]]) -> None:
    fast_by_case = {row["case_id"]: row for row in rows if row.get("group") == "fast_baseline"}
    for row in rows:
        fast = fast_by_case.get(row["case_id"])
        if not fast or row.get("group") == "fast_baseline":
            continue
        current_ids = row.get("recommended_product_ids") or []
        fast_ids = fast.get("recommended_product_ids") or []
        row["rag_changed_top1"] = bool(row.get("rag_contribution_evaluable") and first_or_empty(current_ids) != first_or_empty(fast_ids))
        row["rag_changed_top3"] = bool(row.get("rag_contribution_evaluable") and current_ids[:3] != fast_ids[:3])
        row["llm_changed_recommendation"] = bool(row.get("llm_applied") and current_ids[:3] != fast_ids[:3])


def build_vs_fast_delta(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups = ["fast_baseline", "balanced_demo", "full_no_query_expansion", "full_llm_all"]
    by_case: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for row in rows:
        by_case.setdefault(row["case_id"], {})[row["group"]] = row
    out = []
    for case_id, items in sorted(by_case.items()):
        fast = items.get("fast_baseline")
        if not fast:
            continue
        base_mrr = reciprocal_rank(fast)
        base_ndcg = ndcg_at_k(fast, 5)
        base_constraint = 1.0 if not fast.get("constraint_violation_count") else 0.0
        base_clarification = 1.0 if fast.get("no_match_reason") == "clarification_required" else 0.0
        base_reason = safe_float(fast.get("grounded_reason_score")) or 0.0
        row = {
            "case_id": case_id,
            "query": fast.get("query", ""),
            "expected_ids": fast.get("expected_product_ids") or [],
            "fast_top1": first_or_empty(fast.get("recommended_product_ids")),
            "fast_expected_rank": fast.get("expected_id_recommended_rank"),
            "fast_already_top1_correct": fast.get("expected_id_recommended_rank") == 1,
        }
        for group in groups[1:]:
            current = items.get(group) or {}
            group_mrr = reciprocal_rank(current) if current else 0.0
            group_ndcg = ndcg_at_k(current, 5) if current else 0.0
            constraint = 1.0 if current and not current.get("constraint_violation_count") else 0.0
            clarification = 1.0 if current.get("no_match_reason") == "clarification_required" else 0.0
            reason = safe_float(current.get("grounded_reason_score")) or 0.0
            top1 = first_or_empty(current.get("recommended_product_ids"))
            fast_top1 = first_or_empty(fast.get("recommended_product_ids"))
            effective_for_uplift = bool(current and not current.get("fallback_to_catalog") and not current.get("timeout"))
            row.update({
                f"{group}_top1": top1,
                f"{group}_expected_rank": current.get("expected_id_recommended_rank"),
                f"{group}_top1_win_vs_fast": bool(effective_for_uplift and current.get("expected_id_recommended_rank") == 1 and fast.get("expected_id_recommended_rank") != 1),
                f"{group}_mrr_delta_vs_fast": round(group_mrr - base_mrr, 4),
                f"{group}_ndcg@5_delta_vs_fast": round(group_ndcg - base_ndcg, 4),
                f"{group}_constraint_delta_vs_fast": round(constraint - base_constraint, 4),
                f"{group}_clarification_delta_vs_fast": round(clarification - base_clarification, 4),
                f"{group}_grounded_reason_delta_vs_fast": round(reason - base_reason, 4),
                f"{group}_rag_evidence_delta_vs_fast": int(bool(current.get("rag_evidence_used_in_card") or current.get("rag_evidence_used_in_reason"))),
                f"{group}_latency_delta_vs_fast": round(float(current.get("latency_ms") or 0) - float(fast.get("latency_ms") or 0), 4),
                f"{group}_timeout_delta_vs_fast": int(bool(current.get("timeout"))) - int(bool(fast.get("timeout"))),
                f"{group}_changed_top1": bool(top1 and fast_top1 and top1 != fast_top1),
            })
        out.append(row)
    return out


def build_rag_diagnostics(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        if not (row.get("rag_attempted") or row.get("retrieved_top_ids") or GROUPS.get(row.get("group"), GROUPS["fast_baseline"]).milvus):
            continue
        out.append({
            "group": row["group"],
            "case_id": row["case_id"],
            "query": row.get("query", ""),
            "retrieved_top_ids": row.get("retrieved_top_ids") or [],
            "expected_ids": row.get("expected_product_ids") or [],
            "expected_id_retrieved_rank": row.get("expected_id_retrieved_rank"),
            "recommended_product_ids": row.get("recommended_product_ids") or [],
            "expected_id_recommended_rank": row.get("expected_id_recommended_rank"),
            "rag_contribution_evaluable": bool(row.get("rag_contribution_evaluable")),
            "rag_changed_top1": bool(row.get("rag_changed_top1")),
            "rag_changed_top3": bool(row.get("rag_changed_top3")),
            "rag_evidence_used_in_card": bool(row.get("rag_evidence_used_in_card")),
            "rag_evidence_used_in_reason": bool(row.get("rag_evidence_used_in_reason")),
            "rag_filtered_by_structured_constraints": bool(row.get("rag_filtered_by_structured_constraints")),
            "fallback_to_catalog": bool(row.get("fallback_to_catalog")),
            "retrieval_error": ((row.get("trace_summary") or {}).get("retrieval") or {}).get("error"),
            "retrieval_timeout": bool(row.get("retrieval_timeout")),
            "query_expansion": row.get("query_expansion") or {},
        })
    return out


def build_llm_diagnostics(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        if not (row.get("llm_attempted") or row.get("llm_applied") or GROUPS.get(row.get("group"), GROUPS["fast_baseline"]).router_llm):
            continue
        router = row.get("router_diagnostics") or {}
        parse = row.get("parse_diagnostics") or {}
        guidance = row.get("guidance_diagnostics") or {}
        out.append({
            "group": row["group"],
            "case_id": row["case_id"],
            "query": row.get("query", ""),
            "router_attempted": bool(row.get("llm_router_attempted")),
            "router_success": bool(row.get("llm_router_success")),
            "router_applied": bool(row.get("llm_router_applied")),
            "local_route": router.get("local_route"),
            "llm_route": router.get("llm_route"),
            "final_route": router.get("final_route"),
            "route_changed_by_llm": router.get("route_changed_by_llm"),
            "route_changed_by_guard": router.get("route_changed_by_guard"),
            "llm_skipped_reason": router.get("llm_skipped_reason"),
            "circuit_open": router.get("circuit_open"),
            "confidence": router.get("confidence"),
            "margin": router.get("margin"),
            "parse_attempted": bool(row.get("llm_parse_attempted")),
            "parse_success": bool(row.get("llm_parse_used")),
            "parse_applied": bool(row.get("llm_parse_applied")),
            "llm_filled_missing_fields": row.get("llm_filled_missing_fields"),
            "llm_changed_category": parse.get("llm_changed_category"),
            "llm_changed_budget": parse.get("llm_changed_budget"),
            "llm_changed_excluded_brands": parse.get("llm_changed_excluded_brands"),
            "llm_changed_attributes": parse.get("llm_changed_attributes"),
            "parse_fallback_reason": parse.get("parse_fallback_reason"),
            "llm_router_failure_reason": str(row.get("llm_router_failure_reason") or ""),
            "llm_router_error_class": str(row.get("llm_router_error_class") or ""),
            "llm_router_elapsed_ms": int(row.get("llm_router_elapsed_ms") or 0),
            "llm_parse_failure_reason": str(row.get("llm_parse_failure_reason") or parse.get("parse_fallback_reason") or ""),
            "llm_parse_error_class": str(row.get("llm_parse_error_class") or ""),
            "llm_parse_elapsed_ms": int(row.get("llm_parse_elapsed_ms") or 0),
            "guidance_attempted": bool(row.get("llm_guidance_attempted")),
            "guidance_success": bool(row.get("llm_guidance_used")),
            "guidance_applied": bool(row.get("llm_guidance_applied")),
            "llm_guidance_failure_reason": str(row.get("llm_guidance_failure_reason") or ""),
            "grounded_reason_score": guidance.get("grounded_reason_score"),
            "hallucination_flag": guidance.get("hallucination_flag"),
            "unsupported_claims": guidance.get("unsupported_claims"),
            "evidence_ids_used": guidance.get("evidence_ids_used"),
            "llm_triggered_clarification": bool(row.get("llm_triggered_clarification")),
            "llm_changed_recommendation": bool(row.get("llm_changed_recommendation")),
        })
    return out


def build_capability_challenge_assessment(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    groups = sorted({row["group"] for row in rows})
    summaries = {group: aggregate([row for row in rows if row["group"] == group]) for group in groups}
    main_metric_keys = ["success_rate", "hit@5", "precision@1", "MRR", "card_accuracy"]
    signatures = {
        group: tuple(summaries[group].get(key) for key in main_metric_keys)
        for group in groups
    }
    main_metrics_identical = len(set(signatures.values())) <= 1 if signatures else False
    deltas = build_vs_fast_delta(rows)
    uplift_cases = {
        group: [
            row["case_id"]
            for row in deltas
            if row.get(f"{group}_top1_win_vs_fast")
        ]
        for group in ["balanced_demo", "full_no_query_expansion", "full_llm_all"]
        if group in groups
    }
    rag_effective = {
        group: sum(1 for row in rows if row["group"] == group and row.get("rag_contribution_evaluable"))
        for group in groups
    }
    llm_applied = {
        group: sum(1 for row in rows if row["group"] == group and row.get("llm_applied"))
        for group in groups
    }
    notes = []
    if main_metrics_identical:
        notes.append("Main recommendation metrics are still identical across compared groups; this run did not prove recommendation uplift over fast_baseline.")
    if not any(uplift_cases.values()):
        notes.append("vs_fast_delta found no top1 recommendation wins; use this as evidence that current challenge cases or current chain weighting still do not expose business uplift.")
    if any(rag_effective.values()) and not any(uplift_cases.values()):
        notes.append("RAG was effectively retrieved on some rows, but the evidence did not change top1 outcomes enough to improve main metrics.")
    if all(count == 0 for count in llm_applied.values()):
        notes.append("LLM was attempted but not effectively applied in this run; do not claim LLM contribution from these rows.")
    return {
        "main_metric_keys": main_metric_keys,
        "main_metrics_identical": main_metrics_identical,
        "metric_signatures": signatures,
        "top1_uplift_cases_vs_fast": uplift_cases,
        "rag_effective_count_by_group": rag_effective,
        "llm_applied_count_by_group": llm_applied,
        "notes": notes,
    }


def build_worst_cases(rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for group in sorted({row["group"] for row in rows}):
        failed = [row for row in rows if row["group"] == group and row["status"] == "failed"]
        out[group] = [
            {
                "case_id": row["case_id"],
                "query": row["query"],
                "expected": {"tool": row["expected_tool"], "ids": row["expected_product_ids"], "category": row["expected_category"]},
                "actual_route": row["actual_tool"],
                "actual_recommended_product_ids": row["recommended_product_ids"],
                "retrieved_top_ids": row["retrieved_top_ids"],
                "failure_type": row["failure_type"],
                "failure_reason": row["failure_reason"],
                "trace_summary": row["trace_summary"],
            }
            for row in failed[:8]
        ]
    return out


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# MallMind 外部模型链路组合评估报告",
        "",
        "## 总览",
        "",
        f"- 报告状态：{report['status']}",
        f"- case 数：{report['config']['case_count']}",
        f"- 实验组：{', '.join(report['config']['groups'])}",
        f"- embedding provider/model：{report['config']['embedding_provider']} / {report['config']['embedding_model']}",
        f"- Milvus collection：{report['config']['milvus_collection']}",
        "",
        "## 本轮环境限制",
        "",
        render_environment_limitations(report),
        "",
        "## 总览表",
        "",
        "| 实验组 | cases | success | route | hit@5 | p@1 | violation | card | avg ms | p95 ms | timeout | LLM | embedding | Milvus |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for group, item in report["summary_by_group"].items():
        lines.append(
            f"| {group} | {item['case_count']} | {fmt(item['success_rate'])} | {fmt(item['route_accuracy'])} | "
            f"{fmt(item['hit@5'])} | {fmt(item['precision@1'])} | {fmt(item['constraint_violation_rate'])} | "
            f"{fmt(item['card_accuracy'])} | {fmt(item['avg_latency_ms'])} | {fmt(item['p95_latency_ms'])} | "
            f"{fmt(item['timeout_rate'])} | {fmt(item['llm_calls_per_case'])} | {fmt(item['embedding_calls_per_case'])} | {fmt(item['milvus_calls_per_case'])} |"
        )
    lines.extend(["", "## 分段结果", ""])
    lines.extend([
        "| case 类型 | cases | success | route | hit@5 | p@1 | card | fallback | failed |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for case_type, item in report["summary_by_case_type"].items():
        lines.append(
            f"| {CASE_TYPE_LABELS.get(case_type, case_type)} | {item['case_count']} | {fmt(item['success_rate'])} | "
            f"{fmt(item['route_accuracy'])} | {fmt(item['hit@5'])} | {fmt(item['precision@1'])} | "
            f"{fmt(item['card_accuracy'])} | {fmt(item['fallback_triggered_rate'])} | {item['failed_count']} |"
        )
    lines.extend(["", "## 最差 case 列表", ""])
    for group, items in report["worst_cases"].items():
        lines.extend([f"### {group}", ""])
        if not items:
            lines.extend(["- 无失败 case。", ""])
            continue
        lines.extend(["| case_id | query | expected | actual route | recommended | retrieved | failure | reason |", "| --- | --- | --- | --- | --- | --- | --- | --- |"])
        for item in items:
            expected = json.dumps(item["expected"], ensure_ascii=False)
            lines.append(
                f"| {md(item['case_id'])} | {md(item['query'])} | {md(expected)} | {md(item['actual_route'])} | "
                f"{md(','.join(item['actual_recommended_product_ids']))} | {md(','.join(item['retrieved_top_ids']))} | "
                f"{md(item['failure_type'])} | {md(item['failure_reason'])} |"
            )
        lines.append("")
    lines.extend(["## 结论建议", ""])
    lines.extend(render_recommendations(report))
    lines.extend(render_capability_markdown_sections(report))
    lines.append("")
    return "\n".join(lines)


def render_environment_limitations(report: Dict[str, Any]) -> str:
    rows = report.get("rows") or []
    joined_errors = " ".join(" ".join(row.get("errors") or []) for row in rows)
    external_blocked = any(
        term in joined_errors
        for term in ["WinError 10013", "Failed to establish a new connection", "urlopen error"]
    )
    llm_requested = any((report.get("group_specs", {}).get(row.get("group"), {}) or {}).get("requirement_llm") or (report.get("group_specs", {}).get(row.get("group"), {}) or {}).get("guidance_llm") for row in rows)
    milvus_requested = any((report.get("group_specs", {}).get(row.get("group"), {}) or {}).get("milvus") for row in rows)
    notes = []
    if external_blocked:
        notes.append("当前运行环境阻断了外部模型网络请求，DashScope embedding / 生成模型调用出现连接失败；因此 A2/B/C 等外部增强组主要反映“外部依赖不可达时的降级表现”，不能代表真实联网环境下的模型收益上限。")
    if llm_requested:
        notes.append("表中的 LLM 指标按“调用尝试次数”和“实际使用率”拆开理解：`LLM` 是每 case 的 LLM 调用尝试数，`llm_*_used_rate` 在 JSON 中记录实际成功使用率。")
    if milvus_requested:
        notes.append("表中的 embedding / Milvus 调用表示该组进入了 RAG 检索路径；如果 embedding provider 不可达，系统会回落到本地结构化商品库评分。")
    return "\n".join(f"- {item}" for item in notes) if notes else "- 未检测到额外环境限制。"


def render_capability_markdown_sections(report: Dict[str, Any]) -> List[str]:
    lines = ["", "## stability_eval", ""]
    lines.extend([
        "| group | cases | success | route | tool | violation | catalog_gap | error | no_500 | timeout | fallback | failed |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for group, item in report.get("stability_eval", {}).items():
        lines.append(
            f"| {group} | {item['case_count']} | {fmt(item['success_rate'])} | {fmt(item['route_accuracy'])} | {fmt(item.get('tool_call_accuracy'))} | "
            f"{fmt(item['constraint_violation_rate'])} | {fmt(item['catalog_gap_correct_rate'])} | {fmt(item['error_rate'])} | "
            f"{fmt(item.get('no_500_rate'))} | {fmt(item['timeout_rate'])} | {fmt(item['fallback_triggered_rate'])} | {item['failed_count']} |"
        )

    lines.extend(["", "## capability_eval", ""])
    lines.extend([
        "| group | cases | llm_attempted | llm_success | llm_applied | router a/s/ap | parse a/s/ap | guidance a/s/ap | rag_attempted | embedding | milvus | nonempty | timeout | fallback | rag_n | rag_hit@5 | rag_p@1 | rag_mrr | rag_top1_changed | evidence_reason | llm_timeout | llm_json_invalid | llm_provider_error |",
        "| --- | ---: | ---: | ---: | ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for group, item in report.get("capability_eval", {}).items():
        lines.append(
            f"| {group} | {item.get('case_count', 0)} | {fmt(item.get('llm_attempted_rate'))} | {fmt(item.get('llm_success_rate', item.get('llm_used_rate')))} | {fmt(item.get('llm_applied_rate'))} | "
            f"{fmt(item.get('llm_router_attempted_rate'))}/{fmt(item.get('llm_router_success_rate'))}/{fmt(item.get('llm_router_applied_rate'))} | "
            f"{fmt(item.get('llm_parse_attempted_rate'))}/{fmt(item.get('llm_parse_success_rate', item.get('llm_parse_used_rate')) )}/{fmt(item.get('llm_parse_applied_rate'))} | "
            f"{fmt(item.get('llm_guidance_attempted_rate'))}/{fmt(item.get('llm_guidance_success_rate', item.get('guidance_used_rate')) )}/{fmt(item.get('llm_guidance_applied_rate'))} | "
            f"{fmt(item.get('rag_attempted_rate'))} | {fmt(item.get('embedding_success_rate'))} | {fmt(item.get('milvus_success_rate'))} | "
            f"{fmt(item.get('retrieval_nonempty_rate'))} | {fmt(item.get('retrieval_timeout_rate'))} | {fmt(item.get('fallback_to_catalog_rate'))} | "
            f"{item.get('rag_contribution_evaluable_count', 0)} | {fmt(item.get('rag_effective_hit@5'))} | {fmt(item.get('rag_effective_precision@1'))} | "
            f"{fmt(item.get('rag_effective_mrr'))} | {fmt(item.get('rag_changed_top1_rate'))} | {fmt(item.get('rag_evidence_used_in_reason_rate'))} | "
            f"{fmt(item.get('llm_timeout_rate'))} | {fmt(item.get('llm_json_invalid_rate'))} | {fmt(item.get('llm_provider_error_rate'))} |"
        )

    lines.extend(render_vs_fast_delta_markdown(report))
    lines.extend(render_rag_diagnostics_markdown(report))
    lines.extend(render_llm_diagnostics_markdown(report))
    lines.extend(render_capability_challenge_assessment_markdown(report))

    lines.extend(["", "## capability/stability conclusions", ""])
    for section, items in (report.get("ablation_conclusions") or {}).items():
        lines.append(f"### {section}")
        if isinstance(items, dict):
            for title, text in items.items():
                lines.append(f"- {title}: {text}")
        else:
            lines.append(f"- {section}: {items}")
    return lines


def render_capability_challenge_assessment_markdown(report: Dict[str, Any]) -> List[str]:
    item = report.get("capability_challenge_assessment") or {}
    lines = ["", "## capability_challenge_assessment", ""]
    if not item:
        return lines + ["- No assessment available."]
    lines.append(f"- main_metrics_identical: {item.get('main_metrics_identical')}")
    uplift = item.get("top1_uplift_cases_vs_fast") or {}
    for group, cases in uplift.items():
        lines.append(f"- {group} top1 wins vs fast: {len(cases)} ({', '.join(cases[:8]) if cases else 'none'})")
    for note in item.get("notes") or []:
        lines.append(f"- {note}")
    return lines


def render_vs_fast_delta_markdown(report: Dict[str, Any]) -> List[str]:
    rows = report.get("vs_fast_delta") or []
    lines = ["", "## vs_fast_delta", ""]
    if not rows:
        return lines + ["- No fast_baseline rows available for aligned comparison."]
    lines.extend([
        "| case_id | fast_top1 | balanced_top1 | balanced_win | c2_top1 | c2_win | full_top1 | full_win | fast_rank | balanced_rank | c2_rank | full_rank |",
        "| --- | --- | --- | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ])
    for row in rows[:80]:
        lines.append(
            f"| {md(row['case_id'])} | {md(row.get('fast_top1'))} | {md(row.get('balanced_demo_top1'))} | {int(bool(row.get('balanced_demo_top1_win_vs_fast')))} | "
            f"{md(row.get('full_no_query_expansion_top1'))} | {int(bool(row.get('full_no_query_expansion_top1_win_vs_fast')))} | "
            f"{md(row.get('full_llm_all_top1'))} | {int(bool(row.get('full_llm_all_top1_win_vs_fast')))} | "
            f"{fmt(row.get('fast_expected_rank'))} | {fmt(row.get('balanced_demo_expected_rank'))} | {fmt(row.get('full_no_query_expansion_expected_rank'))} | {fmt(row.get('full_llm_all_expected_rank'))} |"
        )
    return lines


def render_rag_diagnostics_markdown(report: Dict[str, Any]) -> List[str]:
    rows = report.get("rag_diagnostics") or []
    lines = ["", "## RAG diagnostics", ""]
    if not rows:
        return lines + ["- No RAG-attempted rows."]
    lines.extend([
        "| group | case_id | status | retrieved | expected_rank_in_retrieval | rec_rank | fallback | timeout | evidence_card | evidence_reason |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    for row in rows[:120]:
        lines.append(
            f"| {row['group']} | {md(row['case_id'])} | {int(bool(row.get('rag_contribution_evaluable')))} | "
            f"{md(','.join(row.get('retrieved_top_ids') or [])[:180])} | {fmt(row.get('expected_id_retrieved_rank'))} | "
            f"{fmt(row.get('expected_id_recommended_rank'))} | {int(bool(row.get('fallback_to_catalog')))} | {int(bool(row.get('retrieval_timeout')))} | "
            f"{int(bool(row.get('rag_evidence_used_in_card')))} | {int(bool(row.get('rag_evidence_used_in_reason')))} |"
        )
    return lines


def render_llm_diagnostics_markdown(report: Dict[str, Any]) -> List[str]:
    rows = report.get("llm_diagnostics") or []
    lines = ["", "## LLM diagnostics", ""]
    if not rows:
        return lines + ["- No LLM-attempted rows."]
    lines.extend([
        "| group | case_id | router a/s/ap | router_failure | local | llm | final | parse a/s/ap | parse_failure | guidance a/s/ap | guidance_failure | changed_route | changed_rec | clarification |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: |",
    ])
    for row in rows[:120]:
        lines.append(
            f"| {row['group']} | {md(row['case_id'])} | {int(row.get('router_attempted'))}/{int(row.get('router_success'))}/{int(row.get('router_applied'))} | "
            f"{md(str(row.get('llm_router_failure_reason') or row.get('router_failure_reason') or '')[:30])} | "
            f"{md(row.get('local_route'))} | {md(row.get('llm_route'))} | {md(row.get('final_route'))} | "
            f"{int(row.get('parse_attempted'))}/{int(row.get('parse_success'))}/{int(row.get('parse_applied'))} | "
            f"{md(str(row.get('llm_parse_failure_reason') or '')[:30])} | "
            f"{int(row.get('guidance_attempted'))}/{int(row.get('guidance_success'))}/{int(row.get('guidance_applied'))} | "
            f"{md(str(row.get('llm_guidance_failure_reason') or '')[:30])} | "
            f"{int(bool(row.get('route_changed_by_llm')))} | {int(bool(row.get('llm_changed_recommendation')))} | {int(bool(row.get('llm_triggered_clarification')))} |"
        )
    return lines


def render_recommendations(report: Dict[str, Any]) -> List[str]:
    summaries = report["summary_by_group"]
    balanced = summaries.get("balanced_demo", {})
    full = summaries.get("full_llm_all", {})
    fast = summaries.get("fast_baseline", {})
    recs = [
        "- 默认部署建议：优先使用 balanced_demo 作为 demo / 准生产默认模式，前提是 LLM 与 Milvus 环境稳定。",
        "- CI 与降级建议：fast_baseline 适合作为稳定回归和外部依赖不可用时的兜底模式；不要把 fast 描述为使用 Milvus。",
        "- full 建议：full_llm_all 更适合离线评估或高质量请求，不建议在没有延迟、成本和失败率监控前默认开启。",
        "- query expansion 建议：默认关闭；只有当 A2/C2 对比证明召回收益明显且 query 漂移可控时再打开。",
        "- guidance 建议：默认关闭或仅在商品卡片稳定后开启；开启后要持续检查 reason 是否被商品证据支撑。",
    ]
    if full and balanced and float(full.get("p95_latency_ms") or 0) > float(balanced.get("p95_latency_ms") or 0) * 1.5:
        recs.append("- 本次 full 的 p95 延迟明显高于 balanced，线上默认开启 full 风险偏高。")
    if fast and float(fast.get("success_rate") or 0) > 0:
        recs.append("- fast 仍有可用结果，说明规则 + 本地 catalog scoring 可以承担基础兜底。")
    failed_total = sum(item.get("failed_count", 0) for item in summaries.values())
    if failed_total:
        recs.append("- 后续修复优先级：先看 wrong_route 和 negative_guard_failed，再看 final_recommendation_miss；前两类更可能影响演示稳定性。")
    return recs


def hit_at_k_rate(rows: List[Dict[str, Any]], k: int) -> float:
    return rate(bool(set(row.get("recommended_product_ids", [])[:k]) & set(row.get("expected_product_ids", []))) for row in rows)


def precision_at_k_rate(rows: List[Dict[str, Any]], k: int) -> float:
    vals = []
    for row in rows:
        relevant = set(row.get("expected_product_ids", []))
        ranked = row.get("recommended_product_ids", [])[:k]
        if relevant:
            vals.append(len(set(ranked) & relevant) / float(k))
    return mean(vals)


def recall_at_k_rate(rows: List[Dict[str, Any]], k: int) -> float:
    vals = []
    for row in rows:
        relevant = set(row.get("expected_product_ids", []))
        ranked = row.get("recommended_product_ids", [])[:k]
        if relevant:
            vals.append(len(set(ranked) & relevant) / float(len(relevant)))
    return mean(vals)


def reciprocal_rank(row: Dict[str, Any]) -> float:
    relevant = set(row.get("expected_product_ids", []))
    for index, product_id in enumerate(row.get("recommended_product_ids", []), 1):
        if product_id in relevant:
            return 1.0 / index
    return 0.0


def ndcg_at_k(row: Dict[str, Any], k: int) -> float:
    relevant = set(row.get("expected_product_ids", []))
    if not relevant:
        return 0.0
    dcg = 0.0
    for index, product_id in enumerate((row.get("recommended_product_ids") or [])[:k], 1):
        if product_id in relevant:
            dcg += 1.0 / (index + 1)
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / (index + 1) for index in range(1, ideal_hits + 1))
    return round(dcg / idcg, 4) if idcg else 0.0


def first_or_empty(items: Any) -> str:
    if not items:
        return ""
    return str(items[0])


def rate(values: Iterable[Any]) -> float:
    vals = [bool(item) for item in values]
    return round(sum(1 for item in vals if item) / len(vals), 4) if vals else 0.0


def mean(values: Iterable[Any]) -> float:
    nums = [float(item) for item in values if item is not None]
    return round(float(statistics.mean(nums)), 4) if nums else 0.0


def percentile(values: Sequence[float], pct: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(item) for item in values)
    index = min(len(ordered) - 1, max(0, round((pct / 100) * (len(ordered) - 1))))
    return round(ordered[index], 4)


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sanitize_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    for key in ("DASHSCOPE_API_KEY", "EMBEDDING_API_KEY", "OPENAI_API_KEY", "ARK_API_KEY", "MALLMIND_LLM_API_KEY", "DEEPSEEK_API_KEY", "MIMO_API_KEY"):
        secret = os.getenv(key)
        if secret:
            text = text.replace(secret, "***")
    return text


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


def md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")[:240]


def fmt(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
