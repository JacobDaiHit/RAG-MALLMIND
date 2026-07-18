"""Pydantic models for catalog JSON, product facts, and PC component records.

``ApiProduct`` and its SKU/review/FAQ children are validated at catalog load,
management API writes, and ingestion. These schemas represent directory facts;
they are distinct from V3's internal routing contracts in ``v3.types``.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator
from rag.recommendation.pc_types import maybe_normalize_pc_component_type


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


class ApiProduct(BaseModel):
    """Normalized ecommerce product used for filtering, scoring, and cards.

    ``product_id``, ``title``, and ``brand`` are the authoritative identifiers
    used by the active catalog and V3 execution path.
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
