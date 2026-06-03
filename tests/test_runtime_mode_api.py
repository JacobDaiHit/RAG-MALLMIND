import json

from fastapi.testclient import TestClient

from rag.api.recommendation_app import app
from rag.recommendation.recommendation_pipeline import recommend_shopping_products


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
        "推荐一款手机",
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
    assert payload["product_cards"]
    trace = payload["trace"]
    assert trace["runtime_mode"] == "fast"
    assert trace["requested_mode"] == "auto"
    assert trace["selected_mode"] == "fast"
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
    assert runtime_events[0]["mode"] in {"fast", "balanced", "full"}
    assert runtime_events[0]["requested_mode"] == "auto"
    assert runtime_events[0]["selected_mode"] in {"fast", "balanced", "full"}
    assert runtime_events[0]["reason"]
    assert "policy" in runtime_events[0]


def test_milvus_disabled_recommendation_still_returns_products(monkeypatch):
    monkeypatch.setenv("RECOMMENDATION_ENABLE_MILVUS", "false")

    response = client.post("/api/recommend", json={"goal": "推荐一款手机", "mode": "fast"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["product_cards"]
    assert payload["trace"]["milvus_retrieval"]["status"] == "disabled"
