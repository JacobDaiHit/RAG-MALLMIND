"""
MallMind 全量自动化测试脚本
==========================

覆盖两条核心业务线：
1. 普通电商商品：搜索、推荐、筛选、对比、购物车、多轮、防幻觉。
2. PC 整机/配件：配件搜索、预算解析、整机方案推荐、兼容性、预算调整、多轮、防幻觉。

运行方式：
    python backend/scripts/run_mallmind_full_tests.py --base-url http://localhost:8000
    python backend/scripts/run_mallmind_full_tests.py --base-url http://localhost:8000 --output .omo/evidence/mallmind-full-test.txt --json-output .omo/evidence/mallmind-full-test.json

约定：
- 默认请求 POST /api/chat，请求体为 {"message": "...", "session_id": "..."}。
- 兼容 reply/message/answer/content 等不同响应字段。
- 兼容 tool_calls、tools、trace、steps、metadata 等不同调试字段。
- 如果你的后端没有暴露 tool_calls，本脚本仍会用回复内容和结构化字段做弱判断。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from pathlib import Path
import requests

os.environ["PYTHONIOENCODING"] = "utf-8"

DEFAULT_BASE_URL = "http://localhost:8011"
DEFAULT_OUTPUT_PATH = ".pytest_tmp/full_test_report/mallmind-full-test.txt"
DEFAULT_JSON_OUTPUT_PATH = ".pytest_tmp/full_test_report/mallmind-full-test.json"
REQUEST_TIMEOUT = 120


# ============================================================
# 基础工具函数
# ============================================================

def send_chat(base_url: str, message: str, session_id: str) -> dict[str, Any]:
    resp = requests.post(
        f"{base_url.rstrip('/')}/api/chat",
        json={"message": message, "session_id": session_id},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def flatten_text(value: Any, max_len: int = 30000) -> str:
    """把响应里的嵌套结构粗略转成文本，便于关键词检查。"""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return text[:max_len]


def extract_reply(data: dict[str, Any]) -> str:
    """兼容不同接口返回字段。"""
    candidates = [
        data.get("reply"),
        data.get("message"),
        data.get("answer"),
        data.get("content"),
        data.get("text"),
        data.get("response"),
    ]
    nested = data.get("data")
    if isinstance(nested, dict):
        candidates.extend([
            nested.get("reply"),
            nested.get("message"),
            nested.get("answer"),
            nested.get("content"),
            nested.get("text"),
        ])

    for item in candidates:
        if isinstance(item, str) and item.strip():
            return item

    return flatten_text(data)


def _walk_dicts(value: Any) -> list[dict[str, Any]]:
    dicts: list[dict[str, Any]] = []
    if isinstance(value, dict):
        dicts.append(value)
        for v in value.values():
            dicts.extend(_walk_dicts(v))
    elif isinstance(value, list):
        for item in value:
            dicts.extend(_walk_dicts(item))
    return dicts


def extract_tool_names(data: dict[str, Any]) -> list[str]:
    """从 tool_calls / trace / steps / metadata 里尽量提取工具名或路由名。"""
    names: list[str] = []

    possible_lists = [
        data.get("tool_calls"),
        data.get("tools"),
        data.get("steps"),
        data.get("events"),
    ]

    trace = data.get("trace")
    if isinstance(trace, dict):
        possible_lists.extend([
            trace.get("tool_calls"),
            trace.get("tools"),
            trace.get("steps"),
            trace.get("events"),
        ])

    metadata = data.get("metadata") or data.get("meta")
    if isinstance(metadata, dict):
        possible_lists.extend([
            metadata.get("tool_calls"),
            metadata.get("tools"),
            metadata.get("steps"),
            metadata.get("events"),
        ])

    for possible in possible_lists:
        if not isinstance(possible, list):
            continue
        for item in possible:
            if not isinstance(item, dict):
                continue
            for key in ("name", "tool", "tool_name", "function_name", "route", "router", "mode"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    names.append(value)
            fn = item.get("function")
            if isinstance(fn, dict) and isinstance(fn.get("name"), str):
                names.append(fn["name"])

    for d in _walk_dicts(data):
        for key in ("route", "router", "runtime_mode", "selected_mode", "tool_name"):
            value = d.get(key)
            if isinstance(value, str) and value:
                names.append(value)

    # 保序去重
    seen = set()
    out = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def contains_any(text: str, keywords: list[str]) -> bool:
    lower = text.lower()
    return any(k.lower() in lower for k in keywords)


def contains_all(text: str, keywords: list[str]) -> bool:
    lower = text.lower()
    return all(k.lower() in lower for k in keywords)


def has_tool_like(tool_names: list[str], aliases: list[str]) -> bool:
    joined = " ".join(tool_names).lower()
    return any(alias.lower() in joined for alias in aliases)


def has_product_signal(reply: str, data: dict[str, Any]) -> bool:
    if "[CARD]" in reply or "[/CARD]" in reply:
        return True
    for key in ("products", "items", "cards", "recommendations", "results"):
        value = data.get(key)
        if isinstance(value, list) and value:
            return True
    text = flatten_text(data)
    return contains_any(text, ["product_id", "sku_id", "商品卡片", "商品名称", "¥", "￥"])


def has_pc_solution_signal(reply: str, data: dict[str, Any]) -> bool:
    text = reply + "\n" + flatten_text(data)
    groups = [
        ["CPU", "处理器"],
        ["GPU", "显卡"],
        ["主板"],
        ["内存", "RAM"],
        ["硬盘", "SSD", "存储"],
        ["电源", "PSU"],
        ["机箱"],
    ]
    hit = sum(1 for g in groups if contains_any(text, g))
    return hit >= 4 and contains_any(text, ["整机", "配置", "方案", "装机", "预算", "合计", "总价"])


def count_pc_parts(reply: str, data: dict[str, Any]) -> int:
    text = reply + "\n" + flatten_text(data)
    groups = [
        ["CPU", "处理器"],
        ["GPU", "显卡"],
        ["主板"],
        ["内存", "RAM"],
        ["硬盘", "SSD", "存储"],
        ["电源", "PSU"],
        ["机箱"],
        ["散热", "风冷", "水冷"],
        ["显示器"],
    ]
    return sum(1 for g in groups if contains_any(text, g))


def extract_prices(text: str) -> list[float]:
    prices = []
    for m in re.finditer(r"(?:¥|￥)?\s*(\d{2,6}(?:\.\d+)?)\s*(?:元|块|rmb|RMB)?", text):
        try:
            prices.append(float(m.group(1)))
        except ValueError:
            pass
    return prices


@dataclass
class TestCase:
    id: str
    name: str
    category: str
    messages: list[str]
    criteria: str
    judge: Callable[["CaseRun"], tuple[bool, str]]
    tags: list[str] = field(default_factory=list)


@dataclass
class CaseRun:
    test: TestCase
    session_id: str
    replies: list[str]
    payloads: list[dict[str, Any]]
    tool_names: list[str]
    duration_ms: float
    error: str = ""

    @property
    def last_reply(self) -> str:
        return self.replies[-1] if self.replies else ""

    @property
    def last_payload(self) -> dict[str, Any]:
        return self.payloads[-1] if self.payloads else {}

    @property
    def all_text(self) -> str:
        return "\n".join(self.replies) + "\n" + flatten_text(self.payloads)


# ============================================================
# 判断器
# ============================================================

def ok() -> Callable[[CaseRun], tuple[bool, str]]:
    return lambda run: (not run.error, "" if not run.error else run.error)


def expect_any_keywords(keywords: list[str]) -> Callable[[CaseRun], tuple[bool, str]]:
    def judge(run: CaseRun) -> tuple[bool, str]:
        passed = contains_any(run.last_reply, keywords) or contains_any(flatten_text(run.last_payload), keywords)
        return passed, "" if passed else f"缺少关键词任一：{keywords}"
    return judge


def expect_all_keyword_groups(groups: list[list[str]]) -> Callable[[CaseRun], tuple[bool, str]]:
    def judge(run: CaseRun) -> tuple[bool, str]:
        text = run.last_reply + "\n" + flatten_text(run.last_payload)
        missing = [g for g in groups if not contains_any(text, g)]
        return not missing, "" if not missing else f"缺少关键词组：{missing}"
    return judge


def expect_forbidden_keywords(forbidden: list[str]) -> Callable[[CaseRun], tuple[bool, str]]:
    def judge(run: CaseRun) -> tuple[bool, str]:
        bad = [k for k in forbidden if k.lower() in run.last_reply.lower()]
        return not bad, "" if not bad else f"出现禁用关键词：{bad}"
    return judge


def expect_product_search() -> Callable[[CaseRun], tuple[bool, str]]:
    def judge(run: CaseRun) -> tuple[bool, str]:
        tool_ok = has_tool_like(run.tool_names, ["search_products", "list_products", "product", "recommend"])
        signal_ok = has_product_signal(run.last_reply, run.last_payload)
        passed = tool_ok or signal_ok
        return passed, "" if passed else "未检测到商品检索/推荐工具或商品结果信号"
    return judge


def expect_compare(groups: list[list[str]] | None = None) -> Callable[[CaseRun], tuple[bool, str]]:
    def judge(run: CaseRun) -> tuple[bool, str]:
        text = run.last_reply + "\n" + flatten_text(run.last_payload)
        compare_ok = contains_any(text, ["对比", "比较", "区别", "差异", "优点", "缺点", "适合"])
        if groups:
            missing = [g for g in groups if not contains_any(text, g)]
            passed = compare_ok and not missing
            reason = "" if passed else f"对比信号={compare_ok}，缺少关键词组={missing}"
            return passed, reason
        return compare_ok, "" if compare_ok else "缺少对比/比较/差异类表达"
    return judge


def expect_pc_parts(min_parts: int = 4) -> Callable[[CaseRun], tuple[bool, str]]:
    def judge(run: CaseRun) -> tuple[bool, str]:
        n = count_pc_parts(run.last_reply, run.last_payload)
        passed = n >= min_parts
        return passed, "" if passed else f"PC 配件覆盖不足：{n}/{min_parts}"
    return judge


def expect_pc_solution(min_parts: int = 5) -> Callable[[CaseRun], tuple[bool, str]]:
    def judge(run: CaseRun) -> tuple[bool, str]:
        signal = has_pc_solution_signal(run.last_reply, run.last_payload)
        n = count_pc_parts(run.last_reply, run.last_payload)
        passed = signal and n >= min_parts
        return passed, "" if passed else f"未形成完整 PC 整机方案，solution_signal={signal}, parts={n}/{min_parts}"
    return judge


def expect_budget_near(target: int, tolerance: float = 0.25) -> Callable[[CaseRun], tuple[bool, str]]:
    """弱判断：回复中应出现接近预算的总价或预算解释。"""
    def judge(run: CaseRun) -> tuple[bool, str]:
        text = run.last_reply + "\n" + flatten_text(run.last_payload)
        prices = extract_prices(text)
        low = target * (1 - tolerance)
        high = target * (1 + tolerance)
        near = [p for p in prices if low <= p <= high]
        budget_word = contains_any(text, ["预算", "总价", "合计", "控制在", "左右", "超出", "低于"])
        passed = bool(near) and budget_word
        return passed, "" if passed else f"未看到接近 {target} 的预算/总价表达；提取价格={prices[:12]}"
    return judge


def and_judges(*judges: Callable[[CaseRun], tuple[bool, str]]) -> Callable[[CaseRun], tuple[bool, str]]:
    def judge(run: CaseRun) -> tuple[bool, str]:
        reasons = []
        for fn in judges:
            passed, reason = fn(run)
            if not passed:
                reasons.append(reason)
        return not reasons, "；".join(r for r in reasons if r)
    return judge


def no_tool_and_guide_to_shopping() -> Callable[[CaseRun], tuple[bool, str]]:
    def judge(run: CaseRun) -> tuple[bool, str]:
        tool_ok = not run.tool_names
        guide_ok = contains_any(run.last_reply, ["导购", "购物", "商品", "推荐", "挑选"])
        return tool_ok and guide_ok, f"tool_ok={tool_ok}, guide_ok={guide_ok}"
    return judge


# ============================================================
# 测试用例定义
# ============================================================

def define_test_cases() -> list[TestCase]:
    tests: list[TestCase] = []
    add = tests.append

    # A. 基础与路由
    add(TestCase("TC-001", "问候回复", "A.基础对话", ["你好"], "不调用商品工具，说明导购身份", no_tool_and_guide_to_shopping()))
    add(TestCase("TC-002", "无关天气请求", "A.基础对话", ["今天天气怎么样？"], "不编造天气，引导回购物", and_judges(expect_forbidden_keywords(["晴", "下雨", "气温", "℃"]), expect_any_keywords(["购物", "商品", "导购", "挑选"]))))
    add(TestCase("TC-003", "代码请求拒绝", "A.基础对话", ["帮我写一个 Python 排序算法"], "不输出代码，引导回购物", and_judges(expect_forbidden_keywords(["def ", "import ", "for i in", "while "]), expect_any_keywords(["购物", "商品", "导购"]))))
    add(TestCase("TC-004", "空消息", "A.基础对话", [""], "不崩溃，有合理提示", ok()))
    add(TestCase("TC-005", "纯表情", "A.基础对话", ["😀🎉👍"], "不崩溃，有合理提示", ok()))
    add(TestCase("TC-006", "特殊字符输入", "A.基础对话", ["推荐 <script>alert('xss')</script> 手机"], "不回显危险脚本，仍识别购物意图", and_judges(expect_forbidden_keywords(["<script>", "alert("]), expect_product_search())))

    # B. 普通商品搜索与推荐
    add(TestCase("TC-101", "模糊手机推荐", "B.普通商品搜索推荐", ["推荐一款手机"], "返回手机商品", and_judges(expect_product_search(), expect_any_keywords(["手机", "iPhone", "小米", "华为", "OPPO", "vivo"]))))
    add(TestCase("TC-102", "学生笔记本", "B.普通商品搜索推荐", ["有没有适合学生的笔记本电脑？"], "返回笔记本并解释学生场景", and_judges(expect_product_search(), expect_all_keyword_groups([["笔记本", "电脑"], ["学生", "性价比", "学习", "轻薄"]]))))
    add(TestCase("TC-103", "Apple 品牌商品", "B.普通商品搜索推荐", ["有哪些 Apple 的商品？"], "识别 Apple/苹果品牌", and_judges(expect_product_search(), expect_any_keywords(["Apple", "苹果", "iPhone", "iPad", "Mac"]))))
    add(TestCase("TC-104", "价格过滤手机", "B.普通商品搜索推荐", ["5000 块以下的手机有哪些？"], "价格上限过滤", and_judges(expect_product_search(), expect_any_keywords(["5000", "以下", "预算", "价格"]))))
    add(TestCase("TC-105", "排除品牌", "B.普通商品搜索推荐", ["推荐一款 8000 块左右的手机，不要小米的"], "排除小米品牌", and_judges(expect_product_search(), expect_forbidden_keywords(["小米", "Redmi", "红米"]))))
    add(TestCase("TC-106", "无结果品类", "B.普通商品搜索推荐", ["有没有卖冰箱的？"], "无结果时不编造", expect_any_keywords(["没有", "未找到", "暂无", "找不到", "抱歉"])))
    add(TestCase("TC-107", "护肤品功能需求", "B.普通商品搜索推荐", ["有没有保湿效果好的护肤品？"], "识别保湿护肤需求", and_judges(expect_product_search(), expect_all_keyword_groups([["护肤", "面霜", "精华", "乳液", "面膜"], ["保湿", "补水"]]))))
    add(TestCase("TC-108", "咖啡人群需求", "B.普通商品搜索推荐", ["有没有适合上班族的咖啡推荐？"], "识别上班族咖啡场景", and_judges(expect_product_search(), expect_all_keyword_groups([["咖啡"], ["上班", "提神", "通勤", "办公室"]]))))
    add(TestCase("TC-109", "送礼场景", "B.普通商品搜索推荐", ["有什么适合送女朋友的礼物？"], "场景化推荐", and_judges(expect_product_search(), expect_any_keywords(["礼物", "送", "女朋友", "精致", "香水", "护肤", "耳机"]))))
    add(TestCase("TC-110", "多品类推荐", "B.普通商品搜索推荐", ["我想买点吃的喝的，还想看看衣服"], "同时识别食品饮料和服饰", and_judges(expect_product_search(), expect_all_keyword_groups([["零食", "食品", "饮料", "吃", "喝"], ["衣服", "服饰", "T恤", "服装"]]))))

    # C. 普通商品对比
    add(TestCase("TC-201", "手机横向对比", "C.普通商品对比", ["帮我对比一下 iPhone 17 Pro 和其他手机"], "至少给出对比维度和结论", and_judges(expect_product_search(), expect_compare([["iPhone", "苹果"], ["对比", "比较", "区别"]]))))
    add(TestCase("TC-202", "同品类咖啡对比", "C.普通商品对比", ["帮我对比一下三顿半咖啡和其他咖啡"], "咖啡同品类对比", and_judges(expect_product_search(), expect_compare([["咖啡"], ["口味", "价格", "适合", "便携"]]))))
    add(TestCase("TC-203", "苹果手机和平板区别", "C.普通商品对比", ["苹果的手机和平板有什么区别？"], "同品牌不同品类对比", and_judges(expect_product_search(), expect_compare([["手机"], ["平板", "iPad"]]))))
    add(TestCase("TC-204", "低价 T 恤对比", "C.普通商品对比", ["帮我对比一下 100 块以下的 T 恤"], "价格维度对比", and_judges(expect_product_search(), expect_compare([["T恤", "T 恤"], ["100", "价格", "便宜"]]))))
    add(TestCase("TC-205", "跨品类对比提示", "C.普通商品对比", ["帮我对比 iPhone 17 Pro 和雅诗兰黛小棕瓶"], "不强行做无意义参数对比", expect_any_keywords(["不同品类", "不适合直接对比", "使用场景不同", "类别不同", "建议分别"])))

    # D. 购物车
    add(TestCase("TC-301", "明确商品加购", "D.购物车", ["帮我把 iPhone 17 Pro 256G 远峰蓝加到购物车"], "调用/完成加购", expect_any_keywords(["购物车", "已", "加入", "成功"])))
    add(TestCase("TC-302", "模糊加购追问", "D.购物车", ["帮我加到购物车"], "缺商品时追问", expect_any_keywords(["哪款", "哪个", "什么商品", "请告诉", "需要先"])))
    add(TestCase("TC-303", "查看购物车", "D.购物车", ["看看我的购物车"], "展示购物车状态", expect_any_keywords(["购物车", "商品", "空", "数量", "合计"])))
    add(TestCase("TC-304", "多轮加购", "D.购物车", ["推荐手机", "就这款，帮我加到购物车", "看看购物车"], "多轮上下文加购后可查看", expect_any_keywords(["购物车", "手机", "iPhone", "商品"])))
    add(TestCase("TC-305", "修改数量", "D.购物车", ["帮我把 iPhone 17 Pro 256G 远峰蓝加到购物车", "把数量改成 2 台", "看看购物车"], "数量应变为 2", expect_any_keywords(["2", "两", "数量", "购物车"])))
    add(TestCase("TC-306", "清空购物车", "D.购物车", ["帮我把 iPhone 17 Pro 256G 远峰蓝加到购物车", "清空购物车", "看看购物车"], "清空后为空", expect_any_keywords(["清空", "空", "没有商品", "购物车"])))

    # E. PC 配件搜索
    add(TestCase("TC-401", "搜索显卡", "E.PC配件搜索", ["推荐几张 4000 元以内适合 2K 游戏的显卡"], "识别显卡和 2K 游戏", and_judges(expect_product_search(), expect_all_keyword_groups([["显卡", "GPU", "RTX", "RX"], ["2K", "游戏"], ["4000", "预算", "以内"]]))))
    add(TestCase("TC-402", "搜索 CPU", "E.PC配件搜索", ["1500 左右的 CPU 有什么推荐？"], "识别 CPU 和价格", and_judges(expect_product_search(), expect_all_keyword_groups([["CPU", "处理器"], ["1500", "预算", "左右"]]))))
    add(TestCase("TC-403", "搜索主板", "E.PC配件搜索", ["给我推荐能搭配 i5 的 B760 主板"], "识别主板芯片组", and_judges(expect_product_search(), expect_all_keyword_groups([["主板", "B760"], ["i5", "Intel", "英特尔"]]))))
    add(TestCase("TC-404", "搜索内存", "E.PC配件搜索", ["32G DDR5 内存条推荐一下"], "识别容量和 DDR5", and_judges(expect_product_search(), expect_all_keyword_groups([["内存", "RAM"], ["32G", "32GB"], ["DDR5"]]))))
    add(TestCase("TC-405", "搜索 SSD", "E.PC配件搜索", ["1TB PCIe 4.0 固态硬盘怎么选？"], "识别 SSD 规格", and_judges(expect_product_search(), expect_all_keyword_groups([["SSD", "固态", "硬盘"], ["1TB"], ["PCIe", "4.0"]]))))
    add(TestCase("TC-406", "搜索电源", "E.PC配件搜索", ["4070 显卡配多大电源？顺便推荐电源"], "电源功率建议不能把 4070 当预算", and_judges(expect_product_search(), expect_all_keyword_groups([["电源", "PSU", "W", "瓦"], ["4070", "RTX"]]))))
    add(TestCase("TC-407", "搜索机箱", "E.PC配件搜索", ["想要一个能装长显卡的 ATX 机箱"], "识别机箱兼容性", and_judges(expect_product_search(), expect_all_keyword_groups([["机箱", "ATX"], ["显卡", "长度", "兼容"]]))))
    add(TestCase("TC-408", "搜索散热器", "E.PC配件搜索", ["i7 处理器需要什么散热器？"], "识别散热需求", and_judges(expect_product_search(), expect_all_keyword_groups([["散热", "风冷", "水冷"], ["i7", "处理器", "CPU"]]))))

    # F. PC 整机方案推荐
    add(TestCase("TC-501", "6000 游戏整机", "F.PC整机方案", ["预算 6000，主要玩 2K 游戏，帮我配一套整机"], "给出完整装机方案并接近预算", and_judges(expect_pc_solution(6), expect_budget_near(6000, 0.30))))
    add(TestCase("TC-502", "8000 剪辑游戏整机", "F.PC整机方案", ["预算 8000，剪辑视频加玩 3A 游戏，推荐一套电脑配置"], "兼顾 CPU/GPU/内存/硬盘", and_judges(expect_pc_solution(6), expect_budget_near(8000, 0.30), expect_all_keyword_groups([["剪辑", "视频", "生产力"], ["游戏", "3A"]]))))
    add(TestCase("TC-503", "10000 深度学习主机", "F.PC整机方案", ["1 万预算，想跑一些深度学习和 CUDA，帮我配电脑"], "优先 NVIDIA/CUDA/显存", and_judges(expect_pc_solution(6), expect_budget_near(10000, 0.35), expect_all_keyword_groups([["CUDA", "深度学习", "AI"], ["NVIDIA", "RTX", "显存", "GPU"]]))))
    add(TestCase("TC-504", "办公静音主机", "F.PC整机方案", ["4000 预算，办公用，要求安静省电，给我一套配置"], "办公静音省电", and_judges(expect_pc_solution(5), expect_budget_near(4000, 0.35), expect_all_keyword_groups([["办公"], ["安静", "静音", "省电", "功耗"]]))))
    add(TestCase("TC-505", "只给显卡型号再给预算", "F.PC整机方案", ["4070 显卡，预算 7000，帮我配一套"], "不能把 4070 误判成预算", and_judges(expect_pc_solution(5), expect_budget_near(7000, 0.35), expect_any_keywords(["4070", "RTX"]))))
    add(TestCase("TC-506", "预算缺失追问", "F.PC整机方案", ["帮我配一台游戏电脑"], "预算缺失时追问或给档位方案", expect_any_keywords(["预算", "价格", "档位", "大概", "多少"])))
    add(TestCase("TC-507", "用途缺失追问", "F.PC整机方案", ["预算 7000，帮我配电脑"], "用途缺失时追问或给通用假设", expect_any_keywords(["用途", "游戏", "办公", "剪辑", "假设", "主要用来"])))
    add(TestCase("TC-508", "明确不要某品牌", "F.PC整机方案", ["预算 7000 配游戏电脑，不要七彩虹显卡"], "排除指定品牌", and_judges(expect_pc_solution(5), expect_forbidden_keywords(["七彩虹"]))))
    add(TestCase("TC-509", "小机箱 ITX", "F.PC整机方案", ["预算 9000，想配一台 ITX 小主机玩游戏"], "识别 ITX 尺寸约束", and_judges(expect_pc_solution(5), expect_all_keyword_groups([["ITX", "小主机", "小机箱"], ["兼容", "尺寸", "散热"]]))))
    add(TestCase("TC-510", "显示器一起配", "F.PC整机方案", ["预算 9000，主机加显示器，主要 2K 游戏"], "包含显示器且说明预算分配", and_judges(expect_pc_solution(5), expect_all_keyword_groups([["显示器"], ["2K"], ["预算", "总价", "分配"]]))))

    # G. PC 多轮与预算调整
    add(TestCase("TC-601", "多轮降到目标预算", "G.PC多轮", ["预算 8000，配一台 2K 游戏主机", "太贵了，降到 6000"], "降到 6000 是目标预算，不是减少 6000", and_judges(expect_pc_solution(5), expect_budget_near(6000, 0.35))))
    add(TestCase("TC-602", "多轮降低差值", "G.PC多轮", ["预算 9000，配一台游戏主机", "降低 1000"], "降低 1000 是差值调整，目标约 8000", and_judges(expect_pc_solution(5), expect_budget_near(8000, 0.35))))
    add(TestCase("TC-603", "追问升级显卡", "G.PC多轮", ["预算 7000，配一台 2K 游戏主机", "显卡能不能升级一点？"], "围绕显卡升级并调整预算/其他配件", and_judges(expect_pc_solution(5), expect_all_keyword_groups([["显卡", "GPU"], ["升级", "提升", "换成"], ["预算", "总价", "取舍"]]))))
    add(TestCase("TC-604", "追问兼容性", "G.PC多轮", ["预算 7000，配一台游戏主机", "这些配件兼容吗？"], "说明主板/CPU/内存/电源/机箱兼容", expect_all_keyword_groups([["兼容"], ["CPU", "处理器"], ["主板"], ["内存"], ["电源", "PSU"], ["机箱", "尺寸"]])))
    add(TestCase("TC-605", "追问为什么这样配", "G.PC多轮", ["预算 6000，配一台游戏主机", "为什么这样搭配？"], "解释配置依据", expect_all_keyword_groups([["原因", "因为", "考虑"], ["性能", "预算", "性价比"], ["CPU", "显卡", "GPU"]])))
    add(TestCase("TC-606", "切换普通商品", "G.PC多轮", ["预算 7000，配一台电脑", "先不看电脑了，推荐手机"], "正确切换到普通手机推荐", and_judges(expect_product_search(), expect_any_keywords(["手机", "iPhone", "小米", "华为"]))))

    # H. PC 方案对比
    add(TestCase("TC-701", "两套预算方案对比", "H.PC方案对比", ["帮我对比 6000 和 8000 两档游戏主机配置"], "比较两档配置差异", and_judges(expect_pc_parts(5), expect_compare([["6000"], ["8000"], ["显卡", "GPU"], ["CPU", "处理器"]]))))
    add(TestCase("TC-702", "Intel vs AMD 方案", "H.PC方案对比", ["7000 预算，Intel 和 AMD 游戏配置怎么选？"], "比较平台差异", and_judges(expect_pc_parts(5), expect_compare([["Intel", "英特尔"], ["AMD", "锐龙"], ["主板", "平台"]]))))
    add(TestCase("TC-703", "NVIDIA vs AMD 显卡", "H.PC方案对比", ["同价位 NVIDIA 显卡和 AMD 显卡怎么选？"], "比较显卡生态和场景", expect_compare([["NVIDIA", "RTX", "CUDA"], ["AMD", "RX"], ["光追", "CUDA", "性价比", "功耗"]])))
    add(TestCase("TC-704", "生产力 vs 游戏配置", "H.PC方案对比", ["8000 预算，剪辑配置和游戏配置有什么区别？"], "比较用途取舍", and_judges(expect_pc_parts(5), expect_compare([["剪辑", "生产力"], ["游戏"], ["CPU", "显卡", "内存"]]))))

    # I. 防幻觉与边界
    add(TestCase("TC-801", "不存在商品 ID", "I.防幻觉边界", ["帮我看看 p_xxx_999 这个商品详情"], "不编造商品", expect_any_keywords(["没有", "不存在", "未找到", "找不到", "抱歉"])))
    add(TestCase("TC-802", "不存在 SKU 加购", "I.防幻觉边界", ["帮我把 s_xxx_999 加到购物车"], "不加购不存在 SKU", expect_any_keywords(["没有", "不存在", "未找到", "找不到", "错误", "抱歉"])))
    add(TestCase("TC-803", "库存不可编造", "I.防幻觉边界", ["这款手机有货吗？库存多少？"], "未查到时不编造库存数字", expect_any_keywords(["库存", "有货", "暂无", "没有", "需要查看", "商品"])))
    add(TestCase("TC-804", "促销不可编造", "I.防幻觉边界", ["这款手机现在有什么优惠活动？"], "不编造活动", expect_any_keywords(["优惠", "活动", "暂无", "没有", "以实际", "需要查看"])))
    add(TestCase("TC-805", "PC 库无货提示", "I.防幻觉边界", ["有没有 RTX 9090 显卡？"], "不存在型号不编造", expect_any_keywords(["没有", "未找到", "不存在", "暂无", "找不到"])))
    add(TestCase("TC-806", "不合理预算", "I.防幻觉边界", ["预算 1000，配一台 4K 光追游戏主机"], "指出预算与需求不匹配", expect_any_keywords(["预算不足", "不现实", "无法", "降低", "提高预算", "取舍"])))
    add(TestCase("TC-807", "超长输入", "I.防幻觉边界", ["我想买手机" * 200], "不崩溃", ok()))
    add(TestCase("TC-808", "PC 超长输入", "I.防幻觉边界", ["预算 6000 主要玩 2K 游戏 " * 120], "不崩溃并识别 PC 需求", and_judges(ok(), expect_pc_solution(4))))

    return tests


# ============================================================
# 执行器与报告
# ============================================================

class FullTestRunner:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.results: list[dict[str, Any]] = []

    def check_server(self) -> None:
        candidates = ["/docs", "/openapi.json", "/health"]
        last_error = None
        for path in candidates:
            try:
                resp = requests.get(f"{self.base_url}{path}", timeout=5)
                if resp.status_code < 500:
                    return
            except Exception as e:
                last_error = e
        raise RuntimeError(f"无法连接后端服务 {self.base_url}，最后错误：{last_error}")

    def run_test(self, test: TestCase) -> dict[str, Any]:
        session_id = f"mallmind-full-{test.id.lower()}-{int(time.time() * 1000)}"
        replies: list[str] = []
        payloads: list[dict[str, Any]] = []
        all_tools: list[str] = []
        start = time.time()
        error = ""

        try:
            for idx, message in enumerate(test.messages):
                if idx > 0:
                    time.sleep(0.3)
                data = send_chat(self.base_url, message, session_id)
                payloads.append(data)
                replies.append(extract_reply(data))
                all_tools.extend(extract_tool_names(data))
        except requests.Timeout:
            error = "请求超时"
        except Exception as e:
            error = f"{type(e).__name__}: {e}"

        duration_ms = round((time.time() - start) * 1000, 2)
        # 保序去重工具名
        all_tools = list(dict.fromkeys(all_tools))

        run = CaseRun(
            test=test,
            session_id=session_id,
            replies=replies,
            payloads=payloads,
            tool_names=all_tools,
            duration_ms=duration_ms,
            error=error,
        )

        if error:
            passed = False
            reason = error
            status = "ERROR"
        else:
            passed, reason = test.judge(run)
            status = "PASS" if passed else "FAIL"

        result = {
            "id": test.id,
            "name": test.name,
            "category": test.category,
            "status": status,
            "criteria": test.criteria,
            "duration_ms": duration_ms,
            "tool_names": all_tools,
            "reply_length": len(run.last_reply),
            "reason": reason,
            "messages": test.messages,
            "last_reply_preview": run.last_reply[:500],
            "tags": test.tags,
        }
        self.results.append(result)
        return result

    def run_all(self, tests: list[TestCase]) -> None:
        print("\n" + "=" * 80)
        print("MallMind 全量自动化测试")
        print(f"目标服务器: {self.base_url}")
        print(f"测试用例数: {len(tests)}")
        print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 80)

        self.check_server()

        current_category = ""
        start = time.time()

        for test in tests:
            if test.category != current_category:
                current_category = test.category
                print(f"\n{'─' * 60}")
                print(f"{current_category}")
                print(f"{'─' * 60}")

            result = self.run_test(test)
            icon = "✅" if result["status"] == "PASS" else "❌" if result["status"] == "FAIL" else "⚠️"
            tools = ", ".join(result["tool_names"]) if result["tool_names"] else "无"
            print(f"{icon} {result['id']} {result['name']}  {result['duration_ms']:.0f}ms  tools={tools}")
            if result["status"] != "PASS":
                print(f"   原因: {result['reason']}")
                print(f"   回复片段: {result['last_reply_preview'][:180]}")

        total_ms = round((time.time() - start) * 1000, 2)
        self.print_summary(total_ms)

    def stats(self) -> dict[str, Any]:
        total = len(self.results)
        passed = sum(1 for r in self.results if r["status"] == "PASS")
        failed = sum(1 for r in self.results if r["status"] == "FAIL")
        errors = sum(1 for r in self.results if r["status"] == "ERROR")
        pass_rate = passed / total * 100 if total else 0

        by_category: dict[str, dict[str, int]] = {}
        for r in self.results:
            cat = r["category"]
            by_category.setdefault(cat, {"PASS": 0, "FAIL": 0, "ERROR": 0})
            by_category[cat][r["status"]] += 1

        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "pass_rate": pass_rate,
            "by_category": by_category,
        }

    def print_summary(self, total_ms: float) -> None:
        s = self.stats()
        print("\n" + "=" * 80)
        print("测试汇总")
        print("=" * 80)
        print(f"总测试数: {s['total']}")
        print(f"通过: {s['passed']}")
        print(f"失败: {s['failed']}")
        print(f"异常: {s['errors']}")
        print(f"通过率: {s['pass_rate']:.1f}%")
        print(f"总耗时: {total_ms:.0f}ms ({total_ms / 1000:.1f}s)")
        print("\n分类统计:")
        for cat, item in s["by_category"].items():
            total = item["PASS"] + item["FAIL"] + item["ERROR"]
            rate = item["PASS"] / total * 100 if total else 0
            print(f"  {cat}: {item['PASS']}/{total} ({rate:.0f}%)")
        print("=" * 80)

    def generate_report(self) -> str:
        s = self.stats()
        lines = []
        lines.append("=" * 80)
        lines.append("MallMind 全量自动化测试报告")
        lines.append("=" * 80)
        lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"目标服务器: {self.base_url}")
        lines.append("")
        lines.append("测试结果汇总")
        lines.append("-" * 50)
        lines.append(f"总测试数: {s['total']}")
        lines.append(f"通过: {s['passed']}")
        lines.append(f"失败: {s['failed']}")
        lines.append(f"异常: {s['errors']}")
        lines.append(f"通过率: {s['pass_rate']:.1f}%")
        lines.append("")
        lines.append("分类统计")
        lines.append("-" * 50)
        for cat, item in s["by_category"].items():
            total = item["PASS"] + item["FAIL"] + item["ERROR"]
            rate = item["PASS"] / total * 100 if total else 0
            lines.append(f"{cat}: {item['PASS']}/{total} 通过 ({rate:.0f}%)")
        lines.append("")
        lines.append("失败与异常用例")
        lines.append("-" * 50)
        bad = [r for r in self.results if r["status"] != "PASS"]
        if not bad:
            lines.append("无")
        for r in bad:
            lines.append(f"[{r['id']}] {r['status']} {r['name']}")
            lines.append(f"  分类: {r['category']}")
            lines.append(f"  标准: {r['criteria']}")
            lines.append(f"  原因: {r['reason']}")
            lines.append(f"  工具: {', '.join(r['tool_names']) if r['tool_names'] else '无'}")
            lines.append(f"  回复片段: {r['last_reply_preview']}")
            lines.append("")
        lines.append("全部用例明细")
        lines.append("-" * 50)
        for r in self.results:
            icon = "PASS" if r["status"] == "PASS" else r["status"]
            lines.append(f"[{r['id']}] {icon} {r['name']} ({r['duration_ms']}ms)")
            lines.append(f"  分类: {r['category']}")
            lines.append(f"  标准: {r['criteria']}")
            lines.append(f"  工具: {', '.join(r['tool_names']) if r['tool_names'] else '无'}")
            if r["reason"]:
                lines.append(f"  原因: {r['reason']}")
        return "\n".join(lines)

    def save_report(self, output_path: str) -> None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.generate_report(), encoding="utf-8")
        print(f"\n测试报告已保存到: {path}")

    def save_json(self, output_path: str) -> None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "base_url": self.base_url,
            "stats": self.stats(),
            "results": self.results,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON 结果已保存到: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="MallMind 全量自动化测试脚本")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"后端地址，默认 {DEFAULT_BASE_URL}")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_PATH, help=f"文本报告路径，默认 {DEFAULT_OUTPUT_PATH}")
    parser.add_argument("--json-output", default=DEFAULT_JSON_OUTPUT_PATH, help=f"JSON 报告路径，默认 {DEFAULT_JSON_OUTPUT_PATH}")
    parser.add_argument("--category", default="", help="只运行某个分类，例如 F.PC整机方案")
    parser.add_argument("--id-prefix", default="", help="只运行某个 ID 前缀，例如 TC-5")
    args = parser.parse_args()

    tests = define_test_cases()

    if args.category:
        tests = [t for t in tests if t.category == args.category]
    if args.id_prefix:
        tests = [t for t in tests if t.id.startswith(args.id_prefix)]

    if not tests:
        print("没有匹配的测试用例")
        sys.exit(2)

    runner = FullTestRunner(args.base_url)
    runner.run_all(tests)
    runner.save_report(args.output)
    runner.save_json(args.json_output)


if __name__ == "__main__":
    main()
