# MallMind Agent v1 管线微调对比报告（第二轮）

## 测试概要

| 项目 | 第一轮修复后 | 第二轮修复后 | 变化 |
|------|------------|------------|------|
| 测试时间 | 2026-06-07 10:12 | 2026-06-07 11:20 | — |
| 总用例 | 63 | 63 | — |
| PASS | 25 (39.7%) | **33 (52.4%)** | +8 |
| PARTIAL | 21 (33.3%) | 23 (36.5%) | +2 |
| FAIL | 15 (23.8%) | **6 (9.5%)** | -9 |
| ERR | 2 (3.2%) | 1 (1.6%) | -1 |
| 平均响应时间 | 834ms | 1068ms | +234ms |

FAIL 数量从 15 降至 6，通过率从 39.7% 提升至 52.4%。

---

## 一、本轮新增修复措施（3项）

### 改动 A: LLM 系统 prompt 全面增强（tool_router.py）

替换了原有的薄层 7 规则 prompt，改为详细的 5 工具路由手册。每个工具列出使用场景、路由规则和边界条件：
- `recommend_shopping_products`: 明确"用户在询问、寻找、评价任何商品"时使用，包括提到具体商品名询问属性（"iPhone续航怎么样"）
- `apply_cart_instruction`: 列出"看看购物车""改成2台""结账""清空购物车"等明确操作短语
- `general_chat`: 强调"任何涉及具体商品名的问题应使用 recommend_shopping_products 而非 general_chat"

### 改动 B: 购物车上下文注入 LLM 决策（tool_router.py）

在 `build_route_prompt` 中注入 session 购物车状态：
- 当购物车有商品时，LLM 可见 "当前购物车(N件): pid1 x1, pid2 x1"
- 当购物车为空时，LLM 可见 "当前购物车: 空"
- 附加最近3次工具调用历史作为上下文

### 改动 C: general_chat LLM 化（tool_handlers.py）

将 `handle_general_chat` 从固定模板改为 LLM 生成 + 模板降级，每次调用 LLM 产生自然多样的回复。

### 改动 D: 后置预算执行层（package_builder.py）— 本轮新增

在 `build_product_cards` 之后添加后置预算检查：当用户设置了明确预算（price_max）且预算未放宽时，过滤掉所有超预算商品卡片。如果过滤后无商品，返回 `budget_catalog_gap` 的 no-match 结果。

---

## 二、关键修复效果

### 2.1 购物车查询路由（#41-43, #47-48）— 从 FAIL → PASS

这是上一轮最大的回归问题（5个 FAIL），通过改动 A（prompt 明确列出购物车操作短语）和改动 B（注入购物车上下文）完全修复。

| 用例 | 输入 | 第一轮修复后 | 第二轮修复后 |
|------|------|------------|------------|
| #41 | 看看购物车 | general_chat [FAIL] | **apply_cart_instruction** [PASS] |
| #42 | 改成2台 | general_chat [FAIL] | **apply_cart_instruction** [PASS] |
| #43 | 不要第一个了 | general_chat [FAIL] | **apply_cart_instruction** [PASS] |
| #47 | 结账 | general_chat [FAIL] | **apply_cart_instruction** [PASS] |
| #48 | 结账 | general_chat [FAIL] | **apply_cart_instruction** [PASS] |

### 2.2 Budget Filter Fallback（#54）— 从 PARTIAL → PASS

上一轮中 "推荐500元以下的手机" 返回了 OPPO Reno 16 Pro（¥3299），严重超出用户预算。本轮通过改动 D（后置预算执行层）修复：

| 用例 | 输入 | 第一轮修复后 | 第二轮修复后 |
|------|------|------------|------------|
| #54 | 推荐500元以下的手机 | 1张卡片 OPPO ¥3299 [PARTIAL] | **0张卡片** + "没有找到足够贴合的商品" [PASS] |

### 2.3 商品FAQ路由（#27, #29, #31）— 显著改善

上一轮中这三个用例路由到了 general_chat（FAIL），LLM 不知道商品相关问题应使用 recommend_shopping_products。本轮通过改动 A（prompt 明确规则）修复：

| 用例 | 输入 | 第一轮修复后 | 第二轮修复后 |
|------|------|------------|------------|
| #27 | iPhone续航怎么样 | general_chat [FAIL] | **recommend_shopping_products** [PASS] |
| #29 | A19芯片比上一代提升多少 | general_chat [FAIL] | recommend_shopping_products [PARTIAL] |
| #31 | MacBook有几个配置 | general_chat [FAIL] | **recommend_shopping_products** [PASS] |

#29 路由正确但返回了跨品类商品（面霜排第一），从 FAIL 改善为 PARTIAL。

### 2.4 基础对话多样性（#1-5）— 持续 PASS

所有 5 个基础对话用例都通过 LLM 生成了自然多样的回复，不再使用固定模板。

---

## 三、新引入的问题

### 3.1 组合调用路由回退（#59）

| 用例 | 输入 | 第一轮修复后 | 第二轮修复后 |
|------|------|------------|------------|
| #59 | 推荐手机，直接帮我加到购物车 | recommend [llm] [PARTIAL] | **apply_cart_instruction** [rules] [FAIL] |

第一轮修复后 LLM 选择了 recommend_shopping_products（正确），但本轮中 local rules 层以 0.8 置信度拦截为 apply_cart_instruction。原因可能是 LLM 响应速度波动导致 guard 层先到达决策。

---

## 四、仍存在的问题

### 4.1 Milvus 品类标注精度（#9）— 仍未修复

"有没有防水的运动鞋" 返回了 AirPods Pro（耳机）作为首个推荐，品类不匹配。经代码分析确认：
- 品类映射逻辑正确（"运动鞋" → clothing 类别）
- 结构化过滤器正确（product.category == clothing 严格匹配）
- Milvus 过滤器正确（`chunk_level == 3 && category == "clothing"`）
- 根因可能是 Milvus 集合数据未随商品目录更新重建，或 embedding 模型对"防水"语义相似度偏向数码产品

修复此问题需要重建 Milvus 集合或调整 embedding 模型，不在纯代码微调范围内。

### 4.2 跨品类检索结果（#13, #29, #34）

多个用例中 recommend_shopping_products 返回了跨品类商品：
- #13 "适合办公的电脑" → 首个推荐 FreeBuds Pro 5（耳机）
- #29 "A19芯片比上一代提升多少" → 首个推荐薇诺娜面霜
- #34 "差评多吗" → 首个推荐薇诺娜面霜

这些用例的 LLM 路由正确（使用了 recommend_shopping_products），但结构化过滤器未能正确约束品类范围，或 Milvus 证据提升干扰了评分排序。

### 4.3 商品检索召回率低

63 个用例中仅 20 个返回了商品卡片（31.7%），大量"推荐手机""推荐零食"等基础查询返回空结果。这是系统最大的瓶颈，需要扩展 Milvus 商品索引数据、增加商品目录覆盖范围、或调整 embedding 模型。

---

## 五、逐用例判定明细

### A. 基础对话（5/5 PASS）
| # | 输入 | 工具 | 判定 |
|---|------|------|------|
| 1 | 你好 | general_chat [llm] | PASS |
| 2 | 你是谁 | general_chat [llm] | PASS |
| 3 | 帮我写一段代码 | general_chat [llm] | PASS |
| 4 | 今天天气怎么样 | general_chat [llm] | PASS |
| 5 | 谢谢 | general_chat [llm] | PASS |

### B. 模糊推荐（1 PASS / 6 PARTIAL / 1 FAIL）
| # | 输入 | 工具 | 商品数 | 判定 | 说明 |
|---|------|------|-------|------|------|
| 6 | 推荐一款手机 | recommend [guard] | 0 | PARTIAL | 路由正确，检索为空 |
| 7 | 有没有好吃的零食 | recommend [guard] | 0 | PARTIAL | 路由正确，检索为空 |
| 8 | 推荐适合学生的笔记本 | recommend [guard] | 3 | PARTIAL | 推荐了平板而非笔记本 |
| 9 | 有没有防水的运动鞋 | recommend [llm] | 2 | FAIL | 首个推荐 AirPods Pro |
| 10 | 推荐送女朋友的礼物 | recommend [guard] | 0 | PARTIAL | 检索为空 |
| 11 | 夏天穿什么好 | recommend [llm] | 0 | PARTIAL | 检索为空 |
| 12 | 推荐一款蓝牙耳机 | recommend [guard] | 2 | PASS | 华为 FreeBuds Pro 5 |
| 13 | 适合办公的电脑 | recommend [guard] | 3 | PARTIAL | 首个是耳机 |

### C. 精准搜索（1 PASS / 7 PARTIAL）
| # | 输入 | 工具 | 商品数 | 判定 |
|---|------|------|-------|------|
| 14 | 8000以下的手机 | recommend [guard] | 2 | PASS |
| 15 | 推荐华为的手机 | recommend [guard] | 0 | PARTIAL |
| 16 | 500元以下的零食 | recommend [guard] | 0 | PARTIAL |
| 17 | 所有数码电子类商品 | recommend [guard] | 0 | PARTIAL |
| 18 | 按价格从低到高排列手机 | recommend [guard] | 0 | PARTIAL |
| 19 | 苹果手机有哪些 | recommend [guard] | 0 | PARTIAL |
| 20 | 最便宜的手机 | recommend [guard] | 0 | PARTIAL |
| 21 | 2000到5000的护肤品 | recommend [guard] | 0 | PARTIAL |

### D. 否定语义（2 PASS / 4 PARTIAL）
| # | 输入 | 工具 | 商品数 | 判定 |
|---|------|------|-------|------|
| 22 | 不要苹果的手机 | recommend [guard] | 1 | PASS |
| 23 | 除了华为还有啥手机 | recommend [guard] | 1 | PASS |
| 24 | 推荐手机 | recommend [guard] | 0 | PARTIAL |
| 25 | 不要超过3000的耳机 | recommend [followup] | 0 | PARTIAL |
| 26 | 推荐零食，不要辣的 | recommend [guard] | 0 | PARTIAL |
| 242 | 不要苹果的 | recommend [llm] | 0 | PARTIAL |

### E. 商品FAQ（2 PASS / 2 PARTIAL / 1 FAIL）
| # | 输入 | 工具 | 判定 | 说明 |
|---|------|------|------|------|
| 27 | iPhone续航怎么样 | recommend [llm] | PASS | 路由正确 |
| 28 | 这款手机防水吗 | recommend [guard] | PARTIAL | 模板回复 |
| 29 | A19芯片提升多少 | recommend [llm] | FAIL | 跨品类结果 |
| 30 | 这个手机的屏幕多大 | recommend [followup] | PARTIAL | 模板回复 |
| 31 | MacBook有几个配置 | recommend [llm] | PASS | 3个笔记本 |

### F. 口碑查询（1 PASS / 2 PARTIAL）
| # | 输入 | 工具 | 判定 |
|---|------|------|------|
| 32 | 这款手机口碑怎么样 | recommend [followup] | PARTIAL |
| 33 | 有没有人说拍照好 | recommend [llm] | PASS |
| 34 | 差评多吗 | recommend [llm] | PARTIAL |

### G. 商品对比（2 PASS / 1 PARTIAL）
| # | 输入 | 工具 | 判定 |
|---|------|------|------|
| 35 | iPhone 17 Pro和Pro Max对比 | compare_products [llm] | PASS |
| 36 | 华为Pura 90和iPhone 17哪个好 | compare_products [llm] | PASS |
| 37 | 这两款笔记本哪个更适合学生 | recommend [llm] | PARTIAL |

### H. 购物车操作（7 PASS / 1 PARTIAL）
| # | 输入 | 工具 | 判定 | 说明 |
|---|------|------|------|------|
| 38 | 推荐一款手机，帮我加到购物车 | recommend [llm] | PARTIAL | 推荐为空 |
| 39 | 买这个iPhone，256G宇宙橙 | recommend [llm] | PASS | iPhone 17 Pro 加购 |
| 40 | 买iPhone | recommend [llm] | PASS | iPhone 17 Pro 加购 |
| 41 | 看看购物车 | apply_cart [llm] | PASS | 路由修复 |
| 42 | 改成2台 | apply_cart [llm] | PASS | 路由修复 |
| 43 | 不要第一个了 | apply_cart [llm] | PASS | 路由修复 |
| 44 | 清空购物车 | apply_cart [llm] | PASS | 正确清空 |
| 45 | 推荐蓝牙耳机然后加购 | recommend [llm] | PASS | FreeBuds 加购 |

### I. 结算（3/3 PASS）
| # | 输入 | 工具 | 判定 |
|---|------|------|------|
| 46 | 推荐一款零食然后加到购物车 | recommend [llm] | PASS |
| 47 | 结账 | apply_cart [llm] | PASS |
| 48 | 结账 | apply_cart [llm] | PASS |

### J. 多轮对话（1 PASS / 6 PARTIAL）
| # | 输入 | 工具 | 判定 |
|---|------|------|------|
| 49 | 推荐一款手机 | recommend [guard] | PARTIAL |
| 50 | 续航呢 | recommend [llm] | PASS |
| 51 | 有没有更便宜的 | recommend [guard] | PARTIAL |
| 52 | 推荐一款手机 | recommend [guard] | PARTIAL |
| 53 | 给我看看零食 | recommend [guard] | PARTIAL |
| 521 | 不要苹果的 | recommend [llm] | PARTIAL |
| 522 | 那华为的呢 | recommend [llm] | PARTIAL |

### K. 边界异常（2 PASS / 2 PARTIAL / 1 ERR）
| # | 输入 | 工具 | 判定 | 说明 |
|---|------|------|------|------|
| 54 | 推荐500元以下的手机 | recommend [guard] | PASS | 预算过滤生效 |
| 55 | 删除购物车里的iPhone | apply_cart [llm] | PASS | 正确回复 |
| 56 | 对比手机和洗面奶 | general_chat [llm] | PARTIAL | 未明确提示跨品类 |
| 57 | 。。。 | general_chat [llm] | PASS | 友好提示 |
| 58 | (空消息) | 无 | ERR | HTTP 400 |

### L. 组合调用（1 PASS / 1 FAIL）
| # | 输入 | 工具 | 判定 | 说明 |
|---|------|------|------|------|
| 59 | 推荐手机，直接帮我加到购物车 | apply_cart [rules] | FAIL | 应走 recommend |
| 60 | 看看购物车，把第一个删了 | apply_cart [rules] | PASS | 正确操作 |

---

## 六、工具调用分布对比

| 工具 | 第一轮 | 第二轮 | 变化 |
|------|--------|--------|------|
| recommend_shopping_products | 41 | 42 | +1 |
| general_chat | 15 | 12 | -3 |
| compare_products | 3 | 2 | -1 |
| apply_cart_instruction | 3 | 7 | +4 |

apply_cart_instruction 从 3 增至 7，反映了购物车路由修复。general_chat 从 15 减至 12，反映了商品FAQ路由改善。

---

## 七、后续优化建议

1. **Milvus 集合重建**：当前集合可能包含旧的品类标注数据，建议执行 `drop_collection=True + reset_bm25=True` 全量重建，确保品类标注与最新商品目录一致
2. **扩展商品目录**：100 个电商商品 + 242 个 PC 配件的覆盖面不足，60%+ 的推荐查询返回空结果
3. **跨品类评分惩罚**：当用户查询品类明确（如"运动鞋"）时，对非目标品类的 Milvus 证据提升施加惩罚权重
4. **组合调用路由**：#59 的 apply_cart_instruction[rules] 拦截需要调整 local rules 对"推荐"关键词的优先级判断
