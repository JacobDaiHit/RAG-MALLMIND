"""Promote typed semantic candidates into executable V3 requirements.

This module does not understand Chinese brand operators.  The single semantic
model call chooses positive/negative/release brand candidate IDs; PromotionGate
only verifies those IDs came from the local turn candidate set, validates text
evidence for budgets, resolves live CardRefs, and rejects conflicts.
"""
from __future__ import annotations

import re
import time

from .config import CLARIFICATION_TTL_SECONDS, SEMANTIC_ATTRIBUTE_ALIASES
from .semantic_contracts import BrandCandidateSet, FactQueryObservation, PcBuildObservation, PcCompareObservation, PcEditObservation, RecommendObservation, SemanticObservation
from .types import ClarificationPlan, PriceKind, PromotionResult, RecommendationMode, RequirementSpecV3, SessionCore, TypeResolutionResult, V3Action


class HardConstraintPromotionGate:
    """The sole local admission point from semantic candidates to execution."""

    def promote(
        self,
        *,
        text: str,
        observation: SemanticObservation,
        registry,
        core: SessionCore,
        type_resolution: TypeResolutionResult | None = None,
        brand_candidates: BrandCandidateSet | None = None,
        base_requirement: RequirementSpecV3 | None = None,
    ) -> PromotionResult:
        if isinstance(observation, RecommendObservation):
            return self._recommend(text, observation, type_resolution, brand_candidates, base_requirement)
        if isinstance(observation, FactQueryObservation):
            return self._fact(observation, core)
        if isinstance(observation, PcBuildObservation):
            return self._pc_build(text, observation)
        if isinstance(observation, PcEditObservation):
            return self._pc_edit(text, observation, registry, core)
        if isinstance(observation, PcCompareObservation):
            return self._pc_compare(observation, core)
        return PromotionResult(None, None, "semantic_action_not_promotable")

    def _recommend(self, text: str, observation: RecommendObservation, type_resolution: TypeResolutionResult | None, brand_candidates: BrandCandidateSet | None, base: RequirementSpecV3 | None) -> PromotionResult:
        if type_resolution is not None and type_resolution.clarification is not None:
            return PromotionResult(None, type_resolution.clarification, type_resolution.reason_code)
        mode = observation.mode or RecommendationMode.PRODUCT
        if mode is RecommendationMode.EXPLORE:
            if observation.target_type_candidate_id or observation.target_type_surface:
                return PromotionResult(None, None, "explore_target_conflict")
            product_types = ()
            excluded_types = type_resolution.exclude_product_type_ids if type_resolution is not None else ()
            continuing = bool(base and base.recommendation_mode is RecommendationMode.EXPLORE)
        elif type_resolution is not None and type_resolution.product_type_ids:
            product_types = type_resolution.product_type_ids
            excluded_types = type_resolution.exclude_product_type_ids
            continuing = bool(base and set(product_types) == set(base.product_type_ids))
        elif observation.target_type_surface:
            return PromotionResult(None, None, "catalog_scope_unsupported")
        elif base is not None:
            product_types, excluded_types, continuing = base.product_type_ids, base.exclude_product_type_ids, True
        else:
            return _clarify("product_type", "你想让我推荐哪一类商品？例如手机、平板、咖啡或篮球鞋。", "product_type_unresolved")

        positive, negative, release, brand_error = _brand_ids(observation, brand_candidates)
        if brand_error:
            return _clarify("brand", "品牌条件无法和当前目录词表唯一对应，请换一种明确说法。", brand_error)
        inherited_excluded = set(base.exclude_brand_family_ids) if continuing and base else set()
        inherited_included = set(base.include_brand_family_ids) if continuing and base else set()
        final_excluded = tuple(sorted((inherited_excluded - set(release)) | set(negative)))
        final_included = tuple(sorted(inherited_included | set(positive)))
        if set(final_excluded) & set(final_included):
            return _clarify("brand", "同一品牌同时被要求和排除，请明确保留还是排除。", "brand_constraint_conflict")

        price_max, price_min, price_target, price_error = _promote_budget(text, observation.budget)
        if price_error:
            return _clarify("budget", "我无法可靠确认这段预算，请明确说上限、下限、目标价或区间。", price_error)
        if continuing and base and observation.budget is None:
            price_max, price_min, price_target = base.price_max, base.price_min, base.price_target
        attributes = set(base.desired_attributes) if continuing and base else set()
        attributes.update(SEMANTIC_ATTRIBUTE_ALIASES[item] for item in observation.desired_attribute_surfaces if item in SEMANTIC_ATTRIBUTE_ALIASES)
        return PromotionResult(
            RequirementSpecV3(
                action=V3Action.RECOMMEND,
                recommendation_mode=mode,
                product_type_ids=product_types,
                exclude_product_type_ids=excluded_types,
                include_brand_family_ids=final_included,
                exclude_brand_family_ids=final_excluded,
                price_max=price_max,
                price_min=price_min,
                price_target=price_target,
                desired_attributes=tuple(sorted(attributes)),
                field_provenance={
                    "product_type_ids": "type_resolution_gate" if type_resolution and type_resolution.product_type_ids else ("catalog_exploration" if mode is RecommendationMode.EXPLORE else "session_core:active_requirement"),
                    "include_brand_family_ids": "brand_candidate_gate",
                    "exclude_brand_family_ids": "brand_candidate_gate",
                    "price_max": "semantic_budget_evidence" if price_max is not None else "",
                    "price_min": "semantic_budget_evidence" if price_min is not None else "",
                    "price_target": "semantic_budget_evidence" if price_target is not None else "",
                },
            ),
            None,
            "semantic_recommendation_promoted",
        )

    def _fact(self, observation: FactQueryObservation, core: SessionCore) -> PromotionResult:
        ranks, kind = observation.card_references, observation.fact_kind
        if kind is None:
            return _clarify("fact_kind", "你想看价格、SKU、详细参数，还是比较两张商品卡？", "fact_kind_unresolved")
        if kind == "compare":
            if len(ranks) != 2 or len(set(ranks)) != 2 or any(rank > len(core.cards) for rank in ranks):
                return _clarify("card_references", "请说明要对比哪两张商品卡，例如“比较第一个和第二个”。", "comparison_card_references_unresolved")
            return PromotionResult(RequirementSpecV3(action=V3Action.PARAMETER_QUERY, target_card_ids=tuple(core.cards[rank - 1].card_id for rank in ranks), query_kind="compare", field_provenance={"target_card_ids": "semantic_card_ranks+session_cards", "query_kind": "semantic_fact_kind"}), None, "semantic_fact_requirement_promoted")
        if len(ranks) != 1 or ranks[0] > len(core.cards):
            return _clarify("card_reference", "请说明要看第几个商品卡。", "card_reference_unresolved")
        return PromotionResult(RequirementSpecV3(action=V3Action.PARAMETER_QUERY, target_card_id=core.cards[ranks[0] - 1].card_id, query_kind=kind, field_provenance={"target_card_id": "semantic_card_rank+session_card", "query_kind": "semantic_fact_kind"}), None, "semantic_fact_requirement_promoted")

    def _pc_build(self, text: str, observation: PcBuildObservation) -> PromotionResult:
        maximum, _minimum, target, error = _promote_budget(text, observation.budget)
        planning_budget = target if target is not None else maximum
        if error or planning_budget is None:
            return _clarify("budget", "装机需要明确总预算，例如“8000 元以内”。", error or "pc_budget_required")
        if not observation.usage_surfaces:
            return _clarify("pc_usage", "这台电脑主要用于游戏、办公、开发还是 AI？", "pc_usage_required")
        return PromotionResult(RequirementSpecV3(action=V3Action.PC_BUILD, price_max=maximum, price_target=target, field_provenance={"pc_planning_budget": "semantic_budget_evidence"}), None, "semantic_pc_requirement_promoted")

    def _pc_edit(self, text: str, observation: PcEditObservation, registry, core: SessionCore) -> PromotionResult:
        from .pc_target_resolver import PcPlanReferenceError, resolve_pc_plan
        try:
            previous = resolve_pc_plan(core, observation.plan_reference)
        except PcPlanReferenceError:
            return _clarify("pc_plan_reference", "请先生成一套未过期的 PC 方案，再说明要怎么修改。", "pc_plan_reference_unresolved")
        if observation.operation is None:
            return _clarify("pc_operation", "请说明是替换配件，还是调整整机预算。", "pc_operation_unresolved")
        if observation.operation.value == "replace_component":
            entity = _pc_component_entity(registry, observation.component_candidate_id)
            if entity is None or not entity.canonical_id.startswith("pc_category:"):
                return _clarify("pc_component", "请说明要替换哪一类配件，例如显卡、CPU 或内存。", "pc_component_unresolved")
            return PromotionResult(RequirementSpecV3(action=V3Action.PC_PLAN_EDIT, price_target=previous.budget, field_provenance={"pc_component": "catalog_candidate_id", "pc_plan": "session_core"}), None, f"semantic_pc_replace_promoted:{entity.canonical_id[len('pc_category:'):]}")
        maximum, _minimum, target, error = _promote_budget(text, observation.budget)
        if error or (target is None and maximum is None):
            return _clarify("budget", "请明确新的整机预算，例如“预算降到 6000”。", error or "pc_budget_required")
        return PromotionResult(RequirementSpecV3(action=V3Action.PC_PLAN_EDIT, price_max=maximum, price_target=target, field_provenance={"pc_plan": "session_core", "pc_planning_budget": "semantic_budget_evidence"}), None, "semantic_pc_budget_edit_promoted")

    def _pc_compare(self, observation: PcCompareObservation, core: SessionCore) -> PromotionResult:
        if core.pc_plans.current is None or core.pc_plans.previous is None:
            return _clarify("pc_plan_references", "请先生成或修改一套方案，再比较当前方案和上一套方案。", "pc_plan_comparison_unresolved")
        return PromotionResult(RequirementSpecV3(action=V3Action.PC_PLAN_COMPARE, field_provenance={"pc_plans": "session_core:current+previous"}), None, "semantic_pc_comparison_promoted")


def _brand_ids(observation: RecommendObservation, candidates: BrandCandidateSet | None) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], str]:
    if candidates is None:
        return (), (), (), "brand_candidates_missing"
    positive = candidates.canonical_ids(observation.positive_brand_candidate_ids)
    negative = candidates.canonical_ids(observation.negative_brand_candidate_ids)
    release = candidates.canonical_ids(observation.release_brand_candidate_ids)
    if positive is None or negative is None or release is None:
        return (), (), (), "brand_candidate_invalid"
    return positive, negative, release, ""


def _promote_budget(text: str, budget) -> tuple[float | None, float | None, float | None, str]:
    if budget is None:
        return None, None, None, ""
    evidence = _resolve_evidence(text, budget.evidence_start, budget.evidence_end, budget.evidence_text)
    if evidence is None:
        return None, None, None, "semantic_budget_evidence_unverifiable"
    amounts = _normalized_amounts(evidence)
    expected = [budget.amount] + ([budget.min_amount] if budget.min_amount is not None else [])
    if not all(any(abs(actual - value) < 0.001 for actual in amounts) for value in expected):
        return None, None, None, "semantic_budget_evidence_mismatch"
    if budget.kind is PriceKind.MAX:
        return budget.amount, None, None, ""
    if budget.kind is PriceKind.MIN:
        return None, budget.amount, None, ""
    if budget.kind is PriceKind.TARGET:
        return None, None, budget.amount, ""
    return budget.amount, budget.min_amount, None, ""


def _resolve_evidence(text: str, start: int, end: int, quoted: str) -> str | None:
    if 0 <= start < end <= len(text) and text[start:end] == quoted:
        return quoted
    if text.count(quoted) == 1:
        return quoted
    compact_quoted, compact_text = re.sub(r"\s+", "", quoted), re.sub(r"\s+", "", text)
    return compact_quoted if compact_quoted and compact_text.count(compact_quoted) == 1 else None


def _normalized_amounts(evidence: str) -> tuple[float, ...]:
    values = []
    for match in re.finditer(r"(\d+(?:[,.]\d+)?)\s*([kKwW千万元块]?)", evidence):
        value, unit = float(match.group(1).replace(",", "")), match.group(2).lower()
        values.append(value * (1000 if unit in {"k", "千"} else 10000 if unit in {"w", "万"} else 1))
    return tuple(values)


def _clarify(field: str, question: str, reason: str) -> PromotionResult:
    return PromotionResult(None, ClarificationPlan(question, (field,), time.time() + CLARIFICATION_TTL_SECONDS, reason), reason)


def _pc_component_entity(registry, candidate_id: str | None):
    """Accept the catalog's exact PC role suffix as a harmless format repair.

    The model still cannot name a part/product ID.  ``pc_gpu`` is merely the
    stable suffix of the locally owned ``pc_category:pc_gpu`` type; no fuzzy
    Chinese alias or free-form category matching is performed here.
    """

    raw = (candidate_id or "").strip()
    entity = registry.product_types.get(raw)
    if entity is None and raw and not raw.startswith("pc_category:"):
        entity = registry.product_types.get(f"pc_category:{raw}")
    return entity
