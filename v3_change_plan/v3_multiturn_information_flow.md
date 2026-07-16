# V3 多轮对话演练：用户每说一句，系统内部到底传了什么

> 这份文件只用具体对话解释 V3 的内部信息传递。商品 ID、价格和卡片 ID 均为示例，不代表真实商品数据。看完应能看清：用户的每句话如何变成约束、如何决定是否调用 LLM、商品卡怎样召回、下一轮怎样找到“第二个”。

## 0. 先认识四个贯穿所有轮次的对象

| 名称 | 直白解释 | 存在哪里 | 例子 |
|---|---|---|---|
| `NormalizedTurn` | 本轮清洗后的用户输入 | 当前请求和 TraceStore | “推荐手机” |
| `RequirementSpecV3` | 本轮要执行的需求清单 | 当前请求和 TraceStore | 手机、有货、3000 元内、不要小米 |
| `SessionCore` | 下一轮需要记住的少量事实 | Redis | 刚展示的三张卡、当前预算、待确认加购 |
| `CardModel` | 发送到前端的结构化商品卡 | SSE 响应和 SessionCore 摘要 | 卡 ID、商品 ID、默认 SKU、价格、目录版本 |

最重要的 ID 链是：

```text
用户看到“第二个”
    -> SessionCore.display_index=2
    -> card_id=c_r1_2
    -> product_id=p_phone_205
    -> sku_id=sku_205_256 或 sku_205_512
    -> catalog_version=cat_20260715
```

系统不能用“第二个标题看起来像 Phone B”这种方式猜。卡片序号只在当前有效展示集里有意义；用户换话题、卡片过期或前端传来新的 card ID 时，要重新验证。

## 1. 场景一：用户只说“推荐手机”——三张卡是怎么来的

### 1.1 用户输入前的会话

这是一个空会话：

```json
{
  "session_id": "s_001",
  "active_domain": "none",
  "inherited_constraints": {},
  "last_displayed_items": [],
  "focus": {},
  "cart": {"items": [], "pending_action": null}
}
```

### 1.2 第一步：InputGuard 不改意思，只做清洗

用户原话：`推荐手机`

InputGuard 生成：

```json
{
  "request_id": "r1",
  "session_id": "s_001",
  "normalized_text": "推荐手机",
  "raw_text_hash": "...",
  "attachments": [],
  "normalization_events": [],
  "security_flags": []
}
```

这里没有 LLM。它只是保证后续看到的输入稳定。

### 1.3 第二步：本地能否证明不用语义 LLM

本地分类表里有唯一映射：

```text
“手机”
  -> category_id=digital
  -> product_type_id=phone
  -> catalog_sub_category_id=智能手机
```

“推荐”也明确是导购动作。这里能直通的原因不是“没有出现复杂词”，而是所有有业务意义的文本都被固定规则处理完了：`推荐` 被解析为动作，`手机` 被解析为唯一分类，剩余内容为空。因此生成安全证明：

```json
{
  "action": "recommend_shopping_products",
  "mode": "product",
  "safety_proof": {
    "grammar_id": "recommend.category_constraints.v1",
    "grammar_version": "1.0",
    "parse_tree": {"action": "recommend", "product_type": "phone", "constraints": []},
    "valid_parse_count": 1,
    "semantic_group_count": 1,
    "semantic_unique": true,
    "semantic_signature": "sha256:...",
    "operator_scopes_resolved": true,
    "unresolved_operators": [],
    "proof_version": "rule-proof-v1",
    "unresolved_spans": [],
    "entities_unique": true,
    "references_unique": true,
    "business_schema_complete": true,
    "conflicts": [],
    "allowed": true
  }
}
```

如果用户说的是“推荐手机，3000 元以内，不要小米，拍照优先”，也可以不调语义 LLM，但理由同样必须逐段写出来：`3000 元以内 -> 预算上限`、`不要小米 -> 硬排除`、`拍照优先 -> 受控软偏好`，并且不能有任何剩余业务文字。只要加上“给妈妈用”“比上一台强”“也许华为更合适”这类未被本地规则完整解释的片段，就转 SemanticParse LLM。完整的逐词解析顺序见施工说明 4.2 节。

`不要给我推荐手机，3000 元以内，pad不错，来点推荐` 也不该追问“搜哪个品类”：统一词表已唯一登记 `pad -> product_type_id=tablet`，因此 SemanticParse 已有平板候选域；“手机”仍只在否定范围内，`3000 元以内` 是上限。但本地不能因 token 都被标记就直接放行：V1 没有覆盖“类目 + 评价词 + 后置推荐动词”的完整 grammar，`valid_parse_count=0`，所以它必须走 SemanticParse。模型输出受限的 `recommend_shopping_products(category=tablet)` 后，再进入平板 CandidateGate 和 embedding 检索；不需要也不允许再问用户想买手机还是平板。

真正需要澄清的反例是：`不要给我推荐手机，3000 元以内，来点推荐`。手机只处于否定范围，句中没有任何正向、可归一的商品对象。此时系统只可问“你想让我推荐哪一类商品？”，在用户回答前不搜手机也不搜平板。该句澄清发出时，系统同时写入 SessionCore：

```json
{
  "pending_clarification": {
    "clarification_id": "cl_r1_01",
    "parent_request_id": "r1",
    "question": "你想让我推荐哪一类商品？",
    "kind": "choose_category",
    "options": [
      {"option_id": "tablet", "label": "平板", "value": ["tablet"]},
      {"option_id": "camera", "label": "相机", "value": ["camera"]}
    ],
    "inherited_draft_fields": {
      "price_max": 3000,
      "exclude_product_type_ids": ["phone"]
    },
    "expires_at": "5 分钟后"
  }
}
```

用户下一轮只说 `对` 时，系统不能把它当成“平板”：这是 `choose_category`，合法答案必须是某个选项或能唯一映射到选项的文本。用户点“平板”或只答“平板”时，本地验证 `clarification_id=cl_r1_01`、TTL 和选项值，写入 `product_type_ids=[tablet]`；此前用户明确的 3000 上限和排除手机一并生效，随后进入平板候选门和 embedding/BM25 排序。用户答“对”则回复“请直接选一种商品类别”，不调用检索。

若用户答的是 `手机也可以`，系统不会机械沿用旧的排除：它按统一词表定位 `phone`，明确释放 `exclude_product_type_ids=[phone]`，但仍需要用户说明究竟要推荐手机、平板还是两者都可。草案过期或 clarification ID 不匹配时，则按普通新请求处理，绝不猜。

这不是唯一的追问类型。若系统问的是 `choose_category`：`你想买手机、平板还是相机？`，用户答“平板”不是确认，而是 `single_choice`；本地用该 plan 的选项表把“平板”映射为 `product_type=tablet`，随即进入平板候选门。若系统问的是可选偏好：`你更看重拍照、游戏、续航还是轻薄？`，用户点“拍照”只写入 `soft.desired_attributes=[camera]`，在当前合格候选中重排，不把拍照当硬过滤，也不要求重新检索所有商品。若用户答“主要拍娃，晚上也要拍”，文本不等于按钮选项，系统才调用受限 SemanticParse，并且只允许补充当前追问的用途/拍照字段，不能趁机改预算、品牌或类别。

结论：这个严格匹配 `推荐手机` 的场景不需要 SemanticParse LLM。注意，这不等于“系统不需要模型”；后面的 embedding 仍可用于商品文本检索。`pad不错，来点推荐` 这类已经知道类目、但不匹配 V1 完整 grammar 的场景仍需 SemanticParse 理解动作，不能由本地猜。

### 1.4 第三步：生成本轮需求清单

```json
{
  "request_id": "r1",
  "action": "recommend_shopping_products",
  "mode": "product",
  "hard": {
    "category_ids": ["digital"],
    "product_type_ids": ["phone"],
    "catalog_sub_category_ids": ["智能手机"],
    "active": true,
    "in_stock": true
  },
  "soft": {
    "diversity": {"brand": true, "price_band": true}
  },
  "missing_fields": ["budget", "primary_usage"],
  "clarification_required": false,
  "field_provenance": {
    "category_ids": "current_turn_taxonomy",
    "active": "system_default",
    "in_stock": "system_default"
  },
  "catalog_version": "cat_20260715"
}
```

没有预算和用途不代表不能推荐。第一次可以给用户探索用的三张卡，同时把预算、用途标成后续可问信息；不能替用户编一个预算。

### 1.5 第四步：先用目录筛资格，再进行检索

目录候选门做的不是“找最像的手机”，而是先问目录：

```text
哪些商品同时满足：
  - 是数码电子里的智能手机
  - 当前上架
  - 当前有库存
```

示例结果：

```json
{
  "allowed_product_ids": ["p_phone_101", "p_phone_205", "p_phone_330", "p_phone_408"],
  "removed": {
    "wrong_category": 31,
    "inactive": 2,
    "out_of_stock": 5
  },
  "catalog_version": "cat_20260715"
}
```

只有这份允许列表生成后，系统才调用 embedding/BM25 检索。传给 Milvus 的过滤条件不是原文“推荐手机”，而是受控字段：

```json
{
  "category_ids": ["digital"],
  "product_type_ids": ["phone"],
  "sub_category_ids": ["智能手机"],
  "product_ids": ["p_phone_101", "p_phone_205", "p_phone_330", "p_phone_408"],
  "active": true,
  "in_stock": true
}
```

### 1.6 第五步：检索、商品聚合、排序和多样性

检索可能拿到同一商品的多段介绍文字。系统必须按 `product_id` 聚合，不能因为 Phone A 有三段文本，就把它当成三台手机展示。

之后排序器在“合格商品”中综合：用户文本相关性、目录属性、价格段、品牌多样性。这里的多样性含义是：如果前三名全是同一品牌、同一价位，系统可以选择一台同样合格但品牌/价位不同的商品，让首次探索更有价值。

假设最后得到三张卡：

```json
{
  "event": "recommendation_cards",
  "request_id": "r1",
  "catalog_version": "cat_20260715",
  "cards": [
    {"card_id": "c_r1_1", "display_index": 1, "product_id": "p_phone_101", "default_sku_id": "sku_101_256", "title": "Phone A", "brand_family_id": "brand_a", "display_price": 2799},
    {"card_id": "c_r1_2", "display_index": 2, "product_id": "p_phone_205", "default_sku_id": "sku_205_256", "title": "Phone B", "brand_family_id": "brand_b", "display_price": 2999},
    {"card_id": "c_r1_3", "display_index": 3, "product_id": "p_phone_330", "default_sku_id": "sku_330_256", "title": "Phone C", "brand_family_id": "brand_c", "display_price": 3299}
  ]
}
```

回答 LLM 若启用，只能根据这三张已验证卡写“适合谁、差异是什么”的说明；卡片中的 ID、价格、SKU 不能由它生成。

### 1.7 第六步：本轮结束，只写一小份 SessionDelta

```json
{
  "active_domain": "shopping",
  "active_catalog_scope": "ecommerce",
  "inherited_constraints": {
    "category_ids": ["digital"],
    "product_type_ids": ["phone"],
    "catalog_sub_category_ids": ["智能手机"]
  },
  "last_displayed_items": ["c_r1_1", "c_r1_2", "c_r1_3"],
  "focus": {"card_id": "c_r1_1", "product_id": "p_phone_101"},
  "recent_turn_append": "展示三台手机；用户未给预算和用途"
}
```

Redis 不保存完整回答、检索分数和模型 prompt；这些放 TraceStore。下一轮系统只需要知道三张卡是谁即可。

## 2. 场景二：“第二个有 512G 吗？现在多少钱”——为什么不需要再做 RAG

### 2.1 目标解析顺序

用户的第二句是：`第二个有 512G 吗？现在多少钱`

本地先看前端是否传 card ID；没有则看“第二个”。SessionCore 中恰好有 display_index=2，所以解析为：

```json
{
  "action": "parameter_query",
  "operation": "sku_and_price",
  "target": {
    "card_id": "c_r1_2",
    "product_id": "p_phone_205",
    "default_sku_id": "sku_205_256",
    "catalog_version": "cat_20260715"
  },
  "safety_proof": {
    "grammar_id": "target.sku.v1",
    "grammar_version": "1.0",
    "parse_tree": {"target": "c_r1_2", "storage_gb": 512, "question": ["sku", "price"]},
    "valid_parse_count": 1,
    "semantic_group_count": 1,
    "semantic_unique": true,
    "semantic_signature": "sha256:...",
    "operator_scopes_resolved": true,
    "unresolved_operators": [],
    "proof_version": "rule-proof-v1",
    "unresolved_spans": [],
    "entities_unique": true,
    "references_unique": true,
    "business_schema_complete": true,
    "allowed": true
  }
}
```

这轮不调语义 LLM、不调 embedding、不做商品推荐。因为用户不是要找相似商品，而是问一件已经明确的商品。

### 2.2 直接读取实时目录

目录返回：

```json
{
  "product_id": "p_phone_205",
  "available_skus": [
    {"sku_id": "sku_205_256", "storage_gb": 256, "price": 2999, "in_stock": true},
    {"sku_id": "sku_205_512", "storage_gb": 512, "price": 3499, "in_stock": true}
  ],
  "catalog_version": "cat_20260715"
}
```

后端确定性计算出：有 512G，价格 3499，比默认 256G 贵 500。可以用模板直接回答，也可让回答 LLM 把这几个已验证字段写得自然。无论如何，LLM 不能把 3499 改成“约 3000”。

SessionCore 只更新：

```json
{"focus": {"card_id": "c_r1_2", "product_id": "p_phone_205", "sku_id": "sku_205_512"}}
```

如果 512G 已经无货，系统明确说无货；不能从描述文本里找一个“可能有 512G”的结论。

## 3. 场景三：“第一和第二个拍照哪个更好，重点看夜景”——先表格、后解释

### 3.1 目标同样先由卡片定位

“第一和第二个”解析为 `p_phone_101` 和 `p_phone_205`。系统生成：

```json
{
  "action": "parameter_query",
  "operation": "compare",
  "targets": ["p_phone_101", "p_phone_205"],
  "soft": {"desired_attributes": ["night_photography"]}
}
```

### 3.2 AttributeRegistry 决定哪些字段能比

手机属性表提前规定：屏幕、芯片、主摄、长焦、OIS、传感器、夜景模式、视频防抖等字段如何读取、如何统一单位、按什么顺序展示。系统从目录读这两台手机的字段，先生成确定性比较表：

```text
字段                   Phone A                 Phone B
主摄规格               目录字段                目录字段
是否有 OIS             目录字段                目录字段
长焦/焦段              目录字段或“未提供”      目录字段或“未提供”
夜景相关模式           目录字段                目录字段
```

只有表生成后，才允许回答 LLM 解释“夜景更适合谁”。它必须引用表里存在的字段；如果两款的传感器尺寸没有入库，它不能擅自补参数或给强结论，应说“目录没有该字段，不能据此判断”。

比较完成后，SessionCore 更新：

```json
{"focus": {"comparison_product_ids": ["p_phone_101", "p_phone_205"], "product_id": "p_phone_205"}}
```

所以用户下一句“第二个的长焦呢”仍能安全定位。

## 4. 场景四：“预算 3000 内，不要小米，拍照优先，重新推荐”——硬条件和偏好怎样分开

这是新的推荐请求，不是问上一张卡的事实。RequirementSpecV3 合并后的结果：

```json
{
  "hard": {
    "category_ids": ["digital"],
    "product_type_ids": ["phone"],
    "catalog_sub_category_ids": ["智能手机"],
    "price": {"max": 3000, "currency": "CNY", "strict": true},
    "exclude_brand_family_ids": ["xiaomi"],
    "active": true,
    "in_stock": true
  },
  "soft": {
    "desired_attributes": ["camera"],
    "usage_tags": ["night_photography"]
  }
}
```

为什么“不要小米”是硬条件，而“拍照优先”不是？因为用户明确命令不能被违背；而“拍照好”可能没有一个完整、统一、每台商品都有的目录字段，它更适合用来排序与解释。

这轮的三道检查：

```mermaid
flowchart LR
    A[明确：不要小米] --> B[品牌表：xiaomi 家族]
    B --> C[目录候选门：不生成小米商品 ID]
    C --> D[Milvus 过滤：召回前排除 xiaomi]
    D --> E[向量/BM25 只在剩余商品中排序]
    E --> F[最终卡片再次检查品牌家族]
```

最终即使小米商品描述与“拍照优先”最接近，也不能显示。

### 4.1 后面用户改口“要小米”怎么办

如果用户下一轮明确说 `要小米`，这是最新的硬命令：

```text
旧：exclude_brand_family_ids=[xiaomi]
新：删除 exclude_brand_family_ids 中的 xiaomi
新：include_brand_ids=[xiaomi]（或按产品需求写品牌家族包含）
```

系统重新走目录候选门和检索，不会拿旧结果直接换文案。若用户说“要小米但不要小米”，条件冲突，只能问清楚。

## 5. 场景五：叙事型品牌表达——为什么仍要调 LLM，但明确不喜欢必须过滤

用户在**新会话**中说：

> 上次同事给我推荐了小米，但我用着并不好。或许还是华为适合我。要不你给我一点推荐？

### 5.1 本地规则为什么不能直接推荐手机或华为

本地能认出“小米”“华为”是品牌，但不能证明：

- 用户要买手机、平板还是别的产品；
- 这次负面体验最终对应手机、平板还是别的商品域；
- “或许”是强制只要华为，还是轻微倾向；
- 用户要的是同类替代品还是泛泛建议。

因此 `safety_proof` 必须失败：

```json
{
  "intent_hint": "recommend",
  "recognized_brand_mentions": ["xiaomi", "huawei"],
  "safety_proof": {
    "allowed": false,
    "reasons": [
      "no_explicit_product_domain",
      "brand_is_cross_category",
      "direct_negative_needs_product_domain_context",
      "tentative_word_或许"
    ]
  }
}
```

### 5.2 SemanticParse LLM 该返回什么

它不返回商品，也不返回已经生效的 hard 条件；它只返回带本轮原文位置的语义观察：

```json
{
  "action": "recommend_shopping_products",
  "category_resolution": {"value": null, "certainty": "unknown"},
  "brand_observations": [
    {"brand_family_id": "xiaomi", "polarity": "negative_experience", "strength": "medium", "scope": "unspecified_prior_product", "source_span": "小米，但我用着并不好"},
    {"brand_family_id": "huawei", "polarity": "tentative_preference", "strength": "weak", "scope": "future_purchase", "source_span": "或许还是华为适合我"}
  ],
  "clarification_required": true,
  "question": "可以。你想让我推荐手机、平板，还是其他产品？"
}
```

随后 HardConstraintPromotionGate 回到用户原文检查：`小米，但我用着并不好` 已唯一归一到 `xiaomi`，且是直接、无条件的负面表达，因此写入 `hard.exclude_brand_family_ids=[xiaomi]`；`或许还是华为适合我` 含“或许”，只写 soft prefer，不能写“只要华为”。即使 LLM 错误地把“或许华为”升级为硬包含，PromotionGate 也会拒绝，理由是 `hedged_positive`。

系统此时只发澄清问题，不调用 embedding，也不跑全库推荐。因为品类未定，检索空间都不知道是什么。

### 5.3 用户补充“手机，三千左右”之后

现在才形成可执行需求：`手机`是明确分类，进入 hard；`三千左右`含“左右”，是价格目标而不是严格上限，进入 soft price target；小米已是硬排除，华为仍是软正向。目录候选门、Milvus 召回前过滤和最终卡片都必须排除小米；排序可提高华为，但不能只允许华为。回答中可透明说明“已排除小米，并把华为放在优先考虑范围”。

用户若明确说“小米也可以”或“要小米”，PromotionGate 才删除 `xiaomi` 黑名单；若明确说“只要华为”，才写华为硬包含。同理，用户说“3000 元以内”才形成 hard price max；“3000 左右”只影响排序与展示价位。

### 5.4 如果此前会话已经在聊手机

若 SessionCore 中当前话题仍是手机，系统可以继承手机类目，不必再问买什么；“小米用着不好”会补充/维持小米硬排除，“或许华为适合”仍只是软正向，不能覆盖历史的硬品牌条件。历史小米黑名单只能由本轮明确“要小米/小米也可以”撤销。

## 6. 场景六：购物车两步确认——“加购”和“确认”不是同一个动作

用户说：`第二个 512G 能加购物车吗？`

系统先用之前的 focus 或目录确定为 `p_phone_205 / sku_205_512`。然后只生成计划：

```json
{
  "pending_action": {
    "id": "pa_001",
    "kind": "add",
    "items": [{"product_id": "p_phone_205", "sku_id": "sku_205_512", "quantity": 1}],
    "bound_session_id": "s_001",
    "expires_at": "2026-07-15T12:00:00+08:00"
  }
}
```

用户看到的是“将加购 Phone B 512G，请确认”。此时购物车还没有被写入。

只有用户在同一 session 的 60 秒内说“确认”，系统才再查一次库存和价格，随后写入。如果超时、用户改成“加第一个”或换了 session，旧 pending action 自动失效，必须重新做计划。

## 7. 场景七：从手机切到 PC——怎样避免旧条件串进新话题

用户说：`算了，给我配一台 8000 的主机`

这不是“再推荐一个手机配件”。系统产生 `TopicTransition=SWITCH`，新建 PC topic：

```text
active_topic: topic_phone_01 -> topic_pc_02
active_catalog_scope: ecommerce -> pc_parts
手机的 category/type、拍照偏好、手机 card 编号：不继承
新的硬条件：预算约 8000、PC 组件/用途条件
SessionCore.pc：保存当前方案 ID、已选配件 ID 和少量方案摘要
```

PC 链路可以用检索找候选 CPU/显卡描述，但最终必须由兼容规则校验，例如主板接口、内存代际、电源功率。它和手机推荐共用“先事实后语言”的原则，但不能共用同一套商品属性。

若用户之后说“刚才第二个手机再便宜点有吗”，前端最好带回原 card ID；否则 `recent_topics` 中必须唯一找到 `topic_phone_01` 的第二张手机卡，系统才允许 `RETURN_TO_RECENT`。它不能把 PC 方案里的“第二个配件”错当成手机第二张卡；只有“第二个”而没有产品域/卡片 ID 时，要请用户点卡片或说明对象。

### 7.1 闲聊、乱码不会偷偷切走商品话题

手机推荐后用户插入：`哈哈哈哈我今天好烦`、`asdf qwe` 或无关的闲聊。InputGuard 清洗后，TopicTransitionResolver 发现其中没有稳定 ID、受控商品动作、可归一实体，也不是未过期追问的合法答案，于是返回 `NOISE_OR_CHAT`：系统可以正常闲聊或提示未理解，但 `SessionDelta` 不修改预算、品类、黑名单、卡片索引或 pending clarification。

因此用户下一句 `第二个有 512G 吗？` 仍在 `topic_phone_01` 中解析；如果用户改为 `我想配主机`，才是明确 `SWITCH`，会作废手机的追问而新开 PC topic。若用户在新 topic 后只回了个“对”，没有有效 pending plan，系统不会把它接回旧手机追问。

## 8. 故障时，哪些请求还能继续，哪些必须停下来

| 故障 | 能继续做什么 | 正确表现 | 不能做什么 |
|---|---|---|---|
| 语义 LLM 不可用 | 精确卡片/SKU/购物车确认；“推荐手机”等完整 safety proof | 走本地确定路径 | 用关键词猜“给妈妈买简单的” |
| embedding/Milvus 不可用 | SKU/价格/参数/比较；可选的结构化兜底推荐 | 明确标记检索降级，仍过目录硬条件 | 从旧缓存返回无货/超预算/被排除品牌 |
| 回答 LLM 不可用 | 所有已验证结构化结果 | 用模板显示卡片和表格 | 丢弃结果或编造补充说明 |
| 价格/库存目录不可用 | 非商品事实的闲聊 | 对事实请求说明暂时无法确认 | 用上轮旧价格假装实时价格 |

任何降级都写进 TraceStore：哪一个模型失败、是否继续、最终是否使用了目录事实。SessionCore 不能因为半失败的推荐而写入一组不可靠卡片。

## 9. 前端收到事件的固定顺序

为了让前端和测试容易处理，所有分支都尽量按同一顺序发送 SSE：

```text
tool_call                 本轮动作和是否通过验证
progress（可选）          当前阶段，不泄露 prompt
recommendation_cards      推荐时的卡片
comparison_table          比较时的表格
pc_build_plan             装机时的方案
cart_preview              购物车待确认计划
answer                    对验证结果的文字说明
session_delta_committed   会话已更新
done
```

与商品有关的事件必须带 `request_id`、`catalog_version`；卡片带 `card_id/product_id/default_sku_id`；比较带 product ID 列表；购物车带 pending action ID 和过期时间。这样下一轮不需要从自然语言里重新猜对象。

## 10. 这份演练应变成端到端测试

至少要把以下断言写成自动测试：

1. “推荐手机”能得到商品级去重的卡片，并将 card ID 摘要写进 session；
2. “第二个 512G”不调 RAG，也能从目录返回正确 SKU、价格和库存；
3. 两卡夜景比较先生成目录字段表，缺字段不编造；
4. “不要小米”在目录候选、Milvus、最终卡片三处都为零命中；
5. “小米用着不好、或许华为适合”中，小米进入硬黑名单，华为只作软正向；品类未知时仍先澄清；
6. “不要小米”后明确“要小米”能正确撤销旧排除；
7. 购物车确认只在正确 session、正确对象、60 秒内生效；
8. 手机和 PC 的卡片、预算、品牌条件不会串话；
9. 闲聊/乱码前后当前商品 topic 的约束和卡片索引不变；明确切 PC 后旧手机条件绝不泄漏；
10. 任一模型故障时不会产生未经验证的商品事实。
