import re
from typing import Any, Dict

from rag.recommendation.pc_build import (
    compare_pc_build_plans,
    generate_pc_build_plan,
    load_pc_parts,
    parse_pc_build_budget,
    parse_pc_usage,
)
from rag.recommendation.session_state import (
    get_previous_pc_build_plan,
    has_last_pc_build_plan,
)


def build_pc_plan_for_message(message: str, session: Any) -> Dict[str, Any]:
    previous = session.last_result if has_last_pc_build_plan(session) else {}
    if previous and is_pc_comparison_only_message(message):
        baseline_offset = comparison_baseline_offset(message) + 1
        baseline = get_previous_pc_build_plan(session, baseline_offset)
        plan = dict(previous)
        if baseline:
            label = "上上一个方案" if baseline_offset == 3 else "上一个方案"
            plan["comparison"] = compare_pc_build_plans(previous, baseline, baseline_label=label)
        plan["_transient_comparison"] = True
        return plan

    budget = parse_pc_build_budget(message, default=float(previous.get("budget") or 7000))
    preferences = parse_pc_preferences(message)
    usage = parse_pc_usage(message)
    comparison_offset = comparison_baseline_offset(message)
    comparison_baseline = get_previous_pc_build_plan(session, comparison_offset) if previous else None
    comparison_label = "上上一个方案" if comparison_offset == 2 else "上一个方案"

    if previous:
        if not usage or usage == ["日常使用"]:
            usage = list(previous.get("usage") or usage)
        previous_preferences = dict(previous.get("preferences") or {})
        previous_preferences.update(preferences)
        preferences = previous_preferences
        lowered = message.lower()
        if any(term in message for term in ["便宜", "预算降", "降低预算", "降到", "减少预算"]):
            budget = max(1, float(previous.get("budget") or budget) - parse_adjustment_amount(message, default=500))
            preferences["adjustment"] = f"预算调整到 {budget:.0f} CNY"
        elif "显卡" in message and any(term in message for term in ["强", "升级", "更好"]):
            preferences["gpu_priority"] = "stronger"
            preferences["adjustment"] = "显卡性能优先"
        elif "黑色" in message or "black" in lowered:
            preferences["color"] = "黑色"
            preferences["adjustment"] = "偏好黑色机箱/显卡"
        elif "白色" in message or "white" in lowered:
            preferences["color"] = "白色"
            preferences["adjustment"] = "偏好白色机箱/显卡"

    validate_pc_part_request(preferences)
    plan = generate_pc_build_plan(
        budget=budget,
        usage=usage,
        preferences=preferences,
        previous_plan=comparison_baseline,
        comparison_label=comparison_label,
    )
    if wants_plan_comparison(message) and not plan.get("comparison") and comparison_baseline:
        plan["comparison"] = compare_pc_build_plans(plan, comparison_baseline, baseline_label=comparison_label)
    return plan


def comparison_baseline_offset(message: str) -> int:
    if any(term in message for term in ["上上", "上上轮", "前两轮", "前两个", "倒数第二"]):
        return 2
    return 1


def wants_plan_comparison(message: str) -> bool:
    return any(term in message for term in ["对比", "比较", "提升", "差别", "区别", "比在哪"])


def is_pc_comparison_only_message(message: str) -> bool:
    if not wants_plan_comparison(message):
        return False
    lowered = message.lower()
    clean_build_terms = [
        "预算", "加到", "降到", "换", "更强", "便宜", "贵一点",
        "白色", "黑色", "颜色", "色系", "低噪", "静音",
        "4060", "4070", "4080", "rx",
    ]
    if any(term in lowered for term in clean_build_terms):
        return False
    build_terms = [
        "预算", "加到", "降到", "换", "更强", "便宜", "贵一点",
        "白色", "低噪", "静音", "4060", "4070", "4080", "rx",
    ]
    return not any(term in lowered for term in build_terms)


def format_pc_plan_comparison_text(comparison: Dict[str, Any]) -> str:
    highlights = comparison.get("highlights") or []
    changes = comparison.get("changes") or []
    lines = ["方案对比：" + " ".join(str(item) for item in highlights if item)]
    if changes:
        details = []
        for item in changes[:4]:
            details.append(f"{item.get('role_name')}: {item.get('from')} -> {item.get('to')}；{item.get('reason')}。")
        lines.append("主要变化：" + "；".join(details))
    return "\n".join(lines)


def validate_pc_part_request(preferences: Dict[str, Any]) -> None:
    terms = [str(term).lower().replace(" ", "") for term in preferences.get("gpu_model_terms") or []]
    price_max = preferences.get("gpu_price_max")
    if not terms and not price_max:
        return
    gpus = [part for part in load_pc_parts() if part.role == "pc_gpu"]
    if price_max is not None:
        gpus = [part for part in gpus if part.price <= float(price_max)]
    if terms:
        gpus = [
            part for part in gpus
            if all(term in " ".join([part.title, part.model, *part.tags]).lower().replace(" ", "") for term in terms)
        ]
    if not gpus:
        term_text = " ".join(terms) or "显卡"
        price_text = f" 且价格不高于 {price_max:g} CNY" if price_max is not None else ""
        raise ValueError(f"商品库里没有找到满足 {term_text}{price_text} 的显卡，无法按这个条件替换。")


def parse_adjustment_amount(text: str, default: float = 500) -> float:
    raw = text or ""
    patterns = [
        r"(?:预算降|降低|便宜|少)\s*(\d+(?:\.\d+)?)\s*(?:元|块|CNY|cny)?",
        r"(?:加|增加|贵)\s*(\d+(?:\.\d+)?)\s*(?:元|块|CNY|cny)?",
        r"(\d+(?:\.\d+)?)\s*(?:元|块|CNY|cny)\s*(?:左右|以内)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.I)
        if match:
            return float(match.group(1))
    return default


def parse_pc_preferences(text: str) -> Dict[str, Any]:
    raw_text = text or ""
    lowered = raw_text.lower()
    normalized = lowered.replace(" ", "")
    preferences: Dict[str, Any] = {}

    if "黑色" in raw_text or "black" in lowered:
        preferences["color"] = "黑色"
    elif "白色" in raw_text or "white" in lowered:
        preferences["color"] = "白色"
    if any(term in raw_text for term in ["低噪", "安静", "静音", "降噪"]):
        preferences["noise"] = "低噪音"
    if any(term in raw_text for term in ["以内", "不超过", "低于", "小于", "最多", "封顶", "<="]) or any(
        term in lowered for term in ["within", "under", "no more than"]
    ):
        preferences["budget_strict"] = True
    clean_gpu_requested = "显卡" in raw_text or "gpu" in lowered
    if clean_gpu_requested and any(term in raw_text for term in ["强", "升级", "更好"]):
        preferences["gpu_priority"] = "stronger"
    if clean_gpu_requested and any(term in raw_text for term in ["换", "替换", "更换", "升级到"]):
        preferences["gpu_priority"] = "upgrade"
        preferences["adjustment"] = "替换显卡"

    excluded = re.findall(r"(?:不要|不想要|排除)\s*([\w\u4e00-\u9fff]+)", raw_text)
    if excluded:
        preferences["exclude_brands"] = excluded

    gpu_terms = []
    for match in re.findall(
        r"(40[0-9]{2}\s*ti|40[0-9]{2}ti|rtx\s*40[0-9]{2}\s*ti|rtx\s*40[0-9]{2}|rx\s*\d{4}\s*xt|rx\s*\d{4})",
        normalized,
        flags=re.I,
    ):
        gpu_terms.append(match.replace("rtx", "").replace("rx", "").strip())
    if gpu_terms:
        preferences["gpu_model_terms"] = gpu_terms

    if clean_gpu_requested:
        price_match = re.search(r"(?:低于|不超过|以内|小于|<=|<)\s*(\d+(?:\.\d+)?)", raw_text)
        if price_match:
            preferences["gpu_price_max"] = float(price_match.group(1))
    return preferences
