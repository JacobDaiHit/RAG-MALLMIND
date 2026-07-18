"""Measure concurrent real-HTTP request correctness for fixed V3 scenarios.

The normal fixed set is deliberately serial because several cases depend on one
session's cards or clarification state.  This runner uses independent sessions
to detect cross-request corruption and reports, rather than hides, a same-session
scenario if one is later added to the fixture.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from final_test.sse import post_sse


DEFAULT_CASES = ROOT / "final_test" / "fixtures" / "concurrency_cases.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run fixed concurrent V3 HTTP requests.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=ROOT / "final_test" / "results" / "v3_concurrency_latest.json")
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    results = [run_case(case, base_url=args.base_url, timeout_seconds=args.timeout_seconds) for case in cases]
    total = sum(item["total"] for item in results)
    correct = sum(item["correct"] for item in results)
    payload = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "base_url": args.base_url,
        "cases": results,
        "concurrent_request_correctness": round(correct / total, 6) if total else None,
        "correct": correct,
        "total": total,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def run_case(case: dict[str, Any], *, base_url: str, timeout_seconds: float) -> dict[str, Any]:
    workers = int(case["workers"])
    session_mode = str(case.get("session_mode") or "unique")

    def one(index: int) -> dict[str, Any]:
        session_id = f"final-concurrency-{case['case_id']}-{index if session_mode == 'unique' else 'shared'}"
        exchange = post_sse(
            base_url=base_url,
            path="/api/chat/stream",
            payload={"session_id": session_id, "message": case["request"], "attachments": [], "images": []},
            timeout_seconds=timeout_seconds,
        )
        route = next((payload for name, payload in exchange.events if name == "v3_routing"), {})
        correct = (
            exchange.status_code == 200
            and route.get("action") == case["expected_action"]
            and route.get("status") == case["expected_status"]
            and not exchange.transport_error
        )
        return {"index": index, "correct": correct, "status": exchange.status_code, "action": route.get("action"), "route_status": route.get("status"), "total_ms": exchange.total_ms, "error": exchange.transport_error}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        samples = list(executor.map(one, range(workers)))
    return {
        "case_id": case["case_id"],
        "session_mode": session_mode,
        "total": len(samples),
        "correct": sum(item["correct"] for item in samples),
        "samples": samples,
    }


if __name__ == "__main__":
    main()
