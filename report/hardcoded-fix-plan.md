# 硬编码修复方案

**最后更新：** 2026-06-13  
**版本：** v1.0  
**关联文档：** [链路架构说明](current-link-architecture.md) 第 1.2 节热力图 + Top 10 优先级表  
**预估工作量：** 3 个阶段，共约 8-10 小时  
**影响范围：** ~500+ inline 硬编码值，横跨 10 个文件

---

## 目录

- [总览：三阶段实施计划](#总览三阶段实施计划)
- [Phase A：速赢修复（< 2 小时）](#phase-a速赢修复-2-小时)
  - [Fix A1：SSE 事件类型 Enum](#fix-a1sse-事件类型-enum)
  - [Fix A2：工具名 Enum](#fix-a2工具名-enum)
  - [Fix A3：购物车操作 Enum](#fix-a3购物车操作-enum)
  - [Fix A4：Session 历史窗口上限](#fix-a4session-历史窗口上限)
- [Phase B：核心修复（2-4 小时）](#phase-b核心修复2-4-小时)
  - [Fix B1：路由评分权重提取](#fix-b1路由评分权重提取)
  - [Fix B2：评分函数常量提取](#fix-b2评分函数常量提取)
  - [Fix B3：LLM Gateway 配置数据化](#fix-b3llm-gateway-配置数据化)
  - [Fix B4：路由关键词列表统一](#fix-b4路由关键词列表统一)
- [Phase C：打磨修复（较低优先级）](#phase-c打磨修复较低优先级)
  - [Fix C1：extract_item_index 泛化](#fix-c1extract_item_index-泛化)
  - [Fix C2：中文 UI 文案 i18n 模块](#fix-c2中文-ui-文案-i18n-模块)
- [风险矩阵总表](#风险矩阵总表)
- [测试策略](#测试策略)
- [附录：已提取命名常量参考](#附录已提取命名常量参考)

---

## 总览：三阶段实施计划

| 阶段 | 修复项 | 预估时间 | 消除 inline 数量 | 风险等级 |
|------|--------|----------|-----------------|----------|
| **A 速赢** | A1-A4 | < 2 小时 | ~180 个 | 低 |
| **B 核心** | B1-B4 | 2-4 小时 | ~250 个 | 中 |
| **C 打磨** | C1-C2 | 2-3 小时 | ~70 个 | 低 |
| **合计** | 10 项 | 8-10 小时 | **~500+ 个** | — |

**实施原则：**

1. **每个 Fix 独立 PR**：方便 review 和回滚
2. **先加后删**：先定义常量/Enum，再逐步替换引用，不做大爆炸重构
3. **行为等价**：所有修复必须保证运行时行为 100% 不变，136 轮回归零退化
4. **每个 Fix 附带测试**：新增或修改的常量必须有单元测试覆盖

---

## Phase A：速赢修复（< 2 小时）

> 特点：改动模式简单（定义 Enum/dataclass + 全局替换），风险低，收益高。

---

### Fix A1：SSE 事件类型 Enum

**优先级：** 1（最高）  
**目标文件：** `tool_handlers.py`（主）、`chat.py`（辅）  
**消除 inline 数量：** ~50 个

#### 当前问题

SSE 事件类型以裸字符串散布在两个文件中，共 25 种事件类型、~50 处引用。拼写错误无法被静态检查发现。

**实际代码（`tool_handlers.py`）：**

```python
# line 47
yield sse_event("delta", {"text": text or "购物车已更新。"})
# line 48
yield sse_event("cart", cart_result)
# line 49
yield sse_event("done", {"session_id": session.session_id})

# line 230
yield sse_event("error", {"label": "商品不存在", "detail": f"product_id {plan_product_id} 不在商品库中。"})
# line 247
yield sse_event("cart_confirmation", {
    "plan": plan,
    "message": _build_confirmation_message(plan, "add"),
})
# line 270
yield sse_event("cart_clarification", {
    "text": ambiguity,
    ...
})
# line 550
yield sse_event("intent_route", {"route": "comparison", ...})
# line 551
yield sse_event("comparison_table", {"rows": compare_result.get("rows") or []})
# line 552
yield sse_event("result", {...})
# line 598
yield sse_event("delta", {"text": "\n".join(text_parts)})
# line 599
yield sse_event("pc_comparison_table", {...})
# line 778
yield sse_event("validation_error", {"label": "PC 方案无法生成", "detail": public_error(exc)})
# line 804
yield sse_event("pc_build_plan", plan)
# line 909
yield sse_event("product_cards", {"products": ...})
# line 910
yield sse_event("candidate_scope", response_payload.get("candidate_scope") or {})
# line 914
yield sse_event("comparison_table", {"rows": comparison_rows})
# line 916
yield sse_event("follow_up_questions", {"questions": ...})
# line 917
yield sse_event("result", response_payload)
# line 937
yield sse_event("cart", cart_result)
# line 947
yield sse_event("done", {"session_id": session.session_id})
```

**`chat.py` 中也有散布引用：**

```python
# line 132
yield sse_event("runtime_mode", {"mode": "balanced", "use_llm": use_llm})
# line 149-158
yield sse_event("tool_call", {...})
# line 177
yield sse_event("progress", {"label": "已收到需求", "detail": "开始整理预算、品类、颜色和功能约束。"})
# line 182
yield sse_event("attachment_analysis", {...})
# line 196
yield sse_event("progress", {"label": "正在解析条件", ...})
```

#### 解决方案

**新建文件 `rag/api/sse_events.py`：**

```python
"""SSE 事件类型枚举——消除散布在 tool_handlers.py / chat.py 中的 ~50 个 inline 字符串。"""
from enum import Enum


class SSEEvent(str, Enum):
    """所有 SSE 事件类型。值即发送到客户端的 event 名。"""

    # ── 主链路事件 ──
    RUNTIME_MODE       = "runtime_mode"
    TOOL_CALL          = "tool_call"
    PROGRESS           = "progress"
    DELTA              = "delta"
    ATTACHMENT_ANALYSIS = "attachment_analysis"
    INTENT_ROUTE       = "intent_route"
    PRODUCT_CARDS      = "product_cards"
    COMPARISON_TABLE   = "comparison_table"
    CART_CONFIRMATION  = "cart_confirmation"
    CART_CLARIFICATION = "cart_clarification"
    FACT_CHECK         = "fact_check"
    PC_BUILD_PLAN      = "pc_build_plan"
    PC_COMPARISON_TABLE = "pc_comparison_table"
    CANDIDATE_SCOPE    = "candidate_scope"
    FOLLOW_UP_QUESTIONS = "follow_up_questions"
    RESULT             = "result"
    CART               = "cart"
    DONE               = "done"

    # ── 推荐图谱事件 ──
    STEP               = "step"
    REQUIREMENT        = "requirement"
    CATALOG            = "catalog"
    PLANS              = "plans"
    GUIDANCE           = "guidance"

    # ── 异常/校验事件 ──
    ERROR              = "error"
    VALIDATION_ERROR   = "validation_error"

    def __str__(self) -> str:
        return self.value
```

**修改 `rag/api/sse.py` 让 `sse_event()` 接受 `str | SSEEvent`：**

```python
def sse_event(event: str | SSEEvent, data: Any) -> str:
    """event 参数现在同时接受字符串和 SSEEvent Enum。"""
    event_name = str(event)  # SSEEvent.__str__ 返回 .value
    ...
```

**替换示例（`tool_handlers.py`）：**

```python
# Before (line 47):
yield sse_event("delta", {"text": text or "购物车已更新。"})
yield sse_event("cart", cart_result)
yield sse_event("done", {"session_id": session.session_id})

# After:
yield sse_event(SSEEvent.DELTA, {"text": text or "购物车已更新。"})
yield sse_event(SSEEvent.CART, cart_result)
yield sse_event(SSEEvent.DONE, {"session_id": session.session_id})
```

#### 影响评估

| 维度 | 值 |
|------|-----|
| 消除 inline 数量 | ~50 处 |
| 修改文件数 | 3（新建 1 + 修改 2） |
| 行为变化 | 无（`SSEEvent.__str__` 返回原始字符串值） |
| 新增测试 | 1 个：验证所有 Enum 值与原始字符串一致 |

#### 风险

- **低风险**：`str(SSEEvent.DELTA) == "delta"` 保证等价
- **注意事项**：`sse_event()` 函数签名需兼容 `str | SSEEvent`，向后兼容

#### 实施步骤

1. 新建 `rag/api/sse_events.py`，定义 `SSEEvent` Enum
2. 修改 `rag/api/sse.py` 的 `sse_event()` 签名兼容 Enum
3. 在 `tool_handlers.py` 中全局替换 25 种事件字符串
4. 在 `chat.py` 中全局替换涉及的 ~10 处
5. 添加单元测试 `test_sse_event_enum.py` 验证 Enum 值完备性

---

### Fix A2：工具名 Enum

**优先级：** 2  
**目标文件：** `chat.py`（主）、`tool_router.py`（辅）  
**消除 inline 数量：** ~30 个

#### 当前问题

8 个工具名在 3 个位置重复定义：注册表、分发逻辑、跳过更新判断。拼写错误无法静态检测。

**实际代码（`chat.py`）：**

```python
# line 76-83: _LIGHTWEIGHT_TOOLS 注册表——6 个工具名
_LIGHTWEIGHT_TOOLS = {
    "apply_cart_instruction",
    "general_chat",
    "compare_products",
    "parameter_query",
    "sku_detail",
    "price_comparison",
}

# line 94-108: _dispatch_lightweight 分发——6 个工具名重复
def _dispatch_lightweight(tool_name, session, tool_call, raw_message, request):
    if tool_name == "apply_cart_instruction":
        yield from handle_cart_v2(session, raw_message, request_product_ids(request), tool_call)
    elif tool_name == "general_chat":
        yield from handle_general_chat(session, tool_call)
    elif tool_name == "compare_products":
        ...
    elif tool_name == "parameter_query":
        ...
    elif tool_name == "sku_detail":
        ...
    elif tool_name == "price_comparison":
        ...

# line 142-146: session 更新跳过逻辑——3 个工具名
_should_update_session = (
    tool_call.get("name") != "apply_cart_instruction"
    and not tool_call.get("downgraded")
    and tool_call.get("name") != "general_chat"
)

# line 204
if tool_name == "generate_pc_build_plan":
    ...
# line 211
tool_name = "recommend_shopping_products"
```

**`tool_router.py` 中也有重复定义：**

```python
# line 26-35: ALLOWED_TOOL_NAMES
ALLOWED_TOOL_NAMES = {
    "recommend_shopping_products",
    "generate_pc_build_plan",
    "compare_products",
    "apply_cart_instruction",
    "general_chat",
    "parameter_query",
    "sku_detail",
    "price_comparison",
}

# line 36-45: LOCAL_ROUTE_NAMES——完全相同的列表再写一遍
LOCAL_ROUTE_NAMES = [
    "recommend_shopping_products",
    "generate_pc_build_plan",
    "compare_products",
    "apply_cart_instruction",
    "general_chat",
    "parameter_query",
    "sku_detail",
    "price_comparison",
]
```

#### 解决方案

**新建文件 `rag/recommendation/tool_names.py`：**

```python
"""工具名枚举——消除 chat.py / tool_router.py / session_state.py 中的重复字符串。"""
from enum import Enum


class ToolName(str, Enum):
    """系统支持的所有工具名。"""
    RECOMMEND    = "recommend_shopping_products"
    PC_BUILD     = "generate_pc_build_plan"
    COMPARE      = "compare_products"
    CART         = "apply_cart_instruction"
    GENERAL_CHAT = "general_chat"
    PARAM_QUERY  = "parameter_query"
    SKU_DETAIL   = "sku_detail"
    PRICE_CMP    = "price_comparison"

    def __str__(self) -> str:
        return self.value


# 按职责分组
LIGHTWEIGHT_TOOLS = frozenset({
    ToolName.CART,
    ToolName.GENERAL_CHAT,
    ToolName.COMPARE,
    ToolName.PARAM_QUERY,
    ToolName.SKU_DETAIL,
    ToolName.PRICE_CMP,
})

HEAVY_TOOLS = frozenset({
    ToolName.RECOMMEND,
    ToolName.PC_BUILD,
})

ALL_TOOL_NAMES = frozenset(ToolName)

# 不更新 session.current 的工具
NO_SESSION_UPDATE_TOOLS = frozenset({
    ToolName.CART,
    ToolName.GENERAL_CHAT,
})
```

**替换示例（`chat.py`）：**

```python
# Before (line 76-83):
_LIGHTWEIGHT_TOOLS = {
    "apply_cart_instruction",
    "general_chat",
    ...
}

# After:
from rag.recommendation.tool_names import LIGHTWEIGHT_TOOLS, ToolName

# line 163 直接使用:
if tool_name in LIGHTWEIGHT_TOOLS:
    ...

# line 142-146 简化:
from rag.recommendation.tool_names import NO_SESSION_UPDATE_TOOLS
_should_update_session = (
    tool_name not in NO_SESSION_UPDATE_TOOLS
    and not tool_call.get("downgraded")
)
```

**替换示例（`tool_router.py`）：**

```python
# Before (line 26-45): 两个列表各写一遍
ALLOWED_TOOL_NAMES = {...}
LOCAL_ROUTE_NAMES = [...]

# After:
from rag.recommendation.tool_names import ALL_TOOL_NAMES, ToolName

ALLOWED_TOOL_NAMES = {t.value for t in ToolName}
LOCAL_ROUTE_NAMES = [t.value for t in ToolName]
```

#### 影响评估

| 维度 | 值 |
|------|-----|
| 消除 inline 数量 | ~30 处 |
| 修改文件数 | 4（新建 1 + 修改 3） |
| 行为变化 | 无 |
| 额外收益 | 消除 `_LIGHTWEIGHT_TOOLS` 和 `_dispatch_lightweight` 之间的不一致风险 |

#### 风险

- **低风险**：纯替换，`str(ToolName.CART) == "apply_cart_instruction"`
- **注意事项**：`tool_call.get("name")` 返回的是 `str`，与 `ToolName` 比较时需确保 `==` 兼容（`str, Enum` 混用 OK）

#### 实施步骤

1. 新建 `rag/recommendation/tool_names.py`
2. 修改 `tool_router.py` 中 `ALLOWED_TOOL_NAMES` 和 `LOCAL_ROUTE_NAMES`
3. 修改 `chat.py` 中 `_LIGHTWEIGHT_TOOLS`、`_dispatch_lightweight`、`_should_update_session`
4. 修改 `session_state.py` 中 `_topic_type_for_tool` 的分支判断
5. 添加测试验证 Enum 值与硬编码字符串一致

---

### Fix A3：购物车操作 Enum

**优先级：** 7  
**目标文件：** `tool_handlers.py`（主）、`session_state.py`（辅）  
**消除 inline 数量：** ~15 个

#### 当前问题

购物车 4 种操作 `("add", "remove", "set_quantity", "clear")` 在多处重复出现。

**实际代码（`tool_handlers.py`）：**

```python
# line 67: 判断 clear
if action == "clear":
# line 71: 判断 remove / set_quantity
if action in ("remove", "set_quantity"):
# line 86: _resolve_cart_action 中验证
if op and op in ("add", "remove", "set_quantity", "clear"):
    return op

# line 144-150: _build_confirmation_message 分支
if operation == "remove":
    return f"确认从购物车移除 {title}？"
if operation == "set_quantity":
    return f"确认将 {title} 的数量修改为 {qty}？"
# 默认 add
```

**`session_state.py` 中同样有：**

```python
# line 972-979: infer_cart_action
def infer_cart_action(instruction: str) -> str:
    if any(keyword in instruction for keyword in ["清空", "全部删除", "删光"]):
        return "clear"
    if any(keyword in instruction for keyword in ["删除", "删掉", "删了", "移除", "不要了"]):
        return "remove"
    if any(keyword in instruction for keyword in ["数量", "改成", "改为", "修改"]):
        return "set_quantity"
    return "add"

# line 897-924: apply_cart_instruction 中的 4 个分支
if action == "clear":
    ...
elif action == "remove":
    ...
elif action == "set_quantity":
    ...
else:  # add
    ...
```

#### 解决方案

**在 `rag/recommendation/tool_names.py` 中新增：**

```python
class CartOperation(str, Enum):
    """购物车操作类型。"""
    ADD          = "add"
    REMOVE       = "remove"
    SET_QUANTITY = "set_quantity"
    CLEAR        = "clear"

    def __str__(self) -> str:
        return self.value

    @classmethod
    def from_text(cls, text: str) -> "CartOperation":
        """从用户消息推断操作类型（替代 infer_cart_action）。"""
        for keyword in ("清空", "全部删除", "删光"):
            if keyword in text:
                return cls.CLEAR
        for keyword in ("删除", "删掉", "删了", "移除", "不要了"):
            if keyword in text:
                return cls.REMOVE
        for keyword in ("数量", "改成", "改为", "修改"):
            if keyword in text:
                return cls.SET_QUANTITY
        return cls.ADD
```

**替换示例：**

```python
# Before (tool_handlers.py line 82-89):
def _resolve_cart_action(args, message):
    op = args.get("operation")
    if op and op in ("add", "remove", "set_quantity", "clear"):
        return op
    return infer_cart_action(message)

# After:
def _resolve_cart_action(args, message):
    op = args.get("operation")
    if op:
        try:
            return CartOperation(op)
        except ValueError:
            pass
    return CartOperation.from_text(message)
```

#### 影响评估

| 维度 | 值 |
|------|-----|
| 消除 inline 数量 | ~15 处 |
| 修改文件数 | 3（`tool_names.py` + `tool_handlers.py` + `session_state.py`） |
| 行为变化 | 无 |

#### 风险

- **低风险**：`CartOperation` 继承 `str`，与原字符串完全兼容
- **注意事项**：`infer_cart_action()` 被 `apply_cart_instruction()` 直接调用，替换时需保持调用链完整

---

### Fix A4：Session 历史窗口上限

**优先级：** 5  
**目标文件：** `session_state.py`  
**消除 inline 数量：** 5 个（但修复了一致性隐患）

#### 当前问题

5 个历史窗口上限分散在不同函数中，且存在三处不一致：

| 窗口 | 上限值 | 位置 | 备注 |
|------|--------|------|------|
| `messages` | 12 | line 830: `del session.messages[:-12]` | remember_recommendation |
| `topic_history`（topic_memory.history） | 8 | line 517: `del history[:-8]` | update_topic_memory |
| `topic_history`（session 级） | 20 | line 684: `session.topic_history[-20:]` | update_session_from_router |
| `recent_queries` | 5 | line 572: `session.recent_queries[-5:]` | update_session_from_router |
| `pc_build_history` | 6 | line 851: `del session.pc_build_history[:-6]` | remember_pc_build_plan |

**额外分散引用：**

```python
# line 729: session_to_json 中又截了一次 5
"topic_history": session.topic_history[-5:],
```

#### 解决方案

**在 `session_state.py` 顶部添加 `SessionLimits` dataclass：**

```python
@dataclass(frozen=True)
class SessionLimits:
    """Session 各历史窗口的最大保留条数。"""
    messages: int = 12
    topic_memory_history: int = 8
    session_topic_history: int = 20
    recent_queries: int = 5
    pc_build_history: int = 6
    tool_history: int = 12
    llm_call_log: int = 20  # chat.py _MAX_LOG_ENTRIES 也应引用此值


# 模块级单例
_SESSION_LIMITS = SessionLimits()
```

**替换示例：**

```python
# Before (line 830):
del session.messages[:-12]

# After:
del session.messages[:-_SESSION_LIMITS.messages]

# Before (line 517):
del history[:-8]

# After:
del history[:-_SESSION_LIMITS.topic_memory_history]

# Before (line 684):
session.topic_history = session.topic_history[-20:]

# After:
session.topic_history = session.topic_history[-_SESSION_LIMITS.session_topic_history:]

# Before (line 572):
session.recent_queries = session.recent_queries[-5:]

# After:
session.recent_queries = session.recent_queries[-_SESSION_LIMITS.recent_queries:]

# Before (line 851):
del session.pc_build_history[:-6]

# After:
del session.pc_build_history[:-_SESSION_LIMITS.pc_build_history:]
```

**`chat.py` 中同步修改：**

```python
# Before (line 247):
_MAX_LOG_ENTRIES = 20

# After:
from rag.recommendation.session_state import _SESSION_LIMITS
# line 265 改为:
while len(log) > _SESSION_LIMITS.llm_call_log:
    log.pop(0)
```

#### 影响评估

| 维度 | 值 |
|------|-----|
| 消除 inline 数量 | 7 处（5 个窗口 + tool_history + chat.py 的 _MAX_LOG_ENTRIES） |
| 修改文件数 | 2（`session_state.py` + `chat.py`） |
| 行为变化 | 无（值完全一致） |
| 额外收益 | 集中管理后，调整窗口大小只改一处 |

#### 风险

- **极低风险**：纯数值替换，行为等价
- **注意事项**：`_SESSION_LIMITS` 为 frozen dataclass，运行期不可变

#### 实施步骤

1. 在 `session_state.py` 顶部定义 `SessionLimits`
2. 替换 6 处窗口上限
3. `chat.py` 中 `_MAX_LOG_ENTRIES` 改为引用 `_SESSION_LIMITS.llm_call_log`
4. 添加测试：验证各窗口在边界条件下行为正确

---

## Phase B：核心修复（2-4 小时）

> 特点：涉及多文件、多函数的常量提取，需要仔细核对每个引用点。

---

### Fix B1：路由评分权重提取

**优先级：** 4  
**目标文件：** `tool_router.py`  
**消除 inline 数量：** ~15 个

#### 当前问题

`score_local_routes()` 函数中 11 个评分权重全部为 inline float，无法一眼看出评分体系的完整图景。

**实际代码（`tool_router.py` line 866-915）：**

```python
def score_local_routes(message: str, session: ShoppingSession) -> Dict[str, Any]:
    ...
    if cart_detected:
        scores["apply_cart_instruction"] += 0.75      # line 880
    if category_detected:
        scores["recommend_shopping_products"] += 0.55  # line 882
    if _has_product_query_intent(text, lowered):
        scores["recommend_shopping_products"] += 0.25  # line 884
    if cart_detected and category_detected:
        scores["recommend_shopping_products"] += 0.10  # line 889
    if single_pc_part:
        scores["recommend_shopping_products"] += 0.60  # line 891
    if pc_intent:
        scores["generate_pc_build_plan"] += 0.75       # line 893
    if pc_followup or pc_history_followup:
        scores["generate_pc_build_plan"] += 0.65       # line 895
    if _looks_like_compare_request(text):
        if pc_followup or pc_intent:
            scores["generate_pc_build_plan"] += 0.15   # line 898
        else:
            scores["compare_products"] += 0.55         # line 900
    if _is_general_chat(text, lowered, topic):
        scores["general_chat"] += 0.80                 # line 902
    if not any(scores.values()):
        scores["recommend_shopping_products"] = 0.45   # line 904
```

另外还有预算乘数表（line 1101-1109）：

```python
def _parse_budget_amount(value: str, unit: str = "") -> float:
    amount = float(re.sub(r"[\s,，]", "", value))
    normalized = (unit or "").strip().lower()
    _cn_multipliers = {
        "亿": 100_000_000,
        "千万": 10_000_000,
        "百万": 1_000_000,
        "十万": 100_000,
        "w": 10_000, "万": 10_000,
        "k": 1_000, "千": 1_000,
        "百": 100,
    }
```

#### 解决方案

**在 `tool_router.py` 模块级定义：**

```python
# ── 路由评分权重 ─────────────────────────────────────────────────────────
# 本地规则路由的信号评分。值越高表示该信号对该工具的指示性越强。
# 修改时务必参考 tests/test_tool_router.py 中的 136 轮回归用例。

class RouteScore:
    """本地路由评分权重常量。"""
    # 购物车意图
    CART_INTENT: float = 0.75
    # 商品品类检测
    CATEGORY_DETECTED: float = 0.55
    # 商品查询意图（搜索/浏览/事实查询）
    PRODUCT_QUERY_INTENT: float = 0.25
    # 组合意图加成（购物车 + 品类并存时，推荐需拉分）
    COMBO_INTENT_BOOST: float = 0.10
    # 单个 PC 配件
    SINGLE_PC_PART: float = 0.60
    # PC 整机意图
    PC_INTENT: float = 0.75
    # PC 后续追问
    PC_FOLLOWUP: float = 0.65
    # PC 场景内的对比（仍属 PC 方案修改）
    PC_COMPARE_BOOST: float = 0.15
    # 通用对比
    COMPARE_REQUEST: float = 0.55
    # 闲聊
    GENERAL_CHAT: float = 0.80
    # 兜底默认（所有信号为零时）
    DEFAULT_FALLBACK: float = 0.45


# ── 预算单位乘数 ─────────────────────────────────────────────────────────

BUDGET_UNIT_MULTIPLIERS: Dict[str, float] = {
    "亿": 100_000_000,
    "千万": 10_000_000,
    "百万": 1_000_000,
    "十万": 100_000,
    "w": 10_000,
    "万": 10_000,
    "k": 1_000,
    "千": 1_000,
    "百": 100,
}
```

**替换示例：**

```python
# Before:
scores["apply_cart_instruction"] += 0.75
scores["recommend_shopping_products"] += 0.55

# After:
scores["apply_cart_instruction"] += RouteScore.CART_INTENT
scores["recommend_shopping_products"] += RouteScore.CATEGORY_DETECTED

# Before (_parse_budget_amount):
_cn_multipliers = {
    "亿": 100_000_000,
    ...
}

# After:
# 直接使用模块级常量
if normalized in BUDGET_UNIT_MULTIPLIERS:
    return amount * BUDGET_UNIT_MULTIPLIERS[normalized]
```

#### 影响评估

| 维度 | 值 |
|------|-----|
| 消除 inline 数量 | ~15 个（11 个评分 + 7 个乘数 - 部分重叠） |
| 修改文件数 | 1（`tool_router.py`） |
| 行为变化 | 无 |
| 额外收益 | 评分体系一目了然，调参只需改一处 |

#### 风险

- **中低风险**：评分权重是路由质量的核心，改值需重跑 136 轮回归
- **注意事项**：`RouteScore` 类仅作为命名空间使用，不实例化

---

### Fix B2：评分函数常量提取

**优先级：** 3  
**目标文件：** `scorer.py`  
**消除 inline 数量：** ~70 个

#### 当前问题

`scorer.py` 中 7 个评分函数 + `build_dynamic_weights` + `apply_evidence_boost` 共含 ~50 个 inline float 和 ~20 个动态权重调整值。

**实际代码（评分函数 inline 常量）：**

```python
# score_scenario_match (line 396-435):
score = 0.25                                            # line 403 - 基础分
score += 0.35  # category match                         # line 405
score += 0.20  # sub_category match                     # line 409/411
score += 0.15  # brand match                            # line 415/417
score += min(term_hits * 0.08, 0.24)                    # line 422 - term match
score += 0.12  # occasion match                         # line 424
score += 0.08  # target_user match                      # line 426
score += 0.06  # scenario match                         # line 429

# score_attribute_match (line 438-469):
score = 0.45                                            # line 443 - 基础分
score += 0.25  # category match                         # line 445
score += 0.15  # sub_category match                     # line 448
score += 0.10  # brand match                            # line 452
score += min(hits / max(...) * 0.20, 0.20)              # line 462 - term match
score += 0.05  # has SKUs                               # line 464

# score_price_fit (line 472-487):
clamp(0.75 + ... * 0.25)                                # line 478
clamp(0.35 - ...)                                       # line 479
clamp(0.45 + ... * 0.35 + ... * 0.20)                   # line 486
clamp(0.5 + ... * 0.5)                                  # line 487

# score_reputation_fit (line 490-495):
return 0.55  # no rating                                # line 492
rating_score = product.rating_avg / 5                    # line 493
volume_bonus = min(product.review_count, 5) / 20         # line 494

# score_availability_fit (line 498-505):
return 0.95  # available                                # line 500
return 1.0   # in_stock                                 # line 502
return 0.65  # unknown                                  # line 504
return 0.25  # other                                    # line 505

# score_sku_fit (line 508-518):
score = 0.55                                            # line 509
score += 0.20  # has SKUs                               # line 511
score += 0.10  # has image                              # line 513
score += 0.10  # has price range                        # line 515
score += 0.05  # has stock                              # line 517

# score_detail_quality (line 521-529):
score = 0.35                                            # line 522
score += 0.20  # has description                        # line 524
score += min(len(product.faqs), 5) * 0.06               # line 526
score += min(len(product.reviews), 5) * 0.03            # line 528
```

**动态权重调整（`build_dynamic_weights` line 360-393）：**

```python
# 低预算场景:
weights["price_fit"] += 0.12       # line 364
weights["scenario_match"] -= 0.04  # line 365
weights["detail_quality"] -= 0.03  # line 366
weights["sku_fit"] -= 0.05         # line 367

# 高品质要求:
weights["attribute_match"] += 0.08 # line 370
weights["detail_quality"] += 0.04  # line 371
weights["price_fit"] -= 0.06       # line 372
weights["availability_fit"] -= 0.06# line 373

# 组合推荐:
weights["scenario_match"] += 0.04  # line 376
weights["attribute_match"] += 0.04 # line 377
weights["price_fit"] -= 0.04       # line 378
weights["reputation_fit"] -= 0.04  # line 379

# 对比需求:
weights["detail_quality"] += 0.05  # line 382
weights["attribute_match"] += 0.03 # line 383
weights["sku_fit"] -= 0.03         # line 384
weights["availability_fit"] -= 0.05# line 385

# 多模态:
weights["scenario_match"] += 0.03  # line 388
weights["detail_quality"] += 0.03  # line 389
weights["price_fit"] -= 0.03       # line 390
weights["reputation_fit"] -= 0.03  # line 391
```

**证据增强（`apply_evidence_boost` line 254-262）：**

```python
base_boost = min(best_hit, 1.0) * 0.07 + min(len(evidence), 3) / 3 * 0.05
boost = min(base_boost, 0.12)
if _has_strong_evidence_match(evidence, query):
    boost = min(base_boost, 0.16)
```

#### 解决方案

**在 `scorer.py` 顶部定义常量命名空间：**

```python
# ── 评分函数内联常量 ─────────────────────────────────────────────────────
# 按评分维度分组，修改任何值后需重跑 tests/test_scorer.py。

class ScenarioScore:
    """score_scenario_match 中各信号的加分值。"""
    BASE: float = 0.25
    CATEGORY_MATCH: float = 0.35
    SUB_CATEGORY_MATCH: float = 0.20
    BRAND_MATCH: float = 0.15
    TERM_HIT_PER: float = 0.08
    TERM_HIT_CAP: float = 0.24
    OCCASION_MATCH: float = 0.12
    TARGET_USER_MATCH: float = 0.08
    SCENARIO_MATCH: float = 0.06


class AttributeScore:
    """score_attribute_match 中各信号的加分值。"""
    BASE: float = 0.45
    CATEGORY_MATCH: float = 0.25
    SUB_CATEGORY_MATCH: float = 0.15
    BRAND_MATCH: float = 0.10
    TERM_MATCH_FACTOR: float = 0.20
    TERM_MATCH_CAP: float = 0.20
    HAS_SKUS: float = 0.05


class PriceScore:
    """score_price_fit 中的阈值和系数。"""
    WITHIN_BUDGET_BASE: float = 0.75
    WITHIN_BUDGET_FACTOR: float = 0.25
    OVER_BUDGET_BASE: float = 0.35
    OVER_BUDGET_DENOM_FACTOR: float = 3.0
    HIGH_BUDGET_QUALITY_FACTOR: float = 0.6
    HIGH_BUDGET_AFFORD_FACTOR: float = 0.20
    NO_BUDGET_BASE: float = 0.5
    NO_BUDGET_AFFORD_FACTOR: float = 0.5
    MEDIUM_BUDGET_CENTER: float = 0.55


class ReputationScore:
    """score_reputation_fit 中的参数。"""
    NO_RATING_BASE: float = 0.55
    MAX_RATING: float = 5.0
    VOLUME_CAP: int = 5
    VOLUME_DIVISOR: float = 20.0


class AvailabilityScore:
    """score_availability_fit 中的固定分值。"""
    AVAILABLE: float = 0.95
    IN_STOCK: float = 1.0
    UNKNOWN: float = 0.65
    OTHER: float = 0.25


class SkuScore:
    """score_sku_fit 中的加分值。"""
    BASE: float = 0.55
    HAS_SKUS: float = 0.20
    HAS_IMAGE: float = 0.10
    HAS_PRICE_RANGE: float = 0.10
    HAS_STOCK: float = 0.05


class DetailScore:
    """score_detail_quality 中的加分值。"""
    BASE: float = 0.35
    HAS_DESCRIPTION: float = 0.20
    FAQ_PER: float = 0.06
    FAQ_CAP: int = 5
    REVIEW_PER: float = 0.03
    REVIEW_CAP: int = 5


class EvidenceBoostParams:
    """apply_evidence_boost 中的参数。"""
    HIT_SCORE_FACTOR: float = 0.07
    COUNT_FACTOR: float = 0.05
    COUNT_CAP: int = 3
    NORMAL_CAP: float = 0.12
    STRONG_CAP: float = 0.16
    CROSS_CATEGORY_PENALTY: float = 0.10


class DynamicWeightAdjust:
    """build_dynamic_weights 中各场景的权重调整量。"""
    # 低预算/有价格上限
    LOW_BUDGET_PRICE_FIT: float = 0.12
    LOW_BUDGET_SCENARIO: float = -0.04
    LOW_BUDGET_DETAIL: float = -0.03
    LOW_BUDGET_SKU: float = -0.05
    # 高品质要求
    HIGH_QUALITY_ATTR: float = 0.08
    HIGH_QUALITY_DETAIL: float = 0.04
    HIGH_QUALITY_PRICE: float = -0.06
    HIGH_QUALITY_AVAIL: float = -0.06
    # 组合推荐
    BUNDLE_SCENARIO: float = 0.04
    BUNDLE_ATTR: float = 0.04
    BUNDLE_PRICE: float = -0.04
    BUNDLE_REPUTATION: float = -0.04
    # 对比需求
    COMPARE_DETAIL: float = 0.05
    COMPARE_ATTR: float = 0.03
    COMPARE_SKU: float = -0.03
    COMPARE_AVAIL: float = -0.05
    # 多模态
    MULTIMODAL_SCENARIO: float = 0.03
    MULTIMODAL_DETAIL: float = 0.03
    MULTIMODAL_PRICE: float = -0.03
    MULTIMODAL_REPUTATION: float = -0.03
```

**替换示例（`score_scenario_match`）：**

```python
# Before:
def score_scenario_match(requirement, product, evidence=None):
    score = 0.25
    if product.category in set(requirement.desired_categories or requirement.required_components):
        score += 0.35
    ...
    score += min(term_hits * 0.08, 0.24)

# After:
def score_scenario_match(requirement, product, evidence=None):
    S = ScenarioScore
    score = S.BASE
    if product.category in set(requirement.desired_categories or requirement.required_components):
        score += S.CATEGORY_MATCH
    ...
    score += min(term_hits * S.TERM_HIT_PER, S.TERM_HIT_CAP)
```

**替换示例（`build_dynamic_weights`）：**

```python
# Before:
if requirement.budget_level == BudgetLevel.low or requirement.price_max is not None:
    weights["price_fit"] += 0.12
    weights["scenario_match"] -= 0.04

# After:
D = DynamicWeightAdjust
if requirement.budget_level == BudgetLevel.low or requirement.price_max is not None:
    weights["price_fit"] += D.LOW_BUDGET_PRICE_FIT
    weights["scenario_match"] += D.LOW_BUDGET_SCENARIO  # 注意：值为 -0.04
```

#### 影响评估

| 维度 | 值 |
|------|-----|
| 消除 inline 数量 | ~70 个（50 个评分常量 + 20 个动态调整） |
| 修改文件数 | 1（`scorer.py`） |
| 行为变化 | 无（纯命名替换） |
| 额外收益 | 评分体系参数一览无余，便于 A/B 调参 |

#### 风险

- **中风险**：评分是推荐质量的核心，任何值的变更都需要全量回归
- **注意事项**：常量类仅作为命名空间使用；替换过程中需逐个函数核对，不可批量盲替

#### 实施步骤

1. 在 `scorer.py` 顶部定义 9 个常量类
2. 逐函数替换 inline float（7 个评分函数 + dynamic_weights + evidence_boost）
3. 每个函数替换后运行 `pytest tests/test_scorer.py -v`
4. 全量替换后运行 136 轮回归测试

---

### Fix B3：LLM Gateway 配置数据化

**优先级：** 6  
**目标文件：** `llm_gateway.py`  
**消除 inline 数量：** ~54 个（9 场景 x 6 参数）

#### 当前问题

`_register_defaults()` 中 9 行 `register()` 调用包含 54 个 inline 值，且无法从外部（如环境变量）覆盖单个场景的配置。

**实际代码（`llm_gateway.py` line 365-378）：**

```python
def _register_defaults() -> None:
    """Register the standard caller scenarios."""
    LLMGateway.register("router",       model_kind="fast", temperature=0,   timeout=15, max_tokens=320,  max_concurrency=5)
    LLMGateway.register("parse",        model_kind="fast", temperature=0.1, timeout=12, max_tokens=1200, max_concurrency=5)
    LLMGateway.register("guidance",     model_kind="main", temperature=0.2, timeout=8,  max_tokens=1500, max_concurrency=5)
    LLMGateway.register("response",     model_kind="main", temperature=0.9, timeout=5,  max_tokens=200,  max_concurrency=5)
    LLMGateway.register("explanation",  model_kind="main", temperature=0.1, timeout=8,  max_tokens=1500, max_concurrency=5)
    LLMGateway.register("rewrite",      model_kind="fast", temperature=0.1, timeout=8,  max_tokens=600,  max_concurrency=5)
    LLMGateway.register("general_chat", model_kind="main", temperature=0.7, timeout=8,  max_tokens=200,  max_concurrency=10)
    LLMGateway.register("filter",       model_kind="fast", temperature=0,   timeout=12, max_tokens=500,  max_concurrency=5)
    LLMGateway.register("attachment",   model_kind="main", temperature=0.1, timeout=15, max_tokens=800,  max_concurrency=3)
```

#### 解决方案

**将配置提取为声明式字典，并支持环境变量覆盖：**

```python
# ── 默认调用方配置表 ─────────────────────────────────────────────────────
# 每个场景的默认参数。可通过环境变量 LLM_CONFIG_{CALLER}_{PARAM} 覆盖。
# 例如: LLM_CONFIG_ROUTER_TIMEOUT=20 将 router 超时改为 20s。

_DEFAULT_CALLER_CONFIGS: Dict[str, Dict[str, Any]] = {
    "router":       {"model_kind": "fast", "temperature": 0.0, "timeout": 15.0, "max_tokens": 320,  "max_concurrency": 5},
    "parse":        {"model_kind": "fast", "temperature": 0.1, "timeout": 12.0, "max_tokens": 1200, "max_concurrency": 5},
    "guidance":     {"model_kind": "main", "temperature": 0.2, "timeout": 8.0,  "max_tokens": 1500, "max_concurrency": 5},
    "response":     {"model_kind": "main", "temperature": 0.9, "timeout": 5.0,  "max_tokens": 200,  "max_concurrency": 5},
    "explanation":  {"model_kind": "main", "temperature": 0.1, "timeout": 8.0,  "max_tokens": 1500, "max_concurrency": 5},
    "rewrite":      {"model_kind": "fast", "temperature": 0.1, "timeout": 8.0,  "max_tokens": 600,  "max_concurrency": 5},
    "general_chat": {"model_kind": "main", "temperature": 0.7, "timeout": 8.0,  "max_tokens": 200,  "max_concurrency": 10},
    "filter":       {"model_kind": "fast", "temperature": 0.0, "timeout": 12.0, "max_tokens": 500,  "max_concurrency": 5},
    "attachment":   {"model_kind": "main", "temperature": 0.1, "timeout": 15.0, "max_tokens": 800,  "max_concurrency": 3},
}


def _apply_env_overrides(configs: Dict[str, Dict[str, Any]]) -> None:
    """从环境变量 LLM_CONFIG_{CALLER}_{PARAM} 覆盖默认值。"""
    for caller, params in configs.items():
        prefix = f"LLM_CONFIG_{caller.upper()}_"
        for key in ("temperature", "timeout", "max_tokens", "max_concurrency", "model_kind"):
            env_key = f"{prefix}{key.upper()}"
            val = os.getenv(env_key)
            if val is None:
                continue
            if key in ("temperature", "timeout"):
                params[key] = float(val)
            elif key in ("max_tokens", "max_concurrency"):
                params[key] = int(val)
            else:
                params[key] = val


def _register_defaults() -> None:
    """Register the standard caller scenarios from declarative config."""
    configs = copy.deepcopy(_DEFAULT_CALLER_CONFIGS)
    _apply_env_overrides(configs)
    for name, params in configs.items():
        LLMGateway.register(name, **params)
```

#### 影响评估

| 维度 | 值 |
|------|-----|
| 消除 inline 数量 | 54 个 → 集中在 1 个字典 |
| 修改文件数 | 1（`llm_gateway.py`） |
| 行为变化 | 无（默认值完全一致） |
| 额外收益 | 支持环境变量覆盖，运维无需改代码 |

#### 风险

- **低风险**：默认值完全一致，且环境变量覆盖为可选功能
- **注意事项**：`_apply_env_overrides` 需在 `_register_defaults()` 中调用，确保模块加载时生效

---

### Fix B4：路由关键词列表统一

**优先级：** 8  
**目标文件：** `tool_router.py`  
**消除 inline 数量：** ~18 个函数内列表

#### 当前问题

`tool_router.py` 中有多处将关键词列表内联在函数体内（而非模块级常量），每次函数调用都重新创建列表对象。

**实际代码（函数内联列表）：**

```python
# is_pc_build_followup (line 1243-1247): 4 个内联列表
previous_plan_terms = ["上一套", "上套", "刚才那套", "之前那套", "这套", "现在这套", "方案", "配置", "整机", "装机", "主机"]
adjust_terms = ["换成", "换强", "换", "升级", "降到", "降低", "加到", "减到", "压到", "预算", "保留", "其他配件", "强一点", "更强", "便宜点",
                "不要", "只要", "改成", "不用", "要Intel", "要AMD", "要NVIDIA"]
pc_part_terms = ["显卡", "gpu", "cpu", "处理器", "主板", "内存", "硬盘", "ssd", "电源", "机箱", "散热", "风冷", "水冷"]
compare_terms = ["差别", "区别", "对比", "比较", "哪里不一样", "提升在哪"]

# _looks_like_pc_followup (line 1268):
zh_terms = ["便宜", "预算", "降到", "强一点", "更强", "升级", "换", "白色", "黑色", "对比", "保留显卡", "瓶颈", "功耗", "升级路径", "为什么", "显示器"]

# _looks_like_single_pc_part_query (line 1231):
single_terms = ["推荐", "买", "一款", "一个", "看看", "显卡", "cpu", "CPU", "主板", "内存", "ssd", "SSD", "电源", "机箱", "散热"]

# _is_general_chat (line 1347-1361): 内联合并多个列表
shopping_signals = [
    *NORMAL_PRODUCT_TERMS.keys(),
    *NORMAL_PRODUCT_ALIASES.keys(),
    *BRAND_OR_PRODUCT_TERMS,
    *SEARCH_INTENT_TERMS,
    *SCENARIO_SHOPPING_TERMS,
    *FACT_QUERY_TERMS,
    *PC_STRONG_TERMS,
    *CART_STRONG_TERMS,
    *COMPARE_TERMS,
    "推荐", "买", "预算", "价格",
]

# _looks_like_short_preference_followup (line 1445):
terms = ["适合", "女生", "男朋友", "女朋友", "通勤", "学生党", "续航", "轻一点", "便携", "安静", "降噪", "白色", "黑色", "耐用", "送礼", "便宜", "贵", "预算", "降", "加", "换", "改成", "更强", "升级"]

# extract_usage (line 1117):
for term in ["游戏", "办公", "视频", "剪辑", "直播", "AI", "训练", "黑神话", "3A", "深度学习", "CUDA", "大模型", "显存", "多开", "模拟器", "修图", "Lightroom", "Photoshop", "音乐制作", "编曲", "开发", "Docker", "IDE", "虚拟机", "网游", "电竞", "LOL", "瓦罗兰特", "CS2", "2K", "4K", "光追"]:
```

#### 解决方案

**将所有函数内联列表提取为模块级 `frozenset`/`tuple` 常量：**

```python
# ── is_pc_build_followup 使用的关键词 ────────────────────────────────────

PC_BUILD_PREVIOUS_PLAN_TERMS: frozenset = frozenset({
    "上一套", "上套", "刚才那套", "之前那套", "这套", "现在这套",
    "方案", "配置", "整机", "装机", "主机",
})

PC_BUILD_ADJUST_TERMS: frozenset = frozenset({
    "换成", "换强", "换", "升级", "降到", "降低", "加到", "减到", "压到",
    "预算", "保留", "其他配件", "强一点", "更强", "便宜点",
    "不要", "只要", "改成", "不用", "要Intel", "要AMD", "要NVIDIA",
})

PC_BUILD_PART_TERMS: frozenset = frozenset({
    "显卡", "gpu", "cpu", "处理器", "主板", "内存", "硬盘", "ssd",
    "电源", "机箱", "散热", "风冷", "水冷",
})

PC_BUILD_COMPARE_TERMS: frozenset = frozenset({
    "差别", "区别", "对比", "比较", "哪里不一样", "提升在哪",
})

# ── PC followup 补充词 ──────────────────────────────────────────────────

PC_FOLLOWUP_ZH_TERMS: frozenset = frozenset({
    "便宜", "预算", "降到", "强一点", "更强", "升级", "换", "白色", "黑色",
    "对比", "保留显卡", "瓶颈", "功耗", "升级路径", "为什么", "显示器",
})

# ── 单 PC 配件查询辅助词 ────────────────────────────────────────────────

SINGLE_PC_PART_QUERY_TERMS: frozenset = frozenset({
    "推荐", "买", "一款", "一个", "看看", "显卡",
    "cpu", "CPU", "主板", "内存", "ssd", "SSD", "电源", "机箱", "散热",
})

# ── 短偏好追问词 ────────────────────────────────────────────────────────

SHORT_PREFERENCE_FOLLOWUP_TERMS: frozenset = frozenset({
    "适合", "女生", "男朋友", "女朋友", "通勤", "学生党", "续航", "轻一点",
    "便携", "安静", "降噪", "白色", "黑色", "耐用", "送礼",
    "便宜", "贵", "预算", "降", "加", "换", "改成", "更强", "升级",
})

# ── 使用场景提取词 ──────────────────────────────────────────────────────

USAGE_TERMS: tuple = (
    "游戏", "办公", "视频", "剪辑", "直播", "AI", "训练", "黑神话", "3A",
    "深度学习", "CUDA", "大模型", "显存", "多开", "模拟器", "修图",
    "Lightroom", "Photoshop", "音乐制作", "编曲", "开发", "Docker", "IDE",
    "虚拟机", "网游", "电竞", "LOL", "瓦罗兰特", "CS2", "2K", "4K", "光追",
)
```

**替换示例：**

```python
# Before (line 1243):
previous_plan_terms = ["上一套", "上套", "刚才那套", ...]
has_previous_reference = any(term in text for term in previous_plan_terms)

# After:
has_previous_reference = any(term in text for term in PC_BUILD_PREVIOUS_PLAN_TERMS)

# Before (line 1117):
for term in ["游戏", "办公", "视频", ...]:
    if term in text:
        usage.append(term)

# After:
for term in USAGE_TERMS:
    if term in text:
        usage.append(term)
```

#### 影响评估

| 维度 | 值 |
|------|-----|
| 消除 inline 数量 | ~18 处函数内列表 |
| 修改文件数 | 1（`tool_router.py`） |
| 行为变化 | 无 |
| 性能提升 | `frozenset` 查找 O(1) 替代列表 O(n)；且避免每次调用重建列表 |

#### 风险

- **低风险**：纯提取，不改变匹配逻辑
- **注意事项**：使用 `frozenset` 而非 `list` 时需注意——对于 `any(term in text ...)` 模式，遍历顺序不影响结果

---

## Phase C：打磨修复（较低优先级）

> 特点：功能增强型修复，非纯重构，需要设计新的逻辑。

---

### Fix C1：extract_item_index 泛化

**优先级：** 9  
**目标文件：** `session_state.py`  
**消除 inline 数量：** 6 个（6 个硬编码正则模式）

#### 当前问题

`extract_item_index()` 仅硬编码支持 1-3，不支持 4 及以上。用户说"第四个"时返回 None。

**实际代码（`session_state.py` line 1071-1084）：**

```python
def extract_item_index(instruction: str) -> Optional[int]:
    text = instruction or ""
    patterns = [
        (r"(?:第\s*)?1\s*(?:个|款|号)", 0),
        (r"(?:第\s*)?2\s*(?:个|款|号)", 1),
        (r"(?:第\s*)?3\s*(?:个|款|号)", 2),
        (r"第一\s*(?:个|款|号)?", 0),
        (r"第二\s*(?:个|款|号)?", 1),
        (r"第三\s*(?:个|款|号)?", 2),
    ]
    for pattern, index in patterns:
        if re.search(pattern, text):
            return index
    return None
```

#### 解决方案

**改为通用数字解析 + 中文数字映射：**

```python
# ── 中文数字映射 ─────────────────────────────────────────────────────────

_CN_NUM_MAP: Dict[str, int] = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "两": 2,  # "两个""两款"
}

# 序数提取正则（支持阿拉伯数字 + 中文数字）
_ITEM_INDEX_ARABIC = re.compile(r"(?:第\s*)?(\d+)\s*(?:个|款|号)")
_ITEM_INDEX_CN = re.compile(r"第\s*([一二三四五六七八九十两])\s*(?:个|款|号)?")


def extract_item_index(instruction: str) -> Optional[int]:
    """从用户指令中提取商品序数索引（0-based）。

    支持：
    - 阿拉伯数字：'第1个' / '1号' / '3款' → 0/0/2
    - 中文数字：'第一个' / '第三款' / '二号' → 0/2/1
    - 无上限限制（旧版仅支持 1-3）
    """
    text = instruction or ""

    # 优先匹配阿拉伯数字
    match = _ITEM_INDEX_ARABIC.search(text)
    if match:
        num = int(match.group(1))
        return num - 1 if num >= 1 else None

    # 其次匹配中文数字
    match = _ITEM_INDEX_CN.search(text)
    if match:
        cn = match.group(1)
        num = _CN_NUM_MAP.get(cn)
        return num - 1 if num and num >= 1 else None

    return None
```

#### 影响评估

| 维度 | 值 |
|------|-----|
| 消除 inline 数量 | 6 个正则模式 → 2 个通用正则 + 1 个映射表 |
| 修改文件数 | 1（`session_state.py`） |
| 行为变化 | **扩展**：支持 4+ 的序数（"第四个""10号"等） |
| 向下兼容 | 1-3 的行为完全一致 |

#### 风险

- **中低风险**：扩展了覆盖范围，但 1-3 的行为不变
- **注意事项**：需添加测试覆盖中文数字和阿拉伯数字的各种边界（"第十个"、"第100个"等）
- **回归测试**：需确认现有购物车操作测试用例不受影响

---

### Fix C2：中文 UI 文案 i18n 模块

**优先级：** 10  
**目标文件：** `tool_handlers.py`（主）、`chat.py`（辅）、`response_generator.py`（辅）  
**消除 inline 数量：** ~35 个中文文案字符串

#### 当前问题

中文 UI 文案散布在多个文件中，无法统一修改或支持多语言。

**实际代码散布示例：**

```python
# tool_handlers.py line 47:
"购物车已更新。"
# tool_handlers.py line 145:
f"确认从购物车移除 {title}？"
# tool_handlers.py line 147:
f"确认将 {title} 的数量修改为 {qty}？"
# tool_handlers.py line 150:
f"确认将 {title} x{qty} 加入购物车{price_hint}？"
# tool_handlers.py line 230:
"商品不存在"
# tool_handlers.py line 234:
"未找到商品"
# tool_handlers.py line 263:
"购物车是空的，没有可操作的商品。"
# tool_handlers.py line 283:
"没找到要操作的商品。你可以说商品名称或'第几个'来指定。"
# tool_handlers.py line 316:
f"已清空购物车（移除了 {count} 件商品）。"
# tool_handlers.py line 396-399:
"我是智能导购助手，主要帮你挑选商品、做商品对比和处理购物车。..."
# tool_handlers.py line 407:
"不客气！有需要随时找我..."
# tool_handlers.py line 409:
"再见！购物有需要随时来找我。"
# tool_handlers.py line 487:
"商品库里暂时没有找到你要对比的具体型号，帮你搜了同类商品。"
# tool_handlers.py line 509:
"对比失败"
# tool_handlers.py line 567:
"方案不足"
# tool_handlers.py line 639:
"你想了解哪款商品的参数？可以告诉我具体型号。"
# tool_handlers.py line 683:
"你想了解哪款商品的配置差异？可以告诉我具体型号。"
# tool_handlers.py line 729:
"你想比价哪款商品？可以告诉我具体型号。"
# tool_handlers.py line 796:
"识别到电脑整机/装机方案需求，进入独立 PC 配置规划链路。"

# chat.py line 177:
"已收到需求"
"开始整理预算、品类、颜色和功能约束。"
# chat.py line 192:
"图片解析完成"
# chat.py line 199:
"正在解析条件"
"大模型会参与需求理解。"
"当前使用规则解析需求。"

# tool_handlers.py line 1033:
"我先按你的需求筛一遍商品库，优先找最相关的真实商品。"

# tool_handlers.py line 1128:
"商品库扫描完成"
# tool_handlers.py line 1133:
"RAG 证据检索完成"
# tool_handlers.py line 1135:
"结构化筛选启动"
# tool_handlers.py line 1153:
"命中候选"
# tool_handlers.py line 1156:
"候选卡片已准备"
# tool_handlers.py line 1157:
"正在生成导购回答"
"正在整理推荐理由和追问。"
```

#### 解决方案

**新建文件 `rag/i18n/zh_cn.py`：**

```python
"""中文 UI 文案集中管理。

所有面向用户的中文文案统一在此定义，便于：
1. 全局搜索和修改
2. 未来国际化 (i18n) 支持
3. 文案一致性审查
"""


# ── 购物车相关 ─────────────────────────────────────────────────────────────

class CartTexts:
    UPDATED = "购物车已更新。"
    EMPTY = "购物车是空的，没有可操作的商品。"
    CLEARED = "已清空购物车（移除了 {count} 件商品）。"
    NO_PRODUCT_FOUND = "没有找到可操作的商品，请先推荐商品或指定 product_id。"
    NO_OPERABLE = "没找到要操作的商品。你可以说商品名称或'第几个'来指定。"

    # 确认文案模板
    CONFIRM_REMOVE = "确认从购物车移除 {title}？"
    CONFIRM_SET_QTY = "确认将 {title} 的数量修改为 {qty}？"
    CONFIRM_ADD = "确认将 {title} x{qty} 加入购物车{price_hint}？"

    # 歧义追问
    AMBIGUITY_INDEX_OOB = "购物车里只有 {count} 个商品，没有第 {index} 个。当前有：{names}，你要操作哪一个？"
    AMBIGUITY_SAME_CATEGORY = "购物车里有多个{category}商品：{names}，你要操作哪一个？可以说名称或'第几个'。"


# ── 错误/校验相关 ──────────────────────────────────────────────────────────

class ErrorTexts:
    PRODUCT_NOT_EXIST_LABEL = "商品不存在"
    PRODUCT_NOT_EXIST_DETAIL = "product_id {product_id} 不在商品库中。"
    NO_ADDABLE_PRODUCT_LABEL = "未找到商品"
    NO_ADDABLE_PRODUCT_DETAIL = "没有找到可加入购物车的商品，请先推荐商品。"
    COMPARE_FAILED_LABEL = "对比失败"
    COMPARE_FAILED_DETAIL = "未能找到可对比的商品，请尝试指定具体型号。"
    COMPARE_NO_MODEL = "商品库里暂时没有找到你要对比的具体型号，帮你搜了同类商品。"
    PC_PLAN_INSUFFICIENT_LABEL = "方案不足"
    PC_PLAN_INSUFFICIENT_DETAIL = "需要至少两个装机方案才能对比。"
    PC_BUILD_LABEL = "PC 方案无法生成"
    RECOMMEND_ERROR_LABEL = "推荐异常"
    VALIDATION_NO_MATCH_LABEL = "需求无法识别"
    PRODUCT_NOT_FOUND_LABEL = "商品不存在"
    PRODUCT_NOT_FOUND_DETAIL = "所有待对比商品均未在商品库中找到。"


# ── 查询引导文案 ───────────────────────────────────────────────────────────

class QueryGuideTexts:
    PARAM_QUERY_PROMPT = "你想了解哪款商品的参数？可以告诉我具体型号。"
    SKU_QUERY_PROMPT = "你想了解哪款商品的配置差异？可以告诉我具体型号。"
    PRICE_CMP_PROMPT = "你想比价哪款商品？可以告诉我具体型号。"


# ── 闲聊/兜底文案 ─────────────────────────────────────────────────────────

class GeneralChatTexts:
    OFF_TOPIC = (
        "我是智能导购助手，主要帮你挑选商品、做商品对比和处理购物车。"
        "这个问题和购物无关，我就不展开了；如果你有购物需求，可以告诉我品类、预算和偏好。"
    )
    INVALID_INPUT = (
        "我是智能导购助手。请告诉我你想买什么商品、预算多少、有什么偏好，"
        "我可以帮你搜索、推荐、对比，或加入购物车。"
    )
    THANKS = "不客气！有需要随时找我，我可以帮你搜商品、做对比、处理购物车。"
    GOODBYE = "再见！购物有需要随时来找我。"
    DEFAULT_GREETING = (
        "你好，我是智能导购助手，可以帮你搜索商品、推荐合适款式、对比商品、"
        "生成整机方案，也可以处理购物车。请告诉我你想买什么。"
    )


# ── 进度/状态文案 ─────────────────────────────────────────────────────────

class ProgressTexts:
    RECEIVED_LABEL = "已收到需求"
    RECEIVED_DETAIL = "开始整理预算、品类、颜色和功能约束。"
    IMAGE_PARSED_LABEL = "图片解析完成"
    PARSING_LABEL = "正在解析条件"
    PARSING_DETAIL_LLM = "大模型会参与需求理解。"
    PARSING_DETAIL_RULE = "当前使用规则解析需求。"
    CATALOG_SCAN_LABEL = "商品库扫描完成"
    CATALOG_SCAN_DETAIL = "共读取 {total} 条本地真实商品数据。"
    RAG_RETRIEVAL_LABEL = "RAG 证据检索完成"
    RAG_RETRIEVAL_DETAIL = "检索到 {hits} 条证据，命中 {matched} 个商品。"
    STRUCTURED_FILTER_LABEL = "结构化筛选启动"
    STRUCTURED_FILTER_DETAIL = "当前使用本地商品属性、SKU、价格和评价进行评分。"
    CATEGORY_FILTER_DONE = "{category}筛选完成"
    CATEGORY_FILTER_DETAIL = "原始 {raw} 条，排除后 {after} 条，预算内命中 {budget} 条。"
    HIT_CANDIDATE = "命中候选 {index}"
    CARDS_READY_LABEL = "候选卡片已准备"
    CARDS_READY_DETAIL = "将展示 {count} 张商品卡片，并保留可对比候选。"
    GENERATING_LABEL = "正在生成导购回答"
    GENERATING_DETAIL = "正在整理推荐理由和追问。"


# ── 路由/系统文案 ─────────────────────────────────────────────────────────

class SystemTexts:
    CHAT_OPENING = "我先按你的需求筛一遍商品库，优先找最相关的真实商品。"
    PC_BUILD_REASON = "识别到电脑整机/装机方案需求，进入独立 PC 配置规划链路。"
    SYSTEM_PROMPT_PREFIX = "系统已开始检索"
    SYSTEM_PROMPT_DETAIL = "正在连接本地商品库并准备结构化筛选。"
```

**替换示例：**

```python
# Before (tool_handlers.py line 47):
yield sse_event("delta", {"text": "购物车已更新。"})

# After:
from rag.i18n.zh_cn import CartTexts
yield sse_event(SSEEvent.DELTA, {"text": CartTexts.UPDATED})

# Before (tool_handlers.py line 145):
return f"确认从购物车移除 {title}？"

# After:
return CartTexts.CONFIRM_REMOVE.format(title=title)
```

#### 影响评估

| 维度 | 值 |
|------|-----|
| 消除 inline 数量 | ~35 个中文文案字符串 |
| 修改文件数 | 4（新建 1 + 修改 3） |
| 行为变化 | 无 |
| 额外收益 | 为未来 i18n 多语言支持打下基础 |

#### 风险

- **低风险**：纯文案提取，不改变逻辑
- **注意事项**：f-string 中的变量插值需改为 `.format()` 调用；进度文案的 `build_chat_progress_events` 函数中有多处使用 f-string 构建动态文案，需仔细对应

#### 实施步骤

1. 新建 `rag/i18n/__init__.py` 和 `rag/i18n/zh_cn.py`
2. 按类别定义文案常量类
3. 逐文件替换：先 `tool_handlers.py`（最多），再 `chat.py`，最后 `response_generator.py`
4. 回归测试确认所有文案显示正确

---

## 风险矩阵总表

| Fix | 影响文件数 | 消除 inline | 行为变化风险 | 回归测试需求 | 总体风险 |
|-----|-----------|-------------|-------------|-------------|----------|
| A1 SSE Event Enum | 3 | ~50 | 无 | 轻量 | **低** |
| A2 Tool Name Enum | 4 | ~30 | 无 | 轻量 | **低** |
| A3 Cart Op Enum | 3 | ~15 | 无 | 轻量 | **低** |
| A4 Session Limits | 2 | ~7 | 无 | 轻量 | **极低** |
| B1 Route Scores | 1 | ~15 | 无（值不变） | 中量（136轮） | **中低** |
| B2 Scorer Constants | 1 | ~70 | 无（值不变） | 重量（评分相关） | **中** |
| B3 Gateway Config | 1 | ~54 | 新增 env 覆盖 | 轻量 | **低** |
| B4 Router Keywords | 1 | ~18 | 无 | 中量（136轮） | **低** |
| C1 Index 泛化 | 1 | ~6 | **扩展行为** | 中量（购物车） | **中低** |
| C2 i18n 文案 | 4 | ~35 | 无 | 轻量 | **低** |

---

## 测试策略

### 每个 Fix 必须通过的测试门禁

```
1. pytest tests/ -x -q                              # 全量单元测试通过
2. pytest tests/test_tool_router.py -v               # 路由回归（如修改了 tool_router.py）
3. pytest tests/test_scorer.py -v                     # 评分回归（如修改了 scorer.py）
4. pytest tests/test_session_state_store.py -v        # Session 测试（如修改了 session_state.py）
5. python tests/run_mallmind_full_enhanced.py          # 136 轮全链路回归（Phase B/C 必须）
```

### 新增测试

| Fix | 新增测试文件 | 测试内容 |
|-----|-------------|----------|
| A1 | `test_sse_event_enum.py` | Enum 值覆盖所有 25 种事件类型 |
| A2 | `test_tool_names.py` | ToolName 覆盖所有 8 个工具；分组正确 |
| A3 | `test_cart_operation.py` | CartOperation.from_text 覆盖所有关键词 |
| A4 | `test_session_limits.py` | SessionLimits 各窗口边界条件 |
| B1 | （已有 `test_tool_router.py`） | 追加评分权重可配置性断言 |
| B2 | （已有评分测试） | 追加常量类存在性断言 |
| B3 | `test_llm_gateway_config.py` | env 覆盖生效；默认值正确 |
| B4 | （已有 `test_tool_router.py`） | 追加 frozenset 不可变性断言 |
| C1 | `test_extract_item_index.py` | 阿拉伯/中文数字；1-10+；边界值 |
| C2 | `test_i18n_texts.py` | 所有文案非空；format 模板可渲染 |

---

## 附录：已提取命名常量参考

以下常量已在代码中正确提取为命名常量，本次修复**不涉及**这些：

| 常量名 | 值 | 文件:行号 | 用途 |
|--------|-----|-----------|------|
| `MAX_MESSAGE_LENGTH` | 2000 | `chat.py:44` | 消息长度上限 |
| `_MAX_LOG_ENTRIES` | 20 | `chat.py:247` | LLM 调用日志窗口（A4 会统一到 SessionLimits） |
| `_CONFIRM_TTL_SECONDS` | 60 | `chat.py:283` | 购物车确认 TTL（定义但未引用——死代码） |
| `_MAX_PRICE` | 500000 | `tool_router.py:732` | 价格上限 |
| `_MIN_SANE_PRICE` | 50 | `tool_router.py:733` | 最低合理价格 |
| `_MAX_BRANDS` | 50 | `tool_router.py:734` | 品牌列表截断 |
| `_CART_CONFIRM_TTL_SECONDS` | 60 | `tool_handlers.py:54` | 购物车确认过期 |
| `DEFAULT_SESSION_TTL_SECONDS` | 7200 | `session_state.py:19` | Session 过期 |
| `DEFAULT_MAX_IN_MEMORY_SESSIONS` | 500 | `session_state.py:20` | 内存 session 上限 |
| `SESSION_CLEANUP_INTERVAL_SECONDS` | 60 | `session_state.py:21` | 清理间隔 |
| `SCHEMA_VERSION` | 2 | `session_state.py:23` | 版本号 |
| `BASE_WEIGHTS` | 7维权重 | `scorer.py:13-21` | 评分基线权重 |
| `_PRICE_DEVIATION_THRESHOLD` | 0.30 | `recommendation_pipeline.py:1240` | 价格偏差阈值 |
| `_FACT_FAILURE_THRESHOLD` | 0.50 | `recommendation_pipeline.py:1241` | 事实校验失败率 |
| `_FAILURE_THRESHOLD` | 5 | `llm_gateway.py:80` | 熔断失败次数 |
| `_OPEN_DURATION_SECONDS` | 30.0 | `llm_gateway.py:81` | 熔断断路时长 |
| `_MAX_LOG` | 100 | `llm_gateway.py:131` | Gateway 日志容量 |

### 已知的死代码

| 位置 | 问题 | 建议 |
|------|------|------|
| `chat.py:283` | `_CONFIRM_TTL_SECONDS = 60` 已定义但从未引用 | 删除或替换为引用 `_CART_CONFIRM_TTL_SECONDS` |

---

*文档完。所有修复项均可独立实施，建议按 Phase A → B → C 顺序推进。*
