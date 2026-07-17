"""Static contract checks for the real external full-chain fixture."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.eval_v3_full_chain import _evaluate_turn


FIXTURE = Path(__file__).parent / "fixtures" / "full_chain_eval_cases.json"


def test_full_chain_fixture_has_unique_ids_and_complete_multiturn_expectations():
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    case_ids = [case["case_id"] for case in cases]

    assert len(case_ids) == len(set(case_ids))
    for case in cases:
        turns = case.get("turns") or [{"query": case["query"]}]
        expected_turns = case.get("expected_turns")
        if expected_turns is not None:
            assert len(expected_turns) == len(turns), case["case_id"]
        for turn in turns:
            assert turn["query"].strip(), case["case_id"]


def test_pc_and_catalog_scope_cases_express_current_v3_intent():
    cases = {case["case_id"]: case for case in json.loads(FIXTURE.read_text(encoding="utf-8"))}

    assert cases["pc_purchase_video_edit_9000_ambiguous"]["expected_reason"] == "computer_purchase_kind_unresolved"
    assert cases["pc_purchase_video_edit_9000_choose_desktop"]["expected_turns"][1]["expected_tool"] == "generate_pc_build_plan"
    assert cases["pc_purchase_video_edit_9000_choose_laptop"]["expected_turns"][1]["expected_tool"] == "recommend_shopping_products"
    assert [turn["query"] for turn in cases["pc_build_multiturn_adjust"]["turns"]] == [
        "7000 元配一台游戏主机",
        "预算降到 6000",
        "上一套和现在这套差别在哪里",
    ]
    assert cases["negative_missing_outdoor_jacket"]["expected_reason"] == "catalog_scope_unsupported"


def test_turn_evaluator_checks_each_multiturn_contract_without_external_services():
    clarification_turn = {
        "tool": None,
        "product_ids": [],
        "categories": [],
        "retrieval": {},
        "candidate_gate": {},
        "error": {},
        "clarification": {"reason": "computer_purchase_kind_unresolved"},
        "route": {"semantic_provider": "test"},
    }
    recommendation_turn = {
        "tool": "recommend_shopping_products",
        "product_ids": ["p_laptop"],
        "categories": ["digital"],
        "retrieval": {"status": "ok", "filter_expression": "product_id in [\"p_laptop\"]"},
        "candidate_gate": {"allowed_product_ids": ["p_laptop"]},
        "error": {},
        "clarification": {},
        "route": {"semantic_provider": "test"},
    }

    clarification = _evaluate_turn(
        clarification_turn,
        {"expected_outcome": "clarification", "expected_reason": "computer_purchase_kind_unresolved"},
    )
    recommendation = _evaluate_turn(
        recommendation_turn,
        {"expected_tool": "recommend_shopping_products", "expected_category": "digital"},
    )

    assert clarification["outcome"] and clarification["external_chat"]
    assert recommendation["outcome"] and recommendation["embedding_milvus"]
