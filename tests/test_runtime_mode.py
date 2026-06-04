from rag.recommendation.runtime_mode import runtime_policy_for_mode
from rag.recommendation.runtime_mode_selector import choose_runtime_mode


def test_runtime_policy_fast_disables_optional_services(monkeypatch):
    monkeypatch.delenv("MALLMIND_LLM_ENABLED", raising=False)

    policy = runtime_policy_for_mode("fast")

    assert policy.use_router_llm is False
    assert policy.use_requirement_llm is False
    assert policy.use_guidance_llm is False
    assert policy.use_vision_llm is False
    assert policy.use_rag_query_expansion is False
    assert policy.use_milvus_retrieval is False


def test_runtime_policy_balanced_allows_router_requirement_llm_and_milvus(monkeypatch):
    monkeypatch.delenv("MALLMIND_LLM_ENABLED", raising=False)

    policy = runtime_policy_for_mode("balanced", llm_configured=True)

    assert policy.use_router_llm is True
    assert policy.use_requirement_llm is True
    assert policy.use_guidance_llm is False
    assert policy.use_vision_llm is False
    assert policy.use_rag_query_expansion is False
    assert policy.use_milvus_retrieval is True


def test_runtime_policy_full_allows_all_when_llm_configured(monkeypatch):
    monkeypatch.delenv("MALLMIND_LLM_ENABLED", raising=False)

    policy = runtime_policy_for_mode("full", llm_configured=True)

    assert policy.use_router_llm is True
    assert policy.use_requirement_llm is True
    assert policy.use_guidance_llm is True
    assert policy.use_vision_llm is True
    assert policy.use_rag_query_expansion is True
    assert policy.use_milvus_retrieval is True


def test_runtime_policy_full_without_llm_keeps_milvus_but_disables_llm_features(monkeypatch):
    monkeypatch.delenv("MALLMIND_LLM_ENABLED", raising=False)

    policy = runtime_policy_for_mode("full", llm_configured=False)

    assert policy.use_router_llm is False
    assert policy.use_requirement_llm is False
    assert policy.use_guidance_llm is False
    assert policy.use_vision_llm is False
    assert policy.use_rag_query_expansion is False
    assert policy.use_milvus_retrieval is True


def test_runtime_policy_global_llm_off_disables_all_llm_features(monkeypatch):
    monkeypatch.setenv("MALLMIND_LLM_ENABLED", "false")

    policy = runtime_policy_for_mode("full", llm_configured=True)

    assert policy.use_router_llm is False
    assert policy.use_requirement_llm is False
    assert policy.use_guidance_llm is False
    assert policy.use_vision_llm is False
    assert policy.use_rag_query_expansion is False
    assert policy.use_milvus_retrieval is True


def test_choose_runtime_mode_auto_defaults_balanced():
    decision = choose_runtime_mode("推荐一款手机", llm_configured=True)

    assert decision.mode == "balanced"
    assert decision.signals["requested_mode"] == "auto"


def test_choose_runtime_mode_complex_constraints_use_balanced():
    decision = choose_runtime_mode("学生党预算 3000，想买拍照好一点的手机，不要太贵", llm_configured=True)

    assert decision.mode == "balanced"


def test_choose_runtime_mode_image_search_uses_full():
    decision = choose_runtime_mode("根据图片找同款外套", requested_mode="full", has_attachments=True, has_image_data=True, llm_configured=True)

    assert decision.mode == "full"


def test_choose_runtime_mode_without_llm_uses_fast():
    decision = choose_runtime_mode("推荐一款手机", llm_configured=False)

    assert decision.mode == "fast"


def test_choose_runtime_mode_test_env_uses_fast():
    decision = choose_runtime_mode("推荐一款手机", is_test_env=True)

    assert decision.mode == "fast"


def test_choose_runtime_mode_respects_explicit_full():
    decision = choose_runtime_mode("推荐一款手机", requested_mode="full", llm_configured=True)

    assert decision.mode == "full"
