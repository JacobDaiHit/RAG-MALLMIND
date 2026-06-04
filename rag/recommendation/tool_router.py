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

from rag.recommendation.llm_client import LLMClientError, OpenAICompatibleChatClient, report_to_dict, run_with_hard_timeout
from rag.recommendation.session_state import (
    ShoppingSession,
    current_topic_json,
    extract_product_ids,
    last_recommended_product_ids,
)
from rag.utils.catalog_scope import normalize_catalog_scope


ALLOWED_TOOL_NAMES = {
    "recommend_shopping_products",
    "generate_pc_build_plan",
    "compare_products",
    "apply_cart_instruction",
    "general_chat",
}
LOCAL_ROUTE_NAMES = [
    "recommend_shopping_products",
    "generate_pc_build_plan",
    "compare_products",
    "apply_cart_instruction",
    "general_chat",
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
ROUTED_CALL_SCHEMA: Dict[str, Any] = {
    "name": "工具名",
    "confidence": "0到1",
    "reason": "选择依据",
    "arguments": {
        "query": "",
        "budget": None,
        "category": "",
        "usage": [],
        "preferences": {},
        "product_ids": [],
        "catalog_scope": "ecommerce",
    },
}

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
    "整机",
    "电脑整机",
    "台式整机",
    "游戏整机",
    "办公整机",
    "电脑主机",
    "台式主机",
    "游戏主机",
    "办公主机",
    "装机",
    "装机单",
    "装机配置",
    "装机方案",
    "配电脑",
    "配台电脑",
    "配一台电脑",
    "配主机",
    "配台主机",
    "配一台主机",
    "组装电脑",
    "组装主机",
    "攒机",
    "电脑配置",
    "主机配置",
    "台式机配置",
    "整套配置",
    "整套配置单",
    "配置单",
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
    "配置",
    "方案",
    "搭配",
    "配一套",
    "给我一套",
    "推荐一套",
    "整套",
    "全套",
    "配一台",
    "主机",
    "电脑",
    "台式机",
]
PC_USE_CASE_TERMS_ZH = [
    "游戏",
    "3A",
    "电竞",
    "办公",
    "学习",
    "剪辑",
    "视频",
    "直播",
    "AI",
    "训练",
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
    "加入车",
    "下单",
    "删除",
    "移除",
    "数量",
    "清空",
    "把这套",
    "把这个加入",
    "把它加入",
]
COMPARE_TERMS = ["对比", "比较", "哪个好", "区别", "差别", "提升在哪"]
GENERAL_CHAT_TERMS = [
    "你是谁",
    "你能做什么",
    "怎么用",
    "如何使用",
    "这个系统",
    "推荐逻辑",
    "为什么这么选",
    "刚才为什么",
    "你用了什么工具",
    "调用了哪个工具",
    "为什么路由",
    "路由原因",
    "走了哪个路由",
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


class RoutedArguments(BaseModel):
    model_config = ConfigDict(extra="allow")

    query: str = ""
    budget: Optional[float] = None
    category: str = ""
    usage: List[str] = Field(default_factory=list)
    preferences: Dict[str, Any] = Field(default_factory=dict)
    product_ids: List[str] = Field(default_factory=list)
    catalog_scope: str = "ecommerce"
    compare_with_previous: bool = False
    quantity: Optional[int] = None
    action: str = ""
    topic: str = ""
    need_full_pc_build: bool = False

    @field_validator("usage", "product_ids", mode="before")
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
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    source: str = "llm"

    @field_validator("name")
    @classmethod
    def _name_allowed(cls, value: str) -> str:
        if value not in ALLOWED_TOOL_NAMES:
            raise ValueError(f"unknown tool name: {value}")
        return value


def route_shopping_tool_call(message: str, session: ShoppingSession, *, use_llm: bool = True) -> Dict[str, Any]:
    """Return one validated tool-call JSON for the current shopping turn."""

    local = local_route_tool_call(message, session)
    llm_call: Optional[Dict[str, Any]] = None
    mode = _runtime_mode_from_session(session)
    llm_skipped = False
    llm_skipped_reason = ""
    if not _router_llm_globally_enabled():
        llm_skipped = True
        llm_skipped_reason = "global_llm_disabled"
    elif not use_llm:
        llm_skipped = True
        llm_skipped_reason = "use_llm_false"
    elif should_skip_llm_route(message, session, local):
        llm_skipped = True
        llm_skipped_reason = "fast_mode" if mode == "fast" else "high_confidence_local"
    elif _router_llm_circuit_open():
        llm_skipped = True
        llm_skipped_reason = "circuit_open"

    if llm_skipped:
        final = validate_and_guard_tool_call(message, session, local, local)
        final["routing_trace"] = {
            "runtime_mode": mode,
            "local": local,
            "local_route_scores": local.get("route_scores"),
            "llm": None,
            "chosen_before_guard": local,
            "final": _trace_identity(final),
            "route_overridden": final.get("name") != local.get("name"),
            "arguments_changed": (final.get("arguments") or {}) != (local.get("arguments") or {}),
            "guard_overridden": final.get("name") != local.get("name"),
            "llm_skipped": True,
            "llm_skipped_reason": llm_skipped_reason,
        }
        return final

    llm_call, llm_failure_reason = try_llm_route_tool_call(message, session)
    if llm_call is None:
        llm_skipped = True
        llm_skipped_reason = llm_failure_reason or "llm_unavailable"
    chosen = llm_call if llm_call and float(llm_call.get("confidence") or 0) >= 0.62 else local
    final = validate_and_guard_tool_call(message, session, chosen, local)
    route_overridden = final.get("name") != (chosen or {}).get("name")
    arguments_changed = (final.get("arguments") or {}) != ((chosen or {}).get("arguments") or {})
    final["routing_trace"] = {
        "runtime_mode": mode,
        "local": local,
        "local_route_scores": local.get("route_scores"),
        "llm": llm_call,
        "chosen_before_guard": chosen,
        "final": _trace_identity(final),
        "route_overridden": route_overridden,
        "arguments_changed": arguments_changed,
        "guard_overridden": route_overridden,
        "llm_skipped": llm_skipped,
        "llm_skipped_reason": llm_skipped_reason,
    }
    return final


def should_skip_llm_route(message: str, session: ShoppingSession, local_call: Dict[str, Any]) -> bool:
    """Avoid expensive LLM routing when local rules are already decisive."""

    mode = _runtime_mode_from_session(session)
    score_info = local_call.get("route_scores") or {}
    confidence = float(score_info.get("confidence") or local_call.get("confidence") or 0)
    margin = float(score_info.get("margin") or 0.0)
    name = str(local_call.get("name") or "")

    if mode == "fast":
        return True

    if mode == "balanced":
        if confidence >= 0.78 and margin >= 0.20:
            return True
        if name in {"apply_cart_instruction", "general_chat"} and confidence >= 0.75:
            return True
        return False

    if mode == "full":
        if confidence >= 0.90 and margin >= 0.35:
            return True
        if name == "apply_cart_instruction" and confidence >= 0.85:
            return True
        return False

    return confidence >= 0.78 and margin >= 0.20


def is_fast_deterministic_case(message: str, session: ShoppingSession) -> bool:
    text = message or ""
    lowered = text.lower()
    topic = current_topic_json(session)
    if _is_general_chat(text, lowered, topic):
        return True
    if _has_cart_intent(text):
        return True
    if _has_pc_intent(text, lowered) or _has_single_pc_part_intent(text, lowered):
        return True
    if detect_normal_product_category(text):
        return True
    if any(term in text for term in PRODUCT_DETAIL_QUERY_TERMS + PRODUCT_DETAIL_FOLLOWUP_TERMS):
        return True
    if any(term in text for term in BRAND_OR_PRODUCT_TERMS):
        return True
    return len(text.strip()) <= 18 and bool(re.search(r"推荐|看看|有哪些|多少钱|价格|参数|库存|有货", text))


def score_local_routes(message: str, session: ShoppingSession) -> Dict[str, Any]:
    text = message or ""
    lowered = text.lower()
    topic = current_topic_json(session)
    slots = extract_slots_rule_based(text)
    scores = {name: 0.0 for name in LOCAL_ROUTE_NAMES}
    pc_intent = _has_pc_intent(text, lowered)
    single_pc_part = _has_single_pc_part_intent(text, lowered)
    pc_followup = topic.get("topic_type") == "pc_build" and _looks_like_pc_followup(text, lowered)

    if _has_cart_intent(text):
        scores["apply_cart_instruction"] += 0.95
    if detect_normal_product_category(text):
        scores["recommend_shopping_products"] += 0.55
    if _has_product_query_intent(text, lowered):
        scores["recommend_shopping_products"] += 0.25
    if single_pc_part:
        scores["recommend_shopping_products"] += 0.60
    if pc_intent:
        scores["generate_pc_build_plan"] += 0.75
    if pc_followup:
        scores["generate_pc_build_plan"] += 0.65
    if _looks_like_compare_request(text):
        if pc_followup or pc_intent:
            scores["generate_pc_build_plan"] += 0.15
        elif _should_compare_products(text, topic, slots, session):
            scores["compare_products"] += 0.55
    if _is_general_chat(text, lowered, topic):
        scores["general_chat"] += 0.80
    if not any(scores.values()):
        scores["recommend_shopping_products"] = 0.45

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_name, top_score = ranked[0]
    second_name, second_score = ranked[1]
    margin = top_score - second_score
    if margin >= 0.50:
        margin_bonus = 0.08
    elif margin >= 0.30:
        margin_bonus = 0.05
    elif margin >= 0.20:
        margin_bonus = 0.03
    else:
        margin_bonus = 0.0
    ambiguity_penalty = 0.0
    if top_score < 0.55:
        ambiguity_penalty += 0.15
    if margin < 0.15:
        ambiguity_penalty += 0.12
    confidence = _clamp(top_score + margin_bonus - ambiguity_penalty, 0.0, 0.99)
    return {
        "scores": scores,
        "top_name": top_name,
        "top_score": top_score,
        "second_name": second_name,
        "second_score": second_score,
        "margin": margin,
        "margin_bonus": margin_bonus,
        "ambiguity_penalty": ambiguity_penalty,
        "confidence": confidence,
    }


def local_route_tool_call(message: str, session: ShoppingSession) -> Dict[str, Any]:
    text = message or ""
    lowered = text.lower()
    topic = current_topic_json(session)
    slots = extract_slots_rule_based(text)
    score_info = score_local_routes(message, session)

    if _has_cart_intent(text):
        return _attach_route_scores(_tool_call("apply_cart_instruction", slots, 0.95, "本地规则识别到购物车操作。", "rules"), score_info)
    if _looks_like_compare_request(text) and _should_compare_products(text, topic, slots, session):
        slots["compare_with_previous"] = _mentions_previous(text)
        return _attach_route_scores(_tool_call("compare_products", slots, 0.88, "本地规则识别到商品或历史方案对比请求。", "rules"), score_info)
    if _has_pc_intent(text, lowered):
        return _attach_route_scores(_tool_call("generate_pc_build_plan", slots, 0.92, "本地规则识别到整机、装机或多配件组合需求。", "rules"), score_info)
    if topic.get("topic_type") == "pc_build" and _looks_like_pc_followup(text, lowered):
        return _attach_route_scores(_tool_call("generate_pc_build_plan", slots, 0.88, "当前主题仍是 PC 整机，用户补充的是颜色、预算或性能偏好。", "topic_memory"), score_info)
    followup_call = resolve_followup_message(text, session)
    if followup_call:
        return _attach_route_scores(followup_call, score_info)

    category = detect_normal_product_category(text)
    if category:
        slots["category"] = category
        slots["catalog_scope"] = "ecommerce"
        return _attach_route_scores(_tool_call("recommend_shopping_products", slots, 0.86, f"用户明确提出普通商品品类：{category}。", "rules"), score_info)
    if _has_single_pc_part_intent(text, lowered):
        slots["category"] = "pc_part"
        slots["catalog_scope"] = "pc_parts"
        return _attach_route_scores(_tool_call("recommend_shopping_products", slots, 0.84, "用户只提到单个 PC 配件，按普通商品推荐处理。", "rules"), score_info)
    if _has_product_query_intent(text, lowered):
        return _attach_route_scores(_tool_call("recommend_shopping_products", slots, 0.84, "本地规则识别到商品搜索、场景购物或事实型商品查询。", "rules"), score_info)
    if _is_general_chat(text, lowered, topic):
        return _attach_route_scores(_tool_call("general_chat", slots, 0.9, "本地规则识别到系统说明、身份或推荐逻辑类问题。", "rules"), score_info)
    return _attach_route_scores(_tool_call("recommend_shopping_products", slots, 0.7, "默认进入普通本地商品推荐。", "rules"), score_info)


def try_llm_route_tool_call(message: str, session: ShoppingSession) -> tuple[Optional[Dict[str, Any]], str]:
    if not _router_llm_globally_enabled():
        return None, "global_llm_disabled"
    if _router_llm_circuit_open():
        return None, "circuit_open"

    acquire_timeout = _env_float("RECOMMENDATION_ROUTER_LLM_ACQUIRE_TIMEOUT_SECONDS", 0.05)
    acquired = _ROUTER_LLM_SEMAPHORE.acquire(timeout=acquire_timeout)
    if not acquired:
        return None, "concurrency_limit"

    try:
        client = OpenAICompatibleChatClient()
        if not client.configured:
            return None, "llm_not_configured"
        if client.config and client.config.timeout_seconds > 5:
            client = OpenAICompatibleChatClient(replace(client.config, timeout_seconds=5))
        payload, report = run_with_hard_timeout(
            lambda: client.chat_json_with_report(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是电商导购系统的工具路由器，只输出 JSON。"
                            "后端会校验并执行工具，你只负责选择工具和抽取参数。"
                            "不要编造商品、价格、库存或优惠。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": build_route_prompt(message, current_topic_json(session)),
                    },
                ],
                model=client.config.fast_model,
                temperature=0,
                max_tokens=700,
            ),
            _llm_timeout("RECOMMENDATION_LLM_ROUTER_TIMEOUT_SECONDS", 3.0),
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
        return None, "llm_timeout_or_error"
    except ValidationError:
        _record_router_llm_failure()
        return None, "llm_validation_error"
    except (LLMClientError, ValueError, TypeError):
        _record_router_llm_failure()
        return None, "llm_timeout_or_error"
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


def validate_and_guard_tool_call(
    message: str,
    session: ShoppingSession,
    tool_call: Dict[str, Any],
    local_call: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    local_call = local_call or local_route_tool_call(message, session)
    call = _parse_or_fallback(tool_call, local_call)
    arguments = merge_route_arguments(call.get("arguments") or {}, extract_slots_rule_based(message))
    call["arguments"] = arguments
    call["confidence"] = _confidence(call.get("confidence"))
    call["reason"] = str(call.get("reason") or "路由器未提供原因。")
    call["source"] = str(call.get("source") or "llm")

    text = message or ""
    lowered = text.lower()
    topic = current_topic_json(session)
    strong_pc = _has_pc_intent(text, lowered)
    single_pc_part = _has_single_pc_part_intent(text, lowered)
    active_pc_followup = topic.get("topic_type") == "pc_build" and _looks_like_pc_followup(text, lowered)
    explicit_normal = detect_normal_product_category(text)
    product_query_intent = _has_product_query_intent(text, lowered)
    followup_call = resolve_followup_message(text, session)

    if _has_cart_intent(text):
        call.update(
            local_call
            if local_call.get("name") == "apply_cart_instruction"
            else _tool_call("apply_cart_instruction", arguments, 0.96, "后端兜底：购物车指令优先。", "guard")
        )
    elif _looks_like_compare_request(text) and _should_compare_products(text, topic, arguments, session):
        arguments["compare_with_previous"] = _mentions_previous(text)
        guarded = _tool_call("compare_products", arguments, max(call["confidence"], 0.88), "后端兜底：明确对比请求走 compare_products。", "guard")
        call.update(guarded)
    elif active_pc_followup and not explicit_normal:
        guarded = _tool_call("generate_pc_build_plan", arguments, max(call["confidence"], 0.9), "后端兜底：PC 主题追问继续调整整机方案。", "guard")
        call.update(guarded)
    elif strong_pc:
        guarded = _tool_call("generate_pc_build_plan", arguments, max(call["confidence"], 0.9), "后端兜底：PC 强意图或 PC 主题追问优先走整机方案。", "guard")
        call.update(guarded)
    elif followup_call:
        merged_followup_args = merge_route_arguments(arguments, followup_call.get("arguments") or {})
        followup_call["arguments"] = merged_followup_args
        followup_call["confidence"] = max(float(followup_call.get("confidence") or 0), call["confidence"], 0.9)
        call.update(followup_call)
    elif explicit_normal or single_pc_part:
        arguments["category"] = explicit_normal or "pc_part"
        arguments["catalog_scope"] = "ecommerce" if explicit_normal else "pc_parts"
        guarded = _tool_call("recommend_shopping_products", arguments, max(call["confidence"], 0.86), f"后端兜底：用户明确切换到普通商品品类 {arguments['category']}。", "guard")
        call.update(guarded)
    elif product_query_intent:
        guarded = _tool_call("recommend_shopping_products", arguments, max(call["confidence"], 0.84), "后端兜底：商品搜索、场景购物或事实型商品查询必须走商品工具。", "guard")
        call.update(guarded)
    elif _is_general_chat(text, lowered, topic):
        call.update(_tool_call("general_chat", arguments, max(call["confidence"], 0.9), "后端兜底：非购物执行问题走 general_chat。", "guard"))

    call["arguments"] = normalize_tool_arguments(call["name"], call.get("arguments") or {})
    return call


def build_route_prompt(message: str, topic_memory: Dict[str, Any]) -> str:
    return (
        "可用工具（标准 OpenAI function tools schema）：\n"
        f"{json.dumps(TOOL_SCHEMAS_FOR_PROMPT, ensure_ascii=False, indent=2)}\n\n"
        "当前短期主题 JSON：\n"
        f"{json.dumps(topic_memory, ensure_ascii=False, indent=2)}\n\n"
        "用户本轮输入：\n"
        f"{message}\n\n"
        "只输出 JSON，格式："
        f"{json.dumps(ROUTED_CALL_SCHEMA, ensure_ascii=False)}"
    )


def extract_slots_rule_based(text: str) -> Dict[str, Any]:
    slots: Dict[str, Any] = {
        "query": " ".join(str(text or "").split()),
        "budget": extract_budget(text),
        "category": detect_normal_product_category(text) or detect_pc_part_category(text),
        "usage": extract_usage(text),
        "preferences": extract_preferences(text),
        "product_ids": extract_product_ids(text),
        "catalog_scope": infer_catalog_scope_for_message(text),
    }
    return slots


def normalize_tool_arguments(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    args = dict(arguments or {})
    if name == "generate_pc_build_plan":
        args["need_full_pc_build"] = True
        args["category"] = "pc_build"
        args.pop("catalog_scope", None)
        args["domain"] = "pc_build"
    elif name in {"compare_products", "apply_cart_instruction"}:
        args["catalog_scope"] = "combined"
    elif name == "recommend_shopping_products":
        args["catalog_scope"] = normalize_catalog_scope(args.get("catalog_scope"))
        if args.get("category") == "pc_part":
            args["catalog_scope"] = "pc_parts"
    if name in {"recommend_shopping_products", "general_chat", "compare_products", "apply_cart_instruction"}:
        args.setdefault("query", "")
    if name == "compare_products":
        product_ids = args.get("product_ids")
        args["product_ids"] = product_ids if isinstance(product_ids, list) else []
        args["compare_with_previous"] = bool(args.get("compare_with_previous"))
    return args


def extract_budget(text: str) -> Optional[float]:
    raw = text or ""
    money = r"(\d+(?:[\s,，]\d{3})*(?:\.\d+)?)\s*(k|K|w|W|千|万|元|块|cny|CNY)?"
    budget_patterns = [
        rf"(?:预算|价格|价位|控制在|不超过|不超|低于|小于|少于|最高|封顶|上限|价钱|最多|至少|按)\s*(?:是|为|在|到)?\s*{money}",
        rf"{money}\s*(?:以内|以下|左右|上下|预算|封顶|档次|价位)",
        rf"(?<![A-Za-z]){money}\s*(?:-|到|至|~|～)\s*{money}",
        rf"(?:<=|<)\s*{money}",
        rf"(?:under|within|below|less than|no more than|budget)\s*{money}",
    ]
    for pattern in budget_patterns:
        match = re.search(pattern, raw, flags=re.I)
        if match:
            return _parse_budget_amount(match.group(1), match.group(2) or "")
    return None


def _parse_budget_amount(value: str, unit: str = "") -> float:
    amount = float(re.sub(r"[\s,，]", "", value))
    normalized = (unit or "").strip().lower()
    if normalized in {"k", "千"}:
        return amount * 1000
    if normalized in {"w", "万"}:
        return amount * 10000
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


def merge_route_arguments(llm_args: Dict[str, Any], rule_args: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(llm_args or {})
    merged["query"] = rule_args.get("query") or merged.get("query") or ""

    rule_ids = rule_args.get("product_ids") if isinstance(rule_args.get("product_ids"), list) else []
    llm_ids = merged.get("product_ids") if isinstance(merged.get("product_ids"), list) else []
    merged["product_ids"] = _dedupe_strings([*llm_ids, *rule_ids])

    if not merged.get("category") and rule_args.get("category"):
        merged["category"] = rule_args["category"]

    merged["catalog_scope"] = normalize_catalog_scope(
        rule_args.get("catalog_scope") or merged.get("catalog_scope") or "ecommerce"
    )

    if rule_args.get("budget") is not None:
        merged["budget"] = rule_args["budget"]

    merged_usage = []
    if isinstance(merged.get("usage"), list):
        merged_usage.extend(str(item) for item in merged["usage"] if str(item).strip())
    if isinstance(rule_args.get("usage"), list):
        merged_usage.extend(str(item) for item in rule_args["usage"] if str(item).strip())
    merged["usage"] = _dedupe_strings(merged_usage)

    preferences = {}
    if isinstance(merged.get("preferences"), dict):
        preferences.update(merged["preferences"])
    if isinstance(rule_args.get("preferences"), dict):
        preferences.update(rule_args["preferences"])
    merged["preferences"] = preferences

    return merged


def _parse_or_fallback(tool_call: Dict[str, Any], local_call: Dict[str, Any]) -> Dict[str, Any]:
    raw = dict(tool_call or {})
    if "tool" in raw and "name" not in raw:
        raw["name"] = raw["tool"]
    if "extracted_slots" in raw and "arguments" not in raw:
        raw["arguments"] = raw["extracted_slots"]
    try:
        return RoutedToolCall.model_validate(raw).model_dump(mode="json")
    except ValidationError:
        return dict(local_call)


def _runtime_mode_from_session(session: ShoppingSession) -> str:
    value = (
        getattr(session, "runtime_mode", None)
        or getattr(session, "selected_mode", None)
        or getattr(session, "mode", None)
        or "balanced"
    )
    value = str(value).lower()
    if value == "auto":
        return "balanced"
    return value if value in {"fast", "balanced", "full"} else "balanced"


def _has_pc_intent(text: str, lowered: str) -> bool:
    raw = text or ""

    if any(term in raw or term.lower() in lowered for term in PC_BUILD_STRONG_TERMS_ZH):
        return True
    if _looks_like_pc_monitor_combo(raw):
        return True

    part_hits = _pc_part_hits(text)
    has_weak = any(term in raw or term.lower() in lowered for term in PC_BUILD_WEAK_TERMS_ZH)
    has_budget_value = extract_budget(raw) is not None
    has_use_case = any(term.lower() in lowered or term in raw for term in PC_USE_CASE_TERMS_ZH)
    has_device = _has_pc_device_term(raw, lowered)
    has_pc_scenario = any(term.lower() in lowered or term in raw for term in PC_SCENARIO_TERMS_ZH)
    has_strong_pc_scenario = any(term.lower() in lowered or term in raw for term in PC_STRONG_SCENARIO_TERMS_ZH)

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


def _looks_like_compare_request(text: str) -> bool:
    return any(term in text for term in COMPARE_TERMS)


def _should_compare_products(text: str, topic: Dict[str, Any], arguments: Dict[str, Any], session: ShoppingSession) -> bool:
    product_ids = arguments.get("product_ids") if isinstance(arguments.get("product_ids"), list) else []
    if len(product_ids) >= 2:
        return True
    if _compare_entity_count(text) >= 2:
        return True
    if _last_result_product_count(session) >= 2 and _looks_like_contextual_compare(text):
        return True
    if _mentions_previous(text) and topic.get("topic_type") in {"pc_build", "comparison", "normal_product", "ecommerce_recommendation", "single_pc_part"}:
        return True
    if any(term in text.lower() for term in [" vs ", "vs", "pk"]):
        return True
    return False


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
    product_ids = last_recommended_product_ids(session)
    query_parts = [str(getattr(session, "last_goal", "") or "").strip(), f"用户追问：{message}"]
    query = "。".join(part for part in query_parts if part)
    return _tool_call(
        "recommend_shopping_products",
        {
            "query": query or str(message or ""),
            "product_ids": product_ids,
            "catalog_scope": current_topic_json(session).get("slots", {}).get("catalog_scope") or "ecommerce",
            "preferences": {"followup_type": "product_detail"},
        },
        0.9,
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


def _confidence(value: Any) -> float:
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(float(value), minimum), maximum)


def _attach_route_scores(call: Dict[str, Any], score_info: Dict[str, Any]) -> Dict[str, Any]:
    call["route_scores"] = score_info
    call["local_rule_confidence"] = float(call.get("confidence") or 0)
    call["route_score_confidence"] = float(score_info.get("confidence") or 0)
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


def _tool_call(name: str, arguments: Dict[str, Any], confidence: float, reason: str, source: str) -> Dict[str, Any]:
    return {
        "name": name,
        "arguments": dict(arguments or {}),
        "confidence": confidence,
        "reason": reason,
        "source": source,
    }
