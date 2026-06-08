# Case 2 修复后追踪报告

测试时间：2026-06-08 15:30

---

## 修复内容

给 `parse_requirement`、`infer_product_type`、`filter_products_for_requirement` 增加了 `session_context` 参数，让多轮追问时品类上下文能从 session 传递到 pipeline。

## Turn 2 对比

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| 产品数 | 3（含小米手机、OPPO手机） | **2（全是耳机）** |
| 候选池 after_exclusion | 11 | **2** |
| target_sub_categories | `[]` | **`['蓝牙耳机']`** |
| top 1 | FreeBuds Pro 5 (0.819) | FreeBuds Pro 5 (0.819) |
| top 2 | **小米 17 Max (0.807)** ❌ | **AirPods Pro 3 (0.709)** ✅ |
| top 3 | **OPPO Find X9 Ultra (0.798)** ❌ | 无 |
| 回复 | "请问您想看哪个品类的商品？" | "我优先推荐 FreeBuds Pro 5..." |

## 修复后链路

```
Turn 2: "需要防水，续航要长一点的。"
  │
  ├─ build_contextual_goal → "我想买一个跑步用的耳机...User added constraints: 需要防水..."
  │
  ├─ _build_session_context(session):
  │   topic_category = "耳机"
  │   last_target_sub_categories = ["蓝牙耳机"]
  │
  ├─ recommend_shopping_products(query, session_context={topic_category: "耳机", ...})
  │
  ├─ parse_requirement(query, session_context={...}):
  │   ├─ infer_desired_categories(query) → [beauty, digital, clothing, food]（query 中无品类词）
  │   │   兜底: session_context["topic_category"] = "耳机" → _map_category_string_to_enum → digital
  │   │   → desired_categories = [digital]
  │   ├─ infer_target_sub_categories(query) → []（query 中无子品类词）
  │   │   兜底: session_context["last_target_sub_categories"] = ["蓝牙耳机"]
  │   │   → target_sub_categories = ["蓝牙耳机"]
  │   └─ 返回 RequirementSpec(target_sub_categories=["蓝牙耳机"], desired_categories=[digital])
  │
  ├─ _resolve_session_product_type(session_context):
  │   topic_category = "耳机" → _PRODUCT_TYPE_FROM_CATEGORY["耳机"] = "earphone"
  │   → session_product_type = "earphone"
  │
  └─ build_recommendation_result(requirement, session_product_type="earphone")
      └─ filter_products_for_requirement(requirement, catalog, digital, session_product_type="earphone")
          ├─ infer_product_type(query, session_product_type="earphone")
          │   query 中无品类词 → 返回 session_product_type = "earphone"
          ├─ category_for_product_type("earphone") → "digital"
          ├─ digital == digital → 不过滤 ✓
          ├─ product_matches_type(product, "earphone") → 只保留耳机类
          └─ 结果: 2 个产品（FreeBuds Pro 5, AirPods Pro 3）✅
```

## 关键改动

| 文件 | 改动 |
|------|------|
| `query_guards.py` | `infer_product_type()` 增加 `session_product_type` 参数兜底 |
| `structured_filter.py` | `filter_products_for_requirement()` 增加 `session_product_type` 参数 |
| `package_builder.py` | `build_recommendation_result()` 和 `score_required_components()` 传递 `session_product_type` |
| `recommendation_pipeline.py` | `recommend_shopping_products()` 和 `parse_requirement()` 增加 `session_context` 参数；新增 `_resolve_session_product_type()` 和 `_map_category_string_to_enum()` |
| `tool_handlers.py` | `handle_recommend()` 提取 `session_context`；`call_recommendation_fn()` 传递 `session_context` |
