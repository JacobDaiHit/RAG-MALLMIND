"""Tests for cart SKU-level operations + clarification mechanism (improvement-proposals.md).

Covers 4 improvement proposals:
  A: fuzzy_match_cart_item (title/brand keyword matching in cart)
  B: handle_cart_v2 supports remove / set_quantity / clear
  C: cart clarification mechanism (ambiguity detection + SSE events)
  D: set_quantity supports ordinal index (no more `None if action == "set_quantity"`)

Run: pytest tests/test_cart_improvements.py -v
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# NOTE: import session_state first to resolve the schemas ↔ recommendation
# circular dependency before ApiProduct / ComponentCategory are loaded.
from rag.recommendation.session_state import (  # noqa: F401 – side-effect import
    ShoppingSession,
    CartItem,
)
from rag.schemas.recommendation import ApiProduct, ComponentCategory


# ── Fixtures ────────────────────────────────────────────────────────────────

def _make_product(
    product_id: str,
    title: str,
    brand: str = "",
    category: ComponentCategory = ComponentCategory.digital,
    sub_category: str = "",
    base_price: float = 100.0,
) -> ApiProduct:
    return ApiProduct(
        product_id=product_id,
        title=title,
        brand=brand,
        category=category,
        category_name=category.value,
        sub_category=sub_category,
        base_price=base_price,
        min_price=base_price,
        max_price=base_price * 1.2,
    )


def _make_catalog(products: List[ApiProduct]) -> Any:
    """Build a lightweight catalog stub compatible with ProductCatalog."""
    from rag.recommendation.product_loader import ProductCatalog
    by_id = {p.product_id: p for p in products}
    by_category: Dict[ComponentCategory, List[ApiProduct]] = {}
    for p in products:
        by_category.setdefault(p.category, []).append(p)
    return ProductCatalog(
        products=products,
        by_id=by_id,
        by_category=by_category,
        source_path=Path("test://fake"),
    )


def _make_session(session_id: str = "test-session") -> Any:
    from rag.recommendation.session_state import ShoppingSession
    return ShoppingSession(session_id=session_id)


# Sample products — titles use specific brand names as keywords so fuzzy matching
# can distinguish products. Titles do NOT contain generic sub_category terms
# (e.g. "智能手机") to allow same-category ambiguity tests.
_PHONE_A = _make_product("p_digital_001", "OPPO Reno12 5G 全网通旗舰手机", "OPPO", sub_category="智能手机")
_PHONE_B = _make_product("p_digital_002", "华为HUAWEI Pura 70 Pro 曲面屏旗舰手机", "华为", sub_category="智能手机")
_EARPHONE = _make_product("p_digital_003", "索尼WH-1000XM5 无线降噪蓝牙耳机", "索尼", sub_category="蓝牙耳机")
_CREAM = _make_product(
    "p_beauty_001", "兰蔻小黑瓶精华肌底液", "兰蔻",
    category=ComponentCategory.beauty, sub_category="精华",
)
_COFFEE = _make_product(
    "p_food_001", "瑞幸咖啡精品速溶美式", "瑞幸",
    category=ComponentCategory.food, sub_category="咖啡",
)

_ALL_PRODUCTS = [_PHONE_A, _PHONE_B, _EARPHONE, _CREAM, _COFFEE]


@pytest.fixture
def catalog():
    return _make_catalog(_ALL_PRODUCTS)


@pytest.fixture
def session_with_cart(catalog):
    """Session with 3 items in cart: two phones + one earphone."""
    from rag.recommendation.session_state import CartItem
    session = _make_session()
    session.cart = {
        "p_digital_001": CartItem(product_id="p_digital_001", quantity=1),
        "p_digital_002": CartItem(product_id="p_digital_002", quantity=2),
        "p_digital_003": CartItem(product_id="p_digital_003", quantity=1),
    }
    return session


# ════════════════════════════════════════════════════════════════════════════
# 方案 A: 商品名称模糊匹配
# ════════════════════════════════════════════════════════════════════════════

class TestFuzzyMatchCartItem:
    """方案 A: fuzzy_match_cart_item — title/brand keyword matching."""

    def test_match_by_brand(self, catalog):
        from rag.recommendation.session_state import fuzzy_match_cart_item
        cart_ids = ["p_digital_001", "p_digital_002", "p_digital_003"]
        result = fuzzy_match_cart_item("把OPPO删掉", cart_ids, catalog)
        assert result == "p_digital_001"

    def test_match_by_title_keyword(self, catalog):
        from rag.recommendation.session_state import fuzzy_match_cart_item
        cart_ids = ["p_digital_001", "p_digital_002", "p_digital_003"]
        result = fuzzy_match_cart_item("删除HUAWEI", cart_ids, catalog)
        assert result == "p_digital_002"

    def test_match_multiple_keywords_returns_best(self, catalog):
        from rag.recommendation.session_state import fuzzy_match_cart_item
        cart_ids = ["p_digital_001", "p_digital_002", "p_digital_003"]
        # "索尼" + "耳机" matches 2 keywords for p_digital_003 (索尼WH-1000XM5 无线降噪蓝牙耳机)
        result = fuzzy_match_cart_item("把索尼耳机删掉", cart_ids, catalog)
        assert result == "p_digital_003"

    def test_no_match_returns_none(self, catalog):
        from rag.recommendation.session_state import fuzzy_match_cart_item
        cart_ids = ["p_digital_001", "p_digital_002"]
        result = fuzzy_match_cart_item("删除索尼耳机", cart_ids, catalog)
        assert result is None

    def test_empty_cart_returns_none(self, catalog):
        from rag.recommendation.session_state import fuzzy_match_cart_item
        result = fuzzy_match_cart_item("删除OPPO", [], catalog)
        assert result is None

    def test_none_catalog_returns_none(self):
        from rag.recommendation.session_state import fuzzy_match_cart_item
        result = fuzzy_match_cart_item("删除OPPO", ["p_digital_001"], None)
        assert result is None

    def test_cross_category_match(self, catalog):
        from rag.recommendation.session_state import fuzzy_match_cart_item
        cart_ids = ["p_beauty_001", "p_food_001"]
        result = fuzzy_match_cart_item("把兰蔻删了", cart_ids, catalog)
        assert result == "p_beauty_001"


class TestResolveCartProductIds:
    """方案 A: resolve_cart_product_ids — fuzzy match integration in resolution chain."""

    def test_explicit_id_takes_priority_over_fuzzy(self, session_with_cart, catalog):
        from rag.recommendation.session_state import resolve_cart_product_ids
        # Even though "OPPO" fuzzy-matches p_digital_001, explicit IDs should win
        result = resolve_cart_product_ids(
            session_with_cart, "删除OPPO p_digital_002", "remove",
            product_ids=["p_digital_002"], catalog=catalog,
        )
        assert result == ["p_digital_002"]

    def test_fuzzy_match_before_ordinal(self, session_with_cart, catalog):
        from rag.recommendation.session_state import resolve_cart_product_ids
        # "删除华为" should fuzzy-match p_digital_002, not fall to ordinal
        result = resolve_cart_product_ids(
            session_with_cart, "删除华为", "remove", catalog=catalog,
        )
        assert result == ["p_digital_002"]

    def test_ordinal_fallback_when_no_fuzzy(self, session_with_cart, catalog):
        from rag.recommendation.session_state import resolve_cart_product_ids, extract_item_index
        # "删除第二个" — no fuzzy match, should use ordinal
        idx = extract_item_index("删除第二个")
        result = resolve_cart_product_ids(
            session_with_cart, "删除第二个", "remove",
            index=idx, catalog=catalog,
        )
        assert result == ["p_digital_002"]

    def test_fuzzy_match_empty_cart(self, catalog):
        from rag.recommendation.session_state import resolve_cart_product_ids
        session = _make_session()
        result = resolve_cart_product_ids(
            session, "删除OPPO", "remove", catalog=catalog,
        )
        assert result == []


# ════════════════════════════════════════════════════════════════════════════
# 方案 B: handle_cart_v2 支持 remove / set_quantity / clear
# ════════════════════════════════════════════════════════════════════════════

class TestInferCartAction:
    """方案 B: infer_cart_action — correct action detection."""

    def test_remove_keywords(self):
        from rag.recommendation.session_state import infer_cart_action
        for msg in ["删除购物车里的OPPO", "删掉这个", "删了第二个", "移除它", "不要了"]:
            assert infer_cart_action(msg) == "remove", f"Failed for: {msg}"

    def test_set_quantity_keywords(self):
        from rag.recommendation.session_state import infer_cart_action
        for msg in ["把数量改成3", "数量改为2", "修改数量", "把第一个改成5"]:
            assert infer_cart_action(msg) == "set_quantity", f"Failed for: {msg}"

    def test_clear_keywords(self):
        from rag.recommendation.session_state import infer_cart_action
        for msg in ["清空购物车", "全部删除", "删光"]:
            assert infer_cart_action(msg) == "clear", f"Failed for: {msg}"

    def test_add_default(self):
        from rag.recommendation.session_state import infer_cart_action
        assert infer_cart_action("把这个加入购物车") == "add"

    def test_add_with_quantity_instruction_stays_add(self):
        from rag.recommendation.session_state import infer_cart_action
        assert infer_cart_action("把 p_beauty_010 加入购物车，数量 1") == "add"
        assert infer_cart_action("推荐个手机") == "add"


class TestResolveCartAction:
    """方案 B: _resolve_cart_action — explicit op takes priority."""

    def test_explicit_op_priority(self):
        from rag.recommendation.tool_handlers import _resolve_cart_action
        # Router says "remove" even though message looks like "add"
        assert _resolve_cart_action({"operation": "remove"}, "加入购物车") == "remove"

    def test_fallback_to_infer(self):
        from rag.recommendation.tool_handlers import _resolve_cart_action
        assert _resolve_cart_action({}, "删除购物车里的OPPO") == "remove"

    def test_invalid_op_falls_back(self):
        from rag.recommendation.tool_handlers import _resolve_cart_action
        assert _resolve_cart_action({"operation": "invalid_op"}, "删除OPPO") == "remove"


class TestApplyCartInstruction:
    """方案 B: apply_cart_instruction — end-to-end cart operations."""

    def test_remove_by_fuzzy_name(self, session_with_cart, catalog):
        from rag.recommendation.session_state import apply_cart_instruction
        result = apply_cart_instruction(
            session=session_with_cart,
            instruction="删除OPPO Reno",
            catalog=catalog,
        )
        assert result["action"] == "remove"
        assert "p_digital_001" not in session_with_cart.cart
        assert any("移除" in m for m in result["messages"])

    def test_set_quantity_by_fuzzy_name(self, session_with_cart, catalog):
        from rag.recommendation.session_state import apply_cart_instruction
        result = apply_cart_instruction(
            session=session_with_cart,
            instruction="把华为数量改成5",
            catalog=catalog,
        )
        assert result["action"] == "set_quantity"
        assert session_with_cart.cart["p_digital_002"].quantity == 5

    def test_clear_cart(self, session_with_cart, catalog):
        from rag.recommendation.session_state import apply_cart_instruction
        result = apply_cart_instruction(
            session=session_with_cart,
            instruction="清空购物车",
            catalog=catalog,
        )
        assert result["action"] == "clear"
        assert len(session_with_cart.cart) == 0

    def test_add_to_cart(self, catalog):
        from rag.recommendation.session_state import apply_cart_instruction
        session = _make_session()
        result = apply_cart_instruction(
            session=session,
            instruction="把 p_digital_001 加入购物车",
            catalog=catalog,
            product_ids=["p_digital_001"],
        )
        assert result["action"] == "add"
        assert "p_digital_001" in session.cart


class TestBuildConfirmationMessage:
    """方案 B: _build_confirmation_message — operation-aware text."""

    def test_remove_confirmation(self):
        from rag.recommendation.tool_handlers import _build_confirmation_message
        plan = {"product_title": "OPPO Reno12", "quantity": 1, "estimated_unit_price": 2999}
        msg = _build_confirmation_message(plan, "remove")
        assert "移除" in msg
        assert "OPPO Reno12" in msg

    def test_set_quantity_confirmation(self):
        from rag.recommendation.tool_handlers import _build_confirmation_message
        plan = {"product_title": "iPhone 15", "quantity": 3, "estimated_unit_price": 8999}
        msg = _build_confirmation_message(plan, "set_quantity")
        assert "修改为 3" in msg

    def test_add_confirmation(self):
        from rag.recommendation.tool_handlers import _build_confirmation_message
        plan = {"product_title": "OPPO Reno12", "quantity": 2, "estimated_unit_price": 2999}
        msg = _build_confirmation_message(plan, "add")
        assert "加入购物车" in msg


# ════════════════════════════════════════════════════════════════════════════
# 方案 C: 追问机制
# ════════════════════════════════════════════════════════════════════════════

class TestCheckCartAmbiguity:
    """方案 C: _check_cart_ambiguity — ambiguity detection."""

    def test_ordinal_out_of_range(self, catalog):
        from rag.recommendation.session_state import CartItem
        from rag.recommendation.tool_handlers import _check_cart_ambiguity
        # Cart with only 2 items; user asks for the 3rd (extract_item_index max = 2)
        session = _make_session()
        session.cart = {
            "p_digital_001": CartItem(product_id="p_digital_001", quantity=1),
            "p_digital_002": CartItem(product_id="p_digital_002", quantity=1),
        }
        cart_ids = list(session.cart.keys())
        result = _check_cart_ambiguity(session, "删除第三个", cart_ids, catalog)
        assert result is not None
        assert "只有 2 个" in result
        assert "第 3" in result

    def test_same_category_ambiguity(self, catalog):
        from rag.recommendation.session_state import CartItem
        from rag.recommendation.tool_handlers import _check_cart_ambiguity
        # Two phones (智能手机) in cart, neither title contains "智能手机"
        session = _make_session()
        session.cart = {
            "p_digital_001": CartItem(product_id="p_digital_001", quantity=1),
            "p_digital_002": CartItem(product_id="p_digital_002", quantity=1),
        }
        cart_ids = list(session.cart.keys())
        # "删掉那个智能手机" — category term "智能手机" shared by both items
        result = _check_cart_ambiguity(session, "删掉那个智能手机", cart_ids, catalog)
        assert result is not None
        assert "多个" in result

    def test_no_ambiguity_when_fuzzy_hits(self, session_with_cart, catalog):
        from rag.recommendation.tool_handlers import _check_cart_ambiguity
        cart_ids = list(session_with_cart.cart.keys())
        # "删除OPPO" fuzzy-matches exactly one item → no ambiguity
        result = _check_cart_ambiguity(session_with_cart, "删除OPPO", cart_ids, catalog)
        assert result is None

    def test_no_ambiguity_with_ordinal(self, session_with_cart, catalog):
        from rag.recommendation.tool_handlers import _check_cart_ambiguity
        cart_ids = list(session_with_cart.cart.keys())
        # "删除第一个" has explicit ordinal → no ambiguity
        result = _check_cart_ambiguity(session_with_cart, "删除第一个", cart_ids, catalog)
        assert result is None

    def test_no_ambiguity_single_item(self, catalog):
        from rag.recommendation.session_state import CartItem
        from rag.recommendation.tool_handlers import _check_cart_ambiguity
        session = _make_session()
        session.cart = {"p_digital_001": CartItem(product_id="p_digital_001", quantity=1)}
        result = _check_cart_ambiguity(session, "删掉手机", ["p_digital_001"], catalog)
        assert result is None

    def test_empty_cart_no_ambiguity(self, catalog):
        from rag.recommendation.tool_handlers import _check_cart_ambiguity
        session = _make_session()
        result = _check_cart_ambiguity(session, "删除OPPO", [], catalog)
        assert result is None


class TestCartClarificationSSE:
    """方案 C: cart_clarification SSE events are emitted correctly."""

    @staticmethod
    def _collect_sse_events(handler_gen):
        """Parse SSE events from a generator yielding sse_event() strings.

        Each yielded string has the format:
            event: <type>\\ndata: <json>\\n\\n
        """
        events = []
        for raw in handler_gen:
            event_type = None
            data = None
            for line in raw.strip().split("\n"):
                line = line.strip()
                if line.startswith("event: "):
                    event_type = line[7:]
                elif line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        pass
            if event_type and data is not None:
                events.append({"event": event_type, "data": data})
        return events

    def test_ambiguity_emits_cart_clarification(self, session_with_cart, catalog):
        from rag.recommendation.tool_handlers import _handle_cart_modify
        tool_call = {"arguments": {"operation": "remove"}}
        events = self._collect_sse_events(
            _handle_cart_modify(
                session_with_cart, "删掉那个智能手机", catalog,
                "remove", [], tool_call,
            )
        )
        event_types = [e.get("event") for e in events]
        assert "cart_clarification" in event_types
        # Verify cart_items are included for frontend rendering
        clarify_event = next(e for e in events if e.get("event") == "cart_clarification")
        assert "cart_items" in clarify_event.get("data", {})
        assert len(clarify_event["data"]["cart_items"]) == 3

    def test_empty_cart_emits_message(self, catalog):
        from rag.recommendation.tool_handlers import _handle_cart_modify
        session = _make_session()
        tool_call = {"arguments": {"operation": "remove"}}
        events = self._collect_sse_events(
            _handle_cart_modify(session, "删除OPPO", catalog, "remove", [], tool_call)
        )
        event_types = [e.get("event") for e in events]
        assert "delta" in event_types  # "购物车是空的"

    def test_product_not_found_emits_clarification(self, session_with_cart, catalog):
        """When fuzzy match fails AND no ordinal, _handle_cart_modify should
        still find a product (falls back to first item). This tests the
        edge case where the catalog doesn't contain the resolved ID."""
        from rag.recommendation.tool_handlers import _handle_cart_modify
        # Remove p_digital_003 from catalog to trigger product-not-found
        from rag.recommendation.session_state import CartItem
        session = _make_session()
        session.cart = {"nonexistent_pid": CartItem(product_id="nonexistent_pid", quantity=1)}
        tool_call = {"arguments": {"operation": "remove"}}
        events = self._collect_sse_events(
            _handle_cart_modify(session, "删除某个东西", catalog, "remove", [], tool_call)
        )
        event_types = [e.get("event") for e in events]
        assert "cart_clarification" in event_types


# ════════════════════════════════════════════════════════════════════════════
# 方案 D: set_quantity 支持序数
# ════════════════════════════════════════════════════════════════════════════

class TestExtractItemIndex:
    """方案 D: extract_item_index works for all action types."""

    def test_ordinal_patterns(self):
        from rag.recommendation.session_state import extract_item_index
        assert extract_item_index("第一个") == 0
        assert extract_item_index("第二个") == 1
        assert extract_item_index("第三款") == 2
        assert extract_item_index("1号") == 0
        assert extract_item_index("2号") == 1

    def test_no_ordinal(self):
        from rag.recommendation.session_state import extract_item_index
        assert extract_item_index("删除OPPO") is None
        assert extract_item_index("清空购物车") is None


class TestExtractQuantity:
    """方案 D: extract_quantity — no conflict with ordinal patterns."""

    def test_quantity_patterns(self):
        from rag.recommendation.session_state import extract_quantity
        assert extract_quantity("数量改成3") == 3
        assert extract_quantity("改为5") == 5
        assert extract_quantity("修改为2") == 2
        assert extract_quantity("x10") == 10

    def test_no_quantity(self):
        from rag.recommendation.session_state import extract_quantity
        assert extract_quantity("删除第一个") is None
        assert extract_quantity("清空购物车") is None

    def test_no_conflict_with_ordinal(self):
        """'把第二个数量改成3' should yield index=1 AND quantity=3."""
        from rag.recommendation.session_state import extract_item_index, extract_quantity
        msg = "把第二个数量改成3"
        assert extract_item_index(msg) == 1
        assert extract_quantity(msg) == 3


class TestSetQuantityWithOrdinal:
    """方案 D: end-to-end set_quantity with ordinal index."""

    def test_set_quantity_second_item(self, session_with_cart, catalog):
        from rag.recommendation.session_state import apply_cart_instruction
        result = apply_cart_instruction(
            session=session_with_cart,
            instruction="把第二个数量改成5",
            catalog=catalog,
        )
        assert result["action"] == "set_quantity"
        # Second item in cart insertion order is p_digital_002
        assert session_with_cart.cart["p_digital_002"].quantity == 5

    def test_set_quantity_first_item(self, session_with_cart, catalog):
        from rag.recommendation.session_state import apply_cart_instruction
        result = apply_cart_instruction(
            session=session_with_cart,
            instruction="把第一个数量改为10",
            catalog=catalog,
        )
        assert result["action"] == "set_quantity"
        assert session_with_cart.cart["p_digital_001"].quantity == 10

    def test_set_quantity_ordinal_out_of_range(self, session_with_cart, catalog):
        from rag.recommendation.session_state import apply_cart_instruction
        result = apply_cart_instruction(
            session=session_with_cart,
            instruction="把第五个数量改成3",
            catalog=catalog,
        )
        # Should not crash, should report no target
        assert result["action"] == "set_quantity"


# ════════════════════════════════════════════════════════════════════════════
# Integration: handle_cart_v2 dispatch
# ════════════════════════════════════════════════════════════════════════════

class TestHandleCartV2Dispatch:
    """方案 B+C: handle_cart_v2 routes to correct sub-handlers."""

    @staticmethod
    def _collect_sse_events(handler_gen):
        """Parse SSE events from a generator yielding sse_event() strings."""
        events = []
        for raw in handler_gen:
            event_type = None
            data = None
            for line in raw.strip().split("\n"):
                line = line.strip()
                if line.startswith("event: "):
                    event_type = line[7:]
                elif line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        pass
            if event_type and data is not None:
                events.append({"event": event_type, "data": data})
        return events

    def test_clear_dispatches_directly(self, session_with_cart, catalog):
        from rag.recommendation.tool_handlers import handle_cart_v2
        from rag.recommendation.session_state import CartItem
        session = _make_session()
        session.cart = {"p_digital_001": CartItem(product_id="p_digital_001", quantity=1)}
        tool_call = {"arguments": {}}
        events = self._collect_sse_events(
            handle_cart_v2(session, "清空购物车", [], tool_call)
        )
        # Clear should NOT produce cart_confirmation, just delta + cart + done
        event_types = [e.get("event") for e in events]
        assert "cart_confirmation" not in event_types
        assert "delta" in event_types
        assert len(session.cart) == 0

    def test_remove_dispatches_to_modify(self, session_with_cart, catalog):
        from rag.recommendation.tool_handlers import handle_cart_v2
        tool_call = {"arguments": {}}
        # NOTE: handle_cart_v2 loads the real catalog internally;
        # "OPPO Reno" won't fuzzy-match real products, so the handler
        # falls back to the first cart item — but still emits cart_confirmation.
        events = self._collect_sse_events(
            handle_cart_v2(session_with_cart, "删除OPPO Reno", [], tool_call)
        )
        event_types = [e.get("event") for e in events]
        # Should produce cart_confirmation (plan+confirm mode)
        assert "cart_confirmation" in event_types
        confirm_event = next(e for e in events if e.get("event") == "cart_confirmation")
        plan = confirm_event.get("data", {}).get("plan", {})
        assert plan.get("operation") == "remove"

    def test_set_quantity_dispatches_to_modify(self, session_with_cart, catalog):
        from rag.recommendation.tool_handlers import handle_cart_v2
        tool_call = {"arguments": {}}
        events = self._collect_sse_events(
            handle_cart_v2(session_with_cart, "把华为数量改成3", [], tool_call)
        )
        event_types = [e.get("event") for e in events]
        assert "cart_confirmation" in event_types
        confirm_event = next(e for e in events if e.get("event") == "cart_confirmation")
        plan = confirm_event.get("data", {}).get("plan", {})
        assert plan.get("operation") == "set_quantity"
        assert plan.get("quantity") == 3

    def test_pending_cart_plan_is_saved_for_confirm_request(self, monkeypatch):
        """The confirm endpoint may run in a later request, so the plan must be persisted."""
        monkeypatch.setenv("SESSION_BACKEND", "memory")
        from rag.recommendation.session_state import get_session
        from rag.recommendation.tool_handlers import handle_cart_v2

        session = _make_session("cart-pending-save-test")
        tool_call = {"arguments": {"quantity": 2}}
        events = self._collect_sse_events(
            handle_cart_v2(session, "把这个加入购物车", ["p_digital_001"], tool_call)
        )

        assert "cart_confirmation" in [e.get("event") for e in events]
        reloaded = get_session("cart-pending-save-test")
        assert reloaded.pending_cart_action.get("operation") == "add"
        assert reloaded.pending_cart_action.get("product_id") == "p_digital_001"
        assert reloaded.pending_cart_action.get("quantity") == 2


# ════════════════════════════════════════════════════════════════════════════
# Edge cases & robustness
# ════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Robustness checks for the cart improvement implementations."""

    def test_fuzzy_match_short_keywords_filtered(self, catalog):
        """Single-character keywords should be filtered out (min 2 chars)."""
        from rag.recommendation.session_state import fuzzy_match_cart_item
        cart_ids = ["p_digital_001"]
        # "O" is only 1 char, should not match
        result = fuzzy_match_cart_item("O", cart_ids, catalog)
        assert result is None

    def test_fuzzy_match_empty_instruction(self, catalog):
        from rag.recommendation.session_state import fuzzy_match_cart_item
        result = fuzzy_match_cart_item("", ["p_digital_001"], catalog)
        assert result is None

    def test_cart_item_list_format(self, session_with_cart, catalog):
        from rag.recommendation.tool_handlers import _cart_item_list
        items = _cart_item_list(session_with_cart, catalog)
        assert len(items) == 3
        for item in items:
            assert "index" in item
            assert "product_id" in item
            assert "title" in item
            assert "quantity" in item
        assert items[0]["index"] == 1

    def test_make_plan_stores_operation(self):
        from rag.recommendation.tool_handlers import _make_plan
        plan = _make_plan("p_digital_001", "OPPO Reno12", "remove", 1, 2999, 2999)
        assert plan["operation"] == "remove"
        assert plan["product_id"] == "p_digital_001"
        assert "created_at" in plan
        assert "expires_at" in plan
