"""Deterministic product filtering before scoring.

The recommender should narrow candidates with database fields first, then let
semantic scoring rank the survivors. When a hard budget removes every product
in a category, the filter relaxes only that budget constraint so the system can
still explain the nearest alternatives instead of hallucinating products.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Set

from rag.recommendation.query_guards import (
    category_for_product_type,
    infer_product_type,
    parse_pc_part_constraints,
    product_matches_pc_constraints,
    product_query_preference_match,
    product_matches_type,
    budget_relaxation_allowed,
)
from rag.schemas import ApiProduct, ComponentCategory, RequirementSpec

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FilterDiagnostics:
    category: ComponentCategory
    raw_count: int = 0
    after_stock_count: int = 0
    after_exclusion_count: int = 0
    after_brand_whitelist_count: int = 0
    brand_whitelist_applied: bool = False
    brand_whitelist_relaxed: bool = False
    after_target_count: int = 0
    after_must_have_count: int = 0
    after_budget_count: int = 0
    after_llm_count: int = 0
    inferred_product_type: str = ""
    product_type_filter_applied: bool = False
    product_type_candidate_count: int = 0
    pc_part_constraints: Dict[str, Any] = field(default_factory=dict)
    pc_constraint_filter_applied: bool = False
    pc_constraint_candidate_count: int = 0
    pc_constraint_relaxed: bool = False
    returned_count: int = 0
    relaxed_constraints: List[str] = field(default_factory=list)
    budget_filter_strict: bool = True
    budget_gap_reason: str = ""
    hard_constraint_passed_ids: Set[str] = field(default_factory=set)

    def to_trace(self) -> Dict[str, object]:
        return {
            "category": self.category.value,
            "raw_count": self.raw_count,
            "after_stock_count": self.after_stock_count,
            "after_exclusion_count": self.after_exclusion_count,
            "after_brand_whitelist_count": self.after_brand_whitelist_count,
            "brand_whitelist_applied": self.brand_whitelist_applied,
            "brand_whitelist_relaxed": self.brand_whitelist_relaxed,
            "after_target_count": self.after_target_count,
            "after_must_have_count": self.after_must_have_count,
            "after_budget_count": self.after_budget_count,
            "after_llm_count": self.after_llm_count,
            "inferred_product_type": self.inferred_product_type,
            "product_type_filter_applied": self.product_type_filter_applied,
            "product_type_candidate_count": self.product_type_candidate_count,
            "pc_part_constraints": dict(self.pc_part_constraints),
            "pc_constraint_filter_applied": self.pc_constraint_filter_applied,
            "pc_constraint_candidate_count": self.pc_constraint_candidate_count,
            "pc_constraint_relaxed": self.pc_constraint_relaxed,
            "returned_count": self.returned_count,
            "relaxed_constraints": list(self.relaxed_constraints),
            "budget_filter_strict": self.budget_filter_strict,
            "budget_gap_reason": self.budget_gap_reason,
        }


def filter_products_for_requirement(
    requirement: RequirementSpec,
    products: Iterable[ApiProduct],
    category: ComponentCategory,
) -> tuple[List[ApiProduct], FilterDiagnostics]:
    """Apply structured constraints for one category with safe fallback."""

    raw = [product for product in products if product.category == category]
    stock_filtered = [product for product in raw if is_available(product)]
    exclusion_filtered = [
        product
        for product in stock_filtered
        if not violates_brand_or_text_exclusion(requirement, product)
    ]

    hard_passed_ids = {p.product_id for p in exclusion_filtered}

    # ── 品牌白名单硬过滤 ──
    # requirement.brands 此前仅做 scorer 加分（soft boost），不做硬过滤，
    # 导致 brands=["华为"] 时仍可能返回 MacBook Air。
    # 新增：品牌白名单硬过滤 + 安全降级（过滤后为空则保留过滤前结果）。
    brand_whitelist_applied = False
    brand_whitelist_relaxed = False
    if requirement.brands:
        brand_filtered = [
            product for product in exclusion_filtered
            if _matches_brand_requirement(product, requirement.brands)
        ]
        if brand_filtered:
            exclusion_filtered = brand_filtered
            brand_whitelist_applied = True
        else:
            # 安全降级：品牌过滤后为空，保留过滤前结果
            brand_whitelist_relaxed = True

    target_filtered = [
        product
        for product in exclusion_filtered
        if matches_target_sub_category(requirement, product)
    ]
    if not target_filtered:
        target_filtered = exclusion_filtered

    # 当 LLM 路由器已明确设定 desired_categories 时，信任路由器的品类决策，
    # 跳过 infer_product_type 的品类交叉拒绝。避免"笔记本电脑"关键词
    # 覆盖路由器对"双肩包→clothing"的正确判断。
    has_explicit_category = bool(requirement.desired_categories or requirement.required_components)
    inferred_product_type = None if category.value.startswith("pc_") else infer_product_type(requirement.raw_query)
    product_type_category = category_for_product_type(inferred_product_type)
    if has_explicit_category and product_type_category and category.value != product_type_category:
        inferred_product_type = None
        product_type_category = None
    if product_type_category and category.value != product_type_category:
        allow_cross_category_bundle = bool(
            requirement.need_bundle
            and len(requirement.desired_categories or requirement.required_components) > 1
        )
        if not allow_cross_category_bundle:
            diagnostics = FilterDiagnostics(
                category=category,
                raw_count=len(raw),
                after_stock_count=len(stock_filtered),
                after_exclusion_count=len(exclusion_filtered),
                after_target_count=len(target_filtered),
                after_must_have_count=0,
                after_budget_count=0,
                inferred_product_type=inferred_product_type or "",
                product_type_filter_applied=True,
                product_type_candidate_count=0,
                pc_part_constraints={},
                returned_count=0,
                hard_constraint_passed_ids=hard_passed_ids,
            )
            return [], diagnostics
        inferred_product_type = None
    product_type_filtered = [
        product
        for product in target_filtered
        if product_matches_type(product, inferred_product_type)
    ]
    product_type_filter_applied = bool(inferred_product_type and product_type_filtered)
    typed_candidates = product_type_filtered if product_type_filter_applied else target_filtered

    must_have_filtered = [
        product
        for product in typed_candidates
        if matches_all_required_terms(requirement, product)
    ]
    if not must_have_filtered:
        must_have_filtered = typed_candidates

    pc_constraints = parse_pc_part_constraints(requirement.raw_query) if category.value.startswith("pc_") else {}
    pc_constraint_filtered = [
        product
        for product in must_have_filtered
        if product_matches_pc_constraints(product, pc_constraints)
    ]
    pc_constraint_filter_applied = bool(pc_constraints and pc_constraint_filtered)
    pc_constraint_relaxed = bool(pc_constraints and not pc_constraint_filtered)
    constrained_candidates = pc_constraint_filtered if pc_constraint_filter_applied else must_have_filtered

    preference_filtered = [
        product
        for product in constrained_candidates
        if product_query_preference_match(product, requirement.raw_query)
    ]
    preferred_candidates = preference_filtered if preference_filtered else constrained_candidates
    budget_filtered = [
        product
        for product in preferred_candidates
        if matches_budget(requirement, product)
    ]
    relaxed: List[str] = []
    budget_filter_strict = not budget_relaxation_allowed(requirement.raw_query)
    budget_gap_reason = ""
    returned = budget_filtered
    if requirement.price_max is not None and not budget_filtered and preferred_candidates:
        if budget_filter_strict:
            returned = []
            budget_gap_reason = "budget_catalog_gap"
        else:
            returned = preferred_candidates
            relaxed.append("price_max")
            budget_gap_reason = "explicit_budget_relaxation"

    # ── LLM filter layer ──
    # When deterministic filters leave fields incomplete (e.g. brand exclusion
    # that needs semantic understanding), let the LLM do a soft filter pass.
    llm_filtered = returned
    if returned and _has_incomplete_fields(requirement):
        llm_filtered = _llm_filter_products(requirement, returned)
        if not llm_filtered:
            llm_filtered = returned  # fallback: keep all if LLM removed everything

    diagnostics = FilterDiagnostics(
        category=category,
        raw_count=len(raw),
        after_stock_count=len(stock_filtered),
        after_exclusion_count=len(exclusion_filtered),
        after_brand_whitelist_count=len(exclusion_filtered) if brand_whitelist_applied else len(exclusion_filtered),
        brand_whitelist_applied=brand_whitelist_applied,
        brand_whitelist_relaxed=brand_whitelist_relaxed,
        after_target_count=len(target_filtered),
        after_must_have_count=len(must_have_filtered),
        after_budget_count=len(budget_filtered),
        after_llm_count=len(llm_filtered),
        inferred_product_type=inferred_product_type or "",
        product_type_filter_applied=product_type_filter_applied,
        product_type_candidate_count=len(product_type_filtered),
        pc_part_constraints=pc_constraints,
        pc_constraint_filter_applied=pc_constraint_filter_applied,
        pc_constraint_candidate_count=len(pc_constraint_filtered),
        pc_constraint_relaxed=pc_constraint_relaxed,
        returned_count=len(llm_filtered),
        relaxed_constraints=relaxed,
        budget_filter_strict=budget_filter_strict,
        budget_gap_reason=budget_gap_reason,
        hard_constraint_passed_ids=hard_passed_ids,
    )
    return llm_filtered, diagnostics


def is_available(product: ApiProduct) -> bool:
    if product.stock_quantity is not None and product.stock_quantity <= 0:
        return False
    status = (product.stock_status or "").lower()
    return status not in {"sold_out", "out_of_stock", "unavailable"}


def violates_brand_or_text_exclusion(requirement: RequirementSpec, product: ApiProduct) -> bool:
    """Check text-based exclusion only.

    Brand exclusion is now handled by the LLM filter layer, which can
    understand semantic relationships (e.g. sub-brands, aliases) that a
    simple string match cannot.
    """
    text = collect_product_text(product)
    for term in requirement.excluded_terms:
        key = normalize(term)
        if key and key in text:
            return True
    return False


def matches_all_required_terms(requirement: RequirementSpec, product: ApiProduct) -> bool:
    terms = [term for term in requirement.must_have_terms if term]
    if not terms:
        return True
    text = collect_product_text(product)
    return all(normalize(term) in text for term in terms)


def matches_target_sub_category(requirement: RequirementSpec, product: ApiProduct) -> bool:
    """Exact field match: product.sub_category against target_sub_categories.

    Uses the structured sub_category field, not keyword matching against text.
    """
    terms = [term for term in requirement.target_sub_categories if term]
    if not terms:
        return True
    return product.sub_category in terms


def matches_budget(requirement: RequirementSpec, product: ApiProduct) -> bool:
    price = product.min_price or product.base_price
    if requirement.price_min is not None and price < requirement.price_min:
        return False
    if requirement.price_max is not None and price > requirement.price_max:
        return False
    return True


def collect_product_text(product: ApiProduct) -> str:
    values = [
        product.product_id,
        product.title,
        product.brand,
        product.category.value,
        product.category_name,
        product.sub_category,
        product.description,
        " ".join(product.tags),
        " ".join(product.best_for),
        " ".join(product.not_good_for),
        " ".join(product.supported_scenarios),
        " ".join(f"{sku.sku_id} {' '.join(sku.properties.values())}" for sku in product.skus),
    ]
    return normalize(" ".join(values))


def normalize(value: object) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _matches_brand_requirement(product: ApiProduct, brands: List[str]) -> bool:
    """Check if a product's brand matches any of the required brands.

    Uses normalized comparison to handle sub-brands and aliases.
    E.g. brands=["华为"] matches product.brand="HUAWEI" or "华为".
    """
    if not product.brand:
        return False
    product_brand_norm = normalize(product.brand)
    for required_brand in brands:
        required_norm = normalize(required_brand)
        if not required_norm:
            continue
        # 双向子串匹配：处理子品牌和别名
        if required_norm in product_brand_norm or product_brand_norm in required_norm:
            return True
    return False


# ── LLM filter layer ────────────────────────────────────────────────────

def _has_incomplete_fields(requirement: RequirementSpec) -> bool:
    """Return True when the requirement has soft constraints that
    deterministic filters cannot fully resolve (e.g. brand exclusion
    which needs semantic understanding of sub-brands / aliases)."""
    if requirement.excluded_brands:
        return True
    return False


def _llm_filter_products(
    requirement: RequirementSpec,
    candidates: List[ApiProduct],
) -> List[ApiProduct]:
    """Use the LLM to soft-filter products against constraints that
    deterministic filters cannot handle (brand exclusion, semantic terms, etc.).

    Sends a single batched request with all candidate products.  On any
    failure (timeout, parse error, LLM unavailable) returns the original
    list unchanged so the pipeline never breaks.
    """
    try:
        from rag.recommendation.llm_client import (
            OpenAICompatibleChatClient,
            run_with_hard_timeout,
        )
    except ImportError:
        return candidates

    client = OpenAICompatibleChatClient()
    if not client.configured:
        return candidates

    # ── Build constraint description ──
    constraints: List[str] = []
    if requirement.excluded_brands:
        brands_text = "、".join(requirement.excluded_brands)
        constraints.append(
            f"用户排除品牌: {brands_text}（包括其子品牌、关联品牌、别名）"
        )

    if not constraints:
        return candidates

    # ── Build product listing (cap at 30 to stay within token budget) ──
    capped = candidates[:30]
    product_lines: List[str] = []
    for i, p in enumerate(capped):
        price = p.min_price or p.base_price or 0
        product_lines.append(
            f"{i + 1}. ID={p.product_id} | {p.title} | 品牌={p.brand or '未知'} | ¥{price}"
        )
    product_text = "\n".join(product_lines)

    constraint_text = "\n".join(f"- {c}" for c in constraints)

    prompt = (
        "你是商品筛选助手。根据用户的筛选条件，判断以下每个商品是否符合条件。\n\n"
        f"【筛选条件】\n{constraint_text}\n\n"
        f"【商品列表】\n{product_text}\n\n"
        "输出 JSON 对象，格式：{\"keep\": [保留的商品ID列表]}\n"
        "注意：\n"
        "- 排除品牌时，其子品牌、关联品牌、贴牌产品也应排除\n"
        "- 如果无法确定是否属于排除品牌，保留该商品\n"
        "- 只输出 JSON，不要解释"
    )

    try:
        _timeout = float(os.getenv("RECOMMENDATION_LLM_FILTER_TIMEOUT_SECONDS", "12"))
        result, _report = run_with_hard_timeout(
            lambda: client.chat_json_with_report(
                [{"role": "user", "content": prompt}],
                model=os.getenv("MALLMIND_LLM_FILTER_MODEL")
                or client.config.fast_model,
                temperature=0.0,
                max_tokens=500,
            ),
            _timeout,
            "llm_filter",
        )
        keep_ids = set(result.get("keep", []))
        filtered = [p for p in capped if p.product_id in keep_ids]
        # Include any products beyond the capped range (not evaluated by LLM)
        if len(candidates) > 30:
            filtered.extend(candidates[30:])
        return filtered
    except Exception as exc:
        logger.warning("LLM filter failed, keeping all candidates: %s", exc)
        return candidates
