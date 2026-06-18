"""Rebuild the optional Milvus product vector evidence index."""
from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path
from typing import Optional, Sequence


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rag.ingestion.embedding import get_embedding_service
from rag.ingestion.product_chunks import build_all_catalog_chunks
from rag.storage.milvus_client import MilvusManager
from rag.storage.milvus_writer import MilvusWriter


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("local", "dashscope", "openai_compatible"))
    parser.add_argument("--model")
    parser.add_argument("--dim", type=int)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--recreate", action="store_true", help="Explicitly drop and recreate the Milvus collection.")
    args = parser.parse_args(argv)

    if args.provider:
        os.environ["EMBEDDING_PROVIDER"] = args.provider
    if args.model:
        os.environ["EMBEDDING_MODEL"] = args.model
    if args.dim:
        os.environ["EMBEDDING_DIM"] = str(args.dim)
        os.environ["DENSE_EMBEDDING_DIM"] = str(args.dim)

    chunks = build_all_catalog_chunks()
    if args.limit is not None:
        chunks = chunks[: max(0, args.limit)]
    chunk_stats = _chunk_stats(chunks)

    embedding_service = get_embedding_service()
    batch_size = max(1, args.batch_size or int(os.getenv("EMBEDDING_BATCH_SIZE", "10")))

    print(f"chunks={len(chunks)}", flush=True)
    print(f"ecommerce_chunks={chunk_stats['ecommerce_chunks']}", flush=True)
    print(f"pc_chunks={chunk_stats['pc_chunks']}", flush=True)
    print(f"image_like_text_chunks={chunk_stats['image_like_text_chunks']}", flush=True)
    print(f"provider={embedding_service.provider_name}", flush=True)
    print(f"model={embedding_service.model}", flush=True)
    print(f"dim={embedding_service.dim}", flush=True)
    print(f"batch_size={batch_size}", flush=True)
    if args.dry_run:
        print("status=dry_run", flush=True)
        return 0

    milvus_ok, milvus_error = _milvus_port_available()
    if not milvus_ok:
        print("status=failed", flush=True)
        print(f"error=Milvus is not reachable: {milvus_error}", flush=True)
        return 1

    try:
        manager = MilvusManager()
        if manager.has_collection() and not args.recreate:
            print("status=failed", flush=True)
            print(
                "error=Collection already exists; rerun with --recreate to avoid duplicate chunks and BM25 drift.",
                flush=True,
            )
            return 1
        MilvusWriter(embedding_service=embedding_service, milvus_manager=manager).write_documents(
            chunks,
            batch_size=batch_size,
            reset_bm25=args.recreate,
            drop_collection=args.recreate,
        )
    except Exception as exc:
        print(f"status=failed", flush=True)
        print(f"error={exc}", flush=True)
        return 1

    print("status=ok", flush=True)
    return 0


def _milvus_port_available(timeout_seconds: float = 2.0) -> tuple[bool, str]:
    host = os.getenv("MILVUS_HOST", "localhost")
    port = int(os.getenv("MILVUS_PORT", "19530"))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_seconds)
    try:
        sock.connect((host, port))
        return True, ""
    except Exception as exc:
        return False, f"{host}:{port} ({exc})"
    finally:
        sock.close()


def _chunk_stats(chunks: list[dict]) -> dict[str, int]:
    image_like = 0
    for chunk in chunks:
        text = str(chunk.get("text") or "").lower()
        if "images/" in text or "images\\" in text or text.endswith((".jpg", ".jpeg", ".png", ".svg")):
            image_like += 1
    return {
        "ecommerce_chunks": sum(1 for chunk in chunks if chunk.get("file_type") == "ecommerce_product"),
        "pc_chunks": sum(1 for chunk in chunks if chunk.get("file_type") == "jd_pc_product"),
        "image_like_text_chunks": image_like,
    }


if __name__ == "__main__":
    raise SystemExit(main())
