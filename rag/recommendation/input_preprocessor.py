"""Input preprocessing for the guided shopping agent.

The production multimodal pieces are intentionally represented as normalized
signals here: browser-side audio/VLM/OCR outputs can be merged into the same
textual intent before routing and retrieval.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


MAX_SIGNAL_CHARS = 1800


@dataclass(frozen=True)
class PreprocessedInput:
    """Cleaned user input plus normalized multimodal signals."""

    text: str
    modalities: List[str] = field(default_factory=lambda: ["text"])
    audio_transcript: str = ""
    image_descriptions: List[str] = field(default_factory=list)
    extracted_texts: List[str] = field(default_factory=list)
    attachment_summaries: List[str] = field(default_factory=list)

    def to_trace(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "modalities": self.modalities,
            "audio_transcript": self.audio_transcript,
            "image_descriptions": self.image_descriptions,
            "extracted_texts": self.extracted_texts,
            "attachment_summaries": self.attachment_summaries,
        }


def preprocess_user_input(goal: Any, attachments: Optional[List[Dict[str, Any]]] = None) -> PreprocessedInput:
    """Clean text and merge pre-analyzed voice/image attachment signals."""

    attachments = [item for item in attachments or [] if isinstance(item, dict)]
    text = clean_text(goal)
    modalities = ["text"]
    audio_parts: List[str] = []
    image_descriptions: List[str] = []
    extracted_texts: List[str] = []
    summaries: List[str] = []

    for item in attachments[:12]:
        file_type = clean_text(item.get("type")).lower()
        name = clean_text(item.get("name"))
        summary = clean_text(item.get("summary"), MAX_SIGNAL_CHARS)
        extracted = clean_text(item.get("extracted_text"), MAX_SIGNAL_CHARS)
        transcript = clean_text(item.get("transcript") or item.get("audio_transcript"), MAX_SIGNAL_CHARS)

        item_modalities = item.get("input_modalities") or []
        for modality in item_modalities:
            value = clean_text(modality).lower()
            if value:
                modalities.append(value)
        if file_type.startswith("image/") or item.get("kind") == "image":
            modalities.append("image")
            image_descriptions.append(summary or f"{name or '图片附件'}：已接收图片，等待 VLM/OCR 描述。")
        if file_type.startswith("audio/") or item.get("kind") == "audio":
            modalities.append("audio")
            if transcript:
                audio_parts.append(transcript)
        if extracted:
            extracted_texts.append(extracted)
        if summary:
            summaries.append(summary)

    return PreprocessedInput(
        text=text,
        modalities=dedupe_strings(modalities) or ["text"],
        audio_transcript=" ".join(audio_parts)[:MAX_SIGNAL_CHARS],
        image_descriptions=dedupe_strings(image_descriptions)[:6],
        extracted_texts=dedupe_strings(extracted_texts)[:6],
        attachment_summaries=dedupe_strings(summaries)[:6],
    )


def build_preprocessed_goal(goal: Any, attachments: Optional[List[Dict[str, Any]]] = None) -> str:
    """Return the text that should enter intent parsing and retrieval."""

    prepared = preprocess_user_input(goal, attachments)
    parts = [prepared.text]
    if prepared.audio_transcript:
        parts.append(f"语音转文字：{prepared.audio_transcript}")
    if prepared.image_descriptions:
        parts.append("图片特征/VLM描述：" + " ".join(prepared.image_descriptions))
    if prepared.extracted_texts:
        parts.append("附件OCR/抽取文本：" + " ".join(prepared.extracted_texts))
    return clean_text(" ".join(part for part in parts if part))


def clean_text(value: Any, limit: int = 4000) -> str:
    """Normalize user-facing text while preserving Chinese product terms."""

    text = str(value or "").replace("\x00", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("，,", "，").replace("。。", "。")
    if limit > 0:
        return text[:limit]
    return text


def dedupe_strings(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        value = clean_text(item)
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
