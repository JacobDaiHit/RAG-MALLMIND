"""Catalog-only execution for certified card fact queries."""
from __future__ import annotations

from typing import Any, Iterable

from rag.api.sse import sse_event
from rag.recommendation.session_state import save_session

from .session import apply_session_delta, fact_query_delta, load_session_core
from .types import RequirementSpecV3


def execute_certified_fact_query(*, session: Any, requirement: RequirementSpecV3, catalog: Any) -> Iterable[str]:
    """Answer only from a non-expired card reference and the live catalog."""

    core = load_session_core(session)
    if requirement.query_kind == "compare":
        yield from _execute_card_comparison(session=session, requirement=requirement, catalog=catalog, core=core)
        return
    card = next((item for item in core.cards if item.card_id == requirement.target_card_id), None)
    if card is None:
        yield sse_event("clarification", {"question": "刚才的商品卡已失效，请重新让我推荐一次。", "reason": "expired_or_unknown_card"})
        yield sse_event("done", {"session_id": session.session_id})
        return
    product = catalog.get(card.product_id)
    if product is None:
        yield sse_event("error", {"label": "目录事实暂不可确认", "detail": "当前商品已无法从目录读取，请稍后重试。"})
        yield sse_event("done", {"session_id": session.session_id})
        return
    facts, text = _facts_and_text(product, requirement.query_kind or "")
    apply_session_delta(session, fact_query_delta(core))
    save_session(session)
    yield sse_event("product_fact", {"card_id": card.card_id, "product_id": card.product_id, "query_kind": requirement.query_kind, "facts": facts})
    yield sse_event("delta", {"text": text})
    yield sse_event("done", {"session_id": session.session_id})


def _execute_card_comparison(*, session: Any, requirement: RequirementSpecV3, catalog: Any, core: Any) -> Iterable[str]:
    cards = [next((item for item in core.cards if item.card_id == card_id), None) for card_id in requirement.target_card_ids]
    if len(cards) != 2 or any(card is None for card in cards):
        yield sse_event("clarification", {"question": "刚才的商品卡已失效，请重新推荐后再比较。", "reason": "expired_or_unknown_comparison_card"})
        yield sse_event("done", {"session_id": session.session_id})
        return
    products = [catalog.get(card.product_id) for card in cards]
    if any(product is None for product in products):
        yield sse_event("error", {"label": "目录事实暂不可确认", "detail": "至少一件对比商品已无法从目录读取。"})
        yield sse_event("done", {"session_id": session.session_id})
        return
    rows = [_comparison_row(product) for product in products]
    apply_session_delta(session, fact_query_delta(core))
    save_session(session)
    yield sse_event("comparison_table", {"rows": rows, "source": "v3_live_catalog"})
    yield sse_event("delta", {"text": _comparison_text(rows)})
    yield sse_event("done", {"session_id": session.session_id})


def _facts_and_text(product: Any, query_kind: str) -> tuple[dict[str, object], str]:
    if query_kind == "price":
        facts = {"base_price": product.base_price, "min_price": product.min_price, "max_price": product.max_price, "currency": product.currency}
        return facts, f"「{product.title}」当前目录参考价为 ¥{product.base_price:g}，价格区间 ¥{product.min_price:g}–¥{product.max_price:g}。"
    if query_kind == "skus":
        skus = [
            {"sku_id": sku.sku_id, "properties": dict(sku.properties or {}), "price": sku.price if sku.price is not None else product.base_price}
            for sku in product.skus
        ]
        if not skus:
            return {"skus": []}, f"「{product.title}」当前目录没有可确认的 SKU 配置。"
        summary = "；".join(f"{item['sku_id']}（{item['properties']}，¥{item['price']:g}）" for item in skus)
        return {"skus": skus}, f"「{product.title}」的可选 SKU：{summary}。"
    specs = dict((product.metadata or {}).get("specs") or {})
    if not specs:
        return {"specs": {}}, f"「{product.title}」当前目录没有结构化参数；参考价为 ¥{product.base_price:g}。"
    summary = "；".join(f"{key}: {value}" for key, value in specs.items())
    return {"specs": specs}, f"「{product.title}」的结构化参数：{summary}。"


def _comparison_row(product: Any) -> dict[str, object]:
    return {
        "product_id": str(product.product_id),
        "title": str(product.title),
        "brand": str(product.brand),
        "price": float(product.min_price or product.base_price),
        "currency": str(product.currency),
        "stock_status": str(product.stock_status),
        "specs": dict((product.metadata or {}).get("specs") or {}),
        "skus": [{"sku_id": str(sku.sku_id), "price": float(sku.price if sku.price is not None else product.base_price), "properties": dict(sku.properties or {})} for sku in product.skus[:3]],
    }


def _comparison_text(rows: list[dict[str, object]]) -> str:
    return "\n".join(
        ["以下是两张商品卡的实时目录事实对比："]
        + [f"{index}. {row['title']}：¥{float(row['price']):g}，库存状态 {row['stock_status']}。" for index, row in enumerate(rows, start=1)]
    )
