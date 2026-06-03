"""FastAPI routes for PC build-plan generation."""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from rag.api.request_models import PcBuildGenerateRequest
from rag.recommendation.pc_build import generate_pc_build_plan
from rag.utils.runtime_errors import public_error


router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/api/pc-build/generate")
def generate_pc_build(request: PcBuildGenerateRequest) -> Dict[str, Any]:
    """Generate a compatible purchasable PC build from real local parts."""

    try:
        return generate_pc_build_plan(
            budget=request.budget,
            usage=request.usage,
            preferences=request.preferences,
        )
    except ValueError as exc:
        logger.warning("PC build generation validation failed: %s", exc)
        raise HTTPException(status_code=400, detail=public_error(exc)) from exc
