"""Contract tests for the V3 one-call, action-specific SemanticParse boundary.

These tests intentionally assert intermediate typed objects and topic changes,
not model prose.  They protect the main safety property of this refactor: a
follow-up may inherit only compatible state, while a new tool or product topic
starts a clean execution context.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from rag.recommendation.product_loader import load_combined_product_catalog
from rag.recommendation.v3.orchestrator import _apply_topic_transition, _normalize_ambiguous_computer_observation, V3Orchestrator
from rag.recommendation.v3.catalog_exploration import CatalogExplorationPlanner
from rag.recommendation.v3.promotion import HardConstraintPromotionGate
from rag.recommendation.v3.registry import CatalogNormalizationRegistry
from rag.recommendation.v3.semantic_contracts import (
    CartObservation,
    FactQueryObservation,
    GeneralChatObservation,
    PcBuildObservation,
    RecommendObservation,
    build_brand_candidate_set,
)
from rag.recommendation.v3.semantic_parse import SemanticParser, _decode_observation, _messages
from rag.recommendation.v3.session import apply_session_delta, clarification_delta, empty_session_core, general_chat_delta, recommendation_delta
from rag.recommendation.v3.type_candidates import build_type_candidate_set
from rag.recommendation.v3.type_resolution_gate import TypeResolutionGate
from rag.recommendation.v3.types import (
    CardModel,
    ClarificationPlan,
    CartTargetRef,
    CartTargetSource,
    LLMUsage,
    ParseStatus,
    PriceConstraint,
    PriceKind,
    RecommendationMode,
    RequirementSpecV3,
    SemanticParseResult,
    TypeSurfaceEvidence,
    V3Action,
)


class FakeSemanticParser:
    """A deterministic SemanticParse boundary for orchestrator contract tests."""

    def __init__(self, observation):
        self.observation = observation
        self.calls = 0

    def parse(self, **_kwargs):
        self.calls += 1
        return SemanticParseResult(self.observation, "test", "test", 1, usage=LLMUsage(1, 1, 2))


def _catalog_registry():
    catalog = load_combined_product_catalog()
    return catalog, CatalogNormalizationRegistry.from_catalog(catalog)


def _budget(text: str, amount: float, kind: PriceKind = PriceKind.MAX) -> PriceConstraint:
    evidence = "5000 元以内" if "5000" in text else "3000 元以内"
    start = text.index(evidence)
    return PriceConstraint(kind, amount, None, "CNY", start, start + len(evidence), evidence)


def _phone_observation(text: str, *, budget: PriceConstraint | None = None) -> RecommendObservation:
    start = text.index("手机")
    return RecommendObservation(
        target_type_surface="手机",
        target_type_evidence=TypeSurfaceEvidence("手机", start, start + 2, "手机"),
        budget=budget,
    )


def test_decoder_accepts_only_action_specific_fields():
    observation = _decode_observation({"action": "fact_query", "card_references": [1, 2], "fact_kind": "compare"})
    assert isinstance(observation, FactQueryObservation)
    assert observation.card_references == (1, 2)
    with pytest.raises(ValueError, match="outside its contract"):
        _decode_observation({"action": "fact_query", "card_references": [1], "fact_kind": "price", "quantity": 2})
    with pytest.raises(ValueError, match="mode is required"):
        _decode_observation({"action": "recommend", "target_type_candidate_id": "phone"})


def test_recommendation_promotion_uses_only_registry_brand_candidates():
    text = "推荐华为手机，5000 元以内，不要小米"
    catalog, registry = _catalog_registry()
    candidate_set = build_type_candidate_set(text=text, registry=registry, catalog=catalog)
    phone = next(item for item in candidate_set.candidates if item.display_name == "手机")
    observation = RecommendObservation(
        mode=RecommendationMode.PRODUCT,
        target_type_surface="手机",
        target_type_candidate_id=phone.canonical_type_id,
        target_type_evidence=TypeSurfaceEvidence("手机", 4, 6, "手机"),
        positive_brand_candidate_ids=("brand:huawei",),
        negative_brand_candidate_ids=("brand:xiaomi",),
        budget=_budget(text, 5000),
    )
    type_result = TypeResolutionGate().resolve(text=text, observation=observation, candidate_set=candidate_set, registry=registry)
    promoted = HardConstraintPromotionGate().promote(
        text=text,
        observation=observation,
        registry=registry,
        core=empty_session_core(),
        type_resolution=type_result,
        brand_candidates=build_brand_candidate_set(text=text, registry=registry),
    )
    assert promoted.requirement is not None
    assert promoted.requirement.include_brand_family_ids == ("huawei",)
    assert promoted.requirement.exclude_brand_family_ids == ("xiaomi",)
    assert promoted.requirement.price_max == 5000


def test_brand_release_removes_only_an_existing_negative_constraint():
    text = "小米也可以"
    _catalog, registry = _catalog_registry()
    observation = RecommendObservation(release_brand_candidate_ids=("brand:xiaomi",))
    base = RequirementSpecV3(action=V3Action.RECOMMEND, product_type_ids=("digital/phone",), exclude_brand_family_ids=("xiaomi", "oppo"))
    promoted = HardConstraintPromotionGate().promote(
        text=text,
        observation=observation,
        registry=registry,
        core=empty_session_core(),
        brand_candidates=build_brand_candidate_set(text=text, registry=registry),
        base_requirement=base,
    )
    assert promoted.requirement is not None
    assert promoted.requirement.exclude_brand_family_ids == ("oppo",)
    assert not promoted.requirement.include_brand_family_ids


def test_fact_query_promotes_card_ranks_only_against_live_session_cards():
    core = recommendation_delta(
        RequirementSpecV3(action=V3Action.RECOMMEND, product_type_ids=("digital/phone",)),
        (CardModel("card-1", "p_digital_016", (), "手机", 1, 9999999999),),
    ).core
    promoted = HardConstraintPromotionGate().promote(
        text="第一个多少钱",
        observation=FactQueryObservation(card_references=(1,), fact_kind="price"),
        registry=None,
        core=core,
    )
    assert promoted.requirement is not None
    assert promoted.requirement.target_card_id == "card-1"
    assert promoted.requirement.query_kind == "price"


def test_fact_query_out_of_range_requires_clarification():
    promoted = HardConstraintPromotionGate().promote(
        text="第二个多少钱",
        observation=FactQueryObservation(card_references=(2,), fact_kind="price"),
        registry=None,
        core=empty_session_core(),
    )
    assert promoted.clarification is not None
    assert promoted.reason_code == "card_reference_unresolved"


def test_topic_transition_merges_only_a_compatible_pending_recommendation():
    first = RecommendObservation(target_type_surface="手机", target_type_candidate_id="digital/phone")
    second = RecommendObservation(budget=_budget("预算 3000 元以内", 3000))
    merged = _apply_topic_transition(
        _core_with_pending(first, "推荐手机"), second, "预算 3000 元以内"
    )
    observation, text, base = merged
    assert isinstance(observation, RecommendObservation)
    assert observation.target_type_candidate_id == "digital/phone"
    assert observation.budget is not None and observation.budget.amount == 3000
    assert text == "推荐手机 预算 3000 元以内"
    assert base is None


def test_topic_transition_drops_pending_fields_when_user_names_a_new_type():
    previous = RecommendObservation(
        target_type_surface="电脑",
        budget=_budget("预算 5000 元以内", 5000),
        pc_usage_surfaces=("剪辑",),
    )
    current = RecommendObservation(target_type_surface="手机", target_type_candidate_id="digital/phone")
    observation, text, base = _apply_topic_transition(_core_with_pending(previous, "我要一台剪辑电脑，预算 5000 元以内"), current, "还是推荐手机")
    assert observation is current
    assert text == "还是推荐手机"
    assert base is None


def test_topic_transition_drops_recommendation_when_tool_changes_to_fact_query():
    previous = RecommendObservation(target_type_surface="手机", target_type_candidate_id="digital/phone", budget=_budget("预算 5000 元以内", 5000))
    current = FactQueryObservation(card_references=(1,), fact_kind="price")
    observation, text, base = _apply_topic_transition(_core_with_pending(previous, "推荐手机，预算 5000 元以内"), current, "第一个多少钱")
    assert observation is current
    assert text == "第一个多少钱"
    assert base is None


def test_general_chat_observation_is_a_fieldless_variant():
    prompt = _messages(text="讲个笑话")
    assert "general_chat 除 action 外无字段" in prompt[0]["content"]
    assert _decode_observation({"action": "general_chat"}) == GeneralChatObservation()


def test_orchestrator_uses_one_semantic_parse_for_a_non_grammar_request():
    catalog, _registry = _catalog_registry()
    fake = FakeSemanticParser(RecommendObservation())
    decision = V3Orchestrator(semantic_parser=fake).decide(
        _turn("pad不错，来点推荐"), catalog=catalog, session=SimpleNamespace(v3_core={})
    )
    assert fake.calls == 1
    assert decision.status in {ParseStatus.LOCAL_CLARIFY, ParseStatus.REJECT}


def test_pending_general_chat_is_not_merged_by_the_orchestrator():
    core = _core_with_pending(RecommendObservation(target_type_surface="手机", target_type_candidate_id="digital/phone"), "推荐手机")
    observation, text, base = _apply_topic_transition(core, GeneralChatObservation(), "今天天气怎么样")
    assert isinstance(observation, GeneralChatObservation)
    assert text == "今天天气怎么样"
    assert base is None


def test_general_chat_delta_closes_old_question_but_keeps_card_references():
    core = recommendation_delta(
        RequirementSpecV3(action=V3Action.RECOMMEND, product_type_ids=("digital/phone",)),
        (CardModel("card-1", "p_digital_016", (), "手机", 1, 9999999999),),
    ).core
    pending = _core_with_pending(RecommendObservation(target_type_surface="手机"), "推荐手机")
    with_cards = type(core)(
        schema_version=core.schema_version,
        topic=core.topic,
        active_requirement=core.active_requirement,
        cards=core.cards,
        pending_clarification=pending.pending_clarification,
        cart_lines=core.cart_lines,
        pending_cart_plan=core.pending_cart_plan,
        pc_plans=core.pc_plans,
    )
    updated = general_chat_delta(with_cards).core
    assert updated.pending_clarification is None
    assert updated.cards == core.cards


def test_pc_build_is_the_only_observation_that_can_carry_pc_execution_fields():
    observation = _decode_observation({"action": "pc_build", "budget": None, "usage_surfaces": ["游戏"], "computer_purchase_evidence": None})
    assert isinstance(observation, PcBuildObservation)
    with pytest.raises(ValueError):
        _decode_observation({"action": "recommend", "target_type_surface": "手机", "pc_operation": "replace_component"})


def test_explicit_out_of_catalog_surface_is_rejected_not_turned_into_a_type_question():
    catalog, registry = _catalog_registry()
    observation = RecommendObservation(target_type_surface="汽车")
    result = TypeResolutionGate().resolve(
        text="推荐一辆汽车",
        observation=observation,
        candidate_set=build_type_candidate_set(text="推荐一辆汽车", registry=registry, catalog=catalog),
        registry=registry,
    )
    assert result.reason_code == "catalog_scope_unsupported"


def test_unproven_pc_build_becomes_a_purchase_form_clarification_context():
    raw = PcBuildObservation(budget=_budget("预算 3000 元以内", 3000), usage_surfaces=("剪辑",))
    normalized = _normalize_ambiguous_computer_observation("我要一台剪辑视频用的电脑，预算 3000 元以内", raw)
    assert isinstance(normalized, RecommendObservation)
    assert normalized.computer_purchase_kind.value == "unknown"
    assert normalized.pc_usage_surfaces == ("剪辑",)


def test_cart_observation_cannot_carry_fact_query_kind():
    observation = _decode_observation({"action": "cart", "operation": "add", "target_ref": {"source": "card", "rank": 1}, "quantity": 2})
    assert isinstance(observation, CartObservation)
    assert observation.target_ref == CartTargetRef(CartTargetSource.CARD, 1)
    with pytest.raises(ValueError):
        _decode_observation({"action": "cart", "operation": "add", "target_ref": {"source": "card", "rank": 1}, "fact_kind": "price"})


def test_explore_mode_cannot_carry_a_concrete_type_and_exclusion_needs_no_evidence():
    catalog, registry = _catalog_registry()
    candidates = build_type_candidate_set(text="不要手机和耳机，推荐平板", registry=registry, catalog=catalog)
    phone = next(item.canonical_type_id for item in candidates.candidates if item.display_name == "手机")
    earbuds = next(item.canonical_type_id for item in candidates.candidates if item.display_name == "耳机")
    tablet = next(item.canonical_type_id for item in candidates.candidates if item.display_name == "平板")
    result = TypeResolutionGate().resolve(
        text="不要手机和耳机，推荐平板",
        observation=RecommendObservation(mode=RecommendationMode.PRODUCT, target_type_candidate_id=tablet, exclude_type_candidate_ids=(phone, earbuds)),
        candidate_set=candidates,
        registry=registry,
    )
    assert result.exclude_product_type_ids == tuple(sorted((phone, earbuds)))
    invalid = TypeResolutionGate().resolve(
        text="随便看看",
        observation=RecommendObservation(mode=RecommendationMode.EXPLORE, target_type_candidate_id=tablet),
        candidate_set=candidates,
        registry=registry,
    )
    assert invalid.clarification is not None
    assert invalid.reason_code == "explore_target_conflict"


def test_semantic_parser_retries_once_after_schema_error_and_records_both_attempts():
    class FakeClient:
        configured = True
        config = SimpleNamespace(provider="test", fast_model="test")

        def __init__(self):
            self.calls = 0

        def chat_json_with_report(self, *_args, **_kwargs):
            self.calls += 1
            payload = {"action": "recommend", "mode": "product", "target_type_candidate_id": "phone", "unexpected": True} if self.calls == 1 else {"action": "recommend", "mode": "product", "target_type_candidate_id": "phone"}
            return payload, SimpleNamespace(elapsed_ms=1, usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5})

    client = FakeClient()
    result = SemanticParser(client=client).parse(text="推荐手机", registry=None)
    assert result.observation is not None
    assert client.calls == 2
    assert [item.outcome for item in result.attempts] == ["schema_invalid", "accepted"]
    assert result.attempts[0].reason_code == "schema_extra_field"
    assert result.usage.total_tokens == 10


def test_catalog_exploration_uses_real_diverse_non_pc_catalog_directions():
    catalog, _registry = _catalog_registry()
    directions = CatalogExplorationPlanner().plan(
        message="不知道买什么，随便看看",
        requirement=RequirementSpecV3(action=V3Action.RECOMMEND, recommendation_mode=RecommendationMode.EXPLORE),
        catalog=catalog,
    )
    assert directions
    assert len(directions) <= 3
    assert len({item.parent_category for item in directions}) == len(directions)
    assert all(not item.requirement.product_type_ids[0].startswith("pc_category:") for item in directions)


def _core_with_pending(observation, source_text: str):
    core = empty_session_core()
    plan = ClarificationPlan("请补充信息", ("required",), 9999999999, "test_pending")
    session = SimpleNamespace(v3_core={})
    apply_session_delta(session, clarification_delta(core, plan=plan, observation=observation, source_text=source_text))
    from rag.recommendation.v3.session import load_session_core

    return load_session_core(session, now=1.0)


def _turn(text: str):
    from rag.recommendation.v3.normalization import normalize_turn

    return normalize_turn(session_id="semantic-contract", message=text)
