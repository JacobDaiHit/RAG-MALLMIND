## V2 Guard 降级 + Prompt 优化 — 最终重测报告

测试时间：2026-06-07 | 服务器：http://127.0.0.1:8000

---

### 一、代码修改总结（3 处改动，均在 tool_router.py）

| # | 改动位置 | 改动内容 | 目的 |
|---|---------|---------|------|
| 1 | Line 1024 | `max_tokens` 320 → 600 | 解决 LLM 输出字段截断问题 |
| 2 | Line 1130-1136 | 删除 `_llm_chosen` 分支下全部 guard 逻辑，改为直接 return | LLM 拥有路由最高权威，guard 仅作 fallback |
| 3 | Line 958-1015 | 完全重写 system prompt，加入完整 JSON 模板 + 7 个提取示例 | 引导 LLM 输出 brands/sort_order/price_min 等新字段 |

---

### 二、25 条 FAIL/PARTIAL 案例最终结果

#### 已修复（FAIL/PARTIAL → PASS）— 13 条

| # | 原评级 | 现评级 | 输入 | 变化说明 |
|---|--------|--------|------|---------|
| 114 | FAIL | **PASS** | 有没有防水的运动手表 | src=llm, recommend → 诚实告知无此商品 |
| 120 | FAIL | **PASS** | 有没有好看的裙子 | src=llm, recommend → 诚实告知无此商品 |
| 128 | FAIL | **PASS** | 最贵的商品是什么 | **src=llm**（原 hallucination_guard），guard bypass 生效 |
| 130 | FAIL | **PASS** | 华为Pura 90 Pro 的详细信息 | src=llm, 首推华为 Pura 90 Pro ✅ |
| 133 | PARTIAL | **PASS** | AirPods Pro 3 支持心率监测吗 | **src=llm**（原 hallucination_guard），推 AirPods Pro 3 心率监测版 |
| 148 | FAIL | **PASS** | 帮我把 iPhone 17 Pro 加到购物车 | src=llm, recommend → iPhone 17 Pro |
| 151 | FAIL | **PASS** | 把第一个去掉 | **src=llm, apply_cart_instruction**（原 hallucination_guard → general_chat） |
| 165 | FAIL | **PASS** | 你们有卖 PS5 吗 | **src=llm**（原 hallucination_guard），诚实告知无 PS5 |
| 167 | FAIL | **PASS** | 三星Galaxy S30怎么样 | src=llm, 诚实告知无此商品 |
| 168 | FAIL | **PASS** | 有没有一百万以上的商品 | src=llm, 诚实告知无此商品 |
| 171 | FAIL | **PASS** | 手机+耳机，总共不超过1万 | src=guard, 预算解析正确 |
| 131 | PARTIAL | **PASS** | 小米17 Ultra 有几个版本 | src=llm, 首推小米 17 Ultra ✅ |
| 149 | PARTIAL | **PASS** | 我要买华为Pura 90 Pro，黑色的 | src=llm, 首推华为 Pura 90 Pro ✅ |

#### 仍 FAIL/PARTIAL — LLM 字段提取问题（4 条）

| # | 输入 | 当前状态 | 具体问题 |
|---|------|---------|---------|
| 124 | 华为品牌的商品有哪些 | FAIL→PARTIAL | src=llm, 首推华为 FreeBuds Pro 5 ✅，但 LLM 未提取 brands 字段 |
| 146 | 看看运动鞋，不要Nike的 | FAIL | LLM 提取了 category=运动鞋 但 exclude_brands 未生效，Nike 仍排第一 |
| 123 | 所有商品按价格从低到高排列 | PARTIAL | LLM 输出 "sort_by" 而非 "sort_order"，排序未生效 |
| 112 | PARTIAL | PARTIAL | 有什么好吃的零食推荐吗 | src=guard, 首推李锦记酱油（非零食） |

> **根因：** sensenova-6.7-flash-lite (7B) 模型无法稳定输出 brands/sort_order/price_min/price_max 等新字段。即使 prompt 已优化，模型仍只返回约 12 个基础字段。

#### 仍 PARTIAL — Pipeline/上下文问题（5 条）

| # | 输入 | 当前状态 | 具体问题 |
|---|------|---------|---------|
| 141 | 这款耳机有差评吗 | PARTIAL | 无上下文，应追问是哪款耳机而非直接推荐 |
| 157 | 续航怎么样 | PARTIAL | 未理解上文是 OPPO Reno，推了续航手机 |
| 164 | 都不要，看看别的 | PARTIAL | 推荐了与上轮相同的手机 |
| 170 | 高端护肤品送妈妈，预算3000 | PARTIAL | 推了 89 元薇诺娜，未按预算筛选高端品 |
| 126 | 第二页的商品 | PARTIAL | src=llm（原 hallucination_guard），但仍推商品而非追问上下文 |

#### 已 PASS（原本就是 PASS）

| # | 输入 | 状态 |
|---|------|------|
| 150 | 看看我的购物车 | PASS — apply_cart_instruction |
| 152 | 把华为耳机数量改成2 | PASS — apply_cart_instruction |
| 156 | 推荐一款手机 | PASS — recommend 手机 |
| 163 | 推荐一款手机（新 session） | PASS — recommend 手机 |

---

### 三、统计汇总

| 指标 | 数值 |
|------|------|
| 总测试案例 | 25 |
| **FAIL/PARTIAL → PASS（已修复）** | **13** |
| 仍 FAIL（品牌/排除过滤） | 2 |
| 仍 PARTIAL（字段提取 + pipeline） | 7 |
| 原本 PASS（无变化） | 4 |
| **修复率（改善 / 原 FAIL+PARTIAL 21 条）** | **61.9%** |

---

### 四、关键改进验证

**Guard 降级 — 完全生效**

之前被 hallucination_guard 错误拦截的 4 条案例全部修复：
- #128 "最贵的商品是什么" → src=llm, recommend_shopping_products ✅
- #133 "AirPods Pro 3 支持心率监测吗" → src=llm, 推 AirPods Pro 3 心率监测版 ✅
- #151 "把第一个去掉" → src=llm, apply_cart_instruction ✅
- #165 "你们有卖 PS5 吗" → src=llm, 诚实告知无 PS5 ✅

**Prompt 优化 — 部分生效**

- 目录无商品时的兜底回复明显改善（#114, #120, #167, #168 都正确告知"无此商品"）
- 具体商品查询路由准确率提升（#130 华为 Pura, #131 小米 Ultra, #149 华为 Pura 黑色）
- 品牌字段提取仍受限于 7B 模型能力

---

### 五、剩余问题及建议

**P1 — LLM 字段提取能力（影响 #124, #146, #123）**

sensenova-6.7-flash-lite (7B) 无法稳定输出 brands/sort_order/price_min/price_max。建议方向：
1. 升级路由模型（如 sensenova-12b 或更大模型）
2. 两阶段策略：第一阶段仅做工具选择（5 分类），第二阶段用更大模型做参数提取
3. Pipeline 层面增加 rule-based 品牌/排序提取作为 LLM 兜底

**P2 — Pipeline 品牌硬过滤（影响 #146）**

exclude_brands 已从 LLM 提取但 pipeline 未执行硬过滤。需在 structured_filter 中将品牌排除作为硬约束。

**P3 — 多轮对话上下文理解（影响 #157, #164）**

多轮对话中 LLM 未能理解上下文（"续航怎么样" 不知道指 OPPO Reno），属于 LLM 多轮记忆能力问题。

**P4 — 预算/价格过滤（影响 #170）**

budget=3000 已正确提取，但推荐了 89 元商品。需检查 apply_routed_arguments 的价格过滤逻辑。
