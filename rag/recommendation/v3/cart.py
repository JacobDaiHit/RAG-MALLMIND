"""Create and apply typed, confirmation-gated shopping-cart plans.

``create_cart_plan`` resolves only valid SessionCore card/cart references;
``apply_cart_plan`` performs the real mutation after confirmation; delta and
snapshot helpers keep state serialization out of the HTTP route. This module
never parses language or calls a model.
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import time
from typing import Any

from .session import CART_CONFIRM_TTL_SECONDS
from .types import CartLine, CartOperation, CartPlan, SemanticObservation, SessionCore, SessionDelta


class CartPlanningError(ValueError):
    """A user-visible cart request lacks one safely resolvable field."""


def create_cart_plan(*, core: SessionCore, observation: SemanticObservation, catalog: Any, now: float | None = None) -> CartPlan | None:
    """Create a catalog-validated proposal; it never mutates the cart."""

    operation = observation.cart_operation
    if operation is None:
        raise CartPlanningError("请说明要加入、删除、修改数量、查看还是清空购物车。")
    if operation is CartOperation.VIEW:
        return None
    timestamp = time.time() if now is None else now
    if operation is CartOperation.CLEAR:
        return _plan(operation, None, None, None, "全部商品", None, timestamp)
    line, product = _resolve_target(core, observation, catalog, operation)
    quantity = _quantity_for(operation, observation.quantity, line)
    return _plan(
        operation,
        product_id=str(product.product_id),
        sku_id=line.sku_id if line else None,
        quantity=quantity,
        title=str(product.title),
        unit_price=float(product.min_price or product.base_price),
        now=timestamp,
    )


def cart_plan_delta(core: SessionCore, plan: CartPlan) -> SessionDelta:
    return SessionDelta(
        core=replace(core, pending_clarification=None, pending_cart_plan=plan),
        reason="v3_cart_plan_created",
    )


def apply_cart_plan(*, core: SessionCore, catalog: Any, confirmed: bool, now: float | None = None) -> tuple[SessionDelta, dict[str, object]]:
    """Apply or cancel the sole pending V3 plan, exactly once."""

    plan = core.pending_cart_plan
    if plan is None:
        raise CartPlanningError("当前没有待确认的购物车操作。")
    check_at = time.time() if now is None else now
    if plan.expires_at < check_at:
        raise CartPlanningError("购物车确认已过期，请重新操作。")
    if not confirmed:
        return SessionDelta(replace(core, pending_cart_plan=None), "v3_cart_plan_cancelled"), {"status": "cancelled", "cart": cart_snapshot(core, catalog)}
    lines = list(core.cart_lines)
    if plan.operation is CartOperation.CLEAR:
        lines = []
    elif plan.product_id is not None:
        lines = _apply_line(lines, plan)
    next_core = replace(core, cart_lines=tuple(lines), pending_cart_plan=None, pending_clarification=None)
    return SessionDelta(next_core, "v3_cart_plan_applied"), {
        "status": "applied",
        "action": plan.operation.value,
        "cart": cart_snapshot(next_core, catalog),
    }


def cart_snapshot(core: SessionCore, catalog: Any) -> dict[str, object]:
    """Render cart facts from live catalog; silently omit deleted products."""

    items: list[dict[str, object]] = []
    total = 0.0
    for index, line in enumerate(core.cart_lines, start=1):
        product = catalog.get(line.product_id)
        if product is None:
            continue
        price = float(product.min_price or product.base_price)
        total += price * line.quantity
        items.append({
            "index": index,
            "product_id": line.product_id,
            "sku_id": line.sku_id,
            "title": str(product.title),
            "price": price,
            "quantity": line.quantity,
        })
    return {"items": items, "count": sum(int(item["quantity"]) for item in items), "total_price": round(total, 2)}


def _resolve_target(core: SessionCore, observation: SemanticObservation, catalog: Any, operation: CartOperation) -> tuple[CartLine | None, Any]:
    if operation is CartOperation.ADD:
        rank = observation.target_card_rank
        if rank is None or rank > len(core.cards):
            raise CartPlanningError("请说明要加入刚才第几个商品卡；商品卡过期时请先重新推荐。")
        card = core.cards[rank - 1]
        product = catalog.get(card.product_id)
        if product is None:
            raise CartPlanningError("该商品已无法从当前目录读取，不能加入购物车。")
        return None, product
    rank = observation.target_cart_rank
    if rank is None or rank > len(core.cart_lines):
        raise CartPlanningError("请说明要操作购物车中的第几个商品。")
    line = core.cart_lines[rank - 1]
    product = catalog.get(line.product_id)
    if product is None:
        raise CartPlanningError("该购物车商品已无法从当前目录读取，不能继续操作。")
    return line, product


def _quantity_for(operation: CartOperation, requested: int | None, line: CartLine | None) -> int | None:
    if operation is CartOperation.ADD:
        return requested or 1
    if operation is CartOperation.SET_QUANTITY:
        if requested is None:
            raise CartPlanningError("请说明要改成几件。")
        return requested
    if operation is CartOperation.REMOVE:
        return line.quantity if line else None
    raise CartPlanningError("不支持的购物车操作。")


def _plan(operation: CartOperation, product_id: str | None, sku_id: str | None, quantity: int | None, title: str, unit_price: float | None, now: float) -> CartPlan:
    raw = f"{operation.value}:{product_id}:{sku_id}:{quantity}:{now:.6f}"
    return CartPlan(
        plan_id=f"cart_{sha256(raw.encode('utf-8')).hexdigest()[:16]}",
        operation=operation,
        product_id=product_id,
        sku_id=sku_id,
        quantity=quantity,
        expires_at=now + CART_CONFIRM_TTL_SECONDS,
        title=title,
        unit_price=unit_price,
    )


def _apply_line(lines: list[CartLine], plan: CartPlan) -> list[CartLine]:
    assert plan.product_id is not None
    key = (plan.product_id, plan.sku_id)
    index = next((idx for idx, item in enumerate(lines) if (item.product_id, item.sku_id) == key), None)
    if plan.operation is CartOperation.ADD:
        if index is None:
            return [*lines, CartLine(plan.product_id, plan.sku_id, int(plan.quantity or 1))]
        current = lines[index]
        lines[index] = CartLine(current.product_id, current.sku_id, current.quantity + int(plan.quantity or 1))
        return lines
    if index is None:
        raise CartPlanningError("该商品不在购物车中，无法继续操作。")
    if plan.operation is CartOperation.REMOVE:
        return [item for item in lines if (item.product_id, item.sku_id) != key]
    if plan.operation is CartOperation.SET_QUANTITY:
        lines[index] = CartLine(plan.product_id, plan.sku_id, int(plan.quantity or 1))
        return lines
    raise CartPlanningError("不支持的购物车操作。")
