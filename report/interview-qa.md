# MallMind 电商 AI Agent 项目 -- 面试问答手册

> 适用场景：华为 2012 实验室技术面试（Agent 方向）
> 项目代码路径：`D:\github\tripmind\trad_rag`
> 最后更新：2026-06-13

---

## 目录

- [Category 1: RAG 架构与检索](#category-1-rag-架构与检索)
- [Category 2: Agent 设计与工具编排](#category-2-agent-设计与工具编排)
- [Category 3: LLM 调用与幻觉防治](#category-3-llm-调用与幻觉防治)
- [Category 4: 多轮对话与状态管理](#category-4-多轮对话与状态管理)
- [Category 5: 工程实践与系统设计](#category-5-工程实践与系统设计)
- [Category 6: 深度追问与挑战性问题](#category-6-深度追问与挑战性问题)

---

## Category 1: RAG 架构与检索

### Q1: 什么是 RAG？你的项目为什么使用 RAG 而不是纯 LLM？

**参考答案：**

RAG（Retrieval-Augmented Generation）是"检索增强生成"的缩写。核心思想是在 LLM 生成回答之前，先从外部知识库中检索相关文档片段作为上下文证据（evidence），注入到 prompt 中，让 LLM 基于真实数据生成回答。

我的项目 MallMind 是一个电商导购系统，使用 RAG 有三个核心原因：

第一，**防止幻觉**。纯 LLM 会编造不存在的商品、虚假价格和库存信息。我们的系统要求所有推荐结果必须来自本地商品库中的真实上架商品，RAG 提供了"grounding"（接地）机制，确保推荐结果有据可查。

第二，**实时性**。商品库中的价格、库存、SKU 配置是动态的，而 LLM 的训练数据有截止日期。通过 RAG，每次推荐都能获取最新的商品数据。

第三，**可控性和可解释性**。我们的评分模块 `scorer.py` 中的 `score_product()` 函数基于 7 个维度（场景匹配、属性匹配、价格适配、口碑、库存、SKU、详情质量）做可解释评分，每个维度都有明确的权重和得分原因。这比让 LLM 黑箱输出推荐结果要可控得多。

具体实现上，我们的 RAG 层是**可选的证据增强层**，而非强依赖。即使 Milvus 向量数据库不可用，系统仍可通过结构化筛选 + 确定性评分来推荐商品。这个设计在 `retrieval.py` 的 `EvidenceRetriever.retrieve()` 方法中体现得很清楚——所有 Milvus 异常都被捕获并降级，返回的 `RetrievalEvidence` 会标记 `status="unavailable"` 或 `status="failed"`，下游评分模块据此决定是否使用 evidence boost。

**追问预警：**
1. 你的 RAG 和传统的"先检索再生成"范式有什么区别？为什么说你的 RAG 是"可选证据层"？
2. 如果 Milvus 挂了，你的推荐质量会下降多少？有没有量化数据？

---

### Q2: 你的向量检索是怎么工作的？使用了什么 embedding 模型？

**参考答案：**

我们的向量检索基于 Milvus 向量数据库，embedding 模型使用的是 `BAAI/bge-m3`（默认配置），向量维度 1024。这个模型的特点是同时支持 dense embedding（稠密向量）和 sparse embedding（稀疏向量，即 BM25），这也是我们能做混合检索的基础。

embedding 服务封装在 `rag/ingestion/embedding.py` 文件中。`LocalEmbeddingProvider` 类使用 HuggingFace 的 `HuggingFaceEmbeddings` 加载本地模型，调用 `embed_documents()` 做批量向量化。同时我们也实现了 `OpenAICompatibleEmbeddingProvider`，可以对接 DashScope 等远程 embedding API，通过环境变量 `EMBEDDING_PROVIDER` 切换。

稀疏向量方面，我们在 `embedding.py` 中自己实现了一套 BM25 算法，包括词频统计、倒排索引、IDF 计算，并将词表和 df（document frequency）持久化到 `data/bm25_state.json`，支持增量更新。`get_sparse_embedding()` 方法返回的是 `SparseEmbedding` 对象（包含 indices 和 values），可以直接传入 Milvus 的 hybrid search API。

检索时，在 `retrieval.py` 的 `_retrieve_variant()` 方法中，我们先调用 `embedding_service.get_embeddings([query])` 获取稠密向量，再调用 `embedding_service.get_sparse_embedding(query)` 获取稀疏向量，然后调用 `manager.hybrid_retrieve()` 做混合检索。如果混合检索失败，会降级到纯稠密检索 `manager.dense_retrieve()`。

**追问预警：**
1. bge-m3 模型相比于其他 embedding 模型（如 text-embedding-ada-002）有什么优势？
2. 你的 BM25 是自己实现的还是用 Milvus 内置的？为什么这样选择？

---

### Q3: 请解释你的混合检索策略（向量 + BM25 + RRF）。

**参考答案：**

我们的混合检索分为两个层面：

**第一个层面是 Milvus 内部的 hybrid search。** Milvus 2.x 原生支持同时传入 dense embedding 和 sparse embedding，在服务端对两路召回结果做融合。我们在 `retrieval_fusion.py` 的 `_vector_recall()` 方法中调用 `manager.hybrid_retrieve(dense_embedding=..., sparse_embedding=..., top_k=...)` 来执行。如果 hybrid search 失败（比如 sparse index 未建好），会自动降级到 `dense_retrieve()` 纯稠密检索。

**第二个层面是候选列表的 RRF（Reciprocal Rank Fusion）融合。** 这是更关键的一层。在 `retrieval_fusion.py` 的 `_rrf_fuse()` 函数中，我们将**规则筛选链产出的候选列表**（rule_filtered）和**向量检索召回的候选列表**（vector_products）做 RRF 融合。

RRF 的公式是：`score(d) = Σ weight_i / (k + rank_i(d))`，其中 k 默认 60（通过 `RRF_K` 环境变量配置），`weight_i` 是各通道的权重。规则筛选通道权重 `RULE_FILTER_WEIGHT=0.6`，向量检索通道权重 `VECTOR_RECALL_WEIGHT=0.4`。

设计上的关键考量是：**向量检索是"加性"的（additive），它补充但永远不替代确定性筛选。** 这在 `retrieval_fusion.py` 开头的 design principles 注释中有明确说明。如果向量检索全部失败，`fuse_candidates()` 函数直接返回 rule_filtered 列表不变，状态标记为 `"vector_empty"` 或 `"disabled"`。

融合结果 `FusionResult` 会记录详细的 trace 信息，包括 `overlap_ids`（两路都命中的）、`rule_only_ids`（仅规则命中的）、`vector_only_ids`（仅向量命中的），以及每个商品的 RRF 得分，便于调试和观测。

**追问预警：**
1. RRF 中 k=60 是怎么选的？调过吗？不同 k 值对结果有什么影响？
2. 为什么规则筛选的权重（0.6）比向量检索（0.4）高？

---

### Q4: 当用户的查询很模糊时，你怎么处理检索？

**参考答案：**

模糊查询是我们系统中非常常见的场景，我们通过多层机制来应对：

**第一层：查询改写（Query Rewriting）。** `query_rewriter.py` 中的 `rewrite_query()` 函数实现了"规则优先、LLM 兜底"的改写策略。对于多轮对话中的追问，如"还有吗"、"便宜点的"、"白色的"，规则改写器会从 `session.current` 中继承品类、品牌、预算等上下文，拼接到当前查询中。例如用户先说"推荐蓝牙耳机"，然后说"便宜点的"，改写后变成"蓝牙耳机 便宜点的"。

**第二层：查询扩展（Query Expansion）。** 在 `retrieval.py` 的 `_build_query_variants()` 方法中，我们为每个检索目标生成最多 3 个 query variant：
- `base`：基础查询，直接拼接用户需求字段
- `step_back`：step-back 扩展，从具体问题抽象出更宽泛的检索词
- `hyde`：HyDE（Hypothetical Document Embeddings），让 LLM 先生成一个"假设的理想商品描述"，用这个描述去做向量检索

这三种变体通过 `QUERY_EXPANSION_ENABLED` 环境变量控制是否启用。

**第三层：需求解析中的追问机制。** 在 `recommendation_pipeline.py` 的 `parse_requirement()` 中，如果 LLM 解析发现用户需求不够明确（如缺少品类、预算），会在 `RequirementSpec.clarification_question` 中生成一个追问，如"预算上限大概是多少？"。这个追问会通过 SSE 事件推送给前端。

**第四层：评分层的容错。** 即使检索结果不够精准，`scorer.py` 的 7 维度评分会根据商品的实际属性重新排序，把不相关的商品排到后面。

**追问预警：**
1. HyDE 扩展的延迟是多少？对用户体验有影响吗？
2. 如果改写后查询反而偏离了用户意图怎么办？有没有保护机制？

---

### Q5: 你的商品数据是怎么做 chunking 的？

**参考答案：**

我们的商品数据 chunking 策略在 `rag/ingestion/product_chunks.py` 和 `rag/storage/parent_chunk_store.py` 中实现，采用的是**层级式 chunking（Hierarchical Chunking）**。

每个商品会生成多个层级的 chunk：
- **Level 1（商品概要）**：包含 title、brand、category、base_price 等核心字段，一个商品一条
- **Level 2（属性/描述块）**：把 description、tags、FAQ 等长文本字段分别拆分成独立 chunk
- **Level 3（叶子节点）**：最细粒度的拆分，比如单个 SKU 配置、单个评价

检索时，我们通过 Milvus 的 filter 表达式 `chunk_level == 3`（由 `LEAF_RETRIEVE_LEVEL` 环境变量控制）来定位到最细粒度的 chunk，这样检索结果更精准。同时每个 chunk 都带有 `product_id`、`category`、`brand` 等元数据字段，用于 Milvus 的 boolean filter。

这种层级设计的好处是：检索时命中细粒度 chunk（比如某个 SKU 的配置信息），但返回给用户的是完整的商品卡片（通过 `product_id` 关联回 `catalog` 中的完整 `ApiProduct` 对象）。在 `retrieval.py` 的 `_compact_hit()` 函数中可以看到，hit 会被压缩为只保留 `product_id`、`filename`、`chunk_type`、`score`、`text`（截断到 500 字符）等关键字段。

此外，我们在 Milvus collection 中启用了 dynamic fields（动态字段），这样商品的 `category`、`brand` 等字段可以直接写入 Milvus 的元数据中，支持 filter 表达式过滤，而不需要在应用层做后过滤。

**追问预警：**
1. 为什么选择 chunk_level=3 而不是 level 1 做检索？有什么实验数据支持？
2. 如果商品描述很长，chunk 之间的上下文关系怎么保持？

---

### Q6: 你如何评估检索质量？

**参考答案：**

我们有多层评估机制：

**离线评估：** 项目中有专门的评估脚本 `scripts/eval_retrieval.py`，可以对检索管线做 recall@K、precision@K 的定量评估。具体做法是准备一组标注好的 query-product 对（即"这个查询应该命中哪些商品"），然后跑检索管线看命中率。

**在线 trace：** 每次检索都会生成详细的 trace 信息。在 `RetrievalEvidence.to_trace()` 方法中，会输出 embedding_model、embedding_dim、milvus_collection、raw_hit_count、postprocess 前后的 chunk 数量、matched_product_ids 等完整信息。`FusionResult.to_trace()` 还会记录 RRF 融合统计——两路各召回多少、重叠多少、最终融合多少。

**消融实验：** 我们有 `scripts/eval_full_chain_ablation.py` 和 `scripts/eval_model_chain_ablation.py`，可以做端到端的消融实验，对比"仅规则筛选"、"仅向量检索"、"混合检索+RRF"等不同配置下的推荐质量。

**事实校验层：** `recommendation_pipeline.py` 中的 `fact_check_result()` 函数会在推荐结果返回前做最终校验——检查每个 `product_id` 是否存在于真实商品库、价格偏差是否超过 30% 等。如果 failure_rate 超过 50%，会标记 `degraded=True`。这个事实校验结果可以作为检索/推荐质量的代理指标。

**测试覆盖：** 我们有 21 个测试用例、136 轮对话全部通过，覆盖了各种检索场景。

**追问预警：**
1. recall@K 中 K 取多少？你的检索 recall 大约是什么水平？
2. 有没有做过向量检索 vs 纯规则筛选的 A/B 测试？结果怎样？

---

### Q7: RRF 融合时，如果规则筛选和向量检索的结果完全不重叠怎么办？

**参考答案：**

这在实际使用中确实会发生，我们的处理方式体现在 `_rrf_fuse()` 函数的逻辑中：

首先，完全不重叠不是错误。RRF 的设计初衷就是融合不同排序信号。不重叠的情况下，每个商品只从一个通道获得 RRF 分数，排序完全由权重和排名决定。由于规则筛选权重 0.6 > 向量检索权重 0.4，规则筛选出来的商品在同等排名下天然排在前面。

其次，在 `FusionResult` 中会记录 `overlap_ids`、`rule_only_ids`、`vector_only_ids` 三个集合。如果 overlap 为 0，trace 中可以清楚看到，这对后续的可观测性和调试很有价值。

第三，实际场景中完全不重叠比较少见，因为我们的规则筛选和向量检索用的是同一份商品库（`catalog_products`），向量检索的结果会通过 `product_id` 映射回 catalog，如果该 product_id 不存在于 catalog 中会被丢弃。

最后，如果向量检索完全为空（Milvus 不可用或无匹配），`fuse_candidates()` 直接返回 rule_filtered 列表，状态标记为 `"vector_empty"`，系统退化为纯规则筛选模式，不影响可用性。

**追问预警：**
1. 有没有考虑过在 RRF 之外使用其他融合算法（如 Borda count）？
2. 向量检索引入的"噪声"候选会不会拉低整体推荐质量？

---

### Q8: 你们的检索后处理（postprocess）做了什么？

**参考答案：**

检索后处理在 `retrieval.py` 的 `_apply_rag_postprocess()` 方法中实现，调用 `rag/utils/retrieval_postprocess.py` 中的两个核心函数：

**第一步是 reranking（重排序）。** `_rerank_documents()` 对 Milvus 返回的原始 hit 做精排。Milvus 的 hybrid search 返回的结果是基于向量相似度的粗排，reranker 会用更精细的模型（或规则）重新计算 query-document 的相关性分数。

**第二步是 auto-merging（自动合并）。** `_auto_merge_documents()` 将来自同一 `product_id` 的多个 chunk 合并成一条更完整的文档。比如一个商品可能在 level 3 有 3 个 chunk（概要、SKU 配置、用户评价），如果都被检索到了，合并后能给评分模块提供更完整的信息。

后处理是完全可选的，通过 `RAG_POSTPROCESS_ENABLED` 环境变量控制。如果后处理失败（比如 reranker 服务不可用），会直接返回原始 hits 的 top_k 截断结果，不影响主流程。这个错误会被记录在 trace 的 `rag_postprocess_error` 字段中。

**追问预警：**
1. reranker 用的是什么模型？为什么不直接用 reranker 的分数而还要做 RRF？
2. auto-merge 的策略是什么？简单拼接还是有更复杂的逻辑？

---

## Category 2: Agent 设计与工具编排

### Q9: 什么是 AI Agent？你的系统为什么算是一个 Agent？

**参考答案：**

AI Agent 的核心定义是：一个能够**感知环境、做出决策、采取行动**并根据反馈调整的智能体。与传统 chatbot 的区别在于 Agent 具备：(1) 工具调用能力，(2) 规划和决策能力，(3) 状态记忆，(4) 反馈闭环。

MallMind 满足以上所有特征：

**工具调用能力：** 我们定义了 8 个工具处理器（`tool_handlers.py`），包括 `recommend`（商品推荐）、`compare`（商品对比）、`pc_build`（装机方案）、`cart`（购物车 CRUD）、`general_chat`（闲聊）、`parameter_query`（参数查询）、`sku_detail`（SKU 配置查询）、`price_comparison`（价格比较）。每个工具都有明确的输入 schema 和执行逻辑。

**规划和决策能力：** `tool_router.py` 中的 `route_shopping_tool_call()` 函数实现了"LLM-first + 本地规则 fallback"的双通道路由。LLM 路由器接收用户消息和 session 上下文，输出工具选择和参数提取。如果 LLM 不可用，14 步决策树（`local_route_tool_call()`）基于关键词匹配和状态推断选择工具。

**状态记忆：** `session_state.py` 中的 `ShoppingSession` 包含 5 个分层子状态（ConversationState、RecommendationState、CartState、PCBuildState、ObservabilityState），跨多轮对话保持上下文。

**反馈闭环：** 购物车操作有 plan+confirm 模式，推荐后有事实校验层，工具路由有争议检测（LLM vs 本地规则不一致时的降级策略）。

**追问预警：**
1. 你的 Agent 和 ReAct 范式有什么区别？
2. 你的 Agent 有"规划"能力吗？还只是简单的工具路由？

---

### Q10: 你的工具路由架构是怎样的？LLM-first + 规则 fallback 是怎么工作的？

**参考答案：**

工具路由是系统的核心调度层，实现在 `tool_router.py` 的 `route_shopping_tool_call()` 函数中。

**工作流程：**

1. **本地规则先执行（always）：** 函数一进来就调用 `local_route_tool_call(message, session)` 计算本地规则的路由结果，存在 `local` 变量中。这不仅作为 fallback，还作为后续争议检测的参照基线。

2. **检查 LLM 可用性：** 通过 `_router_llm_globally_enabled()` 检查全局开关（`MALLMIND_LLM_ENABLED` 环境变量），通过 `_router_llm_circuit_open()` 检查熔断器状态。

3. **LLM 路由尝试：** 如果 LLM 可用，调用 `try_llm_route_tool_call()`，构建 router prompt（`build_router_messages()`），通过 `OpenAICompatibleChatClient.chat_json_with_report()` 让 LLM 输出结构化 JSON，再用 Pydantic 的 `RoutedToolCall.model_validate()` 校验。

4. **融合决策：**
   - LLM 成功 -> 使用 LLM 结果，`router_final_source="llm"`
   - LLM 失败（超时/JSON 无效/网络错误）-> 使用本地规则结果，`router_final_source="rules_fallback"`
   - LLM 禁用 -> 使用本地规则结果，`router_final_source="rules"`

5. **路由输出校验（`validate_tool_call()`）：** 即使 LLM 成功返回，还要经过校验层：白名单检查（工具名是否在 `ALLOWED_TOOL_NAMES` 中）、值域裁剪（价格不超过 50 万）、争议检测（LLM 判断和规则判断不一致时，检查是否有闲聊信号或购物车意图被误判）。

特别值得一提的是**购物车意图保护**机制：当用户说"把手机加入购物车"时，LLM 常常因为有"手机"这个商品关键词而误判为 `recommend_shopping_products`。我们在 `validate_tool_call()` 中加了 `_has_cart_intent()` 检测，如果消息包含明确的购物车操作词，无论 LLM 如何路由，强制纠正为 `apply_cart_instruction`。

**追问预警：**
1. LLM 路由和本地规则路由的一致率大约是多少？争议率高吗？
2. 为什么不直接用 LLM 路由？规则 fallback 的存在意义是什么？

---

### Q11: 14 步决策树是怎么设计的？

**参考答案：**

14 步决策树在 `local_route_tool_call()` 函数中实现，是一个从高优先级到低优先级的规则链。每一步检测特定的意图信号，命中就立即返回对应的工具调用：

1. **购物车意图检测**（`_has_cart_intent()`）：检查 CART_STRONG_TERMS（如"购物车"、"加购"、"删除"、"清空"）+ 正则匹配"把...加入车"模式。如果同时有推荐意图（如"推荐耳机并加到购物车"），则不在此步返回，留给 LLM 处理组合意图。

2. **PC 装机 followup 检测**（`is_pc_build_followup()`）：检查 session 中是否有 `pc_build_history`，消息中是否有"上一套"、"换成"、"升级"等调整词。

3. **商品对比检测**（`_looks_like_compare_request()`）：检查 COMPARE_TERMS（"对比"、"比较"、"哪个好"、"vs"）。

4. **SKU 查询检测**（`_has_sku_detail_intent()`）：正则匹配 "12+256和16+512差多少"等模式。

5. **价格比较检测**（`_has_price_comparison_intent()`）：检查"比官网便宜"、"值不值"等。

6. **参数查询检测**（`_has_parameter_query_intent()`）：检查"功耗多少"、"支持NFC"等。

7. **PC 整机意图检测**（`_has_pc_intent()`）：这是最复杂的规则，检查 PC_BUILD_STRONG_TERMS（"整机"、"装机"、"配电脑"等），以及多信号组合（配件词命中数 + 预算值 + 使用场景 + 设备词）。

8. **PC 主题延续检测**：当前话题是 pc_build 且 `_looks_like_pc_followup()` 命中。

9. **追问检测**（`resolve_followup_message()`）：如果上一轮有推荐结果且当前消息是商品细节追问。

10. **普通商品品类检测**（`detect_normal_product_category()`）：通过 NORMAL_PRODUCT_TERMS 和 NORMAL_PRODUCT_ALIASES 匹配。

11. **单个 PC 配件检测**（`_has_single_pc_part_intent()`）：如"推荐一款显卡"。

12. **商品搜索意图检测**（`_has_product_query_intent()`）：搜索词 + 场景词 + 事实查询词。

13. **闲聊检测**（`_is_general_chat()`）：系统说明、身份问题、无购物信号。

14. **默认兜底**：`recommend_shopping_products`，默认进入普通商品推荐。

每一步都通过 `score_local_routes()` 计算评分并附加在返回结果中（`route_scores`），供后续观测和调试使用。

**追问预警：**
1. 决策树的步骤顺序是怎么确定的？为什么购物车在第 1 步而 PC 整机在第 7 步？
2. 如果两条规则同时命中（比如"对比这两个耳机并加入购物车"），怎么处理？

---

### Q12: 如果要给系统添加一个新工具，需要做哪些工作？

**参考答案：**

添加新工具需要修改以下几个位置，这也是我们系统的已知技术债之一——工具的注册不够"插拔化"：

1. **在 `ALLOWED_TOOL_NAMES` 集合中注册工具名**（`tool_router.py` 第 26 行），否则校验层会拒绝。

2. **在 `TOOL_SCHEMAS_FOR_PROMPT` 列表中添加工具的 JSON Schema**（`tool_router.py` 第 47 行），这个 schema 会注入到 LLM 路由器的 system prompt 中，告诉 LLM 这个工具的名称、描述和参数格式。

3. **在 `LOCAL_ROUTE_NAMES` 列表中添加**（`tool_router.py` 第 36 行），使其参与本地规则的评分。

4. **在 `local_route_tool_call()` 的决策树中添加对应的检测步骤**，包括定义意图检测词表和检测函数。

5. **在 `tool_handlers.py` 中实现 handler 函数**，遵循 generator pattern（`yield sse_event(...)`），处理业务逻辑并通过 SSE 事件流式返回结果。

6. **在 API 路由层（`rag/api/routes/chat.py`）中注册分发逻辑**，将路由结果中的工具名映射到对应的 handler。

这个过程确实比较繁琐。如果时间充裕，我会设计一个 `ToolRegistry` 类，让每个工具以装饰器方式注册，自动注入到 schema 列表和路由规则中，类似 Flask 的 `@app.route()` 模式。

**追问预警：**
1. 你的工具路由支持动态工具发现吗？还是需要重新部署？
2. 8 个工具之间有没有依赖关系？比如"推荐后自动加购物车"是怎么实现的？

---

### Q13: 你的系统和 LangChain Agent 有什么区别？

**参考答案：**

主要区别在四个方面：

**1. 工具路由方式不同。** LangChain Agent 通常是 ReAct 模式——LLM 思考（Thought）-> 选择工具（Action）-> 观察结果（Observation）-> 循环。我们的系统是**单步路由**——一次 LLM 调用决定工具，然后直接执行。没有"思考-行动-观察"的循环，这降低了延迟（少了一轮 LLM 调用），但也意味着不支持复杂的多步推理。

**2. 确定性保障不同。** LangChain Agent 完全依赖 LLM 做工具选择，如果 LLM 判断错误就直接走错路径。我们的系统有**本地规则兜底**和**争议检测**机制——即使 LLM 路由出错，规则层也能纠正。特别是购物车意图保护这类硬规则，确保关键操作不会被 LLM 误判。

**3. 幻觉控制更严格。** LangChain 的 Agent 在生成回答时是自由文本，没有事实验证。我们在推荐结果返回前有 `fact_check_result()` 层，会验证每个 `product_id` 是否真实存在、价格偏差是否在 30% 以内。

**4. 领域特化 vs 通用框架。** LangChain 是通用 Agent 框架，我们针对电商场景做了大量特化——7 维度评分、12 步筛选链、PC 装机兼容性检查、购物车 CRUD 等，这些是 LangChain 开箱即用做不到的。

当然，如果从零开始，用 LangChain 或 LangGraph 做原型可能更快。但我们的选择在当时是合理的——对延迟、可控性和幻觉防治有更严格的要求。

**追问预警：**
1. 如果你的系统需要支持多步工具调用（比如"先推荐耳机，再对比，然后把好的加入购物车"），架构要怎么改？
2. 你有没有考虑过用 LangGraph 的状态图来管理工具编排？

---

### Q14: 工具选择有歧义时怎么处理？

**参考答案：**

工具选择歧义是我们重点处理的场景，有三层防护：

**第一层：评分排序机制。** `score_local_routes()` 函数为每个工具计算一个置信度分数。比如购物车意图 +0.75 分，普通商品品类 +0.55 分，PC 整机 +0.75 分。如果多个工具同时有分数，取最高分的。分数和排名信息通过 `route_scores` 附加到结果中，供观测使用。

**第二层：LLM 路由仲裁。** 本地规则不确定时（比如分数差距小），LLM 路由器能看到更丰富的上下文（session 的累积状态、最近 3 轮查询、购物车内容、当前话题类型），做出更准确的判断。LLM 路由的 prompt 中包含详细的工具选择规则和示例。

**第三层：校验层争议解决。** `validate_tool_call()` 中的争议检测机制会检查 LLM 和本地规则是否一致。如果不一致，根据预定义的降级策略决定：
- LLM 说 `recommend` 但消息包含闲聊信号 -> 降级为本地规则的 `general_chat`
- LLM 说 `general_chat` 但消息包含明确购物信号 -> 升级为本地规则的推荐工具
- LLM 没有识别购物车意图但规则检测到了 -> 强制纠正为 `apply_cart_instruction`

每次争议解决都会在 trace 中记录 `validation.issues` 和 `validation.conflict`，便于后续分析 LLM 路由的准确率并迭代 prompt。

**追问预警：**
1. 你有没有统计过 LLM 路由 vs 规则路由的一致率？
2. 争议检测中的降级策略会不会引入新的错误？

---

### Q15: 你的 8 个工具处理器中，最复杂的是哪个？为什么？

**参考答案：**

最复杂的是 `handle_recommend()`，它在 `tool_handlers.py` 中实现，是系统的主推荐链路。

复杂度来自它需要编排多个子系统的协作：

1. **输入预处理**：调用 `preprocess_user_input()` 清洗文本，处理多模态输入（图片通过 `retrieve_image_evidence()` 做图片相似召回，音频做转录）。

2. **推荐管线调用**：通过 `call_recommendation_fn()` 调用 `recommend_shopping_products()`，后者内部又涉及需求解析（规则 + LLM）、结构化筛选（12 步 filter chain）、RRF 融合、7 维度评分、证据增强等。

3. **事实校验**：调用 `fact_check_result()` 验证推荐结果中的每个 product_id 和价格。

4. **状态更新**：调用 `remember_recommendation()` 保存推荐结果到 session，`update_topic_memory()` 更新话题记忆。如果是 PC 配件场景，还要调用 `_apply_pc_component_update()` 更新当前装机方案。

5. **响应生成**：调用 `generate_natural_response()` 生成自然语言回复（LLM-first，模板 fallback）。

6. **SSE 事件流**：整个过程中通过 10+ 种 SSE 事件类型（progress、intent_route、delta、product_cards、candidate_scope、comparison_table、follow_up_questions、result、cart、done）逐步推送进度和结果。

7. **组合意图处理**：如果 LLM 路由带了 `action="add_to_cart"`，推荐完成后还要自动把排名第一的商品加入购物车。

**追问预警：**
1. 这么复杂的流程，如果中间某一步失败了怎么办？有统一的错误处理吗？
2. handle_recommend 的端到端延迟大约是多少？瓶颈在哪？

---

## Category 3: LLM 调用与幻觉防治

### Q16: 你怎么防止 LLM 在商品推荐中产生幻觉？

**参考答案：**

幻觉防治是我们系统的核心设计目标之一，贯穿整个推荐链路：

**1. 源头控制：不用 LLM 生成商品数据。** 所有推荐商品都来自本地商品库（通过 `product_loader.py` 加载的 `ProductCatalog`），LLM 只负责**理解需求**（提取品类、预算、偏好）和**路由工具选择**，绝不负责"想出"商品。

**2. 结构化筛选优先于语义理解。** `structured_filter.py` 中的 12 步筛选链用确定性规则（品类、库存、品牌、价格、关键词匹配等）过滤商品，这些规则不受 LLM 影响。

**3. 事实校验层。** `fact_check_result()` 在最终返回前做三重校验：
- product_id 是否存在于真实商品库（不存在则移除）
- 价格偏差是否超过 30%（超过则自动修正为真实价格）
- 库存状态检查（sold_out 商品标记但不移除）

如果 failure_rate 超过 50%，整个结果标记为 `degraded`。

**4. 响应生成的约束。** `response_generator.py` 中的 `generate_natural_response()` 在调用 LLM 生成自然语言回复时，会将商品卡片数据（标题、价格、品牌）作为结构化输入传入 prompt，明确要求 LLM "基于以下商品数据生成回复，不要编造数据"。如果 LLM 生成失败，会 fallback 到模板化回复（`_OPENING_VARIANTS`、`_LEAD_VARIANTS` 等变体库随机选择）。

**5. LLM 输出格式校验。** 所有需要 LLM 输出 JSON 的场景（路由、需求解析、guidance），都用 Pydantic 做 schema 校验（如 `RoutedToolCall.model_validate()`），不符合 schema 的输出会被拒绝并降级。

**追问预警：**
1. 价格偏差阈值 30% 是怎么定的？为什么不更严格？
2. 如果 LLM 在 natural response 中说"这款手机支持5G"但商品数据里没有这个信息，你怎么检测？

---

### Q17: 解释你的事实校验机制（fact_check）的详细实现。

**参考答案：**

事实校验在 `recommendation_pipeline.py` 的 `fact_check_result()` 函数中实现，位于推荐结果生成之后、返回给前端之前。

校验逻辑如下：

```python
_PRICE_DEVIATION_THRESHOLD = 0.30  # 30%
_FACT_FAILURE_THRESHOLD = 0.50     # 50%
```

对 `product_cards` 列表中的每张卡片：

1. **product_id 存在性校验**：通过 `catalog.get(pid)` 查找商品。如果找不到，记录 `issue="not_found_in_catalog"`，该卡片从结果中移除，`removed` 计数 +1。

2. **价格一致性校验**：比较卡片中的 `price` 和 catalog 中商品的 `base_price`。如果偏差超过 30%（`abs(card_price - real_price) / real_price > 0.30`），自动将卡片价格修正为真实价格，并记录 `_original_price`（修正前的错误价格）。`fixed` 计数 +1。

3. **库存状态检查**：如果商品的 `stock_status` 是 `"sold_out"` 或 `"out_of_stock"`，记录 issue 但不移除卡片（仅标记）。

最终计算 `failure_rate = (removed + fixed) / total`。如果 failure_rate <= 50% 且 removed == 0，`passed=True`；否则 `passed=False`，并可能标记 `degraded=True`。

在 `tool_handlers.py` 的 `handle_recommend()` 中，校验结果会写入 `session.last_fact_check_status`，并通过 SSE 事件推送给前端。

这个设计的核心思想是：**宁可降级也不传递虚假信息**。价格自动修正保证了用户看到的价格一定是商品库中的真实售价，product_id 校验保证了推荐的一定是真实存在的商品。

**追问预警：**
1. 30% 的价格偏差阈值在实际运行中有被触发过吗？通常是什么原因导致的？
2. 如果 fact_check 发现所有商品都有问题（failure_rate > 50%），系统会怎么响应？

---

### Q18: 当 LLM 不可用时，你的系统怎么保证功能正常？

**参考答案：**

我们的系统设计了全面的 LLM 降级策略，确保在 LLM 完全不可用时仍能完成核心推荐功能：

**路由层降级：** `route_shopping_tool_call()` 中，如果 LLM 路由失败（超时、JSON 无效、网络错误），自动降级到 `local_route_tool_call()` 的 14 步规则决策树。规则路由完全基于关键词匹配和 session 状态推断，不依赖任何 LLM 调用。

**需求解析降级：** `parse_requirement()` 中，如果 LLM 解析失败，降级到 `parse_requirement_rule_based()`，通过正则表达式和关键词匹配提取品类、预算、品牌、偏好等结构化信息。

**响应生成降级：** `generate_natural_response()` 中，如果 LLM 生成失败，fallback 到模板变体库（`_OPENING_VARIANTS`、`_LEAD_VARIANTS`、`_NO_MATCH_VARIANTS` 等），通过随机选择模板生成多样化回复。

**闲聊回复降级：** `handle_general_chat()` 中的 `_generate_general_chat_fallback()` 提供了基于规则的回复模板，覆盖问候、感谢、告别、非购物问题等场景。

**熔断器保护：** 路由层有熔断器机制（`_record_router_llm_failure()`），当 60 秒内失败次数达到阈值（默认 5 次），自动将 LLM 标记为不可用（`_ROUTER_LLM_DISABLED_UNTIL`），后续请求直接使用规则路由，不再尝试调用 LLM，避免无效的超时等待。

**核心推荐管线完全不依赖 LLM：** `structured_filter.py` 的筛选链、`scorer.py` 的评分、`retrieval_fusion.py` 的 RRF 融合都是纯确定性算法。LLM 在推荐管线中只是"锦上添花"（增强需求理解和生成解释），不是必要条件。

**追问预警：**
1. 降级模式下推荐质量和正常模式差多少？有量化数据吗？
2. 你的熔断器用的是哪种经典模式（closed/open/half-open）？

---

### Q19: 不同场景下 LLM 的 temperature 设置有什么区别？为什么？

**参考答案：**

我们为不同场景设置了不同的 temperature，核心原则是**需要确定性的场景用低温，需要多样性的场景用较高温**：

- **工具路由（router）**：`temperature=0`。路由要求确定性输出，同样的输入应该路由到同样的工具。温度高了会导致路由不稳定。代码在 `try_llm_route_tool_call()` 中：`temperature=0`。

- **需求解析（parse）**：`temperature=0.1`。需求解析需要从用户文本中提取结构化信息，基本是信息抽取任务，需要高确定性。略高于 0 是为了避免极端情况下的重复输出。代码在 `parse_requirement()` 中。

- **导购解释（guidance）**：`temperature=0.2`。guidance 需要生成购买建议和追问，允许一定的多样性，但仍需要基于事实。代码在 `enrich_recommendation_result()` 中。

- **闲聊回复（general_chat）**：`temperature=0.7`。闲聊需要自然多样的回复，避免每次"你好"都回复一模一样的话。代码在 `_generate_general_chat_llm_response()` 中。

- **查询改写（query_rewrite）**：`temperature=0.1`。改写需要准确反映用户意图，不能有创造性偏差。

- **证据解释（explanation）**：使用 guidance 的同一模型配置，`temperature` 约 0.2。

此外，`llm_gateway.py` 中的 `_CallerConfig` 为 9 种调用场景分别配置了 model_kind（fast/main）、temperature、timeout、max_tokens 和 max_concurrency，虽然目前尚未完全迁移到 gateway 调用方式，但设计蓝图已经就位。

**追问预警：**
1. 路由 temperature=0 会不会导致 LLM 输出过于死板？有没有边界情况？
2. 你对比过不同 temperature 下的路由准确率吗？

---

### Q20: LLM Gateway 的设计思路是什么？为什么还没迁移？

**参考答案：**

LLM Gateway（`llm_gateway.py`）是我设计的一个统一 LLM 调用编排层，目标是将散落在各处的 `OpenAICompatibleChatClient()` 实例化替换为一个集中管理的注册表。

**设计要点：**

1. **9 种调用场景**，每种有独立的 `_CallerConfig`：router、parse、guidance、response、explanation、rewrite、general_chat、filter、attachment。每种配置了 model_kind（fast/main）、temperature、timeout、max_tokens、max_concurrency。

2. **并发控制**：每个 caller 场景有独立的 `_ConcurrencyLimiter`（基于 `threading.Semaphore`），防止某个场景的高并发耗尽全局 LLM 配额。

3. **独立熔断器**：每个 caller 有自己的 `_CircuitState`，跟踪连续失败次数，支持 closed -> open -> half-open 三态转换。

4. **一行调用**：`LLMGateway.call("router", messages)` 自动选择正确的模型、超时、并发限制和熔断状态。

**为什么还没迁移：** 当前所有 LLM 调用仍使用直接实例化 `OpenAICompatibleChatClient()` 的方式，这在功能上是等价的，只是代码组织上不够优雅。没迁移的原因是：(1) 直接调用的方式已经经过了充分测试，运行稳定；(2) 迁移涉及多个调用点的修改，需要逐一切换和回归测试；(3) Gateway 的核心价值（独立并发控制、独立熔断）在当前用户量级下不是瓶颈。这是一个典型的"技术债"——我们知道更好的做法，但在优先级排序上选择了其他功能迭代。

**追问预警：**
1. 如果要迁移，你会怎么做？一次性切换还是逐步迁移？
2. Gateway 的熔断器和 tool_router 中已有的熔断器会冲突吗？

---

## Category 4: 多轮对话与状态管理

### Q21: 你怎么管理多轮对话的上下文？

**参考答案：**

多轮对话状态管理是系统的核心基础设施，在 `session_state.py` 中实现。

**Session 结构：** `ShoppingSession` 是主状态类，包含 5 个分层子状态 dataclass：

1. **`ConversationState`**：对话级状态，包括 `session_id`、`messages`（消息历史）、`recent_queries`（最近 3 轮查询及其路由参数）、`chat_topic`（当前话题标签）。

2. **`RecommendationState`**：推荐级状态，包括 `current`（累积路由参数——品类、品牌、预算上限等，每轮更新）、`last_goal`（上一轮查询目标）、`last_result`（上一轮推荐结果，含商品卡片）。

3. **`CartState`**：购物车状态，包括 `cart`（Dict[product_id, CartItem]）和 `pending_cart_action`（待确认的购物车操作计划，有 60 秒 TTL）。

4. **`PCBuildState`**：PC 装机状态，包括 `pc_build_history`（历史装机方案列表）和 `current_pc_build`（当前方案，包含 8 个组件角色的 product_id/price）。

5. **`ObservabilityState`**：可观测状态，包括 `topic_memory`（话题记忆——topic_type、slots、source）、`topic_history`（话题变更历史）、`llm_call_log`（LLM 调用日志）、`last_fact_check_status`。

**状态更新机制：** 每次对话 turn 结束后，`save_session()` 持久化 session。`record_turn()` 在 `session_context.py` 中记录 turn 摘要。`merge_requirement_memory()` 将本轮的路由参数合并到 `current` 中，实现跨轮次的约束累积（比如第一轮说"推荐耳机"，第二轮说"预算 500 以内"，`current` 会同时包含品类和预算）。

**Session 生命周期：** TTL 默认 7200 秒（2 小时），内存中最多保留 500 个 session，每 60 秒清理过期 session。

**追问预警：**
1. 为什么把状态分成 5 个子状态而不是一个大 flat 结构？
2. Session 持久化用的是什么方案？文件？Redis？

---

### Q22: 多轮对话中的代词消解是怎么实现的？

**参考答案：**

代词消解在 `query_rewriter.py` 中实现，是查询改写的核心功能之一。

**规则消解（`_resolve_pronouns()`）：** 通过正则匹配常见代词模式，然后从 `last_result` 中提取上一轮推荐的商品标题来替换：

- "这个怎么样" / "这款好吗" -> 替换为 `[上一轮第一个商品标题] 怎么样`
- "这两个对比" -> 替换为 `[标题1]和[标题2] 对比`
- "它好吗" -> 替换为 `[商品标题] 好吗`
- "第一个怎么样" -> 通过 `_cn_to_int()` 把中文数字转成 int，从商品标题列表中取对应索引的标题

**属性继承（`_inherit_attributes()`）：** 当用户只说"白色的"、"大一点"这种纯属性追问时，`_ATTRIBUTE_ONLY_RE` 正则匹配后，从 `session_current` 中继承品类、品牌和预算约束，拼接成完整查询。例如上一轮是"推荐华为蓝牙耳机"，用户说"白色的"，改写为"蓝牙耳机 华为 白色"。

**追问扩展（`_expand_followup()`）：** "还有吗"、"换一批"等短追问，会从 `last_goal` 或 `session_current` 中提取上一轮查询的基线，拼接为"蓝牙耳机 还有吗"。

**LLM 兜底改写（`_llm_rewrite()`）：** 对于规则无法处理的复杂改写（如含约束修改信号"换成"、"不要"的短句，或 10 字以内含未消解代词的追问），调用 LLM 做上下文改写。prompt 中注入了上一轮查询、当前品类/品牌/预算、推荐商品标题等上下文，要求 LLM 输出一行完整的搜索查询。

整个改写器遵循"规则优先、LLM 兜底"的设计范式，兼顾了低延迟（规则改写几乎零延迟）和高覆盖（LLM 处理复杂情况）。

**追问预警：**
1. 代词消解会不会出错？比如"这个"指的不是上一轮推荐的商品而是更早的？
2. LLM 改写的延迟对用户体验有影响吗？

---

### Q23: 怎么检测用户切换了话题？

**参考答案：**

话题切换检测分布在多个模块中：

**工具路由层：** `tool_router.py` 的 LLM 路由 prompt 中有明确的话题切换判断规则：
- 如果 Accumulated state 显示 PC 构建话题，用户说"换个话题，推荐手机" -> 切换为单品推荐
- 如果当前是商品推荐话题，用户说"配台电脑" -> 切换为 PC 整机方案
- 用户说"不要了"、"算了"、"看看别的" -> 可能是话题切换

**`_is_general_chat()` 函数**中，会检查 `_has_active_shopping_topic(topic)` 和 `_looks_like_short_preference_followup(text)`。如果当前有活跃购物话题但用户消息是短的偏好追问（如"白色的"），则不判为闲聊而是继续购物流程。

**`topic_memory` 机制：** `update_topic_memory()` 在每次工具调用后更新，记录 `topic_type`（如 "normal_product"、"pc_build"、"cart"、"comparison"）和 `slots`（当时的路由参数）。当下一个 turn 的路由结果与当前 topic_type 不一致时，自然完成了话题切换。

**PC 装机 followup 检测：** `is_pc_build_followup()` 和 `is_pc_build_followup()` 通过检查 session 中是否有 `pc_build_history` 和消息中的调整词，判断用户是在继续 PC 装机话题还是已经切换到其他话题。

**`resolve_followup_message()` 中的购物车主题保护：** 当 session 的 topic_type 是 "cart" 时，追问继续走购物车工具，不会因为推荐结果中有商品就强制回到推荐路由。

**追问预警：**
1. 如果用户在 PC 装机话题中突然问"推荐个鼠标"，你的系统怎么处理？
2. 话题切换时，之前的累积状态（current）会被清空吗？

---

### Q24: "删除购物车里的第二个商品"这种查询怎么处理？

**参考答案：**

这种查询涉及购物车操作 + 序数引用，处理流程如下：

**1. 路由阶段：** `_has_cart_intent()` 检测到"删除"（在 `CART_STRONG_TERMS` 中）和"购物车"，路由到 `apply_cart_instruction` 工具。

**2. 工具处理阶段：** `handle_cart_v2()` 被调用，它实现了 plan+confirm 模式。`_resolve_cart_action()` 从消息中推断操作类型为 `"remove"`。

**3. 商品定位阶段：** `_resolve_product_for_cart()` 按优先级尝试：
   - 显式 product_ids（此处无）
   - 名称模糊匹配 `fuzzy_match_cart_item()`（此处无名称）
   - 序数提取 `extract_item_index(message)`：从"第二个"中提取出 index=1（0-based），然后从 `session.cart.keys()` 列表中取 `cart_ids[1]`

**4. 歧义检查：** `_check_cart_ambiguity()` 检查：
   - 序数越界：如果购物车只有 1 个商品但用户说"第二个"，返回追问"购物车里只有 1 个商品，没有第 2 个"
   - 同品类歧义：如果用户说"删掉那个手机"但购物车有两个手机，追问"购物车里有多个手机：华为 Mate60、小米 14，你要操作哪一个？"

**5. 确认模式：** 构建 plan（`_make_plan()`），包含 operation="remove"、product_id、product_title、过期时间（60 秒后），存入 `session.pending_cart_action`。通过 SSE 发送 `cart_confirmation` 事件，前端显示"确认从购物车移除 华为 Mate60？"。

**6. 用户确认：** 用户说"确认"后，系统执行实际的删除操作，更新 session.cart 并通过 `save_session()` 持久化。

**追问预警：**
1. 如果用户说"删掉它"但上下文中"它"指什么不清楚，系统怎么处理？
2. plan+confirm 模式的 60 秒 TTL 过期后会怎样？

---

### Q25: session.current 的累积更新是怎么工作的？

**参考答案：**

`session.current` 是一个 Dict，存储跨轮次累积的路由参数（品类、品牌、预算、catalog_scope 等），由 `session_context.py` 中的 `merge_requirement_memory()` 管理。

工作原理是：每一轮工具路由完成后，路由器提取的参数（如 category、brands、price_max 等）会被合并到 `current` 中。下一轮路由时，LLM 路由器的 user prompt（`_build_router_user_prompt()`）会注入 `Accumulated state: {...}` 信息，让 LLM 知道之前的约束。

在需求构建阶段（`_requirement_from_args_v2()`），如果本轮路由参数中某个字段为 None（LLM 没有提取到），会从 `session.current` 中继承上一轮的值。如果某个字段为特殊的 `__CLEAR__` 标记，则显式清空（用于用户说"不要品牌限制了"这种场景）。

举个例子：
- 第 1 轮："推荐蓝牙耳机" -> current = {"category": "digital", "sub_category": "蓝牙耳机"}
- 第 2 轮："预算 500 以内" -> LLM 提取 price_max=500，category 为空 -> 从 current 继承 -> requirement 同时包含 sub_category="蓝牙耳机" 和 price_max=500
- 第 3 轮："换成白色的" -> LLM 提取 color preference，category 为空 -> 继续继承

这种累积机制确保了多轮对话中约束不会丢失，同时允许用户逐步细化需求。

**追问预警：**
1. 如果用户第 3 轮说"不要耳机了，看看手机"，累积状态怎么处理？
2. session.current 会不会积累太多噪声？比如第 1 轮的品牌约束在第 5 轮已经不适用了。

---

## Category 5: 工程实践与系统设计

### Q26: 为什么选择 SSE 而不是 WebSocket？

**参考答案：**

选择 SSE（Server-Sent Events）而非 WebSocket 是基于我们的具体场景做的权衡：

**SSE 的优势：**

1. **单向流足够：** 我们的场景是客户端发请求，服务端流式返回进度、结果和商品卡片。不需要服务端主动推送消息到客户端。SSE 天然支持这种"请求-流式响应"模式。

2. **实现简单：** SSE 基于 HTTP，不需要额外的协议升级和连接管理。在 FastAPI 中，我们只需返回一个 `StreamingResponse`，yield SSE 格式的事件字符串即可。`sse.py` 中的 `sse_event()` 函数只有两行代码。

3. **自动重连：** 浏览器的 EventSource API 内置了自动重连机制。如果用 WebSocket，需要自己实现重连逻辑。

4. **与 HTTP 基础设施兼容：** SSE 走标准 HTTP，不需要特殊的负载均衡配置。

**SSE 的劣势及应对：**

1. **单向通信：** 如果需要客户端中途取消请求或发送新信息，SSE 做不到。但我们的场景中，用户如果要修改查询，直接发新请求即可，不需要在流中途插入消息。

2. **连接数限制：** 浏览器对同一域名的 SSE 连接数有限制（通常 6 个）。但我们是短连接（推荐完成后发 `done` 事件关闭），不会长时间占用。

我们有 25 种 SSE 事件类型（delta、progress、product_cards、comparison_table、cart、cart_confirmation、cart_clarification、pc_build_plan、error、done 等），前端根据事件类型做增量渲染。

**追问预警：**
1. 如果用户在推荐流返回过程中想取消，怎么做？
2. 25 种事件类型有没有版本控制？新增事件类型会不会破坏老前端？

---

### Q27: 你的熔断器（Circuit Breaker）是怎么实现的？

**参考答案：**

我们在 LLM 调用层实现了熔断器模式，保护系统免受 LLM 服务不可用时的级联故障。

**实现位置：** `tool_router.py` 中的路由层熔断器，以及 `llm_gateway.py` 中为每个 caller 场景设计的独立熔断器。

**三态模型（以 tool_router 为例）：**

1. **Closed（闭合/正常）：** 正常调用 LLM。每次成功调用，`_record_router_llm_success()` 清空失败记录。

2. **Open（断开/熔断）：** 当 60 秒内失败次数达到阈值（默认 5 次，通过 `RECOMMENDATION_ROUTER_LLM_CIRCUIT_FAILURES` 配置），`_record_router_llm_failure()` 设置 `_ROUTER_LLM_DISABLED_UNTIL = now + cooldown`（默认 30 秒冷却期）。在此期间，`_router_llm_circuit_open()` 返回 True，所有请求直接走规则路由，不再尝试 LLM。

3. **Half-Open（半开/试探）：** 冷却期过后，下一个请求会尝试调用 LLM。如果成功，熔断器重置为 closed（失败记录清空）；如果继续失败，重新进入 open 状态。

**并发安全：** 所有熔断器状态操作都在 `_ROUTER_LLM_CIRCUIT_LOCK`（`threading.Lock()`）保护下进行，防止多线程竞争。

**`llm_gateway.py` 的独立熔断器：** `_CircuitState` dataclass 为每个 caller 维护独立的 `consecutive_failures`、`half_open_until` 和 `state` 字段。这样，路由器的熔断不会影响需求解析的 LLM 调用，反之亦然。

**追问预警：**
1. 熔断器的失败阈值 5 次和冷却期 30 秒是怎么调的？有实验依据吗？
2. 半开状态下如果那个试探请求很慢（比如 15 秒超时），会不会阻塞？

---

### Q28: 并发控制和超时处理是怎么做的？

**参考答案：**

**并发控制：**

1. **路由层并发限制：** `_ROUTER_LLM_SEMAPHORE = threading.BoundedSemaphore(2)`，最多允许 2 个并发的 LLM 路由请求。获取信号量有超时（`RECOMMENDATION_ROUTER_LLM_ACQUIRE_TIMEOUT_SECONDS`，默认 0.5 秒），获取失败直接走规则路由。

2. **Gateway 层独立并发控制（设计态）：** `_ConcurrencyLimiter` 为每个 caller 场景维护独立的 Semaphore，`max_concurrency` 在 `_CallerConfig` 中配置。

**超时处理：**

1. **socket 级超时：** 路由 LLM 的 socket 超时默认 15 秒（`RECOMMENDATION_ROUTER_LLM_SOCKET_TIMEOUT_SECONDS`），可通过环境变量调整。

2. **硬超时（`run_with_hard_timeout()`）：** 在 LLM 调用外包一层线程级超时。函数在子线程中执行，主线程 `join(timeout)` 等待。如果超时，抛出 `TimeoutError`。这确保即使底层 HTTP 库的超时机制失效，也不会无限等待。

3. **各场景独立超时：** 路由 15 秒、需求解析 12 秒、guidance 8 秒、查询改写 5 秒。每个场景根据复杂度和用户等待容忍度单独配置。

4. **Milvus 连接超时：** `MILVUS_CONNECT_TIMEOUT_SECONDS = 0.75`，快速检测 Milvus 是否可达，不可达时立即降级到结构化筛选。

**异常分类处理：** 在 `try_llm_route_tool_call()` 中，不同的异常被分类为不同的 failure reason：`llm_timeout`、`llm_json_invalid`、`network_error`、`llm_provider_error`。这些 reason 记录在 routing_trace 中，便于分析哪种故障最常见。

**追问预警：**
1. `run_with_hard_timeout()` 如果子线程超时了，那个线程会一直跑下去吗？怎么清理？
2. 为什么路由层并发限制只有 2？如果同时有 10 个用户在请求呢？

---

### Q29: 当所有筛选条件把商品全部过滤掉时，你怎么处理？

**参考答案：**

这是"无结果"场景，在 `structured_filter.py` 中通过**优雅降级（graceful degradation）** 机制处理。

12 步筛选链的设计原则是：**当某个硬约束导致所有候选被消除时，只放松那个约束，而不是跳过整个筛选链。** 具体实现：

1. 每一步筛选后检查候选数量。如果某步把候选降到 0，记录该步骤到 `FilterDiagnostics.relaxed_constraints`，并回退到上一步的结果继续。

2. **预算放松：** `budget_relaxation_allowed()` 判断是否允许放松预算。如果严格的 price_max 过滤掉了所有商品，系统会放松预算约束，返回最接近预算的候选，并在 trace 中标记 `budget_filter_strict=False` 和 `budget_gap_reason`。这样用户看到的不是空结果，而是"商品库里没有找到 500 CNY 以内的合适商品，下面给出同类最近备选"。

3. **品牌白名单放松：** 如果 `brands=["华为"]` 但商品库中没有华为品牌的该品类商品，`brand_whitelist_relaxed=True`，系统会推荐其他品牌的候选，并在回复中说明"没有找到华为品牌的在售商品"。

4. **PC 配件约束放松：** `pc_constraint_relaxed=True`，当功耗、尺寸等 PC 特定约束过滤掉所有候选时放松。

5. **前端响应：** 在 `build_chat_delta_lines()` 中，如果 `product_cards` 为空，会根据具体原因生成不同的提示：预算过高/过低、品牌排除过多、品类无数据等，引导用户调整条件。

**追问预警：**
1. 放松约束后推荐出来的商品质量会不会很差？怎么保证？
2. "优雅降级"的决策是在筛选层做的还是在评分层做的？

---

### Q30: 你的测试是怎么做的？21 个测试用例 / 136 轮对话是什么概念？

**参考答案：**

我们的测试是**端到端的多轮对话集成测试**，而不是传统的单元测试。

每个测试用例模拟一个完整的用户购物场景，包含多轮对话（turns）。例如：
- 用例 1："推荐蓝牙耳机" -> "便宜点的" -> "第二个加入购物车" -> "购物车里有什么"（4 轮）
- 用例 2："配一台 8000 元的游戏电脑" -> "CPU 换成 Intel 的" -> "对比一下两个方案"（3 轮）

136 轮意味着平均每个用例约 6.5 轮对话，覆盖了：
- 单轮推荐
- 多轮追问（代词消解、属性继承）
- 话题切换（从推荐切换到购物车、从 PC 装机切换到手机推荐）
- 购物车 CRUD（加购、删除、修改数量、清空）
- PC 装机多轮迭代（生成方案 -> 修改组件 -> 对比方案）
- 边界情况（空购物车操作、序数越界、不存在的商品）

测试通过的标准是：每轮对话的 SSE 事件流中，`done` 事件正常到达、`error` 事件不包含未预期的异常、推荐结果中的 `product_id` 都存在于商品库。

测试框架使用 pytest，配置在 `pytest.ini` 中。测试目录在 `tests/` 下。

**追问预警：**
1. 这些测试是自动化的吗？CI 中跑一次要多久？
2. 有没有做过压力测试？系统能支持多少并发？

---

### Q31: 如果给你更多时间，你最想改进什么？

**参考答案：**

按优先级排列：

1. **消除硬编码值：** 项目中有约 500+ 硬编码值（词表、阈值、超时时间等），分散在代码各处。我会引入一个统一的配置中心（如 YAML 配置文件或数据库配置表），把所有可配置项集中管理，支持热更新。

2. **完成 LLM Gateway 迁移：** 把 `llm_gateway.py` 的设计落地，将所有 LLM 调用切换到 Gateway 模式，实现独立的并发控制、熔断器和调用日志。

3. **工具注册插拔化：** 设计 `ToolRegistry` 类，让工具通过装饰器注册，自动注入到 LLM prompt 的 schema 列表、本地路由的评分系统和 API 路由的分发表中。

4. **引入 A/B 测试框架：** 目前路由策略、评分权重、RRF 参数都是凭经验设定的。我会引入 A/B 测试框架，在线上对关键参数做实验，用数据驱动优化。

5. **增强多步规划能力：** 当前是单步路由，不支持"先推荐 -> 再对比 -> 再加购"的自动链式操作。可以引入类似 LangGraph 的状态图，支持多步任务规划。

6. **实时价格接入：** 当前价格是商品库中的静态数据。接入实时价格 API 后，在最终展示前刷新价格，避免推荐过期价格。

**追问预警：**
1. 500+ 硬编码值这个技术债是怎么产生的？为什么不从一开始就用配置文件？
2. 如果只能改一个，你选哪个？为什么？

---

## Category 6: 深度追问与挑战性问题

### Q32: 如果系统推荐了错误的商品，你怎么调试？

**参考答案：**

我们的系统设计了完善的可观测性基础设施，调试推荐错误有清晰的路径：

**第一步：检查路由决策。** 查看 `routing_trace`，确认工具选择是否正确：
- `router_final_source`：是 LLM 还是 rules？
- `validation.issues`：有没有争议检测触发？
- `route_scores`：各工具的评分是多少？margin 大不大？

**第二步：检查需求解析。** 查看 `requirement_parsing` trace：
- 品类是否正确识别？品牌是否提取正确？预算是否解析正确？
- 如果是 LLM 解析，`llm_parse_used` 为 true，可以看 LLM 的输出是否准确

**第三步：检查筛选链。** 查看 `candidate_scope.by_category` 中的 `FilterDiagnostics`：
- `raw_count`：原始商品数
- 每一步筛选后的数量变化
- `relaxed_constraints`：哪些约束被放松了？

**第四步：检查评分。** 每个推荐商品的 `ScoreBreakdown` 包含 7 个维度的得分、权重、权重调整原因。可以定位到是哪个维度的分数导致了错误排序。

**第五步：检查 RAG 证据。** 查看 `retrieval` trace：
- `retrieved_chunk_count`：检索到多少证据？
- `matched_product_ids`：命中了哪些商品？
- `evidence_boost`：证据加成对最终分数的影响有多大？

**第六步：检查事实校验。** `fact_check` 字段显示有没有 product_id 不存在或价格被修正的情况。

所有这些 trace 信息都通过 SSE 事件和 `result` 事件传递给前端，也可以通过 `ObservabilityState.llm_call_log` 回溯 LLM 调用历史。

**追问预警：**
1. 如果问题出在 embedding 模型本身（语义理解偏差），你怎么发现？
2. 你有没有实际的调试案例可以分享？

---

### Q33: 你的系统和商业购物助手（如淘宝 AI 导购、京东智能客服）相比如何？

**参考答案：**

**我们的优势：**
1. **透明度高：** 7 维度可解释评分、完整的 trace 链路、事实校验层，每一步决策都有据可查。商业系统通常是黑箱。
2. **幻觉控制严格：** 所有推荐商品必须来自真实商品库，价格自动校验。商业系统有时会出现推荐不存在的商品或虚假优惠信息。
3. **多工具协调：** 8 个工具覆盖推荐、对比、装机、购物车、参数查询、SKU 查询、价格比较、闲聊。商业系统通常只有推荐 + 客服。
4. **PC 装机特化：** 8 个组件角色的兼容性检查、多方案对比、迭代修改，这是通用购物助手不具备的。

**我们的劣势：**
1. **数据规模：** 我们的商品库是本地数据集（约百条级别），商业系统有百万级甚至亿级商品。
2. **实时数据：** 我们没有接入实时库存、价格、优惠券。
3. **用户画像：** 我们没有用户历史行为数据，做不到个性化推荐。
4. **模型规模：** 我们使用的是开源小模型或 API 调用，商业系统可能使用自研大模型。
5. **前端体验：** 我们的前端是纯 HTML/CSS/JS，商业系统有成熟的 App 和小程序。

**定位差异：** 我们的系统更像是一个"技术验证原型"，展示了 RAG + Agent 架构在电商场景的可行性，特别是在可控性、可解释性和幻觉防治方面的设计思路。如果要商业化，需要解决数据规模、实时性和个性化这三个核心问题。

**追问预警：**
1. 如果淘宝想采用你的架构，需要做哪些改造？
2. 你觉得 AI 购物助手的核心竞争力是什么？

---

### Q34: 你的 RAG 方法有什么局限性？

**参考答案：**

坦率地说，有以下几个核心局限：

**1. 依赖商品数据质量。** 我们的 RAG 从商品 chunk 中检索证据，如果商品描述不完整、标签不准确、FAQ 质量低，检索出来的证据就没有价值。这本质上是"garbage in, garbage out"的问题。

**2. 无法处理商品库中不存在的商品。** 如果用户要一个我们商品库里没有的商品（比如最新发布的 iPhone），RAG 无法"检索"到不存在的东西。系统会推荐最接近的替代品，但用户可能不满意。

**3. 语义检索的精度问题。** 向量检索基于语义相似度，但"相似"不等于"相关"。例如搜索"适合跑步的耳机"，向量可能召回"跑步鞋"的 chunk，因为它们在语义空间中距离近。我们通过 Milvus 的 category filter 缓解了这个问题，但不能完全消除。

**4. 不支持实时信息。** RAG 的证据来自预索引的 chunk，不包含实时库存、价格变动、促销活动等信息。

**5. 跨品类推荐的局限。** 当用户说"去三亚旅行的一套装备"，需要跨多个品类（防晒霜 + 泳衣 + 墨镜）组合推荐。我们的 RAG 是按品类独立检索的，缺乏跨品类的关联推理能力。虽然 `need_bundle` 标志可以触发多品类检索，但组合逻辑是在评分层做的，不是在检索层。

**6. chunking 粒度问题。** 如果一个商品的多个 chunk 被检索到但内容相互矛盾（比如不同 SKU 的价格差异很大），auto-merge 后可能产生混淆的上下文。

**追问预警：**
1. 如果要支持实时信息，你的架构要怎么改？
2. GraphRAG 能不能解决你的跨品类推荐问题？

---

### Q35: 如果商品库扩大到 10 万条，你的系统需要怎么改？

**参考答案：**

当前系统在百条级别运行良好，扩展到 10 万条需要在以下几个层面做改造：

**1. 筛选链性能优化。** 当前 `structured_filter.py` 对每个品类做线性扫描。10 万条时需要引入索引——按品类、品牌、价格区间建倒排索引，将筛选复杂度从 O(n) 降到 O(k)。可以使用 SQLite 或 Elasticsearch 做结构化索引。

**2. 评分性能优化。** `score_products()` 对所有通过筛选的商品做 7 维度评分。10 万条中如果有 1 万条通过筛选，逐条评分会很慢。解决方案：
- 先用轻量级特征（价格、评分、品牌匹配度）做粗排，只对 top-100 做完整 7 维度评分
- 评分结果缓存（同一查询 + 相同商品，短时间内缓存 score）

**3. Milvus 成为必需。** 当前 Milvus 是可选的，10 万条时向量检索成为必要的召回手段。需要优化 Milvus 的 index 类型（IVF_FLAT -> HNSW），调整 nprobe 参数平衡精度和速度。

**4. 商品加载方式改变。** 当前 `product_loader.py` 可能是一次性加载全部商品到内存。10 万条需要改为分页加载或按需加载，使用数据库查询代替全量内存。

**5. Session 持久化。** 当前 session 存在内存中（`DEFAULT_MAX_IN_MEMORY_SESSIONS = 500`）。10 万商品带来的用户量增长需要切换到 Redis 或数据库存储 session。

**6. 前端渲染优化。** 推荐结果不能一次性返回所有商品，需要分页（当前最多返回 6 张卡片），加载更多走 AJAX 请求。

**追问预警：**
1. 10 万条商品的 embedding 索引要占多少内存？怎么优化？
2. 你的 RRF 融合在 10 万条规模下性能如何？

---

### Q36: 如果 LLM 生成的文本和商品卡片数据矛盾，会发生什么？

**参考答案：**

这是一个重要的场景，我们有防御机制：

**在推荐回复生成阶段：** `generate_natural_response()` 函数将商品卡片数据（标题、价格、品牌等）作为结构化输入传给 LLM，要求 LLM 基于这些数据生成自然语言回复。prompt 中明确要求"不要编造商品数据"。

**但 LLM 仍可能出错：** 比如商品数据中没有"5G"信息，LLM 可能自行补充"这款手机支持5G"。

**防御措施：**

1. **模板 fallback 优先用于关键数据：** 在 `_LEAD_VARIANTS` 等模板中，价格和标题是通过 `{title}` 和 `{price:g}` 格式化填入的，不是 LLM 自由生成的。这保证了核心数据（价格、标题）一定来自商品卡片。

2. **事实校验层只校验结构化数据：** `fact_check_result()` 校验的是 `product_cards` 中的 product_id 和 price，不校验自然语言回复文本。这是当前的一个已知局限。

3. **改进方向：** 可以在响应生成后增加一个"文本事实校验"步骤——从 LLM 生成的文本中提取关键声明（如"支持5G"、"续航24小时"），然后与商品数据交叉验证。这可以通过 NLI（Natural Language Inference）模型或规则匹配实现。

**追问预警：**
1. 你觉得 LLM 生成的回复文本需要事实校验吗？怎么做？
2. 如果用户因为 LLM 的错误描述做了购买决定，责任在谁？

---

### Q37: 如何实现实时价格？

**参考答案：**

当前系统的价格是商品库中的静态 `base_price`，实现实时价格需要以下改造：

**方案一：延迟刷新（推荐）。** 在推荐管线的最后一步（`fact_check_result()` 之后、返回前端之前），对推荐结果中的少量商品（top 6）调用实时价格 API 刷新价格。这样不会影响筛选和评分阶段的性能（它们仍使用静态价格做排序），只在最终展示时更新。

```python
# 伪代码
for card in payload["product_cards"][:6]:
    real_time_price = price_api.get(card["product_id"])
    if real_time_price:
        card["price"] = real_time_price
        card["_price_source"] = "real_time"
```

**方案二：缓存层。** 引入价格缓存（Redis），TTL 设为 5-15 分钟。推荐时使用缓存价格。缓存 miss 时异步触发价格更新。这样不会增加推荐延迟，但价格可能有几分钟的延迟。

**方案三：价格变更推送。** 商品系统通过消息队列（Kafka/RocketMQ）推送价格变更事件，我们的系统订阅这些事件并更新本地缓存。这是最实时的方案，但需要与商品系统做集成。

**需要额外考虑的问题：**
- 实时价格变动可能导致排序结果与展示不一致（排序时用 500 元，展示时变成 600 元）。需要在刷新价格后重新排序，或在 UI 上标注"价格已更新"。
- 实时价格 API 的可用性和延迟。如果 API 超时，需要 fallback 到静态价格。
- 价格缓存一致性：多实例部署时如何保证缓存一致。

**追问预警：**
1. 实时价格刷新会增加多少延迟？
2. 如果实时价格和静态价格差异很大，你的评分需要重新计算吗？

---

### Q38: PC 装机方案的兼容性检查是怎么做的？

**参考答案：**

PC 装机是系统中最复杂的特化功能之一，涉及 8 个组件角色：CPU、GPU、主板、内存、SSD、电源、机箱、散热器。

兼容性检查在 `pc_compatibility.py` 中实现，主要检查：

1. **CPU-主板兼容性：** CPU 的 socket 类型（如 LGA1700、AM5）必须与主板的 socket 匹配。例如 Intel 13代 Core 需要 LGA1700 主板，AMD Ryzen 7000 需要 AM5 主板。

2. **内存-主板兼容性：** DDR4 内存不能插在只支持 DDR5 的主板上，反之亦然。

3. **电源功率充足性：** `cost_estimator.py` 中估算整机功耗（CPU TDP + GPU TDP + 其他组件固定功耗），确保电源额定功率 > 总功耗 * 1.3（留 30% 余量）。

4. **机箱-主板尺寸兼容性：** ATX 主板需要 ATX 或更大尺寸的机箱，ITX 主板可以用在任何机箱中。

5. **散热器-CPU 兼容性：** 散热器的 TDP 散热能力需要 >= CPU TDP。

装机流程在 `pc_session_flow.py` 的 `build_pc_plan_for_message()` 中编排，`pc_types.py` 定义了组件类型系统。装机结果保存在 `session.pc_build_history` 中，支持多方案对比（`compare_pc_build_plans()`）。

用户可以通过多轮对话迭代修改方案，如"CPU 换成 Intel 的"、"内存升级到 32G"、"预算加到一万"。每次修改都会重新检查兼容性。

**追问预警：**
1. 兼容性数据库是怎么维护的？硬编码还是数据驱动？
2. 如果用户要求的组合在预算内无法满足，系统怎么处理？

---

### Q39: 你的系统中有多少处 LLM 调用？它们分别做什么？

**参考答案：**

系统中有 9 个 LLM 调用场景，在 `llm_gateway.py` 中定义为 `_CallerConfig`：

1. **router（工具路由）**：根据用户消息和 session 上下文选择工具并提取参数。fast_model, temperature=0, timeout=15s。

2. **parse（需求解析）**：将自然语言购物需求解析为结构化 RequirementSpec。fast_model, temperature=0.1, timeout=12s。仅在规则解析不够时才调用（`should_use_llm_requirement_parse()` 判断）。

3. **guidance（导购解释）**：基于推荐结果生成购买建议、追问和优化建议。main_model, temperature=0.2, timeout=8s。

4. **response（响应生成）**：生成自然语言回复文本（替代硬编码模板）。main_model, temperature 可变。

5. **explanation（证据解释）**：基于 RAG 证据和评分结果生成可解释推荐理由。main_model, temperature=0.2。

6. **rewrite（查询改写）**：多轮对话中的代词消解和上下文补全。fast_model, temperature=0.1, timeout=5s。

7. **general_chat（闲聊回复）**：生成闲聊回复。main_model, temperature=0.7, timeout 较短。

8. **filter（结构化筛选辅助）**：当规则无法判断某些约束时，用 LLM 做辅助判断（较少使用）。

9. **attachment（附件分析）**：处理图片上传，用 VLM（视觉语言模型）分析图片内容，提取颜色、品类等特征用于检索。

当前所有这些调用都直接使用 `OpenAICompatibleChatClient`，尚未迁移到 LLM Gateway。每次调用都通过 `run_with_hard_timeout()` 包装，确保不会无限等待。

**追问预警：**
1. 一次完整的推荐请求最多会触发几次 LLM 调用？延迟怎么算？
2. 这些 LLM 调用之间有依赖关系吗？能并行化吗？

---

### Q40: 你的项目中最有技术挑战的一个问题是什么？你是怎么解决的？

**参考答案：**

最有技术挑战的是**多轮对话中的意图继承与话题切换的平衡**。

**问题描述：** 用户在多轮对话中，有时是在细化上一轮的需求（"便宜点的"、"白色的"、"换成华为的"），有时是在切换话题（"不要耳机了，看看手机"）。系统需要准确区分这两种情况：如果该继承时没继承，用户需要重复之前的所有约束；如果该切换时没切换，系统会带着旧的品类/品牌约束去推荐新品类。

**解决过程：**

1. **最初的方案**是用累积状态 `session.current`：每轮路由参数合并到 current，下一轮 LLM 路由时注入。但这导致了"状态污染"——用户在第 5 轮切换品类时，第 1 轮的 sub_category 仍在 current 中，导致 LLM 被旧约束干扰。

2. **改进方案**是在 `query_rewriter.py` 中引入 `_needs_llm_rewrite()` 检测，当消息包含约束修改信号（"换成"、"不要"、"去掉"）时，用 LLM 改写查询，让 LLM 决定哪些旧约束保留、哪些丢弃。

3. **进一步优化**是在 `_build_router_user_prompt()` 中，对 PC 配件场景特殊处理——不注入 `sub_category` 和 `must_have_terms`，因为这些是组件级约束，每轮应该由 LLM 根据当前查询重新判断。

4. **购物车意图保护**（`validate_tool_call()` 中的 `_has_cart_intent()`）也是一个关键补丁——LLM 经常把"把手机加入购物车"误判为推荐，因为消息中包含商品关键词。通过硬规则覆盖 LLM 的判断，解决了这个高频错误。

这个挑战的本质是：**在保持上下文连续性和允许话题灵活性之间找到平衡。** 没有完美的规则可以覆盖所有情况，所以我们采用了"规则 + LLM + 校验"的三层架构，让每层互补。

**追问预警：**
1. 有没有用户的真实对话案例，展示这个问题是怎么发生的？
2. 你认为这个问题有根本性的解决方案吗？还是需要一直"打补丁"？

---

### Q41: 你的系统中 ~500+ 硬编码值具体是什么？你打算怎么治理？

**参考答案：**

这 500+ 硬编码值主要分布在以下几类：

1. **意图检测词表**（约占 40%）：`tool_router.py` 中的 `PC_STRONG_TERMS`、`PC_BUILD_STRONG_TERMS_ZH`、`CART_STRONG_TERMS`、`COMPARE_TERMS`、`GENERAL_CHAT_TERMS`、`PARAMETER_QUERY_TERMS`、`PRICE_COMPARISON_TERMS`、`SKU_DETAIL_PATTERNS` 等。每个列表从几个到几十个词不等。

2. **品类/品牌映射**（约占 20%）：`NORMAL_PRODUCT_TERMS`、`NORMAL_PRODUCT_ALIASES`、`PC_PART_CATEGORIES`、`PC_PART_CATEGORY_TERMS_ZH`、`BRAND_OR_PRODUCT_TERMS` 等。

3. **评分权重和阈值**（约占 15%）：`BASE_WEIGHTS`（7 维度权重）、`RRF_K=60`、`VECTOR_RECALL_WEIGHT=0.4`、`RULE_FILTER_WEIGHT=0.6`、`_PRICE_DEVIATION_THRESHOLD=0.30`、`_FACT_FAILURE_THRESHOLD=0.50` 等。

4. **超时和并发配置**（约占 10%）：各种 `_TIMEOUT_SECONDS` 常量、`_MAX_CONCURRENCY` 值、TTL 值。

5. **模板文本**（约占 15%）：`response_generator.py` 中的变体库（`_OPENING_VARIANTS`、`_LEAD_VARIANTS` 等）、`_generate_general_chat_fallback()` 中的模板文本。

**治理方案：**

1. **配置中心化：** 将所有词表、权重、阈值、超时值抽取到一个 `config.yaml` 文件中，启动时加载。词表变更不需要改代码和重新部署。

2. **动态词表：** 意图检测词表改为从数据库或远程配置中心加载，支持运营人员通过管理后台添加新词，无需开发介入。

3. **常量文件：** 在过渡期，至少将所有硬编码值抽取到一个 `constants.py` 文件中，集中管理，而不是散落在几十个文件中。

4. **A/B 测试驱动的权重调优：** 评分权重和 RRF 参数通过 A/B 测试框架动态调整，而不是凭经验硬编码。

**追问预警：**
1. 为什么不在项目一开始就用配置文件？
2. 硬编码值最多的词表部分，如果让你用模型自动挖掘关键词，你会怎么做？

---

### Q42: 你的多模态能力（图片上传 + VLM 分析、音频转录）是怎么实现的？

**参考答案：**

多模态处理在 `input_preprocessor.py` 和 `image_retrieval.py` 中实现。

**图片处理流程：**

1. **上传：** 用户通过前端上传图片，API 层（`rag/api/routes/attachments.py`）接收并存储到 `.uploads/` 目录。

2. **VLM 分析：** `preprocess_user_input()` 调用 VLM（视觉语言模型，通过 `OpenAICompatibleChatClient` 的 vision 能力）分析图片内容，提取关键特征——颜色、品类、材质、物体等。例如一张红色连衣裙的照片，VLM 可能输出"红色连衣裙，A字裙，短袖，适合夏季"。

3. **图片相似召回：** `retrieve_image_evidence()` 使用图片 embedding（或 VLM 提取的文本特征）在商品库中做相似检索，找到视觉上相似的商品。这个 evidence 会和文本检索的 evidence 合并。

4. **文本特征注入：** VLM 提取的文本特征被注入到用户的查询中，作为额外的检索约束。比如用户说"找同款"，系统会用 VLM 提取的"红色连衣裙 A字裙"作为检索关键词。

**音频处理流程：**

音频通过语音转录 API（如 Whisper）转换为文本，然后走标准的文本处理流程。转录结果通过 `preprocess_user_input()` 与文本消息合并。

**多模态 trace：** 所有多模态分析结果都记录在 `result.trace["attachments"]` 和 `result.trace["attachment_analysis"]` 中，包括分析状态、提取的特征、使用的模型等。

**追问预警：**
1. VLM 用的是什么模型？图片分析的延迟是多少？
2. 如果图片质量很差（模糊、光线暗），VLM 分析失败了怎么办？

---

## 附录：关键文件索引

| 文件路径 | 职责 |
|---------|------|
| `rag/recommendation/tool_router.py` | 工具路由（LLM + 14步决策树 + 校验层） |
| `rag/recommendation/tool_handlers.py` | 8个工具处理器实现 |
| `rag/recommendation/retrieval.py` | Milvus 证据检索 |
| `rag/recommendation/retrieval_fusion.py` | RRF 混合检索融合 |
| `rag/recommendation/structured_filter.py` | 12步确定性筛选链 |
| `rag/recommendation/scorer.py` | 7维度可解释评分 |
| `rag/recommendation/recommendation_pipeline.py` | 推荐管线主流程 + 事实校验 |
| `rag/recommendation/session_state.py` | Session 状态管理（5个子状态） |
| `rag/recommendation/query_rewriter.py` | 查询改写（代词消解 + 属性继承） |
| `rag/recommendation/response_generator.py` | 自然语言响应生成 |
| `rag/recommendation/llm_client.py` | LLM 调用客户端 |
| `rag/recommendation/llm_gateway.py` | LLM Gateway 设计（未完全迁移） |
| `rag/ingestion/embedding.py` | Embedding 服务（dense + sparse/BM25） |
| `rag/storage/milvus_client.py` | Milvus 向量数据库客户端 |
| `rag/api/sse.py` | SSE 事件格式化 |
| `rag/recommendation/pc_build.py` | PC 装机方案生成 |
| `rag/recommendation/pc_compatibility.py` | PC 组件兼容性检查 |
| `rag/recommendation/input_preprocessor.py` | 输入预处理（多模态） |
| `rag/recommendation/image_retrieval.py` | 图片相似检索 |
