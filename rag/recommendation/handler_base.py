"""Shared handler utilities and trace-span infrastructure.

Provides:
- ``trace_span``: context-manager for nested performance spans.
- ``handler_common``: shared logic extracted from individual handlers
  (catalog loading, session update, SSE helpers).
"""
from __future__ import annotations

import contextlib
import logging
import time
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)


# ── trace span ────────────────────────────────────────────────────────────

@contextlib.contextmanager
def trace_span(
    name: str,
    trace_id: str = "",
    parent_id: str = "",
    **extra: Any,
) -> Generator[Dict[str, Any], None, None]:
    """Lightweight tree-structured span for performance observability.

    Usage::

        with trace_span("filter_products", trace_id=tid) as span:
            products = do_filter(...)
            span["candidate_count"] = len(products)

    The span dict is yielded so the caller can attach arbitrary metadata.
    On exit, ``duration_ms`` is computed automatically.
    """
    span: Dict[str, Any] = {
        "name": name,
        "trace_id": trace_id,
        "parent_id": parent_id,
        "start_ns": time.perf_counter_ns(),
        **extra,
    }
    try:
        yield span
    except Exception as exc:
        span["error"] = str(exc)
        span["error_type"] = type(exc).__name__
        raise
    finally:
        span["duration_ms"] = round((time.perf_counter_ns() - span["start_ns"]) / 1e6, 2)
        _record_span(span)


# Thread-local span storage (same pattern as _parse_trace_local)
import threading as _threading

_span_store = _threading.local()


def _record_span(span: Dict[str, Any]) -> None:
    """Append a completed span to the thread-local log."""
    if not hasattr(_span_store, "spans"):
        _span_store.spans = []
    _span_store.spans.append(span)
    # Keep at most 200 spans per thread
    if len(_span_store.spans) > 200:
        _span_store.spans = _span_store.spans[-200:]


def get_trace_spans() -> List[Dict[str, Any]]:
    """Return all recorded spans for the current thread (for logging / SSE)."""
    return list(getattr(_span_store, "spans", []))


def clear_trace_spans() -> None:
    """Clear the thread-local span log."""
    _span_store.spans = []


def generate_trace_id(session_id: str = "") -> str:
    """Generate a short, unique-ish trace ID for a request."""
    ts = int(time.time() * 1000) % 1000000
    suffix = abs(hash(session_id)) % 10000 if session_id else 0
    return f"t{ts}-{suffix}"


# ── handler common helpers ────────────────────────────────────────────────

def load_catalog_safe(scope: str = "combined"):
    """Load product catalog with graceful error handling.

    Returns the catalog or None if loading fails.
    """
    try:
        from rag.recommendation.product_loader import load_catalog_for_scope, load_combined_product_catalog
        if scope == "combined":
            return load_combined_product_catalog()
        return load_catalog_for_scope(scope)
    except Exception:
        logger.warning("Failed to load catalog for scope=%s", scope, exc_info=True)
        return None


def resolve_product_ids_from_session(session: Any) -> List[str]:
    """Extract the most recently recommended product IDs from session.

    Unified helper replacing duplicated logic across handlers.
    """
    from rag.recommendation.session_state import last_recommended_product_ids
    return last_recommended_product_ids(session)


def safe_catalog_get(catalog: Any, product_id: str) -> Optional[Any]:
    """Safely fetch a product from catalog, returning None on any error."""
    if catalog is None or not product_id:
        return None
    try:
        return catalog.get(product_id)
    except Exception:
        return None
