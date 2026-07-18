"""Metric definitions for fixed V3 evaluation records.

The functions work on JSON-serializable records emitted by ``runner.py``.  A
metric with no applicable denominator is ``None`` rather than an invented 0 or
100 percent, so a green report cannot hide missing evidence.
"""
from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Any, Iterable


def summarize(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Compute routing, safety, constraint, fact, and engineering metrics."""

    rows = list(records)
    routing_rows = [row for row in rows if row.get("expected_action")]
    shopping_rows = [row for row in rows if row.get("expected_domain") == "shopping"]
    general_rows = [row for row in rows if row.get("expected_domain") == "general"]
    safe_rows = [row for row in rows if row.get("actual_status") == "safe_direct"]
    direct_eligible = [row for row in rows if row.get("safe_direct_policy") != "ignore"]
    ambiguous_rows = [row for row in rows if row.get("expected_outcome") == "clarification"]
    unsupported_rows = [row for row in rows if row.get("expected_reason") == "catalog_scope_unsupported"]
    expected_execution_rows = [row for row in rows if row.get("expected_outcome") in {"recommendation", "fact", "cart_plan", "cart_view", "cart_applied", "cart_cancelled", "pc_plan", "general_chat"}]
    recommendation_rows = [row for row in rows if row.get("expected_outcome") == "recommendation"]
    fact_rows = [row for row in rows if row.get("expected_outcome") == "fact"]
    price_rows = [row for row in recommendation_rows + fact_rows if row.get("fact_checks", {}).get("price_checked")]
    sku_rows = [row for row in recommendation_rows + fact_rows if row.get("fact_checks", {}).get("sku_checked")]
    stock_rows = [row for row in fact_rows if row.get("fact_checks", {}).get("stock_checked")]
    constraint_rows = [row for row in rows if row.get("constraint_expected")]
    token_rows = [row for row in rows if row.get("llm_calls", 0) > 0]
    semantic_rows = [row for row in rows if row.get("semantic_parse_called")]
    retrieval_rows = [row for row in recommendation_rows if row.get("candidate_allowlist_nonempty")]
    first_event = [row["first_event_ms"] for row in rows if isinstance(row.get("first_event_ms"), int)]
    first_business_event = [row["first_business_event_ms"] for row in rows if isinstance(row.get("first_business_event_ms"), int)]
    total_latency = [row["total_ms"] for row in rows if isinstance(row.get("total_ms"), int)]

    by_constraint: dict[str, dict[str, int | float | None]] = {}
    for field in ("include_brand", "exclude_brand", "brand_release", "price_max", "price_min", "price_target", "product_type", "exclude_type", "recommendation_mode", "multi_category", "quantity", "card_reference", "comparison_reference", "computer_purchase_kind"):
        applicable = [row for row in constraint_rows if field in row.get("constraint_expected", {})]
        by_constraint[field] = _rate(sum(bool(row.get("constraint_checks", {}).get(field)) for row in applicable), len(applicable))

    return {
        "case_count": len(rows),
        "pass_rate": _rate(sum(bool(row.get("passed")) for row in rows), len(rows)),
        "routing": {
            "intent_accuracy": _rate(sum(bool(row.get("route_correct")) for row in routing_rows), len(routing_rows)),
            "unsupported_request_recall": _rate(sum(row.get("actual_reason") == "catalog_scope_unsupported" for row in unsupported_rows), len(unsupported_rows)),
            "general_misrouted_as_shopping_rate": _rate(sum(row.get("actual_action") != "general_chat" for row in general_rows), len(general_rows)),
            "shopping_misrouted_as_general_rate": _rate(sum(row.get("actual_action") == "general_chat" for row in shopping_rows), len(shopping_rows)),
        },
        "local_routing": {
            "false_accept_rate": _rate(sum(not bool(row.get("safe_direct_correct")) for row in safe_rows), len(safe_rows)),
            "safe_direct_coverage": _rate(len(safe_rows), len(rows)),
            "safe_direct_eligible_coverage": _rate(sum(row.get("actual_status") == "safe_direct" for row in direct_eligible), len(direct_eligible)),
            "semantic_llm_fallback_rate": _rate(sum(bool(row.get("semantic_parse_called")) for row in rows), len(rows)),
            "wrong_rejection_rate": _rate(sum(bool(row.get("actual_error")) for row in expected_execution_rows), len(expected_execution_rows)),
            "ambiguous_query_clarification_rate": _rate(sum(bool(row.get("actual_clarification")) for row in ambiguous_rows), len(ambiguous_rows)),
        },
        "constraints": by_constraint,
        "facts": {
            "product_id_validity": _rate(sum(bool(row.get("fact_checks", {}).get("product_ids_valid")) for row in recommendation_rows), len(recommendation_rows)),
            "price_consistency": _rate(sum(bool(row.get("fact_checks", {}).get("price_consistent")) for row in price_rows), len(price_rows)),
            "sku_consistency": _rate(sum(bool(row.get("fact_checks", {}).get("sku_consistent")) for row in sku_rows), len(sku_rows)),
            "stock_consistency": _rate(sum(bool(row.get("fact_checks", {}).get("stock_consistent")) for row in stock_rows), len(stock_rows)),
            "expired_card_misuse_count": sum(int(row.get("expired_card_misuse", 0)) for row in rows),
            "excluded_product_reappearance_rate": _rate(sum(bool(row.get("fact_checks", {}).get("excluded_brand_reappeared")) for row in recommendation_rows), len(recommendation_rows)),
        },
        "engineering": {
            "first_event_ms": _latency(first_event),
            "first_business_event_ms": _latency(first_business_event),
            "total_response_ms": _latency(total_latency),
            "llm_calls_per_request": _average([int(row.get("llm_calls", 0)) for row in rows]),
            "semantic_schema_retry_rate": _rate(sum(bool(row.get("semantic_schema_retry")) for row in semantic_rows), len(semantic_rows)),
            "semantic_retry_repaired_rate": _rate(sum(bool(row.get("semantic_schema_retry")) and row.get("actual_status") != "reject" for row in semantic_rows), sum(bool(row.get("semantic_schema_retry")) for row in semantic_rows)),
            "semantic_max_attempt_count": max((int(row.get("semantic_attempt_count", 0)) for row in semantic_rows), default=0),
            "reported_tokens_per_llm_request": _average([int(row["total_tokens"]) for row in token_rows if isinstance(row.get("total_tokens"), int)]),
            "token_reporting_coverage": _rate(sum(isinstance(row.get("total_tokens"), int) for row in token_rows), len(token_rows)),
            "milvus_failure_fallback_rate": _rate(sum(row.get("retrieval_status") == "unavailable" and bool(row.get("recommendation_returned")) for row in retrieval_rows), sum(row.get("retrieval_status") == "unavailable" for row in retrieval_rows)),
            "redis_failure_recovery": None,
            "concurrent_request_correctness": None,
        },
        "diagnostics": {
            "actual_statuses": dict(sorted(Counter(str(row.get("actual_status") or "missing") for row in rows).items())),
            "actual_actions": dict(sorted(Counter(str(row.get("actual_action") or "missing") for row in rows).items())),
            "redis_failure_recovery_note": "需要带 Redis 故障注入的独立运行；当前普通全链路集不伪造该值。",
            "concurrent_request_correctness_note": "需要运行 final_test/concurrency.py；当前串行集不伪造该值。",
        },
    }


def markdown_report(summary: dict[str, Any], records: Iterable[dict[str, Any]]) -> str:
    """Render a compact human-reviewable report beside the JSON artifact."""

    rows = list(records)
    routing = summary["routing"]
    local = summary["local_routing"]
    facts = summary["facts"]
    engineering = summary["engineering"]
    lines = [
        "# MallMind V3 固定评测报告",
        "",
        f"- 用例数：{summary['case_count']}；通过率：{_percent(summary['pass_rate'])}",
        "- 本报告把 `SAFE_DIRECT` 的错误放行单独统计；不能用普通通过率替代它。",
        "",
        "## 指标定义（分子 / 分母）",
        "",
        "- **统计单位**：一条 record 就是一次实际 HTTP 请求；多轮场景会拆成多条 record。因此本报告的 61 是请求轮数，不是场景数。",
        "- **通过率**：`passed=true` 的请求 / 全部请求。`passed` 同时要求 HTTP 状态、动作、结果类型、fixture 声明的约束、以及已返回目录事实全部符合预期；它是固定集的端到端通过率，不是单纯的模型理解率。",
        "- **Intent Accuracy**：`actual_action == expected_action` 的请求 / 声明了 `expected_action` 的请求；只判断“系统决定做什么”，不判断卡片、价格或购物车是否最终完成。",
        "- **不支持请求召回率**：实际 `reason=catalog_scope_unsupported` 的请求 / fixture 期望 `catalog_scope_unsupported` 的请求。",
        "- **闲聊误判为购物**：期望域为 `general`、实际 action 不是 `general_chat` 的请求 / 全部 general 请求；**购物误判为闲聊**反向计算。",
        "- **False Accept Rate**：实际状态为 `safe_direct` 但未通过该条端到端断言的请求 / 全部 `safe_direct` 请求；它衡量本地规则错误直接放行，越低越好。",
        "- **SAFE_DIRECT 覆盖率**：实际状态为 `safe_direct` 的请求 / 全部请求；**LLM 回退比例**：`semantic_parse_called=true` 的请求 / 全部请求。",
        "- **预期执行请求的 error 事件率**：期望推荐、事实查询、购物车、PC 方案或泛聊，且 SSE 收到非空 `error` 事件的请求 / 全部期望执行请求；澄清不计入此指标。",
        "- **歧义句追问率**：期望 clarification 且实际收到 `clarification` 事件的请求 / 全部期望 clarification 请求。",
        "- **各约束保持率**：fixture 明确声明某字段（品牌、价格、卡片序号、数量等）的请求中，该字段在 RequirementSpecV3、购物车计划或比较结果满足预期的数量 / 声明该字段的请求数；未声明该字段的请求不进入分母。",
        "- **商品 ID 有效率**：推荐卡片中所有 product_id 都能在本次加载的本地目录找到的推荐请求 / 全部期望推荐请求。价格、SKU、库存一致率只统计实际返回了对应事实的请求，并逐项与同次加载的目录比较；没有返回事实的请求由端到端通过率和对应 outcome 断言判定，不会被当作“一致”。",
        "- **首个可显示事件延迟**：发起 HTTP 请求到收到第一个完整 SSE event 的时间；当前通常是无业务结论的 `progress(stage=understanding)`。**首个业务结果延迟**：到首次收到 `clarification`、`error`、`delta`、商品卡/事实、购物车或 PC 结果事件的时间。**总响应延迟**：到流读取结束的时间。三者只统计对应事件存在的请求；mean 为平均值，p50/p95 为样本分位数。",
        "- **LLM 调用次数 / 请求**：每请求的 SemanticParse 调用（通常 0/1；仅 schema 不合法时最多 2）加上回答生成阶段 `model_usage` 事件数，再对所有请求取平均。`schema retry` 只统计第一次已返回 JSON 但 decoder 拒绝、随后允许一次修复调用的请求；网络错误和超时不计入。平均 token 只统计服务商实际回报 `total_tokens` 的调用；token 覆盖率是有该数值的调用 / 全部实际 LLM 调用。",
        "- **N/A**：该批次没有适用分母。例如 Milvus 故障降级必须先注入 Milvus 故障；普通成功运行不能伪造此指标。",
        "",
        "## 路由与安全",
        "",
        "| 指标 | 结果 |",
        "| --- | --- |",
        f"| Intent Accuracy | {_percent(routing['intent_accuracy'])} |",
        f"| 不支持请求召回率 | {_percent(routing['unsupported_request_recall'])} |",
        f"| 闲聊误判为购物 | {_percent(routing['general_misrouted_as_shopping_rate'])} |",
        f"| 购物误判为闲聊 | {_percent(routing['shopping_misrouted_as_general_rate'])} |",
        f"| False Accept Rate（越低越好） | {_percent(local['false_accept_rate'])} |",
        f"| SAFE_DIRECT 覆盖率 | {_percent(local['safe_direct_coverage'])} |",
        f"| LLM 回退比例 | {_percent(local['semantic_llm_fallback_rate'])} |",
        f"| 预期执行请求的 error 事件率 | {_percent(local['wrong_rejection_rate'])} |",
        f"| 歧义句追问率 | {_percent(local['ambiguous_query_clarification_rate'])} |",
        "",
        "## 约束与事实",
        "",
        "| 指标 | 结果 |",
        "| --- | --- |",
    ]
    lines.extend(f"| {name} 保持率 | {_percent(value)} |" for name, value in summary["constraints"].items())
    lines.extend([
        f"| 商品 ID 有效率 | {_percent(facts['product_id_validity'])} |",
        f"| 价格一致率 | {_percent(facts['price_consistency'])} |",
        f"| SKU 一致率 | {_percent(facts['sku_consistency'])} |",
        f"| 库存一致率 | {_percent(facts['stock_consistency'])} |",
        f"| 过期卡片误使用次数 | {facts['expired_card_misuse_count']} |",
        f"| 被排除品牌重新出现率（越低越好） | {_percent(facts['excluded_product_reappearance_rate'])} |",
        "",
        "## 工程指标",
        "",
        "| 指标 | 结果 |",
        "| --- | --- |",
        f"| 首个可显示事件延迟 | {_latency_text(engineering['first_event_ms'])} |",
        f"| 首个业务结果延迟 | {_latency_text(engineering['first_business_event_ms'])} |",
        f"| 总响应延迟 | {_latency_text(engineering['total_response_ms'])} |",
        f"| LLM 调用次数 / 请求 | {_number(engineering['llm_calls_per_request'])} |",
        f"| SemanticParse schema 重试率 | {_percent(engineering['semantic_schema_retry_rate'])} |",
        f"| 重试后恢复可执行率 | {_percent(engineering['semantic_retry_repaired_rate'])} |",
        f"| 单请求 SemanticParse 最大尝试次数 | {engineering['semantic_max_attempt_count']} |",
        f"| 有 token 回报的 LLM 请求平均 token | {_number(engineering['reported_tokens_per_llm_request'])} |",
        f"| token 回报覆盖率 | {_percent(engineering['token_reporting_coverage'])} |",
        f"| Milvus 故障时目录排序降级成功率 | {_percent(engineering['milvus_failure_fallback_rate'])} |",
        "| Redis 故障恢复 | 需单独运行故障注入，普通集不填充 |",
        "| 并发请求正确率 | 需单独运行 `concurrency.py`，普通集不填充 |",
        "",
        "## 失败用例",
        "",
    ])
    failed = [row for row in rows if not row.get("passed")]
    if not failed:
        lines.append("无。")
    else:
        lines.extend(f"- `{row['case_id']}/{row['turn_id']}`：{row.get('failure_reason') or '不符合固定期望'}" for row in failed)
    return "\n".join(lines) + "\n"


def _rate(numerator: int | bool, denominator: int | bool) -> float | None:
    return round(float(numerator) / int(denominator), 6) if int(denominator) else None


def _average(values: list[int]) -> float | None:
    return round(float(mean(values)), 3) if values else None


def _latency(values: list[int]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {"mean": round(float(mean(values)), 3), "p50": float(_percentile(ordered, 0.5)), "p95": float(_percentile(ordered, 0.95))}


def _percentile(values: list[int], ratio: float) -> int:
    index = min(len(values) - 1, max(0, round((len(values) - 1) * ratio)))
    return values[index]


def _percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.2f}%"


def _number(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def _latency_text(value: dict[str, float] | None) -> str:
    if value is None:
        return "N/A"
    return f"mean {value['mean']:.1f} ms / p50 {value['p50']:.0f} ms / p95 {value['p95']:.0f} ms"
