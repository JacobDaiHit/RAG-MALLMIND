"""Tests for recommendation_app SSE and cart behavior."""

import base64
import json

from fastapi.testclient import TestClient

import rag.api.attachments as attachment_api
import rag.api.recommendation_app as recommendation_app
from rag.recommendation.image_retrieval import ImageRetrievalEvidence
from rag.api.recommendation_app import VALIDATION_VERSION, app, sse_event


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


def test_sse_event_formats_event_and_data_correctly():
    result = sse_event("test_event", {"key": "value"})
    assert result.startswith("event: test_event\n")
    assert '"key": "value"' in result
    assert result.endswith("\n\n")


def test_sse_event_handles_chinese_characters():
    result = sse_event("done", {"label": "unicode-label"})
    assert "unicode-label" in result


def test_sse_event_produces_valid_json_data_line():
    result = sse_event("info", {"x": 1, "y": [2, 3]})
    _, data_json = result.strip().split("data: ")
    parsed = json.loads(data_json.split("\n")[0])
    assert parsed == {"x": 1, "y": [2, 3]}


def test_validation_error_stream_yields_exactly_two_events():
    response = client.get("/api/stream-recommend", params={"goal": ""})
    assert response.status_code == 200
    events = _parse_sse_text(response.text)
    assert len(events) == 2
    assert events[0][0] == "validation_error"
    assert events[1][0] == "done"


def test_validation_error_stream_uses_clean_chinese_labels():
    response = client.get("/api/stream-recommend", params={"goal": ""})
    events = _parse_sse_text(response.text)
    assert "label" in events[0][1]
    assert events[0][1]["validation_version"] == VALIDATION_VERSION
    assert "label" in events[1][1]


def test_validation_error_stream_detail_is_empty_goal_error():
    response = client.get("/api/stream-recommend", params={"goal": ""})
    events = _parse_sse_text(response.text)
    assert "goal cannot be empty" in events[0][1]["detail"]


def test_validation_error_stream_sse_response_content_type():
    response = client.get("/api/stream-recommend", params={"goal": ""})
    assert response.headers["content-type"] == "text/event-stream"


def test_chat_stream_returns_readme_closure_events(monkeypatch):
    monkeypatch.setattr(recommendation_app, "STREAM_LLM_ENABLED", False)
    response = client.post(
        "/api/chat/stream",
        json={
            "session_id": "test-chat-stream",
            "message": "推荐一款300元以内的蓝牙耳机，不要白色，续航要久",
            "attachments": [],
            "images": [],
        },
    )

    assert response.status_code == 200
    events = _parse_sse_text(response.text)
    event_names = [event[0] for event in events]
    assert "intent_route" in event_names
    assert "delta" in event_names
    assert "progress" in event_names
    assert "product_cards" in event_names
    assert "candidate_scope" in event_names
    assert "comparison_table" in event_names
    assert event_names[-1] == "done"


def test_chat_stream_emits_multiple_progress_updates(monkeypatch):
    monkeypatch.setattr(recommendation_app, "STREAM_LLM_ENABLED", False)
    response = client.post(
        "/api/chat/stream",
        json={
            "session_id": "test-chat-progress",
            "message": "推荐一款300元以内的蓝牙耳机，不要白色，续航要久",
            "attachments": [],
            "images": [],
        },
    )

    assert response.status_code == 200
    events = _parse_sse_text(response.text)
    progress_events = [data for event, data in events if event == "progress"]
    progress_text = "\n".join(f"{item.get('label')} {item.get('detail')}" for item in progress_events)

    assert len(progress_events) >= 5
    assert all(item.get("label") for item in progress_events[:3])
    assert all(item.get("detail") is not None for item in progress_events[:3])
    assert any("RAG" in str(item.get("label")) or item.get("detail") for item in progress_events)


def test_chat_stream_general_recommendation_does_not_force_comparison(monkeypatch):
    monkeypatch.setattr(recommendation_app, "STREAM_LLM_ENABLED", False)
    response = client.post(
        "/api/chat/stream",
        json={
            "session_id": "test-chat-no-forced-comparison",
            "message": "推荐一款300元以内的蓝牙耳机，不要白色，续航要久",
            "attachments": [],
            "images": [],
        },
    )

    assert response.status_code == 200
    events = _parse_sse_text(response.text)
    comparison_events = [data for event, data in events if event == "comparison_table"]
    delta_text = "\n".join(data.get("text", "") for event, data in events if event == "delta")

    assert comparison_events == [{"rows": []}]
    assert "意图路由" not in delta_text
    assert "candidate_scope" not in delta_text


def test_chat_stream_passes_enabled_llm_flag_to_recommendation(monkeypatch):
    seen = {}
    original_recommend = recommendation_app.recommend_shopping_products

    def fake_recommend(goal, use_llm=True, image_retrieval_evidence=None):
        seen["use_llm"] = use_llm
        return original_recommend(goal, use_llm=False, image_retrieval_evidence=image_retrieval_evidence)

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(recommendation_app, "STREAM_LLM_ENABLED", True)
    monkeypatch.setattr(recommendation_app, "is_llm_configured", lambda: True)
    monkeypatch.setattr(recommendation_app, "recommend_shopping_products", fake_recommend)

    response = client.post(
        "/api/chat/stream",
        json={
            "session_id": "test-chat-llm-enabled",
            "message": "推荐一款300元以内的蓝牙耳机，不要白色，续航要久",
            "attachments": [],
            "images": [],
        },
    )

    assert response.status_code == 200
    events = _parse_sse_text(response.text)
    result_events = [data for event, data in events if event == "result"]

    assert seen["use_llm"] is True
    assert result_events[0]["trace"]["stream_llm_enabled"] is True


def test_prepare_recommendation_context_reuses_attachment_and_session_context():
    session = recommendation_app.get_session("test-shared-context-helper")
    session.last_goal = "recommend entry-level noise cancelling earphones"

    contextual_goal, attachments, report = recommendation_app.prepare_recommendation_context(
        "battery",
        [{"name": "earphone.jpg", "type": "image/jpeg", "size": 12}],
        session,
    )

    assert "User added constraints" in contextual_goal
    assert "图片上下文" in contextual_goal
    assert attachments == [{"name": "earphone.jpg", "type": "image/jpeg", "size": 12}]
    assert report["count"] == 1
    assert report["reused_count"] == 1


def test_chat_stream_analyzes_image_payload_before_recommendation(monkeypatch):
    class FakeVisionClient:
        configured = True

        class Config:
            model = "fake-vision-model"

        config = Config()

        def chat_json(self, *args, **kwargs):
            return {
                "summary": "图片中是黑色连帽卫衣，适合同款或相似穿搭。",
                "extracted_text": "BLACK JACKET",
                "signals": ["image_input", "clothing", "black_jacket"],
                "shopping_hints": ["服饰运动", "黑色", "连帽卫衣", "同款"],
                "visual_query_terms": ["服饰运动", "卫衣", "黑色", "连帽", "棉质", "通勤"],
                "visual_attributes": {
                    "category": "服饰运动",
                    "sub_category": "卫衣",
                    "colors": ["黑色"],
                    "materials": ["棉质"],
                    "features": ["连帽"],
                    "scene": "通勤休闲",
                    "style": "basic",
                },
            }

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(recommendation_app, "STREAM_LLM_ENABLED", True)
    monkeypatch.setattr(recommendation_app, "is_llm_configured", lambda: True)
    monkeypatch.setattr(attachment_api, "OpenAICompatibleChatClient", FakeVisionClient)
    image_payload = base64.b64encode(b"demo-image-bytes").decode("ascii")

    response = client.post(
        "/api/chat/stream",
        json={
            "session_id": "test-chat-image-payload",
            "message": "我想找同款外套，预算500以内",
            "mode": "full",
            "attachments": [
                {
                    "name": "street.jpg",
                    "type": "image/jpeg",
                    "size": 16,
                    "data_url": f"data:image/jpeg;base64,{image_payload}",
                }
            ],
            "images": [],
        },
    )

    assert response.status_code == 200
    events = _parse_sse_text(response.text)
    attachment_events = [data for event, data in events if event == "attachment_analysis"]
    result_events = [data for event, data in events if event == "result"]

    assert attachment_events
    assert attachment_events[0]["attachments"][0]["analysis_status"] == "success"
    assert "黑色连帽卫衣" in attachment_events[0]["attachments"][0]["summary"]
    assert attachment_events[0]["attachments"][0]["visual_attributes"]["sub_category"] == "卫衣"
    assert "棉质" in attachment_events[0]["attachments"][0]["visual_query_terms"]

    result = result_events[0]
    assert result["requirement"]["need_multimodal"] is True
    assert "image" in result["requirement"]["input_modalities"]
    assert "黑色连帽卫衣" in result["trace"]["attachments"][0]["summary"]
    assert "卫衣" in result["requirement"]["target_sub_categories"]
    assert "黑色" in result["requirement"]["must_have_terms"]
    assert result["trace"]["preprocessed_input"]["modalities"] == ["text", "image"]


def test_chat_stream_fuses_image_vector_retrieval_trace(monkeypatch):
    monkeypatch.setattr(recommendation_app, "STREAM_LLM_ENABLED", False)

    def fake_retrieve_image_evidence(*args, **kwargs):
        return ImageRetrievalEvidence(
            by_product_id={
                "p_clothes_005": [
                    {
                        "product_id": "p_clothes_005",
                        "title": "李宁 运动生活系列 男子连帽套头卫衣 基础Logo印花上衣",
                        "category": "clothing",
                        "score": 0.93,
                        "retrieval_mode": "image_vector",
                        "embedding_version": "pixel-hist-v1",
                    }
                ]
            },
            total_hits=1,
            status="ok",
            index_path="test-index",
            query_count=1,
        )

    monkeypatch.setattr(recommendation_app, "retrieve_image_evidence", fake_retrieve_image_evidence)

    response = client.post(
        "/api/chat/stream",
        json={
            "session_id": "test-chat-image-vector-fusion",
            "message": "帮我找同款卫衣，预算500以内",
            "attachments": [{"name": "street.jpg", "type": "image/jpeg", "size": 16}],
            "images": [],
        },
    )

    assert response.status_code == 200
    events = _parse_sse_text(response.text)
    result = [data for event, data in events if event == "result"][0]
    assert result["trace"]["image_retrieval"]["status"] == "ok"
    assert "p_clothes_005" in result["trace"]["image_retrieval"]["matched_product_ids"]
    assert result["trace"]["fused_retrieval"]["total_hits"] >= 1
    assert "p_clothes_005" in result["trace"]["fused_retrieval"]["matched_product_ids"]


def test_cart_actions_adds_grounded_product_by_id():
    response = client.post(
        "/api/cart/actions",
        json={
            "session_id": "test-cart-actions",
            "instruction": "把这个加入购物车",
            "product_ids": ["p_digital_001"],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["cart"]["items"][0]["product_id"] == "p_digital_001"
    assert data["cart"]["total_price"] > 0


def _tool_names(data):
    return {item.get("name") for item in data.get("tool_calls", [])}


def test_chat_search_and_fact_queries_return_product_tool_calls(monkeypatch):
    monkeypatch.setattr(recommendation_app, "STREAM_LLM_ENABLED", True)
    monkeypatch.setattr(recommendation_app, "is_llm_configured", lambda: True)
    cases = [
        ("有没有适合学生的笔记本电脑？", {"search_products"}),
        ("Apple 的商品有哪些？", {"list_products", "search_products"}),
        ("有没有卖冰箱的？", {"search_products"}),
        ("iPhone 17 Pro 的续航怎么样？", {"get_product_detail", "search_products"}),
        ("有什么适合送女朋友的礼物？", {"search_products"}),
    ]

    for index, (message, expected_tools) in enumerate(cases):
        response = client.post(
            "/api/chat",
            json={
                "session_id": f"test-chat-product-query-current-contract-{index}",
                "message": message,
                "attachments": [],
                "images": [],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert _tool_names(data) & expected_tools


def test_chat_cart_add_accepts_cart_capability_and_verifies_business_result(monkeypatch):
    monkeypatch.setattr(recommendation_app, "STREAM_LLM_ENABLED", True)
    monkeypatch.setattr(recommendation_app, "is_llm_configured", lambda: True)
    response = client.post(
        "/api/chat",
        json={
            "session_id": "test-chat-cart-add-current-contract",
            "message": "把 p_digital_001 加入购物车",
            "attachments": [],
            "images": [],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert _tool_names(data) & {"add_to_cart", "cart_instruction", "apply_cart_instruction"}
    assert data["cart"]["count"] == 1
    assert data["cart"]["items"][0]["product_id"] == "p_digital_001"
    assert data["cart"]["total_price"] > 0


def test_chat_cart_ambiguous_add_routes_to_cart_without_claiming_success(monkeypatch):
    monkeypatch.setattr(recommendation_app, "STREAM_LLM_ENABLED", False)
    response = client.post(
        "/api/chat",
        json={
            "session_id": "test-chat-cart-add-ambiguous-current-contract",
            "message": "帮我加入购物车",
            "attachments": [],
            "images": [],
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert _tool_names(data) & {"cart_instruction", "apply_cart_instruction"}
    assert data["cart"]["count"] == 0
    assert data["cart"]["items"] == []


def test_chat_followup_product_detail_uses_previous_recommendation(monkeypatch):
    monkeypatch.setattr(recommendation_app, "STREAM_LLM_ENABLED", False)
    session_id = "test-chat-product-detail-followup-current-contract"
    first = client.post(
        "/api/chat",
        json={
            "session_id": session_id,
            "message": "推荐一款手机",
            "attachments": [],
            "images": [],
        },
    )
    assert first.status_code == 200

    second = client.post(
        "/api/chat",
        json={
            "session_id": session_id,
            "message": "续航怎么样？",
            "attachments": [],
            "images": [],
        },
    )

    assert second.status_code == 200
    assert _tool_names(second.json()) & {"get_product_detail", "search_products"}


def test_cart_action_messages_are_specific_for_update_and_remove():
    session_id = "test-cart-action-specific-messages"
    add = client.post(
        "/api/cart/actions",
        json={
            "session_id": session_id,
            "instruction": "加入购物车",
            "product_ids": ["p_digital_001"],
        },
    )
    assert add.status_code == 200

    update = client.post(
        "/api/cart/actions",
        json={
            "session_id": session_id,
            "instruction": "数量改成 2",
            "product_ids": ["p_digital_001"],
        },
    )
    assert update.status_code == 200
    update_text = "\n".join(update.json()["messages"])
    assert "数量" in update_text
    assert "修改" in update_text
    assert "2" in update_text

    remove = client.post(
        "/api/cart/actions",
        json={
            "session_id": session_id,
            "instruction": "删除",
            "product_ids": ["p_digital_001"],
        },
    )
    assert remove.status_code == 200
    assert "移除" in "\n".join(remove.json()["messages"])


def test_products_endpoint_exposes_pc_parts_without_static_images():
    response = client.get("/api/products", params={"category": "pc_cpu"})

    assert response.status_code == 200
    data = response.json()
    assert data["count"] > 0
    product = data["products"][0]
    assert product["product_id"].startswith("pc_cpu_")
    assert "image_url" not in product
    assert "image_path" not in product


def test_product_compare_returns_structured_rows():
    response = client.post(
        "/api/products/compare",
        json={"product_ids": ["p_digital_001", "p_digital_002"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 2
    assert len(data["rows"]) == 2
    assert data["recommendation"]["product_id"] in {"p_digital_001", "p_digital_002"}


def test_recommend_endpoint_infers_pc_parts_scope_for_pc_part_catalog():
    cases = [
        "推荐一款 RTX 4070 显卡",
        "推荐一款 CPU",
        "推荐一款 SSD",
        "推荐一款机箱",
    ]

    for goal in cases:
        response = client.post(
            "/api/recommend",
            json={
                "goal": goal,
                "attachments": [],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["trace"]["catalog_scope"] == "pc_parts"
        assert data["trace"]["recommendation_domain"] == "single_pc_part"
        assert data["product_cards"]
        assert all(card["category"].startswith("pc_") for card in data["product_cards"])


def test_parse_adjustment_amount_ignores_gpu_model_numbers():
    assert recommendation_app.parse_adjustment_amount("换 4070，预算别变", default=500) == 500
    assert recommendation_app.parse_adjustment_amount("预算降 800 元", default=500) == 800
