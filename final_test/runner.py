"""Run the fixed V3 quality set against a real MallMind HTTP server.

This runner deliberately does not import FastAPI's TestClient.  Supplying
``--base-url http://127.0.0.1:8000`` exercises the configured external Chat
model, embedding provider, Milvus, and session backend exactly as the running
service sees them.  It writes raw per-turn evidence plus computed JSON/Markdown
reports; a passing pytest suite is never substituted for this evaluation.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from final_test.metrics import markdown_report, summarize
from final_test.sse import post_json, post_sse
from rag.recommendation.product_loader import load_combined_product_catalog
from rag.recommendation.v3.registry import CatalogNormalizationRegistry


DEFAULT_CASES = ROOT / "final_test" / "fixtures" / "fixed_eval_cases.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run fixed V3 quality metrics against a real SSE server.")
    parser.add_argument("--base-url", required=True, help="For example: http://127.0.0.1:8000")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "final_test" / "results")
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    parser.add_argument("--run-id", default="", help="Optional unique session namespace; default is generated for every run.")
    parser.add_argument("--limit", type=int, default=0, help="Run only the first N cases; 0 means all.")
    parser.add_argument("--case-id", action="append", default=[], help="Run one named case; repeat this flag for multiple cases.")
    args = parser.parse_args()

    cases = _load_cases(args.cases)
    if args.case_id:
        selected = set(args.case_id)
        cases = [case for case in cases if case["case_id"] in selected]
        missing = selected - {case["case_id"] for case in cases}
        if missing:
            raise ValueError(f"unknown fixed-eval case_id(s): {', '.join(sorted(missing))}")
    if args.limit:
        cases = cases[: args.limit]
    catalog = load_combined_product_catalog()
    registry = CatalogNormalizationRegistry.from_catalog(catalog)
    run_id = args.run_id.strip() or datetime.now().strftime("%Y%m%d%H%M%S%f")
    records = list(_run_cases(cases, run_id=run_id, base_url=args.base_url, timeout_seconds=args.timeout_seconds, catalog=catalog, registry=registry))
    summary = summarize(records)
    generated_at = datetime.now(timezone.utc).astimezone().isoformat()
    payload = {
        "generated_at": generated_at,
        "base_url": args.base_url,
        "cases_file": str(args.cases),
        "mode": "live_http",
        "run_id": run_id,
        "records": records,
        "summary": summary,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = args.output_dir / f"v3_fixed_eval_{stamp}.json"
    markdown_path = args.output_dir / f"v3_fixed_eval_{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(markdown_report(summary, records), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(markdown_path), "pass_rate": summary["pass_rate"]}, ensure_ascii=False))


def _load_cases(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("fixed evaluation fixture must be a JSON array")
    identifiers = [str(item.get("case_id") or "") for item in raw]
    if not all(identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError("each fixed evaluation case requires a unique case_id")
    return raw


def _run_cases(
    cases: Iterable[dict[str, Any]], *, run_id: str, base_url: str, timeout_seconds: float, catalog: Any, registry: CatalogNormalizationRegistry
) -> Iterable[dict[str, Any]]:
    for case in cases:
        # Every runner invocation gets a fresh namespace.  Turns within one
        # case intentionally share it, while a later rerun cannot inherit
        # cards, pending clarification, or cart state from an earlier run.
        session_id = f"final-eval-{run_id}-{case['case_id']}"
        turns = case.get("turns") or [{
            "text": case.get("text"),
            "expect": case.get("expect") or {},
            "attachments": case.get("attachments") or [],
            "images": case.get("images") or [],
        }]
        for index, turn in enumerate(turns, start=1):
            expect = dict(case.get("expect") or {})
            expect.update(turn.get("expect") or {})
            transport = str(turn.get("transport") or "chat")
            if transport == "cart_confirm":
                yield _run_cart_confirm(case, index, turn, expect, session_id, base_url, timeout_seconds)
            else:
                yield _run_chat_turn(case, index, turn, expect, session_id, base_url, timeout_seconds, catalog, registry)


def _run_chat_turn(
    case: dict[str, Any],
    index: int,
    turn: dict[str, Any],
    expect: dict[str, Any],
    session_id: str,
    base_url: str,
    timeout_seconds: float,
    catalog: Any,
    registry: CatalogNormalizationRegistry,
) -> dict[str, Any]:
    text = str(turn.get("text") or "")
    exchange = post_sse(
        base_url=base_url,
        path="/api/chat/stream",
        payload={
            "session_id": session_id,
            "message": text,
            "attachments": list(turn.get("attachments") or []),
            "images": list(turn.get("images") or []),
        },
        timeout_seconds=timeout_seconds,
    )
    events = _event_map(exchange.events)
    route = _first(events, "v3_routing")
    decision_trace = _first(events, "v3_trace")
    runtime = _first(events, "runtime_mode")
    result = _first(events, "result")
    requirement = result.get("requirement_v3") if isinstance(result.get("requirement_v3"), dict) else {}
    cards = _first(events, "product_cards").get("cards") or []
    error = _first(events, "error")
    clarification = _first(events, "clarification")
    product_fact = _first(events, "product_fact")
    comparison = _first(events, "comparison_table")
    cart_plan = _first(events, "cart_confirmation")
    cart_view = _first(events, "cart")
    pc_plan = _first(events, "pc_build_plan")
    pc_comparison = _first(events, "pc_plan_comparison")
    actual_action = route.get("action") or _first(events, "tool_call").get("name")
    actual_outcome = "rejection" if exchange.status_code >= 400 else _outcome(cards, error, clarification, product_fact, comparison, cart_plan, cart_view, pc_plan, pc_comparison, actual_action)
    constraint_expected = dict(expect.get("constraints") or {})
    constraint_checks = _constraint_checks(
        requirement,
        constraint_expected,
        route=route,
        cart_plan=cart_plan,
        product_fact=product_fact,
        comparison=comparison,
    )
    fact_checks = _fact_checks(cards, product_fact, comparison, constraint_expected, catalog, registry)
    semantic_usage = route.get("semantic_usage") if isinstance(route.get("semantic_usage"), dict) else {}
    semantic_attempts = route.get("semantic_attempts") if isinstance(route.get("semantic_attempts"), list) else []
    general_usage = [payload for payload in events.get("model_usage", []) if isinstance(payload, dict)]
    token_values = [value for value in [semantic_usage.get("total_tokens"), *[item.get("total_tokens") for item in general_usage]] if isinstance(value, int)]
    llm_calls = (len(semantic_attempts) if route.get("semantic_parse_called") else 0) + len(general_usage)
    route_correct = _route_correct(actual_action, expect.get("action"))
    outcome_correct = _outcome_correct(actual_outcome, expect.get("outcome"), expect.get("reason"), error, clarification)
    safe_direct_correct = bool(route_correct and all(constraint_checks.values()) and expect.get("safe_direct") != "forbid")
    fact_correct = _facts_correct(fact_checks, actual_outcome)
    expected_http_rejection = expect.get("outcome") == "rejection" and exchange.status_code >= 400
    passed = bool((exchange.status_code == 200 or expected_http_rejection) and route_correct and outcome_correct and all(constraint_checks.values()) and fact_correct)
    return {
        "case_id": case["case_id"],
        "turn_id": f"turn_{index}",
        "text": text,
        "expected_action": expect.get("action"),
        "expected_outcome": expect.get("outcome"),
        "expected_reason": expect.get("reason"),
        "expected_domain": expect.get("domain"),
        "safe_direct_policy": expect.get("safe_direct", "ignore"),
        "constraint_expected": constraint_expected,
        "actual_action": actual_action,
        "actual_status": route.get("status"),
        "actual_reason": route.get("reason") or error.get("reason"),
        "decision_trace": decision_trace,
        "actual_error": error,
        "actual_clarification": clarification,
        "actual_outcome": actual_outcome,
        "semantic_parse_called": bool(route.get("semantic_parse_called")),
        "semantic_attempts": semantic_attempts,
        "semantic_attempt_count": len(semantic_attempts),
        "semantic_schema_retry": any(isinstance(item, dict) and item.get("outcome") == "schema_invalid" for item in semantic_attempts),
        "llm_calls": llm_calls,
        "total_tokens": sum(token_values) if token_values else None,
        "first_event_ms": exchange.first_event_ms,
        "first_business_event_ms": exchange.first_business_event_ms,
        "total_ms": exchange.total_ms,
        "http_status": exchange.status_code,
        "transport_error": exchange.transport_error,
        "route_correct": route_correct,
        "safe_direct_correct": safe_direct_correct,
        "constraint_checks": constraint_checks,
        "fact_checks": fact_checks,
        "candidate_allowlist_nonempty": bool((_first(events, "candidate_gate").get("allowed_product_ids") or [])),
        "retrieval_status": ((result.get("trace") or {}).get("v3_retrieval") or {}).get("status"),
        "recommendation_returned": bool(cards),
        "expired_card_misuse": int(bool(expect.get("expired_card")) and actual_outcome == "fact"),
        "events": [name for name, _payload in exchange.events],
        "passed": passed,
        "failure_reason": _failure_reason(exchange, route_correct, outcome_correct, constraint_checks, fact_correct),
    }


def _run_cart_confirm(
    case: dict[str, Any], index: int, turn: dict[str, Any], expect: dict[str, Any], session_id: str, base_url: str, timeout_seconds: float
) -> dict[str, Any]:
    status, data, total_ms, transport_error = post_json(
        base_url=base_url,
        path="/api/cart/confirm",
        payload={"session_id": session_id, "confirmed": bool(turn.get("confirmed", True))},
        timeout_seconds=timeout_seconds,
    )
    outcome = "cart_applied" if data.get("status") == "applied" else "cart_cancelled" if data.get("status") == "cancelled" else "rejection"
    outcome_correct = not expect.get("outcome") or outcome == expect.get("outcome")
    return {
        "case_id": case["case_id"], "turn_id": f"turn_{index}", "text": "[cart_confirm]",
        "expected_action": None, "expected_outcome": expect.get("outcome"), "expected_reason": expect.get("reason"),
        "expected_domain": expect.get("domain"), "safe_direct_policy": "ignore", "constraint_expected": {},
        "actual_action": "apply_cart_instruction", "actual_status": "CONFIRMED", "actual_reason": "",
        "actual_error": {}, "actual_clarification": {}, "actual_outcome": outcome,
        "semantic_parse_called": False, "llm_calls": 0, "total_tokens": None,
        "first_event_ms": None, "first_business_event_ms": None, "total_ms": total_ms, "http_status": status, "transport_error": transport_error,
        "route_correct": True, "safe_direct_correct": True, "constraint_checks": {},
        "fact_checks": {"product_ids_valid": True, "price_checked": False, "sku_checked": False, "stock_checked": False},
        "candidate_allowlist_nonempty": False, "retrieval_status": None, "recommendation_returned": False,
        "expired_card_misuse": 0, "events": ["cart_confirm"],
        "passed": bool(status == 200 and outcome_correct), "failure_reason": "" if status == 200 and outcome_correct else f"cart_confirm:{status}:{outcome}",
    }


def _event_map(events: tuple[tuple[str, dict[str, Any]], ...]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for name, payload in events:
        grouped.setdefault(name, []).append(payload)
    return grouped


def _first(events: dict[str, list[dict[str, Any]]], name: str) -> dict[str, Any]:
    values = events.get(name) or []
    return values[0] if values and isinstance(values[0], dict) else {}


def _outcome(cards: list[Any], error: dict[str, Any], clarification: dict[str, Any], product_fact: dict[str, Any], comparison: dict[str, Any], cart_plan: dict[str, Any], cart_view: dict[str, Any], pc_plan: dict[str, Any], pc_comparison: dict[str, Any], action: str | None) -> str:
    if error:
        return "rejection"
    if clarification:
        return "clarification"
    if cards:
        return "recommendation"
    if product_fact or comparison.get("rows"):
        return "fact"
    if cart_plan:
        return "cart_plan"
    if cart_view:
        return "cart_view"
    if pc_plan or pc_comparison:
        return "pc_plan"
    if action == "general_chat":
        return "general_chat"
    return "unknown"


def _route_correct(actual: str | None, expected: object) -> bool:
    return expected is None or actual == expected


def _outcome_correct(actual: str, expected: object, expected_reason: object, error: dict[str, Any], clarification: dict[str, Any]) -> bool:
    if expected is None:
        return True
    if actual != expected:
        return False
    if expected == "rejection":
        return not expected_reason or error.get("reason") == expected_reason
    if expected == "clarification":
        return bool(clarification) and (not expected_reason or clarification.get("reason") == expected_reason)
    return not error


def _constraint_checks(
    requirement: dict[str, Any],
    expected: dict[str, Any],
    *,
    route: dict[str, Any],
    cart_plan: dict[str, Any],
    product_fact: dict[str, Any],
    comparison: dict[str, Any],
) -> dict[str, bool]:
    mapping = {
        "include_brand": "include_brand_family_ids",
        "exclude_brand": "exclude_brand_family_ids",
        "product_type": "product_type_ids",
        "exclude_type": "exclude_product_type_ids",
        "price_max": "price_max",
        "price_min": "price_min",
        "price_target": "price_target",
        "recommendation_mode": "recommendation_mode",
    }
    checks: dict[str, bool] = {}
    for metric, field in mapping.items():
        if metric not in expected:
            continue
        wanted = expected[metric]
        actual = requirement.get(field)
        checks[metric] = set(wanted).issubset(set(actual or [])) if isinstance(wanted, list) else actual == wanted
    if "brand_release" in expected:
        released = set(expected["brand_release"] if isinstance(expected["brand_release"], list) else [expected["brand_release"]])
        checks["brand_release"] = not bool(released & set(requirement.get("exclude_brand_family_ids") or []))
    if "quantity" in expected:
        plan = cart_plan.get("plan") if isinstance(cart_plan.get("plan"), dict) else {}
        checks["quantity"] = plan.get("quantity") == expected["quantity"]
    if "card_reference" in expected:
        checks["card_reference"] = bool(product_fact.get("card_id") or cart_plan)
    if "comparison_reference" in expected:
        checks["comparison_reference"] = len(comparison.get("rows") or []) == 2
    if "computer_purchase_kind" in expected:
        checks["computer_purchase_kind"] = route.get("computer_purchase_kind") == expected["computer_purchase_kind"]
    if "multi_category" in expected:
        total_types = len(requirement.get("product_type_ids") or []) + len(requirement.get("exclude_product_type_ids") or [])
        checks["multi_category"] = total_types >= int(expected["multi_category"])
    return checks


def _fact_checks(cards: list[Any], product_fact: dict[str, Any], comparison: dict[str, Any], expected: dict[str, Any], catalog: Any, registry: CatalogNormalizationRegistry) -> dict[str, bool]:
    products = [catalog.get(str(card.get("product_id") or "")) for card in cards if isinstance(card, dict)]
    ids_valid = bool(cards) and all(product is not None for product in products)
    facts = product_fact.get("facts") if isinstance(product_fact.get("facts"), dict) else {}
    fact_product = catalog.get(str(product_fact.get("product_id") or "")) if product_fact else None
    price_checked = bool(cards) or "base_price" in facts
    price_consistent = True
    sku_checked = bool(cards) or "skus" in facts
    sku_consistent = True
    specs_checked = "specs" in facts
    specs_consistent = True
    for card, product in zip(cards, products):
        if product is None:
            price_consistent = sku_consistent = False
            continue
        price_consistent &= float(card.get("price")) == float(product.min_price or product.base_price)
        expected_skus = {str(item.sku_id): float(item.price if item.price is not None else product.base_price) for item in product.skus}
        for sku in card.get("skus") or []:
            sku_consistent &= str(sku.get("sku_id")) in expected_skus and float(sku.get("price")) == expected_skus[str(sku.get("sku_id"))]
    if "base_price" in facts:
        price_consistent &= fact_product is not None and float(facts.get("base_price")) == float(fact_product.base_price)
        price_consistent &= fact_product is not None and float(facts.get("min_price")) == float(fact_product.min_price)
        price_consistent &= fact_product is not None and float(facts.get("max_price")) == float(fact_product.max_price)
    if "skus" in facts:
        expected_skus = {
            str(item.sku_id): (dict(item.properties or {}), float(item.price if item.price is not None else fact_product.base_price))
            for item in (fact_product.skus if fact_product is not None else ())
        }
        for sku in facts.get("skus") or []:
            key = str(sku.get("sku_id") or "")
            sku_consistent &= key in expected_skus
            if key in expected_skus:
                expected_properties, expected_price = expected_skus[key]
                sku_consistent &= dict(sku.get("properties") or {}) == expected_properties and float(sku.get("price")) == expected_price
    if "specs" in facts:
        specs_consistent &= fact_product is not None and dict(facts.get("specs") or {}) == dict((fact_product.metadata or {}).get("specs") or {})
    stock_checked = False
    stock_consistent = True
    for row in comparison.get("rows") or []:
        product = catalog.get(str(row.get("product_id") or ""))
        stock_checked = True
        stock_consistent &= product is not None and str(row.get("stock_status")) == str(product.stock_status)
    excluded_brand_reappeared = False
    for card, product in zip(cards, products):
        if product is None:
            continue
        entity = registry.brand_by_surface(str(product.brand))
        excluded_brand_reappeared |= entity is not None and entity.canonical_id in set(expected.get("exclude_brand") or [])
    return {
        "product_ids_valid": ids_valid,
        "price_checked": price_checked,
        "price_consistent": price_consistent,
        "sku_checked": sku_checked,
        "sku_consistent": sku_consistent,
        "specs_checked": specs_checked,
        "specs_consistent": specs_consistent,
        "stock_checked": stock_checked,
        "stock_consistent": stock_consistent,
        "excluded_brand_reappeared": excluded_brand_reappeared,
    }


def _facts_correct(checks: dict[str, bool], outcome: str) -> bool:
    if outcome == "recommendation":
        return bool(checks.get("product_ids_valid")) and bool(checks.get("price_consistent")) and bool(checks.get("sku_consistent")) and not checks.get("excluded_brand_reappeared")
    if outcome == "fact":
        return bool(checks.get("price_consistent", True)) and bool(checks.get("sku_consistent", True)) and bool(checks.get("specs_consistent", True)) and bool(checks.get("stock_consistent", True))
    return True


def _failure_reason(exchange: Any, route_ok: bool, outcome_ok: bool, constraint_checks: dict[str, bool], fact_ok: bool) -> str:
    if exchange.transport_error:
        return f"transport:{exchange.transport_error}"
    if not route_ok:
        return "action_mismatch"
    if not outcome_ok:
        return "outcome_mismatch"
    if not all(constraint_checks.values()):
        return "constraint_mismatch"
    if not fact_ok:
        return "catalog_fact_mismatch"
    return ""


if __name__ == "__main__":
    main()
