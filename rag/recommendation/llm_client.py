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
class LLMConfig:
    """大模型连接配置，集中保存 base_url、model、api_key 等环境变量结果。"""
    api_key: str
    base_url: str
    model: str
    fast_model: str
    timeout_seconds: int = 60


@dataclass
class LLMCallReport:
    """大模型调用诊断报告，用来记录耗时、状态码和脱敏错误信息。"""
    configured: bool
    success: bool = False
    url: str = ""
    model: str = ""
    status_code: Optional[int] = None
    elapsed_ms: int = 0
    error: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)
    response_preview: str = ""

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "configured": self.configured,
            "success": self.success,
            "status_code": self.status_code,
            "elapsed_ms": self.elapsed_ms,
            "has_error": bool(self.error),
        }

    def to_debug_dict(self) -> Dict[str, Any]:
        return {
            "configured": self.configured,
            "success": self.success,
            "url": self.url,
            "model": self.model,
            "status_code": self.status_code,
            "elapsed_ms": self.elapsed_ms,
            "error": sanitize_text(self.error),
            "usage": self.usage,
            "response_preview": sanitize_text(self.response_preview),
        }


class LLMClientError(RuntimeError):
    """Raised when the configured generation model cannot complete a request."""

    def __init__(self, message: str, report: Optional[LLMCallReport] = None):
        super().__init__(message)
        self.report = report


def get_llm_config() -> Optional[LLMConfig]:
    """Read an OpenAI-compatible generation model config from .env."""

    api_key = _clean_env(
        "ARK_API_KEY",
        "OPENAI_API_KEY",
        "API_KEY",
        "LLM_API_KEY",
    )
    base_url = _clean_env(
        "BASE_URL",
        "OPENAI_BASE_URL",
        "LLM_BASE_URL",
        default="https://api.openai.com/v1",
    )
    model = _clean_env("MODEL", "LLM_MODEL")
    fast_model = _clean_env("FAST_MODEL", "GRADE_MODEL", "MODEL", "LLM_FAST_MODEL")
    timeout = _clean_int_env("LLM_TIMEOUT_SECONDS", default=60)

    if not api_key or not model:
        return None
    return LLMConfig(
        api_key=api_key,
        base_url=base_url or "https://api.openai.com/v1",
        model=model,
        fast_model=fast_model or model,
        timeout_seconds=timeout,
    )


def is_llm_configured() -> bool:
    """大模型客户端：判断是否满足 llm configured 条件。"""
    return get_llm_config() is not None


class OpenAICompatibleChatClient:
    """Minimal chat-completions client for Ark, DashScope, OpenAI, and compatible APIs."""

    def __init__(self, config: Optional[LLMConfig] = None):
        self.config = config or get_llm_config()

    @property
    def configured(self) -> bool:
        return self.config is not None

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
        if not self.config:
            report = LLMCallReport(configured=False, error="Generation model is not configured.")
            raise LLMClientError(report.error, report)

        request_model = model or self.config.model
        payload = {
            "model": request_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        url = self._chat_completions_url()
        report = LLMCallReport(
            configured=True,
            url=url,
            model=request_model,
        )
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
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
            report.error = f"Generation API returned HTTP {exc.code}: {detail}"
            logger.warning("Generation API HTTP error: %s", sanitize_text(report.error))
            raise LLMClientError(report.error, report) from exc
        except urllib.error.URLError as exc:
            report.elapsed_ms = _elapsed_ms(started_at)
            report.error = f"Generation API request failed: {exc}"
            logger.warning("Generation API request failed: %s", sanitize_text(report.error))
            raise LLMClientError(report.error, report) from exc
        except (TimeoutError, socket.timeout, OSError) as exc:
            report.elapsed_ms = _elapsed_ms(started_at)
            report.error = f"Generation API request timed out or was interrupted: {exc}"
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
        if not self.config:
            return LLMCallReport(configured=False, error="Generation model is not configured.")
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
                url=self._chat_completions_url(),
                model=model or self.config.model,
                error=str(exc),
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
    content = data["choices"][0]["message"]["content"]
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
