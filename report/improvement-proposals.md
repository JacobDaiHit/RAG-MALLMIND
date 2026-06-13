# 链路改造建议：购物车精确操作 & 回复多样化

**创建日期：** 2026-06-12  
**关联文档：** [当前链路架构说明](current-link-architecture.md) · [Phase 4 回归测试报告](bound_test_phase4.md)

---

## 目录

1. [改造一：购物车 SKU 级精确操作 + 系统追问机制](#一购物车-sku-级精确操作--系统追问机制)
2. [改造二：系统回复多样化增强](#二系统回复多样化增强)

---

## 一、购物车 SKU 级精确操作 + 系统追问机制

### 1.1 现状分析

当前购物车操作链路涉及三个关键函数：

| 函数 | 文件 | 职责 |
|------|------|------|
| `handle_cart_v2()` | `tool_handlers.py` | 计划+确认模式（仅支持 add 操作） |
| `apply_cart_instruction()` | `session_state.py` | 购物车变更引擎（支持 add/remove/set_quantity/clear） |
| `resolve_cart_product_ids()` | `session_state.py` | 产品 ID 解析（支持序数、显式 ID、上次推荐引用） |

**已有的定位手段：**

| 方式 | 示例 | 覆盖 |
|------|------|------|
| 显式 product_id | `p_digital_016` | 技术层面可行，但用户不会输入 |
| 序数引用 | "第一个""第二款""3号" | 支持 1~3，通过 `extract_item_index()` |
| 上次推荐引用 | "刚才那款""这个""上一个" | 通过 `references_previous_item()` |
| 清空关键词 | "清空""全部删除""删光" | 通过 `infer_cart_action()` |

**已知的 5 个核心问题：**

**问题 1：无法按商品名称定位**  
用户说"把 OPPO Reno 删掉"时，`resolve_cart_product_ids()` 无法将"OPPO Reno"匹配到 `session.cart` 中的 `p_digital_016`。函数仅做正则提取 product_id 和序数解析，不支持标题模糊匹配。

**问题 2：序数顺序与展示不一致**  
`remove` 操作的序数基于 `session.cart.keys()` 的插入顺序（Python dict 有序），但前端展示顺序可能不同。用户说"删除第二个"时，可能指展示顺序而非插入顺序。

**问题 3：handle_cart_v2 仅支持 add 操作**  
`handle_cart_v2()` 创建 CartActionPlan 时只处理 `operation="add"`。当用户说"删除购物车里的XX"时，路由到 `apply_cart_instruction` 走的是 v2 handler，但 v2 只创建 add 类型的 plan。`cart_confirm()` 端点（line 317）也硬编码了 `"把 {product_id} {title} 加入购物车，数量 {quantity}"`。

**问题 4：缺乏追问机制**  
当用户说"删掉一个手机"而购物车有两部手机时，系统无法追问"你要删哪一部？"，而是默认操作第一个匹配项或直接失败。

**问题 5：修改数量无法精确定位**  
`set_quantity` 操作时，`extract_item_index()` 返回 None（代码 line 892：`index = None if action == "set_quantity" else extract_item_index(instruction)`），因此修改数量时完全依赖 `references_previous_item()` 或默认行为，无法指定"把第二件改成3个"。

### 1.2 改造方案

#### 方案 A：商品名称模糊匹配（优先级：高）

**目标：** 让用户能通过商品名称/关键词定位购物车中的商品。

**改造点：** `resolve_cart_product_ids()` 新增名称匹配层

```python
def resolve_cart_product_ids(session, instruction, action, *, product_ids=None, index=None):
    # 现有：显式 ID
    explicit_ids = product_ids or extract_product_ids(instruction)
    if explicit_ids:
        return select_by_index(explicit_ids, index)

    # 🟣 新增：标题模糊匹配
    catalog = load_combined_product_catalog()
    cart_ids = list(session.cart.keys())
    matched = _fuzzy_match_cart_item(instruction, cart_ids, catalog)
    if matched:
        return matched

    # 现有：序数/引用/默认逻辑
    if action == "remove":
        ...
```

**`_fuzzy_match_cart_item()` 实现思路：**

```python
def _fuzzy_match_cart_item(instruction: str, cart_ids: List[str], catalog) -> List[str]:
    """从购物车中模糊匹配商品标题。"""
    matched = []
    for pid in cart_ids:
        product = catalog.get(pid)
        if not product:
            continue
        title = product.title or ""
        brand = getattr(product, "brand", "") or ""
        # 分词匹配：用户消息中包含标题关键词
        keywords = set(re.findall(r'[\u4e00-\u9fff]+|[A-Za-z]+', title + " " + brand))
        # 过滤掉过短的关键词（< 2字符）
        keywords = {k for k in keywords if len(k) >= 2}
        hits = sum(1 for kw in keywords if kw in instruction)
        if hits >= 1:  # 至少匹配一个关键词
            matched.append((hits, pid))
    if matched:
        matched.sort(key=lambda x: x[0], reverse=True)
        return [matched[0][1]]  # 返回最佳匹配
    return []
```

**涉及文件：** `session_state.py`  
**风险：** 低——仅在显式 ID 和序数之后增加一层匹配，不影响现有逻辑。

#### 方案 B：handle_cart_v2 支持 remove/set_quantity 操作（优先级：高）

**目标：** 让 v2 确认流程覆盖删除和修改数量操作。

**改造点 1：** `handle_cart_v2()` 增加操作类型判断

```python
def handle_cart_v2(session, message, product_ids, tool_call):
    args = dict(tool_call.get("arguments") or {})
    operation = args.get("operation", "add") or "add"

    # 🟣 解析操作类型（也可以从 message 推断）
    action = infer_cart_action(message)  # "add" / "remove" / "set_quantity" / "clear"
    if action == "clear":
        # 直接执行，不走确认（或走轻量确认）
        session.cart.clear()
        save_session(session)
        yield sse_event("delta", {"text": "已清空购物车。"})
        yield sse_event("cart", cart_snapshot(session, catalog))
        yield sse_event("done", {})
        return

    # 产品 ID 解析（含名称模糊匹配）
    plan_product_id = _resolve_cart_target(session, message, product_ids, args)
    ...

    plan = {
        "operation": action,       # 🟣 不再是硬编码 "add"
        "product_id": plan_product_id,
        "product_title": title,
        "quantity": quantity,
        ...
    }
```

**改造点 2：** `cart_confirm()` 端点支持多种操作

```python
@router.post("/api/cart/confirm")
def cart_confirm(request):
    ...
    operation = plan.get("operation", "add")

    if operation == "remove":
        # 合成删除指令
        instruction = f"删除 {plan.get('product_id')} {title}"
        result = apply_cart_instruction(session, instruction, catalog, product_ids=[plan.get("product_id")])
    elif operation == "set_quantity":
        instruction = f"把 {plan.get('product_id')} {title} 数量改为 {quantity}"
        result = apply_cart_instruction(session, instruction, catalog, product_ids=[plan.get("product_id")])
    else:
        # 现有 add 逻辑
        ...
```

**涉及文件：** `tool_handlers.py`、`chat.py`  
**风险：** 中——需要确保 v2 plan 中 operation 字段正确传递，且前端确认 UI 适配不同操作类型。

#### 方案 C：系统追问机制（优先级：中）

**目标：** 当操作目标不明确时，系统主动追问而非盲目执行。

**触发条件设计：**

| 场景 | 触发条件 | 追问内容 |
|------|----------|----------|
| 多个同类商品 | 购物车中有 ≥2 个同品类商品，用户说"删掉那个手机" | "购物车里有 [OPPO Reno] 和 [iPhone 15]，你要删掉哪一部？" |
| 名称无匹配 | 模糊匹配返回空，但购物车非空 | "购物车里没有包含'XX'的商品，当前购物车中有：[商品列表]，你要操作哪一个？" |
| 序数越界 | `extract_item_index()` 返回的 index 超出购物车范围 | "购物车里只有 {n} 个商品，没有第 {index+1} 个哦。" |
| 修改数量无目标 | `set_quantity` 没有明确的 product_id | "你想修改哪个商品的数量？当前购物车中有：[商品列表]" |

**实现方式：** 新增 SSE 事件 `cart_clarification`

```python
# session_state.py 或 tool_handlers.py 中
def _check_cart_ambiguity(session, instruction, action, matched_ids):
    """检查购物车操作是否有歧义，返回追问文本或 None。"""
    cart = session.cart
    if not cart:
        return "购物车是空的，先去逛逛吧～"

    if action in ("remove", "set_quantity") and not matched_ids:
        items = _format_cart_items(session, catalog)
        return f"没找到要操作的商品。当前购物车里有：\n{items}\n你想操作哪一个？"

    if action == "remove":
        # 检测同品类歧义
        category_counts = _group_cart_by_category(cart, catalog)
        for cat, ids in category_counts.items():
            if len(ids) >= 2 and _mentions_category(instruction, cat):
                items = ", ".join(catalog.get(pid).title for pid in ids if catalog.get(pid))
                return f"购物车里有多个{cat}商品：{items}，你要操作哪一个？可以说名称或'第几个'。"

    return None  # 无歧义
```

**SSE 事件流改造：**

```python
# handle_cart_v2 中
ambiguity = _check_cart_ambiguity(session, message, action, resolved_ids)
if ambiguity:
    yield sse_event("cart_clarification", {
        "text": ambiguity,
        "cart_items": _cart_item_list(session),  # 供前端渲染选择器
    })
    yield sse_event("done", {})
    return
```

**前端适配：** 收到 `cart_clarification` 事件时，展示购物车列表 + 可点击的商品按钮，用户点击后发送带 product_id 的精确指令。

**涉及文件：** `tool_handlers.py`、`session_state.py`、`frontend/app.js`  
**风险：** 中——需要新增 SSE 事件类型和前端交互组件。

#### 方案 D：set_quantity 支持序数（优先级：低）

**目标：** 修复 `set_quantity` 操作不能使用序数的问题。

**当前代码（line 892）：**
```python
index = None if action == "set_quantity" else extract_item_index(instruction)
```

**修复：**
```python
index = extract_item_index(instruction)  # 所有操作都支持序数
```

**影响分析：** 这个 `None if` 条件可能是有意为之（避免"改成第二个"中的"第二个"被误解析为数量），但 `extract_item_index()` 和 `extract_quantity()` 使用不同的正则模式，不会冲突。建议直接移除这个限制。

**涉及文件：** `session_state.py`（1 行修改）  
**风险：** 低。

### 1.3 实施优先级

```
Phase 1（高优）：方案 A（名称模糊匹配）+ 方案 D（set_quantity 序数支持）
  → 1~2 天工作量，低风险，立竿见影

Phase 2（高优）：方案 B（v2 支持 remove/set_quantity）
  → 2~3 天工作量，中等风险，需要前后端联调

Phase 3（中优）：方案 C（追问机制）
  → 3~5 天工作量，涉及新 SSE 事件 + 前端组件
```

---

## 二、系统回复多样化增强

### 2.1 现状分析

当前回复生成有两层机制：

| 层级 | 实现 | 状态 |
|------|------|------|
| LLM 生成 | `_llm_diverse_response()` — temperature=0.9 | ⚠️ MIMO 中文退化，实际不可用 |
| 模板兜底 | `naturalize_response()` — 组合式模板 | ✅ 运行中 |

**模板变体盘点：**

| 模板数组 | 变体数 | 示例 |
|----------|--------|------|
| `_OPENING_VARIANTS` | 6 | "帮你筛了一遍商品库，" / "按你的需求筛了一下，" |
| `_LEAD_VARIANTS` | 5 | "首推 XX，大概 YY 块" / "XX 挺适合你的，YY 左右" |
| `_LEAD_NO_PRICE` | 3 | "最推荐 XX" / "首推 XX，各方面匹配度很高" |
| `_TAIL_VARIANTS` | 4 | "下面保留了候选卡片" / "候选商品卡片就在下面" |
| `_NO_MATCH_VARIANTS` | 3 | "这次没有找到足够匹配的商品" |
| `_BUDGET_OVER_VARIANTS` | 2 | "XX CNY 内暂时没找到合适的" |
| `_BRAND_MISS_VARIANTS` | 2 | "没有找到 XX 品牌的在售商品" |

**理论组合空间：** 6 × 5 × 4 = 120 种（有价格时）。

**已知的 5 个核心问题：**

**问题 1：只提及第一个商品**  
`naturalize_response()` 只取 `cards[0]` 作为 lead（line 222-228），其余推荐商品在文本回复中完全不可见。用户看到的文字总是"首推 XX"，即使推荐了 3~5 款商品。

**问题 2：无多轮记忆**  
`generate_natural_response()` 不知道上一轮回复了什么。连续两轮推荐时，可能使用相同的开场白和结尾，体验重复。

**问题 3：负面场景模板过少**  
`_NO_MATCH_VARIANTS` 仅 3 种，`_BUDGET_OVER_VARIANTS` 仅 2 种。频繁遇到无匹配场景时，用户会感到机器味重。

**问题 4：缺乏场景感知**  
模板不区分首次推荐 vs 追问推荐 vs 对比后推荐，也不区分品类（手机 vs 护肤品 vs PC 配件）。用户说"推荐个面膜"和"推荐个显卡"收到同样风格的回复。

**问题 5：LLM prompt 约束过紧**  
`_RESPONSE_PROMPT` 要求"2-3句话，不超过120字"，且"不用'根据你的需求''推荐理由如下'等套路句式"。虽然避免了套话，但 120 字上限让 LLM 难以产出有个性的回复。

### 2.2 改造方案

#### 方案 E：多商品提及（优先级：高）

**目标：** 让文本回复涵盖 Top-2 或 Top-3 推荐商品，而非只提第一个。

**改造 `naturalize_response()`：**

```python
def naturalize_response(payload):
    ...
    # 主打 + 副推
    if cards:
        lead = cards[0]
        title = lead.get("title", "")
        price = lead.get("price")
        if price is not None:
            lines.append(_pick(_LEAD_VARIANTS, title=title, price=price))
        else:
            lines.append(_pick(_LEAD_NO_PRICE, title=title))

        # 🟣 新增：副推商品（第 2~3 个）
        if len(cards) >= 2:
            runner_up = cards[1]
            ru_title = runner_up.get("title", "")
            ru_price = runner_up.get("price")
            if ru_price is not None:
                lines.append(_pick(_RUNNER_UP_VARIANTS, title=ru_title, price=ru_price))
            else:
                lines.append(_pick(_RUNNER_UP_NO_PRICE, title=ru_title))
```

**新增模板数组：**

```python
_RUNNER_UP_VARIANTS = [
    "另外 {title} 也不错，{price:g} CNY 左右。",
    "还可以看看 {title}，参考价 {price:g} CNY。",
    "备选的话，{title} 也值得考虑，{price:g} 上下。",
    "如果想多比较，{title} 也在候选里，约 {price:g} CNY。",
]

_RUNNER_UP_NO_PRICE = [
    "另外 {title} 也不错，可以一起对比下。",
    "备选的话，{title} 也值得看看。",
]
```

**涉及文件：** `response_generator.py`  
**效果：** 文本回复从单商品变为多商品，信息量提升。组合空间从 120 种扩展为 120 × 4 = 480 种（含副推时）。

#### 方案 F：多轮去重 + 场景记忆（优先级：高）

**目标：** 避免连续两轮使用相同的开场/结尾，增加上下文感知。

**改造思路：** 在 session 中记录上一轮回复的模板索引。

```python
# session_state.py — ShoppingSession 新增字段
last_response_templates: Dict[str, int] = field(default_factory=dict)
# 记录上次使用的 opening/lead/tail 索引

# response_generator.py
def naturalize_response(payload, session=None):
    ...
    # 避免与上次相同的开场
    last_opening = (session.last_response_templates or {}).get("opening") if session else None
    opening_idx = _pick_avoiding(_OPENING_VARIANTS, last_opening)
    ...
    # 记录本次选择
    if session:
        session.last_response_templates = {
            "opening": opening_idx,
            "lead": lead_idx,
            "tail": tail_idx,
        }
```

**`_pick_avoiding()` 实现：**

```python
def _pick_avoiding(variants: List[str], avoid_index: Optional[int]) -> int:
    """随机选一个，但避免选到 avoid_index。"""
    if len(variants) <= 1:
        return 0
    candidates = [i for i in range(len(variants)) if i != avoid_index]
    return random.choice(candidates)
```

**涉及文件：** `session_state.py`（新增字段）、`response_generator.py`（选模板逻辑）  
**效果：** 连续两轮不会出现相同的开场白，体验更自然。

#### 方案 G：品类感知模板（优先级：中）

**目标：** 不同品类使用不同风格的模板。

**改造思路：** 按 `catalog_scope` 或 `category` 分模板池。

```python
_CATEGORY_OPENINGS = {
    "digital": [
        "帮你比了几款数码产品，",
        "从在售数码里挑了一下，",
        "数码产品库筛了一轮，",
    ],
    "beauty": [
        "帮你看了几款护肤品，",
        "从美妆库里选了一些，",
        "按你的肤质筛了一下，",
    ],
    "clothes": [
        "帮你挑了几件衣服，",
        "从服装库里选了一些款式，",
    ],
    "food": [
        "帮你找了几款食品，",
        "零食库里挑了一下，",
    ],
    "pc_parts": [
        "从配件库里匹配了一下，",
        "帮你选了几款 PC 配件，",
    ],
}

_DEFAULT_OPENINGS = _OPENING_VARIANTS  # 兜底

def _get_openings(category: str) -> List[str]:
    return _CATEGORY_OPENINGS.get(category, _DEFAULT_OPENINGS)
```

**涉及文件：** `response_generator.py`  
**效果：** 手机推荐和护肤品推荐的开场白风格不同，更贴合品类语境。

#### 方案 H：负面场景模板扩充（优先级：中）

**目标：** 增加无匹配、超预算、品牌缺失场景的模板数量。

**扩充后：**

```python
_NO_MATCH_VARIANTS = [
    "这次没有找到足够匹配的商品，可以换个关键词或调一下预算再试试。",
    "商品库里暂时没有完全符合的，调整一下条件再搜搜？",
    "没找到特别贴合的，要不要放宽预算或者换个品类看看？",
    # 🟣 新增
    "筛了一圈没找到特别合适的，试试调高预算或减少筛选条件？",
    "暂时没有完全对口的商品，换个描述方式也许能找到。",
    "这次筛选结果为空，可能是条件太严了，放松一些试试？",
    "商品库里没找到匹配的，可以试试其他品类或品牌。",
]

_BUDGET_OVER_VARIANTS = [
    "{budget:g} CNY 内暂时没有合适的候选，下面给出同类最近备选。",
    "{budget:g} 以内暂时没找到，看看这几款接近预算的吧。",
    # 🟣 新增
    "你的预算 {budget:g} CNY 稍紧了点，这几款稍超一点但性价比不错。",
    "{budget:g} 以内的选择有限，帮你找了几款最接近的。",
    "预算 {budget:g} 内没找到理想的，稍微加点预算选择会多很多。",
]

_BRAND_MISS_VARIANTS = [
    "没有找到 {brands} 品牌的在售商品，下面推荐了其他品牌的候选。",
    "{brands} 品牌目前缺货，先看看这些替代品吧。",
    # 🟣 新增
    "{brands} 暂时没有在售的，帮你挑了几个其他品牌的替代品。",
    "{brands} 品牌在商品库里没有匹配的，其他品牌也有类似的选择。",
]
```

**涉及文件：** `response_generator.py`  
**效果：** 负面场景从 3/2/2 种扩充到 7/5/4 种，减少重复感。

#### 方案 I：LLM prompt 优化 + 重试策略（优先级：低）

**目标：** 改善 LLM 生成质量，为将来 MIMO 中文能力改善或切换模型做准备。

**改造点 1：放宽字数限制**

```python
_RESPONSE_PROMPT = """...
【约束】:
...
2. 3-4句话，不超过200字。（原：2-3句话，不超过120字）
3. 语气自然，像真人导购。
..."""
```

**改造点 2：增加对话上下文**

```python
_RESPONSE_PROMPT = """...
【上次回复摘要】: {last_response}  （避免重复类似表述）
【当前轮次】: 第 {turn_number} 轮对话
..."""
```

**改造点 3：重试策略**  
当前 `_llm_diverse_response()` 直接调用 `OpenAICompatibleChatClient()` 而非走 `LLMGateway`（line 141），这是一个遗漏——应统一为 `LLMGateway.call("response", messages)` 以享受熔断和并发管理。

**涉及文件：** `response_generator.py`  
**效果：** 为 LLM 路径恢复可用后的质量提升做准备。

### 2.3 实施优先级

```
Phase 1（高优）：方案 E（多商品提及）+ 方案 H（负面模板扩充）
  → 1 天工作量，纯模板增改，零风险

Phase 2（高优）：方案 F（多轮去重）
  → 1~2 天工作量，需新增 session 字段

Phase 3（中优）：方案 G（品类感知模板）
  → 1~2 天工作量，模板设计

Phase 4（低优）：方案 I（LLM prompt + Gateway 统一）
  → 1 天工作量，等 LLM 中文能力就绪后实施
```

---

## 附录：改造依赖关系

```
方案 A（名称匹配）    ← 独立，可先做
方案 B（v2 多操作）   ← 依赖方案 A（名称匹配作为目标解析基础）
方案 C（追问机制）    ← 依赖方案 A + B（歧义检测需要完善的匹配逻辑）
方案 D（set_quantity 序数）← 独立，1 行修复

方案 E（多商品提及）  ← 独立
方案 F（多轮去重）    ← 独立
方案 G（品类感知）    ← 独立
方案 H（模板扩充）    ← 独立
方案 I（LLM prompt） ← 依赖外部模型改善
```

**推荐实施路径：**
```
第一批：A + D + E + H （2~3 天，低风险，效果显著）
第二批：B + F （3~4 天，中等风险，架构改动）
第三批：C + G （4~6 天，涉及前端组件 + 模板设计）
第四批：I （视模型情况）
```

---

*文档完。*
