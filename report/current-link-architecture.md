# 后端链路架构说明

**最后更新：** 2026-06-11  
**版本：** v3（🟢 v2改造 + 🔵 v3新增）  
**关联文档：** [v2 链路改造方案](link-transformation-plan.md) · [死代码审计报告](dead-code-audit-rag.md) · [v3 问题报告](bound_test_v3_issues.md)

> ⚠️ 本文档为链路架构的**权威说明**。后续如有链路改动，必须同步更新此文档。

---

## 目录

1. [总览：请求生命周期 + 节点状态](#一总览请求生命周期)
2. [入口层：chat_stream()](#二入口层chat_stream)
3. [路由层：route_shopping_tool_call()](#三路由层route_shopping_tool_call)
4. [校验层：validate_tool_call()](#四校验层validate_tool_call)
5. [Session 状态管理](#五session-状态管理)
6. [处理层：5 个工具处理器](#六处理层5-个工具处理器)
7. [推荐管道：recommend_shopping_products()](#七推荐管道recommend_shopping_products)
8. [响应生成层](#八响应生成层)
9. [事实校验层](#九事实校验层)
10. [话题切换机制](#十话题切换机制)
11. [可观测性](#十一可观测性)
12. [Session 字段速查表](#十二session-字段速查表)
13. [文件索引](#十三文件索引)

---

## 一、总览：请求生命周期 + 节点状态

```
客户端 POST /api/chat/stream
         │
         ▼
    ┌──────────────────────────────────────────────────────────┐
    │  chat.py:chat_stream()                                   │
    │                                                          │
    │  ① sanitize_input()           🟢 输入消毒         ✅     │
    │  ② get_session()              读取/创建 session   ✅     │
    │  ③ route_shopping_tool_call() LLM+本地路由        ✅     │
    │     ├─ LLM 路由: mimo-v2.5                           ⚠️   │
    │     └─ 本地规则: 11步决策树                          ✅    │
    │  ④ validate_tool_call()       🟢 路由校验         ✅     │
    │  ⑤ update_session_from_router()  状态累积         ✅     │
    │     ├─ topic_memory.topic_type 注入路由prompt 🔵    ✅    │
    │     └─ 话题切换检测（显式信号+品类感知）🔵         ✅    │
    │  ⑥ 工具分发 ← Router多模式感知 🔵                 ✅     │
    │     ├─ apply_cart_instruction → handle_cart_v2() 🟢 ✅    │
    │     ├─ general_chat           → handle_general_chat()✅   │
    │     ├─ compare_products       → handle_compare_v2()🟢⚠️  │
    │     ├─ generate_pc_build_plan → handle_pc_build()   ✅   │
    │     └─ recommend_shopping_products → handle_recommend()  │
    │  ⑦ 推荐/PC处理完成                                    │
    │  ⑧ generate_natural_response() 🔵 响应多样化     ✅     │
    │     ├─ LLM 生成 (mimo-v2.5, t=0.9)             ⚠️    │
    │     └─ 模板变体库 (5~8种随机)                    ✅     │
    │  ⑨ SSE 事件流返回 + _end_span() 🟢              ✅     │
    └──────────────────────────────────────────────────────────┘
         │
         ▼
    客户端收到 SSE 事件流（text/event-stream）
```

### 节点状态图例

| 符号 | 含义 |
|------|------|
| ✅ | 正常 — 21 case / 136 轮全量回归通过 |
| ⚠️ | 降级运行 — 功能正常但有已知局限（MIMO 中文、空对比降级） |
| ❌ | 不可用 — 需要修复 |
| 🟢 | v2 改造新增（Phase 1-6） |
| 🔵 | v3 改造新增（Part A-D） |

### 关键设计原则

- 路由：LLM + 本地规则双通道，本地规则兜底保证可用性
- 事实校验：基于真实产品库（RAG），不从 LLM 生成价格/库存
- 购物车：计划+确认模式（v2），防止误操作
- 回复：LLM 多样化生成 + 模板变体兜底（v3）
- 话题切换：显式信号 + 品类感知 + topic_memory 注入（v3）
- 所有校验层均为确定性规则（< 1ms），不增加首 token 延迟

---

## 二、入口层：chat_stream()

**文件：** [rag/api/routes/chat.py](rag/api/routes/chat.py)  
**端点：** `POST /api/chat/stream`  
**请求体：** `ChatStreamRequest { session_id, message, attachments, images }`  
**返回：** `StreamingResponse` (SSE, media_type="text/event-stream")

### 步骤详解

#### ① sanitize_input() 🟢 ✅

```python
def sanitize_input(message: str, session_id: str) -> str:
```

- 校验 `session_id` 非空
- 修剪 `message` 首尾空白，校验非空
- 消息长度上限 **2000 字符**，超长自动截断
- 不通过则抛出 `HTTPException(400)`

#### ② get_session() ✅

**文件：** [rag/recommendation/session_state.py](rag/recommendation/session_state.py)

- 生产环境：session_id 为空时**拒绝请求**
- 非生产环境：session_id 为空时使用 `"default"`
- 存储后端：生产环境 Redis / 开发环境内存
- 新 session 自动初始化 `topic_memory`（含 topic_type="unknown"）

#### ③ route_shopping_tool_call() ✅ ⚠️

见[第三章](#三路由层route_shopping_tool_call)。LLM 路由使用 MIMO `mimo-v2.5`，中文长上下文偶有退化（⚠️）。本地规则兜底保证可用。

#### ④ validate_tool_call() 🟢 ✅

见[第四章](#四校验层validate_tool_call)。值域裁剪 + 白名单 + LLM vs 本地争议检测。

#### ⑤ update_session_from_router() ✅ 🔵

见[第五章](#五session-状态管理)。v3 增强：
- `topic_memory.topic_type` 注入 LLM router user prompt
- 话题切换检测扩展到电商场景（`should_start_new_product_topic`）
- 显式话题切换信号（"换个话题""不要了""算了"）

#### ⑥ 工具分发 ✅ 🔵

Router 现支持三种推荐模式的感知（🔵 v3 System Prompt 增强）：

| 工具名 | 处理器 | 适用场景 |
|--------|--------|----------|
| `recommend_shopping_products` | `handle_recommend()` | 单品推荐 / 组合推荐（need_bundle=true） |
| `generate_pc_build_plan` | `handle_pc_build()` | PC 整机方案 + 方案修改 ⚠️ |
| `compare_products` | `handle_compare_v2()` 🟢 | 商品对比 + 事实校验 |
| `apply_cart_instruction` | `handle_cart_v2()` 🟢 | 购物车操作（计划+确认） |
| `general_chat` | `handle_general_chat()` | 闲聊/非购物问题 |

> ⚠️ `generate_pc_build_plan` 后续修改已修复（v3），但 PC 方案之间对比仍走空。

#### ⑦ handle_recommend() 内部流程 ✅

见[第七章](#七推荐管道recommend_shopping_products)。

#### ⑧ generate_natural_response() 🔵 ✅

见[第八章](#八响应生成层)。v3 新增，替代原 `build_chat_delta_lines()` 硬编码模板。

#### ⑨ SSE 事件流 + _end_span() 🟢 ✅

最终通过 `safe_stream()` 包装，异常自动捕获。每次请求结束记录结构化 span 日志。

---

## 三、路由层：route_shopping_tool_call()

**文件：** [rag/recommendation/tool_router.py](rag/recommendation/tool_router.py)  
**状态：** ✅ (路由 136/136 = 100%)

### 总体策略

```
LLM 路由（主）→ 失败 → 本地规则（降级）
```

### 3.1 LLM 路由：try_llm_route_tool_call() ⚠️

```
用户消息
  │
  ├─ _build_router_system_prompt()
  │     ├─ 🔵 推荐模式选择规则（单品/组合/PC三种）
  │     ├─ 🔵 话题切换判断规则
  │     ├─ 工具定义（5 个工具的 JSON Schema）
  │     ├─ 品类枚举（beauty/digital/clothing/food + 9 个 PC 配件）
  │     ├─ 价格提取规则（6 种预算模式）
  │     ├─ 🔵 PC 构建后续规则（"修改已有方案 → 继续用 generate_pc_build_plan"）
  │     └─ 输出约束：仅输出 JSON，不要编造商品/价格
  │
  ├─ _build_router_user_prompt()
  │     ├─ session.current（累积状态 JSON）
  │     ├─ session.recent_queries（最近 5 轮）
  │     ├─ session.chat_topic
  │     ├─ 🔵 topic_memory.topic_type（"pc_build"/"ecommerce_recommendation"等）
  │     ├─ 购物车快照（前 5 项）
  │     └─ 用户消息（前 500 字符）
  │
  └─ LLM 调用
        ├─ model: MALLMIND_ROUTER_MODEL（默认 mimo-v2.5）⚠️
        ├─ temperature=0（确定性输出）
        ├─ thinking disabled
        ├─ max_tokens: 800（v3 从 320 提升）
        ├─ 超时：15s socket + 硬超时
        ├─ 并发控制：信号量（默认最大 2 并发）
        └─ 返回 JSON → RoutedToolCall.model_validate()
```

**容灾机制：**
- 熔断器：60s 内连续失败 ≥5 次 → 禁用 LLM 30s
- 并发超限 → 降级本地规则
- LLM 未配置 → 降级本地规则

> ⚠️ MIMO `mimo-v2.5` 在复杂路由 prompt 下中文输出偶有退化。已通过 max_tokens=800 缓解。长期建议评估中文能力更强的路由模型。

### 3.2 本地规则路由：local_route_tool_call() ✅

**决策树（顺序匹配，先到先得）：**

```
1. 购物车意图 + 非推荐意图 → apply_cart_instruction
2. 🔵 PC 构建后续（is_pc_build_followup 含分支D） → generate_pc_build_plan
3. 对比请求 → compare_products
4. PC 意图 → generate_pc_build_plan
5. PC 话题后续（topic_type=="pc_build"） → generate_pc_build_plan
6. 商品详情追问 → recommend_shopping_products
7. 常规商品品类 → recommend_shopping_products
8. 单个 PC 配件 → recommend_shopping_products
9. 商品查询意图 → recommend_shopping_products
10. 通用对话 → general_chat
11. 兜底 → recommend_shopping_products
```

### 3.3 is_pc_build_followup() 🔵 ✅

v3 增强：新增分支 D — 有 PC 配件词 + session 中有 PC 构建历史 → 直接判定为 followup。解决了 Case 4 "CPU要Intel的，不要AMD" 路由失败问题。

```python
# 🔵 新增分支 D
if has_pc_part and bool(getattr(session, "pc_build_history", None)):
    return True
```

---

## 四、校验层：validate_tool_call() 🟢 ✅

**文件：** [rag/recommendation/tool_router.py](rag/recommendation/tool_router.py)  
**状态：** ✅ (21 case 零误判)

```
router 输出 (LLM 或 本地规则)
        │
        ▼
validate_tool_call(tool_call, local_result, message, session)
        │
        ├─ 1. 白名单校验 → 不在 → 降级到 general_chat
        │
        ├─ 2. 值域裁剪
        │     ├─ price_max > 500000 → 裁剪到 500000
        │     ├─ price_max < 50 且非 PC → 标记 budget_insane
        │     └─ brands/exclude_brands > 50 项 → 截断
        │
        └─ 3. LLM vs 本地争议检测
              ├─ LLM 推荐 + message 含闲聊信号 → 用本地结果
              ├─ LLM 闲聊 + message 含购物信号 → 用本地结果
              └─ 争议记录到 routing_trace.validation.conflict
```

### 降级策略

- 校验失败 → 降级到 `general_chat`
- 争议路由 → 使用本地规则结果（更保守）
- 降级后的 tool_call 携带 `downgraded: true` + `downgrade_reason`

### Session 更新策略

- **争议路由结果** → **不更新** session.current
- **general_chat** → **不更新** session.current
- **apply_cart_instruction** → **不更新** session.current

---

## 五、Session 状态管理

**文件：** [rag/recommendation/session_state.py](rag/recommendation/session_state.py)  
**状态：** ✅

### 5.1 ShoppingSession 结构

```
ShoppingSession
├─ session_id: str
├─ updated_at: float
├─ messages: List[str]                # 最近 12 条
├─ last_goal: str
├─ last_result: Any
├─ cart: Dict[str, CartItem]
├─ topic_memory: Dict                 # 话题记忆（核心切换机制）
│   ├─ topic_type: str                # pc_build|ecommerce_recommendation|single_pc_part|...
│   ├─ route: str                     # 上次路由工具名
│   ├─ category: str                  # 上次品类
│   └─ history: List[Dict]            # 话题历史（最多 8 个）
├─ current: Dict                      # 累积路由状态
│   ├─ tool_call, query, category, catalog_scope
│   ├─ brands, exclude_brands
│   ├─ price_min, price_max, budget
│   ├─ preferences, must_have_terms, sub_category
│   └─ product_ids
├─ recent_queries: List[Dict]         # 最近 5 条
├─ chat_topic: str                    # 展示用（不参与路由决策）
├─ pc_build_history: List[Dict]       # PC 构建历史（最多 6 个）
├─ pending_cart_action: Dict          # 🟢 待确认购物车计划
├─ last_fact_check_status: str        # 🟢 "passed"|"fail"
└─ llm_call_log: List[Dict]           # 🟢 调用日志（最多 20 条）
```

### 5.2 话题切换机制 🔵 ✅

v3 增强了三层话题切换检测：

**第一层：显式切换信号**
```
"换个话题"、"不要了"、"算了"、"不买了"、"看看别的"
→ 立即触发 should_start_new_product_topic() = True
```

**第二层：品类感知切换**
```
当前 topic_type == "ecommerce_recommendation"
  且 新消息品类 ≠ 当前品类
→ 触发话题切换
```

**第三层：PC 场景豁免**
```
topic_type == "pc_build"
  且 消息包含配件词 + 有 PC 构建历史
→ is_pc_build_followup() 分支 D 阻止切换
```

### 5.3 current 字段累积规则

| 字段 | 规则 |
|------|------|
| `tool_call` | **覆盖** |
| `query` | **覆盖** |
| `category` | 覆盖（保留旧值兜底） |
| `catalog_scope` | 覆盖（保留旧值兜底） |
| `brands` | **累积去重**；传 `[]` = 清空；不传 = 保留旧值 |
| `exclude_brands` | 同 brands |
| `price_min/price_max/budget` | **覆盖**（仅当传入非 None） |
| `preferences` | **合并** |
| `must_have_terms` | PC 配件：**替换**；电商：**累积** |
| `sub_category` | PC 配件：**仅替换**；电商：保留旧值兜底 |
| `product_ids` | 覆盖（仅当传入非空） |

---

## 六、处理层：5 个工具处理器

**文件：** [rag/recommendation/tool_handlers.py](rag/recommendation/tool_handlers.py)  
**状态：** ✅ (全量通过)

### 6.1 handle_cart_v2() 🟢 ✅

```
购物车 v2：计划 + 确认模式

tool_call.arguments
    │
    ├─ 1. 提取 product_id, quantity, operation
    ├─ 2. 🟢 catalog 真实性校验 → product_id 不存在则 error
    ├─ 3. 生成 CartActionPlan（含真实价格、60s 过期）
    ├─ 4. 存入 session.pending_cart_action
    └─ 5. 返回 cart_confirmation SSE 事件
```

**确认端点：** `POST /api/cart/confirm`

### 6.2 handle_general_chat() ✅

```
1. update_topic_memory("general_chat")
2. LLM 生成回复（temperature=0.7, max_tokens=200）
3. LLM 失败 → 模板回复兜底
```

### 6.3 handle_compare_v2() 🟢 ⚠️

```
对比 v2：加入事实校验

product_ids
    │
    ├─ 1. 🟢 校验所有 product_id 真实存在
    ├─ 2. 🟢 同品类检测 + 价格区间检测
    ├─ 3. compare_products(catalog, valid_ids)
    └─ 4. 返回 fact_check + comparison_table SSE 事件
```

> ⚠️ 对比结果为空时（14/136 轮），缺少语义化降级（如"没找到型号，帮你推荐同类"）。已知问题，见 [v3 问题报告](bound_test_v3_issues.md)。

### 6.4 handle_pc_build() ✅

```
1. build_pc_plan_for_message(message, session)
     ├─ 解析预算/偏好/用途
     ├─ 融合 session.current（brands, exclude_brands, price_max）
     └─ generate_pc_build_plan() → 穷举 CPU+主板+内存+GPU+...
2. 兼容性检查 + 软评分
3. 写入 session.pc_build_history（最多 6 个）
```

### 6.5 handle_recommend()（内部使用 v2+v3 组件）✅

```
1. prepare_recommendation_context() → build_contextual_goal()
2. recommend_shopping_products(goal, router_arguments, session)
     ├─ validate_business_goal()
     ├─ 🟢 _requirement_from_args_v2() — session 感知
     ├─ build_recommendation_result()
     │   ├─ filter_products_for_requirement() → 10步过滤链
     │   ├─ score_products() → 7维度评分
     │   └─ build_recommendation_plan()
     └─ 🟢 fact_check_result() — 事实校验
3. model_to_dict(result) → payload
4. 🟢 fact_check_result(payload, catalog)
5. remember_recommendation(session, goal, payload)
6. 🔵 generate_natural_response(payload, session, message)
     ├─ LLM 可用 → llm_diverse_response (t=0.9)
     └─ 否则 → naturalize_response (模板变体)
7. yield SSE 事件流（delta + cards + comparison + result）
```

---

## 七、推荐管道：recommend_shopping_products()

**文件：** [rag/recommendation/recommendation_pipeline.py](rag/recommendation/recommendation_pipeline.py)  
**状态：** ✅

### 7.1 _requirement_from_args_v2() 🟢 ✅

与 v1 的关键区别：

| 场景 | v1 | v2 |
|------|-----|-----|
| `brands` 为 None | 空列表 | 继承 `session.current.brands` |
| `price_max` 为 None | None | 继承 `session.current.price_max` |
| `category` 为空 | 空品类 | 继承 `session.current.category` |
| 用户主动清空 | 不支持 | `brands: "__CLEAR__"` → 清空 |
| `catalog_scope` 为空 | "ecommerce" | 继承 `session.current.catalog_scope` |

### 7.2 三种推荐模式 🔵

Router System Prompt 现包含三种模式的判断规则：

| 模式 | 工具 | 触发条件 |
|------|------|----------|
| **单品推荐** | `recommend_shopping_products` | 单品类单商品（面霜、耳机、手机）；单PC配件 → catalog_scope=pc_parts |
| **组合推荐** | `recommend_shopping_products` (need_bundle=true) | 多互补商品（"防晒一套"、"配齐护肤套装"）；触发词：一套/全套/搭配/套装/穿搭/配齐 |
| **PC 整机** | `generate_pc_build_plan` | 完整主机需求 + 方案修改；触发词：配电脑/装机/整机/配置单 |

### 7.3 build_recommendation_result()

**文件：** [rag/recommendation/package_builder.py](rag/recommendation/package_builder.py)

```
requirement + catalog
    │
    ├─ 1. 加载商品库（load_catalog_for_scope）
    ├─ 2. 早期退出：detect_no_match_reason() / clarification_required()
    ├─ 3. 逐品类评分
    │      for each desired_category:
    │        ├─ filter_products_for_requirement() 10步过滤
    │        └─ score_products() 7维度加权评分
    ├─ 4. 预算缺口检测
    ├─ 5. build_recommendation_plan() → 每品类 top-1
    └─ 6. post-budget 兜底过滤
```

---

## 八、响应生成层 🔵 ✅

**文件：** [rag/recommendation/response_generator.py](rag/recommendation/response_generator.py)  
**状态：** ✅ 模板模式 (LLM 模式 ⚠️ — MIMO 中文退化)

### 架构

```
handle_recommend() 生成 payload 后
    │
    ▼
generate_natural_response(payload, session, message)
    │
    ├─ LLM 可用 + fact_check 未降级
    │    → llm_diverse_response(payload, context)
    │       ├─ model: RECOMMENDATION_RESPONSE_MODEL (默认 mimo-v2.5) ⚠️
    │       ├─ temperature=0.9, max_tokens=200, timeout=5s
    │       ├─ prompt 约束：不编造商品/价格/库存
    │       └─ 失败 → 降级模板
    │
    └─ 否则 → naturalize_response(payload)
         └─ 8种开场 × 5种推荐语 × 4种结尾 = 160种组合
```

### 模板变体示例

| 组件 | 变体数 | 示例 |
|------|--------|------|
| 开场 | 8 | "帮你筛了一遍商品库，" / "按你的需求筛了一下，" / "我在商品库里找到了这些，" |
| 推荐 | 5 | "首推 XX，大概 YY 块" / "XX 挺适合你的，YY 左右" / "优先看看 XX，YY CNY 性价比很高" |
| 结尾 | 4 | "下面保留了候选卡片～" / "更多选择在下方卡片里" / "候选商品卡片就在下面" |

### 效果对比

| 场景 | v2 输出 | v3 输出 |
|------|---------|---------|
| 面霜推荐 | "优先推荐当前最匹配的上架商品：薇诺娜..." | "我在商品库里找到了这些，薇诺娜...268 CNY 性价比很高。候选商品卡片就在下面" |
| 手机推荐 | 固定模板 | "我从上架商品里挑了几款，OPPO...挺适合你的，3299 左右～" |

> ⚠️ LLM 生成模式因 MIMO 中文退化暂不可用，当前运行在模板变体模式。模板已充分多样化（160 种组合）。

---

## 九、事实校验层 🟢 ✅

**文件：** [rag/recommendation/recommendation_pipeline.py](rag/recommendation/recommendation_pipeline.py)  
**状态：** ✅

### fact_check_result(payload, catalog)

```
推荐结果 payload
    │
    ├─ 1. product_id 存在性验证 → 不在 catalog → 剔除
    ├─ 2. 价格偏差校验 → 偏差 > 30% → 自动修正为真实 base_price
    ├─ 3. 库存标记 → stock_status="sold_out" → 标记不剔除
    └─ 4. 降级判断 → 失败率 > 50% → degraded=True
```

### 在对比中的事实校验（handle_compare_v2 🟢）

```
product_ids → catalog.get() 校验 → 同品类检测 → 价格区间检测
```

---

## 十、话题切换机制 🔵 ✅

**文件：** [rag/recommendation/session_state.py](rag/recommendation/session_state.py)  
**状态：** ✅

### 三层检测架构

```
用户消息
    │
    ├─ 第一层：显式切换信号 🔵
    │   "换个话题"、"不要了"、"算了"、"看看别的"
    │   → should_start_new_product_topic() = True
    │
    ├─ 第二层：品类感知切换 🔵
    │   当前 topic_type=="ecommerce_recommendation"
    │   且 新品类 ≠ 旧品类
    │   → 触发切换
    │
    └─ 第三层：PC 场景豁免 + followup 检测 🔵
        ├─ is_pc_build_followup() 分支 D → 阻止切换
        └─ looks_like_followup() 收紧 → 减少误判
```

### v2 → v3 改进

| 机制 | v2 | v3 |
|------|-----|-----|
| 切换检测范围 | 仅 PC 场景 | PC + 电商场景 |
| 显式信号 | 无 | "换个话题""不要了""算了" |
| followup 判定 | ≤12字符=followup | 仅明确追问模式 + ≤6字符 |
| PC followup | 3个分支（关键词） | 4个分支（+会话历史感知） |

---

## 十一、可观测性 🟢 ✅

**文件：** [rag/api/routes/chat.py](rag/api/routes/chat.py)  
**状态：** ✅

### _end_span() 结构化日志

```
每次请求结束记录到 session.llm_call_log:
{
    span_id, tool_name, success,
    fact_check_passed, elapsed_ms, timestamp
}
```

- 保存最近 **20 条**（滑动窗口）
- 可通过 `GET /api/health` 或 `session_to_json()` 读取

---

## 十二、Session 字段速查表

| 字段 | 类型 | 默认值 | 写入方 | 读取方 | 版本 |
|------|------|--------|--------|--------|------|
| `session_id` | `str` | 必填 | 构造时 | 全局 | v1 |
| `topic_memory` | `Dict` | 默认 | `update_topic_memory()` | router, 话题切换, PC followup | v1 |
| `topic_memory.topic_type` | `str` | "unknown" | `_topic_type_for_tool()` | router user prompt 🔵, local_route, should_start_new_product_topic | v1 |
| `current` | `Dict` | `{}` | `update_session_from_router()` | LLM router, PC build | v1 |
| `pc_build_history` | `List[Dict]` | `[]` | `remember_pc_build_plan()` | `is_pc_build_followup()` 🔵 | v1 |
| `pending_cart_action` | `Dict` | `{}` | `handle_cart_v2()` | `cart_confirm()` | 🟢 v2 |
| `last_fact_check_status` | `str` | `"passed"` | `handle_recommend()` | `chat_stream()` | 🟢 v2 |
| `llm_call_log` | `List[Dict]` | `[]` | `_end_span()` | 调试/监控 | 🟢 v2 |

---

## 十三、文件索引

| 层级 | 文件 | 说明 | 状态 |
|------|------|------|------|
| 入口 | [rag/api/routes/chat.py](rag/api/routes/chat.py) | 主聊天流 + 购物车确认 + sanitize + span | ✅ |
| 兼容 | [rag/api/routes/legacy_chat_compat.py](rag/api/routes/legacy_chat_compat.py) | `/api/chat` 旧版非流式 | ✅ |
| 上下文 | [rag/api/app_context.py](rag/api/app_context.py) | 推荐上下文预处理 | ✅ |
| 路由 | [rag/recommendation/tool_router.py](rag/recommendation/tool_router.py) | LLM+本地路由 + 🟢校验 + 🔵模式感知/PC修复 | ✅ ⚠️ |
| 处理器 | [rag/recommendation/tool_handlers.py](rag/recommendation/tool_handlers.py) | 5 工具处理器（🟢v2 + 🔵响应生成集成） | ✅ |
| 响应生成 | [rag/recommendation/response_generator.py](rag/recommendation/response_generator.py) | 🔵 LLM多样+模板变体（160种组合） | ✅ ⚠️ |
| 管道 | [rag/recommendation/recommendation_pipeline.py](rag/recommendation/recommendation_pipeline.py) | 推荐主管道 + 🟢v2需求+事实校验 | ✅ |
| 过滤 | [rag/recommendation/structured_filter.py](rag/recommendation/structured_filter.py) | 10步过滤链 + LLM过滤层 | ✅ |
| 评分 | [rag/recommendation/scorer.py](rag/recommendation/scorer.py) | 7维度可解释评分 | ✅ |
| 构建 | [rag/recommendation/package_builder.py](rag/recommendation/package_builder.py) | 推荐结果+套餐+卡片构建 | ✅ |
| 对比 | [rag/recommendation/comparison.py](rag/recommendation/comparison.py) | 产品对比+赢家选择 | ✅ ⚠️ |
| 价格 | [rag/recommendation/cost_estimator.py](rag/recommendation/cost_estimator.py) | 套餐总价估算 | ✅ |
| PC | [rag/recommendation/pc_build.py](rag/recommendation/pc_build.py) | PC配置穷举+兼容性检查 | ✅ |
| PC流 | [rag/recommendation/pc_session_flow.py](rag/recommendation/pc_session_flow.py) | PC构建对话流 | ✅ |
| Session | [rag/recommendation/session_state.py](rag/recommendation/session_state.py) | 会话存储+话题切换🔵 | ✅ |
| LLM | [rag/recommendation/llm_client.py](rag/recommendation/llm_client.py) | LLM客户端（mimo/dashscope） | ✅ ⚠️ |
| 模型 | [rag/schemas/recommendation.py](rag/schemas/recommendation.py) | Pydantic数据模型 | ✅ |
| 存储 | [rag/storage/](rag/storage/) | 向量存储（Milvus+缓存） | ✅ |
| 摄入 | [rag/ingestion/](rag/ingestion/) | 商品索引构建（仅离线） | ✅ |
| 工具 | [rag/utils/](rag/utils/) | 共享工具 | ✅ |

---

*文档完。如有链路改动，请同步更新此文档。*
