import json
from pathlib import Path

from scripts.eval_model_chain_ablation import build_capability_ablation_conclusions, build_vs_fast_delta, call_counters, capability_aggregate, expected_negative_ok, normalize_product_id_for_eval
from rag.recommendation.recommendation_pipeline import recommend_shopping_products


def test_normalize_product_id_for_eval_strips_pc_prefixes_and_versions():
    assert (
        normalize_product_id_for_eval("pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3")
        == "pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5"
    )
    assert normalize_product_id_for_eval("pc_psu_pc_seed_psu_super_flower_leadex_g_750w_rev4") == "pc_seed_psu_super_flower_leadex_g_750w"
    assert normalize_product_id_for_eval("pc_gpu_pc_seed_gpu_nvidia_geforce_rtx_4060_ti_8g_v2") == "pc_seed_gpu_nvidia_geforce_rtx_4060_ti_8g"


def test_expected_unsupported_accepts_safety_restricted_category():
    case = {"case_group": "negative", "expected_no_match_reason": "unsupported"}

    assert expected_negative_ok(case, "safety_restricted_category", [])
    assert expected_negative_ok(case, "unsupported_category", [])


def test_call_counters_distinguish_llm_attempt_success_and_applied():
    routing_trace = {
        "llm_skipped": False,
        "llm": {"name": "recommend_shopping_products", "arguments": {"query": "q"}},
        "chosen_before_guard": {"name": "recommend_shopping_products", "arguments": {"query": "q"}},
        "final": {"name": "recommend_shopping_products", "arguments": {"query": "q"}},
        "guard_overridden": False,
    }
    trace = {
        "requirement_parsing": {"llm_parse_requested": True, "llm_parse_used": True},
        "llm_guidance": "enabled",
    }

    counters = call_counters(
        type("Group", (), {"router_llm": True, "requirement_llm": True, "guidance_llm": True, "milvus": False})(),
        routing_trace,
        trace,
        {},
        tool_name="recommend_shopping_products",
    )

    assert counters["llm_attempted"] is True
    assert counters["llm_used"] is True
    assert counters["llm_applied"] is True
    assert counters["llm_router_attempted"] is True
    assert counters["llm_router_success"] is True
    assert counters["llm_router_applied"] is True


def test_call_counters_distinguish_rag_availability_and_fallback():
    group = type("Group", (), {"router_llm": False, "requirement_llm": False, "guidance_llm": False, "milvus": True})()

    timeout = call_counters(group, {}, {}, {"status": "timeout", "retrieval_backend": "milvus", "retrieval_timeout": True}, tool_name="recommend_shopping_products")
    assert timeout["rag_attempted"] is True
    assert timeout["retrieval_timeout"] is True
    assert timeout["fallback_to_catalog"] is True
    assert timeout["rag_contribution_evaluable"] is False

    ok = call_counters(group, {}, {}, {"status": "ok", "retrieval_backend": "milvus", "matched_product_ids": ["p1"]}, tool_name="recommend_shopping_products")
    assert ok["embedding_success"] is True
    assert ok["milvus_success"] is True
    assert ok["retrieval_nonempty"] is True
    assert ok["rag_contribution_evaluable"] is True


def test_capability_conclusions_mark_unexercised_router_and_rag():
    rows = [
        {
            "group": "balanced_demo",
            "status": "ok",
            "route_correct": True,
            "expected_product_ids": ["p1"],
            "recommended_product_ids": ["p1"],
            "case_group": "ecommerce",
            "constraint_violation_count": 0,
            "card_metrics": {},
            "latency_ms": 1,
            "timeout": False,
            "errors": [],
            "fallback_triggered": True,
            "degraded_success": False,
            "llm_calls": 1,
            "embedding_calls": 1,
            "milvus_calls": 1,
            "llm_router_used": False,
            "llm_parse_used": False,
            "llm_guidance_used": False,
            "query_expansion_used": False,
            "llm_attempted": True,
            "llm_used": False,
            "llm_applied": False,
            "llm_router_attempted": True,
            "llm_router_success": False,
            "llm_router_applied": False,
            "llm_parse_attempted": False,
            "llm_parse_applied": False,
            "llm_guidance_attempted": False,
            "llm_guidance_applied": False,
            "rag_attempted": True,
            "embedding_success": False,
            "milvus_success": False,
            "retrieval_nonempty": False,
            "retrieval_timeout": True,
            "fallback_to_catalog": True,
            "rag_contribution_evaluable": False,
        }
    ]

    conclusions = build_capability_ablation_conclusions(rows)

    assert "LLM router not effectively exercised" in conclusions["capability_eval"]["LLM_router_B"]
    assert "RAG 未有效测到" in conclusions["capability_eval"]["RAG_B"]


def test_ambiguous_gift_request_returns_clarification_not_invalid_goal():
    result = recommend_shopping_products("送女朋友礼物", use_llm=False, use_milvus_retrieval=False)

    assert result.trace["no_match_reason"] == "clarification_required"
    assert result.trace["clarification_required"] is True
    assert result.follow_up_questions
