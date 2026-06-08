## V2 FAIL & PARTIAL 案例复测报告

测试时间：2026-06-07 19:33–19:38 | 服务器：http://127.0.0.1:8000 | 模型：sensenova-6.7-flash-lite

---

### 一、总览

| 类别 | 原始状态 | 本轮状态 | 说明 |
|------|---------|---------|------|
| 原始 FAIL (13条) | 全部失败 | 3条已修复，10条仍失败 | #114, #120, #171 已修复 |
| 原始 PARTIAL (12条) | 部分通过 | 2条已修复，10条仍有问题 | #112, #126 已修复 |
| **合计 25 条** | — | **5条修复，20条仍有问题** | 修复率 20% |

---

### 二、已修复的案例（5条）

| # | 输入 | 原始状态 | 本轮结果 |
|---|------|---------|---------|
| 114 | 有没有防水的运动手表 | FAIL | **FIXED** — 返回"没有找到足够贴合的商品"，兜底逻辑生效 |
| 120 | 有没有好看的裙子 | FAIL | **FIXED** — 同上，兜底逻辑生效 |
| 171 | 手机+耳机，总共不超过1万 | FAIL | **FIXED** — budget 正确解析为 10000.0（之前是 1.0），返回华为FreeBuds Pro 5 等3件商品 |
| 112 | 有什么好吃的零食推荐吗 | PARTIAL | **FIXED** — 3件结果全部是食品（良品铺子肉松饼等），不再混入酱油 |
| 126 | 第二页的商品 | PARTIAL | **FIXED** — 正确追问"能告诉我您具体想看哪类商品吗" |

---

### 三、仍失败的 FAIL 案例（10条）

#### 3.1 品牌/排除过滤失效（3条）

**#124 — 华为品牌的商品有哪些**
- 路由：`recommend_shopping_products(query="华为品牌的商品有哪些")`
- 问题：tool arguments 中没有 `brands` 字段。LLM 路由器虽识别了品牌意图，但提取的参数在 `merge_route_arguments` 阶段被丢弃——`merge_route_arguments` 不传播 `brands` 字段。推荐结果首位是 The Ordinary 精华液（非华为），4个结果中仅1个华为。
- 根因链：`tool_router.py` 的 `RoutedArguments`（L557-587）无 `brands` 字段 → `extract_slots_rule_based`（L1171-1181）不提取品牌 → `merge_route_arguments`（L1299-1331）不合并品牌 → `handle_recommend` 不读取品牌参数 → pipeline 仅靠 `BRAND_HINTS`（12个硬编码品牌）做软加分。
- 结果：商品卡片 `['p_beauty_018', 'p_digital_005', 'p_clothes_005', 'p_food_003']`，首位是 The Ordinary。

**#130 — 华为Pura 90 Pro 的详细信息**
- 路由：`recommend_shopping_products(query="华为Pura 90 Pro 的详细信息")`
- 问题：同上，品牌未作为硬过滤。首位推荐 The Ordinary 精华液，华为 Pura 90 Pro 未出现。
- 结果：商品卡片 `['p_beauty_018', 'p_digital_005', 'p_clothes_006', 'p_food_003']`。

**#146 — 看看运动鞋，不要Nike的**
- 路由：`recommend_shopping_products`，arguments 中**有** `exclude_brands: ["Nike"]`。
- 问题：尽管路由器正确提取了 `exclude_brands`，但 `handle_recommend` 从不读取 tool call 中的品牌参数。pipeline 内部靠 `extract_exclusions` 重新从文本提取，依赖 `BRAND_HINTS` + 4个前缀词（"不要/除了/非/别买"）。Nike 在 `BRAND_HINTS` 中为"耐克"而非"Nike"，匹配失败。最终首位推荐 Nike Air Zoom Pegasus 41。
- 结果：商品卡片 `['p_clothes_007', 'p_clothes_010', 'p_clothes_012']`，Nike 排第一！

**修复方向**：
1. `tool_router.py` 的 `RoutedArguments` 和 formal schema 中加入 `brands`/`exclude_brands` 字段
2. `extract_slots_rule_based` 中加入品牌提取逻辑
3. `merge_route_arguments` 传播品牌字段
4. `handle_recommend` 读取并将品牌参数注入 `RequirementSpec`
5. `structured_filter.py` 加入正向品牌硬过滤步骤
6. `BRAND_HINTS` 增加英文品牌名映射（Nike → 耐克 + Nike）

#### 3.2 防幻觉失败（3条）

**#165 — 你们有卖 PS5 吗**
- 路由：`general_chat`（hallucination_guard 正确触发，阻止了 LLM 覆盖到 recommend）
- 问题：路由层正确判定为 general_chat，但 `handle_general_chat` 调用的 LLM **无法访问商品目录**，生成的回复"有的呀！我们平台上有各种版本的 PS5 主机和配件"完全是幻觉。
- 根因：`tool_handlers.py` L69-77 的 general_chat system prompt 没有告知 LLM 目录中有哪些商品，也没有指示"不要声称拥有目录中不存在的商品"。
- 修复方向：在 general_chat 的 LLM prompt 中注入商品目录的品类/品牌概览，或添加"不要声称拥有具体商品，除非通过 recommend 工具查询"的约束。

**#167 — 三星Galaxy S30怎么样**
- 路由：`recommend_shopping_products`
- 问题：pipeline 没有识别到"三星Galaxy S30"不在目录中，返回了完全不相关的商品（欧莱雅防晒、耳机等）。`requested_missing_subcategory` 未能识别"三星手机"这一具体产品不在目录。
- 修复方向：在 pipeline 的 `query_guards.py` 中增加对具体品牌+型号组合的缺失检测。

**#168 — 有没有一百万以上的商品**
- 路由：`recommend_shopping_products`
- 问题：pipeline 没有识别到目录中不存在超高价商品，返回了118元的安热沙防晒。budget extraction 将"一百万"解析为 `price_min=1000000`，但 `filter_products_for_requirement` 的预算过滤未能生效（目录最高价远低于100万）。
- 修复方向：`query_guards.py` 的 `requested_missing_subcategory` 或预算范围检测应检查目录价格区间，当用户最低预算远超目录最高价时返回空结果。

#### 3.3 hallucination guard 过度拦截（1条）

**#128 — 最贵的商品是什么**
- 路由：`general_chat`（hallucination_guard 触发，阻止了 LLM 覆盖到 recommend）
- 问题：v2_analysis 中问题是"LLM 覆盖 local rules 的 general_chat"，现在 hallucination guard 修复了这个覆盖。但修复过头了——"最贵的商品是什么"是一个合法的购物查询，应该走 recommend。Local rules 把"商品"不当作产品品类词，`detect_normal_product_category` 返回空，`_has_product_query_intent` 也未匹配"最贵的"。
- 根因：`tool_router.py` 的 `NORMAL_PRODUCT_TERMS` 和 `SEARCH_INTENT_TERMS` 都缺少"商品"/"最贵"/"最便宜"等通用购物词汇。
- 修复方向：在 `SEARCH_INTENT_TERMS` 或 `_is_general_chat` 的检测逻辑中加入"最贵/最便宜/最高/最低价"等排序查询词和"商品/产品"等通用商品词。

#### 3.4 购物车路由错误（2条）

**#148 — 帮我把 iPhone 17 Pro 加到购物车**
- 路由：`apply_cart_instruction`（conf=0.95, src=llm）
- 问题：作为 session 首条消息，没有历史推荐商品。`apply_cart_instruction` 直接执行但找不到可操作商品，返回"没有找到可操作的商品"。预期行为是应先走 `recommend_shopping_products` 搜索 iPhone 再加购。
- 修复方向：`apply_cart_instruction` 在 session 无历史商品时，回退到 `recommend_shopping_products` 先搜索再操作。

**#152 — 把华为耳机数量改成2**
- 路由：`apply_cart_instruction`（conf=0.9, src=llm）
- 问题：路由正确（v2_analysis 中被 guard 拦到 recommend 的问题已修复），但执行时修改了**所有**购物车商品的数量为2，而非仅华为耳机。
- 修复方向：cart 的 `update` 操作需正确解析目标商品（品牌/名称匹配），而非批量修改所有商品。

#### 3.5 其他 FAIL（1条）

**#146 已在 3.1 中详述。**

---

### 四、仍有问题的 PARTIAL 案例（10条）

| # | 输入 | 本轮表现 | 问题 |
|---|------|---------|------|
| 123 | 所有商品按价格从低到高排列 | 返回4件商品，首位眼霜139元 | 返回结果未按价格排序；"排序"意图未被pipeline执行 |
| 131 | 小米17 Ultra 有几个版本 | 返回 AHC眼霜 等无关品 | 小米17 Ultra 不在目录中，pipeline 未识别并兜底 |
| 133 | AirPods Pro 3 支持心率监测吗 | 路由到 general_chat，回复模糊 | hallucination_guard 过度拦截（同 #128），应走 recommend 查 AirPods 信息 |
| 141 | 这款耳机有差评吗 | 路由到 recommend，返回华为FreeBuds | 无上下文时应追问"哪款耳机"而非直接推荐 |
| 149 | 我要买华为Pura 90 Pro，黑色的 | 返回科颜氏等无关品 | 同 #130，品牌硬过滤缺失 |
| 150 | 看看我的购物车 | 路由正确，4件商品 | 回复文案异常：重复显示"已将...加入购物车"而非展示购物车内容 |
| 151 | 把第一个去掉 | 路由到 general_chat | hallucination_guard 拦截了"去掉"操作意图，应走 apply_cart_instruction |
| 157 | 续航怎么样 | 推荐小米17 Max | 未理解上文推荐的是 OPPO Reno 16 Pro，直接按"续航"关键词推荐新产品 |
| 164 | 都不要，看看别的 | 返回完全相同的3款手机 | pipeline 未利用 session 历史排除已推荐商品 |
| 170 | 高端护肤品送妈妈，预算3000 | 返回薇诺娜89元 | "高端"语义未体现，pipeline 推荐了低价品而非高端品 |

---

### 五、根因汇总（按影响范围排序）

| 优先级 | 根因 | 影响的案例 | 涉及文件 |
|-------|------|-----------|---------|
| **P0** | 品牌过滤不作为硬约束 | #124, #130, #146, #149 | tool_router.py, tool_handlers.py, structured_filter.py, recommendation_pipeline.py |
| **P1** | hallucination guard 过度拦截合法购物查询 | #128, #133, #151 | tool_router.py (NORMAL_PRODUCT_TERMS, SEARCH_INTENT_TERMS) |
| **P2** | general_chat LLM 无目录感知导致幻觉 | #165 | tool_handlers.py (_generate_general_chat_llm_response) |
| **P3** | 缺失商品/型号未兜底 | #131, #167, #168 | query_guards.py (requested_missing_subcategory) |
| **P4** | cart 操作商品定位不精确 | #148, #152 | tool_handlers.py (apply_cart_instruction) |
| **P5** | 多轮对话上下文理解不足 | #157, #164 | session_context.py, recommendation_pipeline.py |
| **P6** | 排序/高端等语义未执行 | #123, #170 | recommendation_pipeline.py (parse_requirement, scorer) |
| **P7** | 无上下文时未追问而直接推荐 | #141 | tool_router.py (local rules), tool_handlers.py |
| **P8** | cart 回复文案格式异常 | #150 | tool_handlers.py (apply_cart_instruction 回复生成) |

---

### 六、详细修复方案

#### 方案 A：品牌硬过滤（解决 P0，影响 #124, #130, #146, #149）

1. **`tool_router.py`**：
   - `RoutedArguments` 增加 `brands: List[str]` 和 `exclude_brands: List[str]` 字段
   - `extract_slots_rule_based` 调用品牌检测，从用户文本提取品牌
   - `merge_route_arguments` 传播 brands/exclude_brands
   - formal tool schema 加入 brands/exclude_brands 参数声明

2. **`tool_handlers.py`**（handle_recommend）：
   - 从 tool_call["arguments"] 读取 brands/exclude_brands
   - 将其注入到 recommendation pipeline 的 RequirementSpec 中

3. **`structured_filter.py`**（filter_products_for_requirement）：
   - 在 exclusion_filtered 之前加入正向品牌过滤步骤：当 `requirement.brands` 非空时，仅保留 `product.brand in requirement.brands` 的商品

4. **`BRAND_HINTS`**：增加英文名映射（Nike=耐克, Apple=苹果, Huawei=华为, Samsung=三星, Xiaomi=小米 等）

#### 方案 B：扩充购物意图词表（解决 P1，影响 #128, #133, #151）

1. **`tool_router.py`**：
   - `SEARCH_INTENT_TERMS` 加入"最贵", "最便宜", "最高", "最低", "排序", "排列"
   - `NORMAL_PRODUCT_TERMS` 加入"商品", "产品", "货物"
   - `_is_general_chat` 逻辑：当文本包含"去掉/改成/清空/加到购物车"等 cart 操作词时不判定为 general_chat

#### 方案 C：general_chat 目录感知（解决 P2，影响 #165）

1. **`tool_handlers.py`**（_generate_general_chat_llm_response）：
   - 在 system prompt 中注入"当前商品目录包含以下品类和品牌：..."的摘要
   - 增加规则："如果用户询问某个具体商品是否在卖，而你不确定，请引导用户使用搜索功能而非直接声称有"

#### 方案 D：缺失商品兜底增强（解决 P3，影响 #131, #167, #168）

1. **`query_guards.py`**（requested_missing_subcategory）：
   - 当检索结果与 query 关键词的编辑距离/语义相似度极低时，判定为 missing
   - 增加目录价格区间检测：当用户预算下限 > 目录最高价时返回"无超高价商品"
   - 增加品牌+型号组合检测：当品牌存在但该型号不存在时返回"该型号暂无"
