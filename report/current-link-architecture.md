# 后端链路架构说明

**最后更新：** 2026-06-12  
**版本：** v4.2（🟢 v2改造 + 🔵 v3新增 + 🟣 v4架构治理 + 🔒 硬编码审计）  
**关联文档：** [v2 链路改造方案](link-transformation-plan.md) · [死代码审计报告](dead-code-audit-rag.md) · [v3 问题报告](bound_test_v3_issues.md) · [Phase 4 回归测试报告](bound_test_phase4.md) · [改造建议文档](improvement-proposals.md) · [硬编码修正方案](hardcoded-fix-plan.md) · [LLM 调用链路报告](llm-call-chain.md) · [设计目标评估](link-design-target-evaluation.md)

> ⚠️ 本文档为链路架构的**权威说明**。后续如有链路改动，必须同步更新此文档。

---

## 目录

1. [总览：请求生命周期 + 节点状态](#一总览请求生命周期)
2. [入口层：chat_stream()](#二入口层chat_stream)
3. [路由层：route_shopping_tool_call()](#三路由层route_shopping_tool_call)
4. [校验层：validate_tool_call()](#四校验层validate_tool_call)
5. [Session 状态管理](#五session-状态管理)
6. [处理层：8 个工具处理器](#六处理层8-个工具处理器)
7. [推荐管道：recommend_shopping_products()](#七推荐管道recommend_shopping_products)
8. [响应生成层](#八响应生成层)
9. [事实校验层](#九事实校验层)
10. [话题切换机制](#十话题切换机制)
11. [可观测性](#十一可观测性)
12. [LLM Gateway 统一调用层](#十二llm-gateway-统一调用层)
13. [Session 字段速查表](#十三session-字段速查表)
14. [文件索引](#十四文件索引)

---

## 一、总览：请求生命周期 + 节点状态

### 1.1 完整请求生命周期（逐步展开）

```
客户端 POST /api/chat/stream
  body: { session_id, message, attachments?, images? }
         │
         ▼
 ════════════════════════════════════════════════════════════════
  A 入口层：chat_stream()                    chat.py
 ════════════════════════════════════════════════════════════════
         │
  ┌──────┴──────┐
  │ ① sanitize  │  🟢 输入消毒
  └──────┬──────┘
         │  🔒 硬编码: MAX_MESSAGE_LENGTH = 2000 字符（超长截断）
         │  🔒 硬编码: HTTP 400（session_id 缺失 / message 为空）
         ▼
  ┌──────────────┐
  │ ② get_session│  读取/创建 session
  └──────┬───────┘
         │  🔒 硬编码: session TTL = 7200s（2小时）
         │  🔒 硬编码: max_in_memory_sessions = 500
         │  🔒 硬编码: cleanup_interval = 60s
         │  🔒 硬编码: schema_version = 2
         ▼
 ════════════════════════════════════════════════════════════════
  B 路由层：route_shopping_tool_call()       tool_router.py
 ════════════════════════════════════════════════════════════════
         │
  ┌──────┴───────────────────┐
  │ ③ LLM 路由（主通道）     │  try_llm_route_tool_call()
  │    LLMGateway.call(      │
  │      "router", msgs)     │
  └──────┬───────────────────┘
         │  🔒 硬编码: model = MALLMIND_ROUTER_MODEL → mimo-v2.5 ⚠️
         │  🔒 硬编码: temperature = 0（确定性）
         │  🔒 硬编码: max_tokens = 320（env 可调）
         │  🔒 硬编码: timeout = 15s
         │  🔒 硬编码: max_concurrency = 5（Gateway 统一）
         │  🔒 硬编码: router prompt 截断 message[:500]
         │  🔒 硬编码: 购物车快照最多 5 项
         │  🔒 硬编码: recent_queries 注入最近 3 条
         │  🔒 硬编码: 熔断窗口 60s / 阈值 5 次 / 断路 30s
         │
         │  失败 → 降级
         ▼
  ┌──────────────────────────┐
  │ ③' 本地规则路由（降级）   │  local_route_tool_call()
  │    14 步决策树            │  + score_local_routes()
  └──────┬───────────────────┘
         │  🔒 硬编码: 11 个评分权重（0.75/0.55/0.25/…）全部 inline
         │  🔒 硬编码: ~350 个关键词（25 个命名常量列表）
         │  🔒 硬编码: 短消息阈值 32 字符（PC followup / 商品追问）
         │  🔒 硬编码: 偏好追问阈值 24 字符
         │  🔒 硬编码: 预算乘数表（亿→1e8, 万→1e4, …）7 个
         │  🔒 硬编码: PC 配件命中阈值 ≥1（弱）/ ≥3（纯）
         │  🔒 硬编码: 金额提取正则（内联，未提取为常量）
         ▼
 ════════════════════════════════════════════════════════════════
  C 校验层：validate_tool_call()             tool_router.py
 ════════════════════════════════════════════════════════════════
         │
         │  🔒 命名常量: _MAX_PRICE = 500000
         │  🔒 命名常量: _MIN_SANE_PRICE = 50
         │  🔒 命名常量: _MAX_BRANDS = 50
         │  争议路由 → 使用本地规则结果（更保守）
         │  校验失败 → 降级到 general_chat
         ▼
 ════════════════════════════════════════════════════════════════
  D 状态累积：update_session_from_router()   session_state.py
 ════════════════════════════════════════════════════════════════
         │
         │  🔵 topic_memory.topic_type 注入路由 prompt
         │  🔵 话题切换三层检测（显式信号 → 品类感知 → PC 豁免）
         │  🔒 硬编码: messages 保留最近 12 条
         │  🔒 硬编码: topic_history 上限 20 条（与 topic_memory
         │     的 8 条、session_to_json 的 5 条 — 三处不一致 ⚠️）
         │  🔒 硬编码: recent_queries 上限 5 条
         │  🔒 硬编码: pc_build_history 上限 6 条
         ▼
 ════════════════════════════════════════════════════════════════
  E 工具分发：_LIGHTWEIGHT_TOOLS 注册表      chat.py
 ════════════════════════════════════════════════════════════════
         │
         ├─ 🟢 轻量级（快速路径，无视觉 LLM / 附件处理）
         │   │  🔒 硬编码: 6 个工具名字符串（inline set，未用 Enum）
         │   │  🔒 硬编码: use_vision_llm = True（重量级路径，无法配置关闭）
         │   │
         │   ├── apply_cart_instruction → handle_cart_v2()
         │   │     │  🔒 命名常量: _CART_CONFIRM_TTL_SECONDS = 60s
         │   │     │  🔒 硬编码: 4 种操作 (add/remove/set_quantity/clear)
         │   │     │  🔒 硬编码: quantity 下限 max(..., 1)，出现 6 次
         │   │     │  🔒 硬编码: extract_item_index 仅支持 1–3（6 个正则）
         │   │     │  🔒 硬编码: extract_quantity 正则（内联）
         │   │     │  🔒 硬编码: 模糊匹配命中阈值 hits ≥ 1
         │   │     │  🔒 硬编码: 中文 UI 模板 ~30 处
         │   │     ▼
         │   │
         │   ├── general_chat → handle_general_chat()
         │   │     │  🔒 硬编码: temperature = 0.7, max_tokens = 200
         │   │     │  🔒 硬编码: 最短回复阈值 5 字符
         │   │     │  🔒 硬编码: 离题/感谢/告别/默认 4 组关键词
         │   │     ▼
         │   │
         │   ├── compare_products → handle_compare_v2()  ⚠️
         │   │     │  🔒 硬编码: 价格比阈值 0.2（大价差判定）
         │   │     │  🔒 硬编码: fact_issues 容限 ≤ 1
         │   │     │  🔒 硬编码: 对比候选 limit = 3 / 2
         │   │     │  🔒 硬编码: PC 方案对比最少 2 个 plan
         │   │     ▼
         │   │
         │   ├── parameter_query → handle_parameter_query()
         │   ├── sku_detail      → handle_sku_query()
         │   └── price_comparison → handle_price_comparison()
         │
         └─ 🟢 重量级（需要 prepare_recommendation_context）
             │
             ├── generate_pc_build_plan → handle_pc_build()
             │     │  🔒 硬编码: PC 组件角色前缀元组 ×2（重复定义）
             │     │  🔒 硬编码: LLM teaching guidance 最多 2 条
             │     │  🔒 硬编码: 每品类 top 候选展示上限 4
             │     ▼
             │
             └── recommend_shopping_products → handle_recommend()
                   │  🔒 硬编码: 检索超时 8s（env 可调）
                   │  🔒 硬编码: 描述截断 220 字符
                   │  🔒 硬编码: 评分原因最多 8 条
                   │  🔒 硬编码: 备选卡片数 4/0/2（对比/组合/默认）
                   │  🔒 硬编码: 备选评分阈值 0.55 + delta 0.12
                   │  🔒 硬编码: MMR 同品牌连续上限 2
                   ▼
 ════════════════════════════════════════════════════════════════
  F 推荐管道（仅推荐路径）                    recommendation_pipeline.py
 ════════════════════════════════════════════════════════════════
         │
         │  ┌─ validate_business_goal()
         │  │    🔒 硬编码: 最短输入 2 字符 / 符号比上限 0.35
         │  │    🔒 硬编码: SHOPPING_GOAL_KEYWORDS ~82 个
         │  │
         │  ├─ _requirement_from_args_v2() 🟢
         │  │    session.current 继承（brands/price/category）
         │  │    🔒 硬编码: 模糊预算乘数 ×1.1
         │  │    🔒 硬编码: 预算分级阈值 300 CNY
         │  │
         │  ├─ filter_products_for_requirement() — 12步过滤链
         │  │    🔒 硬编码: LLM 过滤候选上限 30（出现 2 次）
         │  │    🔒 硬编码: LLM 过滤 temperature=0, max_tokens=500
         │  │    （含品类预筛→库存→品牌排除→白名单→子品类→产品类型
         │  │     →必要属性→PC约束→偏好匹配→预算→LLM语义过滤→最终输出）
         │  │
         │  ├─ score_products() — 7维度评分
         │  │    🔒 命名常量: BASE_WEIGHTS（7维权重向量）
         │  │    🔒 硬编码: 20 个动态权重调整值（+0.12/−0.06/…）
         │  │    🔒 硬编码: ~50 个评分函数内联常量
         │  │    🔒 硬编码: 证据增强参数（0.07/0.05/3/0.12/0.16）
         │  │
         │  ├─ fact_check_result() 🟢
         │  │    🔒 命名常量: _PRICE_DEVIATION_THRESHOLD = 0.30
         │  │    🔒 命名常量: _FACT_FAILURE_THRESHOLD = 0.50
         │  │
         │  └─ generate_natural_response() 🔵
         │       🔒 硬编码: 模板变体 6 组列表（happy path 6×5×4=120，
         │         含无价格/超预算/品牌未命中/无匹配等分支，总组合 >120）
         │       🔒 硬编码: LLM temperature = 0.9, max_tokens = 200
         │       🔒 硬编码: LLM 超时 5s
         │       🔒 硬编码: 输出截断 300 字符
         ▼
 ════════════════════════════════════════════════════════════════
  G SSE 事件流返回                             chat.py + sse.py
 ════════════════════════════════════════════════════════════════
         │
         │  SSE 事件类型（全部 inline 字符串，无 Enum/常量，共 25 种）：
         │    主链路: runtime_mode → tool_call → progress → delta →
         │      attachment_analysis → intent_route → product_cards →
         │      comparison_table → cart_confirmation → cart_clarification →
         │      fact_check → pc_build_plan → pc_comparison_table →
         │      candidate_scope → follow_up_questions → result → cart → done
         │    推荐图谱: step → requirement → catalog → plans → guidance
         │    异常/校验: error → validation_error
         │
         │  🔒 硬编码: span_id = f"{session_id}-{int(t*1000)%100000}"
         │  🔒 硬编码: llm_call_log 滑动窗口 20 条
         │  🔒 硬编码: media_type = "text/event-stream"
         │  🔒 硬编码: 中文进度文案 4 处（"已收到需求"/"开始整理…"等）
         ▼
    客户端收到 SSE 事件流
```

### 1.2 硬编码热力图

按文件统计 inline 硬编码数量（已提取为命名常量的不计入）：

```
文件                              inline 数量   严重程度
─────────────────────────────────────────────────────────
tool_router.py                    ~120         🔴 高（评分权重+关键词+正则）
tool_handlers.py                  ~90          🔴 高（SSE事件类型+UI模板+操作字符串）
scorer.py                         ~70          🔴 高（50+评分常量+20动态权重）
chat.py                           ~80          🟡 中（工具名重复+端点路径+事件类型）
session_state.py                  ~60          🟡 中（历史窗口+正则+关键词）
recommendation_pipeline.py        ~30          🟡 中（LLM参数+截断+关键词列表）
llm_gateway.py                    ~25          🟡 中（9场景×6参数=54个值）
package_builder.py                ~15          🟢 低（截断+卡片限制）
response_generator.py             ~10          🟢 低（LLM参数+截断）
structured_filter.py              ~5           🟢 低
─────────────────────────────────────────────────────────
总计                              ~500+        🔴
```

**最需要优先提取为命名常量的 Top 10：**

| 优先级 | 位置 | 当前形态 | 建议 |
|--------|------|----------|------|
| 1 | `tool_handlers.py` SSE 事件类型 | 25 种事件类型、~50 处 inline 字符串 `"delta"/"done"/"step"/…` | 提取为 `SSEEventType` Enum |
| 2 | `chat.py` 工具名 | 6 个字符串在注册表 + 分发 + 跳过更新中重复 3 次 | 提取为 `ToolName` Enum |
| 3 | `scorer.py` 评分函数常量 | ~50 个 inline float（0.75/0.45/0.35/…） | 提取为 `ScoreConstants` 命名空间 |
| 4 | `tool_router.py` 路由评分权重 | 11 个 inline float（0.75/0.55/0.80/…） | 提取为 `ROUTE_SCORES` dict |
| 5 | `session_state.py` 历史窗口上限 | 12/5/8/20/6 五个数字分散在不同函数 | 提取为 `SessionLimits` dataclass |
| 6 | `llm_gateway.py` 9 场景配置 | 54 个 inline 值（每场景 6 参数） | 提取为 `_DEFAULT_CALLER_CONFIGS` dict |
| 7 | `tool_handlers.py` 购物车操作 | `("add","remove","set_quantity","clear")` 重复 3 次 | 提取为 `CartOperation` Enum |
| 8 | `tool_router.py` inline 关键词列表 | ~18 处函数内列表未提取为模块常量 | 统一提取到模块级 |
| 9 | `session_state.py` extract_item_index | 仅硬编码 1–3，不支持 4+ | 改为通用数字解析或扩展覆盖 |
| 10 | `chat.py` 中文 UI 文案 | 4 处进度文案 + 合成购物车指令模板 | 提取为 i18n 文案模块 |

### 1.3 节点状态图例

| 符号 | 含义 |
|------|------|
| ✅ | 正常 — 21 case / 136 轮全量回归通过 |
| ⚠️ | 降级运行 — 功能正常但有已知局限（MIMO 中文、空对比降级） |
| ❌ | 不可用 — 需要修复 |
| 🔒 | 硬编码 — 该值直接写在源码中，未提取为命名常量或配置项 |
| 🟢 | v2 改造新增（Phase 1-6） |
| 🔵 | v3 改造新增（Part A-D） |
| 🟣 | v4 架构治理新增（Phase 4：LLM Gateway / Session 分层 / trace_span / 注册表分发） |

### 1.4 关键设计原则

- 路由：LLM + 本地规则双通道，LLM-first 成功即采纳，失败降级本地规则；validate_tool_call 三条硬规则覆写（闲聊信号/购物信号/购物车关键词）
- 事实校验：基于真实产品库（RAG），不从 LLM 生成价格/库存
- 购物车：计划+确认模式（v2），防止误操作（add/remove/set_quantity 三步确认，clear 直接执行）；🟣 session 上下文解析序数指代
- 回复：LLM 多样化生成 + 模板变体兜底（v3）
- 话题切换：显式信号 + 品类感知 + topic_memory 注入（v3）
- 🟣 LLM 调用：LLMGateway 注册表已创建（9 种场景），⚠️ 调用点尚未迁移，仍使用直接实例化
- 🟣 可观测性：trace_span 树形上下文管理器，自动记录 duration_ms + 异常
- 🟣 Session 分层：5 个子状态 dataclass（Conversation / Recommendation / Cart / PCBuild / Observability）
- 🟣 轻量分发：`_LIGHTWEIGHT_TOOLS` 注册表模式，跳过重量级上下文准备
- 所有校验层均为确定性规则（< 1ms），不增加首 token 延迟

### 1.5 已提取的命名常量清单（参考）

| 常量名 | 值 | 文件 | 用途 |
|--------|-----|------|------|
| `MAX_MESSAGE_LENGTH` | 2000 | chat.py:44 | 消息长度上限 |
| `_MAX_LOG_ENTRIES` | 20 | chat.py:247 | LLM 调用日志窗口 |
| `_CONFIRM_TTL_SECONDS` | 60 | chat.py:283 | 购物车确认 TTL（⚠️ 定义但未引用） |
| `_MAX_PRICE` | 500000 | tool_router.py:732 | 价格上限 |
| `_MIN_SANE_PRICE` | 50 | tool_router.py:733 | 最低合理价格 |
| `_MAX_BRANDS` | 50 | tool_router.py:734 | 品牌列表截断 |
| `_CART_CONFIRM_TTL_SECONDS` | 60 | tool_handlers.py:54 | 购物车确认过期 |
| `DEFAULT_SESSION_TTL_SECONDS` | 7200 | session_state.py:19 | Session 过期 |
| `DEFAULT_MAX_IN_MEMORY_SESSIONS` | 500 | session_state.py:20 | 内存 session 上限 |
| `SESSION_CLEANUP_INTERVAL_SECONDS` | 60 | session_state.py:21 | 清理间隔 |
| `SCHEMA_VERSION` | 2 | session_state.py:23 | 版本号 |
| `BASE_WEIGHTS` | 7维权重 | scorer.py:13-21 | 评分基线权重 |
| `_PRICE_DEVIATION_THRESHOLD` | 0.30 | recommendation_pipeline.py:1240 | 价格偏差阈值 |
| `_FACT_FAILURE_THRESHOLD` | 0.50 | recommendation_pipeline.py:1241 | 事实校验失败率 |
| `_FAILURE_THRESHOLD` | 5 | llm_gateway.py:80 | 熔断失败次数 |
| `_OPEN_DURATION_SECONDS` | 30.0 | llm_gateway.py:81 | 熔断断路时长 |
| `_MAX_LOG` | 100 | llm_gateway.py:131 | Gateway 日志容量 |

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

见[第三章](#三路由层route_shopping_tool_call)。🟣 LLM 调用统一经 `LLMGateway.call("router")`，使用 MIMO `mimo-v2.5`，中文长上下文偶有退化（⚠️）。本地规则兜底保证可用。

#### ④ validate_tool_call() 🟢 ✅

见[第四章](#四校验层validate_tool_call)。值域裁剪 + 白名单 + LLM vs 本地争议检测。

#### ⑤ update_session_from_router() ✅ 🔵

见[第五章](#五session-状态管理)。v3 增强：
- `topic_memory.topic_type` 注入 LLM router user prompt
- 话题切换检测扩展到电商场景（`should_start_new_product_topic`）
- 显式话题切换信号（"换个话题""不要了""算了"）

#### ⑥ 工具分发 ✅ 🔵🟣

🟣 v4 引入 `_LIGHTWEIGHT_TOOLS` 注册表模式，将不需要重量级上下文准备（`prepare_recommendation_context()`）的工具统一走 `_dispatch_lightweight()` 快速路径：

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

**分发逻辑：**
```
if tool_name in _LIGHTWEIGHT_TOOLS:
    → _dispatch_lightweight()    # 快速路径，无视觉 LLM、无附件处理
    → trace_span("handle_{tool_name}") 包裹
else:
    → prepare_recommendation_context()  # 重量级：视觉 LLM + 附件处理
    → handle_pc_build() 或 handle_recommend()
```

**完整工具注册表（8 个工具）：**

| 工具名 | 处理器 | 路径 | 适用场景 |
|--------|--------|------|----------|
| `recommend_shopping_products` | `handle_recommend()` | 重量级 | 单品推荐 / 组合推荐（need_bundle=true） |
| `generate_pc_build_plan` | `handle_pc_build()` | 重量级 | PC 整机方案 + 方案修改 ⚠️ |
| `compare_products` | `handle_compare_v2()` 🟢 | 轻量级 | 商品对比 + 事实校验 |
| `apply_cart_instruction` | `handle_cart_v2()` 🟢 | 轻量级 | 购物车操作（计划+确认） |
| `general_chat` | `handle_general_chat()` | 轻量级 | 闲聊/非购物问题 |
| `parameter_query` | `handle_parameter_query()` | 轻量级 | 商品参数查询 |
| `sku_detail` | `handle_sku_query()` | 轻量级 | SKU 变体/配置差异查询 |
| `price_comparison` | `handle_price_comparison()` | 轻量级 | 价格对比/值不值 |

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

### 3.1 LLM 路由：try_llm_route_tool_call() ⚠️ 🟣

```
用户消息
  │
  ├─ _build_router_system_prompt()
  │     ├─ 🔵 推荐模式选择规则（单品/组合/PC三种）
  │     ├─ 🔵 话题切换判断规则
  │     ├─ 工具定义（8 个工具的 JSON Schema）🟣
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
  └─ 🟣 LLMGateway.call("router", messages)
        ├─ model: MALLMIND_ROUTER_MODEL（默认 mimo-v2.5）⚠️
        ├─ temperature=0（确定性输出）
        ├─ thinking disabled
        ├─ max_tokens: 800（v3 从 320 提升）
        ├─ timeout=15s（🟣 Gateway 统一管理）
        ├─ 🟣 并发控制：Gateway semaphore（max_concurrency=5）
        ├─ 🟣 熔断器：Gateway CircuitState（5次失败 → 30s断路）
        └─ 返回 JSON → RoutedToolCall.model_validate()
```

**🟣 容灾机制（LLMGateway 统一）：**
- 熔断器：`_CircuitState`，连续失败 ≥5 次 → 断路 30s（`_FAILURE_THRESHOLD=5`，`_OPEN_DURATION_SECONDS=30`）
- 并发控制：`_ConcurrencyLimiter`（信号量，每个 caller 独立配置）
- LLM 未配置 → 降级本地规则
- 超时：`run_with_hard_timeout()` 硬超时兜底

> ⚠️ MIMO `mimo-v2.5` 在复杂路由 prompt 下中文输出偶有退化。已通过 max_tokens=800 缓解。长期建议评估中文能力更强的路由模型。

### 3.2 本地规则路由：local_route_tool_call() ✅

**决策树（顺序匹配，先到先得；🟣 扩展至 14 步）：**

```
 1. 购物车意图 + 非推荐意图 → apply_cart_instruction
 2. 🔵 PC 构建后续（is_pc_build_followup 含分支D） → generate_pc_build_plan
 3. 对比请求 → compare_products
 4. 🟣 SKU 配置详情（_has_sku_detail_intent） → sku_detail
 5. 🟣 价格对比（_has_price_comparison_intent） → price_comparison
 6. 🟣 参数查询（_has_parameter_query_intent） → parameter_query
 7. PC 意图 → generate_pc_build_plan
 8. PC 话题后续（topic_type=="pc_build"） → generate_pc_build_plan
 9. 商品详情追问 → recommend_shopping_products
10. 常规商品品类 → recommend_shopping_products
11. 单个 PC 配件 → recommend_shopping_products
12. 商品查询意图 → recommend_shopping_products
13. 通用对话 → general_chat
14. 兜底 → recommend_shopping_products
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
        └─ 3. LLM vs 本地争议检测（⚠️ 无置信度分数，纯规则覆写）
              ├─ LLM 推荐 + message 含闲聊信号 → 用本地结果
              ├─ LLM 闲聊 + message 含购物信号 → 用本地结果
              ├─ message 含购物车关键词 → 强制 apply_cart_instruction
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
**状态：** ✅ 🟣

### 5.1 ShoppingSession 结构

```
ShoppingSession (schema_version=2 🟣)
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
├─ llm_call_log: List[Dict]           # 🟢 调用日志（最多 20 条）
└─ schema_version: int = 2            # 🟣 前向兼容版本号
```

### 5.1.1 🟣 子状态视图 dataclass（Phase 4 新增）

v4 引入 5 个结构化视图 dataclass，提供作用域读取接口，不替代 session 原始字段：

```python
# 对话状态
@dataclass
class ConversationState:
    session_id: str
    messages: List[str]
    recent_queries: List[Dict]
    chat_topic: str

# 推荐状态
@dataclass
class RecommendationState:
    current: Dict[str, Any]
    last_goal: str
    last_result: Any
    last_requirement: Dict[str, Any]

# 购物车状态
@dataclass
class CartState:
    cart: Dict[str, CartItem]
    pending_cart_action: Dict[str, Any]

# PC 构建状态
@dataclass
class PCBuildState:
    pc_build_history: List[Dict]
    current_pc_build: Dict[str, Any]

# 可观测性状态
@dataclass
class ObservabilityState:
    topic_memory: Dict[str, Any]
    topic_history: List[Dict]
    llm_call_log: List[Dict]
    last_fact_check_status: str
```

**访问方式：** 通过 `ShoppingSession` 的视图方法：
```python
session.conversation_state()   → ConversationState
session.recommendation_state() → RecommendationState
session.cart_state()           → CartState
session.pc_build_state()       → PCBuildState
session.observability_state()  → ObservabilityState
```

**schema_version 机制：**
- 当前值：`SCHEMA_VERSION = 2`
- 序列化时写入 `schema_version` 字段
- 反序列化时检查版本（`session_from_dict()`），缺失则默认为当前版本
- 用于前向/后向兼容判断

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

## 六、处理层：8 个工具处理器

**文件：** [rag/recommendation/tool_handlers.py](rag/recommendation/tool_handlers.py)  
**状态：** ✅ (全量通过)  
**🟣 共用基础：** [rag/recommendation/handler_base.py](rag/recommendation/handler_base.py) — `trace_span()`、`load_catalog_safe()`、`resolve_product_ids_from_session()`、`safe_catalog_get()`

### 6.1 handle_cart_v2() 🟢🟣 ✅

```
购物车 v2：计划 + 确认模式

tool_call.arguments
    │
    ├─ 1. 提取 product_id, quantity, operation
    │     └─ 🟣 quantity 空值安全：int(None) → 默认 1
    ├─ 2. 🟣 三级产品 ID 解析：
    │     ├─ Tier 1：显式 product_ids（请求参数或 tool_call.arguments）
    │     ├─ Tier 2：session 上下文解析
    │     │   ├─ last_recommended_product_ids(session)
    │     │   ├─ extract_item_index(message) → "第一部""第二款"等
    │     │   └─ references_previous_item(message) → "刚才那款""这个"等
    │     └─ Tier 3：默认取 last_recommended[0]
    ├─ 3. 🟢 catalog 真实性校验 → product_id 不存在则 error
    ├─ 4. 生成 CartActionPlan（含真实价格、60s 过期）
    ├─ 5. 存入 session.pending_cart_action
    └─ 6. 返回 cart_confirmation SSE 事件
```

**确认端点：** `POST /api/cart/confirm`（TTL = 60s）

**已知局限：**
- 每次只处理单个产品（取 `product_ids[0]`）
- 删除/修改操作无法按商品名称精确定位（仅靠序数或 ID），详见[改造建议](improvement-proposals.md)

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
     │   ├─ filter_products_for_requirement() → 12步过滤链
     │   ├─ score_products() → 7维度评分
     │   └─ build_recommendation_plan()
     └─ 🟢 fact_check_result() — 事实校验
3. model_to_dict(result) → payload
4. 🟢 fact_check_result(payload, catalog)
5. remember_recommendation(session, goal, payload)
6. 🔵 generate_natural_response(payload, session, message)
     ├─ 🟣 LLM 可用 → LLMGateway.call("response") (t=0.9)
     └─ 否则 → naturalize_response (模板变体)
7. yield SSE 事件流（delta + cards + comparison + result）
```

### 6.6 handle_parameter_query() 🟣 ✅

```
商品参数查询：
1. _resolve_product() → 从消息或 session 历史定位产品
2. 在商品描述中做关键词匹配查找参数
3. 返回 delta SSE 事件
```

### 6.7 handle_sku_query() 🟣 ✅

```
SKU 变体/配置差异查询：
1. _resolve_product() → 定位产品
2. 展示该产品的 SKU 级别配置和价格差异
3. 返回 delta SSE 事件
```

### 6.8 handle_price_comparison() 🟣 ✅

```
价格对比/值不值查询：
1. _resolve_product() → 定位产品
2. 展示 min/max 价格区间和逐 SKU 定价
3. 返回 delta SSE 事件
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
    │        ├─ filter_products_for_requirement() 12步过滤
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
    ├─ 0 卡片短路：_NO_MATCH_VARIANTS（3 种）或 _BUDGET_OVER_VARIANTS（2 种）
    │
    ├─ LLM 可用 + fact_check 未降级
    │    → _llm_diverse_response(payload, context)
    │       ├─ 🟣 model: 经 LLMGateway.call("response")
    │       │   ├─ model_kind="main", temperature=0.9
    │       │   ├─ max_tokens=200, timeout=5s
    │       │   └─ max_concurrency=5
    │       ├─ prompt 约束：不编造商品/价格/库存
    │       ├─ 输出截断至 300 字符
    │       └─ 失败 → 降级模板
    │
    └─ 否则 → naturalize_response(payload)
         └─ 6种开场 × 5种推荐语（含 3种无价格变体） × 4种结尾
            + 超预算(2种) / 品牌未命中(2种) / 无匹配(3种) 条件分支
            = happy path 120 种，含分支 >120 种组合
```

### 模板变体清单

| 模板数组 | 变体数 | 用途 |
|----------|--------|------|
| `_OPENING_VARIANTS` | 6 | 开场白（"帮你筛了一遍商品库"等） |
| `_LEAD_VARIANTS` | 5 | 推荐导语（含价格） |
| `_LEAD_NO_PRICE` | 3 | 推荐导语（无价格时） |
| `_TAIL_VARIANTS` | 4 | 结尾引导（指向卡片） |
| `_NO_MATCH_VARIANTS` | 3 | 无匹配结果 |
| `_BUDGET_OVER_VARIANTS` | 2 | 超预算提示 |
| `_BRAND_MISS_VARIANTS` | 2 | 品牌未找到 |

### 效果对比

| 场景 | v2 输出 | v3 输出 |
|------|---------|---------|
| 面霜推荐 | "优先推荐当前最匹配的上架商品：薇诺娜..." | "我在商品库里找到了这些，薇诺娜...268 CNY 性价比很高。候选商品卡片就在下面" |
| 手机推荐 | 固定模板 | "我从上架商品里挑了几款，OPPO...挺适合你的，3299 左右～" |

> ⚠️ LLM 生成模式因 MIMO 中文退化暂不可用，当前运行在模板变体模式。模板已充分多样化（120 种组合）。
>
> 关于响应多样化的进一步改造建议，详见[改造建议文档](improvement-proposals.md)。

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

## 十一、可观测性 🟢🟣 ✅

**文件：** [rag/api/routes/chat.py](rag/api/routes/chat.py) · [rag/recommendation/handler_base.py](rag/recommendation/handler_base.py)  
**状态：** ✅

### 11.1 trace_span() 树形上下文管理器 🟣

```python
# handler_base.py
@contextmanager
def trace_span(name: str, trace_id: str = "", parent_id: str = "", **extra):
    """
    自动记录 span 的 name, trace_id, parent_id, start_ns, duration_ms。
    异常时附加 error + error_type 到 span dict。
    线程本地存储，上限 200 span/线程。
    """
```

**在 chat.py 中的使用：**
```python
# 路由阶段
with trace_span("route_tool_call", trace_id=trace_id) as span:
    tool_call = route_shopping_tool_call(...)
    span["source"] = tool_call.get("router_final_source")
    span["result"] = tool_call.get("name")

# 轻量级处理器
with trace_span(f"handle_{tool_name}", trace_id=trace_id) as span:
    yield from _dispatch_lightweight(tool_name, session, ...)

# 重量级处理器
with trace_span("handle_recommend", trace_id=trace_id) as span:
    yield from handle_recommend(...)
```

### 11.2 _end_span() 结构化日志

```
每次请求结束记录到 session.llm_call_log:
{
    span_id, tool_name, success,
    fact_check_passed, elapsed_ms, timestamp
}
```

- 保存最近 **20 条**（滑动窗口）
- 可通过 `GET /api/health` 或 `session_to_json()` 读取

### 11.3 LLMGateway 调用日志 🟣

```
LLMGateway._call_log（独立于 trace_span）:
{
    caller, success, elapsed_ms,
    error_code, timestamp
}
```

- 保存最近 **100 条**（线程安全，`_log_lock`）
- 可通过 `LLMGateway.get_call_log()` 读取

---

## 十二、LLM Gateway 统一调用层 🟣 ✅

**文件：** [rag/recommendation/llm_gateway.py](rag/recommendation/llm_gateway.py)  
**状态：** ✅（Gateway 已创建并注册 9 种场景，⚠️ 但 0/9 调用点完成迁移）

> ⚠️ **迁移现状：** Gateway 类已完成设计（熔断器 + 并发控制 + 调用日志），9 种场景配置已注册。但所有调用点仍直接使用 `OpenAICompatibleChatClient` 实例化，尚未切换到 `LLMGateway.call()`。Router 有独立的熔断器和信号量实现（`tool_router.py`），与 Gateway 的实现并存。详见 [LLM 调用链路报告](llm-call-chain.md)。

### 设计思路

取代散落的 `OpenAICompatibleChatClient()` 实例化，所有 LLM 调用统一经 `LLMGateway` 注册表管理，实现：模型选择、超时控制、温度配置、并发限制、熔断保护。

### 核心 API

```python
class LLMGateway:
    @classmethod
    def register(cls, name, *, model_kind="fast", temperature=0.0,
                 timeout=15.0, max_tokens=1600, max_concurrency=5)

    @classmethod
    def call(cls, caller_name, messages, *, text_mode=False, **overrides)
        → (payload_or_text, LLMCallReport)

    @classmethod
    def call_text(cls, caller_name, messages, **overrides)
        → (str, LLMCallReport)

    @classmethod
    def get_call_log(cls) → List[Dict]

    @classmethod
    def reset(cls)  # 测试用
```

### 9 种默认调用场景

| 调用方 | model_kind | temperature | timeout | max_tokens | max_concurrency |
|--------|-----------|-------------|---------|------------|-----------------|
| `router` | fast | 0 | 15s | 320 | 5 |
| `parse` | fast | 0.1 | 12s | 1200 | 5 |
| `guidance` | main | 0.2 | 8s | 1500 | 5 |
| `response` | main | 0.9 | 5s | 200 | 5 |
| `explanation` | main | 0.1 | 8s | 1500 | 5 |
| `rewrite` | fast | 0.1 | 8s | 600 | 5 |
| `general_chat` | main | 0.7 | 8s | 200 | 10 |
| `filter` | fast | 0 | 12s | 500 | 5 |
| `attachment` | main | 0.1 | 15s | 800 | 3 |

### 调用流程

```
LLMGateway.call("router", messages)
    │
    ├─ 1. 查找 _CallerConfig（未注册 → 自动注册默认值）
    ├─ 2. 应用 per-call overrides（temperature, max_tokens, timeout, model）
    ├─ 3. 检查熔断器 → 断路中 → 抛出 LLMClientError(circuit_open)
    ├─ 4. 获取并发信号量 → 超时 → 抛出 LLMClientError
    ├─ 5. 实例化 OpenAICompatibleChatClient
    ├─ 6. _resolve_model(kind, client) → "fast" 或 "main" 映射具体模型
    ├─ 7. run_with_hard_timeout() → client.chat_json / chat_text
    ├─ 8. 记录 success/failure → 更新熔断器 + _call_log
    └─ 9. 释放信号量（finally）
```

### 模型解析规则

| model_kind | 优先级 |
|------------|--------|
| `fast` | `MALLMIND_ROUTER_MODEL` 环境变量 > `client.config.fast_model` |
| `main` | `MALLMIND_GUIDANCE_MODEL` 环境变量 > `client.config.model` |

---

## 十三、Session 字段速查表

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
| `schema_version` | `int` | `2` | 构造时 | `session_from_dict()` 反序列化 | 🟣 v4 |

---

## 十四、文件索引

| 层级 | 文件 | 说明 | 状态 |
|------|------|------|------|
| 入口 | [rag/api/routes/chat.py](rag/api/routes/chat.py) | 主聊天流 + 🟣 _LIGHTWEIGHT_TOOLS 注册表分发 + 购物车确认 + sanitize + span | ✅ |
| 兼容 | [rag/api/routes/legacy_chat_compat.py](rag/api/routes/legacy_chat_compat.py) | `/api/chat` 旧版非流式 | ✅ |
| 上下文 | [rag/api/app_context.py](rag/api/app_context.py) | 推荐上下文预处理 | ✅ |
| 路由 | [rag/recommendation/tool_router.py](rag/recommendation/tool_router.py) | LLM+本地路由 + 🟢校验 + 🔵模式感知/PC修复 + 🟣 LLM Gateway 集成 | ✅ ⚠️ |
| 处理器 | [rag/recommendation/tool_handlers.py](rag/recommendation/tool_handlers.py) | 8 工具处理器（🟢v2 + 🔵响应生成 + 🟣session ID 解析修复） | ✅ |
| 处理基础 | [rag/recommendation/handler_base.py](rag/recommendation/handler_base.py) | 🟣 trace_span 上下文管理器 + 共享工具函数（catalog 加载、ID 解析） | ✅ |
| LLM网关 | [rag/recommendation/llm_gateway.py](rag/recommendation/llm_gateway.py) | 🟣 统一 LLM 调用注册表（9 场景配置 + 熔断 + 并发 + 日志）⚠️ 调用点尚未迁移 | ✅ ⚠️ |
| 响应生成 | [rag/recommendation/response_generator.py](rag/recommendation/response_generator.py) | 🔵 LLM多样+模板变体（happy path 120+，含分支 >120） + 🟣 LLM Gateway 集成 | ✅ ⚠️ |
| 管道 | [rag/recommendation/recommendation_pipeline.py](rag/recommendation/recommendation_pipeline.py) | 推荐主管道 + 🟢v2需求+事实校验 | ✅ |
| 过滤 | [rag/recommendation/structured_filter.py](rag/recommendation/structured_filter.py) | 12步过滤链（含 LLM 语义过滤层） | ✅ |
| 评分 | [rag/recommendation/scorer.py](rag/recommendation/scorer.py) | 7维度可解释评分 | ✅ |
| 构建 | [rag/recommendation/package_builder.py](rag/recommendation/package_builder.py) | 推荐结果+套餐+卡片构建 | ✅ |
| 对比 | [rag/recommendation/comparison.py](rag/recommendation/comparison.py) | 产品对比+赢家选择 | ✅ ⚠️ |
| 价格 | [rag/recommendation/cost_estimator.py](rag/recommendation/cost_estimator.py) | 套餐总价估算 | ✅ |
| PC | [rag/recommendation/pc_build.py](rag/recommendation/pc_build.py) | PC配置穷举+兼容性检查 | ✅ |
| PC流 | [rag/recommendation/pc_session_flow.py](rag/recommendation/pc_session_flow.py) | PC构建对话流 | ✅ |
| Session | [rag/recommendation/session_state.py](rag/recommendation/session_state.py) | 会话存储+话题切换🔵 + 🟣子状态视图dataclass + schema_version v2 | ✅ |
| LLM | [rag/recommendation/llm_client.py](rag/recommendation/llm_client.py) | LLM客户端（mimo/dashscope） | ✅ ⚠️ |
| 查询改写 | [rag/recommendation/query_rewriter.py](rag/recommendation/query_rewriter.py) | 多轮对话查询改写（代词消解+属性继承） | ✅ |
| 会话上下文 | [rag/recommendation/session_context.py](rag/recommendation/session_context.py) | 多轮上下文记忆管理（滑动窗口+压缩） | ✅ |
| 解释生成 | [rag/recommendation/explanation_builder.py](rag/recommendation/explanation_builder.py) | 基于证据的推荐理由生成（字段白名单防幻觉） | ✅ |
| 意图路由 | [rag/recommendation/intent_router.py](rag/recommendation/intent_router.py) | 需求→推荐模式映射（7种路由） | ✅ |
| 查询守卫 | [rag/recommendation/query_guards.py](rag/recommendation/query_guards.py) | 确定性品类/产品类型推断 | ✅ |
| 检索 | [rag/recommendation/retrieval.py](rag/recommendation/retrieval.py) | 向量检索 + 多变体查询扩展 | ✅ |
| 检索融合 | [rag/recommendation/retrieval_fusion.py](rag/recommendation/retrieval_fusion.py) | RRF 多源结果融合 | ✅ |
| 附件处理 | [rag/api/attachments.py](rag/api/attachments.py) | 图片/附件上传 + VLM 多模态分析 | ✅ |
| 图像检索 | [rag/recommendation/image_retrieval.py](rag/recommendation/image_retrieval.py) | 像素直方图嵌入 + 相似商品检索 | ✅ |
| 输入预处理 | [rag/recommendation/input_preprocessor.py](rag/recommendation/input_preprocessor.py) | 多模态信号处理（音频转录+附件） | ✅ |
| 模型 | [rag/schemas/recommendation.py](rag/schemas/recommendation.py) | Pydantic数据模型 | ✅ |
| 存储 | [rag/storage/](rag/storage/) | 向量存储（Milvus+缓存） | ✅ |
| 摄入 | [rag/ingestion/](rag/ingestion/) | 商品索引构建（仅离线） | ✅ |
| 工具 | [rag/utils/](rag/utils/) | 共享工具 | ✅ |
| 前端 | [frontend/index.html](frontend/index.html) | 🟣 新建对话按钮 + 缓存版本更新 | ✅ |
| 前端 | [frontend/app.js](frontend/app.js) | 🟣 tool_call 徽章展示 + Enter 发送 + session 重置 | ✅ |

---

## 十五、补充报告索引（v4.2 新增）

| 报告 | 文件 | 内容 |
|------|------|------|
| 硬编码修正方案 | [report/hardcoded-fix-plan.md](hardcoded-fix-plan.md) | Top 10 优先修复项，含 before/after 代码示例，分 3 阶段实施 |
| LLM 调用链路报告 | [report/llm-call-chain.md](llm-call-chain.md) | 9 个 LLM 调用场景完整追踪，含 prompt 模板、参数配置、降级策略 |
| 设计目标评估 | [report/link-design-target-evaluation.md](link-design-target-evaluation.md) | 9 个链路设计目标逐项评估，整体达成率 ~67% |
| 面试题准备 | [report/interview-qa.md](interview-qa.md) | 42 个技术面试题 + 参考答案 + 追问预警，覆盖 RAG/Agent/LLM/工程 |
| 购物车改进评估 | [report/cart-improvement-evaluation.md](cart-improvement-evaluation.md) | 4 个购物车改进提案实施评估，49/49 测试通过 |

---

*文档完（v4.2）。如有链路改动，请同步更新此文档。*
