from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class GoalRequest(BaseModel):
    """Request body for business-goal analysis and recommendation endpoints."""

    goal: str
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    session_id: Optional[str] = None
    mode: str = "auto"


class AttachmentAnalyzeRequest(BaseModel):
    """Request body for browser-provided attachment data URLs."""

    attachments: List[Dict[str, Any]] = Field(default_factory=list)


class PromptFinalizeRequest(BaseModel):
    """Request body for merging the original goal, attachments, and follow-up answers."""

    goal: str
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    answers: List[Dict[str, str]] = Field(default_factory=list)


class ProductUpsertRequest(BaseModel):
    """Request body for creating or updating an ecommerce product."""

    product: Dict[str, Any] = Field(default_factory=dict)


class FeedbackRequest(BaseModel):
    """Request body for collecting user feedback on a recommendation."""

    goal: str
    selected_product_id: Optional[str] = None
    rating: Optional[int] = None
    follow_up: Optional[str] = None
    comment: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ChatStreamRequest(BaseModel):
    """Request body for the chat and chat stream endpoints."""

    session_id: str = Field(default_factory=lambda: f"session-{uuid4().hex}")
    message: str = ""
    images: List[Dict[str, Any]] = Field(default_factory=list)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    stream: bool = True
    mode: str = "auto"


class CartActionRequest(BaseModel):
    """Request body for cart mutation endpoints."""

    session_id: str = Field(default_factory=lambda: f"session-{uuid4().hex}")
    instruction: str = ""
    product_ids: List[str] = Field(default_factory=list)


class ProductCompareRequest(BaseModel):
    """Request body for comparing product cards."""

    product_ids: List[str] = Field(default_factory=list)


class PcBuildGenerateRequest(BaseModel):
    """Request body for direct PC build-plan generation."""

    budget: float = Field(..., gt=0)
    usage: List[str] = Field(default_factory=list)
    preferences: Dict[str, Any] = Field(default_factory=dict)
