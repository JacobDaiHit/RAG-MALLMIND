"""Offline multimodal evaluation smoke tests.

These tests use mocked VLM responses so they are stable in CI and useful as a
baseline before adding image embeddings or ASR.
"""

import base64
import json

from fastapi.testclient import TestClient

import rag.api.attachments as attachment_api
import rag.api.recommendation_app as recommendation_app
from rag.api.attachments import analyze_attachment_payloads
from rag.api.recommendation_app import app
from rag.recommendation.llm_client import LLMClientError


client = TestClient(app)


def _image_data_url(seed: bytes = b"demo-image") -> str:
    return "data:image/jpeg;base64," + base64.b64encode(seed).decode("ascii")


def _parse_sse_text(raw: str):
    events = []
    for block in raw.strip().split("\n\n"):
        if not block.strip():
            continue
        event_name = "message"
        data_str = ""
        for line in block.strip().split("\n"):
            if line.startswith("event: "):
                event_name = line[len("event: ") :]
            elif line.startswith("data: "):
                data_str = line[len("data: ") :]
        events.append((event_name, json.loads(data_str) if data_str else {}))
    return events


def _install_fake_vision(monkeypatch, payload=None, *, configured=True, error=None):
    class FakeVisionClient:
        class Config:
            model = "fake-vision-model"

        config = Config()

        @property
        def configured(self):
            return configured

        def chat_json(self, *args, **kwargs):
            if error:
                raise error
            return payload or {}

    monkeypatch.setattr(attachment_api, "OpenAICompatibleChatClient", FakeVisionClient)


def test_eval_image_success_extracts_structured_visual_fields(monkeypatch):
    _install_fake_vision(
        monkeypatch,
        {
            "summary": "图片中是一副黑色头戴式降噪耳机。",
            "extracted_text": "",
            "signals": ["image_input", "digital", "headphones"],
            "shopping_hints": ["数码电子", "黑色", "降噪耳机"],
            "visual_query_terms": ["蓝牙耳机", "黑色", "降噪", "头戴式"],
            "visual_attributes": {
                "category": "数码电子",
                "sub_category": "蓝牙耳机",
                "colors": ["黑色"],
                "features": ["降噪", "头戴式"],
                "scene": "通勤",
            },
        },
    )

    attachments = analyze_attachment_payloads(
        [{"name": "headphone.jpg", "type": "image/jpeg", "size": 12, "data_url": _image_data_url()}]
    )

    item = attachments[0]
    assert item["analysis_status"] == "success"
    assert item["visual_attributes"]["sub_category"] == "蓝牙耳机"
    assert "降噪" in item["visual_query_terms"]
    assert "黑色" in item["shopping_hints"]


def test_eval_unconfigured_vision_model_degrades_to_image_context(monkeypatch):
    _install_fake_vision(monkeypatch, configured=False)

    attachments = analyze_attachment_payloads(
        [{"name": "street.jpg", "type": "image/jpeg", "size": 12, "data_url": _image_data_url()}]
    )

    item = attachments[0]
    assert item["analysis_status"] == "skipped"
    assert item["analysis_source"] == "vision_model_unconfigured"
    assert "image" in item["input_modalities"]
    assert "vision_or_ocr_required" in item["signals"]


def test_eval_vision_error_returns_fallback_status(monkeypatch):
    _install_fake_vision(monkeypatch, error=LLMClientError("mock vision timeout"))

    attachments = analyze_attachment_payloads(
        [{"name": "broken.jpg", "type": "image/jpeg", "size": 12, "data_url": _image_data_url()}]
    )

    item = attachments[0]
    assert item["analysis_status"] == "fallback"
    assert item["analysis_source"] == "vision_model_error"
    assert "mock vision timeout" in item["summary"]
    assert "vision_or_ocr_required" in item["signals"]


def test_eval_product_screenshot_ocr_enters_chat_trace(monkeypatch):
    _install_fake_vision(
        monkeypatch,
        {
            "summary": "商品详情截图显示一款 499 元蓝牙耳机。",
            "extracted_text": "主动降噪 蓝牙耳机 到手价 499 元",
            "signals": ["image_input", "ocr", "price"],
            "shopping_hints": ["蓝牙耳机", "主动降噪", "499元"],
            "visual_query_terms": ["蓝牙耳机", "主动降噪", "499元"],
            "visual_attributes": {
                "category": "数码电子",
                "sub_category": "蓝牙耳机",
                "budget": "499元",
                "features": ["主动降噪"],
            },
        },
    )
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(recommendation_app, "STREAM_LLM_ENABLED", True)
    monkeypatch.setattr(recommendation_app, "is_llm_configured", lambda: True)

    response = client.post(
        "/api/chat/stream",
        json={
            "session_id": "eval-product-screenshot",
            "message": "按截图推荐一款类似耳机",
            "mode": "full",
            "attachments": [{"name": "sku.jpg", "type": "image/jpeg", "size": 12, "data_url": _image_data_url()}],
            "images": [],
        },
    )

    events = _parse_sse_text(response.text)
    result = [data for event, data in events if event == "result"][0]
    assert response.status_code == 200
    assert "主动降噪" in result["trace"]["attachments"][0]["extracted_text"]
    assert "蓝牙耳机" in result["requirement"]["target_sub_categories"]
    assert result["requirement"]["need_multimodal"] is True


def test_eval_street_same_style_request_uses_visual_terms(monkeypatch):
    _install_fake_vision(
        monkeypatch,
        {
            "summary": "街拍图中是一件黑色连帽卫衣，偏休闲通勤。",
            "extracted_text": "",
            "signals": ["image_input", "street_style", "clothing"],
            "shopping_hints": ["服饰运动", "卫衣", "黑色", "连帽", "同款"],
            "visual_query_terms": ["服饰运动", "卫衣", "黑色", "连帽", "通勤", "同款"],
            "visual_attributes": {
                "category": "服饰运动",
                "sub_category": "卫衣",
                "colors": ["黑色"],
                "features": ["连帽"],
                "scene": "通勤休闲",
            },
        },
    )
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(recommendation_app, "STREAM_LLM_ENABLED", True)
    monkeypatch.setattr(recommendation_app, "is_llm_configured", lambda: True)

    response = client.post(
        "/api/chat/stream",
        json={
            "session_id": "eval-street-style",
            "message": "帮我找同款，预算500以内",
            "mode": "full",
            "attachments": [{"name": "street.jpg", "type": "image/jpeg", "size": 12, "data_url": _image_data_url()}],
            "images": [],
        },
    )

    events = _parse_sse_text(response.text)
    result = [data for event, data in events if event == "result"][0]
    assert response.status_code == 200
    assert "卫衣" in result["requirement"]["target_sub_categories"]
    assert "黑色" in result["requirement"]["must_have_terms"]
    assert result["intent_route"]["route"] in {"multimodal_product_recommendation", "single_product_recommendation", "condition_filter"}


def test_eval_pc_config_screenshot_extracts_component_ocr(monkeypatch):
    _install_fake_vision(
        monkeypatch,
        {
            "summary": "配置截图包含 Ryzen 5 7500F、RTX 4060、B650 主板和 16GB 内存。",
            "extracted_text": "CPU Ryzen 5 7500F GPU RTX 4060 主板 B650 内存 16GB SSD 1TB",
            "signals": ["image_input", "pc_config", "ocr"],
            "shopping_hints": ["PC配置", "Ryzen 5 7500F", "RTX 4060", "B650", "16GB内存"],
            "visual_query_terms": ["PC配置", "CPU", "GPU", "Ryzen 5 7500F", "RTX 4060", "B650"],
            "visual_attributes": {
                "category": "PC配置",
                "sub_category": "整机配置截图",
                "model": "Ryzen 5 7500F / RTX 4060",
                "features": ["B650", "16GB内存", "1TB SSD"],
            },
        },
    )
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setattr(recommendation_app, "STREAM_LLM_ENABLED", True)
    monkeypatch.setattr(recommendation_app, "is_llm_configured", lambda: True)

    response = client.post(
        "/api/chat/stream",
        json={
            "session_id": "eval-pc-config",
            "message": "按这张PC配置截图帮我配一台电脑，预算7000以内",
            "mode": "full",
            "attachments": [{"name": "pc-config.jpg", "type": "image/jpeg", "size": 12, "data_url": _image_data_url()}],
            "images": [],
        },
    )

    events = _parse_sse_text(response.text)
    attachment_event = [data for event, data in events if event == "attachment_analysis"][0]
    route_event = [data for event, data in events if event == "intent_route"][0]
    assert response.status_code == 200
    assert "RTX 4060" in attachment_event["attachments"][0]["extracted_text"]
    assert route_event["route"] == "pc_build_plan"
