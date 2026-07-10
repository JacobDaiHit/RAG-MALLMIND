"""Resolve product references from router arguments and session context."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence

from rag.recommendation.brand_normalizer import expand_brand_terms, normalize_brand_text
from rag.recommendation.llm_client import LLMClientError, OpenAICompatibleChatClient
from rag.recommendation.product_loader import ProductCatalog
from rag.recommendation.session_state import last_recommended_product_ids
from rag.security.prompt_guard import defense_prefix, defense_suffix, wrap_user_input
from rag.schemas import ApiProduct


logger = logging.getLogger(__name__)


def resolve_product_from_context(
    catalog: ProductCatalog,
    session: Any,
    tool_call: Dict[str, Any],
) -> Optional[ApiProduct]:
    """Pick the concrete product a follow-up tool should answer about."""

    args = dict(tool_call.get("arguments") or {})
    explicit_ids = _explicit_product_ids(args)
    for product_id in explicit_ids:
        product = catalog.get(product_id)
        if product is not None:
            return product

    candidates = _candidate_products(catalog, session, args)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    selected = _llm_select_product(candidates, args)
    if selected is not None:
        return selected
    return _rank_candidates(candidates, args)[0]


def product_identity_text(product: ApiProduct) -> str:
    metadata = product.metadata or {}
    specs = metadata.get("specs") if isinstance(metadata.get("specs"), dict) else {}
    spec_text = " ".join(f"{key} {value}" for key, value in specs.items())
    values = [
        product.product_id,
        product.title,
        product.brand,
        product.category_name,
        product.sub_category,
        product.description,
        " ".join(product.tags),
        spec_text,
    ]
    for sku in product.skus:
        values.extend(str(value) for value in sku.properties.values())
    return " ".join(str(value or "") for value in values)


def _explicit_product_ids(args: Dict[str, Any]) -> List[str]:
    values: List[str] = []
    product_ids = args.get("product_ids")
    if isinstance(product_ids, list):
        values.extend(str(item).strip() for item in product_ids if str(item).strip())
    for key in ("target_product_id", "product_id"):
        value = str(args.get(key) or "").strip()
        if value:
            values.append(value)
    return _dedupe(values)


def _candidate_products(catalog: ProductCatalog, session: Any, args: Dict[str, Any]) -> List[ApiProduct]:
    candidate_ids = last_recommended_product_ids(session)
    products = [catalog.get(product_id) for product_id in candidate_ids]
    candidates = [product for product in products if product is not None]
    if not candidates:
        return []

    filtered = _filter_candidates_by_context(candidates, args)
    return filtered or candidates


def _filter_candidates_by_context(candidates: Sequence[ApiProduct], args: Dict[str, Any]) -> List[ApiProduct]:
    brands = expand_brand_terms(args.get("brands") or [])
    sub_category = str(args.get("sub_category") or "").strip()
    mentions = [str(item).strip() for item in (args.get("product_mentions") or []) if str(item).strip()]

    filtered: List[ApiProduct] = []
    for product in candidates:
        identity = normalize_brand_text(product_identity_text(product))
        brand_ok = not brands or any(normalize_brand_text(term) in identity for term in brands)
        sub_category_ok = not sub_category or normalize_brand_text(sub_category) in identity
        mention_ok = not mentions or any(normalize_brand_text(term) in identity for term in mentions)
        if brand_ok and sub_category_ok and mention_ok:
            filtered.append(product)
    return filtered


def _llm_select_product(candidates: Sequence[ApiProduct], args: Dict[str, Any]) -> Optional[ApiProduct]:
    client = OpenAICompatibleChatClient()
    if not client.configured:
        return None

    options = [
        {
            "index": index,
            "product_id": product.product_id,
            "title": product.title,
            "brand": product.brand,
            "sub_category": product.sub_category,
            "price": product.base_price,
            "sku_summary": [
                {"sku_id": sku.sku_id, "properties": sku.properties, "price": sku.price}
                for sku in product.skus[:4]
            ],
        }
        for index, product in enumerate(candidates, 1)
    ]
    prompt = {
        "query": args.get("query") or "",
        "product_mentions": args.get("product_mentions") or [],
        "attribute": args.get("attribute") or "",
        "sku_criteria": args.get("sku_criteria") or "",
        "brands": args.get("brands") or [],
        "sub_category": args.get("sub_category") or "",
        "options": options,
    }
    messages = [
        {
            "role": "system",
            "content": (
                f"{defense_prefix()}\n"
                "你负责从候选商品中选择用户当前追问的唯一商品。只能选择 options 里的商品，"
                "如果无法确定就返回 index=null。输出严格 JSON："
                '{"index": 1, "confidence": 0.0, "reason": ""}'
                f"\n{defense_suffix()}"
            ),
        },
        {"role": "user", "content": wrap_user_input(json.dumps(prompt, ensure_ascii=False), max_len=4000)},
    ]
    try:
        payload = client.chat_json(messages, temperature=0.0, max_tokens=200)
    except (LLMClientError, json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.debug("product reference LLM selection failed: %s", exc)
        return None

    try:
        selected_index = int(payload.get("index"))
        confidence = float(payload.get("confidence") or 0)
    except (TypeError, ValueError):
        return None
    if confidence < 0.45:
        return None
    if 1 <= selected_index <= len(candidates):
        return candidates[selected_index - 1]
    return None


def _rank_candidates(candidates: Sequence[ApiProduct], args: Dict[str, Any]) -> List[ApiProduct]:
    query_terms = [
        args.get("query") or "",
        args.get("attribute") or "",
        args.get("sku_criteria") or "",
        " ".join(str(item) for item in (args.get("product_mentions") or [])),
        " ".join(str(item) for item in expand_brand_terms(args.get("brands") or [])),
        args.get("sub_category") or "",
    ]
    normalized_terms = [normalize_brand_text(term) for term in query_terms if normalize_brand_text(term)]

    def score(product: ApiProduct) -> float:
        identity = normalize_brand_text(product_identity_text(product))
        hits = sum(1 for term in normalized_terms if term in identity)
        rating = float(product.rating_avg or 0)
        return hits * 10 + rating

    return sorted(candidates, key=score, reverse=True)


def _dedupe(items: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result

