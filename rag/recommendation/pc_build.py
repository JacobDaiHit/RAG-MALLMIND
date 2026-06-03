"""Rule-based PC build planner backed by the local JD PC parts dataset."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from rag.recommendation.pc_compatibility import check_pc_build_compatibility
from rag.recommendation.pc_types import (
    PC_ROLE_TO_LEGACY,
    REQUIRED_PC_ROLES,
    base_model_key,
    legacy_pc_component_type,
    normalize_pc_component_type,
    pc_component_name_zh,
)


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_PC_PRODUCTS_PATH = ROOT_DIR / "data" / "jd_pc_products" / "products.json"
REQUIRED_ROLES = list(REQUIRED_PC_ROLES)
LEGACY_REQUIRED_ROLES = [PC_ROLE_TO_LEGACY[role] for role in REQUIRED_PC_ROLES]


@dataclass(frozen=True)
class PcPart:
    product_id: str
    role: str
    title: str
    brand: str
    model: str
    price: float
    currency: str
    stock_status: str
    stock_quantity: Optional[int]
    specs: Dict[str, Any]
    tags: List[str]
    selling_points: List[str]
    limitations: List[str]
    recommendation_text: str
    source: Dict[str, Any]
    data_path: str = "data/jd_pc_products/products.json"


def load_pc_parts(path: Optional[Path] = None) -> List[PcPart]:
    product_path = path or DEFAULT_PC_PRODUCTS_PATH
    with product_path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    rows = payload if isinstance(payload, list) else payload.get("products", [])
    return [normalize_pc_part(row, source_path=product_path) for row in rows if isinstance(row, dict)]


def normalize_pc_part(row: Dict[str, Any], source_path: Optional[Path] = None) -> PcPart:
    raw = row.get("raw") or {}
    fields = row.get("advice_pc_fields") or {}
    specs = row.get("standardized_specs") or row.get("specs") or {}
    role = normalize_pc_component_type(row.get("component_type") or row.get("part_type") or raw.get("category") or "")
    raw_id = row.get("part_id") or row.get("id") or raw.get("sku") or row.get("model") or ""
    clean_id = str(raw_id).strip() or f"{role}_{len(str(row))}"
    product_id = clean_id if clean_id.startswith(f"{role}_") else f"{role}_{clean_id}"
    source = dict(row.get("source") or {})
    source.pop("image_url", None)
    source.pop("screenshot_path", None)
    source.pop("screenshots", None)
    return PcPart(
        product_id=product_id,
        role=role,
        title=str(row.get("title") or fields.get("title") or raw.get("title") or clean_id),
        brand=str(row.get("brand") or fields.get("brand") or raw.get("brand") or ""),
        model=str(row.get("model") or fields.get("model") or raw.get("model") or ""),
        price=float(row.get("price_cny") or row.get("price") or raw.get("price") or 0),
        currency=str(row.get("currency") or "CNY"),
        stock_status=str(row.get("stock_status") or raw.get("stock_status") or "available_for_demo"),
        stock_quantity=row.get("stock_quantity") if isinstance(row.get("stock_quantity"), int) else None,
        specs=dict(specs),
        tags=split_terms(fields.get("tags") or raw.get("tags") or row.get("tags")),
        selling_points=split_terms(fields.get("selling_points") or raw.get("selling_points") or row.get("selling_points")),
        limitations=split_terms(fields.get("limitations") or raw.get("limitations") or row.get("limitations")),
        recommendation_text=str(fields.get("recommendation_text") or raw.get("recommendation_text") or ""),
        source=source,
        data_path=_relative_path(source_path or DEFAULT_PC_PRODUCTS_PATH),
    )


def split_terms(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None:
        return []
    return [item.strip() for item in re.split(r"[|,，、；;]", str(value)) if item.strip()]


def generate_pc_build_plan(
    budget: float,
    usage: Optional[Iterable[str]] = None,
    preferences: Optional[Dict[str, Any]] = None,
    parts: Optional[List[PcPart]] = None,
    previous_plan: Optional[Dict[str, Any]] = None,
    comparison_label: str = "上一个方案",
) -> Dict[str, Any]:
    if budget <= 0:
        raise ValueError("budget must be greater than 0")
    usage_terms = [str(item) for item in (usage or []) if str(item).strip()]
    prefs = preferences or {}
    all_parts = parts or load_pc_parts()
    grouped = group_parts(deduplicate_pc_parts(all_parts))
    missing = [role for role in REQUIRED_PC_ROLES if not grouped.get(role)]
    if missing:
        raise ValueError(f"missing PC component data: {', '.join(missing)}")

    grouped = limit_grouped_candidates(grouped, budget, prefs)
    budget_limit = resolve_budget_limit(budget, prefs)
    best: Optional[Tuple[float, Dict[str, PcPart], Dict[str, Any], Dict[str, float]]] = None
    evaluated = 0
    rejected = 0

    for cpu in grouped["pc_cpu"]:
        for motherboard in grouped["pc_motherboard"]:
            for memory in grouped["pc_memory"]:
                for gpu in grouped["pc_gpu"]:
                    for case in grouped["pc_case"]:
                        for cooler in grouped["pc_cooler"]:
                            for psu in grouped["pc_psu"]:
                                for storage in grouped["pc_storage"]:
                                    selected = {
                                        "pc_cpu": cpu,
                                        "pc_motherboard": motherboard,
                                        "pc_gpu": gpu,
                                        "pc_memory": memory,
                                        "pc_storage": storage,
                                        "pc_psu": psu,
                                        "pc_case": case,
                                        "pc_cooler": cooler,
                                    }
                                    evaluated += 1
                                    total = sum(part.price for part in selected.values())
                                    if total > budget_limit:
                                        rejected += 1
                                        continue
                                    compatibility = check_pc_build_compatibility(selected, preferences=prefs)
                                    if not compatibility["valid"]:
                                        rejected += 1
                                        continue
                                    soft_scores = soft_score_breakdown(selected, total, budget, usage_terms, prefs)
                                    score = sum(soft_scores.values())
                                    if best is None or score > best[0]:
                                        best = (score, selected, compatibility, soft_scores)

    if best is None:
        return build_no_plan_response(budget, usage_terms, prefs, grouped, evaluated=evaluated, rejected=rejected)

    _score, selected, compatibility, soft_scores = best
    total = round(sum(part.price for part in selected.values()), 2)
    parts_payload = [part_to_payload(role, selected[role], usage_terms) for role in REQUIRED_PC_ROLES]
    plan = {
        "type": "pc_build_plan",
        "title": "本地结构化 PC 整机方案",
        "budget": budget,
        "total_price": total,
        "currency": "CNY",
        "usage": usage_terms,
        "preferences": prefs,
        "summary": build_summary(selected, total, budget, usage_terms, prefs),
        "recommendation_reasons": build_plan_reasons(selected, total, budget, usage_terms, prefs),
        "parts": parts_payload,
        "items": [legacy_item(item) for item in parts_payload],
        "compatibility": compatibility,
        "warnings": collect_warnings(selected, total, budget, budget_limit, compatibility),
        "tradeoffs": build_tradeoffs(selected, total, budget, prefs),
        "upgrade_suggestions": build_upgrade_suggestions(selected),
        "alternatives": build_alternatives(selected, grouped, budget_limit),
        "evidence": build_evidence(selected),
        "trace": {
            "retrieval_mode": "local_pc_catalog",
            "retrieved_chunk_count": len(selected),
            "matched_product_ids": [part.product_id for part in selected.values()],
            "structured_compatibility_validation_applied": True,
            "candidate_counts": {role: len(items) for role, items in grouped.items()},
            "evaluated_build_count": evaluated,
            "rejected_build_count": rejected,
            "soft_scores": soft_scores,
        },
    }
    if previous_plan:
        plan["comparison"] = compare_pc_build_plans(plan, previous_plan, baseline_label=comparison_label)
    return plan


def resolve_budget_limit(budget: float, preferences: Dict[str, Any]) -> float:
    return budget if preferences.get("budget_strict") else budget * 1.08


def deduplicate_pc_parts(parts: List[PcPart]) -> List[PcPart]:
    best: Dict[str, PcPart] = {}
    for part in parts:
        key = base_model_key(part.brand, part.role, part.model, part.title)
        current = best.get(key)
        if current is None or _part_preference_tuple(part) < _part_preference_tuple(current):
            best[key] = part
    return list(best.values())


def _part_preference_tuple(part: PcPart) -> Tuple[float, int]:
    completeness = len([value for value in part.specs.values() if value not in ("", None, [])])
    return (part.price, -completeness)


def group_parts(parts: List[PcPart]) -> Dict[str, List[PcPart]]:
    grouped: Dict[str, List[PcPart]] = {}
    for part in parts:
        grouped.setdefault(part.role, []).append(part)
    for role_parts in grouped.values():
        role_parts.sort(key=lambda item: item.price)
    return grouped


def limit_grouped_candidates(grouped: Dict[str, List[PcPart]], budget: float, preferences: Optional[Dict[str, Any]] = None) -> Dict[str, List[PcPart]]:
    preferences = preferences or {}
    gpu_cap = budget * (0.72 if preferences.get("gpu_priority") in {"stronger", "upgrade"} else 0.66)
    price_caps = {
        "pc_cpu": budget * 0.38,
        "pc_motherboard": budget * 0.30,
        "pc_gpu": gpu_cap,
        "pc_memory": budget * 0.22,
        "pc_storage": budget * 0.22,
        "pc_psu": budget * 0.22,
        "pc_case": budget * 0.20,
        "pc_cooler": budget * 0.16,
    }
    limits = {"pc_cpu": 5, "pc_motherboard": 6, "pc_gpu": 7, "pc_memory": 4, "pc_storage": 4, "pc_psu": 4, "pc_case": 5, "pc_cooler": 4}
    limited: Dict[str, List[PcPart]] = {}
    for role, role_parts in grouped.items():
        cap = price_caps.get(role, budget)
        if role == "pc_gpu" and preferences.get("gpu_price_max"):
            cap = min(cap, float(preferences["gpu_price_max"]))
        affordable = [part for part in role_parts if part.price <= cap]
        if role == "pc_gpu" and preferences.get("gpu_model_terms"):
            terms = [str(term).lower().replace(" ", "") for term in preferences["gpu_model_terms"]]
            matched = [
                part for part in affordable
                if all(term in " ".join([part.title, part.model, *part.tags]).lower().replace(" ", "") for term in terms)
            ]
            affordable = matched
        limited[role] = sample_price_spread(affordable or role_parts, limits.get(role, 8))
    return limited


def sample_price_spread(parts: List[PcPart], limit: int) -> List[PcPart]:
    if len(parts) <= limit:
        return list(parts)
    indexes = {0, len(parts) - 1}
    step = (len(parts) - 1) / float(max(limit - 1, 1))
    for index in range(limit):
        indexes.add(round(index * step))
    return [parts[index] for index in sorted(indexes)[:limit]]


def soft_score_breakdown(selected: Dict[str, PcPart], total: float, budget: float, usage_terms: List[str], preferences: Dict[str, Any]) -> Dict[str, float]:
    gpu = selected["pc_gpu"]
    cpu = selected["pc_cpu"]
    memory = selected["pc_memory"]
    storage = selected["pc_storage"]
    case = selected["pc_case"]
    cooler = selected["pc_cooler"]
    psu = selected["pc_psu"]
    usage_text_value = "".join(usage_terms).lower()
    scores = {
        "budget_fit": max(0.0, 1.0 - abs(budget - total) / max(budget, 1)),
        "performance_fit": min((gpu.price / max(total, 1)) * 1.8, 1.0),
        "noise_fit": 0.5,
        "appearance_fit": 0.5,
        "upgrade_fit": 0.5,
        "value_fit": min((gpu.price + cpu.price) / max(total, 1), 1.0),
        "evidence_fit": min(sum(len(part.specs) for part in selected.values()) / 80, 1.0),
    }
    if any(term in usage_text_value for term in ["game", "游戏", "3a", "2k"]):
        scores["performance_fit"] += 0.25 if gpu.price >= 1800 else 0.05
    if preferences.get("gpu_priority") in {"stronger", "upgrade"}:
        scores["performance_fit"] += 0.25
    if str(preferences.get("noise") or "").lower() in {"低噪音", "安静", "quiet"} or "低噪" in str(preferences.get("noise") or ""):
        cpu_tdp = float(cpu.specs.get("tdp_w") or 0)
        noise = float(cooler.specs.get("noise_db") or 99)
        scores["noise_fit"] = (0.45 if cpu_tdp <= 105 else 0.2) + (0.45 if noise <= 30 else 0.2)
    color = str(preferences.get("color") or "").lower()
    if color:
        text = " ".join([case.title, gpu.title, *case.tags, *gpu.tags]).lower()
        scores["appearance_fit"] = 0.95 if ("white" in text or "白" in text or color in text) else 0.45
    if str(selected["pc_motherboard"].specs.get("memory_type") or "").upper() == "DDR5":
        scores["upgrade_fit"] += 0.2
    if float(psu.specs.get("wattage_w") or 0) >= float(gpu.specs.get("recommended_psu_w") or 0) + 100:
        scores["upgrade_fit"] += 0.15
    return {key: round(min(value, 1.0), 4) for key, value in scores.items()}


def part_to_payload(role: str, part: PcPart, usage_terms: List[str]) -> Dict[str, Any]:
    return {
        "role": role,
        "role_name": pc_component_name_zh(role),
        "product_id": part.product_id,
        "title": part.title,
        "brand": part.brand,
        "model": part.model,
        "price": part.price,
        "currency": part.currency,
        "stock_status": part.stock_status,
        "stock_quantity": part.stock_quantity,
        "key_specs": part.specs,
        "specs": part.specs,
        "tags": part.tags,
        "reason": build_part_reason(role, part, usage_terms),
        "warnings": part.limitations[:1],
        "source": part.source,
    }


def legacy_item(item: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(item)
    payload["role"] = PC_ROLE_TO_LEGACY.get(str(item.get("role")), str(item.get("role")))
    return payload


def build_part_reason(role: str, part: PcPart, usage_terms: List[str]) -> str:
    specs = part.specs
    if role == "pc_cpu":
        return f"本地数据标明 socket={specs.get('socket', 'unknown')}，核心/线程 {specs.get('cores', 'unknown')}/{specs.get('threads', 'unknown')}，TDP {specs.get('tdp_w', 'unknown')}W。"
    if role == "pc_motherboard":
        return f"与 CPU 使用同平台校验，内存类型 {specs.get('memory_type', 'unknown')}，板型 {specs.get('form_factor', 'unknown')}。"
    if role == "pc_gpu":
        return f"面向 {usage_text(usage_terms)} 保留独显预算，长度 {specs.get('length_mm', 'unknown')}mm，建议电源 {specs.get('recommended_psu_w', 'unknown')}W。"
    if role == "pc_memory":
        return f"内存类型 {specs.get('memory_type', 'unknown')}，容量 {specs.get('capacity_gb', 'unknown')}GB，频率 {specs.get('speed_mhz', 'unknown')}MHz。"
    if role == "pc_storage":
        return f"容量 {specs.get('capacity_gb', 'unknown')}GB，接口 {specs.get('interface', 'unknown')}，顺序读取 {specs.get('read_mb_s', 'unknown')}MB/s。"
    if role == "pc_psu":
        return f"额定功率 {specs.get('wattage_w', 'unknown')}W，按显卡建议功率和整机估算功耗校验。"
    if role == "pc_case":
        return f"主板/显卡/散热器尺寸由机箱字段校验，显卡限长 {specs.get('gpu_clearance_mm', specs.get('max_gpu_length_mm', 'unknown'))}mm。"
    if role == "pc_cooler":
        return f"按 CPU socket、散热能力和机箱限高/冷排位校验，标称能力 {specs.get('cooling_capacity_w', specs.get('tdp_w', 'unknown'))}W。"
    return f"作为 {pc_component_name_zh(role)} 纳入方案，价格和规格来自本地 PC 配件数据。"


def usage_text(usage_terms: List[str]) -> str:
    return "、".join(usage_terms) if usage_terms else "日常使用"


def build_plan_reasons(selected: Dict[str, PcPart], total: float, budget: float, usage_terms: List[str], preferences: Dict[str, Any]) -> List[str]:
    reasons = [
        f"总价 {total:.0f} CNY，预算 {budget:.0f} CNY，价格由本地数据逐项求和。",
        "先执行 CPU/主板 socket、内存类型、机箱尺寸、散热器、显卡长度和电源功率硬校验，再做软评分排序。",
        "RAG/索引只用于证据解释和命中追踪，不替代结构化兼容性判断。",
        f"核心组合为 {selected['pc_cpu'].model} + {selected['pc_gpu'].model}。",
    ]
    if preferences.get("color"):
        reasons.append(f"已把外观偏好作为软约束记录：{preferences['color']}。")
    if preferences.get("noise"):
        reasons.append("已把低噪偏好作为软评分，优先低 TDP CPU 和低噪散热字段。")
    return reasons


def build_summary(selected: Dict[str, PcPart], total: float, budget: float, usage_terms: List[str], preferences: Dict[str, Any]) -> str:
    adjustment = f"本轮按“{preferences['adjustment']}”重新选择。" if preferences.get("adjustment") else ""
    return f"{adjustment}为 {usage_text(usage_terms)} 生成一套本地可审计 PC 方案，总价约 {total:.0f} CNY，核心为 {selected['pc_cpu'].model} + {selected['pc_gpu'].model}。"


def collect_warnings(selected: Dict[str, PcPart], total: float, budget: float, budget_limit: Optional[float], compatibility: Dict[str, Any]) -> List[str]:
    warnings: List[str] = list(compatibility.get("warnings") or [])
    if total > budget:
        warnings.append("总价略高于预算，建议确认预算弹性或继续降低预算。")
    for part in selected.values():
        warnings.extend(part.limitations[:1])
    return dedupe(warnings)[:6]


def build_tradeoffs(selected: Dict[str, PcPart], total: float, budget: float, preferences: Dict[str, Any]) -> List[str]:
    tradeoffs = []
    if total > budget:
        tradeoffs.append("为保持性能和兼容性，总价使用了预算弹性。")
    if preferences.get("color"):
        tradeoffs.append("白色/黑色外观是软约束，仅在标题或标签有证据时加分，不会覆盖硬兼容。")
    if preferences.get("noise"):
        tradeoffs.append("低噪音依赖本地噪音/TDP 字段，真实噪音仍受风扇曲线和环境影响。")
    return tradeoffs


def build_upgrade_suggestions(selected: Dict[str, PcPart]) -> List[str]:
    suggestions = []
    if str(selected["pc_motherboard"].specs.get("memory_type") or "").upper() == "DDR5":
        suggestions.append("DDR5 平台后续升级内存和 CPU 的空间更好。")
    if float(selected["pc_psu"].specs.get("wattage_w") or 0) >= float(selected["pc_gpu"].specs.get("recommended_psu_w") or 0) + 150:
        suggestions.append("电源功率有余量，可支持中等幅度显卡升级。")
    return suggestions


def build_alternatives(selected: Dict[str, PcPart], grouped: Dict[str, List[PcPart]], budget_limit: float) -> List[Dict[str, Any]]:
    alternatives: List[Dict[str, Any]] = []
    current_total = sum(item.price for item in selected.values())
    for role in ["pc_gpu", "pc_cpu", "pc_case"]:
        current = selected[role]
        for part in grouped.get(role, []):
            if part.product_id == current.product_id:
                continue
            delta = round(part.price - current.price, 2)
            if current_total + delta <= budget_limit:
                alternatives.append(
                    {
                        "role": role,
                        "replace_product_id": current.product_id,
                        "with_product_id": part.product_id,
                        "title": part.title,
                        "price_delta": delta,
                        "reason": "同类配件可作为预算、外观或性能偏好的替换候选，替换后仍需重新运行结构化兼容校验。",
                    }
                )
                break
    return alternatives


def build_evidence(selected: Dict[str, PcPart]) -> List[Dict[str, Any]]:
    evidence = []
    for role, part in selected.items():
        evidence.append(
            {
                "product_id": part.product_id,
                "part_id": part.product_id,
                "role": role,
                "title": part.title,
                "source_path": part.data_path,
                "retrieval_mode": "local_pc_catalog",
                "evidence_text": part.recommendation_text or "; ".join(part.selling_points[:2]),
            }
        )
    return evidence


def build_no_plan_response(budget: float, usage_terms: List[str], preferences: Dict[str, Any], grouped: Dict[str, List[PcPart]], *, evaluated: int = 0, rejected: int = 0) -> Dict[str, Any]:
    cheapest = {role: min(parts, key=lambda part: part.price).price for role, parts in grouped.items() if role in REQUIRED_PC_ROLES and parts}
    minimum_total = round(sum(cheapest.values()), 2)
    return {
        "type": "pc_build_plan",
        "title": "未找到完整兼容方案",
        "budget": budget,
        "total_price": 0,
        "currency": "CNY",
        "usage": usage_terms,
        "preferences": preferences,
        "summary": "当前预算或偏好下没有找到完整且通过硬兼容校验的本地 PC 方案。",
        "recommendation_reasons": [
            f"当前数据集中最便宜完整配件总价约 {minimum_total:.0f} CNY。",
            "系统只基于本地结构化商品库计算，不会编造缺失配件。",
        ],
        "parts": [],
        "items": [],
        "compatibility": {"valid": False, "status": "failed", "errors": ["no compatible complete build found"], "warnings": [], "checks": []},
        "warnings": [f"建议提高预算或放宽偏好；当前最便宜完整配件组合约 {minimum_total:.0f} CNY。"],
        "tradeoffs": [],
        "upgrade_suggestions": [],
        "alternatives": [],
        "evidence": [],
        "trace": {
            "retrieval_mode": "local_pc_catalog",
            "retrieved_chunk_count": 0,
            "matched_product_ids": [],
            "structured_compatibility_validation_applied": True,
            "candidate_counts": {role: len(items) for role, items in grouped.items()},
            "evaluated_build_count": evaluated,
            "rejected_build_count": rejected,
        },
    }


def compare_pc_build_plans(current: Dict[str, Any], baseline: Dict[str, Any], baseline_label: str = "上一个方案") -> Dict[str, Any]:
    current_items = _plan_items_by_role(current)
    baseline_items = _plan_items_by_role(baseline)
    price_delta = round(float(current.get("total_price") or 0) - float(baseline.get("total_price") or 0), 2)
    changes = []
    unchanged = []
    for role in REQUIRED_PC_ROLES:
        now = current_items.get(role) or {}
        old = baseline_items.get(role) or {}
        if not now or not old:
            continue
        if now.get("product_id") == old.get("product_id"):
            unchanged.append({"role": role, "role_name": pc_component_name_zh(role), "title": now.get("title")})
            continue
        changes.append(
            {
                "role": role,
                "role_name": pc_component_name_zh(role),
                "from": old.get("title"),
                "to": now.get("title"),
                "price_delta": round(float(now.get("price") or 0) - float(old.get("price") or 0), 2),
                "reason": compare_role_change(role, now, old),
            }
        )
    highlights = [f"总价相对{baseline_label}{format_delta(price_delta)}。"]
    highlights.append("发生变化的配件：" + "、".join(item["role_name"] for item in changes) + "。" if changes else "主要配件与对比方案保持一致。")
    return {
        "baseline_label": baseline_label,
        "current_total": current.get("total_price"),
        "baseline_total": baseline.get("total_price"),
        "price_delta": price_delta,
        "highlights": highlights,
        "changes": changes,
        "unchanged_roles": unchanged,
    }


def _plan_items_by_role(plan: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    result = {}
    for item in plan.get("parts") or plan.get("items") or []:
        try:
            role = normalize_pc_component_type(item.get("role"))
        except ValueError:
            continue
        result[role] = item
    return result


def compare_role_change(role: str, current: Dict[str, Any], baseline: Dict[str, Any]) -> str:
    current_specs = current.get("key_specs") or current.get("specs") or {}
    old_specs = baseline.get("key_specs") or baseline.get("specs") or {}
    keys_by_role = {
        "pc_gpu": ["chipset", "vram_gb", "power_w", "recommended_psu_w"],
        "pc_cpu": ["socket", "cores", "threads", "tdp_w"],
        "pc_memory": ["capacity_gb", "memory_type", "speed_mhz"],
        "pc_storage": ["capacity_gb", "interface", "read_mb_s"],
        "pc_psu": ["wattage_w", "efficiency_rating", "atx_version"],
        "pc_case": ["gpu_clearance_mm", "cooler_clearance_mm", "motherboard_support"],
        "pc_motherboard": ["socket", "chipset", "memory_type", "form_factor"],
        "pc_cooler": ["tdp_w", "cooling_capacity_w", "height_mm", "radiator_size_mm"],
    }
    return compare_specs(current_specs, old_specs, keys_by_role.get(role, []))


def compare_specs(current_specs: Dict[str, Any], old_specs: Dict[str, Any], keys: List[str]) -> str:
    parts = []
    for key in keys:
        old = old_specs.get(key)
        new = current_specs.get(key)
        if old != new and old not in (None, "") and new not in (None, ""):
            parts.append(f"{key}: {old} -> {new}")
    return "；".join(parts) if parts else "结构化字段未显示明确参数差异，主要由预算、偏好或兼容性筛选导致。"


def format_delta(value: float) -> str:
    if value > 0:
        return f"增加 {value:.0f} CNY"
    if value < 0:
        return f"降低 {abs(value):.0f} CNY"
    return "持平"


def role_name(role: str) -> str:
    return pc_component_name_zh(role)


_PC_MONEY_TOKEN = r"(\d+(?:\.\d+)?)\s*(k|K|w|W|千|万|元|块|cny|CNY)?"


def parse_amount_value(value: str, unit: str = "") -> float:
    amount = float(value)
    normalized_unit = (unit or "").strip().lower()
    if normalized_unit in {"k", "千"}:
        return amount * 1000
    if normalized_unit in {"w", "万"}:
        return amount * 10000
    return amount


def parse_pc_build_budget(text: str, default: Optional[float] = 7000) -> Optional[float]:
    raw = text or ""
    patterns = [
        rf"(?:预算|预算在|预算为|控制在|控制到|压到|压在)\s*{_PC_MONEY_TOKEN}",
        rf"{_PC_MONEY_TOKEN}\s*(?:以内|以下|之内|内)",
        rf"{_PC_MONEY_TOKEN}\s*(?:元|块|cny|CNY)?\s*(?:配(?:台|一台)?电脑|装(?:台|一台)?电脑|配主机|装主机|配整机|装整机)",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.I)
        if match:
            return parse_amount_value(match.group(1), match.group(2))
    return default


def parse_pc_usage(text: str) -> List[str]:
    terms = []
    for keyword in ["3A游戏", "2K游戏", "游戏", "轻度剪辑", "剪辑", "AI", "开发", "办公", "直播", "低噪音", "黑神话"]:
        if keyword.lower() in (text or "").lower():
            terms.append(keyword)
    return terms or ["日常使用"]


def _relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def dedupe(items: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
