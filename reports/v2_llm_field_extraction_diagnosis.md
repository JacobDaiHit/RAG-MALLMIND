## LLM 路由字段提取失败诊断 & 复测报告

测试时间：2026-06-07 20:33–20:38 | 服务器：http://127.0.0.1:8000  
路由模型：sensenova-6.7-flash-lite | max_tokens=320

---

### 一、根因诊断：LLM 为什么不填充新字段

#### 1.1 实测证据

对全部 17 个独立 FAIL/PARTIAL 案例的 LLM 路由原始返回做了逐条检查，结论一致：

**LLM 始终只返回 12 个基础字段**：
`query, budget, category, usage, preferences, product_ids, catalog_scope, compare_with_previous, quantity, action, topic, need_full_pc_build`

**以下 8 个新字段从未被输出**（除 #146 的 exclude_brands 外）：
`brands, exclude_brands, sort_order, price_min, price_max, must_have_terms, excluded_terms, target_sub_categories`

典型示例——#124 "华为品牌的商品有哪些" LLM 原始返回：
```json
{
  "name": "recommend_shopping_products",
  "arguments": {
    "query": "华为品牌商品",
    "budget": null, "category": "",
    "usage": [], "preferences": {},
    "product_ids": [], "catalog_scope": "ecommerce",
    "compare_with_previous": false, "quantity": null,
    "action": "", "topic": "", "need_full_pc_build": false
  },
  "confidence": 0.9, "source": "llm"
}
```
——`brands` 字段完全缺失，品牌信息丢失在 LLM 输出阶段。

#### 1.2 根因分析

| 因素 | 说明 |
|------|------|
| **max_tokens=320 过紧** | 包含全部 17 个字段（含 8 个新字段）的完整 JSON 约需 250–350 个 token。320 上限使 LLM 被迫"省略"不强制的字段 |
| **模型能力不足** | sensenova-6.7-flash-lite（7B 级别）难以可靠地遵循包含 17 个参数的复杂 JSON schema。它倾向于只输出"必需"字段和少量明显字段 |
| **字段名混淆** | #123 中 LLM 输出了 `"sort"` 而非 `"sort_order"`——模型将 `sort_order` 误记为 `sort`，说明 prompt 中示例不够突出 |
| **Pydantic 默认值"静默吞噬"** | `RoutedArguments` 给 `brands=[]`、`sort_order=None` 设了默认值。当 LLM 不输出这些字段时，Pydantic 不会报错，而是默默填入默认值——没有 fail-fast 反馈 |

#### 1.3 影响链

```
LLM 不输出 brands/exclude_brands/sort_order
  → Pydantic 默认值 brands=[], sort_order=None
  → merge_route_arguments 拿到空值，rule-based 也无法补充（rule 没这些字段）
  → structured_filter.py 的品牌硬过滤因 brands=[] 而不触发
  → 最终效果：所有新管道改动形同虚设
```

---

### 二、全部 25 条 FAIL & PARTIAL 案例复测结果

#### 2.1 已修复（本轮相比 v2_analysis 新增修复 1 条）

| # | 输入 | 原始 | 本轮 | 说明 |
|---|------|------|------|------|
| 114 | 有没有防水的运动手表 | FAIL | **PASS** | 返回"没有找到足够贴合的商品" |
| 120 | 有没有好看的裙子 | FAIL | **PASS** | 同上 |
| 148 | 帮我把 iPhone 17 Pro 加到购物车 | FAIL | **PASS** | 现在先搜索再推荐 iPhone 17 Pro，cart 自动加入 |
| 171 | 手机+耳机，总共不超过1万 | FAIL | **PASS** | budget 正确解析为 10000.0 |
| 112 | 有什么好吃的零食推荐吗 | PARTIAL | **PASS** | 3件结果全是食品 |
| 126 | 第二页的商品 | PARTIAL | **PASS** | 正确追问品类 |

#### 2.2 仍失败/部分失败的 FAIL 案例（9条）

| # | 输入 | 本轮路由 | 表现 | 失败原因 |
|---|------|---------|------|---------|
| 124 | 华为品牌的商品有哪些 | recommend | 首位推荐 The Ordinary 精华液 | LLM 未输出 brands 字段，品牌过滤无法触发 |
| 128 | 最贵的商品是什么 | general_chat (hallucination_guard) | "目前我还没有具体的价格数据" | hallucination guard 过度拦截，local rules 未识别"最贵"为购物意图 |
| 130 | 华为Pura 90 Pro 的详细信息 | recommend | 首位推荐 The Ordinary | 同 #124，LLM 未输出 brands |
| 146 | 看看运动鞋，不要Nike的 | recommend | 首位 Nike Air Zoom Pegasus 41 | LLM **正确输出** exclude_brands=["Nike"]，但 Nike 仍排第一。pipeline 内部的品牌名匹配可能有中英文不一致问题（Nike vs 耐克） |
| 165 | 你们有卖 PS5 吗 | general_chat (hallucination_guard) | "有的！PS5 游戏主机我们这里有售" | general_chat LLM 无目录感知，幻觉声称有 PS5 |
| 167 | 三星Galaxy S30怎么样 | recommend | 推荐欧莱雅防晒等无关品 | 目录无三星手机，pipeline 未兜底 |
| 168 | 有没有一百万以上的商品 | recommend | 推荐 59 元 The Ordinary | 目录无超高价商品，pipeline 未检测价格区间 |
| 152 | 把华为耳机数量改成2 | apply_cart | 将所有购物车商品数量改为2 | cart update 未精确定位目标商品 |

#### 2.3 仍有问题的 PARTIAL 案例（10条）

| # | 输入 | 本轮表现 | 核心问题 |
|---|------|---------|---------|
| 123 | 所有商品按价格从低到高排列 | LLM 输出 `"sort"` 而非 `"sort_order"`，字段被丢弃 | 排序未生效 |
| 131 | 小米17 Ultra 有几个版本 | 推荐 AHC 眼霜等无关品 | 目录无小米17 Ultra，pipeline 未识别 |
| 133 | AirPods Pro 3 支持心率监测吗 | hallucination_guard 拦截到 general_chat | 应走 recommend 查 AirPods 信息 |
| 141 | 这款耳机有差评吗 | 直接推荐华为FreeBuds | 无上下文应追问 |
| 149 | 我要买华为Pura 90 Pro，黑色的 | 推荐 iPhone 17 Pro | LLM 未输出 brands，品牌过滤不触发 |
| 150 | 看看我的购物车 | 路由正确，5件商品 | 回复文案仍显示"已将...加入购物车" |
| 151 | 把第一个去掉 | hallucination_guard 拦截到 general_chat | LLM 正确选了 apply_cart，但 guard 误判 |
| 157 | 续航怎么样 | 推荐小米17 Max | 未理解上文是 OPPO Reno |
| 164 | 都不要，看看别的 | 返回完全相同的3款手机 | 未排除已推荐商品 |
| 170 | 高端护肤品送妈妈，预算3000 | 推荐薇诺娜 89 元 | "高端"语义未体现 |

---

### 三、修复意见（仅限提升 LLM 兜底能力，不硬编码，不正则匹配）

#### 意见 1：增大 max_tokens（P0，解决字段缺失的直接瓶颈）

**问题**：`RECOMMENDATION_ROUTER_LLM_MAX_TOKENS` 默认 320，对包含 8 个新字段的完整 JSON 来说太紧。LLM 被迫省略字段来保证 JSON 不截断。

**建议**：将默认值从 320 提升到 **600**。给 LLM 充足的空间输出所有字段。

**影响范围**：#124, #130, #149（brands）；#123（sort_order）；#168, #170（price_min/price_max）；所有需要新字段的案例。

#### 意见 2：优化 system prompt 的输出格式约束（P0）

**问题**：当前 prompt 中参数说明以文本列表形式给出，示例也分散在文本中。7B 级模型难以从长文本中准确提取所有字段名和格式要求。

**建议**：
1. 在 system prompt 的"输出格式"部分，提供一个**完整的 JSON 模板**，显式列出所有必须出现的字段（包括新字段），而不是只在参数说明中用文字描述
2. 将当前分散的示例改为**完整的输入→输出对**，包含所有字段（不只是变化的字段）
3. 在 ROUTED_CALL_SCHEMA 中将新字段用 `"<必填>"` 标注，强化模型输出意识

示例 prompt 改进方向（仅示意）：
```
输出格式（严格按此 JSON 结构，所有字段必须出现）：
{
  "name": "工具名",
  "confidence": 0.9,
  "reason": "选择依据",
  "source": "llm",
  "arguments": {
    "query": "用户原始文本",
    "budget": null,
    "category": "",
    "brands": [],          // 用户想要的品牌，必须提取
    "exclude_brands": [],  // 用户排除的品牌，必须提取
    "sort_order": null,    // 排序：price_asc/price_desc/rating_desc
    "price_min": null,
    "price_max": null,
    "usage": [],
    "preferences": {},
    "product_ids": [],
    "catalog_scope": "ecommerce",
    "must_have_terms": [],
    "excluded_terms": [],
    "target_sub_categories": [],
    "action": ""
  }
}
```

**影响范围**：所有需要新字段的案例。

#### 意见 3：升级路由模型（P1）

**问题**：`sensenova-6.7-flash-lite` 是 7B 级别的轻量模型，在处理 17 参数的复杂 JSON schema 时能力不足。即使 prompt 优化后，可靠性和字段覆盖率仍有限。

**建议**：将 `MALLMIND_ROUTER_MODEL` 切换到更强大的模型，例如：
- `sensenova-6.7-flash`（非 lite 版本）
- 或更大的通用模型

路由是单次调用，延迟容忍度较高（当前平均 5-7s），可以换取更高的参数提取准确率。

**影响范围**：所有案例的 LLM 路由质量。

#### 意见 4：在 Pydantic 层增加 fail-fast 验证（P2）

**问题**：当前 `RoutedArguments` 给所有新字段设了默认值（`brands=[]`, `sort_order=None`）。当 LLM 不输出这些字段时，Pydantic 不会报错，问题被静默掩盖。

**建议**：在 `validate_and_guard_tool_call` 中增加诊断日志——当 LLM source 为 "llm" 但 `brands`/`exclude_brands`/`sort_order` 全为空时，记录一条 warning 日志，标明"LLM 未提取关键参数"。这不会改变行为，但能帮助快速定位问题。

#### 意见 5：对 `#146 exclude_brands` 做品牌名归一化（P2）

**问题**：#146 中 LLM 正确提取了 `exclude_brands=["Nike"]`，但 pipeline 内部品牌匹配可能是基于中文名"耐克"而非英文名"Nike"。structured_filter.py 的 `normalize` 函数可能未做中英文品牌映射。

**建议**：在 `structured_filter.py` 的品牌过滤逻辑中，增加品牌名归一化映射表（Nike↔耐克, Apple↔苹果, Huawei↔华为 等）。这不是硬编码规则判断，而是数据层面的同义词映射。

#### 意见 6：general_chat 的 LLM prompt 增加目录感知（P2）

**问题**：#165 "你们有卖 PS5 吗"路由到 general_chat 后，LLM 回复"有的！PS5 游戏主机我们这里有售"——这是纯幻觉。

**建议**：在 general_chat 的 system prompt 中增加一条规则：
```
"如果用户询问某个具体商品是否有售（如 PS5、Switch 等），而你不确定目录中是否有该商品，
 请回复'让我帮您搜索一下'并引导用户描述更多需求，不要直接声称有或没有。"
```

---

### 四、优先级排序

| 优先级 | 修改意见 | 预期影响 | 实现难度 |
|-------|---------|---------|---------|
| **P0** | 增大 max_tokens 到 600 | 直接解决字段截断问题 | 改一个默认值 |
| **P0** | 优化 system prompt 输出格式 | 让 7B 模型可靠输出所有字段 | 重写 prompt 段落 |
| **P1** | 升级路由模型 | 从根本上提升参数提取准确率 | 改环境变量 |
| **P2** | fail-fast 诊断日志 | 加速问题定位 | 加几行 log |
| **P2** | 品牌名归一化 | 修复 #146 中英文不匹配 | 加映射表 |
| **P2** | general_chat prompt 增加目录感知 | 修复 #165 幻觉 | 加一条规则 |

---

### 五、不可通过 LLM 改善的案例（需要后端逻辑修改）

以下案例的根因不在 LLM 路由层，需要后端 pipeline/guard 逻辑修改：

| # | 问题 | 所需后端修改 |
|---|------|-------------|
| 128 | hallucination guard 过度拦截 | NORMAL_PRODUCT_TERMS 加入"商品"/"最贵"等词 |
| 133 | 同上，AirPods 查询被拦截 | 同上，产品名应触发购物意图 |
| 151 | "把第一个去掉"被拦截 | hallucination guard 需识别 cart 操作词 |
| 167 | 三星S30 不存在但未兜底 | query_guards 增加品牌+型号缺失检测 |
| 168 | 一百万预算未兜底 | query_guards 增加价格区间检测 |
| 152 | cart update 改全部数量 | cart handler 精确匹配目标商品 |
| 157 | 多轮上下文丢失 | session_context 增强上下文传递 |
| 164 | 不排除已推荐商品 | recommendation pipeline 利用 session 排除历史 |
