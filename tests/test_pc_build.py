import json

from fastapi.testclient import TestClient

from rag.api.recommendation_app import app
from rag.recommendation.pc_build import generate_pc_build_plan


client = TestClient(app)


def _parse_sse_text(raw: str):
    events = []
    for block in raw.strip().split("\n\n"):
        if not block.strip():
            continue
        event_name = "message"
        data_str = ""
        for line in block.strip().split("\n"):
            if line.startswith("event: "):
                event_name = line[len("event: "):]
            elif line.startswith("data: "):
                data_str = line[len("data: "):]
        events.append((event_name, json.loads(data_str) if data_str else {}))
    return events


def test_generate_pc_build_plan_returns_compatible_real_parts():
    plan = generate_pc_build_plan(
        budget=7000,
        usage=["3A游戏", "轻度剪辑"],
        preferences={"color": "白色", "noise": "低噪音"},
    )

    assert plan["type"] == "pc_build_plan"
    assert plan["compatibility"]["status"] == "pass"
    assert {item["role"] for item in plan["items"]} == {
        "cpu",
        "motherboard",
        "gpu",
        "memory",
        "ssd",
        "psu",
        "case",
        "cpu_cooler",
    }
    assert all(item["product_id"].startswith("pc_") for item in plan["items"])
    assert plan["total_price"] > 0


def test_generate_pc_build_plan_respects_strict_budget_cap():
    plan = generate_pc_build_plan(
        budget=7000,
        usage=["3A游戏"],
        preferences={"budget_strict": True},
    )

    assert plan["type"] == "pc_build_plan"
    assert plan["total_price"] <= 7000
    assert plan["preferences"]["budget_strict"] is True


def test_pc_build_generate_endpoint_matches_readme_shape():
    response = client.post(
        "/api/pc-build/generate",
        json={
            "budget": 7000,
            "usage": ["3A游戏", "轻度剪辑"],
            "preferences": {"color": "白色", "noise": "低噪音", "exclude_brands": []},
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "pc_build_plan"
    assert data["budget"] == 7000
    assert data["items"]
    assert data["compatibility"]["checks"]


def test_chat_stream_emits_pc_build_plan_event():
    response = client.post(
        "/api/chat/stream",
        json={
            "session_id": "test-pc-stream",
            "message": "帮我配一台 7000 元以内的游戏电脑，白色低噪音",
            "images": [],
        },
    )

    assert response.status_code == 200
    assert "event: pc_build_plan" in response.text
    assert "event: product_cards" not in response.text
    assert response.text.strip().endswith('data: {"session_id": "test-pc-stream"}')


def test_chat_stream_treats_within_budget_as_hard_cap():
    response = client.post(
        "/api/chat/stream",
        json={
            "session_id": "test-pc-stream-strict-budget",
            "message": "帮我配一台 7000 元以内的游戏电脑，白色",
            "images": [],
        },
    )

    assert response.status_code == 200
    plans = [data for event, data in _parse_sse_text(response.text) if event == "pc_build_plan"]
    assert plans
    assert plans[0]["budget"] == 7000
    assert plans[0]["total_price"] <= 7000
    assert plans[0]["preferences"]["budget_strict"] is True


def test_pc_build_followup_can_lower_budget():
    session_id = "test-pc-followup-lower-budget"
    first = client.post(
        "/api/chat/stream",
        json={"session_id": session_id, "message": "帮我配一台 7000 元以内的游戏电脑", "images": []},
    )
    second = client.post(
        "/api/chat/stream",
        json={"session_id": session_id, "message": "再便宜 500 元", "images": []},
    )

    first_plan = [data for event, data in _parse_sse_text(first.text) if event == "pc_build_plan"][0]
    second_plan = [data for event, data in _parse_sse_text(second.text) if event == "pc_build_plan"][0]
    assert second_plan["budget"] == first_plan["budget"] - 500
    assert second_plan["total_price"] <= second_plan["budget"] * 1.08


def test_pc_build_plan_includes_grounded_reasons_and_previous_comparison():
    first = generate_pc_build_plan(
        budget=7000,
        usage=["游戏"],
        preferences={"color": "白色", "noise": "低噪音", "budget_strict": True},
    )
    second = generate_pc_build_plan(
        budget=10000,
        usage=["游戏"],
        preferences={"color": "白色", "noise": "低噪音", "budget_strict": True},
        previous_plan=first,
    )

    assert first["recommendation_reasons"]
    assert all(item["reason"] for item in first["items"])
    assert second["comparison"]["baseline_label"] == "上一个方案"
    assert second["comparison"]["price_delta"] == second["total_price"] - first["total_price"]
    assert second["comparison"]["highlights"]


def test_pc_build_whole_plan_can_be_added_to_cart_by_followup():
    session_id = "test-pc-add-whole-plan"
    first = client.post(
        "/api/chat/stream",
        json={"session_id": session_id, "message": "帮我配一台 7000 元以内的游戏电脑", "images": []},
    )
    assert "event: pc_build_plan" in first.text

    second = client.post(
        "/api/chat/stream",
        json={"session_id": session_id, "message": "把这套加入购物车", "images": []},
    )

    cart_events = [data for event, data in _parse_sse_text(second.text) if event == "cart"]
    assert cart_events
    assert cart_events[0]["cart"]["count"] == 8
    assert all(item["product_id"].startswith("pc_") for item in cart_events[0]["cart"]["items"])


def test_pc_topic_memory_keeps_color_followup_on_pc_route():
    session_id = "test-pc-topic-memory-color-followup"
    first = client.post(
        "/api/chat/stream",
        json={"session_id": session_id, "message": "帮我配一台 7000 元以内的游戏电脑，白色，低噪音", "images": []},
    )
    second = client.post(
        "/api/chat/stream",
        json={"session_id": session_id, "message": "我想要黑色色系的了", "images": []},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    events = _parse_sse_text(second.text)
    event_names = [event for event, _data in events]
    plans = [data for event, data in events if event == "pc_build_plan"]
    tool_calls = [data for event, data in events if event == "tool_call"]

    assert "pc_build_plan" in event_names
    assert "product_cards" not in event_names
    assert tool_calls[0]["name"] == "generate_pc_build_plan"
    assert plans[0]["preferences"]["color"] == "黑色"
    assert plans[0]["topic_memory"]["topic_type"] == "pc_build"


def test_explicit_product_category_switches_away_from_pc_topic(monkeypatch):
    import rag.api.recommendation_app as recommendation_app

    monkeypatch.setattr(recommendation_app, "STREAM_LLM_ENABLED", False)
    session_id = "test-topic-memory-switch-to-headphones"
    client.post(
        "/api/chat/stream",
        json={"session_id": session_id, "message": "帮我配一台 7000 元以内的游戏电脑，白色，低噪音", "images": []},
    )
    second = client.post(
        "/api/chat/stream",
        json={"session_id": session_id, "message": "推荐一个 500 元以内黑色降噪耳机", "images": []},
    )

    events = _parse_sse_text(second.text)
    event_names = [event for event, _data in events]
    tool_calls = [data for event, data in events if event == "tool_call"]
    results = [data for event, data in events if event == "result"]

    assert second.status_code == 200
    assert "product_cards" in event_names
    assert "pc_build_plan" not in event_names
    assert tool_calls[0]["name"] == "recommend_shopping_products"
    assert results[0]["trace"]["topic_memory"]["topic_type"] == "ecommerce_recommendation"
    assert results[0]["trace"]["topic_memory"]["subject"] == "耳机"
