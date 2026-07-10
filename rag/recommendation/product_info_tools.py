"""Grounded answers for product parameter, SKU, and price follow-ups."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from rag.recommendation.llm_client import LLMClientError, OpenAICompatibleChatClient
from rag.recommendation.product_loader import load_combined_product_catalog
from rag.recommendation.product_reference import product_identity_text, resolve_product_from_context
from rag.security.prompt_guard import defense_prefix, defense_suffix, wrap_user_input
from rag.schemas import ApiProduct


logger = logging.getLogger(__name__)


@dataclass
class ProductInfoAnswer:
    text: str
    product: Optional[ApiProduct] = None


def answer_parameter_query(session: Any, tool_call: Dict[str, Any]) -> ProductInfoAnswer:
    args = dict(tool_call.get("arguments") or {})
    catalog = load_combined_product_catalog()
    product = resolve_product_from_context(catalog, session, tool_call)
    if product is None:
        return ProductInfoAnswer("你想了解哪款商品的参数？可以告诉我具体型号。")

    specs = _product_specs(product)
    attribute = str(args.get("attribute") or "").strip()
    field_key = _llm_resolve_spec_key(product, specs, args)
    if field_key and field_key in specs:
        value = _format_spec_value(field_key, specs[field_key])
        text = _llm_grounded_text(
            task="parameter",
            product=product,
            args=args,
            facts={
                "answer_field": field_key,
                "answer_value": value,
                "available_specs": specs,
                "base_price": product.base_price,
            },
            fallback=f"「{product.title}」的{attribute or field_key}是 {value}。",
        )
    else:
        text = _llm_grounded_text(
            task="parameter_missing",
            product=product,
            args=args,
            facts={
                "available_specs": specs,
                "description": product.description,
                "base_price": product.base_price,
            },
            fallback=(
                f"「{product.title}」当前商品库没有找到“{attribute or '该参数'}”的结构化字段；"
                f"参考价 ¥{product.base_price:g}，建议以商品详情页为准。"
            ),
        )
    return ProductInfoAnswer(text, product)


def answer_sku_query(session: Any, tool_call: Dict[str, Any]) -> ProductInfoAnswer:
    args = dict(tool_call.get("arguments") or {})
    catalog = load_combined_product_catalog()
    product = resolve_product_from_context(catalog, session, tool_call)
    if product is None:
        return ProductInfoAnswer("你想了解哪款商品的配置差异？可以告诉我具体型号。")
    if not product.skus:
        return ProductInfoAnswer(f"「{product.title}」暂时没有多种配置可选。", product)

    facts = {
        "sku_criteria": args.get("sku_criteria") or args.get("query") or "",
        "skus": [_sku_payload(sku, product.base_price) for sku in product.skus],
        "price_range": [product.min_price, product.max_price],
    }
    fallback = _fallback_sku_text(product)
    text = _llm_grounded_text("sku", product, args, facts, fallback=fallback)
    return ProductInfoAnswer(text, product)


def answer_price_comparison(session: Any, tool_call: Dict[str, Any]) -> ProductInfoAnswer:
    args = dict(tool_call.get("arguments") or {})
    catalog = load_combined_product_catalog()
    product = resolve_product_from_context(catalog, session, tool_call)
    if product is None:
        return ProductInfoAnswer("你想比价哪款商品？可以告诉我具体型号。")

    official_price = _metadata_value(product, "official_price", "official_price_cny", "msrp", "msrp_cny")
    facts = {
        "base_price": product.base_price,
        "price_range": [product.min_price, product.max_price],
        "official_price": official_price,
        "official_price_available": official_price is not None,
        "skus": [_sku_payload(sku, product.base_price) for sku in product.skus],
        "pricing_note": product.pricing_note,
    }
    fallback = _fallback_price_text(product, official_price)
    text = _llm_grounded_text("price", product, args, facts, fallback=fallback)
    return ProductInfoAnswer(text, product)


def _product_specs(product: ApiProduct) -> Dict[str, Any]:
    metadata = product.metadata or {}
    specs = metadata.get("specs")
    if isinstance(specs, dict):
        return {str(key): value for key, value in specs.items() if value not in ("", None, [], {})}
    return {}


def _llm_resolve_spec_key(product: ApiProduct, specs: Dict[str, Any], args: Dict[str, Any]) -> str:
    if not specs:
        return ""
    client = OpenAICompatibleChatClient()
    if not client.configured:
        return _exact_spec_key(specs, args)
    prompt = {
        "query": args.get("query") or "",
        "attribute": args.get("attribute") or "",
        "product": {
            "title": product.title,
            "brand": product.brand,
            "description": product.description,
        },
        "available_spec_keys": list(specs.keys()),
        "available_specs": specs,
    }
    messages = [
        {
            "role": "system",
            "content": (
                f"{defense_prefix()}\n"
                "你负责把用户问的商品参数映射到 available_spec_keys 中最贴近的一个字段。"
                "只能返回已有字段；没有合适字段就返回空字符串。输出严格 JSON："
                '{"field_key": "", "confidence": 0.0, "reason": ""}'
                f"\n{defense_suffix()}"
            ),
        },
        {"role": "user", "content": wrap_user_input(json.dumps(prompt, ensure_ascii=False), max_len=4000)},
    ]
    try:
        payload = client.chat_json(messages, temperature=0.0, max_tokens=200)
    except (LLMClientError, json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.debug("parameter spec LLM mapping failed: %s", exc)
        return _exact_spec_key(specs, args)
    field_key = str(payload.get("field_key") or "").strip()
    try:
        confidence = float(payload.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    if confidence >= 0.45 and field_key in specs:
        return field_key
    return _exact_spec_key(specs, args)


def _exact_spec_key(specs: Dict[str, Any], args: Dict[str, Any]) -> str:
    terms = [str(args.get("attribute") or ""), str(args.get("query") or "")]
    normalized_terms = [term.lower().strip() for term in terms if term.strip()]
    for key in specs:
        folded = key.lower()
        if any(term == folded or term in folded for term in normalized_terms):
            return key
    return ""


def _llm_grounded_text(
    task: str,
    product: ApiProduct,
    args: Dict[str, Any],
    facts: Dict[str, Any],
    *,
    fallback: str,
) -> str:
    client = OpenAICompatibleChatClient()
    if not client.configured:
        return fallback

    prompt = {
        "task": task,
        "query": args.get("query") or "",
        "product": {
            "product_id": product.product_id,
            "title": product.title,
            "brand": product.brand,
            "sub_category": product.sub_category,
            "description": product.description,
            "identity_text": product_identity_text(product)[:1200],
        },
        "facts": facts,
    }
    messages = [
        {
            "role": "system",
            "content": (
                f"{defense_prefix()}\n"
                "你是电商导购助手。必须只基于 facts 回答，不要编造官网价、库存、优惠或未提供参数。"
                "如果 facts 明确缺少官网价或字段，直接说明缺少，给出已有可用信息。"
                "回答要短，中文，适合直接展示在聊天气泡里。"
                f"\n{defense_suffix()}"
            ),
        },
        {"role": "user", "content": wrap_user_input(json.dumps(prompt, ensure_ascii=False), max_len=6000)},
    ]
    try:
        text = client.chat_text(messages, temperature=0.1, max_tokens=500).strip()
    except LLMClientError as exc:
        logger.debug("product info answer LLM failed: %s", exc)
        return fallback
    text = text or fallback
    if product.title and product.title not in text:
        text = f"关于「{product.title}」：{text}"
    return text


def _sku_payload(sku: Any, base_price: float) -> Dict[str, Any]:
    return {
        "sku_id": sku.sku_id,
        "properties": dict(sku.properties or {}),
        "price": sku.price if sku.price is not None else base_price,
    }


def _format_spec_value(key: str, value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    unit = _unit_for_spec_key(key)
    return f"{value}{unit}" if unit and str(value).strip() else str(value)


def _unit_for_spec_key(key: str) -> str:
    suffix_units: List[Tuple[str, str]] = [
        ("_w", "W"),
        ("_gb", "GB"),
        ("_mb", "MB"),
        ("_mhz", "MHz"),
        ("_ghz", "GHz"),
        ("_mm", "mm"),
        ("_bit", "bit"),
    ]
    lowered = key.lower()
    for suffix, unit in suffix_units:
        if lowered.endswith(suffix):
            return unit
    return ""


def _metadata_value(product: ApiProduct, *keys: str) -> Optional[Any]:
    metadata = product.metadata or {}
    for key in keys:
        if metadata.get(key) not in (None, "", [], {}):
            return metadata[key]
    return None


def _fallback_sku_text(product: ApiProduct) -> str:
    lines = [f"「{product.title}」的可选配置："]
    for sku in product.skus:
        props = " / ".join(str(value) for value in (sku.properties or {}).values())
        price = sku.price if sku.price is not None else product.base_price
        lines.append(f"- {props}：¥{price:g}")
    return "\n".join(lines)


def _fallback_price_text(product: ApiProduct, official_price: Optional[Any]) -> str:
    lines = [f"「{product.title}」的商品库参考价是 ¥{product.base_price:g}。"]
    if product.min_price and product.max_price and product.min_price != product.max_price:
        lines.append(f"当前商品库价格区间：¥{product.min_price:g} ~ ¥{product.max_price:g}。")
    if official_price is None:
        lines.append("商品库没有官网价字段，所以不能判断是否比官网便宜。")
    else:
        lines.append(f"商品库记录的官网/指导价是 ¥{float(official_price):g}。")
    return "\n".join(lines)
