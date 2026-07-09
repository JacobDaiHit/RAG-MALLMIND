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
- Router LLM 的 user prompt 会注入上一轮可见 `product_cards` 和当前购物车商品列表，带 `product_id`、标题、品牌、价格/数量，用于让购物车工具下传精确目标。
- `sanitize_input()` 校验 session、空消息、长度，并用 `prompt_guard.detect_injection()` 拦截明显 prompt injection。
- `resolve_runtime_policy()` 只生成同一条主链路上的功能开关，不会切换到几套完全不同的业务链路。
- `route_shopping_tool_call()` 先跑本地规则路由，再按 runtime policy 尝试 LLM 路由。LLM 失败时回退本地规则。
- `validate_tool_call()` 在 LLM 路由之后继续纠偏，因此“LLM 决策”不是最终无条件生效。明确购物车意图会被纠正到 `apply_cart_instruction`，预算、品类、未知工具名也会被校验。
- `safe_stream()` 包住 SSE generator；异常会输出 `error` + `done`，避免把服务端堆栈直接暴露给客户端。

### 2.1 单次用户请求生命周期（函数级展开）

这一节按一次真实 `POST /api/chat/stream` 请求展开，重点说明“用户一句话进来，到前端收到实际回复”为止系统做了什么。

#### A. HTTP 请求进入后端

入口函数是 `rag/api/routes/chat.py::chat_stream()`。它不是直接返回一个普通 JSON，而是返回 `StreamingResponse`，媒体类型是 `text/event-stream`。也就是说，后端会一边处理一边向前端推 SSE 事件，例如 `runtime_mode`、`tool_call`、`progress`、`delta`、`product_cards`、`cart_confirmation`、`done`。

`chat_stream()` 外层先做三件事：

```text
request.attachments + request.images -> raw_attachments
request.message + request.session_id -> sanitize_input()
request.session_id -> get_session()
```

- `sanitize_input()`：做输入清洗。它会检查 `session_id` 是否存在、`message` 是否为空、消息是否超过 `MAX_MESSAGE_LENGTH=2000`，还会调用 `detect_injection()` 拦截明显 prompt injection。这个函数不是理解用户需求的，只负责“请求能不能进主链路”。
- `get_session()`：从 session store 取 `ShoppingSession`。当前 session 里保存上一轮推荐、购物车、话题记忆、PC 配置历史等。后续“把第二款加入购物车”能生效，就是靠这个 session 记住上一轮展示过哪些商品卡。
- `safe_stream()`：真正执行 generator 的外层保护。`unsafe_generate()` 里如果有没捕获的异常，它会转成 `error` 事件，再补一个 `done`，避免 SSE 半路断掉。

#### B. 运行策略：决定本轮开哪些能力

进入 `unsafe_generate()` 后，第一步是 `resolve_runtime_policy()`。它只是 `chat.py` 里的薄封装，真实逻辑在 `rag/api/runtime_context.py::build_runtime_policy()`。

`build_runtime_policy()` 会输出本轮能力开关：

- `use_llm`：路由和需求理解是否能用外部 LLM。
- `use_llm_guidance`：推荐后的导购话术/追问是否能用 LLM。
- `use_vision_llm`：图片附件是否能走视觉模型。
- `use_milvus_retrieval`：推荐链路是否启用 Milvus 证据检索。
- `use_rag_query_expansion`：是否启用 query expansion，目前只在 `full` 且环境变量允许时打开。

这里要注意：`fast/balanced/full/auto` 不是几套业务链路。它们只是同一条主链路上的功能门控。后端会先把这个策略通过 `runtime_mode` SSE 发给前端。

#### C. 工具路由：决定本轮该做推荐、购物车、对比还是闲聊

路由入口是 `rag/recommendation/tool_router.py::route_shopping_tool_call()`。它会先执行本地规则，再尝试 LLM 路由：

```text
route_shopping_tool_call()
  -> local_route_tool_call()
  -> try_llm_route_tool_call()  当 use_llm=true 且 LLM 可用
  -> 返回一个 tool_call
```

`local_route_tool_call()` 是规则路由器。它会调用：

- `extract_slots_rule_based()`：用规则抽取预算、品类、商品 ID、使用场景、偏好、`catalog_scope` 等基础 slots。
- `_has_cart_intent()`：判断是不是购物车操作，比如加入、删除、清空。
- `_looks_like_compare_request()`：判断是不是对比。
- `_has_sku_detail_intent()`、`_has_parameter_query_intent()`、`_has_price_comparison_intent()`：判断 SKU、参数、价格类问题。
- `_has_pc_intent()` / `_has_single_pc_part_intent()`：区分 PC 整机方案和单个 PC 配件推荐。
- `resolve_followup_message()`：处理上一轮推荐后的追问，比如“这个续航怎么样”“第二款加入购物车”。

`try_llm_route_tool_call()` 是 LLM 路由器。它会调用：

- `build_router_messages()`：组装 system prompt 和 user prompt。
- `_build_router_system_prompt()`：告诉 LLM 可用工具、输出 JSON schema、类目规则、购物车参数规则。
- `_build_router_user_prompt()`：把当前用户输入、`current` 累积状态、最近 query、话题、上一轮可见商品卡、当前购物车商品列表一起放进去。

购物车相关的关键改造也在这里：`_build_router_user_prompt()` 会把上一轮 `product_cards` 格式化成带 `product_id/title/brand/price` 的候选列表，把当前购物车格式化成带 `product_id/title/brand/quantity` 的列表。这样 LLM 如果判断进入 `apply_cart_instruction`，可以在 arguments 里传：

```text
operation
product_ids
target_product_id
target_product_index
target_product_mention
quantity
```

#### D. 路由纠偏：LLM 不是最终裁判

路由结束后，`chat_stream()` 会立刻调用 `validate_tool_call()`。

`validate_tool_call()` 做的是确定性 guard：

- 工具名不在白名单里，就降级到 `general_chat`。
- `price_max` 或 `budget` 超过 `_MAX_PRICE=500000`，会被裁剪。
- `brands` / `exclude_brands` 超过 `_MAX_BRANDS=50` 会被截断。
- LLM 把闲聊误判成推荐时，会按本地规则改回去。
- LLM 把购物请求误判成闲聊时，会按本地规则改回去。
- 消息里有明确购物车意图时，无论 LLM 怎么路由，都会强制修正为 `apply_cart_instruction`。

纠偏结果会写到 `routing_trace.validation`。如果发生改路由，还会带 `downgraded` 和 `downgrade_reason`。这里不负责最终选商品，购物车商品 ID 的二次校验在 `handle_cart_v2()` 里。

#### E. 会话状态更新

路由确定后，`chat_stream()` 会判断是否调用 `update_session_from_router()`。

它只对非购物车、非闲聊、非降级工具更新 session。原因是购物车和闲聊不应该污染推荐主题。`update_session_from_router()` 会更新：

- `chat_topic`：当前是推荐、闲聊还是其他话题。
- `recent_queries`：最近几轮用户 query。
- `current`：累积的类目、预算、品牌、排除品牌、偏好、`catalog_scope` 等。
- `topic_history`：历史话题摘要。

然后后端会发出 `tool_call` SSE，让前端知道本轮最终选择了哪个工具。

#### F. 轻量工具链路：购物车、闲聊、对比、参数、SKU、价格

如果工具在 `_LIGHTWEIGHT_TOOLS` 里，`chat_stream()` 直接调用 `_dispatch_lightweight()`，不会进入附件分析和推荐上下文准备。

轻量工具包括：

- `handle_cart_v2()`：购物车主入口。它会把 add/remove/set_quantity 转成待确认计划，`clear` 目前直接执行。
- `handle_general_chat()`：闲聊入口。它会调用 `_generate_general_chat_llm_response()` 生成简短回复，失败时用 `_generate_general_chat_fallback()` 模板。
- `handle_compare_v2()`：商品对比。它优先用路由参数里的 `product_ids`，没有就尝试上一轮推荐结果。
- `handle_parameter_query()`：参数查询，比如“这个显卡功耗多少”。
- `handle_sku_query()`：SKU 变体查询，比如“12+256 和 16+512 差多少钱”。
- `handle_price_comparison()`：价格确认，比如“比官网便宜吗”。

这些函数通常输出 `delta`、`cart_confirmation`、`cart`、`comparison_table` 或 `done`。其中购物车链路后面单独展开。

#### G. 重量工具前置：准备多轮上下文和附件

如果工具不是轻量工具，`chat_stream()` 会先调用 `prepare_recommendation_context()`。

这个函数在 `rag/api/app_context.py`，主要做三件事：

```text
prepare_attachments_for_recommendation()
build_contextual_goal()
goal_with_attachment_context()
```

- `prepare_attachments_for_recommendation()`：解析图片/附件。如果 runtime 允许视觉模型，会在 `attachments.py::analyze_image_attachment()` 里调用视觉 LLM，抽取 OCR、品类、品牌、型号、颜色、场景等。
- `build_contextual_goal()`：把当前 message 和 session 历史结合。如果用户是在追问上一轮，会拼成“上一轮目标 + User added constraints”；如果检测到新话题，则只用当前 message。
- `goal_with_attachment_context()`：把附件解析出的摘要、OCR、视觉关键词拼回推荐目标。

这一步完成后，后端会发 `progress`，如果有附件还会发 `attachment_analysis`。

#### H. 推荐链路：怎么解析需求、挑商品、生成回复

推荐入口是 `tool_handlers.handle_recommend()`。它做的是 API 层编排：

```text
handle_recommend()
  -> validate_goal()
  -> retrieve_image_evidence()
  -> call_recommendation_fn()
  -> fact_check_result()
  -> remember_recommendation()
  -> update_topic_memory()
  -> generate_natural_response()
  -> emit product_cards / result / done
```

几个关键函数的实际作用：

- `validate_goal()`：确认推荐目标不是空，也符合基本业务输入规则。
- `retrieve_image_evidence()`：如果用户上传图片，从图片向量索引里找相似商品证据。
- `call_recommendation_fn()`：兼容不同 `recommendation_fn` 参数的调用包装器，实际通常调用 `recommend_shopping_products()`。
- `fact_check_result()`：用本地 catalog 校验商品 ID、价格等事实，防止响应里出现不存在或价格偏差过大的商品。
- `remember_recommendation()`：把 `last_goal`、`last_requirement`、`last_result` 写入 session，后续“第二款加入购物车”要靠这里保存的商品卡。
- `update_topic_memory()`：更新短期话题 JSON。
- `generate_natural_response()`：把结构化推荐结果转成自然语言 `delta`，不是商品事实源。

推荐核心在 `recommendation_pipeline.py::recommend_shopping_products()`：

```text
recommend_shopping_products()
  -> router_arguments 存在：_requirement_from_args_v2()
  -> router_arguments 不存在：parse_requirement()
  -> build_recommendation_result()
  -> enrich_recommendation_result()
  -> attach_grounded_explanation()
```

- `_requirement_from_args_v2()`：把 router 给出的结构化参数转成 `RequirementSpec`。主 `/api/chat/stream` 大多数请求会走这里，因为 router 已经抽了 slots。
- `parse_requirement()`：规则解析 + 可选 LLM parse。旁路接口或没有 router arguments 的请求更容易走这里。
- `build_recommendation_result()`：真正挑商品的核心函数。
- `enrich_recommendation_result()`：生成导购建议、追问、优化建议；LLM 不可用时用规则模板。
- `attach_grounded_explanation()`：基于商品卡和证据生成解释，要求只能从给定 evidence 里说。

真正挑商品在 `package_builder.py::build_recommendation_result()`：

```text
load_catalog_for_scope()
rewrite_query(..., use_llm=False)
detect_no_match_reason()
retrieve_evidence_with_timeout()
fuse_text_and_image_evidence()
score_required_components()
build_recommendation_plan()
build_product_cards()
build_candidate_scope()
build_comparison_table()
```

- `load_catalog_for_scope()`：按 `catalog_scope` 选择普通电商 catalog、PC 配件 catalog 或 combined catalog。
- `rewrite_query(..., use_llm=False)`：用当前 session 做规则型 query 改写，比如补充上下文，不走 LLM。
- `retrieve_evidence_with_timeout()`：可选 Milvus 检索。失败或超时不阻断推荐。
- `score_required_components()`：对每个目标类目筛候选并打分。
- `filter_products_for_requirement()`：结构化过滤，处理品类、预算、品牌、排除词、库存等硬条件。
- `fuse_candidates()`：把规则候选和向量召回候选融合。
- `score_products()`：综合场景匹配、属性匹配、价格、口碑、库存、SKU、详情质量、RAG evidence 等分数排序。
- `build_product_cards()`：把排序后的商品变成前端能展示的商品卡。

推荐结果回到 `handle_recommend()` 后，会按顺序发：

```text
intent_route
progress
delta
product_cards
candidate_scope
comparison_table
follow_up_questions
result
done
```

如果 router 参数里有 `action=add_to_cart`，推荐结束后不会直接写购物车，而是生成 `cart_confirmation`，等待 `/api/cart/confirm`。

#### I. 购物车链路：怎么根据 query 找到要操作的商品

购物车入口是 `tool_handlers.handle_cart_v2()`。它先调用 `_resolve_cart_action()` 判断动作：

- `operation=add/remove/set_quantity/clear/view`：优先用 LLM 或 router 下传的结构化动作。
- 没有 `operation` 时，用 `session_state.infer_cart_action()` 从中文关键词判断。

加购走 `_handle_cart_add()`，删除和改数量走 `_handle_cart_modify()`，查看走 `_handle_cart_view()`，清空走 `_handle_cart_clear()`。

真正决定商品的是 `_resolve_product_for_cart()`：

```text
显式 product_ids（请求附件/消息 ID）
-> LLM/router 下传 product_ids / target_product_id
-> target_product_index 或消息里的“第一款/第二款”
-> 当前购物车或上一轮 product_cards 的服务端顺序校验
-> 标题/品牌模糊匹配
-> LLM 消歧 _llm_resolve_cart_product()
-> 首项兜底
```

这里有两层保护：

- 加购时，LLM 传下来的 `product_id` 必须属于上一轮用户实际看到的 `product_cards`。
- 删除/改数量时，LLM 传下来的 `product_id` 必须属于当前 `cart`。

如果 LLM 同时传了 `target_product_index=2` 和错误的 `product_id`，服务端会按用户原文序号和当前可见列表重新解析，优先相信服务端上下文。`last_recommended_product_ids()` 也已经改成优先按 `product_cards` 顺序返回，保证“第二款”对应前端实际展示的第二张卡。

购物车不会马上写入 `cart`。`_handle_cart_add()` / `_handle_cart_modify()` 会先创建 plan：

```text
_make_plan()
session.pending_cart_action = plan
save_session(session)
emit cart_confirmation
```

用户点击确认后，`rag/api/routes/chat.py::cart_confirm()` 会：

```text
读取 session.pending_cart_action
检查 expires_at
根据 operation 拼 instruction
调用 session_state.apply_cart_instruction()
清空 pending_cart_action
save_session()
返回 cart snapshot
```

`apply_cart_instruction()` 才是真正写购物车的函数。它会再次调用 `infer_cart_action()` 和 `resolve_cart_product_ids()`，然后修改 `session.cart`，最后用 `cart_snapshot()` 生成购物车展示数据。

#### J. 闲聊和其他非推荐链路

如果路由是 `general_chat`，系统走 `handle_general_chat()`：

- 先调用 `update_topic_memory()`，把当前 topic 标成 general chat。
- 再调用 `_generate_general_chat_llm_response()`，让 LLM 用 1 到 3 句话回答。
- 如果 LLM 不可用或输出太短，走 `_generate_general_chat_fallback()` 模板。
- 最后发 `delta` 和 `done`。

如果路由是 `compare_products`，系统走 `handle_compare_v2()`：

- 优先使用 router 或请求里的 `product_ids`。
- 如果没有，尝试上一轮推荐结果。
- 调用 `compare_products()` 生成对比行、赢家、价格/参数差异。

如果路由是 `parameter_query`、`sku_detail`、`price_comparison`：

- handler 会根据 router 提取的 `product_mentions`、`attribute`、`sku_criteria`、上一轮商品卡等上下文定位商品。
- 这些链路主要返回 `delta`，不会进入完整推荐组包。

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
  -> 购物车工具会尽量输出 operation / product_ids / target_product_id / target_product_index / quantity
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
     -> LLM 可输出 operation=add、product_ids、target_product_id、target_product_index
  -> validate_tool_call()
  -> tool = apply_cart_instruction
  -> handle_cart_v2()
  -> _resolve_cart_action() 得到 add
  -> _handle_cart_add()
     -> _resolve_product_for_cart()
        -> 显式 product_ids
        -> tool_call.arguments.product_ids / target_product_id，且必须命中上一轮可见商品卡
        -> target_product_index 或用户原文序号，按 product_cards 展示顺序解析
        -> 上一轮推荐 last_result 的上一款引用
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
     -> LLM 可输出 operation=remove/set_quantity、product_ids、target_product_id、target_product_index
  -> validate_tool_call()
  -> tool = apply_cart_instruction
  -> handle_cart_v2()
  -> _handle_cart_modify()
     -> 空购物车直接 delta 提示
     -> _check_cart_ambiguity() 检查同品类歧义、序号越界
     -> _resolve_product_for_cart()
        -> 显式 id / LLM 目标 id，且必须命中当前 cart items
        -> 当前 cart 模糊匹配
        -> 序号，按当前购物车顺序解析
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

- `tests/test_cart_improvements.py` 当前通过：56 passed。
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
