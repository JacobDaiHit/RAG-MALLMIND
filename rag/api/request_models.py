"""Pydantic request contracts for the active HTTP API.

The V3 chat path consumes ``ChatStreamRequest``, cart endpoints consume
``CartActionRequest``, and direct card comparison uses
``ProductCompareRequest``. Attachment/image fields remain intentionally in the
chat contract for a future controlled multimodal observer; current V3 rejects
them rather than falling back to an unverified legacy path.
"""
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


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

    session_id: str = Field(default_factory=lambda: f"session-{uuid4().hex}")
    product_ids: List[str] = Field(default_factory=list)
