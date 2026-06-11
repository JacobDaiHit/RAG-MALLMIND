# 回复多样化 + PC 路由修复 + 话题切换增强

**日期:** 2026-06-11  
**基于:** 21 case 边界测试 · 回复生成链路分析 · PC 路由根因分析 · 话题切换机制分析

---

## 零、架构评估：当前链路能否支持三种推荐模式

### 现状

当前链路**已经原生支持**三种推荐类型：

| 模式 | 触发条件 | 路由工具 | 数据流 |
|------|----------|----------|--------|
| **单品推荐** | `need_bundle=False` 且单品类命中 | `recommend_shopping_products` | `build_recommendation_result()` → 单品类过滤+评分 |
| **组合推荐** | `need_bundle=True` 或多品类命中 | `recommend_shopping_products` | `build_recommendation_result()` → 跨品类组合 |
| **PC 方案** | 识别 PC 意图 | `generate_pc_build_plan` | 独立 PC 链路 → `build_pc_plan_for_message()` |

**组合推荐的触发条件：**
- LLM 或规则解析器检测到 `BUNDLE_KEYWORDS`（一套、全套、搭配、套装、穿搭、配齐）
- 或用户指定多个品类（如"防晒霜+遮阳帽+墨镜"），`desired_categories` 有多个值

**三种模式能共存的关键：**
- Router 先决定走 `recommend_shopping_products` 还是 `generate_pc_build_plan`
- 前者内部根据 `need_bundle` 和品类数量自动选择单品/组合
- PC 链路完全独立，有自己的约束解析和配置生成逻辑

**结论：架构本身已经支持三种模式，不需要新增路由工具。问题在于 Router 在模式切换时的判断不够准确。**

---

## 一、chat_topic 改造影响评估

### 分析结论：`chat_topic` 是死字段，改它没用

`chat_topic` 当前只有两个作用：
1. 在 LLM router 的 user prompt 中显示为 `Chat topic: {chat_topic}`（纯提示，不触发任何分支逻辑）
2. 在 `session_to_json()` 中输出给调试/前端

**没有任何代码**用 `chat_topic == "xxx"` 做条件判断。

### 真正控制话题切换的是 `topic_memory.topic_type`

```
topic_memory.topic_type 的值和影响:

"pc_build"                     → local_route_tool_call 的 PC followup 判断
                               → should_start_new_product_topic 的豁免/切换逻辑
                               → has_last_pc_build_plan 的历史判断

"single_pc_part"               → 同上，作为 PC 子状态参与切换检测

"ecommerce_recommendation"     → should_start_new_product_topic 直接 return False
                               → 意味着电商推荐场景下永不检测话题切换！

"comparison" / "cart" / "general_chat"
                               → 同上，永不检测话题切换
```

### 修正方案：不改 `chat_topic`，改为增强 `topic_memory.topic_type` 的使用

- **Router user prompt** 注入 `topic_memory.topic_type`（当前缺失）
- **`should_start_new_product_topic()`** 扩展覆盖到电商场景
- **System prompt** 增加"会话主题保持"规则

---

## 二、方案 A：回复多样化

### 根因

`build_chat_delta_lines()` 是用户看到回复的**唯一来源**，它从不调用 LLM，全部硬编码模板。

```
用户看到:
  "我先按你的需求筛一遍商品库，优先找最相关的真实商品。
   我优先推荐 XX商品，参考价约 ¥XXX。
   下面保留了候选商品卡片，你可以继续对比或加入购物车。"
```

LLM 富化（`enrich_recommendation_result` / `attach_grounded_explanation`）生成的内容被写入 `teaching_guidance` 和 `feedback_summary.grounded_explanation`，但**不被 `build_chat_delta_lines` 引用**——等于白做了。

### 改造方案

在 `remember_recommendation` 之后、SSE delta 之前，插入**响应生成器**。

```
  ⑦ remember_recommendation(session, goal, payload)
      │
      ▼
  🟢 ⑦.5 generate_natural_response(payload, session, message)
      │
      ├─ LLM 可用 且 payload.fact_check.degraded != True
      │    → llm_diverse_response(payload, context)
      │       ├─ temperature=0.9, max_tokens=200, timeout=5s
      │       └─ 失败 → 降级
      │
      └─ 否则 → naturalize_response(payload)
           └─ 5~8 种表达随机选择
      │
      ▼
  ⑧ yield SSE delta = 生成的文本
```

### 新文件：`rag/recommendation/response_generator.py`

```python
def generate_natural_response(
    payload: Dict[str, Any],
    session: Any,
    message: str,
) -> str:
    """生成多样化自然语言回复，LLM 优先，模板兜底."""

def llm_diverse_response(payload, context) -> Optional[str]:
    """调用 LLM 生成回复。temperature=0.9，禁止编造."""

def naturalize_response(payload) -> str:
    """模板变体库，5~8 种表达随机选择."""
```

### LLM Prompt 设计

```
你是友好的电商导购助手。根据事实数据，用自然语言回复用户。

【用户需求】: {message}
【推荐商品】: {products}  （名称、品牌、价格）
【预算】: {budget}

【约束】:
1. 不编造商品名、价格、库存
2. 2-3句话，不超过120字
3. 像真人导购，不用"根据你的需求""推荐理由如下"等套路句式
4. 超预算时友好提醒

只输出回复文本。
```

### 预期效果对比

| 场景 | 当前输出 | 改进后 |
|------|----------|--------|
| 面霜推荐 | "我先按你的需求筛一遍商品库…我优先推荐 薇诺娜…" | "薇诺娜这款特护霜很适合敏感肌，268块性价比不错。玉兰油大红瓶更实惠，89块～" |
| PC 配置 | "核心为Core i5…总价9332…硬校验…软评分…" | "这套 i5+4070 玩《黑神话》高画质绰绰有余，9332块略超预算但绝对值～" |
| 无结果 | "这次没有找到足够贴合的商品…" | "8000块暂时配不出《黑神话》整机，降到4060Ti能省1500，要不要看看？" |

### 性能控制

- 复用现有 LLM 熔断器（60s内5次失败→禁用30s）
- 超时 5s（远低于路由器的 15s）
- 失败无缝降级到模板变体库

---

## 三、方案 B：PC 路由修复

### 根因分析

Case 4 Turn 2："CPU要Intel的，不要AMD" 被路由到 `recommend_shopping_products` 而不是 `generate_pc_build_plan`。

**三重合因导致失败：**

| 层面 | 根因 |
|------|------|
| **LLM 路由（主因）** | System prompt 第 1422 行教导 LLM："更换单个配件（'CPU要Intel'）应使用 recommend_shopping_products，catalog_scope=pc_parts" |
| **本地路由** | `is_pc_build_followup()` 的 `adjust_terms` 不包含 "不要"/"Intel"/"AMD"，三个分支均未触发 |
| **上下文注入** | `topic_memory.topic_type="pc_build"` 未注入 LLM user prompt；LLM 看不到"当前在PC构建话题中"的信号 |

### 修改 1：修正 System Prompt

**文件：** `rag/recommendation/tool_router.py` `_build_router_system_prompt()`

删除：
```
- "更换单个配件（'CPU要Intel'）应使用 recommend_shopping_products，catalog_scope=pc_parts。"
```

替换为：
```
- "如果 Accumulated state 显示上一轮使用了 generate_pc_build_plan，
   且用户新消息是对已有方案的修改（如换CPU品牌、加内存、改预算），
   必须继续使用 generate_pc_build_plan，不得切换工具。"
- "修改已有PC方案的例子：'CPU要Intel的，不要AMD'、'内存升级到32G'、
   '显卡换成RTX 4070'、'预算加到一万'——这些都应使用 generate_pc_build_plan。"
```

### 修改 2：增强 `is_pc_build_followup()`

**文件：** `rag/recommendation/tool_router.py`

```python
def is_pc_build_followup(message: str, session: ShoppingSession) -> bool:
    # ... 现有三个分支保持不变 ...

    # 🟢 新增分支 D：有PC配件词 + session 中有PC构建历史 → 直接判为followup
    if has_pc_part and bool(getattr(session, "pc_build_history", None)):
        return True

    # 🟢 扩展 adjust_terms：
    #   新增 "不要", "只要", "要Intel", "要AMD", "要NVIDIA",
    #        "改成", "换成", "不用"
    # ...
```

关键新增：**分支 D 是"有 PC 历史 + 提了配件词 = 就是对历史方案的修改"**。这个判断比关键词匹配更可靠，因为它依赖 session 上下文的实际状态。

### 修改 3：Router User Prompt 注入 topic_memory

**文件：** `rag/recommendation/tool_router.py` `_build_router_user_prompt()`

当前注入的内容：
```
Accumulated state: {session.current}
Recent queries: [...]
Chat topic: recommendation    ← 无区分度
```

增加：
```python
topic = current_topic_json(session)
topic_type = topic.get("topic_type", "")
if topic_type == "pc_build":
    parts.append(
        f"当前话题: PC装机方案。"
        f"用户可能在修改或追问已生成的配置。"
        f"如果有新的硬件需求，继续使用 generate_pc_build_plan。"
    )
elif topic_type == "ecommerce_recommendation":
    parts.append("当前话题: 商品推荐。用户在筛选或追问商品细节。")
```

### 修改 4：`should_start_new_product_topic()` 盲区修复（方案 C）

见下一节。

---

## 四、方案 C：话题切换增强

### 当前盲区

| 盲区 | 现象 | 影响 |
|------|------|------|
| **电商场景无切换检测** | `should_start_new_product_topic()` 仅当 `topic_type in {"pc_build","single_pc_part"}` 时检查 | 从"推荐手机"转到"推荐咖啡"，上下文被错误拼接 |
| **`looks_like_followup()` 太宽泛** | ≤12字符一律视为 followup；关键词含 "预算""显卡"等通用词 | "换显卡"被误判为 followup |
| **PC→单品切换盲区** | `pc_build_terms` 不包含 "显卡""CPU" 等单个配件词 | "换显卡"不在豁免列表中，被错误触发切换 |
| **无显式切换信号** | 用户说"不要了""换个话题""算了"时无感知 | Case 21 "算了，我不要了" 后仍延续旧上下文 |

### 修改

#### C1：扩展 `should_start_new_product_topic()` 到电商场景

**文件：** `rag/recommendation/session_state.py`

```python
def should_start_new_product_topic(session, message: str) -> bool:
    topic = current_topic_json(session)
    topic_type = topic.get("topic_type", "")

    # 🟢 新增：显式话题切换信号优先检测
    if _has_explicit_topic_switch(message):
        return True

    # 原逻辑：仅 PC 场景检测 → 🟢 扩展为所有推荐场景
    if topic_type not in {"pc_build", "single_pc_part", "ecommerce_recommendation"}:
        return False  # comparison/cart/chat 不检测切换

    # 🟢 新增：ecommerce 场景下的切换检测
    if topic_type == "ecommerce_recommendation":
        last_category = topic.get("category", "")
        new_category = _detect_category_from_message(message)
        # 品类明确改变 → 切换
        if new_category and new_category != last_category:
            return True
        return False

    # ... 原有 PC 场景逻辑保留 ...
```

#### C2：新增显式话题切换信号

```python
_EXPLICIT_SWITCH_SIGNALS = [
    "换个话题", "不要了", "算了", "不买了",
    "看看别的", "换一个", "不看这个了", "我想要",
    "帮我推荐", "推荐一下", "有什么推荐",
]

def _has_explicit_topic_switch(message: str) -> bool:
    """用户明确表达了切换意图."""
    text = message.strip()
    # 短消息以 "帮我推荐" 或 "我想要" 开头 → 可能是新话题
    if any(text.startswith(s) for s in ["帮我", "我想", "推荐"]):
        return True
    return any(s in text for s in _EXPLICIT_SWITCH_SIGNALS)
```

#### C3：收紧 `looks_like_followup()`

移除过于宽泛的关键词，增加上下文感知：

```python
def looks_like_followup(message: str) -> bool:
    text = message.strip()
    # 🟢 短消息（≤12字符）不再一律视为 followup
    # 只检测明确的 followup 模式
    followup_patterns = [
        "还有", "另外", "再加", "顺便", "对了",
        "那", "这个", "这款", "多", "少", "够",
        "能", "可以", "有没有", "能不能"
    ]
    return any(text.startswith(p) for p in followup_patterns)
```

---

## 五、Router 多模式感知增强

### 当前问题

LLM Router 的 system prompt 没有明确告诉 LLM 三种推荐模式的区别和切换规则。LLM 不知道：
- 什么情况下应该输出 `need_bundle=True`
- PC 配件什么时候应该作为单品推荐 vs 整机方案
- 话题切换时如何判断

### 修改

在 system prompt 中增加"模式感知"段落：

```
【推荐模式选择规则】

1. 单品推荐 (recommend_shopping_products, need_bundle=false)
   - 用户只需要单个品类的一个商品（面霜、耳机、手机）
   - 用户提到单个PC配件（"推荐一款显卡"）→ catalog_scope=pc_parts

2. 组合推荐 (recommend_shopping_products, need_bundle=true)
   - 用户需要多个互补商品（"去三亚的防晒一套"、"配齐护肤套装"）
   - 触发词：一套、全套、搭配、组合、套装、穿搭、配齐、旅行装备

3. PC整机方案 (generate_pc_build_plan)
   - 用户要配完整的电脑主机
   - 触发词：配电脑、装机、整机、配置单、配一台
   - 如果已经在PC构建话题中，后续修改（换CPU、加内存、调预算）
     也继续使用此工具

【话题切换判断】
- 如果当前话题是PC构建，用户说"换个话题，推荐个手机"
  → 切换为 recommend_shopping_products（新品类的单品推荐）
- 如果当前话题是商品推荐，用户说"配台电脑"
  → 切换为 generate_pc_build_plan
- 如果不确定，优先保持当前话题
```

---

## 六、修改文件清单

| 文件 | 改动 | 说明 |
|------|------|------|
| `rag/recommendation/response_generator.py` | 🟢 新建 | 响应生成器（LLM多样+模板兜底） |
| `rag/api/routes/chat.py` | 🔧 修改 | 在 handler 返回后调用响应生成器 |
| `rag/recommendation/tool_router.py` | 🔧 修改 3 处 | System prompt + `is_pc_build_followup` + user prompt |
| `rag/recommendation/session_state.py` | 🔧 修改 2 处 | `should_start_new_product_topic` 扩展 + `looks_like_followup` 收紧 |
| `.env` | 🟢 新增 2 行 | `RECOMMENDATION_RESPONSE_LLM=true`, `RECOMMENDATION_RESPONSE_MODEL=mimo-v2.5` |

---

## 七、风险与回退

| 改动 | 风险 | 回退 |
|------|------|------|
| 响应生成器 | LLM 超时增加延迟 | 5s 超时+熔断，降级模板 <1ms |
| System prompt 修改 | LLM 路由行为改变 | 可通过 env 回退旧 prompt |
| `is_pc_build_followup` 分支 D | PC 配件推荐误判为 PC 构建 | 仅当有 PC 历史时才触发，不影响首轮 |
| `should_start_new_product_topic` 扩展 | 话题切换过于激进 | 保守渐进：先加显式信号检测，再扩展到电商场景 |
| `looks_like_followup` 收紧 | followup 拼接变少 | 新用户的追问仍会被 router 识别，只是不走拼接路径 |

---

## 八、验证方式

```bash
# 1. PC 路由修复验证
# Case 4 Turn 2 必须路由到 generate_pc_build_plan

# 2. 话题切换验证 — Case 21 全场景切换
# 手机→华为排除→4千预算→智能手表→放弃→PC装机→1万预算→3A→对比→购物车

# 3. 回复多样化验证 — 同一查询多次请求
# "推荐一款面霜" × 3 → delta 文本不应完全相同

# 4. 组合推荐验证
# "推荐去三亚旅游的防晒一套" → 应返回多品类商品组合

# 5. 全量回归
python tests/run_bound_test.py
```

---

*方案完。*
