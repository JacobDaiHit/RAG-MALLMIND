from typing import Any, List


def dedupe_strings(items: List[str]) -> List[str]:
    """Return strings in their original order without duplicates."""

    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def clean_compact_text(value: Any, limit: int) -> str:
    """Convert any value to a single-line string and cap it for API responses."""

    text = " ".join(str(value or "").replace("\x00", " ").split())
    if limit <= 0:
        return text
    return text[:limit]


def normalize_lookup_text(value: Any) -> str:
    """Normalize names for fuzzy contains matching across Chinese and English text."""

    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
