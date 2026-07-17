"""Local promotion gate from untrusted semantic observations to V3 contracts."""
from __future__ import annotations

import re
import time

from .config import (
    CLARIFICATION_TTL_SECONDS,
    EXPLICIT_EXCLUDE_TEMPLATES,
    SEMANTIC_ATTRIBUTE_ALIASES,
)
from .registry import CatalogNormalizationRegistry
from .pc_target_resolver import PcPlanReferenceError, resolve_pc_plan
from .types import (
    ClarificationPlan,
    PromotionResult,
    PriceKind,
    PcPlanOperation,
    RequirementSpecV3,
    SemanticObservation,
    SessionCore,
    TypeResolutionResult,
    V3Action,
)


class HardConstraintPromotionGate:
    """The only module allowed to turn semantic hints into hard constraints."""

    def promote(
        self,
        *,
        text: str,
        observation: SemanticObservation,
        registry: CatalogNormalizationRegistry,
        core: SessionCore,
        type_resolution: TypeResolutionResult | None = None,
    ) -> PromotionResult:
        if observation.action is V3Action.RECOMMEND:
            return self._recommend(text, observation, registry, type_resolution)
        if observation.action is V3Action.PARAMETER_QUERY:
            return self._fact(observation, core)
        if observation.action is V3Action.PC_BUILD:
            return self._pc_build(text, observation)
        if observation.action is V3Action.PC_PLAN_EDIT:
            return self._pc_edit(text, observation, registry, core)
        if observation.action is V3Action.PC_PLAN_COMPARE:
            return self._pc_compare(observation, core)
        return PromotionResult(None, None, "semantic_action_not_migrated")

    def _recommend(
        self,
        text: str,
        observation: SemanticObservation,
        registry: CatalogNormalizationRegistry,
        type_resolution: TypeResolutionResult | None,
    ) -> PromotionResult:
        if type_resolution is None or not type_resolution.product_type_ids:
            return PromotionResult(None, None, "type_resolution_missing")
        if observation.price_constraint is None and "price" in observation.missing_fields:
            return _clarify("price", "我无法确定这几个金额分别代表上限、目标还是区间，请明确预算范围。", "semantic_price_ambiguous")
        price_max, price_min, price_target, price_reason = _promote_price_constraint(text, observation)
        if price_reason:
            return _clarify("price_max", "我没有可靠确认预算上限，请明确说例如“3000 元以内”。", price_reason)
        excluded = _promote_exclusions(text, observation.exclude_brand_surfaces, registry)
        included = _promote_inclusions(text, observation.include_brand_surfaces, registry)
        if set(excluded) & set(included):
            return _clarify("brand", "同一品牌同时被要求和排除，请明确保留还是排除。", "brand_constraint_conflict")
        attributes = tuple(sorted({SEMANTIC_ATTRIBUTE_ALIASES[item] for item in observation.desired_attribute_surfaces if item in SEMANTIC_ATTRIBUTE_ALIASES}))
        return PromotionResult(
            RequirementSpecV3(
                action=V3Action.RECOMMEND,
                product_type_ids=type_resolution.product_type_ids,
                exclude_product_type_ids=type_resolution.exclude_product_type_ids,
                include_brand_family_ids=included,
                exclude_brand_family_ids=excluded,
                price_max=price_max,
                price_min=price_min,
                price_target=price_target,
                desired_attributes=attributes,
                field_provenance={
                    "product_type_ids": "type_resolution_gate",
                    "exclude_product_type_ids": "type_resolution_gate",
                    "include_brand_family_ids": "promotion_gate:explicit_include",
                    "exclude_brand_family_ids": "promotion_gate:explicit_exclude",
                    "price_max": "promotion_gate:semantic_price_evidence" if price_max is not None else "",
                    "price_min": "promotion_gate:semantic_price_evidence" if price_min is not None else "",
                    "price_target": "promotion_gate:semantic_price_evidence" if price_target is not None else "",
                    "desired_attributes": "semantic_parse:soft_preference",
                },
            ),
            None,
            "semantic_requirement_promoted",
        )

    def _fact(self, observation: SemanticObservation, core: SessionCore) -> PromotionResult:
        if observation.query_kind == "compare":
            ranks = observation.target_card_ranks
            if len(ranks) != 2 or len(set(ranks)) != 2 or any(rank > len(core.cards) for rank in ranks):
                return _clarify("card_references", "请说明要对比哪两张商品卡，例如“比较第一个和第二个”。", "comparison_card_references_unresolved")
            cards = tuple(core.cards[rank - 1] for rank in ranks)
            return PromotionResult(
                RequirementSpecV3(
                    action=V3Action.PARAMETER_QUERY,
                    target_card_ids=tuple(card.card_id for card in cards),
                    query_kind="compare",
                    field_provenance={"target_card_ids": "semantic_parse+session_cards", "query_kind": "semantic_parse"},
                ),
                None,
                "semantic_comparison_requirement_promoted",
            )
        rank = observation.target_card_rank
        if rank is None or rank > len(core.cards) or not observation.query_kind:
            return _clarify("card_reference", "请说明要看第几个商品卡，以及想看参数、SKU 还是价格。", "card_reference_unresolved")
        card = core.cards[rank - 1]
        return PromotionResult(
            RequirementSpecV3(
                action=V3Action.PARAMETER_QUERY,
                target_card_id=card.card_id,
                query_kind=observation.query_kind,
                field_provenance={"target_card_id": "semantic_parse+session_card", "query_kind": "semantic_parse"},
            ),
            None,
            "semantic_fact_requirement_promoted",
        )

    def _pc_build(self, text: str, observation: SemanticObservation) -> PromotionResult:
        maximum, _minimum, target, reason = _promote_price_constraint(text, observation)
        planning_budget = target if target is not None else maximum
        if reason or planning_budget is None:
            return _clarify("budget", "装机需要一个明确总预算，例如“8000 元以内”。", reason or "pc_budget_required")
        if not observation.pc_usage_surfaces:
            return _clarify("pc_usage", "这台电脑主要用于游戏、办公、开发还是 AI？", "pc_usage_required")
        return PromotionResult(
            RequirementSpecV3(
                action=V3Action.PC_BUILD,
                price_max=maximum,
                price_target=target,
                field_provenance={
                    "price_max": "promotion_gate:semantic_price_evidence" if maximum is not None else "",
                    "price_target": "promotion_gate:semantic_price_evidence" if target is not None else "",
                    "pc_planning_budget": "price_target" if target is not None else "price_max",
                },
            ),
            None,
            "semantic_pc_requirement_promoted",
        )

    def _pc_edit(
        self,
        text: str,
        observation: SemanticObservation,
        registry: CatalogNormalizationRegistry,
        core: SessionCore,
    ) -> PromotionResult:
        try:
            previous = resolve_pc_plan(core, observation.pc_plan_reference)
        except PcPlanReferenceError:
            return _clarify("pc_plan_reference", "请先生成一套未过期的 PC 方案，再说明要怎么修改。", "pc_plan_reference_unresolved")
        operation = observation.pc_operation
        if operation is PcPlanOperation.REPLACE_COMPONENT:
            entity = registry.product_type_by_surface(observation.pc_component_category_surface or "")
            if entity is None or not entity.canonical_id.startswith("pc_category:"):
                return _clarify("pc_component", "请说明要替换哪一类配件，例如显卡、CPU 或内存。", "pc_component_unresolved")
            component_id = entity.canonical_id[len("pc_category:"):]
            return PromotionResult(
                RequirementSpecV3(
                    action=V3Action.PC_PLAN_EDIT,
                    price_target=previous.budget,
                    field_provenance={
                        "pc_plan": "session_core:current_or_previous",
                        "pc_component": "semantic_parse+registry",
                        "pc_planning_budget": "session_core:previous_budget",
                    },
                ),
                None,
                f"semantic_pc_replace_promoted:{component_id}",
            )
        if operation is PcPlanOperation.ADJUST_BUDGET:
            maximum, _minimum, target, reason = _promote_price_constraint(text, observation)
            budget = target if target is not None else maximum
            if reason or budget is None:
                return _clarify("budget", "请明确新的整机预算，例如“预算降到 6000”。", reason or "pc_budget_required")
            return PromotionResult(
                RequirementSpecV3(
                    action=V3Action.PC_PLAN_EDIT,
                    price_max=maximum,
                    price_target=target,
                    field_provenance={"pc_plan": "session_core:current_or_previous", "pc_planning_budget": "promotion_gate:semantic_price_evidence"},
                ),
                None,
                "semantic_pc_budget_edit_promoted",
            )
        return _clarify("pc_operation", "请说明是替换某个配件，还是调整整机预算。", "pc_operation_unresolved")

    def _pc_compare(self, observation: SemanticObservation, core: SessionCore) -> PromotionResult:
        try:
            resolve_pc_plan(core, observation.pc_plan_reference)
        except PcPlanReferenceError:
            return _clarify("pc_plan_reference", "当前没有可比较的 PC 方案，请先生成方案。", "pc_plan_reference_unresolved")
        if core.pc_plans.current is None or core.pc_plans.previous is None:
            return _clarify("pc_plan_references", "请先生成或修改一套方案，再比较当前方案和上一套方案。", "pc_plan_comparison_unresolved")
        return PromotionResult(
            RequirementSpecV3(action=V3Action.PC_PLAN_COMPARE, field_provenance={"pc_plans": "session_core:current+previous"}),
            None,
            "semantic_pc_comparison_promoted",
        )


def _promote_price_constraint(text: str, observation: SemanticObservation) -> tuple[float | None, float | None, float | None, str]:
    """Validate LLM-provided price evidence without enumerating Chinese grammar.

    Local code validates the quoted source span and normalized number only.  It
    does not try to infer whether “预算 1000” means a maximum; that semantic
    classification is the semantic parser's constrained responsibility.
    """

    constraint = observation.price_constraint
    if constraint is None:
        return None, None, None, ""
    evidence = _resolve_evidence(text, constraint.evidence_start, constraint.evidence_end, constraint.evidence_text)
    if evidence is None:
        return None, None, None, "semantic_price_evidence_unverifiable"
    amounts = _normalized_amounts(evidence)
    expected = [constraint.amount] + ([constraint.min_amount] if constraint.min_amount is not None else [])
    if not all(any(abs(actual - value) < 0.001 for actual in amounts) for value in expected):
        return None, None, None, "semantic_price_evidence_mismatch"
    if constraint.kind is PriceKind.MAX:
        return constraint.amount, None, None, ""
    if constraint.kind is PriceKind.TARGET:
        return None, None, constraint.amount, ""
    return constraint.amount, constraint.min_amount, None, ""


def _resolve_evidence(text: str, start: int, end: int, quoted: str) -> str | None:
    if 0 <= start < end <= len(text) and text[start:end] == quoted:
        return quoted
    positions = []
    cursor = text.find(quoted)
    while cursor >= 0:
        positions.append(cursor)
        cursor = text.find(quoted, cursor + 1)
    if len(positions) == 1:
        return quoted
    # Model tokenization may remove a space in "5000 元以内".  This remains
    # fail-closed: the whitespace-normalized quotation must occur exactly once
    # in the whitespace-normalized source; no numeric or wording rewrite is
    # accepted here.
    compact_quoted = re.sub(r"\s+", "", quoted)
    compact_text = re.sub(r"\s+", "", text)
    if compact_quoted and compact_text.count(compact_quoted) == 1:
        return compact_quoted
    return None


def _normalized_amounts(evidence: str) -> tuple[float, ...]:
    import re

    values = []
    for match in re.finditer(r"(\d+(?:[,.]\d+)?)\s*([kKwW千万元块]?)", evidence):
        number = float(match.group(1).replace(",", ""))
        unit = match.group(2).lower()
        values.append(number * (1000 if unit in {"k", "千"} else 10000 if unit in {"w", "万"} else 1))
    return tuple(values)


def _promote_exclusions(text: str, surfaces: tuple[str, ...], registry: CatalogNormalizationRegistry) -> tuple[str, ...]:
    promoted = []
    for surface in surfaces:
        entity = registry.brand_by_surface(surface)
        if entity is not None and _has_explicit_exclusion(text, entity.aliases):
            promoted.append(entity.canonical_id)
    return tuple(sorted(set(promoted)))


def _promote_inclusions(text: str, surfaces: tuple[str, ...], registry: CatalogNormalizationRegistry) -> tuple[str, ...]:
    promoted = []
    for surface in surfaces:
        entity = registry.brand_by_surface(surface)
        if entity is None:
            continue
        if any(token in text for alias in entity.aliases for token in (f"要{alias}", f"只要{alias}", f"{alias}也可以")):
            promoted.append(entity.canonical_id)
    return tuple(sorted(set(promoted)))


def _has_explicit_exclusion(text: str, aliases: tuple[str, ...]) -> bool:
    for alias in aliases:
        if any(template.format(brand=alias) in text for template in EXPLICIT_EXCLUDE_TEMPLATES):
            return True
        if re.search(re.escape(alias) + r".{0,8}(?:不.*好|不喜欢|不适合|别推荐|不要)", text):
            return True
    return False


def _clarify(field: str, question: str, reason: str) -> PromotionResult:
    return PromotionResult(
        None,
        ClarificationPlan(question=question, missing_fields=(field,), expires_at=time.time() + CLARIFICATION_TTL_SECONDS, reason_code=reason),
        reason,
    )
