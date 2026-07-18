"""One external-Chat semantic observation pass for non-grammar user turns.

``SemanticParser.parse`` sends a compact policy, current catalog capability,
and local type candidates to the configured model, then decodes only typed
observations. ``_messages`` is the single SemanticParse prompt builder;
``_decode_observation`` rejects unknown enum/field shapes. This module never
turns model output into catalog IDs, prices, SKUs, inventory, or side effects.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Mapping

from rag.recommendation.llm_client import LLMClientError, OpenAICompatibleChatClient, run_with_hard_timeout

from .config import SEMANTIC_PARSE_POLICY_VERSION, SEMANTIC_PARSE_TIMEOUT_SECONDS
from .registry import CatalogNormalizationRegistry
from .type_candidates import render_type_candidates
from .types import CommerceIntent, ComputerPurchaseKind, PcPlanOperation, PcPlanReference, PriceConstraint, PriceKind, PurchaseKindEvidence, SemanticObservation, SemanticParseResult, TaxonomyCandidateSet, TypeSurfaceEvidence, V3Action


_ACTION_NAMES = {action.value: action for action in V3Action}
_COMMERCE_INTENT_NAMES = {intent.value: intent for intent in CommerceIntent}
_PC_OPERATION_NAMES = {operation.value: operation for operation in PcPlanOperation}
_PC_REFERENCE_NAMES = {reference.value: reference for reference in PcPlanReference}
_COMPUTER_PURCHASE_KIND_NAMES = {kind.value: kind for kind in ComputerPurchaseKind}
_QUERY_KINDS = frozenset({"specifications", "skus", "price", "compare"})


class SemanticParser:
    """Ask the model once for semantic observations, never executable facts."""

    def __init__(self, client: OpenAICompatibleChatClient | None = None) -> None:
        self._client = client or OpenAICompatibleChatClient()

    def parse(
        self,
        *,
        text: str,
        registry: CatalogNormalizationRegistry,
        catalog: Any = None,
        candidate_set: TaxonomyCandidateSet | None = None,
    ) -> SemanticParseResult:
        if not self._client.configured:
            return SemanticParseResult(None, "", "", 0, "semantic_llm_unavailable")
        started = time.perf_counter()
        try:
            payload, report = run_with_hard_timeout(
                lambda: self._client.chat_json_with_report(
                    _messages(text=text, registry=registry, catalog=catalog, candidate_set=candidate_set),
                    model=self._client.config.fast_model,
                    temperature=0.0,
                    max_tokens=500,
                ),
                SEMANTIC_PARSE_TIMEOUT_SECONDS,
                "v3_semantic_parse",
            )
            observation = _decode_observation(payload)
            return SemanticParseResult(
                observation=observation,
                provider=self._client.config.provider,
                model=self._client.config.fast_model,
                elapsed_ms=report.elapsed_ms,
            )
        except TimeoutError:
            return SemanticParseResult(None, self._client.config.provider, self._client.config.fast_model, _elapsed_ms(started), "semantic_llm_timeout")
        except (LLMClientError, ValueError, TypeError, OSError, ConnectionError):
            return SemanticParseResult(None, self._client.config.provider, self._client.config.fast_model, _elapsed_ms(started), "semantic_llm_invalid")


def _messages(
    *,
    text: str,
    registry: CatalogNormalizationRegistry,
    catalog: Any = None,
    candidate_set: TaxonomyCandidateSet | None = None,
) -> list[dict[str, str]]:
    system = (
        "你是语义观察器，只输出一个 JSON 对象。"
        "你只观察用户意图，绝不输出商品ID、SKU ID、目录价格、库存、推荐结果，也不执行购物车。"
        "action 只能是：recommend_shopping_products、parameter_query、apply_cart_instruction、general_chat、generate_pc_build_plan、edit_pc_build_plan、compare_pc_build_plans。"
        "commerce_intent 只能是 none/recommend/compare/cart/pc_plan；它只表示商品意图，不能执行。"
        "动作规则：明确要推荐、购买、寻找或选择普通商品时使用 recommend_shopping_products；"
        "即使商品不在目录能力表中，也要保留用户说的商品类型原词到 target_type_surface，不能因此改为 general_chat。"
        "查询已展示商品的参数、SKU、价格或比较时用 parameter_query；没有卡片序号/两个引用时保留该动作并在 missing_fields 写 card_reference 或 card_references。"
        "电脑购买形式：原句明确出现配/装/组一台、攒机、装机、DIY 或配置单时，computer_purchase_kind=desktop_build 且使用 generate_pc_build_plan；仅说电脑、主机、游戏主机，或只给 RTX 等硬件规格，不代表用户要装机，填 unknown 并澄清购买形式。明确说笔记本时填 laptop 且使用 recommend_shopping_products；明确说成品台式机时填 prebuilt_desktop 且使用 recommend_shopping_products。"
        "computer_purchase_kind 仅在原句真的涉及电脑、笔记本或主机时填写；任何值（包括 unknown）都必须用 computer_purchase_evidence 精确回指本轮原句中的电脑/主机或明确装机短语。其它商品请求该字段和 evidence 都填 null。"
        "购物车增删改查用 apply_cart_instruction；修改当前/上一套方案用 edit_pc_build_plan；比较两套方案用 compare_pc_build_plans；只有完全不涉及购物、商品卡、购物车或装机的问候/知识聊天才用 general_chat。"
        "target_type_surface、品牌和用途字段保留用户原话；target_type_surface 必须复制原句连续片段，不能把 SSD 改写成固态硬盘；不确定的字段填 null 或空数组，不得编造目录事实。"
        "可能/也许/或许/听说等弱表达不得写入 include_brand_surfaces 或 exclude_brand_surfaces。"
        "price_constraint 为对象或 null，字段为 kind(max/target/range)、amount、min_amount、currency、evidence_start、evidence_end、evidence_text。"
        "金额必须来自原句：预算1000=max，1000左右=target，800到1000=range；没有金额时 price_constraint=null 且不得在 missing_fields 写 price；只有原句存在冲突金额或金额性质无法判断时才写 missing_fields=price。"
        "推荐时 target_type_surface 必须是用户真正想要的原词，并用 target_type_evidence 精确回指原句；"
        "target_type_candidate_id 只能从本轮类型候选中选一个，不能确定则为 null。"
        "用户明确说不要的类别，填 exclude_type_candidate_ids 和与之逐项对应的 exclude_type_evidences；不要把排除类别写成目标。"
        "target_type_evidence 必须是对象，不得是字符串，格式固定为"
        '{"surface":"原词","evidence_start":起始下标,"evidence_end":结束下标,"evidence_text":"原词"}；'
        "evidence_text 必须等于 surface。exclude_type_evidences 也必须是同结构对象数组，顺序和数量与 exclude_type_candidate_ids 完全一致。"
        "送礼、夏天用的东西、性价比数码产品等只给泛用途/大类而没有具体商品类型时：commerce_intent=recommend，target_type_surface=null，missing_fields 写 product_type；无商品卡的比较：commerce_intent=compare。"
        "例：配一台 7000 游戏主机 -> desktop_build + PC build；推荐一台剪辑笔记本 -> laptop + 普通推荐；剪辑电脑 9000 -> unknown + 澄清购买形式。"
        "PC 编辑可填 pc_operation(replace_component/adjust_budget)、pc_plan_reference(current/previous)、pc_component_category_surface、upgrade_direction；模型不能填产品ID。"
        "输出字段固定为：action, commerce_intent, target_type_surface, target_type_candidate_id, target_type_evidence, exclude_type_candidate_ids, exclude_type_evidences, include_brand_surfaces, exclude_brand_surfaces, price_constraint, desired_attribute_surfaces, target_card_rank, target_card_ranks, target_cart_rank, query_kind, cart_operation, quantity, pc_usage_surfaces, pc_operation, pc_plan_reference, pc_component_category_surface, upgrade_direction, computer_purchase_kind, computer_purchase_evidence, missing_fields。"
        f"策略版本：{SEMANTIC_PARSE_POLICY_VERSION}。"
    )
    capability = _catalog_capability_summary(registry=registry, catalog=catalog)
    candidates = render_type_candidates(candidate_set) if candidate_set is not None else "类型候选：无。target_type_candidate_id 必须为 null。"
    return [
        {"role": "system", "content": system},
        {"role": "system", "content": capability},
        {"role": "system", "content": candidates},
        {"role": "user", "content": text},
    ]


def _catalog_capability_summary(*, registry: CatalogNormalizationRegistry, catalog: Any = None) -> str:
    """Give the model a compact scope map, never the full product/SKU list."""

    if catalog is None:
        return "目录能力表：数码（手机、平板、耳机）；PC 配件（CPU、显卡、主板、内存、固态、电源、机箱）；其他商品需保留用户原话，由后续目录校验。"
    grouped: dict[str, set[str]] = defaultdict(set)
    pc_categories: set[str] = set()
    for product in catalog.products:
        category = str(product.category.value)
        if category.startswith("pc_"):
            pc_categories.add(category)
        else:
            grouped[category].add(str(product.sub_category))
    labels = {"beauty": "美妆", "clothing": "服饰", "digital": "数码", "food": "食品"}
    sections = [
        f"{labels.get(category, category)}（{'、'.join(sorted(values))}）"
        for category, values in sorted(grouped.items())
    ]
    pc_display = {
        "pc_cpu": "CPU", "pc_gpu": "显卡", "pc_motherboard": "主板", "pc_memory": "内存",
        "pc_storage": "固态硬盘", "pc_psu": "电源", "pc_case": "机箱", "pc_cooler": "散热器",
    }
    if pc_categories:
        sections.append(f"PC 配件（{'、'.join(pc_display.get(item, item) for item in sorted(pc_categories))}）")
    return "当前目录能力表（仅用于判断覆盖范围，不是商品事实）：" + "；".join(sections) + "。目录外商品仍按推荐意图输出，由后续目录校验处理。"


def _decode_observation(payload: Mapping[str, Any]) -> SemanticObservation:
    action_raw = str(payload.get("action") or "").strip()
    action = _ACTION_NAMES.get(action_raw)
    if action is None:
        raise ValueError("semantic action is not allowed")
    commerce_intent = _COMMERCE_INTENT_NAMES.get(str(payload.get("commerce_intent") or "none").strip())
    if commerce_intent is None:
        raise ValueError("semantic commerce_intent is not allowed")
    pc_operation = _PC_OPERATION_NAMES.get(str(payload.get("pc_operation") or "").strip())
    pc_reference = _PC_REFERENCE_NAMES.get(str(payload.get("pc_plan_reference") or "").strip())
    computer_purchase_kind_raw = str(payload.get("computer_purchase_kind") or "").strip()
    computer_purchase_kind = None
    if computer_purchase_kind_raw:
        computer_purchase_kind = _COMPUTER_PURCHASE_KIND_NAMES.get(computer_purchase_kind_raw)
        if computer_purchase_kind is None:
            raise ValueError("semantic computer_purchase_kind is not allowed")
    query_kind = _optional_text(payload.get("query_kind"))
    if action is V3Action.PARAMETER_QUERY and query_kind not in _QUERY_KINDS:
        # Missing fact fields are a clarification problem, not an excuse to
        # reject the entire request. PromotionGate will refuse execution.
        query_kind = None
    if action is not V3Action.PARAMETER_QUERY:
        query_kind = None
    price_constraint = _decode_price_constraint(payload.get("price_constraint"))
    rank = payload.get("target_card_rank")
    if rank is not None:
        rank = int(rank)
        if rank <= 0:
            raise ValueError("semantic target_card_rank must be positive")
    cart_rank = payload.get("target_cart_rank")
    if cart_rank is not None:
        cart_rank = int(cart_rank)
        if cart_rank <= 0:
            raise ValueError("semantic target_cart_rank must be positive")
    card_ranks = _positive_int_tuple(payload.get("target_card_ranks"))
    cart_operation = _decode_cart_operation(payload.get("cart_operation"))
    quantity = payload.get("quantity")
    if quantity is not None:
        quantity = int(quantity)
        if quantity <= 0:
            raise ValueError("semantic quantity must be positive")
    return SemanticObservation(
        action=action,
        commerce_intent=commerce_intent,
        target_type_surface=_optional_text(payload.get("target_type_surface")),
        target_type_candidate_id=_optional_text(payload.get("target_type_candidate_id")),
        target_type_evidence=_decode_type_evidence(payload.get("target_type_evidence")),
        exclude_type_candidate_ids=_string_tuple(payload.get("exclude_type_candidate_ids")),
        exclude_type_evidences=_type_evidence_tuple(payload.get("exclude_type_evidences")),
        include_brand_surfaces=_string_tuple(payload.get("include_brand_surfaces")),
        exclude_brand_surfaces=_string_tuple(payload.get("exclude_brand_surfaces")),
        price_max=price_constraint.amount if price_constraint and price_constraint.kind is PriceKind.MAX else None,
        price_constraint=price_constraint,
        desired_attribute_surfaces=_string_tuple(payload.get("desired_attribute_surfaces")),
        target_card_rank=rank,
        target_card_ranks=card_ranks,
        target_cart_rank=cart_rank,
        query_kind=query_kind,
        cart_operation=cart_operation,
        quantity=quantity,
        pc_usage_surfaces=_string_tuple(payload.get("pc_usage_surfaces")),
        pc_operation=pc_operation,
        pc_plan_reference=pc_reference,
        pc_component_category_surface=_optional_text(payload.get("pc_component_category_surface")),
        upgrade_direction=_optional_text(payload.get("upgrade_direction")),
        computer_purchase_kind=computer_purchase_kind,
        computer_purchase_evidence=_decode_purchase_kind_evidence(payload.get("computer_purchase_evidence")),
        missing_fields=_string_tuple(payload.get("missing_fields")),
    )


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _decode_cart_operation(value: Any):
    from .types import CartOperation

    raw = str(value or "").strip()
    return CartOperation(raw) if raw in {item.value for item in CartOperation} else None


def _positive_int_tuple(value: Any) -> tuple[int, ...]:
    if not isinstance(value, list):
        return ()
    result = tuple(dict.fromkeys(int(item) for item in value))
    if any(item <= 0 for item in result):
        raise ValueError("semantic target_card_ranks must be positive")
    return result


def _decode_price_constraint(value: Any) -> PriceConstraint | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("semantic price_constraint must be an object")
    kind = PriceKind(str(value.get("kind") or ""))
    amount = float(value["amount"])
    minimum = float(value["min_amount"]) if value.get("min_amount") is not None else None
    start = int(value["evidence_start"])
    end = int(value["evidence_end"])
    evidence_text = str(value.get("evidence_text") or "")
    if amount <= 0 or (minimum is not None and (minimum <= 0 or minimum > amount)):
        raise ValueError("semantic price amounts are invalid")
    if start < 0 or end <= start or not evidence_text:
        raise ValueError("semantic price evidence is invalid")
    if kind is PriceKind.RANGE and minimum is None:
        raise ValueError("semantic range requires min_amount")
    return PriceConstraint(kind, amount, minimum, str(value.get("currency") or "CNY"), start, end, evidence_text)


def _decode_type_evidence(value: Any) -> TypeSurfaceEvidence | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("type evidence must be an object")
    surface = _optional_text(value.get("surface"))
    evidence_text = _optional_text(value.get("evidence_text"))
    if surface is None or evidence_text is None:
        raise ValueError("type evidence requires surface and evidence_text")
    start = int(value["evidence_start"])
    end = int(value["evidence_end"])
    if start < 0 or end <= start:
        raise ValueError("type evidence range is invalid")
    return TypeSurfaceEvidence(surface, start, end, evidence_text)


def _decode_purchase_kind_evidence(value: Any) -> PurchaseKindEvidence | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("computer purchase evidence must be an object")
    surface = _optional_text(value.get("surface"))
    evidence_text = _optional_text(value.get("evidence_text"))
    if surface is None or evidence_text is None:
        raise ValueError("computer purchase evidence requires surface and evidence_text")
    start = int(value["evidence_start"])
    end = int(value["evidence_end"])
    if start < 0 or end <= start:
        raise ValueError("computer purchase evidence range is invalid")
    return PurchaseKindEvidence(surface, start, end, evidence_text)


def _type_evidence_tuple(value: Any) -> tuple[TypeSurfaceEvidence, ...]:
    if not isinstance(value, list):
        return ()
    decoded = tuple(_decode_type_evidence(item) for item in value)
    return tuple(item for item in decoded if item is not None)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
