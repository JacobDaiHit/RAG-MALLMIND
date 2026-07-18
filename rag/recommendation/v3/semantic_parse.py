"""One external-Chat pass that decodes an action-specific semantic object.

``SemanticParser`` is the only LLM semantic boundary.  It receives small local
type/brand/session context, accepts one discriminated JSON action object, and
returns no catalog fact or side effect.  Missing required action fields remain
visible as ``None``/empty values so ClarificationPolicy can ask one precise
question rather than silently guessing.
"""
from __future__ import annotations

import time
from typing import Any, Mapping

from rag.recommendation.llm_client import LLMClientError, OpenAICompatibleChatClient, run_with_hard_timeout

from .config import SEMANTIC_PARSE_MAX_ATTEMPTS, SEMANTIC_PARSE_POLICY_VERSION, SEMANTIC_PARSE_TIMEOUT_SECONDS
from .semantic_contracts import (
    BrandCandidateSet,
    CartObservation,
    FactQueryObservation,
    GeneralChatObservation,
    PcBuildObservation,
    PcCompareObservation,
    PcEditObservation,
    RecommendObservation,
    SemanticContext,
    SemanticObservation,
    render_brand_candidates,
)
from .types import CartOperation, CartTargetRef, CartTargetSource, ComputerPurchaseKind, LLMUsage, PcPlanOperation, PcPlanReference, PriceConstraint, PriceKind, PurchaseKindEvidence, RecommendationMode, SemanticParseAttempt, SemanticParseResult, TaxonomyCandidateSet, TypeSurfaceEvidence, V3Action
from .type_candidates import render_type_candidates


_FACT_KINDS = frozenset({"price", "skus", "specifications", "compare"})
_ACTION_BY_WIRE = {
    "recommend": V3Action.RECOMMEND,
    "fact_query": V3Action.PARAMETER_QUERY,
    "cart": V3Action.APPLY_CART,
    "pc_build": V3Action.PC_BUILD,
    "pc_edit": V3Action.PC_PLAN_EDIT,
    "pc_compare": V3Action.PC_PLAN_COMPARE,
    "general_chat": V3Action.GENERAL_CHAT,
}


class SemanticParser:
    """Decode one action object, with at most one logged schema-repair retry."""

    def __init__(self, client: OpenAICompatibleChatClient | None = None) -> None:
        self._client = client or OpenAICompatibleChatClient()

    def parse(
        self,
        *,
        text: str,
        registry,
        catalog: Any = None,
        candidate_set: TaxonomyCandidateSet | None = None,
        brand_candidate_set: BrandCandidateSet | None = None,
        context: SemanticContext | None = None,
    ) -> SemanticParseResult:
        if not self._client.configured:
            return SemanticParseResult(None, "", "", 0, "semantic_llm_unavailable")
        started = time.perf_counter()
        attempts: list[SemanticParseAttempt] = []
        usages: list[LLMUsage] = []
        for number in range(1, SEMANTIC_PARSE_MAX_ATTEMPTS + 1):
            try:
                payload, report = run_with_hard_timeout(
                    lambda: self._client.chat_json_with_report(
                        _messages(
                            text=text,
                            candidate_set=candidate_set,
                            brand_candidate_set=brand_candidate_set,
                            context=context,
                            repair_attempt=number > 1,
                        ),
                        model=self._client.config.fast_model,
                        temperature=0.0,
                        max_tokens=280,
                    ),
                    SEMANTIC_PARSE_TIMEOUT_SECONDS,
                    "v3_semantic_parse" if number == 1 else "v3_semantic_parse_schema_retry",
                )
            except TimeoutError:
                attempts.append(SemanticParseAttempt(number, "timeout", "semantic_llm_timeout", _elapsed_ms(started)))
                return _failed_result(self._client, started, "semantic_llm_timeout", attempts, usages)
            except (LLMClientError, TypeError, OSError, ConnectionError):
                attempts.append(SemanticParseAttempt(number, "transport_error", "semantic_llm_invalid", _elapsed_ms(started)))
                return _failed_result(self._client, started, "semantic_llm_invalid", attempts, usages)

            usage = _usage(getattr(report, "usage", {}))
            usages.append(usage)
            try:
                observation = _decode_observation(payload)
            except ValueError as exc:
                attempts.append(SemanticParseAttempt(number, "schema_invalid", _decode_error_code(exc), int(report.elapsed_ms), usage))
                if number < SEMANTIC_PARSE_MAX_ATTEMPTS:
                    continue
                return _failed_result(self._client, started, "semantic_llm_invalid", attempts, usages)
            attempts.append(SemanticParseAttempt(number, "accepted", "", int(report.elapsed_ms), usage))
            return SemanticParseResult(
                observation=observation,
                provider=self._client.config.provider,
                model=self._client.config.fast_model,
                elapsed_ms=_elapsed_ms(started),
                usage=_sum_usage(usages),
                attempts=tuple(attempts),
            )
        return _failed_result(self._client, started, "semantic_llm_invalid", attempts, usages)


def _messages(*, text: str, candidate_set: TaxonomyCandidateSet | None = None, brand_candidate_set: BrandCandidateSet | None = None, context: SemanticContext | None = None, repair_attempt: bool = False, **_unused: Any) -> list[dict[str, str]]:
    """Render a compact discriminated-union contract, not a giant field list."""

    system = (
        "你是电商导购的语义观察器，只输出一个 JSON 对象。"
        "你不能输出商品ID、SKU、目录价格、库存、商品卡 token，也不能执行购物车。"
        "action 只能是 recommend/fact_query/cart/pc_build/pc_edit/pc_compare/general_chat。"
        "必须只输出 action 对应 schema 的字段；不能混入其他 action 的字段。"
        "recommend: mode(product/explore|null),target_type_candidate_id|null,target_type_surface|null,target_type_evidence|null,"
        "positive_brand_candidate_ids[],negative_brand_candidate_ids[],release_brand_candidate_ids[],budget|null,"
        "exclude_type_candidate_ids[],desired_attribute_surfaces[],"
        "computer_purchase_kind|null,computer_purchase_evidence|null,pc_usage_surfaces[]。"
        "fact_query: card_references[],fact_kind(price/skus/specifications/compare|null)。"
        "cart: operation(add/remove/set_quantity/view/clear|null),target_ref({source:card/cart,rank:正整数}|null),quantity|null。"
        "pc_build: budget|null,usage_surfaces[],computer_purchase_evidence|null。"
        "pc_edit: operation(replace_component/adjust_budget|null),plan_reference(current/previous|null),"
        "component_candidate_id|null,upgrade_direction|null,budget|null。"
        "pc_compare: plan_reference(current/previous|null)。general_chat 除 action 外无字段。"
        "target_ref 的 rank 只能是正整数序号，例如第一个写 1，绝不能写 first/第一个。"
        "pc_edit 的 component_candidate_id 只能从类型候选中复制 pc_category: 开头的 ID。"
        "target_type_evidence 和 computer_purchase_evidence 均为 "
        "{surface,evidence_start,evidence_end,evidence_text} 或 null；"
        "budget 为 {kind:max/min/target/range,amount,min_amount,currency,evidence_start,evidence_end,evidence_text} 或 null；"
        "其证据必须逐字来自本轮原句。"
        "推荐/购买/找商品，即使目录可能没有，也必须 action=recommend，不能改成 general_chat。"
        "用户明确点名目录外类别时，target_type_candidate_id=null，并把原词写入 target_type_surface；"
        "若能给出位置，也写 target_type_evidence。"
        "开放式购物（送礼、不知道买什么、随便看看、奖励自己、宿舍添点东西）也是 action=recommend，"
        "写 mode=explore 且两个 target_type 字段都为 null；探索模式绝不能带具体类型。"
        "只有用户明确表达“还不知道买什么/随便看看/帮我想方向”时才能写 mode=explore。"
        "“推荐一辆汽车”“推荐一种处方药”“推荐一个X”都是明确目标，必须写 mode=product；"
        "即使 X 不在候选菜单，也必须把 X 原样写入 target_type_surface，绝不能改成 explore。"
        "第一个多少钱→fact_query+card_references:[1]+fact_kind:price；"
        "第二个有哪些SKU→fact_query+card_references:[2]+fact_kind:skus；"
        "比较第一个和第二个参数→fact_query+card_references:[1,2]+fact_kind:compare。"
        "把第一个加入购物车→cart+operation:add+target_ref:{source:card,rank:1}；"
        "把购物车第一个改成3件→cart+operation:set_quantity+target_ref:{source:cart,rank:1}+quantity:3；"
        "删除购物车第一个→cart+operation:remove+target_ref:{source:cart,rank:1}。"
        "‘不要只推荐小米’不是排除小米，negative_brand_candidate_ids 必须为空。"
        "用户明确说某品牌也可以时写 release_brand_candidate_ids，不把它写成正向强制品牌。"
        "品牌候选出现时必须判断其作用：明确要求该品牌→positive，明确不要/不考虑该品牌→negative，"
        "“某品牌也可以”→release；“不要只推荐某品牌”三者都不填。"
        "如果当前上下文有未完成澄清，简短回答可补齐该动作；若用户明显换话题，输出新 action，绝不沿用旧字段。"
        f"策略版本：{SEMANTIC_PARSE_POLICY_VERSION}。"
    )
    if repair_attempt:
        system += "上一次 JSON 不符合 action schema。本次只能输出一个严格匹配当前 action 的 JSON；不要补充解释或其他 action 字段。"
    type_candidates = render_type_candidates(candidate_set) if candidate_set is not None else "类型候选：无。"
    brand_candidates = render_brand_candidates(brand_candidate_set or BrandCandidateSet("none", ()))
    session_context = _render_context(context or SemanticContext())
    return [
        {"role": "system", "content": system},
        {"role": "system", "content": type_candidates},
        {"role": "system", "content": brand_candidates},
        {"role": "system", "content": session_context},
        {"role": "system", "content": _pending_action_instruction(context or SemanticContext())},
        {"role": "user", "content": text},
    ]


def _render_context(context: SemanticContext) -> str:
    pending = f"待澄清动作={context.pending_action}，缺字段={','.join(context.pending_missing_fields)}" if context.pending_action else "无待澄清动作"
    return (
        "本地会话摘要（不是商品事实）："
        f"当前商品类型={','.join(context.active_product_type_ids) or '无'}；"
        f"当前排除品牌={','.join(context.active_excluded_brand_ids) or '无'}；"
        f"可引用商品卡数量={context.live_card_count}；购物车条目={context.cart_line_count}；"
        f"PC当前方案={'有' if context.has_current_pc_plan else '无'}，上一方案={'有' if context.has_previous_pc_plan else '无'}；{pending}。"
    )


def _pending_action_instruction(context: SemanticContext) -> str:
    """Add one short, action-specific continuation rule only when applicable."""

    if context.pending_action != V3Action.RECOMMEND.value or "computer_purchase_kind" not in context.pending_missing_fields:
        return "无额外待追问动作规则。"
    return (
        "上一轮正在确认电脑购买形式：本轮“配台主机/装机/DIY”必须 action=pc_build；"
        "本轮“笔记本”必须 action=recommend、mode=product、computer_purchase_kind=laptop，"
        "并从本轮类型候选选择笔记本 ID。"
    )


def _decode_observation(payload: Mapping[str, Any]) -> SemanticObservation:
    action = _ACTION_BY_WIRE.get(_text(payload.get("action")) or "")
    if action is None:
        raise ValueError("semantic action is not allowed")
    if action is V3Action.RECOMMEND:
        _reject_unknown(payload, {"action", "mode", "target_type_surface", "target_type_candidate_id", "target_type_evidence", "exclude_type_candidate_ids", "positive_brand_candidate_ids", "negative_brand_candidate_ids", "release_brand_candidate_ids", "budget", "desired_attribute_surfaces", "computer_purchase_kind", "computer_purchase_evidence", "pc_usage_surfaces"})
        mode = _strict_enum_or_none(RecommendationMode, payload.get("mode"))
        if mode is None:
            raise ValueError("semantic recommend mode is required")
        return RecommendObservation(
            mode=mode,
            target_type_surface=_text(payload.get("target_type_surface")),
            target_type_candidate_id=_text(payload.get("target_type_candidate_id")),
            target_type_evidence=_decode_type_evidence(payload.get("target_type_evidence")),
            exclude_type_candidate_ids=_strings(payload.get("exclude_type_candidate_ids")),
            positive_brand_candidate_ids=_strings(payload.get("positive_brand_candidate_ids")),
            negative_brand_candidate_ids=_strings(payload.get("negative_brand_candidate_ids")),
            release_brand_candidate_ids=_strings(payload.get("release_brand_candidate_ids")),
            budget=_decode_budget(payload.get("budget")),
            desired_attribute_surfaces=_strings(payload.get("desired_attribute_surfaces")),
            computer_purchase_kind=_strict_enum_or_none(ComputerPurchaseKind, payload.get("computer_purchase_kind")),
            computer_purchase_evidence=_decode_purchase_evidence(payload.get("computer_purchase_evidence")),
            pc_usage_surfaces=_strings(payload.get("pc_usage_surfaces")),
        )
    if action is V3Action.PARAMETER_QUERY:
        _reject_unknown(payload, {"action", "card_references", "fact_kind"})
        kind = _text(payload.get("fact_kind"))
        return FactQueryObservation(card_references=_positive_ints(payload.get("card_references")), fact_kind=kind if kind in _FACT_KINDS else None)
    if action is V3Action.APPLY_CART:
        _reject_unknown(payload, {"action", "operation", "target_ref", "quantity"})
        return CartObservation(operation=_strict_enum_or_none(CartOperation, payload.get("operation")), target_ref=_decode_cart_target_ref(payload.get("target_ref")), quantity=_positive_int(payload.get("quantity")))
    if action is V3Action.PC_BUILD:
        _reject_unknown(payload, {"action", "budget", "usage_surfaces", "computer_purchase_evidence"})
        return PcBuildObservation(budget=_decode_budget(payload.get("budget")), usage_surfaces=_strings(payload.get("usage_surfaces")), computer_purchase_evidence=_decode_purchase_evidence(payload.get("computer_purchase_evidence")))
    if action is V3Action.PC_PLAN_EDIT:
        _reject_unknown(payload, {"action", "operation", "plan_reference", "component_candidate_id", "upgrade_direction", "budget"})
        return PcEditObservation(operation=_strict_enum_or_none(PcPlanOperation, payload.get("operation")), plan_reference=_strict_enum_or_none(PcPlanReference, payload.get("plan_reference")), component_candidate_id=_text(payload.get("component_candidate_id")), upgrade_direction=_text(payload.get("upgrade_direction")), budget=_decode_budget(payload.get("budget")))
    if action is V3Action.PC_PLAN_COMPARE:
        _reject_unknown(payload, {"action", "plan_reference"})
        return PcCompareObservation(plan_reference=_enum_or_none(PcPlanReference, payload.get("plan_reference")))
    _reject_unknown(payload, {"action"})
    return GeneralChatObservation()


def _reject_unknown(payload: Mapping[str, Any], allowed: set[str]) -> None:
    if set(payload) - allowed:
        raise ValueError("semantic action contains fields outside its contract")


def _decode_budget(value: Any) -> PriceConstraint | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("budget must be an object")
    return PriceConstraint(
        kind=PriceKind(str(value.get("kind") or "")),
        amount=float(value["amount"]),
        min_amount=float(value["min_amount"]) if value.get("min_amount") is not None else None,
        currency=str(value.get("currency") or "CNY"),
        evidence_start=int(value["evidence_start"]),
        evidence_end=int(value["evidence_end"]),
        evidence_text=str(value["evidence_text"]),
    )


def _decode_type_evidence(value: Any) -> TypeSurfaceEvidence | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("type evidence must be an object")
    surface, evidence = _text(value.get("surface")), _text(value.get("evidence_text"))
    if surface is None or evidence is None:
        raise ValueError("type evidence requires surface and evidence_text")
    return TypeSurfaceEvidence(surface, int(value["evidence_start"]), int(value["evidence_end"]), evidence)


def _decode_purchase_evidence(value: Any) -> PurchaseKindEvidence | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("computer evidence must be an object")
    surface, evidence = _text(value.get("surface")), _text(value.get("evidence_text"))
    if surface is None or evidence is None:
        raise ValueError("computer evidence requires surface and evidence_text")
    return PurchaseKindEvidence(surface, int(value["evidence_start"]), int(value["evidence_end"]), evidence)


def _usage(raw: object) -> LLMUsage:
    data = raw if isinstance(raw, Mapping) else {}
    return LLMUsage(_count(data, "prompt_tokens", "input_tokens"), _count(data, "completion_tokens", "output_tokens"), _count(data, "total_tokens"))


def _sum_usage(values: list[LLMUsage]) -> LLMUsage:
    def total(name: str) -> int | None:
        numbers = [getattr(value, name) for value in values if getattr(value, name) is not None]
        return sum(numbers) if numbers else None

    return LLMUsage(total("prompt_tokens"), total("completion_tokens"), total("total_tokens"))


def _failed_result(client: OpenAICompatibleChatClient, started: float, reason: str, attempts: list[SemanticParseAttempt], usages: list[LLMUsage]) -> SemanticParseResult:
    return SemanticParseResult(
        None,
        client.config.provider,
        client.config.fast_model,
        _elapsed_ms(started),
        reason,
        _sum_usage(usages),
        tuple(attempts),
    )


def _decode_error_code(exc: ValueError) -> str:
    message = str(exc)
    if "outside its contract" in message:
        return "schema_extra_field"
    if "enum" in message:
        return "schema_enum_invalid"
    if "target_ref" in message:
        return "schema_target_ref_invalid"
    return "schema_invalid"


def _count(data: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        try:
            value = int(data.get(key))
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return value
    return None


def _positive_int(value: Any) -> int | None:
    try:
        result = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return result if result is not None and result > 0 else None


def _positive_ints(value: Any) -> tuple[int, ...]:
    return tuple(dict.fromkeys(item for item in (_positive_int(raw) for raw in value) if item is not None)) if isinstance(value, list) else ()


def _decode_cart_target_ref(value: Any) -> CartTargetRef | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("target_ref must be an object")
    _reject_unknown(value, {"source", "rank"})
    source = _strict_enum_or_none(CartTargetSource, value.get("source"))
    rank = _positive_int(value.get("rank"))
    if source is None or rank is None:
        raise ValueError("target_ref must contain a valid source and positive rank")
    return CartTargetRef(source, rank)


def _strings(value: Any) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip())) if isinstance(value, list) else ()


def _enum_or_none(enum_type, value: Any):
    try:
        return enum_type(str(value)) if value else None
    except ValueError:
        return None


def _strict_enum_or_none(enum_type, value: Any):
    if value is None or value == "":
        return None
    try:
        return enum_type(str(value))
    except ValueError as exc:
        raise ValueError("semantic enum is not allowed") from exc


def _text(value: Any) -> str | None:
    result = str(value or "").strip()
    return result or None


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
