"""LLM Fallback 诊断测试 — 量化 LLM 字段提取能力 + 根因分类

不修改后端代码，仅通过测试收集诊断数据：
1. LLM 原始输出字段覆盖率
2. FAIL/PARTIAL 案例根因分类（LLM_CAPABILITY / PIPELINE_BUG / CONTEXT_MISSING）
3. 多轮对话上下文传递验证
4. LLM 降级链路完整性验证

用法：
  python tests/test_llm_fallback_diagnostics.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_agent_v1 import TestCase, TestResult, send_chat_stream

BASE_URL = os.getenv("MALLMIND_TEST_BASE_URL", "http://127.0.0.1:8000")
REPORT_DIR = Path(__file__).resolve().parents[1] / "reports"

# ============================================================================
# 诊断用例定义
# ============================================================================

# ── A. 字段提取诊断（验证 LLM 是否输出 brands/sort_order/price 等字段）──
FIELD_EXTRACTION_CASES = [
    {
        "id": "FE-01",
        "input": "华为品牌的商品有哪些",
        "expected_fields": ["brands"],
        "root_cause": "LLM_CAPABILITY",
        "description": "LLM 应提取 brands=['华为']",
    },
    {
        "id": "FE-02",
        "input": "看看运动鞋，不要Nike的",
        "expected_fields": ["exclude_brands"],
        "root_cause": "LLM_CAPABILITY",
        "description": "LLM 应提取 exclude_brands=['Nike']",
    },
    {
        "id": "FE-03",
        "input": "所有商品按价格从低到高排列",
        "expected_fields": ["sort_order"],
        "root_cause": "LLM_CAPABILITY",
        "description": "LLM 应提取 sort_order='asc'",
    },
    {
        "id": "FE-04",
        "input": "3000到5000之间的手机",
        "expected_fields": ["price_min", "price_max"],
        "root_cause": "LLM_CAPABILITY",
        "description": "LLM 应提取 price_min=3000, price_max=5000",
    },
    {
        "id": "FE-05",
        "input": "有什么好吃的零食推荐吗",
        "expected_fields": ["must_have_terms"],
        "root_cause": "LLM_CAPABILITY",
        "description": "LLM 应提取 must_have_terms=['零食'] 或 category='食品'",
    },
    {
        "id": "FE-06",
        "input": "高端护肤品送妈妈，预算3000",
        "expected_fields": ["budget", "must_have_terms"],
        "root_cause": "LLM_CAPABILITY",
        "description": "LLM 应提取 budget=3000 + 语义修饰（高端）",
    },
    {
        "id": "FE-07",
        "input": "帮我把 iPhone 17 Pro 加到购物车",
        "expected_fields": ["product_ids"],
        "root_cause": "LLM_CAPABILITY",
        "description": "LLM 应提取 product_ids 或识别为 cart 操作",
    },
    {
        "id": "FE-08",
        "input": "推荐一款2000元以下的蓝牙耳机",
        "expected_fields": ["budget", "category"],
        "root_cause": "LLM_CAPABILITY",
        "description": "LLM 应提取 budget=2000 + category='耳机'",
    },
]

# ── B. 多轮对话上下文诊断 ──
MULTI_TURN_SEQUENCES = [
    {
        "id": "MT-01",
        "name": "续航追问（期望理解上文）",
        "session_id": "diag_mt_battery",
        "steps": [
            {"input": "推荐一款OPPO手机", "expect_tool": "recommend_shopping_products"},
            {"input": "续航怎么样", "expect_behavior": "应基于上文 OPPO 回答续航，而非推荐新品"},
        ],
        "root_cause": "CONTEXT_MISSING",
    },
    {
        "id": "MT-02",
        "name": "排除已推荐（期望返回不同商品）",
        "session_id": "diag_mt_exclude",
        "steps": [
            {"input": "推荐一款手机", "expect_tool": "recommend_shopping_products"},
            {"input": "都不要，看看别的", "expect_behavior": "应返回与上轮不同的手机"},
        ],
        "root_cause": "CONTEXT_MISSING",
    },
    {
        "id": "MT-03",
        "name": "这款耳机（期望追问而非推荐）",
        "session_id": "diag_mt_earphone",
        "steps": [
            {"input": "这款耳机有差评吗", "expect_behavior": "无上下文时应追问是哪款耳机"},
        ],
        "root_cause": "CONTEXT_MISSING",
    },
    {
        "id": "MT-04",
        "name": "第二页（期望追问品类）",
        "session_id": "diag_mt_page2",
        "steps": [
            {"input": "第二页的商品", "expect_behavior": "无上下文时应追问是哪个品类的第二页"},
        ],
        "root_cause": "CONTEXT_MISSING",
    },
]

# ── C. LLM 降级链路诊断 ──
DEGRADATION_CASES = [
    {
        "id": "DG-01",
        "input": "华为品牌的商品有哪些",
        "description": "当 LLM 未提取 brands 时，pipeline 是否有 fallback",
        "check": "product_cards 中应至少有 1 个华为品牌商品",
    },
    {
        "id": "DG-02",
        "input": "看看运动鞋，不要Nike的",
        "description": "当 LLM 未提取 exclude_brands 时，pipeline 是否从文本中提取排除",
        "check": "product_cards 中不应包含 Nike 商品",
    },
    {
        "id": "DG-03",
        "input": "有没有防水的运动手表",
        "description": "目录无运动手表时，应诚实告知而非推荐无关商品",
        "check": "product_cards 应为空或回复中应明确告知无此商品",
    },
    {
        "id": "DG-04",
        "input": "你们有卖 PS5 吗",
        "description": "应走 general_chat 并诚实告知无 PS5",
        "check": "回复中不应声称有 PS5",
    },
]

# ============================================================================
# 根因分类标签
# ============================================================================
ROOT_CAUSE_LABELS = {
    # LLM 字段提取能力不足（7B 模型上限）
    "LLM_CAPABILITY": "7B 模型无法稳定输出 brands/sort_order/price 等字段",
    # Pipeline bug（系统代码问题）
    "PIPELINE_BUG": "pipeline 逻辑缺陷（如预算过滤、品牌硬过滤缺失）",
    # 多轮上下文缺失
    "CONTEXT_MISSING": "多轮对话上下文未正确注入 LLM prompt",
    # Prompt 问题（可通过优化 prompt 解决）
    "PROMPT_ISSUE": "prompt 模板不够清晰或示例不足",
    # 未知
    "UNKNOWN": "待诊断",
}

# ============================================================================
# 工具函数
# ============================================================================

@dataclass
class FieldExtractionResult:
    case_id: str
    input_text: str
    expected_fields: List[str]
    actual_args: Dict[str, Any]
    extracted_fields: List[str]
    missing_fields: List[str]
    field_coverage: float
    root_cause: str
    routing_source: str = ""
    tool_name: str = ""
    elapsed_ms: int = 0


@dataclass
class MultiTurnResult:
    case_id: str
    name: str
    session_id: str
    steps: List[Dict[str, Any]]
    step_results: List[Dict[str, Any]] = field(default_factory=list)
    context_passed: bool = False
    root_cause: str = ""


@dataclass
class DegradationResult:
    case_id: str
    input_text: str
    description: str
    tool_name: str = ""
    product_count: int = 0
    product_brands: List[str] = field(default_factory=list)
    text_response: str = ""
    degradation_ok: bool = False
    check_detail: str = ""


def run_single_query(session_id: str, message: str) -> Dict[str, Any]:
    """发送单条消息，返回解析后的事件数据"""
    response = send_chat_stream(message, session_id=session_id)
    events = response["events"]

    result = {
        "tool_name": "",
        "tool_args": {},
        "tool_confidence": None,
        "tool_source": "",
        "routing_trace": {},
        "text_response": "",
        "product_cards": [],
        "product_count": 0,
        "product_brands": [],
        "events": events,
        "elapsed_ms": response.get("elapsed_ms", 0),
    }

    for evt in events:
        etype = evt.get("event", "")
        edata = evt.get("data", {})

        if etype == "tool_call":
            result["tool_name"] = edata.get("name", "")
            result["tool_args"] = edata.get("arguments") or {}
            result["tool_confidence"] = edata.get("confidence")
            result["tool_source"] = edata.get("source", "")
            result["routing_trace"] = edata.get("routing_trace") or {}
        elif etype == "delta":
            result["text_response"] += edata.get("text", "")
        elif etype == "product_cards":
            cards = edata if isinstance(edata, list) else edata.get("products", edata.get("cards", []))
            result["product_cards"] = cards
            result["product_count"] = len(cards)
            result["product_brands"] = [c.get("brand", "?") for c in cards]

    return result


def check_field_extraction(actual_args: Dict, expected_fields: List[str]) -> Tuple[List[str], List[str]]:
    """检查 LLM 输出的 arguments 中是否包含预期字段"""
    extracted = []
    missing = []
    for f in expected_fields:
        # 检查字段是否存在且非空
        val = actual_args.get(f)
        if val is not None and val != "" and val != [] and val != {}:
            extracted.append(f)
        else:
            # 检查变体字段名（如 sort_by vs sort_order）
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


# ============================================================================
# 测试执行
# ============================================================================

def run_field_extraction_diagnostics() -> List[FieldExtractionResult]:
    """A. 字段提取诊断"""
    results = []
    print("\n" + "=" * 80)
    print("  A. LLM 字段提取诊断")
    print("=" * 80)

    for case in FIELD_EXTRACTION_CASES:
        print(f"\n  [{case['id']}] {case['input']}")
        qr = run_single_query(f"diag_fe_{case['id'].lower()}", case["input"])

        extracted, missing = check_field_extraction(qr["tool_args"], case["expected_fields"])
        coverage = len(extracted) / len(case["expected_fields"]) if case["expected_fields"] else 0.0

        result = FieldExtractionResult(
            case_id=case["id"],
            input_text=case["input"],
            expected_fields=case["expected_fields"],
            actual_args=qr["tool_args"],
            extracted_fields=extracted,
            missing_fields=missing,
            field_coverage=coverage,
            root_cause=case["root_cause"],
            routing_source=qr["tool_source"],
            tool_name=qr["tool_name"],
            elapsed_ms=qr["elapsed_ms"],
        )
        results.append(result)

        status = "  " if coverage >= 1.0 else "  " if coverage > 0 else "  "
        print(f"    工具: {qr['tool_name']} (src={qr['tool_source']})")
        print(f"    提取: {extracted}")
        print(f"    缺失: {missing}")
        print(f"    覆盖率: {coverage:.0%} {status}")
        print(f"    完整 args: {json.dumps(qr['tool_args'], ensure_ascii=False)[:200]}")

    return results


def run_multi_turn_diagnostics() -> List[MultiTurnResult]:
    """B. 多轮对话上下文诊断"""
    results = []
    print("\n" + "=" * 80)
    print("  B. 多轮对话上下文诊断")
    print("=" * 80)

    for seq in MULTI_TURN_SEQUENCES:
        print(f"\n  [{seq['id']}] {seq['name']}")
        mtr = MultiTurnResult(
            case_id=seq["id"],
            name=seq["name"],
            session_id=seq["session_id"],
            steps=seq["steps"],
            root_cause=seq["root_cause"],
        )

        prev_tool = ""
        prev_cards = []
        for i, step in enumerate(seq["steps"]):
            print(f"    Step {i+1}: \"{step['input']}\"")
            qr = run_single_query(seq["session_id"], step["input"])
            step_result = {
                "input": step["input"],
                "tool": qr["tool_name"],
                "source": qr["tool_source"],
                "cards": qr["product_count"],
                "brands": qr["product_brands"],
                "reply_preview": qr["text_response"][:100],
            }
            mtr.step_results.append(step_result)
            print(f"      -> 工具: {qr['tool_name']} (src={qr['tool_source']})")
            print(f"      -> 商品: {qr['product_count']} 个, 品牌: {qr['product_brands'][:3]}")
            print(f"      -> 回复: {qr['text_response'][:80]}...")

            # 检查上下文是否传递
            if i > 0 and "expect_behavior" in step:
                # 对比上一步的商品和当前步的商品
                current_cards = set(c.get("title", "") for c in qr.get("product_cards", []))
                prev_card_titles = set(c.get("title", "") for c in prev_cards)
                if current_cards and prev_card_titles:
                    overlap = current_cards & prev_card_titles
                    if "看看别的" in step["input"] and overlap:
                        step_result["context_issue"] = f"返回了与上轮相同的商品: {overlap}"
                    elif "续航" in step["input"] and not overlap:
                        # 可能正确（基于上文回答续航）或错误（推荐了新品）
                        step_result["context_note"] = "商品不同，可能是基于上文回答或推荐了新品"

            prev_tool = qr["tool_name"]
            prev_cards = qr.get("product_cards", [])

        results.append(mtr)

    return results


def run_degradation_diagnostics() -> List[DegradationResult]:
    """C. LLM 降级链路诊断"""
    results = []
    print("\n" + "=" * 80)
    print("  C. LLM 降级链路诊断")
    print("=" * 80)

    for case in DEGRADATION_CASES:
        print(f"\n  [{case['id']}] {case['input']}")
        qr = run_single_query(f"diag_dg_{case['id'].lower()}", case["input"])

        dr = DegradationResult(
            case_id=case["id"],
            input_text=case["input"],
            description=case["description"],
            tool_name=qr["tool_name"],
            product_count=qr["product_count"],
            product_brands=qr["product_brands"],
            text_response=qr["text_response"],
        )

        # 检查降级行为
        if case["id"] == "DG-01":
            # 华为品牌：至少应有 1 个华为
            huawei_count = sum(1 for b in dr.product_brands if "华为" in str(b))
            dr.degradation_ok = huawei_count > 0
            dr.check_detail = f"华为商品数: {huawei_count}/{dr.product_count}"
        elif case["id"] == "DG-02":
            # 不要Nike：不应有 Nike
            has_nike = any("Nike" in str(b) or "耐克" in str(b) for b in dr.product_brands)
            dr.degradation_ok = not has_nike
            dr.check_detail = f"包含Nike: {has_nike}, 品牌: {dr.product_brands}"
        elif case["id"] == "DG-03":
            # 无运动手表：0 cards 或回复中告知无此商品
            no_product_honest = dr.product_count == 0 or "没有" in dr.text_response or "暂无" in dr.text_response
            dr.degradation_ok = no_product_honest
            dr.check_detail = f"商品数: {dr.product_count}, 诚实告知: {no_product_honest}"
        elif case["id"] == "DG-04":
            # 无PS5：不应声称有 PS5
            claims_ps5 = "有" in dr.text_response and "PS5" in dr.text_response
            dr.degradation_ok = not claims_ps5
            dr.check_detail = f"声称有PS5: {claims_ps5}"

        status = "PASS" if dr.degradation_ok else "FAIL"
        print(f"    工具: {dr.tool_name}")
        print(f"    商品: {dr.product_count} 个, 品牌: {dr.product_brands[:3]}")
        print(f"    检查: {dr.check_detail}")
        print(f"    结果: {status}")

        results.append(dr)

    return results


# ============================================================================
# 报告生成
# ============================================================================

def generate_diagnostic_report(
    field_results: List[FieldExtractionResult],
    mt_results: List[MultiTurnResult],
    deg_results: List[DegradationResult],
) -> str:
    """生成完整诊断报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# LLM Fallback 诊断报告",
        "",
        f"测试时间：{now}",
        f"服务器：{BASE_URL}",
        "",
        "---",
        "",
        "## 一、LLM 字段提取覆盖率",
        "",
        "| # | 输入 | 预期字段 | 提取 | 缺失 | 覆盖率 | 工具 | 来源 |",
        "|---|------|---------|------|------|--------|------|------|",
    ]

    total_expected = 0
    total_extracted = 0
    for r in field_results:
        total_expected += len(r.expected_fields)
        total_extracted += len(r.extracted_fields)
        coverage_str = f"{r.field_coverage:.0%}"
        extracted_str = ", ".join(r.extracted_fields) if r.extracted_fields else "-"
        missing_str = ", ".join(r.missing_fields) if r.missing_fields else "-"
        lines.append(
            f"| {r.case_id} | {r.input_text[:25]} | {', '.join(r.expected_fields)} | "
            f"{extracted_str} | {missing_str} | {coverage_str} | {r.tool_name} | {r.routing_source} |"
        )

    overall_coverage = total_extracted / total_expected if total_expected > 0 else 0
    lines.extend([
        "",
        f"**总体字段覆盖率: {overall_coverage:.1%} ({total_extracted}/{total_expected})**",
        "",
        "> 字段覆盖率反映 LLM 的参数提取能力。7B 模型通常只能稳定输出 name/query/confidence/reason，",
        "> brands/sort_order/price_min 等字段的覆盖率取决于 prompt 质量和模型能力。",
        "",
        "## 二、LLM 原始输出（arguments 详情）",
        "",
    ])

    for r in field_results:
        lines.append(f"### {r.case_id}: {r.input_text}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(r.actual_args, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")

    lines.extend([
        "## 三、多轮对话上下文诊断",
        "",
        "| # | 场景 | Step 1 工具 | Step 2 工具 | 上下文传递 |",
        "|---|------|-----------|-----------|-----------|",
    ])

    for mt in mt_results:
        s1_tool = mt.step_results[0]["tool"] if mt.step_results else "?"
        s2_tool = mt.step_results[1]["tool"] if len(mt.step_results) > 1 else "N/A"
        context_note = ""
        if len(mt.step_results) > 1:
            s2 = mt.step_results[1]
            if "context_issue" in s2:
                context_note = f"FAIL: {s2['context_issue']}"
            elif "context_note" in s2:
                context_note = f"NOTE: {s2['context_note']}"
            else:
                context_note = "待人工确认"
        else:
            context_note = "单步"
        lines.append(f"| {mt.case_id} | {mt.name[:25]} | {s1_tool} | {s2_tool} | {context_note} |")

    lines.extend([
        "",
        "## 四、LLM 降级链路诊断",
        "",
        "| # | 输入 | 检查项 | 结果 | 详情 |",
        "|---|------|--------|------|------|",
    ])

    for dr in deg_results:
        status = "PASS" if dr.degradation_ok else "FAIL"
        lines.append(f"| {dr.case_id} | {dr.input_text[:20]} | {dr.description[:30]} | {status} | {dr.check_detail} |")

    lines.extend([
        "",
        "## 五、根因分类汇总",
        "",
        "| 根因类型 | 数量 | 说明 |",
        "|---------|------|------|",
    ])

    cause_counts: Dict[str, int] = {}
    for r in field_results:
        cause_counts[r.root_cause] = cause_counts.get(r.root_cause, 0) + 1
    for mt in mt_results:
        cause_counts[mt.root_cause] = cause_counts.get(mt.root_cause, 0) + 1

    for cause, count in sorted(cause_counts.items(), key=lambda x: -x[1]):
        desc = ROOT_CAUSE_LABELS.get(cause, cause)
        lines.append(f"| {cause} | {count} | {desc} |")

    lines.extend([
        "",
        "## 六、改进方向",
        "",
        "### 6.1 LLM_CAPABILITY（7B 模型字段提取上限）",
        "",
        "当前 sensenova-6.7-flash-lite 只能稳定输出 4 个基础字段。",
        "改进方向（不修改后端）：",
        "1. **Prompt 优化**：简化 prompt，减少字段数量，增加 few-shot 示例",
        "2. **两阶段策略**：第一阶段仅做工具选择（5 分类），第二阶段用更大模型做参数提取",
        "3. **模型升级**：替换为 sensenova-12b 或更大模型",
        "",
        "### 6.2 CONTEXT_MISSING（多轮上下文未注入）",
        "",
        "改进方向（不修改后端）：",
        "1. **测试侧**：在测试中注入上下文（如先发推荐请求再发追问），验证 session 是否正确传递",
        "2. **诊断**：记录 session 的 topic_memory 和 last_result，确认数据是否到达 LLM prompt",
        "",
        "### 6.3 测试基础设施改进",
        "",
        "1. **字段覆盖率指标**：每次 prompt 修改后重新运行字段提取诊断，量化改进效果",
        "2. **根因分类标签**：区分 LLM 能力不足 和 系统 bug，避免误判",
        "3. **LLM 原始输出收集**：记录每个 case 的完整 arguments，为 prompt 优化提供数据",
    ])

    return "\n".join(lines)


# ============================================================================
# 主入口
# ============================================================================

def main():
    print("\n" + "=" * 80)
    print("  LLM Fallback 诊断测试")
    print(f"  服务器: {BASE_URL}")
    print("=" * 80)

    # 检查服务器
    import urllib.request
    try:
        req = urllib.request.Request(f"{BASE_URL}/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            print("\n  服务器在线")
    except Exception as exc:
        print(f"\n  警告: 服务器不可达 ({exc})")

    # 运行三类诊断
    field_results = run_field_extraction_diagnostics()
    mt_results = run_multi_turn_diagnostics()
    deg_results = run_degradation_diagnostics()

    # 生成报告
    report_md = generate_diagnostic_report(field_results, mt_results, deg_results)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / "llm_fallback_diagnostics.md"
    report_path.write_text(report_md, encoding="utf-8")

    # 打印汇总
    total_fields = sum(len(r.expected_fields) for r in field_results)
    extracted_fields = sum(len(r.extracted_fields) for r in field_results)
    coverage = extracted_fields / total_fields if total_fields > 0 else 0
    deg_pass = sum(1 for d in deg_results if d.degradation_ok)

    print("\n" + "=" * 80)
    print(f"  诊断完成")
    print(f"  字段覆盖率: {coverage:.1%} ({extracted_fields}/{total_fields})")
    print(f"  降级链路: {deg_pass}/{len(deg_results)} PASS")
    print(f"  报告: {report_path}")
    print("=" * 80)


if __name__ == "__main__":
    main()
