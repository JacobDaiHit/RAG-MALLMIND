"""Natural-language shopping intent parsing and result enrichment."""
from __future__ import annotations

import json
import logging
import os
import re
import threading as _threading
import time
from typing import Any, Dict, List, Optional

from rag.recommendation.input_preprocessor import clean_text
from rag.recommendation.explanation_builder import build_evidence_grounded_explanation
from rag.recommendation.package_builder import build_recommendation_result
from rag.recommendation.llm_client import LLMClientError, OpenAICompatibleChatClient, get_llm_provider_trace, report_to_dict, run_with_hard_timeout
from rag.recommendation.brand_normalizer import canonicalize_brand_terms
from rag.recommendation.query_guards import is_pc_query
from rag.security.prompt_guard import defense_prefix, defense_suffix, wrap_user_input
from rag.schemas import BudgetLevel, ComponentCategory, RecommendationResult, RequirementLevel, RequirementSpec
from rag.schemas.recommendation import CATEGORY_NAME_TO_KEY
from rag.utils.runtime_errors import public_error


logger = logging.getLogger(__name__)


class InvalidGoalError(ValueError):
    """Raised when the input does not look like a shopping request."""


SHOPPING_GOAL_KEYWORDS = [
    "推荐",
    "买",
    "购买",
    "导购",
    "商品",
    "护肤",
    "美妆",
    "洗面奶",
    "面霜",
    "精华",
    "防晒",
    "眼霜",
    "手机",
    "耳机",
    "蓝牙耳机",
    "降噪",
    "电脑",
    "平板",
    "相机",
    "键盘",
    "鼠标",
    "数码",
    "穿搭",
    "衣服",
    "鞋",
    "跑鞋",
    "运动",
    "零食",
    "饮料",
    "咖啡",
    "食品",
    "同款",
    "购物车",
    "下单",
    "对比",
    "预算",
    "显卡",
    "CPU",
    "主板",
    "内存",
    "SSD",
    "电源",
    "机箱",
    "散热",
    "有哪些",
    "有什么",
    "有没有",
    "看看",
    "查询",
    "价格",
    "库存",
    "参数",
    "规格",
    "尺寸",
    "屏幕",
    "续航",
    "材质",
    "评价",
    "适合",
    "送礼",
    "礼物",
    "送女朋友",
    "送男朋友",
    "gift",
    "学生",
    "通勤",
    "办公",
    "游戏",
    "Apple",
    "apple",
    "iPhone",
    "iphone",
    "iPad",
    "ipad",
    "MacBook",
    "macbook",
    "Mac",
    "mac",
    "ecommerce",
    "shopping",
    "product",
]

CATEGORY_KEYWORDS = {
    ComponentCategory.beauty: ["美妆", "护肤", "洗面奶", "面霜", "精华", "防晒", "眼霜", "乳液", "敏感肌", "油皮", "干皮", "控油", "补水", "保湿"],
    ComponentCategory.digital: ["数码", "手机", "耳机", "蓝牙耳机", "电脑", "笔记本", "平板", "拍照", "续航", "游戏", "办公", "电子"],
    ComponentCategory.clothing: ["服饰", "穿搭", "衣服", "外套", "t恤", "短袖", "裤", "裙", "鞋", "跑鞋", "运动鞋", "通勤", "户外"],
    ComponentCategory.food: ["食品", "饮料", "零食", "咖啡", "坚果", "方便面", "方便食品", "能量", "无糖", "低糖", "囤货"],
}

SUB_CATEGORY_KEYWORDS = [
    "洗面奶",
    "面霜",
    "精华",
    "防晒",
    "眼霜",
    "智能手机",
    "蓝牙耳机",
    "平板电脑",
    "笔记本电脑",
    "短袖T恤",
    "卫衣",
    "速干T恤",
    "运动长裤",
    "运动短裤",
    "瑜伽裤",
    "户外裤",
    "跑步鞋",
    "运动鞋",
    "篮球鞋",
    "徒步鞋",
    "背包",
    "帽子",
    "外套",
    "咖啡",
    "功能饮料",
    "坚果/零食",
    "方便食品",
]

# Aliases: bare product terms that should map to canonical SUB_CATEGORY_KEYWORDS.
# For example, "手机" (bare) → "智能手机" (canonical).
SUB_CATEGORY_ALIASES: Dict[str, str] = {
    "手机": "智能手机",
    "电脑": "笔记本电脑",
    "笔记本": "笔记本电脑",
    "平板": "平板电脑",
    "耳机": "蓝牙耳机",
}

BRAND_HINTS = [
    "雅诗兰黛",
    "科颜氏",
    "兰蔻",
    "小米",
    "华为",
    "苹果",
    "联想",
    "优衣库",
    "耐克",
    "阿迪达斯",
    "红牛",
    "瑞幸",
]

BUNDLE_KEYWORDS = ["一套", "全套", "搭配", "组合", "套装", "方案", "穿搭", "配齐", "整套"]


_PC_SUB_CATEGORY_MAP = {
    "显卡": ComponentCategory.pc_gpu,
    "GPU": ComponentCategory.pc_gpu,
    "CPU": ComponentCategory.pc_cpu,
    "处理器": ComponentCategory.pc_cpu,
    "主板": ComponentCategory.pc_motherboard,
    "内存": ComponentCategory.pc_memory,
    "固态硬盘": ComponentCategory.pc_storage,
    "SSD": ComponentCategory.pc_storage,
    "电源": ComponentCategory.pc_psu,
    "机箱": ComponentCategory.pc_case,
    "散热器": ComponentCategory.pc_cooler,
}


def _requirement_from_args(args: Dict[str, Any], user_goal: str) -> RequirementSpec:
    """Map LLM router arguments directly to RequirementSpec.

    No session merging — accumulated state is already handled by
    update_session_from_router() before this function is called.
    """

    raw_query = str(args.get("query") or user_goal or "").strip()
    category_str = str(args.get("category") or "").strip().lower()
    sub_category = str(args.get("sub_category") or "").strip()
    catalog_scope = str(args.get("catalog_scope") or "ecommerce").strip()

    # category → ComponentCategory
    desired_categories = []
    if catalog_scope == "pc_parts" and sub_category:
        pc_cat = _PC_SUB_CATEGORY_MAP.get(sub_category)
        if pc_cat:
            desired_categories = [pc_cat]
    if not desired_categories and category_str in ComponentCategory.__members__:
        desired_categories = [ComponentCategory[category_str]]

    # price: budget → price_max fallback
    price_max = args.get("price_max")
    price_min = args.get("price_min")
    budget = args.get("budget")
    if price_max is None and budget is not None:
        price_max = budget

    return RequirementSpec(
        raw_query=raw_query,
        desired_categories=desired_categories,
        required_components=desired_categories,
        target_sub_categories=[sub_category] if sub_category else [],
        brands=canonicalize_brand_terms(args.get("brands") or []),
        excluded_brands=canonicalize_brand_terms(args.get("exclude_brands") or []),
        must_have_terms=[str(t) for t in (args.get("must_have_terms") or []) if str(t).strip()],
        price_min=price_min,
        price_max=price_max,
        preferences=[str(p) for p in (args.get("preferences") or []) if str(p).strip()] if isinstance(args.get("preferences"), list) else [],
    )


# ── 🟢 新增: session 感知版需求构建 (⑤.5) ──

_CLEAR_SENTINEL = "__CLEAR__"


def _requirement_from_args_v2(
    args: Dict[str, Any],
    user_goal: str,
    session: Optional[Any] = None,
) -> RequirementSpec:
    """Map LLM router arguments to RequirementSpec, merging session.current history.

    与 v1 的区别:
    - 若 router_arguments 中某字段为 None → 从 session.current 继承
    - 若 router_arguments 中某字段为 __CLEAR__ → 显式清空
    """
    session_current: Dict[str, Any] = getattr(session, "current", {}) if session else {}
    # The router is an orchestration hint, not a second incompatible intent
    # schema. Start from the canonical rule parser so attachment context,
    # exclusions, bundle intent and modalities cannot be lost.
    fallback = parse_requirement_rule_based(user_goal, skip_keyword_check=True)

    raw_query = str(user_goal or args.get("query") or "").strip()
    category_str = str(args.get("category") or "").strip().lower()
    sub_category = str(args.get("sub_category") or "").strip()
    catalog_scope = str(args.get("catalog_scope") or "").strip()

    # 🟢 继承 session.current 中未覆盖的字段
    # category
    if not category_str and session_current.get("category"):
        category_str = str(session_current["category"] or "").strip().lower()
    # sub_category
    if not sub_category and session_current.get("sub_category"):
        sub_category = str(session_current["sub_category"] or "").strip()
    # catalog_scope
    if not catalog_scope and session_current.get("catalog_scope"):
        catalog_scope = str(session_current["catalog_scope"] or "").strip()
    # price
    price_max = args.get("price_max")
    price_min = args.get("price_min")
    budget = args.get("budget")
    if price_max is None:
        price_max = session_current.get("price_max")
    if price_min is None:
        price_min = session_current.get("price_min")
    if budget is None and price_max is None:
        budget = session_current.get("budget")
    if price_max is None and budget is not None:
        price_max = budget
    # brands — __CLEAR__ 主动清空
    if "brands" in args:
        if args["brands"] == _CLEAR_SENTINEL:
            brands = []
        else:
            brands = canonicalize_brand_terms(args.get("brands") or [])
    elif session_current.get("brands"):
        brands = canonicalize_brand_terms(session_current["brands"])
    else:
        brands = []
    # exclude_brands
    if "exclude_brands" in args:
        if args["exclude_brands"] == _CLEAR_SENTINEL:
            excluded_brands = []
        else:
            excluded_brands = canonicalize_brand_terms(args.get("exclude_brands") or [])
    elif session_current.get("exclude_brands"):
        excluded_brands = canonicalize_brand_terms(session_current["exclude_brands"])
    else:
        excluded_brands = []

    # category → ComponentCategory
    desired_categories = []
    if catalog_scope == "pc_parts" and sub_category:
        pc_cat = _PC_SUB_CATEGORY_MAP.get(sub_category)
        if pc_cat:
            desired_categories = [pc_cat]
    if not desired_categories and category_str in ComponentCategory.__members__:
        desired_categories = [ComponentCategory[category_str]]
    # 🟢 校验 category 是否存在于产品库枚举中
    if not desired_categories and category_str:
        # 尝试 CATEGORY_NAME_TO_KEY 中文映射
        mapped = CATEGORY_NAME_TO_KEY.get(category_str)
        if mapped:
            desired_categories = [mapped]

    if not desired_categories:
        desired_categories = list(fallback.desired_categories)

    target_sub_categories = dedupe_strings(
        ([sub_category] if sub_category else []) + list(fallback.target_sub_categories)
    )
    must_have_terms = [str(t) for t in (args.get("must_have_terms") or []) if str(t).strip()]
    must_have_terms = dedupe_strings([*must_have_terms, *fallback.must_have_terms])
    excluded_terms = [str(t) for t in (args.get("excluded_terms") or []) if str(t).strip()]
    excluded_terms = dedupe_strings([*excluded_terms, *fallback.excluded_terms])
    preference_terms = _router_preference_terms(args.get("preferences"), args.get("usage"))
    preference_terms = dedupe_strings([*preference_terms, *fallback.preferences])

    need_bundle = bool(args.get("need_bundle", fallback.need_bundle))
    need_comparison = bool(args.get("need_comparison", fallback.need_comparison))
    need_cart_action = bool(args.get("need_cart_action", fallback.need_cart_action))
    need_multimodal = bool(args.get("need_multimodal", fallback.need_multimodal))
    task_type = fallback.task_type
    if need_bundle:
        task_type = "bundle_recommendation"
    elif need_comparison:
        task_type = "comparison"

    return RequirementSpec(
        raw_query=raw_query,
        scenario=fallback.scenario,
        task_type=task_type,
        desired_categories=desired_categories,
        required_components=desired_categories,
        target_sub_categories=target_sub_categories,
        brands=(brands if "brands" in args or session_current.get("brands") else canonicalize_brand_terms(fallback.brands)),
        excluded_brands=(
            excluded_brands
            if "exclude_brands" in args or session_current.get("exclude_brands")
            else canonicalize_brand_terms(fallback.excluded_brands)
        ),
        must_have_terms=must_have_terms,
        excluded_terms=excluded_terms,
        price_min=price_min,
        price_max=price_max,
        preferences=preference_terms,
        occasion=fallback.occasion,
        target_user=fallback.target_user,
        budget_level=fallback.budget_level,
        need_bundle=need_bundle,
        need_comparison=need_comparison,
        need_cart_action=need_cart_action,
        need_multimodal=need_multimodal,
        input_modalities=list(fallback.input_modalities),
        output_modalities=list(fallback.output_modalities),
        languages=list(fallback.languages),
        missing_fields=list(fallback.missing_fields),
        assumptions=list(fallback.assumptions),
        clarification_question=fallback.clarification_question,
    )


def _router_preference_terms(preferences: Any, usage: Any) -> List[str]:
    """Flatten router preference objects into RequirementSpec string terms."""

    terms: List[str] = []
    if isinstance(preferences, dict):
        for key, value in preferences.items():
            if isinstance(value, bool):
                if value:
                    terms.append(str(key))
            elif isinstance(value, list):
                terms.extend(str(item) for item in value if str(item).strip())
            elif value not in (None, ""):
                terms.append(str(value))
    elif isinstance(preferences, list):
        terms.extend(str(item) for item in preferences if str(item).strip())
    elif isinstance(preferences, str) and preferences.strip():
        terms.append(preferences.strip())
    if isinstance(usage, list):
        terms.extend(str(item) for item in usage if str(item).strip())
    elif isinstance(usage, str) and usage.strip():
        terms.append(usage.strip())
    return dedupe_strings(terms)


def recommend_shopping_products(
    user_goal: str,
    use_llm: bool = True,
    image_retrieval_evidence: Any = None,
    use_llm_guidance: Optional[bool] = None,
    catalog_scope: str = "ecommerce",
    use_milvus_retrieval: bool = True,
    use_rag_query_expansion: bool = False,
    use_llm_explanation: Optional[bool] = None,
    skip_keyword_check: bool = False,
    router_arguments: Optional[Dict[str, Any]] = None,
    session: Optional[Any] = None,  # 🟢 新增: session 感知
) -> RecommendationResult:
    """Recommend ecommerce products and bundles from a shopping goal."""

    validate_business_goal(user_goal, skip_keyword_check=skip_keyword_check)
    if router_arguments:
        parse_trace = _reset_parse_trace()
        parse_trace["llm_parse_failure_reason"] = "router_arguments_applied"
        parse_trace["llm_parse_error_class"] = "skipped"
        requirement = _requirement_from_args_v2(router_arguments, user_goal, session=session)
    else:
        requirement = parse_requirement(user_goal, use_llm=use_llm, skip_keyword_check=skip_keyword_check)
    requirement_parse_trace = build_requirement_parse_trace(requirement, use_llm=use_llm)
    if is_pc_query(user_goal) and catalog_scope != "pc_parts":
        catalog_scope = "pc_parts"
    result = build_recommendation_result(
        requirement,
        catalog_scope=catalog_scope,
        image_retrieval_evidence=image_retrieval_evidence,
        use_milvus_retrieval=use_milvus_retrieval,
        use_rag_query_expansion=use_rag_query_expansion,
        session=session,
    )
    result.trace["requirement_parsing"] = requirement_parse_trace
    result.trace.update(get_llm_provider_trace())
    result.trace["llm_requirement_parse_used"] = requirement_parse_trace["llm_parse_used"]
    result.trace["rule_parse_used"] = requirement_parse_trace["rule_parse_used"]
    guidance_enabled = should_use_llm_guidance(user_goal) if use_llm_guidance is None else use_llm_guidance
    result = enrich_recommendation_result(result, use_llm=use_llm and guidance_enabled)
    explanation_enabled = guidance_enabled if use_llm_explanation is None else bool(use_llm_explanation)
    attach_grounded_explanation(result, use_llm=use_llm and explanation_enabled)
    return result


def recommend_api_stack(user_goal: str, use_llm: bool = True, catalog_scope: str = "ecommerce") -> RecommendationResult:
    """Backward-compatible alias for older tests and scripts."""

    return recommend_shopping_products(user_goal, use_llm=use_llm, catalog_scope=catalog_scope)


def recommend_shopping_bundle(user_goal: str, use_llm: bool = True, catalog_scope: str = "ecommerce") -> RecommendationResult:
    return recommend_shopping_products(user_goal, use_llm=use_llm, catalog_scope=catalog_scope)


def attach_grounded_explanation(result: RecommendationResult, *, use_llm: bool) -> None:
    requirement = model_to_dict(result.requirement)
    if not result.product_cards and not result.comparison_table:
        result.trace["explanation_mode"] = "skipped"
        result.trace["llm_used_for_explanation"] = False
        # ── 标准化 explanation trace ──
        result.trace["llm_explanation_attempted"] = False
        result.trace["llm_explanation_success"] = False
        result.trace["llm_explanation_failure_reason"] = "no_cards_or_comparison"
        return
    explanation = build_evidence_grounded_explanation(
        user_need=result.requirement.raw_query,
        parsed_requirement=requirement,
        selected_products=result.product_cards,
        comparison_table=result.comparison_table if result.requirement.need_comparison else None,
        use_llm=use_llm,
    )
    mode = explanation.get("mode") or "template"
    result.trace["explanation_mode"] = mode
    result.trace["llm_used_for_explanation"] = mode == "llm_evidence_grounded"
    result.trace["explanation_llm_input_fields"] = sorted((explanation.get("llm_input") or {}).keys())
    if explanation.get("fallback_reason"):
        result.trace["explanation_fallback_reason"] = explanation["fallback_reason"]
    result.feedback_summary["grounded_explanation"] = explanation.get("explanation") or {}
    # ── 标准化 explanation trace 字段 ──
    expl_trace = explanation.get("_trace") or {}
    result.trace["llm_explanation_attempted"] = expl_trace.get("llm_explanation_attempted", bool(use_llm))
    result.trace["llm_explanation_success"] = expl_trace.get("llm_explanation_success", mode == "llm_evidence_grounded")
    result.trace["llm_explanation_failure_reason"] = expl_trace.get("llm_explanation_failure_reason", explanation.get("fallback_reason") or "")


# ── 线程安全的 parse trace（使用 threading.local 替代模块级 dict） ──

_parse_trace_local = _threading.local()


def _get_parse_trace() -> Dict[str, Any]:
    """Return the current thread's parse trace, initialising it if needed."""
    if not hasattr(_parse_trace_local, "value"):
        _parse_trace_local.value = {}
    return _parse_trace_local.value


def _reset_parse_trace() -> Dict[str, Any]:
    """Reset and return a fresh parse trace dict for the current thread."""
    _parse_trace_local.value = {
        "llm_parse_attempted": False,
        "llm_parse_success": False,
        "llm_parse_applied": False,
        "llm_parse_failure_reason": "",
        "llm_parse_error_class": "",
        "llm_parse_elapsed_ms": 0,
    }
    return _parse_trace_local.value


def parse_requirement(user_goal: str, use_llm: bool = True, *, skip_keyword_check: bool = False) -> RequirementSpec:
    """Parse shopping intent with rule fallback and optional LLM enhancement."""

    _pt = _reset_parse_trace()
    rule_requirement = parse_requirement_rule_based(user_goal, skip_keyword_check=skip_keyword_check)
    if not use_llm or not should_use_llm_requirement_parse(user_goal, rule_requirement):
        _pt["llm_parse_failure_reason"] = "llm_disabled_by_runtime_policy" if not use_llm else "rule_parse_sufficient_or_auto_skipped"
        _pt["llm_parse_error_class"] = "skipped"
        return rule_requirement

    _pt["llm_parse_attempted"] = True
    client = OpenAICompatibleChatClient()
    if not client.configured:
        _pt["llm_parse_failure_reason"] = "llm_not_configured"
        _pt["llm_parse_error_class"] = "skipped"
        rule_requirement.assumptions.append("未配置生成式大模型，当前使用规则解析购物需求。")
        return rule_requirement

    _parse_start = time.perf_counter()
    try:
        parsed, report = run_with_hard_timeout(
            lambda: client.chat_json_with_report(
            [
                {
                    "role": "system",
                    "content": "你是传统电商 AI 导购的需求理解器。只输出 JSON，不要解释。",
                },
                {"role": "user", "content": build_requirement_prompt(user_goal, rule_requirement)},
            ],
                model=os.getenv("MALLMIND_PARSE_MODEL") or client.config.fast_model,
                temperature=0.1,
                max_tokens=1200,
            ),
            _float_env("RECOMMENDATION_LLM_PARSE_TIMEOUT_SECONDS", 12.0),
            "requirement_parse",
        )
        _pt["llm_parse_elapsed_ms"] = int((time.perf_counter() - _parse_start) * 1000)
        requirement = requirement_from_llm_payload(parsed, rule_requirement)
        requirement.assumptions.append(
            f"生成式大模型已参与需求理解，耗时 {report.elapsed_ms}ms。"
        )
        _pt["llm_parse_success"] = True
        _pt["llm_parse_applied"] = True
        return requirement
    except TimeoutError:
        _pt["llm_parse_elapsed_ms"] = int((time.perf_counter() - _parse_start) * 1000)
        _pt["llm_parse_failure_reason"] = "llm_timeout"
        _pt["llm_parse_error_class"] = "timeout"
        logger.warning("LLM requirement parsing timed out; falling back to rules")
        rule_requirement.assumptions.append("生成式大模型需求解析超时，已降级为规则解析。")
        return rule_requirement
    except (LLMClientError, ValueError, TypeError, ConnectionError, PermissionError, OSError) as exc:
        _pt["llm_parse_elapsed_ms"] = int((time.perf_counter() - _parse_start) * 1000)
        text = str(exc).lower()
        if "timeout" in text or "timed out" in text:
            _pt["llm_parse_failure_reason"] = "llm_timeout"
            _pt["llm_parse_error_class"] = "timeout"
        elif isinstance(exc, (ConnectionError, PermissionError, OSError)):
            _pt["llm_parse_failure_reason"] = "network_error"
            _pt["llm_parse_error_class"] = type(exc).__name__.lower()
        elif isinstance(exc, (ValueError, TypeError)):
            _pt["llm_parse_failure_reason"] = "llm_json_invalid"
            _pt["llm_parse_error_class"] = "json_invalid"
        else:
            _pt["llm_parse_failure_reason"] = "llm_provider_error"
            _pt["llm_parse_error_class"] = "provider_error"
        logger.warning("LLM requirement parsing failed; falling back to rules: %s", exc)
        rule_requirement.assumptions.append("生成式大模型需求解析失败，已降级为规则解析。")
        return rule_requirement


def should_use_llm_requirement_parse(message: str, rule_requirement: RequirementSpec) -> bool:
    """Use LLM parsing only for requests where rules are likely underspecified."""

    mode = os.getenv("RECOMMENDATION_LLM_PARSE", "auto").strip().lower()
    if mode in {"0", "false", "off", "disabled", "never"}:
        return False
    if mode in {"1", "true", "on", "enabled", "always"}:
        return True

    text = message or ""
    lowered = text.lower()
    if rule_requirement.need_bundle or rule_requirement.need_multimodal:
        return True
    if has_simple_category_or_brand_query(text, rule_requirement):
        return False
    complex_terms = [
        "适合",
        "送",
        "礼物",
        "学生",
        "学生党",
        "上班族",
        "通勤",
        "场景",
        "夏天",
        "冬天",
        "敏感肌",
        "油皮",
        "干皮",
        "性价比",
        "预算有限",
        "不要太贵",
        "搭配",
        "一套",
        "组合",
        "方案",
        "travel",
        "gift",
        "student",
        "commute",
    ]
    if any(term in text or term in lowered for term in complex_terms):
        return True
    missing = set(rule_requirement.missing_fields or [])
    return bool(missing and "category" not in missing)


def has_simple_category_or_brand_query(message: str, rule_requirement: RequirementSpec) -> bool:
    text = message or ""
    simple_terms = [
        "推荐",
        "看看",
        "有哪些",
        "多少钱",
        "价格",
        "参数",
        "库存",
        "有货",
        "手机",
        "耳机",
        "零食",
        "咖啡",
        "护肤",
        "美妆",
        "电脑",
        "笔记本",
        "Apple",
        "iPhone",
        "iPad",
        "Mac",
    ]
    has_category = bool(rule_requirement.desired_categories)
    has_specific_filter = bool(
        rule_requirement.price_max is not None
        or rule_requirement.price_min is not None
        or rule_requirement.brands
        or rule_requirement.excluded_brands
        or rule_requirement.target_sub_categories
    )
    return has_category and (has_specific_filter or any(term in text for term in simple_terms))


def should_use_llm_guidance(message: str) -> bool:
    if not _env_bool("RECOMMENDATION_LLM_GUIDANCE", default=False):
        return False
    detail_terms = ["详细解释", "完整分析", "购买建议", "为什么推荐", "解释一下", "详细分析"]
    return any(term in (message or "") for term in detail_terms)


def build_requirement_parse_trace(requirement: RequirementSpec, *, use_llm: bool) -> Dict[str, Any]:
    assumptions = list(getattr(requirement, "assumptions", []) or [])
    llm_used = any("生成式大模型已参与需求理解" in item for item in assumptions)
    fallback_reason = ""
    if not use_llm:
        fallback_reason = "llm_disabled_by_runtime_policy"
    elif any("未配置生成式大模型" in item for item in assumptions):
        fallback_reason = "llm_not_configured"
    elif any("生成式大模型需求解析失败" in item for item in assumptions):
        fallback_reason = "llm_parse_failed"
    elif any("超时" in item for item in assumptions):
        fallback_reason = "llm_timeout"
    elif not llm_used:
        fallback_reason = "rule_parse_sufficient_or_auto_skipped"
    # 从线程安全的 parse trace 读取标准化字段
    pt = dict(_get_parse_trace()) if _get_parse_trace() else {}
    return {
        "rule_parse_used": True,
        "llm_parse_requested": bool(use_llm),
        "llm_parse_used": llm_used,
        "parse_fallback_reason": fallback_reason,
        # ── 标准化 LLM parse trace 字段 ──
        "llm_parse_attempted": pt.get("llm_parse_attempted", bool(use_llm and fallback_reason not in {"llm_disabled_by_runtime_policy", "rule_parse_sufficient_or_auto_skipped"})),
        "llm_parse_success": pt.get("llm_parse_success", llm_used),
        "llm_parse_applied": pt.get("llm_parse_applied", llm_used),
        "llm_parse_failure_reason": pt.get("llm_parse_failure_reason", fallback_reason if not llm_used else ""),
        "llm_parse_error_class": pt.get("llm_parse_error_class", ""),
        "llm_parse_elapsed_ms": pt.get("llm_parse_elapsed_ms", 0),
    }


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def build_requirement_prompt(user_goal: str, fallback: RequirementSpec) -> str:
    category_values = ", ".join(item.value for item in [ComponentCategory.beauty, ComponentCategory.digital, ComponentCategory.clothing, ComponentCategory.food])
    budget_values = ", ".join(item.value for item in BudgetLevel)
    return f"""
{defense_prefix()}

请从用户购物需求中抽取结构化约束，用于传统电商商品推荐。

{wrap_user_input(user_goal, max_len=600)}

规则解析初稿：
{json_dumps(model_to_dict(fallback))}

只输出 JSON，字段限定为：
{{
  "scenario": "shopping/general/skin_care/device_purchase/outfit_bundle/snack_drink/gift/travel 等",
  "task_type": "single_product_recommendation 或 bundle_recommendation 或 comparison 或 cart_action",
  "desired_categories": ["从这些值选择：{category_values}"],
  "target_sub_categories": ["洗面奶/蓝牙耳机/跑步鞋等"],
  "brands": ["用户明确想要的品牌"],
  "excluded_brands": ["用户明确排除的品牌"],
  "must_have_terms": ["必须满足的属性、场景、功效"],
  "excluded_terms": ["不要、不含、除了等否定条件"],
  "preferences": ["偏好词，例如轻量、拍照、保湿、无糖"],
  "price_min": null 或数字,
  "price_max": null 或数字,
  "budget_level": "{budget_values}",
  "need_bundle": true/false,
  "need_comparison": true/false,
  "need_cart_action": true/false,
  "need_multimodal": true/false,
  "missing_fields": ["缺失但会影响推荐的问题"],
  "assumptions": ["关键假设"],
  "clarification_question": "当用户需求模糊时，生成一个最关键的追问（如预算、品类、品牌偏好），不需要追问则为空字符串"
}}

要求：
1. 不要添加 schema 外字段。
2. 不要编造商品、价格、优惠券或库存。
3. 如果用户表达“一整套/搭配/旅行方案”，need_bundle=true，并保留跨类目意图。
4. 如果有“不要/不含/除了”，必须写入 excluded_terms 或 excluded_brands。
{defense_suffix()}
""".strip()


def requirement_from_llm_payload(payload: Dict[str, Any], fallback: RequirementSpec) -> RequirementSpec:
    data = model_to_dict(fallback)
    allowed = set(data.keys())
    for key, value in payload.items():
        if key in allowed:
            data[key] = value
    data["raw_query"] = fallback.raw_query
    data["desired_categories"] = normalize_categories(data.get("desired_categories"), fallback.desired_categories)
    data["required_components"] = data["desired_categories"]
    data["excluded_categories"] = normalize_categories(data.get("excluded_categories"), fallback.excluded_categories)
    data["budget_level"] = normalize_enum_value(data.get("budget_level"), BudgetLevel, fallback.budget_level)
    data["quality_requirement"] = normalize_enum_value(data.get("quality_requirement"), RequirementLevel, fallback.quality_requirement)
    for key in (
        "target_sub_categories",
        "brands",
        "excluded_brands",
        "must_have_terms",
        "excluded_terms",
        "preferences",
        "missing_fields",
        "assumptions",
    ):
        data[key] = normalize_string_list(data.get(key), getattr(fallback, key, []))
    for key in ("need_bundle", "need_comparison", "need_cart_action", "need_multimodal"):
        data[key] = bool(data.get(key))
    # ── clarification_question normalization ──
    cq = data.get("clarification_question")
    if not isinstance(cq, str):
        cq = ""
    data["clarification_question"] = cq.strip()
    return RequirementSpec(**data)


def enrich_recommendation_result(result: RecommendationResult, use_llm: bool = True) -> RecommendationResult:
    fallback = build_rule_based_guidance(result)
    result.teaching_guidance = fallback["teaching_guidance"]
    result.follow_up_questions = fallback["follow_up_questions"]
    result.optimization_suggestions = fallback["optimization_suggestions"]
    result.feedback_summary = fallback["feedback_summary"]

    # ── 标准化 guidance trace 字段（初始化） ──
    result.trace["llm_guidance_attempted"] = bool(use_llm)
    result.trace["llm_guidance_success"] = False
    result.trace["llm_guidance_failure_reason"] = ""

    if not use_llm:
        result.trace["llm_guidance"] = "disabled"
        result.trace["llm_guidance_failure_reason"] = "llm_disabled_by_runtime_policy"
        return result

    client = OpenAICompatibleChatClient()
    if not client.configured:
        result.trace["llm_guidance"] = "not_configured"
        result.trace["llm_guidance_failure_reason"] = "llm_not_configured"
        result.trace.update(get_llm_provider_trace())
        return result

    try:
        payload, report = run_with_hard_timeout(
            lambda: client.chat_json_with_report(
            [
                {"role": "system", "content": "你是谨慎的传统电商导购助手，只输出 JSON。"},
                {"role": "user", "content": build_guidance_prompt(result)},
            ],
                model=os.getenv("MALLMIND_GUIDANCE_MODEL") or client.config.model,
                temperature=0.2,
                max_tokens=1500,
            ),
            _float_env("RECOMMENDATION_LLM_GUIDANCE_TIMEOUT_SECONDS", 8.0),
            "guidance",
        )
        result.teaching_guidance = normalize_string_list(payload.get("teaching_guidance"), fallback["teaching_guidance"])[:6]
        result.follow_up_questions = normalize_string_list(payload.get("follow_up_questions"), fallback["follow_up_questions"])[:6]
        result.optimization_suggestions = normalize_string_list(payload.get("optimization_suggestions"), fallback["optimization_suggestions"])[:6]
        result.trace["llm_guidance"] = "enabled"
        result.trace["llm_guidance_success"] = True
        result.trace["llm_guidance_call"] = report_to_dict(report)
    except TimeoutError:
        logger.warning("LLM guidance timed out; using rule-based guidance")
        result.trace["llm_guidance"] = "fallback"
        result.trace["llm_guidance_failure_reason"] = "llm_timeout"
    except (LLMClientError, ValueError, TypeError, ConnectionError, PermissionError, OSError) as exc:
        logger.warning("LLM guidance failed; using rule-based guidance: %s", exc)
        result.trace["llm_guidance"] = "fallback"
        result.trace["llm_guidance_failure_reason"] = _classify_llm_exception(exc)
        result.trace["llm_guidance_error"] = public_error(exc)
        result.trace["llm_error_sanitized"] = public_error(exc)
    return result


def _classify_llm_exception(exc: Exception) -> str:
    """Classify an LLM exception into a stable reason code."""
    text = str(exc).lower()
    if "timeout" in text or "timed out" in text:
        return "llm_timeout"
    if isinstance(exc, (ConnectionError, PermissionError, OSError)):
        return "network_error"
    if isinstance(exc, (ValueError, TypeError)):
        return "llm_json_invalid"
    return "llm_provider_error"


def parse_requirement_rule_based(user_goal: str, *, skip_keyword_check: bool = False) -> RequirementSpec:
    validate_business_goal(user_goal, skip_keyword_check=skip_keyword_check)
    normalized = clean_text(user_goal)
    lower = normalized.lower()
    desired_categories = infer_desired_categories(normalized, lower)
    price_min, price_max = extract_price_range(normalized)
    excluded_terms, excluded_brands = extract_exclusions(normalized)
    must_have_terms = infer_must_have_terms(normalized, lower)
    preferences = infer_preferences(normalized, lower)
    need_bundle = has_any(lower, BUNDLE_KEYWORDS)
    need_comparison = has_any(lower, ["对比", "比较", "哪个", "哪款更", "a和b", "a 和 b"])
    need_cart_action = has_any(lower, ["购物车", "加购", "加入购物车", "下单", "删除第二个", "数量改"])
    need_multimodal = has_any(lower, ["图片", "照片", "拍照", "上传", "同款", "街拍", "截图"])
    scenario = infer_scenario(lower, desired_categories, need_bundle, need_comparison, need_cart_action)
    target_sub_categories = infer_target_sub_categories(normalized)
    brands = canonicalize_brand_terms(brand for brand in BRAND_HINTS if brand in normalized and brand not in excluded_brands)

    missing_fields = []
    if not desired_categories:
        missing_fields.append("category")
    if price_max is None and not has_any(lower, ["便宜", "性价比", "高端", "旗舰", "贵价", "平价"]):
        missing_fields.append("budget_level")
    if need_bundle and len(desired_categories) <= 1 and not has_any(lower, ["穿搭", "防晒", "三亚", "度假", "出差"]):
        missing_fields.append("bundle_context")

    if not desired_categories:
        desired_categories = [ComponentCategory.beauty, ComponentCategory.digital, ComponentCategory.clothing, ComponentCategory.food]
    if need_bundle and ComponentCategory.clothing in desired_categories and has_any(lower, ["防晒", "护肤", "度假", "三亚"]):
        desired_categories = dedupe_categories([ComponentCategory.beauty, ComponentCategory.clothing])

    return RequirementSpec(
        raw_query=normalized,
        scenario=scenario,
        task_type=infer_task_type(need_bundle, need_comparison, need_cart_action),
        required_components=desired_categories,
        desired_categories=desired_categories,
        target_sub_categories=target_sub_categories,
        brands=brands,
        excluded_brands=canonicalize_brand_terms(excluded_brands),
        must_have_terms=must_have_terms,
        excluded_terms=excluded_terms,
        preferences=preferences,
        occasion=infer_occasion(lower),
        target_user=infer_target_user(lower),
        price_min=price_min,
        price_max=price_max,
        budget_level=infer_budget_level(lower, price_max),
        need_bundle=need_bundle,
        need_comparison=need_comparison,
        need_cart_action=need_cart_action,
        need_multimodal=need_multimodal,
        input_modalities=infer_input_modalities(lower),
        output_modalities=["text"],
        languages=["zh"],
        quality_requirement=infer_quality_requirement(lower),
        missing_fields=missing_fields,
        assumptions=build_assumptions(price_max, desired_categories, need_bundle),
    )


def validate_business_goal(user_goal: str, *, skip_keyword_check: bool = False) -> None:
    normalized = user_goal.strip()
    lower = normalized.lower()
    if len(normalized) < 2:
        raise InvalidGoalError("请输入更完整的购物需求，例如类目、预算、用途或偏好。")
    meaningful_chars = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", normalized)
    if len(meaningful_chars) < 2:
        raise InvalidGoalError("输入内容过短或缺少可识别信息，请补充购物需求。")
    symbol_count = sum(1 for char in normalized if not re.match(r"[\u4e00-\u9fffA-Za-z0-9\s]", char))
    if symbol_count / max(len(normalized), 1) > 0.35:
        raise InvalidGoalError("输入中符号比例过高，请输入自然语言购物需求。")
    if not skip_keyword_check:
        if not has_any(lower, SHOPPING_GOAL_KEYWORDS):
            raise InvalidGoalError("未识别到有效购物场景，请描述想买什么、预算、用途或偏好。")


def infer_desired_categories(raw: str, lower: str) -> List[ComponentCategory]:
    categories = []
    for category, keywords in CATEGORY_KEYWORDS.items():
        if has_any(lower, keywords):
            categories.append(category)

    travel_bundle_terms = [
        "三亚",
        "度假",
        "旅行",
        "海边",
        "防晒到穿搭",
        "防晒穿搭",
        "穿搭方案",
        "从防晒到穿搭",
    ]
    if has_any(lower, travel_bundle_terms):
        categories.extend([ComponentCategory.beauty, ComponentCategory.clothing])

    return dedupe_categories(categories)


def infer_target_sub_categories(raw: str) -> List[str]:
    results = [item for item in SUB_CATEGORY_KEYWORDS if item.lower() in raw.lower()]
    for alias, canonical in SUB_CATEGORY_ALIASES.items():
        if alias.lower() in raw.lower() and canonical not in results:
            results.append(canonical)
    return results


def _cn_unit_to_float(value_str: str, unit_str: str = "") -> float:
    """Parse a number string with an optional Chinese unit into a float."""
    amount = float(value_str)
    _u = (unit_str or "").strip()
    _multipliers = {
        "亿": 100_000_000, "千万": 10_000_000, "百万": 1_000_000,
        "十万": 100_000, "万": 10_000, "千": 1_000, "百": 100,
    }
    return amount * _multipliers.get(_u, 1)


def extract_price_range(text: str) -> tuple[Optional[float], Optional[float]]:
    _CU = r"(千万|百万|十万|万|千|百|亿)"  # capturing Chinese unit
    range_patterns = [
        rf"(\d+(?:\.\d+)?)\s*{_CU}?\s*(?:元|块)?\s*(?:到|至|-|~)\s*(\d+(?:\.\d+)?)\s*{_CU}?\s*(?:元|块)?",
    ]
    upper_bound_patterns = [
        rf"(\d+(?:\.\d+)?)\s*{_CU}?\s*(?:元|块)?\s*(?:以内|以下|之内|内)",
        rf"(?:不要超过|不要超出|别超过|别超出|不超过|不超出|低于|少于)\s*(\d+(?:\.\d+)?)\s*{_CU}?\s*(?:元|块)?",
    ]
    lower_bound_patterns = [
        rf"(\d+(?:\.\d+)?)\s*{_CU}?\s*(?:元|块)?\s*(?:以上|起)",
        rf"(?<!不)(?<!不要)(?<!别)(?:高于|超过)\s*(\d+(?:\.\d+)?)\s*{_CU}?\s*(?:元|块)?",
    ]
    fuzzy_patterns = [
        rf"(\d+(?:\.\d+)?)\s*{_CU}?\s*(?:元|块)?\s*(?:左右|附近)",
    ]
    bare_budget_patterns = [
        rf"预算\s*(\d+(?:\.\d+)?)\s*{_CU}?\s*(?:元|块)?(?!\s*(?:左右|附近|以上|以内|以下|之内|内))",
    ]
    for pattern in range_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        first = _cn_unit_to_float(match.group(1), match.group(2) or "")
        second = _cn_unit_to_float(match.group(3), match.group(4) or "")
        return min(first, second), max(first, second)
    for pattern in upper_bound_patterns:
        match = re.search(pattern, text)
        if match:
            return None, _cn_unit_to_float(match.group(1), match.group(2) or "")
    for pattern in lower_bound_patterns:
        match = re.search(pattern, text)
        if match:
            return _cn_unit_to_float(match.group(1), match.group(2) or ""), None
    for pattern in fuzzy_patterns:
        match = re.search(pattern, text)
        if match:
            return None, round(_cn_unit_to_float(match.group(1), match.group(2) or "") * 1.1, 2)
    for pattern in bare_budget_patterns:
        match = re.search(pattern, text)
        if match:
            return None, _cn_unit_to_float(match.group(1), match.group(2) or "")
    return None, None


def extract_exclusions(text: str) -> tuple[List[str], List[str]]:
    excluded_terms: List[str] = []
    excluded_brands: List[str] = []
    for match in re.finditer(r"(?:不要|不含|别要|排除|除了)\s*([^，。,.；;、\s]+)", text):
        value = match.group(1).strip()
        if value and not re.match(r"^(?:超过|超出|超|高于|大于|贵于|多)", value):
            excluded_terms.append(value)
    for brand in BRAND_HINTS:
        if any(prefix + brand in text for prefix in ["不要", "除了", "非", "别买"]):
            excluded_brands.append(brand)
    if "酒精" in text and has_any(text, ["不要", "不含", "无酒精"]):
        excluded_terms.append("酒精")
    return dedupe_strings(excluded_terms), dedupe_strings(excluded_brands)


def infer_must_have_terms(raw: str, lower: str) -> List[str]:
    terms = []
    for keyword in [
        "油皮",
        "干皮",
        "敏感肌",
        "保湿",
        "补水",
        "控油",
        "轻量",
        "拍照",
        "续航",
        "降噪",
        "开放式",
        "入耳式",
        "半入耳",
        "游戏",
        "办公",
        "便携",
        "快充",
        "性价比",
        "无糖",
        "低糖",
        "防晒",
        "透气",
        "通勤",
        "跑步",
        "黑色",
        "白色",
        "蓝色",
        "粉色",
        "短款",
        "宽松",
        "修身",
        "连帽",
        "圆领",
        "针织",
        "速干",
        "纯棉",
        "棉质",
        "棉",
        "牛仔",
        "皮革",
        "防水",
        "透气",
    ]:
        if keyword in raw:
            terms.append(keyword)
    return dedupe_strings(terms)


def infer_preferences(raw: str, lower: str) -> List[str]:
    preferences = infer_must_have_terms(raw, lower)
    for keyword in ["便宜", "平价", "高端", "旗舰", "礼物", "旅行", "出差", "学生", "上班", "夏天", "冬天", "不黏腻", "耐用", "小巧", "静音", "同款", "相似款", "街拍", "通勤", "户外", "运动", "休闲"]:
        if keyword in raw:
            preferences.append(keyword)
    return dedupe_strings(preferences)


def infer_scenario(lower: str, categories: List[ComponentCategory], need_bundle: bool, need_comparison: bool, need_cart_action: bool) -> str:
    if need_cart_action:
        return "cart_or_order_action"
    if need_comparison:
        return "product_comparison"
    if need_bundle:
        if ComponentCategory.clothing in categories:
            return "outfit_bundle"
        return "shopping_bundle"
    if ComponentCategory.beauty in categories:
        return "skin_care"
    if ComponentCategory.digital in categories:
        return "device_purchase"
    if ComponentCategory.clothing in categories:
        return "apparel_sports"
    if ComponentCategory.food in categories:
        return "snack_drink"
    return "general_shopping"


def infer_task_type(need_bundle: bool, need_comparison: bool, need_cart_action: bool) -> str:
    if need_cart_action:
        return "cart_action"
    if need_comparison:
        return "comparison"
    if need_bundle:
        return "bundle_recommendation"
    return "single_product_recommendation"


def infer_budget_level(lower: str, price_max: Optional[float]) -> BudgetLevel:
    if has_any(lower, ["便宜", "低预算", "平价", "省钱", "性价比"]):
        return BudgetLevel.low
    if has_any(lower, ["高端", "贵价", "旗舰", "最好", "不差钱"]):
        return BudgetLevel.high
    if price_max is not None:
        return BudgetLevel.low if price_max <= 300 else BudgetLevel.medium
    if has_any(lower, ["平衡", "适中", "中等"]):
        return BudgetLevel.medium
    return BudgetLevel.unknown


def infer_quality_requirement(lower: str) -> RequirementLevel:
    if has_any(lower, ["最好", "高端", "旗舰", "质量", "效果好", "耐用", "保湿强", "拍照优先"]):
        return RequirementLevel.high
    if has_any(lower, ["随便", "够用", "便宜", "入门"]):
        return RequirementLevel.low
    return RequirementLevel.medium


def infer_input_modalities(lower: str) -> List[str]:
    modalities = ["text"]
    if has_any(lower, ["图片", "照片", "拍照", "上传", "同款", "街拍", "截图"]):
        modalities.append("image")
    if has_any(lower, ["语音", "口述"]):
        modalities.append("audio")
    return dedupe_strings(modalities)


def infer_occasion(lower: str) -> str:
    for keyword in ["三亚", "度假", "旅行", "出差", "通勤", "跑步", "健身", "上班", "开学", "送礼", "熬夜"]:
        if keyword in lower:
            return keyword
    return ""


def infer_target_user(lower: str) -> str:
    for keyword in ["学生", "上班族", "油皮", "干皮", "敏感肌", "男", "女", "儿童", "长辈"]:
        if keyword in lower:
            return keyword
    return ""


def build_assumptions(price_max: Optional[float], categories: List[ComponentCategory], need_bundle: bool) -> List[str]:
    assumptions = ["只从已上架的本地 100 条商品数据中推荐，不编造不存在商品。"]
    if price_max is None:
        assumptions.append("用户未给出明确预算，按价格/口碑/场景综合排序。")
    if need_bundle:
        assumptions.append("检测到套装/搭配意图，会优先在相关类目中组合多个互补商品。")
    assumptions.append("数据集未提供精确库存数量，后端只返回上架状态，不声明实时库存。")
    return assumptions


def build_rule_based_guidance(result: RecommendationResult) -> Dict[str, Any]:
    req = result.requirement
    first_plan = result.plans[0] if result.plans else None
    names = [component.product.title for component in first_plan.components[:3]] if first_plan else []
    teaching = [
        "推荐链路先把自然语言购物需求解析为类目、预算、偏好、否定条件和是否需要套装，再只从本地商品库召回候选。",
        "评分不会让模型凭空决定，而是基于商品标题、SKU、价格、FAQ、评价和用户约束做可解释排序。",
        "价格与库存口径保持谨慎：价格使用数据集 SKU 标价，库存只标注演示上架状态，不输出未提供的精确库存数。",
    ]
    if names:
        teaching.append("当前推荐包含：" + "、".join(names) + "。")
    followups = []
    if "budget_level" in result.missing_fields or req.budget_level == BudgetLevel.unknown:
        followups.append("预算上限大概是多少？这会明显影响推荐排序和套装总价。")
    if "category" in result.missing_fields:
        followups.append("更想看美妆护肤、数码电子、服饰运动还是食品饮料？")
    if req.need_bundle and "bundle_context" in result.missing_fields:
        followups.append("这套方案主要用于什么场景，例如通勤、旅行、运动、送礼还是开学？")
    followups.extend(["是否有明确不要的品牌、成分、颜色、口味或功能？", "更看重价格、口碑、功效/参数，还是搭配完整度？"])
    optimizations = [
        "对高频用户问题可缓存解析后的结构化需求和 Top 商品候选，降低首屏延迟。",
        "Android 商品卡片应展示 product_id、title、price、image_url、reason 和可点击详情，避免端侧再次拼装业务字段。",
        "后续可接入真实库存/优惠接口，在进入下单前刷新价格与库存，防止推荐结果过期。",
        "图片找货可以先用 VLM 抽取颜色、品类、材质或物体，再复用同一套文本召回与评分链路。",
    ]
    return {
        "teaching_guidance": dedupe_strings(teaching)[:6],
        "follow_up_questions": dedupe_strings(followups)[:6],
        "optimization_suggestions": dedupe_strings(optimizations)[:6],
        "feedback_summary": {
            "loop": "capture_query_click_cart_feedback",
            "signals": ["missing_fields", "selected_product", "product_click", "add_to_cart", "purchase"],
            "next_actions": ["补充商品属性", "调权重", "接真实库存价格", "沉淀失败问题"],
        },
    }


def build_guidance_prompt(result: RecommendationResult) -> str:
    compact = {
        "requirement": model_to_dict(result.requirement),
        "plans": [
            {
                "recommendation_type": plan.recommendation_type.value,
                "title": plan.title,
                "products": [
                    {
                        "product_id": component.product.product_id,
                        "title": component.product.title,
                        "price": component.product.base_price,
                        "reason": component.reason,
                    }
                    for component in plan.components
                ],
                "total": model_to_dict(plan.cost_estimate),
            }
            for plan in result.plans
        ],
    }
    # ── clarification hint injection ──
    clarification_hint = ""
    if result.requirement.clarification_question:
        clarification_hint = (
            f"\n\n注意：用户需求尚不明确，建议在追问中包含：{result.requirement.clarification_question}"
        )
    return f"""
{defense_prefix()}

请基于下面的传统电商推荐结果，输出导购解释、追问和优化建议。{clarification_hint}

推荐结果：
{json_dumps(compact)}

只输出 JSON：
{{
  "teaching_guidance": ["说明推荐依据和如何避免幻觉"],
  "follow_up_questions": ["围绕预算、品牌、否定条件、场景追问"],
  "optimization_suggestions": ["后端和 Android 体验优化建议"]
}}
{defense_suffix()}
""".strip()


def normalize_categories(value: Any, fallback: List[ComponentCategory]) -> List[ComponentCategory]:
    if not isinstance(value, list):
        return list(fallback)
    allowed = {item.value: item for item in ComponentCategory}
    result = []
    for item in value:
        raw = item.value if isinstance(item, ComponentCategory) else str(item)
        if raw in allowed:
            result.append(allowed[raw])
    return dedupe_categories(result) or list(fallback)


def normalize_enum_value(value: Any, enum_cls: Any, fallback: Any) -> Any:
    raw = value.value if hasattr(value, "value") else str(value)
    for item in enum_cls:
        if item.value == raw:
            return item
    return fallback


def normalize_string_list(value: Any, fallback: Optional[List[str]] = None) -> List[str]:
    if not isinstance(value, list):
        return list(fallback or [])
    return dedupe_strings([str(item).strip() for item in value if str(item).strip()])


def has_any(text: str, keywords: List[str]) -> bool:
    return any(keyword.lower() in text.lower() for keyword in keywords)


def dedupe_categories(items: List[ComponentCategory]) -> List[ComponentCategory]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def dedupe_strings(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def model_to_dict(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    return dict(value)


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


# ── 🟢 新增: 事实校验层 (⑥.5) ──

_PRICE_DEVIATION_THRESHOLD = 0.30  # 价格偏差超过 30% 自动修正
_FACT_FAILURE_THRESHOLD = 0.50     # 失败率超过 50% 降级


def fact_check_result(
    payload: Dict[str, Any],
    catalog: Any,
) -> Dict[str, Any]:
    """Validate recommendation results against the real product catalog.

    Returns enriched payload with ``fact_check`` metadata.  Corrections
    are applied in-place to the payload.

    校验项:
    1. product_id 是否存在于真实商品库
    2. 价格与 catalog 真实售价偏差 ≤ 30%，否则自动修正
    3. 库存状态标记（不剔除，仅记录）
    """
    cards = payload.get("product_cards") or []
    catalog_get = getattr(catalog, "get", None)
    if not catalog_get or not callable(catalog_get):
        return {**payload, "fact_check": {"passed": True, "product_count": len(cards), "issues": [], "note": "catalog_not_available"}}

    total = len(cards)
    if total == 0:
        return {**payload, "fact_check": {"passed": True, "product_count": 0, "issues": []}}

    issues: List[Dict[str, Any]] = []
    fixed = 0
    removed = 0
    valid_cards = []

    for i, card in enumerate(cards):
        pid = card.get("product_id", "")
        product = catalog_get(pid)
        if not product:
            issues.append({"index": i, "product_id": pid, "issue": "not_found_in_catalog"})
            removed += 1
            continue

        # 价格校验
        real_price = getattr(product, "base_price", None)
        card_price = card.get("price")
        if real_price is not None and card_price is not None:
            try:
                rp, cp = float(real_price), float(card_price)
            except (TypeError, ValueError):
                rp, cp = None, None
            if rp is not None and cp is not None and rp > 0:
                deviation = abs(cp - rp) / rp
                if deviation > _PRICE_DEVIATION_THRESHOLD:
                    card["price"] = rp
                    card["_original_price"] = cp
                    fixed += 1
                    issues.append({"index": i, "product_id": pid, "issue": "price_corrected", "from": cp, "to": rp})

        # 库存标记
        stock_status = getattr(product, "stock_status", None)
        if stock_status in {"sold_out", "out_of_stock"}:
            issues.append({"index": i, "product_id": pid, "issue": "out_of_stock", "status": stock_status})
        valid_cards.append(card)

    payload["product_cards"] = valid_cards
    valid_ids = {str(card.get("product_id") or "") for card in valid_cards}
    comparison_rows = payload.get("comparison_table") or []
    synchronized_rows = []
    for row in comparison_rows:
        product_id = str(row.get("product_id") or "")
        if product_id not in valid_ids:
            continue
        product = catalog_get(product_id)
        if product is not None:
            real_price = getattr(product, "base_price", None)
            if real_price is not None:
                row["price"] = real_price
        synchronized_rows.append(row)
    payload["comparison_table"] = synchronized_rows
    failure_rate = (removed + fixed) / total if total > 0 else 0.0
    passed = failure_rate <= _FACT_FAILURE_THRESHOLD and removed == 0

    payload["fact_check"] = {
        "passed": passed,
        "product_count": total,
        "valid_count": len(valid_cards),
        "removed": removed,
        "fixed": fixed,
        "issues": issues,
        "failure_rate": round(failure_rate, 2),
    }

    if failure_rate > _FACT_FAILURE_THRESHOLD:
        payload["fact_check"]["degraded"] = True
        # 降级标记：调用方可据此选择返回通用回复
    return payload
