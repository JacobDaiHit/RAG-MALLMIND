"""Run fixture cases through the real V3 SSE entrypoint in fixed-size batches."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_CASES = ROOT / "tests" / "fixtures" / "full_chain_eval_cases.json"

from fastapi.testclient import TestClient

from rag.api.recommendation_app import app


def parse_events(raw: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in raw.strip().split("\n\n"):
        if not block.strip():
            continue
        name = next((line[7:] for line in block.splitlines() if line.startswith("event: ")), "message")
        raw_data = next((line[6:] for line in block.splitlines() if line.startswith("data: ")), "{}")
        events.append((name, json.loads(raw_data)))
    return events


def evaluate_case(client: TestClient, case: dict[str, Any], number: int) -> dict[str, Any]:
    turns = case.get("turns") or [{"query": case["query"]}]
    raw_case_id = str(case.get("case_id") or "").strip()
    # Fixture IDs are display metadata, not a source of execution identity.
    # The ordinal is stable for one fixture revision and prevents duplicate or
    # malformed IDs from merging sessions/results.
    report_case_id = raw_case_id or f"fixture_case_{number:03d}"
    session_id = f"v3-full-chain-{number:03d}"
    turn_results = []
    for turn in turns:
        response = client.post("/api/chat/stream", json={"session_id": session_id, "message": turn["query"], "attachments": [], "images": []})
        events = parse_events(response.text)
        route = next((data for name, data in events if name == "v3_routing"), {})
        tool = next((data for name, data in events if name == "tool_call"), {})
        result = next((data for name, data in events if name == "result"), {})
        cards = next((data.get("cards", []) for name, data in events if name == "product_cards"), [])
        error = next((data for name, data in events if name == "error"), {})
        clarification = next((data for name, data in events if name == "clarification"), {})
        trace = result.get("trace") or {}
        turn_results.append({
            "query": turn["query"],
            "http_status": response.status_code,
            "route": route,
            "tool": tool.get("name"),
            "product_ids": [str(card.get("product_id")) for card in cards],
            "categories": [str(card.get("category")) for card in cards],
            "retrieval": trace.get("v3_retrieval") or {},
            "candidate_gate": trace.get("v3_candidate_gate") or {},
            "error": error,
            "clarification": clarification,
        })
    final = turn_results[-1]
    final_expectation = {
        "expected_tool": case.get("expected_tool"),
        "expected_outcome": case.get("expected_outcome"),
        "expected_reason": case.get("expected_reason"),
        "expected_product_ids": case.get("expected_product_ids"),
        "acceptable_product_ids": case.get("acceptable_product_ids"),
        "expected_category": case.get("expected_category"),
    }
    expected_turns = case.get("expected_turns") or [final_expectation]
    if len(expected_turns) != len(turn_results):
        raise ValueError(f"{report_case_id}: expected_turns must contain one entry per user turn")
    turn_checks = [_evaluate_turn(turn, expected) for turn, expected in zip(turn_results, expected_turns)]
    final_checks = turn_checks[-1]
    external_called = all(check["external_chat"] for check in turn_checks)
    passed = all(check["outcome"] for check in turn_checks) and external_called
    expected = set(case.get("expected_product_ids") or []) | set(case.get("acceptable_product_ids") or [])
    expected_tool = str(case.get("expected_tool") or "")
    expected_outcome = str(case.get("expected_outcome") or "tool")
    expected_reason = str(case.get("expected_reason") or "")
    category = case.get("expected_category")
    return {
        "case_number": number,
        "case_id": report_case_id,
        "fixture_case_id": raw_case_id or None,
        "query_or_turns": [item["query"] for item in turn_results],
        "expected_tool": expected_tool,
        "expected_outcome": expected_outcome,
        "expected_reason": expected_reason or None,
        "expected_product_ids": sorted(expected),
        "expected_category": category,
        "passed": passed,
        "checks": {
            "tool": final_checks["tool"],
            "product": final_checks["product"],
            "category": final_checks["category"],
            "outcome": final_checks["outcome"],
            "external_chat": external_called,
            "embedding_milvus": final_checks["embedding_milvus"],
        },
        "turn_checks": turn_checks,
        "turns": turn_results,
    }


def _evaluate_turn(turn: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    expected_ids = set(expected.get("expected_product_ids") or []) | set(expected.get("acceptable_product_ids") or [])
    actual_ids = set(turn["product_ids"])
    expected_tool = str(expected.get("expected_tool") or "")
    expected_outcome = str(expected.get("expected_outcome") or "tool")
    expected_reason = str(expected.get("expected_reason") or "")
    tool_ok = turn["tool"] == expected_tool if expected_tool else turn["tool"] is None
    product_ok = not expected_ids or bool(expected_ids & actual_ids)
    category = expected.get("expected_category")
    recommendation = expected_tool == "recommend_shopping_products"
    category_ok = not recommendation or not category or category in turn["categories"]
    retrieval_ok = turn["retrieval"].get("status") == "ok" and bool(turn["retrieval"].get("filter_expression"))
    clarification_reason = str(turn["clarification"].get("reason") or "")
    if expected_outcome == "clarification":
        outcome_ok = bool(turn["clarification"]) and (not expected_reason or clarification_reason == expected_reason)
        retrieval_applicable = False
    elif expected_outcome == "no_candidates":
        outcome_ok = tool_ok and not turn["product_ids"] and not turn["clarification"] and not turn["candidate_gate"].get("allowed_product_ids") and (
            not turn["error"] or str(turn["error"].get("reason") or "") == "catalog_scope_unsupported"
        )
        retrieval_applicable = False
    elif expected_outcome == "rejection":
        outcome_ok = bool(turn["error"]) and (not expected_reason or str(turn["error"].get("reason") or "") == expected_reason)
        retrieval_applicable = False
    else:
        retrieval_applicable = recommendation
        outcome_ok = tool_ok and product_ok and category_ok and (retrieval_ok if retrieval_applicable else True)
    return {
        "expected_tool": expected_tool or None,
        "expected_outcome": expected_outcome,
        "expected_reason": expected_reason or None,
        "tool": tool_ok,
        "product": product_ok,
        "category": category_ok,
        "outcome": outcome_ok,
        "external_chat": bool(turn["route"].get("semantic_provider")),
        "embedding_milvus": retrieval_ok if retrieval_applicable else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--size", type=int, default=5)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--append", action="store_true", help="Append this case range to an existing batch JSON instead of replacing it.")
    args = parser.parse_args()
    os.environ["V3_RETRIEVAL_ENABLED"] = "true"
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    selected = cases[args.start:args.start + args.size]
    selected_ids = [str(case.get("case_id") or "").strip() for case in selected]
    duplicate_ids = sorted({item for item in selected_ids if item and selected_ids.count(item) > 1})
    missing_id_case_numbers = [args.start + index + 1 for index, item in enumerate(selected_ids) if not item]
    with TestClient(app) as client:
        results = [evaluate_case(client, case, args.start + index + 1) for index, case in enumerate(selected)]
    run = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "start": args.start,
        "size": args.size,
        "case_id_audit": {
            "missing_case_numbers": missing_id_case_numbers,
            "duplicate_case_ids": duplicate_ids,
            "execution_identity": "fixture ordinal",
        },
        "results": results,
        "summary": {"passed": sum(item["passed"] for item in results), "failed": sum(not item["passed"] for item in results)},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.append and args.output.exists():
        existing = json.loads(args.output.read_text(encoding="utf-8"))
        prior_runs = existing.get("runs") or [{
            "generated_at": existing.get("generated_at"),
            "start": existing.get("start"),
            "size": existing.get("size"),
            "results": existing.get("results", []),
            "summary": existing.get("summary", {}),
        }]
        # Re-running the same range replaces only that range.  This keeps one
        # compact batch report while allowing a failed group to be repaired and
        # retested without duplicating cases.
        preserved_runs = [prior_run for prior_run in prior_runs if prior_run.get("start") != args.start]
        runs = sorted([*preserved_runs, run], key=lambda item: int(item.get("start") or 0))
        all_results = [item for stored_run in runs for item in stored_run.get("results", [])]
    else:
        all_results = results
        runs = [run]
    payload = {
        "generated_at": run["generated_at"],
        "cases_file": str(args.cases),
        "external_chat_required": True,
        "embedding_milvus_required": True,
        "runs": runs,
        "results": all_results,
        "summary": {"passed": sum(item["passed"] for item in all_results), "failed": sum(not item["passed"] for item in all_results)},
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(run["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
