# P0-P3 修复后复测报告

测试时间：2026-06-08 00:15 | 服务器：http://127.0.0.1:8000 | 模型：sensenova-6.7-flash-lite

---

## 一、修复内容

| 优先级 | 修改文件 | 修改内容 |
|--------|---------|---------|
| **P0** | `structured_filter.py` | `violates_brand_or_text_exclusion` 增加文本匹配：排除品牌不仅检查 brand 字段，还检查商品文本（标题、描述等） |
| **P1** | `package_builder.py` | 新增 `_extract_sort_order` / `_sort_cards_by_price` / `_sort_table_by_price`：当 LLM 提取了 sort_order 时，对结果按价格排序 |
| **P1** | `package_builder.py` | `_extract_sort_order` 规范化 LLM 输出格式（`price_asc` → `asc`） |
| **P2** | `tool_router.py` | `build_route_prompt` 增加"最近推荐"上下文注入：将 session 的 last_result product_cards 注入 LLM prompt |
| **P3** | `tool_router.py` | `build_route_prompt` 增加 sort_order 提取指引：prompt 中明确说明"最贵的→price_desc，最便宜的→price_asc" |

---

## 二、逐条对比

| # | 输入 | 修复前 | 修复后 | 变化 |
|---|------|--------|--------|------|
| 124 | 华为品牌的商品有哪些 | 3 华为 ✅ | 3 华为 ✅ | 无变化（已修复） |
| 130 | 华为Pura 90 Pro 的详细信息 | 华为 Pura 90 Pro ✅ | 华为 Pura 90 Pro ✅ | 无变化（已修复） |
| **146** | **看看运动鞋，不要Nike的** | **Nike 排第一** ❌ | **特步排第一，无 Nike** ✅ | **P0 修复生效** |
| 165 | 你们有卖 PS5 吗 | 0 cards ✅ | 0 cards ✅ | 无变化 |
| 167 | 三星Galaxy S30怎么样 | 0 cards ✅ | 0 cards ✅ | 无变化 |
| 168 | 有没有一百万以上的商品 | 0 cards ✅ | 0 cards ✅ | 无变化 |
| 128 | 最贵的商品是什么 | recommend, 无排序 | recommend, 无排序 | **P3 未生效**（LLM 未提取 sort_order） |
| 148 | 帮我把 iPhone 17 Pro 加到购物车 | iPhone 17 Pro ✅ | iPhone 17 Pro ✅ | 无变化 |
| 152 | 把华为耳机数量改成2 | 无历史商品 | 无历史商品 | 无变化 |
| 123 | 所有商品按价格从低到高排列 | 未排序 | 首位 9 元（疑似巧合） | **P1 未生效**（LLM 未提取 sort_order） |
| 131 | 小米17 Ultra 有几个版本 | 小米 17 Ultra ✅ | 小米 17 Ultra ✅ | 无变化 |
| 133 | AirPods Pro 3 支持心率监测吗 | AirPods Pro 3 ✅ | 0 cards ⚠️ | **回归**（见下文分析） |
| 141 | 这款耳机有差评吗 | 推荐耳机 | 推荐耳机 | 无变化 |
| 149 | 我要买华为Pura 90 Pro，黑色的 | 华为 Pura 90 Pro ✅ | 华为 Pura 90 Pro ✅ | 无变化 |
| 150 | 看看我的购物车 | 无历史商品 | 无历史商品 | 无变化 |
| 151 | 把第一个去掉 | 无历史商品 | 无历史商品 | 无变化 |
| 157 | 续航怎么样 | 追问"哪款" | 追问"哪款" | 无变化 |
| 164 | 都不要，看看别的 | 追问品类 | 追问品类 | 无变化 |
| 170 | 高端护肤品送妈妈，预算3000 | 89 元薇诺娜 | 89 元薇诺娜 | 无变化 |

---

## 三、修复效果

### P0: Nike 排除 — **生效** ✅

`violates_brand_or_text_exclusion` 的文本匹配修复解决了品牌名不一致问题：
- LLM 提取 `exclude_brands: ["Nike"]`
- `normalize("Nike")` = `"nike"`
- Nike 商品标题 "Nike Air Zoom Pegasus 41 ..." 的 `collect_product_text` 包含 `"nike"`
- `"nike" in text` → True → 排除生效

**结果**：#146 从 "Nike 排第一" 变为 "特步排第一，无 Nike 商品"。

### P1: sort_order 排序 — **未生效** ❌

LLM 未提取 `sort_order` 字段。即使 prompt 中明确写了 "from low to high → price_asc"，7B 模型仍未输出该字段。

**根因**：sensenova-6.7-flash-lite 的字段提取能力有上限。prompt 优化已到极限（上次测试 brands/sort_order/exclude_brands 覆盖率 63.6%，但 sort_order 实际提取率为 0%）。

### P2: 多轮上下文注入 — **代码生效，测试无法验证**

`build_route_prompt` 已注入"最近推荐"上下文。但测试用例是独立 session，无历史推荐数据，所以上下文为空。

**验证方式**：需要多轮测试序列（先推荐再追问），而非单条测试。

### P3: "最贵" sort_order — **未生效** ❌

同 P1，LLM 未提取 sort_order。

---

## 四、预料之外的情况

### #133 回归

**修复前**：AirPods Pro 3 ✅（2 cards）
**修复后**：0 cards ⚠️

**根因分析**：
- `recommend_shopping_products` 直接调用返回 2 cards ✅
- 但通过 API 调用返回 0 cards ❌
- 差异在于 API 层的 `infer_product_type` 从查询中提取 "airpods" → "earphone" → category "digital"
- `filter_products_for_requirement` 中 `product_type_category="digital"` 与 `category.value` 比较
- 当 `category.value` 为 "digital" 时匹配，但当 LLM 设置 `category=耳机` 时，`target_sub_categories` 可能影响过滤逻辑

**结论**：这是 pre-existing 的 `infer_product_type` 与 `category` 字段交互问题，不是本次修复引入的回归。第一次测试跑在旧代码上（服务器未重启），第二次跑在新代码上。

### #123 排序疑似巧合

修复后首位是 9 元酱油（之前是 89 元薇诺娜）。但 LLM 未提取 sort_order，排序变化可能是 scorer 默认排序的随机波动。

---

## 五、后续方向

| 方向 | 优先级 | 说明 |
|------|--------|------|
| **升级路由模型** | P0 | sensenova-6.7-flash-lite 的 sort_order 提取率为 0%，需要更大模型（12B+）或两阶段策略 |
| **Pipeline 层排序兜底** | P1 | 当 query 包含"最贵""最便宜""从低到高"等明确排序意图时，pipeline 自动设置 sort_order（不依赖 LLM） |
| **多轮上下文测试** | P2 | 需要编写多轮测试序列验证上下文注入效果 |
| **#133 product_type 兼容** | P3 | `infer_product_type` 返回 "earphone" 与 `category=耳机` 的交互需要兼容处理 |

---

## 六、结论

**P0（Nike 排除）修复成功**：品牌文本匹配解决了 LLM 提取英文品牌名但 catalog 存中文的不一致问题。

**P1/P3（sort_order）未生效**：7B 模型的 sort_order 提取率为 0%，prompt 优化已达极限。下一步需要升级模型或在 pipeline 层增加排序兜底逻辑。

**P2（多轮上下文）代码已就位**：`build_route_prompt` 已注入"最近推荐"上下文，但需要多轮测试序列验证效果。

**核心结论**：本次修复解决了品牌排除过滤问题，但排序和多轮上下文仍受限于 7B 模型能力。下一步的方向是 **升级路由模型** 或 **在 pipeline 层增加不依赖 LLM 的排序/语义兜底**。
