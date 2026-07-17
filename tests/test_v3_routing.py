"""Golden tests for the first executable V3 deterministic routing slice."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from rag.recommendation.product_loader import load_product_catalog
from rag.recommendation.v3.candidate_gate import CatalogCandidateGate
from rag.recommendation.v3.config import GRAMMAR_VERSION
from rag.recommendation.v3.normalization import normalize_turn
from rag.recommendation.v3.router import V3Router
from rag.recommendation.v3.types import ParseStatus


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "v3_routing_golden.json"


@pytest.fixture(scope="module")
def catalog():
    return load_product_catalog()


@pytest.mark.parametrize("case", json.loads(FIXTURE_PATH.read_text(encoding="utf-8")), ids=lambda case: case["id"])
def test_v3_routing_golden_cases(catalog, case):
    decision = V3Router().route(
        normalize_turn(session_id="v3-routing-test", message=case["text"]),
        catalog=catalog,
    )
    assert decision.status.value == case["expected_status"]

    if decision.status is ParseStatus.SAFE_DIRECT:
        assert decision.requirement is not None
        assert decision.rule_signal.safety_proof is not None
        proof = decision.rule_signal.safety_proof
        assert proof.grammar_id == case["grammar_id"]
        assert proof.grammar_version == GRAMMAR_VERSION
        assert proof.lexical_coverage_complete
        assert proof.operator_scopes_resolved
        assert proof.semantic_group_count == 1
        assert proof.semantic_unique
        assert proof.action_schema_complete
        assert proof.semantic_signature.startswith("sha256:")
        assert decision.requirement.product_type_ids == tuple(case["product_type_ids"])
        assert decision.requirement.price_max == case.get("price_max")
        assert decision.requirement.exclude_brand_family_ids == tuple(case.get("exclude_brand_family_ids", []))
        assert decision.requirement.desired_attributes == tuple(case.get("desired_attributes", []))
    else:
        assert decision.rule_signal.reason_code == case["reason"]


def test_semantic_risk_marker_cannot_become_brand_exclusion(catalog):
    decision = V3Router().route(
        normalize_turn(session_id="v3-routing-test", message="不要只推荐小米"),
        catalog=catalog,
    )
    assert decision.status is ParseStatus.NEEDS_SEMANTIC_LLM
    assert decision.requirement is None
    assert not decision.rule_signal.parse_trees


def test_aliases_are_canonicalized_in_the_registry(catalog):
    router = V3Router()
    decision = router.route(
        normalize_turn(session_id="v3-routing-test", message="推荐手机，不要Xiaomi"),
        catalog=catalog,
    )
    assert decision.status is ParseStatus.SAFE_DIRECT
    assert decision.requirement is not None
    assert decision.requirement.exclude_brand_family_ids == ("xiaomi",)


def test_quantifier_and_fixed_polite_suffix_are_certified_but_open_text_is_not(catalog):
    accepted = V3Router().route(
        normalize_turn(session_id="v3-routing-test", message="推荐一款手机，谢谢"),
        catalog=catalog,
    )
    rejected = V3Router().route(
        normalize_turn(session_id="v3-routing-test", message="推荐一款适合办公室的手机"),
        catalog=catalog,
    )

    assert accepted.status is ParseStatus.SAFE_DIRECT
    assert accepted.rule_signal.safety_proof is not None
    assert accepted.rule_signal.safety_proof.grammar_version == "1.1"
    assert rejected.status is ParseStatus.NEEDS_SEMANTIC_LLM


def test_ranked_card_fact_query_requires_a_live_unique_card(catalog):
    session = SimpleNamespace(
        v3_core={
            "schema_version": 1,
            "topic": None,
            "active_requirement": None,
            "cards": [{"card_id": "card_1", "product_id": "p_digital_001", "sku_ids": [], "title": "iPhone", "rank": 1, "expires_at": time.time() + 60}],
        }
    )
    decision = V3Router().route(
        normalize_turn(session_id="v3-routing-test", message="第一个的参数"),
        catalog=catalog,
        session=session,
    )
    assert decision.status is ParseStatus.SAFE_DIRECT
    assert decision.requirement is not None
    assert decision.requirement.target_card_id == "card_1"
    assert decision.requirement.query_kind == "specifications"
    assert decision.rule_signal.safety_proof is not None
    assert decision.rule_signal.safety_proof.grammar_id == "card.parameter_query.v1"


def test_card_attribute_question_and_expired_card_fail_closed(catalog):
    expired = SimpleNamespace(
        v3_core={
            "schema_version": 1,
            "topic": None,
            "active_requirement": None,
            "cards": [{"card_id": "old", "product_id": "p_digital_001", "sku_ids": [], "title": "iPhone", "rank": 1, "expires_at": time.time() - 1}],
        }
    )
    router = V3Router()
    for text, session in (("第一个的屏幕", expired), ("第一个的参数", expired)):
        decision = router.route(normalize_turn(session_id="v3-routing-test", message=text), catalog=catalog, session=session)
        assert decision.status is ParseStatus.NEEDS_SEMANTIC_LLM
        assert decision.requirement is None


def test_candidate_gate_filters_before_ranking_and_excludes_xiaomi(catalog):
    decision = V3Router().route(
        normalize_turn(session_id="v3-routing-test", message="推荐手机，10000元以内，不要小米"),
        catalog=catalog,
    )
    assert decision.status is ParseStatus.SAFE_DIRECT
    assert decision.requirement is not None
    result = CatalogCandidateGate().evaluate(decision.requirement, catalog=catalog)
    assert result.filters.product_ids
    assert not ({"p_digital_008", "p_digital_009", "p_digital_010"} & set(result.filters.product_ids))
    assert {"p_digital_008", "p_digital_009", "p_digital_010"}.issubset(result.rejected_by_reason["excluded_brand"])


@pytest.mark.parametrize("text, expected_include", [("要小米", ("xiaomi",)), ("小米也可以", ())])
def test_explicit_brand_release_replaces_prior_exclusion(catalog, text, expected_include):
    session = SimpleNamespace(
        v3_core={
            "schema_version": 1,
            "topic": None,
            "active_requirement": {
                "action": "recommend_shopping_products",
                "product_type_ids": ["phone"],
                "exclude_product_type_ids": [],
                "include_brand_family_ids": [],
                "exclude_brand_family_ids": ["xiaomi"],
                "price_max": 10000,
                "desired_attributes": [],
                "target_card_id": None,
                "query_kind": None,
                "field_provenance": {},
            },
            "cards": [],
        }
    )
    decision = V3Router().route(normalize_turn(session_id="v3-routing-test", message=text), catalog=catalog, session=session)
    assert decision.status is ParseStatus.SAFE_DIRECT
    assert decision.requirement is not None
    assert decision.requirement.include_brand_family_ids == expected_include
    assert decision.requirement.exclude_brand_family_ids == ()
    gate = CatalogCandidateGate().evaluate(decision.requirement, catalog=catalog)
    if expected_include:
        assert gate.filters.product_ids == ("p_digital_008", "p_digital_009", "p_digital_010")
    else:
        assert {"p_digital_008", "p_digital_009", "p_digital_010"}.issubset(gate.filters.product_ids)
