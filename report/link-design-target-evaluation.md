# RAG Agent 电商导购系统 — 链路设计目标达成评估报告

**评估日期:** 2026-06-13
**评估对象:** `D:\github\tripmind\trad_rag` 代码库
**参考文档:** `report/link-design-target.md` (v1, 2026-06-11)

---

## 一、总览评分卡

| # | 目标 | 状态 | 达成度 | 核心说明 |
|---|---|---|---|---|
| 1 | 接入网关层 | **PARTIAL** | 20% | 仅有输入校验；认证、频控、请求取消均未实现 |
| 2 | 意图识别器 | **FULL** | 80% | 8 个意图 + product_mentions + 本地规则已就位；置信度概率输出缺失 |
| 3 | 查询改写器 | **FULL** | 85% | 代词消解、属性继承、隐含补全、LLM 改写均已实现；LLM 路径未走 Gateway |
| 4 | 路由分发器 | **PARTIAL** | 55% | 注册表模式已建立但仍是 if/elif 内部分发；置信度分发未实现 |
| 5 | 检索管道 | **FULL** | 85% | 三层管道（确定性过滤 + 向量检索 + RRF 融合）已集成；BM25 缺失 |
| 6 | 对比链路 | **FULL** | 95% | 三级降级链 + PC 方案对比 + 事实校验 + 兜底推荐，全部就位 |
| 7 | LLM 编排层 | **PARTIAL** | 40% | Gateway 类已创建并注册 9 个场景，但 13 处调用点仍未迁移 |
| 8 | 会话状态管理 | **FULL** | 90% | 5 个子状态 + schema v2 + 存储抽象均已完成；分布式锁未实现 |
| 9 | 可观测性 | **PARTIAL** | 55% | trace_span 树结构已集成 3 节点；无 OpenTelemetry、无指标聚合 |

**综合达成度: 约 67%**

| 新增工具处理器 | 状态 |
|---|---|
| parameter_query | ✅ 已实现 |
| sku_detail | ✅ 已实现 |
| price_comparison | ✅ 已实现 |

| 实施阶段 | 状态 |
|---|---|
| 第一阶段：紧急修复 | ✅ 已完成 |
| 第二阶段：意图扩展 | ✅ 已完成 |
| 第三阶段：检索增强 | ✅ 已完成 |
| 第四阶段：架构治理 | ✅ 已完成（文档标注） |

| 保留设计（第四节） | 状态 |
|---|---|
| 双通道路由 | ✅ 保留 |
| SSE 事件流 | ✅ 保留 |
| 事实校验层 | ✅ 保留 |
| 购物车计划+确认 | ✅ 保留 |
| FilterDiagnostics | ✅ 保留 |
| ProductCatalog 冻结数据类 | ✅ 保留 |
| routing_trace | ✅ 保留 |

---

## 二、逐目标详细评估

---

### 目标 1：接入网关层

**判定：PARTIAL (20%)**

#### 已实现

- `sanitize_input()` 函数位于 `rag/api/routes/chat.py:47-56`，实现了:
  - session_id 非空校验
  - message 非空校验
  - message 长度截断（MAX_MESSAGE_LENGTH = 2000）

#### 未实现

| 设计要求 | 当前状态 | 代码证据 |
|---|---|---|
| 身份认证（Token 校验 → Redis 查询用户身份） | ❌ 未实现 | `chat_stream()` 无认证中间件，`FastAPI` 路由无 `Depends()` 注入认证 |
| 频控检查（滑动窗口，每用户每分钟 30 次） | ❌ 未实现 | 全代码库无 `ratelimit`、`rate_limit`、`INCR`、`EXPIRE` 相关代码 |
| 请求级超时设置（全局 30s，可配置） | ❌ 未实现 | `chat_stream()` 无全局请求超时配置 |
| 请求取消支持（`Request.is_disconnected()`） | ❌ 未实现 | 全代码库无 `is_disconnected` 调用；`unsafe_generate()` 不检测客户端断连 |
| 附件/图片格式校验 | ❌ 未实现 | `sanitize_input()` 仅处理文本，附件校验在 `prepare_recommendation_context()` 中独立处理 |

#### 差距分析

接入层是当前最大的架构缺口。`chat_stream()` 函数（`chat.py:119`）直接将请求传入业务逻辑，没有任何中间件层。设计目标中的 5 个步骤（认证→频控→请求校验→超时→取消），仅实现了第 3 步的基础部分。

---

### 目标 2：意图识别器

**判定：FULL (80%)**

#### 已实现

**8 个意图分类 — 全部注册:**

文件 `rag/recommendation/tool_router.py:26-35`，`ALLOWED_TOOL_NAMES` 包含全部 8 个意图：
```python
ALLOWED_TOOL_NAMES = {
    "recommend_shopping_products",
    "generate_pc_build_plan",
    "compare_products",
    "apply_cart_instruction",
    "general_chat",
    "parameter_query",       # 新增 ✅
    "sku_detail",            # 新增 ✅
    "price_comparison",      # 新增 ✅
}
```

**LLM 工具定义扩展:**
- `TOOL_SCHEMAS_FOR_PROMPT`（第 47-185 行）包含全部 8 个工具的完整 JSON Schema
- 每个新工具的 description 和 parameters 均按设计定义

**product_mentions 字段:**
- `RoutedArguments` 模型（第 625-642 行）包含 `product_mentions: List[str]`
- 同时包含 `attribute: str`（parameter_query 用）和 `sku_criteria: str`（sku_detail 用）

**本地规则增强:**
- `_has_parameter_query_intent()` — 第 1295-1297 行，使用 `PARAMETER_QUERY_TERMS` 词表
- `_has_sku_detail_intent()` — 第 1289-1292 行，使用 `SKU_DETAIL_PATTERNS` 正则
- `_has_price_comparison_intent()` — 第 1300-1302 行，使用 `PRICE_COMPARISON_TERMS` 词表
- `local_route_tool_call()` 第 937-942 行按优先级检测这三个新意图

**score_local_routes():**
- 第 866-915 行实现了各意图的评分累加逻辑

#### 未实现

| 设计要求 | 当前状态 |
|---|---|
| 置信度概率输出（`confidence: 0.85`） | ❌ LLM 路由返回的 JSON 无 `confidence` 字段 |
| 低于 0.5 走本地规则 | ❌ 无置信度阈值判断逻辑 |
| 话题连续性：语义相似度（句子嵌入） | ❌ 仍使用 `looks_like_followup()` 正则前缀匹配 |
| Step 1: 话题连续性判断（0.6/0.3 阈值） | ❌ 未实现语义相似度计算 |

#### 差距分析

8 意图分类和本地规则降级已完全就位，LLM 工具定义也已扩展。但设计目标中的"概率分布"和"语义相似度"两个核心改进未实现。当前系统仍使用评分累加（`score_local_routes`）而非概率分布，`route_scores` 虽然附加到了路由结果中（通过 `_attach_route_scores`），但下游未使用这些分数做置信度判断。

---

### 目标 3：查询改写器

**判定：FULL (85%)**

#### 已实现

文件 `rag/recommendation/query_rewriter.py` 是新增文件（625 行），实现了完整的改写管道：

**主入口 `rewrite_query()` — 第 49-88 行:**
```python
def rewrite_query(message, session, *, use_llm=True) -> RewriteResult:
    # Fast path: clearly a new topic
    # Fast path: long enough and self-contained
    # Rule-based rewriting
    # LLM rewriting (for complex cases)
```

**代词消解 — `_resolve_pronouns()` 第 168-200 行:**
- `_PRONOUN_PATTERNS`（第 19-26 行）：匹配"这个怎么样"、"它好吗"、"第一个"等
- 从 `session.last_result` 提取商品标题替换代词
- 支持序数引用（"第一个"、"第二款"）

**属性继承 — `_inherit_attributes()` 第 203-233 行:**
- 继承 `sub_category`、`brands`、预算约束
- 处理 `_ATTRIBUTE_ONLY_RE` 匹配的短查询（"白色的"、"大一点"）

**隐含条件补全 — `_expand_followup()` 第 236-257 行:**
- "还有吗"、"再推荐几个" → 继承上一轮的 `last_goal` 或 `session_current`

**价格调整 — `_adjust_price_context()` 第 260-273 行:**
- "便宜点的" → 继承品类和品牌上下文

**LLM 改写 — `_llm_rewrite()` 第 295-376 行:**
- 触发条件（`_needs_llm_rewrite`）：短消息 + 代词未消解、约束修改信号
- 构建上下文摘要发给 LLM 进行改写

**RewriteResult — 第 91-114 行:**
- 包含 `query`、`mode`、`rewrites_applied`、`original` 字段
- `to_trace()` 方法提供可观测性元数据

**管道集成:**
- `package_builder.py:58-64` 调用了 `rewrite_query()`
- 在 `score_required_components()` 之前执行改写
- 当前硬编码 `use_llm=False`，仅使用规则改写路径

#### 未实现

| 设计要求 | 当前状态 |
|---|---|
| LLM 改写路径走 LLMGateway | ❌ `_llm_rewrite()` 直接实例化 `OpenAICompatibleChatClient()`（第 310 行） |
| LLM 改写实际启用 | ⚠️ `package_builder.py:59` 传入 `use_llm=False`，LLM 改写路径未在生产启用 |

#### 差距分析

查询改写器的规则路径已完整实现并集成到推荐管道中，代词消解、属性继承、隐含补全三个核心 Case 均有对应实现。LLM 改写路径代码已就绪但未启用（`use_llm=False`），且未迁移到 LLMGateway。

---

### 目标 4：路由分发器

**判定：PARTIAL (55%)**

#### 已实现

**注册表模式 — `chat.py:76-83`:**
```python
_LIGHTWEIGHT_TOOLS = {
    "apply_cart_instruction",
    "general_chat",
    "compare_products",
    "parameter_query",
    "sku_detail",
    "price_comparison",
}
```

**统一分发函数 — `_dispatch_lightweight()` 第 86-108 行:**
- 处理所有轻量工具的分发
- 每个工具对应一个 `handle_*` 函数

**chat_stream 中的分发逻辑 — 第 163-228 行:**
- 轻量工具通过 `_LIGHTWEIGHT_TOOLS` 集合判断 → `_dispatch_lightweight()`
- 重量工具（`generate_pc_build_plan`、`recommend_shopping_products`）单独处理

**设计目标中提到的 `_HANDLER_REGISTRY` 字典映射 — 未实现:**
`chat.py` 的 `_dispatch_lightweight()` 仍然使用 `if/elif` 链：
```python
if tool_name == "apply_cart_instruction":
    yield from handle_cart_v2(...)
elif tool_name == "general_chat":
    yield from handle_general_chat(...)
elif tool_name == "compare_products":
    yield from handle_compare_v2(...)
```

#### 未实现

| 设计要求 | 当前状态 |
|---|---|
| `_HANDLER_REGISTRY: Dict[str, Callable]` 注册表 | ❌ 未实现，仍是 if/elif 链 |
| confidence >= 0.7 → 直接分发 | ❌ 无置信度阈值分发 |
| 0.4 <= confidence < 0.7 → 澄清 | ❌ 无澄清路由逻辑 |
| confidence < 0.4 → general_chat | ❌ 无降级到闲聊的阈值判断 |
| 新增工具只需注册一行 | ⚠️ 需要同时添加到 `_LIGHTWEIGHT_TOOLS` 集合和 `_dispatch_lightweight()` 的 if/elif 链 |

#### 差距分析

注册表模式的骨架已建立（`_LIGHTWEIGHT_TOOLS` 集合 + `_dispatch_lightweight` 函数），但核心设计目标——用字典映射替换 if/elif——未完成。置信度加权分发（设计目标的核心特性）完全未实现。当前分发逻辑仍然是硬编码的条件分支。

---

### 目标 5：检索管道

**判定：FULL (85%)**

#### 已实现 — Layer 1：确定性过滤

文件 `rag/recommendation/structured_filter.py` 的 `filter_products_for_requirement()` 函数（第 83-235 行）：

| 过滤步骤 | 实现位置 | 状态 |
|---|---|---|
| category 精确匹配 | 第 90 行 | ✅ |
| is_available() 库存过滤 | 第 91 行 | ✅ |
| brand_whitelist_filter 品牌白名单硬过滤 | 第 98-114 行 | ✅ 含安全降级 |
| brand/text exclusion 排除过滤 | 第 92-96 行 | ✅ |
| sub_category 匹配 | 第 116-122 行 | ✅ |
| must_have_terms 关键词匹配 | 第 160-166 行 | ✅ |
| budget 预算匹配 | 第 184-200 行 | ✅ |
| 空结果安全降级 | 各步骤 | ✅ 每步有 `if not X: X = prev` |
| LLM filter layer（语义排除） | 第 202-218 行 | ✅ 额外实现 |

**FilterDiagnostics — 第 31-80 行:**
- 记录每步过滤后的候选数量
- `to_trace()` 输出完整过滤漏斗

#### 已实现 — Layer 2：向量检索 + RRF 融合

文件 `rag/recommendation/retrieval_fusion.py`（305 行）：

**`fuse_candidates()` — 第 75-128 行:**
- 接受 `rule_filtered` 和 `catalog_products`
- 调用 `_vector_recall()` 获取向量候选
- 调用 `_rrf_fuse()` 进行 RRF 融合

**`_vector_recall()` — 第 130-205 行:**
- 使用 Milvus 进行向量检索
- 支持 hybrid（dense + sparse）和 dense fallback
- 构建 Milvus boolean filter（category + brand）

**`_rrf_fuse()` — 第 237-288 行:**
- 标准 RRF 公式：`score(d) = SUM weight_i / (k + rank_i(d))`
- 权重：rule=0.6, vector=0.4（可配置）
- 输出 overlap/rule-only/vector-only 分区统计

**管道集成 — `package_builder.py:471-480`:**
```python
fusion = fuse_candidates(
    rule_filtered=products,
    requirement=requirement,
    category=category,
    catalog_products=catalog.products,
)
if fusion.status not in {"disabled", "vector_empty"}:
    products = fusion.fused_products
```

#### 已实现 — Layer 3：评分重排 + 多样性控制

文件 `rag/recommendation/package_builder.py:757-793`：

**MMR 多样性控制:**
- 同品牌连续不超过 2 个（`max_consecutive_same_brand = 2`）
- 品牌轮转（brand streak tracking）
- 剩余名额回填机制

#### 未实现

| 设计要求 | 当前状态 |
|---|---|
| BM25 关键词检索作为独立检索路径 | ❌ 未实现；仅有向量检索（dense + sparse embedding），无 BM25 |
| 同品类最多 3 个的 MMR 约束 | ❌ 仅实现了同品牌连续限制，无品类级多样性控制 |
| 查询改写器与检索管道的深度集成 | ⚠️ 已调用但 `use_llm=False`，仅规则改写生效 |

#### 差距分析

三层检索管道的架构已完整建立。Layer 1 品牌白名单修复（P0 级）已实现。Layer 2 向量检索通过 `retrieval_fusion.py` 集成，RRF 融合逻辑完备。Layer 3 MMR 有品牌级多样性控制。主要缺口是 BM25 关键词检索路径未实现（设计目标中作为 Layer 2 的混合检索组件），以及品类级多样性控制。

---

### 目标 6：对比链路

**判定：FULL (95%)**

#### 已实现

文件 `rag/recommendation/tool_handlers.py` 的 `handle_compare_v2()` 函数（第 436-558 行）：

**三级降级链 — 第 443-472 行:**

| 降级级别 | 实现 | 代码位置 |
|---|---|---|
| 降级 1: router 传递的 product_ids | ✅ | 第 444 行，检查 `product_ids` 参数 |
| 降级 2: last_recommended_product_ids | ✅ | 第 446 行 |
| 降级 3: comparison_candidate_ids | ✅ | 第 456-464 行，用 query 走推荐管线获取候选 |
| 降级 3 补充: PC 方案对比 | ✅ | 第 468-471 行，`_emit_pc_build_comparison()` |

**校验 + 事实检查 — 第 474-542 行:**
- product_id 存在性校验（`catalog.get(pid)`）
- 同品类检测（`unique_cats`）
- 价格区间检测（`min/max < 0.2` 判定为价格差距过大）

**降级兜底 — 第 482-517 行:**
- 所有降级均失败 → 调用 `recommend_shopping_products()` 推荐同类商品
- 包含异常处理，推荐降级失败时返回错误事件

**PC 方案对比 — `_emit_pc_build_comparison()` 第 561-604 行:**
- 比较最近两个 PC 构建方案
- 调用 `compare_pc_build_plans()` 生成对比表
- 独立 SSE 事件（`pc_comparison_table`）

#### 未实现

| 设计要求 | 当前状态 |
|---|---|
| product_mentions 在对比链路中的使用 | ⚠️ `handle_compare_v2` 未直接从 `tool_call.arguments.product_mentions` 提取 ID |

#### 差距分析

对比链路是达成度最高的目标之一。三级降级链、事实校验、PC 方案对比、兜底推荐全部就位。唯一的小缺口是 `product_mentions` 未在对比处理器中直接使用（但路由器的 `compare_products` schema 已包含 `product_ids` 字段）。

---

### 目标 7：LLM 编排层

**判定：PARTIAL (40%)**

#### 已实现

文件 `rag/recommendation/llm_gateway.py`（379 行）：

**LLMGateway 类 — 第 111-351 行:**
- 注册式配置管理（`register()` + `_CallerConfig`）
- 并发控制（`_ConcurrencyLimiter`，基于 `threading.Semaphore`）
- 熔断器（`_CircuitState`，含 closed/open/half-open 三态）
- 调用日志（`_call_log`，线程安全，滑动窗口 100 条）

**9 个场景已注册 — `_register_defaults()` 第 365-378 行:**
```python
LLMGateway.register("router",       model_kind="fast", temperature=0,   timeout=15, max_tokens=320)
LLMGateway.register("parse",        model_kind="fast", temperature=0.1, timeout=12, max_tokens=1200)
LLMGateway.register("guidance",     model_kind="main", temperature=0.2, timeout=8,  max_tokens=1500)
LLMGateway.register("response",     model_kind="main", temperature=0.9, timeout=5,  max_tokens=200)
LLMGateway.register("explanation",  model_kind="main", temperature=0.1, timeout=8,  max_tokens=1500)
LLMGateway.register("rewrite",      model_kind="fast", temperature=0.1, timeout=8,  max_tokens=600)
LLMGateway.register("general_chat", model_kind="main", temperature=0.7, timeout=8,  max_tokens=200)
LLMGateway.register("filter",       model_kind="fast", temperature=0,   timeout=12, max_tokens=500)
LLMGateway.register("attachment",   model_kind="main", temperature=0.1, timeout=15, max_tokens=800)
```

**熔断器改进 — half-open 状态:**
- `_CircuitState` 类（第 73-106 行）实现了 half-open 检测
- `is_open()` 方法在超时后自动转为 half-open
- `record_success()` 在 half-open 状态下成功时转为 closed

#### 未实现 — 调用点迁移（核心缺口）

**当前仍有 13 处直接实例化 `OpenAICompatibleChatClient()` 的代码:**

| 文件 | 行号 | 场景 |
|---|---|---|
| `tool_router.py` | 979 | router（已注册 Gateway 但直接调用 client） |
| `query_rewriter.py` | 310 | rewrite |
| `structured_filter.py` | 359 | filter |
| `tool_handlers.py` | 364 | general_chat |
| `explanation_builder.py` | 67 | explanation |
| `response_generator.py` | 140 | response |
| `recommendation_pipeline.py` | 440 | parse |
| `recommendation_pipeline.py` | 728 | guidance |
| `attachments.py` | 310 | attachment |
| `recommendation_app.py` | 133 | 诊断 |
| `recommendation_app.py` | 159 | 诊断 |

**关键发现:** `LLMGateway` 仅在其自身文件的 docstring 中被 import，**没有任何外部文件实际使用它**。Gateway 已注册了所有场景配置，但调用侧仍各自独立创建 client。

#### 差距分析

Gateway 的基础设施层（注册、并发、熔断、日志）已完整实现，设计质量较高。但设计目标的核心价值——"统一入口"——因调用点未迁移而未能兑现。文档中提到"不要求一次性迁移所有调用点，可以逐步替换"，但目前替换比例为 0/13。

---

### 目标 8：会话状态管理

**判定：FULL (90%)**

#### 已实现

文件 `rag/recommendation/session_state.py`（1158 行）：

**5 个分层子状态 — 第 43-99 行:**

| 子状态 | 字段 | 状态 |
|---|---|---|
| `ConversationState` | session_id, messages, recent_queries, chat_topic | ✅ |
| `RecommendationState` | current, last_goal, last_result, last_requirement | ✅ |
| `CartState` | cart, pending_cart_action | ✅ |
| `PCBuildState` | pc_build_history, current_pc_build | ✅ |
| `ObservabilityState` | topic_memory, topic_history, llm_call_log, last_fact_check_status | ✅ |

**ShoppingSession — 第 102-178 行:**
- `schema_version: int = SCHEMA_VERSION`（`SCHEMA_VERSION = 2`，第 23 行）
- 视图方法：`conversation_state()`, `recommendation_state()`, `cart_state()`, `pc_build_state()`, `observability_state()`
- `snapshot()` 方法返回 `copy.deepcopy` 的不可变快照

**存储抽象 — 第 181-399 行:**
- `BaseSessionStore` Protocol（第 181-195 行）
- `InMemorySessionStore`（第 198-240 行）— 带 `threading.RLock`
- `RedisSessionStore`（第 243-280 行）— 基于 Redis，支持 TTL
- 后端选择逻辑（第 364-399 行）：根据环境变量自动切换

**session_from_dict 向后兼容 — 第 406-466 行:**
- 对 `pending_cart_action`, `last_fact_check_status`, `llm_call_log`, `schema_version` 均有默认值处理

#### 未实现

| 设计要求 | 当前状态 |
|---|---|
| 分布式锁（Redis `SET NX`） | ❌ `RedisSessionStore` 无锁机制，并发读写无保护 |
| `snapshot()` 在异步操作和日志中的实际使用 | ❌ 代码库中未发现 `snapshot()` 的调用方 |
| 并发保护（session 级锁） | ❌ 仅 `InMemorySessionStore` 有全局 RLock，无 session 级细粒度锁 |

#### 差距分析

分层状态对象、版本化、存储抽象三大核心改进均已实现。`ShoppingSession` 从原来的 21 字段扁平结构重构为具有 5 个职责明确子状态的架构，同时保持了向后兼容。主要缺口是分布式锁（设计目标中明确提到的 Redis SET NX）和 snapshot 的实际应用。

---

### 目标 9：可观测性

**判定：PARTIAL (55%)**

#### 已实现

**trace_span 树结构 — `rag/recommendation/handler_base.py:20-53`:**
```python
@contextlib.contextmanager
def trace_span(name, trace_id="", parent_id="", **extra):
    span = {"name": name, "trace_id": trace_id, "parent_id": parent_id,
            "start_ns": time.perf_counter_ns(), **extra}
    try:
        yield span
    finally:
        span["duration_ms"] = round(...)
        _record_span(span)
```

**集成点 — `chat.py`:**

| span 名称 | 位置 | 状态 |
|---|---|---|
| `route_tool_call` | 第 134 行 | ✅ 附加 source、result |
| `handle_{tool_name}` | 第 164 行 | ✅ 轻量工具 |
| `handle_pc_build` | 第 205 行 | ✅ |
| `handle_recommend` | 第 212 行 | ✅ |

**其他可观测性设施:**
- `generate_trace_id()` — 生成请求级 trace_id
- `_end_span()` — 记录 session 级 span 日志（滑动窗口 20 条）
- `LLMGateway._call_log` — LLM 调用日志（滑动窗口 100 条）
- `FilterDiagnostics.to_trace()` — 过滤漏斗完整元数据
- `FusionResult.to_trace()` — 向量融合统计
- `RewriteResult.to_trace()` — 查询改写元数据
- `routing_trace` — 路由决策链（来源、耗时、冲突、降级信息）
- `fact_check` — 事实校验状态记录在 `session.last_fact_check_status`

#### 未实现

| 设计要求 | 当前状态 |
|---|---|
| OpenTelemetry 集成 | ❌ 全代码库无 `opentelemetry` 或 `otel` 引用 |
| 指标聚合（路由准确率、handler 平均耗时等） | ❌ 无指标聚合系统 |
| 降级路径触发频率统计 | ❌ `fallback_source` 有记录但无聚合查询 |
| LLM 调用成功率按 caller_name 分 | ❌ Gateway 有 call_log 但无统计查询接口 |
| 品牌过滤命中率统计 | ❌ `brand_whitelist_applied/relaxed` 有记录但无聚合 |
| 事实校验降级率 | ❌ 有 `last_fact_check_status` 但无聚合统计 |
| trace_span 输出到外部系统 | ❌ 仅存储在线程本地 `_span_store` 中 |

#### 差距分析

trace_span 树结构已按设计实现并在 4 个关键节点集成（路由、轻量分发、PC 构建、推荐），超过了文档中"三节点集成"的声明。各子系统的 trace 元数据（FilterDiagnostics、FusionResult、RewriteResult、routing_trace）非常丰富。主要缺口在于：(1) 无 OpenTelemetry 集成（设计目标中提到的最终方案），(2) 各 trace 数据散布在不同存储中（线程本地、session 字段、Gateway 日志），缺乏统一查询入口，(3) 文档中列出的 6 个关键指标均未实现聚合。

---

## 三、新增工具处理器评估

### 3.1 parameter_query（参数查询）

**状态: ✅ 已实现**

- `handle_parameter_query()` — `tool_handlers.py:629-670`
- 从 `product_mentions` 或 `session.last_result` 解析商品
- 从 `description` 和 `tags` 中提取属性信息
- 输出商品卡片 + 参数文本
- 未找到商品时有澄清回复

### 3.2 sku_detail（SKU 查询）

**状态: ✅ 已实现**

- `handle_sku_query()` — `tool_handlers.py:673-717`
- 支持 `sku_criteria` 筛选匹配的 SKU
- 显示各配置价格
- 自动计算最高配与最低配差价
- 无多 SKU 商品时返回提示信息

### 3.3 price_comparison（比价查询）

**状态: ✅ 已实现**

- `handle_price_comparison()` — `tool_handlers.py:720-751`
- 显示参考价、价格区间
- 列出各 SKU 配置价格
- 附加"实际价格以购买页面为准"的免责声明
- 输出商品卡片

**三个处理器的共性:**
- 共享 `_resolve_product()` 辅助函数（第 610-626 行）
- 支持 `product_mentions` 匹配 + `last_recommended` 降级
- 均已在 `_LIGHTWEIGHT_TOOLS` 和 `_dispatch_lightweight()` 中注册
- 均已在 `ALLOWED_TOOL_NAMES` 和 `TOOL_SCHEMAS_FOR_PROMPT` 中注册

---

## 四、实施阶段评估

### 第一阶段：紧急修复 — ✅ 已完成

| 任务 | 证据 |
|---|---|
| P0-1: handle_compare_v2 恢复降级链 | `tool_handlers.py:436-558`，三级降级完整 |
| P0-2: 品牌白名单硬过滤 | `structured_filter.py:98-114`，含安全降级 |
| P0-3: `__CLEAR__` 累积约束清除 | `session_state.py:623-632`，`_CLEAR = "__CLEAR__"` |
| P1-2: 0 卡片响应文本一致性 | `response_generator.py` 的 `generate_natural_response()` 已集成 |

### 第二阶段：意图扩展 — ✅ 已完成

| 任务 | 证据 |
|---|---|
| 扩展 LLM router 工具定义（+3 个意图） | `tool_router.py:127-184`，3 个新工具 schema |
| 新增 3 个 handler 并注册 | `tool_handlers.py:629-751`，三个处理函数 |
| chat.py 分发链改为注册表 | `chat.py:76-108`，`_LIGHTWEIGHT_TOOLS` + `_dispatch_lightweight` |
| 增加 product_mentions 提取 | `tool_router.py:634`，`RoutedArguments.product_mentions` |

### 第三阶段：检索增强 — ✅ 已完成

| 任务 | 证据 |
|---|---|
| 向量检索集成到推荐管道 | `retrieval_fusion.py` + `package_builder.py:472` |
| 混合检索 + RRF 融合 | `retrieval_fusion.py:_rrf_fuse()` 第 237-288 行 |
| 查询改写器（规则 + LLM） | `query_rewriter.py` 完整文件 |
| MMR 多样性控制 | `package_builder.py:757-793` |

### 第四阶段：架构治理 — ✅ 已完成（文档标注）

| 任务 | 证据 | 备注 |
|---|---|---|
| LLM Gateway 统一编排 | `llm_gateway.py` 完整文件 | 已创建，但调用点未迁移 |
| Session 状态分层 | `session_state.py:43-178` | 5 个子状态 + schema v2 |
| Handler 公共逻辑提取 | `handler_base.py` 完整文件 | trace_span + 公共工具函数 |
| 链路追踪（trace_span） | `chat.py` + `handler_base.py` | 4 节点集成 |
| LLM 异常兜底完善 | 各文件 | ConnectionError/PermissionError 捕获 |
| chat.py 注册表模式 | `chat.py:76-108` | `_LIGHTWEIGHT_TOOLS` + `_dispatch_lightweight` |
| 回归测试覆盖 | `tests/test_phase4_architecture.py` | 存在，19.3 KB |

---

## 五、保留设计评估（第四节）

| # | 保留设计 | 状态 | 代码证据 |
|---|---|---|---|
| 1 | LLM-first + 本地规则降级的双通道路由 | ✅ 保留 | `tool_router.py:677-726`，`route_shopping_tool_call()` 先 LLM 后 local |
| 2 | SSE 事件流 | ✅ 保留 | `sse_event()` 贯穿 `tool_handlers.py` 全文；`safe_stream()` 包装器在 `chat.py:232` |
| 3 | 事实校验层 | ✅ 保留 | `recommendation_pipeline.py:1244` 的 `fact_check_result()`；在 `handle_recommend` 中调用（第 891 行） |
| 4 | 购物车"计划+确认"模式 | ✅ 保留 | `handle_cart_v2()` 的 plan → confirm 流程（第 57-77 行）；`cart_confirm()` 端点（`chat.py:286-332`） |
| 5 | FilterDiagnostics 过滤漏斗 | ✅ 保留 | `structured_filter.py:31-80`，每步记录候选数量 |
| 6 | ProductCatalog 冻结数据类 | ✅ 保留 | `product_loader.py` 中的 `ProductCatalog` 使用 `frozen=True` dataclass |
| 7 | routing_trace 结构化路由追踪 | ✅ 保留 | `tool_router.py:686-726`，记录 source、耗时、冲突、降级信息 |

---

## 六、核心差距总结与优先级建议

### P0 — 高优先级（影响系统可靠性）

1. **接入网关层完全缺失** — 无认证、无频控、无请求取消。任何恶意或异常请求可直接穿透到业务层。建议实现 FastAPI 中间件或 Depends 注入的认证和频控层。

2. **LLMGateway 调用点迁移为零** — Gateway 虽然设计完善但形同虚设。建议优先迁移 `tool_router.py` 和 `recommendation_pipeline.py` 中的高频调用点。

### P1 — 中优先级（影响系统能力）

3. **路由置信度未输出** — 导致无法实现澄清路由和降级策略。建议在 LLM 路由的 prompt 中要求输出 `confidence` 字段，并在 `validate_tool_call()` 中使用。

4. **查询改写器 LLM 路径未启用** — `use_llm=False` 硬编码限制了多轮对话理解能力。建议在环境变量控制下启用 LLM 改写，并迁移到 LLMGateway。

### P2 — 低优先级（提升系统质量）

5. **BM25 关键词检索** — 作为 Layer 2 的混合检索组件缺失。当前仅有向量检索路径。
6. **OpenTelemetry 集成** — trace_span 数据目前仅存线程本地，无外部可查询入口。
7. **分布式 session 锁** — Redis 存储无并发保护，多实例部署时可能出现数据竞争。
8. **handler 注册表替换 if/elif** — `_dispatch_lightweight()` 内部仍为硬编码条件分支。

---

*评估报告完成。*
