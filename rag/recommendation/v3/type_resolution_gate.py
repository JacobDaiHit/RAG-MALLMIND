"""Fail-closed conversion from one LLM type choice to catalog IDs."""
from __future__ import annotations

import time

from .config import CLARIFICATION_TTL_SECONDS
from .registry import CatalogNormalizationRegistry
from .types import ClarificationPlan, SemanticObservation, TaxonomyCandidateSet, TypeResolutionResult


class TypeResolutionGate:
    """Own catalog type IDs; SemanticParse only supplies untrusted observations."""

    def resolve(
        self,
        *,
        text: str,
        observation: SemanticObservation,
        candidate_set: TaxonomyCandidateSet,
        registry: CatalogNormalizationRegistry,
    ) -> TypeResolutionResult:
        if not observation.target_type_surface or observation.target_type_evidence is None:
            return _clarify("product_type_unresolved", "你想让我推荐哪一类商品？例如手机、咖啡、防晒或篮球鞋。")
        if not _evidence_matches(text, observation.target_type_evidence):
            return _clarify("target_type_evidence_unverifiable", "我无法确认你想要的商品类型，请直接说明要推荐的类别。")
        candidate_ids = {candidate.canonical_type_id for candidate in candidate_set.candidates}
        selected = observation.target_type_candidate_id
        if selected:
            if selected not in candidate_ids:
                # Models occasionally copy the candidate menu's display label
                # rather than its internal ID.  Only normalize an exact,
                # unique catalog label that is already in this turn's menu.
                selected_entity = registry.product_type_by_surface(selected)
                if selected_entity is None or selected_entity.canonical_id not in candidate_ids:
                    return _clarify("type_candidate_invalid", "我无法确认商品类别，请直接说明要推荐的类别。")
                product_type_ids = (selected_entity.canonical_id,)
                reason = "type_candidate_catalog_label_normalized"
            else:
                product_type_ids = (selected,)
                reason = "type_candidate_selected"
        else:
            # Exact rescue is only for a real catalog spelling (e.g. pad), not
            # semantic containment or a hand-maintained natural-language alias.
            entity = registry.product_type_by_surface(observation.target_type_surface)
            if entity is None:
                return TypeResolutionResult(reason_code="catalog_scope_unsupported")
            product_type_ids = (entity.canonical_id,)
            reason = "type_surface_exact_rescue"
        excluded, exclusion_error = _resolve_exclusions(text, observation, candidate_ids)
        if exclusion_error:
            return _clarify(exclusion_error, "我无法确认需要排除的商品类别，请换一种更明确的说法。")
        if set(product_type_ids) & set(excluded):
            return _clarify("type_constraint_conflict", "同一商品类别同时被要求推荐和排除，请明确你的选择。")
        return TypeResolutionResult(product_type_ids, excluded, reason_code=reason)


def _resolve_exclusions(text: str, observation: SemanticObservation, candidate_ids: set[str]) -> tuple[tuple[str, ...], str]:
    ids = observation.exclude_type_candidate_ids
    evidences = observation.exclude_type_evidences
    if not ids and not evidences:
        return (), ""
    if len(ids) != len(evidences) or len(set(ids)) != len(ids):
        return (), "exclude_type_observation_invalid"
    if any(candidate_id not in candidate_ids for candidate_id in ids):
        return (), "exclude_type_candidate_invalid"
    if any(not _evidence_matches(text, evidence) for evidence in evidences):
        return (), "exclude_type_evidence_unverifiable"
    return tuple(sorted(ids)), ""


def _evidence_matches(text: str, evidence) -> bool:
    if evidence.evidence_text != evidence.surface:
        return False
    if 0 <= evidence.evidence_start < evidence.evidence_end <= len(text):
        if text[evidence.evidence_start:evidence.evidence_end] == evidence.evidence_text:
            return True
    # Chat models occasionally count punctuation positions differently.  A
    # fallback is safe only when the exact raw phrase occurs once; it never
    # changes the phrase, normalizes a synonym, or guesses among duplicates.
    return bool(evidence.evidence_text) and text.count(evidence.evidence_text) == 1


def _clarify(reason_code: str, question: str) -> TypeResolutionResult:
    plan = ClarificationPlan(
        question=question,
        missing_fields=("product_type",),
        expires_at=time.time() + CLARIFICATION_TTL_SECONDS,
        reason_code=reason_code,
    )
    return TypeResolutionResult(clarification=plan, reason_code=reason_code)
