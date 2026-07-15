"""test_agent_v1.py – MallMind 交互式 Agent 测试脚本 (60 用例)

用法:
  1. 先启动后端:  python scripts/run_recommendation_api.py
  2. 再运行本脚本:  python tests/test_agent_v1.py

脚本会逐个发送测试用例，解析 SSE 流式响应，记录工具调用链和最终回复，
最后生成 Markdown 测试报告到 `.pytest_tmp/` 目录。

核心原则：QoderWork 自己阅读输出、理解语义、判断每个 case 是否通过。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback

# Windows GBK 控制台兼容：强制 stdout 使用 UTF-8
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

BASE_URL = os.getenv("MALLMIND_TEST_BASE_URL", "http://127.0.0.1:8000")
CHAT_STREAM_URL = f"{BASE_URL}/api/chat/stream"
HEALTH_URL = f"{BASE_URL}/api/health"
TIMEOUT_SECONDS = 120
REPORT_DIR = Path(__file__).resolve().parents[1] / ".pytest_tmp"
RAW_DIR = REPORT_DIR / "agent_eval_raw"

# ---------------------------------------------------------------------------
# 测试用例定义
# ---------------------------------------------------------------------------

@dataclass
class TestCase:
    id: int
    category: str
    input_text: str
    expected_behavior: str
    session_id: Optional[str] = None  # 多轮共享
    depends_on: Optional[int] = None  # 依赖哪个用例的 session


@dataclass
class TestResult:
    case: TestCase
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    runtime_mode: str = ""
    routing_trace: Dict[str, Any] = field(default_factory=dict)
    text_response: str = ""
    product_cards: List[Dict[str, Any]] = field(default_factory=list)
    comparison_table: Optional[Dict[str, Any]] = None
    cart: Optional[Dict[str, Any]] = None
    pc_build_plan: Optional[Dict[str, Any]] = None
    events_received: List[str] = field(default_factory=list)
    error: str = ""
    elapsed_ms: int = 0
    raw_events: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def tool_names(self) -> List[str]:
        return [tc.get("name", "?") for tc in self.tool_calls]

    @property
    def tool_chain_str(self) -> str:
        if not self.tool_calls:
            return "无工具调用"
        parts = []
        for tc in self.tool_calls:
            name = tc.get("name", "?")
            args = tc.get("arguments") or {}
            conf = tc.get("confidence")
            src = tc.get("source", "?")
            arg_str = ", ".join(f"{k}={v}" for k, v in list(args.items())[:4])
            parts.append(f"{name}({arg_str}) [conf={conf}, src={src}]")
        return " → ".join(parts)


# ── A. 基础对话（5个）──
CASES_A = [
    TestCase(1,  "基础对话", "你好",                       "不调工具，友好问候+自我介绍"),
    TestCase(2,  "基础对话", "你是谁",                     "不调工具，介绍自己是智能导购"),
    TestCase(3,  "基础对话", "帮我写一段代码",             "不调工具，礼貌拒绝+引导购物"),
    TestCase(4,  "基础对话", "今天天气怎么样",             "不调工具，委婉提示只解答购物问题"),
    TestCase(5,  "基础对话", "谢谢",                       "不调工具，友好回应"),
]

# ── B. 模糊推荐（8个）──
CASES_B = [
    TestCase(6,  "模糊推荐", "推荐一款手机",               "recommend_shopping_products → 手机+CARD"),
    TestCase(7,  "模糊推荐", "有没有好吃的零食",           "recommend_shopping_products → 食品类+CARD"),
    TestCase(8,  "模糊推荐", "推荐一款适合学生的笔记本",   "recommend_shopping_products → 笔记本+CARD"),
    TestCase(9,  "模糊推荐", "有没有防水的运动鞋",         "recommend_shopping_products → 运动鞋+CARD"),
    TestCase(10, "模糊推荐", "推荐送女朋友的礼物",         "recommend_shopping_products → 护肤品/美妆+CARD"),
    TestCase(11, "模糊推荐", "夏天穿什么好",               "recommend_shopping_products → 服饰类+CARD"),
    TestCase(12, "模糊推荐", "推荐一款蓝牙耳机",           "recommend_shopping_products → 耳机+CARD"),
    TestCase(13, "模糊推荐", "有没有适合办公的电脑",       "recommend_shopping_products → 笔记本+CARD"),
]

# ── C. 精准搜索（8个）──
CASES_C = [
    TestCase(14, "精准搜索", "8000以下的手机",             "recommend_shopping_products(max_price=8000)"),
    TestCase(15, "精准搜索", "推荐华为的手机",             "recommend_shopping_products(brand=华为)"),
    TestCase(16, "精准搜索", "500元以下的零食",            "recommend_shopping_products(max_price=500, 食品类)"),
    TestCase(17, "精准搜索", "所有数码电子类商品",         "recommend_shopping_products(category=数码电子)"),
    TestCase(18, "精准搜索", "按价格从低到高排列手机",     "recommend_shopping_products → 手机列表"),
    TestCase(19, "精准搜索", "苹果手机有哪些",             "recommend_shopping_products(brand=Apple 苹果)"),
    TestCase(20, "精准搜索", "最便宜的手机",               "recommend_shopping_products → 手机按价格排序"),
    TestCase(21, "精准搜索", "有没有2000到5000的护肤品",   "recommend_shopping_products(min_price=2000,max_price=5000)"),
]

# ── D. 否定语义（5个）──
CASES_D = [
    TestCase(22, "否定语义", "不要苹果的手机",             "exclude_brands=[Apple 苹果]，结果无iPhone"),
    TestCase(23, "否定语义", "除了华为还有啥手机",         "exclude_brands=[华为]"),
    TestCase(24, "否定语义", "推荐手机",                   "多轮第1步：推荐手机", session_id="sess_d24"),
    TestCase(25, "否定语义", "不要超过3000的耳机",         "max_price=3000, 耳机类"),
    TestCase(26, "否定语义", "推荐零食，不要辣的",         "搜索零食并排除辣味相关"),
]

# 24 的第二轮
CASE_D24_FOLLOWUP = TestCase(
    242, "否定语义", "不要苹果的",
    "追问过滤，重新搜索排除苹果",
    session_id="sess_d24", depends_on=24,
)

# ── E. 商品详情和FAQ（5个）──
CASES_E = [
    TestCase(27, "商品FAQ", "iPhone续航怎么样",            "recommend_shopping_products → iPhone相关+FAQ"),
    TestCase(28, "商品FAQ", "这款手机防水吗",              "recommend_shopping_products → 手机信息"),
    TestCase(29, "商品FAQ", "A19芯片比上一代提升多少",     "recommend_shopping_products → iPhone信息"),
    TestCase(30, "商品FAQ", "这个手机的屏幕多大",          "recommend_shopping_products → 手机规格"),
    TestCase(31, "商品FAQ", "MacBook有几个配置",           "recommend_shopping_products → MacBook SKU"),
]

# ── F. 口碑查询（3个）──
CASES_F = [
    TestCase(32, "口碑查询", "这款手机口碑怎么样",          "recommend_shopping_products → 手机评价"),
    TestCase(33, "口碑查询", "有没有人说拍照好",            "recommend_shopping_products → 评价关键词"),
    TestCase(34, "口碑查询", "差评多吗",                    "recommend_shopping_products → 评价分析"),
]

# ── G. 商品对比（3个）──
CASES_G = [
    TestCase(35, "商品对比", "iPhone 17 Pro和Pro Max对比",  "compare_products → 对比表"),
    TestCase(36, "商品对比", "华为Pura 90和iPhone 17哪个好", "compare_products → 对比+推荐"),
    TestCase(37, "商品对比", "这两款笔记本哪个更适合学生",   "对比分析+场景推荐"),
]

# ── H. 购物车操作（8个）──
CASES_H = [
    TestCase(38, "购物车", "推荐一款手机，帮我加到购物车",  "recommend → cart", session_id="sess_h38"),
    TestCase(39, "购物车", "买这个iPhone，256G宇宙橙",      "匹配SKU → add_to_cart", session_id="sess_h39"),
    TestCase(40, "购物车", "买iPhone",                      "应追问颜色和存储版本", session_id="sess_h40"),
    TestCase(41, "购物车", "看看购物车",                     "view_cart", session_id="sess_h38"),
    TestCase(42, "购物车", "改成2台",                        "update_cart_quantity", session_id="sess_h38"),
    TestCase(43, "购物车", "不要第一个了",                   "remove_from_cart", session_id="sess_h38"),
    TestCase(44, "购物车", "清空购物车",                     "clear_cart", session_id="sess_h38"),
    TestCase(45, "购物车", "推荐蓝牙耳机然后把第一个加到购物车", "recommend → add_to_cart", session_id="sess_h45"),
]

# ── I. 结算（3个）──
CASES_I = [
    TestCase(46, "结算", "推荐一款零食然后加到购物车",      "recommend → cart → 准备结算", session_id="sess_i46"),
    TestCase(47, "结算", "结账",                             "view_cart → 汇总", session_id="sess_i46"),
    TestCase(48, "结算", "结账",                             "购物车空时提示", session_id="sess_i48_empty"),
]

# ── J. 多轮对话（5个）──
CASES_J = [
    TestCase(49, "多轮对话", "推荐一款手机",                "第1轮", session_id="sess_j49"),
    TestCase(50, "多轮对话", "续航呢",                      "理解上下文是手机的续航", session_id="sess_j49"),
    TestCase(51, "多轮对话", "有没有更便宜的",              "理解上下文是手机，加价格过滤", session_id="sess_j49"),
    TestCase(52, "多轮对话", "推荐一款手机",                "第1轮(新session)", session_id="sess_j52"),
    TestCase(53, "多轮对话", "给我看看零食",                "话题切换", session_id="sess_j52"),
]
CASE_J52_FOLLOWUPS = [
    TestCase(521, "多轮对话", "不要苹果的",                 "排除苹果", session_id="sess_j52", depends_on=52),
    TestCase(522, "多轮对话", "那华为的呢",                 "推荐华为", session_id="sess_j52", depends_on=521),
]

# ── K. 边界和异常（5个）──
CASES_K = [
    TestCase(54, "边界异常", "推荐500元以下的手机",          "过滤后可能无结果→诚实回答"),
    TestCase(55, "边界异常", "删除购物车里的iPhone",         "购物车里没有→回复没有"),
    TestCase(56, "边界异常", "对比手机和洗面奶",             "提示跨品类无法对比"),
    TestCase(57, "边界异常", "。。。",                       "友好提示请输入问题"),
    TestCase(58, "边界异常", "",                             "空消息→提示"),
]

# ── L. 组合工具调用（2个）──
CASES_L = [
    TestCase(59, "组合调用", "推荐手机，直接帮我加到购物车",  "recommend → add_to_cart", session_id="sess_l59"),
    TestCase(60, "组合调用", "看看购物车，把第一个删了",       "view_cart → remove", session_id="sess_l59"),
]

# 完整用例列表（按顺序）
ALL_CASES: List[TestCase] = (
    CASES_A + CASES_B + CASES_C + CASES_D + [CASE_D24_FOLLOWUP]
    + CASES_E + CASES_F + CASES_G + CASES_H + CASES_I
    + CASES_J + CASE_J52_FOLLOWUPS + CASES_K + CASES_L
)

# ---------------------------------------------------------------------------
# HTTP + SSE 工具
# ---------------------------------------------------------------------------

def check_server_health() -> bool:
    """检查服务器是否在线"""
    try:
        import urllib.request
        with urllib.request.urlopen(HEALTH_URL, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            print(f"  服务器在线: {data.get('status', '?')}")
            return data.get("status") == "ok"
    except Exception as exc:
        print(f"  服务器不可达: {exc}")
        return False


def send_chat_stream(message: str, session_id: str = "") -> Dict[str, Any]:
    """发送 POST /api/chat/stream 并解析 SSE 事件"""
    import urllib.request

    payload = {
        "message": message,
        "session_id": session_id or "",
        "attachments": [],
        "images": [],
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        CHAT_STREAM_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    events = []
    raw_lines = []
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        buffer = ""
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace")
            raw_lines.append(line)
            buffer += line
            # SSE 事件以 \n\n 分隔
            while "\n\n" in buffer:
                event_text, buffer = buffer.split("\n\n", 1)
                event = _parse_sse_event(event_text)
                if event:
                    events.append(event)
        # 处理最后一块
        if buffer.strip():
            event = _parse_sse_event(buffer)
            if event:
                events.append(event)

    return {"events": events, "raw_lines": raw_lines}


def _parse_sse_event(text: str) -> Optional[Dict[str, Any]]:
    """解析单个 SSE 事件文本"""
    event_type = ""
    data_lines = []
    for line in text.strip().split("\n"):
        if line.startswith("event:"):
            event_type = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
    if not event_type and not data_lines:
        return None
    data_str = "\n".join(data_lines)
    try:
        data = json.loads(data_str) if data_str else {}
    except json.JSONDecodeError:
        data = {"_raw": data_str}
    return {"event": event_type, "data": data}


# ---------------------------------------------------------------------------
# 测试执行器
# ---------------------------------------------------------------------------

def run_test_case(case: TestCase) -> TestResult:
    """执行单个测试用例并解析结果"""
    result = TestResult(case=case)
    t0 = time.perf_counter()

    # 独立用例（无 session_id 且不依赖其他用例）使用唯一 session，避免共享 "default" 导致历史污染
    if case.session_id:
        sid = case.session_id
    elif case.depends_on is not None:
        sid = f"test_dep_{case.depends_on}"
    else:
        sid = f"test_case_{case.id}"

    try:
        response = send_chat_stream(case.input_text, session_id=sid)
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
        elif etype == "comparison_table":
            result.comparison_table = edata
        elif etype == "cart":
            # 购物车事件数据可能嵌套: {"cart": {"items": [...]}} 或直接 {"items": [...]}
            result.cart = edata.get("cart") if "cart" in edata else edata
        elif etype == "pc_build_plan":
            result.pc_build_plan = edata
        elif etype == "result":
            # 补充 result 事件中的信息
            if not result.text_response and edata.get("text"):
                result.text_response = edata["text"]
        elif etype == "validation_error":
            result.text_response += f"[验证错误] {edata.get('label', '')}: {edata.get('detail', '')}"
        elif etype == "done":
            pass  # 流结束

    result.elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return result


def run_all_tests(cases: List[TestCase], *, limit: int = 0) -> List[TestResult]:
    """逐个执行所有测试用例"""
    results: List[TestResult] = []
    total = len(cases) if limit <= 0 else min(limit, len(cases))

    print(f"\n{'='*70}")
    print(f"  MallMind Agent v1 测试 – 共 {total} 个用例")
    print(f"  服务器: {BASE_URL}")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")

    for i, case in enumerate(cases[:total]):
        tag = f"[{i+1}/{total}]"
        print(f"{tag} #{case.id} [{case.category}] \"{case.input_text}\"")
        print(f"    预期: {case.expected_behavior}")

        result = run_test_case(case)
        results.append(result)

        # 打印摘要
        status = "ERR" if result.error else "OK"
        print(f"    → {status} | 模式={result.runtime_mode} | 工具={result.tool_chain_str}")
        if result.product_cards:
            card_ids = [c.get("product_id", "?") for c in result.product_cards[:5]]
            print(f"    → 商品卡片: {card_ids}")
        if result.comparison_table:
            print(f"    → 对比表: {list((result.comparison_table or {}).keys())[:3]}")
        if result.cart:
            items = (result.cart or {}).get("items", [])
            print(f"    → 购物车: {len(items)} 件商品")
        resp_preview = result.text_response[:120].replace("\n", " ")
        print(f"    → 回复: {resp_preview}...")
        print(f"    → 耗时: {result.elapsed_ms}ms | 事件: {result.events_received}")
        if result.error:
            print(f"    → ❌ 错误: {result.error}")
        print()

        # 保存原始数据
        _save_raw_event(case, result)

    return results


def _save_raw_event(case: TestCase, result: TestResult):
    """保存原始 SSE 事件到文件"""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / f"case_{case.id:03d}.json"
    payload = {
        "case_id": case.id,
        "input": case.input_text,
        "expected": case.expected_behavior,
        "session_id": case.session_id,
        "runtime_mode": result.runtime_mode,
        "tool_calls": result.tool_calls,
        "text_response": result.text_response,
        "product_card_count": len(result.product_cards),
        "events_received": result.events_received,
        "error": result.error,
        "elapsed_ms": result.elapsed_ms,
        "raw_events": result.raw_events,
    }
    raw_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------

def generate_report(results: List[TestResult]) -> str:
    """生成 Markdown 测试报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    model = os.getenv("MALLMIND_LLM_MODEL", "unknown")

    lines = [
        f"# MallMind Agent v1 测试报告",
        f"",
        f"## 测试时间：{now}",
        f"## 使用模型：{model}",
        f"## 服务器：{BASE_URL}",
        f"## 总用例数：{len(results)}",
        f"",
        f"## 结果汇总",
        f"",
        f"| # | 类别 | 输入 | 工具调用链 | 商品卡数 | 耗时ms | 事件流 | 回复摘要 |",
        f"|---|------|------|-----------|---------|--------|--------|---------|",
    ]

    for r in results:
        inp = r.case.input_text[:30] + ("..." if len(r.case.input_text) > 30 else "")
        chain = r.tool_chain_str[:60] + ("..." if len(r.tool_chain_str) > 60 else "")
        resp = r.text_response[:50].replace("\n", " ").replace("|", "\\|")
        resp += "..." if len(r.text_response) > 50 else ""
        evts = ",".join(r.events_received[:6])
        lines.append(
            f"| {r.case.id} | {r.case.category} | {inp} | {chain} "
            f"| {len(r.product_cards)} | {r.elapsed_ms} | {evts} | {resp} |"
        )

    lines.append("")
    lines.append("## 详细分析")
    lines.append("")

    for r in results:
        lines.append(f"### #{r.case.id} [{r.case.category}] \"{r.case.input_text}\"")
        lines.append(f"")
        lines.append(f"- **预期**: {r.case.expected_behavior}")
        lines.append(f"- **运行时模式**: {r.runtime_mode}")
        lines.append(f"- **路由决策**: {json.dumps(r.routing_trace.get('runtime_mode', ''), ensure_ascii=False)}")
        lines.append(f"- **工具调用链**: {r.tool_chain_str}")
        if r.product_cards:
            card_titles = [c.get("title", c.get("product_id", "?"))[:40] for c in r.product_cards]
            lines.append(f"- **商品卡片** ({len(r.product_cards)}): {card_titles}")
        if r.comparison_table:
            lines.append(f"- **对比表**: 已生成")
        if r.cart:
            items = (r.cart or {}).get("items", [])
            lines.append(f"- **购物车**: {len(items)} 件")
        lines.append(f"- **回复全文**:")
        lines.append(f"  > {r.text_response[:300].replace(chr(10), ' ')}")
        lines.append(f"- **耗时**: {r.elapsed_ms}ms")
        if r.error:
            lines.append(f"- **错误**: {r.error}")
        lines.append(f"")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="MallMind Agent v1 测试脚本")
    parser.add_argument("--limit", type=int, default=0, help="只运行前 N 个用例")
    parser.add_argument("--case-ids", type=str, default="", help="只运行指定用例ID (逗号分隔)")
    parser.add_argument("--base-url", type=str, default="", help="覆盖服务器地址")
    parser.add_argument("--report-only", action="store_true", help="只从 raw 数据重新生成报告")
    args = parser.parse_args()

    global BASE_URL, CHAT_STREAM_URL, HEALTH_URL
    if args.base_url:
        BASE_URL = args.base_url
        CHAT_STREAM_URL = f"{BASE_URL}/api/chat/stream"
        HEALTH_URL = f"{BASE_URL}/api/health"

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    # 检查服务器
    print("检查服务器连通性...")
    if not check_server_health():
        print("\n服务器不可达！请先启动后端：")
        print("  python scripts/run_recommendation_api.py")
        sys.exit(1)

    # 选择用例
    cases = ALL_CASES
    if args.case_ids:
        ids = set(int(x.strip()) for x in args.case_ids.split(","))
        cases = [c for c in cases if c.id in ids]
    if args.limit > 0:
        cases = cases[:args.limit]

    print(f"将运行 {len(cases)} 个用例\n")

    # 执行测试
    results = run_all_tests(cases, limit=args.limit)

    # 生成报告
    report_md = generate_report(results)
    report_path = REPORT_DIR / "agent_eval.md"
    report_path.write_text(report_md, encoding="utf-8")
    print(f"\n报告已生成: {report_path}")

    # 统计
    errors = sum(1 for r in results if r.error)
    no_tools = sum(1 for r in results if not r.tool_calls and not r.error)
    with_cards = sum(1 for r in results if r.product_cards)
    total_ms = sum(r.elapsed_ms for r in results)
    avg_ms = total_ms / len(results) if results else 0

    print(f"\n{'='*70}")
    print(f"  统计:")
    print(f"    总用例: {len(results)}")
    print(f"    HTTP错误: {errors}")
    print(f"    无工具调用: {no_tools}")
    print(f"    有商品卡片: {with_cards}")
    print(f"    平均耗时: {avg_ms:.0f}ms")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
