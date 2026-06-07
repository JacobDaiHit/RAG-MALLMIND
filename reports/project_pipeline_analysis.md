## MallMind (trad_rag) 项目链路梳理与审查报告

> 生成时间：2026-06-07 | 不修改任何代码，仅做分析和报告

---

## 一、项目总览

MallMind 是一个面向比赛演示的电商智能导购 Demo，后端基于 FastAPI，核心能力包括自然语言导购对话、SSE 流式返回、商品推荐、商品对比、购物车、PC 整机方案、RAG 检索、图片找货等。

项目目录结构：

```
rag/
  api/                 -- HTTP 路由层 & 应用入口
    routes/            -- 路由子模块（chat, recommend, attachments, feedback, legacy_chat_compat, common）
    recommendation_app.py  -- FastAPI app 工厂
    runtime_context.py -- 自适应运行时上下文构建
    app_context.py     -- 推荐上下文预处理
    products.py        -- 商品 CRUD 路由
    pc_build.py        -- PC 装机路由
    attachments.py     -- 附件处理逻辑
    sse.py             -- SSE 格式化工具
    request_models.py  -- 请求模型定义
  recommendation/      -- 推荐引擎核心
    tool_router.py     -- 工具路由器（规则 + LLM + Guard 三层）
    tool_handlers.py   -- 五大分支处理器
    recommendation_pipeline.py -- 推荐管线主逻辑
    recommendation_graph.py    -- 图式推荐编排
    intent_router.py   -- 意图路由分类
    runtime_mode.py    -- 运行时模式策略定义
    runtime_mode_selector.py -- 运行时模式选择入口
    adaptive_runtime.py -- 自适应运行时决策引擎
    structured_filter.py -- 结构化过滤（7层漏斗）
    scorer.py          -- 商品评分（动态权重）
    session_state.py   -- 会话状态管理
    session_context.py -- 多轮上下文记忆
    pc_build.py        -- PC 装机方案生成核心
    pc_session_flow.py -- PC 装机会话流程
    pc_compatibility.py -- PC 兼容性校验
    pc_types.py        -- PC 配件类型定义
    comparison.py      -- 商品对比
    image_retrieval.py -- 图片检索
    product_loader.py  -- 商品库加载
    input_preprocessor.py -- 输入预处理
    cost_estimator.py  -- 成本估算
    query_guards.py    -- 查询守卫 & 约束解析
    package_builder.py -- 推荐结果打包
  storage/             -- 存储层
    milvus_client.py   -- Milvus 向量数据库客户端
    milvus_writer.py   -- Milvus 批量写入
    cache.py           -- Redis 缓存
    parent_chunk_store.py -- 父级分块存储（PostgreSQL + Redis）
    database.py        -- PostgreSQL 连接
  ingestion/           -- 数据入库
    embedding.py       -- Embedding 服务（密集 + BM25 稀疏）
    product_chunks.py  -- 商品分块构建
  utils/               -- 工具层
    rag_utils.py       -- RAG 检索工具（遗留版）
    retrieval_postprocess.py -- 检索后处理（活跃版）
    runtime_errors.py  -- 运行时错误处理
  schemas/             -- 数据模型
    models.py          -- SQLAlchemy ORM 模型
    recommendation.py  -- Pydantic 业务模型
  legacy/              -- 已废弃模块
    tools.py           -- LangChain 工具（不再使用）
```

---

## 二、API 路由总表

FastAPI app 在 `rag/api/recommendation_app.py` 的 `create_app()` 中创建，注册 6 个 Router，加上 app 直接挂载的路由，共 21 个端点。

| # | HTTP | 路径 | 函数名 | 文件 | 定位 |
|---|------|------|--------|------|------|
| 1 | GET | `/` | `index` | `recommendation_app.py` | 前端入口 |
| 2 | GET | `/health` | `health` | `recommendation_app.py` | 健康检查 |
| 3 | GET | `/api/health` | `health` | `recommendation_app.py` | 健康检查 |
| 4 | GET | `/api/runtime/diagnostics` | `runtime_diagnostics` | `recommendation_app.py` | 运行时诊断 |
| 5 | GET | `/api/llm/diagnose` | `diagnose_llm` | `recommendation_app.py` | LLM 诊断 |
| 6 | GET | `/api/products` | `list_products` | `products.py` | 商品列表 |
| 7 | GET | `/api/products/{product_id}` | `get_product` | `products.py` | 商品详情 |
| 8 | POST | `/api/products` | `save_product` | `products.py` | 新增商品 |
| 9 | PUT | `/api/products/{product_id}` | `update_product` | `products.py` | 更新商品 |
| 10 | POST | `/api/pc-build/generate` | `generate_pc_build` | `pc_build.py` | 生成PC方案 |
| 11 | POST | `/api/analyze-attachments` | `analyze_attachments` | `routes/attachments.py` | 分析附件 |
| 12 | POST | `/api/feedback` | `collect_feedback` | `routes/feedback.py` | 收集反馈 |
| 13 | POST | `/api/analyze-intent` | `analyze_intent` | `routes/recommend.py` | 意图分析 |
| 14 | POST | `/api/review-requirement` | `review_requirement` | `routes/recommend.py` | 需求审查 |
| 15 | POST | `/api/finalize-prompt` | `finalize_prompt` | `routes/recommend.py` | Prompt 定稿 |
| 16 | POST | `/api/recommend` | `recommend` | `routes/recommend.py` | 非流式推荐 |
| 17 | GET | `/api/stream-recommend` | `stream_recommend` | `routes/recommend.py` | 图式推荐调试流 |
| 18 | POST | `/api/chat` | `chat_compat` | `routes/chat.py` | 旧版兼容接口 |
| 19 | **POST** | **`/api/chat/stream`** | **`chat_stream`** | **`routes/chat.py`** | **主链路** |
| 20 | POST | `/api/cart/actions` | `cart_actions` | `routes/chat.py` | 购物车操作 |
| 21 | POST | `/api/products/compare` | `compare_product_cards` | `routes/chat.py` | 商品对比 |

---

## 三、主链路 Mermaid 图

### 3.1 全局路由总览

```mermaid
graph TB
    subgraph "客户端"
        USER["用户输入 / 图片附件"]
    end

    subgraph "FastAPI 路由层"
        HEALTH["GET /health<br/>recommendation_app.py::health"]
        PRODS["GET /api/products<br/>products.py::list_products"]
        PROD_ID["GET /api/products/{id}<br/>products.py::get_product"]
        PC_API["POST /api/pc-build/generate<br/>pc_build.py::generate_pc_build"]
        ATTACH["POST /api/analyze-attachments<br/>routes/attachments.py::analyze_attachments"]
        FEED["POST /api/feedback<br/>routes/feedback.py::collect_feedback"]
        INTENT["POST /api/analyze-intent<br/>routes/recommend.py::analyze_intent"]
        REVIEW["POST /api/review-requirement<br/>routes/recommend.py::review_requirement"]
        RECOMM["POST /api/recommend<br/>routes/recommend.py::recommend"]
        STREAM_REC["GET /api/stream-recommend<br/>routes/recommend.py::stream_recommend"]
        CHAT_LEGACY["POST /api/chat<br/>routes/chat.py::chat_compat"]
        CHAT_STREAM["POST /api/chat/stream<br/>routes/chat.py::chat_stream"]
        CART["POST /api/cart/actions<br/>routes/chat.py::cart_actions"]
        COMPARE["POST /api/products/compare<br/>routes/chat.py::compare_product_cards"]
    end

    USER --> CHAT_STREAM
    USER -.->|"旧版兼容"| CHAT_LEGACY
    USER -.->|"测试/调试"| RECOMM
    USER -.->|"调试"| STREAM_REC

    style CHAT_STREAM fill:#ff6b6b,color:#fff
    style CHAT_LEGACY fill:#aaa,color:#fff
    style RECOMM fill:#aaa,color:#fff
```

### 3.2 主链路 `/api/chat/stream` 详细流程

```mermaid
graph TB
    REQ["POST /api/chat/stream<br/>routes/chat.py::chat_stream"]

    subgraph "Step 1: 请求预处理"
        S1A["合并附件<br/>raw_attachments = request.attachments + request.images"]
        S1B["获取/创建会话<br/>session_state.py::get_session"]
    end

    subgraph "Step 2: 自适应运行时上下文"
        S2A["build_adaptive_runtime_context()<br/>runtime_context.py::L15"]
        S2B["select_adaptive_runtime()<br/>adaptive_runtime.py::L31"]
        S2C["runtime_policy_for_mode()<br/>runtime_mode.py::L37"]
    end

    subgraph "Step 3: 工具路由"
        S3A["route_shopping_tool_call()<br/>tool_router.py::L608"]
        S3B["remember_tool_call()<br/>session_state.py::L404"]
    end

    subgraph "Step 4: 分支处理"
        CART_H["handle_cart()<br/>tool_handlers.py::L28"]
        CHAT_H["handle_general_chat()<br/>tool_handlers.py::L42"]
        COMP_H["handle_compare()<br/>tool_handlers.py::L64"]
        PC_H["handle_pc_build()<br/>tool_handlers.py::L101"]
        REC_H["handle_recommend()<br/>tool_handlers.py::L135"]
    end

    subgraph "SSE 事件输出"
        EVT["runtime_mode → tool_call → delta → progress<br/>→ intent_route → product_cards → comparison_table<br/>→ pc_build_plan → cart → follow_up_questions → result → done"]
    end

    REQ --> S1A --> S1B --> S2A --> S2B --> S2C --> S3A --> S3B
    S3B -->|"apply_cart_instruction"| CART_H
    S3B -->|"general_chat"| CHAT_H
    S3B -->|"compare_products"| COMP_H
    S3B -->|"generate_pc_build_plan"| PC_H
    S3B -->|"recommend_shopping_products"| REC_H
    CART_H --> EVT
    CHAT_H --> EVT
    COMP_H --> EVT
    PC_H --> EVT
    REC_H --> EVT
```

### 3.3 工具路由三层架构

```mermaid
graph LR
    MSG["用户消息"]

    subgraph "第1层: 规则路由"
        LR["local_route_tool_call()<br/>tool_router.py::L847"]
        LRS["score_local_routes()<br/>tool_router.py::L783"]
        LR --> LRS
    end

    subgraph "第2层: LLM 路由 (可选)"
        SKIP{"should_skip_llm_route()<br/>tool_router.py::L724"}
        LLM["try_llm_route_tool_call()<br/>tool_router.py::L887"]
        THRESH["非对称阈值选择<br/>general_chat降级: conf >= 0.80<br/>购物工具升级: conf >= 0.50"]
    end

    subgraph "第3层: Guard 守卫"
        GUARD["validate_and_guard_tool_call()<br/>tool_router.py"]
    end

    RESULT["最终 tool_call<br/>name + confidence + arguments"]

    MSG --> LR
    MSG --> SKIP
    SKIP -->|"不跳过"| LLM --> THRESH
    SKIP -->|"跳过"| GUARD
    LRS --> THRESH
    THRESH --> GUARD
    GUARD --> RESULT
```

### 3.4 推荐分支 `handle_recommend` 详细流程

```mermaid
graph TB
    HR["handle_recommend()<br/>tool_handlers.py::L135"]

    subgraph "准备阶段"
        P1["确定 catalog_scope<br/>ecommerce / pc_parts / combined"]
        P2["图片检索 (可选)<br/>image_retrieval.py::retrieve_image_evidence"]
    end

    subgraph "推荐管线"
        R1["recommend_shopping_products()<br/>recommendation_pipeline.py::L166"]
        R2["validate_business_goal()<br/>recommendation_pipeline.py"]
        R3["parse_requirement()<br/>recommendation_pipeline.py::L247"]
        R3A["parse_requirement_rule_based()<br/>recommendation_pipeline.py::L595"]
        R3B["LLM 解析 (可选)<br/>build_requirement_prompt + LLM"]
        R4["build_recommendation_result()<br/>package_builder.py"]
    end

    subgraph "结构化过滤 (7层漏斗)"
        F1["库存过滤<br/>structured_filter.py::is_available"]
        F2["品牌/文本排除<br/>structured_filter.py::violates_brand_or_text_exclusion"]
        F3["子类目匹配<br/>structured_filter.py::matches_target_sub_category"]
        F4["商品类型匹配<br/>structured_filter.py::product_matches_type"]
        F5["必含词匹配<br/>structured_filter.py::matches_all_required_terms"]
        F6["PC规格约束<br/>structured_filter.py::product_matches_pc_constraints"]
        F7["偏好+预算<br/>structured_filter.py::matches_budget"]
    end

    subgraph "评分排序"
        SC["score_product()<br/>scorer.py::L37"]
        EB["apply_evidence_boost()<br/>scorer.py::L246"]
    end

    subgraph "RAG 检索 (可选)"
        RAG["retrieve_documents()<br/>rag_utils.py::L290"]
        HYB["hybrid_retrieve()<br/>milvus_client.py::L255"]
        RR["_rerank_documents()<br/>rag_utils.py::L141"]
        AM["_auto_merge_documents()<br/>rag_utils.py::L113"]
    end

    subgraph "后处理"
        ENR["enrich_recommendation_result()<br/>生成 guidance / 追问 / 建议"]
        EXP["attach_grounded_explanation()<br/>基于证据的推荐理由"]
        MEM["remember_recommendation()<br/>session_state.py::L461"]
    end

    HR --> P1 --> P2 --> R1
    R1 --> R2 --> R3
    R3 --> R3A --> R3B
    R3B --> R4
    R4 --> F1 --> F2 --> F3 --> F4 --> F5 --> F6 --> F7
    F7 --> SC --> EB
    R4 -.->|"可选"| RAG
    RAG --> HYB --> RR --> AM
    EB --> ENR --> EXP --> MEM
```

### 3.5 PC 装机链路

```mermaid
graph TB
    PC_REQ["用户PC装机请求"]

    subgraph "会话流程层"
        PC_FLOW["build_pc_plan_for_message()<br/>pc_session_flow.py::L18"]
        PC_PARSE["parse_pc_preferences()<br/>pc_session_flow.py::L169"]
    end

    subgraph "方案生成核心"
        GEN["generate_pc_build_plan()<br/>pc_build.py::L95"]
        LOAD["load_pc_parts()<br/>pc_build.py::L47"]
        DEDUP["deduplicate_pc_parts()<br/>pc_build.py::L197"]
        GROUP["group_parts()<br/>pc_build.py::L212"]
        LIMIT["limit_grouped_candidates()<br/>pc_build.py::L221"]
    end

    subgraph "8层嵌套穷举"
        ENUM["CPU x 主板 x 内存 x 显卡<br/>x 机箱 x 散热 x 电源 x 存储"]
        BUDGET["预算过滤<br/>resolve_budget_limit()::L191"]
        COMPAT["硬兼容性校验 (9项)<br/>pc_compatibility.py::check_pc_build_compatibility"]
        SOFT["软评分 (7维度)<br/>pc_build.py::soft_score_breakdown::L286"]
    end

    subgraph "方案输出"
        SUMM["build_summary()<br/>pc_build.py::L433"]
        REAS["build_plan_reasons()<br/>pc_build.py::L392"]
        WARN["collect_warnings()<br/>pc_build.py::L468"]
        UPGR["build_upgrade_suggestions()<br/>pc_build.py::L492"]
        ALT["build_alternatives()<br/>pc_build.py::L501"]
    end

    PC_REQ --> PC_FLOW --> PC_PARSE --> GEN
    GEN --> LOAD --> DEDUP --> GROUP --> LIMIT --> ENUM
    ENUM --> BUDGET --> COMPAT --> SOFT
    SOFT --> SUMM & REAS & WARN & UPGR & ALT
```

### 3.6 RAG 检索链路

```mermaid
graph TB
    QUERY["用户查询"]

    subgraph "Embedding 服务"
        EMB_DENSE["密集向量<br/>embedding.py::get_embeddings<br/>LocalEmbeddingProvider / OpenAICompatible"]
        EMB_SPARSE["稀疏向量 (BM25)<br/>embedding.py::get_sparse_embedding<br/>tokenize + TF-IDF"]
    end

    subgraph "Milvus 混合检索"
        HYB["hybrid_retrieve()<br/>milvus_client.py::L255"]
        DENSE_ANN["密集向量 ANN 搜索<br/>HNSW 索引"]
        SPARSE_ANN["稀疏向量 ANN 搜索<br/>SPARSE_INVERTED_INDEX"]
        RRF["RRF 融合排序<br/>score = sum 1/(k+rank), k=60"]
    end

    subgraph "后处理"
        RERANK["_rerank_documents()<br/>rag_utils.py::L141<br/>外部 Rerank API"]
        AUTOMERGE["_auto_merge_documents()<br/>rag_utils.py::L113<br/>L3→L2→L1 两段合并"]
        PCS["ParentChunkStore<br/>parent_chunk_store.py<br/>PostgreSQL + Redis"]
    end

    subgraph "降级路径"
        DENSE_FB["dense_retrieve()<br/>milvus_client.py::L347<br/>仅密集向量"]
    end

    QUERY --> EMB_DENSE & EMB_SPARSE
    EMB_DENSE --> DENSE_ANN
    EMB_SPARSE --> SPARSE_ANN
    DENSE_ANN & SPARSE_ANN --> RRF --> RERANK --> AUTOMERGE
    AUTOMERGE --> PCS
    HYB -.->|"混合检索失败"| DENSE_FB

    style HYB fill:#4ecdc4,color:#fff
    style DENSE_FB fill:#ff9f43,color:#fff
```

### 3.7 运行时模式选择

```mermaid
graph TB
    INPUT["输入信号<br/>message, session, requested_mode,<br/>has_attachments, llm_configured, ..."]

    subgraph "降级检查 (最高优先级)"
        D1{"MALLMIND_LLM_ENABLED=false?"}
        D2{"llm_configured=False?"}
        D3{"is_test_env=True?"}
        D4{"system_degraded=True?"}
    end

    DEGRADED["degraded_fast<br/>全部 LLM/RAG 关闭"]

    subgraph "用户显式指定"
        EXPLICIT{"requested in fast/balanced/full?"}
    end

    subgraph "自动选择"
        FULL{"_needs_full()?<br/>complexity >= 0.72"}
        BALANCED{"balanced 条件?<br/>route_conf < 0.85<br/>completeness < 0.75<br/>history >= 0.45"}
        FAST_SAFE{"is_fast_safe_case()?<br/>conf >= 0.93, margin >= 0.45<br/>completeness >= 0.88, complexity <= 0.30<br/>history == 0"}
        DEFAULT["balanced (默认)"]
    end

    subgraph "策略生成"
        POLICY["runtime_policy_for_mode()<br/>runtime_mode.py::L37"]
    end

    INPUT --> D1
    D1 -->|"是"| DEGRADED
    D1 -->|"否"| D2 -->|"是"| DEGRADED
    D2 -->|"否"| D3 -->|"是"| DEGRADED
    D3 -->|"否"| D4 -->|"是"| DEGRADED
    D4 -->|"否"| EXPLICIT
    EXPLICIT -->|"是"| POLICY
    EXPLICIT -->|"否"| FULL
    FULL -->|"是"| POLICY
    FULL -->|"否"| BALANCED
    BALANCED -->|"是"| POLICY
    BALANCED -->|"否"| FAST_SAFE
    FAST_SAFE -->|"是"| POLICY
    FAST_SAFE -->|"否"| DEFAULT --> POLICY
```

---

## 四、置信度计算详解

本系统中"置信度"出现在多个层面，下面逐一说明。

### 4.1 路由置信度 (Route Confidence)

**位置**: `adaptive_runtime.py::L52-55`，`tool_router.py::L783-844`

路由置信度在 `score_local_routes()` 中计算。它为 5 种候选工具（recommend_shopping_products, generate_pc_build_plan, compare_products, apply_cart_instruction, general_chat）各打一个分数，然后取最高分和次高分：

```
margin = top_score - second_score
margin_bonus = 0.03 ~ 0.08 (margin 越大 bonus 越大)
ambiguity_penalty = 0 ~ 0.27 (top_score 低且 margin 小时惩罚大)
confidence = clamp(top_score + margin_bonus - ambiguity_penalty, 0.0, 0.99)
```

这个置信度影响运行时模式选择：
- `confidence < 0.85` 或 `margin < 0.25` → 升级到 balanced
- `confidence < 0.93` 或 `margin < 0.45` → 阻止 fast 模式

在 LLM 路由与规则路由竞争时，使用非对称阈值：LLM 降级到 general_chat 需 conf >= 0.80，升级到购物工具仅需 conf >= 0.50。

### 4.2 需求完整度 (Requirement Completeness)

**位置**: `adaptive_runtime.py::_requirement_completeness()::L203`

```
初始 = 1.0
每个缺失字段: -min(len(missing_fields) * 0.18, 0.54)
无 desired_categories 且无 category: -0.22
无 price_max 且无 budget: -0.08
最终 clamp [0.0, 1.0]
```

`completeness < 0.75` → 升级到 balanced；`< 0.88` → 阻止 fast。

### 4.3 查询复杂度 (Query Complexity)

**位置**: `adaptive_runtime.py::_query_complexity()::L216`

```
基础分 = min(去空格字符数 / 120.0, 0.55)
有附件/图片: +0.35
比较类请求: +0.18
套装类请求: +0.18
多分句 (>=2 逗号/分号/连词): +0.20
最终 clamp [0.0, 1.0]
```

`complexity >= 0.72` → 升级到 full；`> 0.30` → 阻止 fast。

### 4.4 历史依赖度 (History Dependency)

**位置**: `adaptive_runtime.py::_history_dependency()::L230`

```
初始 = 0.0
含追问词 ("刚才"/"之前"/"上一个"/"换成"等): +0.55
含"预算"且有历史上下文: +0.35
session.last_result 存在: +0.18
session.topic_memory 存在: +0.12
session.last_requirement 存在: +0.12
最终 clamp [0.0, 1.0]
```

`history >= 0.45` → 升级到 balanced；`> 0.0` → 阻止 fast。

### 4.5 商品评分 (Product Scoring)

**位置**: `scorer.py::score_product()::L37`，`scorer.py::apply_evidence_boost()::L246`

基础权重（BASE_WEIGHTS）：

| 维度 | 权重 |
|------|------|
| scenario_match (场景匹配) | 0.25 |
| attribute_match (属性匹配) | 0.20 |
| price_fit (价格适配) | 0.20 |
| reputation_fit (口碑适配) | 0.10 |
| availability_fit (可用性) | 0.10 |
| sku_fit (SKU 完整度) | 0.10 |
| detail_quality (详情质量) | 0.05 |

动态权重调整（`build_dynamic_weights()::L352`）根据低预算/高品质/套装/对比/多模态等场景微调权重。

```
base_score = SUM(各维度得分 * 对应权重)

# Evidence Boost
best_hit = max(evidence 中各条目的 score)
base_boost = min(best_hit, 1.0) * 0.07 + min(len(evidence), 3) / 3 * 0.05
boost = min(base_boost, 0.12)  # 普通上限
boost 上限提高到 0.16 (强匹配: 核心商品词命中 evidence top-3)
final_score = clamp(base_score + boost, 0.0, 1.0)
```

### 4.6 PC 装机软评分

**位置**: `pc_build.py::soft_score_breakdown()::L286`

7 个维度，每个 0~1 分，根据用途和偏好动态调权后求和：budget_fit、performance_fit、noise_fit、appearance_fit、upgrade_fit、value_fit、evidence_fit。

### 4.7 Milvus 混合检索评分 (RRF)

**位置**: `milvus_client.py::hybrid_retrieve()::L255`

```
RRF_score(d) = sum(1 / (k + rank_i(d)))   # k=60, 对每个检索列表 i
```

密集向量和稀疏向量各自 ANN 检索后，用 RRF 融合排序。

### 4.8 BM25 稀疏向量

**位置**: `embedding.py::_sparse_vector_for_text_unlocked()::L498`

```
score(token) = IDF(token) * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / avg_doc_len))
其中 IDF = log((N - df + 0.5) / (df + 0.5) + 1),  k1=1.5,  b=0.75
```

---

## 五、代码审查：补丁与不合理之处

### 5.1 临时补丁 / Hotfix (严重程度: 中)

**P1: 根目录编码修复脚本**
- `_fix_encoding.py` (81行): 一次性编码修复脚本，修补 `tool_router.py` 中被编辑工具写坏的中文编码。不应留在仓库根目录。
- `_check_enc.py` (23行): 配合上面的诊断脚本。同样是一次性工具。

**P2: Legacy 模块保留**
- `rag/legacy/tools.py` (112行): 文件头注释明确声明 "The production shopping flow does not use this module"，但仍然存在并通过 `rag/utils/tools.py` 被间接导入。使用了大量全局可变状态且无线程安全保护。
- `rag/utils/tools.py` (9行): 仅做 `from rag.legacy.tools import *` 转发。

**P3: Legacy Chat 兼容层硬编码**
- `rag/api/routes/legacy_chat_compat.py` 第 200-283 行: `_legacy_direct_response()` 函数包含大量针对特定测试用例的硬编码中文关键词匹配（"冰箱"、"iPhone 17 Pro"、"雅诗兰黛"、"咖啡"等），属于典型的"测试驱动补丁"。

### 5.2 重复逻辑 (严重程度: 高)

**D1: `_has_image_data` 定义了三份**
- `routes/chat.py::L230`、`routes/recommend.py::L153`、`routes/legacy_chat_compat.py::L400`
- 三处实现略有差异（recommend.py 额外处理了 str 类型 JSON 解析）

**D2: `_is_test_env` 定义了两份**
- `routes/chat.py::L234`、`routes/recommend.py::L164`，实现完全相同

**D3: `_system_degraded` 定义了两份**
- `routes/chat.py::L238`、`routes/recommend.py::L168`，实现完全相同

**D4: `stream_llm_enabled` 定义了两份**
- `routes/chat.py::L37`、`routes/recommend.py::L31`

**D5: `_chat_mode` 定义了两份 (均为死代码)**
- `routes/chat.py::L223`、`routes/legacy_chat_compat.py::L395`，从未被调用

**D6: `rag_utils.py` 与 `retrieval_postprocess.py` 大面积重复**
- 5 个函数几乎完全相同：`_get_rerank_endpoint`, `_merge_to_parent_level`, `_auto_merge_documents`, `_rerank_documents`, `get_parent_chunk_store`
- 环境变量和常量也重复读取（RERANK_MODEL, RERANK_BINDING_HOST 等）
- 约 140 行冗余代码

**D7: `_parent_chunk_store` 全局单例定义了两份**
- `rag_utils.py::L30`、`retrieval_postprocess.py::L30` 各自维护独立实例

### 5.3 配置不一致 (严重程度: 高)

**C1: `RECOMMENDATION_LLM_GUIDANCE` 默认值冲突**
- `recommendation_graph.py::L27` 默认 **True**
- `recommendation_pipeline.py::L406` 默认 **False**
- `recommendation_app.py::L104` 默认 **"false"**

同一环境变量在三个地方读取，默认值互相矛盾。

**C2: `EMBEDDING_DIM` vs `DENSE_EMBEDDING_DIM`**
- `embedding.py::L45` 优先 `EMBEDDING_DIM`，回退 `DENSE_EMBEDDING_DIM`
- `milvus_client.py::L88` 仅读 `DENSE_EMBEDDING_DIM`
- `milvus_writer.py::L41` 仅读 `DENSE_EMBEDDING_DIM`

如果用户只设了 `EMBEDDING_DIM` 而没设 `DENSE_EMBEDDING_DIM`，embedding 服务和 Milvus 存储之间可能维度不一致。

**C3: `VISION_MODEL` vs `MULTIMODAL_MODEL`**
- `attachments.py::L14`: `os.getenv("VISION_MODEL") or os.getenv("MULTIMODAL_MODEL")`
- 两个环境变量名称指向同一功能，增加配置混乱风险。

### 5.4 线程安全问题 (严重程度: 高)

**T1: `_parse_trace` 全局 dict**
- `recommendation_pipeline.py::L244`: `_parse_trace: Dict[str, Any] = {}`，在 `parse_requirement()` 中通过 `global` 声明修改。FastAPI 使用线程池，多并发请求会竞争同一个 dict。

**T2: Legacy tools 全局变量**
- `legacy/tools.py::L12-15`: 4 个全局可变变量通过 `global` 在函数中修改，无锁保护。

**T3: rag_utils.py 延迟初始化**
- `rag_utils.py::L35-59`: `get_milvus_manager()` 等是 check-then-create 模式，并发环境下可能创建多个实例。
- 对比：`milvus_client.py` 使用了 `threading.RLock()` 保护，但其他模块没有。

### 5.5 硬编码 (严重程度: 中)

| 文件 | 行号 | 硬编码内容 |
|------|------|-----------|
| `storage/database.py` | L14 | `postgresql+psycopg2://postgres:postgres@localhost:5432/langchain_app` |
| `storage/cache.py` | L19 | `redis://localhost:6379/0` |
| `storage/milvus_client.py` | L28 | 默认 `localhost:19530` |
| `schemas/recommendation.py` | L299-308 | 价格分档阈值: 50, 200, 800, 3000 |
| `recommendation/image_retrieval.py` | L31 | `IMAGE_EMBEDDING_DIM = 61` |

### 5.6 死代码 (严重程度: 低)

- `_chat_mode()`: 在 `routes/chat.py::L223` 和 `routes/legacy_chat_compat.py::L395` 中定义但从未被调用
- `rag/legacy/tools.py`: 整个模块 112 行，注释声明不再使用但仍保留
- `rag_utils.py` 中约 140 行重复的 rerank/auto-merge 实现，注释说活跃路径使用 `retrieval_postprocess.py`

### 5.7 错误处理不一致 (严重程度: 中)

- **吞异常**: `legacy/tools.py::L61` (`except Exception: pass`)、`cache.py` 多处静默忽略、`legacy_chat_compat.py::L145`
- **直接抛出**: `parent_chunk_store.py::L86-89`、`database.py::L10-11`
- 调用方无法用统一策略处理这两种风格

### 5.8 过大文件 (严重程度: 中)

| 文件 | 行数 | 说明 |
|------|------|------|
| `tool_router.py` | **1603** | 核心模块，建议拆分 |
| `recommendation_pipeline.py` | **1032** | 核心模块 |
| `session_state.py` | 770 | 会话管理 |
| `pc_build.py` | 750 | PC 装机 |
| `embedding.py` | 584 | Embedding 服务 |

### 5.9 其他问题

- **`datetime.utcnow()` 已弃用**: `schemas/models.py` 5 处、`parent_chunk_store.py` 1 处，Python 3.12 中应改用 `datetime.now(timezone.utc)`
- **多处 `load_dotenv()` 调用**: 6 个模块各自在顶层调用，可能导致环境变量在不同导入顺序下出现意外值
- **`ChatStreamRequest.stream` 字段冗余**: 路由选择完全由 URL 路径决定，`stream: bool = True` 从未被读取

---

## 六、正式上线该怎么走链路

### 6.1 主链路选择

正式上线唯一推荐的主链路是 `POST /api/chat/stream`。客户端（包括计划中的 Android 原生端）应直接对接此端点。其他端点的定位：

- `/api/chat`: 保留给旧脚本和测试，不作为正式入口
- `/api/recommend`: 保留给单次完整推荐返回场景（smoke check）
- `/api/stream-recommend`: 仅调试用，观察 RecommendationGraph 事件
- `/api/pc-build/generate`: 可保留为独立装机 API，但主链路已通过对话式装机（tool_router 路由）覆盖
- `/api/products/*`: 商品 CRUD，管理后台使用

### 6.2 环境配置清单

上线前需明确配置以下环境变量：

```env
# 基础
APP_ENV=production
HOST=0.0.0.0
PORT=8000

# LLM（核心）
MODEL=xxx
BASE_URL=xxx
OPENAI_API_KEY=xxx
MALLMIND_LLM_ENABLED=true

# 视觉模型（可选）
VISION_MODEL=xxx
MULTIMODAL_MODEL=xxx

# 数据库（生产必须显式设置）
DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/dbname

# Redis（生产推荐）
REDIS_URL=redis://host:6379/0

# Milvus（RAG 链路必须）
MILVUS_HOST=xxx
MILVUS_PORT=19530
MILVUS_COLLECTION=xxx
DENSE_EMBEDDING_DIM=1024

# 功能开关
RECOMMENDATION_STREAM_USE_LLM=true
RECOMMENDATION_ENABLE_MILVUS=true
RECOMMENDATION_QUERY_EXPANSION=false
RECOMMENDATION_LLM_GUIDANCE=true  # 注意: 需统一三处默认值
```

### 6.3 上线前检查组合

**无外部服务的基础检查：**

```bash
python scripts/validate_pc_dataset.py --strict
python scripts/index_ecommerce_products.py --dry-run
python scripts/eval_user_scenarios.py --output-json reports/user_scenarios_eval.json --output-md reports/user_scenarios_eval.md
python -m pytest tests -q --basetemp .pytest_tmp/pytest_all
```

**完整增强链路检查：**

```bash
python scripts/index_ecommerce_products.py --rebuild
python scripts/check_vector_index_health.py --output reports/vector_index_health.json
python scripts/eval_user_scenarios.py --use-llm --runtime-mode full --output-json reports/user_scenarios_eval.json --output-md reports/user_scenarios_eval.md
python scripts/eval_model_chain_ablation.py --cases tests/fixtures/capability_challenge_eval_cases.json --groups all --output reports/capability_challenge_eval.json --markdown reports/capability_challenge_eval.md
```

### 6.4 正式链路运行时模式建议

| 场景 | 推荐模式 | 说明 |
|------|---------|------|
| 无 LLM 环境 | `fast` 或 `degraded_fast` | 纯规则路由 + 结构化评分，无需外部服务 |
| 有 LLM 但无 Milvus | `balanced` | LLM 路由增强 + 结构化评分，不依赖向量检索 |
| 完整能力 | `full` | LLM 全链路 + Milvus RAG + 查询扩展 + 视觉理解 |
| 比赛演示 | `auto`（让系统自动选择） | 系统根据查询复杂度、置信度、历史依赖自动决定 |

### 6.5 部署注意事项

- SSE 反向代理时需要关闭 response buffering（Nginx: `proxy_buffering off`）
- 生产环境不应依赖本地默认数据库连接串，`DATABASE_URL` 必须显式设置
- 会话存储：有 Redis 时自动使用 `RedisSessionStore`，否则回退内存存储（重启丢失会话）
- Milvus 未启用/不可达/无 collection/超时时自动降级到本地结构化商品库评分
- LLM 不可用时自动降级到 `degraded_fast` 模式

### 6.6 建议上线前修复的优先问题

1. **`RECOMMENDATION_LLM_GUIDANCE` 默认值冲突** -- 统一为一致的默认值
2. **`_parse_trace` 线程安全** -- 改为函数返回值或请求级上下文传递
3. **合并 `rag_utils.py` 与 `retrieval_postprocess.py`** -- 消除 140 行冗余代码
4. **清理根目录临时脚本** -- 移除 `_fix_encoding.py` 和 `_check_enc.py`
5. **统一 `EMBEDDING_DIM` / `DENSE_EMBEDDING_DIM`** -- 避免维度不一致

---

## 七、问题汇总统计

| 类别 | 发现数量 | 严重程度 |
|------|---------|---------|
| 临时补丁/Hotfix 脚本 | 2个根目录脚本 + 2个遗留模块 | 中 |
| 重复逻辑 | 7组重复函数/变量 | **高** |
| 配置不一致 | 3处 | **高** |
| 线程安全问题 | 3处 | **高** |
| 路由冗余 | 3个功能重叠的推荐端点 | 中 |
| 死代码 | 3处 | 低 |
| 硬编码值 | 5处 | 中 |
| 过大文件 (>500行) | 12个文件 | 中 |
| 全局可变状态 | 9个模块约15个全局变量 | 中-高 |
| 错误处理不一致 | 多处吞异常 vs 直接抛出 | 中 |
| datetime.utcnow() 弃用 | 6处 | 低 |
| load_dotenv() 多处调用 | 6处 | 低 |
| 数据一致性 | 2处 | 中 |
