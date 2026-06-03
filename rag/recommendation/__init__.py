"""Recommendation helpers for ecommerce guided selling."""

from rag.recommendation.product_loader import (
    ProductCatalog,
    ProductCatalogError,
    filter_pc_parts_catalog,
    load_catalog_for_scope,
    load_combined_product_catalog,
    load_pc_parts_product_catalog,
    load_product_catalog,
    load_products,
    upsert_product,
)
from rag.recommendation.cost_estimator import (
    attach_cost_estimates,
    estimate_plan_cost,
    estimate_product_price,
)
from rag.recommendation.input_preprocessor import (
    PreprocessedInput,
    build_preprocessed_goal,
    clean_text,
    preprocess_user_input,
)
from rag.recommendation.intent_router import route_shopping_intent
from rag.recommendation.structured_filter import filter_products_for_requirement
from rag.recommendation.comparison import compare_products
from rag.recommendation.session_state import (
    apply_cart_instruction,
    build_contextual_goal,
    get_session,
    remember_recommendation,
)
from rag.recommendation.package_builder import (
    build_recommendation_plan,
    build_recommendation_result,
    build_plan,
    score_required_components,
)
from rag.recommendation.recommendation_pipeline import (
    InvalidGoalError,
    parse_requirement,
    parse_requirement_rule_based,
    recommend_api_stack,
    recommend_shopping_products,
    recommend_shopping_bundle,
    validate_business_goal,
)
from rag.recommendation.scorer import (
    BASE_WEIGHTS,
    ProductScore,
    build_dynamic_weights,
    score_product,
    score_products,
)

__all__ = [
    "ProductCatalog",
    "ProductCatalogError",
    "ProductScore",
    "PreprocessedInput",
    "BASE_WEIGHTS",
    "InvalidGoalError",
    "attach_cost_estimates",
    "build_plan",
    "build_preprocessed_goal",
    "build_contextual_goal",
    "build_dynamic_weights",
    "build_recommendation_plan",
    "build_recommendation_result",
    "clean_text",
    "compare_products",
    "estimate_plan_cost",
    "estimate_product_price",
    "filter_products_for_requirement",
    "filter_pc_parts_catalog",
    "load_catalog_for_scope",
    "load_combined_product_catalog",
    "load_pc_parts_product_catalog",
    "load_product_catalog",
    "load_products",
    "upsert_product",
    "parse_requirement",
    "parse_requirement_rule_based",
    "preprocess_user_input",
    "recommend_api_stack",
    "recommend_shopping_products",
    "recommend_shopping_bundle",
    "apply_cart_instruction",
    "get_session",
    "remember_recommendation",
    "route_shopping_intent",
    "validate_business_goal",
    "score_required_components",
    "score_product",
    "score_products",
]
