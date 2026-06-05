from rag.recommendation.adaptive_runtime import select_adaptive_runtime
from rag.recommendation.query_guards import clarification_required
from rag.recommendation.session_state import ShoppingSession
from rag.recommendation.tool_router import route_shopping_tool_call
from rag.recommendation.recommendation_pipeline import recommend_shopping_products


def test_high_confidence_sunscreen_query_does_not_upgrade_full():
    decision = select_adaptive_runtime(
        "推荐一款适合油皮夏天用的防晒",
        local_route={"name": "recommend_shopping_products", "confidence": 0.93, "route_scores": {"confidence": 0.93, "margin": 0.32}},
        llm_configured=True,
    )

    assert decision.selected_mode in {"fast", "balanced"}
    assert decision.selected_mode != "full"


def test_broad_phone_query_requires_clarification():
    guard = clarification_required("推荐一款手机")

    assert guard is not None
    assert guard["clarification_required"] is True
    assert guard["no_match_reason"] == "clarification_required"


def test_clarification_questions_are_top_level_followups():
    result = recommend_shopping_products("推荐一款手机", use_llm=False, use_milvus_retrieval=False)

    assert result.trace["clarification_required"] is True
    assert result.follow_up_questions == result.trace["clarification_questions"]
    assert "clarification_required" not in result.follow_up_questions


def test_compare_request_routes_to_compare_and_is_at_least_balanced():
    session = ShoppingSession(session_id="adaptive-compare")
    call = route_shopping_tool_call("帮我比较两款面霜哪个更保湿", session, use_llm=False)
    decision = select_adaptive_runtime("帮我比较两款面霜哪个更保湿", session=session, local_route=call, llm_configured=True)

    assert call["name"] == "compare_products"
    assert decision.selected_mode in {"balanced", "full"}


def test_llm_unavailable_selects_degraded_fast():
    decision = select_adaptive_runtime("推荐一款手机", llm_configured=False)

    assert decision.selected_mode == "degraded_fast"
    assert decision.fallback_used is True


def test_llm_json_invalid_falls_back_to_degraded_fast():
    decision = select_adaptive_runtime("推荐一款手机", llm_configured=True, llm_failure_reason="JSON invalid from parser")

    assert decision.selected_mode == "degraded_fast"
    assert decision.fallback_reason
