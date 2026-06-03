"""Intent routing for the ecommerce guided-selling flow."""
from __future__ import annotations

from typing import Any, Dict, List

from rag.schemas import ComponentCategory, RequirementSpec


PC_BUILD_TERMS = ["配电脑", "装机", "整机", "主机配置", "电脑配置", "pc方案", "pc 方案"]


def route_shopping_intent(requirement: RequirementSpec) -> Dict[str, Any]:
    """Map parsed constraints to the agent branch the API should execute."""

    text = requirement.raw_query.lower()
    route = "single_product_recommendation"
    if requirement.need_cart_action:
        route = "cart_action"
    elif any(term in text for term in PC_BUILD_TERMS):
        route = "pc_build_plan"
    elif requirement.need_comparison:
        route = "product_comparison"
    elif requirement.need_multimodal:
        route = "multimodal_product_recommendation"
    elif requirement.need_bundle or len(requirement.desired_categories) > 1:
        route = "bundle_recommendation"
    elif requirement.missing_fields:
        route = "condition_filter"

    return {
        "route": route,
        "task_type": route_to_task_type(route, requirement.task_type),
        "needs_clarification": bool(requirement.missing_fields),
        "clarification_fields": list(requirement.missing_fields),
        "next_action": next_action_for(route, requirement),
        "supported_now": route in {
            "single_product_recommendation",
            "condition_filter",
            "product_comparison",
            "bundle_recommendation",
            "multimodal_product_recommendation",
            "cart_action",
            "pc_build_plan",
        },
        "reason": build_route_reason(route, requirement),
        "pipeline": [
            "input_preprocessor",
            "intent_router",
            "constraint_parser",
            "structured_filter",
            "scoring_rerank",
            "grounded_generator",
        ],
    }


def route_to_task_type(route: str, fallback: str) -> str:
    mapping = {
        "single_product_recommendation": "single_product_recommendation",
        "condition_filter": "condition_filter",
        "product_comparison": "comparison",
        "bundle_recommendation": "bundle_recommendation",
        "multimodal_product_recommendation": "single_product_recommendation",
        "cart_action": "cart_action",
        "pc_build_plan": "pc_build_plan",
    }
    return mapping.get(route, fallback)


def next_action_for(route: str, requirement: RequirementSpec) -> str:
    if requirement.missing_fields:
        return "ask_follow_up_then_recommend"
    if route == "product_comparison":
        return "compare_candidate_products"
    if route == "cart_action":
        return "operate_cart_after_product_selection"
    if route == "pc_build_plan":
        return "generate_pc_build_plan"
    return "recommend_grounded_products"


def build_route_reason(route: str, requirement: RequirementSpec) -> str:
    categories: List[str] = [
        category.value if isinstance(category, ComponentCategory) else str(category)
        for category in requirement.desired_categories
    ]
    if route == "pc_build_plan":
        return "识别到电脑整机方案意图，进入独立 PC 配置规划链路，使用本地 PC 配件库和兼容性规则生成方案。"
    if route == "condition_filter":
        return "需求中仍缺少关键筛选条件，先给候选范围并追问预算、类目或偏好。"
    if route == "product_comparison":
        return "用户表达了比较/对比意图，返回候选商品表格和默认建议。"
    if route == "bundle_recommendation":
        return "用户需要套装或跨类目搭配，按多个类目分别召回后组合成方案。"
    if route == "multimodal_product_recommendation":
        return "用户包含图片/语音等输入，先融合多模态描述，再按商品库检索推荐。"
    return "识别为单品推荐，按类目、预算、偏好、排除条件筛选并输出商品卡片。"
