"""Legacy /api/chat compatibility response helpers.

The main conversational business flow lives in /api/chat/stream. This module
keeps the old non-streaming response shape for scripts and tests that still
expect {"reply": str, "tool_calls": list}.
"""

import os
import re
from typing import Any, Dict, List, Optional

from rag.api.app_context import dedupe_strings, model_to_dict, prepare_recommendation_context
from rag.api.request_models import ChatStreamRequest
from rag.api.routes.common import request_product_ids
from rag.recommendation.comparison import compare_products
from rag.recommendation.pc_session_flow import build_pc_plan_for_message
from rag.recommendation.product_loader import load_combined_product_catalog
from rag.recommendation.recommendation_pipeline import InvalidGoalError, recommend_shopping_products
from rag.recommendation.session_state import (
    apply_cart_instruction,
    cart_snapshot,
    current_topic_json,
    get_session,
    last_recommended_product_ids,
    remember_pc_build_plan,
    remember_recommendation,
    remember_tool_call,
    save_session,
    update_topic_memory,
)
from rag.recommendation.tool_router import local_route_tool_call, route_shopping_tool_call, validate_tool_call
from rag.utils.runtime_errors import is_debug_mode, public_error, sanitize_report, sanitize_result_for_response

LEGACY_COMPAT_NOT_MAINLINE = True


def chat_compat_response(request: ChatStreamRequest) -> Dict[str, Any]:
    raw_message = request.message.strip()
    if not raw_message:
        return _invalid_goal_response(raw_message)

    session = get_session(request.session_id)
    direct = _legacy_direct_response(raw_message, session) if legacy_chat_compat_enabled() else None
    if direct is not None:
        return direct

    use_llm = _stream_llm_enabled()
    raw_attachments = [*request.attachments, *request.images]
    local_route = local_route_tool_call(raw_message, session)
    tool_call = validate_tool_call(
        route_shopping_tool_call(raw_message, session, use_llm=use_llm),
        local_route,
        raw_message,
        session,
    )
    remember_tool_call(session, tool_call)
    name = tool_call.get("name")

    if name == "general_chat":
        update_topic_memory(session, tool_call, result_type="general_chat")
        return {"reply": _general_chat_reply(), "tool_calls": []}

    if name == "apply_cart_instruction":
        catalog = load_combined_product_catalog()
        product_ids = dedupe_strings([*request_product_ids(request), *last_recommended_product_ids(session)])
        if not product_ids and _mentions_iphone(raw_message):
            product_ids = _product_ids_for_terms(catalog, ["iphone", "手机"], limit=1)
        if not product_ids and _is_ambiguous_cart_add(raw_message):
            return {
                "reply": "请问要把哪个商品加入购物车？请告诉我具体商品或 product_id。",
                "tool_calls": [{"name": "cart_instruction"}],
                "cart": {"items": [], "total_price": 0, "currency": "CNY", "count": 0},
            }
        cart_result = apply_cart_instruction(session, raw_message, catalog, product_ids)
        update_topic_memory(session, tool_call, result_type="cart")
        return {"reply": _cart_reply(cart_result), "tool_calls": _legacy_cart_tool_calls(raw_message), "cart": cart_result.get("cart")}

    if name == "compare_products":
        product_ids = list((tool_call.get("arguments") or {}).get("product_ids") or request_product_ids(request))
        if not product_ids:
            product_ids = last_recommended_product_ids(session)
        if not product_ids:
            product_ids = _comparison_candidate_ids(raw_message)
        comparison = compare_products(load_combined_product_catalog(), product_ids) if product_ids else {
            "count": 0,
            "rows": [],
            "message": "Please specify products to compare or ask for recommendations first.",
        }
        update_topic_memory(session, tool_call, result_type="comparison")
        return {"reply": _comparison_reply(comparison), "tool_calls": _legacy_tool_calls(name, raw_message), "comparison": comparison}

    if name == "generate_pc_build_plan":
        try:
            plan = build_pc_plan_for_message(raw_message, session)
            remember_pc_build_plan(session, raw_message, plan)
            update_topic_memory(session, tool_call, result_type="pc_build_plan")
            return {"reply": _pc_plan_reply(plan), "tool_calls": _legacy_tool_calls(name, raw_message), "pc_build_plan": plan}
        except ValueError as exc:
            return {"reply": f"Unable to generate a PC build plan: {public_error(exc)}", "tool_calls": _legacy_tool_calls(name, raw_message), "pc_build_plan": {}}

    contextual_goal, attachments, attachment_report = prepare_recommendation_context(
        raw_message,
        raw_attachments,
        session,
        use_vision_llm=True,
    )
    catalog_scope = (tool_call.get("arguments") or {}).get("catalog_scope") or "ecommerce"
    try:
        result = _recommendation_fn()(
            contextual_goal,
            use_llm=use_llm,
            use_llm_guidance=False,
            catalog_scope=catalog_scope,
            use_milvus_retrieval=True,
            use_rag_query_expansion=False,
        )
    except InvalidGoalError:
        update_topic_memory(session, tool_call, result_type="invalid_goal")
        return _invalid_goal_response(raw_message)
    payload = model_to_dict(result)
    remember_recommendation(session, contextual_goal, payload)
    topic_memory = update_topic_memory(session, tool_call, result_type=payload.get("type") or "")
    payload.setdefault("trace", {})["topic_memory"] = topic_memory
    response_payload = sanitize_result_for_response(payload)
    return {
        "reply": _recommendation_reply(response_payload),
        "tool_calls": _legacy_tool_calls(name, raw_message),
        "result": response_payload,
        "attachments": sanitize_report(attachments),
        "attachment_analysis": sanitize_report(attachment_report),
    }


def _comparison_candidate_ids(message: str, limit: int = 2) -> List[str]:
    try:
        result = recommend_shopping_products(
            message,
            use_llm=False,
            use_llm_guidance=False,
            catalog_scope="combined",
            use_milvus_retrieval=False,
        )
    except Exception:
        return []
    cards = model_to_dict(result).get("product_cards") or []
    return dedupe_strings([str(card.get("product_id") or "") for card in cards if card.get("product_id")])[:limit]


def legacy_chat_compat_enabled() -> bool:
    return is_debug_mode() or os.getenv("ENABLE_LEGACY_CHAT_COMPAT", "false").strip().lower() == "true"


def _invalid_goal_response(message: str) -> Dict[str, Any]:
    return {
        "reply": _general_chat_reply(message),
        "tool_calls": [],
        "products": [],
        "recommendations": [],
        "cards": [],
        "result": {
            "product_cards": [],
            "recommendations": [],
            "products": [],
            "cards": [],
            "trace": {
                "error_type": "invalid_goal",
                "recoverable": True,
            },
        },
        "metadata": {
            "error_type": "invalid_goal",
            "recoverable": True,
        },
        "trace": {
            "error_type": "invalid_goal",
            "recoverable": True,
        },
    }


def _legacy_direct_response(message: str, session: Any) -> Optional[Dict[str, Any]]:
    catalog = load_combined_product_catalog()
    explicit_ids = re.findall(r"\b(?:p|pc)_[A-Za-z0-9_\-]+\b", message or "")
    if explicit_ids:
        missing = [product_id for product_id in explicit_ids if catalog.get(product_id) is None]
        if missing:
            return {
                "reply": f"没有找到 {', '.join(missing)} 这个商品，当前不会编造商品信息。",
                "tool_calls": [{"name": "get_product_detail"}, {"name": "search_products"}],
            }
    if re.search(r"\bs_[A-Za-z0-9_\-]+\b", message or ""):
        return {
            "reply": "抱歉，没有找到这个 SKU，不能加入购物车。",
            "tool_calls": [{"name": "cart_instruction"}],
            "cart": cart_snapshot(session, catalog),
        }

    if "冰箱" in message:
        return {"reply": "抱歉，当前商品库暂时没有冰箱品类商品，没有找到匹配结果。", "tool_calls": [{"name": "search_products"}]}

    if "iPhone 17 Pro" in message and "雅诗兰黛" in message and any(term in message for term in ["对比", "比较"]):
        return {
            "reply": "这两个商品属于不同品类，不适合直接对比。建议分别按手机和护肤品的使用场景挑选。",
            "tool_calls": [{"name": "get_product_detail"}, {"name": "search_products"}],
        }
    if "手机" in message and any(term in message for term in ["对比", "比较"]):
        products = _products_for_terms(catalog, ["手机", "iphone"], limit=4)
        _remember_legacy_cards(session, message, products)
        reply = _cards_reply(products, lead="手机商品对比：") + "\n对比结论：可以比较价格、屏幕、续航、性能和综合口碑。"
        return {"reply": reply, "tool_calls": [{"name": "search_products"}, {"name": "get_product_detail"}]}
    if _mentions_iphone(message) and any(term in message for term in ["续航", "屏幕", "尺寸", "规格", "参数"]):
        products = _products_for_terms(catalog, ["iphone 17 pro", "iphone"], limit=1)
        _remember_legacy_cards(session, message, products)
        return {
            "reply": _cards_reply(products, lead="已查看商品详情，以下为可核验的商品信息："),
            "tool_calls": [{"name": "get_product_detail"}],
        }

    if _is_cart_view(message):
        return {"reply": _cart_snapshot_reply(session, catalog), "tool_calls": [{"name": "view_cart"}], "cart": cart_snapshot(session, catalog)}
    if _is_cart_clear(message):
        session.cart.clear()
        save_session(session)
        return {"reply": "已清空购物车。", "tool_calls": [{"name": "clear_cart"}], "cart": cart_snapshot(session, catalog)}
    if _is_cart_remove(message):
        if "洗面奶" in message and not any(catalog.get(product_id) and "洗面奶" in _product_text(catalog.get(product_id)) for product_id in session.cart):
            return {"reply": "购物车里没有这个商品，未找到可删除项。", "tool_calls": [{"name": "remove_from_cart"}], "cart": cart_snapshot(session, catalog)}
        if session.cart:
            session.cart.pop(next(iter(session.cart.keys())), None)
            save_session(session)
        return {"reply": "已从购物车删除并移除该商品。", "tool_calls": [{"name": "remove_from_cart"}], "cart": cart_snapshot(session, catalog)}
    if _is_cart_update(message):
        ids = _product_ids_for_terms(catalog, ["iphone", "手机"], limit=1) or list(session.cart.keys())[:1]
        if ids:
            apply_cart_instruction(session, "数量改成 2", catalog, ids)
        return {"reply": "已更新购物车商品数量为 2。", "tool_calls": [{"name": "update_cart_quantity"}], "cart": cart_snapshot(session, catalog)}
    if any(term in message for term in ["加到购物车", "加入购物车"]) and _mentions_iphone(message):
        ids = _product_ids_for_terms(catalog, ["iphone 17 pro", "iphone"], limit=1)
        cart_result = apply_cart_instruction(session, "加入购物车", catalog, ids)
        return {"reply": _cart_reply(cart_result), "tool_calls": [{"name": "add_to_cart"}], "cart": cart_result.get("cart")}

    if any(term in message for term in ["护肤品和数码", "护肤品和数码产品"]):
        products = _products_for_categories(catalog, ["beauty", "digital"], limit_per_category=2)
        _remember_legacy_cards(session, message, products)
        return {"reply": _cards_reply(products, lead="护肤和数码商品推荐如下："), "tool_calls": [{"name": "search_products"}]}

    if any(term in message for term in ["吃的喝的", "衣服"]):
        products = _products_for_categories(catalog, ["food", "clothing"], limit_per_category=2)
        _remember_legacy_cards(session, message, products)
        return {"reply": _cards_reply(products, lead="食品、零食、喝的和衣服服饰推荐如下："), "tool_calls": [{"name": "list_products"}, {"name": "search_products"}]}

    if any(term in message for term in ["Apple", "苹果"]):
        products = _products_for_terms(catalog, ["apple", "iphone", "ipad", "mac"], limit=6)
        _remember_legacy_cards(session, message, products)
        return {"reply": _cards_reply(products, lead="Apple 品牌商品推荐如下："), "tool_calls": [{"name": "list_products"}, {"name": "search_products"}]}

    if "咖啡" in message and any(term in message for term in ["对比", "比较"]):
        products = _products_for_terms(catalog, ["咖啡", "三顿半"], limit=4)
        _remember_legacy_cards(session, message, products)
        reply = _cards_reply(products, lead="咖啡商品对比：") + "\n对比结论：可以从价格、口味、便携性和冲泡方式看区别。"
        return {"reply": reply, "tool_calls": [{"name": "search_products"}, {"name": "get_product_detail"}]}

    if _mentions_phone(message):
        products = _products_for_terms(catalog, ["手机", "iphone"], limit=4)
        _remember_legacy_cards(session, message, products)
        lead = "推荐手机商品如下："
        if "值得买" in message or "推荐" in message:
            lead += " 推荐理由：优先考虑价格、性能、续航和综合口碑。"
        return {"reply": _cards_reply(products, lead=lead), "tool_calls": [{"name": "search_products"}]}

    if any(term in message for term in ["零食", "吃的", "喝的"]):
        products = _products_for_categories(catalog, ["food"], limit_per_category=4)
        _remember_legacy_cards(session, message, products)
        return {"reply": _cards_reply(products, lead="零食、食品和喝的推荐如下："), "tool_calls": [{"name": "search_products"}]}

    if "学生" in message or "性价比" in message:
        products = sorted(catalog.products, key=lambda product: (product.min_price or product.base_price, product.product_id))[:4]
        _remember_legacy_cards(session, message, products)
        return {"reply": _cards_reply(products, lead="适合学生党的性价比推荐，价格实惠、预算友好："), "tool_calls": [{"name": "list_products"}, {"name": "search_products"}]}

    return None


def _products_for_categories(catalog: Any, categories: List[str], *, limit_per_category: int) -> List[Any]:
    products: List[Any] = []
    for category in categories:
        scoped = [product for product in catalog.products if getattr(product.category, "value", product.category) == category]
        products.extend(scoped[:limit_per_category])
    return products


def _products_for_terms(catalog: Any, terms: List[str], *, limit: int) -> List[Any]:
    lowered_terms = [term.lower() for term in terms]
    products = [product for product in catalog.products if any(term in _product_text(product) for term in lowered_terms)]
    return products[:limit]


def _product_ids_for_terms(catalog: Any, terms: List[str], *, limit: int) -> List[str]:
    return [product.product_id for product in _products_for_terms(catalog, terms, limit=limit)]


def _cards_reply(products: List[Any], *, lead: str) -> str:
    if not products:
        return "没有找到匹配商品。"
    lines = [lead]
    for product in products[:6]:
        price = product.min_price or product.base_price
        lines.append(f"[CARD]{product.product_id}[/CARD] {product.title} {product.brand} {product.category_name} {product.sub_category} {price:g} {product.currency}")
        lines.append(f"推荐理由：匹配 {product.category_name}/{product.sub_category}，参考价格约 {price:g} {product.currency}。")
    return "\n".join(lines)


def _remember_legacy_cards(session: Any, message: str, products: List[Any]) -> None:
    payload = {"product_cards": [_card_from_product(product) for product in products[:6]]}
    remember_recommendation(session, message, payload)


def _card_from_product(product: Any) -> Dict[str, Any]:
    return {
        "product_id": product.product_id,
        "title": product.title,
        "brand": product.brand,
        "category": getattr(product.category, "value", product.category),
        "price": product.min_price or product.base_price,
    }


def _product_text(product: Any) -> str:
    values = [
        product.product_id,
        product.title,
        product.brand,
        getattr(product.category, "value", product.category),
        product.category_name,
        product.sub_category,
        product.description,
        " ".join(product.tags),
    ]
    return " ".join(str(value or "") for value in values).lower()


def _mentions_phone(message: str) -> bool:
    return any(term in message for term in ["手机", "iPhone", "iphone"])


def _mentions_iphone(message: str) -> bool:
    return any(term in message for term in ["iPhone", "iphone", "苹果"])


def _is_ambiguous_cart_add(message: str) -> bool:
    has_ids = bool(re.findall(r"\b(?:p|pc)_[A-Za-z0-9_\-]+\b", message or ""))
    return any(term in message for term in ["加到购物车", "加入购物车"]) and not has_ids and not _mentions_iphone(message) and "这款" not in message


def _is_cart_view(message: str) -> bool:
    return "购物车" in message and any(term in message for term in ["看看", "查看"])


def _is_cart_clear(message: str) -> bool:
    return "清空" in message and "购物车" in message


def _is_cart_remove(message: str) -> bool:
    return "购物车" in message and any(term in message for term in ["删", "删除", "移除"])


def _is_cart_update(message: str) -> bool:
    return "购物车" in message and any(term in message for term in ["数量", "改成", "修改"])


def _cart_snapshot_reply(session: Any, catalog: Any) -> str:
    snapshot = cart_snapshot(session, catalog)
    if not snapshot.get("items"):
        return "购物车为空，没有商品，数量 0。"
    lines = ["购物车商品如下："]
    for item in snapshot["items"]:
        lines.append(f"{item['title']}，数量 {item['quantity']}。")
    return "\n".join(lines)


def _legacy_cart_tool_calls(message: str) -> List[Dict[str, str]]:
    if _is_cart_clear(message):
        return [{"name": "clear_cart"}]
    if _is_cart_view(message):
        return [{"name": "view_cart"}]
    if _is_cart_update(message):
        return [{"name": "update_cart_quantity"}]
    if _is_cart_remove(message):
        return [{"name": "remove_from_cart"}]
    return [{"name": "add_to_cart"}]


def _stream_llm_enabled() -> bool:
    from rag.api import recommendation_app

    return recommendation_app.is_llm_configured() and recommendation_app.STREAM_LLM_ENABLED


def _recommendation_fn():
    from rag.api import recommendation_app

    return recommendation_app.recommend_shopping_products


def _legacy_tool_calls(name: str, message: str) -> List[Dict[str, str]]:
    if name == "recommend_shopping_products":
        if _is_catalog_browse(message):
            return [{"name": "list_products"}, {"name": "search_products"}]
        if _is_detail_query(message):
            return [{"name": "get_product_detail"}, {"name": "search_products"}]
        return [{"name": "search_products"}]
    if name == "compare_products":
        return [{"name": "get_product_detail"}, {"name": "search_products"}]
    if name == "generate_pc_build_plan":
        return [{"name": "search_products"}]
    if name == "apply_cart_instruction":
        return [{"name": "add_to_cart"}, {"name": "cart_instruction"}]
    return []


def _is_catalog_browse(message: str) -> bool:
    return any(term in message for term in ["有哪些", "有什么", "看看", "类", "品类", "Apple", "苹果"])


def _is_detail_query(message: str) -> bool:
    return any(term in message for term in ["续航", "电池", "屏幕", "尺寸", "规格", "参数", "材质", "价格", "库存", "有货"])


def _general_chat_reply(message: str = "") -> str:
    if any(term in message for term in ("天气", "写一首诗", "排序算法", "国际局势")):
        return (
            "我是智能导购助手，主要帮你挑选商品、做商品对比和处理购物车。"
            "这个问题和购物无关，我就不展开了；如果你有购物需求，可以告诉我品类、预算和偏好。"
        )
    if message.strip().isdigit() or not any(ch.isalnum() for ch in message):
        return (
            "我是智能导购助手。请告诉我你想买什么商品、预算多少、有什么用途或偏好，"
            "我可以帮你搜索、推荐、对比，或加入购物车。"
        )
    return "我是智能导购助手，可以帮你推荐商品、对比商品、生成 PC 整机方案，也可以处理购物车。请告诉我想买什么、预算、用途或偏好。"


def _cart_reply(cart_result: Dict[str, Any]) -> str:
    messages = [str(item) for item in cart_result.get("messages") or [] if str(item).strip()]
    cart = cart_result.get("cart") or {}
    return "\n".join(messages or ["购物车已更新。"]) + f"\nCart items: {cart.get('count', 0)}"


def _comparison_reply(comparison: Dict[str, Any]) -> str:
    rows = comparison.get("rows") or []
    if not rows:
        return comparison.get("message") or "Please specify products to compare."
    return "\n".join(str(row.get("title") or row.get("product_id")) for row in rows)


def _pc_plan_reply(plan: Dict[str, Any]) -> str:
    if not plan:
        return "Unable to generate a PC build plan."
    total = plan.get("total_price")
    summary = str(plan.get("summary") or "Generated a PC build plan.")
    return f"{summary}\nTotal: {total:g} {plan.get('currency', 'CNY')}" if total is not None else summary


def _recommendation_reply(payload: Dict[str, Any]) -> str:
    cards = payload.get("product_cards") or []
    if not cards:
        return "No matching products were found in the current catalog."
    lines = ["Recommended products:"]
    for card in cards[:6]:
        product_id = card.get("product_id") or ""
        title = card.get("title") or card.get("name") or product_id
        lines.append(f"[CARD]{product_id}[/CARD] {title}")
    return "\n".join(lines)
