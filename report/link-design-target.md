# RAG Agent 电商导购系统 — 链路设计目标（修订版）

**日期:** 2026-06-11  
**版本:** v1  
**关联文档:** [current-link-architecture.md](current-link-architecture.md) · [case12-20_fix_plan_v2.md](../reports/case12-20_fix_plan_v2.md)

---

## 一、当前链路 vs 目标链路：差距总览

当前系统已具备完整的"请求 → 路由 → 处理 → SSE 返回"骨架，但在意图理解深度、查询改写、检索能力、品牌过滤、会话管理等方面存在结构性缺陷。下表逐层对比现状与目标：

| 链路层 | 当前实现 | 核心缺陷 | 目标设计 |
|---|---|---|---|
| **接入层** | `sanitize_input()` 仅做长度校验和非空校验 | 无频控、无认证、无请求级超时 | 接入网关：认证 + 频控 + 请求取消 |
| **意图识别** | LLM 路由 → 5 个工具（recommend/compare/pc_build/cart/chat） | 意图粒度粗，SKU 查询、参数咨询、比价查询全部落入 recommend | 7+ 意图分类，含概率置信度，低置信度走澄清 |
| **查询改写** | `build_contextual_goal()` 拼接字符串 | 无代词消解、无属性继承、无隐含条件补全 | 独立查询改写器：消解 + 继承 + 补全 |
| **路由分发** | if/elif 硬编码分发链 | 无路由置信度，无争议解决策略 | 置信度加权路由 + 争议仲裁 |
| **检索管道** | 规则过滤 → 7 维评分 → top-N 选择 | `brands` 白名单未做硬过滤；无向量检索；无混合检索融合 | 确定性过滤 + 向量检索 + 评分重排 三层管道 |
| **结果融合** | 无 | 单一检索源，无多源融合 | 多源融合（规则 + 向量 + 图谱）+ MMR 多样性控制 |
| **LLM 推理** | 3 处独立实例化 `OpenAICompatibleChatClient` | 无统一 LLM 编排层，各调用点超时/模型/温度各自为政 | 统一 LLM 网关：共享配置、并发控制、熔断 |
| **输出处理** | `generate_natural_response()` LLM 或模板 | 无合规检查；LLM 文本可能与实际卡片数不一致 | 合规过滤 + 一致性校验 + 格式化 |
| **会话管理** | 21 字段可变 dataclass，全局模块变量存储 | 无封装、无版本迁移、无并发保护 | 分层状态对象 + 版本化 + 存储抽象 |
| **可观测性** | `_end_span()` 记录 20 条日志 + `routing_trace` | 无链路追踪、无降级路径日志、无指标聚合 | OpenTelemetry 链路追踪 + 结构化指标 |

---

## 二、链路设计目标：逐层详述

### 目标 1：接入网关层

**当前状态：** `chat.py` 的 `chat_stream()` 直接处理 HTTP 请求，`sanitize_input()` 仅做消息长度截断和非空校验。

**设计目标：**

```
POST /api/chat/stream
    │
    ├─ 1. 身份认证（Token 校验 → Redis 查询用户身份）
    ├─ 2. 频控检查（滑动窗口，每用户每分钟 30 次）
    ├─ 3. 请求校验
    │     ├─ session_id 非空
    │     ├─ message 非空、长度 ≤ 2000
    │     └─ 附件/图片格式校验
    ├─ 4. 请求级超时设置（全局 30s，可配置）
    └─ 5. 请求取消支持（客户端断连 → 终止下游 LLM 调用）
```

**实现要点：**

- 频控：使用 Redis `INCR` + `EXPIRE` 实现滑动窗口计数器，键名 `ratelimit:{user_id}:{minute}`。
- 请求取消：`StreamingResponse` 检测客户端断连（`Request.is_disconnected()`），主动取消正在执行的 LLM 调用。当前所有 LLM 调用使用 `run_with_hard_timeout()` 的线程模型，可通过 `Future.cancel()` 实现。
- 不做的事：不在接入层做意图识别或路由，这些属于业务层。

---

### 目标 2：意图识别器（替换当前 LLM 路由）

**当前状态：** `route_shopping_tool_call()` 将用户消息分为 5 类工具。LLM 路由输出一个 `tool_call` dict，本地规则输出另一个，取 LLM 优先。无置信度，无概率分布。

**设计目标：**

```
用户消息 + session 上下文
    │
    ├─ Step 1: 话题连续性判断
    │     ├─ 当前 query 与 last_query 语义相似度（句子嵌入模型，当前应该是qwen—v3）
    │     ├─ > 0.6 → 延续话题
    │     ├─ < 0.3 → 话题切换
    │     └─ 0.3-0.6 → LLM 判断
    │
    ├─ Step 2: 意图分类（输出概率分布）
    │     intents = [
    │       "single_search",        // "有没有500以下的机械键盘"
    │       "combo_recommendation", // "配一台打游戏的电脑"
    │       "compare_analysis",     // "i7和i9哪个好"
    │       "parameter_query",      // "这个显卡功耗多少"  ← 当前缺失
    │       "price_comparison",     // "京东上这款多少钱"  ← 当前缺失
    │       "sku_detail",           // "12+256和16+512差多少" ← 当前缺失
    │       "cart_action",          // "加到购物车"
    │       "general_chat"          // "今天天气不错"
    │     ]
    │
    ├─ Step 3: 实体抽取（正则 + NER）
    │     entities = {
    │       category: "显卡",
    │       brand: null,
    │       price_range: [3000, 5000],
    │       usage: "游戏",
    │       attributes: {"显存": "8GB以上"},
    │       product_mentions: ["i7-14700K", "i9-14900K"]  ← 当前缺失
    │     }
    │
    └─ 输出: {intent, confidence, entities, is_topic_switch}
```

**与当前系统的对比：**

| 维度 | 当前 | 目标 |
|---|---|---|
| 意图数量 | 5 个（recommend/compare/pc_build/cart/chat） | 8 个（新增 parameter_query, price_comparison, sku_detail） |
| 置信度 | 无（布尔命中） | 概率分布，低于阈值走澄清 |
| 实体抽取 | `extract_slots_rule_based()` 仅提取 budget/category | 增加 product_mentions、attributes、price_range |
| 话题连续性 | `looks_like_followup()` 正则前缀匹配 | 语义相似度 + LLM 仲裁 |
| 争议解决 | `validate_tool_call()` 事后检测 LLM vs 本地冲突 | 前置置信度加权，低置信度自动降级 |

**实现策略：**

意图识别仍采用 LLM-first + 本地规则降级 的双通道架构（保持现有优势），但做以下改进：

1. **扩展 LLM 工具定义**：在 `_build_router_system_prompt()` 中增加 `parameter_query`、`price_comparison`、`sku_detail` 三个工具的 JSON Schema。
2. **增加 `product_mentions` 字段**：在 `RoutedArguments` Pydantic 模型中增加 `product_mentions: List[str]`，教 LLM 从用户消息中提取具体商品型号。
3. **置信度输出**：LLM 路由返回 `{"name": "...", "arguments": {...}, "confidence": 0.85}`。低于 0.5 时走本地规则结果。
4. **本地规则增强**：将 `parameter_query` 识别模式（"功耗多少"、"重量多少"、"尺寸多大"）加入 `local_route_tool_call()` 决策树。

---

### 目标 3：查询改写器（新增层）

**当前状态：** `build_contextual_goal()` 在 `session_state.py` 中实现，逻辑极其简单：如果是追问，将当前消息拼接到上一轮 `last_goal` 后面（用 `"User added constraints:"` 分隔）。无代词消解、无属性继承。

**设计目标：**

```
用户原始消息 + session 上下文
    │
    ├─ Case 1: 代词消解
    │     "这个怎么样" → "[上一轮推荐的显卡型号] 怎么样"
    │
    ├─ Case 2: 属性继承
    │     "有白色的吗" → "[当前品类] + 白色 + [保留的过滤条件]"
    │
    ├─ Case 3: 隐含条件补全
    │     "推荐个CPU" → "推荐 [用户预算范围内] + [用途匹配] + CPU"
    │
    ├─ Case 4: 组合场景
    │     "再加个电源" → "在 [当前配置方案] 基础上，增加一个兼容的电源"
    │
    └─ 输出: enhanced_query（用于检索的增强查询）
```

**实现策略：**

当前 `build_contextual_goal()` 是纯规则函数，升级为"规则 + LLM 轻量改写"双通道：

```python
def rewrite_query(message: str, session: ShoppingSession) -> str:
    """Rewrite user query with context resolution."""
    
    # 快速路径：明确的新话题，不改写
    if should_start_new_product_topic(session, message):
        return message
    
    # 规则改写：处理简单的代词和属性继承
    rule_rewritten = _rule_based_rewrite(message, session)
    if rule_rewritten:
        return rule_rewritten
    
    # LLM 改写：处理复杂的消解和补全
    if _needs_llm_rewrite(message, session):
        return _llm_rewrite(message, session)
    
    return message
```

**规则改写覆盖的场景（不消耗 LLM 调用）：**

- "这个"/"这款"/"这两个" → 替换为 `session.last_result` 中最近推荐的商品标题
- "便宜点的"/"贵一点的" → 继承 `session.current.brands` + 调整 `price_max`
- "还有吗"/"再推荐几个" → 继承完整的 `session.current`

**LLM 改写触发条件（`_needs_llm_rewrite`）：**

- 消息 ≤ 6 字符且是追问（如"白色的呢"）
- 消息包含代词但无法通过规则消解
- 消息包含"换成"/"不要"/"改成"等约束修改信号

---

### 目标 4：路由分发器

**当前状态：** `chat.py` 中 `if/elif` 硬编码分发链。工具之间无共享逻辑。

**设计目标：**

```
intent + entities + confidence
    │
    ├─ confidence ≥ 0.7 → 直接分发
    │     intent → handler 映射表（注册式，非硬编码）
    │
    ├─ 0.4 ≤ confidence < 0.7 → 澄清
    │     向用户确认意图（"你是想对比这两款商品，还是想了解参数？"）
    │
    └─ confidence < 0.4 → general_chat
          不累积推荐状态
```

**Handler 注册表（替换 if/elif）：**

```python
# tool_handlers.py
_HANDLER_REGISTRY: Dict[str, Callable] = {
    "recommend_shopping_products": handle_recommend,
    "compare_products": handle_compare_v2,
    "generate_pc_build_plan": handle_pc_build,
    "apply_cart_instruction": handle_cart_v2,
    "parameter_query": handle_parameter_query,       # 新增(需设计)
    "price_comparison": handle_price_comparison,     # 新增(需设计)
    "sku_detail": handle_sku_query,                  # 新增(需设计)
    "general_chat": handle_general_chat,
}

def dispatch(session, tool_call, context):
    handler = _HANDLER_REGISTRY.get(tool_call["name"])
    if not handler:
        handler = handle_general_chat
    yield from handler(session, tool_call, **context)
```

**好处：** 新增工具只需注册一行，不需要修改 `chat.py` 的分发逻辑。

---

### 目标 5：检索管道（核心改造）

**当前状态：** `structured_filter.py` 的 10 步确定性过滤链 + `scorer.py` 的 7 维评分。无向量检索。`brands` 白名单不参与硬过滤（仅做 scorer 加分）。

**设计目标：三层检索管道**

```
RequirementSpec + ProductCatalog
    │
    ├─ Layer 1: 确定性过滤（硬约束，必须通过）
    │     ├─ category 精确匹配
    │     ├─ is_available() 库存过滤
    │     ├─ brand_whitelist_filter()  ← 新增：品牌白名单硬过滤
    │     ├─ brand/text exclusion 排除过滤
    │     ├─ sub_category 匹配
    │     ├─ must_have_terms 关键词匹配
    │     └─ budget 预算匹配
    │     每步有空结果安全降级（保持现有逻辑）
    │
    ├─ Layer 2: 向量检索 + 关键词混合检索
    │     ├─ query_embedding = embed(enhanced_query)
    │     ├─ vector_candidates = milvus.search(query_embedding, top_k=30,
    │     │                                      filters={category, brand_whitelist})
    │     ├─ keyword_candidates = bm25_search(enhanced_query, top_k=20)
    │     └─ merged = reciprocal_rank_fusion(vector_candidates, keyword_candidates,
    │     │                                    weights=[0.6, 0.4])
    │
    ├─ Layer 3: 评分重排 + 多样性控制
    │     ├─ score_products()（保持现有 7 维评分）
    │     ├─ MMR 多样性控制：同品类最多 3 个，同品牌连续不超过 2 个
    │     └─ 输出 top-N 结果
    │
    └─ 输出: List[ApiProduct]（已排序、已去重、已通过所有约束）
```

**品牌白名单硬过滤（P0 修复项，详见 case12-20_fix_plan_v2.md P0-2）：**

```python
def brand_whitelist_filter(products, brands):
    """Hard filter: only keep products matching required brands.
    Graceful fallback: if filter empties result, keep pre-filter list."""
    if not brands:
        return products
    filtered = [p for p in products if _matches_brand_requirement(p, brands)]
    return filtered if filtered else products  # 安全降级
```

**向量检索接入：**

当前系统已有 Milvus 基础设施（`rag/storage/` 目录和 `use_milvus_retrieval` 参数），但仅用于 embedding 相似度搜索，未在推荐管道的主过滤链中使用。目标是将向量检索作为 Layer 2 集成到 `filter_products_for_requirement()` 之后、`score_products()` 之前。

**实现优先级：** Layer 1 的品牌白名单修复是 P0（立即实施），Layer 2 的向量检索集成是 P1（第二批），Layer 3 的 MMR 多样性控制是 P2。

---

### 目标 6：对比链路（重构）

**当前状态：** `handle_compare_v2()` 无降级逻辑，`product_ids` 为空时直接返回错误。PC 方案对比函数 `compare_pc_build_plans()` 已存在但未接入。

**设计目标：**

```
compare_products 意图
    │
    ├─ Step 1: 获取 product_ids（三级降级）
    │     ├─ 降级 1: router 传递的 product_ids / product_mentions
    │     ├─ 降级 2: last_recommended_product_ids(session)
    │     └─ 降级 3: comparison_candidate_ids(query)
    │
    ├─ Step 2: 话题检测
    │     ├─ topic_type == "pc_build" → PC 方案对比（独立 SSE 事件）
    │     └─ 其他 → 普通商品对比
    │
    ├─ Step 3: 校验 + 事实检查
    │     ├─ product_id 存在性（catalog.get）
    │     ├─ 同品类检测
    │     └─ 价格区间检测
    │
    ├─ Step 4: 执行对比
    │     ├─ 普通: compare_products(catalog, valid_ids) → comparison_table SSE
    │     └─ PC: compare_pc_build_plans(plan_a, plan_b) → pc_comparison_table SSE
    │
    └─ Step 5: 降级兜底
          所有降级均失败 → 转为 recommend_shopping_products 推荐同类商品
```

**详细代码方案见 `case12-20_fix_plan_v2.md` P0-1 和 P1-3。**

---

### 目标 7：LLM 编排层（新增）

**当前状态：** LLM 调用散落在 5+ 个文件中，每个调用点独立实例化 `OpenAICompatibleChatClient`，各自设定 timeout、temperature、max_tokens。

**设计目标：**

```
LLM Gateway（统一入口）
    │
    ├─ 配置管理：统一 model/timeout/temperature 配置
    │     ├─ router: temperature=0, timeout=15s, model=fast_model
    │     ├─ guidance: temperature=0.2, timeout=8s, model=main_model
    │     ├─ filter: temperature=0.0, timeout=12s, model=fast_model
    │     ├─ response: temperature=0.9, timeout=5s, model=main_model
    │     └─ explanation: temperature=0.1, timeout=8s, model=fast_model
    │
    ├─ 并发控制：全局信号量（当前仅 router 有，其他调用点无）
    │
    ├─ 熔断器：统一熔断逻辑（当前仅 router 有 circuit breaker）
    │     └─ 改进：增加 half-open 状态，单次成功不完全重置
    │
    ├─ 缓存：相同 prompt + 参数的结果缓存（TTL 5 分钟）
    │
    └─ 可观测性：每次调用记录 model, tokens, latency, success/failure
```

**实现方式：**

```python
class LLMGateway:
    """Unified LLM call orchestration layer."""
    
    _callers: Dict[str, _LLMCallerConfig] = {}
    
    @classmethod
    def register(cls, name: str, *, model: str, temperature: float,
                 timeout: float, max_tokens: int, max_concurrency: int = 5):
        cls._callers[name] = _LLMCallerConfig(...)
    
    @classmethod
    def call(cls, caller_name: str, messages: List[Dict],
             **overrides) -> Tuple[Dict, CallReport]:
        config = cls._callers[caller_name]
        # 统一超时、并发控制、熔断、异常处理
        ...

# 注册各调用场景
LLMGateway.register("router", model="fast", temperature=0, timeout=15, max_tokens=320)
LLMGateway.register("guidance", model="main", temperature=0.2, timeout=8, max_tokens=1500)
LLMGateway.register("filter", model="fast", temperature=0, timeout=12, max_tokens=500)
LLMGateway.register("response", model="main", temperature=0.9, timeout=5, max_tokens=200)
LLMGateway.register("explanation", model="fast", temperature=0.1, timeout=8, max_tokens=700)
```

**改造路径：** 不要求一次性迁移所有调用点，可以逐步替换。每个调用点的替换是独立的，因为 `LLMGateway.call()` 的返回值格式与当前 `client.chat_json_with_report()` 一致。

---

### 目标 8：会话状态管理（重构）

**当前状态：** `ShoppingSession` 是一个 21 字段的可变 dataclass，同时承载购物车状态、话题记忆、路由累积、PC 构建历史等多种职责。存储使用模块级全局变量。

**设计目标：分层状态对象**

```
ShoppingSession
    │
    ├─ ConversationState（对话级）
    │     ├─ session_id
    │     ├─ messages: List[str]（最近 12 条）
    │     ├─ recent_queries: List[Dict]（最近 5 轮）
    │     └─ chat_topic: str
    │
    ├─ RecommendationState（推荐级）
    │     ├─ current: Dict（累积路由参数）
    │     ├─ last_goal: str
    │     ├─ last_result: Any
    │     └─ last_requirement: RequirementSpec
    │
    ├─ CartState（购物车级）
    │     ├─ cart: Dict[str, CartItem]
    │     └─ pending_cart_action: Dict
    │
    ├─ PCBuildState（PC 构建级）
    │     ├─ pc_build_history: List[Dict]
    │     └─ current_pc_build: Dict
    │
    └─ ObservabilityState（可观测级）
          ├─ topic_memory: Dict
          ├─ topic_history: List[Dict]
          ├─ llm_call_log: List[Dict]
          └─ last_fact_check_status: str
```

**核心改进：**

1. **职责分离：** 每个子状态有明确的读写方和生命周期，减少隐式耦合。
2. **版本化：** `ShoppingSession` 增加 `schema_version: int` 字段，支持向后迁移。
3. **不可变快照：** 提供 `session.snapshot()` 方法返回冻结副本，用于异步操作和日志记录。
4. **存储抽象增强：** 保持当前 `BaseSessionStore` 协议，但增加分布式锁支持（Redis `SET NX`）。

---

### 目标 9：可观测性

**当前状态：** `_end_span()` 记录 20 条结构化日志到 `session.llm_call_log`。`routing_trace` 记录路由决策。但无链路追踪（trace_id 贯穿请求全生命周期），无降级路径日志，无指标聚合。

**设计目标：**

```
每次请求生成唯一 trace_id，贯穿全链路：

trace_id: "abc-123"
    │
    ├─ span: sanitize_input     (0.1ms)
    ├─ span: route_tool_call    (150ms, source=llm, confidence=0.85)
    │     ├─ span: local_route  (2ms, result=recommend)
    │     └─ span: llm_route    (148ms, result=recommend)
    ├─ span: validate_tool_call (0.5ms, conflict=false)
    ├─ span: update_session     (1ms)
    ├─ span: handle_recommend   (800ms)
    │     ├─ span: requirement_build  (5ms)
    │     ├─ span: filter_products    (10ms, stages=10, returned=8)
    │     ├─ span: score_products     (3ms)
    │     ├─ span: llm_guidance       (200ms, success=true)
    │     ├─ span: fact_check         (2ms, degraded=false)
    │     └─ span: response_gen       (150ms, mode=llm)
    └─ span: total              (970ms)
```

**实现方式：** 不需要引入 OpenTelemetry 全家桶。在现有 `_end_span()` 基础上扩展为树状结构：

```python
@contextmanager
def trace_span(name: str, trace_id: str, parent_id: str = None):
    span = {"name": name, "start_ns": time.perf_counter_ns(), "trace_id": trace_id}
    try:
        yield span
    finally:
        span["duration_ms"] = (time.perf_counter_ns() - span["start_ns"]) / 1e6
        _record_span(span)
```

**关键指标（日志聚合后可查询）：**

- 路由准确率（LLM vs 本地一致率）
- 各 handler 平均耗时
- 降级路径触发频率（`fallback_source` 分布）
- LLM 调用成功率（按 caller_name 分）
- 品牌过滤命中率（`brand_whitelist_filter` 降级 vs 通过）
- 事实校验降级率（`fact_check.degraded=True` 占比）

---

## 三、新增工具处理器设计

### 3.1 parameter_query（参数查询）

**触发模式：** "这个显卡功耗多少"、"MateBook 14 重量多少"、"这款有 NFC 吗"

```python
def handle_parameter_query(session, tool_call, **context):
    """Answer factual questions about specific product attributes."""
    arguments = tool_call.get("arguments") or {}
    product_mentions = arguments.get("product_mentions") or []
    attribute = arguments.get("attribute") or ""
    
    catalog = load_combined_product_catalog()
    
    # 从 product_mentions 或 session.last_result 获取商品
    product = _resolve_product(catalog, product_mentions, session)
    if not product:
        yield sse_event("delta", {"text": "你想了解哪款商品的参数？可以告诉我具体型号。"})
        yield sse_event("done", {"session_id": session.session_id})
        return
    
    # 从产品数据中提取属性
    answer = _extract_attribute(product, attribute)
    yield sse_event("delta", {"text": answer})
    yield sse_event("done", {"session_id": session.session_id})
```

### 3.2 sku_detail（SKU 查询）

**触发模式：** "12+256 和 16+512 差多少钱"、"32G+1TB 什么价"

详细方案见 `case12-20_fix_plan_v2.md` P2-2。

### 3.3 price_comparison（比价查询）

**触发模式：** "京东上这款多少钱"、"比官网便宜吗"

```python
def handle_price_comparison(session, tool_call, **context):
    """Compare prices across platforms (when external APIs available)."""
    arguments = tool_call.get("arguments") or {}
    product_mentions = arguments.get("product_mentions") or []
    
    catalog = load_combined_product_catalog()
    product = _resolve_product(catalog, product_mentions, session)
    
    if not product:
        yield sse_event("delta", {"text": "你想比价哪款商品？"})
        yield sse_event("done", {"session_id": session.session_id})
        return
    
    # 当前阶段：返回库中价格信息 + SKU 变体
    skus = product.skus or []
    lines = [f"「{product.title}」的价格信息："]
    for sku in skus:
        props = " / ".join(sku.properties.values())
        lines.append(f"- {props}：¥{sku.price or product.base_price}")
    
    yield sse_event("delta", {"text": "\n".join(lines)})
    yield sse_event("done", {"session_id": session.session_id})
```

---

## 四、当前架构中值得保留的设计

在重构过程中，以下设计模式经验证有效，应予以保留：

1. **LLM-first + 本地规则降级 的双通道路由**。这是系统可靠性的基石。LLM 不可用时仍能服务，优于纯 LLM 方案。

2. **SSE 事件流**。`sse_event()` 生成器模式天然支持流式返回，`safe_stream()` 包装器提供了异常安全网。

3. **事实校验层**。`fact_check_result()` 的价格偏差校验和 product_id 存在性验证是防止 LLM 幻觉的关键防线。

4. **购物车"计划+确认"模式**。`handle_cart_v2()` 的 pending → confirm 两步操作防止误操作。

5. **`FilterDiagnostics` 过滤漏斗**。每一步过滤后的候选数量记录，是排查"为什么推荐为空"的核心工具。

6. **`ProductCatalog` 冻结数据类**。不可变的 catalog 对象避免了并发读写问题，`by_id` 和 `by_category` 索引支持 O(1) 查找。

7. **`routing_trace` 结构化路由追踪**。每个路由决策都附带来源、耗时、冲突信息，便于事后分析。

---

## 五、实施路线图

### 第一阶段：紧急修复（1-2 天）

| 任务 | 涉及文件 | 影响 |
|---|---|---|
| P0-1: handle_compare_v2 恢复降级链 | `tool_handlers.py` | 修复 11 处对比失败 |
| P0-2: 品牌白名单硬过滤 | `structured_filter.py` | 修复品牌返回错误 |
| P0-3: `__CLEAR__` 累积约束清除 | `session_state.py` | 修复多轮过滤过严 |
| P1-2: 0 卡片响应文本一致性 | `response_generator.py` | 修复文本与数据不一致 |

### 第二阶段：意图扩展（3-5 天）

| 任务 | 涉及文件 | 影响 |
|---|---|---|
| 扩展 LLM router 工具定义（+3 个意图） | `tool_router.py` | SKU/参数/比价正确路由 |
| 新增 3 个 handler 并注册 | `tool_handlers.py` | 新意图有对应处理器 |
| `chat.py` 分发链改为注册表 | `chat.py` | 新工具无需改入口 |
| 增加 `product_mentions` 提取 | `tool_router.py` | 对比链路可获取 ID |

### 第三阶段：检索增强（5-7 天）

| 任务 | 涉及文件 | 影响 |
|---|---|---|
| 向量检索集成到推荐管道 | `recommendation_pipeline.py` | 召回率提升 |
| 混合检索 + RRF 融合 | 新增 `retrieval_fusion.py` | 多源结果融合 |
| 查询改写器（规则 + LLM） | 新增 `query_rewriter.py` | 多轮对话理解增强 |
| MMR 多样性控制 | `package_builder.py` | 推荐多样性 |

### 第四阶段：架构治理（7-10 天）

| 任务 | 涉及文件 | 影响 |
|---|---|---|
| LLM Gateway 统一编排 | 新增 `llm_gateway.py` + 迁移各调用点 | LLM 调用统一管理 |
| Session 状态分层 | `session_state.py` | 降低耦合 |
| Handler 公共逻辑提取 | `tool_handlers.py` | 减少重复代码 |
| 链路追踪（trace_span） | `chat.py` + 各 handler | 可观测性增强 |
| LLM 异常兜底完善 | `recommendation_pipeline.py` 等 | 增加 ConnectionError/PermissionError 捕获 |
| 回归测试覆盖 | `tests/` | 防止问题复发 |

---

## 六、设计原则总结

1. **确定性优先，LLM 增强**：能用规则解决的不用 LLM（品牌过滤、库存校验、价格校验），LLM 用于规则无法覆盖的场景（语义理解、代词消解、品牌别名）。

2. **每层有降级**：LLM 不可用 → 本地规则；向量检索超时 → 纯规则过滤；品牌过滤后为空 → 保留过滤前结果。系统在任何单点故障下都能返回结果。

3. **显式优于隐式**：约束清除用 `__CLEAR__` sentinel 而非启发式猜测；品牌过滤用硬过滤 + 安全降级而非仅 scorer 加分；路由置信度用概率值而非布尔判断。

4. **注册优于硬编码**：Handler 注册表替换 if/elif 分发链；LLM 配置注册表替换散落的环境变量；工具定义注册表替换 prompt 中的硬编码字符串。

5. **可观测是功能**：每个降级路径有日志，每个 LLM 调用有 trace，每个过滤步骤有计数。线上排查不依赖复现，靠日志即可定位。

---

*文档完。*
