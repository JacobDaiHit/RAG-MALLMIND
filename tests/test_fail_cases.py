"""测试 v2_fail_partial_retest_report.md 中的失败和部分成功案例

增强版：增加 LLM 原始输出收集 + 根因分类 + 字段提取覆盖率统计
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_agent_v1 import TestCase, TestResult, send_chat_stream

BASE_URL = os.getenv("MALLMIND_TEST_BASE_URL", "http://127.0.0.1:8000")
CHAT_STREAM_URL = f"{BASE_URL}/api/chat/stream"
REPORT_DIR = Path(__file__).resolve().parents[1] / ".pytest_tmp"

# ============================================================================
# 根因分类标签
# ============================================================================
ROOT_CAUSE_LABELS = {
    "LLM_CAPABILITY": "7B 模型无法稳定输出 brands/sort_order/price 等字段",
    "PIPELINE_BUG": "pipeline 逻辑缺陷（如预算过滤、品牌硬过滤缺失）",
    "CONTEXT_MISSING": "多轮对话上下文未正确注入 LLM prompt",
    "PROMPT_ISSUE": "prompt 模板不够清晰或示例不足",
    "GUARD_OVER拦截": "hallucination guard 过度拦截合法购物查询",
    "CART_FALLBACK": "购物车操作在无历史上下文时缺少 fallback",
    "UNKNOWN": "待诊断",
}

# 每个 FAIL/PARTIAL case 的根因分类
ROOT_CAUSE_MAP: Dict[int, str] = {
    124: "LLM_CAPABILITY",   # 华为品牌：LLM 未提取 brands
    130: "LLM_CAPABILITY",   # 华为Pura：LLM 未提取 brands
    146: "LLM_CAPABILITY",   # 不要Nike：LLM 未提取 exclude_brands
    165: "PROMPT_ISSUE",     # PS5：general_chat LLM 无目录感知
    167: "PIPELINE_BUG",     # 三星S30：pipeline 未识别缺失商品
    168: "PIPELINE_BUG",     # 一百万：pipeline 未识别超高价无商品
    128: "GUARD_OVER拦截",   # 最贵商品：guard 过度拦截
    148: "CART_FALLBACK",    # iPhone加购：无历史时 cart 无 fallback
    152: "PIPELINE_BUG",     # 改数量：cart 批量修改而非精确匹配
    123: "LLM_CAPABILITY",   # 价格排序：LLM 输出 sort_by 而非 sort_order
    131: "PIPELINE_BUG",     # 小米Ultra：pipeline 未识别缺失型号
    133: "GUARD_OVER拦截",   # AirPods心率：guard 过度拦截
    141: "CONTEXT_MISSING",  # 耳机差评：无上下文时应追问
    149: "LLM_CAPABILITY",   # 华为Pura黑色：LLM 未提取 brands
    150: "PIPELINE_BUG",     # 看购物车：回复文案异常
    151: "GUARD_OVER拦截",   # 去掉第一个：guard 拦截 cart 操作
    157: "CONTEXT_MISSING",  # 续航：多轮上下文未注入
    164: "CONTEXT_MISSING",  # 看别的：未排除已推荐商品
    170: "LLM_CAPABILITY",   # 高端护肤：LLM 未提取"高端"语义
    112: "LLM_CAPABILITY",   # 零食：LLM 未提取 must_have_terms
    126: "CONTEXT_MISSING",  # 第二页：无上下文时应追问
}

# 每个 case 的 LLM 应提取的关键字段
EXPECTED_FIELDS_MAP: Dict[int, List[str]] = {
    124: ["brands"],
    130: ["brands"],
    146: ["exclude_brands"],
    123: ["sort_order"],
    112: ["must_have_terms"],
    149: ["brands"],
    170: ["budget"],
}

# 报告中的 FAIL 和 PARTIAL 案例
FAIL_CASES = [
    # 品牌/排除过滤失效
    TestCase(124, "FAIL_品牌过滤", "华为品牌的商品有哪些", "仅返回华为品牌商品"),
    TestCase(130, "FAIL_品牌过滤", "华为Pura 90 Pro 的详细信息", "返回华为Pura 90 Pro"),
    TestCase(146, "FAIL_排除品牌", "看看运动鞋，不要Nike的", "不返回Nike商品"),
    # 防幻觉失败
    TestCase(165, "FAIL_防幻觉", "你们有卖 PS5 吗", "诚实告知没有PS5"),
    TestCase(167, "FAIL_防幻觉", "三星Galaxy S30怎么样", "诚实告知没有该商品"),
    TestCase(168, "FAIL_防幻觉", "有没有一百万以上的商品", "诚实告知没有超高价商品"),
    # hallucination guard 过度拦截
    TestCase(128, "FAIL_过度拦截", "最贵的商品是什么", "推荐最贵的商品"),
    # 购物车路由错误
    TestCase(148, "FAIL_购物车", "帮我把 iPhone 17 Pro 加到购物车", "应先推荐再操作"),
    TestCase(152, "FAIL_购物车", "把华为耳机数量改成2", "仅修改华为耳机数量", session_id="fail_cart"),
]

PARTIAL_CASES = [
    TestCase(123, "PARTIAL_排序", "所有商品按价格从低到高排列", "商品按价格排序"),
    TestCase(131, "PARTIAL_兜底", "小米17 Ultra 有几个版本", "识别并正确兜底"),
    TestCase(133, "PARTIAL_过度拦截", "AirPods Pro 3 支持心率监测吗", "应走recommend工具"),
    TestCase(141, "PARTIAL_追问", "这款耳机有差评吗", "应追问是哪款耳机"),
    TestCase(149, "PARTIAL_品牌过滤", "我要买华为Pura 90 Pro，黑色的", "返回华为品牌商品"),
    TestCase(150, "PARTIAL_购物车", "看看我的购物车", "显示购物车内容", session_id="fail_cart"),
    TestCase(151, "PARTIAL_购物车", "把第一个去掉", "应走apply_cart_instruction", session_id="fail_cart"),
    TestCase(157, "PARTIAL_多轮", "续航怎么样", "理解上下文，回答上轮商品", session_id="fail_mt1"),
    TestCase(164, "PARTIAL_多轮", "都不要，看看别的", "排除已推荐商品", session_id="fail_mt2"),
    TestCase(170, "PARTIAL_语义", "高端护肤品送妈妈，预算3000", "推荐高端护肤品"),
]

ALL_TEST_CASES = FAIL_CASES + PARTIAL_CASES


def run_test_case(case: TestCase) -> TestResult:
    """执行单个测试用例"""
    result = TestResult(case=case)
    t0 = time.perf_counter()

    try:
        response = send_chat_stream(case.input_text, session_id=case.session_id or f"fail_case_{case.id}")
    except Exception as exc:
        result.error = f"HTTP 请求失败: {exc}"
        result.elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return result

    events = response["events"]
    result.raw_events = events

    for evt in events:
        etype = evt.get("event", "")
        edata = evt.get("data", {})
        result.events_received.append(etype)

        if etype == "runtime_mode":
            result.runtime_mode = edata.get("selected_mode", "")
        elif etype == "tool_call":
            result.tool_calls.append(edata)
            result.routing_trace = edata.get("routing_trace") or {}
        elif etype == "delta":
            result.text_response += edata.get("text", "")
        elif etype == "product_cards":
            cards = edata if isinstance(edata, list) else edata.get("products", edata.get("cards", []))
            result.product_cards = cards
        elif etype == "cart":
            result.cart = edata.get("cart") if "cart" in edata else edata
        elif etype == "result":
            if not result.text_response and edata.get("text"):
                result.text_response = edata["text"]

    result.elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return result


def extract_llm_raw_args(result: TestResult) -> Dict[str, Any]:
    """从 tool_call 事件中提取 LLM 原始 arguments"""
    for tc in result.tool_calls:
        args = tc.get("arguments") or {}
        if args:
            return dict(args)
    return {}


def check_field_extraction(actual_args: Dict, expected_fields: List[str]) -> Tuple[List[str], List[str]]:
    """检查 LLM 输出的 arguments 中是否包含预期字段"""
    extracted = []
    missing = []
    for f in expected_fields:
        val = actual_args.get(f)
        if val is not None and val != "" and val != [] and val != {}:
            extracted.append(f)
        else:
            # 检查变体字段名
            variant_found = False
            if f == "sort_order":
                for variant in ["sort_by", "sort", "order"]:
                    v = actual_args.get(variant)
                    if v is not None and v != "":
                        extracted.append(f"{f}(via {variant})")
                        variant_found = True
                        break
            if not variant_found:
                missing.append(f)
    return extracted, missing


def analyze_result(result: TestResult) -> Dict[str, Any]:
    """分析测试结果（增强版：增加 LLM 原始输出和根因分类）"""
    case = result.case
    analysis = {
        "case_id": case.id,
        "input": case.input_text,
        "category": case.category,
        "status": "ERROR" if result.error else "OK",
        "tool_chain": result.tool_chain_str,
        "product_count": len(result.product_cards),
        "product_brands": [],
        "routing_source": None,
        # ── 新增：LLM 原始输出 ──
        "llm_raw_args": extract_llm_raw_args(result),
        "root_cause": ROOT_CAUSE_MAP.get(case.id, "UNKNOWN"),
        "root_cause_desc": ROOT_CAUSE_LABELS.get(ROOT_CAUSE_MAP.get(case.id, "UNKNOWN"), ""),
    }

    if result.product_cards:
        analysis["product_brands"] = [c.get("brand", "?") for c in result.product_cards]
        analysis["product_titles"] = [c.get("title", "?") for c in result.product_cards]

    if result.routing_trace:
        analysis["routing_source"] = result.routing_trace.get("router_final_source")
        analysis["local_route"] = result.routing_trace.get("local", {}).get("name") if isinstance(result.routing_trace.get("local"), dict) else None
        analysis["llm_route"] = result.routing_trace.get("llm", {}).get("name") if isinstance(result.routing_trace.get("llm"), dict) else None

    # ── 新增：字段提取检查 ──
    expected_fields = EXPECTED_FIELDS_MAP.get(case.id, [])
    if expected_fields:
        extracted, missing = check_field_extraction(analysis["llm_raw_args"], expected_fields)
        analysis["expected_fields"] = expected_fields
        analysis["extracted_fields"] = extracted
        analysis["missing_fields"] = missing
        analysis["field_coverage"] = len(extracted) / len(expected_fields) if expected_fields else 0.0

    # 分析品牌过滤是否生效
    if "华为" in case.input_text and "不要" not in case.input_text:
        all_huawei = all("华为" in str(b) for b in analysis["product_brands"]) if analysis["product_brands"] else False
        analysis["brand_filter_ok"] = all_huawei

    if "Nike" in case.input_text and "不要" in case.input_text:
        has_nike = any("Nike" in str(t) for t in analysis.get("product_titles", []))
        analysis["exclude_nike_ok"] = not has_nike

    return analysis


def generate_report(results: List[TestResult]) -> str:
    """生成测试报告（增强版：增加 LLM 字段覆盖率和根因分类）"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# 失败案例复测报告（增强版）",
        "",
        f"测试时间：{now}",
        f"测试服务器：{BASE_URL}",
        "",
        "## 测试结果汇总",
        "",
        "| # | 类别 | 输入 | 工具调用 | 商品数 | 根因 | 品牌过滤? | 排除Nike? |",
        "|---|------|------|---------|--------|------|----------|----------|",
    ]

    analyses = []
    for r in results:
        a = analyze_result(r)
        analyses.append(a)
        inp = r.case.input_text[:30] + ("..." if len(r.case.input_text) > 30 else "")
        chain = r.tool_chain_str[:40]
        brand_ok = "✓" if a.get("brand_filter_ok") else " "
        exclude_ok = "✓" if a.get("exclude_nike_ok") else " "
        cause = a.get("root_cause", "?")
        lines.append(f"| {a['case_id']} | {a['category']} | {inp} | {chain} | {a['product_count']} | {cause} | {brand_ok} | {exclude_ok} |")

    # ── 新增：字段覆盖率汇总 ──
    field_cases = [a for a in analyses if "expected_fields" in a]
    if field_cases:
        lines.extend(["", "## LLM 字段提取覆盖率", ""])
        lines.append("| # | 输入 | 预期字段 | 提取 | 缺失 | 覆盖率 | LLM 原始 args |")
        lines.append("|---|------|---------|------|------|--------|--------------|")
        for a in field_cases:
            extracted_str = ", ".join(a["extracted_fields"]) if a["extracted_fields"] else "-"
            missing_str = ", ".join(a["missing_fields"]) if a["missing_fields"] else "-"
            coverage_str = f"{a['field_coverage']:.0%}"
            args_preview = json.dumps(a["llm_raw_args"], ensure_ascii=False)[:80]
            lines.append(f"| {a['case_id']} | {a['input'][:25]} | {', '.join(a['expected_fields'])} | {extracted_str} | {missing_str} | {coverage_str} | {args_preview} |")

        total_expected = sum(len(a["expected_fields"]) for a in field_cases)
        total_extracted = sum(len(a["extracted_fields"]) for a in field_cases)
        overall = total_extracted / total_expected if total_expected > 0 else 0
        lines.append(f"\n**总体字段覆盖率: {overall:.1%} ({total_extracted}/{total_expected})**")

    # ── 新增：根因分类汇总 ──
    cause_counts: Dict[str, int] = {}
    for a in analyses:
        cause = a.get("root_cause", "UNKNOWN")
        cause_counts[cause] = cause_counts.get(cause, 0) + 1

    lines.extend(["", "## 根因分类汇总", ""])
    lines.append("| 根因类型 | 数量 | 说明 |")
    lines.append("|---------|------|------|")
    for cause, count in sorted(cause_counts.items(), key=lambda x: -x[1]):
        desc = ROOT_CAUSE_LABELS.get(cause, cause)
        lines.append(f"| {cause} | {count} | {desc} |")

    lines.append("")
    lines.append("## 详细分析")
    lines.append("")

    for a, r in zip(analyses, results):
        lines.append(f"### #{a['case_id']} [{a['category']}] \"{r.case.input_text}\"")
        lines.append("")
        lines.append(f"- **预期**: {r.case.expected_behavior}")
        lines.append(f"- **根因**: {a.get('root_cause', '?')} — {a.get('root_cause_desc', '')}")
        lines.append(f"- **工具调用**: {a['tool_chain']}")

        # LLM 原始输出
        if a.get("llm_raw_args"):
            lines.append(f"- **LLM 原始 arguments**:")
            lines.append(f"  ```json")
            lines.append(f"  {json.dumps(a['llm_raw_args'], ensure_ascii=False, indent=2)}")
            lines.append(f"  ```")

        # 字段提取
        if "expected_fields" in a:
            lines.append(f"- **字段提取**: 预期={a['expected_fields']}, 提取={a.get('extracted_fields', [])}, 缺失={a.get('missing_fields', [])}")

        if r.product_cards:
            lines.append(f"- **商品卡片** ({len(r.product_cards)}):")
            for c in r.product_cards:
                brand = c.get("brand", "?")
                title = c.get("title", "?")
                price = c.get("min_price", c.get("base_price", "?"))
                lines.append(f"  - {title} (品牌: {brand}, 价格: {price})")
        lines.append(f"- **回复**:")
        lines.append(f"  > {r.text_response[:400].replace(chr(10), ' ')}")
        if "brand_filter_ok" in a:
            lines.append(f"- **品牌过滤检查**: {'PASS' if a['brand_filter_ok'] else 'FAIL'}")
        if "exclude_nike_ok" in a:
            lines.append(f"- **Nike排除检查**: {'PASS' if a['exclude_nike_ok'] else 'FAIL'}")
        if a["routing_source"]:
            lines.append(f"- **路由来源**: {a['routing_source']}")
            if a["local_route"]:
                lines.append(f"  - Local route: {a['local_route']}")
            if a["llm_route"]:
                lines.append(f"  - LLM route: {a['llm_route']}")
        lines.append("")

    return "\n".join(lines)


def main():
    print("\n" + "=" * 70)
    print("  测试 v2_fail_partial_retest_report.md 中的失败案例（增强版）")
    print(f"  服务器: {BASE_URL}")
    print("=" * 70 + "\n")

    # 检查服务器健康
    import urllib.request
    try:
        req = urllib.request.Request(f"{BASE_URL}/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            print("  服务器在线\n")
    except Exception as exc:
        print(f"  警告: 服务器不可达 ({exc})，但继续尝试...\n")

    results: List[TestResult] = []

    for i, case in enumerate(ALL_TEST_CASES):
        print(f"[{i+1}/{len(ALL_TEST_CASES)}] #{case.id} \"{case.input_text}\"")
        result = run_test_case(case)
        results.append(result)

        status = "ERR" if result.error else "OK"
        print(f"  -> {status} | 工具={result.tool_chain_str}")
        if result.product_cards:
            print(f"  -> 商品: {[c.get('title', '?')[:30] for c in result.product_cards]}")
        resp_preview = result.text_response[:80].replace("\n", " ")
        print(f"  -> 回复: {resp_preview}...")

        # 显示 LLM 原始输出
        llm_args = extract_llm_raw_args(result)
        if llm_args:
            print(f"  -> LLM args: {json.dumps(llm_args, ensure_ascii=False)[:120]}")

        # 显示根因
        cause = ROOT_CAUSE_MAP.get(case.id, "UNKNOWN")
        print(f"  -> 根因: {cause}")
        print()

    # 生成报告
    report_md = generate_report(results)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / "fail_cases_retest_report.md"
    report_path.write_text(report_md, encoding="utf-8")
    print(f"\n报告已生成: {report_path}")

    # 打印字段覆盖率统计
    analyses = [analyze_result(r) for r in results]
    field_cases = [a for a in analyses if "expected_fields" in a]
    if field_cases:
        total_expected = sum(len(a["expected_fields"]) for a in field_cases)
        total_extracted = sum(len(a["extracted_fields"]) for a in field_cases)
        coverage = total_extracted / total_expected if total_expected > 0 else 0
        print(f"\n  字段覆盖率: {coverage:.1%} ({total_extracted}/{total_expected})")

    # 打印根因统计
    cause_counts: Dict[str, int] = {}
    for a in analyses:
        cause = a.get("root_cause", "UNKNOWN")
        cause_counts[cause] = cause_counts.get(cause, 0) + 1
    print(f"\n  根因分布:")
    for cause, count in sorted(cause_counts.items(), key=lambda x: -x[1]):
        print(f"    {cause}: {count}")


if __name__ == "__main__":
    main()
