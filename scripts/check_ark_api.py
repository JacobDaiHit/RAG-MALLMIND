import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from rag.recommendation.llm_client import OpenAICompatibleChatClient, report_to_dict  # noqa: E402


def main() -> int:
    """命令行入口函数，解析参数并调度当前脚本的主要流程。"""
    client = OpenAICompatibleChatClient()
    report = client.diagnose()
    print(json.dumps(report_to_dict(report), ensure_ascii=False, indent=2))
    return 0 if report.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
