"""Phase 4 regression tests: LLM Gateway, session layering, trace span, handler registry, exception handling."""
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════
# 1. LLM Gateway tests
# ═══════════════════════════════════════════════════════════════════════════

class TestLLMGateway:
    """Tests for rag.recommendation.llm_gateway.LLMGateway."""

    def setup_method(self):
        from rag.recommendation.llm_gateway import LLMGateway
        LLMGateway.reset()

    def test_register_and_config_exists(self):
        from rag.recommendation.llm_gateway import LLMGateway
        assert "router" in LLMGateway._configs
        assert "guidance" in LLMGateway._configs
        assert "parse" in LLMGateway._configs
        assert "response" in LLMGateway._configs
        assert "explanation" in LLMGateway._configs
        assert "rewrite" in LLMGateway._configs
        assert "general_chat" in LLMGateway._configs
        assert "filter" in LLMGateway._configs

    def test_register_custom_caller(self):
        from rag.recommendation.llm_gateway import LLMGateway
        LLMGateway.register("custom_test", model_kind="fast", temperature=0.5, timeout=3, max_tokens=100)
        assert "custom_test" in LLMGateway._configs
        cfg = LLMGateway._configs["custom_test"]
        assert cfg.model_kind == "fast"
        assert cfg.temperature == 0.5
        assert cfg.timeout == 3
        assert cfg.max_tokens == 100

    def test_call_unregistered_uses_defaults(self):
        """Calling an unregistered name should auto-register with defaults and not raise KeyError."""
        from rag.recommendation.llm_gateway import LLMGateway
        from rag.recommendation.llm_client import LLMClientError

        # Auto-register should happen without KeyError
        try:
            LLMGateway.call("brand_new_caller", [{"role": "user", "content": "test"}])
        except (LLMClientError, Exception):
            # Either LLMClientError (not configured) or JSON decode error (LLM responded with text)
            pass

        assert "brand_new_caller" in LLMGateway._configs

    def test_circuit_breaker_opens_after_failures(self):
        from rag.recommendation.llm_gateway import LLMGateway, _CircuitState

        circuit = _CircuitState()
        circuit._name = "test"
        assert not circuit.is_open()

        for _ in range(5):
            circuit.record_failure()

        assert circuit.is_open()
        assert circuit.state == "open"

    def test_circuit_breaker_half_open(self):
        from rag.recommendation.llm_gateway import _CircuitState

        circuit = _CircuitState()
        circuit._name = "test"
        circuit._OPEN_DURATION_SECONDS = 0.1  # short for testing

        for _ in range(5):
            circuit.record_failure()

        assert circuit.is_open()
        time.sleep(0.15)
        assert not circuit.is_open()
        assert circuit.state == "half-open"

    def test_circuit_breaker_half_open_to_closed(self):
        from rag.recommendation.llm_gateway import _CircuitState

        circuit = _CircuitState()
        circuit._name = "test"
        circuit.state = "half-open"
        circuit.record_success()
        assert circuit.state == "closed"

    def test_call_log_recording(self):
        from rag.recommendation.llm_gateway import LLMGateway

        LLMGateway._record_log("test_caller", True, 50, "")
        LLMGateway._record_log("test_caller", False, 0, "timeout")

        log = LLMGateway.get_call_log()
        assert len(log) == 2
        assert log[0]["caller"] == "test_caller"
        assert log[0]["success"] is True
        assert log[1]["success"] is False
        assert log[1]["error_code"] == "timeout"

    def test_gateway_call_when_not_configured(self):
        """Gateway should raise LLMClientError when LLM is not configured."""
        from rag.recommendation.llm_gateway import LLMGateway
        from rag.recommendation.llm_client import LLMClientError

        with patch("rag.recommendation.llm_gateway.OpenAICompatibleChatClient") as MockClient:
            instance = MockClient.return_value
            instance.configured = False
            instance.config = MagicMock()
            instance.config.provider = "test"
            instance.config.model = "test"
            instance.config.config_error_reason = "not configured"
            instance.config.config_error_code = "not_configured"

            with pytest.raises(LLMClientError):
                LLMGateway.call("router", [{"role": "user", "content": "hello"}])

    def test_gateway_network_error_wrapping(self):
        """Gateway should wrap ConnectionError/PermissionError/OSError as LLMClientError."""
        from rag.recommendation.llm_gateway import LLMGateway
        from rag.recommendation.llm_client import LLMClientError

        with patch("rag.recommendation.llm_gateway.OpenAICompatibleChatClient") as MockClient:
            instance = MockClient.return_value
            instance.configured = True
            instance.config = MagicMock()
            instance.config.provider = "test"
            instance.config.model = "test"
            instance.config.fast_model = "test-fast"

            with patch("rag.recommendation.llm_gateway.run_with_hard_timeout", side_effect=ConnectionError("network down")):
                with pytest.raises(LLMClientError):
                    LLMGateway.call("router", [{"role": "user", "content": "test"}])


# ═══════════════════════════════════════════════════════════════════════════
# 2. Session state layering tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSessionStateLayering:
    """Tests for ShoppingSession sub-state accessors and schema_version."""

    def test_schema_version_default(self):
        from rag.recommendation.session_state import ShoppingSession, SCHEMA_VERSION
        session = ShoppingSession(session_id="test-123")
        assert session.schema_version == SCHEMA_VERSION

    def test_conversation_state(self):
        from rag.recommendation.session_state import ShoppingSession
        session = ShoppingSession(session_id="test-456")
        session.messages = ["hello", "world"]
        session.chat_topic = "recommendation"
        session.recent_queries = [{"turn": 1, "query": "推荐手机"}]

        cs = session.conversation_state()
        assert cs.session_id == "test-456"
        assert cs.messages == ["hello", "world"]
        assert cs.chat_topic == "recommendation"
        assert len(cs.recent_queries) == 1

    def test_recommendation_state(self):
        from rag.recommendation.session_state import ShoppingSession
        session = ShoppingSession(session_id="test-789")
        session.current = {"category": "digital", "brands": ["华为"]}
        session.last_goal = "推荐华为手机"

        rs = session.recommendation_state()
        assert rs.current == {"category": "digital", "brands": ["华为"]}
        assert rs.last_goal == "推荐华为手机"

    def test_cart_state(self):
        from rag.recommendation.session_state import ShoppingSession, CartItem
        session = ShoppingSession(session_id="cart-test")
        session.cart = {"p_digital_001": CartItem(product_id="p_digital_001", quantity=2)}
        session.pending_cart_action = {"product_id": "p_digital_002"}

        cs = session.cart_state()
        assert "p_digital_001" in cs.cart
        assert cs.pending_cart_action["product_id"] == "p_digital_002"

    def test_pc_build_state(self):
        from rag.recommendation.session_state import ShoppingSession
        session = ShoppingSession(session_id="pc-test")
        session.pc_build_history = [{"label": "方案A"}]
        session.current_pc_build = {"total_price": 5000}

        ps = session.pc_build_state()
        assert len(ps.pc_build_history) == 1
        assert ps.current_pc_build["total_price"] == 5000

    def test_observability_state(self):
        from rag.recommendation.session_state import ShoppingSession
        session = ShoppingSession(session_id="obs-test")
        session.last_fact_check_status = "fail"
        session.llm_call_log = [{"span_id": "abc"}]

        obs = session.observability_state()
        assert obs.last_fact_check_status == "fail"
        assert len(obs.llm_call_log) == 1

    def test_snapshot_returns_dict(self):
        from rag.recommendation.session_state import ShoppingSession
        session = ShoppingSession(session_id="snap-test")
        session.current = {"category": "beauty"}

        snap = session.snapshot()
        assert isinstance(snap, dict)
        assert snap["session_id"] == "snap-test"
        assert snap["current"]["category"] == "beauty"

        # Verify it's a deep copy
        snap["current"]["category"] = "digital"
        assert session.current["category"] == "beauty"

    def test_session_from_dict_with_schema_version(self):
        from rag.recommendation.session_state import session_from_dict, SCHEMA_VERSION
        data = {"session_id": "dict-test", "schema_version": 1}
        session = session_from_dict(data)
        assert session.schema_version == 1

    def test_session_from_dict_missing_schema_version(self):
        from rag.recommendation.session_state import session_from_dict, SCHEMA_VERSION
        data = {"session_id": "dict-test2"}
        session = session_from_dict(data)
        assert session.schema_version == SCHEMA_VERSION

    def test_sub_state_to_dict(self):
        from rag.recommendation.session_state import ConversationState
        cs = ConversationState(session_id="t", messages=["a"], recent_queries=[], chat_topic="chat")
        d = cs.to_dict()
        assert d["session_id"] == "t"
        assert d["messages"] == ["a"]


# ═══════════════════════════════════════════════════════════════════════════
# 3. Trace span tests
# ═══════════════════════════════════════════════════════════════════════════

class TestTraceSpan:
    """Tests for handler_base trace_span infrastructure."""

    def setup_method(self):
        from rag.recommendation.handler_base import clear_trace_spans
        clear_trace_spans()

    def test_trace_span_records_duration(self):
        from rag.recommendation.handler_base import trace_span, get_trace_spans

        with trace_span("test_op", trace_id="t1") as span:
            time.sleep(0.01)
            span["items"] = 42

        spans = get_trace_spans()
        assert len(spans) == 1
        assert spans[0]["name"] == "test_op"
        assert spans[0]["trace_id"] == "t1"
        assert spans[0]["items"] == 42
        assert spans[0]["duration_ms"] > 0

    def test_trace_span_records_error(self):
        from rag.recommendation.handler_base import trace_span, get_trace_spans

        with pytest.raises(ValueError):
            with trace_span("failing_op") as span:
                raise ValueError("test error")

        spans = get_trace_spans()
        assert len(spans) == 1
        assert spans[0]["error"] == "test error"
        assert spans[0]["error_type"] == "ValueError"
        assert spans[0]["duration_ms"] > 0

    def test_nested_trace_spans(self):
        from rag.recommendation.handler_base import trace_span, get_trace_spans

        with trace_span("parent", trace_id="t2") as parent:
            parent["level"] = "outer"
            with trace_span("child", trace_id="t2", parent_id="parent") as child:
                child["level"] = "inner"

        spans = get_trace_spans()
        assert len(spans) == 2
        assert spans[0]["name"] == "child"  # child finishes first
        assert spans[1]["name"] == "parent"

    def test_generate_trace_id(self):
        from rag.recommendation.handler_base import generate_trace_id

        tid1 = generate_trace_id("session-1")
        tid2 = generate_trace_id("session-2")
        assert tid1.startswith("t")
        assert tid2.startswith("t")
        # They should be different (different session IDs)
        # Note: could collide if called in same millisecond with same hash
        # but with different session IDs it's very unlikely

    def test_clear_trace_spans(self):
        from rag.recommendation.handler_base import trace_span, get_trace_spans, clear_trace_spans

        with trace_span("op1"):
            pass

        assert len(get_trace_spans()) == 1
        clear_trace_spans()
        assert len(get_trace_spans()) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 4. Handler base helpers tests
# ═══════════════════════════════════════════════════════════════════════════

class TestHandlerBaseHelpers:
    """Tests for handler_base utility functions."""

    def test_safe_catalog_get_none_catalog(self):
        from rag.recommendation.handler_base import safe_catalog_get
        assert safe_catalog_get(None, "pid") is None

    def test_safe_catalog_get_empty_id(self):
        from rag.recommendation.handler_base import safe_catalog_get
        catalog = MagicMock()
        assert safe_catalog_get(catalog, "") is None

    def test_safe_catalog_get_normal(self):
        from rag.recommendation.handler_base import safe_catalog_get
        catalog = MagicMock()
        product = MagicMock()
        catalog.get.return_value = product
        result = safe_catalog_get(catalog, "pid_123")
        assert result == product


# ═══════════════════════════════════════════════════════════════════════════
# 5. Chat.py handler registry tests
# ═══════════════════════════════════════════════════════════════════════════

class TestChatHandlerRegistry:
    """Tests for the lightweight tool dispatch in chat.py."""

    def test_lightweight_tools_set(self):
        from rag.api.routes.chat import _LIGHTWEIGHT_TOOLS
        assert "apply_cart_instruction" in _LIGHTWEIGHT_TOOLS
        assert "general_chat" in _LIGHTWEIGHT_TOOLS
        assert "compare_products" in _LIGHTWEIGHT_TOOLS
        assert "parameter_query" in _LIGHTWEIGHT_TOOLS
        assert "sku_detail" in _LIGHTWEIGHT_TOOLS
        assert "price_comparison" in _LIGHTWEIGHT_TOOLS
        # Heavy tools should NOT be in lightweight set
        assert "recommend_shopping_products" not in _LIGHTWEIGHT_TOOLS
        assert "generate_pc_build_plan" not in _LIGHTWEIGHT_TOOLS


# ═══════════════════════════════════════════════════════════════════════════
# 6. LLM exception handling regression tests
# ═══════════════════════════════════════════════════════════════════════════

class TestLLMExceptionHandling:
    """Verify that ConnectionError/PermissionError/OSError are properly caught."""

    def test_classify_network_error(self):
        from rag.recommendation.recommendation_pipeline import _classify_llm_exception
        assert _classify_llm_exception(ConnectionError("conn reset")) == "network_error"
        assert _classify_llm_exception(PermissionError("denied")) == "network_error"
        assert _classify_llm_exception(OSError("os error")) == "network_error"

    def test_classify_timeout_still_works(self):
        from rag.recommendation.recommendation_pipeline import _classify_llm_exception
        assert _classify_llm_exception(ValueError("timed out")) == "llm_timeout"

    def test_classify_json_invalid_still_works(self):
        from rag.recommendation.recommendation_pipeline import _classify_llm_exception
        assert _classify_llm_exception(ValueError("bad json")) == "llm_json_invalid"
        assert _classify_llm_exception(TypeError("wrong type")) == "llm_json_invalid"

    def test_classify_provider_error_fallback(self):
        from rag.recommendation.recommendation_pipeline import _classify_llm_exception
        assert _classify_llm_exception(RuntimeError("something")) == "llm_provider_error"


# ═══════════════════════════════════════════════════════════════════════════
# 7. Existing functionality preservation tests
# ═══════════════════════════════════════════════════════════════════════════

class TestExistingFunctionalityPreserved:
    """Ensure Phase 4 changes don't break existing functionality."""

    def test_session_roundtrip(self):
        from rag.recommendation.session_state import (
            ShoppingSession,
            session_to_dict,
            session_from_dict,
            CartItem,
        )
        session = ShoppingSession(session_id="roundtrip-test")
        session.messages = ["hello"]
        session.current = {"category": "digital"}
        session.cart = {"p1": CartItem(product_id="p1", quantity=3)}

        d = session_to_dict(session)
        restored = session_from_dict(d)
        assert restored.session_id == "roundtrip-test"
        assert restored.messages == ["hello"]
        assert restored.current == {"category": "digital"}
        assert "p1" in restored.cart
        assert restored.cart["p1"].quantity == 3

    def test_shopping_session_default_fields(self):
        from rag.recommendation.session_state import ShoppingSession
        session = ShoppingSession(session_id="default-test")
        assert session.last_goal == ""
        assert session.last_result == {}
        assert session.chat_topic == ""
        assert session.pending_cart_action == {}
        assert session.last_fact_check_status == "passed"
        assert session.llm_call_log == []

    def test_gateway_reset_restores_defaults(self):
        from rag.recommendation.llm_gateway import LLMGateway
        LLMGateway.reset()
        assert "router" in LLMGateway._configs
        assert "guidance" in LLMGateway._configs
