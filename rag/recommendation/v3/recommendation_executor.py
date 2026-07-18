"""Execute one certified product recommendation and emit SSE product cards.

Entry point ``execute_certified_recommendation`` receives only a promoted
``RequirementSpecV3``.  It applies CandidateGate before retrieval, optionally
uses Milvus evidence to order already-allowed products, materializes short-lived
CardModel references, persists a SessionDelta, and emits the response.  It owns
neither intent parsing nor catalog facts and has no legacy fallback path.
"""
from __future__ import annotations

from hashlib import sha256
import os
import time
from typing import Any, Dict, Iterable

from rag.api.sse import sse_event
from rag.recommendation.session_state import save_session

from .candidate_gate import CatalogCandidateGate
from .config import ATTRIBUTE_RANK_TERMS
from .retrieval import V3EvidenceRetriever
from .session import CARD_TTL_SECONDS, apply_session_delta, load_session_core, recommendation_delta
from .types import CardModel, RequirementSpecV3, RetrievalEvidenceV3, RetrievalFilters


def execute_certified_recommendation(
    *,
    session: Any,
    message: str,
    requirement: RequirementSpecV3,
    proof=None,
    catalog: Any,
) -> Iterable[str]:
    """Run one V3 requirement without any legacy recommendation dependency."""

    yield sse_event("progress", {"label": "系统已开始检索", "detail": "正在按已验证条件筛选本地商品目录。"})
    gate = CatalogCandidateGate().evaluate(requirement, catalog=catalog)
    if not gate.filters.product_ids:
        yield sse_event(
            "candidate_gate",
            {"allowed_product_ids": [], "rejected_by_reason": gate.rejected_by_reason, "status": "empty"},
        )
        yield sse_event(
            "error",
            {
                "label": "当前目录没有可推荐商品",
                "detail": "当前目录没有同时满足品类、库存、预算和品牌排除条件的商品。",
                "reason": "catalog_scope_unsupported",
            },
        )
        yield sse_event("done", {"session_id": session.session_id})
        return
    evidence = _retrieve_evidence(message, gate.filters)
    products = [catalog.get(product_id) for product_id in gate.filters.product_ids]
    evidence_rank = {product_id: index for index, product_id in enumerate(evidence.ranked_product_ids)}
    ranked = sorted(
        (product for product in products if product is not None),
        key=lambda product: _rank_key(product, requirement, evidence_rank),
    )[:3]
    if not ranked:
        yield sse_event("error", {"label": "目录推荐暂不可用", "detail": "候选商品无法从当前目录读取。"})
        yield sse_event("done", {"session_id": session.session_id})
        return
    raw_cards = [_card_payload(product) for product in ranked]
    cards = _materialize_cards(raw_cards, catalog, session.session_id)
    if len(cards) != len(raw_cards):
        yield sse_event("error", {"label": "商品卡校验未通过", "detail": "目录商品引用不完整，本次结果未写入会话。"})
        yield sse_event("done", {"session_id": session.session_id})
        return
    for card, card_ref in zip(raw_cards, cards):
        card["card_id"] = card_ref.card_id

    apply_session_delta(session, recommendation_delta(requirement, cards, previous=load_session_core(session)))
    save_session(session)
    payload = {
        "type": "recommendation",
        "requirement_v3": _requirement_payload(requirement),
        "product_cards": raw_cards,
        "trace": {
            "executor": "v3_catalog_ranker",
            "v3_candidate_gate": {
                "allowed_product_ids": list(gate.filters.product_ids),
                "rejected_by_reason": gate.rejected_by_reason,
            },
            "v3_retrieval": {
                "status": evidence.status,
                "ranked_product_ids": list(evidence.ranked_product_ids),
                "raw_hit_count": evidence.raw_hit_count,
                "filter_expression": evidence.filter_expression,
                "error_code": evidence.error_code,
            },
            "v3_session_updated": True,
            "v3_card_count": len(cards),
        },
    }

    yield sse_event(
        "candidate_gate",
        {"allowed_product_ids": list(gate.filters.product_ids), "rejected_by_reason": gate.rejected_by_reason, "status": "ok"},
    )
    yield sse_event("intent_route", {"route": "v3_recommendation", "action": requirement.action.value})
    yield sse_event("progress", {"label": "目录排序完成", "detail": f"已从 {len(gate.filters.product_ids)} 个合格商品中生成 {len(raw_cards)} 张商品卡。"})
    yield sse_event("delta", {"text": _response_text(raw_cards)})
    yield sse_event("product_cards", {"schema_version": "product_cards.v3", "cards": raw_cards})
    yield sse_event("candidate_scope", {"allowed_product_ids": list(gate.filters.product_ids)})
    yield sse_event("comparison_table", {"rows": []})
    yield sse_event("result", payload)
    yield sse_event("done", {"session_id": session.session_id})


def _materialize_cards(raw_cards: list[Dict[str, Any]], catalog: Any, session_id: str) -> tuple[CardModel, ...]:
    expires_at = time.time() + CARD_TTL_SECONDS
    refs: list[CardModel] = []
    for rank, card in enumerate(raw_cards, start=1):
        product_id = str(card.get("product_id") or "")
        product = catalog.get(product_id) if product_id else None
        if product is None:
            continue
        sku_ids = tuple(str(getattr(sku, "sku_id", "")) for sku in getattr(product, "skus", ()) if getattr(sku, "sku_id", ""))
        token = sha256(f"{session_id}:{product_id}:{expires_at:.3f}".encode("utf-8")).hexdigest()[:16]
        refs.append(CardModel(f"card_{token}", product_id, sku_ids, str(getattr(product, "title", product_id)), rank, expires_at))
    return tuple(refs)


def _rank_key(product: Any, requirement: RequirementSpecV3, evidence_rank: dict[str, int]) -> tuple[int, float, float, str]:
    text = " ".join([
        str(getattr(product, "title", "")),
        str(getattr(product, "description", "")),
        " ".join(getattr(product, "tags", ()) or ()),
        " ".join(getattr(product, "best_for", ()) or ()),
    ]).lower()
    attribute_hits = sum(sum(term in text for term in ATTRIBUTE_RANK_TERMS.get(attribute, ())) for attribute in requirement.desired_attributes)
    price = float(getattr(product, "min_price", 0) or getattr(product, "base_price", 0) or 0)
    product_id = str(getattr(product, "product_id", ""))
    price_distance = abs(price - requirement.price_target) if requirement.price_target is not None else price
    return (evidence_rank.get(product_id, len(evidence_rank) + 1), -float(attribute_hits), price_distance, product_id)


def _retrieve_evidence(message: str, filters: RetrievalFilters) -> RetrievalEvidenceV3:
    if os.getenv("V3_RETRIEVAL_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return RetrievalEvidenceV3("disabled", (), 0, "", "v3_retrieval_disabled")
    return V3EvidenceRetriever().retrieve(query=message, filters=filters)


def _card_payload(product: Any) -> Dict[str, Any]:
    skus = [
        {"sku_id": str(sku.sku_id), "price": float(sku.price if sku.price is not None else product.base_price), "properties": dict(sku.properties or {})}
        for sku in getattr(product, "skus", ())
    ]
    return {
        "product_id": str(product.product_id),
        "title": str(product.title),
        "brand": str(product.brand),
        "category": product.category.value,
        "sub_category": str(product.sub_category),
        "price": float(product.min_price or product.base_price),
        "base_price": float(product.base_price),
        "currency": str(product.currency),
        "image_url": str(product.image_url or ""),
        "skus": skus,
    }


def _requirement_payload(requirement: RequirementSpecV3) -> Dict[str, Any]:
    return {
        "action": requirement.action.value,
        "product_type_ids": list(requirement.product_type_ids),
        "include_brand_family_ids": list(requirement.include_brand_family_ids),
        "exclude_brand_family_ids": list(requirement.exclude_brand_family_ids),
        "price_max": requirement.price_max,
        "price_min": requirement.price_min,
        "price_target": requirement.price_target,
        "desired_attributes": list(requirement.desired_attributes),
        "field_provenance": dict(requirement.field_provenance),
    }


def _response_text(cards: list[Dict[str, Any]]) -> str:
    lines = ["我按已确认条件从当前目录筛出了这些商品："]
    for index, card in enumerate(cards, start=1):
        lines.append(f"{index}. {card['title']}（{card['brand']}，¥{card['price']:g}）")
    return "\n".join(lines)
