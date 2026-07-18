"""Build local A/B/C catalog-type candidates for the one SemanticParse call.

``build_type_candidate_set`` unions every exact type mention (A), full-query
lexical matches (B), and buying-action-window matches (C). It searches catalog
*types*, never products, and intentionally makes no external embedding/Chat
call; product retrieval starts only after TypeResolutionGate confirms a type.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from typing import Any, Iterable

from .config import (
    TYPE_ACTION_ANCHORS,
    TYPE_CANDIDATE_ACTION_WINDOW_LIMIT,
    TYPE_CANDIDATE_FULL_QUERY_LIMIT,
    TYPE_CANDIDATE_PROMPT_MAX_CHARS,
    TYPE_CANDIDATE_RETRIEVAL_VERSION,
)
from .registry import CatalogNormalizationRegistry
from .types import TaxonomyCandidate, TaxonomyCandidateSet, TypeDocument


def build_type_candidate_set(*, text: str, registry: CatalogNormalizationRegistry, catalog: Any) -> TaxonomyCandidateSet:
    """Union all exact mentions with independent full-query and anchor searches.

    A: every exact catalog type mention in the original text, with no rank
    cutoff.  B: lexical retrieval over the whole text.  C: lexical retrieval
    over short spans around a buying-action anchor.  The sources remain on each
    candidate for traceability; the LLM only receives a compact rendered menu.
    """

    documents = _build_documents(registry=registry, catalog=catalog)
    by_id = {document.canonical_type_id: document for document in documents}
    accumulated: dict[str, TaxonomyCandidate] = {}
    for entity_id in _explicit_type_ids(text=text, registry=registry):
        document = by_id.get(entity_id)
        if document is not None:
            accumulated[entity_id] = TaxonomyCandidate(entity_id, document.display_name, 1_000_000.0, ("A_exact",))
    explicit_count = len(accumulated)
    _merge_ranked(accumulated, _rank_documents(text, documents, TYPE_CANDIDATE_FULL_QUERY_LIMIT), "B_full_query")
    for segment in _action_windows(text):
        _merge_ranked(accumulated, _rank_documents(segment, documents, TYPE_CANDIDATE_ACTION_WINDOW_LIMIT), "C_action_window")
    candidates = tuple(
        sorted(accumulated.values(), key=lambda item: ("A_exact" not in item.sources, -item.score, item.canonical_type_id))
    )
    rendered_size = len(render_type_candidates(TaxonomyCandidateSet(
        TYPE_CANDIDATE_RETRIEVAL_VERSION, registry.version, candidates, explicit_count
    )))
    return TaxonomyCandidateSet(
        retrieval_version=TYPE_CANDIDATE_RETRIEVAL_VERSION,
        registry_version=registry.version,
        candidates=candidates,
        explicit_type_count=explicit_count,
        prompt_overflow=rendered_size > TYPE_CANDIDATE_PROMPT_MAX_CHARS,
    )


def render_type_candidates(candidate_set: TaxonomyCandidateSet) -> str:
    """Render no product facts; return an overflow sentinel rather than truncate A."""

    if candidate_set.prompt_overflow:
        return "类型候选过多，不能安全展示菜单：target_type_candidate_id 必须为 null，只抄写目标原词和证据。"
    if not candidate_set.candidates:
        return "类型候选：无。target_type_candidate_id 必须为 null。"
    rows = ["类型候选（推荐时只能选一个 ID；排除项也只能从此处选择）："]
    for candidate in candidate_set.candidates:
        rows.append(f"- {candidate.canonical_type_id} | {candidate.display_name}")
    return "\n".join(rows)


def _build_documents(*, registry: CatalogNormalizationRegistry, catalog: Any) -> tuple[TypeDocument, ...]:
    values: dict[str, list[str]] = defaultdict(list)
    for product in catalog.products:
        values_by_entity = [
            entity.canonical_id
            for entity in registry.product_types.values()
            if str(product.sub_category) in entity.catalog_values
            or (
                entity.canonical_id.startswith("pc_category:")
                and str(product.category.value) == entity.canonical_id[len("pc_category:"):]
            )
        ]
        product_text = " ".join(
            [str(product.title), str(product.sub_category), *product.tags, *product.best_for, *product.supported_scenarios]
        )
        for entity_id in values_by_entity:
            values[entity_id].append(product_text)
    documents = []
    for entity in registry.product_types.values():
        profile_terms = [entity.display_name, *entity.aliases, *values.get(entity.canonical_id, ())]
        profile = _unique_terms(profile_terms, limit=48)
        documents.append(
            TypeDocument(
                canonical_type_id=entity.canonical_id,
                display_name=entity.display_name,
                parent_category=_parent_category(entity.canonical_id),
                profile_text=" ".join(profile),
            )
        )
    return tuple(sorted(documents, key=lambda item: item.canonical_type_id))


def _explicit_type_ids(*, text: str, registry: CatalogNormalizationRegistry) -> tuple[str, ...]:
    normalized = text.casefold()
    matched = {
        entity.canonical_id
        for alias, entity in registry.product_type_aliases()
        if alias and alias.casefold() in normalized
    }
    return tuple(sorted(matched))


def _rank_documents(query: str, documents: Iterable[TypeDocument], limit: int) -> tuple[tuple[TypeDocument, float], ...]:
    query_grams = _grams(query)
    if not query_grams:
        return ()
    ranked: list[tuple[TypeDocument, float]] = []
    for document in documents:
        profile_grams = _grams(document.profile_text)
        overlap = sum((query_grams & profile_grams).values())
        if overlap <= 0:
            continue
        # Distinct overlap suppresses repeated negative mentions; the small
        # exact-name bonus keeps real type words above incidental title overlap.
        score = overlap / max(1, sum(query_grams.values()))
        name_key = _compact(document.display_name)
        if name_key and name_key in _compact(query):
            score += 4.0
        ranked.append((document, score))
    ranked.sort(key=lambda item: (-item[1], item[0].canonical_type_id))
    return tuple(ranked[:limit])


def _merge_ranked(target: dict[str, TaxonomyCandidate], ranked: Iterable[tuple[TypeDocument, float]], source: str) -> None:
    for document, score in ranked:
        existing = target.get(document.canonical_type_id)
        if existing is None:
            target[document.canonical_type_id] = TaxonomyCandidate(document.canonical_type_id, document.display_name, score, (source,))
            continue
        target[document.canonical_type_id] = replace(
            existing,
            score=max(existing.score, score),
            sources=tuple(sorted(set((*existing.sources, source)))),
        )


def _action_windows(text: str) -> tuple[str, ...]:
    windows = []
    for anchor in TYPE_ACTION_ANCHORS:
        start = text.find(anchor)
        while start >= 0:
            windows.append(text[max(0, start - 10):min(len(text), start + len(anchor) + 36)])
            start = text.find(anchor, start + len(anchor))
    return tuple(dict.fromkeys(window for window in windows if window.strip()))


def _grams(value: str) -> Counter[str]:
    chars = [char.casefold() for char in value if char.isalnum() or "\u4e00" <= char <= "\u9fff"]
    if not chars:
        return Counter()
    # Single-character overlap is too noisy for Chinese catalog text (e.g.
    # “荐” would pull unrelated products).  Use character bigrams; exact
    # mentions are independently protected by A and therefore need no unigram.
    return Counter(["".join(chars[index:index + 2]) for index in range(len(chars) - 1)] or chars)


def _compact(value: str) -> str:
    return "".join(char.casefold() for char in value if not char.isspace())


def _unique_terms(values: Iterable[str], *, limit: int) -> tuple[str, ...]:
    result = []
    for value in values:
        cleaned = " ".join(str(value).split())
        if cleaned and cleaned not in result:
            result.append(cleaned[:160])
        if len(result) >= limit:
            break
    return tuple(result)


def _parent_category(canonical_type_id: str) -> str:
    if canonical_type_id.startswith("pc_category:"):
        return "pc"
    if canonical_type_id in {"phone", "tablet", "earbuds"}:
        return "digital"
    return "catalog"
