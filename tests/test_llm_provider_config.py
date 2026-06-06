import json
import pytest

from rag.recommendation import recommendation_pipeline
from rag.recommendation.llm_client import (
    LLMClientError,
    LLMCallReport,
    OpenAICompatibleChatClient,
    build_llm_provider_config,
    is_llm_configured,
)
from scripts.check_llm_provider import main as check_llm_provider_main


LLM_ENV_KEYS = [
    "MALLMIND_LLM_PROVIDER",
    "MALLMIND_LLM_BASE_URL",
    "MALLMIND_LLM_API_KEY",
    "MALLMIND_LLM_MODEL",
    "MALLMIND_LLM_FAST_MODEL",
    "MALLMIND_LLM_TIMEOUT_SECONDS",
    "MALLMIND_ROUTER_MODEL",
    "MALLMIND_PARSE_MODEL",
    "MALLMIND_GUIDANCE_MODEL",
    "ARK_API_KEY",
    "ARK_BASE_URL",
    "BASE_URL",
    "MODEL",
    "FAST_MODEL",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL",
    "MIMO_API_KEY",
    "MIMO_BASE_URL",
    "MIMO_MODEL",
    "OPENAI_API_KEY",
    "API_KEY",
    "LLM_API_KEY",
    "OPENAI_BASE_URL",
    "LLM_BASE_URL",
    "LLM_MODEL",
]


@pytest.fixture(autouse=True)
def clean_llm_env(monkeypatch):
    for key in LLM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_no_key_does_not_configure_client():
    config = build_llm_provider_config()
    client = OpenAICompatibleChatClient(config)

    assert config.configured is False
    assert "api_key" in config.config_error_code
    assert client.configured is False
    assert is_llm_configured() is False


def test_ark_uses_legacy_environment(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "ark-secret")
    monkeypatch.setenv("BASE_URL", "https://ark.example.test/api/v3")
    monkeypatch.setenv("MODEL", "ark-model")
    monkeypatch.setenv("FAST_MODEL", "ark-fast")

    config = build_llm_provider_config()

    assert config.configured is True
    assert config.provider == "ark"
    assert config.base_url == "https://ark.example.test/api/v3"
    assert config.model == "ark-model"
    assert config.fast_model == "ark-fast"


def test_deepseek_prefers_provider_key_then_unified(monkeypatch):
    monkeypatch.setenv("MALLMIND_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("MALLMIND_LLM_MODEL", "deepseek-model")
    monkeypatch.setenv("MALLMIND_LLM_API_KEY", "unified-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret")

    config = build_llm_provider_config()

    assert config.configured is True
    assert config.provider == "deepseek"
    assert config.base_url == "https://api.deepseek.com"
    assert config.api_key == "deepseek-secret"


def test_deepseek_uses_unified_key_when_provider_key_missing(monkeypatch):
    monkeypatch.setenv("MALLMIND_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("MALLMIND_LLM_MODEL", "deepseek-model")
    monkeypatch.setenv("MALLMIND_LLM_API_KEY", "unified-secret")

    config = build_llm_provider_config()

    assert config.configured is True
    assert config.api_key == "unified-secret"


def test_mimo_requires_external_base_and_model(monkeypatch):
    monkeypatch.setenv("MALLMIND_LLM_PROVIDER", "mimo")
    monkeypatch.setenv("MIMO_API_KEY", "mimo-secret")

    config = build_llm_provider_config()

    assert config.configured is False
    assert "base_url" in config.config_error_code
    assert "model" in config.config_error_code


def test_mimo_reads_provider_specific_or_unified_values(monkeypatch):
    monkeypatch.setenv("MALLMIND_LLM_PROVIDER", "mimo")
    monkeypatch.setenv("MALLMIND_LLM_API_KEY", "unified-secret")
    monkeypatch.setenv("MIMO_BASE_URL", "https://mimo.example.test/v1")
    monkeypatch.setenv("MIMO_MODEL", "mimo-model")

    config = build_llm_provider_config()

    assert config.configured is True
    assert config.provider == "mimo"
    assert config.api_key == "unified-secret"
    assert config.base_url == "https://mimo.example.test/v1"
    assert config.model == "mimo-model"


def test_openai_compatible_requires_explicit_base_model_and_key(monkeypatch):
    monkeypatch.setenv("MALLMIND_LLM_PROVIDER", "openai_compatible")

    config = build_llm_provider_config()

    assert config.configured is False
    assert "base_url" in config.config_error_code
    assert "api_key" in config.config_error_code
    assert "model" in config.config_error_code


def test_openai_compatible_configures_when_explicit(monkeypatch):
    monkeypatch.setenv("MALLMIND_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("MALLMIND_LLM_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("MALLMIND_LLM_API_KEY", "secret")
    monkeypatch.setenv("MALLMIND_LLM_MODEL", "model")

    assert build_llm_provider_config().configured is True


def test_llm_non_json_parse_falls_back_to_rules(monkeypatch):
    class NonJsonClient:
        configured = True
        config = type("Config", (), {"fast_model": "fast"})()

        def chat_json_with_report(self, *args, **kwargs):
            raise json.JSONDecodeError("bad", "not-json", 0)

    monkeypatch.setenv("RECOMMENDATION_LLM_PARSE", "always")
    monkeypatch.setattr(recommendation_pipeline, "OpenAICompatibleChatClient", NonJsonClient)

    requirement = recommendation_pipeline.parse_requirement("shopping product for student", use_llm=True)

    assert requirement.raw_query
    assert requirement.assumptions


def test_llm_timeout_falls_back_to_rule_guidance(monkeypatch):
    report = LLMCallReport(configured=True, provider="ark", error="timeout")

    class TimeoutClient:
        configured = True
        config = type("Config", (), {"model": "model"})()

        def chat_json_with_report(self, *args, **kwargs):
            raise LLMClientError("timeout", report)

    result = recommendation_pipeline.recommend_shopping_products("shopping product", use_llm=False)
    monkeypatch.setenv("RECOMMENDATION_LLM_GUIDANCE", "true")
    monkeypatch.setattr(recommendation_pipeline, "OpenAICompatibleChatClient", TimeoutClient)

    enriched = recommendation_pipeline.enrich_recommendation_result(result, use_llm=True)

    assert enriched.trace["llm_guidance"] == "fallback"
    assert "llm_error_sanitized" in enriched.trace


def test_client_error_sanitizes_endpoint_and_key(monkeypatch):
    monkeypatch.setenv("MALLMIND_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("MALLMIND_LLM_BASE_URL", "https://secret.example.test/v1")
    monkeypatch.setenv("MALLMIND_LLM_API_KEY", "sk-secret-token")
    monkeypatch.setenv("MALLMIND_LLM_MODEL", "model")

    def fail_urlopen(*args, **kwargs):
        raise OSError("https://secret.example.test/v1 sk-secret-token boom")

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)
    client = OpenAICompatibleChatClient()

    with pytest.raises(LLMClientError) as exc_info:
        client.chat_text([{"role": "user", "content": "hi"}])

    text = str(exc_info.value)
    assert "sk-secret-token" not in text
    assert "secret.example.test" not in text


def test_check_script_does_not_leak_key(monkeypatch, capsys):
    monkeypatch.setenv("MALLMIND_LLM_API_KEY", "sk-script-secret")

    code = check_llm_provider_main(["--provider", "openai_compatible"])
    output = capsys.readouterr().out

    assert code == 1
    assert "sk-script-secret" not in output
