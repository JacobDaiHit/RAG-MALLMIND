"""Deprecated no-op for the removed PC product image backfill flow."""
from __future__ import annotations


def main() -> int:
    print("PC parts do not use product images in this demo. No image backfill is performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
