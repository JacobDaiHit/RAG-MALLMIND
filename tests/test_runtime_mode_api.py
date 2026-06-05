import json

from fastapi.testclient import TestClient

from rag.api.recommendation_app import app
from rag.recommendation.recommendation_pipeline import recommend_shopping_products
from rag.recommendation.session_state import get_session


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


def test_recommend_fast_mode_does_not_call_llm(monkeypatch):
    class ExplodingClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("LLM client should not be constructed in fast mode")

    monkeypatch.setattr("rag.recommendation.recommendation_pipeline.OpenAICompatibleChatClient", ExplodingClient)

    result = recommend_shopping_products(
        "推荐一款适合油皮夏天用的防晒",
        use_llm=False,
        use_llm_guidance=False,
        use_milvus_retrieval=False,
        use_rag_query_expansion=False,
    )

    assert result.product_cards


def test_recommend_endpoint_default_is_stable_fast_mode():
    response = client.post("/api/recommend", json={"goal": "推荐一款手机"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["product_cards"] == []
    assert payload["trace"]["clarification_required"] is True
    trace = payload["trace"]
    assert trace["runtime_mode"] == "degraded_fast"
    assert trace["requested_mode"] == "auto"
    assert trace["selected_mode"] == "degraded_fast"
    assert trace["llm_configured"] is False
    assert trace["use_milvus_retrieval"] is False
    assert trace["use_rag_query_expansion"] is False
    assert trace["runtime_policy"]["use_requirement_llm"] is False
    assert trace["runtime_policy"]["use_guidance_llm"] is False
    assert trace["runtime_policy"]["use_vision_llm"] is False


def test_chat_stream_emits_runtime_mode_event():
    response = client.post("/api/chat/stream", json={"message": "推荐一款手机"})

    assert response.status_code == 200
    events = _parse_sse_text(response.text)
    runtime_events = [data for name, data in events if name == "runtime_mode"]
    assert runtime_events
    assert runtime_events[0]["mode"] in {"fast", "balanced", "full", "degraded_fast"}
    assert runtime_events[0]["requested_mode"] == "auto"
    assert runtime_events[0]["selected_mode"] in {"fast", "balanced", "full", "degraded_fast"}
    assert runtime_events[0]["reason"]
    assert "policy" in runtime_events[0]


def test_milvus_disabled_recommendation_still_returns_products(monkeypatch):
    monkeypatch.setenv("RECOMMENDATION_ENABLE_MILVUS", "false")

    response = client.post("/api/recommend", json={"goal": "推荐一款适合油皮夏天用的防晒", "mode": "fast"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["product_cards"]
    assert payload["trace"]["milvus_retrieval"]["status"] == "disabled"


def test_recommend_auto_uses_local_route_and_rule_parse_before_fast_policy(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MALLMIND_LLM_ENABLED", "true")
    monkeypatch.setattr("rag.api.routes.recommend.stream_llm_enabled", lambda: True)

    response = client.post("/api/recommend", json={"goal": "推荐一款200元以内的面霜", "mode": "auto"})

    assert response.status_code == 200
    trace = response.json()["trace"]
    assert trace["selected_runtime_mode"] == "fast"
    assert trace["route_confidence"] >= 0.85
    assert trace["route_margin"] >= 0.25
    assert trace["requirement_completeness"] >= 0.75
    assert trace["query_complexity"] <= 0.45
    assert trace["history_dependency"] < 0.45
    assert "high_confidence_simple_query" in trace["reason_codes"]


def test_chat_stream_compare_auto_selects_balanced_with_runtime_signals(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MALLMIND_LLM_ENABLED", "true")
    monkeypatch.setattr("rag.api.routes.chat.stream_llm_enabled", lambda: True)

    response = client.post("/api/chat/stream", json={"message": "帮我比较两款面霜哪个更保湿", "mode": "auto"})

    assert response.status_code == 200
    events = _parse_sse_text(response.text)
    runtime_event = next(data for name, data in events if name == "runtime_mode")
    assert runtime_event["selected_mode"] == "balanced"
    assert "comparison_request" in runtime_event["reason_codes"]
    for key in ["route_confidence", "route_margin", "requirement_completeness", "query_complexity", "history_dependency"]:
        assert key in runtime_event


def test_runtime_context_followup_selects_balanced(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MALLMIND_LLM_ENABLED", "true")
    monkeypatch.setattr("rag.api.routes.recommend.stream_llm_enabled", lambda: True)
    session = get_session("runtime-followup-test")
    session.last_requirement = {"desired_categories": ["clothing"], "target_sub_categories": ["跑步鞋"]}

    response = client.post("/api/recommend", json={"goal": "\u8dd1\u978b\u9884\u7b97500\u4ee5\u5185", "mode": "auto", "session_id": session.session_id})

    assert response.status_code == 200
    trace = response.json()["trace"]
    assert trace["selected_runtime_mode"] == "balanced"
    assert trace["history_dependency"] >= 0.45


def test_runtime_context_full_for_image_or_detailed_analysis():
    from rag.api.runtime_context import build_adaptive_runtime_context
    from rag.recommendation.session_state import ShoppingSession

    context = build_adaptive_runtime_context(
        "请根据图片详细分析并推荐同款外套",
        ShoppingSession(session_id="runtime-full-test"),
        llm_configured=True,
        has_attachments=True,
        has_image_data=True,
    )

    assert context["decision"].selected_mode == "full"
    assert "multimodal_input" in context["decision"].reason_codes
