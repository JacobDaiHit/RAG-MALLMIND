"""Minimal dependency-free SSE client used by the fixed live evaluation.

``post_sse`` measures time to the first complete SSE event and time to the
terminal event.  It records only structured event payloads; it never logs HTTP
authorization headers or model prompts.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class SseExchange:
    status_code: int
    events: tuple[tuple[str, dict[str, Any]], ...]
    first_event_ms: int | None
    total_ms: int
    transport_error: str = ""


def post_sse(*, base_url: str, path: str, payload: dict[str, Any], timeout_seconds: float) -> SseExchange:
    """POST JSON and parse one complete SSE response without buffering metrics."""

    started = time.perf_counter()
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            events, first_event_ms = _read_events(response, started)
            return SseExchange(int(response.status), tuple(events), first_event_ms, _elapsed_ms(started))
    except HTTPError as exc:
        return SseExchange(exc.code, (), None, _elapsed_ms(started), f"http_{exc.code}")
    except (URLError, TimeoutError, OSError) as exc:
        return SseExchange(0, (), None, _elapsed_ms(started), type(exc).__name__)


def post_json(*, base_url: str, path: str, payload: dict[str, Any], timeout_seconds: float) -> tuple[int, dict[str, Any], int, str]:
    """Call a non-streaming V3 endpoint and return sanitized structured data."""

    started = time.perf_counter()
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw)
            return int(response.status), data if isinstance(data, dict) else {}, _elapsed_ms(started), ""
    except HTTPError as exc:
        return exc.code, {}, _elapsed_ms(started), f"http_{exc.code}"
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return 0, {}, _elapsed_ms(started), type(exc).__name__


def _read_events(response: Any, started: float) -> tuple[list[tuple[str, dict[str, Any]]], int | None]:
    events: list[tuple[str, dict[str, Any]]] = []
    event_name = "message"
    data_lines: list[str] = []
    first_event_ms: int | None = None
    for raw_line in response:
        line = raw_line.decode("utf-8").rstrip("\r\n")
        if not line:
            if data_lines:
                data = json.loads("\n".join(data_lines))
                events.append((event_name, data if isinstance(data, dict) else {}))
                if first_event_ms is None:
                    first_event_ms = _elapsed_ms(started)
            event_name = "message"
            data_lines = []
            continue
        if line.startswith("event: "):
            event_name = line[7:]
        elif line.startswith("data: "):
            data_lines.append(line[6:])
    if data_lines:
        data = json.loads("\n".join(data_lines))
        events.append((event_name, data if isinstance(data, dict) else {}))
        if first_event_ms is None:
            first_event_ms = _elapsed_ms(started)
    return events, first_event_ms


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
