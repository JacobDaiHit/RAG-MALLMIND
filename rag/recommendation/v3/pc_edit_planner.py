"""Prepare safe single-component PC edit constraints for the compatibility solver.

``locked_parts_for_component_replacement`` converts an existing catalog-backed
plan into locks for every unchanged part. It intentionally does not select a
new part: the PC solver must find one compatible catalog candidate or fail
explicitly.
"""
from __future__ import annotations

from rag.recommendation.pc_build import PcPart
from rag.recommendation.pc_types import REQUIRED_PC_ROLES

from .types import PcPlanVersion


class PcEditPlanningError(ValueError):
    pass


def locked_parts_for_component_replacement(
    *, previous: PcPlanVersion, component_role: str, all_parts: list[PcPart], stronger_only: bool
) -> list[PcPart]:
    """Keep seven prior parts exact; give the solver only valid replacements for one role."""

    by_id = {part.product_id: part for part in all_parts}
    selected = [by_id[product_id] for product_id in previous.part_product_ids if product_id in by_id]
    by_role = {part.role: part for part in selected}
    missing = [role for role in REQUIRED_PC_ROLES if role not in by_role]
    if missing:
        raise PcEditPlanningError("旧方案的目录配件不完整，无法安全锁定修改。")
    old = by_role.get(component_role)
    if old is None:
        raise PcEditPlanningError("旧方案不包含要替换的配件类型。")
    replacements = [part for part in all_parts if part.role == component_role and part.product_id != old.product_id]
    if stronger_only:
        replacements = [part for part in replacements if part.price > old.price]
    if not replacements:
        raise PcEditPlanningError("当前目录没有可验证的更强替换配件。")
    return [*(by_role[role] for role in REQUIRED_PC_ROLES if role != component_role), *replacements]
