"""Failure tests for V3 invariants that normal end-to-end traffic cannot prove."""
from __future__ import annotations

from dataclasses import replace

import pytest

from rag.recommendation.product_loader import load_combined_product_catalog
from rag.recommendation.session_state import RedisSessionStore, ShoppingSession
from rag.recommendation.v3.fact_query_executor import execute_certified_fact_query
from rag.recommendation.v3.retrieval import V3EvidenceRetriever
from rag.recommendation.v3.session import apply_session_delta, empty_session_core
from rag.recommendation.v3.types import CardModel, RequirementSpecV3, SessionDelta, V3Action


def _events(stream):
    return list(stream)


def test_milvus_failure_never_returns_out_of_allowlist_evidence():
    class Embeddings:
        def get_all_embeddings(self, texts):
            return [[0.1, 0.2]], [{1: 1.0}]

    class FailingMilvus:
        def has_collection(self):
            return True

        def hybrid_retrieve(self, *_args, **_kwargs):
            raise ConnectionError("simulated Milvus outage")

    evidence = V3EvidenceRetriever(manager=FailingMilvus(), embedding_service=Embeddings()).retrieve(
        query="推荐手机", filters=type("Filters", (), {"product_ids": ("p_allowed",)})()
    )
    assert evidence.status == "unavailable"
    assert evidence.ranked_product_ids == ()
    assert "p_allowed" in evidence.filter_expression


def test_expired_card_cannot_be_used_for_catalog_fact_query():
    catalog = load_combined_product_catalog()
    product = catalog.products[0]
    session = ShoppingSession("expired-card")
    expired_core = replace(
        empty_session_core(),
        cards=(CardModel("card_expired", str(product.product_id), (), str(product.title), 1, 0.0),),
    )
    apply_session_delta(session, SessionDelta(expired_core, "final_test_expired_card"))
    output = "".join(_events(execute_certified_fact_query(
        session=session,
        requirement=RequirementSpecV3(action=V3Action.PARAMETER_QUERY, target_card_id="card_expired", query_kind="price"),
        catalog=catalog,
    )))
    assert "expired_or_unknown_card" in output
    assert "product_fact" not in output


def test_redis_network_error_is_visible_not_silently_replaced_with_empty_state():
    class FailingRedis:
        def get(self, _key):
            raise ConnectionError("simulated Redis outage")

        def delete(self, _key):
            return 1

    store = RedisSessionStore.__new__(RedisSessionStore)
    store._client = FailingRedis()
    store._ttl_seconds = 60
    with pytest.raises(ConnectionError):
        store.get("final-test")
