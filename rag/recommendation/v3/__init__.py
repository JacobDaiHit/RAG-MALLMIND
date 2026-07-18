"""The sole V3 guided-selling implementation package.

Public exports expose the orchestrator, deterministic router, actions, and
decisions used by the HTTP boundary. Files below separate parsing, proof,
promotion, catalog validation, retrieval, execution, and SessionCore so no
legacy router or handler can become a second execution authority.
"""

from .orchestrator import V3Orchestrator
from .router import V3Router
from .types import ParseStatus, V3Action, V3ExecutionDecision, V3RouteDecision

__all__ = ["ParseStatus", "V3Action", "V3RouteDecision", "V3ExecutionDecision", "V3Router", "V3Orchestrator"]
