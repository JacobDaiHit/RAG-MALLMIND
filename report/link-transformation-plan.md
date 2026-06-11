# 后端链路改造方案

**日期：** 2026-06-11  
**基于：** 
- 用户链路改造建议
- [死代码审计报告](dead-code-audit-rag.md)（3 个并行代理扫描 55 个文件）
- 3 轮深度代码追踪（chat 流链路、推荐/对比/购物车逻辑、session 与产品加载）

**设计原则：**
1. 尽量少用正则匹配等传统方式做链路修改
2. 一切基于 RAG + 外部大模型
3. 追求稳定、少幻觉、首 token 短的后端

---

## 一、新版完整链路图

```
HTTP POST /api/chat/stream
    │
    ▼
┌─ chat.py:chat_stream() ────────────────────────────────────────────────────┐
│                                                                             │
│  ① get_session(session_id)                                                  │
│     读取或创建 ShoppingSession                                               │
│                                                                             │
│  🟢 新增 ①.5 sanitize_input(message, attachments)                           │
│        ├─ message 长度上限（2000字符）                                       │
│        ├─ 首尾空白修剪                                                       │
│        └─ session_id 格式校验                                                │
│                                                                             │
│  ② route_shopping_tool_call(message, session, use_llm)                      │
│     ├─ try_llm_route_tool_call(message, session)                            │
│     │   ├─ build_router_messages(message, session)                          │
│     │   │   ├─ _build_router_system_prompt()  ← 工具定义+Schema+少量示例    │
│     │   │   └─ _build_router_user_prompt()    ← session.current 上下文注入  │
│     │   ├─ LLM 返回 JSON: {name, arguments, source}                         │
│     │   ├─ RoutedToolCall.model_validate(payload)  ← Pydantic 校验          │
│     │   └─ 🟢 新增: 保留 local_route 结果作对比用                           │
│     └─ LLM 失败 → local_route_tool_call() 降级                              │
│                                                                             │
│  🟢 新增 ②.5 validate_tool_call(tool_call, session, catalog)                │
│        ├─ 工具名白名单（ALLOWED_TOOL_NAMES）                                 │
│        ├─ 🟢 入参值域裁剪：                                                  │
│        │    ├─ price_max > 500000 → 裁剪到 500000（记录裁剪原因）            │
│        │    ├─ price_max < 50 且非 PC 场景 → 标记为 budget_insane           │
│        │    ├─ category 枚举校验 → 不在白名单则降级到 general_chat           │
│        │    └─ brands 列表长度 > 50 → 截断前 50 个                           │
│        ├─ 🟢 路由结果与本地规则结果对比：                                    │
│        │    ├─ LLM 说 recommend 但 message 纯闲聊 → 降级                     │
│        │    ├─ LLM 说 general_chat 但 message 含明确购物信号 → 用本地结果   │
│        │    └─ 对比不一致时记录路由争议日志                                  │
│        └─ 若校验失败 → 降级到 general_chat 或本地路由结果                    │
│                                                                             │
│  ③ update_session_from_router(session, message, tool_call)                  │
│     🟢 改进: 以下场景时跳过 session 更新:                                    │
│        ├─ validate_tool_call 返回了降级后的 tool_call（争议路由不污染状态）  │
│        └─ 工具名为 general_chat（闲聊不累积推荐状态）                        │
│     原有累积规则不变:                                                        │
│     ├─ brands / exclude_brands（区分空列表 vs 未输出）                       │
│     ├─ sub_category / must_have_terms（PC 配件场景替换不累积）               │
│     ├─ price_max / budget（新值覆盖旧值）                                    │
│     ├─ recent_queries（滑动窗口 5 轮）                                      │
│     └─ topic_history / chat_topic                                           │
│                                                                             │
│  ④ 根据 tool_call.name 分发：                                               │
│     ├─ apply_cart_instruction → 🟢 handle_cart_v2()  ← 改为计划+确认         │
│     ├─ general_chat           → handle_general_chat()                       │
│     ├─ compare_products       → 🟢 handle_compare_v2() ← 加入事实校验        │
│     ├─ generate_pc_build_plan → handle_pc_build()      （暂不改动）          │
│     └─ recommend_shopping_products → 🟢 handle_recommend_v2()               │
│                                                                             │
│  🟢 新增 ⑮ start_span(request_id, session_id, message)                      │
│        └─ 记录开始时间、输入摘要，存入 session.llm_call_log                  │
│                                                                             │
│  🟢 新增 ⑱ end_span(total_latency_ms, response_length, tool_name,           │
│                      success, fact_check_passed)                            │
│        └─ 结构化日志写入 session.llm_call_log（保留最近 20 条）              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 二、推荐链路改进版（handle_recommend_v2）

```text
handle_recommend_v2(session, message, tool_call)
    │
    ├─ ⑤ prepare_recommendation_context(message, attachments, session)
    │     └─ build_contextual_goal(session, message)
    │         从 session.recent_queries 拼接上下文
    │     🟢 新增: goal 长度上限检查（≤ 1500 字符），超长截断+记录
    │
    ├─ ⑥ recommend_shopping_products(goal, router_arguments=tool_call.args)
    │     ├─ validate_business_goal(goal)  ← 输入卫生检查（保持现有逻辑）
    │     │   🟢 新增: 若 skip_keyword_check=True 且 goal 语义不清，
    │     │            调用 LLM 快速确认: "这是否为购物意图？"（temperature=0，max_tokens=10）
    │     │
    │     ├─ 🟢 _requirement_from_args_v2(router_arguments, goal, session)
    │     │   │  原: _requirement_from_args() — 纯字段映射，不读 session
    │     │   └─ 现: 融合 session.current 中未覆盖的历史约束
    │     │      - 若 router_arguments.brands 为 None → 继承 session.current.brands
    │     │      - 若 router_arguments.price_max 为 None → 继承 session.current.price_max
    │     │      - 若 router_arguments.category 为 None → 继承 session.current.category
    │     │      - 若 router_arguments.exclude_brands 为 None → 继承 session.current.exclude_brands
    │     │      - 主动清空通过特殊标记 __CLEAR__ 实现（语义：用户明确要重置）
    │     │      - 若 router_arguments.catalog_scope 为 None → 继承 session.current.catalog_scope
    │     │   🟢 新增: 构造完成后校验 category 是否存在于产品库中
    │     │
    │     ├─ build_recommendation_result(requirement, catalog)    ← 不变
    │     │   ├─ filter_products_for_requirement()                ← 不变
    │     │   │   ├─ category 精确匹配
    │     │   │   ├─ matches_target_sub_category()
    │     │   │   ├─ violates_brand_or_text_exclusion()
    │     │   │   ├─ matches_all_required_terms()
    │     │   │   └─ budget 过滤
    │     │   ├─ score_products()                                 ← 不变
    │     │   │   ├─ score_scenario_match()
    │     │   │   ├─ score_attribute_match()
    │     │   │   ├─ score_price_fit()
    │     │   │   └─ score_reputation_fit()
    │     │   └─ build_recommendation_plan()                      ← 不变
    │     │
    │     └─ 🟢 fact_check_result(fact_check_products)           ← 新增
    │           ├─ 验证每个推荐商品 ID 存在于真实商品库
    │           │   （调用 ProductCatalog.get()，不存在的剔除并记录）
    │           ├─ 验证价格与真实售价偏差 ≤ 阈值，否则自动修正为 base_price
    │           ├─ 验证库存状态: stock_status != "sold_out" → 标记但不剔除
    │           └─ 若校验失败率 > 50% → 降级为本地规则推荐或返回通用回复
    │
    ├─ ⑦ remember_recommendation(session, goal, payload)         ← 不变
    │     🟢 新增: 同时写入 session.last_fact_check_status
    │
    └─ ⑧ yield SSE 事件流
         🟢 新增事件类型: "fact_check" = {passed, product_count, issues}
```

---

## 三、购物车链路改进版（handle_cart_v2）

原版直接执行 `apply_cart_instruction` → 改为 **生成计划 + 用户确认**

```text
handle_cart_v2(session, message, tool_call)
    │
    ├─ ⑨ plan_cart_action(tool_call.arguments, session.cart)
    │     └─ 🟢 新增 CartActionPlan {
    │           operation: "add" | "remove" | "update_quantity" | "clear",
    │           product_id: "p_digital_001",
    │           product_title: "xxx",          ← 从 catalog.get(id) 查找真实名称
    │           quantity: 2,
    │           estimated_unit_price: 99.99,   ← 从 catalog 获取真实价格
    │           estimated_total: 199.98
    │        }
    │     🟢 新增: 若 product_id 不存在于 catalog → 返回错误 SSE 事件，终止
    │
    ├─ ⑩ 将计划存入 session.pending_cart_action（含时间戳，60秒过期）
    │
    ├─ ⑪ 返回 SSE 事件: { type: "cart_confirmation", plan: {...} }
    │     前端展示确认按钮（确认 / 取消 / 修改数量）
    │
    └─ 🟢 新增接口 POST /api/cart/confirm
          ├─ 请求体: { session_id, confirmed: true/false, adjusted_quantity: Optional[int] }
          ├─ 验证 session.pending_cart_action 存在且未过期（60秒）
          ├─ 若 confirmed=true: 执行真实购物车写操作
          │   └─ apply_cart_instruction(session, instruction, catalog, [product_id])
          ├─ 若 confirmed=false: 丢弃计划
          ├─ 清空 session.pending_cart_action
          └─ 返回: { status: "applied" | "cancelled", cart: {...} }
```

### 原 `POST /api/cart/actions` 接口处理

保留现有 `/api/cart/actions` 端点，但增加逻辑：
- 若请求来自 SSE 流上下文（有 `session_id` 且有 `pending_cart_action`），直接执行（兼容旧前端）
- 否则走 v2 确认流程

---

## 四、对比模块改进版（handle_compare_v2）

```text
handle_compare_v2(session, message, tool_call)
    │
    ├─ ⑫ 根据 tool_call.arguments.product_ids 获取商品详情
    │     🟢 新增: 所有 product_id 必须通过 ProductCatalog.get() 存在性验证
    │        不存在的 product_id → 记录到 missing_product_ids，并从列表中剔除
    │        若全部不存在 → 返回 "未找到待对比商品" SSE 事件，终止
    │
    ├─ ⑬ 🟢 fact_check_products(product_ids, catalog)
    │     └─ 验证内容:
    │        ├─ 所有 product_id 真实存在
    │        ├─ 对比商品属于同一 sub_category（若不属于，标记 "跨品类对比"）
    │        ├─ 对比商品价格区间是否重叠（若不重叠，标记 "价格区间差异大"）
    │        └─ 输出校验报告 → yield SSE: { type: "fact_check", ... }
    │
    ├─ ⑭ 生成对比表格（属性对齐）
    │     🟢 新增: choose_comparison_winner() 输出中包含真实价格差异（元）
    │
    └─ ⑮ yield SSE 事件流（对比结果 + fact_check 事件）
```

---

## 五、新增 Session 字段

```python
@dataclass
class ShoppingSession:
    # ... 原有字段不变 ...
    
    # 🟢 新增字段
    pending_cart_action: Dict[str, Any] = field(default_factory=dict)
    # 结构: {
    #     "operation": "add"|"remove"|...,
    #     "product_id": str,
    #     "product_title": str,
    #     "quantity": int,
    #     "estimated_total": float,
    #     "created_at": float,    # time.time()
    #     "expires_at": float     # created_at + 60
    # }
    
    last_fact_check_status: str = "passed"   # 🟢 "passed" | "partial_fail" | "fail"
    
    llm_call_log: List[Dict[str, Any]] = field(default_factory=list)  # 🟢 最近 20 条
```

### `session_from_dict()` 兼容性处理

```python
# 新增字段的类型守卫（向后兼容旧 session 数据）
if not isinstance(data.get("pending_cart_action"), dict):
    data["pending_cart_action"] = {}
if not isinstance(data.get("last_fact_check_status"), str):
    data["last_fact_check_status"] = "passed"
if not isinstance(data.get("llm_call_log"), list):
    data["llm_call_log"] = []
```

---

## 六、死代码清理清单（同步执行）

### 第一梯队：直接删除（零风险，配合改造同步执行）

| # | 文件/位置 | 类型 | 行数 | 备注 |
|---|-----------|------|------|------|
| 1 | `rag/retrieval/` 整个包 | 包 | ~3 | 零 imports，从未实现 |
| 2 | `rag/api/response_utils.py` | 文件 | ~33 | 3 个重复函数，零 imports |
| 3 | `rag/legacy/tools.py` | 文件 | ~56 | 自声明已弃用，零生产 imports |
| 4 | `rag/utils/tools.py` | 文件 | ~8 | 死重导出 shim |
| 5 | `rag/recommendation/pc_types.py:80` `legacy_pc_component_type()` | 函数 | ~3 | 导入但从未调用 |
| 6 | `rag/recommendation/pc_build.py:15` `legacy_pc_component_type` 导入 | 死导入 | 1 | 连带清理 |
| 7 | `rag/recommendation/pc_build.py:660` `role_name()` | 函数 | ~2 | 从未被调用 |
| 8 | `rag/recommendation/cost_estimator.py:85-99` `product_currency()`, `pricing_confidence()`, `pricing_rule()` | 3 个函数 | ~12 | 从未被调用 |
| 9 | `rag/recommendation/scorer.py:577` `score_modality_fit()` | 函数 | ~4 | 未接入评分管道 |
| 10 | `rag/recommendation/recommendation_graph.py:78` `run()` | 方法 | ~28 | 同步替代，从未调用 |
| 11 | `rag/recommendation/session_state.py:199` `clear_session()` | 函数 | ~3 | 无 API 端点调用 |
| 12 | `rag/recommendation/session_state.py:89` `InMemorySessionStore.set()` | 方法 | ~3 | 委托给 save()，无人调用 |
| 13 | `rag/recommendation/tool_router.py:133` `ROUTED_CALL_SCHEMA` | 常量 | ~12 | 未被导入/引用 |
| 14 | `rag/recommendation/tool_router.py:847` `normalize_tool_arguments()` | 函数 | ~20 | 从未调用 |
| 15 | `rag/recommendation/tool_router.py:1163` `_should_compare_products()` | 函数 | ~13 | 路由用另一个函数 |
| 16 | `rag/api/recommendation_app.py:25-26,38` 3 个死导入 | 导入 | ~3 | `goal_with_attachment_context`, `normalize_attachments`, `parse_adjustment_amount` |

**小计：~200 行**

### 第二梯队：需要同步更新测试

| # | 文件/位置 | 类型 | 备注 |
|---|-----------|------|------|
| 17 | `rag/recommendation/tool_router.py:973` `merge_route_arguments()` | 函数 | 仅 `tests/diag_mimo_raw.py` 使用，删除后需同步移除测试 |
| 18 | `rag/recommendation/session_context.py:101` `session_context_for_llm()` | 函数 | 仅 `tests/test_session_context_memory.py` 使用 |
| 19 | `rag/recommendation/session_state.py:191` `reset_session()` | 函数 | 仅 `tests/test_session_state_store.py` 使用 |
| 20 | `rag/api/routes/common.py:25-49` `has_image_data()`, `is_test_env()`, `system_degraded()` | 3 个函数 | 仅测试 imports |

### 第三梯队：去重合并（改造过程中逐步执行）

| # | 项目 | 操作 | 优先级 |
|---|------|------|--------|
| 21 | `recommendation_pipeline.py:1122` `model_to_dict()` | 替换为 `from rag.api.app_context import model_to_dict` | 低 |
| 22 | `dedupe_strings()` 4 份副本 | 合并到 `rag/utils/rag_utils.py` | 中 |
| 23 | `_parse_positive_int()` 2 份副本 | 合并到 `rag/utils/rag_utils.py` | 中 |

---

## 七、实施顺序

```
第 1 步: 死代码清理（第一梯队 16 项）— 15 分钟
         ├─ 删除 4 个死文件/包
         ├─ 删除 11 个死函数 + 1 个死导入
         └─ 运行测试确认无破坏

第 2 步: 新增 Session 字段（pending_cart_action, last_fact_check_status, llm_call_log）
         ├─ 修改 ShoppingSession dataclass
         ├─ 修改 session_from_dict() 兼容处理
         └─ 运行测试确认序列化正常

第 3 步: 新增 ①.5 sanitize_input() — 10 分钟
         └─ 在 chat.py chat_stream() 中 message 长度上限 + session_id 校验

第 4 步: 新增 ②.5 validate_tool_call() — 30 分钟
         ├─ 在 tool_router.py 新建 validate_tool_call()
         ├─ 白名单 + 值域裁剪 + 路由争议检测
         └─ 在 chat.py 中插入调用点

第 5 步: 改造 _requirement_from_args_v2() — 20 分钟
         ├─ 在 recommendation_pipeline.py 新建 v2 函数
         ├─ 融合 session.current 历史约束
         └─ 增加 category 产品库存在性校验

第 6 步: 新增 fact_check_result() — 20 分钟
         ├─ 在 recommendation_pipeline.py 新建 fact_check_result()
         ├─ 验证 product_id 存在 + 价格真实 + 库存状态
         └─ 在 handle_recommend() 中调用

第 7 步: 改造 handle_cart_v2() + 新增 /api/cart/confirm — 30 分钟
         ├─ 在 tool_handlers.py 新建 handle_cart_v2()
         ├─ 在 chat.py 新建 cart/confirm 端点
         └─ 在 chat_stream() 中改用 handle_cart_v2()

第 8 步: 改造 handle_compare_v2() — 15 分钟
         ├─ 在 tool_handlers.py 新建 handle_compare_v2()
         ├─ 加入 product_id 存在性验证 + 同品类检测
         └─ 输出 fact_check SSE 事件

第 9 步: 新增 span 日志 — 15 分钟
         ├─ 在 chat.py chat_stream() 入口/出口记录结构化日志
         └─ 记录 LLM 调用延迟、token、校验结果

第 10 步: 死代码第二梯队（测试同步清理）— 10 分钟
          └─ 删除 4 个测试专用函数 + 更新相关测试

第 11 步: session 更新策略微调 — 5 分钟
          └─ general_chat 和争议路由结果不累积 session.current
```

**总计预估：~3 小时**

---

## 八、关键设计决策

| 原设计 | 改进后 |
|--------|--------|
| LLM Router 是唯一决策者，无 guard 层 | **增加 validate_tool_call()**：白名单 + 值域裁剪 + 路由争议对比 + 异常降级 |
| `_requirement_from_args` 纯映射，不合并 session | **改为 v2 版本**：融合 session.current，历史约束自动继承，`__CLEAR__` 主动清空 |
| 推荐/对比结果无事实校验 | **新增 fact_check_result()**：验证商品 ID 存在、价格真实、库存状态 |
| `apply_cart_instruction` 直接写购物车 | **改为计划+确认**：新增 `/api/cart/confirm` 接口，60 秒过期 |
| 无统一可观测性 | **增加结构化 span 日志**：记录 LLM 延迟、token、校验结果 |
| 路由争议无感知 | **LLM vs 本地结果对比**：差异超过阈值则降级，记录争议日志 |
| general_chat 也累积推荐状态 | **跳过 session 更新**：闲聊不污染推荐上下文 |

---

## 九、改造中遵循的原则

1. **最少正则** — validate_tool_call() 的值域裁剪使用数值比较，category 校验使用枚举白名单，不引入新的正则模式
2. **基于 RAG + LLM** — fact_check 层通过 ProductCatalog（向量库+结构化产品库）交叉验证，路由争议检测依赖 LLM 的置信度信号
3. **不作弊** — 事实校验不从 LLM 生成价格/库存信息，只从产品库读取；LLM 仅用于语义判断（"是否为购物意图"）
4. **首 token 快** — 
   - validate_tool_call() 是纯规则（< 1ms）
   - fact_check_result() 只做 dict 查找（< 5ms）
   - `__CLEAR__` 特殊标记在 _requirement_from_args_v2 中单独处理
   - 购物车确认是异步的（不影响 SSE 流首 token）
5. **少幻觉** — 
   - 产品 ID 存在性全部通过 ProductCatalog.by_id 验证
   - 价格从 catalog 读取真实值，不信任 LLM 输出
   - 路由争议检测捕获 LLM 误判

---

## 十、修改文件清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `rag/api/routes/chat.py` | 🔧 改造 | ①.5 sanitize, ②.5 validate 调用, ④ 分发改用 v2 handlers, ⑮⑱ span 日志, 新增 cart/confirm 端点 |
| `rag/recommendation/tool_router.py` | ➕ 新增 + ✂ 删除 | 新增 validate_tool_call()；删除 normalize_tool_arguments(), ROUTED_CALL_SCHEMA, _should_compare_products(), merge_route_arguments() |
| `rag/recommendation/tool_handlers.py` | 🔧 改造 | 新增 handle_recommend_v2(), handle_cart_v2(), handle_compare_v2()；原有 handlers 保留兼容 |
| `rag/recommendation/recommendation_pipeline.py` | 🔧 改造 + ➕ 新增 | 新增 _requirement_from_args_v2(), fact_check_result()；删除 model_to_dict() 本地定义 |
| `rag/recommendation/session_state.py` | 🔧 改造 + ✂ 删除 | 新增 3 个 ShoppingSession 字段 + session_from_dict() 兼容；删除 clear_session(), InMemorySessionStore.set(), reset_session() |
| `rag/recommendation/session_context.py` | ✂ 删除 | 删除 session_context_for_llm() |
| `rag/recommendation/cost_estimator.py` | ✂ 删除 | 删除 product_currency(), pricing_confidence(), pricing_rule() |
| `rag/recommendation/scorer.py` | ✂ 删除 | 删除 score_modality_fit()；保留 average()（内部使用） |
| `rag/recommendation/recommendation_graph.py` | ✂ 删除 | 删除 run() 方法 |
| `rag/recommendation/pc_types.py` | ✂ 删除 | 删除 legacy_pc_component_type() |
| `rag/recommendation/pc_build.py` | ✂ 删除 | 删除 role_name(), legacy_pc_component_type 导入 |
| `rag/api/recommendation_app.py` | ✂ 删除 | 删除 3 个死导入 |
| `rag/api/routes/common.py` | ✂ 删除 | 删除 has_image_data(), is_test_env(), system_degraded() |
| `rag/api/response_utils.py` | 🗑 删除 | 整个文件 |
| `rag/legacy/tools.py` | 🗑 删除 | 整个文件 |
| `rag/utils/tools.py` | 🗑 删除 | 整个文件 |
| `rag/retrieval/__init__.py` | 🗑 删除 | 整个包 |
| `rag/recommendation/__init__.py` | 🔧 改造 | 移除未使用的导出项（ProductScore, BASE_WEIGHTS 等） |
| `rag/utils/rag_utils.py` | 🔧 改造 | 将 dedupe_strings()、_parse_positive_int()、average() 合并到此文件 |

---

## 十一、验证方式

```bash
# 1. 完整测试套件（改造后）
pytest tests/ -v

# 2. 导入检查 — 确认所有 rag 模块无错误加载
python -c "
from rag.api.routes.chat import chat_stream
from rag.recommendation.tool_router import validate_tool_call
from rag.recommendation.tool_handlers import handle_recommend_v2, handle_cart_v2, handle_compare_v2
from rag.recommendation.recommendation_pipeline import _requirement_from_args_v2, fact_check_result
print('ALL IMPORTS OK')
"

# 3. API 启动冒烟测试
python scripts/run_recommendation_api.py &
sleep 3
curl http://localhost:8000/api/health
kill %1

# 4. SSE 流测试（手动）
curl -X POST http://localhost:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"session_id":"test-001","message":"推荐一款1000元以内的蓝牙耳机"}'

# 5. 购物车确认流测试
# Step 1: 发送 "把 p_digital_001 加入购物车" → 收到 cart_confirmation 事件
# Step 2: POST /api/cart/confirm {session_id, confirmed: true} → 收到 status: "applied"

# 6. 死代码验证 — 确认以下 imports 全部失败
python -c "
import rag.retrieval          # ❌ 应该失败
import rag.legacy.tools       # ❌ 应该失败
from rag.api.response_utils import model_to_dict  # ❌ 应该失败
"
```

---

## 十二、需要同步更新的测试文件

| 测试文件 | 改动原因 |
|----------|----------|
| `tests/test_backend_refactor_boundaries.py` | 移除了 `rag.utils.tools` 和 `rag.legacy.tools` |
| `tests/diag_mimo_raw.py` | 移除了 `merge_route_arguments()` |
| `tests/test_session_context_memory.py` | 移除了 `session_context_for_llm()` |
| `tests/test_session_state_store.py` | 移除了 `reset_session()` |
| `tests/test_runtime_mode.py` | 移除了 `has_image_data()`, `is_test_env()` |
| `tests/test_runtime_mode_api.py` | 移除了 `has_image_data()`, `is_test_env()` |
| `tests/test_recommendation_llm.py` | `recommend_api_stack` 可能已移除 |

---

## 十三、风险与回退策略

| 风险 | 概率 | 缓解措施 |
|------|------|----------|
| validate_tool_call() 误判导致正确路由被降级 | 低 | 路由争议日志可观测；`use_llm` 标志可关闭 |
| 购物车确认导致旧前端不兼容 | 中 | 保留 `/api/cart/actions` 直接执行路径；通过 session.pending_cart_action 是否存在判断 |
| fact_check_result() 因空 catalog 全量失败 | 低 | 捕获异常 → 降级返回 unfiltered 结果 |
| session 新字段序列化失败 | 低 | session_from_dict() 类型守卫兜底 |

---

*方案完。下一步：等待审批后按实施顺序逐步执行。*
