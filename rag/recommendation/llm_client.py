"""Configured OpenAI-compatible client and bounded-call utilities.

``OpenAICompatibleChatClient`` is shared by SemanticParse, general chat, and
the dormant multimodal observer. It owns provider/model/env configuration, JSON
response parsing, timing reports, and hard timeouts; it has no V3 action,
catalog-fact, or business-state authority.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from rag.utils.runtime_errors import is_debug_mode, sanitize_report, sanitize_text

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency in lightweight installs
    load_dotenv = None


if load_dotenv:
    load_dotenv()


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMProviderConfig:
    """OpenAI-compatible chat provider config resolved from environment."""
    provider: str
    base_url: str
    api_key: str
    model: str
    fast_model: str
    timeout_seconds: int = 30
    supports_json_mode: bool = False
    supports_tool_calls: bool = False
    extra_headers: Dict[str, str] = field(default_factory=dict)
    configured: bool = True
    config_error_code: str = ""
    config_error_reason: str = ""


LLMConfig = LLMProviderConfig


@dataclass
class LLMCallReport:
    """大模型调用诊断报告，用来记录耗时、状态码和脱敏错误信息。"""
    configured: bool
    provider: str = ""
    success: bool = False
    url: str = ""
    model: str = ""
    status_code: Optional[int] = None
    elapsed_ms: int = 0
    error: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)
    response_preview: str = ""
    config_error_code: str = ""

    def to_public_dict(self) -> Dict[str, Any]:
        payload = {
            "configured": self.configured,
            "success": self.success,
            "status_code": self.status_code,
            "elapsed_ms": self.elapsed_ms,
            "has_error": bool(self.error),
        }
        if self.provider:
            payload["provider"] = self.provider
        if self.config_error_code:
            payload["config_error_code"] = self.config_error_code
        return payload

    def to_debug_dict(self) -> Dict[str, Any]:
        return {
            "configured": self.configured,
            "provider": self.provider,
            "success": self.success,
            "url": self.url,
            "model": self.model,
            "status_code": self.status_code,
            "elapsed_ms": self.elapsed_ms,
            "error": sanitize_text(self.error),
            "usage": self.usage,
            "response_preview": sanitize_text(self.response_preview),
            "config_error_code": self.config_error_code,
        }


class LLMClientError(RuntimeError):
    """Raised when the configured generation model cannot complete a request."""

    def __init__(self, message: str, report: Optional[LLMCallReport] = None):
        super().__init__(message)
        self.report = report


def build_llm_provider_config(provider: Optional[str] = None) -> LLMProviderConfig:
    """Resolve provider settings without exposing secrets."""

    requested = (provider or _clean_env("MALLMIND_LLM_PROVIDER")).strip().lower()
    if not requested:
        requested = "ark" if _clean_env("ARK_API_KEY") else "openai_compatible"
    if requested not in {"ark", "deepseek", "mimo", "openai_compatible"}:
        requested = "openai_compatible"

    timeout = _clean_int_env("MALLMIND_LLM_TIMEOUT_SECONDS", default=_clean_int_env("LLM_TIMEOUT_SECONDS", default=30))
    base_url = _provider_base_url(requested)
    api_key = _provider_api_key(requested)
    model = _provider_model(requested)
    fast_model = _provider_fast_model(requested, model)
    missing = []
    if not base_url:
        missing.append("base_url")
    if not api_key:
        missing.append("api_key")
    if not model:
        missing.append("model")
    configured = not missing
    error_code = "missing_" + "_".join(missing) if missing else ""
    reason = f"LLM provider '{requested}' missing required setting(s): {', '.join(missing)}." if missing else ""
    return LLMProviderConfig(
        provider=requested,
        base_url=base_url,
        api_key=api_key,
        model=model,
        fast_model=fast_model or model,
        timeout_seconds=timeout,
        supports_json_mode=requested in {"ark", "deepseek", "openai_compatible"},
        supports_tool_calls=requested in {"ark", "deepseek", "openai_compatible"},
        configured=configured,
        config_error_code=error_code,
        config_error_reason=reason,
    )


def get_llm_config() -> Optional[LLMConfig]:
    """Read a configured OpenAI-compatible generation model config from .env."""

    config = build_llm_provider_config()
    return config if config.configured else None


def get_llm_provider_trace() -> Dict[str, Any]:
    """Return a public, secret-free provider trace."""

    config = build_llm_provider_config()
    base_url_host = ""
    if config.base_url:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(config.base_url)
            base_url_host = parsed.hostname or ""
        except Exception:
            base_url_host = ""
    return {
        "llm_provider": config.provider,
        "llm_model": config.model,
        "llm_base_url_host": base_url_host,
        "router_model": _role_model("MALLMIND_ROUTER_MODEL", config.fast_model or config.model),
        "parse_model": _role_model("MALLMIND_PARSE_MODEL", config.fast_model or config.model),
        "guidance_model": _role_model("MALLMIND_GUIDANCE_MODEL", config.model),
        "llm_configured": config.configured,
        "llm_config_error_code": config.config_error_code,
        "llm_provider_fallback": "" if config.configured else "local_rule",
        "llm_error_sanitized": config.config_error_reason,
    }


def is_llm_configured() -> bool:
    """大模型客户端：判断是否满足 llm configured 条件。"""
    return get_llm_config() is not None


class OpenAICompatibleChatClient:
    """Minimal chat-completions client for OpenAI-compatible providers."""

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config if config is not None else build_llm_provider_config()

    @property
    def configured(self) -> bool:
        return bool(self.config and self.config.configured)

    def chat_text(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 1200,
    ) -> str:
        data, _report = self.chat_completion(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return _extract_message_content(data)

    def chat_text_with_report(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 1200,
    ) -> Tuple[str, LLMCallReport]:
        """Return plain assistant text together with the provider call report."""

        data, report = self.chat_completion(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return _extract_message_content(data), report

    def chat_json(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 1600,
    ) -> Dict[str, Any]:
        text = self.chat_text(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return extract_json_object(text)

    def chat_json_with_report(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 1600,
    ) -> Tuple[Dict[str, Any], LLMCallReport]:
        data, report = self.chat_completion(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = _extract_message_content(data)
        return extract_json_object(text), report

    def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_tokens: int = 1200,
    ) -> Tuple[Dict[str, Any], LLMCallReport]:
        if not self.config or not self.config.configured:
            config = self.config or build_llm_provider_config()
            report = LLMCallReport(
                configured=False,
                provider=config.provider,
                model=config.model,
                error=config.config_error_reason or "Generation model is not configured.",
                config_error_code=config.config_error_code or "not_configured",
            )
            raise LLMClientError(report.error, report)

        request_model = model or self.config.model
        payload = {
            "model": request_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "thinking": {"type": "disabled"},
        }
        url = self._chat_completions_url()
        report = LLMCallReport(
            configured=True,
            provider=self.config.provider,
            url=url,
            model=request_model,
        )
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        headers.update(self.config.extra_headers or {})
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        started_at = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                report.status_code = response.status
        except urllib.error.HTTPError as exc:
            report.elapsed_ms = _elapsed_ms(started_at)
            report.status_code = exc.code
            detail = exc.read().decode("utf-8", errors="ignore")[:1000]
            report.error = sanitize_text(f"Generation API returned HTTP {exc.code}: {detail}")
            logger.warning("Generation API HTTP error: %s", sanitize_text(report.error))
            raise LLMClientError(report.error, report) from exc
        except urllib.error.URLError as exc:
            report.elapsed_ms = _elapsed_ms(started_at)
            report.error = sanitize_text(f"Generation API request failed: {exc.reason if hasattr(exc, 'reason') else exc}")
            logger.warning("Generation API request failed: %s", sanitize_text(report.error))
            raise LLMClientError(report.error, report) from exc
        except (TimeoutError, socket.timeout, OSError) as exc:
            report.elapsed_ms = _elapsed_ms(started_at)
            report.error = sanitize_text(f"Generation API request timed out or was interrupted: {type(exc).__name__}")
            logger.warning("Generation API timeout/interruption: %s", sanitize_text(report.error))
            raise LLMClientError(report.error, report) from exc

        report.elapsed_ms = _elapsed_ms(started_at)
        try:
            data = json.loads(raw)
            content = _extract_message_content(data)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            report.error = "Generation API returned an unexpected response shape."
            report.response_preview = raw[:500]
            logger.warning("Generation API unexpected response shape: %s", sanitize_text(str(exc)))
            raise LLMClientError(report.error, report) from exc

        report.success = True
        report.usage = data.get("usage") or {}
        report.response_preview = content[:200]
        return data, report
    def diagnose(
        self,
        *,
        model: Optional[str] = None,
        prompt: str = "只回复 OK",
    ) -> LLMCallReport:
        if not self.config or not self.config.configured:
            config = self.config or build_llm_provider_config()
            return LLMCallReport(
                configured=False,
                provider=config.provider,
                model=config.model,
                error=config.config_error_reason or "Generation model is not configured.",
                config_error_code=config.config_error_code or "not_configured",
            )
        try:
            _data, report = self.chat_completion(
                [{"role": "user", "content": prompt}],
                model=model or self.config.model,
                temperature=0,
                max_tokens=8,
            )
            return report
        except LLMClientError as exc:
            return exc.report or LLMCallReport(
                configured=True,
                provider=self.config.provider,
                url=self._chat_completions_url(),
                model=model or self.config.model,
                error=sanitize_text(str(exc)),
            )

    def _chat_completions_url(self) -> str:
        assert self.config is not None
        base_url = self.config.base_url.rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        return f"{base_url}/chat/completions"


def extract_json_object(text: str) -> Dict[str, Any]:
    """Extract the first JSON object from an LLM response."""
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    elif not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]

    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise LLMClientError("Generation model did not return a JSON object.")
    return data


def report_to_dict(report: LLMCallReport) -> Dict[str, Any]:
    if is_debug_mode():
        return report.to_debug_dict()
    return sanitize_report(report.to_public_dict())


def run_with_hard_timeout(callback: Callable[[], Any], timeout_seconds: float, label: str) -> Any:
    """Run an LLM callback with a request-level deadline and fallback on timeout."""

    result_queue: "queue.Queue[Tuple[bool, Any]]" = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            result_queue.put((True, callback()))
        except Exception as exc:  # pragma: no cover - pass through worker exceptions
            result_queue.put((False, exc))

    thread = threading.Thread(target=worker, name=f"llm-{label}-timeout", daemon=True)
    thread.start()
    try:
        ok, value = result_queue.get(timeout=max(float(timeout_seconds), 0.1))
    except queue.Empty as exc:
        report = LLMCallReport(
            configured=True,
            success=False,
            elapsed_ms=int(float(timeout_seconds) * 1000),
            error=f"{label} LLM call exceeded hard timeout of {timeout_seconds:.1f}s.",
        )
        raise LLMClientError(report.error, report) from exc
    if ok:
        return value
    if isinstance(value, Exception):
        raise value
    raise LLMClientError(f"{label} LLM call failed.")


def _clean_env(*names: str, default: str = "") -> str:
    """Return the first non-empty environment value after trimming quotes and comments."""
    for name in names:
        value = os.getenv(name)
        if value is None:
            continue
        cleaned = value.strip().strip("\"'")
        if not cleaned or cleaned.startswith("#"):
            continue
        return cleaned
    return default


def _provider_base_url(provider: str) -> str:
    if provider == "ark":
        return _clean_env("MALLMIND_LLM_BASE_URL", "ARK_BASE_URL", "BASE_URL", "OPENAI_BASE_URL", "LLM_BASE_URL", default="https://ark.cn-beijing.volces.com/api/v3")
    if provider == "deepseek":
        return _clean_env("MALLMIND_LLM_BASE_URL", "DEEPSEEK_BASE_URL", default="https://api.deepseek.com")
    if provider == "mimo":
        return _clean_env("MALLMIND_LLM_BASE_URL", "MIMO_BASE_URL")
    return _clean_env("MALLMIND_LLM_BASE_URL", "OPENAI_BASE_URL", "LLM_BASE_URL")


def _provider_api_key(provider: str) -> str:
    if provider == "ark":
        return _clean_env("MALLMIND_LLM_API_KEY", "ARK_API_KEY", "OPENAI_API_KEY", "API_KEY", "LLM_API_KEY")
    if provider == "deepseek":
        return _clean_env("DEEPSEEK_API_KEY", "MALLMIND_LLM_API_KEY")
    if provider == "mimo":
        return _clean_env("MIMO_API_KEY", "MALLMIND_LLM_API_KEY")
    return _clean_env("MALLMIND_LLM_API_KEY", "OPENAI_API_KEY", "API_KEY", "LLM_API_KEY")


def _provider_model(provider: str) -> str:
    if provider == "ark":
        return _clean_env("MALLMIND_LLM_MODEL", "ARK_MODEL", "MODEL", "LLM_MODEL")
    if provider == "deepseek":
        return _clean_env("MALLMIND_LLM_MODEL", "DEEPSEEK_MODEL", "MODEL", "LLM_MODEL")
    if provider == "mimo":
        return _clean_env("MALLMIND_LLM_MODEL", "MIMO_MODEL")
    return _clean_env("MALLMIND_LLM_MODEL", "LLM_MODEL")


def _provider_fast_model(provider: str, model: str) -> str:
    if provider == "ark":
        return _clean_env("MALLMIND_LLM_FAST_MODEL", "ARK_FAST_MODEL", "FAST_MODEL", "GRADE_MODEL", "MODEL", "LLM_FAST_MODEL", default=model)
    if provider == "deepseek":
        return _clean_env("MALLMIND_LLM_FAST_MODEL", "DEEPSEEK_FAST_MODEL", "FAST_MODEL", "LLM_FAST_MODEL", default=model)
    if provider == "mimo":
        return _clean_env("MALLMIND_LLM_FAST_MODEL", "MIMO_FAST_MODEL", default=model)
    return _clean_env("MALLMIND_LLM_FAST_MODEL", "LLM_FAST_MODEL", default=model)


def _role_model(name: str, default: str) -> str:
    return _clean_env(name, default=default)


def _clean_int_env(name: str, *, default: int) -> int:
    """Read an integer environment variable with fallback."""
    value = _clean_env(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _extract_message_content(data: Dict[str, Any]) -> str:
    """Normalize chat-completions message content into plain text."""
    message = data["choices"][0]["message"]
    content = message.get("content")
    if content is None:
        reasoning = message.get("reasoning")
        if reasoning and isinstance(reasoning, str):
            return reasoning.strip()
        text_val = data["choices"][0].get("text")
        if text_val and isinstance(text_val, str):
            return text_val.strip()
        raise KeyError("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(parts).strip()
    return str(content or "").strip()


def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)
