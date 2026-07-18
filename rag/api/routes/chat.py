"""V3-only HTTP boundary for chat, cart confirmation, and card comparison."""
from __future__ import annotations

import time
from typing import Any, Dict, Iterable

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from rag.api.request_models import CartActionRequest, ChatStreamRequest, ProductCompareRequest
from rag.api.sse import safe_stream, sse_event
from rag.recommendation.product_loader import load_combined_product_catalog
from rag.recommendation.session_state import get_session, save_session
from rag.recommendation.v3.cart import CartPlanningError, apply_cart_plan, cart_plan_delta, cart_snapshot, create_cart_plan
from rag.recommendation.v3.config import CLARIFICATION_TTL_SECONDS
from rag.recommendation.v3.comparison import compare_catalog_products
from rag.recommendation.v3.fact_query_executor import execute_certified_fact_query
from rag.recommendation.v3.general_chat import execute_general_chat
from rag.recommendation.v3.normalization import normalize_turn
from rag.recommendation.v3.orchestrator import V3Orchestrator
from rag.recommendation.v3.pc_executor import execute_v3_pc_plan
from rag.recommendation.v3.recommendation_executor import execute_certified_recommendation
from rag.recommendation.v3.semantic_contracts import CartObservation, FactQueryObservation, RecommendObservation
from rag.recommendation.v3.session import apply_session_delta, clarification_delta, general_chat_delta, load_session_core
from rag.recommendation.v3.types import ClarificationPlan, ParseStatus, V3Action, V3ExecutionDecision
from rag.security.prompt_guard import detect_injection


router = APIRouter()
MAX_MESSAGE_LENGTH = 2000


def sanitize_input(message: str, session_id: str) -> str:
    """Reject unsafe or oversized text; never truncate and continue meaningfully."""

    if not session_id or not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id is required")
    cleaned = str(message or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="message cannot be empty")
    if len(cleaned) > MAX_MESSAGE_LENGTH:
        raise HTTPException(status_code=413, detail="message exceeds the 2000-character limit")
    injection = detect_injection(cleaned)
    if injection.should_block:
        raise HTTPException(status_code=400, detail="Invalid input — request contains disallowed content.")
    return cleaned


@router.post("/api/chat/stream")
def chat_stream(request: ChatStreamRequest) -> StreamingResponse:
    message = sanitize_input(request.message, request.session_id)
    session = get_session(request.session_id)
    attachments = [*request.attachments, *request.images]

    def generate() -> Iterable[str]:
        if attachments:
            yield sse_event("error", {"label": "附件导购暂不可用", "detail": "V3 尚未实现附件的受控语义观察，已拒绝请求，不会回退到旧链路。"})
            yield sse_event("done", {"session_id": session.session_id})
            return
        decision = V3Orchestrator().decide(
            normalize_turn(session_id=session.session_id, message=message),
            catalog=load_combined_product_catalog(),
            session=session,
        )
        yield sse_event("v3_routing", _route_payload(decision))
        yield sse_event("v3_trace", _decision_session_trace(decision, session))
        if decision.status is ParseStatus.LOCAL_CLARIFY:
            yield from _save_and_emit_clarification(session, message, decision)
            return
        if decision.status is ParseStatus.REJECT:
            if decision.reason_code == "catalog_scope_unsupported":
                yield sse_event("error", {"label": "当前商品目录暂不支持该商品", "detail": "当前目录中没有可用于推荐的对应商品。", "reason": decision.reason_code})
            else:
                yield sse_event("error", {"label": "需求暂不可安全执行", "detail": "当前无法可靠理解或执行这条请求，请换一种更明确的说法。", "reason": decision.reason_code})
            yield sse_event("done", {"session_id": session.session_id})
            return
        if decision.status not in {ParseStatus.SAFE_DIRECT, ParseStatus.SEMANTIC_EXECUTABLE} or decision.action is None:
            yield sse_event("error", {"label": "请求状态异常", "detail": "路由没有产生可执行动作。"})
            yield sse_event("done", {"session_id": session.session_id})
            return
        yield sse_event("runtime_mode", {
            "runtime_mode": "v3_deterministic" if decision.status is ParseStatus.SAFE_DIRECT else "v3_semantic",
            "reason": decision.reason_code,
            "semantic_parse_called": decision.semantic is not None,
        })
        yield sse_event(
            "tool_call",
            {
                "name": decision.action.value,
                "source": "v3_deterministic" if decision.status is ParseStatus.SAFE_DIRECT else "v3_semantic",
                "arguments": {"action": decision.action.value},
            },
        )
        yield from _execute_decision(session, message, decision)

    return StreamingResponse(
        safe_stream(generate, {"session_id": session.session_id}),
        media_type="text/event-stream",
        headers={"content-type": "text/event-stream"},
    )


def _execute_decision(session: Any, message: str, decision: V3ExecutionDecision) -> Iterable[str]:
    catalog = load_combined_product_catalog()
    if decision.action is V3Action.RECOMMEND and decision.requirement is not None:
        yield from execute_certified_recommendation(session=session, message=message, requirement=decision.requirement, proof=decision.rule_signal.safety_proof, catalog=catalog)
        return
    if decision.action is V3Action.PARAMETER_QUERY and decision.requirement is not None:
        yield from execute_certified_fact_query(session=session, requirement=decision.requirement, catalog=catalog)
        return
    if decision.action is V3Action.APPLY_CART and decision.semantic and decision.semantic.observation:
        observation = decision.semantic.observation
        if not isinstance(observation, CartObservation):
            yield sse_event("error", {"label": "购物车请求结构异常", "detail": "当前请求未被执行。"})
            yield sse_event("done", {"session_id": session.session_id})
            return
        yield from _stream_cart_plan(session, observation, catalog)
        return
    if decision.action in {V3Action.PC_BUILD, V3Action.PC_PLAN_EDIT, V3Action.PC_PLAN_COMPARE} and decision.requirement is not None and decision.semantic and decision.semantic.observation:
        yield from execute_v3_pc_plan(session=session, requirement=decision.requirement, observation=decision.semantic.observation, catalog=catalog)
        return
    if decision.action is V3Action.GENERAL_CHAT:
        core = load_session_core(session)
        if core.pending_clarification is not None:
            apply_session_delta(session, general_chat_delta(core))
            save_session(session)
        yield from execute_general_chat(session=session, message=message)
        return
    yield sse_event("error", {"label": "V3 动作缺少完整字段", "detail": "当前请求未被执行。"})
    yield sse_event("done", {"session_id": session.session_id})


def _stream_cart_plan(session: Any, observation: Any, catalog: Any) -> Iterable[str]:
    core = load_session_core(session)
    try:
        plan = create_cart_plan(core=core, observation=observation, catalog=catalog)
    except CartPlanningError as exc:
        plan = ClarificationPlan(
            question=str(exc),
            missing_fields=("cart_target",),
            expires_at=time.time() + CLARIFICATION_TTL_SECONDS,
            reason_code="cart_target_unresolved",
        )
        apply_session_delta(session, clarification_delta(core, plan=plan, observation=observation, source_text=""))
        save_session(session)
        yield sse_event("clarification", {"question": plan.question, "missing_fields": list(plan.missing_fields), "expires_at": plan.expires_at, "reason": plan.reason_code})
        yield sse_event("done", {"session_id": session.session_id})
        return
    if plan is None:
        yield sse_event("cart", {"action": "view", **cart_snapshot(core, catalog)})
        yield sse_event("done", {"session_id": session.session_id})
        return
    apply_session_delta(session, cart_plan_delta(core, plan))
    save_session(session)
    yield sse_event("cart_confirmation", {"plan": _cart_plan_payload(plan), "message": _cart_confirmation_message(plan)})
    yield sse_event("done", {"session_id": session.session_id})


def _save_and_emit_clarification(session: Any, message: str, decision: V3ExecutionDecision) -> Iterable[str]:
    if decision.clarification is None or decision.semantic is None or decision.semantic.observation is None:
        yield sse_event("error", {"label": "澄清计划异常", "detail": "缺少可持久化的澄清上下文。"})
        yield sse_event("done", {"session_id": session.session_id})
        return
    plan = decision.clarification
    apply_session_delta(session, clarification_delta(load_session_core(session), plan=plan, observation=decision.semantic.observation, source_text=message))
    save_session(session)
    yield sse_event("clarification", {"question": plan.question, "missing_fields": list(plan.missing_fields), "expires_at": plan.expires_at, "reason": plan.reason_code})
    yield sse_event("done", {"session_id": session.session_id})


@router.post("/api/cart/actions")
def cart_actions(request: CartActionRequest) -> Dict[str, Any]:
    message = sanitize_input(request.instruction, request.session_id)
    session = get_session(request.session_id)
    decision = V3Orchestrator().decide(normalize_turn(session_id=session.session_id, message=message), catalog=load_combined_product_catalog(), session=session)
    if decision.status is not ParseStatus.SEMANTIC_EXECUTABLE or decision.action is not V3Action.APPLY_CART or decision.semantic is None or decision.semantic.observation is None:
        raise HTTPException(status_code=422, detail="购物车指令未能安全解析，请说明操作和目标。")
    core = load_session_core(session)
    try:
        plan = create_cart_plan(core=core, observation=decision.semantic.observation, catalog=load_combined_product_catalog())
    except CartPlanningError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if plan is None:
        return {"status": "view", "action": "view", "cart": cart_snapshot(core, load_combined_product_catalog())}
    apply_session_delta(session, cart_plan_delta(core, plan))
    save_session(session)
    return {"status": "pending_confirmation", "plan": _cart_plan_payload(plan), "message": _cart_confirmation_message(plan)}


@router.post("/api/cart/confirm")
def cart_confirm(request: Dict[str, Any]) -> Dict[str, Any]:
    session = get_session(request.get("session_id"))
    try:
        delta, result = apply_cart_plan(core=load_session_core(session), catalog=load_combined_product_catalog(), confirmed=bool(request.get("confirmed", False)))
    except CartPlanningError as exc:
        raise HTTPException(status_code=410 if "过期" in str(exc) else 400, detail=str(exc)) from exc
    apply_session_delta(session, delta)
    save_session(session)
    return result


@router.post("/api/products/compare")
def compare_product_cards(request: ProductCompareRequest) -> Dict[str, Any]:
    if len(request.product_ids) < 2:
        raise HTTPException(status_code=400, detail="at least two product_ids are required")
    return compare_catalog_products(catalog=load_combined_product_catalog(), product_ids=request.product_ids)


def _route_payload(decision: V3ExecutionDecision) -> Dict[str, Any]:
    proof = decision.rule_signal.safety_proof
    observation = decision.semantic.observation if decision.semantic else None
    return {
        "status": decision.status.value,
        "action": decision.action.value if decision.action else None,
        "reason": decision.reason_code,
        "grammar_id": proof.grammar_id if proof else None,
        "semantic_signature": proof.semantic_signature if proof else None,
        "proof_version": proof.proof_version if proof else None,
        "semantic_provider": decision.semantic.provider if decision.semantic else None,
        "semantic_model": decision.semantic.model if decision.semantic else None,
        "semantic_parse_called": decision.semantic is not None,
        "semantic_usage": {
            "prompt_tokens": decision.semantic.usage.prompt_tokens,
            "completion_tokens": decision.semantic.usage.completion_tokens,
            "total_tokens": decision.semantic.usage.total_tokens,
            "elapsed_ms": decision.semantic.elapsed_ms,
        } if decision.semantic else None,
        "semantic_attempts": [
            {
                "attempt": item.attempt,
                "outcome": item.outcome,
                "reason": item.reason_code,
                "elapsed_ms": item.elapsed_ms,
                "prompt_tokens": item.usage.prompt_tokens,
                "completion_tokens": item.usage.completion_tokens,
            }
            for item in (decision.semantic.attempts if decision.semantic else ())
        ],
        "recommendation_mode": decision.requirement.recommendation_mode.value if decision.requirement and decision.action is V3Action.RECOMMEND else None,
        "computer_purchase_kind": observation.computer_purchase_kind.value if isinstance(observation, RecommendObservation) and observation.computer_purchase_kind else None,
    }


def _decision_session_trace(decision: V3ExecutionDecision, session: Any) -> Dict[str, Any]:
    """Emit minimal decision evidence needed to diagnose CardRef failures.

    This intentionally exposes card counts and ordinal ranks only. Product IDs,
    card tokens, model prompts, and raw model JSON stay out of the SSE trace.
    """

    core = load_session_core(session)
    observation = decision.semantic.observation if decision.semantic else None
    fact = observation if isinstance(observation, FactQueryObservation) else None
    cart = observation if isinstance(observation, CartObservation) else None
    return {
        "semantic_action_variant": observation.action.value if observation else None,
        "semantic_card_references": list(fact.card_references if fact else ()),
        "semantic_cart_target": {
            "source": cart.target_ref.source.value,
            "rank": cart.target_ref.rank,
        } if cart and cart.target_ref else None,
        "semantic_query_kind": fact.fact_kind if fact else None,
        "session_live_card_count": len(core.cards),
        "session_live_card_ranks": list(range(1, len(core.cards) + 1)),
        "session_pending_cart_plan": core.pending_cart_plan is not None,
        "session_cart_line_count": len(core.cart_lines),
    }


def _cart_plan_payload(plan: Any) -> Dict[str, Any]:
    return {"plan_id": plan.plan_id, "operation": plan.operation.value, "product_id": plan.product_id, "sku_id": plan.sku_id, "quantity": plan.quantity, "expires_at": plan.expires_at, "product_title": plan.title, "estimated_unit_price": plan.unit_price}


def _cart_confirmation_message(plan: Any) -> str:
    if plan.operation.value == "clear":
        return "确认清空购物车吗？"
    if plan.operation.value == "remove":
        return f"确认从购物车移除「{plan.title}」吗？"
    if plan.operation.value == "set_quantity":
        return f"确认将「{plan.title}」的数量改为 {plan.quantity} 吗？"
    return f"确认将「{plan.title}」x{plan.quantity} 加入购物车吗？"
