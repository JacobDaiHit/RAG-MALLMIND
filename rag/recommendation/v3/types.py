"""Immutable cross-module contracts for the complete V3 request chain.

This file defines normalized input, grammar/proof results, requirements,
catalog filters, card references, clarification, cart, PC-plan, session, and
execution decisions.  Model-produced semantic observations deliberately live
in ``semantic_contracts.py`` as an action-specific discriminated union.
Modules exchange typed objects rather than nested dictionaries; the HTTP layer
serializes only at its SSE/API boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Tuple


class ParseStatus(str, Enum):
    SAFE_DIRECT = "safe_direct"
    SEMANTIC_EXECUTABLE = "semantic_executable"
    NEEDS_SEMANTIC_LLM = "needs_semantic_llm"
    LOCAL_CLARIFY = "local_clarify"
    REJECT = "reject"


class V3Action(str, Enum):
    RECOMMEND = "recommend_shopping_products"
    PARAMETER_QUERY = "parameter_query"
    APPLY_CART = "apply_cart_instruction"
    GENERAL_CHAT = "general_chat"
    PC_BUILD = "generate_pc_build_plan"
    PC_PLAN_EDIT = "edit_pc_build_plan"
    PC_PLAN_COMPARE = "compare_pc_build_plans"


class PcPlanOperation(str, Enum):
    BUILD = "build"
    REPLACE_COMPONENT = "replace_component"
    ADJUST_BUDGET = "adjust_budget"
    COMPARE = "compare"


class PcPlanReference(str, Enum):
    CURRENT = "current"
    PREVIOUS = "previous"


class ComputerPurchaseKind(str, Enum):
    """The requested form of a computer purchase; never a catalog fact."""

    DESKTOP_BUILD = "desktop_build"
    LAPTOP = "laptop"
    PREBUILT_DESKTOP = "prebuilt_desktop"
    UNKNOWN = "unknown"


class CartOperation(str, Enum):
    ADD = "add"
    REMOVE = "remove"
    SET_QUANTITY = "set_quantity"
    VIEW = "view"
    CLEAR = "clear"


class CartTargetSource(str, Enum):
    """The short-lived list a cart ordinal is allowed to address."""

    CARD = "card"
    CART = "cart"


class RecommendationMode(str, Enum):
    """Recommendation either has one certified type or explores the catalog."""

    PRODUCT = "product"
    EXPLORE = "explore"


class PriceKind(str, Enum):
    MAX = "max"
    MIN = "min"
    TARGET = "target"
    RANGE = "range"


class EntityType(str, Enum):
    PRODUCT_TYPE = "product_type"
    BRAND_FAMILY = "brand_family"
    ATTRIBUTE = "attribute"


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    text: str
    rule_id: str


@dataclass(frozen=True)
class NormalizedTurn:
    request_id: str
    session_id: str
    text: str
    input_events: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CanonicalEntity:
    entity_type: EntityType
    canonical_id: str
    display_name: str
    aliases: Tuple[str, ...]
    catalog_values: Tuple[str, ...] = ()


@dataclass(frozen=True)
class EntityMention:
    entity: CanonicalEntity
    span: Span


@dataclass(frozen=True)
class RequirementSpecV3:
    action: V3Action
    recommendation_mode: RecommendationMode = RecommendationMode.PRODUCT
    product_type_ids: Tuple[str, ...] = ()
    exclude_product_type_ids: Tuple[str, ...] = ()
    include_brand_family_ids: Tuple[str, ...] = ()
    exclude_brand_family_ids: Tuple[str, ...] = ()
    price_max: Optional[float] = None
    price_min: Optional[float] = None
    price_target: Optional[float] = None
    desired_attributes: Tuple[str, ...] = ()
    target_card_id: Optional[str] = None
    target_card_ids: Tuple[str, ...] = ()
    query_kind: Optional[str] = None
    field_provenance: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PriceConstraint:
    """LLM semantic reading anchored to an exact user-text price span."""

    kind: PriceKind
    amount: float
    min_amount: Optional[float]
    currency: str
    evidence_start: int
    evidence_end: int
    evidence_text: str


@dataclass(frozen=True)
class TypeSurfaceEvidence:
    """A raw product-type phrase that must point to this user turn exactly."""

    surface: str
    evidence_start: int
    evidence_end: int
    evidence_text: str


@dataclass(frozen=True)
class PurchaseKindEvidence:
    """A phrase in this user turn proving the selected computer form."""

    surface: str
    evidence_start: int
    evidence_end: int
    evidence_text: str


@dataclass(frozen=True)
class TypeDocument:
    """One catalog-derived type-level search document; never a product fact."""

    canonical_type_id: str
    display_name: str
    parent_category: str
    profile_text: str


@dataclass(frozen=True)
class TaxonomyCandidate:
    canonical_type_id: str
    display_name: str
    score: float
    sources: Tuple[str, ...]


@dataclass(frozen=True)
class TaxonomyCandidateSet:
    retrieval_version: str
    registry_version: str
    candidates: Tuple[TaxonomyCandidate, ...]
    explicit_type_count: int
    prompt_overflow: bool = False


@dataclass(frozen=True)
class TypeResolutionResult:
    """The only local interpretation from candidate IDs to catalog type IDs."""

    product_type_ids: Tuple[str, ...] = ()
    exclude_product_type_ids: Tuple[str, ...] = ()
    clarification: Optional["ClarificationPlan"] = None
    reason_code: str = ""


@dataclass(frozen=True)
class LLMUsage:
    """Safe, numeric-only token accounting for one external model call."""

    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


@dataclass(frozen=True)
class SemanticParseAttempt:
    """One observable model attempt; raw prompts and model text stay out of SessionCore."""

    attempt: int
    outcome: str
    reason_code: str = ""
    elapsed_ms: int = 0
    usage: LLMUsage = LLMUsage()


@dataclass(frozen=True)
class SemanticParseResult:
    observation: Optional["SemanticObservation"]
    provider: str
    model: str
    elapsed_ms: int
    error_code: str = ""
    usage: LLMUsage = LLMUsage()
    attempts: Tuple[SemanticParseAttempt, ...] = ()


@dataclass(frozen=True)
class ClarificationPlan:
    question: str
    missing_fields: Tuple[str, ...]
    expires_at: float
    reason_code: str


@dataclass(frozen=True)
class PendingClarification:
    plan: ClarificationPlan
    observation: "SemanticObservation"
    source_text: str


@dataclass(frozen=True)
class CartLine:
    """A real cart line.  Product and SKU references are catalog validated."""

    product_id: str
    sku_id: Optional[str]
    quantity: int


@dataclass(frozen=True)
class CartTargetRef:
    """One unambiguous ordinal pointing to a recommendation card or cart line."""

    source: CartTargetSource
    rank: int


@dataclass(frozen=True)
class CartPlan:
    """A non-side-effecting cart proposal awaiting an explicit confirmation."""

    plan_id: str
    operation: CartOperation
    product_id: Optional[str]
    sku_id: Optional[str]
    quantity: Optional[int]
    expires_at: float
    title: str
    unit_price: Optional[float]


@dataclass(frozen=True)
class PcPlanVersion:
    """Minimal catalog-validated PC plan state needed by the next turn."""

    plan_id: str
    revision: int
    budget: float
    part_product_ids: Tuple[str, ...]
    usage: Tuple[str, ...]
    parent_plan_id: Optional[str]
    expires_at: float


@dataclass(frozen=True)
class PcPlanHistory:
    """At most two short-lived PC plan references; detailed traces stay outside SessionCore."""

    current: Optional[PcPlanVersion] = None
    previous: Optional[PcPlanVersion] = None


@dataclass(frozen=True)
class PromotionResult:
    requirement: Optional[RequirementSpecV3]
    clarification: Optional[ClarificationPlan]
    reason_code: str


@dataclass(frozen=True)
class CardModel:
    """A short-lived, catalog-validated reference shown to the user."""

    card_id: str
    product_id: str
    sku_ids: Tuple[str, ...]
    title: str
    rank: int
    expires_at: float


@dataclass(frozen=True)
class TopicState:
    topic_id: str
    kind: str
    updated_at: float


@dataclass(frozen=True)
class SessionCore:
    """The only V3 conversation state needed to resolve the next turn."""

    schema_version: int
    topic: Optional[TopicState]
    active_requirement: Optional[RequirementSpecV3]
    cards: Tuple[CardModel, ...]
    pending_clarification: Optional[PendingClarification] = None
    cart_lines: Tuple[CartLine, ...] = ()
    pending_cart_plan: Optional[CartPlan] = None
    pc_plans: PcPlanHistory = field(default_factory=PcPlanHistory)


@dataclass(frozen=True)
class SessionDelta:
    """An explicit replacement for V3 session state; no handler mutates it."""

    core: SessionCore
    reason: str


@dataclass(frozen=True)
class RetrievalFilters:
    """Catalog-validated filters; raw user text never reaches retrieval."""

    product_ids: Tuple[str, ...]
    sub_categories: Tuple[str, ...]
    exclude_brand_family_ids: Tuple[str, ...]
    price_max: Optional[float]


@dataclass(frozen=True)
class CandidateGateResult:
    filters: RetrievalFilters
    rejected_by_reason: Mapping[str, Tuple[str, ...]]


@dataclass(frozen=True)
class RetrievalEvidenceV3:
    status: str
    ranked_product_ids: Tuple[str, ...]
    raw_hit_count: int
    filter_expression: str
    error_code: str = ""


@dataclass(frozen=True)
class ParseTree:
    grammar_id: str
    action: V3Action
    product_type_id: Optional[str]
    price_max: Optional[float]
    exclude_brand_family_ids: Tuple[str, ...]
    include_brand_family_ids: Tuple[str, ...]
    desired_attributes: Tuple[str, ...]
    operator_scopes: Tuple[Tuple[str, str], ...]
    target_card_id: Optional[str] = None
    target_card_ids: Tuple[str, ...] = ()
    query_kind: Optional[str] = None


@dataclass(frozen=True)
class SafetyProof:
    proof_version: str
    grammar_id: str
    grammar_version: str
    parse_tree_id: str
    semantic_signature: str
    lexical_coverage_complete: bool
    unresolved_spans: Tuple[Span, ...]
    operator_scopes_resolved: bool
    unresolved_operators: Tuple[str, ...]
    entity_resolution_unique: bool
    reference_resolution_unique: bool
    valid_parse_count: int
    semantic_group_count: int
    semantic_unique: bool
    action_schema_complete: bool
    missing_required_fields: Tuple[str, ...]
    registry_version: str
    session_version: Optional[int]


@dataclass(frozen=True)
class RuleSignal:
    status: ParseStatus
    consumed_spans: Tuple[Span, ...]
    unresolved_spans: Tuple[Span, ...]
    parse_trees: Tuple[ParseTree, ...]
    safety_proof: Optional[SafetyProof]
    observations: Tuple[EntityMention, ...] = ()
    reason_code: str = ""


@dataclass(frozen=True)
class V3RouteDecision:
    status: ParseStatus
    action: Optional[V3Action]
    requirement: Optional[RequirementSpecV3]
    rule_signal: RuleSignal


@dataclass(frozen=True)
class V3ExecutionDecision:
    status: ParseStatus
    action: Optional[V3Action]
    requirement: Optional[RequirementSpecV3]
    rule_signal: RuleSignal
    semantic: Optional[SemanticParseResult] = None
    clarification: Optional[ClarificationPlan] = None
    reason_code: str = ""
