"""Safe Server-Sent Events helpers shared by active API routes.

``sse_event`` serializes one named event and ``safe_stream`` converts an
iterator failure into a bounded public error plus a final ``done`` event.
Business modules yield these helpers but do not own HTTP response creation.
"""
import json
import logging
from typing import Any, Callable, Dict, Iterable

from rag.utils.runtime_errors import public_error


logger = logging.getLogger(__name__)


def sse_event(event: str, data: Dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def safe_stream(generate: Callable[[], Iterable[str]], done_payload: Dict[str, Any]) -> Iterable[str]:
    try:
        yield from generate()
    except Exception as exc:
        logger.exception("SSE stream failed")
        yield sse_event("error", {"label": "系统异常", "detail": public_error(exc)})
        yield sse_event("done", done_payload)
