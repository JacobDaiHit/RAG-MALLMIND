"""test_agent_v1_supplement.py - MallMind Agent v1 补充测试用例 (72 用例)

来源：用户粘贴的外部测试记录，已适配 MallMind 工具集
（recommend_shopping_products / compare_products / apply_cart_instruction / general_chat）

用法:
  python tests/test_agent_v1_supplement.py

与主测试脚本共享 HTTP + SSE 工具和报告逻辑。
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 复用主测试脚本的基础设施
from test_agent_v1 import (
    TestCase,
    TestResult,
    check_server_health,
    send_chat_stream,
    run_test_case,
    _save_raw_event,
    BASE_URL,
    CHAT_STREAM_URL,
    HEALTH_URL,
    REPORT_DIR,
    RAW_DIR,
)

# ---------------------------------------------------------------------------
# 补充用例定义
# 编号从 101 起，避免与原 63 用例冲突
# ---------------------------------------------------------------------------

# ── A2. 基础对话与边界（8个）──
CASES_A2 = [
    TestCase(101, "基础对话", "你好",                           "不调工具，友好问候+自我介绍"),
    TestCase(102, "基础对话", "你是谁？能做什么？",              "不调工具，详细介绍身份和功能"),
    TestCase(103, "基础对话", "帮我写一首诗",                   "不调工具，礼貌拒绝+引导购物"),
    TestCase(104, "基础对话", "你觉得美国总统大选怎么样？",      "不调工具，礼貌拒绝政治话题"),
    TestCase(105, "基础对话", "用Python写一个爬虫",             "不调工具，拒绝非购物请求"),
    TestCase(106, "基础对话", "你们有什么商品分类？",            "general_chat 或 recommend → 列出分类信息"),
    TestCase(107, "基础对话", "有哪些品牌？",                    "general_chat 或 recommend → 列出品牌"),
    TestCase(108, "基础对话", "谢谢你",                         "不调工具，友好回应"),
]

# ── B2. 语义搜索商品（12个）──
CASES_B2 = [
    TestCase(109, "语义搜索", "推荐一款好用的洗面奶",             "recommend_shopping_products → 洁面类+CARD"),
    TestCase(110, "语义搜索", "有没有适合学生用的笔记本电脑",    "recommend_shopping_products → 笔记本+CARD"),
    TestCase(111, "语义搜索", "推荐一双跑步鞋",                 "recommend_shopping_products → 运动鞋+CARD"),
    TestCase(112, "语义搜索", "有什么好吃的零食推荐吗",          "recommend_shopping_products → 食品类+CARD"),
    TestCase(113, "语义搜索", "我想买个降噪耳机",               "recommend_shopping_products → 耳机+CARD"),
    TestCase(114, "语义搜索", "有没有防水的运动手表",            "recommend_shopping_products → 手表 或 诚实告知无货"),
    TestCase(115, "语义搜索", "推荐一款性价比高的手机",          "recommend_shopping_products → 手机+CARD"),
    TestCase(116, "语义搜索", "送礼给女朋友",                    "recommend_shopping_products → 跨品类推荐+CARD"),
    TestCase(117, "语义搜索", "夏天穿什么衣服比较凉快",          "recommend_shopping_products → 服饰+CARD"),
    TestCase(118, "语义搜索", "有没有适合敏感肌的护肤品",        "recommend_shopping_products → 护肤品+CARD"),
    TestCase(119, "语义搜索", "推荐一款续航好的手机",            "recommend_shopping_products → 手机+CARD"),
    TestCase(120, "语义搜索", "有没有好看的裙子",                "recommend_shopping_products → 连衣裙/服饰 或 诚实告知"),
]

# ── C2. 结构化查询（8个）──
CASES_C2 = [
    TestCase(121, "结构化查询", "给我看看所有数码电子类商品",    "recommend_shopping_products(category=数码电子)+CARD"),
    TestCase(122, "结构化查询", "500元以下的商品有哪些",         "recommend_shopping_products(max_price=500)+CARD"),
    TestCase(123, "结构化查询", "所有商品按价格从低到高排列",    "recommend_shopping_products → 按价格排序列表"),
    TestCase(124, "结构化查询", "华为品牌的商品有哪些",          "recommend_shopping_products(brand=华为)+CARD"),
    TestCase(125, "结构化查询", "3000到5000之间的手机",          "recommend_shopping_products(min_price=3000,max_price=5000, 手机)+CARD"),
    TestCase(126, "结构化查询", "第二页的商品",                  "general_chat → 追问品类 或 合理响应"),
    TestCase(127, "结构化查询", "美妆护肤类有哪些品牌",          "general_chat 或 recommend → 列出品牌"),
    TestCase(128, "结构化查询", "最贵的商品是什么",              "recommend_shopping_products → 高价商品"),
]

# ── D2. 商品详情（5个）──
CASES_D2 = [
    TestCase(129, "商品详情", "iPhone 17 Pro 有什么颜色可以选？", "recommend_shopping_products → iPhone SKU信息"),
    TestCase(130, "商品详情", "华为Pura 90 Pro 的详细信息",       "recommend_shopping_products → 华为手机详情"),
    TestCase(131, "商品详情", "小米17 Ultra 有几个版本？",        "recommend_shopping_products → 小米SKU信息"),
    TestCase(132, "商品详情", "OPPO Find X9 Ultra 拍照怎么样",   "recommend_shopping_products → OPPO手机评价"),
    TestCase(133, "商品详情", "AirPods Pro 3 支持心率监测吗",    "recommend_shopping_products → AirPods信息"),
]

# ── E2. FAQ 搜索（5个）──
CASES_E2 = [
    TestCase(134, "FAQ搜索", "iPhone 17 Pro 的电池续航怎么样",   "recommend_shopping_products → iPhone FAQ"),
    TestCase(135, "FAQ搜索", "华为 FreeBuds Pro 5 降噪效果好不好", "recommend_shopping_products → 耳机评价/FAQ"),
    TestCase(136, "FAQ搜索", "这个面膜敏感肌能用吗",              "recommend_shopping_products → 面膜FAQ/详情"),
    TestCase(137, "FAQ搜索", "折叠屏手机耐用吗",                  "recommend_shopping_products → 折叠屏FAQ/评价"),
    TestCase(138, "FAQ搜索", "运动跑鞋怎么选择尺码",              "recommend_shopping_products → 跑鞋FAQ"),
]

# ── F2. 评价搜索（5个）──
CASES_F2 = [
    TestCase(139, "评价搜索", "哪个手机好评最多",                 "recommend_shopping_products → 手机评价排序"),
    TestCase(140, "评价搜索", "有没有人说 iPhone 17 Pro 拍照好",  "recommend_shopping_products → iPhone评价"),
    TestCase(141, "评价搜索", "这款耳机有差评吗",                 "general_chat 追问 或 recommend → 耳机评价"),
    TestCase(142, "评价搜索", "大家觉得华为手机怎么样",            "recommend_shopping_products → 华为评价"),
    TestCase(143, "评价搜索", "小米手机发热严重吗",               "recommend_shopping_products → 小米评价"),
]

# ── G2. 否定语义 / 排除（4个）──
CASES_G2 = [
    TestCase(144, "否定排除", "推荐手机，但不要苹果的",           "recommend_shopping_products(exclude_brands) → 非iPhone+CARD"),
    TestCase(145, "否定排除", "推荐护肤品，不要兰蔻",             "recommend_shopping_products(exclude_brands) → 非兰蔻+CARD"),
    TestCase(146, "否定排除", "看看运动鞋，不要Nike的",           "recommend_shopping_products(exclude_brands) → 非Nike+CARD"),
    TestCase(147, "否定排除", "推荐耳机，不要华为的，500到2000之间", "recommend_shopping_products(exclude+price) → 耳机+CARD"),
]

# ── H2. 购物车操作（8个）──
CASES_H2 = [
    TestCase(148, "购物车", "帮我把 iPhone 17 Pro 加到购物车",   "apply_cart_instruction → 追问SKU 或 直接加购", session_id="sess_h148"),
    TestCase(149, "购物车", "我要买华为Pura 90 Pro，黑色的",     "apply_cart_instruction → 匹配黑色SKU追问", session_id="sess_h149"),
    TestCase(150, "购物车", "看看我的购物车",                     "apply_cart_instruction(view_cart)", session_id="sess_h148"),
    TestCase(151, "购物车", "把第一个去掉",                       "apply_cart_instruction(remove)", session_id="sess_h148"),
    TestCase(152, "购物车", "把华为耳机数量改成2",                "apply_cart_instruction(update)", session_id="sess_h152"),
    TestCase(153, "购物车", "清空购物车",                         "apply_cart_instruction(clear)", session_id="sess_h148"),
    TestCase(154, "购物车", "购物车里有什么",                     "apply_cart_instruction(view_cart)", session_id="sess_h148"),
    TestCase(155, "购物车", "加一双跑步鞋，要最便宜的",           "recommend → apply_cart_instruction", session_id="sess_h155"),
]

# ── I2. 多轮对话 / 上下文理解（8个）──
CASES_I2 = [
    TestCase(156, "多轮对话", "推荐一款手机",                     "第1轮：推荐手机", session_id="sess_i156"),
    TestCase(157, "多轮对话", "续航怎么样",                       "理解上下文=手机续航", session_id="sess_i156"),
    TestCase(158, "多轮对话", "有没有更便宜的",                   "理解上下文=手机+价格过滤", session_id="sess_i156"),
    TestCase(159, "多轮对话", "那这款的拍照效果呢",               "理解上下文=手机的拍照", session_id="sess_i156"),
    TestCase(160, "多轮对话", "换零食看看吧",                     "话题切换→零食推荐", session_id="sess_i156"),
    TestCase(161, "多轮对话", "第一个不错，帮我加购物车",         "理解'第一个'→加购", session_id="sess_i156"),
    TestCase(162, "多轮对话", "还有别的推荐吗",                   "搜索更多推荐", session_id="sess_i156"),
    TestCase(163, "多轮对话", "对比一下这两款",                   "compare_products → 对比表", session_id="sess_i156"),
]

# ── J2. 防幻觉测试（5个）──
CASES_J2 = [
    TestCase(164, "防幻觉", "你们有卖 PS5 吗",                   "诚实告知无PS5，不编造"),
    TestCase(165, "防幻觉", "iPhone 17 Pro 只要 999 对吧？",     "纠正价格，非999"),
    TestCase(166, "防幻觉", "三星Galaxy S30怎么样",              "诚实告知无此商品"),
    TestCase(167, "防幻觉", "有没有一百万以上的商品",             "诚实告知无"),
    TestCase(168, "防幻觉", "你们这个店叫什么名字？什么时候开业的？", "诚实不知，引导购物"),
]

# ── K2. 复合 / 综合场景（4个）──
CASES_K2 = [
    TestCase(169, "综合场景", "高端护肤品送妈妈，预算3000以内",   "recommend_shopping_products(护肤品,max_price=3000)+CARD"),
    TestCase(170, "综合场景", "手机+耳机，总共不超过1万",         "recommend_shopping_products → 组合方案+预算计算"),
    TestCase(171, "综合场景", "有没有什么限时优惠活动？",         "general_chat → 诚实告知无法查询促销"),
    TestCase(172, "综合场景", "我想退货怎么办",                   "general_chat → 说明职责边界+退货建议"),
]

# 完整补充用例列表
ALL_SUPPLEMENT_CASES: List[TestCase] = (
    CASES_A2 + CASES_B2 + CASES_C2 + CASES_D2
    + CASES_E2 + CASES_F2 + CASES_G2 + CASES_H2
    + CASES_I2 + CASES_J2 + CASES_K2
)


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------

def generate_supplement_report(results: List[TestResult]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    model = os.getenv("MALLMIND_LLM_MODEL", "unknown")

    lines = [
        f"# MallMind Agent v1 补充测试报告（72用例适配版）",
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
# 执行器
# ---------------------------------------------------------------------------

def run_all_supplement_tests(cases: List[TestCase], *, limit: int = 0) -> List[TestResult]:
    results: List[TestResult] = []
    total = len(cases) if limit <= 0 else min(limit, len(cases))

    print(f"\n{'='*70}")
    print(f"  MallMind Agent v1 补充测试 - 共 {total} 个用例")
    print(f"  服务器: {BASE_URL}")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")

    for i, case in enumerate(cases[:total]):
        tag = f"[{i+1}/{total}]"
        print(f"{tag} #{case.id} [{case.category}] \"{case.input_text}\"")
        print(f"    预期: {case.expected_behavior}")

        result = run_test_case(case)
        results.append(result)

        status = "ERR" if result.error else "OK"
        print(f"    -> {status} | 模式={result.runtime_mode} | 工具={result.tool_chain_str}")
        if result.product_cards:
            card_ids = [c.get("product_id", "?") for c in result.product_cards[:5]]
            print(f"    -> 商品卡片: {card_ids}")
        if result.cart:
            items = (result.cart or {}).get("items", [])
            print(f"    -> 购物车: {len(items)} 件商品")
        resp_preview = result.text_response[:120].replace("\n", " ")
        print(f"    -> 回复: {resp_preview}...")
        print(f"    -> 耗时: {result.elapsed_ms}ms | 事件: {result.events_received}")
        if result.error:
            print(f"    -> ERROR: {result.error}")
        print()

        # 保存原始数据（补充用例单独存放）
        _save_raw_event(case, result)

    return results


def main():
    import argparse

    parser = argparse.ArgumentParser(description="MallMind Agent v1 补充测试")
    parser.add_argument("--limit", type=int, default=0, help="只运行前 N 个用例")
    parser.add_argument("--case-ids", type=str, default="", help="只运行指定用例ID (逗号分隔)")
    parser.add_argument("--base-url", type=str, default="", help="覆盖服务器地址")
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

    cases = ALL_SUPPLEMENT_CASES
    if args.case_ids:
        ids = set(int(x.strip()) for x in args.case_ids.split(","))
        cases = [c for c in cases if c.id in ids]
    if args.limit > 0:
        cases = cases[:args.limit]

    print(f"将运行 {len(cases)} 个补充用例\n")

    results = run_all_supplement_tests(cases, limit=args.limit)

    report_md = generate_supplement_report(results)
    report_path = REPORT_DIR / "agent_v1_supplement_report.md"
    report_path.write_text(report_md, encoding="utf-8")
    print(f"\n报告已生成: {report_path}")

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
