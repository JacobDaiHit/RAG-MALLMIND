# 当前后端链路与死代码审计

更新时间：2026-07-09

本文只按当前后端源码的真实调用关系梳理，不参考历史报告里的链路描述。重点校对文件包括 `rag/api/recommendation_app.py`、`rag/api/routes/chat.py`、`rag/api/runtime_context.py`、`rag/recommendation/tool_router.py`、`rag/recommendation/tool_handlers.py`、`rag/recommendation/recommendation_pipeline.py`、`rag/recommendation/package_builder.py`、`rag/recommendation/retrieval.py`、`rag/recommendation/session_state.py`、`rag/api/app_context.py`、`rag/api/attachments.py`、`rag/recommendation/llm_client.py`、`rag/recommendation/explanation_builder.py`、`rag/api/routes/recommend.py`、`rag/api/routes/legacy_chat_compat.py`、`rag/recommendation/recommendation_graph.py`。

## 第一部分：实际链路

### 1. 应用入口与主旁路定位

应用入口在 `rag/api/recommendation_app.py`。`create_app()` 创建 FastAPI app，挂载 `/static`、`/product-images`、`/pc-images`，注册 `products`、`pc_build`、`attachments`、`feedback`、`recommend`、`chat` router。startup 阶段会预热商品目录、embedding service、Milvus、session store；健康与诊断接口包括 `/health`、`/api/health`、`/api/runtime/diagnostics`、`/api/llm/diagnose`。

当前真实主入口是 `POST /api/chat/stream`。其他入口仍存在，但定位不同：

| 端点 | 文件 | 当前定位 |
| --- | --- | --- |
| `POST /api/chat/stream` | `rag/api/routes/chat.py` | 主聊天 SSE 链路，路由、推荐、购物车、多轮 session 都从这里串起来 |
| `POST /api/chat` | `rag/api/routes/chat.py` + `legacy_chat_compat.py` | 旧非流式兼容链路，不是主链路 |
| `POST /api/recommend` | `rag/api/routes/recommend.py` | 非流式推荐测试/辅助链路 |
| `GET /api/stream-recommend` | `rag/api/routes/recommend.py` + `recommendation_graph.py` | graph-style 调试流，不是主聊天链路 |
| `POST /api/cart/actions` | `rag/api/routes/chat.py` | 购物车直接写操作 API，会绕过 chat 确认计划 |
| `POST /api/cart/confirm` | `rag/api/routes/chat.py` | 执行 chat 链路生成的 `pending_cart_action` |
| `POST /api/products/compare` | `rag/api/routes/chat.py` | 商品卡直接对比接口 |
| `POST /api/analyze-attachments` | `rag/api/routes/attachments.py` | 附件解析辅助接口 |
| `POST /api/pc-build/generate` | `rag/api/pc_build.py` | PC 整机方案直接生成接口 |

### 2. `/api/chat/stream` 主链路

`chat_stream()` 的实际执行顺序如下：

```text
ChatStreamRequest
  -> sanitize_input()
  -> get_session()
  -> resolve_runtime_policy()
  -> emit runtime_mode
  -> route_shopping_tool_call()
  -> validate_tool_call()
  -> update_session_from_router() 仅非购物车/非闲聊/非降级工具
  -> emit tool_call
  -> lightweight handler 或 heavy handler
  -> emit result / cart_confirmation / pc_build_plan / delta
  -> emit done
```

关键点：

- `ChatStreamRequest` 只有 `session_id`、`message`、`images`、`attachments`、`stream`、`mode`。它没有顶层 `product_ids` 字段；chat 链路里的商品上下文来自附件里的 `product_id` 或消息文本中的商品 ID。
- `sanitize_input()` 校验 session、空消息、长度，并用 `prompt_guard.detect_injection()` 拦截明显 prompt injection。
- `resolve_runtime_policy()` 只生成同一条主链路上的功能开关，不会切换到几套完全不同的业务链路。
- `route_shopping_tool_call()` 先跑本地规则路由，再按 runtime policy 尝试 LLM 路由。LLM 失败时回退本地规则。
- `validate_tool_call()` 在 LLM 路由之后继续纠偏，因此“LLM 决策”不是最终无条件生效。明确购物车意图会被纠正到 `apply_cart_instruction`，预算、品类、未知工具名也会被校验。
- `safe_stream()` 包住 SSE generator；异常会输出 `error` + `done`，避免把服务端堆栈直接暴露给客户端。

### 3. Runtime policy 是功能门控，不是分模式业务链路

`rag/api/runtime_context.py` 会根据请求 `mode`、附件、消息复杂度、历史依赖和 LLM 是否配置，输出一个 runtime policy：

| 字段 | 实际作用 |
| --- | --- |
| `use_llm` / `use_requirement_llm` | 是否允许路由/需求理解使用外部 LLM |
| `use_llm_guidance` | 是否允许推荐后 guidance LLM 生成追问/导购提示 |
| `use_vision_llm` | 是否允许图片附件调用视觉模型 |
| `use_milvus_retrieval` | 是否允许推荐链路调用 Milvus 证据检索 |
| `use_rag_query_expansion` | 仅 `full` 模式且环境变量允许时，打开 query expansion |

`fast`、`balanced`、`full`、`auto` 在代码里不是几套独立流程，而是同一条 `/api/chat/stream` 主链路上的开关组合。`auto` 对普通请求通常落到 `balanced`，附件或“详细分析”落到 `full`；LLM 未配置时会变成 `degraded_fast`。`route_confidence`、`route_margin`、`requirement_completeness` 等字段目前主要是 trace 可观测信号，不是复杂策略学习结果。

### 4. 工具路由层

工具定义在 `rag/recommendation/tool_router.py::TOOL_SCHEMAS_FOR_PROMPT`，当前可路由工具是：

- `recommend_shopping_products`
- `generate_pc_build_plan`
- `compare_products`
- `apply_cart_instruction`
- `general_chat`
- `parameter_query`
- `sku_detail`
- `price_comparison`

路由实际分三步：

```text
local_route_tool_call()
  -> 规则识别购物车、对比、价格、参数、SKU、PC 整机、PC 单配件、普通商品推荐、闲聊
  -> 生成 category / catalog_scope / budget / product_ids / quantity / action 等 slots

try_llm_route_tool_call()
  -> OpenAICompatibleChatClient
  -> MALLMIND_ROUTER_MODEL 或 fast_model
  -> 要求模型返回 JSON tool call
  -> 超时、异常、熔断时返回 None

validate_tool_call()
  -> 校验工具名、预算、品类、catalog_scope
  -> 本地强信号覆盖 LLM 弱/错路由
  -> 明确购物车语义强制走 apply_cart_instruction
  -> 输出最终 tool_call
```

Router LLM 有独立并发信号量和熔断器，配置来自 `RECOMMENDATION_ROUTER_LLM_*`、`RECOMMENDATION_LLM_ROUTER_TIMEOUT_SECONDS`。它直接使用 `OpenAICompatibleChatClient`，没有走 `llm_gateway.py`。

### 5. 工具分发层

`rag/api/routes/chat.py` 把工具分为轻量工具和重量工具。

轻量工具不执行附件分析和 `prepare_recommendation_context()`：

| tool | handler | 实际行为 |
| --- | --- | --- |
| `apply_cart_instruction` | `tool_handlers.handle_cart_v2()` | 生成购物车确认计划；`clear` 直接执行 |
| `general_chat` | `tool_handlers.handle_general_chat()` | LLM 简答，失败用模板 |
| `compare_products` | `tool_handlers.handle_compare_v2()` | 商品对比；缺 ids 时尝试上一轮推荐 |
| `parameter_query` | `tool_handlers.handle_parameter_query()` | 商品参数问答 |
| `sku_detail` | `tool_handlers.handle_sku_query()` | SKU/配置差异问答 |
| `price_comparison` | `tool_handlers.handle_price_comparison()` | 价格/SKU 价格问答 |

重量工具会先执行 `rag/api/app_context.py::prepare_recommendation_context()`，把多轮上下文和附件结果拼入推荐 query：

| tool | handler | 实际行为 |
| --- | --- | --- |
| `recommend_shopping_products` | `tool_handlers.handle_recommend()` | 推荐商品卡、候选范围、对比表、自然语言回答 |
| `generate_pc_build_plan` | `tool_handlers.handle_pc_build()` | 生成 PC 整机方案和兼容性说明 |

### 6. LLM 调用点

当前项目没有统一通过 `llm_gateway.py` 调用模型，主链路里的 LLM 调用分散在多个模块，统一底层客户端是 `rag/recommendation/llm_client.py::OpenAICompatibleChatClient`。

| 调用点 | 文件/函数 | 模型选择 | 输出 | 失败策略 |
| --- | --- | --- | --- | --- |
| 工具路由 | `tool_router.try_llm_route_tool_call()` | `MALLMIND_ROUTER_MODEL` 或 `fast_model` | JSON tool call | 返回 None，使用本地规则 |
| 推荐需求解析 | `recommendation_pipeline.parse_requirement()` | `MALLMIND_PARSE_MODEL` 或 `fast_model` | `RequirementSpec` JSON 增强 | 回退规则解析 |
| 推荐 guidance | `recommendation_pipeline.enrich_recommendation_result()` | `MALLMIND_GUIDANCE_MODEL` 或主模型 | 追问、导购提示、优化建议 | 回退规则 guidance |
| 证据解释 | `explanation_builder.build_evidence_grounded_explanation()` | `MALLMIND_GUIDANCE_MODEL` 或 `fast_model` | grounded explanation JSON | 回退模板解释 |
| 闲聊 | `tool_handlers._generate_general_chat_llm_response()` | 默认模型 | 简短自然语言 | 回退模板 |
| 购物车消歧 | `tool_handlers._llm_resolve_cart_product()` | 默认模型 | 推荐列表中的 1-based 序号 | 规则兜底或取推荐首项 |
| 图片理解 | `attachments.analyze_image_attachment()` | `VISION_MODEL` / `MULTIMODAL_MODEL` / 默认模型 | OCR、视觉线索、商品属性 JSON | 附件降级为元数据线索 |
| Query rewrite | `query_rewriter.rewrite_query()` | 有 LLM 分支 | 改写检索 query | 主推荐链路当前以 `use_llm=False` 调用 |

### 7. 推荐链路

主推荐入口是 `rag/recommendation/recommendation_pipeline.py::recommend_shopping_products()`。`handle_recommend()` 会把 runtime policy、附件证据、router arguments 和 session 一起传入。

实际调用链：

```text
handle_recommend()
  -> validate_goal()
  -> retrieve_image_evidence()
  -> call_recommendation_fn()
     -> recommend_shopping_products()
        -> router_arguments 存在时 _requirement_from_args_v2()
           否则 parse_requirement()
        -> build_recommendation_result()
        -> enrich_recommendation_result()
        -> attach_grounded_explanation()
  -> fact_check_result()
  -> remember_recommendation()
  -> emit result / delta / product_cards / done
```

`router_arguments` 存在时会跳过 LLM parse，直接把路由 slots 转成 `RequirementSpec`；这也是 `/api/chat/stream` 里很多简单请求 trace 显示 `router_arguments_applied` 的原因。没有 router arguments 的旁路接口才会更多依赖 `parse_requirement()`。

`package_builder.build_recommendation_result()` 是真正组包：

```text
RequirementSpec
  -> load_catalog_for_scope()
  -> query_rewriter.rewrite_query(..., use_llm=False)
  -> no_match / missing_subcategory / budget guard
  -> retrieve_evidence_with_timeout()
  -> fuse_text_and_image_evidence()
  -> score_required_components()
     -> filter_products_for_requirement()
     -> fuse_candidates()
     -> score_products()
  -> build_recommendation_plan()
  -> build_product_cards()
  -> budget post enforcement
  -> build_candidate_scope()
  -> build_comparison_table()
```

商品卡来自本地 catalog 和评分结果。LLM 不直接生成商品、价格、库存；它只参与需求理解、导购话术、追问和证据解释。推荐结果返回前会经过 `fact_check_result()`，用 catalog 再校验商品 ID、价格等事实。

### 8. Milvus / RAG 证据链路

Milvus 是推荐的可选证据增强层，不是硬依赖。开关同时受 runtime policy 和环境变量影响：`runtime_policy.use_milvus_retrieval` 为 true，且 `package_builder.MILVUS_RETRIEVAL_ENABLED` 为 true，才会调用在线检索。本次已让 `package_builder.py` 在读取环境变量前加载 `.env`，避免 `.env` 配置不生效。

在线检索路径：

```text
retrieve_evidence_with_timeout()
  -> retrieve_requirement_evidence()
  -> EvidenceRetriever.retrieve()
  -> MilvusManager.hybrid_retrieve()
  -> dense + sparse hybrid search
  -> optional query expansion / rerank / auto-merge postprocess
  -> RetrievalEvidence
```

融合路径：

```text
structured_filter 规则候选
  + RetrievalEvidence.by_product_id
  + ImageRetrievalEvidence.by_product_id
  -> retrieval_fusion.fuse_candidates()
  -> scorer.score_products()
  -> product_cards
```

实际 API smoke 结果：

- `推荐一款750W金牌电源`：`runtime_mode=balanced`、`use_milvus_retrieval=true`、RAG 命中 12 条证据/8 个商品，返回 `pc_psu_*` 电源卡。
- `油皮夏天用的防晒推荐`：`runtime_mode=balanced`、`use_milvus_retrieval=true`、RAG 命中 12 条证据/8 个商品，返回 `p_beauty_010` 等防晒/护肤卡。

### 9. 购物车增删链路

聊天入口的购物车工具是 `apply_cart_instruction`，但 chat 链路不会立刻写购物车，而是先生成确认计划。核心代码在 `rag/recommendation/tool_handlers.py::handle_cart_v2()` 和 `rag/api/routes/chat.py::cart_confirm()`。

#### 加购物车

```text
用户："把第一款加入购物车"
  -> route_shopping_tool_call()
  -> validate_tool_call()
  -> tool = apply_cart_instruction
  -> handle_cart_v2()
  -> _resolve_cart_action() 得到 add
  -> _handle_cart_add()
     -> _resolve_product_for_cart()
        -> 显式 product_ids
        -> tool_call.arguments.product_ids
        -> 上一轮推荐 last_result 的序号/上一款引用
        -> 品牌或标题模糊匹配
        -> LLM 推荐列表消歧
        -> 推荐首项兜底
     -> session.pending_cart_action = plan
     -> save_session(session)
     -> emit cart_confirmation
  -> /api/cart/confirm
     -> 读取 pending_cart_action
     -> 根据 plan 拼真实 instruction
     -> session_state.apply_cart_instruction()
     -> infer_cart_action()
     -> resolve_cart_product_ids()
     -> 修改 session.cart
     -> save_session(session)
```

本次已修复两点：

- `_handle_cart_add()` / `_handle_cart_modify()` 写入 `pending_cart_action` 后会立即 `save_session(session)`，避免 Redis/新请求确认时丢 plan。
- 推荐结果里的 `action=add_to_cart` 不再直接调用 `apply_cart_instruction()` 写购物车，而是生成 `cart_confirmation`，和普通聊天加购保持一致。

#### 删除/改数量

```text
用户："删除第一款" / "把第一个数量改为 2"
  -> route_shopping_tool_call()
  -> validate_tool_call()
  -> tool = apply_cart_instruction
  -> handle_cart_v2()
  -> _handle_cart_modify()
     -> 空购物车直接 delta 提示
     -> _check_cart_ambiguity() 检查同品类歧义、序号越界
     -> _resolve_product_for_cart()
        -> 显式 id
        -> 当前 cart 模糊匹配
        -> 序号
        -> previous item 引用
        -> cart 第一项兜底
     -> session.pending_cart_action = plan
     -> save_session(session)
     -> emit cart_confirmation / cart_clarification
  -> /api/cart/confirm
     -> operation=remove 或 set_quantity
     -> apply_cart_instruction()
     -> save_session(session)
```

`clear` 是例外：`handle_cart_v2()` 遇到 `clear` 会直接调用 `_handle_cart_clear()`，不走确认。

`POST /api/cart/actions` 是直接写操作 API，它调用 `session_state.apply_cart_instruction()`，不会生成 `pending_cart_action`。这条旁路仍然存在。

### 10. 多轮对话与历史 session

多轮上下文核心在 `rag/recommendation/session_state.py`。`ShoppingSession` 主要字段：

- `messages`：推荐回答等历史消息。
- `last_goal`、`last_requirement`、`last_result`：上一轮推荐目标、结构化需求、结果 payload。
- `cart`、`pending_cart_action`：购物车状态和待确认操作。
- `topic_memory`、`chat_topic`、`topic_history`、`recent_queries`、`current`：短期话题和路由 slots 累积。
- `recent_turns`、`recent_turns_summary`：多轮过程记录和摘要。
- `pc_build_history`、`current_pc_build`：PC 整机方案历史和当前配置。
- `tool_history`、`llm_call_log`：工具调用和 span 日志。

session store 由 `SESSION_BACKEND` 控制：

- `memory`：默认开发路径，本进程内保存。
- `redis`：配置 `REDIS_URL` 后可跨请求/进程保存，并带 TTL。

主链路里的上下文更新点：

- `/api/chat/stream` 路由后，对非购物车、非闲聊、非降级工具调用 `update_session_from_router()`，更新 `current`、`recent_queries`、`topic_history`、`chat_topic`。
- 重量工具执行前调用 `prepare_recommendation_context()`，内部会用 `build_contextual_goal(session, message)` 判断是否是追问。追问会拼成 `last_goal + User added constraints`，显式换话题则重新开始。
- 推荐成功后 `remember_recommendation()` 保存 `last_goal`、`last_requirement`、`last_result`、消息历史、turn 记录。
- `update_topic_memory()` 在闲聊、对比、参数、SKU、价格等 handler 中维护短期 topic JSON。
- PC 整机链路通过 `remember_pc_build_plan()` 和 `save_pc_build_to_session()` 维护 `pc_build_history` 与 `current_pc_build`。

### 11. PC 整机与单配件链路

PC 整机方案和单个 PC 配件推荐是两条不同业务处理：

- 整机方案：`generate_pc_build_plan` -> `handle_pc_build()` -> `pc_session_flow.build_pc_plan_for_message()` -> `pc_build.generate_pc_build_plan()` -> 兼容性检查 -> 保存 `pc_build_history`。
- 单配件推荐：仍走 `recommend_shopping_products`，但 `catalog_scope=pc_parts`，例如显卡、CPU、SSD、主板、电源。

因此“推荐一款 750W 金牌电源”不会进入整机规划，而是普通推荐链路 + PC 配件 catalog + Milvus 证据增强。

### 12. SSE 输出与可观测性

`/api/chat/stream` 常见事件：

- `runtime_mode`：本轮 runtime policy。
- `tool_call`：最终工具名、参数、路由来源和 routing trace。
- `progress`：推荐阶段进度，如读取 catalog、RAG 检索、筛选完成、命中候选。
- `attachment_analysis`：图片/附件解析摘要。
- `cart_confirmation`：购物车待确认 plan。
- `cart_clarification`：购物车歧义追问。
- `result`：推荐结果 payload。
- `delta`：自然语言片段。
- `pc_build_plan`：PC 整机方案。
- `error` / `validation_error` / `done`：错误和结束。

`_end_span()` 会把工具名、耗时、成功状态、事实校验状态写入 `session.llm_call_log`，最多保留 20 条。

## 第二部分：可能存在的问题、死代码情况与收敛项

### 13. 购物车相关问题

已修复：

- chat 加购/删除生成 `pending_cart_action` 后已经持久化，避免确认请求读不到 plan。
- 推荐后自动加购已改为确认计划，不再直接写购物车。
- `infer_cart_action()` 已修正“加入购物车，数量 1”被误判成 `set_quantity` 的问题；当前确认 add 返回 `action=add`。

仍需关注：

- `/api/cart/actions` 仍是直接写 API，会绕过确认计划。如果产品希望所有购物车写操作都必须确认，需要收敛这条旁路。
- `legacy_chat_compat.py` 仍有旧兼容购物车逻辑，虽然不是主链路，但容易和主链路语义不一致。
- `_resolve_product_for_cart()` 在 add 或 remove 消歧失败时有“推荐首项/购物车首项兜底”，体验上方便，但误操作风险较高。
- `clear` 当前直接执行，不走确认；如果清空购物车属于高风险动作，应改成确认计划。

### 14. 场景化推荐与排序问题

场景化推荐仍有排序质量风险。比如“篮球实战鞋 缓震”现在可以路由到 `recommend_shopping_products`，但实际命中里可能出现相邻泛品类商品排在运动鞋前面。这说明当前 `catalog_scope`、别名召回、Milvus 证据和结构化 scorer 的融合还不能完全保证“场景强相关优先”。

建议补一组场景化评测集：

- 运动：篮球实战鞋、跑步鞋、健身服、骑行装备。
- 护肤：油皮夏天防晒、敏感肌修护、男士控油。
- 数码：学生党手机、通勤降噪耳机、拍照手机。
- PC：单配件检索、整机装机、替换某个组件。

每条 case 至少看 top1/top3 是否同品类、是否满足核心场景词、是否违反预算/品牌排除。

### 15. RAG / Milvus 问题

- `scripts/check_vector_index_health.py` 的 dense-only smoke 曾失败，但真实业务 API 的 hybrid retrieval 能返回 `status=ok` 和合理商品。健康脚本和业务检索链路不完全一致，容易误报。
- `package_builder.MILVUS_RETRIEVAL_ENABLED` 原来在 `.env` 加载前读取环境变量，可能导致 `.env` 里开了 Milvus 但代码仍认为 disabled；本次已在 `package_builder.py` 读取前加载 `.env`。
- Milvus 失败时主推荐会降级到结构化 catalog scoring，这是可用性友好设计，但如果 trace 展示不清楚，排查“为什么没有走向量库”会困难。
- RAG 后处理中的 rerank/auto-merge 是否真正开启，需要按环境变量和 trace 再核对；它不是主推荐必经步骤。

### 16. LLM 抽象与半接入模块

- `llm_gateway.py` 不是 `/api/chat/stream` 主链路的统一网关。主链路直接散落调用 `OpenAICompatibleChatClient`。本次已在源码里标注 `EXPERIMENTAL_NOT_MAINLINE = True`，避免误读。
- `recommendation_graph.py` 只服务 `/api/stream-recommend` 调试流，不是主聊天/购物车链路。本次已标注 `DEBUG_GRAPH_NOT_MAINLINE = True`。
- `legacy_chat_compat.py` 是 `/api/chat` 旧兼容响应，不是主入口。本次已标注 `LEGACY_COMPAT_NOT_MAINLINE = True`。
- `query_rewriter.py` 有 LLM 分支，但主推荐链路当前调用 `rewrite_query(..., use_llm=False)`，所以不要在介绍主链路时说 query rewrite 依赖 LLM。

### 17. 测试与文档漂移问题

- `tests/test_cart_improvements.py` 当前通过：51 passed。
- `scripts.check_llm_provider` 当前通过：chat、json output、router schema 均 success。
- 真实 API smoke 当前通过：PC 电源推荐、防晒推荐、加购确认、删除确认。
- `tests/test_tool_router.py` 当前结果为 39 passed / 9 failed，失败主要来自旧测试期望和当前源码不一致，例如期望旧 trace 字段、旧路由名称或旧 category 语义。建议后续单独按“当前真实路由合同”重写这组测试，而不是让代码迎合过期断言。

### 18. 建议优先收敛的事项与施工状态

1. 购物车确认一致性：已施工。`pending_cart_action` 已持久化；推荐后 add-to-cart 已改成确认计划；add/action 语义已修正。剩余工作是决定是否让 `/api/cart/actions` 和 `clear` 也强制确认。
2. 主链路文档收敛：已施工。本报告按 `/api/chat/stream`、`recommend_shopping_products()`、`handle_cart_v2()`、`session_state` 的真实代码重写，明确 runtime mode 只是功能门控。
3. LLM 网关定位收敛：已施工。`llm_gateway.py` 已标注实验/非主链路；当前主链路仍直接使用 `OpenAICompatibleChatClient`，后续如要统一抽象，应另开重构。
4. Debug/legacy 模块定位收敛：已施工。`recommendation_graph.py`、`legacy_chat_compat.py` 已在源码和本文中标注非主链路，降低误读风险。
5. 场景化推荐评测：待施工。需要建立 top1/top3 相关性、场景词满足、预算/排除约束的 case 集。
6. Router 测试重写：待施工。应以 `route_shopping_tool_call()` + `validate_tool_call()` 当前输出为准更新断言。
7. RAG 健康检查对齐：待施工。建议把健康脚本改成业务同款 hybrid retrieval，或者在报告里区分 infra health 与 business retrieval health。
8. 购物车高风险兜底策略：待施工。建议将“兜底第一项”改为追问，特别是 remove/set_quantity。
