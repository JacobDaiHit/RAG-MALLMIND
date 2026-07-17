"""V3's single routing authority for deterministic and semantic turns."""
from __future__ import annotations

from typing import Any

from .promotion import HardConstraintPromotionGate
from .clarification_policy import ClarificationPolicy
from .computer_purchase_kind import ComputerPurchaseKindValidator
from .registry import CatalogNormalizationRegistry
from .router import V3Router
from .semantic_parse import SemanticParser
from .session import load_session_core
from .type_candidates import build_type_candidate_set
from .type_resolution_gate import TypeResolutionGate
from .types import ComputerPurchaseKind, ParseStatus, V3Action, V3ExecutionDecision
from .config import EXPLICIT_PRODUCT_REQUEST_MARKERS


class V3Orchestrator:
    """Either certifies a grammar or performs exactly one semantic parse."""

    def __init__(self, *, semantic_parser: SemanticParser | None = None, promotion_gate: HardConstraintPromotionGate | None = None) -> None:
        self._router = V3Router()
        self._semantic_parser = semantic_parser or SemanticParser()
        self._promotion_gate = promotion_gate or HardConstraintPromotionGate()
        self._clarification_policy = ClarificationPolicy()
        self._computer_purchase_kind_validator = ComputerPurchaseKindValidator()
        self._type_resolution_gate = TypeResolutionGate()

    def decide(self, turn, *, catalog: Any, session: Any) -> V3ExecutionDecision:
        local = self._router.route(turn, catalog=catalog, session=session)
        if local.status is ParseStatus.SAFE_DIRECT:
            return V3ExecutionDecision(local.status, local.action, local.requirement, local.rule_signal, reason_code="safety_proof_complete")
        registry = CatalogNormalizationRegistry.from_catalog(catalog)
        core = load_session_core(session)
        candidate_set = build_type_candidate_set(text=turn.text, registry=registry, catalog=catalog)
        parsed = self._semantic_parser.parse(text=turn.text, registry=registry, catalog=catalog, candidate_set=candidate_set)
        if parsed.observation is None:
            return V3ExecutionDecision(
                ParseStatus.REJECT,
                None,
                None,
                local.rule_signal,
                semantic=parsed,
                reason_code=parsed.error_code or "semantic_parse_rejected",
            )
        purchase_validation = self._computer_purchase_kind_validator.validate(text=turn.text, observation=parsed.observation)
        if purchase_validation.clarification is not None:
            return V3ExecutionDecision(
                ParseStatus.LOCAL_CLARIFY,
                parsed.observation.action,
                None,
                local.rule_signal,
                semantic=parsed,
                clarification=purchase_validation.clarification,
                reason_code=purchase_validation.reason_code,
            )
        validated_observation = purchase_validation.observation or parsed.observation
        observation = _merge_pending_observation(core, validated_observation)
        semantic_text = _merge_pending_text(core, turn.text, observation)
        parsed = type(parsed)(observation, parsed.provider, parsed.model, parsed.elapsed_ms, parsed.error_code)
        if _general_chat_outside_catalog(text=turn.text, observation=observation, registry=registry):
            return V3ExecutionDecision(
                ParseStatus.REJECT,
                V3Action.RECOMMEND,
                None,
                local.rule_signal,
                semantic=parsed,
                reason_code="catalog_scope_unsupported",
            )
        clarification = self._clarification_policy.plan(observation=observation, core=core, catalog=catalog)
        if clarification is not None:
            return V3ExecutionDecision(
                ParseStatus.LOCAL_CLARIFY,
                observation.action,
                None,
                local.rule_signal,
                semantic=parsed,
                clarification=clarification,
                reason_code=clarification.reason_code,
            )
        if observation.action in {V3Action.APPLY_CART, V3Action.GENERAL_CHAT}:
            return V3ExecutionDecision(
                ParseStatus.SEMANTIC_EXECUTABLE,
                observation.action,
                None,
                local.rule_signal,
                semantic=parsed,
                reason_code="semantic_cart_observation",
            )
        type_resolution = None
        if observation.action is V3Action.RECOMMEND:
            type_resolution = self._type_resolution_gate.resolve(
                text=turn.text,
                observation=observation,
                candidate_set=candidate_set,
                registry=registry,
            )
            if type_resolution.clarification is not None:
                return V3ExecutionDecision(
                    ParseStatus.LOCAL_CLARIFY,
                    observation.action,
                    None,
                    local.rule_signal,
                    semantic=parsed,
                    clarification=type_resolution.clarification,
                    reason_code=type_resolution.reason_code,
                )
            if not type_resolution.product_type_ids:
                return V3ExecutionDecision(
                    ParseStatus.REJECT,
                    observation.action,
                    None,
                    local.rule_signal,
                    semantic=parsed,
                    reason_code=type_resolution.reason_code or "catalog_scope_unsupported",
                )
        promoted = self._promotion_gate.promote(
            text=semantic_text,
            observation=observation,
            registry=registry,
            core=core,
            type_resolution=type_resolution,
        )
        if promoted.clarification is not None:
            return V3ExecutionDecision(
                ParseStatus.LOCAL_CLARIFY,
                parsed.observation.action,
                None,
                local.rule_signal,
                semantic=parsed,
                clarification=promoted.clarification,
                reason_code=promoted.reason_code,
            )
        if promoted.requirement is None:
            return V3ExecutionDecision(
                ParseStatus.REJECT,
                parsed.observation.action,
                None,
                local.rule_signal,
                semantic=parsed,
                reason_code=promoted.reason_code,
            )
        return V3ExecutionDecision(
            ParseStatus.SEMANTIC_EXECUTABLE,
            promoted.requirement.action,
            promoted.requirement,
            local.rule_signal,
            semantic=parsed,
            reason_code=promoted.reason_code,
        )


def _merge_pending_observation(core, current):
    pending = core.pending_clarification
    if pending is None:
        return current
    if pending.plan.reason_code == "computer_purchase_kind_unresolved":
        if current.computer_purchase_kind not in {
            ComputerPurchaseKind.DESKTOP_BUILD,
            ComputerPurchaseKind.LAPTOP,
            ComputerPurchaseKind.PREBUILT_DESKTOP,
        }:
            return current
    elif pending.observation.action is not current.action:
        return current
    previous = pending.observation
    return type(current)(
        action=current.action,
        commerce_intent=current.commerce_intent if current.commerce_intent.value != "none" else previous.commerce_intent,
        target_type_surface=current.target_type_surface or previous.target_type_surface,
        target_type_candidate_id=current.target_type_candidate_id or previous.target_type_candidate_id,
        target_type_evidence=current.target_type_evidence or previous.target_type_evidence,
        exclude_type_candidate_ids=current.exclude_type_candidate_ids or previous.exclude_type_candidate_ids,
        exclude_type_evidences=current.exclude_type_evidences or previous.exclude_type_evidences,
        include_brand_surfaces=current.include_brand_surfaces or previous.include_brand_surfaces,
        exclude_brand_surfaces=current.exclude_brand_surfaces or previous.exclude_brand_surfaces,
        price_max=current.price_max if current.price_max is not None else previous.price_max,
        price_constraint=current.price_constraint or previous.price_constraint,
        desired_attribute_surfaces=current.desired_attribute_surfaces or previous.desired_attribute_surfaces,
        target_card_rank=current.target_card_rank if current.target_card_rank is not None else previous.target_card_rank,
        target_card_ranks=current.target_card_ranks or previous.target_card_ranks,
        target_cart_rank=current.target_cart_rank if current.target_cart_rank is not None else previous.target_cart_rank,
        query_kind=current.query_kind or previous.query_kind,
        cart_operation=current.cart_operation or previous.cart_operation,
        quantity=current.quantity if current.quantity is not None else previous.quantity,
        pc_usage_surfaces=current.pc_usage_surfaces or previous.pc_usage_surfaces,
        pc_operation=current.pc_operation or previous.pc_operation,
        pc_plan_reference=current.pc_plan_reference or previous.pc_plan_reference,
        pc_component_category_surface=current.pc_component_category_surface or previous.pc_component_category_surface,
        upgrade_direction=current.upgrade_direction or previous.upgrade_direction,
        computer_purchase_kind=current.computer_purchase_kind or previous.computer_purchase_kind,
        computer_purchase_evidence=current.computer_purchase_evidence or previous.computer_purchase_evidence,
        missing_fields=current.missing_fields,
    )


def _merge_pending_text(core, current_text: str, observation) -> str:
    pending = core.pending_clarification
    if pending is None:
        return current_text
    if pending.plan.reason_code == "computer_purchase_kind_unresolved":
        if observation.computer_purchase_kind not in {
            ComputerPurchaseKind.DESKTOP_BUILD,
            ComputerPurchaseKind.LAPTOP,
            ComputerPurchaseKind.PREBUILT_DESKTOP,
        }:
            return current_text
    elif pending.observation.action is not observation.action:
        return current_text
    return f"{pending.source_text} {current_text}".strip()


def _general_chat_outside_catalog(*, text: str, observation, registry: CatalogNormalizationRegistry) -> bool:
    """Fail closed only for explicit product requests absent from the catalog.

    This is a narrow backstop for a model that ignores the SemanticParse action
    contract.  It deliberately does not classify ordinary cart follow-ups or
    open-ended gift requests as out-of-catalog products.
    """

    if observation.action is not V3Action.GENERAL_CHAT:
        return False
    normalized = text.lower()
    if not any(marker in text for marker in EXPLICIT_PRODUCT_REQUEST_MARKERS):
        return False
    return not any(alias.lower() in normalized for alias, _entity in registry.product_type_aliases())
