"""Own typed, short-lived V3 conversational state and its serialization.

``load_session_core``/``apply_session_delta`` are the sole adapter to the
runtime session store. Delta constructors persist only what the next turn needs:
current requirement, card references, pending clarification, cart confirmation,
and current/previous PC plans; full model/retrieval traces do not enter Redis.
"""
from __future__ import annotations

from dataclasses import asdict
import time
from typing import Any, Mapping, Optional

from .semantic_contracts import SemanticObservation, deserialize_observation, serialize_observation
from .types import CardModel, CartLine, CartOperation, CartPlan, ClarificationPlan, PcPlanHistory, PcPlanVersion, PendingClarification, RecommendationMode, RequirementSpecV3, SessionCore, SessionDelta, TopicState, V3Action


SESSION_CORE_VERSION = 5
CARD_TTL_SECONDS = 15 * 60
CART_CONFIRM_TTL_SECONDS = 60


def empty_session_core() -> SessionCore:
    return SessionCore(
        schema_version=SESSION_CORE_VERSION,
        topic=None,
        active_requirement=None,
        cards=(),
        pending_clarification=None,
        cart_lines=(),
        pending_cart_plan=None,
        pc_plans=PcPlanHistory(),
    )


def load_session_core(session: Any, *, now: Optional[float] = None) -> SessionCore:
    """Read one V3 core from the legacy transport object without trusting it."""

    payload = getattr(session, "v3_core", None)
    if not isinstance(payload, Mapping) or payload.get("schema_version") not in {1, 2, 3, 4, SESSION_CORE_VERSION}:
        return empty_session_core()
    try:
        topic_raw = payload.get("topic")
        topic = (
            TopicState(
                topic_id=str(topic_raw["topic_id"]),
                kind=str(topic_raw["kind"]),
                updated_at=float(topic_raw["updated_at"]),
            )
            if isinstance(topic_raw, Mapping)
            else None
        )
        requirement = _deserialize_requirement(payload.get("active_requirement"))
        check_at = time.time() if now is None else now
        pending = _deserialize_pending(payload.get("pending_clarification"), check_at=check_at)
        cards = tuple(
            CardModel(
                card_id=str(item["card_id"]),
                product_id=str(item["product_id"]),
                sku_ids=tuple(str(sku) for sku in item.get("sku_ids", ())),
                title=str(item["title"]),
                rank=int(item["rank"]),
                expires_at=float(item["expires_at"]),
            )
            for item in payload.get("cards", ())
            if isinstance(item, Mapping) and float(item.get("expires_at", 0)) >= check_at
        )
        cart_lines = tuple(
            CartLine(
                product_id=str(item["product_id"]),
                sku_id=str(item["sku_id"]) if item.get("sku_id") else None,
                quantity=int(item["quantity"]),
            )
            for item in payload.get("cart_lines", ())
            if isinstance(item, Mapping) and int(item.get("quantity", 0)) > 0
        )
        pending_cart_plan = _deserialize_cart_plan(payload.get("pending_cart_plan"), check_at=check_at)
        pc_plans = _deserialize_pc_plans(payload, check_at=check_at)
    except (KeyError, TypeError, ValueError):
        return empty_session_core()
    return SessionCore(
        schema_version=SESSION_CORE_VERSION,
        topic=topic,
        active_requirement=requirement,
        cards=cards,
        pending_clarification=pending,
        cart_lines=cart_lines,
        pending_cart_plan=pending_cart_plan,
        pc_plans=pc_plans,
    )


def apply_session_delta(session: Any, delta: SessionDelta) -> None:
    """Write exactly one serialized V3 core at the legacy persistence boundary."""

    session.v3_core = _serialize_core(delta.core)


def recommendation_delta(
    requirement: RequirementSpecV3,
    cards: tuple[CardModel, ...],
    *,
    previous: Optional[SessionCore] = None,
    now: Optional[float] = None,
) -> SessionDelta:
    updated_at = time.time() if now is None else now
    core = SessionCore(
        schema_version=SESSION_CORE_VERSION,
        topic=TopicState(topic_id="shopping-exploration" if requirement.recommendation_mode is RecommendationMode.EXPLORE else "shopping-recommendation", kind="exploration" if requirement.recommendation_mode is RecommendationMode.EXPLORE else "recommendation", updated_at=updated_at),
        active_requirement=requirement,
        cards=cards,
        pending_clarification=None,
        cart_lines=previous.cart_lines if previous else (),
        pending_cart_plan=previous.pending_cart_plan if previous else None,
        pc_plans=previous.pc_plans if previous else PcPlanHistory(),
    )
    return SessionDelta(core=core, reason="v3_recommendation_completed")


def fact_query_delta(core: SessionCore, *, now: Optional[float] = None) -> SessionDelta:
    updated_at = time.time() if now is None else now
    return SessionDelta(
        core=SessionCore(
            schema_version=SESSION_CORE_VERSION,
            topic=TopicState(topic_id="shopping-fact-query", kind="fact_query", updated_at=updated_at),
            active_requirement=core.active_requirement,
            cards=core.cards,
            pending_clarification=None,
            cart_lines=core.cart_lines,
            pending_cart_plan=core.pending_cart_plan,
            pc_plans=core.pc_plans,
        ),
        reason="v3_fact_query_completed",
    )


def general_chat_delta(core: SessionCore, *, now: Optional[float] = None) -> SessionDelta:
    """Close a stale pending question without discarding useful cards or cart state.

    A non-commerce interlude must not let a later bare "对" accidentally answer
    an old shopping clarification.  It intentionally retains the previous
    recommendation and cards, so a later explicit card reference still works.
    """

    updated_at = time.time() if now is None else now
    return SessionDelta(
        core=SessionCore(
            schema_version=SESSION_CORE_VERSION,
            topic=TopicState(topic_id="general-chat", kind="general_chat", updated_at=updated_at),
            active_requirement=core.active_requirement,
            cards=core.cards,
            pending_clarification=None,
            cart_lines=core.cart_lines,
            pending_cart_plan=core.pending_cart_plan,
            pc_plans=core.pc_plans,
        ),
        reason="v3_general_chat_interlude",
    )


def pc_plan_delta(core: SessionCore, plan: PcPlanVersion) -> SessionDelta:
    return SessionDelta(
        core=SessionCore(
            schema_version=SESSION_CORE_VERSION,
            topic=TopicState(topic_id="pc-build", kind="pc_build", updated_at=time.time()),
            active_requirement=core.active_requirement,
            cards=core.cards,
            pending_clarification=None,
            cart_lines=core.cart_lines,
            pending_cart_plan=core.pending_cart_plan,
            pc_plans=PcPlanHistory(current=plan, previous=core.pc_plans.current),
        ),
        reason="v3_pc_plan_completed",
    )


def _serialize_core(core: SessionCore) -> dict[str, object]:
    return {
        "schema_version": core.schema_version,
        "topic": asdict(core.topic) if core.topic else None,
        "active_requirement": _serialize_requirement(core.active_requirement),
        "cards": [asdict(card) for card in core.cards],
        "pending_clarification": _serialize_pending(core.pending_clarification),
        "cart_lines": [asdict(line) for line in core.cart_lines],
        "pending_cart_plan": _serialize_cart_plan(core.pending_cart_plan),
        "pc_plans": _serialize_pc_plans(core.pc_plans),
    }


def _serialize_requirement(requirement: Optional[RequirementSpecV3]) -> Optional[dict[str, object]]:
    if requirement is None:
        return None
    return {
        "action": requirement.action.value,
        "recommendation_mode": requirement.recommendation_mode.value,
        "product_type_ids": list(requirement.product_type_ids),
        "exclude_product_type_ids": list(requirement.exclude_product_type_ids),
        "include_brand_family_ids": list(requirement.include_brand_family_ids),
        "exclude_brand_family_ids": list(requirement.exclude_brand_family_ids),
        "price_max": requirement.price_max,
        "price_min": requirement.price_min,
        "price_target": requirement.price_target,
        "desired_attributes": list(requirement.desired_attributes),
        "target_card_id": requirement.target_card_id,
        "target_card_ids": list(requirement.target_card_ids),
        "query_kind": requirement.query_kind,
        "field_provenance": dict(requirement.field_provenance),
    }


def _deserialize_requirement(raw: object) -> Optional[RequirementSpecV3]:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("active_requirement must be an object")
    return RequirementSpecV3(
        action=V3Action(str(raw["action"])),
        recommendation_mode=RecommendationMode(str(raw.get("recommendation_mode") or RecommendationMode.PRODUCT.value)),
        product_type_ids=tuple(str(value) for value in raw.get("product_type_ids", ())),
        exclude_product_type_ids=tuple(str(value) for value in raw.get("exclude_product_type_ids", ())),
        include_brand_family_ids=tuple(str(value) for value in raw.get("include_brand_family_ids", ())),
        exclude_brand_family_ids=tuple(str(value) for value in raw.get("exclude_brand_family_ids", ())),
        price_max=float(raw["price_max"]) if raw.get("price_max") is not None else None,
        price_min=float(raw["price_min"]) if raw.get("price_min") is not None else None,
        price_target=float(raw["price_target"]) if raw.get("price_target") is not None else None,
        desired_attributes=tuple(str(value) for value in raw.get("desired_attributes", ())),
        target_card_id=str(raw["target_card_id"]) if raw.get("target_card_id") else None,
        target_card_ids=tuple(str(value) for value in raw.get("target_card_ids", ())),
        query_kind=str(raw["query_kind"]) if raw.get("query_kind") else None,
        field_provenance={str(key): str(value) for key, value in dict(raw.get("field_provenance", {})).items()},
    )


def clarification_delta(core: SessionCore, *, plan: ClarificationPlan, observation: SemanticObservation, source_text: str) -> SessionDelta:
    pending = PendingClarification(plan=plan, observation=observation, source_text=source_text)
    return SessionDelta(
        core=SessionCore(
            schema_version=SESSION_CORE_VERSION,
            topic=TopicState(topic_id="shopping-clarification", kind="clarification", updated_at=time.time()),
            active_requirement=core.active_requirement,
            cards=core.cards,
            pending_clarification=pending,
            cart_lines=core.cart_lines,
            pending_cart_plan=core.pending_cart_plan,
            pc_plans=core.pc_plans,
        ),
        reason="v3_clarification_requested",
    )


def _serialize_pending(pending: Optional[PendingClarification]) -> Optional[dict[str, object]]:
    if pending is None:
        return None
    return {
        "plan": asdict(pending.plan),
        "observation": serialize_observation(pending.observation),
        "source_text": pending.source_text,
    }


def _deserialize_pending(raw: object, *, check_at: float) -> Optional[PendingClarification]:
    if not isinstance(raw, Mapping):
        return None
    plan_raw = raw.get("plan")
    observation_raw = raw.get("observation")
    if not isinstance(plan_raw, Mapping) or not isinstance(observation_raw, Mapping):
        return None
    plan = ClarificationPlan(
        question=str(plan_raw["question"]),
        missing_fields=tuple(str(value) for value in plan_raw.get("missing_fields", ())),
        expires_at=float(plan_raw["expires_at"]),
        reason_code=str(plan_raw["reason_code"]),
    )
    if plan.expires_at < check_at:
        return None
    observation = deserialize_observation(observation_raw)
    if observation is None:
        return None
    return PendingClarification(plan=plan, observation=observation, source_text=str(raw.get("source_text") or ""))




def _serialize_cart_plan(plan: Optional[CartPlan]) -> Optional[dict[str, object]]:
    if plan is None:
        return None
    return {
        "plan_id": plan.plan_id,
        "operation": plan.operation.value,
        "product_id": plan.product_id,
        "sku_id": plan.sku_id,
        "quantity": plan.quantity,
        "expires_at": plan.expires_at,
        "title": plan.title,
        "unit_price": plan.unit_price,
    }


def _deserialize_cart_plan(raw: object, *, check_at: float) -> Optional[CartPlan]:
    if not isinstance(raw, Mapping):
        return None
    plan = CartPlan(
        plan_id=str(raw["plan_id"]),
        operation=CartOperation(str(raw["operation"])),
        product_id=str(raw["product_id"]) if raw.get("product_id") else None,
        sku_id=str(raw["sku_id"]) if raw.get("sku_id") else None,
        quantity=int(raw["quantity"]) if raw.get("quantity") is not None else None,
        expires_at=float(raw["expires_at"]),
        title=str(raw.get("title") or ""),
        unit_price=float(raw["unit_price"]) if raw.get("unit_price") is not None else None,
    )
    return plan if plan.expires_at >= check_at else None


def _serialize_pc_plan(plan: Optional[PcPlanVersion]) -> Optional[dict[str, object]]:
    if plan is None:
        return None
    return {
        "plan_id": plan.plan_id,
        "revision": plan.revision,
        "budget": plan.budget,
        "part_product_ids": list(plan.part_product_ids),
        "usage": list(plan.usage),
        "parent_plan_id": plan.parent_plan_id,
        "expires_at": plan.expires_at,
    }


def _deserialize_pc_plan(raw: object, *, check_at: float, legacy: bool = False) -> Optional[PcPlanVersion]:
    if not isinstance(raw, Mapping):
        return None
    plan = PcPlanVersion(
        plan_id=str(raw["plan_id"]),
        revision=int(raw.get("revision") or 1),
        budget=float(raw["budget"]),
        part_product_ids=tuple(str(value) for value in raw.get("part_product_ids", ())),
        usage=tuple(str(value) for value in raw.get("usage", ())),
        parent_plan_id=None if legacy else (str(raw["parent_plan_id"]) if raw.get("parent_plan_id") else None),
        expires_at=float(raw["expires_at"]),
    )
    return plan if plan.expires_at >= check_at else None


def _serialize_pc_plans(plans: PcPlanHistory) -> dict[str, object]:
    return {"current": _serialize_pc_plan(plans.current), "previous": _serialize_pc_plan(plans.previous)}


def _deserialize_pc_plans(payload: Mapping[str, Any], *, check_at: float) -> PcPlanHistory:
    raw = payload.get("pc_plans")
    if isinstance(raw, Mapping):
        return PcPlanHistory(
            current=_deserialize_pc_plan(raw.get("current"), check_at=check_at),
            previous=_deserialize_pc_plan(raw.get("previous"), check_at=check_at),
        )
    # Schema v1 carried only pc_plan.  Read it once as the current version;
    # the next write uses the v2 pc_plans payload exclusively.
    return PcPlanHistory(current=_deserialize_pc_plan(payload.get("pc_plan"), check_at=check_at, legacy=True))
