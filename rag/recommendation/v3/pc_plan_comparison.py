"""Render PC plan differences from current catalog facts, never model memory."""
from __future__ import annotations

from typing import Any

from rag.recommendation.pc_build import PcPart, compare_pc_build_plans, part_to_payload
from rag.recommendation.pc_types import REQUIRED_PC_ROLES

from .types import PcPlanVersion


class PcPlanComparisonError(ValueError):
    pass


def compare_pc_versions(*, current: PcPlanVersion, previous: PcPlanVersion, all_parts: list[PcPart]) -> dict[str, Any]:
    by_id = {part.product_id: part for part in all_parts}
    current_plan = _snapshot(current, by_id)
    previous_plan = _snapshot(previous, by_id)
    return compare_pc_build_plans(current_plan, previous_plan, baseline_label="上一套方案")


def _snapshot(version: PcPlanVersion, by_id: dict[str, PcPart]) -> dict[str, Any]:
    parts = [by_id[product_id] for product_id in version.part_product_ids if product_id in by_id]
    by_role = {part.role: part for part in parts}
    missing = [role for role in REQUIRED_PC_ROLES if role not in by_role]
    if missing:
        raise PcPlanComparisonError("PC 方案引用的目录配件已缺失，无法比较。")
    payloads = [part_to_payload(role, by_role[role], list(version.usage)) for role in REQUIRED_PC_ROLES]
    return {"parts": payloads, "total_price": round(sum(part.price for part in parts), 2)}
