# Case 2 链路追踪报告：为什么"跑步耳机"第2轮混入小米手机？

追踪时间：2026-06-08

---

## 一、Turn 1 完整链路："我想买一个跑步用的耳机，有什么推荐？"

### 1.1 路由层

```
local_route_tool_call()
  → detect_normal_product_category("我想买一个跑步用的耳机") → "耳机"
  → _tool_call("recommend_shopping_products", category="耳机", confidence=0.86)
  → should_skip_llm_route() → confidence=0.86 >= 0.78, margin 足够 → True（跳过 LLM）
  → guard: category="耳机" 已明确，不覆盖
  → 最终路由: recommend_shopping_products, source=guard
```

### 1.2 Pipeline 层

```
handle_recommend()
  → prepare_recommendation_context() → contextual_goal = "我想买一个跑步用的耳机，有什么推荐？"
  → recommend_shopping_products(contextual_goal)
    → parse_requirement("我想买一个跑步用的耳机，有什么推荐？")
      → infer_desired_categories(): "耳机" in CATEGORY_KEYWORDS → [digital]
      → infer_target_sub_categories(): "蓝牙耳机" in text → ["蓝牙耳机"]
      → infer_must_have_terms(): "跑步" in text → ["跑步"]
      → 返回 RequirementSpec:
          desired_categories: [digital]
          target_sub_categories: ["蓝牙耳机"]
          must_have_terms: ["跑步"]
    → build_recommendation_result(requirement)
      → filter_products_for_requirement(requirement, catalog, digital)
        → infer_product_type("我想买一个跑步用的耳机") → "earphone"
        → category_for_product_type("earphone") → "digital"
        → digital == digital → 不过滤 ✓
        → product_matches_type(product, "earphone") → 只保留耳机类
        → must_have: "跑步" in product text → 进一步筛选
        → 最终: 1 个产品 (AirPods Pro 3, score=0.929)
```

**结果**：1 个卡片（AirPods Pro 3），品类正确。

### 1.3 Session 更新

```
update_topic_memory():
  topic_type = "single_product_recommendation"
  subject = "耳机"
  category = "耳机"
  slots = {catalog_scope: "ecommerce", ...}

remember_recommendation():
  last_goal = "我想买一个跑步用的耳机，有什么推荐？"
  last_result = {product_cards: [AirPods Pro 3], ...}
```

---

## 二、Turn 2 完整链路："需要防水，续航要长一点的。"

### 2.1 上下文构建（关键断点 #1）

```
build_contextual_goal(session, "需要防水，续航要长一点的。"):
  session.last_goal = "我想买一个跑步用的耳机，有什么推荐？"
  should_start_new_product_topic() → topic != pc_build → False
  looks_like_followup("需要防水，续航要长一点的。") → len=12 ≤ 12 → True
  base_goal = "我想买一个跑步用的耳机，有什么推荐？"  （去掉 ". User added constraints:" 后缀）
  return "我想买一个跑步用的耳机，有什么推荐？. User added constraints: 需要防水，续航要长一点的。"
```

**contextual_goal 构建正确** ✅ — 保留了"耳机"上下文。

### 2.2 路由层（关键断点 #2）

```
route_shopping_tool_call(contextual_goal):
  local_route = local_route_tool_call(contextual_goal)
    → detect_normal_product_category("我想买一个跑步用的耳机...") → "耳机"
    → _tool_call("recommend_shopping_products", category="耳机", confidence=0.9)

  should_skip_llm_route() → confidence=0.9 >= 0.78 → True（跳过 LLM）

  但 LLM 被调用了！（router_attempted=True, router_success=True）
  → LLM 返回: name="recommend_shopping_products", category="耳机", must_have_terms=["防水","长续航"]

  关键差异:
  ├─ local.arguments.query = "我想买一个跑步用的耳机，有什么推荐？. User added constraints: 需要防水，续航要长一点的。"
  ├─ llm.arguments.query   = "需要防水，续航要长一点的。"  ← LLM 只提取了追问部分！
  └─ llm.arguments.category = "耳机"  ← LLM 正确提取了品类

  chosen = llm (confidence=0.9 >= 0.50)
  → merge_route_arguments(llm_args, rule_args)
  → 最终 query = "需要防水，续航要长一点的。"  ← 覆盖了 contextual_goal！
```

**问题暴露**：LLM 路由器只提取了追问部分作为 query，丢弃了上下文中"耳机"和"跑步"的信息。`merge_route_arguments` 用 LLM 的 query 覆盖了规则的 query。

### 2.3 Pipeline 层（关键断点 #3）

```
recommend_shopping_products("需要防水，续航要长一点的。")
  → parse_requirement("需要防水，续航要长一点的。")
    → infer_desired_categories(): 
      - "防水" 不在 CATEGORY_KEYWORDS 中
      - "续航" 不在 CATEGORY_KEYWORDS 中
      → 返回 [] → 默认全部 4 个品类 [beauty, digital, clothing, food]
    → infer_target_sub_categories():
      - "蓝牙耳机" 不在 "需要防水，续航要长一点的。" 中
      → 返回 []  ← 品类子类型丢失！
    → infer_must_have_terms():
      - "防水" in text → ["防水"]
      - "续航" 不在 must_have_terms 列表中（有"续航"但不在 infer_must_have_terms 中）
        实际检查: "续航" 不在 infer_must_have_terms 的 keyword 列表中
        但 "长续航" 也不在
      → 返回 [] 或 ["防水"]
    → 实际 RequirementSpec:
        desired_categories: [beauty, digital, clothing, food]
        target_sub_categories: []  ← 空！
        must_have_terms: ["防水", "长续航"]  ← 由 LLM 路由器提取
```

**问题暴露**：`parse_requirement()` 只看当前 query 文本，不知道上文是"耳机"。`target_sub_categories` 丢失。

### 2.4 结构化过滤（关键断点 #4）

```
filter_products_for_requirement(requirement, catalog, digital):
  infer_product_type("需要防水，续航要长一点的。") → None
  └─ "需要防水，续航要长一点的。" 不包含任何 PRODUCT_TYPE_TERMS 中的词
     （"耳机""airpods""手机""iphone" 等都不在文本中）
  → product_type_category = None
  → product_type_filter_applied = False  ← 不过滤品类！

  must_have_filtered = products where "防水" AND "长续航" in text
  → "长续航" 出现在很多手机的标题中:
    - 小米 17 Max "大屏长续航高性能影音游戏5G智能手机" ✓
    - OPPO Find X9 Ultra "超大底影像旗舰2K高刷屏长续航5G智能手机" ✓
    - OPPO Reno 16 Pro "轻薄人像摄影高刷屏快充5G智能手机" ✓
    - 华为 FreeBuds Pro 5 "主动降噪真无线蓝牙耳机" ← 没有"长续航"！
  → 但实际上 FreeBuds Pro 5 也被返回了（score=0.819），说明 "长续航" 匹配逻辑可能有宽松回退
```

**结果**：11 个 digital 产品通过过滤（包含手机、平板、耳机），top 3 是 FreeBuds Pro 5、小米 17 Max、OPPO Find X9 Ultra。

---

## 三、根因链路图

```
Turn 1: "我想买一个跑步用的耳机"
  │
  ├─ parse_requirement → target_sub_categories=["蓝牙耳机"], must_have=["跑步"]
  ├─ infer_product_type → "earphone" → 过滤只保留耳机
  └─ 结果: AirPods Pro 3 ✅
  │
  │ session.last_goal = "我想买一个跑步用的耳机..."
  │ session.topic_memory.category = "耳机"
  │
  ▼
Turn 2: "需要防水，续航要长一点的。"
  │
  ├─ build_contextual_goal → "我想买一个跑步用的耳机...User added constraints: 需要防水..." ✅
  │
  ├─ 【断点 #1】LLM 路由器只提取追问部分
  │   llm.arguments.query = "需要防水，续航要长一点的。"  ← 丢弃了"耳机"上下文
  │   merge_route_arguments → 最终 query = "需要防水，续航要长一点的。"
  │
  ├─ 【断点 #2】parse_requirement 只看当前 query
  │   target_sub_categories = []  ← "蓝牙耳机" 丢失
  │   must_have_terms = ["防水", "长续航"]
  │
  ├─ 【断点 #3】infer_product_type 从 query 文本推断
  │   "需要防水，续航要长一点的。" → None  ← 无品类关键词
  │   product_type_filter_applied = False  ← 不过滤品类
  │
  └─ 结果: 小米 17 Max（手机）、OPPO Find X9 Ultra（手机）混入 ❌
```

---

## 四、三个断点的详细分析

### 断点 #1：LLM 路由器丢弃上下文

**位置**：`tool_router.py` → `try_llm_route_tool_call()` → `build_route_prompt()`

**现象**：LLM 从 contextual_goal 中只提取了追问部分作为 query，丢弃了"耳机"上下文。

**原因**：`build_route_prompt()` 将完整的 contextual_goal 发给 LLM，但 LLM 的输出 schema 中 query 字段被理解为"用户本轮输入"，而非完整上下文。LLM 正确提取了 `category: "耳机"`，但 query 只写了追问部分。

**影响**：`merge_route_arguments()` 用 LLM 的 query 覆盖了规则的 query，导致 pipeline 收到的 query 丢失了品类上下文。

### 断点 #2：parse_requirement 不感知 session

**位置**：`recommendation_pipeline.py` → `parse_requirement()`

**现象**：`parse_requirement()` 只从当前 query 文本提取 `target_sub_categories`，不知道 session 的 `topic_memory.category = "耳机"`。

**原因**：`parse_requirement()` 是无状态函数，不接收 session 参数。它只能从文本中提取品类信息。

**影响**：`target_sub_categories` 丢失，后续过滤无法锁定"蓝牙耳机"子品类。

### 断点 #3：infer_product_type 只看当前 query

**位置**：`query_guards.py` → `infer_product_type()`

**现象**：`infer_product_type("需要防水，续航要长一点的。")` 返回 None，因为文本中没有"耳机""airpods"等品类关键词。

**原因**：`infer_product_type()` 只检查当前 query 文本中的 PRODUCT_TYPE_TERMS，不参考 session 上下文。

**影响**：`product_type_filter_applied = False`，所有 digital 品类产品（包括手机、平板）都能通过过滤。

---

## 五、Session 状态追踪

### Turn 1 后的 session 状态

```
session.last_goal = "我想买一个跑步用的耳机，有什么推荐？"
session.last_result = {
  product_cards: [AirPods Pro 3],
  requirement: {desired_categories: [digital], target_sub_categories: ["蓝牙耳机"], ...}
}
session.topic_memory = {
  topic_type: "single_product_recommendation",
  subject: "耳机",
  category: "耳机",
  route: "recommend_shopping_products",
  slots: {catalog_scope: "ecommerce", ...}
}
session.messages = ["我想买一个跑步用的耳机，有什么推荐？"]
```

### Turn 2 中 session 的使用情况

| session 数据 | 是否被使用 | 使用位置 | 效果 |
|-------------|-----------|---------|------|
| `last_goal` | ✅ | `build_contextual_goal()` | 正确构建 contextual_goal |
| `topic_memory.category` | ✅ | `build_route_prompt()` | LLM prompt 中有 "耳机" |
| `topic_memory.slots` | ❌ | — | 未传递给 pipeline |
| `last_result.product_cards` | ❌ | — | 未传递给 pipeline |
| `last_result.requirement.target_sub_categories` | ❌ | — | 未传递给 pipeline |
| `messages` | ❌ | — | 未传递给 pipeline |

**关键发现**：session 的 `topic_memory` 和 `last_result` 中有完整的品类上下文（"耳机"、`target_sub_categories: ["蓝牙耳机"]`），但这些信息**没有传递给 `recommend_shopping_products()` 的 `parse_requirement()`**。

---

## 六、总结

**根因**：多轮追问时，品类上下文在三层之间断裂：

1. **路由层**：LLM 路由器正确提取了 `category: "耳机"`，但 query 只写了追问部分（丢弃了品类上下文）
2. **Pipeline 入口**：`recommend_shopping_products()` 收到的 query 是 "需要防水，续航要长一点的。"，没有品类信息
3. **需求解析层**：`parse_requirement()` 从 query 文本中提取不到 "蓝牙耳机"（`target_sub_categories` 为空）
4. **品类推断层**：`infer_product_type()` 从 query 文本中推断不出品类（`product_type_filter_applied = False`）
5. **过滤层**：没有品类约束 → "长续航" 匹配了手机 → 小米 17 Max 混入

**session 有数据但没用上**：session 的 `topic_memory.category = "耳机"` 和 `last_result.requirement.target_sub_categories = ["蓝牙耳机"]` 都存在，但 `recommend_shopping_products()` 不读取这些数据。

**修复方向**（不改代码，仅分析）：
- 方案 A：让 `recommend_shopping_products()` 接受 `session` 参数，从中读取 `target_sub_categories` 作为兜底
- 方案 B：让 `build_contextual_goal()` 在 contextual_goal 中显式标注品类（如 "[品类: 耳机] 我想买一个跑步用的耳机...User added constraints: 需要防水..."）
- 方案 C：让 LLM 路由器的 `merge_route_arguments()` 保留规则的 query（而非用 LLM 的 query 覆盖）
