"""Validate that the fixed evaluation set is explicit enough to be meaningful."""
from __future__ import annotations

import json
from pathlib import Path


FIXTURE = Path(__file__).parent / "fixtures" / "fixed_eval_cases.json"


def test_fixed_fixture_has_unique_ids_and_covers_required_domains():
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    identifiers = [case["case_id"] for case in cases]
    assert len(identifiers) == len(set(identifiers))
    serialized = json.dumps(cases, ensure_ascii=False)
    for action in ("recommend_shopping_products", "parameter_query", "apply_cart_instruction", "general_chat", "generate_pc_build_plan", "edit_pc_build_plan", "compare_pc_build_plans"):
        assert action in serialized
    for reason in ("catalog_scope_unsupported", "cart_target_unresolved", "computer_purchase_kind_unresolved"):
        assert reason in serialized
    required_cases = {
        "attachment_rejected_without_legacy_fallback",
        "card_parameter_fact_multiturn",
        "cart_set_quantity_remove_and_clear",
        "brand_blacklist_release_multiturn",
        "exploration_topic_switch_does_not_inherit",
        "pc_component_replacement",
        "pc_compare_without_two_versions_clarifies",
    }
    assert required_cases <= set(identifiers)


def test_every_chat_turn_declares_an_outcome_and_safe_direct_policy():
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for case in cases:
        turns = case.get("turns") or [{"text": case.get("text"), "expect": case.get("expect")}]
        for turn in turns:
            if turn.get("transport") == "cart_confirm":
                continue
            expectation = dict(case.get("expect") or {})
            expectation.update(turn.get("expect") or {})
            assert turn.get("text")
            assert expectation.get("outcome")
            assert expectation.get("safe_direct") in {"allow", "forbid", "ignore"}
