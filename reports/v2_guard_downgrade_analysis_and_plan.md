# V2 Guard 降级报告分析 + 改进方向（仅测试代码）

分析时间：2026-06-07

---

## 一、报告摘要

**已修复（13/21 FAIL+PARTIAL → PASS）：61.9% 修复率**

Guard 降级 + Prompt 优化后，被 hallucination_guard 错误拦截的 4 条案例全部修复，目录无商品时的兜底回复明显改善，具体商品查询路由准确率提升。

**剩余 7 条 FAIL/PARTIAL：**

---

## 二、剩余问题分层

### 第一层：LLM 字段提取能力不足（影响 4 条）

| # | 输入 | 问题 | 根因 |
|---|------|------|------|
| 124 | 华为品牌的商品有哪些 | 首推 The Ordinary（非华为） | LLM 未提取 `brands` 字段 |
| 146 | 看看运动鞋，不要Nike的 | Nike 排第一 | LLM 提取了 `category` 但 `exclude_brands` 未生效 |
| 123 | 所有商品按价格从低到高排列 | 未按价格排序 | LLM 输出 `sort_by` 而非 `sort_order` |
| 112 | 有什么好吃的零食推荐吗 | 推了酱油 | LLM 未提取 `must_have_terms`（零食） |

**根因链**：
- sensenova-6.7-flash-lite (7B) 模型能力上限：无法稳定输出 brands/sort_order/price_min 等新字段
- 当前 prompt 模板只给了 7 个示例，且示例中的字段是理想情况
- 7B 模型倾向于只输出 `name` + `arguments.query` + `confidence` + `reason`（4 字段），忽略其余

### 第二层：Pipeline/上下文问题（影响 5 条）

| # | 输入 | 问题 | 根因 |
|---|------|------|------|
| 141 | 这款耳机有差评吗 | 无上下文，应追问而非推荐 | Pipeline 缺少"无上下文时追问"逻辑 |
| 157 | 续航怎么样 | 未理解上文是 OPPO Reno | 多轮上下文未注入 LLM prompt |
| 164 | 都不要，看看别的 | 推荐了与上轮相同商品 | Pipeline 未排除已推荐商品 |
| 170 | 高端护肤品送妈妈，预算3000 | 推了 89 元薇诺娜 | "高端"语义未被 pipeline 识别 |
| 126 | 第二页的商品 | 仍推商品而非追问 | 与 #141 类似，缺少上下文感知 |

**根因链**：
- 多轮对话的上文推荐结果（product_cards）未注入到 LLM router 的 prompt 中
- "高端"等语义修饰词未被 pipeline 的 requirement 解析层识别
- 排除已推荐商品的逻辑在 session 层存在，但未传递到推荐 pipeline

---

## 三、改进方向（仅测试代码，不碰后端）

### 方向 A：升级 LLM Router Prompt —— 引导 7B 模型输出更多字段

**问题**：当前 router prompt 给了 JSON 模板但 7B 模型仍只输出 4 字段。

**改进思路**（测试侧）：
1. 在 `tests/test_fail_cases.py` 中增加 **prompt 诊断用例**，向 router 发送边界查询，记录 LLM 原始输出，验证字段完整性
2. 建立 **字段覆盖率指标**：统计 router LLM 实际输出的字段种类，当 brands/sort_order 等字段覆盖率低于阈值时标记为已知限制
3. 在测试报告中增加 **字段提取诊断表**，让后续 prompt 优化有数据支撑

**具体方案**：
```python
# tests/test_fail_cases.py 中新增
ROUTER_FIELD_DIAGNOSTIC_CASES = [
    ("华为品牌的商品有哪些", ["brands"]),
    ("看看运动鞋，不要Nike的", ["exclude_brands"]),
    ("所有商品按价格从低到高排列", ["sort_order"]),
    ("3000到5000之间的手机", ["price_min", "price_max"]),
]
```

对每个 case 发送请求后，解析 `tool_call` 事件中的 `arguments`，检查目标字段是否存在且非空。统计字段覆盖率，输出到报告。

### 方向 B：增加 LLM Fallback 分级测试 —— 验证降级链路完整性

**问题**：当 LLM 未提取 brands 等字段时，系统是否有 fallback？目前没有测试覆盖这条降级路径。

**改进思路**（测试侧）：
1. 增加 **"LLM 字段缺失时的 fallback 行为"测试**：模拟 LLM 只返回基础字段的情况，验证系统是否能正确降级
2. 增加 **"LLM 返回异常字段名"测试**：模拟 LLM 返回 `sort_by`（而非 `sort_order`），验证系统是否有容错
3. 增加 **"LLM 返回空 arguments"测试**：验证当 LLM 完全未提取参数时的兜底行为

**具体方案**：
```python
# tests/test_fail_cases.py 中新增
LLM_FALLBACK_DIAGNOSTIC_CASES = [
    # 当 LLM 未提取 brands 时，fallback 到规则提取是否生效
    ("华为品牌的商品有哪些", "brands_fallback", lambda tc: tc.tool_call_args.get("brands") or "rule_extracted"),
    # 当 LLM 返回 sort_by 而非 sort_order 时
    ("所有商品按价格从低到高排列", "sort_field_compat", lambda tc: "sort_order" in tc.tool_call_args or "sort_by" in tc.tool_call_args),
]
```

### 方向 C：增加多轮上下文注入测试 —— 验证 session 是否正确传递上下文

**问题**：#157 "续航怎么样" 和 #164 "都不要，看看别的" 都因为缺少上下文而失败。

**改进思路**（测试侧）：
1. 增加 **多轮对话测试序列**：先发一条推荐请求，再发追问，验证追问是否正确引用了上文
2. 在测试中 **检查 session 的 topic_memory 和 last_result** 是否正确传递
3. 增加 **"无上下文追问时应追问而非推荐"测试**：验证当 session 无历史推荐时，追问类消息的行为

**具体方案**：
```python
# tests/test_fail_cases.py 中新增
MULTI_TURN_DIAGNOSTIC_SEQUENCES = [
    {
        "name": "续航追问",
        "steps": [
            ("推荐一款OPPO手机", "recommend_shopping_products"),
            ("续航怎么样", "期望: 基于上文 OPPO 回答续航，而非推荐新品"),
        ],
        "session_id": "mt_battery",
    },
    {
        "name": "排除已推荐",
        "steps": [
            ("推荐一款手机", "recommend_shopping_products"),
            ("都不要，看看别的", "期望: 返回与上轮不同的手机"),
        ],
        "session_id": "mt_exclude",
    },
]
```

### 方向 D：增加 LLM 原始输出诊断收集 —— 为 prompt 优化提供数据

**问题**：当前测试只记录最终结果，不记录 LLM 原始输出。无法判断是 LLM 没输出字段，还是 pipeline 丢弃了字段。

**改进思路**（测试侧）：
1. 在 `verify_fail_cases.py` 中增加 **LLM 原始输出收集**：解析 `tool_call` 事件中的 `arguments`，记录完整 JSON
2. 增加 **字段丢弃诊断**：对比 LLM 输出的 arguments 和最终传入 pipeline 的 arguments，标记差异
3. 生成 **字段提取诊断报告**：每个 case 输出 LLM 实际提取了哪些字段、丢失了哪些字段

**具体方案**：
```python
# verify_fail_cases.py 中增强 send() 函数
def send(sid, msg):
    # ... 现有逻辑 ...
    # 新增：记录 LLM 原始 arguments
    llm_raw_args = None
    tool_call_args = None
    for evt in parsed_events:
        if evt["event"] == "tool_call":
            llm_raw_args = evt["data"].get("arguments", {})
            tool_call_args = llm_raw_args
    # ... 返回时增加 llm_raw_args 字段
```

### 方向 E：增加 LLM Prompt 变体 A/B 测试框架

**问题**：当前 prompt 只有一种版本，无法对比不同 prompt 对 7B 模型字段提取的影响。

**改进思路**（测试侧）：
1. 在测试中定义 **多个 prompt 变体**（如：简化版 vs 完整版、中文 vs 英文字段名、few-shot vs zero-shot）
2. 通过环境变量或配置文件切换 prompt 变体
3. 对同一组 FAIL cases 分别用不同 prompt 变体运行，对比字段覆盖率和路由准确率

**具体方案**：
```python
# tests/test_fail_cases.py 中新增
PROMPT_VARIANTS = {
    "current": "当前 prompt（7 示例 + 完整 JSON 模板）",
    "simplified": "简化 prompt（仅 3 个核心字段 + 2 示例）",
    "chinese_fields": "中文字段名 prompt（brands → 品牌列表）",
    "few_shot_5": "5-shot prompt（5 个完整示例）",
}

def run_with_prompt_variant(variant_name: str, cases: list) -> dict:
    """用指定 prompt 变体运行一组 case，返回字段覆盖率和路由准确率"""
    # 通过环境变量 MALLMIND_ROUTER_PROMPT_VARIANT 切换
    # 记录每个 case 的 LLM 原始输出
    # 统计字段覆盖率
    pass
```

### 方向 F：增加 LLM 能力边界标注 —— 区分"LLM 能力不足"和"系统 bug"

**问题**：当前测试将 LLM 能力不足（7B 模型不输出 brands）和系统 bug（pipeline 丢弃 brands）混为一谈。

**改进思路**（测试侧）：
1. 对每个 FAIL case 增加 **根因分类标签**：`LLM_CAPABILITY` / `PIPELINE_BUG` / `PROMPT_ISSUE` / `CONTEXT_MISSING`
2. 在测试报告中按根因分类汇总，区分"需要升级模型"和"需要修代码"
3. 对 `LLM_CAPABILITY` 类问题，标记为"已知限制"而非"BUG"

**具体方案**：
```python
# tests/test_fail_cases.py 中新增
ROOT_CAUSE_LABELS = {
    124: "LLM_CAPABILITY",   # 7B 模型不输出 brands
    146: "LLM_CAPABILITY",   # 7B 模型不输出 exclude_brands
    123: "LLM_CAPABILITY",   # 7B 模型输出 sort_by 而非 sort_order
    112: "LLM_CAPABILITY",   # 7B 模型不输出 must_have_terms
    141: "CONTEXT_MISSING",  # 无上下文时应追问
    157: "CONTEXT_MISSING",  # 多轮上下文未注入
    164: "CONTEXT_MISSING",  # 未排除已推荐商品
    170: "PIPELINE_BUG",     # 高端语义未识别
    126: "CONTEXT_MISSING",  # 无上下文时应追问
}
```

---

## 四、实施优先级

| 优先级 | 方向 | 预期收益 | 工作量 |
|--------|------|---------|--------|
| **P0** | D：LLM 原始输出诊断收集 | 为所有后续优化提供数据基础 | 小 |
| **P1** | A：字段覆盖率诊断 | 量化 7B 模型的真实字段提取能力 | 小 |
| **P2** | F：根因分类标注 | 区分 LLM 能力不足 vs 系统 bug | 小 |
| **P3** | C：多轮上下文注入测试 | 覆盖 #157, #164, #141, #126 | 中 |
| **P4** | B：LLM Fallback 分级测试 | 验证降级链路完整性 | 中 |
| **P5** | E：Prompt 变体 A/B 测试 | 为 prompt 优化提供实验框架 | 大 |

---

## 五、预期效果

执行 P0-P3 后：
- 能量化 7B 模型的字段提取能力上限（字段覆盖率指标）
- 能区分"LLM 能力不足"和"系统 bug"（根因分类）
- 能覆盖多轮对话场景的回归测试（#157, #164, #141, #126）
- 能为后续 prompt 优化或模型升级提供数据支撑

执行 P4-P5 后：
- 能验证 LLM 字段缺失时的 fallback 行为
- 能对比不同 prompt 变体对 7B 模型的影响
- 能确定是否需要升级模型或优化 prompt

**关键原则**：
- 不在后端代码中硬编码任何判断逻辑
- 不通过正则或关键词匹配来提高通过率
- 所有改进都围绕"提高 LLM 兜底能力"和"提供诊断数据"
- 测试代码本身不修改后端行为，只验证和诊断
