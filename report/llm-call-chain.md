# LLM 调用链路全景文档

> 本文档详尽记录了推荐导购系统中 **所有 LLM（大语言模型）调用路径**，包括调用入口、触发条件、输入输出格式、参数配置、降级策略和下游消费者。
>
> 最后更新：2026-06-13

---

## 目录

1. [架构总览](#1-架构总览)
2. [LLMGateway 核心架构](#2-llmgateway-核心架构)
3. [9 大注册场景配置表](#3-9-大注册场景配置表)
4. [LLM 调用站点详细文档](#4-llm-调用站点详细文档)
   - 4.1 [router — 工具路由](#41-router--工具路由)
   - 4.2 [parse — 需求解析](#42-parse--需求解析)
   - 4.3 [guidance — 导购引导](#43-guidance--导购引导)
   - 4.4 [response — 自然语言响应生成](#44-response--自然语言响应生成)
   - 4.5 [explanation — 证据锚定解释](#45-explanation--证据锚定解释)
   - 4.6 [rewrite — 多轮查询改写](#46-rewrite--多轮查询改写)
   - 4.7 [general_chat — 闲聊/系统说明](#47-general_chat--闲聊系统说明)
   - 4.8 [filter — 语义商品筛选](#48-filter--语义商品筛选)
   - 4.9 [attachment — 图片/VLM 视觉分析](#49-attachment--图片vlm-视觉分析)
5. [未迁移到 Gateway 的直接调用](#5-未迁移到-gateway-的直接调用)
6. [无 LLM 调用的模块说明](#6-无-llm-调用的模块说明)
7. [端到端调用链路图](#7-端到端调用链路图)
8. [模型解析策略](#8-模型解析策略)
9. [全局环境变量清单](#9-全局环境变量清单)

---

## 1. 架构总览

系统采用 **LLM-first, rule-fallback** 策略：在每条用户消息的处理链路中，多个环节会调用大模型，每个环节都有独立的规则降级方案。

```
用户消息 (POST /api/chat/stream)
  │
  ├─── [LLM①] 工具路由 (tool_router.py → try_llm_route_tool_call)
  │     ├── 成功 → 分发到对应 handler
  │     └── 失败 → 本地规则路由 (local_route_tool_call)
  │
  ├─── [LLM②] 图片附件分析 (attachments.py → analyze_image_attachment)
  │     └── 仅在 use_vision_llm=true 且图片有 payload 时触发
  │
  ├─── handler: general_chat
  │     └── [LLM③] 闲聊回复 (tool_handlers.py → _generate_general_chat_llm_response)
  │
  ├─── handler: recommend_shopping_products
  │     ├── [LLM④] 需求解析 (recommendation_pipeline.py → parse_requirement)
  │     ├── [LLM⑤] 语义筛选 (structured_filter.py → _llm_filter_products)
  │     ├── [LLM⑥] 导购引导 (recommendation_pipeline.py → enrich_recommendation_result)
  │     ├── [LLM⑦] 证据解释 (explanation_builder.py → build_evidence_grounded_explanation)
  │     ├── [LLM⑧] 响应生成 (response_generator.py → _llm_diverse_response)
  │     └── [LLM⑨] 查询改写 (query_rewriter.py → _llm_rewrite) — 在路由前执行
  │
  └─── handler: generate_pc_build_plan
        └── 纯规则，无 LLM 调用
```

**LLM 调用总计：9 个独立调用站点**，对应 LLMGateway 的 9 个注册场景。

---

## 2. LLMGateway 核心架构

**源文件**: `rag/recommendation/llm_gateway.py`

LLMGateway 是所有 LLM 调用的统一编排层，提供以下核心能力：

### 2.1 熔断器 (Circuit Breaker)

```python
@dataclass
class _CircuitState:
    consecutive_failures: int = 0
    half_open_until: float = 0.0
    state: str = "closed"  # closed | open | half-open

    _FAILURE_THRESHOLD: int = 5          # 连续 5 次失败触发熔断
    _OPEN_DURATION_SECONDS: float = 30.0 # 熔断持续 30 秒
```

**状态机**:
- `closed`（正常）→ 连续 5 次失败 → `open`（熔断）
- `open` → 30 秒后自动进入 → `half-open`（半开）
- `half-open` → 一次成功 → `closed`；一次失败 → 回到 `open`

**触发行为**: 熔断器打开时，`LLMGateway.call()` 立即抛出 `LLMClientError`（`config_error_code="circuit_open"`），不发起网络请求。

### 2.2 并发限制器 (Concurrency Limiter)

```python
class _ConcurrencyLimiter:
    def __init__(self, name: str, max_concurrency: int):
        self._sem = threading.Semaphore(max_concurrency)

    def acquire(self, timeout: float = 0) -> bool:
        return self._sem.acquire(timeout=timeout)
```

- 每个 caller 场景有独立的信号量
- 获取超时时间 = `min(timeout, 2.0)` 秒
- 获取失败时抛出 `LLMClientError`（`config_error_code="concurrency_limit"`）

### 2.3 硬超时 (Hard Timeout)

```python
def run_with_hard_timeout(callback, timeout_seconds, label):
    """在独立线程中执行 LLM 调用，超时则抛 LLMClientError。"""
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    result_queue.get(timeout=max(float(timeout_seconds), 0.1))
```

- 使用 `threading.Thread` + `queue.Queue` 实现
- 超时后守护线程仍在运行（不中断 HTTP 请求），但主线程已返回
- 超时抛出 `LLMClientError`，report 中 `elapsed_ms = timeout_seconds * 1000`

### 2.4 调用流程

```
LLMGateway.call(caller_name, messages)
  │
  ├── 1. 查找注册配置 (未注册则用默认值自动注册)
  ├── 2. 检查熔断器 → 打开则抛 circuit_open
  ├── 3. 获取并发信号量 → 超时则抛 concurrency_limit
  ├── 4. 创建 OpenAICompatibleChatClient
  │     └── 未配置 → 抛 not_configured
  ├── 5. 解析模型 (fast/main → 实际模型名)
  ├── 6. 执行 chat_json_with_report / chat_text
  │     └── 包裹在 run_with_hard_timeout 中
  ├── 7. 成功 → record_success + record_log
  └── 8. 失败 → record_failure + record_log + re-raise
```

### 2.5 可观测性

- `_call_log`: 线程安全的最近 100 条调用记录（caller、success、elapsed_ms、error_code、timestamp）
- `get_call_log()`: 返回调用日志副本
- `reset()`: 清空所有状态（主要用于测试）

---

## 3. 9 大注册场景配置表

**注册位置**: `rag/recommendation/llm_gateway.py` → `_register_defaults()`

| # | caller_name | model_kind | temperature | timeout (s) | max_tokens | max_concurrency | 用途 |
|---|-------------|------------|-------------|-------------|------------|-----------------|------|
| 1 | `router` | fast | 0.0 | 15 | 320 | 5 | 工具路由：选择 handler 并提取参数 |
| 2 | `parse` | fast | 0.1 | 12 | 1200 | 5 | 需求解析：从自然语言抽取结构化约束 |
| 3 | `guidance` | main | 0.2 | 8 | 1500 | 5 | 导购引导：生成教学建议和追问 |
| 4 | `response` | main | 0.9 | 5 | 200 | 5 | 响应生成：自然语言多样化回复 |
| 5 | `explanation` | main | 0.1 | 8 | 1500 | 5 | 证据解释：基于商品数据生成推荐理由 |
| 6 | `rewrite` | fast | 0.1 | 8 | 600 | 5 | 查询改写：多轮对话上下文消解 |
| 7 | `general_chat` | main | 0.7 | 8 | 200 | 10 | 闲聊回复：非购物问题的自然回复 |
| 8 | `filter` | fast | 0.0 | 12 | 500 | 5 | 语义筛选：品牌排除等软约束过滤 |
| 9 | `attachment` | main | 0.1 | 15 | 800 | 3 | 图片分析：VLM 视觉理解 |

**模型类型解析**:
- `fast` → `os.getenv("MALLMIND_ROUTER_MODEL")` 或 `client.config.fast_model`
- `main` → `os.getenv("MALLMIND_GUIDANCE_MODEL")` 或 `client.config.model`

---

## 4. LLM 调用站点详细文档

### 4.1 router — 工具路由

| 项目 | 详情 |
|------|------|
| **Caller name** | `router`（Gateway 注册名），但 **当前实际未走 Gateway**，使用直接客户端调用 |
| **源文件** | `rag/recommendation/tool_router.py` |
| **函数** | `try_llm_route_tool_call(message, session)` |
| **Gateway 迁移状态** | **未迁移** — 使用独立熔断器和信号量 |

**触发条件**:
1. `use_llm=True`（来自 `stream_llm_enabled()`）
2. 全局开关 `MALLMIND_LLM_ENABLED` 为 true（默认 true）
3. 独立熔断器未打开
4. 并发信号量可获取（`RECOMMENDATION_ROUTER_LLM_MAX_CONCURRENCY`，默认 2）

**输入格式**:
```
System: "你是电商导购系统的工具路由器。根据用户输入选择正确的工具并提取参数，输出严格 JSON。..."
User:   "Accumulated state: {...}\nRecent queries: ...\nChat topic: ...\nUser: {message}"
```

系统提示词包含：
- 8 种工具定义（recommend_shopping_products, generate_pc_build_plan, compare_products, apply_cart_instruction, general_chat, parameter_query, sku_detail, price_comparison）
- 推荐模式选择规则（单品/组合/PC整机）
- 话题切换判断规则
- 完整输出 JSON Schema
- category/sub_category 枚举值表
- brands 规则和价格规则
- 3 个 few-shot 示例

用户提示词包含：
- Accumulated state（session.current 累积状态）
- Recent queries（最近 3 轮查询）
- Chat topic（聊天主题）
- topic_memory 上下文（PC装机/商品推荐）
- Cart 状态（购物车商品列表）
- 用户原始消息（截断 500 字符）

**输出格式**:
```json
{
  "name": "recommend_shopping_products",
  "arguments": {
    "query": "用户原始输入",
    "category": "digital",
    "sub_category": "笔记本电脑",
    "catalog_scope": "ecommerce",
    "brands": [],
    "exclude_brands": [],
    "price_min": null,
    "price_max": 5000,
    "budget": 5000,
    "is_explicit_budget": true,
    "must_have_terms": ["轻薄"],
    "sort_order": null,
    "action": "",
    "product_ids": [],
    "product_mentions": [],
    "attribute": "",
    "sku_criteria": "",
    "quantity": null,
    "compare_with_previous": false,
    "usage": [],
    "preferences": {},
    "topic": "",
    "need_full_pc_build": false
  },
  "source": "llm"
}
```

经 `RoutedToolCall`（Pydantic）校验后返回。

**参数**:
| 参数 | 值 | 来源 |
|------|------|------|
| model | `MALLMIND_ROUTER_MODEL` 或 `client.config.fast_model` | 环境变量 / provider 配置 |
| temperature | 0 | 硬编码 |
| max_tokens | `RECOMMENDATION_ROUTER_LLM_MAX_TOKENS`（默认 320） | 环境变量 |
| timeout | `RECOMMENDATION_LLM_ROUTER_TIMEOUT_SECONDS`（默认 15.0） | 环境变量 |
| socket_timeout | `RECOMMENDATION_ROUTER_LLM_SOCKET_TIMEOUT_SECONDS`（默认 15.0） | 环境变量 |

**独立熔断器**:
- 失败阈值: `RECOMMENDATION_ROUTER_LLM_CIRCUIT_FAILURES`（默认 5）
- 冷却时间: `RECOMMENDATION_ROUTER_LLM_CIRCUIT_COOLDOWN_SECONDS`（默认 30.0）
- 统计窗口: 60 秒内的失败次数

**降级行为**:
- LLM 失败 → 回退到 `local_route_tool_call()`（纯规则路由）
- 降级原因记录在 `routing_trace.llm_router_failure_reason`
- 最终来源标记为 `router_final_source: "rules_fallback"`

**下游消费者**: `chat_stream()` 在 `rag/api/routes/chat.py` 中调用 `route_shopping_tool_call()` → 分发到对应 handler

---

### 4.2 parse — 需求解析

| 项目 | 详情 |
|------|------|
| **Caller name** | `parse`（Gateway 注册名），但 **当前实际未走 Gateway**，使用直接客户端调用 |
| **源文件** | `rag/recommendation/recommendation_pipeline.py` |
| **函数** | `parse_requirement(user_goal, use_llm, skip_keyword_check)` |
| **Gateway 迁移状态** | **未迁移** — 直接使用 `OpenAICompatibleChatClient` |

**触发条件**:
1. `use_llm=True`
2. `should_use_llm_requirement_parse()` 返回 true（auto 模式下：规则解析不充分 且 包含复杂场景词）
3. `OpenAICompatibleChatClient` 已配置

**auto 模式决策逻辑**（`RECOMMENDATION_LLM_PARSE`，默认 `auto`）:
- `off/disabled/never` → 不启用 LLM
- `on/enabled/always` → 总是启用
- `auto` → 满足以下任一条件时启用：
  - 需要 bundle 或 multimodal
  - 不是简单的品类/品牌查询
  - 包含复杂词（"适合"、"送礼"、"学生"、"通勤"、"性价比"等）
  - 有缺失字段且不是 category

**输入格式**:
```
System: "你是传统电商 AI 导购的需求理解器。只输出 JSON，不要解释。"
User:   结构化 prompt（见 build_requirement_prompt()）
```

用户提示词包含：
- 用户原始购物需求
- 规则解析初稿（JSON）
- 输出 JSON Schema（限定字段枚举）
- category 枚举值、budget_level 枚举值
- 4 条约束规则（不编造、套装处理、排除处理等）

**输出格式** (JSON):
```json
{
  "scenario": "shopping/general/skin_care/...",
  "task_type": "single_product_recommendation/bundle_recommendation/comparison/cart_action",
  "desired_categories": ["beauty", "digital"],
  "target_sub_categories": ["蓝牙耳机"],
  "brands": ["华为"],
  "excluded_brands": [],
  "must_have_terms": ["降噪"],
  "excluded_terms": [],
  "preferences": ["轻量"],
  "price_min": null,
  "price_max": 500,
  "budget_level": "medium",
  "need_bundle": false,
  "need_comparison": false,
  "need_cart_action": false,
  "need_multimodal": false,
  "missing_fields": [],
  "assumptions": [],
  "clarification_question": ""
}
```

**参数**:
| 参数 | 值 | 来源 |
|------|------|------|
| model | `MALLMIND_PARSE_MODEL` 或 `client.config.fast_model` | 环境变量 |
| temperature | 0.1 | 硬编码 |
| max_tokens | 1200 | 硬编码 |
| timeout | `RECOMMENDATION_LLM_PARSE_TIMEOUT_SECONDS`（默认 12.0） | 环境变量 |

**降级行为**:
- 超时 → `TimeoutError` → 回退到规则解析，assumptions 追加"生成式大模型需求解析超时，已降级为规则解析。"
- 客户端/网络错误 → 回退到规则解析
- JSON 解析失败 → 回退到规则解析
- 所有降级信息记录在 `_parse_trace`（线程安全）和 `requirement.assumptions`

**下游消费者**: `recommend_shopping_products()` → 生成 `RequirementSpec` → 传递给 `build_recommendation_result()` 进行商品筛选和评分

---

### 4.3 guidance — 导购引导

| 项目 | 详情 |
|------|------|
| **Caller name** | `guidance`（Gateway 注册名），但 **当前实际未走 Gateway**，使用直接客户端调用 |
| **源文件** | `rag/recommendation/recommendation_pipeline.py` |
| **函数** | `enrich_recommendation_result(result, use_llm)` |
| **Gateway 迁移状态** | **未迁移** |

**触发条件**:
1. `use_llm=True` 且 `should_use_llm_guidance()` 返回 true
2. `RECOMMENDATION_LLM_GUIDANCE` 环境变量为 true（**默认 false**）
3. 用户消息包含特定词（"详细解释"、"完整分析"、"购买建议"、"为什么推荐"、"解释一下"、"详细分析"）
4. 客户端已配置

**输入格式**:
```
System: "你是谨慎的传统电商导购助手，只输出 JSON。"
User:   build_guidance_prompt(result)
```

用户提示词包含：
- 结构化需求（requirement）
- 推荐方案（plans）含商品列表、价格、推荐理由
- 追问提示（clarification_question 注入）
- 输出 JSON Schema

**输出格式** (JSON):
```json
{
  "teaching_guidance": ["说明推荐依据和如何避免幻觉"],
  "follow_up_questions": ["围绕预算、品牌、否定条件、场景追问"],
  "optimization_suggestions": ["后端和 Android 体验优化建议"]
}
```

**参数**:
| 参数 | 值 | 来源 |
|------|------|------|
| model | `MALLMIND_GUIDANCE_MODEL` 或 `client.config.model` | 环境变量 |
| temperature | 0.2 | 硬编码 |
| max_tokens | 1500 | 硬编码 |
| timeout | `RECOMMENDATION_LLM_GUIDANCE_TIMEOUT_SECONDS`（默认 8.0） | 环境变量 |

**降级行为**:
- 任何失败 → 回退到 `build_rule_based_guidance()`（模板化引导）
- 降级原因记录在 `result.trace["llm_guidance_failure_reason"]`
- trace 字段：`llm_guidance_attempted`、`llm_guidance_success`、`llm_guidance`（enabled/disabled/fallback/not_configured）

**下游消费者**: `result.teaching_guidance`、`result.follow_up_questions`、`result.optimization_suggestions` 传递给前端 SSE

---

### 4.4 response — 自然语言响应生成

| 项目 | 详情 |
|------|------|
| **Caller name** | `response`（Gateway 注册名），但 **当前实际未走 Gateway**，使用直接客户端调用 |
| **源文件** | `rag/recommendation/response_generator.py` |
| **函数** | `_llm_diverse_response(payload, message)` |
| **Gateway 迁移状态** | **未迁移** |

**触发条件**:
1. `RECOMMENDATION_RESPONSE_LLM` 环境变量为 true（默认 true）
2. 推荐结果有商品卡片或 PC 方案（0 卡片时短路返回模板）
3. 事实校验未降级（`fact_check.degraded != True`）
4. 客户端已配置

**输入格式**:
```
User only（无 system prompt）:
"你是友好的电商导购助手。根据以下事实数据，用自然、有人情味的语言回复用户。

【用户需求】: {message}
【推荐商品】: {products}
【预算】: {budget}
【无匹配原因】: {no_match}

【约束】:
1. 绝对不能编造商品名称、价格、库存。...
2. 2-3句话，不超过120字。
3. 语气自然，像真人导购...
4. 如果超预算或没有品牌匹配，友好提醒。
5. 如果没有匹配商品，简短说明原因并建议调整条件。

只输出回复文本，不要引号或前缀。"
```

**输出格式**: 纯文本（通过 `chat_json_with_report` 调用，提取 `content`/`text` 字段），截断 300 字符

**参数**:
| 参数 | 值 | 来源 |
|------|------|------|
| model | `RECOMMENDATION_RESPONSE_MODEL` 或 `client.config.fast_model` 或 `client.config.model` | 环境变量 |
| temperature | 0.9 | 硬编码（高随机性保证多样性） |
| max_tokens | 200 | 硬编码 |
| timeout | `RECOMMENDATION_RESPONSE_TIMEOUT_SECONDS`（默认 5.0） | 环境变量 |

**降级行为**:
- 任何异常（TimeoutError、LLMClientError、Exception） → 回退到 `naturalize_response()`（模板变体库随机选择）
- 模板库包含 6 种开头变体、5 种主推变体、4 种结尾变体、3 种无匹配变体

**下游消费者**: `generate_natural_response()` 返回的文本行通过 SSE `delta` 事件发送给前端

---

### 4.5 explanation — 证据锚定解释

| 项目 | 详情 |
|------|------|
| **Caller name** | `explanation`（Gateway 注册名），但 **当前实际未走 Gateway**，使用直接客户端调用 |
| **源文件** | `rag/recommendation/explanation_builder.py` |
| **函数** | `build_evidence_grounded_explanation(...)` |
| **Gateway 迁移状态** | **未迁移** |

**触发条件**:
1. `use_llm=True`（由 `use_llm_explanation` 参数控制，默认跟随 `use_llm_guidance`）
2. 推荐结果有商品卡片或对比表（无则跳过，`mode="skipped"`）
3. 客户端已配置

**输入格式**:
```
System: "Only output strict JSON. Explain only from the provided evidence. Do not invent product facts."
User:   JSON 格式白名单数据
```

用户输入 JSON 结构（`build_llm_explanation_input()` 构建，字段经白名单过滤）:
```json
{
  "user_need": "用户需求文本",
  "parsed_requirement": { "raw_query", "scenario", "desired_categories", "brands", ... },
  "selected_products": [{ "title", "brand", "price", "tags", "best_for", "score_breakdown", ... }],
  "comparison_table": [{ "title", "brand", "price", ... }]
}
```

**输出格式** (JSON，经 `validate_explanation_output()` 校验):
```json
{
  "why_recommended": ["推荐理由列表"],
  "evidence_points": ["证据点列表"],
  "constraint_explanation": "约束解释文本",
  "tradeoff": "取舍说明",
  "caveat": "注意事项"
}
```

**参数**:
| 参数 | 值 | 来源 |
|------|------|------|
| model | `MALLMIND_GUIDANCE_MODEL` 或 `client.config.fast_model` | 环境变量 |
| temperature | 0.1 | 硬编码 |
| max_tokens | 700 | 硬编码（注意：Gateway 注册为 1500，但实际调用使用 700） |
| timeout | 8.0（函数参数 `timeout_seconds`） | 函数默认值 |

**降级行为**:
- 超时 → `mode="fallback"`，`fallback_reason="llm_timeout"` → 使用 `template_explanation()`
- 网络/客户端错误 → `mode="fallback"` → 使用 `template_explanation()`
- JSON 校验失败 → `mode="fallback"` → 使用 `template_explanation()`
- 模板解释基于商品标签、best_for、价格等结构化数据生成

**下游消费者**: `result.feedback_summary["grounded_explanation"]` 和 trace 中的 `explanation_mode`

---

### 4.6 rewrite — 多轮查询改写

| 项目 | 详情 |
|------|------|
| **Caller name** | `rewrite`（Gateway 注册名），但 **当前实际未走 Gateway**，使用直接客户端调用 |
| **源文件** | `rag/recommendation/query_rewriter.py` |
| **函数** | `_llm_rewrite(message, session_current, last_result, last_goal)` |
| **Gateway 迁移状态** | **未迁移** |

**触发条件**（`_needs_llm_rewrite()`）:
1. `use_llm=True`
2. 规则改写未产生有效结果
3. 存在 session 上下文（session_current 或 last_result）
4. 满足以下任一条件：
   - 消息长度 ≤ 10 且包含未消解代词（"这个"、"它"、"第一个"等）
   - 包含约束修改信号（"换成"、"改成"、"不要"、"去掉"、"加上"、"排除"、"除了"、"只要"）

**输入格式**:
```
User only（无 system prompt）:
"你是电商导购查询改写器。根据对话上下文，将用户的追问改写为一个完整、明确的搜索查询...

【对话上下文】
上一轮用户查询: ...
当前品类: ...
当前品牌: ...
当前预算上限: ...
上一轮推荐商品: ...

【用户追问】
{message}

改写要求：
1. 将代词替换为具体商品名称
2. 补全继承的品类、品牌、预算等约束
3. 保留用户本轮新增的条件
4. 只输出一行改写文本"
```

**输出格式**: 纯文本（一行改写后的查询），通过 `chat_completion()` 调用后提取文本

**参数**:
| 参数 | 值 | 来源 |
|------|------|------|
| model | `MALLMIND_REWRITE_MODEL` 或 `client.config.fast_model` | 环境变量 |
| temperature | 0.1 | 硬编码 |
| max_tokens | 200 | 硬编码 |
| timeout | `RECOMMENDATION_QUERY_REWRITE_TIMEOUT_SECONDS`（默认 5.0） | 环境变量 |

**降级行为**:
- 任何异常 → 返回 `None` → `rewrite_query()` 返回原始查询（`mode="no_rewrite_needed"`）
- 仅在改写结果有效（非空、不同于原始、长度大于原始）时才采用

**下游消费者**: `rewrite_query()` 的返回 `RewriteResult.query` 用作后续检索和路由的搜索查询

---

### 4.7 general_chat — 闲聊/系统说明

| 项目 | 详情 |
|------|------|
| **Caller name** | `general_chat`（Gateway 注册名），但 **当前实际未走 Gateway**，使用直接客户端调用 |
| **源文件** | `rag/recommendation/tool_handlers.py` |
| **函数** | `_generate_general_chat_llm_response(query)` |
| **Gateway 迁移状态** | **未迁移** |

**触发条件**:
- 工具路由选择了 `general_chat`（用户消息与购物无关）
- 客户端已配置

**输入格式**:
```
System: "你是一个电商智能导购助手。用户问了一个与具体商品推荐无关的问题，请你用自然、友好、多样的方式回复。
回复规则：
1. 如果是问候，友好回应并简短介绍自己的能力
2. 如果是身份问题，介绍自己是智能导购助手
3. 如果是购物无关的问题，委婉说明自己专注购物领域
4. 如果是感谢或告别，礼貌回应
5. 回复要简短（1-3句话），自然口语化，不要每次都一模一样
直接输出回复文本，不要加引号或前缀。"
User: {query}
```

**输出格式**: 纯文本，通过 `chat_text()` 调用，去除首尾引号，要求长度 > 5 字符

**参数**:
| 参数 | 值 | 来源 |
|------|------|------|
| model | `client.config.model`（未指定特定 env 变量） | provider 默认 |
| temperature | 0.7 | 硬编码 |
| max_tokens | 200 | 硬编码 |
| timeout | 无独立超时控制（使用 client.config.timeout_seconds） | — |

**降级行为**:
- 客户端未配置 → 返回空字符串 → 调用 `_generate_general_chat_fallback()`
- LLM 调用失败 → 返回空字符串 → 调用 `_generate_general_chat_fallback()`
- 回复过短（≤ 5 字符） → 返回空字符串 → 调用模板降级
- 模板降级覆盖：问候、非购物话题、数字/符号、感谢、告别、默认

**下游消费者**: `handle_general_chat()` 中通过 SSE `delta` 事件发送文本给前端

---

### 4.8 filter — 语义商品筛选

| 项目 | 详情 |
|------|------|
| **Caller name** | `filter`（Gateway 注册名），但 **当前实际未走 Gateway**，使用直接客户端调用 |
| **源文件** | `rag/recommendation/structured_filter.py` |
| **函数** | `_llm_filter_products(requirement, candidates)` |
| **Gateway 迁移状态** | **未迁移** |

**触发条件**（`_has_incomplete_fields()`）:
1. 确定性过滤后仍有未解决的软约束
2. 当前仅检测 `requirement.excluded_brands`（排除品牌需要语义理解子品牌/别名）
3. 候选列表非空
4. 客户端已配置

**输入格式**:
```
User only（无 system prompt）:
"你是商品筛选助手。根据用户的筛选条件，判断以下每个商品是否符合条件。

【筛选条件】
- 用户排除品牌: {brands}（包括其子品牌、关联品牌、别名）

【商品列表】
1. ID=xxx | 标题 | 品牌=xxx | ¥价格
2. ...（最多 30 条）

输出 JSON 对象，格式：{"keep": [保留的商品ID列表]}
注意：
- 排除品牌时，其子品牌、关联品牌、贴牌产品也应排除
- 如果无法确定是否属于排除品牌，保留该商品
- 只输出 JSON，不要解释"
```

**输出格式** (JSON):
```json
{ "keep": ["product_id_1", "product_id_3", ...] }
```

**参数**:
| 参数 | 值 | 来源 |
|------|------|------|
| model | `MALLMIND_LLM_FILTER_MODEL` 或 `client.config.fast_model` | 环境变量 |
| temperature | 0.0 | 硬编码 |
| max_tokens | 500 | 硬编码 |
| timeout | `RECOMMENDATION_LLM_FILTER_TIMEOUT_SECONDS`（默认 12.0） | 环境变量 |

**降级行为**:
- 任何异常 → 返回原始候选列表（不过滤任何商品）
- 超过 30 条的商品不参与 LLM 评估，直接保留
- LLM 移除所有商品 → 回退保留全部

**下游消费者**: `filter_products_for_requirement()` 返回的 `FilterDiagnostics.after_llm_count` 记录过滤后数量

---

### 4.9 attachment — 图片/VLM 视觉分析

| 项目 | 详情 |
|------|------|
| **Caller name** | `attachment`（Gateway 注册名），但 **当前实际未走 Gateway**，使用直接客户端调用 |
| **源文件** | `rag/api/attachments.py` |
| **函数** | `analyze_image_attachment(item, raw_bytes, data_url)` |
| **Gateway 迁移状态** | **未迁移** |

**触发条件**:
1. 用户上传了图片附件（有 `data_url` payload）
2. `use_vision_llm=True`（来自 `prepare_recommendation_context`）
3. 图片解码成功且大小 ≤ `MAX_ATTACHMENT_ANALYSIS_BYTES`（默认 6MB）
4. 客户端已配置

**输入格式**（多模态消息）:
```
System: "你是谨慎的电商图片理解助手，只输出合法 JSON。"
User: [
  { "type": "text", "text": "请分析这张用户上传到电商导购系统的图片。..." },
  { "type": "image_url", "image_url": { "url": "data:image/..." } }
]
```

系统提示词要求提取：
- OCR 文本
- 可见商品品类、品牌、型号/SKU
- 颜色款式、价格/预算
- 场景和用户偏好线索
- visual_query_terms（短词数组）
- visual_attributes（结构化对象）

**输出格式** (JSON):
```json
{
  "summary": "图片摘要文本",
  "extracted_text": "OCR 提取文本",
  "signals": ["image_input", "vision_model_called"],
  "shopping_hints": ["导购线索数组"],
  "visual_query_terms": ["服饰运动", "卫衣", "黑色", "连帽"],
  "visual_attributes": {
    "category": "clothing",
    "sub_category": "卫衣",
    "colors": ["黑色"],
    "materials": ["棉"],
    "brand": "",
    "model": "",
    "style": "连帽",
    "scene": "通勤",
    "visible_text": "",
    "budget": ""
  }
}
```

**参数**:
| 参数 | 值 | 来源 |
|------|------|------|
| model | `VISION_MODEL` 或 `MULTIMODAL_MODEL` 或 `client.config.model` | 环境变量 |
| temperature | 0.1 | 硬编码 |
| max_tokens | 800 | 硬编码 |
| timeout | 无独立超时（使用 `client.config.timeout_seconds`，默认 30s） | — |

**降级行为**:
- 客户端未配置 → `analysis_source="vision_model_unconfigured"`，保留元数据
- 视觉模型调用失败 → `analysis_source="vision_model_error"`，summary 记录错误
- 解码失败 → `analysis_source="decode_error"`
- 文件过大 → `analysis_source="too_large"`
- 非图片格式 → `analysis_source="unsupported_file_type"`

**下游消费者**: `goal_with_attachment_context()` 将 `shopping_hints` 和 `visual_query_terms` 注入到需求解析的用户目标中

---

## 5. 未迁移到 Gateway 的直接调用

**关键发现**: 虽然 LLMGateway 注册了 9 个 caller 场景，但 **所有 9 个场景当前仍使用直接客户端调用**（`OpenAICompatibleChatClient`），并未实际通过 `LLMGateway.call()` 发起请求。

| # | 场景 | Gateway 注册 | 实际调用方式 | 独立熔断器 | 独立信号量 |
|---|------|-------------|-------------|-----------|-----------|
| 1 | router | 是 | 直接客户端 | 是（独立实现） | 是（BoundedSemaphore） |
| 2 | parse | 是 | 直接客户端 | 否 | 否 |
| 3 | guidance | 是 | 直接客户端 | 否 | 否 |
| 4 | response | 是 | 直接客户端 | 否 | 否 |
| 5 | explanation | 是 | 直接客户端 | 否 | 否 |
| 6 | rewrite | 是 | 直接客户端 | 否 | 否 |
| 7 | general_chat | 是 | 直接客户端 | 否 | 否 |
| 8 | filter | 是 | 直接客户端 | 否 | 否 |
| 9 | attachment | 是 | 直接客户端 | 否 | 否 |

**说明**: LLMGateway 已设计完成并注册了配置，但各调用站点尚未迁移。Gateway 的熔断器和并发限制器目前处于"待用"状态。`tool_router.py` 中的 router 场景有自己独立的熔断器和信号量实现。

---

## 6. 无 LLM 调用的模块说明

### 6.1 `rag/recommendation/session_context.py`

纯规则模块，不包含任何 LLM 调用。负责：
- 多轮对话记录（`record_turn()`）
- 溢出轮次压缩（`compact_turns()`）— 纯字符串拼接，截断 1200 字符
- 需求记忆合并（`merge_requirement_memory()`）— 字典合并 + 去重

### 6.2 `rag/recommendation/pc_session_flow.py`

纯规则模块，不包含任何 LLM 调用。负责：
- PC 装机方案生成（`build_pc_plan_for_message()`）— 调用 `generate_pc_build_plan()`（纯规则/数据库匹配）
- 预算解析（`parse_budget_target_amount()`、`parse_budget_delta_amount()`）— 正则匹配
- 偏好解析（`parse_pc_preferences()`）— 关键词匹配
- 方案对比（`compare_pc_build_plans()`）— 结构化对比

### 6.3 `rag/api/routes/chat.py`

编排层，不包含直接 LLM 调用。负责：
- 输入消毒（`sanitize_input()`）
- 调用 `route_shopping_tool_call()` 进行路由
- 调用 `validate_tool_call()` 校验路由结果
- 分发到各 handler（`_dispatch_lightweight()` 或 `handle_pc_build`/`handle_recommend`）
- 管理 trace span

---

## 7. 端到端调用链路图

### 7.1 推荐商品链路

```
POST /api/chat/stream { message: "预算500以内推荐蓝牙耳机" }
  │
  ├─[LLM①] route_shopping_tool_call()
  │    └─ try_llm_route_tool_call()
  │         └─ client.chat_json_with_report(build_router_messages())
  │              ├─ System: "你是电商导购系统的工具路由器..."
  │              └─ User: "Accumulated state: {...}\nUser: 预算500以内推荐蓝牙耳机"
  │         → { name: "recommend_shopping_products", arguments: { category: "digital", sub_category: "蓝牙耳机", price_max: 500 } }
  │
  ├─ validate_tool_call() — 白名单/值域/争议检测（无 LLM）
  │
  ├─ prepare_recommendation_context()
  │    └─ [LLM⑨] rewrite_query() — 如需要多轮改写
  │         └─ _llm_rewrite()
  │              └─ client.chat_completion(prompt)
  │                   → "华为 蓝牙耳机 降噪 500元以内"
  │
  ├─ handle_recommend()
  │    └─ recommend_shopping_products()
  │         │
  │         ├─ [LLM④] parse_requirement()
  │         │    └─ client.chat_json_with_report(build_requirement_prompt())
  │         │         → RequirementSpec { desired_categories: [digital], price_max: 500, ... }
  │         │
  │         ├─ build_recommendation_result() — 确定性筛选 + 评分（无 LLM）
  │         │    └─ [LLM⑧] _llm_filter_products() — 如有排除品牌
  │         │         └─ client.chat_json_with_report("你是商品筛选助手...")
  │         │              → { keep: ["pid_1", "pid_3"] }
  │         │
  │         ├─ [LLM⑥] enrich_recommendation_result() — 如启用 guidance
  │         │    └─ client.chat_json_with_report(build_guidance_prompt())
  │         │         → { teaching_guidance: [...], follow_up_questions: [...] }
  │         │
  │         └─ [LLM⑦] attach_grounded_explanation()
  │              └─ build_evidence_grounded_explanation()
  │                   └─ client.chat_json("Only output strict JSON...")
  │                        → { why_recommended: [...], evidence_points: [...] }
  │
  ├─ fact_check_result() — 事实校验（无 LLM）
  │
  └─ generate_natural_response()
       └─ [LLM⑤] _llm_diverse_response()
            └─ client.chat_json_with_report(prompt)
                 → "帮你挑了几款蓝牙耳机，最推荐 Sony WF-1000XM5，约 499 CNY。"
```

### 7.2 闲聊链路

```
POST /api/chat/stream { message: "你是谁" }
  │
  ├─[LLM①] route → { name: "general_chat" }
  │
  └─ handle_general_chat()
       └─ [LLM③] _generate_general_chat_llm_response()
            └─ client.chat_text(system + user)
                 → "你好，我是智能导购助手，可以帮你搜索商品、推荐合适款式..."
```

### 7.3 图片分析链路

```
POST /api/chat/stream { message: "帮我找同款", attachments: [{ data_url: "data:image/jpeg;base64,..." }] }
  │
  ├─ prepare_recommendation_context()
  │    └─ prepare_attachments_for_recommendation()
  │         └─ [LLM⑨'] analyze_image_attachment()
  │              └─ client.chat_json(system + multimodal_user_message)
  │                   → { summary: "黑色连帽卫衣", visual_query_terms: ["卫衣", "黑色", "连帽"], ... }
  │
  └─ goal_with_attachment_context() — 将图片线索注入需求文本
       → "帮我找同款 图片上下文：图片解析结果：摘要：黑色连帽卫衣；导购线索：卫衣、黑色、连帽"
```

---

## 8. 模型解析策略

### 8.1 Provider 解析

`build_llm_provider_config()` 按以下优先级解析 provider:

```
MALLMIND_LLM_PROVIDER → ARK_API_KEY 存在则 "ark" → "openai_compatible"
```

支持的 provider: `ark`（火山引擎）、`deepseek`、`mimo`、`openai_compatible`

### 8.2 模型解析

| 角色 | 环境变量 | 回退 |
|------|---------|------|
| 主模型 | `MALLMIND_LLM_MODEL` / `ARK_MODEL` / `MODEL` / `LLM_MODEL` | — |
| 快速模型 | `MALLMIND_LLM_FAST_MODEL` / `ARK_FAST_MODEL` / `FAST_MODEL` | 主模型 |
| Router 专用 | `MALLMIND_ROUTER_MODEL` | fast_model |
| Parse 专用 | `MALLMIND_PARSE_MODEL` | fast_model |
| Guidance 专用 | `MALLMIND_GUIDANCE_MODEL` | model（主模型） |
| Response 专用 | `RECOMMENDATION_RESPONSE_MODEL` | fast_model → model |
| Rewrite 专用 | `MALLMIND_REWRITE_MODEL` | fast_model |
| Filter 专用 | `MALLMIND_LLM_FILTER_MODEL` | fast_model |
| VLM 视觉 | `VISION_MODEL` / `MULTIMODAL_MODEL` | client.config.model |

### 8.3 Gateway 模型映射

```python
def _resolve_model(kind: str, client: OpenAICompatibleChatClient) -> str:
    if kind == "fast":
        return os.getenv("MALLMIND_ROUTER_MODEL") or client.config.fast_model
    return os.getenv("MALLMIND_GUIDANCE_MODEL") or client.config.model
```

---

## 9. 全局环境变量清单

### 9.1 LLM Provider 配置

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `MALLMIND_LLM_PROVIDER` | LLM 提供商 | ark / openai_compatible |
| `MALLMIND_LLM_BASE_URL` | API 基础 URL | provider 特定 |
| `MALLMIND_LLM_API_KEY` | API 密钥 | provider 特定 |
| `MALLMIND_LLM_MODEL` | 主模型名称 | provider 特定 |
| `MALLMIND_LLM_FAST_MODEL` | 快速模型名称 | 主模型 |
| `MALLMIND_LLM_TIMEOUT_SECONDS` | 全局 socket 超时 | 30 |

### 9.2 角色模型覆盖

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `MALLMIND_ROUTER_MODEL` | Router 专用模型 | fast_model |
| `MALLMIND_PARSE_MODEL` | Parse 专用模型 | fast_model |
| `MALLMIND_GUIDANCE_MODEL` | Guidance 专用模型 | model |
| `MALLMIND_REWRITE_MODEL` | Rewrite 专用模型 | fast_model |
| `MALLMIND_LLM_FILTER_MODEL` | Filter 专用模型 | fast_model |
| `RECOMMENDATION_RESPONSE_MODEL` | Response 专用模型 | fast_model |
| `VISION_MODEL` / `MULTIMODAL_MODEL` | VLM 视觉模型 | client.config.model |

### 9.3 开关与超时

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `MALLMIND_LLM_ENABLED` | 全局 LLM 开关 | true |
| `RECOMMENDATION_LLM_PARSE` | Parse 模式（auto/on/off） | auto |
| `RECOMMENDATION_LLM_GUIDANCE` | Guidance 开关 | false |
| `RECOMMENDATION_RESPONSE_LLM` | Response LLM 开关 | true |
| `MALLMIND_GUIDANCE_LLM` | Handler 层 Guidance 开关 | false |
| `RECOMMENDATION_LLM_PARSE_TIMEOUT_SECONDS` | Parse 硬超时 | 12.0 |
| `RECOMMENDATION_LLM_ROUTER_TIMEOUT_SECONDS` | Router 硬超时 | 15.0 |
| `RECOMMENDATION_LLM_GUIDANCE_TIMEOUT_SECONDS` | Guidance 硬超时 | 8.0 |
| `RECOMMENDATION_RESPONSE_TIMEOUT_SECONDS` | Response 硬超时 | 5.0 |
| `RECOMMENDATION_QUERY_REWRITE_TIMEOUT_SECONDS` | Rewrite 硬超时 | 5.0 |
| `RECOMMENDATION_LLM_FILTER_TIMEOUT_SECONDS` | Filter 硬超时 | 12.0 |
| `RECOMMENDATION_ROUTER_LLM_SOCKET_TIMEOUT_SECONDS` | Router socket 超时 | 15.0 |

### 9.4 熔断与并发

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `RECOMMENDATION_ROUTER_LLM_MAX_CONCURRENCY` | Router 最大并发 | 2 |
| `RECOMMENDATION_ROUTER_LLM_ACQUIRE_TIMEOUT_SECONDS` | Router 信号量获取超时 | 0.5 |
| `RECOMMENDATION_ROUTER_LLM_CIRCUIT_FAILURES` | Router 熔断失败阈值 | 5 |
| `RECOMMENDATION_ROUTER_LLM_CIRCUIT_COOLDOWN_SECONDS` | Router 熔断冷却时间 | 30.0 |
| `RECOMMENDATION_ROUTER_LLM_MAX_TOKENS` | Router max_tokens | 320 |

### 9.5 其他

| 变量 | 用途 | 默认值 |
|------|------|--------|
| `MAX_ATTACHMENT_ANALYSIS_BYTES` | 图片解析大小上限 | 6MB |
| `MAX_ATTACHMENT_TEXT_CHARS` | OCR 文本截断长度 | 1800 |
| `MALLMIND_MILVUS_RETRIEVAL` | Milvus 向量检索开关 | true |
| `MALLMIND_RAG_QUERY_EXPANSION` | RAG 查询扩展开关 | false |

---

## 附录 A: LLM 调用矩阵总览

| # | 调用站点 | 源文件 | 调用方式 | 模式 | 温度 | 超时 | 降级策略 |
|---|---------|--------|---------|------|------|------|---------|
| 1 | Router | tool_router.py | `chat_json_with_report` | JSON | 0.0 | 15s | 本地规则路由 |
| 2 | Parse | recommendation_pipeline.py | `chat_json_with_report` | JSON | 0.1 | 12s | 规则解析 RequirementSpec |
| 3 | Guidance | recommendation_pipeline.py | `chat_json_with_report` | JSON | 0.2 | 8s | 模板化引导文本 |
| 4 | Response | response_generator.py | `chat_json_with_report` | JSON→text | 0.9 | 5s | 模板变体库随机选择 |
| 5 | Explanation | explanation_builder.py | `chat_json` | JSON | 0.1 | 8s | 结构化模板解释 |
| 6 | Rewrite | query_rewriter.py | `chat_completion` | text | 0.1 | 5s | 返回原始查询 |
| 7 | General Chat | tool_handlers.py | `chat_text` | text | 0.7 | (全局) | 5 类模板回复 |
| 8 | Filter | structured_filter.py | `chat_json_with_report` | JSON | 0.0 | 12s | 保留全部候选 |
| 9 | Attachment | attachments.py | `chat_json` (多模态) | JSON | 0.1 | (全局) | 元数据保留 |

## 附录 B: 底层客户端调用方法对照

| 方法 | 返回类型 | 使用场景 |
|------|---------|---------|
| `chat_text()` | `str` | general_chat |
| `chat_json()` | `Dict[str, Any]` | explanation, attachment |
| `chat_json_with_report()` | `(Dict, LLMCallReport)` | router, parse, guidance, response, filter |
| `chat_completion()` | `(Dict, LLMCallReport)` | rewrite（原始 API 响应） |

## 附录 C: LLMGateway.call() 与直接调用的参数差异

当未来迁移到 Gateway 时，需注意以下参数差异：

| 场景 | 当前 max_tokens | Gateway 注册 max_tokens | 差异 |
|------|----------------|------------------------|------|
| router | env (320) | 320 | 一致 |
| parse | 1200 | 1200 | 一致 |
| guidance | 1500 | 1500 | 一致 |
| response | 200 | 200 | 一致 |
| explanation | **700** | **1500** | **不一致** — Gateway 注册值更大 |
| rewrite | 200 | 600 | **不一致** — Gateway 注册值更大 |
| general_chat | 200 | 200 | 一致 |
| filter | 500 | 500 | 一致 |
| attachment | 800 | 800 | 一致 |

> **注意**: explanation 和 rewrite 场景的 Gateway 注册 max_tokens 与实际调用使用的值不一致。迁移时应以实际业务需求为准。
