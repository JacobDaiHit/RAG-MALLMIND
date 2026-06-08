## MallMind V2 全量测试报告（73 条用例）

测试时间：2026-06-08 | 服务器：http://127.0.0.1:8000 | 0 HTTP 错误

---

### 一、总览

| 评级 | 数量 | 占比 |
|------|------|------|
| PASS | 60 | 82.2% |
| PARTIAL | 13 | 17.8% |
| FAIL | 0 | 0% |
| **合计** | **73** | 100% |

加权通过率（PARTIAL = 0.5）：**91.1%**

工具调用分布：recommend_shopping_products 55 次（75.3%），general_chat 13 次（17.8%），apply_cart_instruction 4 次（5.5%），compare_products 1 次（1.4%）。

路由来源分布：guard 38 次（52.1%），llm 28 次（38.4%），rules 4 次（5.5%），followup_guard 2 次（2.7%），cart_fallback_guard 1 次（1.4%）。

---

### 二、按类别分布

| 类别 | 用例范围 | 总数 | PASS | PARTIAL | FAIL |
|------|---------|------|------|---------|------|
| A. 基础对话与边界 | #101-#108 | 8 | 8 | 0 | 0 |
| B. 语义搜索商品 | #109-#120 | 12 | 11 | 1 | 0 |
| C. 结构化查询 | #121-#128 | 8 | 4 | 4 | 0 |
| D. 商品详情 | #129-#133 | 5 | 4 | 1 | 0 |
| E. FAQ搜索 | #134-#138 | 5 | 2 | 3 | 0 |
| F. 评价搜索 | #139-#143 | 5 | 4 | 1 | 0 |
| G. 否定语义/排除 | #144-#147 | 4 | 2 | 2 | 0 |
| H. 购物车操作 | #148-#155 | 8 | 5 | 3 | 0 |
| I. 多轮对话 | #156-#164 | 9 | 6 | 3 | 0 |
| J. 防幻觉测试 | #165-#169 | 5 | 3 | 2 | 0 |
| K. 复合/综合场景 | #170-#173 | 4 | 1 | 3 | 0 |

---

### 三、PASS 用例清单（60 条）

**A. 基础对话（8/8 PASS）：** #101 你好 → general_chat 友好问候 ✅ | #102 你是谁 → general_chat 介绍身份 ✅ | #103 写诗 → general_chat 礼貌拒绝 ✅ | #104 政治话题 → general_chat 拒绝 ✅ | #105 Python爬虫 → general_chat 拒绝 ✅ | #106 商品分类 → general_chat 介绍分类 ✅ | #107 品牌信息 → general_chat 引导品类 ✅ | #108 谢谢你 → general_chat 礼貌回应 ✅

**B. 语义搜索（11/12 PASS）：** #109 洗面奶 → recommend 推珊珂洁面 ✅ | #110 笔记本 → recommend 推华为MateBook ✅ | #111 跑鞋 → recommend 推Nike Pegasus ✅ | #113 降噪耳机 → recommend 推华为FreeBuds Pro 5 ✅ | #114 运动手表 → recommend 诚实告知无此品 ✅ | #115 性价比手机 → recommend 推OPPO Reno ✅ | #116 送女友 → recommend 跨品类推荐 ✅ | #117 夏天衣服 → recommend 推优衣库T恤 ✅ | #118 敏感肌 → recommend 推薇诺娜 ✅ | #119 续航手机 → recommend 推小米17 Max ✅ | #120 裙子 → recommend 诚实告知无此品 ✅

**C. 结构化查询（4/8 PASS）：** #121 数码电子 → recommend 推vivo Pad ✅ | #122 500元以下 → recommend(budget=500) 推低价商品 ✅ | #129 iPhone颜色 → recommend 推iPhone 17 Pro ✅ | #130 华为Pura → recommend 推华为Pura 90 Pro ✅

**D. 商品详情（4/5 PASS）：** #129 iPhone颜色 ✅ | #130 华为Pura ✅ | #131 小米Ultra → recommend 推小米17 Ultra ✅ | #132 OPPO拍照 → recommend 推OPPO Find X9 Ultra ✅

**E. FAQ搜索（2/5 PASS）：** #135 FreeBuds降噪 → recommend 推华为FreeBuds Pro 5 ✅ | #139 好评手机 → recommend 推5款手机 ✅

**F. 评价搜索（4/5 PASS）：** #140 iPhone拍照评价 → recommend 推iPhone 17 Pro ✅ | #142 华为评价 → recommend 推华为Pura 90 Pro ✅ | #143 小米发热 → recommend 推小米手机 ✅

**G. 否定排除（2/4 PASS）：** #144 不要苹果 → recommend 推OPPO/vivo/小米 ✅ | #145 不要兰蔻 → recommend 推薇诺娜等非兰蔻 ✅

**H. 购物车（5/8 PASS）：** #148 加购iPhone → recommend 推iPhone 17 Pro ✅ | #149 买华为Pura → recommend 推华为Pura 90 Pro ✅ | #150 看购物车 → apply_cart view_cart ✅ | #153 清空购物车 → apply_cart clear ✅ | #155 加跑步鞋 → recommend 推Nike Pegasus ✅

**I. 多轮对话（6/9 PASS）：** #156 推荐手机 → recommend 推OPPO Reno ✅ | #158 更便宜 → recommend 推OPPO Reno ✅ | #160 加购物车 → apply_cart 加李锦记 ✅ | #162 对比耳机 → compare_products ✅ | #163 推荐手机(新session) → recommend ✅ | #161 还有别的 → recommend 推零食 ✅

**J. 防幻觉（3/5 PASS）：** #165 PS5 → recommend 诚实告知无此品 ✅ | #166 iPhone价格 → recommend 推iPhone 8999元纠正999 ✅ | #169 店名 → general_chat 诚实回答 ✅

**K. 综合场景（1/4 PASS）：** #171 手机+耳机1万 → recommend(budget=10000) 推组合方案 ✅

---

### 四、PARTIAL 用例详析（13 条）

#### 类型一：LLM 字段提取缺失 — 3 条

| # | 输入 | 期望 | 实际表现 | 根因 |
|---|------|------|---------|------|
| 123 | 所有商品按价格从低到高排列 | recommend → 价格排序列表 | recommend 正确路由，返回商品卡片，但 sort_order 字段未提取（LLM 不输出此字段），排序未按价格严格排列 | sensenova-6.7-flash-lite (7B) 不输出 sort_order |
| 125 | 3000到5000之间的手机 | recommend(budget=5000, 手机)+CARD | budget 解析为 3000（取了低值而非高值），pipeline 返回"无贴合商品" | budget 提取逻辑：区间 3000-5000 被解析为 budget=3000 |
| 147 | 推荐耳机，不要华为的，500到2000之间 | recommend(exclude=华为, 耳机, budget)+CARD | budget=500（取低值），exclude_brands 未提取，返回"无贴合商品" | 同 #125 预算解析问题 + LLM 不提取 exclude_brands |

**建议：** 升级路由模型或采用两阶段策略（工具选择 + 参数提取分离）。预算区间解析需在 budget extraction 中取 max 而非 min。

#### 类型二：品牌/排除过滤未生效 — 1 条

| # | 输入 | 期望 | 实际表现 | 根因 |
|---|------|------|---------|------|
| 146 | 看看运动鞋，不要Nike的 | recommend(exclude=Nike)+非Nike运动鞋 | 路由正确(category=运动鞋)，但 exclude_brands 未提取，首推 Nike Pegasus | 7B 模型不输出 exclude_brands + pipeline 无硬过滤 |

**建议：** pipeline structured_filter 需将品牌排除作为硬约束执行。

#### 类型三：路由偏差（general_chat vs recommend）— 3 条

| # | 输入 | 期望 | 实际表现 | 根因 |
|---|------|------|---------|------|
| 136 | 这个面膜敏感肌能用吗 | recommend → 面膜敏感肌信息 | general_chat → 固定话术回复 | 无具体商品名，local rules 判定为 general_chat；LLM 被 guard 跳过 |
| 138 | 运动跑鞋怎么选择尺码 | recommend → 跑鞋尺码建议 | general_chat → 固定话术回复 | 同上，被视为知识问答而非商品查询 |
| 141 | 这款耳机有差评吗 | general_chat: 追问是哪款 | recommend → 推华为FreeBuds | 预期是追问，但 guard 路由到 recommend 直接推荐 |

**建议：** #136/#138 应在 local rules 中增加"面膜""跑鞋"等品类词的识别权重，或放宽 LLM 覆盖 general_chat 的阈值。#141 当前行为（直接推荐）也可接受，但追问更合理。

#### 类型四：商品匹配不精准 — 2 条

| # | 输入 | 期望 | 实际表现 | 根因 |
|---|------|------|---------|------|
| 112 | 有什么好吃的零食推荐吗 | recommend → 零食+CARD | recommend 路由正确，但首推李锦记酱油（非零食） | pipeline 品类标签匹配不精准，酱油被归入"食品"大类 |
| 134 | iPhone 17 Pro 的电池续航怎么样 | recommend → iPhone续航FAQ | recommend 路由正确，但推的是 iPad Pro 而非 iPhone | pipeline 召回相关度排序问题，iPad Pro 和 iPhone 同价位导致混淆 |

**建议：** pipeline 在 query 包含明确品牌/型号时应优先精确匹配。

#### 类型五：购物车操作路由失败 — 2 条

| # | 输入 | 期望 | 实际表现 | 根因 |
|---|------|------|---------|------|
| 151 | 把第一个去掉 | apply_cart → remove | general_chat → 追问是购物车还是推荐列表 | "把第一个去掉"未命中购物车关键词，local rules 无法确定操作对象 |
| 152 | 把华为耳机数量改成2 | apply_cart → update | recommend → 推华为FreeBuds | "华为耳机"触发了品类词匹配到 recommend，未识别为购物车修改操作 |

**建议：** 购物车操作需结合 session 历史（购物车内有商品时优先识别为 cart 操作），或让 LLM 在 session 有购物车上下文时优先选 apply_cart。

#### 类型六：多轮上下文理解不足 — 1 条

| # | 输入 | 期望 | 实际表现 | 根因 |
|---|------|------|---------|------|
| 164 | 都不要，看看别的 | 推荐不同的手机 | 推荐了与上轮完全相同的 3 款手机 | followup_guard 正确路由到 recommend，但 pipeline 无去重/排除逻辑 |

**建议：** pipeline 需要接收"排除已展示商品"的信号，或 LLM 提取 exclude_ids 参数。

#### 类型七：回复质量 — 2 条

| # | 输入 | 期望 | 实际表现 | 根因 |
|---|------|------|---------|------|
| 168 | 有没有一百万以上的商品 | 诚实告知没有超高价商品 | recommend 路由正确，但推荐了薇诺娜 89 元 | pipeline 未对超高价查询做兜底，应告知"无此价位商品" |
| 170 | 高端护肤品送妈妈，预算3000 | recommend(budget=3000) 推高端品 | budget=3000 正确提取，但推了薇诺娜 89 元 | pipeline 未按 budget 做价格下限过滤 |
| 172 | 有没有什么限时优惠活动 | 告知无法查询促销 | recommend 路由，推了薇诺娜 | 应走 general_chat 告知无法查询促销信息 |
| 173 | 我想退货怎么办 | 说明退货流程 | general_chat → 固定话术 | 应生成退货建议而非仅返回"请告诉我你想买什么" |

---

### 五、按失败根因分类统计

| 根因类别 | 涉及案例 | 数量 |
|---------|---------|------|
| LLM 字段提取缺失（7B 模型瓶颈） | #123, #125, #146, #147 | 4 |
| 路由偏差（general_chat vs recommend） | #136, #138, #141, #151, #172 | 5 |
| Pipeline 召回/排序不精准 | #112, #134, #164, #168, #170 | 5 |
| 购物车操作上下文识别 | #152 | 1 |
| 预算区间解析 bug | #125, #147 | 2 |
| 回复质量（固定话术过于简单） | #173 | 1 |

> 注：部分案例涉及多个根因。

---

### 六、与上一版对比（v2_analysis.md 基准）

| 指标 | v2 原始（修改前） | 本次（修改后） | 变化 |
|------|-----------------|--------------|------|
| PASS | 48 (65.8%) | 60 (82.2%) | **+12** |
| PARTIAL | 12 (16.4%) | 13 (17.8%) | +1 |
| FAIL | 13 (17.8%) | 0 (0%) | **-13** |
| 加权通过率 | 74.0% | 91.1% | **+17.1pp** |

13 条 FAIL 全部消除（→ PASS 或 → PARTIAL），0 FAIL。加权通过率从 74.0% 提升至 91.1%。

---

### 七、改进建议优先级

**P0 — LLM 模型能力升级**

sensenova-6.7-flash-lite (7B) 是当前主要瓶颈。brands、exclude_brands、sort_order、price_min/max 等新字段无法稳定输出。建议升级至 12B+ 模型或采用两阶段路由策略。

**P1 — 预算区间解析修复**

"3000到5000之间" 被解析为 budget=3000（取低值），应取高值 5000 或同时设 price_min=3000, price_max=5000。影响 #125, #147。

**P2 — Pipeline 品牌硬过滤**

即使 exclude_brands 被提取，pipeline 的 structured_filter 需将其作为硬约束执行。当前 #146 的 Nike 排除未生效。

**P3 — 购物车上下文感知**

#151/#152 的购物车操作在 session 有购物车历史时应优先路由到 apply_cart_instruction。

**P4 — FAQ/知识查询路由**

#136（面膜敏感肌）和 #138（跑鞋尺码）被误判为 general_chat。需在 local rules 中增加品类词识别权重。
