import json
from typing import Any, Dict, Iterator


def model_to_dict(value: Any) -> Any:
    """Convert Pydantic v1/v2 models to JSON-serializable dictionaries."""

    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    return value


def sse_event(event: str, data: Dict[str, Any]) -> str:
    """Wrap an event name and payload as browser EventSource text."""

    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def validation_error_events(error_detail: str, validation_version: str) -> Iterator[str]:
    """Yield the standard validation-error SSE sequence."""

    yield sse_event(
        "validation_error",
        {
            "label": "需求无法识别",
            "detail": error_detail,
            "validation_version": validation_version,
        },
    )
    yield sse_event("done", {"label": "推荐已停止"})
