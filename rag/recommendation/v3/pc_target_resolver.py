"""Resolve a PC follow-up reference only from unexpired SessionCore versions."""
from __future__ import annotations

from .types import PcPlanReference, PcPlanVersion, SessionCore


class PcPlanReferenceError(ValueError):
    pass


def resolve_pc_plan(core: SessionCore, reference: PcPlanReference | None) -> PcPlanVersion:
    selected = core.pc_plans.previous if reference is PcPlanReference.PREVIOUS else core.pc_plans.current
    if selected is None:
        label = "上一套" if reference is PcPlanReference.PREVIOUS else "当前"
        raise PcPlanReferenceError(f"{label} PC 方案已不存在或已过期，请先生成一套新方案。")
    return selected
