"""test_agent_v1_combined.py - MallMind Agent v1 全量测试（原有63 + 补充72 = 135用例）

用法:
  python tests/test_agent_v1_combined.py

会依次运行所有用例并生成统一报告。
"""
from __future__ import annotations

import json
import os
import sys
import time

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from test_agent_v1 import (
    ALL_CASES as ORIGINAL_CASES,
    TestCase,
    TestResult,
    check_server_health,
    run_test_case,
    run_all_tests,
    _save_raw_event,
    BASE_URL,
    REPORT_DIR,
    RAW_DIR,
)
from test_agent_v1_supplement import (
    ALL_SUPPLEMENT_CASES,
)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="MallMind Agent v1 全量测试")
    parser.add_argument("--limit", type=int, default=0, help="只运行前 N 个用例")
    parser.add_argument("--base-url", type=str, default="", help="覆盖服务器地址")
    parser.add_argument("--original-only", action="store_true", help="只运行原有63用例")
    parser.add_argument("--supplement-only", action="store_true", help="只运行补充72用例")
    args = parser.parse_args()

    if args.base_url:
        import test_agent_v1 as _mod
        _mod.BASE_URL = args.base_url
        _mod.CHAT_STREAM_URL = f"{args.base_url}/api/chat/stream"
        _mod.HEALTH_URL = f"{args.base_url}/api/health"

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    print("检查服务器连通性...")
    if not check_server_health():
        print("\n服务器不可达！请先启动后端。")
        sys.exit(1)

    # 选择用例
    if args.original_only:
        cases = ORIGINAL_CASES
    elif args.supplement_only:
        cases = ALL_SUPPLEMENT_CASES
    else:
        cases = ORIGINAL_CASES + ALL_SUPPLEMENT_CASES

    if args.limit > 0:
        cases = cases[:args.limit]

    print(f"将运行 {len(cases)} 个用例（原有: {len(ORIGINAL_CASES)}, 补充: {len(ALL_SUPPLEMENT_CASES)}）\n")

    results = run_all_tests(cases, limit=args.limit)

    # 统计
    errors = sum(1 for r in results if r.error)
    no_tools = sum(1 for r in results if not r.tool_calls and not r.error)
    with_cards = sum(1 for r in results if r.product_cards)
    total_ms = sum(r.elapsed_ms for r in results)
    avg_ms = total_ms / len(results) if results else 0

    # 分区统计
    orig_ids = {c.id for c in ORIGINAL_CASES}
    orig_results = [r for r in results if r.case.id in orig_ids]
    supp_results = [r for r in results if r.case.id not in orig_ids]

    def section_stats(label, section_results):
        if not section_results:
            return
        n = len(section_results)
        e = sum(1 for r in section_results if r.error)
        c = sum(1 for r in section_results if r.product_cards)
        print(f"    [{label}] 总用例: {n} | HTTP错误: {e} | 有商品卡: {c}")

    print(f"\n{'='*70}")
    print(f"  全量统计:")
    print(f"    总用例: {len(results)}")
    print(f"    HTTP错误: {errors}")
    print(f"    无工具调用: {no_tools}")
    print(f"    有商品卡片: {with_cards}")
    print(f"    平均耗时: {avg_ms:.0f}ms")
    section_stats("原有63", orig_results)
    section_stats("补充72", supp_results)
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
