"""Graph-style orchestration for the ecommerce recommendation workflow.

The older RAG pipeline already proved useful as a node-based workflow. This
module applies the same shape to the guided-selling path without making the LLM
the decision maker: parsing, evidence retrieval, scoring, package building, and
guidance remain explicit nodes with streamable events.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

from rag.recommendation.intent_router import route_shopping_intent
from rag.recommendation.package_builder import MILVUS_RETRIEVAL_ENABLED, build_recommendation_result
from rag.recommendation.product_loader import ProductCatalog, load_product_catalog
from rag.recommendation.recommendation_pipeline import (
    enrich_recommendation_result,
    parse_requirement,
    validate_business_goal,
)
from rag.schemas import RecommendationResult, RequirementSpec

# Guidance is optional because the main recommendation path must remain usable
# even when no generation model is configured.
LLM_GUIDANCE_ENABLED = os.getenv("RECOMMENDATION_LLM_GUIDANCE", "false").lower() == "true"


@dataclass(frozen=True)
class RecommendationGraphEvent:
    event: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RecommendationGraphState:
    goal: str
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    use_llm: bool = True
    use_guidance_llm: bool = False
    use_milvus_retrieval: bool = True
    use_rag_query_expansion: bool = False
    requirement: Optional[RequirementSpec] = None
    catalog: Optional[ProductCatalog] = None
    catalog_count: int = 0
    result: Optional[RecommendationResult] = None


class RecommendationGraph:
    def stream(
        self,
        goal: str,
        *,
        attachments: Optional[List[Dict[str, Any]]] = None,
        use_llm: bool = True,
        use_guidance_llm: bool = False,
        use_milvus_retrieval: bool = True,
        use_rag_query_expansion: bool = False,
    ) -> Iterator[RecommendationGraphEvent]:
        state = RecommendationGraphState(
            goal=goal,
            attachments=list(attachments or []),
            use_llm=use_llm,
            use_guidance_llm=use_guidance_llm,
            use_milvus_retrieval=use_milvus_retrieval,
            use_rag_query_expansion=use_rag_query_expansion,
        )
        yield from self._validate_goal(state)
        yield from self._parse_requirement(state)
        yield from self._load_catalog(state)
        yield from self._build_plans(state)
        yield from self._enrich_guidance(state)
        if state.result is not None:
            yield RecommendationGraphEvent("result", _model_to_dict(state.result))
        yield RecommendationGraphEvent("done", {"label": "推荐完成"})

    def _validate_goal(self, state: RecommendationGraphState) -> Iterator[RecommendationGraphEvent]:
        yield _step("正在校验业务目标", state.goal)
        validate_business_goal(state.goal)
        if state.attachments:
            yield _step(
                "已接收附件输入",
                {
                    "count": len(state.attachments),
                    "types": sorted({item.get("type") or "unknown" for item in state.attachments}),
                    "names": [item.get("name") for item in state.attachments[:4]],
                },
            )

    def _parse_requirement(self, state: RecommendationGraphState) -> Iterator[RecommendationGraphEvent]:
        yield _step(
            "正在解析需求",
            "调用生成式模型增强解析" if state.use_llm else "使用规则解析",
        )
        state.requirement = parse_requirement(state.goal, use_llm=state.use_llm)
        yield RecommendationGraphEvent("requirement", _model_to_dict(state.requirement))
        route = route_shopping_intent(state.requirement)
        yield RecommendationGraphEvent("intent_route", route)
        if state.requirement.missing_fields:
            yield _step("发现需要追问的字段", state.requirement.missing_fields)
        yield _step(
            "需求解析完成",
            {
                "scenario": state.requirement.scenario,
                "task_type": route.get("task_type") or state.requirement.task_type,
                "intent_route": route.get("route"),
                "required_components": [item.value for item in state.requirement.required_components],
            },
        )

    def _load_catalog(self, state: RecommendationGraphState) -> Iterator[RecommendationGraphEvent]:
        yield _step("正在加载电商商品库", "读取本地商品、SKU、图片和可检索详情")
        state.catalog = load_product_catalog()
        state.catalog_count = len(state.catalog.products)
        yield RecommendationGraphEvent("catalog", {"candidate_count": state.catalog_count})
        yield _step("商品库加载完成", {"candidate_count": state.catalog_count})

    def _build_plans(self, state: RecommendationGraphState) -> Iterator[RecommendationGraphEvent]:
        if state.requirement is None:
            raise RuntimeError("requirement must be parsed before building plans")
        retrieval_detail = (
            "按商品类目执行 query rewrite、Milvus hybrid retrieval、rerank/auto-merge 后处理"
            if MILVUS_RETRIEVAL_ENABLED
            else "当前跳过 Milvus，使用结构化商品库评分兜底"
        )
        yield _step("正在检索商品详情证据", retrieval_detail)
        catalog = state.catalog or load_product_catalog()
        state.result = build_recommendation_result(
            state.requirement,
            catalog=catalog,
            use_milvus_retrieval=state.use_milvus_retrieval,
            use_rag_query_expansion=state.use_rag_query_expansion,
        )
        retrieval_trace = state.result.trace.get("milvus_retrieval", {})
        yield _step(
            "商品详情证据检索完成",
            {
                "status": retrieval_trace.get("status"),
                "total_hits": retrieval_trace.get("total_hits", 0),
                "matched_product_ids": retrieval_trace.get("matched_product_ids", [])[:8],
                "query_variants": len(retrieval_trace.get("query_variants") or []),
            },
        )
        yield _step(
            "动态权重评分完成",
            state.result.trace.get("dynamic_weight_reasons") or ["使用基础评分权重。"],
        )
        yield _step("正在生成一套购物方案", "低预算、均衡、品质优先")
        yield RecommendationGraphEvent(
            "plans",
            _pick_fields(
                state.result,
                "requirement",
                "plans",
                "product_cards",
                "candidate_scope",
                "comparison_table",
                "intent_route",
                "candidate_count",
                "missing_fields",
                "risks",
                "trace",
            ),
        )

    def _enrich_guidance(self, state: RecommendationGraphState) -> Iterator[RecommendationGraphEvent]:
        if state.result is None:
            raise RuntimeError("plans must be built before guidance")
        yield _step("正在生成导购解释与优化建议", "结合商品、价格、风险和证据")
        state.result = enrich_recommendation_result(
            state.result,
            use_llm=state.use_guidance_llm and LLM_GUIDANCE_ENABLED,
        )
        yield RecommendationGraphEvent(
            "guidance",
            _pick_fields(
                state.result,
                "teaching_guidance",
                "follow_up_questions",
                "optimization_suggestions",
                "feedback_summary",
                "intent_route",
                "comparison_table",
                "trace",
            ),
        )


def stream_recommendation_graph(
    goal: str,
    *,
    attachments: Optional[List[Dict[str, Any]]] = None,
    use_llm: bool = True,
    use_guidance_llm: bool = False,
    use_milvus_retrieval: bool = True,
    use_rag_query_expansion: bool = False,
) -> Iterator[RecommendationGraphEvent]:
    """Convenience wrapper used by the FastAPI SSE endpoint."""

    yield from RecommendationGraph().stream(
        goal,
        attachments=attachments,
        use_llm=use_llm,
        use_guidance_llm=use_guidance_llm,
        use_milvus_retrieval=use_milvus_retrieval,
        use_rag_query_expansion=use_rag_query_expansion,
    )


def _step(label: str, detail: Any = "") -> RecommendationGraphEvent:
    return RecommendationGraphEvent("step", {"label": label, "detail": detail})


def _pick_fields(value: Any, *fields: str) -> Dict[str, Any]:
    payload = _model_to_dict(value)
    return {field: payload.get(field) for field in fields}


def _model_to_dict(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    return value
