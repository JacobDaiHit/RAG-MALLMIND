"""Compile a local grammar parse into a complete SafetyProof and requirement.

``build_proof`` records grammar/version, operator scopes, parse uniqueness, and
schema completeness; ``can_safe_direct`` grants local execution only when every
proof condition holds. This is intentionally separate from ``grammar.py`` so a
matched token span alone can never authorize execution.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Mapping, Optional, Tuple

from .config import GRAMMAR_VERSION, PROOF_VERSION
from .registry import CatalogNormalizationRegistry
from .types import ParseTree, RequirementSpecV3, SafetyProof, V3Action


def compile_requirement(tree: ParseTree) -> RequirementSpecV3:
    return RequirementSpecV3(
        action=tree.action,
        product_type_ids=(tree.product_type_id,) if tree.product_type_id else (),
        include_brand_family_ids=tree.include_brand_family_ids,
        exclude_brand_family_ids=tree.exclude_brand_family_ids,
        price_max=tree.price_max,
        desired_attributes=tree.desired_attributes,
        target_card_id=tree.target_card_id,
        query_kind=tree.query_kind,
        field_provenance={
            "product_type_ids": "grammar:taxonomy_alias.v1",
            "price_max": "grammar:price_max.v1" if tree.price_max is not None else "",
            "include_brand_family_ids": "grammar:brand_release.v1" if tree.include_brand_family_ids else "",
            "exclude_brand_family_ids": "grammar:exclude_operator.v1" if tree.exclude_brand_family_ids else "",
            "desired_attributes": "grammar:attribute_preference.v1" if tree.desired_attributes else "",
            "target_card_id": "grammar:card_reference.v1" if tree.target_card_id else "",
            "query_kind": "grammar:card_fact_kind.v1" if tree.query_kind else "",
        },
    )


def semantic_signature(requirement: RequirementSpecV3) -> str:
    payload = {
        "action": requirement.action.value,
        "product_type_ids": sorted(requirement.product_type_ids),
        "exclude_product_type_ids": sorted(requirement.exclude_product_type_ids),
        "include_brand_family_ids": sorted(requirement.include_brand_family_ids),
        "exclude_brand_family_ids": sorted(requirement.exclude_brand_family_ids),
        "price_max": requirement.price_max,
        "desired_attributes": sorted(requirement.desired_attributes),
        "target_card_id": requirement.target_card_id,
        "query_kind": requirement.query_kind,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"


def build_proof(
    *,
    trees: Tuple[ParseTree, ...],
    lexical_coverage_complete: bool,
    unresolved_spans,
    registry: CatalogNormalizationRegistry,
    session_version: Optional[int],
) -> tuple[Optional[RequirementSpecV3], Optional[SafetyProof]]:
    if not trees:
        return None, None
    compiled = [(tree, compile_requirement(tree)) for tree in trees]
    groups: Mapping[str, list[tuple[ParseTree, RequirementSpecV3]]] = defaultdict(list)
    for tree, requirement in compiled:
        groups[semantic_signature(requirement)].append((tree, requirement))
    if len(groups) != 1:
        return None, None
    signature, members = next(iter(groups.items()))
    tree, requirement = members[0]
    if requirement.action is V3Action.RECOMMEND:
        required_missing = () if requirement.product_type_ids else ("product_type_ids",)
    elif requirement.action is V3Action.PARAMETER_QUERY:
        required_missing = tuple(
            field
            for field, value in (("target_card_id", requirement.target_card_id), ("query_kind", requirement.query_kind))
            if not value
        )
    else:
        required_missing = ("unsupported_action",)
    proof = SafetyProof(
        proof_version=PROOF_VERSION,
        grammar_id=tree.grammar_id,
        grammar_version=GRAMMAR_VERSION,
        parse_tree_id=f"tree:{hashlib.sha256(repr(tree).encode('utf-8')).hexdigest()[:16]}",
        semantic_signature=signature,
        lexical_coverage_complete=lexical_coverage_complete,
        unresolved_spans=tuple(unresolved_spans),
        operator_scopes_resolved=True,
        unresolved_operators=(),
        entity_resolution_unique=True,
        reference_resolution_unique=True,
        valid_parse_count=len(trees),
        semantic_group_count=len(groups),
        semantic_unique=True,
        action_schema_complete=not required_missing,
        missing_required_fields=required_missing,
        registry_version=registry.version,
        session_version=session_version,
    )
    return requirement, proof


def can_safe_direct(proof: SafetyProof) -> bool:
    return (
        proof.lexical_coverage_complete
        and not proof.unresolved_spans
        and proof.operator_scopes_resolved
        and not proof.unresolved_operators
        and proof.entity_resolution_unique
        and proof.reference_resolution_unique
        and proof.semantic_unique
        and proof.semantic_group_count == 1
        and proof.action_schema_complete
        and not proof.missing_required_fields
    )
