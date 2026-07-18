"""V3-only HTTP integration checks; no legacy route or session fields exist."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from rag.api.recommendation_app import app
from rag.recommendation.session_state import get_session


client = TestClient(app)


@pytest.fixture(autouse=True)
def disable_live_retrieval(monkeypatch):
    monkeypatch.setenv("V3_RETRIEVAL_ENABLED", "false")


def _events(raw: str):
    parsed = []
    for block in raw.strip().split("\n\n"):
        if not block:
            continue
        name = next((line[7:] for line in block.splitlines() if line.startswith("event: ")), "message")
        data = next((line[6:] for line in block.splitlines() if line.startswith("data: ")), "{}")
        parsed.append((name, json.loads(data)))
    return parsed


def test_certified_recommendation_uses_v3_and_writes_only_compact_core():
    session_id = "v3-api-recommend"
    response = client.post("/api/chat/stream", json={"session_id": session_id, "message": "推荐手机，10000元以内，不要小米，拍照优先"})
    assert response.status_code == 200
    events = _events(response.text)
    route = next(data for name, data in events if name == "v3_routing")
    trace = next(data for name, data in events if name == "v3_trace")
    cards = next(data["cards"] for name, data in events if name == "product_cards")
    assert route["grammar_id"] == "recommend.category_constraints.v1"
    assert route["recommendation_mode"] == "product"
    assert trace["session_live_card_count"] == 0
    assert trace["semantic_card_references"] == []
    assert "card_ids" not in trace
    assert cards and all(card["product_id"] not in {"p_digital_008", "p_digital_009", "p_digital_010"} for card in cards)
    session = get_session(session_id)
    assert set(session.__dict__) == {"session_id", "updated_at", "v3_core"}
    assert session.v3_core["active_requirement"]["exclude_brand_family_ids"] == ["xiaomi"]


def test_attachment_and_removed_legacy_routes_do_not_fallback():
    response = client.post("/api/chat/stream", json={"session_id": "v3-api-attachment", "message": "推荐手机", "attachments": [{"name": "x.png"}]})
    assert response.status_code == 200
    assert any(name == "error" and data["label"] == "附件导购暂不可用" for name, data in _events(response.text))
    assert client.post("/api/chat", json={"session_id": "x", "message": "hi"}).status_code == 404
    assert client.post("/api/recommend", json={"goal": "推荐手机"}).status_code == 404


def test_v3_card_fact_query_reads_live_catalog_card_reference():
    session_id = "v3-api-fact"
    client.post("/api/chat/stream", json={"session_id": session_id, "message": "推荐手机，10000元以内，不要小米"})
    response = client.post("/api/chat/stream", json={"session_id": session_id, "message": "第一个的参数"})
    event_names = [name for name, _data in _events(response.text)]
    assert "product_fact" in event_names
    assert "tool_call" in event_names


def test_empty_candidate_gate_reports_catalog_scope_without_retrieval():
    response = client.post(
        "/api/chat/stream",
        json={"session_id": "v3-api-no-candidates", "message": "推荐手机，1元以内"},
    )
    assert response.status_code == 200
    events = _events(response.text)
    error = next(data for name, data in events if name == "error")
    assert error["reason"] == "catalog_scope_unsupported"
    assert "product_cards" not in [name for name, _data in events]
    assert "retrieval_evidence" not in [name for name, _data in events]
