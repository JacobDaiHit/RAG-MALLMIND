from rag.recommendation.session_context import merge_requirement_memory, record_turn, session_context_for_llm
from rag.recommendation.session_state import CartItem, ShoppingSession, resolve_cart_product_ids


def test_multiturn_running_shoes_requirement_merge():
    session = ShoppingSession(session_id="session-context")

    merge_requirement_memory(session, {"raw_query": "帮我推荐跑鞋", "desired_categories": ["clothing"], "target_sub_categories": ["跑步鞋"]}, "帮我推荐跑鞋")
    merge_requirement_memory(session, {"raw_query": "要轻量的", "preferences": ["轻量"]}, "要轻量的")
    merged = merge_requirement_memory(session, {"raw_query": "预算 500 以内", "price_max": 500}, "预算 500 以内")

    assert merged["desired_categories"] == ["clothing"]
    assert "轻量" in merged["preferences"]
    assert merged["price_max"] == 500


def test_topic_switch_does_not_pollute_new_topic():
    session = ShoppingSession(session_id="session-switch")
    merge_requirement_memory(session, {"raw_query": "帮我推荐跑鞋", "desired_categories": ["clothing"], "target_sub_categories": ["跑步鞋"]}, "帮我推荐跑鞋")
    merged = merge_requirement_memory(session, {"raw_query": "再推荐一款手机", "desired_categories": ["digital"], "target_sub_categories": ["智能手机"]}, "再推荐一款手机")

    assert merged["desired_categories"] == ["digital"]
    assert "跑步鞋" not in merged.get("target_sub_categories", [])


def test_last_result_supports_previous_item_context():
    session = ShoppingSession(session_id="session-last")
    session.last_result = {"product_cards": [{"product_id": "p_beauty_001"}, {"product_id": "p_beauty_002"}]}

    ids = resolve_cart_product_ids(session, "把刚才那款加入购物车", "add")

    assert ids == ["p_beauty_001"]


def test_cart_index_remove_resolves_correct_item():
    session = ShoppingSession(session_id="session-cart")
    session.cart = {
        "p_beauty_001": CartItem(product_id="p_beauty_001"),
        "p_beauty_002": CartItem(product_id="p_beauty_002"),
    }

    ids = resolve_cart_product_ids(session, "删掉第一个", "remove", index=0)

    assert ids == ["p_beauty_001"]


def test_recent_turns_are_compacted_after_eight_turns():
    session = ShoppingSession(session_id="session-compact")
    for index in range(10):
        record_turn(session, role="user", content=f"turn {index}", tool_name="recommend_shopping_products")

    context = session_context_for_llm(session)
    assert len(context["recent_turns"]) == 8
    assert context["recent_turns_summary"]
