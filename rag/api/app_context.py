import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from rag.api.attachments import goal_with_attachment_context, prepare_attachments_for_recommendation
from rag.recommendation import InvalidGoalError, validate_business_goal
from rag.recommendation.session_state import build_contextual_goal


ROOT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT_DIR / "frontend"
PRODUCT_IMAGES_DIR = ROOT_DIR / "data" / "ecommerce_products" / "images"
VALIDATION_VERSION = "ecommerce-input-validation-v1"
STREAM_LLM_ENABLED = os.getenv("RECOMMENDATION_STREAM_USE_LLM", "true").lower() == "true"
ALLOWED_ORIGINS = [item.strip() for item in os.getenv("RECOMMENDATION_CORS_ORIGINS", "*").split(",") if item.strip()]
CORS_ALLOW_CREDENTIALS = ALLOWED_ORIGINS != ["*"]
CATEGORY_LABELS = {
    "beauty": "美妆护肤",
    "digital": "数码电子",
    "clothing": "服饰运动",
    "food": "食品饮料",
}


def model_to_dict(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    return value


def prepare_recommendation_context(
    message: str,
    attachments: Any,
    session: Any = None,
    *,
    use_vision_llm: bool = True,
) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
    """Prepare the canonical goal, attachments, and analysis report for recommendation flows."""

    prepared_attachments, attachment_report = prepare_attachments_for_recommendation(
        attachments,
        use_vision_llm=use_vision_llm,
    )
    contextual_message = build_contextual_goal(session, message) if session is not None else message
    contextual_goal = goal_with_attachment_context(contextual_message, prepared_attachments)
    return contextual_goal, prepared_attachments, attachment_report


def validate_goal(goal: str) -> None:
    if not goal.strip():
        raise InvalidGoalError("goal cannot be empty")
    validate_business_goal(goal)


def build_requirement_questions(requirement: Any, attachments: List[Dict[str, Any]]) -> List[str]:
    questions: List[str] = []
    missing = set(requirement.missing_fields or [])
    if "category" in missing:
        questions.append("更想看美妆护肤、数码电子、服饰运动还是食品饮料？")
    if "budget_level" in missing or getattr(requirement.budget_level, "value", requirement.budget_level) == "unknown":
        questions.append("预算上限大概是多少？更偏低价、均衡还是品质优先？")
    if getattr(requirement, "need_bundle", False) and "bundle_context" in missing:
        questions.append("这套搭配/采购主要用于什么场景，例如通勤、旅行、运动、送礼还是开学？")
    if attachments:
        questions.append("这些附件是用于找同款/相似商品，还是补充购物需求背景？")
    return dedupe_strings(questions)[:3]


def build_complete_prompt(goal: str, attachments: List[Dict[str, Any]], answers: List[Dict[str, str]]) -> str:
    lines = ["请根据以下完整需求推荐传统电商商品或套装方案，并说明价格、风险、推荐依据和取舍理由。", "", "原始需求：", goal.strip()]
    if attachments:
        lines.extend(["", "附件输入："])
        for item in attachments:
            lines.append(f"- {item.get('name', 'attachment')} ({item.get('type') or 'unknown'}, {item.get('size', 0)} bytes)")
            if item.get("summary"):
                lines.append(f"  解析摘要：{item['summary']}")
            if item.get("extracted_text"):
                lines.append(f"  抽取文本：{item['extracted_text']}")
    cleaned_answers = [item for item in answers if isinstance(item, dict) and (item.get("answer") or "").strip()][:3]
    if cleaned_answers:
        lines.extend(["", "需求追问补充："])
        for index, item in enumerate(cleaned_answers, 1):
            lines.append(f"{index}. {item.get('question') or f'追问 {index}'}")
            lines.append(f"   用户回答：{item.get('answer')}")
    return "\n".join(lines)


def dedupe_strings(items: List[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
