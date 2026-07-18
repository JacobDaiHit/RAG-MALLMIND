"""Action-specific, untrusted outputs accepted from the one SemanticParse call.

The external model never returns one giant optional-field object.  It returns
exactly one of these small observations, selected by ``action``.  This keeps a
card price question from carrying PC fields, and makes a missing fact kind a
visible contract failure instead of an accidentally ignored optional field.

These objects contain user-language candidates and ordinal references only.
Catalog IDs, SKU IDs, prices from the catalog, CardRef tokens, and side effects
remain outside the model boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Union

from .types import CartOperation, CartTargetRef, CartTargetSource, ComputerPurchaseKind, PcPlanOperation, PcPlanReference, PriceConstraint, PurchaseKindEvidence, RecommendationMode, TypeSurfaceEvidence, V3Action


@dataclass(frozen=True)
class RecommendObservation:
    action: V3Action = field(default=V3Action.RECOMMEND, init=False)
    mode: RecommendationMode | None = None
    target_type_surface: str | None = None
    target_type_candidate_id: str | None = None
    target_type_evidence: TypeSurfaceEvidence | None = None
    exclude_type_candidate_ids: tuple[str, ...] = ()
    positive_brand_candidate_ids: tuple[str, ...] = ()
    negative_brand_candidate_ids: tuple[str, ...] = ()
    release_brand_candidate_ids: tuple[str, ...] = ()
    budget: PriceConstraint | None = None
    desired_attribute_surfaces: tuple[str, ...] = ()
    computer_purchase_kind: ComputerPurchaseKind | None = None
    computer_purchase_evidence: PurchaseKindEvidence | None = None
    pc_usage_surfaces: tuple[str, ...] = ()


@dataclass(frozen=True)
class FactQueryObservation:
    action: V3Action = field(default=V3Action.PARAMETER_QUERY, init=False)
    card_references: tuple[int, ...] = ()
    fact_kind: str | None = None


@dataclass(frozen=True)
class CartObservation:
    action: V3Action = field(default=V3Action.APPLY_CART, init=False)
    operation: CartOperation | None = None
    target_ref: CartTargetRef | None = None
    quantity: int | None = None


@dataclass(frozen=True)
class PcBuildObservation:
    action: V3Action = field(default=V3Action.PC_BUILD, init=False)
    budget: PriceConstraint | None = None
    usage_surfaces: tuple[str, ...] = ()
    computer_purchase_evidence: PurchaseKindEvidence | None = None


@dataclass(frozen=True)
class PcEditObservation:
    action: V3Action = field(default=V3Action.PC_PLAN_EDIT, init=False)
    operation: PcPlanOperation | None = None
    plan_reference: PcPlanReference | None = None
    component_candidate_id: str | None = None
    upgrade_direction: str | None = None
    budget: PriceConstraint | None = None


@dataclass(frozen=True)
class PcCompareObservation:
    action: V3Action = field(default=V3Action.PC_PLAN_COMPARE, init=False)
    plan_reference: PcPlanReference | None = None


@dataclass(frozen=True)
class GeneralChatObservation:
    action: V3Action = field(default=V3Action.GENERAL_CHAT, init=False)


SemanticObservation = Union[
    RecommendObservation,
    FactQueryObservation,
    CartObservation,
    PcBuildObservation,
    PcEditObservation,
    PcCompareObservation,
    GeneralChatObservation,
]

PcObservation = Union[PcBuildObservation, PcEditObservation, PcCompareObservation]


@dataclass(frozen=True)
class BrandCandidate:
    """One registry-backed brand mentioned in this turn, without model facts."""

    candidate_id: str
    canonical_brand_id: str
    display_name: str
    spans: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class BrandCandidateSet:
    registry_version: str
    candidates: tuple[BrandCandidate, ...]

    def canonical_ids(self, candidate_ids: tuple[str, ...]) -> tuple[str, ...] | None:
        by_id = {item.candidate_id: item.canonical_brand_id for item in self.candidates}
        if any(item not in by_id for item in candidate_ids):
            return None
        return tuple(sorted(set(by_id[item] for item in candidate_ids)))


@dataclass(frozen=True)
class SemanticContext:
    """Small local state supplied to the single model call for turn linking."""

    active_product_type_ids: tuple[str, ...] = ()
    active_excluded_brand_ids: tuple[str, ...] = ()
    live_card_count: int = 0
    cart_line_count: int = 0
    has_current_pc_plan: bool = False
    has_previous_pc_plan: bool = False
    pending_action: str | None = None
    pending_missing_fields: tuple[str, ...] = ()


def build_brand_candidate_set(*, text: str, registry) -> BrandCandidateSet:
    """Extract only exact registry aliases present in this user turn.

    The model selects polarity among these IDs; this local code does not try to
    infer Chinese negation or preference scope.  A candidate therefore proves
    the brand was named, while semantic polarity remains the model's job.
    """

    grouped: dict[str, BrandCandidate] = {}
    lowered = text.casefold()
    for alias, entity in registry.brand_aliases():
        token = str(alias).strip()
        if not token:
            continue
        spans: list[tuple[int, int]] = []
        start = lowered.find(token.casefold())
        while start >= 0:
            spans.append((start, start + len(token)))
            start = lowered.find(token.casefold(), start + len(token))
        if not spans:
            continue
        candidate_id = f"brand:{entity.canonical_id}"
        previous = grouped.get(candidate_id)
        merged = tuple(sorted(set((previous.spans if previous else ()) + tuple(spans))))
        grouped[candidate_id] = BrandCandidate(candidate_id, entity.canonical_id, entity.display_name, merged)
    return BrandCandidateSet(registry.version, tuple(sorted(grouped.values(), key=lambda item: item.candidate_id)))


def render_brand_candidates(candidate_set: BrandCandidateSet) -> str:
    if not candidate_set.candidates:
        return "品牌候选：无；三个品牌候选数组必须为空。"
    rows = ["品牌候选（只能选择下列 ID；未出现的品牌不得编造）："]
    rows.extend(f"- {item.candidate_id} | {item.display_name}" for item in candidate_set.candidates)
    return "\n".join(rows)


def serialize_observation(observation: SemanticObservation) -> dict[str, object]:
    """Persist a typed pending clarification without an unbounded raw dict."""

    data: dict[str, object] = {"action": observation.action.value}
    if isinstance(observation, RecommendObservation):
        data.update({
            "mode": observation.mode.value if observation.mode else None,
            "target_type_surface": observation.target_type_surface,
            "target_type_candidate_id": observation.target_type_candidate_id,
            "target_type_evidence": _serialize_type_evidence(observation.target_type_evidence),
            "exclude_type_candidate_ids": list(observation.exclude_type_candidate_ids),
            "positive_brand_candidate_ids": list(observation.positive_brand_candidate_ids),
            "negative_brand_candidate_ids": list(observation.negative_brand_candidate_ids),
            "release_brand_candidate_ids": list(observation.release_brand_candidate_ids),
            "budget": _serialize_budget(observation.budget),
            "desired_attribute_surfaces": list(observation.desired_attribute_surfaces),
            "computer_purchase_kind": observation.computer_purchase_kind.value if observation.computer_purchase_kind else None,
            "computer_purchase_evidence": _serialize_purchase_evidence(observation.computer_purchase_evidence),
            "pc_usage_surfaces": list(observation.pc_usage_surfaces),
        })
    elif isinstance(observation, FactQueryObservation):
        data.update({"card_references": list(observation.card_references), "fact_kind": observation.fact_kind})
    elif isinstance(observation, CartObservation):
        data.update({"operation": observation.operation.value if observation.operation else None, "target_ref": _serialize_cart_target_ref(observation.target_ref), "quantity": observation.quantity})
    elif isinstance(observation, PcBuildObservation):
        data.update({"budget": _serialize_budget(observation.budget), "usage_surfaces": list(observation.usage_surfaces), "computer_purchase_evidence": _serialize_purchase_evidence(observation.computer_purchase_evidence)})
    elif isinstance(observation, PcEditObservation):
        data.update({"operation": observation.operation.value if observation.operation else None, "plan_reference": observation.plan_reference.value if observation.plan_reference else None, "component_candidate_id": observation.component_candidate_id, "upgrade_direction": observation.upgrade_direction, "budget": _serialize_budget(observation.budget)})
    elif isinstance(observation, PcCompareObservation):
        data["plan_reference"] = observation.plan_reference.value if observation.plan_reference else None
    return data


def deserialize_observation(raw: Mapping[str, object]) -> SemanticObservation | None:
    """Restore only a complete known action variant; malformed state expires."""

    try:
        action = V3Action(str(raw["action"]))
        if action is V3Action.RECOMMEND:
            return RecommendObservation(
                mode=_enum_or_none(RecommendationMode, raw.get("mode")),
                target_type_surface=_text(raw.get("target_type_surface")),
                target_type_candidate_id=_text(raw.get("target_type_candidate_id")),
                target_type_evidence=_deserialize_type_evidence(raw.get("target_type_evidence")),
                exclude_type_candidate_ids=_strings(raw.get("exclude_type_candidate_ids")),
                positive_brand_candidate_ids=_strings(raw.get("positive_brand_candidate_ids")),
                negative_brand_candidate_ids=_strings(raw.get("negative_brand_candidate_ids")),
                release_brand_candidate_ids=_strings(raw.get("release_brand_candidate_ids")),
                budget=_deserialize_budget(raw.get("budget")),
                desired_attribute_surfaces=_strings(raw.get("desired_attribute_surfaces")),
                computer_purchase_kind=_enum_or_none(ComputerPurchaseKind, raw.get("computer_purchase_kind")),
                computer_purchase_evidence=_deserialize_purchase_evidence(raw.get("computer_purchase_evidence")),
                pc_usage_surfaces=_strings(raw.get("pc_usage_surfaces")),
            )
        if action is V3Action.PARAMETER_QUERY:
            return FactQueryObservation(card_references=_positive_ints(raw.get("card_references")), fact_kind=_text(raw.get("fact_kind")))
        if action is V3Action.APPLY_CART:
            return CartObservation(operation=_enum_or_none(CartOperation, raw.get("operation")), target_ref=_deserialize_cart_target_ref(raw.get("target_ref")), quantity=_positive_int(raw.get("quantity")))
        if action is V3Action.PC_BUILD:
            return PcBuildObservation(budget=_deserialize_budget(raw.get("budget")), usage_surfaces=_strings(raw.get("usage_surfaces")), computer_purchase_evidence=_deserialize_purchase_evidence(raw.get("computer_purchase_evidence")))
        if action is V3Action.PC_PLAN_EDIT:
            return PcEditObservation(operation=_enum_or_none(PcPlanOperation, raw.get("operation")), plan_reference=_enum_or_none(PcPlanReference, raw.get("plan_reference")), component_candidate_id=_text(raw.get("component_candidate_id")), upgrade_direction=_text(raw.get("upgrade_direction")), budget=_deserialize_budget(raw.get("budget")))
        if action is V3Action.PC_PLAN_COMPARE:
            return PcCompareObservation(plan_reference=_enum_or_none(PcPlanReference, raw.get("plan_reference")))
        if action is V3Action.GENERAL_CHAT:
            return GeneralChatObservation()
    except (KeyError, TypeError, ValueError):
        return None
    return None


def _serialize_type_evidence(value: TypeSurfaceEvidence | None) -> dict[str, object] | None:
    return None if value is None else {"surface": value.surface, "evidence_start": value.evidence_start, "evidence_end": value.evidence_end, "evidence_text": value.evidence_text}


def _deserialize_type_evidence(value: object) -> TypeSurfaceEvidence | None:
    if not isinstance(value, Mapping):
        return None
    try:
        surface, start, end, evidence = str(value["surface"]), int(value["evidence_start"]), int(value["evidence_end"]), str(value["evidence_text"])
    except (KeyError, TypeError, ValueError):
        return None
    return TypeSurfaceEvidence(surface, start, end, evidence) if surface and evidence and start >= 0 and end > start else None


def _serialize_purchase_evidence(value: PurchaseKindEvidence | None) -> dict[str, object] | None:
    return None if value is None else {"surface": value.surface, "evidence_start": value.evidence_start, "evidence_end": value.evidence_end, "evidence_text": value.evidence_text}


def _serialize_cart_target_ref(value: CartTargetRef | None) -> dict[str, object] | None:
    return None if value is None else {"source": value.source.value, "rank": value.rank}


def _deserialize_cart_target_ref(value: object) -> CartTargetRef | None:
    if not isinstance(value, Mapping):
        return None
    source = _enum_or_none(CartTargetSource, value.get("source"))
    rank = _positive_int(value.get("rank"))
    return CartTargetRef(source, rank) if source is not None and rank is not None else None


def _deserialize_purchase_evidence(value: object) -> PurchaseKindEvidence | None:
    if not isinstance(value, Mapping):
        return None
    try:
        surface, start, end, evidence = str(value["surface"]), int(value["evidence_start"]), int(value["evidence_end"]), str(value["evidence_text"])
    except (KeyError, TypeError, ValueError):
        return None
    return PurchaseKindEvidence(surface, start, end, evidence) if surface and evidence and start >= 0 and end > start else None


def _serialize_budget(value: PriceConstraint | None) -> dict[str, object] | None:
    return None if value is None else {"kind": value.kind.value, "amount": value.amount, "min_amount": value.min_amount, "currency": value.currency, "evidence_start": value.evidence_start, "evidence_end": value.evidence_end, "evidence_text": value.evidence_text}


def _deserialize_budget(value: object) -> PriceConstraint | None:
    if not isinstance(value, Mapping):
        return None
    try:
        from .types import PriceKind
        return PriceConstraint(PriceKind(str(value["kind"])), float(value["amount"]), float(value["min_amount"]) if value.get("min_amount") is not None else None, str(value["currency"]), int(value["evidence_start"]), int(value["evidence_end"]), str(value["evidence_text"]))
    except (KeyError, TypeError, ValueError):
        return None


def _strings(value: object) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip())) if isinstance(value, (list, tuple)) else ()


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return parsed if parsed is not None and parsed > 0 else None


def _positive_ints(value: object) -> tuple[int, ...]:
    return tuple(dict.fromkeys(item for item in (_positive_int(raw) for raw in value) if item is not None)) if isinstance(value, (list, tuple)) else ()


def _enum_or_none(enum_type, value: object):
    try:
        return enum_type(str(value)) if value else None
    except ValueError:
        return None


def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
