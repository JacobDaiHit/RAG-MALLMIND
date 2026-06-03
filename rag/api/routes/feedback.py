from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from rag.api.request_models import FeedbackRequest
from rag.recommendation.feedback_store import append_feedback_record


router = APIRouter()


@router.post("/api/feedback")
def collect_feedback(request: FeedbackRequest) -> Dict[str, Any]:
    if not request.goal.strip():
        raise HTTPException(status_code=400, detail="goal cannot be empty")
    if request.rating is not None and not 1 <= request.rating <= 5:
        raise HTTPException(status_code=400, detail="rating must be between 1 and 5")
    payload = request.model_dump(mode="json") if hasattr(request, "model_dump") else request.dict()
    return append_feedback_record(payload)
