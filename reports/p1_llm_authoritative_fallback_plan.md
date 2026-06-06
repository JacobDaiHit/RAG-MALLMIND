## P1 修改方案：LLM 权威化兜底架构

### 设计原则

当前路由架构是三层防线：local 规则 → LLM 路由 → Guard 兜底。问题不在 LLM 的判断能力（Router LLM 条件成功率 91.2%），而在于 Guard 层用关键词负匹配推翻了 LLM 的正确判断。本次修改的核心原则：

1. **LLM 路由决策应当是权威的** —— Guard 层只做"硬正向修正"（购物车、PC build 追问等确定性强且 LLM 不可能知道的场景），不做"负向覆盖"。
2. **不增加任何正则或关键词匹配来引导路由** —— 所有路由修正靠架构逻辑而非词表。
3. **管线下游不应二次拦截路由层已确认的购物请求** —— `validate_business_goal()` 的关键词门禁与路由层功能重复且更弱。

---

### 改动 1（P0）：route_shopping_tool_call 显式标记 LLM 选择结果

**文件**：`rag/recommendation/tool_router.py`，函数 `route_shopping_tool_call()`

**问题**：line 672 的 `chosen = llm_call if ... else local` 选出 LLM 结果后，没有打标记。下游 Guard 层通过 `call.get("source")` 来判断是否来自 LLM，但 `source` 字段的值取决于 LLM 输出 JSON 中是否包含 `"source": "llm"` —— 这是一个不可靠的外部依赖。

**修改方案**：在 line 672 之后，如果 chosen 来自 LLM（即 `chosen is llm_call`），显式注入标记字段：

```python
chosen = llm_call if llm_call and float(llm_call.get("confidence") or 0) >= 0.50 else local
# ── 显式标记 LLM 选择结果，供 Guard 层识别 ──
if chosen is llm_call and chosen is not None:
    chosen = dict(chosen)  # 避免修改原始 dict
    chosen["_llm_chosen"] = True
```

同时在 routing_trace 中也加入这个标记，便于后续调试。

**影响 case**：为改动 2 提供可靠的判断依据。

---

### 改动 2（P0）：Guard 层取消负向覆盖，只保留硬正向修正

**文件**：`rag/recommendation/tool_router.py`，函数 `validate_and_guard_tool_call()`

**问题**：当前 Guard 层（line 992-1035）有 10 个 elif 分支。前 5 个是"硬正向修正"（购物车、PC build 追问、对比、PC 追问、PC 强意图），后 5 个是"软正向/负向覆盖"（followup、品类检测、搜索意图、general_chat）。其中两个负向分支直接导致了错误：

- `_looks_like_compare_request(text)`（line 1003-1006）：COMPARE_TERMS 中的 "哪个更" 匹配了 "防晒哪个更不油"，把 LLM 正确的 `recommend_shopping_products` 覆盖成 `compare_products`。
- `_is_general_chat(text, lowered, topic)`（line 1026-1035）：虽然有 `_llm_routed_to_product` 保护，但因为 source 字段不可靠（见改动 1），保护未生效，把 LLM 正确的 `recommend_shopping_products` 覆盖成 `general_chat`。

**修改方案**：

**2a. 删除 `_looks_like_compare_request` Guard 分支**

删除 line 1003-1006 的整个 elif 块。理由：`local_route_tool_call()` line 852 已经对真正的对比请求做了路由（`compare_products`），LLM 也能正确区分对比 vs 属性偏好。Guard 层的重复检测只会产生假阳性。

```
# 删除以下分支：
elif _looks_like_compare_request(text):
    arguments["compare_with_previous"] = _mentions_previous(text)
    guarded = _tool_call("compare_products", ...)
    call.update(guarded)
```

如果担心完全删除风险太大，可以改为**条件跳过**：当 chosen 来自 LLM 时不执行 compare guard。

```python
elif _looks_like_compare_request(text) and not call.get("_llm_chosen"):
    # 仅在 LLM 未参与或未选择时，用规则做对比兜底
    arguments["compare_with_previous"] = _mentions_previous(text)
    guarded = _tool_call("compare_products", arguments, max(call["confidence"], 0.88), "后端兜底：明确对比请求走 compare_products。", "guard")
    call.update(guarded)
```

**2b. 修复 `_is_general_chat` Guard 分支的判断依据**

将 line 1030-1034 的 `_llm_routed_to_product` 判断从依赖 `source` 字段改为依赖改动 1 注入的 `_llm_chosen` 标记：

```python
elif _is_general_chat(text, lowered, topic):
    # 改动：用 _llm_chosen 标记代替 source 字段判断
    if not call.get("_llm_chosen"):
        call.update(_tool_call("general_chat", arguments, max(call["confidence"], 0.9),
                               "后端兜底：非购物执行问题走 general_chat。", "guard"))
```

含义：如果路由层已经通过 LLM 选择了购物工具（`_llm_chosen=True`），Guard 层不再用 `_is_general_chat` 的负匹配把它覆盖回 `general_chat`。

**影响 case**：
- `cap_gift_parents_digital`（"送父母一个实用数码产品"）：LLM 正确路由 → Guard 不再覆盖 → 修复
- `cap_rag_sunscreen_less_oily`（"防晒哪个更不油"）：LLM 正确路由 → compare guard 不再覆盖 → 修复

---

### 改动 3（P0）：validate_business_goal 信任路由层决策

**文件**：`rag/recommendation/recommendation_pipeline.py`，函数 `validate_business_goal()`（line 654-666）

**问题**：`validate_business_goal()` 用 `SHOPPING_GOAL_KEYWORDS` 列表（line 27-109）做关键词门禁。"开学需要学习和宿舍用的东西"中的"开学""需要""东西"不在列表中，"开始跑步训练，帮我配一套入门装备"中的"跑步训练""装备""入门"不在列表中。但路由层（local + LLM）已经正确判断这些请求应走 `recommend_shopping_products`。

这是典型的**下游二次拦截**：路由层做了正确的购物意图判断，管线入口又用一个更弱的关键词列表做了反向验证，把正确的请求拦截掉了。

**修改方案（选一种）**：

**方案 A（推荐）：给 validate_business_goal 加 bypass 参数**

```python
def validate_business_goal(user_goal: str, *, skip_keyword_check: bool = False) -> None:
    normalized = user_goal.strip()
    lower = normalized.lower()
    if len(normalized) < 2:
        raise InvalidGoalError("请输入更完整的购物需求，例如类目、预算、用途或偏好。")
    meaningful_chars = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", normalized)
    if len(meaningful_chars) < 2:
        raise InvalidGoalError("输入内容过短或缺少可识别信息，请补充购物需求。")
    symbol_count = sum(1 for char in normalized if not re.match(r"[\u4e00-\u9fffA-Za-z0-9\s]", char))
    if symbol_count / max(len(normalized), 1) > 0.35:
        raise InvalidGoalError("输入中符号比例过高，请输入自然语言购物需求。")
    # 改动：如果路由层已确认购物意图，跳过关键词门禁
    if not skip_keyword_check:
        if not has_any(lower, SHOPPING_GOAL_KEYWORDS):
            raise InvalidGoalError("未识别到有效购物场景，请描述想买什么、预算、用途或偏好。")
```

调用方（`recommend_shopping_products()` 和 handler 层）在路由来源是 `recommend_shopping_products` 工具时传入 `skip_keyword_check=True`。

**方案 B（最小改动）：在 handler 层 catch InvalidGoalError 并降级处理**

在 `tool_handlers.py` 的 `recommend_shopping_products` handler 中，如果 `InvalidGoalError` 被抛出，不返回错误，而是返回一个带引导回复的空推荐结果。这不需要修改 `validate_business_goal` 本身，但需要修改 handler。

**影响 case**：
- `cap_bundle_school_start`（"开学需要学习和宿舍用的东西"）：不再被 InvalidGoalError 拦截 → 修复
- `cap_bundle_beginner_running`（"开始跑步训练，帮我配一套入门装备"）：同上 → 修复

---

### 改动 4（P1）：Router LLM 系统 prompt 增强

**文件**：`rag/recommendation/tool_router.py`，函数 `try_llm_route_tool_call()`（line 896-901）

**当前 prompt**：
```
你是电商导购系统的工具路由器，只输出 JSON。
后端会校验并执行工具，你只负责选择工具和抽取参数。
不要编造商品、价格、库存或优惠。
```

**问题**：prompt 过于简短，缺少对边界场景的指导。导致：
- "处方药怎么买" → LLM 认为处方药不是常规电商商品，路由到 general_chat（但测试期望 recommend_shopping_products）
- "防晒哪个更不油" → LLM 可能不确定这是推荐还是对比

**修改方案**：在 system prompt 中增加场景指导（不增加关键词匹配，只增加判断原则）：

```
你是电商导购系统的工具路由器，只输出 JSON。
后端会校验并执行工具，你只负责选择工具和抽取参数。
不要编造商品、价格、库存或优惠。

路由原则：
- 只要用户在询问、寻找、评价任何商品（包括药品、保健品等非典型品类），一律使用 recommend_shopping_products。
- 用户要求"配一套""一起推荐""开学要用的东西"等多商品或场景化请求，使用 recommend_shopping_products。
- "哪个更不油""哪个更轻""哪款更适合"等表达是属性偏好筛选，不是商品对比，使用 recommend_shopping_products。
- 只有用户明确提到两个具体商品名并要求比较时才使用 compare_products。
- general_chat 仅用于"你是谁""怎么用""推荐逻辑是什么"等系统元问题。
- 输出 JSON 中必须包含 "source": "llm"。
```

**影响 case**：
- `cap_negative_medicine`（"处方药怎么买"）：prompt 明确"药品也算商品" → LLM 更可能路由到 recommend_shopping_products → 修复
- `cap_rag_sunscreen_less_oily`（双重保险）：prompt 明确"哪个更不油是属性偏好" → LLM 判断更稳定

---

### 改动 5（P1）：route_shopping_tool_call 非对称置信度阈值

**文件**：`rag/recommendation/tool_router.py`，函数 `route_shopping_tool_call()`，line 672

**当前逻辑**：
```python
chosen = llm_call if llm_call and float(llm_call.get("confidence") or 0) >= 0.50 else local
```

LLM confidence >= 0.50 就选 LLM 结果，无论方向。

**问题**：LLM 把 local 的正确 `recommend_shopping_products` 降级为 `general_chat`（如 cap_negative_medicine）时，0.50 的门槛太低。LLM 在边界 case 上容易过度谨慎。

**修改方案**：非对称阈值 —— LLM 升级到购物工具时低门槛，LLM 降级到 general_chat 时高门槛：

```python
_llm_conf = float((llm_call or {}).get("confidence") or 0)
_llm_name = (llm_call or {}).get("name") or ""
_local_name = local.get("name") or ""

if llm_call is None:
    chosen = local
elif _llm_name == "general_chat" and _local_name != "general_chat":
    # LLM 要把非 general_chat 降级为 general_chat → 需要高置信度
    chosen = llm_call if _llm_conf >= 0.80 else local
else:
    # LLM 升级到购物工具 → 保持低门槛
    chosen = llm_call if _llm_conf >= 0.50 else local
```

**影响 case**：
- `cap_negative_medicine`（"处方药怎么买"）：local = recommend_shopping_products, LLM = general_chat (confidence 可能 < 0.80) → 选 local → 修复
- 不影响其他 LLM 修正 case（那些 LLM 返回的是 recommend_shopping_products，走低门槛分支）

---

### 改动 6（P2）：RAG 基础设施修复

**问题**：embedding_success = 0.093，milvus_success = 0.0。所有依赖语义检索的推荐质量 case 都无法验证。

**排查清单**：
1. `.env` 中 `EMBEDDING_PROVIDER=dashscope`，`DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`，`DASHSCOPE_API_KEY=your_dashscope_api_key_here` —— 确认 API key 是否仍有效、是否被限速。
2. `MILVUS_URI=http://localhost:19530` —— 确认本地 Milvus 服务是否在运行（`docker ps | grep milvus`）。
3. `MILVUS_COLLECTION=mallmind_product_chunks_qwen_v1` —— 确认 collection 是否存在且有数据。
4. `EMBEDDING_TIMEOUT_SECONDS=8` —— 如果 dashscope 延迟较高，可以尝试增大到 15。

**影响 case**：
- `cap_bundle_tablet_coffee`（"学习平板和办公室咖啡一起推荐"）：RAG 恢复后跨品类召回改善
- `cap_rag_review_dry_skin_cream`（"哪款面霜评价里更适合干皮？"）：RAG 恢复后评价内容检索可用

---

### 改动优先级和影响范围总结

| 优先级 | 改动 | 文件 | 直接修复的 case | 风险 |
|:---:|:---|:---|:---|:---|
| P0 | 1. 显式标记 _llm_chosen | tool_router.py route_shopping_tool_call() | 为改动2提供基础 | 极低，纯增量字段 |
| P0 | 2. Guard 取消负向覆盖 | tool_router.py validate_and_guard_tool_call() | cap_gift_parents_digital, cap_rag_sunscreen_less_oily | 中，需要回归测试确认不会引入假阴性 |
| P0 | 3. validate_business_goal bypass | recommendation_pipeline.py | cap_bundle_school_start, cap_bundle_beginner_running | 低，只跳过已被路由层确认的关键词门禁 |
| P1 | 4. Router prompt 增强 | tool_router.py try_llm_route_tool_call() | cap_negative_medicine | 低，prompt 变更 |
| P1 | 5. 非对称置信度阈值 | tool_router.py route_shopping_tool_call() | cap_negative_medicine（双重保险） | 低，只影响 LLM→general_chat 降级 |
| P2 | 6. RAG 基础设施修复 | .env + 运维 | cap_bundle_tablet_coffee, cap_rag_review_dry_skin_cream | 取决于基础设施状态 |

### 预期修复效果

修改前 balanced_demo：success=0.837 (36/43)，route=0.930 (40/43)

修改后预期：
- 改动 1+2 → 修复 2 case（gift_parents_digital + sunscreen_less_oily）
- 改动 3 → 修复 2 case（bundle_school_start + bundle_beginner_running）
- 改动 4+5 → 修复 1 case（negative_medicine）
- 改动 6 → 修复 2 case（bundle_tablet_coffee + rag_review_dry_skin_cream）

全部修复后预期：success=1.000 (43/43)，route=1.000 (43/43)

### 回归风险

改动 2 删除/弱化 Guard 后，需要确认以下场景不会退化：
- 真正的对比请求（"iPhone 和 Pixel 哪个好"）：`local_route_tool_call()` 的 `_looks_like_compare_request()` 仍会正确路由到 `compare_products`，LLM 也会正确判断。Guard 不再做二次确认，但前两层已经够了。
- 真正的非购物请求（"今天天气怎么样"）：`local_route_tool_call()` 走 `_is_general_chat()` → `general_chat`，LLM 也应该判断为 `general_chat`。Guard 不再做二次确认，但前两层已经够了。
- 边缘 case（"随便看看"）：`local_route_tool_call()` 可能走 `recommend_shopping_products`（默认 fallback）或 `general_chat`（取决于 `_is_general_chat` 判断）。如果走 `general_chat`，LLM 会二次判断。如果走 `recommend_shopping_products`，管线会处理。两个路径都是安全的。

建议修改后用 20-case 和 43-case 两轮回归测试确认无退化。
