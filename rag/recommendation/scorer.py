"""Explainable scoring for traditional ecommerce products."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from rag.recommendation.cost_estimator import estimate_product_price
from rag.recommendation.query_guards import parse_pc_part_constraints, product_pc_constraint_bonus
from rag.schemas import ApiProduct, BudgetLevel, ComponentCategory, RequirementLevel, RequirementSpec, ScoreBreakdown
from rag.schemas.recommendation import price_to_price_tier, rating_to_quality_tier


BASE_WEIGHTS: Dict[str, float] = {
    "scenario_match": 0.25,
    "attribute_match": 0.20,
    "price_fit": 0.20,
    "reputation_fit": 0.10,
    "availability_fit": 0.10,
    "sku_fit": 0.10,
    "detail_quality": 0.05,
}


@dataclass(frozen=True)
class ProductScore:
    """Explainable score result for one ecommerce product."""

    product: ApiProduct
    score: ScoreBreakdown
    weights: Dict[str, float]
    weight_reasons: List[str]
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    evidence_boost: float = 0.0
    evidence_match: float = 0.0


def score_product(
    requirement: RequirementSpec,
    product: ApiProduct,
    evidence: Optional[List[Dict[str, Any]]] = None,
) -> ProductScore:
    evidence = evidence or []
    query = requirement.raw_query
    weights, weight_reasons = build_dynamic_weights(requirement)
    # 场景和属性匹配可以从当前 product_id 的 evidence 中读取信号
    scenario = score_scenario_match(requirement, product, evidence=evidence)
    attribute = score_attribute_match(requirement, product, evidence=evidence)
    components = {
        "scenario_match": scenario,
        "attribute_match": attribute,
        "price_fit": score_price_fit(requirement, product),
        "reputation_fit": score_reputation_fit(product),
        "availability_fit": score_availability_fit(product),
        "sku_fit": score_sku_fit(product),
        "detail_quality": score_detail_quality(product),
    }
    base_score = sum(components[name] * weights[name] for name in weights)
    final_score = apply_evidence_boost(base_score, evidence, query=query)
    # ── 跨品类证据惩罚：当查询品类明确时，抑制非目标品类的 evidence boost ──
    desired_cats = requirement.desired_categories or requirement.required_components
    if desired_cats and len(desired_cats) == 1 and evidence:
        target_cat = desired_cats[0].value
        evidence_cats = {item.get("category", "") for item in evidence if item.get("category")}
        if evidence_cats and all(cat != target_cat for cat in evidence_cats):
            # 所有 evidence 来自非目标品类，回退 evidence boost 并施加惩罚
            final_score = clamp(final_score - 0.10)
    # 计算 evidence 带来的增量
    base_without_evidence = apply_evidence_boost(base_score, [])
    evidence_boost = round(final_score - base_without_evidence, 4)
    # evidence_match 记录 scenario/attribute 中因 evidence 获得的加成
    scenario_no_ev = score_scenario_match(requirement, product, evidence=None)
    attribute_no_ev = score_attribute_match(requirement, product, evidence=None)
    evidence_match = round((scenario - scenario_no_ev) + (attribute - attribute_no_ev), 4)
    reasons = build_score_reasons(requirement=requirement, product=product, components=components)
    reasons.extend(build_evidence_reasons(evidence))
    return ProductScore(
        product=product,
        score=ScoreBreakdown(
            scenario_match=round(components["scenario_match"], 4),
            attribute_match=round(components["attribute_match"], 4),
            price_fit=round(components["price_fit"], 4),
            reputation_fit=round(components["reputation_fit"], 4),
            availability_fit=round(components["availability_fit"], 4),
            sku_fit=round(components["sku_fit"], 4),
            detail_quality=round(components["detail_quality"], 4),
            final_score=round(final_score, 4),
            reasons=reasons + weight_reasons,
        ),
        weights=weights,
        weight_reasons=weight_reasons,
        evidence=evidence,
        evidence_boost=evidence_boost,
        evidence_match=evidence_match,
    )


def score_products(
    requirement: RequirementSpec,
    products: Iterable[ApiProduct],
    evidence_by_product_id: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[ProductScore]:
    evidence_by_product_id = evidence_by_product_id or {}
    scored = [
        score_product(
            requirement=requirement,
            product=product,
            evidence=evidence_by_product_id.get(product.product_id, []),
        )
        for product in products
        if not violates_exclusions(requirement, product)
    ]
    scored = apply_price_aware_price_fit(requirement, scored)
    scored = apply_pc_constraint_boost(requirement, scored)
    return sorted(scored, key=lambda item: item.score.final_score, reverse=True)


def violates_exclusions(requirement: RequirementSpec, product: ApiProduct) -> bool:
    text = _collect_product_text(product)
    if product.category in set(requirement.excluded_categories):
        return True
    if product.brand and product.brand in set(requirement.excluded_brands):
        return True
    return any(term and term.lower() in text for term in requirement.excluded_terms)


def apply_price_aware_price_fit(requirement: RequirementSpec, scored: List[ProductScore]) -> List[ProductScore]:
    price_infos: Dict[str, Tuple[Optional[float], str]] = {}
    known_costs: List[float] = []
    for item in scored:
        price, currency, _ = estimate_product_price(requirement, item.product)
        price_infos[item.product.product_id] = (price, currency)
        if price is not None:
            known_costs.append(max(price, 0.0))
    if not known_costs:
        return scored

    min_cost = min(known_costs)
    max_cost = max(known_costs)
    adjusted = []
    for item in scored:
        price, currency = price_infos[item.product.product_id]
        price_fit = price_fit_from_product_price(requirement, price, min_cost, max_cost)
        adjusted.append(rebuild_product_score_with_price_fit(item, price_fit, price, currency))
    return adjusted


def apply_pc_constraint_boost(requirement: RequirementSpec, scored: List[ProductScore]) -> List[ProductScore]:
    constraints = parse_pc_part_constraints(requirement.raw_query)
    if not constraints:
        return scored
    adjusted: List[ProductScore] = []
    for item in scored:
        bonus = product_pc_constraint_bonus(item.product, constraints)
        if bonus <= 0:
            adjusted.append(item)
            continue
        old = item.score
        new_score = old.model_copy(
            update={
                "final_score": round(clamp(old.final_score + bonus), 4),
                "reasons": [*old.reasons, "结构化规格与用户明确 PC 配件属性更匹配。"],
            }
        )
        adjusted.append(
            ProductScore(
                product=item.product,
                score=new_score,
                weights=item.weights,
                weight_reasons=item.weight_reasons,
                evidence=item.evidence,
                evidence_boost=item.evidence_boost,
                evidence_match=item.evidence_match,
            )
        )
    return adjusted


def price_fit_from_product_price(
    requirement: RequirementSpec,
    product_price: Optional[float],
    min_cost: float,
    max_cost: float,
) -> float:
    if product_price is None:
        return 0.2
    if requirement.price_max is not None:
        if product_price <= requirement.price_max:
            return clamp(1.0 - max(product_price - (requirement.price_min or 0), 0) / max(requirement.price_max, 1))
        return clamp(0.35 - (product_price - requirement.price_max) / max(requirement.price_max * 3, 1))
    if max_cost <= min_cost:
        affordability = 1.0
    else:
        affordability = 1.0 - ((product_price - min_cost) / (max_cost - min_cost))
    if requirement.budget_level == BudgetLevel.low:
        return clamp(affordability)
    if requirement.budget_level == BudgetLevel.medium:
        return clamp(1.0 - abs(affordability - 0.55) / 0.55)
    if requirement.budget_level == BudgetLevel.high:
        return clamp(0.45 + (1.0 - affordability) * 0.35 + affordability * 0.20)
    return clamp(0.45 + affordability * 0.55)


def rebuild_product_score_with_price_fit(
    item: ProductScore,
    price_fit: float,
    product_price: Optional[float],
    currency: str,
) -> ProductScore:
    old = item.score
    components = {
        "scenario_match": old.scenario_match,
        "attribute_match": old.attribute_match,
        "price_fit": price_fit,
        "reputation_fit": old.reputation_fit,
        "availability_fit": old.availability_fit,
        "sku_fit": old.sku_fit,
        "detail_quality": old.detail_quality,
    }
    base_score = sum(components[name] * item.weights[name] for name in item.weights)
    # 重算 evidence boost（price_fit 变了，但 evidence boost 不变）
    base_without_evidence = apply_evidence_boost(base_score, [])
    final_score = apply_evidence_boost(base_score, item.evidence)
    evidence_boost = round(final_score - base_without_evidence, 4)
    reasons = build_price_aware_reasons(item.product, product_price, currency) + [
        reason for reason in old.reasons if not reason.startswith("价格匹配：")
    ]
    return ProductScore(
        product=item.product,
        score=ScoreBreakdown(
            scenario_match=old.scenario_match,
            attribute_match=old.attribute_match,
            price_fit=round(price_fit, 4),
            reputation_fit=old.reputation_fit,
            availability_fit=old.availability_fit,
            sku_fit=old.sku_fit,
            detail_quality=old.detail_quality,
            final_score=round(final_score, 4),
            reasons=reasons,
        ),
        weights=item.weights,
        weight_reasons=item.weight_reasons,
        evidence=item.evidence,
        evidence_boost=evidence_boost,
        evidence_match=item.evidence_match,
    )


def build_price_aware_reasons(product: ApiProduct, product_price: Optional[float], currency: str) -> List[str]:
    if product_price is None:
        return ["价格匹配：该商品缺少可比较价格，预算敏感场景会降低优先级。"]
    return [f"价格匹配：当前最低 SKU 约 {product_price:g} {currency}，已用于预算适配评分。"]


def apply_evidence_boost(base_score: float, evidence: List[Dict[str, Any]], query: str = "") -> float:
    if not evidence:
        return clamp(base_score)
    best_hit = max(float(item.get("score") or 0.0) for item in evidence)
    base_boost = min(best_hit, 1.0) * 0.07 + min(len(evidence), 3) / 3 * 0.05
    boost = min(base_boost, 0.12)
    if _has_strong_evidence_match(evidence, query):
        boost = min(base_boost, 0.16)
    return round(clamp(base_score + boost), 4)


# ── evidence strong-match helpers ──────────────────────────────────────────

# 核心商品词/属性词：这些词如果命中 evidence title/text/sub_category，表示强相关
# 注意不包含泛词如"推荐""适合""帮我""看看""想买""求推荐""配一套""装备""开学"等
_CORE_PRODUCT_TERMS = {
    # 品类词
    "耳机", "蓝牙耳机", "降噪豆", "键盘", "鼠标", "手机", "平板", "显示器", "笔记本",
    "鞋", "跑鞋", "运动鞋", "篮球鞋", "徒步鞋", "训练鞋",
    "外套", "运动裤", "运动上衣", "T恤", "短袖", "卫衣", "裤", "裙", "羽绒服", "冲锋衣",
    "面霜", "精华", "防晒", "眼霜", "洗面奶", "乳液",
    "咖啡", "零食", "饮料", "功能饮料", "坚果", "方便食品",
    "显卡", "CPU", "处理器", "主板", "内存", "SSD", "固态", "硬盘", "电源", "机箱", "散热",
    # 属性词
    "缓震", "降噪", "音质", "通勤", "跑步", "训练", "篮球", "实战",
    "油皮", "干皮", "敏感肌", "保湿", "补水", "控油", "防晒",
    "拍照", "续航", "快充", "便携", "无糖", "低糖",
    "透气", "防水", "黑色", "白色", "蓝色", "静音", "轻量",
    "送礼", "礼物", "学生", "办公", "游戏", "运动", "旅行", "日用",
    # 品牌词
    "耐克", "阿迪达斯", "小米", "华为", "苹果", "雅诗兰黛", "科颜氏", "兰蔻",
    "Nike", "Adidas", "Apple", "Sony", "索尼", "三星",
}


def _has_strong_evidence_match(evidence: List[Dict[str, Any]], query: str) -> bool:
    """Check if any evidence chunk in top-3 by score has title/text/sub_category
    matching a core product/attribute term from the query."""
    if not query or not evidence:
        return False
    query_terms = _extract_core_query_terms(query)
    if not query_terms:
        return False
    ranked = sorted(evidence, key=lambda item: float(item.get("score") or 0.0), reverse=True)
    for item in ranked[:3]:
        chunk_text = " ".join(
            str(item.get(key) or "")
            for key in ("title", "text", "sub_category", "filename")
        ).lower()
        if any(term.lower() in chunk_text for term in query_terms):
            return True
    return False


def _extract_core_query_terms(query: str) -> set:
    """Extract core product/attribute terms from query by intersection with the
    known core term set.  Does NOT match generic shopping words."""
    lowered = query.lower()
    return {term for term in _CORE_PRODUCT_TERMS if term.lower() in lowered}


def _evidence_scenario_bonus(query: str, evidence: List[Dict[str, Any]]) -> float:
    """Small scenario-match bonus when evidence chunk titles/texts contain
    core product or scenario terms from the query (max +0.05)."""
    query_terms = _extract_core_query_terms(query)
    if not query_terms:
        return 0.0
    matched = 0
    for item in evidence[:5]:
        chunk_text = " ".join(
            str(item.get(key) or "")
            for key in ("title", "text", "sub_category", "filename")
        ).lower()
        if any(term.lower() in chunk_text for term in query_terms):
            matched += 1
    return min(matched * 0.016, 0.05)


def _evidence_attribute_bonus(query: str, evidence: List[Dict[str, Any]]) -> float:
    """Small attribute-match bonus when evidence chunks contain core attribute
    terms from the query that also appear in must_have_terms / preferences
    pattern (max +0.05)."""
    query_terms = _extract_core_query_terms(query)
    if not query_terms:
        return 0.0
    matched = 0
    for item in evidence[:5]:
        chunk_text = " ".join(
            str(item.get(key) or "")
            for key in ("title", "text", "sub_category", "filename")
        ).lower()
        if any(term.lower() in chunk_text for term in query_terms):
            matched += 1
    return min(matched * 0.016, 0.05)


def build_evidence_reasons(evidence: List[Dict[str, Any]]) -> List[str]:
    if not evidence:
        return ["当前主要依据结构化商品详情、FAQ、SKU 与用户评价评分。"]
    snippets = []
    for item in evidence[:2]:
        label = item.get("filename") or item.get("product_id") or "商品证据"
        snippets.append(f"{label} 召回分 {float(item.get('score') or 0):.4f}")
    return [f"商品知识证据命中：{'; '.join(snippets)}。"]


def build_dynamic_weights(requirement: RequirementSpec) -> Tuple[Dict[str, float], List[str]]:
    weights = dict(BASE_WEIGHTS)
    reasons: List[str] = []
    if requirement.budget_level == BudgetLevel.low or requirement.price_max is not None:
        weights["price_fit"] += 0.12
        weights["scenario_match"] -= 0.04
        weights["detail_quality"] -= 0.03
        weights["sku_fit"] -= 0.05
        reasons.append("用户给出低预算或价格上限，价格适配权重提高。")
    if requirement.quality_requirement == RequirementLevel.high:
        weights["attribute_match"] += 0.08
        weights["detail_quality"] += 0.04
        weights["price_fit"] -= 0.06
        weights["availability_fit"] -= 0.06
        reasons.append("用户更关注品质/效果，属性匹配与详情证据权重提高。")
    if requirement.need_bundle:
        weights["scenario_match"] += 0.04
        weights["attribute_match"] += 0.04
        weights["price_fit"] -= 0.04
        weights["reputation_fit"] -= 0.04
        reasons.append("用户需要一整套方案，场景匹配和跨品类互补性权重提高。")
    if requirement.need_comparison:
        weights["detail_quality"] += 0.05
        weights["attribute_match"] += 0.03
        weights["sku_fit"] -= 0.03
        weights["availability_fit"] -= 0.05
        reasons.append("用户有对比决策需求，FAQ、评价和属性完整度权重提高。")
    if requirement.need_multimodal:
        weights["scenario_match"] += 0.03
        weights["detail_quality"] += 0.03
        weights["price_fit"] -= 0.03
        weights["reputation_fit"] -= 0.03
        reasons.append("用户包含图片/拍照找货意图，商品图片与详情证据会影响排序。")
    return normalize_weights(weights), reasons


def score_scenario_match(
    requirement: RequirementSpec,
    product: ApiProduct,
    evidence: Optional[List[Dict[str, Any]]] = None,
) -> float:
    query = requirement.raw_query.lower()
    text = _collect_product_text(product)
    score = 0.25
    if product.category in set(requirement.desired_categories or requirement.required_components):
        score += 0.35
    if product.sub_category and product.sub_category in query:
        score += 0.20
    if product.brand and product.brand in query:
        score += 0.15
    term_hits = sum(1 for term in requirement.preferences + requirement.must_have_terms if term and term.lower() in text)
    score += min(term_hits * 0.08, 0.24)
    if requirement.occasion and requirement.occasion.lower() in text:
        score += 0.12
    if requirement.target_user and requirement.target_user.lower() in text:
        score += 0.08
    for scenario in product.supported_scenarios:
        if scenario and (scenario.lower() in query or scenario in requirement.scenario):
            score += 0.06
            break
    # evidence 场景信号：仅匹配 query 中的核心商品词/场景词，不用泛词
    if evidence:
        evidence_scenario_bonus = _evidence_scenario_bonus(query, evidence)
        score += evidence_scenario_bonus
    return clamp(score)


def score_attribute_match(
    requirement: RequirementSpec,
    product: ApiProduct,
    evidence: Optional[List[Dict[str, Any]]] = None,
) -> float:
    score = 0.45
    if product.category in set(requirement.desired_categories or requirement.required_components):
        score += 0.25
    if product.sub_category in requirement.target_sub_categories:
        score += 0.15
    if product.brand in requirement.brands:
        score += 0.10
    text = _collect_product_text(product)
    if requirement.must_have_terms:
        hits = sum(1 for term in requirement.must_have_terms if term and term.lower() in text)
        score += min(hits / max(len(requirement.must_have_terms), 1) * 0.20, 0.20)
    if product.skus:
        score += 0.05
    # evidence 属性信号：仅匹配 query 中的核心属性词，不用泛词
    if evidence:
        evidence_attr_bonus = _evidence_attribute_bonus(requirement.raw_query, evidence)
        score += evidence_attr_bonus
    return clamp(score)


def score_price_fit(requirement: RequirementSpec, product: ApiProduct) -> float:
    price = product.min_price or product.base_price
    price_tier = price_to_price_tier(price)
    quality_tier = rating_to_quality_tier(product.rating_avg, product.review_count)
    if requirement.price_max is not None:
        if price <= requirement.price_max:
            return clamp(0.75 + (requirement.price_max - price) / max(requirement.price_max, 1) * 0.25)
        return clamp(0.35 - (price - requirement.price_max) / max(requirement.price_max * 3, 1))
    affordability = 1.0 - normalize_level(price_tier)
    if requirement.budget_level == BudgetLevel.low:
        return clamp(affordability)
    if requirement.budget_level == BudgetLevel.medium:
        return clamp(1.0 - abs(price_tier - 3) / 4)
    if requirement.budget_level == BudgetLevel.high:
        return clamp(normalize_level(quality_tier) * 0.6 + (1 - affordability) * 0.4)
    return clamp(0.5 + affordability * 0.5)


def score_reputation_fit(product: ApiProduct) -> float:
    if product.rating_avg is None:
        return 0.55
    rating_score = product.rating_avg / 5
    volume_bonus = min(product.review_count, 5) / 20
    return clamp(rating_score + volume_bonus)


def score_availability_fit(product: ApiProduct) -> float:
    if product.stock_status.startswith("available"):
        return 0.95
    if product.stock_status in {"in_stock", "available"}:
        return 1.0
    if product.stock_status in {"unknown", ""}:
        return 0.65
    return 0.25


def score_sku_fit(product: ApiProduct) -> float:
    score = 0.55
    if product.skus:
        score += 0.20
    if product.image_url:
        score += 0.10
    if product.min_price and product.max_price:
        score += 0.10
    if product.stock_quantity is not None:
        score += 0.05
    return clamp(score)


def score_detail_quality(product: ApiProduct) -> float:
    score = 0.35
    if product.description:
        score += 0.20
    if product.faqs:
        score += min(len(product.faqs), 5) * 0.06
    if product.reviews:
        score += min(len(product.reviews), 5) * 0.03
    return clamp(score)


def build_score_reasons(
    requirement: RequirementSpec,
    product: ApiProduct,
    components: Dict[str, float],
) -> List[str]:
    reasons = []
    if product.category in set(requirement.desired_categories or requirement.required_components):
        reasons.append(f"{product.title} 属于用户关注的 {product.category_name} 类目。")
    if components["scenario_match"] >= 0.75:
        reasons.append("商品标题、标签、FAQ 或详情与用户场景匹配度较高。")
    elif components["scenario_match"] < 0.45:
        reasons.append("商品与用户表达的场景直接匹配证据较少。")
    if components["price_fit"] >= 0.75:
        reasons.append("价格与预算要求较匹配。")
    elif components["price_fit"] < 0.45:
        reasons.append("价格可能超出用户预算，需要谨慎推荐。")
    if product.rating_avg:
        reasons.append(f"用户评价均分 {product.rating_avg:g}/5，参与口碑评分。")
    if product.faqs:
        reasons.append(f"商品提供 {len(product.faqs)} 条官方 FAQ，可作为回答证据。")
    if product.not_good_for:
        reasons.append("不适用提示：" + "；".join(product.not_good_for[:2]) + "。")
    return reasons


def normalize_level(value: int) -> float:
    return clamp((value - 1) / 4)


def average(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def normalize_weights(weights: Dict[str, float]) -> Dict[str, float]:
    cleaned = {name: max(value, 0.0) for name, value in weights.items() if name in BASE_WEIGHTS}
    total = sum(cleaned.values())
    if total <= 0:
        return dict(BASE_WEIGHTS)
    return {name: value / total for name, value in cleaned.items()}


def score_modality_fit(requirement: RequirementSpec, product: ApiProduct) -> Optional[float]:
    if not requirement.need_multimodal:
        return None
    return 1.0 if product.image_url else 0.35


def _collect_product_text(product: ApiProduct) -> str:
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
        " ".join(f"{faq.question} {faq.answer}" for faq in product.faqs[:3]),
        " ".join(review.content for review in product.reviews[:3]),
    ]
    for sku in product.skus:
        values.extend(str(value) for value in sku.properties.values())
    return " ".join(values).lower()
