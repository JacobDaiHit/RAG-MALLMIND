"""Build optional Milvus evidence index from the product catalogs."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rag.ingestion.product_chunks import build_all_catalog_chunks
from rag.ingestion.embedding import EmbeddingService, embedding_service
from rag.storage.milvus_client import MilvusManager
from rag.storage.milvus_writer import MilvusWriter


def resolve_index_target(*, v3: bool) -> tuple[str, Path | None]:
    """Return the collection and BM25 state owned by one indexing target.

    Sparse vector coordinates are defined by the BM25 vocabulary.  Therefore a
    V3 rebuild must never reset the state used by the legacy collection.
    """

    if not v3:
        return os.getenv("MILVUS_COLLECTION", "embeddings_collection"), None
    collection = os.getenv("MILVUS_V3_COLLECTION", "mallmind_product_evidence_v3")
    state_value = os.getenv("MILVUS_V3_BM25_STATE_PATH", "data/bm25_state_v3.json")
    state_path = Path(state_value)
    if not state_path.is_absolute():
        state_path = ROOT_DIR / state_path
    return collection, state_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Index ecommerce and JD PC product chunks into Milvus.")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--rebuild", action="store_true", help="Drop the old Milvus collection and reset BM25 before indexing.")
    parser.add_argument("--dry-run", action="store_true", help="Build chunks without writing to Milvus.")
    parser.add_argument("--v3", action="store_true", help="Write the V3 catalog-fact collection with canonical filter fields.")
    args = parser.parse_args()

    chunks = build_all_catalog_chunks()
    collection, v3_bm25_state_path = resolve_index_target(v3=args.v3)
    if args.dry_run:
        print(json.dumps({"collection": collection, "chunk_count": len(chunks), "v3": args.v3, "sample": chunks[0] if chunks else {}}, ensure_ascii=False))
        return

    manager = MilvusManager(collection_name=collection)
    if manager.has_collection() and not args.rebuild:
        raise SystemExit(
            "Refusing to append a full catalog into an existing auto-id collection. "
            "Rerun with --rebuild to avoid duplicate chunks and BM25 drift."
        )
    service = EmbeddingService(state_path=v3_bm25_state_path) if v3_bm25_state_path else embedding_service
    MilvusWriter(embedding_service=service, milvus_manager=manager).write_documents(
        chunks,
        batch_size=max(1, args.batch_size),
        reset_bm25=args.rebuild,
        drop_collection=args.rebuild,
    )
    print(f"Indexed {len(chunks)} product chunks into Milvus.")


if __name__ == "__main__":
    main()
