"""Shopping total estimator for ecommerce recommendation plans."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from rag.schemas import ApiProduct, CostEstimate, RecommendationPlan, RequirementSpec, SelectedComponent

def estimate_plan_cost(requirement: RequirementSpec, components: List[SelectedComponent]) -> CostEstimate:
    """Estimate the one-time shopping total for a product bundle."""

    breakdown: Dict[str, Any] = {}
    currencies = set()
    total_min = 0.0
    total_max = 0.0
    assumptions = [
        "价格来自本地电商商品数据集 SKU 标价，库存数量未在数据集中提供，不输出精确库存数字。",
    ]
    if requirement.price_max is not None:
        assumptions.append(f"用户预算上限约 {requirement.price_max:g} CNY，套餐会优先控制总价。")

    for component in components:
        min_price, max_price, component_breakdown, component_assumptions = estimate_component_price(component)
        total_min += min_price
        total_max += max_price
        breakdown[component.product.product_id] = component_breakdown
        currencies.add(component.product.currency)
        assumptions.extend(component_assumptions)

    currency = next(iter(currencies)) if len(currencies) == 1 else "CNY"
    return CostEstimate(
        total_price_min=round(total_min, 2),
        total_price_max=round(total_max, 2),
        currency=currency,
        assumptions=dedupe(assumptions),
        breakdown=breakdown,
    )


def estimate_component_price(component: SelectedComponent) -> Tuple[float, float, Dict[str, Any], List[str]]:
    """Return min/max price for one selected product."""

    product = component.product
    quantity = max(component.quantity, 1)
    sku_prices = [sku.price for sku in product.skus if sku.price is not None]
    min_price = min(sku_prices) if sku_prices else product.min_price or product.base_price
    max_price = max(sku_prices) if sku_prices else product.max_price or product.base_price
    selected_sku = choose_selected_sku(product, component.selected_sku_id)
    selected_price = selected_sku.price if selected_sku and selected_sku.price is not None else product.base_price
    breakdown = {
        "role": component.role.value,
        "pricing_model": "sku_price",
        "currency": product.currency,
        "quantity": quantity,
        "base_price": product.base_price,
        "min_price": min_price,
        "max_price": max_price,
        "selected_sku_id": selected_sku.sku_id if selected_sku else component.selected_sku_id,
        "selected_price": selected_price,
        "selected_total_price": round(selected_price * quantity, 2),
    }
    assumptions = []
    if product.stock_quantity is None:
        assumptions.append(f"{product.product_id} 未提供精确库存数量，仅按已上架商品参与推荐。")
    return min_price * quantity, max_price * quantity, breakdown, assumptions


def estimate_product_price(
    requirement: RequirementSpec,
    product: ApiProduct,
) -> Tuple[Optional[float], str, List[str]]:
    """Return a comparable one-time product price for scoring and selection."""

    price = product.min_price or product.base_price
    if price is None:
        return None, product.currency, [f"{product.product_id} 缺少可比较价格。"]
    return float(price), product.currency, []


def attach_cost_estimates(requirement: RequirementSpec, plans: List[RecommendationPlan]) -> List[RecommendationPlan]:
    for plan in plans:
        plan.cost_estimate = estimate_plan_cost(requirement, plan.components)
    return plans



def choose_selected_sku(product: ApiProduct, sku_id: Optional[str]) -> Any:
    if sku_id:
        for sku in product.skus:
            if sku.sku_id == sku_id:
                return sku
    if not product.skus:
        return None
    return sorted(
        product.skus,
        key=lambda sku: abs((sku.price if sku.price is not None else product.base_price) - product.base_price),
    )[0]


def dedupe(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
