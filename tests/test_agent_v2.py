"""test_agent_v2.py – MallMind Agent v2 扩展测试 (72 用例)

基于 test_agent_v1.py 基础设施，覆盖附件中的全量新场景。
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

from pathlib import Path
from datetime import datetime
from typing import List

# 复用 v1 的基础设施
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_agent_v1 import (
    TestCase, TestResult, send_chat_stream, _parse_sse_event, _save_raw_event,
    BASE_URL, TIMEOUT_SECONDS,
)

# 覆盖报告目录
REPORT_DIR = Path(__file__).resolve().parents[1] / ".pytest_tmp"
RAW_DIR = REPORT_DIR / "agent_eval_raw"

# ======================================================================
# 测试用例定义 (72 cases, IDs 101-172)
# ======================================================================

# ── A. 基础对话与边界 (#101-#108) ──
CASES_A = [
    TestCase(101, "基础对话", "你好",                       "不调工具，友好问候+自我介绍"),
    TestCase(102, "基础对话", "你是谁？能做什么？",         "不调工具，详细介绍身份和功能"),
    TestCase(103, "基础对话", "帮我写一首诗",               "不调工具，礼貌拒绝非购物请求"),
    TestCase(104, "基础对话", "你觉得美国总统大选怎么样？", "不调工具，礼貌拒绝政治话题"),
    TestCase(105, "基础对话", "用Python写一个爬虫",         "不调工具，拒绝非购物请求"),
    TestCase(106, "基础对话", "你们有什么商品分类？",       "介绍商品分类信息"),
    TestCase(107, "基础对话", "有哪些品牌？",               "介绍品牌信息"),
    TestCase(108, "基础对话", "谢谢你",                     "不调工具，礼貌回应"),
]

# ── B. 语义搜索商品 (#109-#120) ──
CASES_B = [
    TestCase(109, "语义搜索", "推荐一款好用的洗面奶",           "recommend_shopping_products → 洗面奶+CARD"),
    TestCase(110, "语义搜索", "有没有适合学生用的笔记本电脑", "recommend_shopping_products → 笔记本+CARD"),
    TestCase(111, "语义搜索", "推荐一双跑步鞋",               "recommend_shopping_products → 跑鞋+CARD"),
    TestCase(112, "语义搜索", "有什么好吃的零食推荐吗",       "recommend_shopping_products → 零食+CARD"),
    TestCase(113, "语义搜索", "我想买个降噪耳机",             "recommend_shopping_products → 降噪耳机+CARD"),
    TestCase(114, "语义搜索", "有没有防水的运动手表",         "recommend → 告知无运动手表或推荐替代"),
    TestCase(115, "语义搜索", "推荐一款性价比高的手机",       "recommend_shopping_products → 手机+CARD"),
    TestCase(116, "语义搜索", "送礼给女朋友",                 "recommend_shopping_products → 跨品类礼物+CARD"),
    TestCase(117, "语义搜索", "夏天穿什么衣服比较凉快",       "recommend_shopping_products → 夏季服饰+CARD"),
    TestCase(118, "语义搜索", "有没有适合敏感肌的护肤品",     "recommend_shopping_products → 敏感肌护肤+CARD"),
    TestCase(119, "语义搜索", "推荐一款续航好的手机",         "recommend_shopping_products → 长续航手机+CARD"),
    TestCase(120, "语义搜索", "有没有好看的裙子",             "recommend → 告知无裙装或推荐替代"),
]

# ── C. 结构化查询 (#121-#128) ──
CASES_C = [
    TestCase(121, "结构化查询", "给我看看所有数码电子类商品",   "recommend(category=数码电子)+CARD"),
    TestCase(122, "结构化查询", "500元以下的商品有哪些",         "recommend(budget=500)+CARD"),
    TestCase(123, "结构化查询", "所有商品按价格从低到高排列",   "recommend → 价格排序列表"),
    TestCase(124, "结构化查询", "华为品牌的商品有哪些",         "recommend(brand=华为)+CARD"),
    TestCase(125, "结构化查询", "3000到5000之间的手机",         "recommend(budget=5000, 手机)+CARD"),
    TestCase(126, "结构化查询", "第二页的商品",                 "追问是哪个品类的第二页"),
    TestCase(127, "结构化查询", "美妆护肤类有哪些品牌",         "介绍美妆品牌信息"),
    TestCase(128, "结构化查询", "最贵的商品是什么",             "recommend → 高价商品"),
]

# ── D. 商品详情 (#129-#133) ──
CASES_D = [
    TestCase(129, "商品详情", "iPhone 17 Pro 有什么颜色可以选？", "recommend → iPhone SKU/颜色信息"),
    TestCase(130, "商品详情", "华为Pura 90 Pro 的详细信息",       "recommend → 华为Pura 90 Pro详情"),
    TestCase(131, "商品详情", "小米17 Ultra 有几个版本？",         "recommend → 小米17 Ultra版本信息"),
    TestCase(132, "商品详情", "OPPO Find X9 Ultra 拍照怎么样",    "recommend → OPPO拍照评价"),
    TestCase(133, "商品详情", "AirPods Pro 3 支持心率监测吗",     "recommend → AirPods功能信息"),
]

# ── E. FAQ搜索 (#134-#138) ──
CASES_E = [
    TestCase(134, "FAQ搜索", "iPhone 17 Pro 的电池续航怎么样",      "recommend → iPhone续航FAQ"),
    TestCase(135, "FAQ搜索", "华为 FreeBuds Pro 5 降噪效果好不好",  "recommend → FreeBuds降噪评价"),
    TestCase(136, "FAQ搜索", "这个面膜敏感肌能用吗",                 "recommend → 面膜敏感肌信息"),
    TestCase(137, "FAQ搜索", "折叠屏手机耐用吗",                     "recommend → 折叠屏耐用性FAQ"),
    TestCase(138, "FAQ搜索", "运动跑鞋怎么选择尺码",                 "recommend → 跑鞋尺码建议"),
]

# ── F. 评价搜索 (#139-#143) ──
CASES_F = [
    TestCase(139, "评价搜索", "哪个手机好评最多",                    "recommend → 好评手机"),
    TestCase(140, "评价搜索", "有没有人说 iPhone 17 Pro 拍照好",     "recommend → iPhone拍照评价"),
    TestCase(141, "评价搜索", "这款耳机有差评吗",                    "general_chat: 追问是哪款耳机（无上下文）"),
    TestCase(142, "评价搜索", "大家觉得华为手机怎么样",              "recommend → 华为评价"),
    TestCase(143, "评价搜索", "小米手机发热严重吗",                  "recommend → 小米发热评价"),
]

# ── G. 否定语义/排除 (#144-#147) ──
CASES_G = [
    TestCase(144, "否定排除", "推荐手机，但不要苹果的",             "recommend(exclude=苹果, 手机)+CARD"),
    TestCase(145, "否定排除", "推荐护肤品，不要兰蔻",               "recommend(exclude=兰蔻, 护肤)+CARD"),
    TestCase(146, "否定排除", "看看运动鞋，不要Nike的",             "recommend(exclude=Nike, 运动鞋)+CARD"),
    TestCase(147, "否定排除", "推荐耳机，不要华为的，500到2000之间", "recommend(exclude=华为, 耳机, budget)+CARD"),
]

# ── H. 购物车操作 (#148-#155) ──
cart_sess = "v2_cart"
CASES_H = [
    TestCase(148, "购物车", "帮我把 iPhone 17 Pro 加到购物车",  "recommend → iPhone+CARD, 追问SKU", session_id=cart_sess),
    TestCase(149, "购物车", "我要买华为Pura 90 Pro，黑色的",    "recommend → 华为+CARD, 追问版本", session_id=cart_sess),
    TestCase(150, "购物车", "看看我的购物车",                    "apply_cart_instruction → view_cart", session_id=cart_sess),
    TestCase(151, "购物车", "把第一个去掉",                      "apply_cart_instruction → remove", session_id=cart_sess),
    TestCase(152, "购物车", "把华为耳机数量改成2",               "apply_cart_instruction → update", session_id=cart_sess),
    TestCase(153, "购物车", "清空购物车",                        "apply_cart_instruction → clear", session_id=cart_sess),
    TestCase(154, "购物车", "购物车里有什么",                    "apply_cart_instruction → view_cart", session_id=cart_sess),
    TestCase(155, "购物车", "加一双跑步鞋，要最便宜的",          "recommend → 跑步鞋+CARD", session_id=cart_sess),
]

# ── I. 多轮对话 (#156-#163) ──
mt_sess1 = "v2_mt1"  # #156-#157
mt_sess2 = "v2_mt2"  # #158
mt_sess3 = "v2_mt3"  # #159-#161
mt_sess4 = "v2_mt4"  # #162
mt_sess5 = "v2_mt5"  # #163
CASES_I = [
    TestCase(156, "多轮对话", "推荐一款手机",             "第1轮: recommend → 手机+CARD", session_id=mt_sess1),
    TestCase(157, "多轮对话", "续航怎么样",               "理解上下文=iPhone 17 Pro, 续航FAQ", session_id=mt_sess1),
    TestCase(158, "多轮对话", "有没有更便宜的",           "理解上下文=手机, 推荐更便宜手机", session_id=mt_sess1),
    TestCase(159, "多轮对话", "换零食看看吧",             "话题切换: recommend → 零食+CARD", session_id=mt_sess3),
    TestCase(160, "多轮对话", "第一个不错，帮我加购物车", "理解第一个=零食, 加购物车", session_id=mt_sess3),
    TestCase(161, "多轮对话", "还有别的推荐吗",           "继续推荐零食/护肤品", session_id=mt_sess3),
    TestCase(162, "多轮对话", "对比一下这两款耳机",       "compare_products → 耳机对比表"),
    TestCase(163, "多轮对话", "推荐一款手机",             "第1轮(新session)", session_id=mt_sess5),
]
# #163 后面跟 "都不要，看看别的" 需要额外加一个
CASES_I.append(
    TestCase(164, "多轮对话", "都不要，看看别的",         "理解不满意, 推荐其他手机", session_id=mt_sess5),
)

# ── J. 防幻觉测试 (#165-#169) ──
CASES_J = [
    TestCase(165, "防幻觉", "你们有卖 PS5 吗",                  "诚实告知没有PS5，不编造"),
    TestCase(166, "防幻觉", "iPhone 17 Pro 只要 999 对吧？",     "纠正价格：实际 8999 元"),
    TestCase(167, "防幻觉", "三星Galaxy S30怎么样",              "诚实告知没有该商品"),
    TestCase(168, "防幻觉", "有没有一百万以上的商品",            "诚实告知没有超高价商品"),
    TestCase(169, "防幻觉", "你们这个店叫什么名字？什么时候开业的？", "诚实回答不知道"),
]

# ── K. 复合/综合场景 (#170-#172) ──
CASES_K = [
    TestCase(170, "综合场景", "高端护肤品送妈妈，预算3000以内", "recommend(budget=3000, 护肤)+CARD"),
    TestCase(171, "综合场景", "手机+耳机，总共不超过1万",       "recommend → 手机+耳机组合方案"),
    TestCase(172, "综合场景", "有没有什么限时优惠活动？",       "诚实告知无法查询促销"),
    TestCase(173, "综合场景", "我想退货怎么办",                 "说明退货建议流程"),
]

ALL_CASES_V2 = (
    CASES_A + CASES_B + CASES_C + CASES_D + CASES_E +
    CASES_F + CASES_G + CASES_H + CASES_I + CASES_J + CASES_K
)


# ---------------------------------------------------------------------------
# 执行器
# ---------------------------------------------------------------------------

def run_test_case_v2(case: TestCase) -> TestResult:
    """执行单个测试用例"""
    result = TestResult(case=case)
    t0 = time.perf_counter()

    if case.session_id:
        sid = case.session_id
    elif case.depends_on is not None:
        sid = f"v2_dep_{case.depends_on}"
    else:
        sid = f"v2_case_{case.id}"

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
            result.cart = edata.get("cart") if "cart" in edata else edata
        elif etype == "pc_build_plan":
            result.pc_build_plan = edata
        elif etype == "result":
            if not result.text_response and edata.get("text"):
                result.text_response = edata["text"]
        elif etype == "validation_error":
            result.text_response += f"[验证错误] {edata.get('label', '')}: {edata.get('detail', '')}"
        elif etype == "done":
            pass

    result.elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return result


def run_all_v2(cases: List[TestCase], *, limit: int = 0) -> List[TestResult]:
    total = len(cases) if limit <= 0 else min(limit, len(cases))
    print(f"\n{'='*70}")
    print(f"  MallMind Agent v2 扩展测试 – 共 {total} 个用例")
    print(f"  服务器: {BASE_URL}")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")

    results: List[TestResult] = []
    for i, case in enumerate(cases[:total]):
        tag = f"[{i+1}/{total}]"
        print(f"{tag} #{case.id} [{case.category}] \"{case.input_text}\"")
        print(f"    预期: {case.expected_behavior}")

        result = run_test_case_v2(case)
        results.append(result)

        status = "ERR" if result.error else "OK"
        print(f"    -> {status} | 模式={result.runtime_mode} | 工具={result.tool_chain_str}")
        if result.product_cards:
            card_ids = [c.get("product_id", "?") for c in result.product_cards[:5]]
            print(f"    -> 商品卡片: {card_ids}")
        if result.comparison_table:
            print(f"    -> 对比表: {list((result.comparison_table or {}).keys())[:3]}")
        if result.cart:
            items = (result.cart or {}).get("items", [])
            print(f"    -> 购物车: {len(items)} 件商品")
        resp_preview = result.text_response[:120].replace("\n", " ")
        print(f"    -> 回复: {resp_preview}...")
        print(f"    -> 耗时: {result.elapsed_ms}ms | 事件: {result.events_received}")
        if result.error:
            print(f"    -> ERR: {result.error}")
        print()

        # 保存原始数据
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        raw_path = RAW_DIR / f"case_{case.id:03d}.json"
        payload = {
            "case_id": case.id, "input": case.input_text,
            "expected": case.expected_behavior, "session_id": case.session_id,
            "runtime_mode": result.runtime_mode, "tool_calls": result.tool_calls,
            "text_response": result.text_response,
            "product_card_count": len(result.product_cards),
            "events_received": result.events_received, "error": result.error,
            "elapsed_ms": result.elapsed_ms, "raw_events": result.raw_events,
        }
        raw_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return results


def generate_v2_report(results: List[TestResult]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    model = os.getenv("MALLMIND_LLM_MODEL", "unknown")
    lines = [
        f"# MallMind Agent v2 扩展测试报告", "",
        f"## 测试时间：{now}", f"## 使用模型：{model}",
        f"## 服务器：{BASE_URL}", f"## 总用例数：{len(results)}", "",
        "## 结果汇总", "",
        "| # | 类别 | 输入 | 工具调用链 | 商品卡数 | 耗时ms | 事件流 | 回复摘要 |",
        "|---|------|------|-----------|---------|--------|--------|---------|",
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
        lines.append("")
        lines.append(f"- **预期**: {r.case.expected_behavior}")
        lines.append(f"- **运行时模式**: {r.runtime_mode}")
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
        lines.append("")
    return "\n".join(lines)


def check_server_health() -> bool:
    import urllib.request
    try:
        req = urllib.request.Request(f"{BASE_URL}/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            ok = data.get("status") == "ok"
            if ok:
                print(f"  服务器在线: ok")
            return ok
    except Exception as exc:
        print(f"  服务器不可达: {exc}")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="MallMind Agent v2 扩展测试")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--case-ids", type=str, default="")
    parser.add_argument("--base-url", type=str, default="")
    args = parser.parse_args()

    if args.base_url:
        import test_agent_v1
        test_agent_v1.BASE_URL = args.base_url
        test_agent_v1.CHAT_STREAM_URL = f"{args.base_url}/api/chat/stream"

    print("检查服务器连通性...")
    if not check_server_health():
        print("服务器不可达！请先启动后端。")
        sys.exit(1)

    cases = ALL_CASES_V2
    if args.case_ids:
        ids = set(int(x.strip()) for x in args.case_ids.split(","))
        cases = [c for c in cases if c.id in ids]
    if args.limit > 0:
        cases = cases[:args.limit]

    print(f"将运行 {len(cases)} 个用例\n")
    results = run_all_v2(cases, limit=args.limit)

    report_md = generate_v2_report(results)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / "agent_eval.md"
    report_path.write_text(report_md, encoding="utf-8")
    print(f"\n报告已生成: {report_path}")

    errors = sum(1 for r in results if r.error)
    with_cards = sum(1 for r in results if r.product_cards)
    total_ms = sum(r.elapsed_ms for r in results)
    avg_ms = total_ms / len(results) if results else 0
    print(f"\n{'='*70}")
    print(f"  统计:")
    print(f"    总用例: {len(results)}")
    print(f"    HTTP错误: {errors}")
    print(f"    有商品卡片: {with_cards}")
    print(f"    平均耗时: {avg_ms:.0f}ms")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
