import pytest

from rag.recommendation.session_state import ShoppingSession
from rag.recommendation.tool_router import (
    TOOL_SCHEMAS,
    extract_budget,
    route_shopping_tool_call,
    validate_and_guard_tool_call,
)


def _session(topic_type: str = "unknown") -> ShoppingSession:
    session = ShoppingSession(session_id="router-test")
    session.topic_memory = {
        "topic_type": topic_type,
        "subject": "",
        "route": "",
        "category": "",
        "slots": {},
    }
    return session


def test_tool_schemas_use_openai_function_shape():
    assert all(item["type"] == "function" for item in TOOL_SCHEMAS)
    assert all("function" in item and "parameters" in item["function"] for item in TOOL_SCHEMAS)
    assert {item["function"]["name"] for item in TOOL_SCHEMAS} >= {"general_chat", "compare_products"}


def test_extract_budget_ignores_gpu_model_numbers_without_budget_semantics():
    assert extract_budget("对比 4060 和 4070") is None
    assert extract_budget("我想要 4070 显卡，预算 3000") == 3000
    assert extract_budget("推荐 2 个 500 元以内的耳机") == 500


def test_single_pc_part_routes_to_product_recommendation():
    call = route_shopping_tool_call("推荐一个白色机箱", _session(), use_llm=False)

    assert call["name"] == "recommend_shopping_products"
    assert call["arguments"]["category"] == "pc_part"


def test_full_pc_intent_routes_to_pc_build_plan():
    call = route_shopping_tool_call("预算 7000 配一台游戏电脑，显卡用 4070", _session(), use_llm=False)

    assert call["name"] == "generate_pc_build_plan"
    assert call["arguments"]["budget"] == 7000


def test_plain_join_word_does_not_force_cart_route():
    call = route_shopping_tool_call("推荐加入降噪功能的耳机，预算 500 元以内", _session(), use_llm=False)

    assert call["name"] == "recommend_shopping_products"
    assert call["name"] != "apply_cart_instruction"


def test_general_chat_has_route_and_trace():
    call = route_shopping_tool_call("你是谁？这个系统怎么用？", _session(), use_llm=False)

    assert call["name"] == "general_chat"
    assert call["routing_trace"]["local"]["name"] == "general_chat"
    assert "route_overridden" in call["routing_trace"]
    assert "arguments_changed" in call["routing_trace"]


def test_compare_with_product_ids_routes_to_compare_products():
    call = route_shopping_tool_call("对比 p_digital_001 和 p_digital_002 哪个好", _session("normal_product"), use_llm=False)

    assert call["name"] == "compare_products"
    assert call["arguments"]["product_ids"] == ["p_digital_001", "p_digital_002"]


def test_compare_model_numbers_does_not_become_budget_or_pc_build():
    call = route_shopping_tool_call("对比 4060 和 4070", _session("pc_build"), use_llm=False)

    assert call["name"] == "compare_products"
    assert call["arguments"]["budget"] is None


def test_pc_topic_part_replacement_stays_pc_build_followup():
    call = route_shopping_tool_call("显卡换成 4070", _session("pc_build"), use_llm=False)

    assert call["name"] == "generate_pc_build_plan"
    assert call["arguments"]["category"] == "pc_build"


def test_contextual_compare_uses_previous_product_cards():
    session = _session("normal_product")
    session.last_result = {
        "product_cards": [
            {"product_id": "p_digital_001"},
            {"product_id": "p_digital_002"},
        ]
    }

    call = route_shopping_tool_call("这两个哪个好？", session, use_llm=False)

    assert call["name"] == "compare_products"


def test_normal_product_followup_in_active_topic_is_not_general_chat():
    call = route_shopping_tool_call("续航长一点", _session("normal_product"), use_llm=False)

    assert call["name"] == "recommend_shopping_products"


def test_router_word_inside_product_request_does_not_force_general_chat():
    call = route_shopping_tool_call("路由器推荐一下，预算 300 元以内", _session(), use_llm=False)

    assert call["name"] == "recommend_shopping_products"


def test_rx_substring_does_not_create_pc_intent():
    call = route_shopping_tool_call("推荐一款 orxproof 背包", _session(), use_llm=False)

    assert call["name"] == "recommend_shopping_products"


@pytest.mark.parametrize(
    "message",
    [
        "有没有适合学生的笔记本电脑？",
        "数码电子类有哪些商品？",
        "Apple 的商品有哪些？",
        "有没有卖冰箱的？",
        "iPhone 17 Pro 的续航怎么样？",
        "有什么适合送女朋友的礼物？",
    ],
)
def test_search_and_scenario_queries_route_to_product_tools(message):
    call = route_shopping_tool_call(message, _session(), use_llm=False)

    assert call["name"] == "recommend_shopping_products"
    assert call["routing_trace"]["local"]["name"] == "recommend_shopping_products"


@pytest.mark.parametrize(
    "message",
    [
        "iPhone 17 Pro 多少钱？",
        "iPhone 17 Pro 的屏幕尺寸是多少？",
        "这件 T 恤是什么材质的？",
        "三顿半咖啡有哪些口味？",
    ],
)
def test_fact_product_queries_force_product_tool_even_without_llm(message):
    call = route_shopping_tool_call(message, _session(), use_llm=False)

    assert call["name"] == "recommend_shopping_products"


def test_fact_product_query_guard_overrides_general_chat_choice():
    call = validate_and_guard_tool_call(
        "iPhone 17 Pro 多少钱？",
        _session(),
        {
            "name": "general_chat",
            "arguments": {"query": "iPhone 17 Pro 多少钱？"},
            "confidence": 0.99,
            "reason": "simulated bad LLM route",
            "source": "llm",
        },
    )

    assert call["name"] == "recommend_shopping_products"


@pytest.mark.parametrize(
    "message",
    ["续航怎么样？", "不要小米的", "有便宜点的吗？", "这款咖啡有什么口味？"],
)
def test_product_detail_followups_inherit_last_recommendation(message):
    session = _session("normal_product")
    session.last_goal = "推荐一款手机"
    session.last_result = {
        "product_cards": [
            {"product_id": "p_digital_001"},
            {"product_id": "p_digital_002"},
        ]
    }

    call = route_shopping_tool_call(message, session, use_llm=False)

    assert call["name"] == "recommend_shopping_products"
    assert call["source"] == "followup_guard"
    assert call["arguments"]["product_ids"] == ["p_digital_001", "p_digital_002"]
    assert "用户追问" in call["arguments"]["query"]


def test_product_detail_followup_guard_overrides_general_chat_choice():
    session = _session("normal_product")
    session.last_goal = "推荐一款咖啡"
    session.last_result = {"product_cards": [{"product_id": "p_food_001"}]}

    call = validate_and_guard_tool_call(
        "有什么口味？",
        session,
        {
            "name": "general_chat",
            "arguments": {"query": "有什么口味？"},
            "confidence": 0.99,
            "reason": "simulated bad LLM route",
            "source": "llm",
        },
    )

    assert call["name"] == "recommend_shopping_products"
    assert call["arguments"]["product_ids"] == ["p_food_001"]


def test_phone_routes_to_ecommerce_scope():
    call = route_shopping_tool_call("推荐一款手机", _session(), use_llm=False)

    assert call["name"] == "recommend_shopping_products"
    assert call["arguments"]["catalog_scope"] == "ecommerce"


def test_gpu_query_routes_to_pc_parts_scope_not_pc_build():
    call = route_shopping_tool_call("推荐一款 RTX 4070 显卡", _session(), use_llm=False)

    assert call["name"] == "recommend_shopping_products"
    assert call["arguments"]["catalog_scope"] == "pc_parts"
    assert call["arguments"]["category"] == "pc_part"


def test_gpu_model_with_bundle_words_routes_to_pc_build():
    call = route_shopping_tool_call("4070 显卡，预算 7000，帮我配一套", _session(), use_llm=False)

    assert call["name"] == "generate_pc_build_plan"
    assert call["arguments"]["budget"] == 7000


def test_plain_gpu_budget_routes_to_pc_part_not_pc_build():
    call = route_shopping_tool_call("推荐一款 4000 元以内的 RTX 4070 显卡", _session(), use_llm=False)

    assert call["name"] == "recommend_shopping_products"
    assert call["arguments"]["catalog_scope"] == "pc_parts"
    assert call["arguments"]["category"] == "pc_part"


def test_pc_topic_gpu_upgrade_stays_pc_build():
    call = route_shopping_tool_call("显卡强一点", _session("pc_build"), use_llm=False)

    assert call["name"] == "generate_pc_build_plan"


def test_pc_topic_explicit_phone_switches_to_ecommerce():
    call = route_shopping_tool_call("那推荐个手机", _session("pc_build"), use_llm=False)

    assert call["name"] == "recommend_shopping_products"
    assert call["arguments"]["catalog_scope"] == "ecommerce"


def test_phone_config_does_not_route_to_pc_build():
    call = route_shopping_tool_call("这款手机配置怎么样？", _session(), use_llm=False)

    assert call["name"] == "recommend_shopping_products"
    assert call["arguments"]["catalog_scope"] == "ecommerce"


def test_office_quiet_config_routes_to_pc_build():
    call = route_shopping_tool_call("4000 预算，办公用，要求安静省电，给我一套配置", _session(), use_llm=False)

    assert call["name"] == "generate_pc_build_plan"
    assert call["arguments"]["budget"] == 4000


def test_local_route_scores_are_returned():
    call = route_shopping_tool_call("推荐一款手机", _session(), use_llm=False)

    assert "route_scores" in call
    assert "routing_trace" in call
    assert call["route_scores"]["top_name"] == "recommend_shopping_products"


def test_route_scores_keep_separate_confidence_fields():
    call = route_shopping_tool_call("推荐一款手机", _session(), use_llm=False)

    assert "route_scores" in call
    assert "local_rule_confidence" in call
    assert "route_score_confidence" in call
    assert call["route_score_confidence"] == call["route_scores"]["confidence"]


def test_ambiguous_bundle_uses_route_score_confidence_not_hardcoded_confidence():
    session = _session()
    session.runtime_mode = "balanced"

    call = route_shopping_tool_call("4000 预算，给我一套", session, use_llm=False)

    assert "route_scores" in call
    assert call["route_score_confidence"] == call["route_scores"]["confidence"]


def test_fast_mode_never_uses_router_llm():
    session = _session()
    session.runtime_mode = "fast"

    call = route_shopping_tool_call("4000 预算，给我一套", session, use_llm=True)

    assert call["routing_trace"]["runtime_mode"] == "fast"
    assert call["routing_trace"]["llm_skipped"] is True


def test_global_llm_off_forces_local_route(monkeypatch):
    monkeypatch.setenv("MALLMIND_LLM_ENABLED", "false")
    session = _session()
    session.runtime_mode = "full"

    call = route_shopping_tool_call("4000 预算，给我一套", session, use_llm=True)

    assert call["routing_trace"]["llm_skipped"] is True
    assert call["routing_trace"]["llm_skipped_reason"] == "global_llm_disabled"


def test_router_llm_concurrency_limit_records_reason(monkeypatch):
    session = _session()
    session.runtime_mode = "full"

    class FakeSemaphore:
        def acquire(self, timeout=None):
            return False

        def release(self):
            raise AssertionError("release should not be called when acquire failed")

    monkeypatch.setenv("MALLMIND_LLM_ENABLED", "true")
    monkeypatch.setattr("rag.recommendation.tool_router._ROUTER_LLM_SEMAPHORE", FakeSemaphore())

    call = route_shopping_tool_call("4000 预算，给我一套", session, use_llm=True)

    assert call["routing_trace"]["llm_skipped"] is True
    assert call["routing_trace"]["llm_skipped_reason"] == "concurrency_limit"
