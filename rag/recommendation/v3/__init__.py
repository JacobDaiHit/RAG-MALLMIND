"""V3 guided-selling routing boundary.

Only modules in this package may issue a V3 deterministic execution permit.
Legacy routing remains a temporary semantic fallback while the V3 vertical
cuts are migrated; it never overrides a V3 ``SAFE_DIRECT`` decision.
"""

from .orchestrator import V3Orchestrator
from .router import V3Router
from .types import ParseStatus, V3Action, V3ExecutionDecision, V3RouteDecision

__all__ = ["ParseStatus", "V3Action", "V3RouteDecision", "V3ExecutionDecision", "V3Router", "V3Orchestrator"]
