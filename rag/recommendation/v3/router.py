"""Deterministic front gate for the small locally provable language subset.

``V3Router.route`` normalizes the turn against the current catalog/session,
asks ``GrammarParser`` for all parses, and delegates proof construction to
``proof.py``. It returns ``SAFE_DIRECT`` only with a complete SafetyProof;
otherwise the orchestrator performs exactly one SemanticParse call.
"""
from __future__ import annotations

from typing import Any, Tuple

from rag.recommendation.product_loader import ProductCatalog

from .grammar import GrammarParser
from .proof import build_proof, can_safe_direct
from .registry import CatalogNormalizationRegistry
from .session import load_session_core
from .types import (
    CanonicalEntity,
    EntityMention,
    EntityType,
    ParseStatus,
    RuleSignal,
    Span,
    V3RouteDecision,
)


class V3Router:
    """A fail-closed local DSL router with no heuristic fallback."""

    def route(self, turn, *, catalog: ProductCatalog, session: Any = None) -> V3RouteDecision:
        registry = CatalogNormalizationRegistry.from_catalog(catalog)
        parser = GrammarParser(registry)
        core = load_session_core(session)
        parsed = parser.parse_all(turn.text, cards=core.cards, prior_requirement=core.active_requirement)
        session_version = core.schema_version
        requirement, proof = build_proof(
            trees=parsed.trees,
            lexical_coverage_complete=not parsed.unresolved_spans,
            unresolved_spans=parsed.unresolved_spans,
            registry=registry,
            session_version=session_version,
        )
        observations = self._observations(turn.text, registry)
        if proof is not None and can_safe_direct(proof):
            signal = RuleSignal(
                status=ParseStatus.SAFE_DIRECT,
                consumed_spans=parsed.consumed_spans,
                unresolved_spans=parsed.unresolved_spans,
                parse_trees=parsed.trees,
                safety_proof=proof,
                observations=observations,
            )
            return V3RouteDecision(
                status=ParseStatus.SAFE_DIRECT,
                action=requirement.action if requirement else None,
                requirement=requirement,
                rule_signal=signal,
            )
        signal = RuleSignal(
            status=ParseStatus.NEEDS_SEMANTIC_LLM,
            consumed_spans=parsed.consumed_spans,
            unresolved_spans=parsed.unresolved_spans,
            parse_trees=parsed.trees,
            safety_proof=proof,
            observations=observations,
            reason_code=parsed.reason_code or "safety_proof_incomplete",
        )
        return V3RouteDecision(
            status=ParseStatus.NEEDS_SEMANTIC_LLM,
            action=None,
            requirement=None,
            rule_signal=signal,
        )

    @staticmethod
    def _observations(text: str, registry: CatalogNormalizationRegistry) -> Tuple[EntityMention, ...]:
        mentions: list[EntityMention] = []
        for alias, entity in [*registry.product_type_aliases(), *registry.brand_aliases()]:
            start = text.lower().find(alias.lower())
            if start >= 0:
                mentions.append(
                    EntityMention(
                        entity=entity,
                        span=Span(start, start + len(alias), text[start:start + len(alias)], "registry_observation.v1"),
                    )
                )
        mentions.sort(key=lambda item: (item.span.start, -(item.span.end - item.span.start)))
        return tuple(mentions)
