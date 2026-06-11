# 边界测试 v3 问题报告

**日期:** 2026-06-11  
**测试范围:** 21 cases / 136 turns  
**通过率:** 路由 100% / 错误 0  
**对话记录:** [bound_test_v3_20260611_141356_raw_qa.md](bound_test_v3_20260611_141356_raw_qa.md)

---

## 总览

全量测试路由成功率 100%，无 SSE 错误事件。但发现 **23 处次优行为**，分属两大类：

| 类型 | 数量 | 影响 |
|------|------|------|
| `compare_products` 返回空结果 | **14** | 用户看到空对比表，体验差 |
| `recommend_shopping_products` 返回 0 卡片 | **9** | 用户看到"无匹配"，未能正确兜底 |

---

## 问题一：compare_products 高频返回空结果（14 处）

### 现象

涉及 Case 12/14/15/16/17/18/19/20 共 8 个 case：

```
Case12 T6: "你推荐的两款主板有什么区别？" → compare_products → 0 cards
Case14 T4: "特仑苏和金典哪个更适合？" → compare_products → 0 cards
Case15 T6: "HOKA Clifton 9和Nike Pegasus 41哪个更软？" → compare_products → 0 cards
Case17 T4: "萨洛蒙和迈乐哪个抓地力更好？" → compare_products → 0 cards
...
```

### 根因分析

三层原因叠加：

**1. Router 正确识别对比意图，但 LLM 无法提取 product_ids**

对比请求需要具体的 product_id（如 `p_digital_001`）才能执行。但 LLM router 在用户提到品牌名/型号名（"HOKA Clifton 9"、"华为MateBook"）时，无法将其映射到商品库中的实际 product_id。`product_ids` 字段为空。

**2. 降级逻辑 `comparison_candidate_ids()` 依赖推荐兜底**

当 product_ids 为空时，`handle_compare`（及 v2）调用 `comparison_candidate_ids()`：
```python
result = recommend_shopping_products(query, use_llm=False, ...)
```
这个函数用规则模式做推荐搜索，但：
- 规则解析器不认识 "HOKA Clifton 9" 这样的具体型号名
- 返回的结果与用户想对比的商品不匹配
- 若推荐也返回空，对比就彻底失败

**3. 商品库缺少对应品类**

"HOKA Clifton 9"、"Nike Pegasus 41"、"小米MIX Fold 5"、"华为MateBook 14 鸿蒙版"——这些具体型号不在商品库中。即使对比逻辑正确，库中没有商品就无法对比。

### 修改建议

**P0：增加 "对比意图 → 推荐兜底" 的语义化降级**

当 `compare_products` 返回空结果时，自动切换为推荐模式，向用户说明"没有找到你提到的具体型号，但以下是同类商品"。

**文件：** `rag/recommendation/tool_handlers.py` `handle_compare_v2`

```python
# 在 compare_products 返回空后
if not compare_result.get("rows"):
    # 🟢 降级：对比失败 → 自动推荐同类商品
    query = (tool_call.get("arguments") or {}).get("query") or ""
    yield sse_event("delta", {
        "text": f"商品库里暂时没有找到你要对比的具体型号，帮你搜了一下同类商品。"
    })
    # 切换为推荐模式
    result = recommend_shopping_products(query, catalog_scope="combined", use_llm=True)
    ...
```

**P1：Router prompt 增加模型名→品牌推理规则**

当用户提到具体型号名时，LLM 应提取品牌名作为关键字而非留空 product_ids：

> "如果用户提到具体产品型号（如'HOKA Clifton 9'），请将型号名放入 query，并提取品牌名（HOKA）放入 brands。不要强行猜测 product_id。"

---

## 问题二：recommend 返回 0 卡片（9 处）

### 现象

集中在 Case 19（游戏手机长对话）和 Case 20（商务笔记本）：

```
Case19 T2:  "要散热好一点，不发热降频的" → recommend → 0 cards
Case19 T10: "算了，我要不要考虑一下折叠屏？" → recommend → 0 cards
Case19 T15: "好，我还是买直板机吧，就小米17 Ultra" → recommend → 0 cards
Case20 T12: "联想那个高配版32G+1TB多少钱？" → recommend → 0 cards
Case20 T16: "可以加内存吗？" → recommend → 0 cards
```

### 根因分析

**1. 追问被当作全新推荐（Case19 T2/T15, Case20 T16）**

用户在多轮对话中的追问（"散热好一点"、"就小米17 Ultra"、"可以加内存吗"）被 router 识别为 `recommend_shopping_products`，但这些是**对已有推荐结果的细化追问**，不是新的推荐请求。

问题在于 `looks_like_followup()` 收紧后（本次改造），部分追问不再被判定为 followup，导致 `build_contextual_goal()` 将其当作全新目标处理。新品类的过滤条件与历史上下文组合后，可能产生空结果。

**2. 商品库品类缺失（Case19 T10 "折叠屏"）**

商品库中没有折叠屏手机品类，返回 0 卡片是预期行为。但回复文本太生硬：

> "当前商品库没有找到该品类 7000 CNY 以内的合适商品"

应改为引导性回复。

**3. 具体 SKU 信息查询（Case20 T12 "32G+1TB多少钱"）**

用户查询的是具体配置的价格，这是 inventory 查询而非推荐。Router 将其路由为 `recommend_shopping_products` 是正确的（因为没有单独的 "查询价格" 工具），但推荐管线无法回答"多少钱"。

### 修改建议

**P0：followup 检测需要上下文感知**

当前 `looks_like_followup()` 只看消息文本，不看 session 状态。应改为：如果上一轮有推荐结果（`session.last_result` 非空），且当前消息是短追问（≤20字符），则优先判定为 followup。

**文件：** `rag/recommendation/session_state.py`

```python
def looks_like_followup(message: str, session: ShoppingSession = None) -> bool:
    text = message.strip()
    # 🟢 新增: 有历史推荐结果 + 短消息 → 很可能是追问
    if session and getattr(session, "last_result", None):
        last_cards = (session.last_result or {}).get("product_cards") or []
        if last_cards and len(text) <= 20:
            return True
    # ... 原有逻辑 ...
```

**P1：0 卡片时生成引导性回复**

在 `response_generator.naturalize_response` 中，针对不同的 `no_match_reason` 给出不同的降级回复：

```python
_GUIDED_FALLBACKS = {
    "budget_catalog_gap": "这个预算下暂时没有合适的，要不放宽到 {suggested_budget} 看看？",
    "unsupported_category": "你提到的品类目前商品库中还没有收录，换个相近的品类试试？",
    "safety_restricted_category": "这个品类暂不支持推荐，看看其他商品？",
}
```

**P2（数据）：扩展商品库品类覆盖**

折叠屏、具体笔记本配置 SKU 等品类目前缺失。这是一个数据问题，需要扩展 `products.json` 的数据覆盖。

---

## 问题三：回复开头的 "我先按你的需求筛一遍商品库" 仍为硬编码

### 现象

每条回复的**第一条 delta** 永远是：

> "我先按你的需求筛一遍商品库，优先找最相关的真实商品。"

这不是来自 `response_generator`，而是 `chat.py:151` 中的 `build_chat_opening()`。

### 修改建议

将 `chat.py:151` 的固定开头也交给 `response_generator` 处理，或在 `chat.py` 中移除它，让 `handle_recommend` 中的 `generate_natural_response` 全权负责所有 delta 文本。

**文件：** `rag/api/routes/chat.py` 行 151

```python
# 修改前
yield sse_event("delta", {"text": build_chat_opening(raw_message, session)})

# 修改后 — 移除固定开头，交给 response_generator 处理
# （handle_recommend 中的 generate_natural_response 已经包含开场白）
```

---

## 问题四：购物车 v2 确认模式在边界测试中未触发

### 现象

所有 `apply_cart_instruction` 的调用（Case 14/17/19/20/21 共 7 处）都使用了 v2 的 `handle_cart_v2()`，返回了 `cart_confirmation` 事件。但测试脚本（`run_bound_test.py`）没有模拟用户确认步骤，因此购物车实际未写入。

### 影响

这是测试脚本的局限，不是链路问题。但暴露了一个设计缺陷：**`POST /api/chat/stream` 返回 `cart_confirmation` 后，前端需要额外调用 `POST /api/cart/confirm` 才能生效**，而旧版前端可能不知道这个新端点。

### 修改建议

在 `cart_confirmation` SSE 事件中增加 `auto_confirm_url` 字段，让前端可以直接跳转。同时在 `.env` 中保留 `CART_CONFIRMATION_REQUIRED` 开关，允许在测试/调试环境下跳过确认：

```python
if not os.getenv("CART_CONFIRMATION_REQUIRED", "true").lower() == "false":
    # v2 确认模式
else:
    # v1 直接执行模式
```

---

## 问题五：PC 方案对比仍返回空

### 现象

Case 12 T6 "你推荐的两款主板有什么区别？" 路由到 `compare_products` 但返回空。Case 21 T8 "你推荐的两个装机配置有什么区别？" 同样。

### 根因

PC 方案对比（两个 `pc_build_plan` 之间）与商品对比（两个 `product_id` 之间）是不同的概念。当前的 `compare_products` 只能对比商品库中的具体 product_id，无法对比两个 PC 配置方案。`compare_pc_build_plans()` 函数存在（`pc_build.py:568`），但在 `handle_compare` / `handle_compare_v2` 中没有被调用。

### 修改建议

在 `handle_compare_v2` 中，当检测到当前话题为 PC 构建时，调用 `compare_pc_build_plans()` 而非 `compare_products()`：

```python
if topic_type == "pc_build" and session.pc_build_history:
    # 对比两个 PC 方案
    current_plan = session.pc_build_history[-1]
    previous_plan = session.pc_build_history[-2] if len(session.pc_build_history) >= 2 else None
    if previous_plan:
        comparison = compare_pc_build_plans(current_plan, previous_plan)
        ...
```

---

## 总结

| 优先级 | 问题 | 影响范围 | 修改难度 |
|--------|------|----------|----------|
| **P0** | compare_products 返回空 — 需语义降级 | 8 个 case / 14 轮 | 中 |
| **P0** | recommend 返回 0 卡片 — followup 检测 | 4 个 case / 9 轮 | 低 |
| **P1** | 回复开头硬编码 | 全量 136 轮 | 低 |
| **P1** | 0 卡片引导性回复 | 全量 | 低 |
| **P1** | PC 方案对比不支持 | Case 12/21 | 中 |
| **P2** | 购物车确认与旧前端兼容 | 7 轮 | 低 |
| **P2** | 商品库品类扩展（折叠屏等） | 数据层面 | 中 |

---

*报告完。*
