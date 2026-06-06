from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Sequence

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rag.recommendation.llm_client import (  # noqa: E402
    LLMClientError,
    OpenAICompatibleChatClient,
    build_llm_provider_config,
    extract_json_object,
    report_to_dict,
)
from rag.recommendation.tool_router import RoutedToolCall  # noqa: E402
from rag.utils.runtime_errors import sanitize_text  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check an OpenAI-compatible LLM provider without leaking secrets.")
    parser.add_argument("--provider", choices=("ark", "deepseek", "mimo", "openai_compatible"), default=None)
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--fast-model")
    parser.add_argument("--timeout-seconds", type=int)
    args = parser.parse_args(argv)

    apply_overrides(args)
    config = build_llm_provider_config(args.provider)
    client = OpenAICompatibleChatClient(config)
    result: Dict[str, Any] = {
        "provider": config.provider,
        "configured": config.configured,
        "config_error_code": config.config_error_code,
        "base_url": _public_base_url(config.base_url),
        "model": config.model,
        "fast_model": config.fast_model,
        "timeout_seconds": config.timeout_seconds,
        "checks": {},
    }
    if not config.configured:
        result["fallback_reason"] = config.config_error_reason
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    result["checks"]["chat"] = check_chat(client)
    result["checks"]["json_output"] = check_json(client)
    result["checks"]["router_schema"] = check_router_schema(client)
    ok = all(item.get("success") for item in result["checks"].values())
    print(json.dumps(_truncate(result), ensure_ascii=False, indent=2))
    return 0 if ok else 1


def apply_overrides(args: argparse.Namespace) -> None:
    if args.provider:
        os.environ["MALLMIND_LLM_PROVIDER"] = args.provider
    if args.base_url:
        os.environ["MALLMIND_LLM_BASE_URL"] = args.base_url
    if args.model:
        os.environ["MALLMIND_LLM_MODEL"] = args.model
    if args.fast_model:
        os.environ["MALLMIND_LLM_FAST_MODEL"] = args.fast_model
    if args.timeout_seconds:
        os.environ["MALLMIND_LLM_TIMEOUT_SECONDS"] = str(args.timeout_seconds)


def check_chat(client: OpenAICompatibleChatClient) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        text = client.chat_text([{"role": "user", "content": "Reply with OK only."}], temperature=0, max_tokens=16)
        return {"success": True, "latency_ms": elapsed_ms(started), "preview": sanitize_text(text)[:40]}
    except LLMClientError as exc:
        return check_error(exc, started)


def check_json(client: OpenAICompatibleChatClient) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        payload = client.chat_json(
            [
                {"role": "system", "content": "Only output strict JSON text."},
                {"role": "user", "content": 'Return {"ok": true, "provider": "check"} as JSON.'},
            ],
            model=client.config.fast_model if client.config else None,
            temperature=0,
            max_tokens=80,
        )
        return {"success": bool(payload.get("ok") is True), "latency_ms": elapsed_ms(started), "keys": sorted(payload.keys())[:8]}
    except (LLMClientError, json.JSONDecodeError, ValueError, TypeError) as exc:
        return check_error(exc, started)


def check_router_schema(client: OpenAICompatibleChatClient) -> Dict[str, Any]:
    started = time.perf_counter()
    prompt = (
        "Only output JSON for this schema: "
        '{"name":"recommend_shopping_products","arguments":{"query":"recommend a phone","catalog_scope":"ecommerce"},'
        '"confidence":0.9,"reason":"shopping request","source":"llm"}'
    )
    try:
        text = client.chat_text(
            [{"role": "system", "content": "Only output strict JSON text."}, {"role": "user", "content": prompt}],
            model=os.getenv("MALLMIND_ROUTER_MODEL") or (client.config.fast_model if client.config else None),
            temperature=0,
            max_tokens=180,
        )
        payload = extract_json_object(text)
        parsed = RoutedToolCall.model_validate(payload)
        return {"success": True, "latency_ms": elapsed_ms(started), "name": parsed.name}
    except (LLMClientError, json.JSONDecodeError, ValueError, TypeError) as exc:
        return check_error(exc, started)


def check_error(exc: Exception, started: float) -> Dict[str, Any]:
    report = getattr(exc, "report", None)
    payload = report_to_dict(report) if report is not None else {}
    payload.update({
        "success": False,
        "latency_ms": elapsed_ms(started),
        "error": sanitize_text(str(exc))[:240],
        "fallback_reason": "provider_error_or_timeout",
    })
    return payload


def elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _public_base_url(value: str) -> str:
    return value.rstrip("/") if value else ""


def _truncate(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _truncate(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_truncate(item) for item in value[:20]]
    if isinstance(value, str):
        return sanitize_text(value)[:240]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
