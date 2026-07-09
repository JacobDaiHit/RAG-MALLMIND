"""LLM-assisted tool routing with deterministic guards for shopping workflows."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import deque
from dataclasses import replace
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from rag.recommendation.llm_client import LLMClientError, OpenAICompatibleChatClient, get_llm_provider_trace, report_to_dict, run_with_hard_timeout
from rag.recommendation.session_state import (
    ShoppingSession,
    current_topic_json,
    extract_product_ids,
    last_recommended_product_ids,
)
from rag.schemas.recommendation import CATEGORY_NAME_TO_KEY, ComponentCategory
from rag.security.prompt_guard import defense_prefix, defense_suffix, wrap_user_input
from rag.utils.catalog_scope import normalize_catalog_scope


ALLOWED_TOOL_NAMES = {
    "recommend_shopping_products",
    "generate_pc_build_plan",
    "compare_products",
    "apply_cart_instruction",
    "general_chat",
    "parameter_query",
    "sku_detail",
    "price_comparison",
}
LOCAL_ROUTE_NAMES = [
    "recommend_shopping_products",
    "generate_pc_build_plan",
    "compare_products",
    "apply_cart_instruction",
    "general_chat",
    "parameter_query",
    "sku_detail",
    "price_comparison",
]

TOOL_SCHEMAS_FOR_PROMPT: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "recommend_shopping_products",
            "description": "根据用户需求推荐普通商品，例如耳机、键盘、手机、显示器、服饰、食品、美妆，或单个 PC 配件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "budget": {"type": ["number", "null"]},
                    "category": {"type": "string"},
                    "usage": {"type": "array", "items": {"type": "string"}},
                    "preferences": {"type": "object"},
                    "product_ids": {"type": "array", "items": {"type": "string"}},
                    "catalog_scope": {"type": "string", "enum": ["ecommerce", "pc_parts", "combined"]},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_pc_build_plan",
            "description": "生成或调整完整 PC 整机方案，包含 CPU、GPU、主板、内存、SSD、电源、机箱、散热等核心组件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "budget": {"type": ["number", "null"]},
                    "usage": {"type": "array", "items": {"type": "string"}},
                    "preferences": {"type": "object"},
                    "product_ids": {"type": "array", "items": {"type": "string"}},
                    "catalog_scope": {"type": "string", "enum": ["ecommerce", "pc_parts", "combined"]},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_products",
            "description": "对比多个商品，或对比当前方案和之前方案。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "product_ids": {"type": "array", "items": {"type": "string"}},
                    "compare_with_previous": {"type": "boolean"},
                    "preferences": {"type": "object"},
                    "catalog_scope": {"type": "string", "enum": ["combined"]},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_cart_instruction",
            "description": "执行购物车操作，例如加入购物车、移除、替换、清空或修改数量。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "product_ids": {"type": "array", "items": {"type": "string"}},
                    "quantity": {"type": ["integer", "null"]},
                    "action": {"type": "string"},
                    "catalog_scope": {"type": "string", "enum": ["combined"]},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parameter_query",
            "description": "查询特定商品的具体参数/规格，如功耗、重量、尺寸、是否支持某功能等。用户已明确指向某款商品且只问一个属性。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "product_mentions": {"type": "array", "items": {"type": "string"}, "description": "用户提到的具体商品型号"},
                    "attribute": {"type": "string", "description": "用户询问的属性（功耗/重量/尺寸/NFC等）"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sku_detail",
            "description": "查询同一商品不同 SKU 变体（如不同存储/内存配置）之间的价格差异。用户消息中包含具体配置参数对比（如'12+256和16+512差多少'）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "product_mentions": {"type": "array", "items": {"type": "string"}, "description": "用户提到的商品型号"},
                    "sku_criteria": {"type": "string", "description": "SKU 筛选条件（如'12+256'、'32G+1TB'）"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "price_comparison",
            "description": "价格比较/价格确认类问题，如'比官网便宜吗'、'京东上这款多少钱'。用户关心的是价格而非推荐新商品。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "product_mentions": {"type": "array", "items": {"type": "string"}, "description": "用户提到的商品型号"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "general_chat",
            "description": "回答与购物执行无关的系统说明、身份、使用方式、推荐逻辑、路由原因或闲聊问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "topic": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
]
TOOL_SCHEMAS = TOOL_SCHEMAS_FOR_PROMPT
PC_STRONG_TERMS = [
    "整机",
    "主机",
    "装机",
    "配电脑",
    "电脑配置",
    "游戏电脑",
    "pc方案",
    "pc 方案",
    "主机方案",
    "整套配置",
    "整套电脑",
    "配置单",
    "攒机",
]
PC_PART_TERMS = [
    "cpu",
    "gpu",
    "i3",
    "i5",
    "i7",
    "i9",
    "b650",
    "b760",
    "z790",
    "ddr4",
    "ddr5",
    "显卡",
    "处理器",
    "主板",
    "内存",
    "电源",
    "机箱",
    "散热",
    "固态",
    "ssd",
    "硬盘",
    "4060",
    "4070",
    "4080",
    "4090",
]
PC_BUILD_CONTEXT_TERMS = [
    "整套",
    "整机",
    "配置",
    "装机",
    "搭配",
    "方案",
    "预算整机",
    "配一台",
    "配个电脑",
    "配电脑",
]
PC_CONTINUATION_TERMS = [
    "预算",
    "加到",
    "降到",
    "便宜",
    "贵",
    "更强",
    "升级",
    "换",
    "改成",
    "颜色",
    "色系",
    "黑色",
    "白色",
    "低噪",
    "静音",
    "安静",
    "对比",
    "比较",
    "提升",
    "上一个",
    "上上个",
]
NORMAL_PRODUCT_TERMS = {
    "耳机": "耳机",
    "蓝牙耳机": "耳机",
    "键盘": "键盘",
    "鼠标": "鼠标",
    "手机": "手机",
    "平板": "平板",
    "显示器": "显示器",
    "外套": "外套",
    "衣服": "服饰",
    "鞋": "服饰",
    "运动鞋": "服饰",
    "跑鞋": "服饰",
    "篮球鞋": "服饰",
    "护肤": "护肤",
    "面霜": "护肤",
    "零食": "食品",
    "饮料": "食品",
}
PC_PART_CATEGORIES = {
    "cpu": "pc_part",
    "gpu": "pc_part",
    "显卡": "pc_part",
    "处理器": "pc_part",
    "主板": "pc_part",
    "内存": "pc_part",
    "电源": "pc_part",
    "机箱": "pc_part",
    "散热": "pc_part",
    "固态": "pc_part",
    "ssd": "pc_part",
    "硬盘": "pc_part",
}


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


_ROUTER_LLM_MAX_CONCURRENCY = max(1, _env_int("RECOMMENDATION_ROUTER_LLM_MAX_CONCURRENCY", 2))
_ROUTER_LLM_SEMAPHORE = threading.BoundedSemaphore(_ROUTER_LLM_MAX_CONCURRENCY)
_ROUTER_LLM_FAILURE_TIMES = deque(maxlen=20)
_ROUTER_LLM_DISABLED_UNTIL = 0.0
_ROUTER_LLM_CIRCUIT_LOCK = threading.Lock()
PC_BUILD_STRONG_TERMS_ZH = [
    "电脑整机",
    "台式整机",
    "游戏整机",
    "办公整机",
    "电脑主机",
    "台式主机",
    "游戏主机",
    "办公主机",
    "配电脑",
    "配台电脑",
    "配一台电脑",
    "配台主机",
    "配一台主机",
    "组装电脑",
    "组装主机",
    "电脑配置",
    "主机配置",
    "台式机配置",
    "整套配置",
    "整套配置单",
    "游戏电脑",
    "办公电脑",
    "剪辑电脑",
    "深度学习电脑",
    "CUDA电脑",
    "pc方案",
    "pc 方案",
    "PC方案",
]
PC_BUILD_WEAK_TERMS_ZH = [
    "主机",
    "电脑",
    "台式机",
]
PC_USE_CASE_TERMS_ZH = [
    "CUDA",
    "深度学习",
    "安静",
    "静音",
    "省电",
    "低功耗",
    "深度学习",
    "本地模型",
    "大模型",
    "LLM",
    "llm",
    "显存",
    "多开",
    "模拟器",
    "安卓模拟器",
    "摄影后期",
    "修图",
    "Lightroom",
    "Photoshop",
    "PS",
    "LR",
    "音乐制作",
    "编曲",
    "DAW",
    "录音",
    "程序开发",
    "开发",
    "编译",
    "Docker",
    "IDE",
    "虚拟机",
    "网游",
    "LOL",
    "瓦罗兰特",
    "CS2",
    "2K",
    "4K",
    "光追",
    "瓶颈",
    "带得动",
    "压得住",
]
PC_SCENARIO_TERMS_ZH = PC_USE_CASE_TERMS_ZH
PC_DEVICE_TERMS_ZH = [
    "主机",
    "电脑",
    "整机",
    "装机",
    "配置",
    "配一套",
    "台式机",
    "PC",
    "pc",
]
PC_MONITOR_COMBO_TERMS_ZH = [
    "主机加显示器",
    "主机和显示器",
    "电脑加显示器",
    "整机加显示器",
    "台式机加显示器",
    "主机+显示器",
    "主机加屏幕",
]
PC_STRONG_SCENARIO_TERMS_ZH = [
    "深度学习",
    "CUDA",
    "本地模型",
    "大模型",
    "LLM",
    "llm",
    "显存",
    "多开",
    "模拟器",
    "安卓模拟器",
    "摄影后期",
    "修图",
    "Lightroom",
    "Photoshop",
    "音乐制作",
    "编曲",
    "DAW",
    "程序开发",
    "编译",
    "Docker",
    "IDE",
    "虚拟机",
    "剪辑视频",
]
PC_PART_CATEGORY_TERMS_ZH = {
    "显卡": "pc_part",
    "cpu": "pc_part",
    "CPU": "pc_part",
    "处理器": "pc_part",
    "主板": "pc_part",
    "内存": "pc_part",
    "SSD": "pc_part",
    "ssd": "pc_part",
    "固态": "pc_part",
    "硬盘": "pc_part",
    "电源": "pc_part",
    "机箱": "pc_part",
    "散热": "pc_part",
}
CART_STRONG_TERMS = [
    "购物车",
    "加购",
    "加入购物车",
    "下单",
    "删除",
    "删掉",
    "删了",
    "移除",
    "去掉",
    "数量",
    "清空",
    "把这套",
    "把这个加入",
    "把它加入",
]
COMPARE_TERMS = ["对比", "比较", "哪个好", "哪个更", "哪款更", "区别", "差别", "提升在哪", "vs", "PK", "pk"]
GENERAL_CHAT_TERMS = [
    "你是谁",
    "你能做什么",
    "这个系统",
    "推荐逻辑",
    "为什么这么选",
    "刚才为什么",
    "你用了什么工具",
    "调用了哪个工具",
]
SEARCH_INTENT_TERMS = [
    "有哪些",
    "有什么",
    "有没有",
    "看看",
    "找",
    "查询",
    "多少钱",
]
SCENARIO_SHOPPING_TERMS = [
    "适合",
    "送礼",
    "送女朋友",
    "学生",
    "学生党",
    "上班族",
    "夏天",
    "通勤",
    "宿舍",
    "办公",
    "游戏",
    "旅行",
]
FACT_QUERY_TERMS = [
    "多少钱",
    "价格",
    "几块",
    "有货",
    "库存",
    "参数",
    "规格",
    "尺寸",
    "屏幕",
    "续航",
    "电池",
    "材质",
    "面料",
    "成分",
    "口味",
    "保质期",
    "优惠",
    "活动",
    "评价",
    "口碑",
]
PRODUCT_DETAIL_QUERY_TERMS = FACT_QUERY_TERMS
PRODUCT_DETAIL_FOLLOWUP_TERMS = [
    "续航",
    "电池",
    "口味",
    "味道",
    "材质",
    "尺寸",
    "屏幕",
    "配置",
    "参数",
    "价格",
    "便宜点",
    "贵一点",
    "不要",
    "换",
    "这款",
    "它",
    "这个",
]
NORMAL_PRODUCT_ALIASES = {
    "数码电子": "数码电子",
    "笔记本电脑": "数码电子",
    "笔记本": "数码电子",
    "电脑": "数码电子",
    "手机": "手机",
    "平板": "平板",
    "耳机": "耳机",
    "蓝牙耳机": "耳机",
    "键盘": "键盘",
    "鼠标": "鼠标",
    "显示器": "显示器",
    "冰箱": "家用电器",
    "家电": "家用电器",
    "礼物": "礼物",
    "咖啡": "食品",
    "零食": "食品",
    "饮料": "食品",
    "T 恤": "服饰",
    "T恤": "服饰",
    "衣服": "服饰",
    "外套": "服饰",
    "鞋": "服饰",
    "运动鞋": "服饰",
    "跑鞋": "服饰",
    "篮球鞋": "服饰",
    "护肤": "护肤",
    "面霜": "护肤",
}
BRAND_OR_PRODUCT_TERMS = [
    "Apple",
    "苹果",
    "iPhone",
    "iPad",
    "MacBook",
    "Mac",
    "华为",
    "小米",
    "Redmi",
    "荣耀",
    "OPPO",
    "vivo",
    "三星",
    "Sony",
    "索尼",
    "iPhone 17 Pro",
]

PC_BUILD_TERMS_ZH = PC_BUILD_STRONG_TERMS_ZH

# ── 新增意图检测词表 ──
# 参数查询：用户问某个具体属性
PARAMETER_QUERY_TERMS = [
    "功耗多少", "功率多少", "重量多少", "多重", "多大", "尺寸多少",
    "支持NFC", "有没有NFC", "防水", "防尘", "刷新率",
    "屏幕尺寸", "电池容量", "内存多大", "存储多大",
    "散热怎么样", "噪音多少",
]
# SKU 查询：用户对比同一商品不同配置
SKU_DETAIL_PATTERNS = [
    r"\d+\+\d+.*差",           # "12+256和16+512差多少"
    r"\d+[gG].*\d+[gG].*差",   # "16G和32G差多少"
    r"\d+[tT].*差",             # "1T和2T差多少"
    r"标准版.*Pro.*差",         # "标准版和Pro版差价"
    r"差价",                     # 直接提到"差价"
    r"差多少钱",                 # "差多少钱"
    r"哪个配置",                 # "哪个配置更划算"
]
# 价格比较/确认：用户在确认价格而非搜索新商品
PRICE_COMPARISON_TERMS = [
    "比官网便宜", "比官网贵", "比京东", "比天猫",
    "官方价", "官网价", "市场价", "零售价",
    "这个价格怎么样", "值不值", "划算吗",
    "便宜吗", "贵吗",
]


class RoutedArguments(BaseModel):
    model_config = ConfigDict(extra="allow")

    query: str = ""
    budget: Optional[float] = None
    category: str = ""
    usage: List[str] = Field(default_factory=list)
    preferences: Dict[str, Any] = Field(default_factory=dict)
    product_ids: List[str] = Field(default_factory=list)
    product_mentions: List[str] = Field(default_factory=list)
    attribute: str = ""
    sku_criteria: str = ""
    catalog_scope: str = "ecommerce"
    compare_with_previous: bool = False
    quantity: Optional[int] = None
    action: str = ""
    topic: str = ""
    need_full_pc_build: bool = False

    @field_validator("usage", "product_ids", "product_mentions", mode="before")
    @classmethod
    def _coerce_string_list(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @field_validator("preferences", mode="before")
    @classmethod
    def _coerce_preferences(cls, value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}


class RoutedToolCall(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    arguments: RoutedArguments = Field(default_factory=RoutedArguments)
    reason: str = ""
    source: str = "llm"

    @field_validator("name")
    @classmethod
    def _name_allowed(cls, value: str) -> str:
        if value not in ALLOWED_TOOL_NAMES:
            raise ValueError(f"unknown tool name: {value}")
        return value


def route_shopping_tool_call(message: str, session: ShoppingSession, *, use_llm: bool = True) -> Dict[str, Any]:
    """Return one validated tool-call JSON for the current shopping turn.

    LLM-first: always try LLM router first.  Fall back to local rules only when
    LLM is unavailable or fails.  No guard layer — the LLM decision is final.
    """

    local = local_route_tool_call(message, session)

    if not use_llm or not _router_llm_globally_enabled():
        local["routing_trace"] = {
            **get_llm_provider_trace(),
            "local": _trace_identity(local),
            "llm": None,
            "llm_router_attempted": False,
            "llm_router_success": False,
            "llm_router_failure_reason": "llm_disabled",
            "router_final_source": "rules",
        }
        return local

    _router_start = time.perf_counter()
    llm_call, llm_failure_reason = try_llm_route_tool_call(message, session)
    _router_elapsed_ms = int((time.perf_counter() - _router_start) * 1000)

    if llm_call is not None:
        llm_call["routing_trace"] = {
            **get_llm_provider_trace(),
            "local": _trace_identity(local),
            "llm": _trace_identity(llm_call),
            "llm_router_attempted": True,
            "llm_router_success": True,
            "llm_router_failure_reason": "",
            "llm_router_elapsed_ms": _router_elapsed_ms,
            "router_final_source": "llm",
        }
        return llm_call

    # LLM failed — fallback to local
    local["routing_trace"] = {
        **get_llm_provider_trace(),
        "local": _trace_identity(local),
        "llm": None,
        "llm_router_attempted": True,
        "llm_router_success": False,
        "llm_router_failure_reason": llm_failure_reason or "llm_unavailable",
        "llm_router_elapsed_ms": _router_elapsed_ms,
        "router_final_source": "rules_fallback",
    }
    return local


# ── 🟢 新增: 路由输出校验层 (②.5) ──

_CATEGORY_WHITELIST = {c.value for c in ComponentCategory}
_MAX_PRICE = 500000
_MIN_SANE_PRICE = 50
_MAX_BRANDS = 50
# 闲聊信号词（LLM 误判推荐时的降级依据）
_GENERAL_CHAT_SIGNALS = {"你好", "谢谢", "再见", "帮助", "怎么用", "你是谁", "hello", "hi", "hey", "thanks"}


def validate_tool_call(
    tool_call: Dict[str, Any],
    local_result: Dict[str, Any],
    message: str,
    session: ShoppingSession,
) -> Dict[str, Any]:
    """Validate and sanitize a routed tool call before dispatch.

    Returns the validated (possibly downgraded) tool_call dict.  Adds
    ``validation`` metadata to the routing_trace so the caller can
    inspect guard decisions.

    校验步骤:
    1. 工具名白名单
    2. 入参值域裁剪
    3. 路由争议检测（LLM vs 本地规则）
    """
    name = tool_call.get("name", "general_chat")
    args = dict(tool_call.get("arguments") or {})
    trace = dict(tool_call.get("routing_trace") or {})
    validation: Dict[str, Any] = {"passed": True, "issues": []}
    downgraded = False
    downgrade_reason = ""

    # ── 白名单校验 ──
    if name not in ALLOWED_TOOL_NAMES:
        validation["issues"].append(f"unknown_tool:{name}")
        validation["passed"] = False
        name = "general_chat"
        args = {"query": message}
        downgraded = True
        downgrade_reason = f"unknown_tool:{name}"

    # ── 值域裁剪 ──
    price_key = next((k for k in ("price_max", "budget") if k in args and args[k] is not None), None)
    if price_key is not None:
        try:
            price_val = float(args[price_key])
        except (TypeError, ValueError):
            price_val = 0.0
        if price_val > _MAX_PRICE:
            args[price_key] = _MAX_PRICE
            validation["issues"].append(f"price_clamped:{price_val}->{_MAX_PRICE}")
        if price_val < _MIN_SANE_PRICE and name not in {"generate_pc_build_plan"} and price_val > 0:
            validation["issues"].append(f"budget_insane:{price_val}")

    # ── category 枚举校验 ──
    if args.get("category") and args["category"] not in _CATEGORY_WHITELIST:
        # 也接受非 PC 的中文分类名
        if args["category"] not in CATEGORY_NAME_TO_KEY and args["category"] not in {"pc_build", "pc_part"}:
            validation["issues"].append(f"unknown_category:{args['category']}")
            # 不清除 category — 下游 structured_filter 会忽略未知值

    # ── brands 列表截断 ──
    for list_key in ("brands", "exclude_brands"):
        if list_key in args and isinstance(args[list_key], list) and len(args[list_key]) > _MAX_BRANDS:
            args[list_key] = args[list_key][:_MAX_BRANDS]
            validation["issues"].append(f"{list_key}_truncated:>{_MAX_BRANDS}")

    # ── LLM vs 本地规则争议检测 ──
    local_name = local_result.get("name", "")
    source = trace.get("router_final_source", "")
    llm_vs_local_conflict = False

    if source == "llm" and local_name and local_name != name:
        # LLM 说 recommend 但 message 包含闲聊信号
        if name in {"recommend_shopping_products", "compare_products"} and any(
            signal in (message or "").lower() for signal in _GENERAL_CHAT_SIGNALS
        ):
            validation["issues"].append("llm_recommend_on_chat_signal")
            validation["passed"] = False
            name = local_name
            downgraded = True
            downgrade_reason = "llm_recommend_on_chat_signal"
            llm_vs_local_conflict = True
        # LLM 说 general_chat 但 message 含明确购物信号 + 本地规则识别到
        elif name == "general_chat" and local_name in {
            "recommend_shopping_products",
            "compare_products",
            "generate_pc_build_plan",
        }:
            validation["issues"].append("llm_chat_on_shopping_signal")
            validation["passed"] = False
            name = local_name
            args = dict(local_result.get("arguments") or args)
            downgraded = True
            downgrade_reason = "llm_chat_on_shopping_signal"
            llm_vs_local_conflict = True

    # ── 🟣 v4: 购物车意图保护 ──
    # LLM 常将含商品关键词的购物车操作消息（如"把手机加入购物车"、"删除购物车里的OPPO"）
    # 误判为 recommend_shopping_products。当消息包含明确购物车操作词时，
    # 无论 LLM 如何路由，强制纠正为 apply_cart_instruction。
    if (
        name != "apply_cart_instruction"
        and _has_cart_intent(message or "")
    ):
        validation["issues"].append("llm_overridden_cart_intent")
        validation["passed"] = False
        name = "apply_cart_instruction"
        args = {
            "query": message,
            "product_ids": args.get("product_ids") or [],
        }
        downgraded = True
        downgrade_reason = "llm_overridden_cart_intent"
        llm_vs_local_conflict = True

    if llm_vs_local_conflict:
        validation["conflict"] = {"llm": name, "local": local_name, "resolved_to": name}

    # ── 组装返回 ──
    validated: Dict[str, Any] = {
        **tool_call,
        "name": name,
        "arguments": args,
    }
    if downgraded:
        validated["downgraded"] = True
        validated["downgrade_reason"] = downgrade_reason
    validated["routing_trace"] = {
        **trace,
        "validation": validation,
    }
    return validated


def validate_and_guard_tool_call(
    message: str,
    session: ShoppingSession,
    tool_call: Dict[str, Any],
) -> Dict[str, Any]:
    """Backward-compatible wrapper for older tests and scripts."""
    local_result = local_route_tool_call(message, session)
    call = dict(tool_call or {})
    trace = dict(call.get("routing_trace") or {})
    trace.setdefault("router_final_source", call.get("source") or "llm")
    call["routing_trace"] = trace
    return validate_tool_call(call, local_result, message, session)

# 已弃置，仅作为诊断元数据写入 routing_trace
def score_local_routes(message: str, session: ShoppingSession) -> Dict[str, Any]:
    text = message or ""
    lowered = text.lower()
    topic = current_topic_json(session)
    slots = extract_slots_rule_based(text)
    scores = {name: 0.0 for name in LOCAL_ROUTE_NAMES}
    pc_intent = _has_pc_intent(text, lowered)
    single_pc_part = _has_single_pc_part_intent(text, lowered)
    pc_followup = topic.get("topic_type") == "pc_build" and _looks_like_pc_followup(text, lowered)
    pc_history_followup = is_pc_build_followup(text, session)

    cart_detected = _has_cart_intent(text)
    category_detected = bool(detect_normal_product_category(text))
    if cart_detected:
        scores["apply_cart_instruction"] += 0.75
    if category_detected:
        scores["recommend_shopping_products"] += 0.55
    if _has_product_query_intent(text, lowered):
        scores["recommend_shopping_products"] += 0.25
    # 组合意图（购物车 + 商品品类）：用户说"推荐X，再加到购物车"等，
    # 首要动作是推荐（加购依赖推荐结果），给推荐额外加分拉大 margin，
    # 使本地路由在组合场景下有足够置信度，减少不必要的 LLM 路由依赖。
    if cart_detected and category_detected:
        scores["recommend_shopping_products"] += 0.10
    if single_pc_part:
        scores["recommend_shopping_products"] += 0.60
    if pc_intent:
        scores["generate_pc_build_plan"] += 0.75
    if pc_followup or pc_history_followup:
        scores["generate_pc_build_plan"] += 0.65
    if _looks_like_compare_request(text):
        if pc_followup or pc_intent:
            scores["generate_pc_build_plan"] += 0.15
        else:
            scores["compare_products"] += 0.55
    if _is_general_chat(text, lowered, topic):
        scores["general_chat"] += 0.80
    if not any(scores.values()):
        scores["recommend_shopping_products"] = 0.45

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_name, top_score = ranked[0]
    second_name, second_score = ranked[1]
    return {
        "scores": scores,
        "top_name": top_name,
        "top_score": top_score,
        "second_name": second_name,
        "second_score": second_score,
    }


def local_route_tool_call(message: str, session: ShoppingSession) -> Dict[str, Any]:
    text = message or ""
    lowered = text.lower()
    topic = current_topic_json(session)
    slots = extract_slots_rule_based(text)
    score_info = score_local_routes(message, session)

    if _has_cart_intent(text):
        # 组合意图（推荐+加购）让 LLM 路由，本地规则仅处理纯购物车操作
        has_recommend_intent = bool(detect_normal_product_category(text)) or _has_product_query_intent(text, lowered)
        if not has_recommend_intent:
            return _attach_route_scores(_tool_call("apply_cart_instruction", slots, "本地规则识别到购物车操作。", "rules"), score_info)
    if is_pc_build_followup(text, session):
        slots["followup"] = True
        slots["source"] = "pc_build_history_guard"
        return _attach_route_scores(_tool_call("generate_pc_build_plan", slots, "PC build history guard: current turn adjusts or compares the previous build plan.", "pc_build_history_guard"), score_info)
    if _looks_like_compare_request(text):
        slots["compare_with_previous"] = _mentions_previous(text)
        return _attach_route_scores(_tool_call("compare_products", slots, "本地规则识别到商品或历史方案对比请求。", "rules"), score_info)
    if _has_sku_detail_intent(text):
        return _attach_route_scores(_tool_call("sku_detail", slots, "本地规则识别到 SKU 配置级价格查询。", "rules"), score_info)
    if _has_price_comparison_intent(text):
        return _attach_route_scores(_tool_call("price_comparison", slots, "本地规则识别到价格比较/确认请求。", "rules"), score_info)
    if _has_parameter_query_intent(text):
        return _attach_route_scores(_tool_call("parameter_query", slots, "本地规则识别到商品参数/规格查询。", "rules"), score_info)
    if _has_pc_intent(text, lowered):
        return _attach_route_scores(_tool_call("generate_pc_build_plan", slots, "本地规则识别到整机、装机或多配件组合需求。", "rules"), score_info)
    if topic.get("topic_type") == "pc_build" and _looks_like_pc_followup(text, lowered):
        return _attach_route_scores(_tool_call("generate_pc_build_plan", slots, "当前主题仍是 PC 整机，用户补充的是颜色、预算或性能偏好。", "topic_memory"), score_info)
    followup_call = resolve_followup_message(text, session)
    if followup_call:
        return _attach_route_scores(followup_call, score_info)

    category = detect_normal_product_category(text)
    if category:
        slots["category"] = category
        slots["catalog_scope"] = "ecommerce"
        return _attach_route_scores(_tool_call("recommend_shopping_products", slots, f"用户明确提出普通商品品类：{category}。", "rules"), score_info)
    if _has_single_pc_part_intent(text, lowered):
        slots["category"] = "pc_part"
        slots["catalog_scope"] = "pc_parts"
        return _attach_route_scores(_tool_call("recommend_shopping_products", slots, "用户只提到单个 PC 配件，按普通商品推荐处理。", "rules"), score_info)
    if _has_product_query_intent(text, lowered):
        return _attach_route_scores(_tool_call("recommend_shopping_products", slots, "本地规则识别到商品搜索、场景购物或事实型商品查询。", "rules"), score_info)
    if _is_general_chat(text, lowered, topic):
        return _attach_route_scores(_tool_call("general_chat", slots, "本地规则识别到系统说明、身份或推荐逻辑类问题。", "rules"), score_info)
    return _attach_route_scores(_tool_call("recommend_shopping_products", slots, "默认进入普通本地商品推荐。", "rules"), score_info)


def try_llm_route_tool_call(message: str, session: ShoppingSession) -> tuple[Optional[Dict[str, Any]], str]:
    if not _router_llm_globally_enabled():
        return None, "global_llm_disabled"
    if _router_llm_circuit_open():
        return None, "circuit_open"

    acquire_timeout = _env_float("RECOMMENDATION_ROUTER_LLM_ACQUIRE_TIMEOUT_SECONDS", 0.5)
    acquired = _ROUTER_LLM_SEMAPHORE.acquire(timeout=acquire_timeout)
    if not acquired:
        return None, "concurrency_limit"

    try:
        client = OpenAICompatibleChatClient()
        if not client.configured:
            return None, "llm_not_configured"
        # 使用专用 env 配置 router socket timeout，默认 15s（之前硬编码 5s 导致 SensNova 等 provider 频繁超时）
        _router_socket_timeout = _env_float("RECOMMENDATION_ROUTER_LLM_SOCKET_TIMEOUT_SECONDS", 15.0)
        if client.config and client.config.timeout_seconds > _router_socket_timeout:
            client = OpenAICompatibleChatClient(replace(client.config, timeout_seconds=_router_socket_timeout))
        payload, report = run_with_hard_timeout(
            lambda: client.chat_json_with_report(
                build_router_messages(message, session),
                model=os.getenv("MALLMIND_ROUTER_MODEL") or client.config.fast_model,
                temperature=0,
                max_tokens=_env_int("RECOMMENDATION_ROUTER_LLM_MAX_TOKENS", 320),
            ),
            _llm_timeout("RECOMMENDATION_LLM_ROUTER_TIMEOUT_SECONDS", 15.0),
            "tool_router",
        )
        parsed = RoutedToolCall.model_validate(payload)
        data = parsed.model_dump(mode="json")
        data["source"] = data.get("source") or "llm"
        data["llm_report"] = report_to_dict(report)
        _record_router_llm_success()
        return data, ""
    except TimeoutError:
        _record_router_llm_failure()
        return None, "llm_timeout"
    except (ValidationError, json.JSONDecodeError):
        _record_router_llm_failure()
        return None, "llm_json_invalid"
    except (LLMClientError, ConnectionError, PermissionError, OSError) as exc:
        _record_router_llm_failure()
        text = str(exc).lower()
        if isinstance(exc, (ConnectionError, PermissionError, OSError)):
            return None, "network_error"
        return None, "llm_timeout" if "timeout" in text or "timed out" in text else "llm_provider_error"
    except (ValueError, TypeError):
        _record_router_llm_failure()
        return None, "llm_json_invalid"
    finally:
        _ROUTER_LLM_SEMAPHORE.release()


def _llm_timeout(name: str, default: float) -> float:
    return _env_float(name, default)


def _router_llm_globally_enabled() -> bool:
    return _env_bool("MALLMIND_LLM_ENABLED", True)


def _router_llm_circuit_open() -> bool:
    with _ROUTER_LLM_CIRCUIT_LOCK:
        return time.time() < _ROUTER_LLM_DISABLED_UNTIL


def _record_router_llm_failure() -> None:
    global _ROUTER_LLM_DISABLED_UNTIL
    now = time.time()
    with _ROUTER_LLM_CIRCUIT_LOCK:
        _ROUTER_LLM_FAILURE_TIMES.append(now)
        threshold = _env_int("RECOMMENDATION_ROUTER_LLM_CIRCUIT_FAILURES", 5)
        cooldown = _env_float("RECOMMENDATION_ROUTER_LLM_CIRCUIT_COOLDOWN_SECONDS", 30.0)
        recent = [item for item in _ROUTER_LLM_FAILURE_TIMES if now - item <= 60]
        if len(recent) >= threshold:
            _ROUTER_LLM_DISABLED_UNTIL = now + cooldown


def _record_router_llm_success() -> None:
    with _ROUTER_LLM_CIRCUIT_LOCK:
        _ROUTER_LLM_FAILURE_TIMES.clear()


# Maps bare product terms detected by the local router to canonical sub_category values.
# This ensures _requirement_from_args_v2 receives sub_category even without LLM routing.
_LOCAL_SUB_CATEGORY_MAP: Dict[str, str] = {
    "手机": "智能手机",
    "耳机": "蓝牙耳机",
    "蓝牙耳机": "蓝牙耳机",
    "平板": "平板电脑",
    "笔记本": "笔记本电脑",
    "笔记本电脑": "笔记本电脑",
    "电脑": "笔记本电脑",
}


def extract_slots_rule_based(text: str) -> Dict[str, Any]:
    category = detect_normal_product_category(text) or detect_pc_part_category(text)
    # Infer sub_category from detected category for known product terms
    sub_category = ""
    if category:
        lowered = text.lower()
        for term, sub_cat in _LOCAL_SUB_CATEGORY_MAP.items():
            if term.lower() in lowered:
                sub_category = sub_cat
                break
    slots: Dict[str, Any] = {
        "query": " ".join(str(text or "").split()),
        "budget": extract_budget(text),
        "category": category,
        "sub_category": sub_category,
        "usage": extract_usage(text),
        "preferences": extract_preferences(text),
        "product_ids": extract_product_ids(text),
        "catalog_scope": infer_catalog_scope_for_message(text),
    }
    return slots




def extract_budget(text: str) -> Optional[float]:
    """Extract budget from text as a FALLBACK when LLM router is unavailable."""
    raw = text or ""
    money = r"(\d+(?:[\s,，]\d{3})*(?:\.\d+)?)\s*(k|K|w|W|千|万|百|亿|千万|百万|十万|元|块|cny|CNY)?"
    budget_patterns = [
        # idx 0: 区间型优先 (X到Y / X-Y / X~Y) → 取上限
        rf"(?<![A-Za-z]){money}\s*(?:-|到|至|~|～)\s*{money}",
        # idx 1: 上限型关键词
        rf"(?:预算|价格|价位|控制在|不要超过|不要超出|别超过|别超出|不超过|不超|低于|小于|少于|最高|封顶|上限|价钱|最多|按)\s*(?:是|为|在)?\s*{money}",
        # idx 2: 后缀型 (X以内 / X以下 / X预算)
        rf"{money}\s*(?:以内|以下|预算|封顶|档次|价位)",
        # idx 3: 左右/上下型
        rf"{money}\s*(?:左右|上下)",
        # idx 4: 符号型
        rf"(?:<=|<)\s*{money}",
        # idx 5: 英文型
        rf"(?:under|within|below|less than|no more than|budget)\s*{money}",
        # idx 6: 中文单位型
        r"(\d+(?:\.\d+)?)\s*(千万|百万|十万|万|千|百|亿)",
    ]
    for idx, pattern in enumerate(budget_patterns):
        match = re.search(pattern, raw, flags=re.I)
        if match:
            if idx == 0:
                # 区间型：取上限值（group 3 = 第二个数字）
                upper_amount = match.group(3)
                if upper_amount is not None:
                    return _parse_budget_amount(upper_amount, match.group(4) or "")
            return _parse_budget_amount(match.group(1), match.group(2) or "")
    return None


def _parse_budget_amount(value: str, unit: str = "") -> float:
    amount = float(re.sub(r"[\s,，]", "", value))
    normalized = (unit or "").strip().lower()
    _cn_multipliers = {
        "亿": 100_000_000,
        "千万": 10_000_000,
        "百万": 1_000_000,
        "十万": 100_000,
        "w": 10_000, "万": 10_000,
        "k": 1_000, "千": 1_000,
        "百": 100,
    }
    if normalized in _cn_multipliers:
        return amount * _cn_multipliers[normalized]
    return amount


def extract_usage(text: str) -> List[str]:
    usage = []
    for term in ["游戏", "办公", "视频", "剪辑", "直播", "AI", "训练", "黑神话", "3A", "深度学习", "CUDA", "大模型", "显存", "多开", "模拟器", "修图", "Lightroom", "Photoshop", "音乐制作", "编曲", "开发", "Docker", "IDE", "虚拟机", "网游", "电竞", "LOL", "瓦罗兰特", "CS2", "2K", "4K", "光追"]:
        if term in text:
            usage.append(term)
    return usage


def extract_preferences(text: str) -> Dict[str, Any]:
    prefs: Dict[str, Any] = {}
    lowered = (text or "").lower()
    if "黑色" in text or "black" in lowered:
        prefs["color"] = "黑色"
    elif "白色" in text or "white" in lowered:
        prefs["color"] = "白色"
    if any(term in text for term in ["低噪", "安静", "静音", "降噪"]):
        prefs["noise"] = "低噪音"
    if any(term in text for term in ["以内", "不超过", "低于", "小于", "封顶", "<="]):
        prefs["budget_strict"] = True
    if any(term in text for term in ["更强", "升级", "提升"]):
        prefs["performance"] = "upgrade"
    return prefs


def detect_normal_product_category(text: str) -> str:
    lowered = (text or "").lower()
    if _looks_like_pc_monitor_combo(text or ""):
        return ""
    for term, category in NORMAL_PRODUCT_ALIASES.items():
        if term == "显示器" and _has_pc_device_term(text or "", lowered):
            continue
        if _contains_term(text, lowered, term):
            return category
    for term, category in NORMAL_PRODUCT_TERMS.items():
        if term in text:
            return category
    return ""


def detect_pc_part_category(text: str) -> str:
    if any(term in (text or "") for term in PC_PART_CATEGORY_TERMS_ZH):
        return "pc_part"
    for term, category in PC_PART_CATEGORIES.items():
        if _pc_part_term_matches(text, term):
            return category
    return ""


def infer_catalog_scope_for_message(text: str) -> str:
    lowered = (text or "").lower()
    if _has_single_pc_part_intent(text, lowered):
        return "pc_parts"
    return "ecommerce"


def _has_pc_intent(text: str, lowered: str) -> bool:
    raw = text or ""

    if any(term in raw or term.lower() in lowered for term in PC_BUILD_STRONG_TERMS_ZH):
        return True
    if _looks_like_pc_monitor_combo(raw):
        return True

    part_hits = _pc_part_hits(text)
    has_weak = any(term in raw or term.lower() in lowered for term in PC_BUILD_WEAK_TERMS_ZH)
    has_budget_value = extract_budget(raw) is not None or bool(re.search(r"\d{3,6}\s*(?:元|块|rmb|cny)?", raw, flags=re.I))
    has_use_case = any(term.lower() in lowered or term in raw for term in PC_USE_CASE_TERMS_ZH)
    has_device = _has_pc_device_term(raw, lowered)
    has_pc_scenario = any(term.lower() in lowered or term in raw for term in PC_SCENARIO_TERMS_ZH)
    has_strong_pc_scenario = any(term.lower() in lowered or term in raw for term in PC_STRONG_SCENARIO_TERMS_ZH)

    if has_budget_value and part_hits and any(term in raw for term in ["配一套", "装机", "整机", "主机", "台式机", "配置一台", "配台"]):
        return True

    if has_budget_value and any(term in raw for term in ["配一台", "游戏主机", "主机", "台式机", "整机", "装机"]):
        return True

    if has_budget_value and any(term in raw for term in ["保留显卡", "其他配件"]):
        return True

    if has_budget_value and has_pc_scenario and has_device:
        return True

    if has_budget_value and has_strong_pc_scenario:
        return True

    if has_budget_value and any(term in raw for term in ["档次", "不要太丐", "用料"]):
        return True

    if has_pc_scenario and has_device and any(term in raw for term in ["配", "方案", "怎么配", "推荐"]):
        return True

    if any(term in raw for term in ["瓶颈", "兼容", "带得动", "压得住"]):
        return bool(part_hits) or has_device

    if _looks_like_single_pc_part_query(text, lowered):
        return False

    if has_weak and len(part_hits) >= 1:
        return True

    if has_weak and has_budget_value and has_use_case:
        return True

    return len(part_hits) >= 3


def _has_single_pc_part_intent(text: str, lowered: str) -> bool:
    return bool(detect_pc_part_category(text)) and not _has_pc_intent(text, lowered)


def _looks_like_single_pc_part_query(text: str, lowered: str) -> bool:
    if not detect_pc_part_category(text):
        return False
    if any(term in text or term.lower() in lowered for term in PC_BUILD_STRONG_TERMS_ZH + PC_BUILD_WEAK_TERMS_ZH):
        return False
    single_terms = ["推荐", "买", "一款", "一个", "看看", "显卡", "cpu", "CPU", "主板", "内存", "ssd", "SSD", "电源", "机箱", "散热"]
    return any(term in text or term in lowered for term in single_terms)


def is_pc_build_followup(message: str, session: ShoppingSession) -> bool:
    if not getattr(session, "pc_build_history", None):
        return False
    text = "".join(str(message or "").split())
    lowered = text.lower()
    if not text:
        return False

    previous_plan_terms = ["上一套", "上套", "刚才那套", "之前那套", "这套", "现在这套", "方案", "配置", "整机", "装机", "主机"]
    adjust_terms = ["换成", "换强", "换", "升级", "降到", "降低", "加到", "减到", "压到", "预算", "保留", "其他配件", "强一点", "更强", "便宜点",
                    "不要", "只要", "改成", "不用", "要Intel", "要AMD", "要NVIDIA"]  # 🟢 扩展品牌拒绝/偏好词
    pc_part_terms = ["显卡", "gpu", "cpu", "处理器", "主板", "内存", "硬盘", "ssd", "电源", "机箱", "散热", "风冷", "水冷"]
    compare_terms = ["差别", "区别", "对比", "比较", "哪里不一样", "提升在哪"]

    has_previous_reference = any(term in text for term in previous_plan_terms)
    has_adjustment = any(term in text or term in lowered for term in adjust_terms)
    has_pc_part = any(term in text or term in lowered for term in pc_part_terms)
    has_compare = any(term in text for term in compare_terms)

    # 🟢 新增分支 D：有PC配件词 + session中有PC构建历史 → 直接判为followup
    if has_pc_part and bool(getattr(session, "pc_build_history", None)):
        return True

    if has_previous_reference and (has_adjustment or has_compare or has_pc_part):
        return True
    if has_pc_part and has_adjustment:
        return True
    if has_adjustment and len(text) <= 32:
        return True
    return False


def _looks_like_pc_followup(text: str, lowered: str) -> bool:
    zh_terms = ["便宜", "预算", "降到", "强一点", "更强", "升级", "换", "白色", "黑色", "对比", "保留显卡", "瓶颈", "功耗", "升级路径", "为什么", "显示器"]
    return any(term in text or term in lowered for term in [*PC_CONTINUATION_TERMS, *zh_terms])


def _has_pc_device_term(text: str, lowered: str) -> bool:
    return any(term in text or term.lower() in lowered for term in PC_DEVICE_TERMS_ZH)


def _looks_like_pc_monitor_combo(text: str) -> bool:
    raw = text or ""
    if any(term in raw for term in PC_MONITOR_COMBO_TERMS_ZH):
        return True
    return "显示器" in raw and any(term in raw for term in ["主机", "电脑", "整机", "装机", "台式机"])


def _has_cart_intent(text: str) -> bool:
    if any(term in text for term in CART_STRONG_TERMS):
        return True
    return bool(re.search(r"把(这个|它|这套|以上|第?\d+个).{0,8}(加入|加到|放进).{0,4}(车|购物车)", text or ""))


def _has_sku_detail_intent(text: str) -> bool:
    """Detect SKU-level queries: price differences between configurations."""
    import re as _re
    return any(_re.search(pat, text) for pat in SKU_DETAIL_PATTERNS)


def _has_parameter_query_intent(text: str) -> bool:
    """Detect parameter/spec queries about a specific product."""
    return any(term in text for term in PARAMETER_QUERY_TERMS)


def _has_price_comparison_intent(text: str) -> bool:
    """Detect price comparison/confirmation queries (not product search)."""
    return any(term in text for term in PRICE_COMPARISON_TERMS)


def _looks_like_compare_request(text: str) -> bool:
    return any(term in text for term in COMPARE_TERMS)




def _mentions_previous(text: str) -> bool:
    return any(term in text for term in ["上一个", "上个", "刚才", "上一版", "之前", "前面"])


def _compare_entity_count(text: str) -> int:
    normalized = (text or "").lower().replace(" ", "")
    entities = set(extract_product_ids(text))
    entities.update(re.findall(r"(?:rtx)?40[0-9]{2}(?:ti)?|rx\d{4}(?:xt)?", normalized))
    return len(entities)


def _looks_like_contextual_compare(text: str) -> bool:
    return any(term in text for term in ["这两个", "这几款", "这几个", "哪个更好", "哪个更值得", "哪个值得买", "对比一下", "比较一下", "哪个好"])


def _last_result_product_count(session: ShoppingSession) -> int:
    result = getattr(session, "last_result", None) or {}
    cards = result.get("product_cards") or []
    if cards:
        return len(cards)
    rows = result.get("rows") or result.get("comparison_table") or []
    if rows:
        return len(rows)
    plans = result.get("plans") or []
    if plans:
        return len(plans)
    return 0


def _is_general_chat(text: str, lowered: str, topic: Optional[Dict[str, Any]] = None) -> bool:
    if any(term in text or term in lowered for term in GENERAL_CHAT_TERMS):
        return True
    if _has_active_shopping_topic(topic) and _looks_like_short_preference_followup(text):
        return False
    if _has_product_query_intent(text, lowered):
        return False
    shopping_signals = [
        *NORMAL_PRODUCT_TERMS.keys(),
        *NORMAL_PRODUCT_ALIASES.keys(),
        *BRAND_OR_PRODUCT_TERMS,
        *SEARCH_INTENT_TERMS,
        *SCENARIO_SHOPPING_TERMS,
        *FACT_QUERY_TERMS,
        *PC_STRONG_TERMS,
        *CART_STRONG_TERMS,
        *COMPARE_TERMS,
        "推荐",
        "买",
        "预算",
        "价格",
    ]
    return not any(term in text or term in lowered for term in shopping_signals) and not _pc_part_hits(text)


def _has_product_query_intent(text: str, lowered: str) -> bool:
    if contains_product_category_or_brand(text):
        return True
    return _has_any_term(text, lowered, [*SEARCH_INTENT_TERMS, *SCENARIO_SHOPPING_TERMS, *FACT_QUERY_TERMS])


def resolve_followup_message(message: str, session: ShoppingSession) -> Optional[Dict[str, Any]]:
    if not has_last_recommendation(session) or not is_product_detail_followup(message):
        return None
    topic = current_topic_json(session)
    product_ids = last_recommended_product_ids(session)
    query_parts = [str(getattr(session, "last_goal", "") or "").strip(), f"用户追问：{message}"]
    query = "。".join(part for part in query_parts if part)
    # 当会话主题仍在购物车操作上下文中时，追问应继续走购物车工具，
    # 而非强制回到推荐——避免 followup_guard 在购物车多轮中错误地覆盖路由。
    if topic.get("topic_type") == "cart":
        return _tool_call(
            "apply_cart_instruction",
            {"query": query or str(message or "")},
            "购物车主题下的追问，继续走购物车工具。",
            "followup_guard",
        )
    return _tool_call(
        "recommend_shopping_products",
        {
            "query": query or str(message or ""),
            "product_ids": product_ids,
            "catalog_scope": current_topic_json(session).get("slots", {}).get("catalog_scope") or "ecommerce",
            "preferences": {"followup_type": "product_detail"},
        },
        "用户在上一轮推荐后追问商品详情。",
        "followup_guard",
    )


def has_last_recommendation(session: ShoppingSession) -> bool:
    result = getattr(session, "last_result", None) or {}
    if result.get("type") == "pc_build_plan":
        return False
    return bool(last_recommended_product_ids(session) or result.get("product_cards") or result.get("plans"))


def is_product_detail_followup(message: str) -> bool:
    text = "".join(str(message or "").split())
    lowered = text.lower()
    if not text:
        return False
    if len(text) <= 32 and _has_any_term(text, lowered, PRODUCT_DETAIL_FOLLOWUP_TERMS):
        return True
    return _has_any_term(text, lowered, FACT_QUERY_TERMS)


def contains_product_category_or_brand(text: str) -> bool:
    lowered = (text or "").lower()
    return bool(
        detect_normal_product_category(text)
        or detect_pc_part_category(text)
        or extract_product_ids(text)
        or _has_any_term(text, lowered, BRAND_OR_PRODUCT_TERMS)
    )


def _has_any_term(text: str, lowered: str, terms: List[str]) -> bool:
    return any(_contains_term(text, lowered, term) for term in terms)


def _contains_term(text: str, lowered: str, term: str) -> bool:
    if not term:
        return False
    return term in text or term.lower() in lowered


def _has_active_shopping_topic(topic: Optional[Dict[str, Any]]) -> bool:
    return (topic or {}).get("topic_type") in {"normal_product", "ecommerce_recommendation", "single_pc_part", "pc_build", "comparison", "cart"}


def _looks_like_short_preference_followup(text: str) -> bool:
    clean = "".join(str(text or "").split())
    if len(clean) > 24:
        return False
    terms = ["适合", "女生", "男朋友", "女朋友", "通勤", "学生党", "续航", "轻一点", "便携", "安静", "降噪", "白色", "黑色", "耐用", "送礼", "便宜", "贵", "预算", "降", "加", "换", "改成", "更强", "升级"]
    return any(term in clean for term in terms)


def _pc_part_hits(text: str) -> List[str]:
    return _dedupe_strings([*[term for term in PC_PART_TERMS if _pc_part_term_matches(text, term)], *_gpu_model_hits(text)])


def _pc_part_term_matches(text: str, term: str) -> bool:
    lowered = (text or "").lower()
    if term in {"cpu", "gpu"}:
        return bool(re.search(rf"\b{term}\b", lowered))
    if re.fullmatch(r"40[0-9]{2}", term):
        return bool(re.search(rf"\b(?:rtx\s*)?{term}(?:\s*ti)?\b", lowered))
    return term in text or term in lowered


def _gpu_model_hits(text: str) -> List[str]:
    lowered = (text or "").lower()
    return re.findall(r"\b(?:rtx\s*)?40[0-9]{2}(?:\s*ti)?\b|\brx\s?\d{4}(?:\s?xt)?\b", lowered)


def _attach_route_scores(call: Dict[str, Any], score_info: Dict[str, Any]) -> Dict[str, Any]:
    call["route_scores"] = score_info
    return call


def _dedupe_strings(items: List[Any]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _trace_identity(call: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    call = call or {}
    return {
        "name": call.get("name"),
        "arguments": call.get("arguments") or {},
    }


def _tool_call(name: str, arguments: Dict[str, Any], reason: str, source: str) -> Dict[str, Any]:
    return {
        "name": name,
        "arguments": dict(arguments or {}),
        "reason": reason,
        "source": source,
    }




def build_router_messages(message: str, session=None) -> List[Dict[str, str]]:
    """Build the full [system, user] message list for the LLM router."""

    return [
        {"role": "system", "content": _build_router_system_prompt()},
        {"role": "user", "content": _build_router_user_prompt(message, session)},
    ]


def _build_router_system_prompt() -> str:
    """System prompt: role, tool definitions, routing rules, output format."""

    return (
        f"{defense_prefix()}\n\n"
        "你是电商导购系统的工具路由器。根据用户输入选择正确的工具并提取参数，输出严格 JSON。\n"
        "不要编造商品、价格、库存或优惠信息。仅输出 JSON，不要额外解释。\n\n"

        # 🟢 新增: 推荐模式选择规则
        "## 推荐模式选择规则\n"
        "系统支持三种推荐模式，请根据用户需求和上下文选择:\n\n"
        "1. **单品推荐** (recommend_shopping_products, need_bundle=false)\n"
        "   - 用户只需要单个品类的一个商品（面霜、耳机、手机）\n"
        "   - 用户提到单个 PC 配件（\"推荐一款显卡\"）→ catalog_scope=pc_parts\n\n"
        "2. **组合推荐** (recommend_shopping_products, need_bundle=true)\n"
        "   - 用户需要多个互补商品（\"去三亚的防晒一套\"、\"配齐护肤套装\"）\n"
        "   - 触发词：一套、全套、搭配、组合、套装、穿搭、配齐、旅行装备\n\n"
        "3. **PC 整机方案** (generate_pc_build_plan)\n"
        "   - 用户要配完整的电脑主机\n"
        "   - 触发词：配电脑、装机、整机、配置单、配一台\n"
        "   - 如果已在 PC 构建话题中，后续修改也继续使用此工具\n\n"
        "## 话题切换判断\n"
        "- 如果 Accumulated state 显示 PC 构建话题，用户说\"换个话题，推荐手机\"→ 切换为单品推荐\n"
        "- 如果当前是商品推荐话题，用户说\"配台电脑\"→ 切换为 PC 整机方案\n"
        "- 用户说\"不要了\"\"算了\"\"看看别的\"→ 可能是话题切换，根据后续内容重新判断\n"
        "- 不确定时，优先保持当前话题\n\n"

        "## 输出 Schema（所有字段必须输出，无法提取的设为 null 或 []）\n"
        "{\n"
        '  "name": "工具名",\n'
        '  "arguments": {\n'
        '    "query": "用户原始输入",\n'
        '    "category": "beauty|digital|clothing|food|null",\n'
        '    "sub_category": "标准值|null",\n'
        '    "catalog_scope": "ecommerce|pc_parts",\n'
        '    "brands": ["品牌"],\n'
        '    "exclude_brands": ["排除品牌"],\n'
        '    "price_min": null,\n'
        '    "price_max": null,\n'
        '    "budget": null,\n'
        '    "is_explicit_budget": true,\n'
        '    "must_have_terms": ["属性词"],\n'
        '    "sort_order": "price_asc|price_desc|rating_desc|null",\n'
        '    "action": "add_to_cart 或空",\n'
        '    "product_ids": ["商品ID"],\n'
        '    "product_mentions": ["用户提到的具体商品型号"],\n'
        '    "attribute": "用户询问的属性（仅 parameter_query）",\n'
        '    "sku_criteria": "SKU筛选条件（仅 sku_detail）",\n'
        '    "quantity": null,\n'
        '    "compare_with_previous": false,\n'
        '    "usage": ["使用场景"],\n'
        '    "preferences": {},\n'
        '    "topic": "",\n'
        '    "need_full_pc_build": false\n'
        "  },\n"
        '  "source": "llm"\n'
        "}\n\n"

        "## 可用工具\n"
        "### 1. recommend_shopping_products\n"
        "商品搜索、推荐、筛选、属性询问。用户说\"买XX\"时加 action=\"add_to_cart\"。\n"
        "### 2. compare_products\n"
        "用户明确要求比较两个具体商品时使用。\"哪个更适合学生\"不属于对比。\n"
        "### 3. apply_cart_instruction\n"
        "购物车操作：加购、查看、修改数量、删除、清空。\n"
        "### 4. generate_pc_build_plan\n"
        "PC 整机配置单。用于：(1) 新装机需求，(2) 对已有PC方案的修改（换CPU品牌、加内存、改预算、换显卡等）。\n"
        "如果 Accumulated state 显示上一轮使用了 generate_pc_build_plan，且用户新消息是对已有方案的修改，"
        "必须继续使用 generate_pc_build_plan，不得切换到 recommend_shopping_products。\n"
        "PC方案修改的例子：\"CPU要Intel的，不要AMD\"、\"内存升级到32G\"、\"显卡换成RTX 4070\"、\"预算加到一万\"。\n"
        "### 5. general_chat\n"
        "仅用于与购物完全无关的问题。涉及具体商品名必须用其他工具。\n"
        "### 6. parameter_query\n"
        "用户已明确指向某款商品，只问一个具体参数/规格（功耗、重量、尺寸、是否支持某功能）。\n"
        "必须提取 product_mentions（商品型号）和 attribute（属性名）。\n"
        "示例：\"这个显卡功耗多少\"→ product_mentions:[\"该显卡\"], attribute:\"功耗\"\n\n"
        "### 7. sku_detail\n"
        "用户询问同一商品不同配置/变体之间的价格差异。\n"
        "触发模式：\"12+256和16+512差多少\"、\"32G+1TB什么价\"、\"标准版和Pro版差价\"。\n"
        "必须提取 product_mentions 和 sku_criteria。\n\n"
        "### 8. price_comparison\n"
        "用户关心的是价格信息而非推荐新商品。\n"
        "触发模式：\"比官网便宜吗\"、\"京东上这款多少钱\"、\"这个价格怎么样\"。\n"
        "与 recommend_shopping_products 的区别：用户不是在搜索新商品，而是在确认/比较已知商品的价格。\n\n"

        "## category 枚举\n"
        "- beauty: 美妆护肤（面霜/精华/面膜/眉笔/粉底液）\n"
        "- digital: 数码电子（手机/平板/笔记本/耳机/PC配件）\n"
        "- clothing: 服饰运动/箱包/户外（跑鞋/徒步鞋/背包/衣服/帽子）\n"
        "- food: 食品饮料（牛奶/咖啡/零食/饮料）\n\n"

        "## sub_category 标准值（必须严格使用）\n"
        "普通商品：背包、跑步鞋、徒步鞋、篮球鞋、运动短裤、运动长裤、速干T恤、卫衣、瑜伽裤、户外裤、"
        "帽子、防晒、面霜、精华、眼霜、面膜、洁面、化妆水、卸妆、唇釉、粉底液、蜜粉、眉笔、牛奶、酸奶、"
        "咖啡、功能饮料、茶饮、碳酸饮料、坚果/零食、方便食品、调味品、智能手机、平板电脑、笔记本电脑、真无线耳机\n"
        "PC配件：显卡、CPU、主板、内存、固态硬盘、电源、机箱、散热器\n"
        "映射：双肩包→背包，手机→智能手机，笔记本→笔记本电脑，耳机→真无线耳机\n\n"

        "## catalog_scope\n"
        "普通商品→ecommerce。PC配件→pc_parts（此时 category=digital）。\n\n"

        "## brands 规则\n"
        "- 商品制造商品牌（华为/小米/Nike/华硕）→ brands\n"
        "- 芯片厂商不是品牌：NVIDIA→must_have_terms含GeForce或RTX，AMD→含Radeon或RX，Intel→含Core或酷睿，Gore-Tex→含Gore-Tex\n"
        "- 用户要\"替代品\"或\"其他品牌都可以\"→ brands:[]\n"
        "- 用户说\"不要X\"→ exclude_brands:[\"X\"]\n"
        "- Accumulated state 中 exclude_brands 与本轮 brands 冲突时，从 exclude 中删除\n\n"

        "## 价格规则\n"
        "仅当用户明确提到预算约束时输出 price_min/price_max/budget，并设 is_explicit_budget=true。\n"
        "用户询问商品价格（\"多少钱\"\"价格\"）→ is_explicit_budget=false，不输出 price_min/price_max/budget。\n"
        "① 区间型（\"3000到5000\"）→ price_min=3000, price_max=5000, budget=null\n"
        "② 上限型（\"不超过5000\"）→ price_max=5000, budget=5000, price_min=null\n"
        "③ 下限型（\"3000以上\"）→ price_min=3000, price_max=null, budget=null\n"
        "④ 约数型（\"5000左右\"）→ price_min=4000, price_max=6000, budget=null\n"
        "⑤ 总预算型（\"总共不超过1万\"）→ budget=10000, price_max=10000, price_min=null\n"
        "⑥ 询问价格（\"多少钱\"）→ price_min=null, price_max=null, budget=null, is_explicit_budget=false\n"
        "一个查询只匹配一种类型。区间型绝不提取 budget。\n\n"

        "## 示例\n"
        "用户：\"预算5000以内推荐轻薄本\"\n"
        '{"name":"recommend_shopping_products","arguments":{"query":"预算5000以内推荐轻薄本",'
        '"category":"digital","sub_category":"笔记本电脑","catalog_scope":"ecommerce",'
        '"brands":[],"exclude_brands":[],"action":"","product_ids":[],'
        '"price_min":null,"price_max":5000,"budget":5000,"is_explicit_budget":true,'
        '"must_have_terms":["轻薄"],"sort_order":null,"quantity":null,"compare_with_previous":false,'
        '"usage":[],"preferences":{},"topic":"","need_full_pc_build":false},"source":"llm"}\n\n'
        "用户：\"那个联想电脑7999元怎么样？\"\n"
        '{"name":"recommend_shopping_products","arguments":{"query":"那个联想电脑7999元怎么样？",'
        '"category":"digital","sub_category":"笔记本电脑","catalog_scope":"ecommerce",'
        '"brands":["联想"],"exclude_brands":[],"action":"","product_ids":[],'
        '"price_min":null,"price_max":null,"budget":null,"is_explicit_budget":false,'
        '"must_have_terms":[],"sort_order":null,"quantity":null,"compare_with_previous":false,'
        '"usage":[],"preferences":{},"topic":"","need_full_pc_build":false},"source":"llm"}\n\n'
        "用户：\"推荐跑鞋并加到购物车\"\n"
        '{"name":"recommend_shopping_products","arguments":{"query":"推荐跑鞋并加到购物车",'
        '"category":"clothing","sub_category":"跑步鞋","catalog_scope":"ecommerce",'
        '"brands":[],"exclude_brands":[],"action":"add_to_cart","product_ids":[],'
        '"price_min":null,"price_max":null,"budget":null,"is_explicit_budget":false,'
        '"must_have_terms":[],"sort_order":null,"quantity":null,"compare_with_previous":false,'
        '"usage":[],"preferences":{},"topic":"","need_full_pc_build":false},"source":"llm"}\n'
        f"\n{defense_suffix()}"
    )


def _build_router_user_prompt(message: str, session=None) -> str:
    """User prompt: session context + user message. No tool definitions."""

    parts = []

    if session is not None:
        current = getattr(session, "current", None) or {}
        recent_queries = getattr(session, "recent_queries", None) or []
        chat_topic = getattr(session, "chat_topic", "")

        if current:
            # PC 配件场景：不注入 sub_category 和 must_have_terms，避免 LLM 从累积状态继承
            # 这些字段是组件级约束，每轮应由 LLM 根据当前查询重新判断
            prompt_current = dict(current)
            if prompt_current.get("catalog_scope") == "pc_parts":
                prompt_current.pop("sub_category", None)
                prompt_current.pop("must_have_terms", None)
            parts.append(f"Accumulated state: {json.dumps(prompt_current, ensure_ascii=False)}")
        if recent_queries:
            queries_str = "; ".join(q.get("query", "") for q in recent_queries[-3:])
            parts.append(f"Recent queries: {queries_str}")
        if chat_topic:
            parts.append(f"Chat topic: {chat_topic}")

        # 🟢 注入 topic_memory 的关键上下文（话题类型 + 路由来源）
        topic = getattr(session, "topic_memory", None) or {}
        topic_type = topic.get("topic_type", "")
        if topic_type == "pc_build":
            parts.append("当前话题: PC装机方案。用户可能在修改或追问已生成的配置。如有新硬件需求，继续使用 generate_pc_build_plan。")
        elif topic_type == "ecommerce_recommendation":
            parts.append("当前话题: 商品推荐。用户在筛选或追问商品细节。")

        cart = getattr(session, "cart", None) or {}
        if cart:
            items = [f"{pid} x{item.quantity}" for pid, item in list(cart.items())[:5]]
            parts.append(f"Cart({len(cart)}): {', '.join(items)}")

    parts.append(wrap_user_input(str(message or ""), max_len=500))
    return "\n".join(parts)
