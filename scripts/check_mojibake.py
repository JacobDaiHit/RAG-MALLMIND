from pathlib import Path


ROOT = Path(".")
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "dist",
    "build",
    "chroma_db",
    "chroma_db_nvidia",
}
EXTS = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".tsx",
    ".vue",
    ".editorconfig",
    ".ps1",
}
SUSPICIOUS_CODEPOINTS = [
    0x93B4,
    0x5BB8,
    0x7EEF,
    0x935F,
    0x9428,
    0x951B,
    0x9286,
    0x6D63,
    0x72B2,
    0x5997,
    0x9A9E,
    0x20AC,
    0xFFFD,
]
SUSPICIOUS = [chr(codepoint) for codepoint in SUSPICIOUS_CODEPOINTS]


def should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def has_private_use(text: str) -> bool:
    return any("\ue000" <= char <= "\uf8ff" for char in text)


def main() -> int:
    suspicious_files = 0
    for path in ROOT.rglob("*"):
        if not path.is_file() or should_skip(path) or path.suffix.lower() not in EXTS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            print(f"{path}: DECODE_ERROR: {exc}")
            suspicious_files += 1
            continue

        hits = []
        for line_no, line in enumerate(text.splitlines(), 1):
            if any(token in line for token in SUSPICIOUS) or has_private_use(line):
                hits.append((line_no, line[:240]))
        if hits:
            suspicious_files += 1
            print(f"\n{path}")
            for line_no, line in hits[:20]:
                print(f"  line {line_no}: {line}")
            if len(hits) > 20:
                print(f"  ... {len(hits) - 20} more hits")

    print(f"\nSuspicious files: {suspicious_files}")
    return 1 if suspicious_files else 0


if __name__ == "__main__":
    raise SystemExit(main())
