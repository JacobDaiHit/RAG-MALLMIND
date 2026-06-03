from __future__ import annotations

import logging
import os
import re
from dataclasses import is_dataclass, asdict
from typing import Any, Dict


PUBLIC_ERROR_FALLBACK = "系统暂时无法完成请求，请稍后重试。"
DEBUG_ENVS = {"dev", "development", "test", "testing", "ci"}
SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "token",
    "secret",
    "password",
    "base_url",
    "endpoint",
    "url",
    "model",
    "fast_model",
    "vision_model",
    "rerank_model",
    "rerank_endpoint",
    "response_preview",
    "connection_string",
    "redis_url",
    "postgres_url",
    "database_url",
    "milvus_uri",
    "index_path",
    "file_path",
    "path",
}
PUBLIC_TRACE_KEYS = {
    "runtime_mode",
    "requested_mode",
    "selected_mode",
    "retrieval_status",
    "milvus_enabled",
    "milvus_status",
    "image_retrieval_status",
    "candidate_count",
    "matched_product_count",
    "fallback_used",
    "llm_configured",
    "llm_used",
    "use_milvus_retrieval",
    "use_rag_query_expansion",
    "elapsed_ms",
    "catalog_scope",
    "recommendation_domain",
    "stream_llm_enabled",
    "stream_llm_reason",
}


def is_debug_mode() -> bool:
    return os.getenv("APP_ENV", "").strip().lower() in DEBUG_ENVS


def public_error(exc: BaseException, fallback: str = PUBLIC_ERROR_FALLBACK) -> str:
    if is_debug_mode():
        return sanitize_text(str(exc))
    return fallback


def log_exception(message: str, exc: BaseException) -> None:
    logging.getLogger(__name__).exception("%s: %s", message, sanitize_text(str(exc)))


def sanitize_text(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    replacements = [
        (r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]"),
        (r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^'\"\s,;}]+", r"\1=[REDACTED]"),
        (r"(?i)(redis|postgres(?:ql)?|mysql|mongodb|milvus)://[^\s'\"<>]+", r"\1://[REDACTED]"),
        (r"https?://[^\s'\"<>]+", "[URL_REDACTED]"),
        (r"(?i)\b[A-Z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)+[^\\/:*?\"<>|\r\n]*", "[PATH_REDACTED]"),
        (r"(?<!\w)/(?:home|var|tmp|mnt|Users|opt|app|workspace)(?:/[^\s'\"<>]+)+", "[PATH_REDACTED]"),
        (r"(?i)\b(sk|ak|rk)-[A-Za-z0-9_-]{12,}", "[KEY_REDACTED]"),
    ]
    for pattern, repl in replacements:
        text = re.sub(pattern, repl, text)
    return text


def sanitize_report(report: Any) -> Any:
    return _sanitize_value(_to_plain(report), force_public=not is_debug_mode())


def sanitize_trace(trace: Any) -> Any:
    plain = _to_plain(trace)
    if is_debug_mode():
        return _sanitize_value(plain, force_public=False)
    if not isinstance(plain, dict):
        return {}
    public = {
        key: _sanitize_value(value, force_public=True)
        for key, value in plain.items()
        if key in PUBLIC_TRACE_KEYS
    }
    retrieval = plain.get("milvus_retrieval") or plain.get("retrieval") or {}
    if isinstance(retrieval, dict):
        public.setdefault("retrieval_status", retrieval.get("status"))
        public.setdefault("milvus_status", retrieval.get("status"))
        public.setdefault("candidate_count", retrieval.get("total_hits") or retrieval.get("candidate_count"))
    image_retrieval = plain.get("image_retrieval") or {}
    if isinstance(image_retrieval, dict):
        public.setdefault("image_retrieval_status", image_retrieval.get("status"))
    if "plans" in plain and "matched_product_count" not in public:
        public["matched_product_count"] = _count_matched_products(plain.get("plans"))
    public.setdefault("llm_used", plain.get("llm_guidance") == "enabled")
    return {key: value for key, value in public.items() if value is not None}


def sanitize_result_for_response(result: Any) -> Dict[str, Any]:
    payload = _to_plain(result)
    if not isinstance(payload, dict):
        return {}
    payload["trace"] = sanitize_trace(payload.get("trace") or {})
    return _sanitize_value(payload, force_public=not is_debug_mode())


sanitize_trace_for_public_response = sanitize_trace


def _to_plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {key: _to_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_plain(item) for item in value]
    if isinstance(value, tuple):
        return [_to_plain(item) for item in value]
    return value


def _sanitize_value(value: Any, *, force_public: bool) -> Any:
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if force_public and key_text.lower() in SENSITIVE_KEYS:
                continue
            cleaned[key_text] = _sanitize_value(item, force_public=force_public)
        return cleaned
    if isinstance(value, list):
        return [_sanitize_value(item, force_public=force_public) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def _count_matched_products(plans: Any) -> int:
    if not isinstance(plans, list):
        return 0
    product_ids = set()
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        for component in plan.get("components") or []:
            if not isinstance(component, dict):
                continue
            product = component.get("product") or {}
            if isinstance(product, dict) and product.get("product_id"):
                product_ids.add(str(product["product_id"]))
    return len(product_ids)
