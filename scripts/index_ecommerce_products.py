"""Build optional Milvus evidence index from the product catalogs."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rag.ingestion.product_chunks import build_all_catalog_chunks
from rag.ingestion.embedding import embedding_service
from rag.storage.milvus_client import MilvusManager
from rag.storage.milvus_writer import MilvusWriter


def main() -> None:
    parser = argparse.ArgumentParser(description="Index ecommerce and JD PC product chunks into Milvus.")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--rebuild", action="store_true", help="Drop the old Milvus collection and reset BM25 before indexing.")
    parser.add_argument("--dry-run", action="store_true", help="Build chunks without writing to Milvus.")
    args = parser.parse_args()

    chunks = build_all_catalog_chunks()
    if args.dry_run:
        print(f"Built {len(chunks)} product chunks.")
        return

    manager = MilvusManager()
    if manager.has_collection() and not args.rebuild:
        raise SystemExit(
            "Refusing to append a full catalog into an existing auto-id collection. "
            "Rerun with --rebuild to avoid duplicate chunks and BM25 drift."
        )
    MilvusWriter(embedding_service=embedding_service, milvus_manager=manager).write_documents(
        chunks,
        batch_size=max(1, args.batch_size),
        reset_bm25=args.rebuild,
        drop_collection=args.rebuild,
    )
    print(f"Indexed {len(chunks)} product chunks into Milvus.")


if __name__ == "__main__":
    main()
