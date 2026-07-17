"""Small fail-closed grammar for the first V3 deterministic slice."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

from .config import ATTRIBUTE_PREFERENCES, BRAND_RELEASE_PREFIXES, BRAND_RELEASE_SUFFIXES, CARD_FACT_QUERY_KINDS, EXCLUDE_OPERATORS, GRAMMAR_VERSION, RECOMMEND_POLITE_SUFFIXES, RECOMMEND_QUANTIFIERS, RECOMMEND_VERBS, SEMANTIC_RISK_MARKERS
from .registry import CatalogNormalizationRegistry
from .types import ParseTree, Span, V3Action

_PRICE_MAX = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?:元|块)?\s*(?:以内|以下|不超过|最多)")
_SPACE_OR_PUNCTUATION = re.compile(r"(?:\s|，|,|、)+")
_CARD_FACT_QUERY = re.compile(
    r"^第(?P<rank>[一二三四五六七八九十]|\d+)(?:个|款|台)(?:商品|手机|平板|耳机)?的?(?P<kind>参数|配置|SKU|sku|价格)(?:是什么|怎么样|有哪些)?[？?]?$"
)
_CHINESE_RANKS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


@dataclass(frozen=True)
class GrammarParseResult:
    trees: Tuple[ParseTree, ...]
    consumed_spans: Tuple[Span, ...]
    unresolved_spans: Tuple[Span, ...]
    reason_code: str = ""


class GrammarParser:
    """Enumerates all V1 grammar parses; never chooses an arbitrary winner."""

    def __init__(self, registry: CatalogNormalizationRegistry) -> None:
        self._registry = registry

    def parse_all(self, text: str, *, cards=(), prior_requirement=None) -> GrammarParseResult:
        if not text:
            return GrammarParseResult((), (), (), "empty_text")
        if any(marker in text for marker in SEMANTIC_RISK_MARKERS):
            return GrammarParseResult((), (), (Span(0, len(text), text, "semantic_risk_marker"),), "semantic_risk_marker")
        card_tree = self._parse_card_fact_query(text, cards)
        if card_tree is not None:
            span = Span(0, len(text), text, "card_fact_query.v1")
            return GrammarParseResult((card_tree,), (span,), (), "")
        release_tree = self._parse_brand_release(text, prior_requirement)
        if release_tree is not None:
            span = Span(0, len(text), text, "brand_release.v1")
            return GrammarParseResult((release_tree,), (span,), (), "")
        tree, spans, residual = self._parse_recommend(text)
        if tree is None:
            unresolved = (Span(0, len(text), text, "unsupported_grammar"),)
            return GrammarParseResult((), (), unresolved, "unsupported_grammar")
        if residual:
            return GrammarParseResult((), tuple(spans), (Span(residual[0], residual[1], text[residual[0]:residual[1]], "unresolved"),), "unresolved_text")
        return GrammarParseResult((tree,), tuple(spans), (), "")

    def _parse_recommend(self, text: str) -> tuple[ParseTree | None, List[Span], tuple[int, int] | None]:
        cursor = 0
        spans: List[Span] = []
        verb = next((item for item in RECOMMEND_VERBS if text.startswith(item)), None)
        if verb is None:
            return None, spans, None
        spans.append(Span(0, len(verb), verb, "recommend_verb.v1"))
        cursor = len(verb)
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        quantifier = next((item for item in RECOMMEND_QUANTIFIERS if text.startswith(item, cursor)), None)
        if quantifier is not None:
            spans.append(Span(cursor, cursor + len(quantifier), quantifier, "recommend_quantifier.v1"))
            cursor += len(quantifier)

        category_match = self._longest_prefix_entity(text[cursor:], self._registry.product_type_aliases())
        if category_match is None:
            return None, spans, None
        category_alias, category = category_match
        end = cursor + len(category_alias)
        spans.append(Span(cursor, end, category_alias, "taxonomy_alias.v1"))
        cursor = end

        price_max = None
        excluded_brands: List[str] = []
        attributes: List[str] = []
        scopes: List[tuple[str, str]] = []
        while cursor < len(text):
            connector = _SPACE_OR_PUNCTUATION.match(text, cursor)
            if connector is not None:
                cursor = connector.end()
                if cursor >= len(text):
                    return None, spans, (connector.start(), connector.end())

            price = _PRICE_MAX.match(text, cursor)
            if price is not None and price_max is None:
                price_max = float(price.group("value"))
                spans.append(Span(cursor, price.end(), price.group(0), "price_max.v1"))
                cursor = price.end()
                continue

            excluded = self._parse_exclude_brand(text, cursor)
            if excluded is not None:
                end, operator, brand_alias, brand_id = excluded
                if brand_id in excluded_brands:
                    return None, spans, (cursor, end)
                excluded_brands.append(brand_id)
                spans.append(Span(cursor, cursor + len(operator), operator, "exclude_operator.v1"))
                spans.append(Span(cursor + len(operator), end, brand_alias, "brand_alias.v1"))
                scopes.append(("exclude", f"brand_family:{brand_id}"))
                cursor = end
                continue

            attribute = next((item for item in ATTRIBUTE_PREFERENCES if text.startswith(item, cursor)), None)
            if attribute is not None and ATTRIBUTE_PREFERENCES[attribute] not in attributes:
                attributes.append(ATTRIBUTE_PREFERENCES[attribute])
                spans.append(Span(cursor, cursor + len(attribute), attribute, "attribute_preference.v1"))
                cursor += len(attribute)
                continue
            polite = next((item for item in RECOMMEND_POLITE_SUFFIXES if text.startswith(item, cursor)), None)
            if polite is not None and cursor + len(polite) == len(text):
                spans.append(Span(cursor, cursor + len(polite), polite, "recommend_polite_suffix.v1"))
                cursor += len(polite)
                continue
            return None, spans, (cursor, len(text))

        return (
            ParseTree(
                grammar_id="recommend.category_constraints.v1",
                action=V3Action.RECOMMEND,
                product_type_id=category.canonical_id,
                price_max=price_max,
                exclude_brand_family_ids=tuple(sorted(excluded_brands)),
                include_brand_family_ids=(),
                desired_attributes=tuple(sorted(attributes)),
                operator_scopes=tuple(scopes),
            ),
            spans,
            None,
        )

    @staticmethod
    def _parse_card_fact_query(text: str, cards) -> ParseTree | None:
        match = _CARD_FACT_QUERY.fullmatch(text)
        if match is None:
            return None
        rank_text = match.group("rank")
        rank = _CHINESE_RANKS.get(rank_text)
        if rank is None and rank_text.isdigit():
            rank = int(rank_text)
        if rank is None or rank < 1 or rank > len(cards):
            return None
        card = cards[rank - 1]
        kind = CARD_FACT_QUERY_KINDS[match.group("kind")]
        return ParseTree(
            grammar_id="card.parameter_query.v1",
            action=V3Action.PARAMETER_QUERY,
            product_type_id=None,
            price_max=None,
            exclude_brand_family_ids=(),
            include_brand_family_ids=(),
            desired_attributes=(),
            operator_scopes=(("target", f"card:{card.card_id}"),),
            target_card_id=card.card_id,
            query_kind=kind,
        )

    def _parse_brand_release(self, text: str, prior_requirement) -> ParseTree | None:
        if prior_requirement is None or prior_requirement.action is not V3Action.RECOMMEND:
            return None
        brand_match = None
        force_include = False
        for prefix in BRAND_RELEASE_PREFIXES:
            if text.startswith(prefix):
                brand_match = self._longest_prefix_entity(text[len(prefix):], self._registry.brand_aliases())
                if brand_match is not None and len(prefix) + len(brand_match[0]) != len(text):
                    return None
                force_include = brand_match is not None
                break
        if brand_match is None:
            for suffix in BRAND_RELEASE_SUFFIXES:
                if text.endswith(suffix):
                    brand_match = self._longest_prefix_entity(text[:-len(suffix)], self._registry.brand_aliases())
                    if brand_match is not None and len(brand_match[0]) != len(text) - len(suffix):
                        return None
                    break
        if brand_match is None:
            return None
        _alias, brand = brand_match
        if brand.canonical_id not in prior_requirement.exclude_brand_family_ids:
            return None
        return ParseTree(
            grammar_id="recommend.brand_release.v1",
            action=V3Action.RECOMMEND,
            product_type_id=prior_requirement.product_type_ids[0] if len(prior_requirement.product_type_ids) == 1 else None,
            price_max=prior_requirement.price_max,
            exclude_brand_family_ids=tuple(item for item in prior_requirement.exclude_brand_family_ids if item != brand.canonical_id),
            include_brand_family_ids=(brand.canonical_id,) if force_include else (),
            desired_attributes=prior_requirement.desired_attributes,
            operator_scopes=(("release", f"brand_family:{brand.canonical_id}"),),
        )

    def _parse_exclude_brand(self, text: str, cursor: int) -> tuple[int, str, str, str] | None:
        operator = next((item for item in EXCLUDE_OPERATORS if text.startswith(item, cursor)), None)
        if operator is None:
            return None
        brand_match = self._longest_prefix_entity(text[cursor + len(operator):], self._registry.brand_aliases())
        if brand_match is None:
            return None
        brand_alias, brand = brand_match
        return cursor + len(operator) + len(brand_alias), operator, brand_alias, brand.canonical_id

    @staticmethod
    def _longest_prefix_entity(text: str, pairs) -> tuple[str, object] | None:
        matches = [(alias, entity) for alias, entity in pairs if text.startswith(alias)]
        if not matches:
            return None
        matches.sort(key=lambda item: len(item[0]), reverse=True)
        longest = len(matches[0][0])
        best = [item for item in matches if len(item[0]) == longest]
        return best[0] if len(best) == 1 else None
