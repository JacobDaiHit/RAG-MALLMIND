"""Build ecommerce product recommendations from scored catalog candidates."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Dict, Iterable, List, Optional

from rag.recommendation.cost_estimator import estimate_plan_cost
from rag.recommendation.image_retrieval import ImageRetrievalEvidence
from rag.recommendation.intent_router import route_shopping_intent
from rag.recommendation.product_loader import ProductCatalog, load_catalog_for_scope
from rag.recommendation.query_guards import budget_relaxation_allowed, clarification_required, detect_no_match_reason, infer_product_type, is_pc_query, requested_missing_subcategory
from rag.recommendation.retrieval import RetrievalEvidence, evidence_summary, retrieve_requirement_evidence
from rag.recommendation.scorer import ProductScore, score_products
from rag.recommendation.structured_filter import filter_products_for_requirement
from rag.schemas import (
    ApiProduct,
    ComponentCategory,
    CostEstimate,
    RecommendationPlan,
    RecommendationResult,
    RecommendationType,
    RequirementSpec,
    SelectedComponent,
)
from rag.utils.catalog_scope import normalize_catalog_scope


MILVUS_RETRIEVAL_ENABLED = os.getenv(
    "RECOMMENDATION_ENABLE_MILVUS",
    os.getenv("RECOMMENDATION_USE_MILVUS", "false"),
).lower() == "true"
RETRIEVAL_TIMEOUT_SECONDS = float(os.getenv("RECOMMENDATION_RETRIEVAL_TIMEOUT_SECONDS", "8"))


def build_recommendation_result(
    requirement: RequirementSpec,
    catalog: Optional[ProductCatalog] = None,
    catalog_scope: str = "ecommerce",
    use_milvus_retrieval: bool = True,
    use_rag_query_expansion: bool = False,
    image_retrieval_evidence: Optional[ImageRetrievalEvidence] = None,
) -> RecommendationResult:
    """Build one ecommerce-native recommendation plan from catalog candidates."""

    normalized_scope = normalize_catalog_scope(catalog_scope)
    pc_route_detected = is_pc_query(requirement.raw_query)
    if pc_route_detected and normalized_scope != "pc_parts":
        normalized_scope = "pc_parts"
    catalog = catalog or load_catalog_for_scope(normalized_scope)
    recommendation_domain = recommendation_domain_for_scope(normalized_scope)
    # ── clarification_required 不再拦截宽泛查询，让大模型兜底 ──
    _ = clarification_required(requirement.raw_query)
    if normalized_scope == "pc_parts":
        requirement = ensure_pc_part_requirement(requirement, catalog)
    no_match_reason = detect_no_match_reason(requirement.raw_query, price_max=requirement.price_max)
    if no_match_reason and (normalized_scope != "pc_parts" or no_match_reason == "budget_impossible"):
        return build_no_recommendation_result(
            requirement=requirement,
            catalog=catalog,
            catalog_scope=normalized_scope,
            recommendation_domain=recommendation_domain,
            no_match_reason=no_match_reason,
            pc_route_detected=pc_route_detected,
        )
    missing_subcategory = requested_missing_subcategory(requirement.raw_query, catalog.products)
    if missing_subcategory and normalized_scope != "pc_parts":
        return build_no_recommendation_result(
            requirement=requirement,
            catalog=catalog,
            catalog_scope=normalized_scope,
            recommendation_domain=recommendation_domain,
            no_match_reason=missing_subcategory["no_match_reason"],
            pc_route_detected=pc_route_detected,
            trace_extras=missing_subcategory,
        )
    retrieval_evidence = retrieve_evidence_with_timeout(
        requirement,
        use_milvus_retrieval,
        use_rag_query_expansion=use_rag_query_expansion,
    )
    fused_evidence = fuse_text_and_image_evidence(retrieval_evidence, image_retrieval_evidence)
    grouped_scores, filter_diagnostics = score_required_components(requirement, catalog, fused_evidence)
    budget_gap_categories = [
        category.value
        for category, diagnostics in filter_diagnostics.items()
        if diagnostics.budget_gap_reason == "budget_catalog_gap"
    ]
    if budget_gap_categories and not any(grouped_scores.values()):
        return build_no_recommendation_result(
            requirement=requirement,
            catalog=catalog,
            catalog_scope=normalized_scope,
            recommendation_domain=recommendation_domain,
            no_match_reason="budget_catalog_gap",
            pc_route_detected=pc_route_detected,
            trace_extras={
                "budget_gap_reason": "budget_catalog_gap",
                "budget_gap_categories": budget_gap_categories,
                "structured_filter": {
                    category.value: diagnostics.to_trace()
                    for category, diagnostics in filter_diagnostics.items()
                },
            },
        )
    intent_route = route_shopping_intent(requirement)
    plan = build_recommendation_plan(requirement, grouped_scores)
    plans = [plan]
    product_cards = build_product_cards(plans, grouped_scores, requirement=requirement)

    # ── 后置预算执行层：确保明确预算不被穿透 ──
    if requirement.price_max is not None and product_cards:
        budget_strict = not budget_relaxation_allowed(requirement.raw_query)
        if budget_strict:
            budget_enforced_cards = [
                card for card in product_cards
                if not card.get("price") or card["price"] <= requirement.price_max
            ]
            if not budget_enforced_cards:
                return build_no_recommendation_result(
                    requirement=requirement,
                    catalog=catalog,
                    catalog_scope=normalized_scope,
                    recommendation_domain=recommendation_domain,
                    no_match_reason="budget_catalog_gap",
                    pc_route_detected=pc_route_detected,
                    trace_extras={
                        "budget_gap_reason": "budget_catalog_gap",
                        "post_budget_enforcement": True,
                        "pre_enforcement_card_count": len(product_cards),
                        "price_max": requirement.price_max,
                        "structured_filter": {
                            category.value: diagnostics.to_trace()
                            for category, diagnostics in filter_diagnostics.items()
                        },
                    },
                )
            product_cards = budget_enforced_cards

    candidate_scope = build_candidate_scope(requirement, catalog, grouped_scores)
    comparison_table = build_comparison_table(grouped_scores)
    missing_fields = list(requirement.missing_fields)
    missing_categories = [
        category.value
        for category in requirement.desired_categories
        if not grouped_scores.get(category)
    ]
    if missing_categories:
        missing_fields.append(f"missing_categories: {', '.join(missing_categories)}")

    return RecommendationResult(
        requirement=requirement,
        plans=plans,
        candidate_count=len(catalog.products),
        product_cards=product_cards,
        candidate_scope=candidate_scope,
        comparison_table=comparison_table,
        intent_route=intent_route,
        missing_fields=missing_fields,
        risks=collect_result_risks(plans),
        trace={
            "catalog_source": str(catalog.source_path),
            "catalog_scope": normalized_scope,
            "catalog_product_count": len(catalog.products),
            "recommendation_domain": recommendation_domain,
            "input_preprocessor": "text/image/audio signals are normalized before parse_requirement at API entrypoints",
            "intent_route": intent_route,
            "candidate_scope": candidate_scope,
            "structured_filter": {
                category.value: diagnostics.to_trace()
                for category, diagnostics in filter_diagnostics.items()
            },
            "inferred_product_type": infer_product_type(requirement.raw_query),
            "pc_route_detected": pc_route_detected,
            "retrieval": retrieval_evidence.to_trace(),
            "milvus_retrieval": retrieval_evidence.to_trace(),
            "runtime_retrieval_policy": {
                "use_milvus_retrieval": use_milvus_retrieval,
                "use_rag_query_expansion": use_rag_query_expansion,
            },
            "image_retrieval": (image_retrieval_evidence or ImageRetrievalEvidence()).to_trace(),
            "fused_retrieval": fused_evidence.to_trace(),
            "desired_categories": [item.value for item in requirement.desired_categories],
            "candidate_counts_by_category": {
                category.value: len(scores)
                for category, scores in grouped_scores.items()
            },
            "dynamic_weights": collect_dynamic_weights(grouped_scores),
            "dynamic_weight_reasons": collect_weight_reasons(grouped_scores),
            "evidence_boost_max": _evidence_boost_max(grouped_scores),
            "evidence_match_max": _evidence_match_max(grouped_scores),
            "evidence_boost_any": _evidence_boost_any(grouped_scores),
            "evidence_boost_per_category": _evidence_boost_per_category(grouped_scores),
        },
    )


def build_no_recommendation_result(
    *,
    requirement: RequirementSpec,
    catalog: ProductCatalog,
    catalog_scope: str,
    recommendation_domain: str,
    no_match_reason: str,
    pc_route_detected: bool,
    trace_extras: Optional[Dict[str, object]] = None,
) -> RecommendationResult:
    candidate_scope = {
        "active_filters": {
            "categories": [category.value for category in (requirement.desired_categories or requirement.required_components)],
            "price_min": requirement.price_min,
            "price_max": requirement.price_max,
            "brands": list(requirement.brands),
            "excluded_brands": list(requirement.excluded_brands),
            "must_have_terms": list(requirement.must_have_terms),
            "excluded_terms": list(requirement.excluded_terms),
            "preferences": list(requirement.preferences),
        },
        "total_catalog_count": len(catalog.products),
        "by_category": {},
        "clarification_needed": [no_match_reason],
    }
    trace = {
        "catalog_source": str(catalog.source_path),
        "catalog_scope": catalog_scope,
        "catalog_product_count": len(catalog.products),
        "recommendation_domain": recommendation_domain,
        "candidate_scope": candidate_scope,
        "desired_categories": [item.value for item in requirement.desired_categories],
        "candidate_counts_by_category": {},
        "inferred_product_type": infer_product_type(requirement.raw_query),
        "product_type_filter_applied": False,
        "product_type_candidate_count": 0,
        "no_match_reason": no_match_reason,
        "fallback_blocked_reason": no_match_reason,
        "pc_route_detected": pc_route_detected,
    }
    if trace_extras:
        trace.update(trace_extras)
    follow_up_questions = list((trace_extras or {}).get("clarification_questions") or [no_match_reason])
    return RecommendationResult(
        requirement=requirement,
        plans=[],
        candidate_count=len(catalog.products),
        product_cards=[],
        candidate_scope=candidate_scope,
        comparison_table=[],
        intent_route={
            "route": "no_recommendation",
            "task_type": "clarify_or_no_recommendation",
            "supported_now": True,
            "reason": no_match_reason,
        },
        missing_fields=[no_match_reason],
        risks=[no_match_reason],
        follow_up_questions=follow_up_questions,
        trace=trace,
    )


def recommendation_domain_for_scope(scope: str) -> str:
    if scope == "pc_parts":
        return "single_pc_part"
    if scope == "combined":
        return "combined"
    return "ecommerce"


def ensure_pc_part_requirement(requirement: RequirementSpec, catalog: ProductCatalog) -> RequirementSpec:
    categories = [category for category in catalog.by_category.keys() if category.value.startswith("pc_")]
    if not categories:
        return requirement
    current = list(requirement.desired_categories or requirement.required_components)
    pc_current = [category for category in current if category.value.startswith("pc_")]
    if pc_current:
        categories = pc_current
    else:
        detected = detect_pc_part_categories(requirement.raw_query)
        if detected:
            categories = detected
    return requirement.model_copy(
        update={
            "desired_categories": categories,
            "required_components": categories,
            "need_bundle": False,
            "task_type": "single_product_recommendation",
        }
    )


def detect_pc_part_categories(query: str) -> List[ComponentCategory]:
    text = (query or "").lower()
    compact = "".join(ch.lower() for ch in str(query or "") if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    explicit_role_terms = [
        (ComponentCategory.pc_motherboard, ["主板", "motherboard"]),
        (ComponentCategory.pc_case, ["机箱", "case"]),
        (ComponentCategory.pc_psu, ["电源", "psu"]),
        (ComponentCategory.pc_memory, ["内存", "memory"]),
        (ComponentCategory.pc_storage, ["ssd", "固态", "硬盘", "存储"]),
        (ComponentCategory.pc_cpu, ["cpu", "处理器"]),
        (ComponentCategory.pc_gpu, ["显卡", "gpu", "rtx", "rx "]),
        (ComponentCategory.pc_cooler, ["散热", "cooler", "水冷", "风冷"]),
    ]
    for category, terms in explicit_role_terms:
        if any(term in text or "".join(ch.lower() for ch in term if ch.isalnum() or "\u4e00" <= ch <= "\u9fff") in compact for term in terms):
            return [category]
    mapping = [
        (ComponentCategory.pc_gpu, ["显卡", "gpu", "rtx", "4070", "4060", "4080", "4090", "rx "]),
        (ComponentCategory.pc_cpu, ["cpu", "处理器"]),
        (ComponentCategory.pc_motherboard, ["主板", "motherboard"]),
        (ComponentCategory.pc_memory, ["内存", "memory", "ddr4", "ddr5"]),
        (ComponentCategory.pc_storage, ["ssd", "固态", "硬盘", "存储"]),
        (ComponentCategory.pc_psu, ["电源", "psu"]),
        (ComponentCategory.pc_case, ["机箱", "case"]),
        (ComponentCategory.pc_cooler, ["散热", "cooler", "水冷", "风冷"]),
    ]
    mapping.extend(
        [
            (ComponentCategory.pc_gpu, ["显卡", "gpu", "rtx", "4070", "4060", "4080", "4090", "rx "]),
            (ComponentCategory.pc_cpu, ["cpu", "处理器"]),
            (ComponentCategory.pc_motherboard, ["主板", "motherboard"]),
            (ComponentCategory.pc_memory, ["内存", "memory", "ddr4", "ddr5"]),
            (ComponentCategory.pc_storage, ["固态", "硬盘", "存储", "ssd"]),
            (ComponentCategory.pc_psu, ["电源", "psu"]),
            (ComponentCategory.pc_case, ["机箱", "case"]),
            (ComponentCategory.pc_cooler, ["散热", "风冷", "水冷", "cooler"]),
        ]
    )
    detected: List[ComponentCategory] = []
    for category, terms in mapping:
        if category in detected:
            continue
        if any(term in text for term in terms):
            detected.append(category)
    return detected


def retrieve_evidence_with_timeout(
    requirement: RequirementSpec,
    use_milvus_retrieval: bool,
    use_rag_query_expansion: bool = False,
) -> RetrievalEvidence:
    if not use_milvus_retrieval or not MILVUS_RETRIEVAL_ENABLED:
        return RetrievalEvidence(
            status="disabled",
            error="Milvus product evidence retrieval is disabled; using structured ecommerce catalog scoring.",
            query_expansion_enabled=use_rag_query_expansion,
        )
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(
        retrieve_requirement_evidence,
        requirement,
        requirement.desired_categories,
        use_query_expansion=use_rag_query_expansion,
    )
    try:
        return future.result(timeout=RETRIEVAL_TIMEOUT_SECONDS)
    except TimeoutError:
        future.cancel()
        return RetrievalEvidence(
            status="timeout",
            error=f"Product evidence retrieval exceeded {RETRIEVAL_TIMEOUT_SECONDS:.1f}s and fell back to structured scoring.",
            query_expansion_enabled=use_rag_query_expansion,
        )
    finally:
        executor.shutdown(wait=False)


def fuse_text_and_image_evidence(
    text_evidence: RetrievalEvidence,
    image_evidence: Optional[ImageRetrievalEvidence],
) -> RetrievalEvidence:
    """Merge optional image-vector hits into the existing evidence boost path."""

    if not image_evidence or not image_evidence.by_product_id:
        return text_evidence
    by_product_id: Dict[str, List[Dict[str, object]]] = {
        product_id: list(items)
        for product_id, items in text_evidence.by_product_id.items()
    }
    for product_id, hits in image_evidence.by_product_id.items():
        for hit in hits:
            score = float(hit.get("score") or 0.0)
            by_product_id.setdefault(product_id, []).append(
                {
                    "product_id": product_id,
                    "filename": "product_image",
                    "chunk_type": "image_vector",
                    "doc_type": "image_vector",
                    "category": hit.get("category", ""),
                    "brand": "",
                    "title": hit.get("title", ""),
                    "chunk_id": f"image_vector:{product_id}",
                    "score": score,
                    "text": f"商品图片向量相似度 {score:.4f}",
                    "retrieval_mode": hit.get("retrieval_mode", "image_vector"),
                    "embedding_version": hit.get("embedding_version", ""),
                }
            )
    by_category: Dict[str, int] = dict(text_evidence.by_category)
    for hits in image_evidence.by_product_id.values():
        for hit in hits:
            category = str(hit.get("category") or "")
            if category:
                by_category[category] = by_category.get(category, 0) + 1
    status = text_evidence.status
    if image_evidence.status == "ok":
        status = "ok" if status not in {"failed"} else "partial"
    return RetrievalEvidence(
        by_product_id=by_product_id,
        by_category=by_category,
        total_hits=text_evidence.total_hits + image_evidence.total_hits,
        status=status,
        error=text_evidence.error,
        query_variants=text_evidence.query_variants,
        query_expansion_enabled=text_evidence.query_expansion_enabled,
        postprocess=[
            *text_evidence.postprocess,
            {
                "retrieval_mode": "image_vector",
                "status": image_evidence.status,
                "total_hits": image_evidence.total_hits,
            },
        ],
    )


def score_required_components(
    requirement: RequirementSpec,
    catalog: ProductCatalog,
    retrieval_evidence: Optional[RetrievalEvidence] = None,
) -> tuple[Dict[ComponentCategory, List[ProductScore]], Dict[ComponentCategory, object]]:
    grouped: Dict[ComponentCategory, List[ProductScore]] = {}
    diagnostics_by_category: Dict[ComponentCategory, object] = {}
    evidence_by_id = retrieval_evidence.by_product_id if retrieval_evidence else {}
    categories = requirement.desired_categories or requirement.required_components
    for category in categories:
        products, diagnostics = filter_products_for_requirement(
            requirement=requirement,
            products=catalog.products,
            category=category,
        )
        diagnostics_by_category[category] = diagnostics
        grouped[category] = score_products(requirement, products, evidence_by_product_id=evidence_by_id)
    return grouped, diagnostics_by_category


def build_recommendation_plan(
    requirement: RequirementSpec,
    grouped_scores: Dict[ComponentCategory, List[ProductScore]],
) -> RecommendationPlan:
    selected: List[SelectedComponent] = []
    for category in requirement.desired_categories or requirement.required_components:
        score = select_recommended_product(grouped_scores.get(category, []))
        if score is None:
            continue
        selected.append(to_selected_component(score))

    recommendation_type = infer_recommendation_type(requirement, selected)
    cost_estimate = estimate_plan_cost(requirement, selected)
    risks = build_plan_risks(selected)
    risks.extend(build_budget_risks(requirement, cost_estimate))
    return RecommendationPlan(
        recommendation_type=recommendation_type,
        title=build_recommendation_title(recommendation_type),
        summary=build_plan_summary(recommendation_type, selected, requirement),
        components=selected,
        cost_estimate=cost_estimate,
        pros=build_plan_pros(recommendation_type, selected),
        cons=build_plan_cons(recommendation_type, selected),
        suitable_for=build_suitable_for(recommendation_type, requirement),
        risks=dedupe(risks),
        evidence=build_evidence(selected),
        score_table=build_score_table(selected),
    )


def build_plan(
    requirement: RequirementSpec,
    grouped_scores: Dict[ComponentCategory, List[ProductScore]],
) -> RecommendationPlan:
    """Compatibility wrapper for callers that still import `build_plan`."""

    return build_recommendation_plan(requirement, grouped_scores)


def select_recommended_product(scores: List[ProductScore]) -> Optional[ProductScore]:
    if not scores:
        return None
    return scores[0]


def infer_recommendation_type(
    requirement: RequirementSpec,
    selected: List[SelectedComponent],
) -> RecommendationType:
    if requirement.task_type == RecommendationType.pc_build_plan.value:
        return RecommendationType.pc_build_plan
    if requirement.need_bundle or len(selected) > 1:
        return RecommendationType.shopping_bundle
    return RecommendationType.single_product


def build_recommendation_title(recommendation_type: RecommendationType) -> str:
    return {
        RecommendationType.single_product: "单品推荐",
        RecommendationType.shopping_bundle: "组合推荐",
        RecommendationType.pc_build_plan: "电脑主机方案推荐",
    }[recommendation_type]


def to_selected_component(score: ProductScore) -> SelectedComponent:
    evidence_ids, _ = evidence_summary(score.evidence)
    return SelectedComponent(
        role=score.product.category,
        product=score.product,
        reason=build_component_reason(score),
        score=score.score,
        evidence_doc_ids=evidence_ids or [score.product.product_id],
        selected_sku_id=select_representative_sku_id(score.product),
    )


def select_representative_sku_id(product: ApiProduct) -> Optional[str]:
    if not product.skus:
        return None
    return sorted(
        product.skus,
        key=lambda sku: abs((sku.price if sku.price is not None else product.base_price) - product.base_price),
    )[0].sku_id


def build_component_reason(score: ProductScore) -> str:
    product = score.product
    reason = (
        f"{product.title} 属于 {product.category_name}/{product.sub_category}，"
        f"综合分 {score.score.final_score:.4f}，"
        f"参考价 {product.min_price:g}-{product.max_price:g} {product.currency}。"
    )
    if product.rating_avg:
        reason += f" 评价均分 {product.rating_avg:g}/5。"
    _, evidence_lines = evidence_summary(score.evidence)
    if evidence_lines:
        reason += " 检索证据：" + "；".join(evidence_lines) + "。"
    return reason


def build_plan_summary(
    recommendation_type: RecommendationType,
    components: List[SelectedComponent],
    requirement: RequirementSpec,
) -> str:
    names = " + ".join(component.product.title for component in components)
    if not names:
        return "当前商品库缺少可推荐候选，无法生成完整购物建议。"
    if recommendation_type == RecommendationType.shopping_bundle:
        prefix = "按场景组合多个互补商品"
    elif recommendation_type == RecommendationType.pc_build_plan:
        prefix = "电脑主机方案需要进入独立 PC 配置规划链路"
    else:
        prefix = "优先推荐当前最匹配的上架商品"
    if requirement.price_max is not None:
        total_min = sum(component.product.min_price for component in components)
        if total_min > requirement.price_max:
            prefix = f"未找到严格满足 {requirement.price_max:g} CNY 预算的上架商品，以下为最接近候选"
    return f"{prefix}：{names}"


def build_plan_pros(
    recommendation_type: RecommendationType,
    components: List[SelectedComponent],
) -> List[str]:
    pros = ["只从本地商品库选择真实上架商品", "推荐理由引用价格、SKU、FAQ、评价和结构化属性"]
    if recommendation_type == RecommendationType.shopping_bundle:
        pros.append("组合内商品按场景互补，适合一次性采购或搭配")
    if len(components) == 1:
        pros.append("单品决策路径短，适合快速加购或继续对比同类商品")
    return pros


def build_plan_cons(
    recommendation_type: RecommendationType,
    components: List[SelectedComponent],
) -> List[str]:
    cons = ["数据集不含实时优惠券，最终成交价需要下单前刷新"]
    if recommendation_type == RecommendationType.shopping_bundle:
        cons.append("组合总价可能高于单品预算，需要确认是否确实需要整套")
    if not components:
        cons.append("当前类目或预算约束过窄，商品库中没有足够候选")
    return cons


def build_suitable_for(recommendation_type: RecommendationType, requirement: RequirementSpec) -> List[str]:
    base = [requirement.occasion or "日常使用", requirement.target_user or "普通消费者"]
    if recommendation_type == RecommendationType.shopping_bundle:
        return ["需要一整套搭配或采购清单", *base]
    if recommendation_type == RecommendationType.pc_build_plan:
        return ["电脑主机配置需求", *base]
    return ["明确单品需求", *base]


def build_plan_risks(components: List[SelectedComponent]) -> List[str]:
    risks = ["数据集未提供实时库存和优惠券，进入交易前需要刷新价格与库存。"]
    for component in components:
        risks.extend(component.product.risk_notes[:1])
    return dedupe(risks)


def build_budget_risks(requirement: RequirementSpec, cost_estimate: CostEstimate) -> List[str]:
    if requirement.price_max is None or cost_estimate.total_price_min <= requirement.price_max:
        return []
    return [
        f"当前商品库没有严格落在 {requirement.price_max:g} CNY 以内的完整候选，最低可选总价约 {cost_estimate.total_price_min:g} CNY。",
    ]


def build_evidence(components: List[SelectedComponent]) -> List[str]:
    evidence = []
    for component in components:
        product = component.product
        evidence.append(f"{product.product_id}: {product.description[:220]}")
        if product.faqs:
            evidence.append(f"{product.product_id}: FAQ - {product.faqs[0].question}")
        if component.score:
            evidence.extend(component.score.reasons[:2])
    return dedupe(evidence)


def build_score_table(components: List[SelectedComponent]) -> List[Dict[str, object]]:
    rows = []
    for component in components:
        score = component.score
        if score is None:
            continue
        rows.append(
            {
                "role": component.role.value,
                "product_id": component.product.product_id,
                "title": component.product.title,
                "final_score": score.final_score,
                "scenario_match": score.scenario_match,
                "attribute_match": score.attribute_match,
                "price_fit": score.price_fit,
                "reputation_fit": score.reputation_fit,
                "availability_fit": score.availability_fit,
                "sku_fit": score.sku_fit,
                "detail_quality": score.detail_quality,
                "reasons": score.reasons[:8],
            }
        )
    return rows




def build_product_cards(
    plans: List[RecommendationPlan],
    grouped_scores: Optional[Dict[ComponentCategory, List[ProductScore]]] = None,
    requirement: Optional[RequirementSpec] = None,
) -> List[Dict[str, object]]:
    """Flatten selected products and close alternatives into unique product cards."""

    cards: List[Dict[str, object]] = []
    seen = set()
    for plan in plans:
        for component in plan.components:
            product = component.product
            if product.product_id in seen:
                continue
            seen.add(product.product_id)
            cards.append(product_card_from_component(component, plan.recommendation_type.value))

    if grouped_scores:
        alternative_limit = alternative_card_limit(requirement, plans)
        for score in relevant_alternative_scores(grouped_scores, seen, limit=alternative_limit):
            product = score.product
            seen.add(product.product_id)
            cards.append(product_card_from_score(score, "alternative_candidate"))
    return cards


def alternative_card_limit(
    requirement: Optional[RequirementSpec],
    plans: List[RecommendationPlan],
) -> int:
    """Keep recommendation cards focused, especially in chat surfaces."""

    if requirement and requirement.need_comparison:
        return 4
    if plans and plans[0].recommendation_type == RecommendationType.shopping_bundle:
        return 0
    return 2


def relevant_alternative_scores(
    grouped_scores: Dict[ComponentCategory, List[ProductScore]],
    selected_ids: set,
    *,
    limit: int,
) -> List[ProductScore]:
    if limit <= 0:
        return []
    ranked = top_product_scores(grouped_scores, limit=16)
    if not ranked:
        return []
    best_score = ranked[0].score.final_score
    threshold = max(0.55, best_score - 0.12)
    alternatives: List[ProductScore] = []
    for score in ranked:
        if score.product.product_id in selected_ids:
            continue
        if score.score.final_score < threshold:
            continue
        alternatives.append(score)
        if len(alternatives) >= limit:
            break
    return alternatives


def product_card_from_component(component: SelectedComponent, source: str) -> Dict[str, object]:
    product = component.product
    score = component.score.final_score if component.score else None
    card = {
        "product_id": product.product_id,
        "title": product.title,
        "name": product.title,
        "brand": product.brand,
        "category": product.category.value,
        "category_name": product.category_name,
        "sub_category": product.sub_category,
        "price": product.min_price or product.base_price,
        "price_range": [product.min_price, product.max_price],
        "currency": product.currency,
        "image_url": product.image_url,
        "stock_status": product.stock_status,
        "stock_quantity": product.stock_quantity,
        "rating_avg": product.rating_avg,
        "review_count": product.review_count,
        "reason": component.reason,
        "score": score,
        "source": source,
        "selected_sku_id": component.selected_sku_id,
    }
    if product.category.value.startswith("pc_"):
        card.pop("image_url", None)
    return card


def product_card_from_score(score: ProductScore, source: str) -> Dict[str, object]:
    return product_card_from_component(to_selected_component(score), source)


def build_candidate_scope(
    requirement: RequirementSpec,
    catalog: ProductCatalog,
    grouped_scores: Dict[ComponentCategory, List[ProductScore]],
) -> Dict[str, object]:
    """Summarize structured filtering before final selection."""

    categories = requirement.desired_categories or requirement.required_components
    scope: Dict[str, object] = {
        "active_filters": {
            "categories": [category.value for category in categories],
            "price_min": requirement.price_min,
            "price_max": requirement.price_max,
            "brands": list(requirement.brands),
            "excluded_brands": list(requirement.excluded_brands),
            "must_have_terms": list(requirement.must_have_terms),
            "excluded_terms": list(requirement.excluded_terms),
            "preferences": list(requirement.preferences),
        },
        "total_catalog_count": len(catalog.products),
        "by_category": {},
        "clarification_needed": list(requirement.missing_fields),
    }
    by_category: Dict[str, object] = {}
    for category in categories:
        raw_products = catalog.filter_by_category(category)
        scored = grouped_scores.get(category, [])
        within_budget = [
            item
            for item in scored
            if requirement.price_max is None or (item.product.min_price or item.product.base_price) <= requirement.price_max
        ]
        by_category[category.value] = {
            "raw_count": len(raw_products),
            "after_exclusion_count": len(scored),
            "within_budget_count": len(within_budget),
            "top_candidates": [
                {
                    "product_id": item.product.product_id,
                    "title": item.product.title,
                    "price": item.product.min_price or item.product.base_price,
                    "score": item.score.final_score,
                }
                for item in scored[:5]
            ],
        }
    scope["by_category"] = by_category
    return scope


def build_comparison_table(grouped_scores: Dict[ComponentCategory, List[ProductScore]]) -> List[Dict[str, object]]:
    """Create a compact product-level comparison table."""

    rows: List[Dict[str, object]] = []
    for rank, score in enumerate(top_product_scores(grouped_scores, limit=6), 1):
        product = score.product
        rows.append(
            {
                "rank": rank,
                "product_id": product.product_id,
                "title": product.title,
                "brand": product.brand,
                "category": product.category.value,
                "category_name": product.category_name,
                "price": product.min_price or product.base_price,
                "price_range": [product.min_price, product.max_price],
                "currency": product.currency,
                "rating_avg": product.rating_avg,
                "review_count": product.review_count,
                "score": score.score.final_score,
                "strength": build_product_strength(score),
                "tradeoff": build_product_tradeoff(product),
                "recommendation": rank == 1,
            }
        )
    return rows


def top_product_scores(
    grouped_scores: Dict[ComponentCategory, List[ProductScore]],
    *,
    limit: int,
) -> List[ProductScore]:
    seen = set()
    ranked: List[ProductScore] = []
    for scores in grouped_scores.values():
        for score in scores:
            product_id = score.product.product_id
            if product_id in seen:
                continue
            seen.add(product_id)
            ranked.append(score)
    return sorted(ranked, key=lambda item: item.score.final_score, reverse=True)[:limit]


def build_product_strength(score: ProductScore) -> str:
    product = score.product
    parts = [f"综合分 {score.score.final_score:.4f}"]
    if product.rating_avg is not None:
        parts.append(f"评分 {product.rating_avg:g}/5")
    if product.faqs:
        parts.append("FAQ 信息较完整")
    return "，".join(parts) + "。"


def build_product_tradeoff(product: ApiProduct) -> str:
    if product.not_good_for:
        return "需要注意：" + "；".join(product.not_good_for[:2]) + "。"
    return "真实下单前仍需刷新实时价格、库存和优惠。"


def average(values: List[object]) -> float:
    numbers = []
    for value in values:
        try:
            numbers.append(float(value))
        except (TypeError, ValueError):
            continue
    return sum(numbers) / len(numbers) if numbers else 0.0


def collect_result_risks(plans: Iterable[RecommendationPlan]) -> List[str]:
    risks = []
    for plan in plans:
        risks.extend(plan.risks)
    return dedupe(risks)


def collect_dynamic_weights(grouped_scores: Dict[ComponentCategory, List[ProductScore]]) -> Dict[str, Dict[str, float]]:
    weights: Dict[str, Dict[str, float]] = {}
    for category, scores in grouped_scores.items():
        if not scores:
            continue
        weights[category.value] = {
            name: round(value, 4)
            for name, value in scores[0].weights.items()
        }
    return weights


def collect_weight_reasons(grouped_scores: Dict[ComponentCategory, List[ProductScore]]) -> List[str]:
    reasons: List[str] = []
    for scores in grouped_scores.values():
        for score in scores[:1]:
            reasons.extend(score.weight_reasons)
    return dedupe(reasons)


def dedupe(items: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _evidence_boost_max(grouped_scores: Dict[ComponentCategory, List[ProductScore]]) -> float:
    return round(max(
        (score.evidence_boost for scores in grouped_scores.values() for score in scores),
        default=0.0,
    ), 4)


def _evidence_match_max(grouped_scores: Dict[ComponentCategory, List[ProductScore]]) -> float:
    return round(max(
        (score.evidence_match for scores in grouped_scores.values() for score in scores),
        default=0.0,
    ), 4)


def _evidence_boost_any(grouped_scores: Dict[ComponentCategory, List[ProductScore]]) -> bool:
    return any(score.evidence_boost > 0 for scores in grouped_scores.values() for score in scores)


def _evidence_boost_per_category(
    grouped_scores: Dict[ComponentCategory, List[ProductScore]],
) -> Dict[str, float]:
    result: Dict[str, float] = {}
    for category, scores in grouped_scores.items():
        if scores:
            result[category.value] = round(max(score.evidence_boost for score in scores), 4)
    return result
