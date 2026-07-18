"""Own the single V3 decision chain from one turn to one typed execution plan.

The orchestrator does not retrieve products or mutate carts.  It chooses the
safe local grammar when proved; otherwise it makes one action-specific
SemanticParse call, applies an explicit topic transition, promotes local
candidate IDs, and returns either execution, one clarification, or rejection.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from .clarification_policy import ClarificationPolicy
from .promotion import HardConstraintPromotionGate
from .registry import CatalogNormalizationRegistry
from .router import V3Router
from .semantic_contracts import (
    GeneralChatObservation,
    PcBuildObservation,
    RecommendObservation,
    SemanticContext,
    SemanticObservation,
    build_brand_candidate_set,
)
from .semantic_parse import SemanticParser
from .session import load_session_core
from .type_candidates import build_type_candidate_set
from .type_resolution_gate import TypeResolutionGate
from .types import ComputerPurchaseKind, ParseStatus, RecommendationMode, V3Action, V3ExecutionDecision
from .config import EXPLICIT_PRODUCT_REQUEST_MARKERS


class V3Orchestrator:
    """One turn has one execution authority: SafetyProof or SemanticParse."""

    def __init__(self, *, semantic_parser: SemanticParser | None = None, promotion_gate: HardConstraintPromotionGate | None = None) -> None:
        self._router = V3Router()
        self._semantic_parser = semantic_parser or SemanticParser()
        self._promotion_gate = promotion_gate or HardConstraintPromotionGate()
        self._clarification_policy = ClarificationPolicy()
        self._type_resolution_gate = TypeResolutionGate()

    def decide(self, turn, *, catalog: Any, session: Any) -> V3ExecutionDecision:
        local = self._router.route(turn, catalog=catalog, session=session)
        if local.status is ParseStatus.SAFE_DIRECT:
            return V3ExecutionDecision(local.status, local.action, local.requirement, local.rule_signal, reason_code="safety_proof_complete")

        core = load_session_core(session)
        registry = CatalogNormalizationRegistry.from_catalog(catalog)
        type_candidates = build_type_candidate_set(text=turn.text, registry=registry, catalog=catalog)
        brand_candidates = build_brand_candidate_set(text=turn.text, registry=registry)
        parsed = self._semantic_parser.parse(
            text=turn.text,
            registry=registry,
            catalog=catalog,
            candidate_set=type_candidates,
            brand_candidate_set=brand_candidates,
            context=_semantic_context(core),
        )
        if parsed.observation is None:
            return V3ExecutionDecision(ParseStatus.REJECT, None, None, local.rule_signal, semantic=parsed, reason_code=parsed.error_code or "semantic_parse_rejected")

        normalized = _normalize_ambiguous_computer_observation(turn.text, parsed.observation)
        observation, semantic_text, base_requirement = _apply_topic_transition(core, normalized, turn.text)
        parsed = replace(parsed, observation=observation)
        if isinstance(observation, GeneralChatObservation):
            if _general_chat_outside_catalog(text=turn.text, registry=registry):
                return V3ExecutionDecision(ParseStatus.REJECT, V3Action.RECOMMEND, None, local.rule_signal, semantic=parsed, reason_code="catalog_scope_unsupported")
            return V3ExecutionDecision(ParseStatus.SEMANTIC_EXECUTABLE, V3Action.GENERAL_CHAT, None, local.rule_signal, semantic=parsed, reason_code="semantic_general_chat")

        computer_clarification = _computer_form_clarification(turn.text, observation)
        if computer_clarification is not None:
            return V3ExecutionDecision(ParseStatus.LOCAL_CLARIFY, observation.action, None, local.rule_signal, semantic=parsed, clarification=computer_clarification, reason_code=computer_clarification.reason_code)

        clarification = self._clarification_policy.plan(observation=observation, core=core, catalog=catalog)
        if clarification is not None:
            return V3ExecutionDecision(ParseStatus.LOCAL_CLARIFY, observation.action, None, local.rule_signal, semantic=parsed, clarification=clarification, reason_code=clarification.reason_code)

        if observation.action is V3Action.APPLY_CART:
            return V3ExecutionDecision(ParseStatus.SEMANTIC_EXECUTABLE, observation.action, None, local.rule_signal, semantic=parsed, reason_code="semantic_cart_observation")

        type_resolution = None
        if isinstance(observation, RecommendObservation) and (observation.target_type_surface or observation.target_type_candidate_id or observation.exclude_type_candidate_ids):
            type_resolution = self._type_resolution_gate.resolve(text=turn.text, observation=observation, candidate_set=type_candidates, registry=registry)

        promoted = self._promotion_gate.promote(
            text=semantic_text,
            observation=observation,
            registry=registry,
            core=core,
            type_resolution=type_resolution,
            brand_candidates=brand_candidates,
            base_requirement=base_requirement,
        )
        if promoted.clarification is not None:
            return V3ExecutionDecision(ParseStatus.LOCAL_CLARIFY, observation.action, None, local.rule_signal, semantic=parsed, clarification=promoted.clarification, reason_code=promoted.reason_code)
        if promoted.requirement is None:
            return V3ExecutionDecision(ParseStatus.REJECT, observation.action, None, local.rule_signal, semantic=parsed, reason_code=promoted.reason_code)
        return V3ExecutionDecision(ParseStatus.SEMANTIC_EXECUTABLE, promoted.requirement.action, promoted.requirement, local.rule_signal, semantic=parsed, reason_code=promoted.reason_code)


def _semantic_context(core) -> SemanticContext:
    active = core.active_requirement
    pending = core.pending_clarification
    return SemanticContext(
        active_product_type_ids=active.product_type_ids if active else (),
        active_excluded_brand_ids=active.exclude_brand_family_ids if active else (),
        live_card_count=len(core.cards),
        cart_line_count=len(core.cart_lines),
        has_current_pc_plan=core.pc_plans.current is not None,
        has_previous_pc_plan=core.pc_plans.previous is not None,
        pending_action=pending.observation.action.value if pending else None,
        pending_missing_fields=pending.plan.missing_fields if pending else (),
    )


def _apply_topic_transition(core, current: SemanticObservation, current_text: str):
    """Merge only a compatible short reply; a new action never inherits stale state."""

    pending = core.pending_clarification
    if pending is not None:
        merged = _merge_pending(pending.observation, current)
        if merged is not None:
            return merged, f"{pending.source_text} {current_text}".strip(), core.active_requirement
    if isinstance(current, RecommendObservation) and core.active_requirement is not None and not current.target_type_surface and not current.target_type_candidate_id:
        return current, current_text, core.active_requirement
    return current, current_text, None


def _merge_pending(previous: SemanticObservation, current: SemanticObservation) -> SemanticObservation | None:
    if isinstance(previous, RecommendObservation) and isinstance(current, RecommendObservation):
        # A concrete new type starts a new shopping topic; it must not inherit
        # the pending computer/price/brand conditions.
        if current.target_type_surface and (
            previous.target_type_candidate_id is None
            or current.target_type_candidate_id != previous.target_type_candidate_id
        ):
            return None
        return RecommendObservation(
            mode=current.mode or previous.mode,
            target_type_surface=current.target_type_surface or previous.target_type_surface,
            target_type_candidate_id=current.target_type_candidate_id or previous.target_type_candidate_id,
            target_type_evidence=current.target_type_evidence or previous.target_type_evidence,
            exclude_type_candidate_ids=current.exclude_type_candidate_ids or previous.exclude_type_candidate_ids,
            positive_brand_candidate_ids=current.positive_brand_candidate_ids or previous.positive_brand_candidate_ids,
            negative_brand_candidate_ids=current.negative_brand_candidate_ids or previous.negative_brand_candidate_ids,
            release_brand_candidate_ids=current.release_brand_candidate_ids or previous.release_brand_candidate_ids,
            budget=current.budget or previous.budget,
            desired_attribute_surfaces=current.desired_attribute_surfaces or previous.desired_attribute_surfaces,
            computer_purchase_kind=current.computer_purchase_kind or previous.computer_purchase_kind,
            computer_purchase_evidence=current.computer_purchase_evidence or previous.computer_purchase_evidence,
            pc_usage_surfaces=current.pc_usage_surfaces or previous.pc_usage_surfaces,
        )
    if isinstance(previous, RecommendObservation) and isinstance(current, PcBuildObservation):
        return PcBuildObservation(
            budget=current.budget or previous.budget,
            usage_surfaces=current.usage_surfaces or previous.pc_usage_surfaces,
            computer_purchase_evidence=current.computer_purchase_evidence or previous.computer_purchase_evidence,
        )
    return None


def _computer_form_clarification(text: str, observation: SemanticObservation):
    from .types import ClarificationPlan
    import time
    if isinstance(observation, RecommendObservation) and observation.computer_purchase_kind is ComputerPurchaseKind.UNKNOWN:
        return ClarificationPlan("请明确要买笔记本、成品台式机，还是让我按预算配一台台式主机？", ("computer_purchase_kind",), time.time() + 600, "computer_purchase_kind_unresolved")
    if isinstance(observation, PcBuildObservation) and not _has_explicit_build_signal(text):
        return ClarificationPlan("如果你想让我组装台式主机，请明确说“配一台/装机/DIY”。", ("computer_purchase_kind",), time.time() + 600, "computer_purchase_kind_unresolved")
    return None


def _normalize_ambiguous_computer_observation(text: str, observation: SemanticObservation) -> SemanticObservation:
    """Downgrade an unproven first-time PC build to a safe purchase-form question.

    This is not a second router and does not infer a PC action.  SemanticParse
    already observed budget/use; local validation merely prevents its
    unsupported ``pc_build`` action from reaching the solver when the original
    sentence lacks an explicit DIY/build signal.  The resulting pending
    recommendation can later merge either a laptop reply or an explicit build
    reply without leaking into unrelated topics.
    """

    if not isinstance(observation, PcBuildObservation) or _has_explicit_build_signal(text):
        return observation
    return RecommendObservation(
        mode=RecommendationMode.PRODUCT,
        budget=observation.budget,
        pc_usage_surfaces=observation.usage_surfaces,
        computer_purchase_kind=ComputerPurchaseKind.UNKNOWN,
        computer_purchase_evidence=observation.computer_purchase_evidence,
    )


def _has_explicit_build_signal(text: str) -> bool:
    from .config import PC_BUILD_EXPLICIT_SIGNALS
    return any(signal.lower() in text.lower() for signal in PC_BUILD_EXPLICIT_SIGNALS)


def _general_chat_outside_catalog(*, text: str, registry) -> bool:
    if not any(marker in text for marker in EXPLICIT_PRODUCT_REQUEST_MARKERS):
        return False
    normalized = text.lower()
    return not any(alias.lower() in normalized for alias, _entity in registry.product_type_aliases())
