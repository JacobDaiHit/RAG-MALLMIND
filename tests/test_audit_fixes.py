from types import SimpleNamespace

from rag.api.routes import chat
from rag.recommendation import recommendation_pipeline, response_generator, retrieval_fusion
from rag.recommendation.tool_handlers import product_cards_payload
from rag.storage.milvus_client import _normalize_search_hit


def test_milvus_hit_normalization_supports_nested_entity():
    hit = {
        "id": 7,
        "distance": 0.82,
        "entity": {
            "text": "商品证据",
            "product_id": "p_1",
            "title": "测试商品",
            "category": "digital",
        },
    }

    normalized = _normalize_search_hit(hit)

    assert normalized["id"] == 7
    assert normalized["product_id"] == "p_1"
    assert normalized["text"] == "商品证据"
    assert normalized["score"] == 0.82


def test_milvus_hit_normalization_supports_flat_entity():
    normalized = _normalize_search_hit(
        {"id": 8, "score": 0.4, "product_id": "p_2", "text": "flat"}
    )

    assert normalized["product_id"] == "p_2"
    assert normalized["text"] == "flat"
    assert normalized["score"] == 0.4


def test_retrieval_fusion_reuses_evidence_and_rejects_vector_only(monkeypatch):
    allowed = SimpleNamespace(product_id="allowed")
    blocked = SimpleNamespace(product_id="blocked")
    monkeypatch.setattr(retrieval_fusion, "VECTOR_RECALL_ENABLED", True)
    monkeypatch.setattr(
        retrieval_fusion,
        "_vector_recall",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not retrieve twice")),
    )

    result = retrieval_fusion.fuse_candidates(
        rule_filtered=[allowed],
        requirement=SimpleNamespace(),
        category=SimpleNamespace(value="digital"),
        catalog_products=[allowed, blocked],
        retrieved_product_ids=["blocked", "allowed"],
    )

    assert [item.product_id for item in result.fused_products] == ["allowed"]
    assert not result.vector_only_ids


def test_response_generator_uses_plain_text_contract(monkeypatch):
    class FakeClient:
        configured = True
        config = SimpleNamespace(fast_model="fast", model="main")

        def chat_text_with_report(self, *args, **kwargs):
            return "首选测试耳机，参考价 ¥399，适合通勤。", SimpleNamespace()

    monkeypatch.setattr(response_generator, "OpenAICompatibleChatClient", FakeClient)
    monkeypatch.setenv("RECOMMENDATION_RESPONSE_LLM", "true")
    payload = {
        "product_cards": [{"product_id": "p_1", "title": "测试耳机", "price": 399}],
        "requirement": {"price_max": 500},
        "fact_check": {"passed": True},
    }

    assert response_generator.generate_natural_response(payload, message="通勤耳机") == [
        "首选测试耳机，参考价 ¥399，适合通勤。"
    ]


def test_response_generator_rejects_unsupported_price(monkeypatch):
    class FakeClient:
        configured = True
        config = SimpleNamespace(fast_model="fast", model="main")

        def chat_text_with_report(self, *args, **kwargs):
            return "首选测试耳机，只要 ¥299。", SimpleNamespace()

    monkeypatch.setattr(response_generator, "OpenAICompatibleChatClient", FakeClient)
    monkeypatch.setenv("RECOMMENDATION_RESPONSE_LLM", "true")
    payload = {
        "product_cards": [{"product_id": "p_1", "title": "测试耳机", "price": 399}],
        "requirement": {"price_max": 500},
        "fact_check": {"passed": True},
    }

    result = response_generator.generate_natural_response(payload, message="通勤耳机")

    assert "299" not in result[0]
    assert "测试耳机" in result[0]


def test_product_card_sse_payload_is_versioned_and_compatible():
    cards = [{"product_id": "p_1"}]

    payload = product_cards_payload(cards)

    assert payload["schema_version"] == "product_cards.v2"
    assert payload["cards"] == cards
    assert payload["products"] == cards


def test_router_arguments_preserve_bundle_preferences_and_multimodal():
    requirement = recommendation_pipeline._requirement_from_args_v2(
        {
            "query": "旅行护肤一套",
            "category": "beauty",
            "need_bundle": True,
            "usage": ["旅行"],
            "preferences": {"texture": "清爽", "portable": True},
        },
        "旅行护肤一套，图片上下文：黑色便携包装",
    )

    assert requirement.need_bundle is True
    assert requirement.need_multimodal is True
    assert "旅行" in requirement.preferences
    assert "清爽" in requirement.preferences
    assert "portable" in requirement.preferences


def test_runtime_policy_fast_disables_external_enhancements(monkeypatch):
    monkeypatch.setattr(chat, "stream_llm_enabled", lambda: True)
    monkeypatch.setenv("RECOMMENDATION_ENABLE_MILVUS", "true")

    policy = chat.resolve_runtime_policy("fast")

    assert policy["mode"] == "fast"
    assert policy["use_llm"] is False
    assert policy["use_milvus_retrieval"] is False
    assert policy["use_vision_llm"] is False

