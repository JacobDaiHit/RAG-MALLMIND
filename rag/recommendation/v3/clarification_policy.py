"""Generate fixed clarification plans for missing executable fields.

``ClarificationPolicy.plan`` consumes an already-decoded semantic observation
and SessionCore; it does not attempt Chinese intent classification. It owns
stable reasons/questions for missing product type, card references, cart target,
and ambiguous computer purchase form.
"""
from __future__ import annotations

import time

from .config import CLARIFICATION_TTL_SECONDS
from .types import CartOperation, ClarificationPlan, CommerceIntent, ComputerPurchaseKind, SemanticObservation, SessionCore, V3Action


class ClarificationPolicy:
    """Owns questions caused by missing executable references, not language parsing."""

    def plan(self, *, observation: SemanticObservation, core: SessionCore, catalog=None) -> ClarificationPlan | None:
        if (
            observation.computer_purchase_kind is ComputerPurchaseKind.UNKNOWN
            and observation.action in {V3Action.RECOMMEND, V3Action.PC_BUILD}
        ):
            return _computer_purchase_plan(catalog)
        if (
            observation.commerce_intent is CommerceIntent.RECOMMEND
            and observation.action not in {V3Action.PC_BUILD, V3Action.PC_PLAN_EDIT, V3Action.PC_PLAN_COMPARE}
            and not observation.target_type_surface
        ):
            return _plan(
                "你想让我推荐哪一类商品？例如手机、咖啡、防晒或篮球鞋。",
                ("product_type",),
                "product_type_unresolved",
            )
        if observation.commerce_intent is CommerceIntent.COMPARE and not _has_two_live_card_ranks(observation, core):
            return _plan(
                "请说明要对比哪两张商品卡，例如“比较第一个和第二个”。",
                ("card_references",),
                "comparison_card_references_unresolved",
            )
        if (
            observation.commerce_intent is CommerceIntent.CART
            and observation.cart_operation not in {CartOperation.VIEW, CartOperation.CLEAR}
            and observation.target_card_rank is None
            and observation.target_cart_rank is None
        ):
            return _plan(
                "请说明要操作哪一个商品卡或购物车商品，例如“加入第一个”。",
                ("cart_target",),
                "cart_target_unresolved",
            )
        return None


def _has_two_live_card_ranks(observation: SemanticObservation, core: SessionCore) -> bool:
    ranks = observation.target_card_ranks
    return len(ranks) == 2 and len(set(ranks)) == 2 and all(1 <= rank <= len(core.cards) for rank in ranks)


def _plan(question: str, missing_fields: tuple[str, ...], reason_code: str) -> ClarificationPlan:
    return ClarificationPlan(question, missing_fields, time.time() + CLARIFICATION_TTL_SECONDS, reason_code)


def _computer_purchase_plan(catalog) -> ClarificationPlan:
    options = []
    if _catalog_has_sub_category(catalog, "笔记本"):
        options.append("笔记本")
    if _catalog_has_pc_build_capability(catalog):
        options.append("让我按预算配一台台式主机")
    if _catalog_has_sub_category(catalog, "成品台式"):
        options.append("成品台式机")
    if not options:
        question = "请明确要买笔记本、成品台式机，还是让我按预算配一台台式主机？"
    elif len(options) == 1:
        question = f"当前目录只支持{options[0]}。你想按这个形式继续吗？"
    elif len(options) == 2:
        question = f"你想买{options[0]}，还是{options[1]}？"
    else:
        question = f"你想买{options[0]}、{options[2]}，还是{options[1]}？"
    return _plan(question, ("computer_purchase_kind",), "computer_purchase_kind_unresolved")


def _catalog_has_sub_category(catalog, marker: str) -> bool:
    if catalog is None:
        return False
    return any(marker in str(product.sub_category) for product in catalog.products)


def _catalog_has_pc_build_capability(catalog) -> bool:
    if catalog is None:
        return False
    required = {"pc_cpu", "pc_gpu", "pc_motherboard", "pc_memory", "pc_storage", "pc_psu", "pc_case", "pc_cooler"}
    present = {str(product.category.value) for product in catalog.products}
    return required <= present
