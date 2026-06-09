import re
from typing import Any, Dict, Optional

from rag.recommendation.pc_build import (
    compare_pc_build_plans,
    generate_pc_build_plan,
    load_pc_parts,
    parse_amount_value,
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

    # ── 注入 session.current 中路由器累积的信号 ──
    current = getattr(session, "current", None) or {}
    if current.get("brands"):
        preferences["brands"] = current["brands"]
    if current.get("exclude_brands"):
        preferences["exclude_brands"] = current["exclude_brands"]
    if current.get("must_have_terms"):
        preferences["must_have_terms"] = current["must_have_terms"]
    if current.get("price_max") is not None:
        budget = current["price_max"]
    accumulated_prefs = current.get("preferences") or {}
    if isinstance(accumulated_prefs, dict):
        for key, val in accumulated_prefs.items():
            if key not in preferences and val:
                preferences[key] = val
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
        target_budget = parse_budget_target_amount(message)
        delta_budget = parse_budget_delta_amount(message)
        if target_budget is not None:
            budget = max(1, target_budget)
            preferences["adjustment"] = f"预算调整到 {budget:.0f} CNY"
        elif delta_budget is not None:
            budget = max(1, float(previous.get("budget") or budget) + delta_budget)
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
    amount = parse_budget_delta_amount(text)
    if amount is not None:
        return abs(amount)
    target = parse_budget_target_amount(text)
    if target is not None:
        return target
    return default


def parse_budget_target_amount(text: str) -> Optional[float]:
    raw = text or ""
    patterns = [
        r"(?:降到|降至|压到|压在|控制到|控制在|改到|改成|提高到|升到)\s*(\d+(?:[\s,，]\d{3})*(?:\.\d+)?)\s*(k|K|w|W|千|万|元|块|CNY|cny)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.I)
        if match:
            return parse_amount_value(match.group(1), match.group(2) or "")
    return None


def parse_budget_delta_amount(text: str) -> Optional[float]:
    raw = text or ""
    patterns = [
        (r"(?:预算降|降低预算|减少预算|降低|减少|少|便宜|压低)\s*(\d+(?:[\s,，]\d{3})*(?:\.\d+)?)\s*(k|K|w|W|千|万|元|块|CNY|cny)?", -1.0),
        (r"(?:预算加|增加预算|提高|增加|加)\s*(\d+(?:[\s,，]\d{3})*(?:\.\d+)?)\s*(k|K|w|W|千|万|元|块|CNY|cny)?", 1.0),
    ]
    for pattern, sign in patterns:
        match = re.search(pattern, raw, flags=re.I)
        if match:
            return sign * parse_amount_value(match.group(1), match.group(2) or "")
    return None


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
    if any(term in raw_text for term in ["无独显", "核显", "集显", "不需要独立显卡", "不想要独立显卡", "不要独立显卡"]):
        preferences["no_discrete_gpu"] = True
        preferences["scenario_note"] = "无独显/核显办公"
    if any(term.lower() in lowered for term in ["ai", "cuda", "深度学习", "本地模型", "大模型", "llm", "显存"]):
        preferences["workload"] = "ai"
        preferences["gpu_priority"] = "nvidia_vram"
        preferences["scenario_note"] = "AI/CUDA/显存优先"
    if any(term in raw_text for term in ["多开", "模拟器", "安卓模拟器"]):
        preferences["workload"] = "emulator"
        preferences["cpu_memory_priority"] = True
        preferences["scenario_note"] = "多开模拟器"
    if any(term.lower() in lowered for term in ["摄影后期", "修图", "lightroom", "photoshop", "ps", "lr"]):
        preferences["workload"] = "photo"
        preferences["cpu_memory_storage_priority"] = True
        preferences["scenario_note"] = "Lightroom/Photoshop 修图"
    if any(term.lower() in lowered for term in ["音乐制作", "编曲", "daw", "录音"]):
        preferences["workload"] = "music"
        preferences["cpu_memory_storage_priority"] = True
        preferences["noise"] = preferences.get("noise") or "低噪音"
        preferences["scenario_note"] = "音乐制作/编曲"
    if any(term.lower() in lowered for term in ["程序开发", "开发", "编译", "docker", "ide", "虚拟机"]):
        preferences["workload"] = "development"
        preferences["cpu_memory_priority"] = True
        preferences["scenario_note"] = "开发/Docker/虚拟机"
    if any(term in raw_text for term in ["网游", "电竞", "LOL", "瓦罗兰特", "CS2"]):
        preferences["workload"] = "esports"
        preferences["avoid_overkill_gpu"] = True
        preferences["scenario_note"] = "网游/电竞/LOL/瓦罗兰特"
    if any(term in raw_text for term in ["刷网页", "网页"]) and any(term in raw_text for term in ["预算", "配电脑", "电脑"]):
        preferences["workload"] = "office_web"
        preferences["avoid_overkill_gpu"] = True
        preferences["budget_overkill_warning"] = True
        preferences["scenario_note"] = "刷网页/办公"
    if any(term in raw_text for term in ["瓶颈", "带得动", "压得住"]):
        preferences["workload"] = "bottleneck"
        preferences["scenario_note"] = "CPU/GPU 瓶颈分析"
        preferences["bottleneck_note"] = True
    if "兼容" in raw_text:
        preferences["compatibility_question"] = True
        compatibility_terms = []
        for match in re.findall(r"(i[3579][-\s]?\d+[A-Za-z]*|B\d{3}|Z\d{3}|DDR[45])", raw_text, flags=re.I):
            compatibility_terms.append(match)
        if compatibility_terms:
            preferences["compatibility_terms"] = compatibility_terms
    if "4K" in raw_text or "4k" in lowered:
        preferences["gpu_priority"] = "stronger"
        preferences["scenario_note"] = "4K 游戏显卡优先"
    if any(term in raw_text for term in ["主机加显示器", "主机和显示器", "电脑加显示器", "整机加显示器", "台式机加显示器", "主机+显示器", "主机加屏幕"]) or ("显示器" in raw_text and any(term in raw_text for term in ["主机", "电脑", "整机", "装机"])):
        preferences["needs_monitor"] = True
        preferences["monitor_note"] = "显示器暂不在本地 PC 配件库中，主机方案不编造显示器 SKU。"
        monitor_budget = re.search(r"显示器(?:控制在|预算|不超过|最多)?\s*(\d+(?:[\s,，]\d{3})*(?:\.\d+)?)", raw_text)
        if monitor_budget:
            preferences["monitor_budget"] = parse_amount_value(monitor_budget.group(1), "")
    range_match = re.search(r"(?<![A-Za-z])(\d+(?:[\s,，]\d{3})*(?:\.\d+)?)\s*(k|K|w|W|千|万|元|块)?\s*(?:-|到|至|~|～)\s*(\d+(?:[\s,，]\d{3})*(?:\.\d+)?)\s*(k|K|w|W|千|万|元|块)?", raw_text)
    if range_match:
        preferences["budget_min"] = parse_amount_value(range_match.group(1), range_match.group(2) or "")
        preferences["budget_max"] = parse_amount_value(range_match.group(3), range_match.group(4) or "")
    max_match = re.search(r"(?:最多|不超过|不超|上限|封顶)\s*(\d+(?:[\s,，]\d{3})*(?:\.\d+)?)\s*(k|K|w|W|千|万|元|块)?", raw_text)
    if max_match:
        preferences["budget_max"] = parse_amount_value(max_match.group(1), max_match.group(2) or "")
    min_match = re.search(r"(?:至少|按)\s*(\d+(?:[\s,，]\d{3})*(?:\.\d+)?)\s*(k|K|w|W|千|万|元|块)?\s*(?:档次|级别|价位)?", raw_text)
    if min_match:
        preferences["budget_min"] = parse_amount_value(min_match.group(1), min_match.group(2) or "")
    if any(term in raw_text for term in ["以内", "不超过", "低于", "小于", "最多", "封顶", "<="]) or any(
        term in lowered for term in ["within", "under", "no more than"]
    ):
        preferences["budget_strict"] = True
    clean_gpu_requested = "显卡" in raw_text or "gpu" in lowered
    if clean_gpu_requested and any(term in raw_text for term in ["强", "升级", "更好"]):
        preferences["gpu_priority"] = "stronger"
    if "保留显卡" in raw_text:
        preferences["keep_gpu"] = True
        preferences["adjustment"] = "保留显卡，调整其他配件"
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
