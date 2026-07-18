"""Offline checks for metric formulae, especially false local acceptance."""
from __future__ import annotations

import pytest

from final_test.metrics import summarize


def _row(**overrides):
    base = {
        "case_id": "case", "turn_id": "turn_1", "passed": True,
        "expected_action": "recommend_shopping_products", "expected_domain": "shopping",
        "expected_outcome": "recommendation", "expected_reason": None,
        "safe_direct_policy": "ignore", "actual_action": "recommend_shopping_products",
        "actual_status": "semantic_executable", "actual_reason": "", "actual_error": {},
        "actual_clarification": {}, "semantic_parse_called": True,
        "route_correct": True, "safe_direct_correct": True, "constraint_expected": {},
        "constraint_checks": {}, "fact_checks": {"product_ids_valid": True, "price_checked": True, "price_consistent": True, "sku_checked": True, "sku_consistent": True, "stock_checked": False, "excluded_brand_reappeared": False},
        "candidate_allowlist_nonempty": True, "retrieval_status": "ok", "recommendation_returned": True,
        "llm_calls": 1, "total_tokens": 100, "first_event_ms": 20, "total_ms": 80, "expired_card_misuse": 0,
    }
    base.update(overrides)
    return base


def test_false_accept_rate_counts_only_actual_safe_direct_requests():
    summary = summarize([
        _row(actual_status="safe_direct", safe_direct_correct=True),
        _row(actual_status="safe_direct", safe_direct_correct=False),
        _row(actual_status="semantic_executable", safe_direct_correct=False),
    ])
    assert summary["local_routing"]["false_accept_rate"] == 0.5
    assert summary["local_routing"]["safe_direct_coverage"] == pytest.approx(2 / 3, abs=1e-6)


def test_unmeasured_metrics_are_none_not_fake_success():
    summary = summarize([_row(expected_outcome="general_chat", expected_domain="general", fact_checks={})])
    assert summary["facts"]["stock_consistency"] is None
    assert summary["engineering"]["redis_failure_recovery"] is None
    assert summary["engineering"]["concurrent_request_correctness"] is None
