from pathlib import Path
from types import SimpleNamespace
import importlib
import sys
import types

from fastapi.testclient import TestClient

import rag.api.products as product_routes
import rag.utils.retrieval_postprocess as retrieval_postprocess
from rag.api.recommendation_app import app
from rag.recommendation.llm_client import LLMCallReport, report_to_dict
from rag.utils.runtime_errors import sanitize_result_for_response, sanitize_text


client = TestClient(app)


def test_sanitize_text_redacts_obvious_secrets_and_paths():
    raw = (
        "Bearer sk-demo-secret D:\\github\\tripmind\\trad_rag\\data\\x.json "
        "https://llm.example.com/v1 redis://:pass@localhost:6379/0"
    )

    cleaned = sanitize_text(raw)

    assert "sk-demo-secret" not in cleaned
    assert "D:\\github" not in cleaned
    assert "llm.example.com" not in cleaned
    assert "localhost:6379" not in cleaned


def test_llm_report_public_dict_hides_model_url_and_preview(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    report = LLMCallReport(
        configured=True,
        success=False,
        url="https://llm.example.com/v1/chat/completions",
        model="secret-model-id",
        status_code=500,
        elapsed_ms=12,
        error="provider raw error",
        usage={"prompt_tokens": 1},
        response_preview="raw model text",
    )

    payload = report_to_dict(report)

    assert payload == {
        "configured": True,
        "success": False,
        "status_code": 500,
        "elapsed_ms": 12,
        "has_error": True,
    }


def test_llm_report_debug_dict_keeps_diagnostics(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    report = LLMCallReport(
        configured=True,
        success=False,
        url="https://llm.example.com/v1/chat/completions",
        model="debug-model-id",
        status_code=500,
        elapsed_ms=12,
        error="provider raw error",
        usage={"prompt_tokens": 1},
        response_preview="raw model text",
    )

    payload = report_to_dict(report)

    assert payload["url"] == "https://llm.example.com/v1/chat/completions"
    assert payload["model"] == "debug-model-id"
    assert payload["error"] == "provider raw error"
    assert payload["response_preview"] == "raw model text"


def test_result_trace_is_public_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    payload = {
        "trace": {
            "runtime_mode": "fast",
            "llm_guidance_call": {"url": "https://llm.example.com", "model": "secret-model"},
            "milvus_retrieval": {
                "status": "failed",
                "query_variants": [{"query_preview": "private query"}],
                "postprocess": [{"rerank_endpoint": "https://rerank.example.com"}],
            },
            "attachments": [{"extracted_text": "debug OCR"}],
        }
    }

    result = sanitize_result_for_response(payload)

    assert result["trace"]["runtime_mode"] == "fast"
    assert result["trace"]["milvus_status"] == "failed"
    assert "llm_guidance_call" not in result["trace"]
    assert "attachments" not in result["trace"]
    assert "query_variants" not in str(result)


def test_product_write_api_disabled_by_default_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("ENABLE_PRODUCT_ADMIN_API", raising=False)

    response = client.post("/api/products", json={"product": {}})

    assert response.status_code == 403


def test_product_write_api_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ENABLE_PRODUCT_ADMIN_API", "true")
    monkeypatch.setenv("ADMIN_TOKEN", "token-1")

    response = client.post("/api/products", json={"product": {}}, headers={"X-Admin-Token": "wrong"})

    assert response.status_code == 403


def test_health_hides_paths_and_model_names_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    payload = client.get("/api/health").json()

    assert "app_file" not in payload
    assert "vision_model" not in payload
    assert "llm_configured" in payload


def test_root_health_is_public(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")

    payload = client.get("/health").json()

    assert payload["status"] == "ok"
    assert "timestamp" in payload


def test_runtime_diagnostics_requires_token_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ADMIN_TOKEN", "token-1")

    denied = client.get("/api/runtime/diagnostics")
    allowed = client.get("/api/runtime/diagnostics", headers={"X-Admin-Token": "token-1"})

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert "ARK_API_KEY" not in str(allowed.json())


def test_product_write_api_allows_enabled_token(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ENABLE_PRODUCT_ADMIN_API", "true")
    monkeypatch.setenv("ADMIN_TOKEN", "token-1")
    sample = client.get("/api/products").json()["products"][0]

    catalog = SimpleNamespace(
        products=[sample],
        require=lambda _product_id: product_routes.parse_model_payload(product_routes.ApiProduct, sample),
    )
    monkeypatch.setattr(product_routes, "upsert_product", lambda _product: catalog)

    response = client.post(
        "/api/products",
        json={"product": sample},
        headers={"X-Admin-Token": "token-1"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "saved"


def test_redis_delete_pattern_uses_scan_iter():
    source = Path("rag/storage/cache.py").read_text(encoding="utf-8")

    assert ".scan_iter(" in source
    assert ".keys(" not in source


def test_rerank_meta_does_not_include_provider_response_text():
    for path in ("rag/utils/retrieval_postprocess.py", "rag/utils/rag_utils.py"):
        source = Path(path).read_text(encoding="utf-8")
        assert "http_status_" in source


def test_rerank_failure_returns_candidates_without_endpoint_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(retrieval_postprocess, "RERANK_MODEL", "reranker")
    monkeypatch.setattr(retrieval_postprocess, "RERANK_API_KEY", "secret")
    monkeypatch.setattr(retrieval_postprocess, "RERANK_BINDING_HOST", "https://rerank.example.com")

    def fail_post(*args, **kwargs):
        raise RuntimeError("provider endpoint exploded")

    monkeypatch.setattr(retrieval_postprocess.requests, "post", fail_post)

    docs, meta = retrieval_postprocess._rerank_documents("query", [{"text": "doc"}], top_k=1)

    assert docs
    assert meta["rerank_error"] == "rerank_failed"
    assert "rerank_endpoint" not in meta
    assert "provider endpoint exploded" not in str(meta)


def test_redis_cache_degrades_without_raising(monkeypatch):
    class BrokenClient:
        def get(self, key):
            raise RuntimeError("redis://secret-host:6379 unavailable")

        def setex(self, *args, **kwargs):
            raise RuntimeError("set failed")

        def delete(self, *args, **kwargs):
            raise RuntimeError("delete failed")

        def scan_iter(self, *args, **kwargs):
            raise RuntimeError("scan failed")

    fake_redis = types.SimpleNamespace(Redis=types.SimpleNamespace(from_url=lambda *args, **kwargs: BrokenClient()))
    monkeypatch.setitem(sys.modules, "redis", fake_redis)
    cache_module = importlib.reload(importlib.import_module("rag.storage.cache"))
    cache = cache_module.RedisCache()

    assert cache.get_json("x") is None
    cache.set_json("x", {"ok": True})
    cache.delete("x")
    cache.delete_pattern("x:*")


def test_legacy_chat_demo_branch_disabled_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("ENABLE_LEGACY_CHAT_COMPAT", raising=False)

    response = client.post("/api/chat", json={"message": "推荐冰箱", "attachments": [], "images": []})

    assert response.status_code == 200
    assert "当前商品库暂时没有冰箱" not in response.text
