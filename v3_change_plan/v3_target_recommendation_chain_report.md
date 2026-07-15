# V3 理想推荐链路与全量改造设计报告

> 状态：设计报告，**尚未修改生产代码**。
> 依据：当前仓库的实际请求、Router、推荐、PC、Milvus、切片与 session 实现审计；本报告描述完成 V3 后应达到的目标状态。

## 1. 一句话目标

将当前“Router 工具名、运行 mode、RAG 证据、目录筛选、PC 逻辑、session、trace 相互交叉”的链路，重构为：

```text
统一请求 -> 4 个业务动作 -> 结构化需求与硬约束 -> 目录候选门控
         -> 多通道商品召回 -> 商品级重排/多样化 -> 最终事实校验
         -> 可解释响应/SSE -> 紧凑会话 + 独立 trace
```

核心原则：

1. **目录商品事实优先于模型和向量。** 价格、库存、品牌排除、SKU、PC 兼容性只能由结构化数据/规则确认。
2. **向量检索只补充召回和证据。** 它不能绕过商品硬约束，更不能用相似度“猜”出兼容或库存。
3. **对外接口少而稳定，内部能力可专用。** 对外仅 4 个 action；比较、完整装机仍保留专用内部实现。
4. **LLM 只做擅长的事。** 可用于路由、需求抽取、歧义澄清和文案组织；不能成为硬过滤的唯一执行者。
5. **业务会话和调试 trace 分离。** 下一轮对话只保留必要状态，检索证据/附件分析/调用日志不再塞进 session。

## 2. V3 目标全景图

```text
                                 ┌──────────────────────────────┐
                                 │ Catalog / Product Facts       │
                                 │ 商品、SKU、库存、价格、品牌族 │
                                 └──────────────┬───────────────┘
                                                │
┌──────────┐      ┌─────────────┐      ┌────────▼────────┐
│ HTTP/SSE │ ---> │ Input Guard │ ---> │ V3 Action Router │
│ Request  │      │ + Session   │      │ 4 stable actions │
└──────────┘      └─────────────┘      └────────┬────────┘
                                                 │
                  ┌──────────────────────────────┼───────────────────────────────┐
                  │                              │                               │
        ┌─────────▼─────────┐          ┌─────────▼─────────┐           ┌─────────▼─────────┐
        │ Cart / Chat        │          │ Product Info       │           │ Recommend          │
        │ transactional      │          │ attr/SKU/price/    │           │ product/bundle/   │
        │ / conversational   │          │ compare            │           │ pc_part/pc_build  │
        └────────────────────┘          └────────────────────┘           └─────────┬─────────┘
                                                                                     │
                             ┌──────────────────────────────────────────────────────┘
                             ▼
                 ┌───────────────────────────┐
                 │ Requirement + Constraints │
                 │ include / exclude / soft  │
                 └──────────────┬────────────┘
                                ▼
                 ┌───────────────────────────┐
                 │ Catalog Candidate Gate     │
                 │ hard constraints only      │
                 └──────────────┬────────────┘
                                ▼
          ┌─────────────────────┼──────────────────────┐
          ▼                     ▼                      ▼
     SKU/Exact + BM25       Dense/Image            PC Compatibility
     lexical recall          semantic recall        graph / solver
          └─────────────────────┼──────────────────────┘
                                ▼
                 ┌───────────────────────────┐
                 │ Product-ID aggregation     │
                 │ rerank + diversification   │
                 └──────────────┬────────────┘
                                ▼
                 ┌───────────────────────────┐
                 │ Final fact/constraint gate │
                 │ cards / plan / comparison  │
                 └──────────────┬────────────┘
                                ▼
                 ┌───────────────────────────┐
                 │ SSE response + SessionCore │
                 │ TraceStore (separate)      │
                 └───────────────────────────┘
```

## 3. 对外 API 与动作契约

### 3.1 删除的公共概念

不再接受或传播：`fast`、`balanced`、`full`、`degraded_fast`、`runtime_mode`、`requested_mode`、`selected_mode`。

用户请求不应决定是否使用 Milvus/LLM。服务端根据功能开关与依赖健康生成内部 `ExecutionPolicy`；它只用于执行与观测，不是一个用户可选业务模式。

### 3.2 唯一的 Router 输出：`RoutedAction`

```json
{
  "name": "apply_cart_instruction | general_chat | parameter_query | recommend_shopping_products",
  "arguments": {},
  "reason": "可观测原因，不参与业务执行",
  "source": "rules | llm | fallback"
}
```

四个 name 的职责：

| name | 内部判别字段 | 典型请求 | 输出边界 |
| --- | --- | --- | --- |
| `apply_cart_instruction` | `operation` | 加购、删除、改数量、查看购物车 | 购物车计划/确认结果 |
| `general_chat` | 无 | 问候、能力说明、非商品闲聊 | 对话文本 |
| `parameter_query` | `operation=attribute/sku/price/compare` | 参数、SKU、价格、商品对比、方案对比 | 单商品信息或 `comparison_table` |
| `recommend_shopping_products` | `mode=product/bundle/pc_part/pc_build` | 推荐、组合、单配件、完整装机 | 商品卡片或 `pc_build_plan` |

示例：

```json
{
  "name": "recommend_shopping_products",
  "arguments": {
    "mode": "product",
    "catalog_scope": "ecommerce",
    "product_mentions": [],
    "price_max": 3000,
    "include_brand_ids": [],
    "exclude_brand_family_ids": ["xiaomi"],
    "categories": ["phone"],
    "sub_categories": [],
    "must_have_terms": ["拍照"],
    "preferences": {"usage": ["旅行"]}
  }
}
```

Router 的本地规则和 LLM Router 都只能生成这个形状；旧的 `compare_products`、`sku_detail`、`price_comparison`、`generate_pc_build_plan` 不再是对外 name。为安全迁移，可临时保留“旧 action -> 新 action”的输入适配器，但禁止新 Router 输出旧 name。

## 4. 理想在线链路：从请求到响应

### 4.1 入口与执行策略

1. API 校验 `session_id`、文本长度、附件大小/类型，完成注入防护与文本规范化。
2. 只读取 `SessionCore`，不读取或序列化完整历史结果。读取后不立即写 Redis；本轮结束集中保存。
3. 由服务端解析 `ExecutionPolicy`：Router LLM、需求 LLM、视觉、Milvus、query expansion 是否可用，以及各项的健康/超时原因。
4. 先运行确定性 Router；若配置允许，再由 LLM Router 在小型 `RouterContext` 上修正。LLM 不可用时仍能生成同一 `RoutedAction`。
5. 校验 action schema、清洗 ID/价格/枚举，并合并会话中仍有效的约束。若存在冲突，直接返回澄清，不进入检索。

`RouterContext` 固定上限，只包含当前约束、最近 3 个约束增量、最多 5 个上轮展示商品摘要、最多 8 个购物车项和 PC 当前方案摘要；不传全量 `last_result`、附件内容、检索 trace 或调用日志。

### 4.2 四条动作分支

#### A. `apply_cart_instruction`

```text
解析 operation/目标商品/数量
  -> 从 SessionCore 与 catalog 校验目标
  -> add/remove/set_quantity 生成 CartPlan
  -> 写入 pending_cart_action（短 TTL）
  -> SSE: cart_plan
  -> 用户 confirm/cancel
  -> 原子更新 cart，清除 pending plan，SSE: cart_result
```

购物车是事务状态，允许在“生成计划”和“确认”两个节点显式 checkpoint；其他普通推荐请求不应为了刷新 TTL 而多次全量写 session。

#### B. `general_chat`

```text
最小对话上下文 -> 受保护的生成/模板回答 -> SSE delta/done
```

它不触发商品召回，也不应污染推荐约束；若用户消息含明确商品意图，Router 必须转到推荐或信息查询。

#### C. `parameter_query`

```text
统一商品目标解析
  -> explicit product/SKU ID
  -> 当前轮商品名
  -> 上轮展示商品摘要/PC 方案摘要
  -> 歧义时澄清
  -> attribute | sku | price | compare 专用内部服务
```

- `attribute`：从目录/SKU facts 取属性；可用 LLM 组织文本，但不能编造缺失字段。
- `sku`：从 `SkuIndexDocument` 与目录解析变体；返回明确 SKU 属性、价格和库存。
- `price`：优先实时目录价格和时间戳；不把向量文本内的旧价格当事实。
- `compare`：复用多目标解析和比较评分；保留 `comparison_table`。PC 方案对比使用已保存的 `plan_id`，而非重新从自然语言猜测整个历史。

#### D. `recommend_shopping_products`

`mode=product/bundle/pc_part` 进入商品推荐链；`mode=pc_build` 在普通商品推荐前分流到完整装机链。两者共享 requirement、品牌/库存/价格约束和索引事实，但 PC build 的候选选择必须经过兼容性 solver。

## 5. 理想商品推荐链

### 5.1 阶段 1：构建 `RequirementSpecV3`

需求解析输出不可混淆的结构：

```text
HardConstraints
  product_ids / sku_ids
  categories / exact sub_categories
  include_brand_ids / exclude_brand_family_ids
  active / stock_state
  hard price boundary（用户明确“必须/最多/不超过”）
  PC compatibility constraints

SoftPreferences
  场景、口碑、风格、颜色、轻薄、性能倾向、非强制预算

Clarification
  missing fields、包含与排除冲突、无法映射的品牌/属性
```

需求解析可由规则、LLM 或两者组合得到；但品牌、类目、ID、数值最终必须用 catalog dictionary/枚举验证。未经验证的词不能直接进入 Milvus expression。

### 5.2 阶段 2：目录候选门控（唯一硬约束入口）

先在 catalog 构造 `CandidateScope`，再调用任意召回器：

```text
all catalog products
  ∩ requested category/sub-category
  ∩ active
  ∩ in_stock
  ∩ include brand（若用户明确指定）
  ∩ NOT excluded brand family
  ∩ explicit product/SKU（若有）
  ∩ PC structural constraints（若有）
  = allowed_product_ids
```

要求：

- 被用户明确排除的条件永不放宽；库存/下架也不放宽。
- 对硬预算，若目录无结果，返回 `budget_catalog_gap` 与最近替代，不把超预算商品伪装成满足要求。
- 对软预算、颜色、口碑等可有受控回退，但 trace 必须标出回退理由和候选变化。
- `allowed_product_ids` 是所有召回器的上界。任何 dense、BM25、图像、RRF、rerank、vector rescue 都不能返回集合之外的商品。

### 5.3 阶段 3：多通道召回

在 `allowed_product_ids` 范围内并行进行：

| 通道 | 主要命中内容 | 优先级 |
| --- | --- | --- |
| Exact ID/SKU | 明确商品 ID、型号、SKU | 最高，直接定位 |
| Attribute/facet | 规格、价格区间、品牌、子类、库存 | 确定性候选 |
| BM25/倒排 | 型号、专有名词、罕见规格 | 高 |
| Dense embedding | 场景、自然语言偏好、同义表达 | 中，补充召回 |
| 图像向量 | 有图片且意图为找同款/风格时 | 按需 |
| PC compatibility graph | 完整装机/单配件的接口、功耗、尺寸、代际 | PC 必经 |

Milvus 的 pre-filter 是性能优化，不是硬约束唯一来源。它使用由 catalog 验证后的字段，例如：

```text
active == true
&& category == "phone"
&& brand_family_id not in ["xiaomi"]
&& sub_category in ["camera_phone"]
```

对于显式 ID、或 `allowed_product_ids` 较小的情形，可附加 `product_id in [...]` 保证集合一致；候选过大时避免超长表达式，但最终仍由目录 gate 强制校验。

### 5.4 阶段 4：商品级聚合、排序与多样化

检索的最小单位是证据 chunk，但推荐的最小单位必须是商品或 SKU：

1. 按 `product_id` 聚合多个 profile/SKU/FAQ/review 命中，保留最强证据和 evidence type 分布。
2. 先以规则特征打分：硬约束通过、价格贴合、场景、规格、品牌、库存、PC 兼容性、证据匹配。
3. 可选 cross-encoder 仅重排 top N 商品摘要；其分数只能在已通过硬门控的候选中作用。
4. 对多个通道可用 RRF 或学习排序融合，但输入列表必须属于同一 `allowed_product_ids` 集合。
5. 最后执行 MMR/xQuAD 式多样化：同款去重、限制同品牌连续、适当覆盖价格段/方案差异；多样化不能破坏硬约束。

### 5.5 阶段 5：最终事实校验与输出

生成商品卡片之前，以目录的最新事实再校验：商品仍 active、库存可售、价格符合硬边界、品牌/属性未排除、SKU 存在。卡片要带：

```text
product_id, sku_id（如有）, title, brand, current_price, stock_state,
matched_constraints, tradeoffs, evidence_refs, catalog_version
```

LLM 可把已校验的字段写成自然语言推荐理由，但不能新增商品、修改价格、掩盖未满足的硬约束。若某字段未知，应明确写“目录未提供”，不要推断。

## 6. 理想完整装机链

完整装机不是普通商品推荐的一个 prompt，而是结构化组合优化：

```text
pc_build action
  -> normalize PC requirement（预算、用途、已拥有配件、排除品牌、外观/噪音偏好）
  -> per-component CandidateScope（CPU/GPU/主板/...）
  -> compatibility graph / rule solver
       socket、内存代际、主板尺寸、机箱、散热、PSU 功率、接口等
  -> 组合搜索与预算分配
  -> 完整方案 fact gate
  -> pc_build_plan（components、价格、兼容性、风险、plan_id）
  -> 保存 PCPlanSummary + 独立 plan record
```

变更已有方案时：根据 `plan_id/current_pc_build` 和新 requirement 生成 component-level delta，只重新搜索受影响配件并再次跑完整兼容校验。不得只把原句扔回解析器，也不得让普通商品推荐替代完整方案生成。

PC 方案比较走 `parameter_query.operation=compare`，读取两个稳定 plan record，对组件、价格和兼容风险进行结构化对比，保持 `comparison_table` SSE 契约。

## 7. 理想离线数据、切片与入库链

### 7.1 三类索引实体

```text
ProductIndexDocument
  product_id, product_version, catalog_version, content_hash
  category, sub_category, brand_id, brand_family_id
  active, stock_state, min_price, max_price, rating, updated_at
  tags, searchable_attributes, compatibility

EvidenceChunk
  chunk_id, product_id, sku_id?, evidence_type
  text, dense_vector, sparse_vector, source_version, chunk_hash

SkuIndexDocument
  sku_id, product_id, properties, price, stock_state, active
```

切片规则：稳定 profile 一条；每个 SKU 一条；每条 FAQ 一条；评论按主题摘要或低权重独立条目。不要把 12 个 SKU、8 个 FAQ、10 个评论混成一个向量；不要以 `chunk_level=3` 冒充层级。

### 7.2 版本化入库

```text
catalog snapshot
  -> schema validation / brand normalization / content hashing
  -> chunk build + manifest
  -> embedding batch
  -> write candidate collection/version
  -> count/filter/sample-query validation
  -> atomically switch active index version
  -> retain previous version for rollback
```

每次 manifest 至少记录：目录商品数、SKU 数、每种 evidence 数、类目/品牌分布、空/超长文本、重复 ID、schema 版本、embedding 模型/维度、写入数、可查询数和 sample query 结果。

如果没有真实的长文档父子层级，删除 `ParentChunkStore` 与 auto-merge 在线路径；如果以后需要说明书 RAG，建立真实 `product -> section -> leaf` 并同时写父块，不能只写叶子。

## 8. 会话、存储与上下文设计

### 8.1 `SessionCore`（业务必需）

```text
session_id, schema_version, updated_at
cart, pending_cart_action
current_constraints（规范化的 include/exclude/预算/类目）
last_displayed_items <= 5（id/title/brand/price/category）
last_pc_build_summary（plan_id、组件 ID、预算、兼容摘要）
pc_build_history_summaries <= 3
recent_turns <= 4 + compact_turn_summary
```

### 8.2 `TraceStore`（非业务状态）

```text
request_id, session_id, ttl
routing trace, execution policy, retrieval trace, evidence refs,
attachment analysis, full response snapshot, LLM spans, timing/errors
```

写入策略：普通请求从头到尾收集 dirty state，结束时一次保存；购物车确认、需跨请求生效的 plan 才显式 checkpoint。Redis session payload 不再包含全量 `last_result`、检索 snippets、附件内容、`llm_call_log`。

V2 -> V3 迁移时，从旧 `last_result` 抽出最多 5 个展示商品和当前约束，从旧 PC 历史抽出 summary；迁移失败也必须保留购物车，降级而非中断会话。

## 9. SSE、可观测性与降级

### 9.1 SSE 兼容边界

保留前端所需的语义事件：`tool_call`、`progress`、`delta`、`comparison_table`、`pc_build_plan`、`cart_plan`、`cart_result`、`done`、`error`。移除 `runtime_mode`。

每个 `tool_call` 应只暴露 V3 action 和 operation/mode；每个结果都带 `request_id`，便于前端或管理员按需读取 trace。

### 9.2 合理降级

| 故障 | 降级动作 | 不可改变的内容 |
| --- | --- | --- |
| Router LLM 不可用 | 规则 Router | action schema、硬约束 |
| Requirement LLM 不可用 | 规则解析 + 澄清 | 品牌/库存/价格排除 |
| Milvus 不可用/超时 | catalog gate + lexical/规则排序 | allowed product scope |
| reranker 不可用 | 规则分数排序 | 最终 fact gate |
| 图像检索失败 | 文本/结构化通道 | 已解析的硬约束 |
| PC solver 无可行解 | 明确解释缺口、请求放宽 | 兼容性绝不伪造 |

降级原因写入 TraceStore，例如 `milvus_timeout`，但不得再把请求标为 `fast` 或让前端依 mode 改业务展示。

### 9.3 必备指标

```text
route_action_distribution / route_fallback_rate
hard_constraint_leak_rate（目标为 0）
pre_filter_count -> recalled_count -> fused_count -> final_count
brand/SKU/库存/预算淘汰原因
index_catalog_mismatch_rate
P50/P95 latency by stage
session payload bytes / writes per request
LLM calls and tokens per request
PC compatibility failure reason distribution
```

## 10. 端到端示例：用户说“预算 3000，旅行拍照用，不要小米的手机”

```text
1. Router
   -> recommend_shopping_products(mode=product)

2. RequirementSpecV3
   -> category=phone, price_max=3000, soft_usage=[旅行, 拍照]
   -> exclude_brand_family_ids=[xiaomi]

3. Catalog gate
   -> 仅 phone、active、in_stock、price<=3000、brand_family!=xiaomi
   -> 得到 allowed_product_ids

4. Recall
   -> BM25: “旅行 拍照”
   -> dense: “旅行拍照用手机”
   -> 两者都用 validated Milvus pre-filter；结果再与 allowed IDs 求交

5. Aggregate/rerank
   -> 每商品聚合证据，按拍照/预算/场景/价格排序，做品牌与机型多样化

6. Final fact gate
   -> 重新读取价格/库存/品牌族；若小米或下架商品混入，直接淘汰并记 trace

7. Response
   -> 商品卡、取舍、证据引用、可能的预算缺口；LLM 仅组织文字

8. State
   -> SessionCore 保存 3~5 张商品摘要和规范化约束；完整检索过程写 TraceStore
```

即使 Milvus、LLM 同时不可用，第 2、3、6 步仍然保证“不返回小米、不过预算、不过期下架”的底线；区别只在于候选召回广度和解释丰富度。

## 11. 现有模块的处理清单

| 现有模块/概念 | V3 处理 | 原因 |
| --- | --- | --- |
| `rag/api/routes/chat.py` | 重构为 4-action 分派、统一保存边界 | 当前承载八工具与轻/重分支 |
| `tool_router.py` | 重写 schema/prompt/local rules 为 `RoutedAction` | 删除 8-name 耦合 |
| `product_info_tools.py` | 合成内部 ProductInfo service | 参数/SKU/价格共享定位逻辑 |
| `comparison.py` | 保留 | 比较表与方案比较是独立能力 |
| `pc_build.py` / compatibility | 保留并接收 normalized requirement | 不能被普通推荐替换 |
| `runtime_context.py` | 删除 public mode 语义，替为内部 `ExecutionPolicy` | 删除多套 mode 控制面 |
| `/api/stream-recommend` | 统一到主策略或下线为开发工具 | 当前硬编码 balanced |
| `session_state.py` | 迁移到 SessionCore/TraceStore | 当前全量写和结果重复 |
| `product_chunks.py` | 重做为实体/原子证据生成器 | 当前四块拼接和伪 hierarchy |
| `milvus_writer.py` / `milvus_client.py` | 显式 schema、版本/增量写入、字段契约 | 当前生成字段未落库 |
| `parent_chunk_store.py` / auto-merge | 删除，或真正构建父层后再保留 | 当前缺少父文档 |
| `retrieval.py` / `retrieval_fusion.py` | 改为受 CandidateScope 限域的多通道召回 | 防止向量/救援越过硬条件 |
| `structured_filter.py` | 提升为 catalog candidate gate | 负品牌必须确定性硬过滤 |
| `tests/test_runtime_mode.py` | 删除/替换 | 当前导入不存在模块 |

## 12. 实施顺序与上线门槛

### Phase 0：先建立安全基线

修坏测试；新增硬约束泄漏测试；为当前链路记录召回/过滤/会话/延迟指标。此阶段不改变功能。

### Phase 1：正确性优先

实现品牌实体与负约束的 catalog gate，禁止 rescue 绕过；禁用未落库字段过滤和无效 auto-merge。此阶段即可保证“不要小米”等约束不泄漏。

### Phase 2：公共契约收敛

引入四 action 与 V2 适配器；移除 runtime mode 的 public API/SSE；保持 `comparison_table`、`pc_build_plan`、购物车确认事件兼容。

### Phase 3：索引重建与召回升级

定义新 schema/manifest，重建版本化索引，接入 CandidateScope 限域的 lexical/dense/image/PC graph 召回，商品级聚合和重排。

### Phase 4：会话与可观测性迁移

上线 SessionCore/TraceStore、一次请求一次常规写入、V2 迁移和完整 stage trace。

### Phase 5：清理与发布

删除旧工具名、mode、伪 hierarchy、失效脚本/测试和兼容适配器；完成回归、故障注入、Milvus 实测和性能验收后发布。

发布硬门槛：

- 硬约束泄漏率为 0（品牌排除、库存、下架、显式 SKU、严格预算）；
- `pytest` 无因失效模块导入导致的收集错误；
- 新索引 schema/writer/retrieval 字段契约与 sample query 通过；
- PC 完整装机、调配、比较和购物车两步确认端到端通过；
- 降级场景不改变 action 与硬约束；
- session 写入次数和 payload 字节数较 V2 明确下降；
- 前端 SSE 兼容事件通过。

## 13. 非目标与设计约束

- 不以“更多 LLM 调用”替代商品事实、规则或兼容性检查。
- 不在没有真实点击/成交数据时声称实现个性化协同过滤或 LTR。
- 不为了前置过滤而把实时价格/库存当作向量索引的唯一事实。
- 不为工具收敛删除比较、SKU、PC solver 等确有业务差异的内部核心。
- 不在本报告阶段直接修改任何生产模块；所有改动应按本目录的施工方案逐项实现并验证。

## 14. 外部大模型与 Embedding 模型：当前作用、风险与 V3 职责边界

### 14.1 总览：当前外部模型并非只在“RAG”中出现

当前项目的外部模型调用分成四类：

| 类别 | 当前实现 | 是否影响商品集合 | V3 定位 |
| --- | --- | --- | --- |
| 生成式文本 LLM | `OpenAICompatibleChatClient`，模型通过 Router/Parse/Guidance 等环境变量配置 | 当前部分会影响路由、需求和商品目标；不应直接决定硬约束 | 只做受约束的理解、澄清与表述 |
| 视觉 LLM | 同一兼容客户端，`VISION_MODEL_NAME` 或默认 chat model | 可提取图片线索，间接影响需求/检索 | 只提取候选线索，需 catalog 验证 |
| 稠密 embedding | `EmbeddingService`；本次实测配置为 DashScope `text-embedding-v4`、1024 维 | 影响 Milvus 语义召回和证据排序 | 保留为补充召回，不得越过 CandidateScope |
| 外部 reranker（可选） | `RERANK_BINDING_HOST` + `RERANK_MODEL` HTTP 服务 | 当前只重排 chunk | 若保留，改为商品级 top-N 重排 |

另外，BM25 稀疏向量是 `EmbeddingService` 内部自行维护的词表和统计，不调用外部 embedding 服务；图片相似度当前是本地 61 维 `pixel-hist-v1`，不是 CLIP/SigLIP 等外部图像模型。

### 14.2 当前生成式 LLM 的完整调用清单

| 调用点 | 当前输入与输出 | 当前业务效果 | 失败/关闭时 | V3 处理 |
| --- | --- | --- | --- | --- |
| `tool_router.route_shopping_tool_call` | 消息 + 小型 session context -> 8 个工具名及 arguments | 决定进入推荐、比较、SKU、PC 等分支 | 本地规则路由 | **保留**，但只输出 4-action `RoutedAction`；规则优先、schema 更小 |
| `recommendation_pipeline.parse_requirement` | 原句 + 规则解析初稿 -> `RequirementSpec` JSON | 补全场景、偏好、预算、品牌、排除条件等 | 规则 Requirement | **保留但降权**：LLM 输出是候选，品牌/类目/价格/ID 均须字典/枚举验证 |
| `api/attachments.analyze_image_attachment` | 图片 + 提示 -> OCR、型号、颜色、类目、预算、视觉检索词 | 为图片导购、PC 截图解析提供线索 | 标记视觉失败，要求文字补充 | **保留按需调用**：只提取线索，禁止把视觉猜测直接写成商品事实 |
| `product_reference._llm_select_product` | 候选商品摘要 + 当前追问 -> 候选序号 | 多轮“这款/第一个”的商品指代消解 | 规则排序/无结果 | **可选保留**：仅在候选集合已由服务端限制时使用；低置信度必须澄清 |
| `product_info_tools._llm_resolve_spec_key` | 可用规格键 + 用户问法 -> 字段键 | 将自然语言“屏幕多大”映射到 specs 字段 | 精确键名匹配 | **建议替换为同义词字典/规则**；LLM 只作最后的低风险备选 |
| `product_info_tools._llm_grounded_text` | 已验证的参数/SKU/价格 facts -> 短回答 | 组织商品信息文案 | 模板事实回答 | **保留为可选表述层**；不得再次查找/篡改事实 |
| `structured_filter._llm_filter_products` | 最多 30 个候选 + `excluded_brands` -> keep IDs | 当前试图识别子品牌/别名排除 | 异常时保留全部候选 | **删除出硬过滤链路**：改为 BrandEntity/brand family 的确定性 catalog gate |
| `query_rewriter._llm_rewrite` | 短追问 + session current/last result -> 完整检索 query | 解决代词、追加约束 | 原 query | **当前主推荐调用传入 `use_llm=False`，实际未在主链路启用**；V3 可保留为受控实验，不能改变硬约束 |
| `rag_utils.step_back_expand` / `generate_hypothetical_document` | 原 query -> step-back 问答/HyDE 假设文档 | query expansion 后多次 Milvus 召回 | 不扩展 | **默认关闭并与主 LLM 配置统一**；当前通过单独 ARK/LangChain 配置调用，是另一条外部模型控制面 |
| `recommendation_pipeline.enrich_recommendation_result` | 已选商品结果 -> teaching/follow-up/优化建议 JSON | 丰富推荐后的建议 | 规则 guidance | **保留为可选后处理**，不允许改商品集合、卡片事实或约束状态 |
| `explanation_builder.build_evidence_grounded_explanation` | 已选卡片、对比表、evidence -> 推荐理由 JSON | 生成证据化解释 | 模板 explanation | **保留**，输入白名单和 JSON 校验继续加强 |
| `response_generator.generate_natural_response` | 最终 payload -> 2--3 句聊天文案 | 当前默认可调用 LLM 让回复更自然 | 模板文案 | **保留但设置调用预算**；它只能改措辞，不能增加商品/价格/库存信息 |
| `comparison_summary.summarize_comparison` | 已生成 comparison rows -> 2--4 句总结 | 解释比较结果 | 规则 summary | **保留可选**，比较表先于文案产出 |
| `tool_handlers.handle_general_chat` | 闲聊原句 -> 对话回复 | 非商品闲聊自然化 | 固定模板 | **保留**；限流、短超时，不进入商品决策 |
| `tool_handlers._llm_resolve_cart_product` | 上轮展示商品 + 加购指令 -> 商品序号 | 模糊加购目标消解 | 规则/继续追问 | **不应作为自动下单依据**：V3 低置信或多候选一律澄清，仍须二次 confirm |

`pc_build.py` 的完整装机组合与兼容性检查应保持为本地结构化逻辑；外部 LLM 可以解析用途/偏好或解释方案，但不能决定 socket、功耗、尺寸等兼容事实。

### 14.3 当前外部文本 LLM 的问题

1. **调用面分散。** Router、需求解析、商品信息、闲聊、购物车、比较、query rewrite、HyDE 分别直接实例化 `OpenAICompatibleChatClient` 或另一套 LangChain/ARK client；`LLMGateway` 并没有统一覆盖 general chat 等调用。
2. **模型与行为耦合到旧 mode。** `runtime_context` 同时用 fast/balanced/full 决定 LLM、视觉、Milvus 和 query expansion，导致同一业务请求能力不稳定。
3. **一次请求可能产生多次无价值调用。** 典型复杂推荐可能包括 Router、需求解析、视觉、guidance、explanation、response generator；参数查询可能先做规格键映射，再生成回答。没有统一的 request-level call budget。
4. **品牌排除错误地依赖 LLM。** 当前 LLM 失败时会保留候选，不能满足“不要小米”这种硬约束。
5. **旧 HyDE/step-back 是第二套供应商配置。** 它使用 `ARK_API_KEY/MODEL/BASE_URL` 和 `init_chat_model`，绕过主客户端的超时、熔断、TraceStore 与模型角色记录。
6. **当前 query rewrite 的 LLM 能力实际上未接入主推荐。** `package_builder` 调用 `rewrite_query(..., use_llm=False)`；保留该模块但宣传其在线作用会产生文档/实现漂移。

### 14.4 V3 生成式模型职责矩阵

V3 应统一为 `ModelGateway`（可在现有 `llm_gateway.py` 上演进），调用必须声明 role、输入 DTO、最大 token、超时、允许失败策略和是否可影响业务状态。

| V3 role | 是否默认调用 | 可读取 | 可输出/影响 | 明确禁止 |
| --- | --- | --- | --- | --- |
| `route` | 可选，规则不确定时 | `RouterContext` | 4-action name、operation/mode、候选约束 | 商品事实、价格、库存、绕过 action schema |
| `requirement_extract` | 仅复杂需求/图片线索时 | 原句、规则初稿、受限附件线索 | 候选 Requirement patch | 未验证 brand/ID/价格成为硬事实 |
| `vision_extract` | 有图片时按需 | 图片、用户问题 | OCR/类目/型号/视觉属性候选 | 声称目录中不存在的商品、库存或价格 |
| `reference_disambiguate` | 仅多候选指代 | 服务端已限域候选摘要 | candidate ID 或 `ambiguous` | 选择集合外 ID；自动执行购物车操作 |
| `explain` | 可选 | 最终 fact-checked cards/plan/evidence | 解释、取舍、澄清话术 | 改卡片、添加事实、改约束 |
| `general_chat` | 可选 | 原句 + 极小能力上下文 | 闲聊文本 | 调商品检索、写 session 约束 |

明确移除 `brand_exclusion_filter`、`cart_auto_select` 这类把 LLM 当最终业务判定器的 role。所有模型输出先通过 Pydantic schema、枚举/ID/brand dictionary 验证，再与 server-side `CandidateScope` 求交。

建议单次请求预算：普通文字推荐最多 `route + requirement_extract + explain` 三次；有图片时加一次 `vision_extract`；商品信息查询默认零次或一次表述调用；完整装机不因生成式文案重复调用 solver。预算耗尽时，立即走规则/模板，不影响正确性。

### 14.5 外部 embedding 在当前链路中的真实作用

#### A. 离线索引阶段

```text
catalog products
  -> product_chunks（当前为 profile/SKU/FAQ/review 拼接块）
  -> EmbeddingService.get_all_embeddings
       -> dense: 外部 DashScope/OpenAI-compatible/local provider
       -> sparse: 本地 BM25 统计与向量
  -> MilvusWriter.insert
       -> dense_embedding + sparse_embedding + 部分 metadata
```

本次实际执行的 `scripts/rebuild_product_vector_index.py --dry-run` 显示当前配置为：DashScope provider、`text-embedding-v4`、1024 维；dry-run 共构造 884 个商品文本块。外部 embedding 的作用是把每块商品文本映射为稠密向量，供之后的语义相似召回；它**不生成商品、不解析意图、不判断库存或价格**。

#### B. 在线文本检索阶段

```text
Requirement query / query variant
  -> EmbeddingService.get_embeddings([query])       # 外部 dense embedding
  -> EmbeddingService.get_sparse_embedding(query)   # 本地 BM25 sparse vector
  -> Milvus hybrid_search (dense + sparse RRF)
  -> evidence chunks by product_id
  -> 仅作为候选/证据加分，最终仍与 catalog gate 交集
```

当前 `retrieval.py` 对每个 `ComponentCategory`、每个 query variant 都会调用一次外部 dense embedding；若 query expansion 打开，base/step-back/HyDE 最多三份 query 都会增加外部 embedding 调用。Milvus hybrid 失败会尝试 dense-only；embedding 自身失败/超时则整段检索降级为结构化目录评分。

#### C. 图像“embedding”不是外部语义模型

`image_retrieval.py` 使用本地 `PixelImageEmbeddingService`，以颜色直方图、亮度、纹理、边缘和长宽比生成 61 维向量，写到 JSON 文件。它适合离线可测的粗略颜色/构图相似，不具备“识别某型号/理解服装风格”的 CLIP 级语义能力。视觉 LLM 的 OCR/属性提取与这个本地图像向量是两条不同链路。

### 14.6 V3 embedding 与 rerank 的职责边界

1. **Dense embedding 保留，但放在 CandidateScope 之后。** 先从 catalog 计算可用 product IDs；再用经过验证的 metadata 做 Milvus pre-filter；命中回来的 product IDs 仍强制与 `allowed_product_ids` 求交。
2. **BM25 保留为本地精确匹配通道。** 型号、SKU、专有规格不应只依赖语义向量。BM25 词表与索引版本要随 catalog manifest 管理，避免增量写失败后漂移。
3. **按商品聚合，而非按 chunk 直接推荐。** 一个商品的多条 SKU/FAQ/review chunk 只能共同提供证据，不能占据 top-K 多个席位。
4. **可选 reranker 只能重排 hard-gated 商品。** 当前可配置的 HTTP reranker 接收 chunk 文本并在 RAG postprocess 中运行；V3 若保留，应只输入 top-N 商品摘要/关键证据，超时直接使用规则排序。
5. **HyDE/step-back 默认关闭。** 假设文档可能引入商品目录并无的属性；如未来验证有效，只允许扩展语义 query，绝不能生成或修改 filter、品牌排除、价格/库存条件。
6. **索引版本必须绑定 embedding 版本。** `embedding_provider/model/dim`、chunk schema、catalog version、BM25 stats 都写入 manifest。模型或维度改变必须重建 collection，不可向旧 collection 追加。

### 14.7 V3 外部模型的故障与成本验收

| 场景 | 预期行为 |
| --- | --- |
| 文本 LLM 全部不可用 | 规则 Router + 规则 Requirement + catalog gate + 结构化/BM25 排序仍返回正确商品；文案退模板 |
| DashScope embedding 不可用 | 不调用 Milvus 或跳过语义证据，catalog/BM25/规则候选仍遵守全部硬约束 |
| 视觉 LLM 不可用 | 说明图片已收到但需用户补充文字；不虚构 OCR/型号 |
| reranker 超时 | 使用已有商品规则分数；不重试阻塞主请求 |
| query expansion LLM 失败 | 只使用 base query；不影响 brand/price/stock filter |
| 任一模型 JSON 非法 | schema reject + 明确 fallback；不得把原始模型文本当 action/constraint 执行 |
| 单请求超过模型预算 | 跳过后续可选文案调用，保留事实结果并记录 TraceStore |

验收指标除模型可用率、P95 延迟和 token/请求外，还必须有：每个 model role 的调用数、成功/超时/JSON-invalid/fallback 率、模型输出被验证拒绝率、embedding 检索对最终 top-K 的实际贡献，以及硬约束泄漏率（无论外部模型是否可用均为 0）。
