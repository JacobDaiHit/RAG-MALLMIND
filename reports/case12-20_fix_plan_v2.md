# Case 12-20 链路分析与修改方案（修订版 v2）

**日期:** 2026-06-11  
**版本:** v2（根据审阅意见修订）  
**关联报告:** [bound_test_v3_issues.md](bound_test_v3_issues.md)、[v1 版报告](case12-20_link_analysis_and_fix_report.md)

---

## 零、v1 版主要错误清单

在展开修订方案前，先明确 v1 版中被审阅指出的错误，本修订版逐一修正：

| 原编号 | 位置 | 错误类型 | 问题描述 |
|---|---|---|---|
| E1 | P0-1 | 代码错误 | `yield from handle_recommend(...)` 参数数量、顺序均错误，`handle_recommend` 签名为 8 个位置参数 + keyword-only，v1 传了错乱的 8 个 |
| E2 | P0-1 | 逻辑缺失 | `_handle_pc_build_comparison` 仅提及名称，未给出任何实现 |
| E3 | P0-1 | 设计缺陷 | 降级时直接用上一轮 product_ids 而不校验有效性，也未考虑商品可能已下架 |
| E4 | P0-2 | 逻辑错误 | 清空价格约束的条件 `if args.get("brands")` 过于宽泛，循环内仅 `pass` 未实际移除 |
| E5 | P0-2 | 覆盖不全 | 仅关注价格，未处理 `sub_category`、`must_have_terms` 等其他累积约束 |
| E6 | P0-3 | 方向错误 | 未追查品牌过滤失效根因，仅建议增加事后校验 |
| E7 | P1-1 | 无效修改 | `query` 字段在 `handle_compare_v2` 中未被用于搜索商品 ID |
| E8 | P1-2 | 假设遗漏 | 未确认 `_NO_MATCH_VARIANTS` 模板内容是否匹配空结果场景 |
| E9 | P1-3 | 设计缺陷 | 复用商品对比 handler 处理 PC 组件对比，数据结构不一致 |
| E10 | P2-2 | 职责过重 | 在推荐函数中塞入 SKU 差价查询，违反单一职责 |

---

## 一、新发现：品牌过滤的核心根因

在 v1 报告中，P0-3 仅指出"品牌过滤返回了错误商品"但未追查根因。本次重新审阅源码后发现了一个**之前完全遗漏的核心 bug**：

### 1.1 `RequirementSpec.brands` 在过滤管线中不作为硬过滤条件

**完整过滤链路：**

```
Router args.brands
  → update_session_from_router()   累积到 session.current["brands"]
  → recommend_shopping_products()  调用 _requirement_from_args_v2()
  → _requirement_from_args_v2()    构建 RequirementSpec(brands=["华为"])
  → filter_products_for_requirement()  ← 关键断点
      → 库存过滤 is_available()
      → 排除过滤 violates_brand_or_text_exclusion()  ← 只检查 excluded_terms，不检查 brands！
      → 子品类过滤 matches_target_sub_category()
      → 关键词过滤 matches_all_required_terms()
      → 预算过滤 matches_budget()
      → LLM 语义过滤 _llm_filter_products()  ← 只在 excluded_brands 非空时触发
```

`violates_brand_or_text_exclusion` 的实际实现（`structured_filter.py:217-229`）：

```python
def violates_brand_or_text_exclusion(requirement: RequirementSpec, product: ApiProduct) -> bool:
    """Check text-based exclusion only.

    Brand exclusion is now handled by the LLM filter layer, which can
    understand semantic relationships (e.g. sub-brands, aliases) that a
    simple string match cannot.
    """
    text = collect_product_text(product)
    for term in requirement.excluded_terms:   # ← 只检查 excluded_terms
        key = normalize(term)
        if key and key in text:
            return True
    return False
    # requirement.brands（品牌白名单）在整个函数中从未被使用
```

### 1.2 `brands` 仅在 scorer 中做加分（soft boost），不做硬过滤

**scorer.py 第 412-417 行**（relevance scorer）：

```python
if product.brand:
    if requirement.brands:
        if product.brand in requirement.brands:
            score += 0.15    # ← 仅加 0.15 分，不排除非匹配品牌
```

**scorer.py 第 451-455 行**（attribute scorer）：

```python
if requirement.brands:
    if product.brand in requirement.brands:
        score += 0.10    # ← 仅加 0.10 分
```

### 1.3 Case 20 T6/T7/T8 的真正根因

当 router 传 `brands=["华为"]` 时：

1. `RequirementSpec.brands = ["华为"]` 被正确设置
2. `filter_products_for_requirement()` 中，**华为产品没有被优先保留，非华为产品也没有被排除**——因为 brands 不参与硬过滤
3. scorer 给华为产品加了 0.15 分，但 MacBook Air 可能在其他维度（品类匹配、关键词、价格等）得分更高
4. 最终 top-N 结果中出现 MacBook Air 而非华为产品

**结论：这不是"品牌过滤逻辑有 bug"，而是"品牌白名单过滤根本不存在"。**

---

## 二、修改方案

### P0-1：handle_compare_v2 恢复降级逻辑

**问题重述：** v2 在 `product_ids` 为空时直接返回 `"商品不存在"` 错误，完全无降级。LLM 路由器从不向 `compare_products` 传递 `product_ids`，导致 14 次对比全部失败。其中 11 次涉及的商品在数据库中存在。

**涉及文件：** `rag/recommendation/tool_handlers.py` — `handle_compare_v2` 函数（第 196-248 行）

**修订方案：**

```python
def handle_compare_v2(session: Any, product_ids: List[str], tool_call: Dict[str, Any]) -> Iterable[str]:
    """Compare products v2: with fallback chain + fact checks."""
    catalog = load_combined_product_catalog()
    fact_issues: List[Dict[str, Any]] = []
    arguments = tool_call.get("arguments") or {}

    # ── 降级链：三级回退 ──
    if not product_ids:
        # 降级 1：从 session 上一轮结果中提取 product_ids
        product_ids = last_recommended_product_ids(session)

    if not product_ids:
        # 降级 2：用 query 关键词走推荐管线获取候选 ID
        query = str(arguments.get("query") or "").strip()
        if query:
            product_ids = comparison_candidate_ids(query, limit=3)

    if not product_ids:
        # 降级 3：如果 session 话题是 PC 装机，尝试对比最近两个方案
        topic = current_topic_json(session)
        if topic.get("topic_type") == "pc_build" and len(session.pc_build_history) >= 2:
            yield from _emit_pc_build_comparison(session, tool_call)
            return

    # ── 校验所有 product_id 真实存在于 catalog ──
    valid_ids = []
    for pid in product_ids:
        if catalog.get(pid):
            valid_ids.append(pid)
        else:
            fact_issues.append({"product_id": pid, "issue": "not_found_in_catalog"})

    if not valid_ids:
        # 所有降级均失败 → 降级为推荐同类商品
        query = str(arguments.get("query") or arguments.get("category") or "").strip()
        if query:
            yield sse_event("delta", {
                "text": "商品库里暂时没有找到你要对比的具体型号，帮你搜了同类商品。"
            })
            # 直接调用 recommend_shopping_products 构造结果
            try:
                result = recommend_shopping_products(
                    query,
                    use_llm=False,
                    use_llm_guidance=False,
                    catalog_scope="combined",
                    use_milvus_retrieval=False,
                    session=session,
                )
                payload = model_to_dict(result)
                cards = payload.get("product_cards") or []
                if cards:
                    yield sse_event("product_cards", {"cards": cards})
                yield sse_event("delta", {
                    "text": naturalize_response({"product_cards": cards}).get("text", "")
                })
            except Exception:
                yield sse_event("error", {
                    "label": "对比失败",
                    "detail": "未能找到可对比的商品，请尝试指定具体型号。"
                })
        else:
            yield sse_event("error", {
                "label": "商品不存在",
                "detail": "所有待对比商品均未在商品库中找到。"
            })
        yield sse_event("done", {"session_id": session.session_id})
        return

    # ── 原有 v2 逻辑：同品类检测、价格区间检测、事实校验 ──
    # ... (保持不变，从第 214 行开始) ...
```

**对 v1 错误的修正：**

1. **修正 E1（参数错误）**：不再调用 `handle_recommend`（其签名复杂且含 8 个位置参数 + keyword-only），改为直接调用 `recommend_shopping_products()` 并手动构造 SSE 事件。函数签名 `recommend_shopping_products(user_goal, use_llm, ..., session)` 的参数都是 keyword 友好的，不会出错。

2. **修正 E3（stale product_ids）**：`last_recommended_product_ids(session)` 返回的 ID 仍然要经过 `catalog.get(pid)` 校验（第 213-217 行），如果商品已下架（不在 catalog 中），会被加入 `fact_issues` 并被排除。降级到 `comparison_candidate_ids` 时，该函数调用 `recommend_shopping_products` 实时搜索，返回的是当前可用商品。

3. **修正 E2（PC 对比未实现）**：见下方 P1-3 的完整实现。

---

### P0-2：品牌白名单硬过滤缺失（新发现，升级为核心修复）

**问题重述：** `RequirementSpec.brands` 在过滤管线中不作为硬过滤条件，仅做 scorer 加分。用户指定 `brands=["华为"]` 时，非华为产品（如 MacBook Air）不会被排除。

**涉及文件：** `rag/recommendation/structured_filter.py` — `filter_products_for_requirement` 函数

**修订方案：** 在 `filter_products_for_requirement` 中，在 `exclusion_filtered`（第 86-90 行）之后、`target_filtered`（第 91-97 行）之前，增加品牌白名单硬过滤：

```python
def filter_products_for_requirement(
    requirement: RequirementSpec,
    products: Iterable[ApiProduct],
    category: ComponentCategory,
) -> tuple[List[ApiProduct], FilterDiagnostics]:
    """Apply structured constraints for one category with safe fallback."""

    raw = [product for product in products if product.category == category]
    stock_filtered = [product for product in raw if is_available(product)]
    exclusion_filtered = [
        product
        for product in stock_filtered
        if not violates_brand_or_text_exclusion(requirement, product)
    ]

    # ── 新增：品牌白名单硬过滤 ──
    if requirement.brands:
        brand_filtered = [
            product for product in exclusion_filtered
            if _matches_brand_requirement(product, requirement.brands)
        ]
        # 安全降级：如果品牌过滤后为空，保留过滤前结果，但记录降级原因
        if brand_filtered:
            exclusion_filtered = brand_filtered
        # 否则保持 exclusion_filtered 不变（品牌过滤后为空则忽略该约束）

    target_filtered = [
        product
        for product in exclusion_filtered
        if matches_target_sub_category(requirement, product)
    ]
    # ... 后续逻辑不变 ...
```

**品牌匹配函数：**

```python
def _matches_brand_requirement(product: ApiProduct, brands: List[str]) -> bool:
    """Check if a product's brand matches any of the required brands.

    Uses normalized comparison to handle sub-brands and aliases.
    E.g. brands=["华为"] should match product.brand="HUAWEI" or "华为".
    """
    if not product.brand:
        return False
    product_brand_norm = normalize(product.brand)
    for required_brand in brands:
        required_norm = normalize(required_brand)
        if required_norm and required_norm in product_brand_norm:
            return True
        if required_norm and product_brand_norm in required_norm:
            return True
    return False
```

这里使用 `normalize`（已有的工具函数：`"".join(ch.lower() for ch in str(value) if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")`）做双向子串匹配，能处理 "HUAWEI" vs "华为"、"Apple" vs "苹果" 等别名情况，也能匹配子品牌（如 "荣耀" 包含在 "荣耀终端" 中）。

**为什么不用 LLM 做品牌白名单过滤：** `_llm_filter_products` 当前仅处理 `excluded_brands`（排除），因为排除需要理解子品牌和别名。白名单过滤用确定性匹配 + 安全降级（匹配后为空则忽略）即可满足需求，且不增加 LLM 调用开销。

---

### P0-3：多轮累积约束导致过滤过严

**问题重述：** Case 19 T15（"就小米17 Ultra"，`brands=["小米"]`）和 Case 20 T12（"联想那个高配版"，`brands=["联想"]`）中，商品在库中但推荐返回 0 卡片。根因是 `update_session_from_router` 跨轮累积了 `price_max`、`sub_category`、`must_have_terms` 等约束，组合后过滤条件过严。

**涉及文件：** `rag/recommendation/session_state.py` — `update_session_from_router`（第 428-549 行）

**修订方案：引入显式清除语义，而非隐式清空**

系统已有 `_CLEAR_SENTINEL = "__CLEAR__"` 机制（`recommendation_pipeline.py:227`），LLM 路由器可以通过输出 `"price_max": "__CLEAR__"` 来表达"清除价格约束"。问题在于 `update_session_from_router` 不处理这个 sentinel，以及路由器 prompt 中未告知 LLM 有此机制。

**修改 1：`update_session_from_router` 支持 `__CLEAR__` sentinel**

在 `update_session_from_router` 的价格合并部分（当前第 506-511 行），改为：

```python
    # price: new values override old; __CLEAR__ explicitly clears
    _CLEAR = "__CLEAR__"
    for key in ("price_min", "price_max", "budget"):
        val = args.get(key)
        if val == _CLEAR:
            # 显式清除：不继承历史值
            pass  # 不写入 new_current，等于清空
        elif val is not None:
            new_current[key] = val
        elif key not in new_current:
            new_current[key] = prev.get(key)
```

**修改 2：Router prompt 增加 `__CLEAR__` 说明**

在 `tool_router.py` 的 LLM router prompt 中增加：

```
当用户明确切换品牌或型号时（如"算了，我买小米"、"不要价格限制了"），
对于不再适用的历史约束，输出 "__CLEAR__" 来显式清除。
例如：用户之前设了 price_max=5000，现在说"就买小米17 Ultra"，应输出：
{"brands": ["小米"], "price_max": "__CLEAR__"}
```

**修改 3：`sub_category` 和 `must_have_terms` 在品牌切换时的处理**

当前代码中 `sub_category` 和 `must_have_terms` 在非 PC 场景下是累积的（第 525-529 行）。当用户切换品牌/品类意图时，历史 `sub_category` 和 `must_have_terms` 可能导致过滤过严。

在 `update_session_from_router` 末尾增加一个"品牌切换检测"逻辑：

```python
    # ── 品牌切换检测：用户明确更换品牌时，清除品类级累积约束 ──
    new_brands = new_current.get("brands") or []
    prev_brands_raw = list(prev.get("brands") or [])
    if new_brands and prev_brands_raw and set(new_brands) != set(prev_brands_raw):
        # 品牌发生了明确变化 → 清除 sub_category 和 must_have_terms
        # 因为旧的子品类和必选条件可能是针对上一个品牌的
        if not is_pc_part:
            new_current["sub_category"] = str(args.get("sub_category") or "").strip()
            new_current["must_have_terms"] = [
                str(t) for t in (args.get("must_have_terms") or []) if str(t).strip()
            ]
```

**为什么不在检测到 brands 时就无条件清空价格：** 审阅意见正确指出，用户说"小米手机，2000-3000元"时，品牌和价格都是当前轮的有效约束，不应清空。本方案采用显式清除语义（`__CLEAR__`），只在 LLM 判断旧约束不再适用时才清除，不依赖启发式猜测。

---

### P1-1：handle_compare_v2 利用 query 搜索商品 ID

**问题重述：** v1 建议"让 LLM 将型号名放入 query"，但 `handle_compare_v2` 并不使用 `query` 搜索商品 ID，所以即使 router 传了 query 也无效。

**修订方案：** 在 P0-1 的降级链中已经包含了 query 的使用（降级 2）：

```python
if not product_ids:
    query = str(arguments.get("query") or "").strip()
    if query:
        product_ids = comparison_candidate_ids(query, limit=3)
```

`comparison_candidate_ids` 调用 `recommend_shopping_products(query, use_llm=False, catalog_scope="combined")` 做关键词搜索，返回候选商品 ID。这意味着只要 router 将型号名放入 `query`（如 `query="HOKA Clifton 9 Nike Pegasus 41"`），降级链就能通过关键词搜索找到对应商品。

**Router prompt 调整：** 在 `tool_router.py` 的 `compare_products` 工具描述中明确：

```
compare_products:
  arguments:
    query: 用户提到的商品型号或关键词（如"HOKA Clifton 9和Nike Pegasus 41"）
    category: 商品品类
    brands: 品牌列表（可选）
  注意：系统会自动从上轮推荐结果获取 product_ids，但 query 字段用于
  兜底搜索，务必填写用户提到的具体型号名称。
```

---

### P1-2：响应文本与实际数据一致性

**问题重述：** Case 19 T2 响应文本说"我从上架商品里挑了几款"但实际 0 卡片。

**确认 `_NO_MATCH_VARIANTS` 内容正确：**

```python
_NO_MATCH_VARIANTS = [
    "这次没有找到足够匹配的商品，可以换个关键词或调一下预算再试试。",
    "商品库里暂时没有完全符合的，调整一下条件再搜搜？",
    "没找到特别贴合的，要不要放宽预算或者换个品类看看？",
]
```

内容语义正确（表达"未找到"），不会与"挑了几款"混淆。

**问题在于 `generate_natural_response` 在 0 卡片时仍走 LLM 路径：**

当 LLM 可用时，`generate_natural_response` 优先调用 LLM 生成文本。LLM 可能生成"挑了几款"之类的措辞而不感知实际卡片数为 0。

**涉及文件：** `rag/recommendation/response_generator.py` — `generate_natural_response`

**修订方案：** 在 `generate_natural_response` 开头增加空卡片短路逻辑：

```python
def generate_natural_response(payload, session=None, message=""):
    cards = payload.get("product_cards") or []
    fc = payload.get("fact_check") or {}

    # ── 0 卡片短路：直接返回 _NO_MATCH_VARIANTS 模板，不走 LLM ──
    if not cards:
        import random
        text = random.choice(_NO_MATCH_VARIANTS)
        # 预算超限场景使用专属模板
        budget = payload.get("budget_info", {})
        if budget.get("over_budget"):
            text = random.choice(_BUDGET_OVER_VARIANTS).format(
                budget=budget.get("budget", 0)
            )
        return {"text": text, "mode": "template_no_match"}

    # ... 原有 LLM / 模板逻辑 ...
```

**为什么不依赖 LLM 生成空结果文本：** LLM 没有实时的卡片数据感知（它看到的是 prompt 中的结构化信息，可能遗漏 count=0），而模板是确定性的、经过审核的文本，0 卡片场景使用模板更可靠。

---

### P1-3：PC 方案对比独立实现

**问题重述：** v1 建议在 `handle_compare_v2` 中复用商品对比逻辑处理 PC 组件对比，但两者数据结构不同（商品对比返回 `comparison_table`，PC 方案对比返回组件差异表），强行复用会导致下游 SSE 事件类型不匹配。

**修订方案：新增独立的 PC 方案对比处理函数，发射独立的 SSE 事件类型。**

**涉及文件：** `rag/recommendation/tool_handlers.py`

```python
def _emit_pc_build_comparison(
    session: Any,
    tool_call: Dict[str, Any],
) -> Iterable[str]:
    """Emit a comparison between the two most recent PC build plans.

    Uses compare_pc_build_plans() from pc_build.py and emits
    pc_comparison_table SSE events (not comparison_table).
    """
    from rag.recommendation.pc_build import compare_pc_build_plans

    history = session.pc_build_history or []
    if len(history) < 2:
        yield sse_event("error", {
            "label": "方案不足",
            "detail": "需要至少两个装机方案才能对比，当前只有一个方案。"
        })
        yield sse_event("done", {"session_id": session.session_id})
        return

    current_plan = history[-1]
    baseline_plan = history[-2]
    baseline_label = baseline_plan.get("label") or "上一个方案"

    comparison = compare_pc_build_plans(current_plan, baseline_plan, baseline_label)

    # 生成可读的对比文本
    highlights = comparison.get("highlights") or []
    changes = comparison.get("changes") or []
    text_parts = list(highlights)
    for change in changes:
        role_name = change.get("role_name", "")
        from_title = change.get("from", "")
        to_title = change.get("to", "")
        reason = change.get("reason", "")
        text_parts.append(f"{role_name}：{from_title} → {to_title}。{reason}")

    yield sse_event("delta", {"text": "\n".join(text_parts)})
    yield sse_event("pc_comparison_table", {
        "comparison": comparison,
        "current_plan": current_plan.get("label", "当前方案"),
        "baseline_plan": baseline_label,
    })
    yield sse_event("done", {"session_id": session.session_id})
```

**调用入口在 `handle_compare_v2` 降级链的降级 3 中**（见 P0-1 修订方案）：

```python
if not product_ids:
    topic = current_topic_json(session)
    if topic.get("topic_type") == "pc_build" and len(session.pc_build_history) >= 2:
        yield from _emit_pc_build_comparison(session, tool_call)
        return
```

**SSE 事件类型区分：**

| 场景 | SSE 事件 | 数据结构 |
|---|---|---|
| 普通商品对比 | `comparison_table` | `{rows: [...]}` |
| PC 方案对比 | `pc_comparison_table` | `{comparison: {...}, current_plan: str, baseline_plan: str}` |

前端需要新增对 `pc_comparison_table` 事件的渲染支持。

---

### P1-4：Router prompt 增强——让 LLM 善用 `__CLEAR__` 和 `query` 字段

**涉及文件：** `rag/recommendation/tool_router.py`

**修订内容：**

1. 在 `compare_products` 工具描述中增加 `query` 字段说明（见 P1-1）
2. 在所有工具的 arguments schema 中增加 `__CLEAR__` 的说明：

```
当用户明确表示不再需要之前的某个约束时（如"不要价格限制了"、"换个品牌"），
对不再适用的字段输出 "__CLEAR__" 来显式清除历史值。
示例：{"brands": ["小米"], "price_max": "__CLEAR__", "sub_category": "__CLEAR__"}
```

3. 增加 few-shot 示例，教 LLM 在对比场景中填写 query：

```
用户: "HOKA Clifton 9和Nike Pegasus 41哪个更软？"
→ {"name": "compare_products", "arguments": {
    "query": "HOKA Clifton 9 Nike Pegasus 41",
    "category": "clothing",
    "brands": ["HOKA", "Nike"]
  }}

用户: "特仑苏和金典哪个更适合？"
→ {"name": "compare_products", "arguments": {
    "query": "特仑苏 金典 纯牛奶",
    "category": "food"
  }}
```

---

### P2-1：扩展折叠屏品类覆盖

**问题：** Case 19 T10/T12 折叠屏查询返回 0 卡片。小米 MIX Fold 5 在库中，但折叠屏品类整体覆盖不足。

**涉及文件：** `data/ecommerce_products/products.json`

**方案：** 在 digital 品类中增加 2-3 款折叠屏手机：

- OPPO Find N6（对标 Case 19 T13）
- 华为 Mate X6
- 荣耀 Magic V4

每个产品需包含完整的 `product_id`、`title`、`brand`、`category`（digital）、`sub_category`（折叠屏手机）、`base_price`、`skus`、`description`、`faqs`、`reviews` 等字段，与现有 100 个产品格式一致。

---

### P2-2：新增 SKU 级查询工具

**问题：** Case 19 T7（"小米17 Ultra 的 12+256 和 16+512 差多少钱"）和 Case 20 T12（"32G+1TB 多少钱"）是 SKU 级查询，当前被路由到 `recommend_shopping_products` 但推荐管线无法回答。

**修订方案（采纳审阅意见，不混入推荐函数）：**

新增独立工具 `query_sku_detail`，由 router 在识别到 SKU 级查询时触发。

**工具定义（添加到 `tool_router.py` 的 `LOCAL_ROUTE_NAMES` 和 LLM tool schema）：**

```python
{
    "name": "query_sku_detail",
    "description": "查询特定商品的 SKU 变体信息（如不同配置的价格差异）",
    "parameters": {
        "product_name": {"type": "string", "description": "商品名称或型号"},
        "sku_criteria": {"type": "string", "description": "SKU 筛选条件，如'12+256'、'32G+1TB'"}
    }
}
```

**处理函数（添加到 `tool_handlers.py`）：**

```python
def handle_sku_query(session: Any, tool_call: Dict[str, Any]) -> Iterable[str]:
    """Answer SKU-level queries: price differences between configurations."""
    arguments = tool_call.get("arguments") or {}
    product_name = str(arguments.get("product_name") or "").strip()
    sku_criteria = str(arguments.get("sku_criteria") or "").strip()

    catalog = load_combined_product_catalog()

    # 搜索匹配商品
    matched_product = None
    for pid, product in catalog.items():
        if product_name and product_name.lower() in product.title.lower():
            matched_product = product
            break

    if not matched_product:
        yield sse_event("delta", {"text": f"商品库中暂时没有找到「{product_name}」。"})
        yield sse_event("done", {"session_id": session.session_id})
        return

    # 筛选匹配的 SKU
    matched_skus = []
    for sku in matched_product.skus:
        sku_text = " ".join(sku.properties.values()).lower()
        if not sku_criteria or sku_criteria.lower() in sku_text:
            matched_skus.append(sku)

    if not matched_skus:
        yield sse_event("delta", {
            "text": f"「{matched_product.title}」没有找到匹配「{sku_criteria}」的配置。"
        })
    else:
        lines = [f"「{matched_product.title}」的配置信息："]
        for sku in matched_skus:
            props = " / ".join(sku.properties.values())
            price = sku.price or matched_product.base_price
            lines.append(f"- {props}：¥{price}")
        yield sse_event("delta", {"text": "\n".join(lines)})

    yield sse_event("done", {"session_id": session.session_id})
```

**在 `chat.py` 的路由分发中增加：**

```python
elif tool_name == "query_sku_detail":
    yield from handle_sku_query(session, tool_call)
```

**Router 识别模式：** 当用户消息中包含"差多少钱"、"XX和XXG差"、"配置价格"等模式时，路由到 `query_sku_detail` 而非 `recommend_shopping_products`。

---

## 三、整体缺失的改进项

### 3.1 LLM 调用异常的兜底完善

**审阅指出：** `enrich_recommendation_result` 和 `attach_grounded_explanation` 中的 LLM 调用可能抛出异常。

**现状确认：** 经审阅源码，两个函数都已有完善的 try/catch：
- `enrich_recommendation_result`：捕获 `TimeoutError`、`LLMClientError`、`ValueError`、`TypeError`，降级到规则化 guidance
- `attach_grounded_explanation` → `build_evidence_grounded_explanation`：同样捕获四类异常，降级到模板解释

**但存在一个遗漏：** `catch` 中没有捕获 `ConnectionError`（网络断连）和 `PermissionError`（403 鉴权失败）。如果 LLM provider 返回 403，可能抛出 `ConnectionError` 或 `PermissionError` 而未被捕获。

**修订方案：** 在两个函数的 except 链中增加 `ConnectionError` 和 `PermissionError`：

```python
except TimeoutError:
    # ... 已有逻辑 ...
except (LLMClientError, ValueError, TypeError) as exc:
    # ... 已有逻辑 ...
except (ConnectionError, PermissionError, OSError) as exc:
    logger.warning("LLM call failed due to network/auth error: %s", exc)
    result.trace["llm_guidance"] = "fallback"
    result.trace["llm_guidance_failure_reason"] = f"network_or_auth_error: {type(exc).__name__}"
```

### 3.2 降级路径可观测性

**问题：** 修复后系统存在多条降级路径（`last_recommended_product_ids` → `comparison_candidate_ids` → PC 方案对比 → 推荐兜底），但目前没有日志记录走了哪条路径，线上排查困难。

**修订方案：** 在 `handle_compare_v2` 降级链中增加结构化日志和 trace 事件：

```python
import logging
logger = logging.getLogger(__name__)

def handle_compare_v2(session, product_ids, tool_call):
    fallback_source = "direct"  # 默认：router 直接传了 product_ids

    if not product_ids:
        product_ids = last_recommended_product_ids(session)
        if product_ids:
            fallback_source = "last_recommended"
            logger.info(
                "compare_v2: fell back to last_recommended_product_ids, count=%d, session=%s",
                len(product_ids), session.session_id
            )

    if not product_ids:
        query = str((tool_call.get("arguments") or {}).get("query") or "").strip()
        if query:
            product_ids = comparison_candidate_ids(query, limit=3)
            if product_ids:
                fallback_source = "comparison_candidates"
                logger.info(
                    "compare_v2: fell back to comparison_candidate_ids, query=%r, count=%d",
                    query, len(product_ids)
                )

    # ... 后续逻辑 ...

    # 在 SSE result 事件中包含降级来源
    yield sse_event("result", {
        "type": "comparison",
        "comparison": compare_result,
        "tool_call": tool_call,
        "fallback_source": fallback_source,  # ← 新增
    })
```

### 3.3 回归测试用例建议

每个修复都应有对应的回归测试，确保问题不再复现：

**P0-1 测试（handle_compare_v2 降级）：**

```python
def test_compare_v2_fallback_to_last_recommended():
    """当 product_ids 为空但 session.last_result 有推荐结果时，应返回对比表。"""
    session = make_session(last_result={"product_cards": [
        {"product_id": "p_food_016"},  # 特仑苏
        {"product_id": "p_food_007"},  # 金典
    ]})
    tool_call = {"name": "compare_products", "arguments": {"category": "food"}}
    events = list(handle_compare_v2(session, [], tool_call))
    comparison_events = [e for e in events if e["type"] == "comparison_table"]
    assert len(comparison_events) == 1
    assert len(comparison_events[0]["data"]["rows"]) >= 2

def test_compare_v2_fallback_to_comparison_candidates():
    """当 session 也无推荐结果时，应通过 query 搜索候选。"""
    session = make_session()
    tool_call = {"name": "compare_products", "arguments": {
        "query": "HOKA Clifton 9 Nike Pegasus 41", "category": "clothing"
    }}
    events = list(handle_compare_v2(session, [], tool_call))
    comparison_events = [e for e in events if e["type"] == "comparison_table"]
    assert len(comparison_events) >= 1
```

**P0-2 测试（品牌白名单硬过滤）：**

```python
def test_brand_whitelist_hard_filter():
    """brands=["华为"] 时，非华为产品应被过滤掉。"""
    requirement = RequirementSpec(
        raw_query="华为笔记本", brands=["华为"],
        desired_categories=[ComponentCategory.digital]
    )
    products = [
        make_product("p1", "华为 MateBook 14", brand="华为", category="digital"),
        make_product("p2", "MacBook Air M5", brand="Apple", category="digital"),
        make_product("p3", "联想 ThinkBook 14+", brand="联想", category="digital"),
    ]
    filtered, diag = filter_products_for_requirement(requirement, products, ComponentCategory.digital)
    assert len(filtered) == 1
    assert filtered[0].product_id == "p1"

def test_brand_whitelist_graceful_fallback():
    """brands 过滤后为空时，应保留过滤前结果（不返回 0 条）。"""
    requirement = RequirementSpec(
        raw_query="方里眉笔", brands=["方里"],
        desired_categories=[ComponentCategory.beauty]
    )
    products = [
        make_product("p1", "花西子眉笔", brand="花西子", category="beauty"),
    ]
    filtered, diag = filter_products_for_requirement(requirement, products, ComponentCategory.beauty)
    assert len(filtered) == 1  # 品牌过滤后为空，降级保留过滤前结果
```

**P0-3 测试（累积约束清除）：**

```python
def test_clear_sentinel_clears_price():
    """__CLEAR__ 应清除历史价格约束。"""
    session = make_session(current={"price_max": 5000, "brands": ["苹果"]})
    tool_call = {"name": "recommend_shopping_products", "arguments": {
        "brands": ["小米"], "price_max": "__CLEAR__"
    }}
    update_session_from_router(session, "就买小米17 Ultra", tool_call)
    assert session.current.get("price_max") is None
    assert "小米" in session.current.get("brands", [])

def test_brand_switch_clears_sub_category():
    """品牌切换时，应清除旧品牌的 sub_category。"""
    session = make_session(current={
        "brands": ["苹果"], "sub_category": "笔记本电脑",
        "must_have_terms": ["M4芯片"]
    })
    tool_call = {"name": "recommend_shopping_products", "arguments": {
        "brands": ["华为"]
    }}
    update_session_from_router(session, "换成华为看看", tool_call)
    assert session.current.get("must_have_terms") == []
    assert session.current.get("sub_category") == ""
```

---

## 四、修改优先级与实施顺序

| 优先级 | 编号 | 修改项 | 涉及文件 | 影响 Case | 预估工作量 |
|---|---|---|---|---|---|
| **P0** | P0-1 | handle_compare_v2 恢复降级逻辑 | `tool_handlers.py` | 12/14/15/16/17/18/19/20（11处对比失败） | 0.5d |
| **P0** | P0-2 | 品牌白名单硬过滤 | `structured_filter.py` | 20（T6/T7/T8 返回错误品牌） | 0.5d |
| **P0** | P0-3 | 多轮累积约束清除 + `__CLEAR__` 支持 | `session_state.py`, `tool_router.py` | 19（T15）、20（T12） | 1d |
| **P1** | P1-1 | query 字段搜索商品 ID | 已包含在 P0-1 中 | 同上 | — |
| **P1** | P1-2 | 0 卡片响应文本一致性 | `response_generator.py` | 19（T2） | 0.5h |
| **P1** | P1-3 | PC 方案对比独立实现 | `tool_handlers.py` | 12（T6） | 0.5d |
| **P1** | P1-4 | Router prompt 增强 | `tool_router.py` | 所有对比 Case | 0.5d |
| **P2** | P2-1 | 扩展折叠屏品类 | `products.json` | 19（T10/T12） | 0.5d |
| **P2** | P2-2 | 新增 SKU 查询工具 | `tool_router.py`, `tool_handlers.py`, `chat.py` | 19（T7）、20（T12/T13） | 1d |
| **辅助** | 3.1 | LLM 异常兜底完善 | `recommendation_pipeline.py`, `explanation_builder.py` | 全局 | 0.5h |
| **辅助** | 3.2 | 降级路径可观测性 | `tool_handlers.py` | 全局 | 0.5h |
| **辅助** | 3.3 | 回归测试用例 | `tests/` | 全局 | 1d |

**建议实施批次：**

- **第一批（紧急修复）**：P0-1 + P0-2 + P1-2，预计 1 天，解决 14 处对比失败 + 3 处品牌错误 + 1 处文本不一致
- **第二批（体验优化）**：P0-3 + P1-3 + P1-4，预计 2 天，解决多轮约束问题 + PC 对比 + router 准确性
- **第三批（功能扩展）**：P2-1 + P2-2 + 3.1 + 3.2 + 3.3，预计 2.5 天，扩展数据 + SKU 工具 + 测试覆盖

---

## 五、回答核心问题（修订版）

**"检查是否真的应该返回对比卡片，还是真的因为数据库缺失所以无法返回"**

结论不变，但根因更加清晰：

**11/14 处对比失败应该能返回卡片。** 商品都在数据库中，失败原因是 `handle_compare_v2` 移除了降级逻辑 + LLM 路由器不传 `product_ids`。

**2-3 处确实因数据库缺失**（折叠屏、"方里"品牌），需扩展数据。

**新发现：3 处品牌错误返回（Case 20 T6/T7/T8）的根因不是"过滤逻辑有 bug"，而是"品牌白名单过滤根本不存在"。** `RequirementSpec.brands` 在 `structured_filter.py` 中从未作为硬过滤条件使用，仅做 scorer 加分。

**9 处 recommend 返回 0 卡片中**，2 处是数据缺失（折叠屏），4 处是累积约束过严（P0-3），2 处是路由错误（SKU/详情查询被路由到推荐），1 处是追问误判。

---

*修订版报告完。*
