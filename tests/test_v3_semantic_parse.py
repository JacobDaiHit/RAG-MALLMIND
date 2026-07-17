"""V3 semantic parse and promotion invariants without external model calls."""
from __future__ import annotations

from types import SimpleNamespace

from rag.recommendation.product_loader import load_combined_product_catalog, load_product_catalog
from rag.recommendation.v3.candidate_gate import CatalogCandidateGate
from rag.recommendation.v3.pc_catalog import canonical_product_key
from rag.recommendation.v3.orchestrator import V3Orchestrator
from rag.recommendation.v3.session import apply_session_delta, clarification_delta, load_session_core
from rag.recommendation.v3.promotion import HardConstraintPromotionGate
from rag.recommendation.v3.registry import CatalogNormalizationRegistry
from rag.recommendation.v3.semantic_parse import SemanticParser, _messages
from rag.recommendation.v3.session import empty_session_core
from rag.recommendation.v3.type_candidates import build_type_candidate_set
from rag.recommendation.v3.type_resolution_gate import TypeResolutionGate
from rag.recommendation.v3.types import ClarificationPlan, CommerceIntent, ComputerPurchaseKind, NormalizedTurn, ParseStatus, PriceConstraint, PriceKind, PurchaseKindEvidence, RequirementSpecV3, SemanticObservation, SemanticParseResult, TypeResolutionResult, TypeSurfaceEvidence, V3Action
from rag.recommendation.v3.retrieval import V3EvidenceRetriever


class FakeSemanticParser:
    def __init__(self, observation):
        self.observation = observation

    def parse(self, *, text, registry, catalog=None, candidate_set=None):
        return SemanticParseResult(self.observation, "test", "test-model", 1)


def _max_price(amount, evidence_start, evidence_end, evidence_text):
    return PriceConstraint(PriceKind.MAX, amount, None, "CNY", evidence_start, evidence_end, evidence_text)


def _resolved(*product_type_ids, excluded=()):
    return TypeResolutionResult(product_type_ids=tuple(product_type_ids), exclude_product_type_ids=tuple(excluded), reason_code="test")


def _type_evidence(text, surface):
    start = text.index(surface)
    return TypeSurfaceEvidence(surface, start, start + len(surface), surface)


def _purchase_evidence(text, surface):
    start = text.index(surface)
    return PurchaseKindEvidence(surface, start, start + len(surface), surface)


def test_semantic_observation_promotes_xiaomi_exclusion_and_catalog_phone():
    catalog = load_product_catalog()
    registry = CatalogNormalizationRegistry.from_catalog(catalog)
    observation = SemanticObservation(
        action=V3Action.RECOMMEND,
        target_type_surface="手机",
        exclude_brand_surfaces=("小米",),
        price_constraint=_max_price(5000, 5, 13, "5000 元以内"),
    )

    promoted = HardConstraintPromotionGate().promote(
        text="推荐一款 5000 元以内的小米以外的手机",
        observation=observation,
        registry=registry,
        core=empty_session_core(),
        type_resolution=_resolved("phone"),
    )

    assert promoted.requirement is not None
    assert promoted.requirement.product_type_ids == ("phone",)
    assert promoted.requirement.exclude_brand_family_ids == ("xiaomi",)
    assert promoted.requirement.price_max == 5000
    allowed = CatalogCandidateGate().evaluate(promoted.requirement, catalog=catalog).filters.product_ids
    assert "p_digital_016" in allowed
    assert not {"p_digital_008", "p_digital_009", "p_digital_010"} & set(allowed)


def test_semantic_weak_brand_preference_is_not_promoted_to_hard_include():
    catalog = load_product_catalog()
    registry = CatalogNormalizationRegistry.from_catalog(catalog)
    observation = SemanticObservation(
        action=V3Action.RECOMMEND,
        target_type_surface="手机",
        include_brand_surfaces=("华为",),
        exclude_brand_surfaces=("小米",),
    )

    promoted = HardConstraintPromotionGate().promote(
        text="上次同事推荐小米，但我用着并不好。或许华为适合我，给我一点推荐。",
        observation=observation,
        registry=registry,
        core=empty_session_core(),
        type_resolution=_resolved("phone"),
    )

    assert promoted.requirement is not None
    assert promoted.requirement.exclude_brand_family_ids == ("xiaomi",)
    assert promoted.requirement.include_brand_family_ids == ()


def test_semantic_coffee_type_does_not_expand_to_digital_category():
    catalog = load_product_catalog()
    registry = CatalogNormalizationRegistry.from_catalog(catalog)
    observation = SemanticObservation(action=V3Action.RECOMMEND, target_type_surface="咖啡")

    promoted = HardConstraintPromotionGate().promote(
        text="推荐一款适合办公室喝的挂耳咖啡",
        observation=observation,
        registry=registry,
        core=empty_session_core(),
        type_resolution=_resolved("sub_category:咖啡"),
    )

    assert promoted.requirement is not None
    gate = CatalogCandidateGate().evaluate(promoted.requirement, catalog=catalog)
    assert gate.filters.sub_categories == ("咖啡",)
    assert gate.filters.product_ids
    assert all(catalog.get(product_id).category.value == "food" for product_id in gate.filters.product_ids)


def test_registry_has_no_hand_maintained_semantic_product_type_alias():
    registry = CatalogNormalizationRegistry.from_catalog(load_combined_product_catalog())

    assert registry.product_type_by_surface("咖啡").canonical_id == "sub_category:咖啡"
    assert registry.product_type_by_surface("主板").canonical_id == "pc_category:pc_motherboard"
    assert registry.product_type_by_surface("篮球实战鞋") is None


def test_pc_candidate_gate_keeps_one_product_per_canonical_key():
    catalog = load_combined_product_catalog()
    requirement = RequirementSpecV3(action=V3Action.RECOMMEND, product_type_ids=("pc_category:pc_psu",))
    allowed = CatalogCandidateGate().evaluate(requirement, catalog=catalog).filters.product_ids
    keys = [canonical_product_key(catalog.get(product_id)) for product_id in allowed]

    assert len(keys) == len(set(keys))


def test_semantic_prompt_uses_compact_catalog_capability_map():
    catalog = load_combined_product_catalog()
    messages = _messages(text="推荐一辆汽车", registry=CatalogNormalizationRegistry.from_catalog(catalog), catalog=catalog)

    assert "目录外商品仍按推荐意图输出" in messages[1]["content"]
    assert "可用品牌表面词" not in messages[1]["content"]
    assert len(messages[1]["content"]) < 2000


def test_type_resolution_rejects_unknown_explicit_product_surface():
    catalog = load_product_catalog()
    registry = CatalogNormalizationRegistry.from_catalog(catalog)
    text = "推荐一辆汽车"
    resolved = TypeResolutionGate().resolve(
        text=text,
        observation=SemanticObservation(
            action=V3Action.RECOMMEND,
            target_type_surface="汽车",
            target_type_evidence=_type_evidence(text, "汽车"),
        ),
        candidate_set=build_type_candidate_set(text=text, registry=registry, catalog=catalog),
        registry=registry,
    )
    assert resolved.product_type_ids == ()
    assert resolved.clarification is None
    assert resolved.reason_code == "catalog_scope_unsupported"


def test_general_chat_cannot_bypass_explicit_out_of_catalog_recommendation():
    catalog = load_product_catalog()
    decision = V3Orchestrator(semantic_parser=FakeSemanticParser(SemanticObservation(action=V3Action.GENERAL_CHAT))).decide(
        NormalizedTurn("scope-guard", "scope-guard", "推荐一辆汽车"),
        catalog=catalog,
        session=SimpleNamespace(v3_core={}),
    )

    assert decision.status is ParseStatus.REJECT
    assert decision.reason_code == "catalog_scope_unsupported"


def test_semantic_commerce_recommendation_without_type_cannot_fall_into_general_chat():
    catalog = load_product_catalog()
    decision = V3Orchestrator(
        semantic_parser=FakeSemanticParser(
            SemanticObservation(action=V3Action.GENERAL_CHAT, commerce_intent=CommerceIntent.RECOMMEND)
        )
    ).decide(
        NormalizedTurn("gift-clarify", "gift-clarify", "送女朋友礼物"),
        catalog=catalog,
        session=SimpleNamespace(v3_core={}),
    )

    assert decision.status is ParseStatus.LOCAL_CLARIFY
    assert decision.reason_code == "product_type_unresolved"
    assert decision.clarification is not None


def test_semantic_comparison_without_two_cards_cannot_fall_into_general_chat():
    catalog = load_product_catalog()
    decision = V3Orchestrator(
        semantic_parser=FakeSemanticParser(
            SemanticObservation(action=V3Action.GENERAL_CHAT, commerce_intent=CommerceIntent.COMPARE)
        )
    ).decide(
        NormalizedTurn("compare-clarify", "compare-clarify", "帮我比较这两个商品"),
        catalog=catalog,
        session=SimpleNamespace(v3_core={}),
    )

    assert decision.status is ParseStatus.LOCAL_CLARIFY
    assert decision.reason_code == "comparison_card_references_unresolved"
    assert decision.clarification is not None


def test_semantic_budget_evidence_promotes_without_local_price_phrase_grammar():
    catalog = load_product_catalog()
    registry = CatalogNormalizationRegistry.from_catalog(catalog)
    text = "推荐一双篮球实战鞋，缓震好，预算 1000"
    start = text.index("预算 1000")
    observation = SemanticObservation(
        action=V3Action.RECOMMEND,
        target_type_surface="篮球鞋",
        price_constraint=_max_price(1000, start, start + len("预算 1000"), "预算 1000"),
    )

    promoted = HardConstraintPromotionGate().promote(
        text=text,
        observation=observation,
        registry=registry,
        core=empty_session_core(),
        type_resolution=_resolved("sub_category:篮球鞋"),
    )

    assert promoted.requirement is not None
    assert promoted.requirement.price_max == 1000


def test_semantic_price_evidence_allows_only_unique_whitespace_normalization():
    catalog = load_product_catalog()
    observation = SemanticObservation(
        action=V3Action.RECOMMEND,
        target_type_surface="手机",
        price_constraint=_max_price(5000, 0, 8, "5000元以内"),
    )
    promoted = HardConstraintPromotionGate().promote(
        text="推荐 5000 元以内的手机",
        observation=observation,
        registry=CatalogNormalizationRegistry.from_catalog(catalog),
        core=empty_session_core(),
        type_resolution=_resolved("phone"),
    )

    assert promoted.requirement is not None
    assert promoted.requirement.price_max == 5000


def test_semantic_price_ambiguity_alone_triggers_price_clarification():
    catalog = load_product_catalog()
    registry = CatalogNormalizationRegistry.from_catalog(catalog)
    promoted = HardConstraintPromotionGate().promote(
        text="推荐一款手机，3000 还是 5000 我没想好",
        observation=SemanticObservation(
            action=V3Action.RECOMMEND,
            target_type_surface="手机",
            missing_fields=("price",),
        ),
        registry=registry,
        core=empty_session_core(),
        type_resolution=_resolved("phone"),
    )

    assert promoted.requirement is None
    assert promoted.clarification is not None
    assert promoted.clarification.reason_code == "semantic_price_ambiguous"


def test_pc_build_accepts_an_explicit_target_budget():
    text = "7000 元左右配一台游戏主机，主要玩 3A"
    price_text = "7000 元左右"
    start = text.index(price_text)
    promoted = HardConstraintPromotionGate().promote(
        text=text,
        observation=SemanticObservation(
            action=V3Action.PC_BUILD,
            price_constraint=PriceConstraint(PriceKind.TARGET, 7000, None, "CNY", start, start + len(price_text), price_text),
            pc_usage_surfaces=("游戏",),
        ),
        registry=CatalogNormalizationRegistry.from_catalog(load_combined_product_catalog()),
        core=empty_session_core(),
    )

    assert promoted.requirement is not None
    assert promoted.requirement.price_max is None
    assert promoted.requirement.price_target == 7000


def test_semantic_price_evidence_not_present_in_user_text_requires_clarification():
    catalog = load_product_catalog()
    registry = CatalogNormalizationRegistry.from_catalog(catalog)
    promoted = HardConstraintPromotionGate().promote(
        text="推荐一款手机",
        observation=SemanticObservation(action=V3Action.RECOMMEND, target_type_surface="手机", price_constraint=_max_price(3000, 0, 4, "3000元")),
        registry=registry,
        core=empty_session_core(),
        type_resolution=_resolved("phone"),
    )
    assert promoted.requirement is None
    assert promoted.clarification is not None
    assert promoted.clarification.reason_code == "semantic_price_evidence_unverifiable"


def test_orchestrator_uses_one_semantic_parse_when_grammar_is_not_safe_direct():
    catalog = load_product_catalog()
    text = "推荐一款适合办公室喝的挂耳咖啡"
    parser = FakeSemanticParser(
        SemanticObservation(
            action=V3Action.RECOMMEND,
            target_type_surface="挂耳咖啡",
            target_type_candidate_id="sub_category:咖啡",
            target_type_evidence=_type_evidence(text, "挂耳咖啡"),
        )
    )
    decision = V3Orchestrator(semantic_parser=parser).decide(
        SimpleNamespace(text=text),
        catalog=catalog,
        session=SimpleNamespace(v3_core={}),
    )

    assert decision.status is ParseStatus.SEMANTIC_EXECUTABLE
    assert decision.requirement is not None
    assert decision.requirement.product_type_ids[0].startswith("sub_category:")
    assert decision.semantic is not None


def test_pending_category_clarification_merges_the_next_turn_without_losing_price():
    """“推荐 3000 以内的” -> “平板” must become one executable request.

    The second short answer has no price on its own.  Its only safe meaning is
    defined by the unexpired typed clarification saved by the first turn.
    """
    first_observation = SemanticObservation(
        action=V3Action.RECOMMEND,
        price_constraint=_max_price(3000, 3, 11, "3000 元以内"),
        missing_fields=("product_type",),
    )
    first = V3Orchestrator(semantic_parser=FakeSemanticParser(first_observation))
    session = SimpleNamespace(v3_core={})
    first_decision = first.decide(
        NormalizedTurn("r-clarify-1", "s-clarify", "推荐 3000 元以内的"),
        catalog=load_combined_product_catalog(),
        session=session,
    )
    assert first_decision.status is ParseStatus.LOCAL_CLARIFY
    assert first_decision.clarification is not None
    apply_session_delta(
        session,
        clarification_delta(
            load_session_core(session),
            plan=first_decision.clarification,
            observation=first_decision.semantic.observation,
            source_text="推荐 3000 元以内的",
        ),
    )

    second_text = "平板"
    second_observation = SemanticObservation(
        action=V3Action.RECOMMEND,
        target_type_surface="平板",
        target_type_candidate_id="tablet",
        target_type_evidence=_type_evidence(second_text, "平板"),
    )
    second = V3Orchestrator(semantic_parser=FakeSemanticParser(second_observation))
    decision = second.decide(
        NormalizedTurn("r-clarify-2", "s-clarify", second_text),
        catalog=load_combined_product_catalog(),
        session=session,
    )

    assert decision.status is ParseStatus.SEMANTIC_EXECUTABLE
    assert decision.requirement is not None
    assert decision.requirement.product_type_ids == ("tablet",)
    assert decision.requirement.price_max == 3000


def test_ambiguous_computer_purchase_clarifies_before_retrieval_or_pc_execution():
    text = "我要一台剪辑视频用的电脑，预算 9000"
    price_text = "预算 9000"
    parser = FakeSemanticParser(
        SemanticObservation(
            action=V3Action.RECOMMEND,
            commerce_intent=CommerceIntent.RECOMMEND,
            target_type_surface="电脑",
            computer_purchase_kind=ComputerPurchaseKind.UNKNOWN,
            computer_purchase_evidence=_purchase_evidence(text, "电脑"),
            price_constraint=_max_price(9000, text.index(price_text), text.index(price_text) + len(price_text), price_text),
            pc_usage_surfaces=("剪辑视频",),
            missing_fields=("computer_purchase_kind",),
        )
    )
    decision = V3Orchestrator(semantic_parser=parser).decide(
        NormalizedTurn("pc-ambiguous", "pc-ambiguous", text),
        catalog=load_combined_product_catalog(),
        session=SimpleNamespace(v3_core={}),
    )

    assert decision.status is ParseStatus.LOCAL_CLARIFY
    assert decision.action is V3Action.RECOMMEND
    assert decision.reason_code == "computer_purchase_kind_unresolved"
    assert decision.requirement is None
    assert decision.clarification is not None
    assert "笔记本" in decision.clarification.question
    assert "台式主机" in decision.clarification.question


def test_unproven_computer_unknown_cannot_hijack_an_unrelated_gift_request():
    text = "送女朋友礼物"
    decision = V3Orchestrator(
        semantic_parser=FakeSemanticParser(
            SemanticObservation(
                action=V3Action.RECOMMEND,
                commerce_intent=CommerceIntent.RECOMMEND,
                computer_purchase_kind=ComputerPurchaseKind.UNKNOWN,
            )
        )
    ).decide(NormalizedTurn("gift-unknown", "gift-unknown", text), catalog=load_combined_product_catalog(), session=SimpleNamespace(v3_core={}))

    assert decision.status is ParseStatus.LOCAL_CLARIFY
    assert decision.reason_code == "product_type_unresolved"
    assert decision.semantic is not None
    assert decision.semantic.observation.computer_purchase_kind is None


def test_computer_purchase_clarification_merges_short_desktop_reply_into_pc_build():
    first_text = "我要一台剪辑视频用的电脑，预算 9000"
    price_text = "预算 9000"
    session = SimpleNamespace(v3_core={})
    first = V3Orchestrator(
        semantic_parser=FakeSemanticParser(
            SemanticObservation(
                action=V3Action.RECOMMEND,
                commerce_intent=CommerceIntent.RECOMMEND,
                target_type_surface="电脑",
                computer_purchase_kind=ComputerPurchaseKind.UNKNOWN,
                computer_purchase_evidence=_purchase_evidence(first_text, "电脑"),
                price_constraint=_max_price(9000, first_text.index(price_text), first_text.index(price_text) + len(price_text), price_text),
                pc_usage_surfaces=("剪辑视频",),
                missing_fields=("computer_purchase_kind",),
            )
        )
    )
    first_decision = first.decide(NormalizedTurn("pc-first", "pc-session", first_text), catalog=load_combined_product_catalog(), session=session)
    assert first_decision.clarification is not None
    apply_session_delta(
        session,
        clarification_delta(load_session_core(session), plan=first_decision.clarification, observation=first_decision.semantic.observation, source_text=first_text),
    )

    reply = "配台主机"
    second = V3Orchestrator(
        semantic_parser=FakeSemanticParser(
            SemanticObservation(
                action=V3Action.PC_BUILD,
                commerce_intent=CommerceIntent.PC_PLAN,
                computer_purchase_kind=ComputerPurchaseKind.DESKTOP_BUILD,
                computer_purchase_evidence=_purchase_evidence(reply, "配台主机"),
            )
        )
    )
    decision = second.decide(NormalizedTurn("pc-second", "pc-session", reply), catalog=load_combined_product_catalog(), session=session)

    assert decision.status is ParseStatus.SEMANTIC_EXECUTABLE
    assert decision.action is V3Action.PC_BUILD
    assert decision.requirement is not None
    assert decision.requirement.price_max == 9000
    assert decision.semantic.observation.pc_usage_surfaces == ("剪辑视频",)


def test_computer_purchase_clarification_merges_short_laptop_reply_into_catalog_recommendation():
    first_text = "我要一台剪辑视频用的电脑，预算 9000"
    price_text = "预算 9000"
    session = SimpleNamespace(v3_core={})
    first_observation = SemanticObservation(
        action=V3Action.RECOMMEND,
        commerce_intent=CommerceIntent.RECOMMEND,
        target_type_surface="电脑",
        computer_purchase_kind=ComputerPurchaseKind.UNKNOWN,
        computer_purchase_evidence=_purchase_evidence(first_text, "电脑"),
        price_constraint=_max_price(9000, first_text.index(price_text), first_text.index(price_text) + len(price_text), price_text),
        pc_usage_surfaces=("剪辑视频",),
        missing_fields=("computer_purchase_kind",),
    )
    first_decision = V3Orchestrator(semantic_parser=FakeSemanticParser(first_observation)).decide(
        NormalizedTurn("laptop-first", "laptop-session", first_text), catalog=load_combined_product_catalog(), session=session
    )
    assert first_decision.clarification is not None
    apply_session_delta(
        session,
        clarification_delta(load_session_core(session), plan=first_decision.clarification, observation=first_observation, source_text=first_text),
    )

    reply = "笔记本"
    second_observation = SemanticObservation(
        action=V3Action.RECOMMEND,
        commerce_intent=CommerceIntent.RECOMMEND,
        target_type_surface="笔记本",
        target_type_candidate_id="sub_category:笔记本电脑",
        target_type_evidence=_type_evidence(reply, "笔记本"),
        computer_purchase_kind=ComputerPurchaseKind.LAPTOP,
        computer_purchase_evidence=_purchase_evidence(reply, "笔记本"),
    )
    decision = V3Orchestrator(semantic_parser=FakeSemanticParser(second_observation)).decide(
        NormalizedTurn("laptop-second", "laptop-session", reply), catalog=load_combined_product_catalog(), session=session
    )

    assert decision.status is ParseStatus.SEMANTIC_EXECUTABLE
    assert decision.action is V3Action.RECOMMEND
    assert decision.requirement is not None
    assert decision.requirement.product_type_ids == ("sub_category:笔记本电脑",)
    assert decision.requirement.price_max == 9000


def test_computer_purchase_action_mismatch_clarifies_instead_of_rewriting_action():
    text = "配台主机"
    decision = V3Orchestrator(
        semantic_parser=FakeSemanticParser(
            SemanticObservation(
                action=V3Action.RECOMMEND,
                commerce_intent=CommerceIntent.PC_PLAN,
                computer_purchase_kind=ComputerPurchaseKind.DESKTOP_BUILD,
                computer_purchase_evidence=_purchase_evidence(text, "配台主机"),
            )
        )
    ).decide(NormalizedTurn("pc-mismatch", "pc-mismatch", text), catalog=load_combined_product_catalog(), session=SimpleNamespace(v3_core={}))

    assert decision.status is ParseStatus.LOCAL_CLARIFY
    assert decision.reason_code == "computer_purchase_action_mismatch"
    assert decision.action is V3Action.RECOMMEND


def test_desktop_build_requires_an_explicit_build_phrase_not_only_game_pc_words():
    text = "我要一台带 RTX 4070 的游戏主机，预算 8000"
    decision = V3Orchestrator(
        semantic_parser=FakeSemanticParser(
            SemanticObservation(
                action=V3Action.PC_BUILD,
                commerce_intent=CommerceIntent.PC_PLAN,
                computer_purchase_kind=ComputerPurchaseKind.DESKTOP_BUILD,
                computer_purchase_evidence=_purchase_evidence(text, "游戏主机"),
            )
        )
    ).decide(NormalizedTurn("pc-implicit", "pc-implicit", text), catalog=load_combined_product_catalog(), session=SimpleNamespace(v3_core={}))

    assert decision.status is ParseStatus.LOCAL_CLARIFY
    assert decision.reason_code == "computer_purchase_kind_unresolved"


def test_computer_purchase_clarification_does_not_leak_into_a_new_topic():
    first_text = "我要一台剪辑视频用的电脑，预算 9000"
    price_text = "预算 9000"
    session = SimpleNamespace(v3_core={})
    first_observation = SemanticObservation(
        action=V3Action.RECOMMEND,
        commerce_intent=CommerceIntent.RECOMMEND,
        computer_purchase_kind=ComputerPurchaseKind.UNKNOWN,
        computer_purchase_evidence=_purchase_evidence(first_text, "电脑"),
        price_constraint=_max_price(9000, first_text.index(price_text), first_text.index(price_text) + len(price_text), price_text),
        pc_usage_surfaces=("剪辑视频",),
        missing_fields=("computer_purchase_kind",),
    )
    first_decision = V3Orchestrator(semantic_parser=FakeSemanticParser(first_observation)).decide(
        NormalizedTurn("topic-first", "topic-session", first_text), catalog=load_combined_product_catalog(), session=session
    )
    apply_session_delta(
        session,
        clarification_delta(load_session_core(session), plan=first_decision.clarification, observation=first_observation, source_text=first_text),
    )

    text = "推荐一双篮球鞋，适合实战，预算 1000"
    price_text = "预算 1000"
    observation = SemanticObservation(
        action=V3Action.RECOMMEND,
        commerce_intent=CommerceIntent.RECOMMEND,
        target_type_surface="篮球鞋",
        target_type_candidate_id="sub_category:篮球鞋",
        target_type_evidence=_type_evidence(text, "篮球鞋"),
        price_constraint=_max_price(1000, text.index(price_text), text.index(price_text) + len(price_text), price_text),
    )
    decision = V3Orchestrator(semantic_parser=FakeSemanticParser(observation)).decide(
        NormalizedTurn("topic-second", "topic-session", text), catalog=load_combined_product_catalog(), session=session
    )

    assert decision.status is ParseStatus.SEMANTIC_EXECUTABLE
    assert decision.requirement is not None
    assert decision.requirement.price_max == 1000
    assert decision.requirement.product_type_ids == ("sub_category:篮球鞋",)


def test_computer_purchase_pending_state_round_trips_through_session_core():
    text = "我要一台剪辑视频用的电脑，预算 9000"
    observation = SemanticObservation(
        action=V3Action.RECOMMEND,
        commerce_intent=CommerceIntent.RECOMMEND,
        computer_purchase_kind=ComputerPurchaseKind.UNKNOWN,
        computer_purchase_evidence=_purchase_evidence(text, "电脑"),
        pc_usage_surfaces=("剪辑视频",),
        missing_fields=("computer_purchase_kind",),
    )
    plan = ClarificationPlan("你想买笔记本，还是让我配一台台式主机？", ("computer_purchase_kind",), 9999999999, "computer_purchase_kind_unresolved")
    session = SimpleNamespace(v3_core={})
    apply_session_delta(session, clarification_delta(load_session_core(session), plan=plan, observation=observation, source_text=text))

    restored = load_session_core(session, now=1.0)
    assert session.v3_core["schema_version"] == 3
    assert restored.pending_clarification is not None
    assert restored.pending_clarification.observation.computer_purchase_kind is ComputerPurchaseKind.UNKNOWN
    assert restored.pending_clarification.observation.pc_usage_surfaces == ("剪辑视频",)


def test_semantic_parser_rejects_unknown_action_without_fallback(monkeypatch):
    class Client:
        configured = True
        config = SimpleNamespace(provider="test", fast_model="test")

        def chat_json_with_report(self, *args, **kwargs):
            return {"action": "delete_everything"}, SimpleNamespace(elapsed_ms=1)

    parser = SemanticParser(client=Client())
    result = parser.parse(text="任意输入", registry=CatalogNormalizationRegistry.from_catalog(load_product_catalog()))

    assert result.observation is None
    assert result.error_code == "semantic_llm_invalid"


def test_semantic_parser_ignores_irrelevant_query_kind_on_a_recommendation():
    class Client:
        configured = True
        config = SimpleNamespace(provider="test", fast_model="test")

        def chat_json_with_report(self, *args, **kwargs):
            return {"action": "recommend_shopping_products", "target_type_surface": "手机", "query_kind": "search"}, SimpleNamespace(elapsed_ms=1)

    result = SemanticParser(client=Client()).parse(text="推荐手机", registry=CatalogNormalizationRegistry.from_catalog(load_product_catalog()))
    assert result.observation is not None
    assert result.observation.action is V3Action.RECOMMEND
    assert result.observation.query_kind is None


def test_semantic_parser_keeps_incomplete_fact_action_for_clarification():
    class Client:
        configured = True
        config = SimpleNamespace(provider="test", fast_model="test")

        def chat_json_with_report(self, *args, **kwargs):
            return {"action": "parameter_query", "commerce_intent": "compare"}, SimpleNamespace(elapsed_ms=1)

    result = SemanticParser(client=Client()).parse(text="帮我比较这两个商品", registry=CatalogNormalizationRegistry.from_catalog(load_product_catalog()))

    assert result.observation is not None
    assert result.observation.action is V3Action.PARAMETER_QUERY
    assert result.observation.query_kind is None


def test_semantic_parser_decodes_computer_purchase_kind_and_evidence():
    text = "7000 元配一台游戏主机"

    class Client:
        configured = True
        config = SimpleNamespace(provider="test", fast_model="test")

        def chat_json_with_report(self, *args, **kwargs):
            return {
                "action": "generate_pc_build_plan",
                "commerce_intent": "pc_plan",
                "computer_purchase_kind": "desktop_build",
                "computer_purchase_evidence": {
                    "surface": "配一台",
                    "evidence_start": text.index("配一台"),
                    "evidence_end": text.index("配一台") + len("配一台"),
                    "evidence_text": "配一台",
                },
            }, SimpleNamespace(elapsed_ms=1)

    result = SemanticParser(client=Client()).parse(text=text, registry=CatalogNormalizationRegistry.from_catalog(load_combined_product_catalog()))

    assert result.observation is not None
    assert result.observation.computer_purchase_kind is ComputerPurchaseKind.DESKTOP_BUILD
    assert result.observation.computer_purchase_evidence is not None
    assert result.observation.computer_purchase_evidence.surface == "配一台"


def test_explicit_prebuilt_desktop_without_catalog_type_is_scope_rejected_after_form_confirmation():
    text = "推荐一台成品台式机，预算 9000"
    decision = V3Orchestrator(
        semantic_parser=FakeSemanticParser(
            SemanticObservation(
                action=V3Action.RECOMMEND,
                commerce_intent=CommerceIntent.RECOMMEND,
                target_type_surface="成品台式机",
                target_type_evidence=_type_evidence(text, "成品台式机"),
                computer_purchase_kind=ComputerPurchaseKind.PREBUILT_DESKTOP,
                computer_purchase_evidence=_purchase_evidence(text, "成品台式机"),
                price_constraint=_max_price(9000, text.index("预算 9000"), text.index("预算 9000") + len("预算 9000"), "预算 9000"),
            )
        )
    ).decide(NormalizedTurn("prebuilt", "prebuilt", text), catalog=load_combined_product_catalog(), session=SimpleNamespace(v3_core={}))

    assert decision.status is ParseStatus.REJECT
    assert decision.reason_code == "catalog_scope_unsupported"


def test_semantic_comparison_requires_two_live_cards_and_promotes_card_ids():
    from rag.recommendation.v3.session import recommendation_delta
    from rag.recommendation.v3.types import CardModel, RequirementSpecV3

    catalog = load_product_catalog()
    cards = (
        CardModel("card-a", "p_digital_008", (), "a", 1, 9999999999),
        CardModel("card-b", "p_digital_016", (), "b", 2, 9999999999),
    )
    core = recommendation_delta(RequirementSpecV3(action=V3Action.RECOMMEND, product_type_ids=("phone",)), cards).core
    promoted = HardConstraintPromotionGate().promote(
        text="比较第一个和第二个手机",
        observation=SemanticObservation(action=V3Action.PARAMETER_QUERY, query_kind="compare", target_card_ranks=(1, 2)),
        registry=CatalogNormalizationRegistry.from_catalog(catalog),
        core=core,
    )
    assert promoted.requirement is not None
    assert promoted.requirement.target_card_ids == ("card-a", "card-b")


def test_v3_retrieval_uses_only_candidate_gate_product_ids():
    class Embeddings:
        def get_all_embeddings(self, texts):
            assert texts == ["推荐手机"]
            return [[0.1, 0.2]], [{1: 0.5}]

    class Manager:
        def has_collection(self):
            return True

        def hybrid_retrieve(self, dense, sparse, *, top_k, filter_expr):
            assert dense == [0.1, 0.2]
            assert sparse == {1: 0.5}
            assert 'product_id in ["p_digital_016"]' in filter_expr
            return [{"product_id": "p_digital_016"}, {"product_id": "not_allowed"}]

    result = V3EvidenceRetriever(manager=Manager(), embedding_service=Embeddings()).retrieve(
        query="推荐手机",
        filters=CatalogCandidateGate().evaluate(
            HardConstraintPromotionGate().promote(
                text="推荐 5000 元以内的小米以外的手机",
                observation=SemanticObservation(action=V3Action.RECOMMEND, target_type_surface="手机", exclude_brand_surfaces=("小米",), price_constraint=_max_price(5000, 3, 11, "5000 元以内")),
                registry=CatalogNormalizationRegistry.from_catalog(load_product_catalog()),
                core=empty_session_core(),
                type_resolution=_resolved("phone"),
            ).requirement,
            catalog=load_product_catalog(),
        ).filters,
    )
    assert result.status == "ok"
    assert result.ranked_product_ids == ("p_digital_016",)


def test_type_candidates_force_explicit_pad_despite_many_negative_types():
    catalog = load_combined_product_catalog()
    registry = CatalogNormalizationRegistry.from_catalog(catalog)
    text = "篮球鞋，雨鞋，手机，靴子，电脑，电扇，窗帘，这些我都不要，给我推荐 pad 吧"

    candidates = build_type_candidate_set(text=text, registry=registry, catalog=catalog)
    by_id = {item.canonical_type_id: item for item in candidates.candidates}

    assert "tablet" in by_id
    assert "A_exact" in by_id["tablet"].sources
    assert "phone" in by_id and "A_exact" in by_id["phone"].sources


def test_type_candidates_use_full_query_and_action_window_for_nonstandard_basketball_surface():
    catalog = load_combined_product_catalog()
    registry = CatalogNormalizationRegistry.from_catalog(catalog)
    candidates = build_type_candidate_set(text="推荐一双篮球实战鞋，缓震好，预算1000", registry=registry, catalog=catalog)
    basketball = next(item for item in candidates.candidates if item.canonical_type_id == "sub_category:篮球鞋")

    assert "A_exact" not in basketball.sources
    assert {"B_full_query", "C_action_window"} & set(basketball.sources)


def test_type_resolution_accepts_candidate_choice_and_type_exclusion_then_candidate_gate_applies_it():
    catalog = load_combined_product_catalog()
    registry = CatalogNormalizationRegistry.from_catalog(catalog)
    text = "不要手机，给我推荐 pad"
    candidates = build_type_candidate_set(text=text, registry=registry, catalog=catalog)
    observation = SemanticObservation(
        action=V3Action.RECOMMEND,
        target_type_surface="pad",
        target_type_candidate_id="tablet",
        target_type_evidence=_type_evidence(text, "pad"),
        exclude_type_candidate_ids=("phone",),
        exclude_type_evidences=(_type_evidence(text, "手机"),),
    )

    resolved = TypeResolutionGate().resolve(text=text, observation=observation, candidate_set=candidates, registry=registry)
    assert resolved.product_type_ids == ("tablet",)
    assert resolved.exclude_product_type_ids == ("phone",)
    promoted = HardConstraintPromotionGate().promote(
        text=text,
        observation=observation,
        registry=registry,
        core=empty_session_core(),
        type_resolution=resolved,
    )
    assert promoted.requirement is not None
    allowed = CatalogCandidateGate().evaluate(promoted.requirement, catalog=catalog).filters.product_ids
    assert allowed
    assert all(catalog.get(product_id).sub_category == "平板电脑" for product_id in allowed)


def test_type_resolution_rejects_candidate_outside_menu_and_fabricated_evidence():
    catalog = load_combined_product_catalog()
    registry = CatalogNormalizationRegistry.from_catalog(catalog)
    text = "推荐 pad"
    candidates = build_type_candidate_set(text=text, registry=registry, catalog=catalog)
    outside = TypeResolutionGate().resolve(
        text=text,
        observation=SemanticObservation(
            action=V3Action.RECOMMEND,
            target_type_surface="推荐",
            target_type_candidate_id="not-a-catalog-type",
            target_type_evidence=_type_evidence(text, "推荐"),
        ),
        candidate_set=candidates,
        registry=registry,
    )
    assert outside.clarification is not None
    assert outside.reason_code == "type_candidate_invalid"
    fabricated = TypeResolutionGate().resolve(
        text=text,
        observation=SemanticObservation(
            action=V3Action.RECOMMEND,
            target_type_surface="pad",
            target_type_candidate_id="tablet",
            target_type_evidence=TypeSurfaceEvidence("pad", 0, 2, "pa"),
        ),
        candidate_set=candidates,
        registry=registry,
    )
    assert fabricated.clarification is not None
    assert fabricated.reason_code == "target_type_evidence_unverifiable"


def test_type_resolution_normalizes_exact_catalog_display_label_from_menu():
    catalog = load_combined_product_catalog()
    registry = CatalogNormalizationRegistry.from_catalog(catalog)
    text = "推荐一台笔记本"
    candidates = build_type_candidate_set(text=text, registry=registry, catalog=catalog)

    resolved = TypeResolutionGate().resolve(
        text=text,
        observation=SemanticObservation(
            action=V3Action.RECOMMEND,
            target_type_surface="笔记本",
            # The menu display label is valid, although it is not the internal ID.
            target_type_candidate_id="笔记本电脑",
            target_type_evidence=_type_evidence(text, "笔记本"),
        ),
        candidate_set=candidates,
        registry=registry,
    )

    assert resolved.product_type_ids == ("sub_category:笔记本电脑",)
    assert resolved.reason_code == "type_candidate_catalog_label_normalized"


def test_type_evidence_unique_exact_fallback_allows_only_one_raw_occurrence():
    catalog = load_combined_product_catalog()
    registry = CatalogNormalizationRegistry.from_catalog(catalog)
    text = "给我推荐 pad"
    candidates = build_type_candidate_set(text=text, registry=registry, catalog=catalog)
    resolved = TypeResolutionGate().resolve(
        text=text,
        observation=SemanticObservation(
            action=V3Action.RECOMMEND,
            target_type_surface="pad",
            target_type_candidate_id="tablet",
            # Deliberately one character off: real models may count punctuation
            # differently, so only a unique exact phrase can recover.
            target_type_evidence=TypeSurfaceEvidence("pad", 0, 3, "pad"),
        ),
        candidate_set=candidates,
        registry=registry,
    )
    assert resolved.product_type_ids == ("tablet",)
    duplicate_text = "pad 还是 pad，推荐一个"
    duplicate_candidates = build_type_candidate_set(text=duplicate_text, registry=registry, catalog=catalog)
    duplicate = TypeResolutionGate().resolve(
        text=duplicate_text,
        observation=SemanticObservation(
            action=V3Action.RECOMMEND,
            target_type_surface="pad",
            target_type_candidate_id="tablet",
            target_type_evidence=TypeSurfaceEvidence("pad", 1, 4, "pad"),
        ),
        candidate_set=duplicate_candidates,
        registry=registry,
    )
    assert duplicate.clarification is not None
    assert duplicate.reason_code == "target_type_evidence_unverifiable"
