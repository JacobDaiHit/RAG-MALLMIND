"""Smoke-check the configured dense embedding provider."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, Sequence


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rag.ingestion.embedding import create_embedding_provider


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("local", "dashscope", "openai_compatible"), default=None)
    parser.add_argument("--model")
    parser.add_argument("--dim", type=int)
    parser.add_argument("--text", default="test vector")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.provider:
        os.environ["EMBEDDING_PROVIDER"] = args.provider
    if args.model:
        os.environ["EMBEDDING_MODEL"] = args.model
    if args.dim:
        os.environ["EMBEDDING_DIM"] = str(args.dim)

    provider = create_embedding_provider()
    expected_dim = int(provider.dim)
    payload = {
        "provider": provider.provider,
        "model": provider.model,
        "expected_dim": expected_dim,
        "actual_dim": None,
        "batch_size": getattr(provider, "batch_size", None),
        "status": "dry_run" if args.dry_run else "pending",
    }
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    try:
        vector = provider.embed_query(args.text)
    except Exception as exc:
        payload.update(
            {
                "status": "failed",
                "error": str(exc),
            }
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1
    payload.update(
        {
            "actual_dim": len(vector),
            "vector_length": len(vector),
            "vector_preview": [round(value, 6) for value in vector[:5]],
            "status": "ok",
        }
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
