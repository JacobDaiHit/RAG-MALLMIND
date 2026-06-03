"""Schemas for the ecommerce guided-selling recommendation flow."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator
from rag.recommendation.pc_types import maybe_normalize_pc_component_type


class BudgetLevel(str, Enum):
    """User budget preference parsed from a shopping request."""

    low = "low"
    medium = "medium"
    high = "high"
    unknown = "unknown"


class RequirementLevel(str, Enum):
    """Generic low/medium/high preference level."""

    low = "low"
    medium = "medium"
    high = "high"
    unknown = "unknown"


class ComponentCategory(str, Enum):
    """Traditional ecommerce product categories used by the catalog."""

    beauty = "beauty"
    digital = "digital"
    clothing = "clothing"
    food = "food"
    pc_cpu = "pc_cpu"
    pc_gpu = "pc_gpu"
    pc_motherboard = "pc_motherboard"
    pc_memory = "pc_memory"
    pc_storage = "pc_storage"
    pc_psu = "pc_psu"
    pc_case = "pc_case"
    pc_cooler = "pc_cooler"


ProductCategory = ComponentCategory


class RecommendationType(str, Enum):
    """Recommendation result types returned to the client."""

    single_product = "single_product"
    shopping_bundle = "shopping_bundle"
    pc_build_plan = "pc_build_plan"


CATEGORY_NAME_TO_KEY = {
    "美妆护肤": ComponentCategory.beauty,
    "数码电子": ComponentCategory.digital,
    "服饰运动": ComponentCategory.clothing,
    "食品饮料": ComponentCategory.food,
}

CATEGORY_KEY_TO_NAME = {
    ComponentCategory.beauty: "美妆护肤",
    ComponentCategory.digital: "数码电子",
    ComponentCategory.clothing: "服饰运动",
    ComponentCategory.food: "食品饮料",
}


class ProductSku(BaseModel):
    """One purchasable SKU under a product."""

    sku_id: str = ""
    properties: Dict[str, str] = Field(default_factory=dict)
    price: Optional[float] = Field(default=None, ge=0)


class ProductFAQ(BaseModel):
    """Grounded FAQ item from the product dataset."""

    question: str = ""
    answer: str = ""


class ProductReview(BaseModel):
    """Grounded user review from the product dataset."""

    nickname: str = ""
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    content: str = ""


class RequirementSpec(BaseModel):
    """Structured shopping intent extracted from the user's natural language."""

    raw_query: str = Field(..., description="Original user request.")
    scenario: str = Field(default="", description="Normalized shopping scenario.")
    task_type: str = Field(default="shopping_recommendation")

    required_components: List[ComponentCategory] = Field(default_factory=list)
    optional_components: List[ComponentCategory] = Field(default_factory=list)

    desired_categories: List[ComponentCategory] = Field(default_factory=list)
    excluded_categories: List[ComponentCategory] = Field(default_factory=list)
    target_sub_categories: List[str] = Field(default_factory=list)
    brands: List[str] = Field(default_factory=list)
    excluded_brands: List[str] = Field(default_factory=list)
    must_have_terms: List[str] = Field(default_factory=list)
    excluded_terms: List[str] = Field(default_factory=list)
    preferences: List[str] = Field(default_factory=list)
    occasion: str = ""
    target_user: str = ""

    price_min: Optional[float] = Field(default=None, ge=0)
    price_max: Optional[float] = Field(default=None, ge=0)
    budget_level: BudgetLevel = BudgetLevel.unknown

    need_bundle: bool = False
    need_comparison: bool = False
    need_cart_action: bool = False
    need_multimodal: bool = False

    input_modalities: List[str] = Field(default_factory=lambda: ["text"])
    output_modalities: List[str] = Field(default_factory=lambda: ["text"])
    languages: List[str] = Field(default_factory=lambda: ["zh"])

    latency_requirement: RequirementLevel = RequirementLevel.medium
    quality_requirement: RequirementLevel = RequirementLevel.medium

    missing_fields: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def sync_category_fields(self) -> "RequirementSpec":
        if not self.desired_categories and self.required_components:
            self.desired_categories = list(self.required_components)
        if not self.required_components and self.desired_categories:
            self.required_components = list(self.desired_categories)
        return self


class ApiProduct(BaseModel):
    """Normalized ecommerce product used for filtering, scoring, and cards.

    The class name is kept for import compatibility. New code should prefer
    `CommerceProduct`, `product_id`, `title`, and `brand`.
    """

    product_id: str = ""
    title: str = ""
    brand: str = ""
    category: ComponentCategory = ComponentCategory.beauty
    category_name: str = ""
    sub_category: str = ""

    base_price: float = Field(default=0, ge=0)
    min_price: float = Field(default=0, ge=0)
    max_price: float = Field(default=0, ge=0)
    currency: str = "CNY"
    stock_status: str = "available_for_demo"
    stock_quantity: Optional[int] = Field(default=None, ge=0)

    image_path: str = ""
    image_url: str = ""
    skus: List[ProductSku] = Field(default_factory=list)
    description: str = ""
    faqs: List[ProductFAQ] = Field(default_factory=list)
    reviews: List[ProductReview] = Field(default_factory=list)
    review_count: int = Field(default=0, ge=0)
    rating_avg: Optional[float] = Field(default=None, ge=0, le=5)

    best_for: List[str] = Field(default_factory=list)
    not_good_for: List[str] = Field(default_factory=list)
    supported_scenarios: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    pricing_note: str = ""
    risk_notes: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_product_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        item = dict(data)
        raw_category = item.get("category")
        category_key = item.get("category_key") or raw_category
        if raw_category in CATEGORY_NAME_TO_KEY:
            item["category_name"] = item.get("category_name") or raw_category
            item["category"] = CATEGORY_NAME_TO_KEY[raw_category].value
        elif maybe_normalize_pc_component_type(category_key):
            item["category"] = maybe_normalize_pc_component_type(category_key)
            member = ComponentCategory(item["category"])
            item["category_name"] = item.get("category_name") or CATEGORY_KEY_TO_NAME.get(member, str(raw_category or category_key))
        elif category_key in {member.value for member in ComponentCategory}:
            item["category"] = category_key
            member = ComponentCategory(category_key)
            item["category_name"] = item.get("category_name") or CATEGORY_KEY_TO_NAME.get(member, str(raw_category or category_key))
        else:
            item["category"] = ComponentCategory.beauty.value
            item["category_name"] = item.get("category_name") or str(raw_category or "未分类")

        item["product_id"] = item.get("product_id") or ""
        item["title"] = item.get("title") or ""
        item["brand"] = item.get("brand") or ""
        item["min_price"] = item.get("min_price", item.get("base_price", 0))
        item["max_price"] = item.get("max_price", item.get("base_price", 0))
        return item

    @model_validator(mode="after")
    def enrich_ecommerce_fields(self) -> "ApiProduct":
        self.category_name = self.category_name or CATEGORY_KEY_TO_NAME.get(self.category, self.category.value)
        if not self.min_price:
            self.min_price = self.base_price
        if not self.max_price:
            self.max_price = self.base_price
        self.pricing_note = self.pricing_note or "传统电商商品价格，按当前 SKU 标价汇总，不代表实时库存。"
        self.risk_notes = self.risk_notes or self.not_good_for[:3]
        return self


CommerceProduct = ApiProduct


class ScoreBreakdown(BaseModel):
    """Explainable score for one selected ecommerce product."""

    scenario_match: float = Field(ge=0, le=1)
    attribute_match: float = Field(ge=0, le=1)
    price_fit: float = Field(ge=0, le=1)
    reputation_fit: float = Field(ge=0, le=1)
    availability_fit: float = Field(ge=0, le=1)
    sku_fit: float = Field(ge=0, le=1)
    detail_quality: float = Field(ge=0, le=1)
    final_score: float = Field(ge=0, le=1)
    reasons: List[str] = Field(default_factory=list)


class SelectedComponent(BaseModel):
    """A product selected into one shopping plan."""

    role: ComponentCategory
    product: ApiProduct
    reason: str = ""
    score: Optional[ScoreBreakdown] = None
    evidence_doc_ids: List[str] = Field(default_factory=list)
    quantity: int = Field(default=1, ge=1)
    selected_sku_id: Optional[str] = None


class CostEstimate(BaseModel):
    """One-time shopping total for ecommerce products or bundles."""

    total_price_min: float = Field(default=0, ge=0)
    total_price_max: float = Field(default=0, ge=0)
    currency: str = "CNY"
    assumptions: List[str] = Field(default_factory=list)
    breakdown: Dict[str, Any] = Field(default_factory=dict)


class RecommendationPlan(BaseModel):
    """One complete ecommerce shopping plan."""

    recommendation_type: RecommendationType
    title: str
    summary: str = ""
    components: List[SelectedComponent] = Field(default_factory=list)
    cost_estimate: CostEstimate = Field(default_factory=CostEstimate)
    pros: List[str] = Field(default_factory=list)
    cons: List[str] = Field(default_factory=list)
    suitable_for: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)
    score_table: List[Dict[str, Any]] = Field(default_factory=list)


class RecommendationResult(BaseModel):
    """Top-level result returned by the ecommerce recommendation pipeline."""

    requirement: RequirementSpec
    plans: List[RecommendationPlan] = Field(default_factory=list)
    candidate_count: int = Field(default=0, ge=0)
    product_cards: List[Dict[str, Any]] = Field(default_factory=list)
    candidate_scope: Dict[str, Any] = Field(default_factory=dict)
    comparison_table: List[Dict[str, Any]] = Field(default_factory=list)
    intent_route: Dict[str, Any] = Field(default_factory=dict)
    missing_fields: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    teaching_guidance: List[str] = Field(default_factory=list)
    follow_up_questions: List[str] = Field(default_factory=list)
    optimization_suggestions: List[str] = Field(default_factory=list)
    feedback_summary: Dict[str, Any] = Field(default_factory=dict)
    trace: Dict[str, Any] = Field(default_factory=dict)


def price_to_price_tier(price: float) -> int:
    if price <= 50:
        return 1
    if price <= 200:
        return 2
    if price <= 800:
        return 3
    if price <= 3000:
        return 4
    return 5


def rating_to_quality_tier(rating: Optional[float], review_count: int) -> int:
    if rating is None:
        return 3
    if rating >= 4.6 and review_count >= 3:
        return 5
    if rating >= 4.0:
        return 4
    if rating >= 3.0:
        return 3
    if rating >= 2.0:
        return 2
    return 1
