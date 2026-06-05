"""Check whether the configured Milvus product chunk index is usable."""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rag.ingestion.embedding import create_embedding_provider, get_configured_embedding_dim
from rag.storage.milvus_client import MilvusManager


SMOKE_QUERIES = [
    {
        "query": "B760 DDR5 主板",
        "must_any": ["pc_motherboard", "主板", "motherboard", "B760", "DDR5"],
        "label": "主板",
    },
    {
        "query": "750W 金牌电源",
        "must_any": ["pc_psu", "电源", "psu", "750W", "gold", "金牌"],
        "label": "电源",
    },
    {
        "query": "2TB NVMe SSD",
        "must_any": ["pc_storage", "SSD", "ssd", "NVMe", "2TB"],
        "label": "SSD",
    },
    {
        "query": "篮球实战鞋 缓震",
        "must_any": ["篮球鞋", "篮球", "缓震"],
        "label": "篮球鞋",
    },
    {
        "query": "油皮 夏天 防晒",
        "must_any": ["防晒", "油皮", "控油"],
        "label": "防晒",
    },
    {
        "query": "推荐户外防风外套",
        "must_not_strong": ["户外裤", "背包", "帽子"],
        "label": "缺失外套负例",
        "kind": "catalog_gap_warning",
    },
]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", type=Path, default=ROOT_DIR / "reports" / "vector_index_health.json")
    parser.add_argument("--expected-count", type=int, default=884)
    parser.add_argument("--count-tolerance", type=int, default=160)
    parser.add_argument("--connect-timeout", type=float, default=2.0)
    args = parser.parse_args(argv)

    load_dotenv(ROOT_DIR / ".env")
    started = time.perf_counter()
    report: Dict[str, Any] = {
        "status": "unknown",
        "errors": [],
        "warnings": [],
        "config": safe_config(),
        "collection": {},
        "smoke_tests": [],
    }

    manager = MilvusManager()
    report["collection"]["name"] = manager.collection_name
    report["config"]["milvus_uri"] = os.getenv("MILVUS_URI") or manager.uri

    try:
        assert_port_open(manager.host, manager.port, timeout=args.connect_timeout)
        client = manager._get_client()
        has_collection = bool(client.has_collection(manager.collection_name))
        report["collection"]["exists"] = has_collection
        if not has_collection:
            fail(report, f"Milvus collection 不存在: {manager.collection_name}")
            return finish(report, args.output, started)

        description = describe_collection(client, manager.collection_name)
        dense_dim = dense_vector_dim(description)
        entity_count = collection_entity_count(client, manager.collection_name)
        indexes = describe_indexes(client, manager.collection_name)
        report["collection"].update(
            {
                "entity_count": entity_count,
                "dense_vector_dim": dense_dim,
                "indexes": indexes,
                "description": compact_description(description),
            }
        )

        expected_dim = get_configured_embedding_dim()
        if dense_dim and dense_dim != expected_dim:
            fail(report, f"embedding dim 不匹配: collection={dense_dim}, config={expected_dim}")
        if entity_count <= 0:
            fail(report, "entity count 为 0")
        elif abs(entity_count - args.expected_count) > args.count_tolerance:
            fail(report, f"entity count 异常: actual={entity_count}, expected≈{args.expected_count}")

        provider = create_embedding_provider()
        report["config"].update(
            {
                "embedding_provider": provider.provider,
                "embedding_model": provider.model,
                "embedding_dim": provider.dim,
            }
        )
        if provider.dim != expected_dim:
            fail(report, f"provider dim 与配置不一致: provider={provider.dim}, config={expected_dim}")

        for spec in SMOKE_QUERIES:
            smoke = run_smoke_query(client, manager.collection_name, provider, spec, args.top_k)
            report["smoke_tests"].append(smoke)
            if smoke.get("status") == "warning":
                warn(report, f"catalog gap warning: {spec['query']} - {smoke.get('warning_reason')}")
            elif not smoke["passed"]:
                fail(report, f"检索 smoke test 未通过: {spec['query']} - {smoke.get('failed_reason')}")

    except Exception as exc:
        fail(report, sanitize_error(exc))

    return finish(report, args.output, started)


def run_smoke_query(client: Any, collection: str, provider: Any, spec: Dict[str, Any], top_k: int) -> Dict[str, Any]:
    query = spec["query"]
    vector = provider.embed_query(query)
    raw_hits = client.search(
        collection_name=collection,
        data=[vector],
        anns_field="dense_embedding",
        search_params={"metric_type": "IP", "params": {"ef": 64}},
        limit=top_k,
        output_fields=[
            "text",
            "product_id",
            "title",
            "category",
            "category_key",
            "component_type",
            "chunk_type",
            "doc_type",
            "source_scope",
            "metadata",
        ],
    )
    hits = [compact_hit(hit) for bucket in raw_hits for hit in bucket]
    text = json.dumps(hits[:3], ensure_ascii=False).lower()
    passed = bool(hits)
    reason = ""
    warning_reason = ""
    status = "ok" if passed else "failed"
    if passed and spec.get("must_any"):
        expected = [str(item).lower() for item in spec["must_any"]]
        passed = any(item.lower() in text for item in expected)
        reason = "" if passed else "top-k 未命中预期品类/关键词"
        status = "ok" if passed else "failed"
    if passed and spec.get("must_not_strong"):
        bad = [str(item).lower() for item in spec["must_not_strong"]]
        top1 = json.dumps(hits[:1], ensure_ascii=False).lower()
        matched_neighbor = any(item in top1 for item in bad)
        if matched_neighbor and spec.get("kind") == "catalog_gap_warning":
            warning_reason = "纯向量检索在缺失外套时返回户外相邻商品；端到端 no-match guard 负责最终拦截。"
            status = "warning"
            passed = True
        else:
            passed = not matched_neighbor
            reason = "" if passed else "负例外套查询被户外裤/背包/帽子强相关占据"
            status = "ok" if passed else "failed"
    if not hits:
        reason = "无检索结果"
        if spec.get("kind") == "catalog_gap_warning":
            passed = True
            reason = ""
            status = "ok"
    return {
        "query": query,
        "expected_label": spec.get("label"),
        "kind": spec.get("kind", "positive_smoke"),
        "status": status,
        "passed": passed,
        "failed_reason": reason,
        "warning_reason": warning_reason,
        "top_k": hits,
    }


def compact_hit(hit: Any) -> Dict[str, Any]:
    entity = hit.get("entity", {}) if isinstance(hit, dict) else {}
    metadata = entity.get("metadata") if isinstance(entity.get("metadata"), dict) else {}
    return {
        "product_id": value(hit, entity, metadata, "product_id"),
        "title": value(hit, entity, metadata, "title"),
        "category": value(hit, entity, metadata, "category"),
        "category_key": value(hit, entity, metadata, "category_key"),
        "component_type": value(hit, entity, metadata, "component_type"),
        "chunk_type": value(hit, entity, metadata, "chunk_type") or value(hit, entity, metadata, "doc_type"),
        "source_scope": value(hit, entity, metadata, "source_scope"),
        "score": float(hit.get("distance", 0.0)) if isinstance(hit, dict) else 0.0,
        "snippet": str(value(hit, entity, metadata, "text") or "")[:220],
    }


def value(hit: Dict[str, Any], entity: Dict[str, Any], metadata: Dict[str, Any], key: str) -> Any:
    return entity.get(key) or metadata.get(key) or hit.get(key) or ""


def assert_port_open(host: str, port: str, *, timeout: float) -> None:
    with socket.create_connection((host, int(port)), timeout=timeout):
        return


def collection_entity_count(client: Any, collection: str) -> int:
    for method_name in ("get_collection_stats", "num_entities"):
        method = getattr(client, method_name, None)
        if not callable(method):
            continue
        try:
            stats = method(collection)
        except TypeError:
            stats = method(collection_name=collection)
        if isinstance(stats, dict):
            for key in ("row_count", "num_entities"):
                if key in stats:
                    return int(stats[key])
    rows = client.query(collection_name=collection, filter="", output_fields=["product_id"], limit=1)
    return len(rows)


def describe_collection(client: Any, collection: str) -> Any:
    return client.describe_collection(collection_name=collection)


def describe_indexes(client: Any, collection: str) -> List[Dict[str, Any]]:
    try:
        indexes = client.list_indexes(collection_name=collection)
    except Exception:
        return []
    out = []
    for name in indexes or []:
        try:
            out.append(client.describe_index(collection_name=collection, index_name=name))
        except Exception:
            out.append({"index_name": name})
    return out


def dense_vector_dim(description: Any) -> int | None:
    fields = []
    if isinstance(description, dict):
        schema = description.get("schema") or {}
        fields = description.get("fields") or schema.get("fields") or []
    for field in fields:
        if field.get("name") != "dense_embedding":
            continue
        params = field.get("params") or {}
        dim = params.get("dim") or params.get("dimension") or field.get("dim")
        return int(dim) if dim else None
    return None


def compact_description(description: Any) -> Dict[str, Any]:
    if not isinstance(description, dict):
        return {}
    fields = []
    for field in (description.get("fields") or (description.get("schema") or {}).get("fields") or []):
        fields.append({"name": field.get("name"), "type": str(field.get("type")), "params": field.get("params") or {}})
    return {"fields": fields}


def safe_config() -> Dict[str, Any]:
    return {
        "milvus_host": os.getenv("MILVUS_HOST", "localhost"),
        "milvus_port": os.getenv("MILVUS_PORT", "19530"),
        "milvus_collection": os.getenv("MILVUS_COLLECTION", "embeddings_collection"),
        "embedding_provider": os.getenv("EMBEDDING_PROVIDER", "local"),
        "embedding_model": os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"),
        "embedding_dim": os.getenv("EMBEDDING_DIM") or os.getenv("DENSE_EMBEDDING_DIM", "1024"),
    }


def sanitize_error(exc: Exception) -> str:
    text = str(exc)
    for name in ("DASHSCOPE_API_KEY", "EMBEDDING_API_KEY", "OPENAI_API_KEY"):
        secret = os.getenv(name)
        if secret:
            text = text.replace(secret, "***")
    return text or exc.__class__.__name__


def fail(report: Dict[str, Any], reason: str) -> None:
    report.setdefault("errors", []).append(reason)


def warn(report: Dict[str, Any], reason: str) -> None:
    report.setdefault("warnings", []).append(reason)


def finish(report: Dict[str, Any], output: Path, started: float) -> int:
    report["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    positive_tests = [item for item in report.get("smoke_tests") or [] if item.get("kind") != "catalog_gap_warning"]
    catalog_gap_tests = [item for item in report.get("smoke_tests") or [] if item.get("kind") == "catalog_gap_warning"]
    positive_failed = any(not item.get("passed") for item in positive_tests)
    catalog_gap_warning = any(item.get("status") == "warning" for item in catalog_gap_tests)
    infra_failed = any(not str(reason).startswith("检索 smoke test 未通过") for reason in report.get("errors") or [])
    report["infrastructure_status"] = "failed" if infra_failed else "ok"
    report["positive_smoke_status"] = "failed" if positive_failed else "ok"
    report["catalog_gap_warning_status"] = "warning" if catalog_gap_warning else "ok"
    if report.get("errors"):
        overall_status = "failed"
    elif report.get("warnings"):
        overall_status = "passed_with_warning"
    else:
        overall_status = "ok"
    report["overall_status"] = overall_status
    report["status"] = overall_status
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Milvus 向量库健康检查: {report['overall_status']}")
    print(f"infrastructure_status: {report['infrastructure_status']}")
    print(f"positive_smoke_status: {report['positive_smoke_status']}")
    print(f"catalog_gap_warning_status: {report['catalog_gap_warning_status']}")
    print(f"collection: {report.get('collection', {}).get('name')}")
    print(f"entity_count: {report.get('collection', {}).get('entity_count')}")
    print(f"dense_vector_dim: {report.get('collection', {}).get('dense_vector_dim')}")
    for item in report.get("smoke_tests") or []:
        mark = "WARNING" if item.get("status") == "warning" else ("OK" if item.get("passed") else "FAIL")
        print(f"- {mark} {item.get('query')} top_hits={len(item.get('top_k') or [])}")
    if report.get("warnings"):
        print("warning:")
        for reason in report["warnings"]:
            print(f"- {reason}")
    if report.get("errors"):
        print("失败原因:")
        for reason in report["errors"]:
            print(f"- {reason}")
    print(f"JSON 报告: {output}")
    return 1 if report["overall_status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
