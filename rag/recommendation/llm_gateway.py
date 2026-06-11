"""Unified LLM call orchestration layer.

Replaces scattered ``OpenAICompatibleChatClient()`` instantiations with a
single registry that centralises model / timeout / temperature / concurrency
configuration for every caller scenario.

Usage::

    from rag.recommendation.llm_gateway import LLMGateway

    # One-line call — picks the right model, timeout, concurrency limit automatically.
    payload, report = LLMGateway.call("router", messages)

    # Override any parameter per-call.
    payload, report = LLMGateway.call("guidance", messages, temperature=0.5)

The return format is identical to ``client.chat_json_with_report()`` so
migration can be done one call-site at a time.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from rag.recommendation.llm_client import (
    LLMCallReport,
    LLMClientError,
    OpenAICompatibleChatClient,
    report_to_dict,
    run_with_hard_timeout,
)

logger = logging.getLogger(__name__)


# ── caller config ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _CallerConfig:
    """Per-scenario LLM call configuration."""
    name: str
    model_kind: str  # "fast" or "main"
    temperature: float
    timeout: float
    max_tokens: int
    max_concurrency: int = 5


# ── simple semaphore-based concurrency limiter ────────────────────────────

class _ConcurrencyLimiter:
    """Thin wrapper around ``threading.Semaphore`` with a label for logging."""

    __slots__ = ("_sem", "_name")

    def __init__(self, name: str, max_concurrency: int):
        self._sem = threading.Semaphore(max_concurrency)
        self._name = name

    def acquire(self, timeout: float = 0) -> bool:
        return self._sem.acquire(timeout=timeout)

    def release(self) -> None:
        self._sem.release()


# ── simple circuit breaker ────────────────────────────────────────────────

@dataclass
class _CircuitState:
    """Tracks consecutive failures per caller for circuit breaking."""
    consecutive_failures: int = 0
    half_open_until: float = 0.0
    state: str = "closed"  # closed | open | half-open

    _FAILURE_THRESHOLD: int = 5
    _OPEN_DURATION_SECONDS: float = 30.0

    def record_success(self) -> None:
        if self.state == "half-open":
            self.state = "closed"
            logger.debug("Circuit breaker for %s: half-open -> closed", self._name)
        self.consecutive_failures = 0

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self._FAILURE_THRESHOLD:
            self.state = "open"
            self.half_open_until = time.time() + self._OPEN_DURATION_SECONDS
            logger.warning(
                "Circuit breaker OPEN for %s after %d consecutive failures",
                self._name, self.consecutive_failures,
            )

    def is_open(self) -> bool:
        if self.state == "open":
            if time.time() >= self.half_open_until:
                self.state = "half-open"
                logger.debug("Circuit breaker for %s: open -> half-open", self._name)
                return False
            return True
        return False


# ── the gateway ───────────────────────────────────────────────────────────

class LLMGateway:
    """Unified entry-point for all LLM calls in the recommendation stack.

    Callers register their config once (see bottom of this file for the
    default registrations).  Each ``call()`` invocation:

    1. Checks the circuit breaker.
    2. Acquires a concurrency slot.
    3. Delegates to ``OpenAICompatibleChatClient`` with the registered
       model / temperature / timeout.
    4. Records success / failure for the circuit breaker.
    5. Returns ``(payload_dict, LLMCallReport)`` — same shape as
       ``client.chat_json_with_report()``.
    """

    _configs: Dict[str, _CallerConfig] = {}
    _limiters: Dict[str, _ConcurrencyLimiter] = {}
    _circuits: Dict[str, _CircuitState] = {}
    _call_log: List[Dict[str, Any]] = []
    _log_lock = threading.Lock()
    _MAX_LOG = 100

    # ── registration ──────────────────────────────────────────────────────

    @classmethod
    def register(
        cls,
        name: str,
        *,
        model_kind: str = "fast",
        temperature: float = 0.0,
        timeout: float = 15.0,
        max_tokens: int = 1600,
        max_concurrency: int = 5,
    ) -> None:
        """Register (or re-register) a caller scenario."""
        cfg = _CallerConfig(
            name=name,
            model_kind=model_kind,
            temperature=temperature,
            timeout=timeout,
            max_tokens=max_tokens,
            max_concurrency=max_concurrency,
        )
        cls._configs[name] = cfg
        cls._limiters[name] = _ConcurrencyLimiter(name, max_concurrency)
        cls._circuits.setdefault(name, _CircuitState())

    # ── primary call interface ────────────────────────────────────────────

    @classmethod
    def call(
        cls,
        caller_name: str,
        messages: List[Dict[str, Any]],
        *,
        text_mode: bool = False,
        **overrides: Any,
    ) -> Any:
        """Execute an LLM call under the registered configuration.

        Parameters
        ----------
        caller_name : registered scenario name (e.g. ``"router"``).
        messages : OpenAI-format message list.
        text_mode : if True, return plain text via ``chat_text()``
            instead of JSON.  Default is JSON mode.
        **overrides : per-call overrides for ``temperature``, ``max_tokens``,
            ``model``, or ``timeout``.

        Returns
        -------
        ``(payload_or_text, LLMCallReport)`` — same shape as
        ``chat_json_with_report()`` (or ``(str, report)`` when *text_mode*).
        """
        cfg = cls._configs.get(caller_name)
        if cfg is None:
            # Unregistered caller — use sensible defaults
            logger.debug("LLMGateway: unregistered caller %r, using defaults", caller_name)
            cfg = _CallerConfig(
                name=caller_name,
                model_kind="fast",
                temperature=overrides.get("temperature", 0.2),
                timeout=overrides.get("timeout", 15.0),
                max_tokens=overrides.get("max_tokens", 1600),
            )
            cls._configs[caller_name] = cfg
            cls._limiters[caller_name] = _ConcurrencyLimiter(caller_name, 5)
            cls._circuits.setdefault(caller_name, _CircuitState())

        temperature = overrides.get("temperature", cfg.temperature)
        max_tokens = overrides.get("max_tokens", cfg.max_tokens)
        timeout = overrides.get("timeout", cfg.timeout)
        model_override = overrides.get("model")

        # ── circuit breaker ──
        circuit = cls._circuits[caller_name]
        if circuit.is_open():
            report = LLMCallReport(
                configured=True,
                provider="gateway",
                model="",
                error=f"Circuit breaker open for {caller_name}",
                config_error_code="circuit_open",
            )
            cls._record_log(caller_name, False, 0, "circuit_open")
            raise LLMClientError(report.error, report)

        # ── concurrency limit ──
        limiter = cls._limiters[caller_name]
        acquired = limiter.acquire(timeout=min(timeout, 2.0))
        if not acquired:
            report = LLMCallReport(
                configured=True,
                provider="gateway",
                model="",
                error=f"Concurrency limit reached for {caller_name}",
                config_error_code="concurrency_limit",
            )
            cls._record_log(caller_name, False, 0, "concurrency_limit")
            raise LLMClientError(report.error, report)

        # ── build client and call ──
        try:
            client = OpenAICompatibleChatClient()
            if not client.configured:
                report = LLMCallReport(
                    configured=False,
                    provider=client.config.provider,
                    model=client.config.model,
                    error=client.config.config_error_reason or "LLM not configured",
                    config_error_code=client.config.config_error_code or "not_configured",
                )
                cls._record_log(caller_name, False, 0, "not_configured")
                raise LLMClientError(report.error, report)

            model = model_override or _resolve_model(cfg.model_kind, client)
            label = f"gateway:{caller_name}"

            if text_mode:
                text = run_with_hard_timeout(
                    lambda: client.chat_text(
                        messages,
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    ),
                    timeout,
                    label,
                )
                report = LLMCallReport(configured=True, provider=client.config.provider, model=model, success=True)
                circuit.record_success()
                cls._record_log(caller_name, True, report.elapsed_ms, "")
                return text, report

            payload, report = run_with_hard_timeout(
                lambda: client.chat_json_with_report(
                    messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ),
                timeout,
                label,
            )
            circuit.record_success()
            cls._record_log(caller_name, True, report.elapsed_ms, "")
            return payload, report

        except LLMClientError:
            circuit.record_failure()
            cls._record_log(caller_name, False, 0, "llm_client_error")
            raise
        except (ConnectionError, PermissionError, OSError) as exc:
            circuit.record_failure()
            cls._record_log(caller_name, False, 0, type(exc).__name__)
            report = LLMCallReport(
                configured=True,
                provider="gateway",
                model="",
                error=f"{type(exc).__name__}: {exc}",
                config_error_code="network_error",
            )
            raise LLMClientError(str(exc), report) from exc
        except TimeoutError:
            circuit.record_failure()
            cls._record_log(caller_name, False, int(timeout * 1000), "timeout")
            raise
        except Exception:
            circuit.record_failure()
            cls._record_log(caller_name, False, 0, "unexpected_error")
            raise
        finally:
            if acquired:
                limiter.release()

    # ── convenience: text-only call ───────────────────────────────────────

    @classmethod
    def call_text(
        cls,
        caller_name: str,
        messages: List[Dict[str, Any]],
        **overrides: Any,
    ) -> Tuple[str, LLMCallReport]:
        """Shortcut for ``call(..., text_mode=True)``."""
        return cls.call(caller_name, messages, text_mode=True, **overrides)

    # ── observability ─────────────────────────────────────────────────────

    @classmethod
    def get_call_log(cls) -> List[Dict[str, Any]]:
        """Return a copy of the recent call log (most recent last)."""
        with cls._log_lock:
            return list(cls._call_log)

    @classmethod
    def reset(cls) -> None:
        """Reset all state — primarily for tests."""
        cls._configs.clear()
        cls._limiters.clear()
        cls._circuits.clear()
        with cls._log_lock:
            cls._call_log.clear()
        _register_defaults()

    # ── internal ──────────────────────────────────────────────────────────

    @classmethod
    def _record_log(cls, caller: str, success: bool, elapsed_ms: int, error_code: str) -> None:
        entry = {
            "caller": caller,
            "success": success,
            "elapsed_ms": elapsed_ms,
            "error_code": error_code,
            "timestamp": time.time(),
        }
        with cls._log_lock:
            cls._call_log.append(entry)
            while len(cls._call_log) > cls._MAX_LOG:
                cls._call_log.pop(0)


# ── model resolution helper ───────────────────────────────────────────────

def _resolve_model(kind: str, client: OpenAICompatibleChatClient) -> str:
    """Map 'fast' / 'main' to the actual model string from env or config."""
    if kind == "fast":
        return os.getenv("MALLMIND_ROUTER_MODEL") or client.config.fast_model
    return os.getenv("MALLMIND_GUIDANCE_MODEL") or client.config.model


# ── default registrations ─────────────────────────────────────────────────

def _register_defaults() -> None:
    """Register the standard caller scenarios."""
    LLMGateway.register("router",        model_kind="fast", temperature=0,   timeout=15, max_tokens=320,  max_concurrency=5)
    LLMGateway.register("parse",         model_kind="fast", temperature=0.1, timeout=12, max_tokens=1200, max_concurrency=5)
    LLMGateway.register("guidance",      model_kind="main", temperature=0.2, timeout=8,  max_tokens=1500, max_concurrency=5)
    LLMGateway.register("response",      model_kind="main", temperature=0.9, timeout=5,  max_tokens=200,  max_concurrency=5)
    LLMGateway.register("explanation",   model_kind="main", temperature=0.1, timeout=8,  max_tokens=1500, max_concurrency=5)
    LLMGateway.register("rewrite",       model_kind="fast", temperature=0.1, timeout=8,  max_tokens=600,  max_concurrency=5)
    LLMGateway.register("general_chat",  model_kind="main", temperature=0.7, timeout=8,  max_tokens=200,  max_concurrency=10)
    LLMGateway.register("filter",        model_kind="fast", temperature=0,   timeout=12, max_tokens=500,  max_concurrency=5)
    LLMGateway.register("attachment",    model_kind="main", temperature=0.1, timeout=15, max_tokens=800,  max_concurrency=3)


_register_defaults()
