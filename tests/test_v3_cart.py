"""V3 cart invariants: semantic text never owns catalog IDs or side effects."""
from __future__ import annotations

import pytest

from rag.recommendation.product_loader import load_product_catalog
from rag.recommendation.v3.cart import CartPlanningError, apply_cart_plan, cart_plan_delta, cart_snapshot, create_cart_plan
from rag.recommendation.v3.session import apply_session_delta, empty_session_core, load_session_core, recommendation_delta
from rag.recommendation.v3.types import CardModel, CartOperation, RequirementSpecV3, SemanticObservation, V3Action


def _core_with_card(now: float = 100.0):
    catalog = load_product_catalog()
    product = catalog.get("p_digital_016")
    card = CardModel("card-phone", product.product_id, (), product.title, 1, now + 900)
    return recommendation_delta(
        requirement=RequirementSpecV3(
            action=V3Action.RECOMMEND,
            product_type_ids=("phone",),
        ),
        cards=(card,),
        now=now,
    ).core


def test_cart_add_is_plan_then_confirm_and_uses_card_catalog_reference():
    catalog = load_product_catalog()
    core = _core_with_card()
    observation = SemanticObservation(
        action=V3Action.APPLY_CART,
        cart_operation=CartOperation.ADD,
        target_card_rank=1,
        quantity=2,
    )
    plan = create_cart_plan(core=core, observation=observation, catalog=catalog, now=100.0)
    assert plan is not None
    assert plan.product_id == "p_digital_016"
    assert not core.cart_lines
    delta, result = apply_cart_plan(core=cart_plan_delta(core, plan).core, catalog=catalog, confirmed=True, now=101.0)
    assert result["status"] == "applied"
    assert result["cart"]["items"][0]["quantity"] == 2
    assert delta.core.cart_lines[0].product_id == "p_digital_016"


def test_cart_cancel_and_expiry_cannot_apply_a_plan():
    catalog = load_product_catalog()
    core = _core_with_card()
    observation = SemanticObservation(
        action=V3Action.APPLY_CART,
        cart_operation=CartOperation.ADD,
        target_card_rank=1,
    )
    plan = create_cart_plan(core=core, observation=observation, catalog=catalog, now=100.0)
    planned = cart_plan_delta(core, plan).core
    cancelled, result = apply_cart_plan(core=planned, catalog=catalog, confirmed=False, now=101.0)
    assert result["status"] == "cancelled"
    assert not cancelled.core.cart_lines
    with pytest.raises(CartPlanningError, match="过期"):
        apply_cart_plan(core=planned, catalog=catalog, confirmed=True, now=161.0)


def test_cart_target_must_be_a_live_card_or_live_cart_line():
    with pytest.raises(CartPlanningError, match="第几个商品卡"):
        create_cart_plan(
            core=empty_session_core(),
            observation=SemanticObservation(
                action=V3Action.APPLY_CART,
                cart_operation=CartOperation.ADD,
                target_card_rank=1,
            ),
            catalog=load_product_catalog(),
        )


def test_cart_state_round_trips_only_through_v3_core():
    class Session:
        v3_core = {}

    catalog = load_product_catalog()
    core = _core_with_card()
    plan = create_cart_plan(
        core=core,
        observation=SemanticObservation(
            action=V3Action.APPLY_CART,
            cart_operation=CartOperation.ADD,
            target_card_rank=1,
        ),
        catalog=catalog,
        now=100.0,
    )
    applied, _ = apply_cart_plan(core=cart_plan_delta(core, plan).core, catalog=catalog, confirmed=True, now=101.0)
    session = Session()
    apply_session_delta(session, applied)
    restored = load_session_core(session, now=102.0)
    assert cart_snapshot(restored, catalog)["items"][0]["product_id"] == "p_digital_016"
