"""Plan bounded, catalog-grounded exploration for open-ended shopping turns.

``CatalogExplorationPlanner`` receives an already promoted explore-mode
requirement.  It never infers recipient preferences, never calls a chat model,
and never selects a product outside CandidateGate.  It chooses at most three
different real catalog directions; the recommendation executor performs the
normal retrieval and final fact validation for one product in each direction.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .candidate_gate import CatalogCandidateGate
from .registry import CatalogNormalizationRegistry
from .types import CandidateGateResult, RecommendationMode, RequirementSpecV3


MAX_EXPLORATION_DIRECTIONS = 3
_COMPATIBILITY_TYPE_PREFIX = "pc_category:"


@dataclass(frozen=True)
class ExplorationDirection:
    """One eligible catalog type and its already-certified candidate pool."""

    requirement: RequirementSpecV3
    gate: CandidateGateResult
    parent_category: str
    score: float


class CatalogExplorationPlanner:
    """Select diverse, in-stock catalog directions without guessing user taste."""

    def plan(self, *, message: str, requirement: RequirementSpecV3, catalog: Any) -> tuple[ExplorationDirection, ...]:
        if requirement.recommendation_mode is not RecommendationMode.EXPLORE:
            raise ValueError("CatalogExplorationPlanner only accepts explore-mode requirements")
        registry = CatalogNormalizationRegistry.from_catalog(catalog)
        directions: list[ExplorationDirection] = []
        for type_id, entity in registry.product_types.items():
            if type_id.startswith(_COMPATIBILITY_TYPE_PREFIX) or type_id in requirement.exclude_product_type_ids:
                continue
            typed_requirement = replace(
                requirement,
                recommendation_mode=RecommendationMode.PRODUCT,
                product_type_ids=(type_id,),
            )
            gate = CatalogCandidateGate().evaluate(typed_requirement, catalog=catalog)
            if not gate.filters.product_ids:
                continue
            products = [catalog.get(product_id) for product_id in gate.filters.product_ids]
            products = [product for product in products if product is not None]
            if not products:
                continue
            parent = str(products[0].category.value)
            profile = _profile_text(entity.display_name, products)
            directions.append(
                ExplorationDirection(typed_requirement, gate, parent, _score(message, profile, products)))

        selected: list[ExplorationDirection] = []
        used_parents: set[str] = set()
        for direction in sorted(directions, key=lambda item: (-item.score, item.requirement.product_type_ids[0])):
            if direction.parent_category in used_parents:
                continue
            selected.append(direction)
            used_parents.add(direction.parent_category)
            if len(selected) == MAX_EXPLORATION_DIRECTIONS:
                break
        return tuple(selected)


def _profile_text(display_name: str, products: list[Any]) -> str:
    fields = [display_name]
    for product in products[:12]:
        fields.extend(
            (
                str(getattr(product, "title", "")),
                str(getattr(product, "description", "")),
                " ".join(getattr(product, "tags", ()) or ()),
                " ".join(getattr(product, "best_for", ()) or ()),
            )
        )
    return " ".join(fields).lower()


def _score(message: str, profile: str, products: list[Any]) -> float:
    """Use only catalog-derived text plus availability/metadata quality.

    Chinese character bigrams make this deterministic fallback useful without
    pretending that words such as “女朋友” prove a category preference.
    """

    query_bigrams = _bigrams(message.lower())
    profile_bigrams = _bigrams(profile)
    lexical = len(query_bigrams & profile_bigrams) / max(1, len(query_bigrams))
    metadata = sum(bool(getattr(product, "title", "")) + bool(getattr(product, "tags", ())) + bool(getattr(product, "description", "")) for product in products)
    quality = min(len(products), 20) / 20 + metadata / max(1, len(products) * 3)
    return lexical + quality


def _bigrams(text: str) -> set[str]:
    compact = "".join(text.split())
    return {compact[index : index + 2] for index in range(max(0, len(compact) - 1))} or ({compact} if compact else set())
