## P0 修改方案（修订版）：LLM 兜底路由 — 不增关键词，改架构决策逻辑

### 设计原则

你的思路是对的：关键词表永远追不上用户的表达方式，正确的做法是——**本地规则不确定时，让 LLM 接管；本地规则确定时（如明确的购物车操作、PC 装机意图），才直接路由。**

当前系统的问题不是"缺少关键词"，而是**三层防线同时把不确定的 query 推向 general_chat**：

```
本地规则层：没有关键词命中 → _is_general_chat() 用负面逻辑判定为闲聊 → 高置信度 0.9
  ↓
LLM 跳过层：should_skip_llm_route() 看到 general_chat + confidence 0.9 → 跳过 LLM
  ↓
Guard 兜底层：_is_general_chat() 再次用负面逻辑确认 → 强制覆写为 general_chat
```

三层都基于同一个有缺陷的 `_is_general_chat()` 负面逻辑（"没有任何购物信号 = 闲聊"），形成了**级联误判**。

---

### 当前代码问题精确追踪

以 query "训练穿的缓震篮球鞋，别太重" 为例，逐步追踪代码执行路径：

**第 1 步：`local_route_tool_call()`（tool_router.py:828）**

```
cart? → 否
PC followup? → 否
compare? → 否
PC intent? → 否
PC topic followup? → 否
followup? → 否
detect_normal_product_category → ""（没有"鞋"/"篮球鞋"等词）
single PC part? → 否
_has_product_query_intent → False（"训练"不在 SEARCH/SCENARIO/FACT 词表中）
_is_general_chat → True（负面逻辑：没有任何 shopping signal）
→ 返回 general_chat, confidence=0.9  ← 高置信度！
```

**第 2 步：`should_skip_llm_route()`（tool_router.py:716）**

```
mode = "balanced"
name = "general_chat", confidence ≈ 0.83+
→ 第 731 行：name == "general_chat" and confidence >= 0.75 → True
→ 跳过 LLM！
```

**第 3 步：`validate_and_guard_tool_call()`（tool_router.py:961）**

```
explicit_normal = "" (detect_normal_product_category 仍为空)
product_query_intent = False
_is_general_chat = True（同一个负面逻辑）
→ 第 1019 行：guard 强制覆写为 general_chat
```

**结论**：LLM 从未被调用过，guard 层也把任何"翻盘"的可能性封死了。

---

### 修改方案：4 处代码变更

#### 改动 1：收窄 `_is_general_chat()` 的判定条件

**位置**：`tool_router.py:1378-1400`

**当前逻辑**（负面逻辑）：
```python
def _is_general_chat(text, lowered, topic=None):
    if any(term in text or term in lowered for term in GENERAL_CHAT_TERMS):
        return True  # 有明确的系统/身份关键词 → 是闲聊
    # ...
    shopping_signals = [...]  # 一大堆购物信号词
    return not any(term in text for term in shopping_signals)  # 没有任何购物信号 → 是闲聊
```

**改为**（正面逻辑）：
```python
def _is_general_chat(text, lowered, topic=None):
    # 只有明确匹配 GENERAL_CHAT_TERMS 才判定为闲聊
    if any(term in text or term in lowered for term in GENERAL_CHAT_TERMS):
        return True
    # 如果有活跃的购物主题且是简短偏好追问，不是闲聊
    if _has_active_shopping_topic(topic) and _looks_like_short_preference_followup(text):
        return False
    # 其余情况一律返回 False —— 让 LLM 来判断
    return False
```

**核心变化**：删除了基于"缺少购物信号"的负面推断。`_is_general_chat()` 只在检测到 `GENERAL_CHAT_TERMS`（"你是谁"、"推荐逻辑"、"路由原因"等明确的系统级问题）时才返回 True。

**理由**：判断一个 query "是不是闲聊"的负面逻辑天然不可靠——你永远无法穷举所有购物信号。正确的做法是把判定权交给 LLM。

#### 改动 2：放宽 `should_skip_llm_route()` 对 general_chat 的跳过条件

**位置**：`tool_router.py:728-733`

**当前逻辑**：
```python
if mode == "balanced":
    if confidence >= 0.78 and margin >= 0.20:
        return True
    if name in {"apply_cart_instruction", "general_chat"} and confidence >= 0.75:
        return True  # ← 问题：general_chat 高置信就跳过 LLM
    return False
```

**改为**：
```python
if mode == "balanced":
    if confidence >= 0.78 and margin >= 0.20:
        return True
    if name == "apply_cart_instruction" and confidence >= 0.75:
        return True  # ← 只有购物车操作才高置信跳过
    # general_chat 不再自动跳过 LLM，让 LLM 有机会纠正
    return False
```

**核心变化**：从 `{"apply_cart_instruction", "general_chat"}` 中移除 `"general_chat"`。

**理由**：购物车操作有硬关键词（"加购"、"清空"），误判率极低，可以信任本地规则。但 `general_chat` 的判定依赖负面逻辑，不应该高置信跳过 LLM。

#### 改动 3：修改 `local_route_tool_call()` 的兜底行为

**位置**：`tool_router.py:863-865`

**当前逻辑**：
```python
if _is_general_chat(text, lowered, topic):
    return general_chat, confidence=0.9  # ← 高置信度！
return recommend_shopping_products, confidence=0.7  # 最终兜底
```

**改为**：
```python
if _is_general_chat(text, lowered, topic):
    # 只有 GENERAL_CHAT_TERMS 显式命中才走高置信 general_chat
    return general_chat, confidence=0.9
# 未匹配任何规则 → 低置信度兜底，给 LLM 留出纠正空间
return recommend_shopping_products, confidence=0.45
```

**核心变化**：将兜底 confidence 从 `0.7` 降到 `0.45`。

**理由**：confidence=0.7 在 balanced 模式下仍然高于 `should_skip_llm_route()` 的一些阈值。降到 0.45 确保：
- `should_skip_llm_route()` 中 confidence < 0.78 → 不会跳过 LLM
- LLM 有机会以自己的置信度覆盖本地路由

同时配合改动 1（`_is_general_chat` 收窄后，大部分原先的 general_chat 判定不会命中），实际效果是：没有关键词命中的 query 会走到 confidence=0.45 的兜底分支，然后 LLM 被调用来做最终判定。

#### 改动 4：修改 Guard 层的 general_chat 兜底

**位置**：`tool_router.py:1019-1020`

**当前逻辑**：
```python
elif _is_general_chat(text, lowered, topic):
    call.update(general_chat, confidence=max(call.confidence, 0.9))
# 没有 else → 隐式返回当前 call（可能是 LLM 的结果）
```

**改为**：
```python
elif _is_general_chat(text, lowered, topic):
    # 只有显式系统问题才强制覆写为 general_chat
    call.update(general_chat, confidence=max(call.confidence, 0.9))
else:
    # 没有正面信号匹配 → 安全兜底到商品推荐，不走闲聊
    if call.get("name") == "general_chat":
        call.update(recommend_shopping_products, confidence=max(call.confidence, 0.6))
```

**核心变化**：当 Guard 层没有任何正面信号匹配时，如果当前路由是 `general_chat`（可能是 LLM 返回的或者本地规则返回的），强制改为 `recommend_shopping_products`。

**理由**：Guard 层是最后一道防线。如果连 Guard 的所有正面条件（购物车、PC、对比、品类、搜索意图）都没匹配，说明系统对 query 的理解很低。此时把路由指向 `recommend_shopping_products` 比 `general_chat` 更安全——因为推荐管线内部有 `validate_business_goal()` 做二次验证，如果真的是非购物 query，会在那里被拦截并返回引导性回复。

---

### 改动的联动效果

以 "训练穿的缓震篮球鞋，别太重" 为例，改动后的执行路径：

```
local_route_tool_call():
  所有关键词不命中
  _is_general_chat() → False（改动 1：只有 GENERAL_CHAT_TERMS 命中才返回 True）
  → 返回 recommend_shopping_products, confidence=0.45（改动 3）

should_skip_llm_route():
  mode = "balanced"
  confidence = 0.45 < 0.78 → 第一个条件不满足
  name = "recommend_shopping_products" ≠ "apply_cart_instruction" → 第二个条件不满足
  → 返回 False（不跳过 LLM）

route_shopping_tool_call():
  LLM 被调用！
  LLM 看到"训练穿的缓震篮球鞋"，正确识别为购物 query
  → 返回 recommend_shopping_products, confidence=0.85

validate_and_guard_tool_call():
  explicit_normal = ""（仍为空）
  product_query_intent = False
  _is_general_chat = False（改动 1）
  → 没有任何 guard 触发覆写
  → 保留 LLM 的 recommend_shopping_products 结果

最终路由：recommend_shopping_products ✓
```

**即使 LLM 调用失败**（超时或不可用）：
```
route_shopping_tool_call():
  LLM 失败 → chosen = local = recommend_shopping_products (confidence=0.45)

validate_and_guard_tool_call():
  没有 guard 触发
  else 分支（改动 4）不触发，因为 call.name 已经是 recommend_shopping_products
  → 保留 recommend_shopping_products

最终路由：recommend_shopping_products ✓（LLM 不工作也能正确路由）
```

---

### 对现有 case 的影响评估

**不受影响的 case**（路由已经正确的 15 个）：

| query | 原路由 | 改动后 | 原因 |
|-------|--------|--------|------|
| "黑咖啡，低糖" | recommend | recommend | `detect_normal_product_category` 命中"咖啡"→ 不受影响 |
| "通勤降噪豆" | recommend | recommend | `SCENARIO_SHOPPING_TERMS` 命中"通勤"→ 不受影响 |
| "200 内油皮夏天防晒" | recommend | recommend | `detect_normal_product_category` 命中"护肤"→ 不受影响 |
| "你好" | general_chat | general_chat | `GENERAL_CHAT_TERMS` 命中"你好"→ 改动 1 仍返回 True |
| "推荐逻辑是什么" | general_chat | general_chat | `GENERAL_CHAT_TERMS` 命中"推荐逻辑"→ 不受影响 |
| "加购第一个" | cart | cart | 购物车意图硬关键词→ 不受影响 |
| "对比 A 和 B" | compare | compare | 对比意图硬关键词→ 不受影响 |

**被修复的 case**（5 个 wrong_route）：

| query | 原路由 | 改动后 | 原因 |
|-------|--------|--------|------|
| "训练穿的缓震篮球鞋" | general_chat ✗ | recommend ✓ | `_is_general_chat` 不再负面推断；LLM 接管 |
| "日常训练跑步鞋" | general_chat ✗ | recommend ✓ | 同上 |
| "1000 内篮球实战鞋" | general_chat ✗ | recommend ✓ | 同上 |
| "运动裤或运动上衣" | general_chat ✗ | recommend ✓ | 同上 |
| "送父母实用数码产品" | general_chat ✗ | recommend ✓ | 同上 |

**需要关注的边界 case**：

有些 query 原本走 `general_chat` 但改动后可能走 `recommend_shopping_products`。需要确认这些 case 在推荐管线中的表现：
- "随便看看" — 如果 `GENERAL_CHAT_TERMS` 不包含"随便看看"，改动后会走推荐管线。`validate_business_goal()` 中"看看"是否在 `SHOPPING_GOAL_KEYWORDS` 中？如果在，会正常处理；如果不在，会返回引导性回复。
- "今天天气怎么样" — `GENERAL_CHAT_TERMS` 不包含天气词。改动后走推荐管线 → `validate_business_goal()` 会因为没有购物关键词而拦截 → 返回引导回复。行为等价于 general_chat，只是路径不同。

**建议**：修改前跑一遍完整的 `test_tool_router.py` 单元测试，确认 28 个现有测试不回归。

---

### 前置条件：LLM 调用可靠性修复

以上 4 处改动让 LLM 成为路由决策的核心参与者。但如果 LLM 调用成功率仍然是 0%，那 LLM 兜底形同虚设。所以 **LLM 调用链路的修复是前置条件**：

| 改动 | 位置 | 内容 |
|------|------|------|
| Router HTTP 超时硬限 | tool_router.py:883-884 | 5s → 10s（或读环境变量） |
| 评估脚本 Router 超时 | eval_model_chain_ablation.py:210 | default 5.0 → 10.0 |
| 评估脚本熔断冷却 | eval_model_chain_ablation.py:466 | "3600" → "120" |
| JSON mode 参数 | llm_client.py:260-266 | 当 supports_json_mode=True 时发送 response_format |
| JSON 提取正则 | llm_client.py:365 | `.*?` → `.*`（修复嵌套 JSON 截断）|

这 5 项是纯工程参数调整，不涉及业务逻辑变更。

---

### 总结

| 层面 | 原方案（已废弃） | 修订方案 |
|------|----------------|---------|
| 路由关键词 | 增加 30+ 词到字典 | **不改字典**，收窄 `_is_general_chat` 负面逻辑 |
| LLM 角色 | 高置信本地路由时才调用 | **低置信时 LLM 必须参与** |
| 兜底行为 | general_chat（闲聊） | **recommend_shopping_products**（推荐管线二次验证） |
| Guard 层 | 负面推断 → general_chat | **正面匹配才覆写**，否则保留 LLM 结果 |
| LLM 可靠性 | 5s 硬限 + 3600s 熔断 | 10s 硬限 + 120s 熔断 + JSON mode |

核心理念：**宁可让推荐管线说"我不太确定你要什么"，也不要把购物 query 丢给闲聊。**
