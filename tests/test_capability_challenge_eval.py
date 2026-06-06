import json
from pathlib import Path

from scripts.eval_model_chain_ablation import build_vs_fast_delta, capability_aggregate


def test_capability_aggregate_exposes_extended_rag_llm_metrics():
    rows = [
        {
            "group": "balanced_demo",
            "status": "ok",
            "route_correct": True,
            "expected_tool": "recommend_shopping_products",
            "expected_product_ids": ["p1"],
            "recommended_product_ids": ["p1", "p2"],
            "case_group": "ecommerce",
            "constraint_violation_count": 0,
            "card_metrics": {"card_accuracy": 1.0},
            "latency_ms": 10,
            "timeout": False,
            "errors": [],
            "fallback_triggered": False,
            "degraded_success": False,
            "llm_calls": 2,
            "embedding_calls": 1,
            "milvus_calls": 1,
            "llm_router_used": True,
            "llm_parse_used": True,
            "llm_guidance_used": False,
            "query_expansion_used": False,
            "llm_attempted": True,
            "llm_used": True,
            "llm_applied": True,
            "llm_router_attempted": True,
            "llm_router_success": True,
            "llm_router_applied": True,
            "llm_parse_attempted": True,
            "llm_parse_applied": True,
            "llm_guidance_attempted": False,
            "llm_guidance_applied": False,
            "rag_attempted": True,
            "embedding_success": True,
            "milvus_success": True,
            "retrieval_nonempty": True,
            "retrieval_timeout": False,
            "fallback_to_catalog": False,
            "rag_contribution_evaluable": True,
            "rag_changed_top1": True,
            "rag_changed_top3": True,
            "rag_evidence_used_in_reason": True,
            "rag_evidence_used_in_card": True,
            "llm_changed_route": True,
            "llm_filled_missing_fields": True,
            "llm_triggered_clarification": False,
            "llm_changed_recommendation": True,
        }
    ]

    item = capability_aggregate(rows)

    assert item["llm_success_rate"] == 1.0
    assert item["llm_parse_success_rate"] == 1.0
    assert item["rag_effective_mrr"] == 1.0
    assert item["rag_changed_top1_rate"] == 1.0
    assert item["rag_evidence_used_in_reason_rate"] == 1.0


def test_build_vs_fast_delta_marks_top1_uplift():
    rows = [
        {
            "case_id": "c1",
            "group": "fast_baseline",
            "query": "q",
            "expected_product_ids": ["p1"],
            "recommended_product_ids": ["p2", "p1"],
            "expected_id_recommended_rank": 2,
            "constraint_violation_count": 0,
            "no_match_reason": None,
            "grounded_reason_score": 0.5,
            "latency_ms": 10,
            "timeout": False,
        },
        {
            "case_id": "c1",
            "group": "balanced_demo",
            "query": "q",
            "expected_product_ids": ["p1"],
            "recommended_product_ids": ["p1", "p2"],
            "expected_id_recommended_rank": 1,
            "constraint_violation_count": 0,
            "no_match_reason": None,
            "grounded_reason_score": 0.8,
            "latency_ms": 20,
            "timeout": False,
            "fallback_to_catalog": False,
            "rag_evidence_used_in_card": True,
            "rag_evidence_used_in_reason": False,
        },
    ]

    delta = build_vs_fast_delta(rows)[0]

    assert delta["balanced_demo_top1_win_vs_fast"] is True
    assert delta["balanced_demo_mrr_delta_vs_fast"] > 0
    assert delta["balanced_demo_rag_evidence_delta_vs_fast"] == 1


def test_capability_challenge_fixture_has_fast_hard_case_mix():
    data = json.loads(Path("tests/fixtures/capability_challenge_eval_cases.json").read_text(encoding="utf-8"))
    case_ids = {item["case_id"] for item in data}

    assert len(data) >= 40
    assert "cap_gift_girlfriend_open" in case_ids
    assert "cap_pc_build_multiturn_hard" in case_ids
    assert "cap_rag_sunscreen_less_oily" in case_ids
