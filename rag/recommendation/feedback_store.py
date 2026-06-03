'''
把一次“推荐反馈”追加写入一个 .jsonl 文件，方便以后做 FAQ、模板、权重调优等离线分析。
'''

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_FEEDBACK_PATH = ROOT_DIR / "data" / "feedback" / "recommendation_feedback.jsonl"


def append_feedback_record(
    payload: Dict[str, Any],
    *,
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Append one recommendation feedback event for later FAQ/template/weight tuning."""

    target = path or DEFAULT_FEEDBACK_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    with target.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {
        "status": "ok",
        "path": str(target),
        "created_at": record["created_at"],
    }
