"""V3 PC execution uses typed budget/usage and stores only a short reference."""
from __future__ import annotations

from types import SimpleNamespace

from rag.recommendation.product_loader import load_combined_product_catalog
from rag.recommendation.v3.pc_executor import execute_v3_pc_plan
from rag.recommendation.v3.promotion import HardConstraintPromotionGate
from rag.recommendation.v3.registry import CatalogNormalizationRegistry
from rag.recommendation.v3.session import empty_session_core, load_session_core, pc_plan_delta
from rag.recommendation.v3.types import PcPlanOperation, PcPlanVersion, RequirementSpecV3, SemanticObservation, V3Action


def test_v3_pc_executor_calls_catalog_solver_with_promoted_fields(monkeypatch):
    captured = {}

    def fake_solver(*, budget, usage, preferences):
        captured.update(budget=budget, usage=usage, preferences=preferences)
        return {"summary": "兼容方案", "parts": [{"product_id": "pc_cpu_1"}, {"product_id": "pc_gpu_1"}]}

    monkeypatch.setattr("rag.recommendation.v3.pc_executor.generate_pc_build_plan", fake_solver)
    session = SimpleNamespace(session_id="pc-v3", v3_core={})
    events = list(
        execute_v3_pc_plan(
            session=session,
            requirement=RequirementSpecV3(action=V3Action.PC_BUILD, price_max=8000),
            observation=SemanticObservation(action=V3Action.PC_BUILD, price_max=8000, pc_usage_surfaces=("游戏",)),
        )
    )
    core = load_session_core(session)
    assert captured == {"budget": 8000, "usage": ["游戏"], "preferences": {}}
    assert core.pc_plans.current is not None
    assert core.pc_plans.current.part_product_ids == ("pc_cpu_1", "pc_gpu_1")
    assert any("pc_build_plan" in event for event in events)


def test_v3_pc_executor_uses_explicit_target_budget_when_no_upper_limit_exists(monkeypatch):
    captured = {}

    def fake_solver(*, budget, usage, preferences):
        captured["budget"] = budget
        return {"summary": "兼容方案", "parts": []}

    monkeypatch.setattr("rag.recommendation.v3.pc_executor.generate_pc_build_plan", fake_solver)
    session = SimpleNamespace(session_id="pc-v3-target", v3_core={})
    list(
        execute_v3_pc_plan(
            session=session,
            requirement=RequirementSpecV3(action=V3Action.PC_BUILD, price_target=7000),
            observation=SemanticObservation(action=V3Action.PC_BUILD, pc_usage_surfaces=("游戏",)),
        )
    )

    assert captured["budget"] == 7000


def test_v3_pc_executor_refuses_missing_execution_fields():
    session = SimpleNamespace(session_id="pc-v3", v3_core={})
    events = list(
        execute_v3_pc_plan(
            session=session,
            requirement=RequirementSpecV3(action=V3Action.PC_BUILD),
            observation=SemanticObservation(action=V3Action.PC_BUILD),
        )
    )
    assert any("pc_execution_unavailable" in event for event in events)


def test_pc_history_keeps_only_current_and_previous_versions():
    first = PcPlanVersion("pc-a", 1, 7000, ("cpu-a",), ("游戏",), None, 9999999999)
    second = PcPlanVersion("pc-b", 2, 6000, ("cpu-b",), ("游戏",), "pc-a", 9999999999)
    core = pc_plan_delta(empty_session_core(), first).core
    updated = pc_plan_delta(core, second).core

    assert updated.pc_plans.current == second
    assert updated.pc_plans.previous == first


def test_pc_component_edit_requires_live_catalog_plan_and_component():
    catalog = load_combined_product_catalog()
    core = pc_plan_delta(
        empty_session_core(),
        PcPlanVersion("pc-a", 1, 7000, ("pc_cpu_1",), ("游戏",), None, 9999999999),
    ).core
    promoted = HardConstraintPromotionGate().promote(
        text="把显卡换强一点",
        observation=SemanticObservation(
            action=V3Action.PC_PLAN_EDIT,
            pc_operation=PcPlanOperation.REPLACE_COMPONENT,
            pc_component_category_surface="显卡",
            upgrade_direction="更强",
        ),
        registry=CatalogNormalizationRegistry.from_catalog(catalog),
        core=core,
    )

    assert promoted.requirement is not None
    assert promoted.requirement.action is V3Action.PC_PLAN_EDIT
