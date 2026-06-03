from rag.recommendation.retrieval import EvidenceRetriever
from rag.recommendation.recommendation_pipeline import parse_requirement_rule_based
from rag.schemas import ComponentCategory


class UnreachableManager:
    host = "127.0.0.1"
    port = "1"


def test_retrieval_unavailable_degrades_without_exception(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    requirement = parse_requirement_rule_based("推荐一款手机")

    evidence = EvidenceRetriever(manager=UnreachableManager()).retrieve(requirement, [ComponentCategory.digital])
    trace = evidence.to_trace()

    assert evidence.status == "unavailable"
    assert trace["status"] == "unavailable"
    assert trace["error"] == "milvus_unavailable"
