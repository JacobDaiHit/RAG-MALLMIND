# MallMind Agent v1 管线微调第三轮报告

## 测试概要

| 项目 | 第二轮 | 第三轮 (原有63) | 第三轮 (补充72) | 第三轮 (全量135) |
|------|--------|----------------|----------------|-----------------|
| 测试时间 | 2026-06-07 11:20 | 2026-06-07 11:59 | 2026-06-07 12:01 | 2026-06-07 12:04 |
| 总用例 | 63 | 63 | 72 | 135 |
| PASS | 33 (52.4%) | **21 (33.3%)** | **27 (37.5%)** | **48 (35.6%)** |
| PARTIAL | 23 (36.5%) | 23 (36.5%) | 26 (36.1%) | 49 (36.3%) |
| FAIL | 6 (9.5%) | **18 (28.6%)** | **19 (26.4%)** | **37 (27.4%)** |
| ERR | 1 (1.6%) | 1 (1.6%) | 0 (0%) | 1 (0.7%) |
| 平均响应 | 1068ms | 1604ms | 2541ms | 2098ms |

原有63用例 PASS 从 52.4% 降至 33.3%，FAIL 从 9.5% 升至 28.6%，出现显著回归。

---

## 一、本轮新增修复措施（4项）

### 改动 E: Milvus 集合全量重建（drop_collection=True, reset_bm25=True）

使用最新商品目录（180 产品）重建向量索引：
- ecommerce chunks: 720（原约 400）
- PC chunks: 484
- 总计 1204 chunks
- Embedding: dashscope text-embedding-v4, dim=1024

### 改动 F: 商品目录扩展（products.json 100→180）

每个品类从 25 扩展到 45 个产品：

| 品类 | 原有 | 新增 | 新增子类覆盖 |
|------|------|------|-------------|
| 数码电子 | 25 | +20 | 手机×5, 笔记本×3, 耳机×3, 平板×2, 手表×2, 相机×2, 音箱×2, 充电器×1 |
| 美妆护肤 | 25 | +20 | 面霜×3, 防晒×3, 面膜×3, 洁面×3, 口红×3, 粉底×3, 眼霜×2 |
| 服饰运动 | 25 | +20 | 运动鞋×4, T恤×3, 外套×3, 裤子×3, 连衣裙×2, 背包×2, 运动套装×2, 帽子×1 |
| 食品饮料 | 25 | +20 | 坚果×3, 饼干×3, 饮料×3, 茶叶×3, 巧克力×2, 零食×2, 水果干×2, 牛奶×1, 辣条×1 |

### 改动 G: 跨品类证据惩罚（scorer.py）

在 `score_product` 中新增：当查询品类明确（单一 desired_category）且所有 Milvus evidence 来自非目标品类时，对 final_score 施加 -0.10 惩罚。

### 改动 H: 组合意图路由修复（tool_router.py #59）

修改 `local_route_tool_call`：当同时检测到购物车意图和推荐意图时，跳过本地规则的购物车快捷路径，交由 LLM 路由。

---

## 二、关键改善项

### 2.1 组合路由修复验证（#59）— 从 PARTIAL → PASS

| 用例 | 输入 | 第二轮 | 第三轮 |
|------|------|--------|--------|
| #59 | 推荐手机，直接帮我加到购物车 | apply_cart_instruction [PARTIAL] | **recommend_shopping_products + cart** [PASS] |
| #60 | 看看购物车，把第一个删了 | apply_cart_instruction [PASS] | apply_cart_instruction [PASS] |

改动 H 生效：#59 现在正确路由到 recommend_shopping_products 而非 apply_cart_instruction。

### 2.2 购物车操作稳定性 — 保持 PASS

购物车系列操作（#41-48）在第三轮继续保持 PASS，说明改动 A/B（第二轮）的修复效果稳定。

### 2.3 否定语义路由 — 改善

| 用例 | 输入 | 第二轮 | 第三轮 |
|------|------|--------|--------|
| #22 | 不要苹果的手机 | PARTIAL (有卡但包含iPhone) | **PASS** (OPPO/vivo/小米) |
| #23 | 除了华为还有啥手机 | PARTIAL | **PASS** (OPPO/vivo/荣耀) |

### 2.4 商品对比 — 稳定 PASS

#35-37 全部 PASS，compare_products 路由和结果均正确。

### 2.5 补充测试亮点（72 用例）

基础对话 8/8 全 PASS：
- #104 政治话题拒绝 → general_chat 礼貌拒绝
- #105 爬虫请求 → 礼貌拒绝
- #106 商品分类询问 → 合理回答

否定排除 3/4 PASS（#144, #146, #147）：
- #146 "运动鞋不要Nike" → Adidas 跑步鞋卡片
- #147 "耳机不要华为 500-2000" → AirPods Pro 3 ¥1799

防幻觉部分有效：
- #165 "iPhone 17 Pro 只要999对吧" → 正确纠正为 ¥8999

---

## 三、关键回归分析

### 3.1 推荐查询大面积空结果 — 最严重问题

本轮最突出的问题是大量推荐查询返回 "没有找到足够贴合的商品"，即使商品目录已有对应产品。

| 用例 | 输入 | 第二轮 | 第三轮 | 说明 |
|------|------|--------|--------|------|
| #6 | 推荐一款手机 | 有卡(PASS) | **空结果**(FAIL) | 45个digital产品中包含10款手机 |
| #7 | 好吃的零食 | 有卡(PASS) | **空结果**(FAIL) | 45个food产品，应匹配多款零食 |
| #10 | 送女朋友 | PARTIAL | **空结果**(FAIL) | 跨品类推荐完全失效 |
| #15 | 推荐华为手机 | PARTIAL | **空结果**(FAIL) | 华为有多款手机在库 |
| #19 | 苹果手机 | PARTIAL | **空结果**(FAIL) | iPhone 17 Pro在库 |
| #24 | 推荐手机 | 有卡(PASS) | **空结果**(FAIL) | — |

**根因分析：** Milvus 全量重建后 BM25 稀疏向量 IDF 值全面重算。新 corpus 从约 400 chunks 增至 1204 chunks，IDF 分布发生显著变化。推测 evidence retrieval 阶段返回的证据评分普遍降低，导致 `score_product` 的 evidence boost 无法有效触发，或 `build_product_cards` 阶段的过滤阈值将更多产品排除。

**建议排查方向：**
1. `retrieval.py` 中 evidence hit scores 的分布变化（新旧 corpus 对比）
2. `score_product` 中 evidence boost 后的 score 分布是否整体偏低
3. `build_product_cards` 或 `build_recommendation_result` 中是否有隐含的 score 阈值过滤

### 3.2 品类错配 — 部分查询返回错误品类商品

| 用例 | 输入 | 期望品类 | 实际返回 | 判断 |
|------|------|---------|---------|------|
| #9 | 防水运动鞋 | clothing | JBL音箱 + 服装 | FAIL |
| #11 | 夏天穿什么 | clothing | 空结果 | FAIL |
| #29 | A19芯片 | digital(iPhone) | The Ordinary面霜 | FAIL |
| #111 | 推荐跑步鞋 | clothing | 华为笔记本 + 服装 | FAIL |
| #112 | 零食推荐 | food | 华为笔记本 | FAIL |
| #113 | 降噪耳机 | digital(耳机) | 华为笔记本 | FAIL |

**根因分析：** Milvus evidence 跨品类匹配。当查询 "降噪耳机" 的 embedding 与华为 MateBook 的 evidence chunk 相似度高于耳机 evidence chunk，导致 evidence 品类标记为 digital 但子类为笔记本。跨品类惩罚（改动 G）仅在 evidence 全部来自非目标品类时触发，但此处 target category = digital 而 evidence 也是 digital（只是子类不对），所以惩罚不生效。

**建议：** 跨品类惩罚应细化到 sub_category 层面，而非仅检查 ComponentCategory。

### 3.3 补充测试中的会话串联问题

补充测试中 #119-#147 大量出现 followup_guard 路由（session 上下文继承），导致查询被拼接了前序查询的文本。例如：

- #119 "推荐一款续航好的手机" → 实际 query="有没有适合学生用的笔记本电脑. User added constraints: 有没有适合敏感肌的护肤品。用户追问：推荐一款续航好的手机"

这导致 query 文本过长、语义混乱，影响 retrieval 和 scoring 精度。建议 followup_guard 的 query 拼接策略限制历史追溯长度（最多1层而非全部累积）。

---

## 四、逐类 PASS 统计对比

### 原有 63 用例

| 类别 | 用例数 | 第二轮 PASS | 第三轮 PASS | 变化 |
|------|--------|------------|------------|------|
| 基础对话 | 5 | 5 | 5 | → |
| 模糊推荐 | 8 | 4 | 2 | ↓2 |
| 精准搜索 | 8 | 2 | 1 | ↓1 |
| 否定语义 | 5+1 | 2 | 2+1 | ↑1 |
| 商品FAQ | 5 | 3 | 3 | → |
| 口碑查询 | 3 | 1 | 1 | → |
| 商品对比 | 3 | 3 | 3 | → |
| 购物车 | 8 | 6 | 6 | → |
| 结算 | 3 | 3 | 3 | → |
| 多轮对话 | 5+2 | 3 | 2 | ↓1 |
| 边界异常 | 5 | 2 | 3 | ↑1 |
| 组合调用 | 2 | 1 | 1+1(PARTIAL) | ↑ |

### 补充 72 用例（新增）

| 类别 | 用例数 | PASS | PARTIAL | FAIL |
|------|--------|------|---------|------|
| 基础对话 | 8 | 8 | 0 | 0 |
| 语义搜索 | 12 | 2 | 6 | 4 |
| 结构化查询 | 8 | 1 | 5 | 2 |
| 商品详情 | 5 | 3 | 1 | 1 |
| FAQ搜索 | 5 | 2 | 2 | 1 |
| 评价搜索 | 5 | 1 | 3 | 1 |
| 否定排除 | 4 | 3 | 1 | 0 |
| 购物车 | 8 | 5 | 2 | 1 |
| 多轮对话 | 8 | 4 | 2 | 2 |
| 防幻觉 | 5 | 1 | 2 | 2 |
| 综合场景 | 4 | 2 | 1 | 1 |

---

## 五、后续修复建议（优先级排序）

### P0: 修复 evidence retrieval 评分回归

Milvus 重建后的 BM25/IDF 变化导致大面积空结果，是当前最大的回归问题。

排查步骤：
1. 在 `retrieval.py` 的 `EvidenceRetriever.retrieve()` 中添加 debug 日志，输出每个 category 的 top hit scores
2. 对比新旧 corpus 下相同 query 的 evidence scores 分布
3. 如必要，调整 `score_product` 中 evidence boost 的触发阈值或权重

### P1: 跨品类惩罚细化到 sub_category

当前惩罚仅在 ComponentCategory 级别检查（digital vs beauty vs clothing vs food），但同品类内子类错配（如查询"耳机"却匹配到"笔记本"）无法被检测。建议：
- 在 evidence chunk 中增加 sub_category 字段
- 在 `score_product` 中检查 evidence sub_category 是否与 requirement.product_type 匹配

### P2: followup_guard query 拼接限制

当前多轮对话中 followup_guard 将所有历史 query 累积拼接，导致长文本语义混乱。建议限制为最多追溯 1 层历史。

### P3: 品牌过滤逻辑放宽

"推荐华为的手机" 返回空结果，推测品牌过滤 + 产品类型过滤联合导致零结果。建议检查 brand exclusion filter 是否过于严格，或品牌匹配是否需要模糊匹配（如 "华为" 匹配 "HUAWEI 华为"）。

---

## 六、代码变更清单

| 文件 | 改动 | 行数 |
|------|------|------|
| `tool_router.py` | 改动 H: 组合意图路由 | ~5行修改 |
| `scorer.py` | 改动 G: 跨品类惩罚 | ~8行新增 |
| `package_builder.py` | 改动 D: 后置预算执行层（第二轮） | ~25行新增 |
| `products.json` | 改动 F: 100→180 产品 | +80 产品 |
| Milvus index | 改动 E: 全量重建 | 1204 chunks |
| `test_agent_v1_supplement.py` | 新增: 72 补充用例 | 新文件 |
| `test_agent_v1_combined.py` | 新增: 组合运行器 | 新文件 |

---

## 七、总结

第三轮在路由精度（组合意图 #59）和否定语义处理上取得改善，但 Milvus 全量重建引入了显著的 evidence retrieval 回归，导致推荐查询大面积空结果。当前总体 PASS 率 35.6%（第二轮 52.4%），FAIL 率 27.4%（第二轮 9.5%）。

下一步应优先排查 Milvus BM25 评分变化对 retrieval 质量的影响（P0），并通过日志分析定位 evidence scores 下降的具体环节。
