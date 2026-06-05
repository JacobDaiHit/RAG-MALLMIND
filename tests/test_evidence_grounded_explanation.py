import pytest

from rag.recommendation.explanation_builder import ALLOWED_LLM_INPUT_FIELDS, build_evidence_grounded_explanation, build_llm_explanation_input


def _product_card():
    return {
        "product_id": "p_digital_001",
        "title": "Catalog Phone",
        "brand": "Catalog Brand",
        "category": "digital",
        "sub_category": "智能手机",
        "price": 1999,
        "tags": ["拍照"],
        "best_for": ["日常使用"],
        "not_good_for": ["重度游戏"],
        "hallucinated_field": "must not pass",
    }


def test_llm_explanation_input_uses_only_whitelisted_top_level_fields():
    payload = build_llm_explanation_input(
        user_need="推荐一款手机",
        parsed_requirement={"raw_query": "推荐一款手机", "price_max": 2000, "unknown": "drop"},
        selected_products=[_product_card()],
    )

    assert set(payload).issubset(ALLOWED_LLM_INPUT_FIELDS)
    assert "unknown" not in payload["parsed_requirement"]
    assert "hallucinated_field" not in payload["selected_products"][0]
    assert "product_id" not in payload["selected_products"][0]


def test_product_card_fields_remain_catalog_owned():
    card = _product_card()
    payload = build_llm_explanation_input(user_need="need", parsed_requirement={}, selected_products=[card])

    selected = payload["selected_products"][0]
    assert selected["title"] == card["title"]
    assert selected["brand"] == card["brand"]
    assert selected["price"] == card["price"]


def test_llm_explanation_disabled_uses_template_fallback():
    result = build_evidence_grounded_explanation(
        user_need="need",
        parsed_requirement={},
        selected_products=[_product_card()],
        use_llm=False,
    )

    assert result["mode"] == "template"
    assert result["explanation"]["why_recommended"]


def test_comparison_explanation_input_includes_comparison_table():
    result = build_evidence_grounded_explanation(
        user_need="对比一下",
        parsed_requirement={"need_comparison": True},
        selected_products=[_product_card()],
        comparison_table=[{"product_id": "p1", "title": "A", "price": 1, "extra": "drop"}],
        use_llm=False,
    )

    assert "comparison_table" in result["llm_input"]
    assert "extra" not in result["llm_input"]["comparison_table"][0]
    assert "product_id" not in result["llm_input"]["comparison_table"][0]


def test_llm_failure_falls_back_to_template(monkeypatch):
    class BrokenClient:
        configured = True

        class config:
            fast_model = "test"

        def chat_json(self, *args, **kwargs):
            raise ValueError("invalid json")

    monkeypatch.setattr("rag.recommendation.explanation_builder.OpenAICompatibleChatClient", BrokenClient)
    result = build_evidence_grounded_explanation(
        user_need="need",
        parsed_requirement={},
        selected_products=[_product_card()],
        use_llm=True,
        timeout_seconds=1,
    )

    assert result["mode"] == "fallback"
    assert result["explanation"]["caveat"]
