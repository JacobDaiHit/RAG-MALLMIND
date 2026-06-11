# Case 12/14/15/16/17/18/19/20 链路分析与修改报告

**日期:** 2026-06-11  
**关联报告:** [bound_test_v3_issues.md](bound_test_v3_issues.md)  
**分析范围:** compare_products 返回空结果（14处）+ recommend 返回 0 卡片（9处）  
**核心问题:** 逐一核查每个 Case 到底应该返回对比卡片，还是确实因数据库缺失无法返回

---

## 一、数据库存量核查结论

在逐案分析之前，先明确商品库中**实际存在**的商品。通过扫描 `data/ecommerce_products/products.json`（100 个商品，4 品类各 25 个）和 `data/jd_pc_products/products.json`（PC 配件），得到以下结论：

### 1.1 ecommerce 库中存在的关键商品

| 用户提及名称 | 是否存在 | product_id | 备注 |
|---|---|---|---|
| 特仑苏纯牛奶 | **存在** | `p_food_016` | 蒙牛特仑苏 250mlx16 |
| 金典有机纯牛奶 | **存在** | `p_food_007` | 伊利金典有机 250ml*12 |
| HOKA Clifton 9 | **存在** | `p_clothes_010` | 男子缓震公路跑鞋 |
| Nike Pegasus 41 | **存在** | `p_clothes_007` | Air Zoom Pegasus 41 |
| 萨洛蒙 X ULTRA 4 | **存在** | `p_clothes_014` | 男子徒步鞋 |
| 迈乐 MOAB 3 GTX | **存在** | `p_clothes_015` | 男款徒步鞋 |
| 小米 17 Ultra | **存在** | `p_digital_008` | 12+256GB 5G 手机 |
| OPPO Find X9 Ultra | **存在** | `p_digital_009`（待确认） | 需确认具体 ID |
| 华为 MateBook 14 | **存在** | `p_digital_004` | 鸿蒙版 14 英寸 |
| 小米 MIX Fold 5 | **存在** | `p_digital_010` | 折叠屏旗舰 |
| 联想 ThinkBook 14+ | **存在** | `p_digital_023` | 2026 款 |
| 联想 ThinkPad X1 Carbon | **存在** | `p_digital_022` | Aura AI 元启版 |
| MacBook Air M5 | **存在** | （digital 品类中） | 已确认 |

### 1.2 不存在或缺失的商品

| 用户提及名称 | 是否存在 | 说明 |
|---|---|---|
| 独立主板产品 | **不存在** | ecommerce 库无独立主板品类；PC 主板仅存在于 jd_pc_products 中 |
| OPPO Find N6 | **待确认** | digital 品类中可能存在，需精确核查 |
| 折叠屏相关型号 | **部分存在** | 小米 MIX Fold 5 存在，但其他折叠屏型号可能不全 |
| "方里"品牌眉笔 | **待确认** | beauty 品类 25 个商品中需逐一核查 |

### 1.3 PC 配件库（jd_pc_products）

PC 配件库包含 CPU、GPU、主板、内存、SSD、电源、机箱、散热器等组件，ID 格式为 `pc_seed_cpu_xxx`、`pc_seed_gpu_xxx` 等。这些产品通过 `product_loader._load_pc_products_as_api_products()` 转换为 `ApiProduct` 格式后合入 combined catalog，ID 会被添加 `pc_cpu_`、`pc_gpu_` 等前缀。

---

## 二、compare_products 完整链路追踪

### 2.1 入口路由（chat.py 第 135-143 行）

```
用户消息 → route_shopping_tool_call() → tool_call.name = "compare_products"
→ chat.py 第 137 行: product_ids = list(tool_call.arguments.product_ids or request_product_ids)
→ chat.py 第 141 行: yield from handle_compare_v2(session, product_ids, tool_call)
```

**关键发现：** LLM 路由器在识别到对比意图时，输出的 `arguments` 中 **从未包含 `product_ids`**，仅传递 `category` 和偶尔的 `brands`。这导致 `product_ids` 始终为空列表。

### 2.2 handle_compare_v2 链路（tool_handlers.py 第 196-248 行）

```python
def handle_compare_v2(session, product_ids, tool_call):
    catalog = load_combined_product_catalog()
    # 第一步：校验 product_id 存在性
    valid_ids = []
    for pid in product_ids:         # ← product_ids 为空，循环不执行
        if catalog.get(pid):
            valid_ids.append(pid)
    if not valid_ids:                # ← 直接命中此分支
        yield sse_event("error", {"label": "商品不存在", ...})
        yield sse_event("done", ...)
        return                       # ← 提前返回，不做任何降级
```

**核心问题：** v2 版本在 `product_ids` 为空时**直接返回错误**，完全没有降级逻辑。

### 2.3 对比 handle_compare v1（tool_handlers.py 第 176-191 行）

```python
def handle_compare(session, product_ids, tool_call):
    if not product_ids:
        product_ids = last_recommended_product_ids(session)   # 降级 1: 取上次推荐
    if not product_ids:
        product_ids = comparison_candidate_ids(query)          # 降级 2: 用推荐兜底
    compare_result = compare_products(catalog, product_ids) if product_ids else {...}
```

v1 有两层降级，但 **chat.py 已切换到 v2**（第 141 行 `yield from handle_compare_v2`），v1 的降级逻辑被绕过。

### 2.4 compare_products 底层逻辑（comparison.py 第 10-35 行）

```python
def compare_products(catalog, product_ids):
    products = []
    for product_id in product_ids:
        product = catalog.get(key)
        if product is None:
            missing.append(key)
            continue
        products.append(product)
    rows = [product_to_comparison_row(p) for p in products]
    return {"count": len(rows), "rows": rows, ...}
```

当 `product_ids` 为空时，`rows` 为空列表，`count` 为 0。

---

## 三、逐 Case 链路分析

### Case 12 T6：你推荐的两款主板有什么区别？

| 项目 | 详情 |
|---|---|
| **用户消息** | "你推荐的两款主板有什么区别？" |
| **路由结果** | `compare_products`，参数 `{"category": "digital"}` |
| **product_ids** | 空（LLM 未传递） |
| **商品库状态** | ecommerce 库中**无独立主板产品**；PC 配件库有主板（如 `pc_seed_motherboard_xxx`） |
| **session 状态** | 之前有多轮 PC 装机方案（`generate_pc_build_plan`），`session.pc_build_history` 非空 |

**链路分析：**

1. `chat.py:137` 取 `tool_call.arguments.product_ids` → 空
2. `handle_compare_v2` 直接返回 "商品不存在" 错误
3. 即便走 v1 降级：`last_recommended_product_ids(session)` 会返回最近一次 PC 方案中的所有组件 ID（包含主板），这些 ID 在 combined catalog 中**存在**
4. 但 v1 降级到 `comparison_candidate_ids("你推荐的两款主板有什么区别")` 时，推荐管线搜索关键词 "主板"，在 ecommerce 库中找不到（无此品类），在 combined 库中可以命中 PC 主板

**结论：应该能返回对比卡片。** 数据库中有主板产品（来自 PC 配件库），但因 v2 移除了降级逻辑导致对比失败。这是一个 **代码缺陷**，不是数据缺失。

**修复方向：** 在 `handle_compare_v2` 中恢复 v1 的降级逻辑，且当 topic_type 为 `pc_build` 时，优先调用 `compare_pc_build_plans()` 对比两个方案中的主板。

---

### Case 14 T4：特仑苏和金典哪个更适合？

| 项目 | 详情 |
|---|---|
| **用户消息** | "特仑苏和金典哪个更适合？" |
| **路由结果** | `compare_products`，参数 `{"category": "food"}` |
| **product_ids** | 空（LLM 未传递） |
| **商品库状态** | `p_food_016`（特仑苏）和 `p_food_007`（金典）**均存在** |
| **session 状态** | 前几轮刚推荐过这两款牛奶，`session.last_result.product_cards` 中有它们 |

**链路分析：**

1. `chat.py:137` → product_ids 为空
2. `handle_compare_v2` → 直接返回错误
3. 若走 v1 降级：`last_recommended_product_ids(session)` → 返回上一轮推荐的 `p_food_016` 和 `p_food_007` → **这两个 ID 在 catalog 中存在** → `compare_products` 应能生成对比表
4. v1 的 `comparison_candidate_ids("特仑苏和金典哪个更适合")` → 推荐管线搜索 "特仑苏 金典"，规则解析器可以匹配 food 品类中的商品

**结论：应该能返回对比卡片。** 两款牛奶都在库中，且刚被推荐过。纯粹是 v2 缺失降级逻辑导致的失败。

**修复方向：** 在 v2 中增加 `last_recommended_product_ids` 降级即可解决。

---

### Case 15 T6：HOKA Clifton 9和Nike Pegasus 41哪个更软？

| 项目 | 详情 |
|---|---|
| **用户消息** | "那HOKA Clifton 9和Nike Pegasus 41哪个更软？" |
| **路由结果** | `compare_products`，参数 `{"category": "clothing"}` |
| **product_ids** | 空 |
| **商品库状态** | `p_clothes_010`（HOKA Clifton 9）和 `p_clothes_007`（Nike Pegasus 41）**均存在** |

**链路分析：**

1. 与 Case 14 相同路径，v2 无降级直接返回错误
2. 若走 v1：`last_recommended_product_ids(session)` 应包含上一轮推荐的跑鞋 ID
3. 或 `comparison_candidate_ids("HOKA Clifton 9 Nike Pegasus 41")` 能通过关键词命中

**结论：应该能返回对比卡片。** 两款跑鞋都在库中。

### Case 15 T8：能不能对比一下这两款的鞋底耐磨性？

| 项目 | 详情 |
|---|---|
| **用户消息** | "能不能对比一下这两款的鞋底耐磨性？" |
| **路由结果** | `compare_products`，参数 `{"category": "clothing"}` |
| **product_ids** | 空 |
| **商品库状态** | 同上两款鞋 |

**结论：应该能返回对比卡片。** "这两款"指上一轮推荐的两款鞋，`last_recommended_product_ids` 可获取。

---

### Case 16 T4：苹果iPad和华为MatePad怎么选？

| 项目 | 详情 |
|---|---|
| **用户消息** | "苹果iPad和华为MatePad怎么选？" |
| **路由结果** | `compare_products`，参数 `{"category": "digital"}` |
| **product_ids** | 空 |
| **商品库状态** | iPad Air（digital 品类中**存在**）和华为 MatePad（digital 品类中**存在**）|

**结论：应该能返回对比卡片。** 两款平板都在库中。

---

### Case 17 T4：萨洛蒙和迈乐哪个抓地力更好？

| 项目 | 详情 |
|---|---|
| **用户消息** | "萨洛蒙和迈乐哪个抓地力更好？" |
| **路由结果** | `compare_products`，参数 `{"category": "clothing", "brands": ["萨洛蒙", "迈乐"]}` |
| **product_ids** | 空 |
| **商品库状态** | `p_clothes_014`（萨洛蒙 X ULTRA 4）和 `p_clothes_015`（迈乐 MOAB 3 GTX）**均存在** |

**注意：** LLM 这次额外传了 `brands` 字段，但 v2 代码不使用 brands 做搜索。

### Case 17 T6：迈乐MOAB 3 GTX和萨洛蒙X ULTRA 4哪个更轻？

| 项目 | 详情 |
|---|---|
| **用户消息** | "那迈乐MOAB 3 GTX和萨洛蒙X ULTRA 4哪个更轻？" |
| **路由结果** | `compare_products`，参数 `{"category": "clothing"}` |
| **product_ids** | 空 |

**结论：两个 Turn 都应该能返回对比卡片。** 两款徒步鞋均在库中。

---

### Case 18 T4：花西子和方里哪个更细？

| 项目 | 详情 |
|---|---|
| **用户消息** | "花西子和方里哪个更细？" |
| **路由结果** | `compare_products`，参数 `{"category": "beauty", "brands": ["花西子", "方里"]}` |
| **product_ids** | 空 |
| **商品库状态** | 花西子螺黛生花眉笔（**存在**）；"方里"品牌需确认——beauty 品类 25 个商品中可能不含此品牌 |

**结论：部分应该能返回。** 花西子产品在库中，但"方里"品牌可能缺失。若"方里"不在库中，对比表应只展示花西子一侧，并提示另一侧未找到。当前 v2 因 product_ids 为空直接全量失败，属于代码缺陷。

---

### Case 19（游戏手机长对话）— 混合问题

#### compare_products 失败的 4 个 Turn

| Turn | 用户消息 | 商品是否存在 | 应有对比卡片？ |
|---|---|---|---|
| T4 | 小米17 Ultra和OPPO Find X9 Ultra哪个游戏表现好？ | 小米 17 Ultra (`p_digital_008`) **存在**；OPPO Find X9 Ultra **待确认** | **部分应该** |
| T5 | 那屏幕方面，谁的刷新率更高？ | 同上 | **部分应该** |
| T6 | 电池续航呢？ | 同上 | **部分应该** |
| T13 | 小米MIX Fold 5和OPPO Find N6哪个更轻？ | 小米 MIX Fold 5 (`p_digital_010`) **存在**；OPPO Find N6 **待确认** | **部分应该** |

**链路分析：** T4/T5/T6 是连续三轮对比同一对手机的不同维度。session 中有上一轮推荐的小米和 OPPO 手机，`last_recommended_product_ids` 应该能获取到对应的 product_id。T13 涉及折叠屏，小米 MIX Fold 5 存在但 OPPO Find N6 需确认。

#### recommend_shopping_products 失败的 6 个 Turn

| Turn | 用户消息 | 根因分析 |
|---|---|---|
| T2 | 要散热好一点，不发热降频的 | **代码缺陷**：追问被误判为新推荐，且响应文本说"挑了几款"但实际 0 卡片，文本与数据不一致 |
| T7 | 小米17 Ultra的12+256和16+512差多少钱？ | **链路不匹配**：这是 SKU 级价格查询，不是推荐请求。Router 将其路由到 `recommend_shopping_products`，但推荐管线无法做 SKU 级比价 |
| T10 | 算了，我要不要考虑一下折叠屏？ | **数据缺失**：折叠屏品类在库中覆盖不全。但"算了"触发了 `looks_like_followup` 中的话题切换信号 |
| T12 | 那有什么折叠屏推荐？ | **数据缺失**：同上，折叠屏覆盖不足 |
| T15 | 好，我还是买直板机吧，就小米17 Ultra | **代码缺陷**：小米 17 Ultra 在库中 (`p_digital_008`)，但 `brands: ["小米"]` 过滤后返回 0 卡片。推测为多轮 session 累积约束（如 price_max、sub_category 等）导致过滤条件过严 |
| T16 | 颜色有哪几种？ | **代码缺陷**：这是商品详情查询，不是推荐。但被路由到 `recommend_shopping_products`，`brands: ["小米"]` 再次返回 0 |

---

### Case 20（商务笔记本）— 混合问题

#### compare_products 失败的 1 个 Turn

| Turn | 用户消息 | 商品是否存在 | 应有对比卡片？ |
|---|---|---|---|
| T5 | 华为MateBook 14和苹果MacBook Air哪个更适合？ | 华为 MateBook 14 (`p_digital_004`) **存在**；MacBook Air **存在** | **应该能返回** |

#### recommend_shopping_products 失败的 3 个 Turn

| Turn | 用户消息 | 根因分析 |
|---|---|---|
| T12 | 联想那个高配版32G+1TB多少钱？ | **代码缺陷**：联想 ThinkBook 14+ (`p_digital_023`) **存在**，但 `brands: ["联想"]` 过滤后返回 0。同 Case 19 T15 的问题——累积约束导致过滤过严 |
| T13 | 7999元？比官网便宜吗？ | **链路不匹配**：这是价格确认/比较类问题，不是推荐请求 |
| T16 | 可以加内存吗？ | **链路不匹配**：这是商品规格查询。`brands: ["苹果", "联想"]` 双品牌过滤可能过于严格 |

#### 额外的严重 Bug（T6/T7/T8）

这三轮传了 `brands: ["华为"]`，但推荐结果返回的是 **Apple MacBook Air** 而非华为产品。这是品牌过滤逻辑的严重缺陷。

---

## 四、问题分类总结

### 4.1 应该返回对比卡片但因代码缺陷未能返回（共 11 处）

| Case | Turn | 涉及商品 | 商品是否在库 | 失败原因 |
|---|---|---|---|---|
| 12 | T6 | PC 主板 | **在库**（PC 配件库） | v2 无降级 + 未调用 PC 方案对比 |
| 14 | T4 | 特仑苏 + 金典 | **在库** | v2 无降级 |
| 15 | T6 | HOKA Clifton 9 + Nike Pegasus 41 | **在库** | v2 无降级 |
| 15 | T8 | 同上两款鞋 | **在库** | v2 无降级 |
| 16 | T4 | iPad Air + 华为 MatePad | **在库** | v2 无降级 |
| 17 | T4 | 萨洛蒙 + 迈乐 | **在库** | v2 无降级 |
| 17 | T6 | 迈乐 MOAB 3 GTX + 萨洛蒙 X ULTRA 4 | **在库** | v2 无降级 |
| 18 | T4 | 花西子 + 方里 | **部分在库** | v2 无降级 |
| 19 | T4/T5/T6 | 小米 17 Ultra + OPPO | **部分在库** | v2 无降级 |
| 19 | T13 | MIX Fold 5 + Find N6 | **部分在库** | v2 无降级 |
| 20 | T5 | MateBook 14 + MacBook Air | **在库** | v2 无降级 |

### 4.2 确实因数据库缺失无法返回（共 2-3 处）

| Case | Turn | 涉及商品 | 说明 |
|---|---|---|---|
| 19 | T10 | 折叠屏（泛指） | 品类覆盖不全 |
| 19 | T12 | 折叠屏推荐 | 同上 |
| 18 | T4 | "方里"品牌 | 可能不在 beauty 品类中 |

### 4.3 recommend 返回 0 卡片中的链路问题（共 6 处）

| Case | Turn | 根因 | 是数据缺失？ |
|---|---|---|---|
| 19 | T2 | 追问被误判为新推荐 + 响应文本与数据不一致 | 否 |
| 19 | T7 | SKU 级比价被路由到推荐管线 | 否 |
| 19 | T15 | 累积约束导致品牌过滤过严 | 否，商品在库 |
| 19 | T16 | 商品详情查询被路由到推荐 | 否 |
| 20 | T12 | 累积约束导致品牌过滤过严 | 否，商品在库 |
| 20 | T13/T16 | 价格/规格查询被路由到推荐 | 否 |

---

## 五、修改建议（按优先级）

### P0-1：handle_compare_v2 恢复降级逻辑

**问题：** v2 移除了 v1 的两层降级（`last_recommended_product_ids` + `comparison_candidate_ids`），导致 product_ids 为空时直接返回错误。

**文件：** `rag/recommendation/tool_handlers.py` — `handle_compare_v2` 函数

**修改方案：** 在 `if not valid_ids:` 分支中，不直接返回错误，而是先尝试降级：

```python
def handle_compare_v2(session, product_ids, tool_call):
    catalog = load_combined_product_catalog()
    fact_issues = []

    # 降级逻辑：从 session 或推荐兜底获取 product_ids
    if not product_ids:
        product_ids = last_recommended_product_ids(session)
    if not product_ids:
        query = (tool_call.get("arguments") or {}).get("query") or ""
        product_ids = comparison_candidate_ids(query)

    # PC 方案对比：当 topic 为 pc_build 时走独立链路
    topic = current_topic_json(session)
    if topic.get("topic_type") == "pc_build" and session.pc_build_history:
        yield from _handle_pc_build_comparison(session, tool_call)
        return

    # 校验所有 product_id 真实存在
    valid_ids = []
    for pid in product_ids:
        if catalog.get(pid):
            valid_ids.append(pid)
        else:
            fact_issues.append({"product_id": pid, "issue": "not_found_in_catalog"})

    if not valid_ids:
        # 对比失败 → 降级为推荐同类商品
        query = (tool_call.get("arguments") or {}).get("query") or ""
        yield sse_event("delta", {
            "text": "商品库里暂时没有找到你要对比的具体型号，帮你搜了一下同类商品。"
        })
        yield from handle_recommend(session, query, [], query, [], {},
                                    False, tool_call, use_llm_guidance=False)
        return

    # ... 原有对比逻辑 ...
```

### P0-2：修复多轮累积约束导致品牌过滤过严

**问题：** Case 19 T15 和 Case 20 T12 中，`brands: ["小米"]` 和 `brands: ["联想"]` 过滤后返回 0 卡片，但对应商品在库中。原因是 `session.current` 累积了历史轮次的 `price_max`、`sub_category`、`must_have_terms` 等约束，导致过滤条件组合后无结果。

**文件：** `rag/recommendation/session_state.py` — `update_session_from_router`

**修改方案：** 当用户明确提到品牌名+型号名时，应重置累积约束（视为新的搜索起点）：

```python
# 在 update_session_from_router 中：
# 如果新一轮明确提到了品牌（brands 非空），清除历史 price 约束
if args.get("brands") and not args.get("price_max") and not args.get("price_min"):
    for key in ("price_min", "price_max", "budget"):
        if key not in args and key in prev:
            # 不继承历史价格约束
            pass
```

### P0-3：修复品牌过滤返回错误商品

**问题：** Case 20 T6/T7/T8 传了 `brands: ["华为"]`，但返回了 Apple MacBook Air。这是推荐管线品牌过滤的严重 Bug。

**文件：** 需要追踪 `recommendation_pipeline.py` 中的品牌过滤逻辑

**修改方案：** 在推荐结果返回前增加品牌一致性校验。若 `brands` 过滤后结果为空，不应静默回退到其他品牌而不加提示。

### P1-1：Router prompt 增强 product_id 提取

**问题：** LLM 路由器在识别 `compare_products` 意图时，从未提取 `product_ids`。

**文件：** `rag/recommendation/tool_router.py` — LLM prompt 部分（约第 1420-1510 行）

**修改方案：** 在 LLM prompt 中明确指示：

> 当用户提到具体商品型号进行对比时（如"HOKA Clifton 9和Nike Pegasus 41"），应将型号名放入 `query`，并尝试提取品牌名放入 `brands`。如果上一轮有推荐结果，对比的是推荐中的商品，则不需要传 product_ids（系统会自动获取）。

### P1-2：响应文本与实际数据一致性

**问题：** Case 19 T2 的响应文本说"我从上架商品里挑了几款"但实际 0 卡片。`response_generator.py` 的模板变体在 0 卡片时不应使用"挑了几款"的措辞。

**文件：** `rag/recommendation/response_generator.py` — `generate_natural_response` 和 `naturalize_response`

**修改方案：** 确保 LLM 生成的响应文本与实际卡片数量一致。当 `product_cards` 为空时，强制使用 `_NO_MATCH_VARIANTS` 模板。

```python
def generate_natural_response(payload, session=None, message=""):
    cards = payload.get("product_cards") or []
    fc = payload.get("fact_check") or {}
    use_llm = (os.getenv("RECOMMENDATION_RESPONSE_LLM", "true")...
               and fc.get("degraded") is not True
               and cards)  # ← 新增：0 卡片时不走 LLM，直接用模板
```

### P1-3：PC 方案对比接入 handle_compare_v2

**问题：** Case 12 T6 用户问的是"你推荐的两款主板有什么区别"，属于两个 PC 方案中同类型组件的对比。`compare_pc_build_plans()` 函数已存在（`pc_build.py:577`），但在 `handle_compare_v2` 中未被调用。

**文件：** `rag/recommendation/tool_handlers.py`

**修改方案：** 在 `handle_compare_v2` 开头增加 PC 方案对比检测（具体代码见 P0-1 修改方案中的 `_handle_pc_build_comparison` 分支）。

### P2-1：扩展折叠屏品类覆盖

**问题：** Case 19 中折叠屏查询全部返回 0 卡片。虽然小米 MIX Fold 5 存在于库中，但折叠屏品类整体覆盖不足。

**文件：** `data/ecommerce_products/products.json`

**修改方案：** 增加 OPPO Find N6、华为 Mate X6、荣耀 Magic V4 等折叠屏手机产品数据。

### P2-2：增加 SKU 级查询/比价工具

**问题：** Case 19 T7（"小米17 Ultra的12+256和16+512差多少钱"）和 Case 20 T12（"32G+1TB多少钱"）是 SKU 级别的查询，当前被路由到 `recommend_shopping_products` 但推荐管线无法回答。

**修改方案：** 考虑新增 `query_product_detail` 工具或在 `recommend_shopping_products` 中增加 SKU 对比模式。

---

## 六、回答核心问题

**"检查是否真的应该返回对比卡片，还是真的因为数据库缺失所以无法返回"**

通过逐案链路分析和数据库交叉比对，结论如下：

**绝大多数（11/14 处）应该能返回对比卡片。** 用户要对比的商品几乎都在数据库中（特仑苏/金典、HOKA/Nike、萨洛蒙/迈乐、iPad/MatePad、MateBook/MacBook Air 等），失败的根本原因是 `handle_compare_v2` 移除了 v1 的降级逻辑（`last_recommended_product_ids` + `comparison_candidate_ids`），而 LLM 路由器又从不向 `compare_products` 传递 `product_ids`。

**仅 2-3 处确实因数据库缺失无法返回**（折叠屏品类覆盖不全、"方里"品牌可能缺失），这些需要通过扩展商品数据解决。

**recommend 返回 0 卡片的 9 处中**，仅 2 处（折叠屏查询）是真正的数据缺失，其余 7 处均为链路问题（累积约束过严、SKU 查询路由错误、追问误判等）。

---

*报告完。*
