"""Legacy 62-case chat compatibility tests for the current FastAPI app.

The old project keeps the canonical case definitions in
D:/github/rag/backend/scripts/run_full_tests.py.  This test loads those case
definitions, then executes them against this project's in-process /api/chat
endpoint so pytest can report each legacy case as a normal test item.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient


os.environ.setdefault("RECOMMENDATION_ENABLE_MILVUS", "false")
os.environ.setdefault("RECOMMENDATION_USE_MILVUS", "false")
os.environ.setdefault("RECOMMENDATION_RETRIEVAL_TIMEOUT_SECONDS", "3")
os.environ.setdefault("RECOMMENDATION_LLM_GUIDANCE", "false")
os.environ.setdefault("RECOMMENDATION_LLM_PARSE", "auto")

LEGACY_SCRIPT = Path(
    os.getenv(
        "LEGACY_FULL_TEST_SCRIPT",
        r"D:\github\rag\backend\scripts\run_full_tests.py",
    )
)


def _load_legacy_namespace() -> Dict[str, Any]:
    if not LEGACY_SCRIPT.exists():
        pytest.skip(
            f"legacy full-test script not found: {LEGACY_SCRIPT}",
            allow_module_level=True,
        )
    source = "from __future__ import annotations\n" + LEGACY_SCRIPT.read_text(encoding="utf-8")
    namespace: Dict[str, Any] = {
        "__name__": "legacy_full_tests_for_pytest",
        "__file__": str(LEGACY_SCRIPT),
    }
    exec(compile(source, str(LEGACY_SCRIPT), "exec"), namespace)
    return namespace


_LEGACY = _load_legacy_namespace()
_LEGACY_CASES: List[Dict[str, Any]] = _LEGACY["define_test_cases"]()
_GET_TOOL_NAMES = _LEGACY["get_tool_names"]


@pytest.fixture(scope="module")
def chat_client() -> TestClient:
    from rag.api.recommendation_app import app

    return TestClient(app)


def test_legacy_case_count() -> None:
    assert len(_LEGACY_CASES) == 62


@pytest.mark.parametrize(
    "case",
    _LEGACY_CASES,
    ids=lambda case: f"{case['id']}-{case['name']}",
)
def test_legacy_chat_case(case: Dict[str, Any], chat_client: TestClient) -> None:
    session_id = f"pytest-{case['id'].lower()}"
    last_reply = ""
    last_tool_calls: List[Dict[str, Any]] = []
    started_at = time.perf_counter()

    for message in case["messages"]:
        response = chat_client.post(
            "/api/chat",
            json={
                "message": message,
                "session_id": session_id,
                "mode": "balanced",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        last_reply = data.get("reply", "")
        last_tool_calls = data.get("tool_calls", [])

    passed = case["judge"](last_reply, last_tool_calls)
    duration_ms = int((time.perf_counter() - started_at) * 1000)
    tool_names = _GET_TOOL_NAMES(last_tool_calls)
    assert passed, (
        f"{case['id']} {case['name']} failed legacy criteria: {case['criteria']}\n"
        f"duration_ms={duration_ms}; tools={tool_names}\n"
        f"reply={last_reply[:1200]}"
    )
