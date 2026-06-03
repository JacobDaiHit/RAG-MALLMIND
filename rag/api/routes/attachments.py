from typing import Any, Dict

from fastapi import APIRouter

from rag.api.attachments import analyze_attachment_payloads, summarize_attachment_analyses
from rag.api.request_models import AttachmentAnalyzeRequest


router = APIRouter()


@router.post("/api/analyze-attachments")
def analyze_attachments(request: AttachmentAnalyzeRequest) -> Dict[str, Any]:
    attachments = analyze_attachment_payloads(request.attachments)
    return {"attachments": attachments, "summary": summarize_attachment_analyses(attachments)}
