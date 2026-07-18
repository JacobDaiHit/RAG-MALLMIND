"""Create one narrow clarification for incomplete action-specific observations."""
from __future__ import annotations

import time

from .config import CLARIFICATION_TTL_SECONDS
from .semantic_contracts import CartObservation, FactQueryObservation, PcEditObservation, RecommendObservation, SemanticObservation
from .types import CartOperation, CartTargetSource, ClarificationPlan, RecommendationMode, SessionCore


class ClarificationPolicy:
    """Checks missing execution fields; it never reinterprets Chinese text."""

    def plan(self, *, observation: SemanticObservation, core: SessionCore, catalog=None) -> ClarificationPlan | None:
        if isinstance(observation, RecommendObservation):
            if observation.mode is RecommendationMode.EXPLORE:
                return None
            if not observation.target_type_surface and not observation.target_type_candidate_id and core.active_requirement is None:
                return _plan("你想让我推荐哪一类商品？例如手机、咖啡、防晒或篮球鞋。", ("product_type",), "product_type_unresolved")
            return None
        if isinstance(observation, FactQueryObservation):
            if observation.fact_kind is None:
                return _plan("你想看价格、SKU、详细参数，还是比较两张商品卡？", ("fact_kind",), "fact_kind_unresolved")
            return None
        if isinstance(observation, CartObservation):
            if observation.operation is None:
                return _plan("请说明要加入、删除、修改数量、查看还是清空购物车。", ("cart_operation",), "cart_operation_unresolved")
            if observation.operation not in {CartOperation.VIEW, CartOperation.CLEAR} and observation.target_ref is None:
                return _plan("请说明要操作哪一个商品卡或购物车商品，例如“加入第一个”。", ("cart_target",), "cart_target_unresolved")
            if observation.operation is CartOperation.ADD and observation.target_ref and observation.target_ref.source is not CartTargetSource.CARD:
                return _plan("加入购物车时请说刚才第几个商品卡，例如“加入第一个”。", ("cart_target",), "cart_target_schema_conflict")
            if observation.operation in {CartOperation.REMOVE, CartOperation.SET_QUANTITY} and observation.target_ref and observation.target_ref.source is not CartTargetSource.CART:
                return _plan("修改或删除时请说购物车中的第几个商品。", ("cart_target",), "cart_target_schema_conflict")
            return None
        if isinstance(observation, PcEditObservation) and observation.operation is None:
            return _plan("请说明是替换某个配件，还是调整整机预算。", ("pc_operation",), "pc_operation_unresolved")
        return None


def _plan(question: str, fields: tuple[str, ...], reason: str) -> ClarificationPlan:
    return ClarificationPlan(question, fields, time.time() + CLARIFICATION_TTL_SECONDS, reason)
