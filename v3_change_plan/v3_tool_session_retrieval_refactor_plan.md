# V3 工具、检索过滤与会话状态改造施工方案

## 0. 结论与边界

本方案基于当前代码链路核验，而非历史评测/设计文档。建议将对外 Router 工具从 8 个收敛为 4 个：

```text
apply_cart_instruction
general_chat
parameter_query
recommend_shopping_products
```

这个收敛是合理的，但只能收敛 **Router 的对外动作名**，不能直接删除比较、SKU、比价和完整装机的内部能力：

| 现有对外工具 | V3 对外工具 | V3 内部 operation / mode | 必须保留的现有能力 |
| --- | --- | --- | --- |
| `apply_cart_instruction` | `apply_cart_instruction` | `add/remove/set_quantity/...` | 60 秒确认、购物车状态 |
| `general_chat` | `general_chat` | - | 非商品闲聊回复 |
| `parameter_query` | `parameter_query` | `attribute` | 参数/规格查询 |
| `sku_detail` | `parameter_query` | `sku` | SKU 解析与商品定位 |
| `price_comparison` | `parameter_query` | `price` | 商品价格与官方价字段回复 |
| `compare_products` | `parameter_query` | `compare` | 多商品定位、比较表、PC 方案比较 |
| `recommend_shopping_products` | `recommend_shopping_products` | `product/bundle/pc_part` | RAG 推荐、套装、PC 单配件推荐 |
| `generate_pc_build_plan` | `recommend_shopping_products` | `pc_build` | 完整装机、兼容性校验、PC 状态与方案比较 |

因此，V3 的目标不是把所有代码压到四个大函数，而是“4 个稳定入口 + 明确的判别字段 + 可复用的内部 handler”。这样能缩短 Router prompt、降低 JSON schema 出错面，也不会牺牲比较表和装机兼容性。

## 1. 已核验的当前真实链路

### 1.1 请求主链路

`rag/api/routes/chat.py::chat_stream` 的当前链路为：

```text
HTTP/SSE 请求
  -> sanitize_input
  -> get_session（立即完整序列化并写入 session store）
  -> resolve_runtime_policy
  -> route_shopping_tool_call
       -> local_route_tool_call 优先
       -> 可用时 LLM Router，否则本地规则兜底
  -> validate_tool_call
  -> update_session_from_router（购物车、闲聊等少数分支不更新）
  -> 轻工具：直接分派
       cart / chat / compare / parameter / sku / price
  -> 重工具：prepare_recommendation_context
       -> generate_pc_build_plan：handle_pc_build
       -> 其余：handle_recommend
```

现有 `_LIGHTWEIGHT_TOOLS` 的确包含比较、参数、SKU、比价；推荐和完整装机在重链路中。这个划分不是按“工具数量”而是按是否需要多模态上下文、RAG 推荐流水线。但它让 `chat.py` 同时承担了八个工具名的分支，Router schema 也被迫暴露了八份定义。

### 1.2 为什么不能把完整装机直接当普通推荐

普通推荐最终走 `handle_recommend -> build_recommendation_result`，结果是商品卡片；它可处理 `pc_parts` 的单配件推荐与替换。完整装机则走：

```text
handle_pc_build
  -> build_pc_plan_for_message
  -> generate_pc_build_plan
  -> check_pc_build_compatibility
  -> pc_build_plan SSE + pc_build_history/current_pc_build 持久化
```

完整装机需要生成多个组件、价格汇总、兼容性结果、后续调配以及方案比较。故 V3 只能把其 **Router 名称** 合入 `recommend_shopping_products`，然后在推荐 handler 内按 `mode=pc_build` 尽早分流到现有 PC 流程；不能删除 `pc_build.py`、兼容性检查或 PC session flow。

另有一个应优先修复的现状：`chat_stream` 已生成 `contextual_goal` 和附件上下文，但 `handle_pc_build` 实际仍把原始 `message` 交给 `build_pc_plan_for_message`。这使完整装机不能稳定消费已归一化的路由参数/附件提取约束。V3 应让 PC builder 接收归一化后的 requirement 与上下文，而不是仅依赖原句解析。

### 1.3 为什么比较不能退化成普通参数查询

`handle_compare_v2` 不只是回答某一商品字段：它会从显式 ID、上一轮推荐、标题匹配或候选推荐中定位 2--3 个目标；支持 PC 方案间比较；并产出 `comparison_table` SSE 结构与胜出结论。V3 可将其收纳为 `parameter_query.operation=compare`，但应保留多目标解析和比较 handler，且保持前端事件契约不变。

相反，`answer_parameter_query`、`answer_sku_query`、`answer_price_comparison` 都复用 `resolve_product_from_context`，差异主要是回答字段与输出文本，适合重构成一个商品信息服务。

## 2. V3 工具契约与分派设计

### 2.1 建议的统一 Router 输出

将当前大量共用/互相无关字段的八套 function schema，替换为四个对外 tool schema；各 schema 仅保留该动作实际需要的字段。

```json
{
  "name": "parameter_query",
  "arguments": {
    "operation": "attribute | sku | price | compare",
    "product_ids": ["..."],
    "product_mentions": ["..."],
    "attribute": "...",
    "compare_with_previous": false
  }
}
```

```json
{
  "name": "recommend_shopping_products",
  "arguments": {
    "mode": "product | bundle | pc_part | pc_build",
    "catalog_scope": "normal | pc_parts",
    "price_min": null,
    "price_max": null,
    "brands": [],
    "categories": [],
    "sub_categories": [],
    "must_have_terms": [],
    "preferences": {},
    "need_comparison": false
  }
}
```

购物车保留它自己的 `operation`、商品目标与数量；闲聊不携带商品检索字段。公共约束可抽成内部 `RouteContext`，不要为了“统一”而让闲聊和购物车 schema 也承载价格、品牌、PC 字段。

### 2.2 V3 目标分派

```text
RoutedAction（4 个 name + operation/mode）
  ├─ apply_cart_instruction -> handle_cart_v2
  ├─ general_chat           -> handle_general_chat
  ├─ parameter_query
  │    ├─ attribute -> 商品信息服务.attribute
  │    ├─ sku       -> 商品信息服务.sku
  │    ├─ price     -> 商品信息服务.price
  │    └─ compare   -> handle_compare_v2（保留 comparison_table）
  └─ recommend_shopping_products
       ├─ pc_build  -> handle_pc_build（完整方案/兼容性）
       └─ product/bundle/pc_part -> prepare_recommendation_context
                                  -> handle_recommend
```

`pc_build` 分支应在普通推荐前决定。是否准备视觉/附件上下文要由 `mode` 和附件存在与否决定：有附件的完整装机必须把结构化提取结果传入 PC builder；纯文本装机无需为了普通商品推荐的所有预处理而重复工作。

### 2.3 迁移方式（避免一次性删代码）

1. 新增 `RoutedAction`/V3 schema 和 `operation`/`mode` 分派，先保留旧工具名到新 action 的适配器。
2. 更新本地规则路由和 LLM Router prompt，使它们只产出 4 个 name；旧 name 只允许适配层读取，禁止再作为新输出。
3. 让 `chat_stream` 只按 4 个 name 分派，保持当前 SSE 类型（尤其 `comparison_table`、`pc_build_plan`）不变。
4. 重构商品信息三个薄 wrapper 为一个内部服务；比较和装机保留为专用内部 handler。
5. 完成回归测试、灰度日志比对后，删除旧 tool schema、旧 allowed name、旧 Router prompt 分支和无调用的 wrapper。删除门禁为 `rg` 无生产代码引用 + 全量路由/端到端测试通过，不能在第一步就删。

## 3. Metadata 预过滤：当前实际实现与 V3 改法

### 3.1 当前行为：向量召回前，但只按两项元数据

在 `rag/recommendation/retrieval.py::EvidenceRetriever._retrieve_variant` 中，每个 `ComponentCategory` 都先构造：

```text
chunk_level == 3 && category == "<ComponentCategory.value>"
```

这个表达式作为 `filter_expr` 传给 `rag/storage/milvus_client.py::hybrid_retrieve`。稠密 ANN、稀疏 ANN 和 dense fallback 都带着该表达式，所以它是 **Milvus 召回前过滤**，不是 RRF 后过滤。

当前真正用于该预过滤的“关键词”不是用户输入的品牌/价格词表，而是代码解析出的 `ComponentCategory` 枚举值；例如商品大类与 PC 配件大类。`chunk_level=3` 固定选叶子商品 chunk。某些场景会把 category/scenario/task type 追加到检索 query 文本，但这只是 query expansion，不是 metadata filter。

索引 chunk 虽含有 `product_id`、`brand`、`sub_category`、`category_name` 等 metadata，当前 `filter_expr` **没有** 用品牌、价格、库存、子类、SKU 或用户关键词过滤。价格/规格等多数信息也在文本内，而非可直接安全筛选的 Milvus 数值字段。

### 3.2 当前的后置约束链路

`package_builder.build_recommendation_result` 取得证据后，在 `score_required_components` 中先以本地商品目录执行 `filter_products_for_requirement`，再同向量证据融合、打分，之后还有预算等后检验。实际承担商品正确性的约束包括：库存排除、品牌白/黑名单、精确子类、类型、must-have、PC 兼容约束、偏好及预算（必要时有受控放宽）。

因此当前架构是：

```text
Milvus 召回前：leaf chunk + ComponentCategory
Milvus 召回后/融合前：本地目录的品牌、子类、库存、预算等结构化约束
融合与排序后：预算/结果完整性等最终校验
```

不能把“已有 category 预过滤”表述成“品牌、价格、库存 metadata 预过滤已完成”。

### 3.3 V3 检索改造

1. 从 requirement 生成一份类型化 `RetrievalFilters`，至少含 `categories`、已校验的 `brands`、精确 `sub_categories`、显式 `product_ids`；所有值由枚举/目录校验和安全转义后才可进入 Milvus expression。
2. 分层使用：
   - 绝对约束：`chunk_level`、category、明确 product ID，可安全前置；
   - 高置信精确约束：品牌、子类，可作为前置缩小候选，同时保留后置校验；
   - 易变化/索引不完整约束：库存、价格、促销、评分，不前置为唯一真相，继续以实时目录后置过滤。
3. 若要做价格/库存前置，先在索引 schema 中新增明确的数值/状态字段及索引版本、更新时间；不允许从 chunk 文本反推数值后直接作为检索事实。
4. 在检索 trace 中记录 `pre_filter_expr`、前置候选量、后置淘汰原因、放宽原因。这样可区分“召回为空”“过滤过严”“目录无货”。
5. 对每种 filter 写断言测试：Milvus 请求包含预期表达式；即使索引 metadata 过期，后置库存/预算校验仍不能放行错误商品。

## 4. Session：当前问题、是否影响 LLM、V3 方案

### 4.1 当前真实状态

`ShoppingSession` 目前会持久化完整扁平对象，包含：`last_result`、`pc_build_history`、`current_pc_build`、`topic_memory`、`tool_history`、`last_requirement`、`recent_turns`、`recent_turns_summary`、`topic_history`、`recent_queries`、`llm_call_log` 等。Redis store 通过 `asdict -> json.dumps -> setex` 每次写入整个 session。

`get_session` 在每一个请求开始就更新 `updated_at` 并调用 `save_session`；路由更新和多个 handler 还会继续调用保存。也就是说，Redis 场景不仅 payload 偏大，同一轮还有多次全量序列化/网络写入的可能。

主要重复/膨胀来源：

- `last_result` 保存完整推荐 payload，可能包含检索 trace、证据、附件分析、过滤诊断等；
- `pc_build_history` 保存完整方案，且最新方案还可能同时存在于 `last_result` 与 `current_pc_build`；
- `messages`、`last_goal`、`last_requirement`、`recent_turns`、`recent_queries`、`topic_history`、`topic_memory.history` 存在不同层次的重叠；
- `llm_call_log` 限制为 20 条但仍是业务 session 的一部分，属于观测信息而非会话决策必需状态。

### 4.2 这是否已经直接压大 Router 的 LLM 输入

**不直接等同。** `tool_router._build_router_user_prompt` 当前只投影了 `session.current`、最近 3 条 `recent_queries`、topic memory 的主题信息、最多 5 张上一轮商品卡片和最多 8 个购物车项；它没有把完整 `last_result`、PC 历史、recent turns 或 llm log 原样塞进 Router prompt。

因此，裁剪 session 的直接收益是 Redis 读写/反序列化、延迟、内存、调试数据泄露面与状态混乱风险；若目标是降低 LLM 压力，还需要显式收紧 **RouterContext** 和各 handler 的输入，而不能只删 Redis 字段。

### 4.3 V3 session 目标模型

保留“可驱动下一步”的最小业务状态，并把重结果和观测数据移出主 session：

```text
SessionCore
  cart / pending_cart_action                 # 事务状态，必须持久化
  route_context                              # 当前品类、预算、品牌、目标、最近 3 个约束增量
  last_displayed_items (<=5)                 # id/title/brand/price/category，供指代解析
  last_pc_build_summary                      # 组件 product_id、预算、兼容摘要、plan_id
  pc_build_history_summaries (<=2~3)         # 用于方案比较，不存全量 trace
  recent_turns (<=4) + compact summary       # 只保留可解释的约束/操作，不重复原始 payload
  schema_version

TraceStore（独立 TTL/key）
  retrieval evidence、附件分析、完整 response、llm_call_log、调试诊断
```

建议新增一个纯函数 `build_router_context(session)`，将 Router 需要的字段限制为固定预算；各 LLM handler 同样使用自己的最小上下文 DTO。不要让业务函数再随意读取整个 `ShoppingSession`。

### 4.4 持久化与迁移

1. 将“本轮 dirty state”集中在请求结束时一次保存；购物车 `plan -> confirm` 等必须在需要跨请求生效的节点显式 checkpoint，不能因批量保存而丢失确认状态。
2. `remember_recommendation` 只抽取商品卡片摘要和必要的 requirement delta；完整 result 写入 trace store 或仅通过响应返回。
3. PC plan 保存 summary + 稳定 `plan_id`；若方案比较确实需要完整组件/兼容信息，则按 `plan_id` 从独立 PC plan store 读取，不在通用 session 复制多份。
4. schema 升至 V3：读到 V2 JSON 时，从旧 `last_result` 和 `pc_build_history` 提取 compact fields；失败时降级为只保留购物车和当前约束，不阻断用户会话。
5. 监控每次 session 的序列化字节数、每请求 save 次数、Redis P95、Router 输入 token 数；以基线与压测数据决定最终上限，不凭感觉设置字段长度。

## 5. 其他当前链路中应一并处理的问题

1. **Router prompt/schema 过大且字段耦合。** 八套工具和大量无关公共字段要求模型一次性选择 name、填复杂参数，增加 JSON 不合法和意图错配面。收敛到四套小 schema，并让 operation/mode 成为明确判别字段。
2. **路由与执行参数脱节。** 完整 PC 目前主要重新解析原始文本；V3 必须把已验证的 `RoutedAction`/requirement 传进 PC builder，附件提取的结构化条件也应进入同一个对象。
3. **比较、SKU、价格的目标解析应中心化。** 现有三类信息查询已共享 `resolve_product_from_context`，比较又有另一套多目标定位。应抽为 `resolve_single_target` / `resolve_multi_targets`，统一处理显式 ID、商品名、上一轮卡片与歧义澄清。
4. **Runtime mode 目前是启发式。** `runtime_context.py` 以少量关键词和是否有历史决定 mode；它可作为性能策略，但不应影响业务 intent 的唯一判断。V3 将 route intent 和 runtime policy 分开记录/测试。
5. **SSE 是兼容边界。** 内部工具合并后不得改掉 `comparison_table`、`pc_build_plan`、购物车确认等前端依赖事件；如需新事件，先做向后兼容双发并完成前端迁移。
6. **代码/注释漂移。** `validate_tool_call` 的说明与实际仍在执行校验的行为不一致。重构时同步更新函数名、注释与测试，避免再次用旧描述误导设计。

## 6. 施工任务拆解与验收

### P0：建立可回归基线

- 为当前八工具本地路由、LLM Router（可 mock）、SSE 事件建立用例快照。
- 覆盖：普通推荐、套装、单 PC 配件、完整装机、装机调配、PC 方案比较、商品比较、参数、SKU、比价、购物车两步确认、闲聊。
- 记录 session payload 字节数、每请求 save 次数、Router prompt token 数、检索 pre/post filter 计数。

**验收：** 基线可重复运行，且不依赖 `reports/` 作为运行产物目录。

### P1：引入四工具动作模型（不删除旧代码）

- 修改：`rag/recommendation/tool_router.py`、`rag/api/routes/chat.py`。
- 新增 4-name schema、`parameter_query.operation`、`recommend_shopping_products.mode` 与 V2->V3 action adapter。
- 同步更新 local route 规则、LLM Router prompt、参数校验和路由 trace。

**验收：** 新 Router 永不输出旧的四个合并前 name；adapter 下旧 fixture 仍能跑通；所有旧 SSE 事件保持。

### P2：重构执行分派与内部服务

- 修改：`rag/api/routes/chat.py`、`rag/recommendation/tool_handlers.py`、`rag/recommendation/product_info_tools.py`、`rag/recommendation/product_reference.py`、`rag/recommendation/pc_session_flow.py`。
- 建立商品信息 operation dispatcher；比较继续调用专用比较核心。
- 将 normalized requirement、router args、附件提取结果传入 PC builder；保留兼容性校验与 PC plan 状态。

**验收：** `mode=pc_build` 生成完整且兼容的方案；`operation=compare` 仍返回比较表；SKU/价格/参数可通过上一轮商品指代定位。

### P3：检索过滤可观测化与安全前置

- 修改：`rag/recommendation/retrieval.py`、`rag/storage/milvus_client.py`；如新增索引字段，再修改 `rag/ingestion/product_chunks.py` 与建库/迁移脚本。
- 落地 `RetrievalFilters`、安全 expression builder、pre/post filter trace。
- 保持库存、实时价格等后置校验为最终事实来源。

**验收：** 测试能证明 filter 在 Milvus request 前生效；metadata 过期时最终商品不会绕过库存/预算校验。

### P4：Session V3 紧凑化

- 修改：`rag/recommendation/session_state.py`、`rag/recommendation/session_context.py`、Router context 构建处及观测写入处。
- 提取 `SessionCore` / `RouterContext` / TraceStore；实现 V2 load migration、一次请求一次常规写入和必要 checkpoint。

**验收：** V2 session 可读；多轮指代、购物车确认、PC 调配/比较不回退；session 字节数和保存次数相较 P0 有明确下降，Router token 预算不增长。

### P5：清理与发布

- 删除旧 Router tool schema、废弃常量、仅服务旧 name 的分支/wrapper；保留比较和 PC 的业务核心。
- 更新 README/架构说明与测试命令，注明“四工具是对外契约，内部仍有专用能力”。
- 运行静态引用检查、单元/集成/端到端测试和 Milvus 实测。

**验收：** 生产代码中不存在旧对外工具名作为 Router 输出；全量测试通过；性能、正确性、SSE 兼容性指标达到 P0 定义的门槛。

## 7. 预计影响文件

核心改动：

```text
rag/api/routes/chat.py
rag/recommendation/tool_router.py
rag/recommendation/tool_handlers.py
rag/recommendation/product_info_tools.py
rag/recommendation/product_reference.py
rag/recommendation/pc_session_flow.py
rag/recommendation/session_state.py
rag/recommendation/session_context.py
rag/recommendation/retrieval.py
rag/storage/milvus_client.py
rag/ingestion/product_chunks.py          # 仅在新增可索引 metadata 字段时
tests/...                                # 路由、SSE、检索、session 迁移与性能回归
```

不应因工具收敛而删除的核心包括：`comparison.py`、`pc_build.py`、兼容性检查、购物车确认逻辑，以及现有商品引用解析能力；它们应由新的四工具动作模型复用。

## 8. 验收官补充审计：Runtime Mode 是应删除的并行控制面

### 8.1 当前不只是“有 fast/balanced 名字”，而是多套并存的行为开关

在线 API 通过 `rag/api/runtime_context.py` 暴露 `auto / fast / balanced / full / degraded_fast`：`GoalRequest.mode` 和 `ChatStreamRequest.mode` 都接受该参数，`chat_stream` 会发出 `runtime_mode` SSE 事件，`/api/recommend` 也把它写入 trace。该策略会同时决定：是否用 LLM 解析、是否用 guidance/vision LLM、是否用 Milvus、是否做 query expansion。

同时存在以下漂移：

- `/api/stream-recommend` 不走同一策略，直接把事件和 trace 硬编码为 `balanced`；
- 评测脚本仍以 `fast_baseline`、`balanced_demo` 等模式分组，并给 `ShoppingSession` 临时挂载 `runtime_mode`；该字段不在 dataclass 中；
- `tests/test_runtime_mode.py` 和 `scripts/eval_full_chain_ablation.py` 导入的 `rag.recommendation.runtime_mode`、`runtime_mode_selector` 文件已不存在。实际执行 `python -m pytest tests/test_runtime_mode.py -q` 在收集阶段就报 `ModuleNotFoundError`；
- README、SSE、trace、附件 `vision_skipped_by_runtime_mode`、handler 参数和评测字段都耦合这个概念，导致“同一请求到底跑了什么”无法由一个配置面确定。

这不是一个可保留的性能策略，而是会改变功能正确性的业务开关。例如 `fast` 会跳过 LLM/Milvus，`full` 才做 query expansion；同一用户请求因 mode 不同得到不同能力，调用方也可绕过默认链路。对正式产品，这种差异应该由服务端明确的功能开关和依赖健康状态控制，而非开放给每条业务请求。

### 8.2 V3 处理原则

删除的是 **对外业务 mode 与自动 mode 选择器**，不是盲删“快速模型”配置。`client.config.fast_model` 是模型供应商的一个模型别名，若仍用于 Router/解析的部署配置，可在后续另行改名为 `default_model` 或按用途命名；它不应再表达 API 的 `fast` 功能路径。

V3 统一为一个不可由用户覆盖的 `ExecutionPolicy`：

```text
ExecutionPolicy
  llm_router_enabled: bool                 # 服务端配置 + 健康状态
  requirement_llm_enabled: bool            # 服务端配置 + 健康状态
  vision_enabled: bool                     # 有附件且服务可用
  milvus_enabled: bool                     # 服务端配置 + collection 健康状态
  query_expansion_enabled: bool            # 独立实验/灰度开关，默认 false
  fallback_reason_codes: list[str]         # 仅解释降级，不改变用户意图
```

原则如下：

1. Router intent 与 ExecutionPolicy 完全解耦。即使 LLM/Milvus 不可用，仍由规则 Router 产出同一 V3 `RoutedAction`，执行层做确定性降级；不能把“降级”伪装成新的业务 mode。
2. 从 `GoalRequest`、`ChatStreamRequest` 删除 `mode`；删除 `runtime_mode` SSE 事件和结果 trace 的业务字段。替换为可选、内部可观测的 `execution_policy`/`fallbacks`，不要让前端据此分支业务行为。
3. `/api/recommend`、`/api/chat/stream`、图式 debug endpoint 必须复用同一个 policy resolver，或将 debug endpoint 标记为开发工具并从生产路由移出；不能再硬编码 `balanced`。
4. 压测/消融不应通过生产 `mode` 参数完成。测试用依赖注入/fixture 显式固定 `llm_enabled`、`milvus_enabled`、`query_expansion_enabled`，并将实验矩阵留在 `scripts/`，不污染 session、SSE 与 public API。
5. 删除不存在模块的导入、失效测试、`session.runtime_mode` 动态属性写入、README mode 文案和 trace 字段；以新的降级测试替代。

### 8.3 Runtime Mode 删除验收

- `rg` 在生产 `rag/` 代码中不再命中 `fast/balanced/full/degraded_fast/runtime_mode/requested_mode/selected_mode` 作为业务控制字段（允许 FastAPI、`fast_model` 等无关名称，需逐项人工确认）。
- `GoalRequest`、`ChatStreamRequest` 的公开 schema 无 `mode`；前端不再发送或显示 runtime mode。
- 无 LLM、无 Milvus、附件视觉失败三种场景，都走相同工具/业务意图并产生明确 fallback reason，商品硬约束不改变。
- `tests/test_runtime_mode.py` 不再是坏导入；替换后的 `test_execution_policy.py` 覆盖健康检查、降级和公共契约。

## 9. 商品切片、入库与索引：当前实现的具体缺陷

### 9.1 当前切片实际上是什么

`rag/ingestion/product_chunks.py` 不是通用文档切片，而是对每个商品生成至多四条人工拼接的证据文本：

| chunk_type | 内容 | 风险 |
| --- | --- | --- |
| `profile` | ID、品牌、类目、价格、评分、标签、详情；PC 额外最多 18 个 specs | 信息密度很高，描述/标签可把同一商品打成冗长“百科块” |
| `sku` | 最多 12 个 SKU | 多个变体混在同一向量，不能精确定位 SKU |
| `faq` | 最多 8 条 FAQ | 多个问答混在同一向量，问题粒度丢失 |
| `review` | 最多 10 条评论 | 评价情绪和具体属性混在同一向量，噪声较大 |

四类 chunk 全部设为 `chunk_level=3`，`parent_chunk_id=root_chunk_id`，但没有生成 level 1/2/root 实体，也没有把父块写进 `ParentChunkStore`。商品索引脚本仅调用 `MilvusWriter.write_documents`，不调用 `ParentChunkStore.upsert_documents`。所以在线检索虽会尝试 `auto_merge`，但找不到父块，实际没有可用的父子合并语义。这是一套“声明了 hierarchy、没有构建 hierarchy”的死设计。

实际 dry-run 已构建出 884 个 chunk（400 个电商、484 个 PC），embedding 为 DashScope `text-embedding-v4`、1024 维。现有 `tests/test_product_chunks.py` 仅有 2 个断言，验证“能生成文本”和“四种 type 存在”，没有验证字段落库、检索效果、同商品去重或层级完整性。

### 9.2 chunk 字段与 Milvus schema/writer 不一致

`product_chunks.py` 生成的字段包括 `category_name`、`sub_category`、`structured_compatibility_fields`。但 `MilvusWriter` 实际 insert 的只有 `category`、`brand` 等，**没有写入前述三项**；同时它把 `metadata` 固定取 `doc.get("metadata") or {}`，而 chunk 并未把 PC specs 放在 `metadata`，导致 PC 兼容结构也没有进入 Milvus。

更严重的是 `retrieval_fusion._build_vector_filter` 已会在有子类条件时构造 `sub_category == "..."`，但集合 schema/writer 中均未写入这个字段。该路径在启用时不是可靠过滤：要么字段缺失导致查询失败并降级，要么无法匹配任何实际值。`retrieval.py` 的主检索目前仅使用 `chunk_level + category`，所以暂未暴露这个一致性问题，但 V3 若直接加 brand/subcategory filter 会踩中同样问题。

此外，collection 使用 `auto_id=True`，writer 一律 insert。重建脚本已用“集合存在则拒绝、要求 `--recreate`”避免全量重复，但没有内容 hash、catalog version、source revision、upsert/delete-by-product 的增量索引能力；商品价格、库存、SKU 更新无法安全同步到向量库。BM25 状态也依赖全量重建/rollback，失败后只能提示人工 full rebuild。

### 9.3 V3 的目标数据模型：先定义可检索商品事实，再定义文本证据

将“商品实体字段”和“文本证据 chunk”分开，不再把所有事实只塞进 prompt 文本：

```text
ProductIndexDocument（每个商品/变体一条；可过滤、可同步）
  product_id, product_version, catalog_version, active
  category, sub_category, brand_id, brand_family_id
  min_price, max_price, stock_state, rating
  tags[], searchable_attributes{}, compatibility{}
  updated_at, content_hash

EvidenceChunk（每条可语义匹配的证据）
  chunk_id, product_id, evidence_type, text, dense/sparse vectors
  source_version, chunk_hash, created_at

SkuIndexDocument（仅 SKU 详情/价格查询需要时）
  sku_id, product_id, properties{}, price, stock_state, active
```

Milvus 可以存 EvidenceChunk 和必要的 filter scalar；目录数据库/JSON catalog 仍是价格、库存和商品事实的最终来源。若继续只用一个 collection，也必须显式写入并查询这些 scalar 字段，而非依赖未写入的 dynamic field。

### 9.4 切片重做建议

1. **取消伪父子切片，或真正实现它。** V3 推荐先取消 `chunk_level`/`ParentChunkStore`/auto-merge，采用 `product_id` 聚合去重；只有当有真实长商品详情、说明书或多页文档时，才建立 `product -> section -> leaf` 三层并同时写入父块。
2. **按证据原子性切片。** profile 只保留稳定商品摘要；每个 SKU 一条；每条 FAQ 一条；每条评论或按主题聚类后的评论摘要一条。每一条都带 `evidence_type`、`product_id`、`sku_id`（如有）、事实来源与版本。
3. **避免把所有评论直接进主召回。** 评论应作为独立、低权重证据类型；无用户的“口碑/避坑”请求时不参与主召回，避免评论中偶发词压过商品规格。
4. **显式 metadata schema。** 至少增加并实际 insert：`sub_category`、`brand_id`、`brand_family_id`、`active`、`stock_state`、`product_version`、`updated_at`、`evidence_type`；PC 增加可筛字段（socket、memory_type、form_factor 等）或用结构化 compatibility service，不把关键兼容事实只放进 text。
5. **幂等增量索引。** 用稳定 `chunk_id`/主键与 `chunk_hash`，实现新增、更新、删除（下架标 `active=false` 或删除）和 catalog version manifest；索引任务在批量成功后原子切换 active version，失败不暴露半更新集合。
6. **索引质量门禁。** 每次构建输出 manifest：目录商品数、SKU 数、各 evidence_type 数、各类目/品牌数、空/超长文本、重复 chunk_id、写入数、可查询数、embedding/schema/version。没有 manifest 与抽样 query 不允许发布新索引。

### 9.5 切片/入库验收

- 单元测试覆盖“chunk 字段 == writer payload == collection schema == retrieval output fields”的完整契约，不再只测文本非空。
- 用 Milvus integration test 验证品牌、子类、product ID、`active` 过滤真实有效；字段缺失必须 fail-fast，不得静默降级为错结果。
- 同商品多个 evidence hit 只作为同一 `product_id` 的证据聚合，不可在候选排名中占满前 K。
- `auto_merge` 若未实现真实 hierarchy 必须从在线链路删除；若保留，必须在构建任务中写入各层父块且有命中断言。
- 更新价格/库存/下架商品后，目录最终校验立即生效；索引同步在约定 SLA 内完成且旧版本可回滚。

## 10. 检索并不等于 RAG：V3 应采用的多阶段候选架构

RAG 的角色是“取证并给 LLM/解释使用”，不是电商推荐的唯一召回方式。混合检索通常只是 dense vector + BM25 的一个候选召回器。对当前小而结构化的商品目录，先做结构化召回往往比先问向量库更准确。

可选方法及其适用位置：

| 方法 | 适合解决的问题 | 当前项目建议 |
| --- | --- | --- |
| Facet/布尔/范围检索 | 品类、品牌、排除品牌、价格、库存、SKU、规格 | 应作为第一阶段硬约束，目录是事实源 |
| 倒排/BM25 | 精确型号、SKU、关键词、罕见规格 | 保留；按 product/SKU 聚合，不只按 chunk 排名 |
| Dense semantic retrieval | 自然语言场景、模糊需求、同义表达 | 保留为补充召回，不可绕过硬约束 |
| Learned sparse（SPLADE/uniCOIL） | 比 BM25 更好的词项扩展且保留可解释词匹配 | 目录扩大后可替换/补充 BM25，不是 P0 |
| Late interaction（ColBERT） | 长商品文本、细粒度词项对齐 | 有真实长详情后评估；当前四块拼接文本收益有限 |
| Cross-encoder reranker | 对 top 20--100 候选进行 query--商品相关性重排 | 值得引入，但输入应为商品摘要/关键 evidence，不是整段 trace |
| Learning-to-Rank | 有点击、加购、成交等行为标签时学习排序 | 当前没有可靠行为日志前不做主排序；先保留特征/曝光日志 |
| 协同过滤 / 双塔召回 | 个性化、相似用户偏好 | 需要匿名行为和用户授权；当前无数据，不应伪造 |
| 图检索/知识图谱 | 配件兼容、品牌--系列--SKU、替代关系 | PC 兼容尤其适合；以结构化约束/图遍历为准，不由向量猜测 |
| 图像向量检索 | 以图找同款、风格相似 | 仅在图片意图时并行召回，再做商品级融合 |
| Query rewriting / multi-query / HyDE | 增强语义召回 | 可作为受控实验，不应作为每次请求的 mode 区别 |
| MMR/xQuAD 多样化 | 避免同品牌/同款占满结果 | 当前已有品牌连续限制，但需升级为商品/品牌/价格段多样化重排 |

建议的 V3 正式链路：

```text
用户请求
  -> 统一 RequirementSpec（含 hard / soft / negative constraints）
  -> Catalog candidate gate（类别、上下架、库存、品牌包含/排除、精确子类、显式 ID）
  -> 多通道召回（精确 SKU/BM25、dense、可选图片、PC compatibility graph）
       每个通道只能从 gate 允许的 product_id 集合中取回
  -> product_id 聚合与去重（evidence 只加分，不替代事实）
  -> rerank（规则特征 + 可选 cross-encoder）
  -> 硬约束最终复核（库存/价格/排除项，绝不 rescue/放宽）
  -> 多样化 -> 商品卡片 + 可追溯证据
```

其中“RRF”只能融合同一约束域的排序列表；不能让一个未通过硬约束的向量命中，因为分数高而经 `vector rescue` 回到候选集。

## 11. 负约束的设计：如何正确实现“不要小米”的召回前过滤

### 11.1 当前行为与缺陷

当前规则解析会把“不要小米”识别为 `RequirementSpec.excluded_brands=["小米"]`。但 `structured_filter.violates_brand_or_text_exclusion` 只硬过滤 `excluded_terms`，不硬过滤 `excluded_brands`；品牌排除交给 `_llm_filter_products`。该 LLM 筛选最多看 30 个候选，服务失败、超时或返回空时会保留原候选。也就是说，当前实现不能保证小米商品被排除，且 `hard_constraint_passed_ids` 仍可能包含它，使向量 rescue 有机会重新纳入。

这是正确性问题，不是“要不要前置优化”的问题：用户显式的品牌排除必须是不可放宽的硬约束。

### 11.2 先做品牌实体规范化，而非直接拼用户字符串

不能直接用用户输入构造 Milvus expr，例如 `brand != "小米"`：别名/子品牌会漏掉，且存在表达式注入与数据不一致风险。应建立 catalog-owned 的品牌字典：

```text
BrandEntity
  brand_id: "xiaomi"
  brand_family_id: "xiaomi"
  display_names: ["小米", "Xiaomi", "MI"]
  aliases: [...]
  child_brand_ids: ["redmi", ...]
```

每个商品入库时填 `brand_id` 与 `brand_family_id`；“不要小米”经 parser + dictionary 解析为 `excluded_brand_family_ids=["xiaomi"]`。若产品目录把 Redmi 归入小米品牌族，则会一并排除；若产品策略希望 Redmi 可保留，必须把它建为独立 family 并让产品/产品说明明确表达，不能由 LLM 临场猜测。

对“不要小米但可以 Redmi”“小米和 Redmi 都不要”“不要小米生态链”这类句子，Parser 应输出明确的 include/exclude entity IDs；存在包含与排除冲突时返回澄清问题，不可静默选择一边。

### 11.3 三层双保险的执行规则

1. **目录 gate（先于向量）是事实源。** 在任何 Milvus 调用前，用本地 catalog 的规范化品牌族字段计算 `allowed_product_ids`：

   ```text
   allowed = category ∩ active ∩ in_stock ∩ not excluded_brand_family
   ```

   对“不要小米”，小米品牌族商品在这里已被移除；它们不能进入 BM25、dense、图片或 PC 图谱召回的候选域。

2. **Milvus pre-filter 是加速层。** 新 collection 具备 `brand_family_id` 后，安全 builder 根据已验证 ID 生成：

   ```text
   chunk_level == 3
   && category == "phone"
   && active == true
   && brand_family_id not in ["xiaomi"]
   ```

   对显式 SKU/product ID 或候选集合足够小时，还可附加 `product_id in [...]`，保证向量库与目录一致；集合过大时使用 category/brand family 等低基数字段，避免超长 expr。所有字面量必须来自 catalog 字典并统一转义，不能接受原始用户文本。

3. **最终强校验不可取消。** `filter_products_for_requirement` 新增确定性的 `matches_excluded_brand_family`，在融合、rescue、rerank、生成卡片前都拒绝排除品牌。`hard_constraint_passed_ids` 只能来自通过该校验后的集合；`vector rescue`、预算放宽、LLM fallback 都不得带回被排除商品。

`excluded_terms` 与“不要有酒精”等属性负约束不能全部安全地转为 Milvus text filter。可将已结构化的成分/属性 tag 前置；自由文本否定仍保留目录属性/规则最终校验，必要时向用户澄清，不可用 LLM 失败后放行。

### 11.4 负约束测试矩阵

| 用例 | 必须结果 |
| --- | --- |
| “推荐手机，不要小米” | Router/Parser 给出 `excluded_brand_family_ids=[xiaomi]`；Milvus expr 带 `not in`；结果和 evidence 均无小米族商品 |
| “小米或华为，但不要小米” | 要么规范化为仅华为，要么要求澄清；不得返回小米 |
| “不要小米但 Redmi 可以” | 依据品牌族策略可验证地保留 Redmi，trace 给出规则版本 |
| Milvus 超时/不可用 | 本地 catalog gate 仍不返回小米 |
| LLM filter 不可用/返回错误 | 不影响品牌排除结果 |
| vector rescue 开启 | `hard_constraint_passed_ids` 不含排除品牌，rescue 无法带回 |
| 索引 metadata 过期 | 目录最终校验挡住排除品牌并记录 `index_catalog_mismatch` |

## 12. 补充 P0/P1 施工任务（先修正确性与可验证性）

### P0-A：删除 Runtime Mode 与修复测试漂移

- 盘点并删除 public schema、API 路由、SSE、trace、README、前端和测试中的 mode 契约；以 `ExecutionPolicy` 内部对象替代。
- 移除/改造导入不存在模块的测试和脚本；禁止在 `ShoppingSession` 上动态挂载 runtime 属性。
- `/api/stream-recommend` 与主 chat/recommend 统一或下线，避免第二套固定 `balanced` 链路。

**阻断验收：** `pytest` 不再因 runtime mode 模块缺失而在收集阶段失败；无 LLM/Milvus 的降级不改变用户的 Router action 与硬约束。

### P0-B：负品牌排除硬化

- 扩展 requirement 解析为稳定的品牌 entity/family ID；修正 `structured_filter`，使 excluded brand 在目录 gate 中确定性拒绝。
- 将 `hard_constraint_passed_ids` 计算移动到全部硬负约束之后；禁止 rescue 绕过。
- 先完成本地 gate 和最终校验测试，再做 Milvus `not in` 优化；索引不可用时也必须正确。

**阻断验收：** “不要小米”的所有上表场景通过，且 LLM/Milvus 关闭、超时、失败时结果一致地不含排除品牌。

### P0-C：停止使用未落库字段与伪父子层级

- 在 V3 index 未重建前，禁用 `retrieval_fusion` 中对未写入 `sub_category` 的 Milvus expr；改为目录 gate + `product_id` 限域或仅 category filter。
- 关闭无数据支撑的 auto-merge，或在索引任务中真正生成并写入 parent document；二选一，不保留“可能自动合并”的假能力。
- 增加 schema/writer/output field contract tests。

**阻断验收：** 任一可配置 pre-filter 字段都可从 chunk 构建追踪到 writer、collection、查询及命中输出；不存在引用未落库字段的检索表达式。

### P1：V3 商品索引与多阶段检索落地

- 建立 versioned catalog manifest、brand dictionary、ProductIndexDocument/EvidenceChunk/SkuIndexDocument 的明确契约。
- 重建 collection（schema 变更不能向旧 auto-id collection 直接追加）；实现可回滚的 versioned rebuild，之后再实现 hash-based incremental update。
- 以 catalog gate 为单一硬约束入口，接入 lexical/dense/image/PC graph 多通道，按 product_id 聚合、rerank、最终复核和多样化。

**验收：** 召回、融合、rerank、最终卡片四个阶段都有 product ID 集合与淘汰原因 trace；任何硬排除项在四阶段均为零泄漏。
