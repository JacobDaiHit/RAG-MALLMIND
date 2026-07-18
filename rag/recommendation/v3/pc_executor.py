"""Execute typed PC build, edit, and comparison requirements.

``execute_v3_pc_plan`` dispatches a promoted PC action to the directory-backed
solver, edit planner, or comparison renderer, stores current/previous plan
versions through SessionDelta, and emits SSE. It accepts no LLM-generated part
ID and never uses the obsolete PC session flow.
"""
from __future__ import annotations

from hashlib import sha256
import time
from typing import Any, Iterable

from rag.api.sse import sse_event
from rag.recommendation.pc_build import generate_pc_build_plan, load_pc_parts
from rag.recommendation.session_state import save_session

from .pc_edit_planner import PcEditPlanningError, locked_parts_for_component_replacement
from .pc_plan_comparison import PcPlanComparisonError, compare_pc_versions
from .pc_target_resolver import PcPlanReferenceError, resolve_pc_plan
from .registry import CatalogNormalizationRegistry
from .session import apply_session_delta, load_session_core, pc_plan_delta
from .semantic_contracts import PcBuildObservation, PcEditObservation, PcObservation
from .types import PcPlanOperation, PcPlanVersion, RequirementSpecV3, V3Action


PC_PLAN_TTL_SECONDS = 30 * 60


def execute_v3_pc_plan(*, session: Any, requirement: RequirementSpecV3, observation: PcObservation, catalog: Any = None) -> Iterable[str]:
    """Execute only catalog-validated PC actions; LLM surfaces never select product IDs."""

    core = load_session_core(session)
    try:
        if requirement.action is V3Action.PC_PLAN_COMPARE:
            yield from _compare(session=session, core=core)
            return
        if requirement.action is V3Action.PC_BUILD:
            plan, budget, usage = _build(requirement, observation)
            parent_plan_id = None
        elif requirement.action is V3Action.PC_PLAN_EDIT:
            plan, budget, usage, parent_plan_id = _edit(requirement, observation, core, catalog)
        else:
            raise ValueError("unsupported V3 PC action")
    except (ValueError, OSError, KeyError, TypeError, PcEditPlanningError, PcPlanReferenceError) as exc:
        yield sse_event("error", {"label": "装机目录事实暂不可确认", "detail": str(exc), "reason": "pc_execution_unavailable"})
        yield sse_event("done", {"session_id": session.session_id})
        return
    part_ids = _part_ids(plan)
    if not part_ids:
        yield sse_event("error", {"label": "当前预算下没有完整兼容方案", "detail": "目录未找到通过兼容校验的完整配件组合。", "reason": "pc_solver_no_compatible_plan"})
        yield sse_event("done", {"session_id": session.session_id})
        return
    timestamp = time.time()
    previous = core.pc_plans.current
    revision = (previous.revision + 1) if previous else 1
    plan_id = "pc_" + sha256(f"{session.session_id}:{revision}:{budget}:{part_ids}:{timestamp:.6f}".encode("utf-8")).hexdigest()[:16]
    version = PcPlanVersion(plan_id, revision, float(budget), part_ids, tuple(usage), parent_plan_id, timestamp + PC_PLAN_TTL_SECONDS)
    apply_session_delta(session, pc_plan_delta(core, version))
    save_session(session)
    yield sse_event("pc_build_plan", {"schema_version": "pc_build_plan.v3", "plan_id": plan_id, "revision": revision, "parent_plan_id": parent_plan_id, "plan": plan})
    yield sse_event("delta", {"text": str(plan.get("summary") or "已按目录兼容事实生成 PC 方案。")})
    yield sse_event("done", {"session_id": session.session_id})


def _build(requirement: RequirementSpecV3, observation: PcBuildObservation) -> tuple[dict[str, Any], float, tuple[str, ...]]:
    budget = _planning_budget(requirement)
    usage = tuple(observation.usage_surfaces)
    if budget is None or not usage:
        raise ValueError("V3 PC execution requires an explicit budget and usage")
    return generate_pc_build_plan(budget=budget, usage=list(usage), preferences={}), budget, usage


def _edit(requirement: RequirementSpecV3, observation: PcEditObservation, core, catalog: Any) -> tuple[dict[str, Any], float, tuple[str, ...], str]:
    previous = resolve_pc_plan(core, observation.plan_reference)
    usage = previous.usage
    budget = _planning_budget(requirement) or previous.budget
    if observation.operation is PcPlanOperation.ADJUST_BUDGET:
        return (
            generate_pc_build_plan(budget=budget, usage=list(usage), preferences={"budget_strict": True, "adjustment": "budget"}),
            budget,
            usage,
            previous.plan_id,
        )
    if observation.operation is not PcPlanOperation.REPLACE_COMPONENT:
        raise ValueError("V3 PC edit requires a supported edit operation")
    if catalog is None:
        from rag.recommendation.product_loader import load_combined_product_catalog

        catalog = load_combined_product_catalog()
    entity = CatalogNormalizationRegistry.from_catalog(catalog).product_types.get(observation.component_candidate_id or "")
    if entity is None or not entity.canonical_id.startswith("pc_category:"):
        raise ValueError("PC component category is not catalog-validated")
    role = entity.canonical_id[len("pc_category:"):]
    stronger = (observation.upgrade_direction or "").lower() in {"stronger", "upgrade", "更强", "升级"}
    candidates = locked_parts_for_component_replacement(previous=previous, component_role=role, all_parts=load_pc_parts(), stronger_only=stronger)
    preferences = {"budget_strict": True, "adjustment": f"replace:{role}"}
    if role == "pc_gpu" and stronger:
        preferences["gpu_priority"] = "stronger"
    return generate_pc_build_plan(budget=budget, usage=list(usage), preferences=preferences, parts=candidates), budget, usage, previous.plan_id


def _compare(*, session: Any, core) -> Iterable[str]:
    if core.pc_plans.current is None or core.pc_plans.previous is None:
        yield sse_event("clarification", {"question": "请先生成或修改一套方案，再比较当前方案和上一套方案。", "reason": "pc_plan_comparison_unresolved"})
        yield sse_event("done", {"session_id": session.session_id})
        return
    try:
        comparison = compare_pc_versions(current=core.pc_plans.current, previous=core.pc_plans.previous, all_parts=load_pc_parts())
    except (PcPlanComparisonError, ValueError, OSError, KeyError, TypeError) as exc:
        yield sse_event("error", {"label": "PC 方案差异暂不可确认", "detail": str(exc), "reason": "pc_plan_comparison_unavailable"})
        yield sse_event("done", {"session_id": session.session_id})
        return
    yield sse_event("pc_plan_comparison", {"current_plan_id": core.pc_plans.current.plan_id, "previous_plan_id": core.pc_plans.previous.plan_id, "comparison": comparison})
    yield sse_event("delta", {"text": "已按当前目录事实比较两套 PC 方案。"})
    yield sse_event("done", {"session_id": session.session_id})


def _planning_budget(requirement: RequirementSpecV3) -> float | None:
    return requirement.price_target if requirement.price_target is not None else requirement.price_max


def _part_ids(plan: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(item.get("product_id"))
        for item in (plan.get("parts") or plan.get("items") or [])
        if isinstance(item, dict) and item.get("product_id")
    )
