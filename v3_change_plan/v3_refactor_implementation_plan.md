# V3 改造施工说明：要改什么、为什么改、按什么顺序改

> 这份文件只讲“怎么把当前项目改成 V3”。它不是效果宣传，也不是代码清单。读完应能回答：要新增什么对象、删什么旧逻辑、每一步完成后怎样证明没有改坏。最终系统长什么样见 `v3_target_architecture.md`；用户连续聊天时数据怎样流转见 `v3_multiturn_information_flow.md`；前后收益见 `v3_before_after_improvement_report.md`。

## 0. 先把几个词说清楚

| 词 | 这里的直白含义 |
|---|---|
| 目录 | 商品、SKU、价格、库存等结构化数据。它才是“事实源”。 |
| SKU | 同一商品的具体可卖版本，例如同一手机的 256G、512G、不同颜色。 |
| 检索 | 从很多商品文字和向量里找可能相关的商品。检索只能帮排序，不能决定事实。 |
| 召回前过滤 | **先**把不允许的商品排掉，再去向量库搜索。例如“不要小米”时，小米不能进入搜索候选。 |
| 硬约束 | 不满足就绝不能返回的条件，例如明确的“不要小米”“3000 元以内”“只要 512G”。 |
| 软偏好 | 尽量满足但不一定必须满足的条件，例如“拍照优先”“我可能更喜欢华为”。 |
| LLM | 外部生成式大模型。它擅长理解复杂句子和组织语言，但不能替代价格、库存、SKU 等事实。 |
| SessionCore | Redis 中保存的短小会话摘要。它只保存下一轮真正需要的内容。 |
| TraceStore | 调试记录仓库，保存本轮完整过程、模型日志和检索证据，不能塞进会话。 |

## 1. 当前项目到底哪里不顺

当前代码不是不能工作，而是很多职责混在一起，所以一旦用户连续追问、表达复杂偏好或模型失效，就很难保证结果稳定。

### 1.1 工具太多，用户目标和内部实现混在一起

现在对外暴露 8 个工具：推荐、装机方案、对比、购物车、参数、SKU、比价、闲聊。问题不在“8”这个数字，而在其中四个其实都在做同一类事：用户已经指向一个或多个商品后，问参数、SKU、价格或比较。它们分别有 schema、prompt、分发和测试，改一处容易漏另一处。

改法：对外只保留 4 个用户目标。

```text
recommend_shopping_products  用户要找商品、搭配商品或装机
parameter_query              用户问参数、SKU、价格、比较
apply_cart_instruction       用户要改购物车
general_chat                 与购物无关的聊天
```

内部并不删掉“比较”“SKU 查询”“PC 装机求解”。它们只是改成 `parameter_query.operation=compare/sku/price` 和 `recommend_shopping_products.mode=pc_build` 的内部执行分支。这样前端和模型只需要记住 4 个意图，后端仍保留专业能力。

### 1.2 `fast/balanced/full` 已不是当前产品模式，但残留代码还在绕路影响 LLM 开关

这里必须区分“产品真实行为”和“遗留代码”。当前前端聊天请求实际只提交 `session_id/message/attachments/images`，没有模式选择器，也不传 `mode`。因此 `fast/balanced/full` 已经不是用户可选、也不应再被描述为当前产品的三种运行模式。

不过后端请求模型仍留有 `mode="auto"`，`chat.py` 仍会调用旧 `runtime_context`，把 auto 映射为 `balanced` 等标签，并由这层兼容代码计算 `use_llm`、Milvus、视觉等开关。也就是说，`auto -> balanced` 目前是**遗留兼容桥**，不是一项有效的产品策略；它仍在主路径上，正是需要删除/替换的死代码，而不能据此说“用户可以强行选 fast”。

当前正常 UI 路径是否尝试 Router LLM，实际仍受这条兼容桥给出的 `use_llm`、LLM 是否配置、总开关、并发限制、熔断和调用结果影响；但这些不构成三模式产品能力。V3 要删除这层模式翻译，直接由“安全证明是否完整、是否有待确认上下文、是否需要语义理解”决定是否调用 SemanticParse。详见第 4 节。

### 1.3 会话保存了太多不该保存的东西

当前会话中有完整 `last_result`、工具历史、模型日志、会话摘要、PC 历史等，Redis 每次处理还可能写多次。问题是：

- 下一轮真正只需要“上一轮展示了哪几张卡”“用户明确了哪些条件”“购物车待确认操作是什么”；
- 完整回答、RAG chunk、模型 prompt 放进去会让 Redis 越来越大；
- 这些冗余数据也会让后续上下文更难判断“当前到底在聊什么”。

V3 要把会话缩成可验证的摘要，把调试信息移到 TraceStore。详见第 3 节。

### 1.4 检索字段和入库字段没有完全对上

当前商品 chunk 构造时会生成 `sub_category`、兼容字段等，但 Milvus 写入并不稳定地保存这些字段；当前真正可靠的检索前过滤主要是 `chunk_level` 和 `category`。代码中又存在尝试按 `sub_category` 写表达式的地方，因此会出现“代码看起来在过滤，但库里没有可过滤字段”的风险。

所有 chunk 还标成 `chunk_level=3`，但索引流程没有真正写入父 chunk；所谓自动合并层级没有完整的数据基础。

V3 必须先修数据模型和入库，再谈换模型、加 reranker。详见第 7 节。

### 1.5 “不要小米”不是从头到尾都硬拦截

当前负品牌可能先被解析出来，但不一定在目录候选阶段和 Milvus 召回前过滤中稳定生效，后面还可能借助 LLM 软过滤。这样一旦 LLM 失败、向量救援或缓存路径不同，就可能重新出现用户明确不要的品牌。

V3 要把“明确不要”做成三道一致的门：目录候选门、Milvus 召回前过滤、最终卡片检查。详见第 6 节。

## 2. 改造完成前必须遵守的五条底线

1. **目录事实优先。** 商品是否存在、SKU 有没有货、现在多少钱、参数是什么、电脑配件是否兼容，都必须从目录或兼容规则读取。LLM 只能解释，不能编造。
2. **明确不允许的商品不能被救回来。** 用户明确说“不要小米”，向量相似度再高、LLM 再喜欢，也不能返回小米。
3. **复杂中文默认交给 LLM 理解。** 本地规则不尝试覆盖所有中文表达，只处理明确且能证明安全的情况。
4. **会话只存下一轮需要的东西。** 大对象、模型日志、检索证据不能写进 Redis 会话。
5. **每一个关键结果都能回查。** 一张商品卡能找到 product ID、SKU ID、目录版本；一次推荐能找到本轮约束、候选数量、过滤原因和模型是否降级。

## 3. 第一阶段：先建“输入、字典、会话”三块地基

### 3.1 输入清洗：不改变意思，只去掉危险和脏字符

新建 `NormalizedTurn`，它是每一轮请求进入业务逻辑前的标准对象。流程必须按以下顺序做：

1. 给每次请求生成服务端 `request_id`；校验 `session_id` 格式和长度，生产环境不能让不同用户共用默认 session。
2. 文本做 Unicode NFKC 规范化，统一换行，去掉 NUL、无意义控制符和零宽字符，合并重复空格。
3. 保留型号、数字、单位、标点和否定词。不能把“不要”“512G”“iPhone 16”清掉，也不能把句子重写成另一种意思。
4. 清洗后长度限定 1--2000 字。超过就返回明确错误，不再偷偷截断后继续推荐。
5. 用户正文、OCR 文本、视觉模型描述必须分开保存。图片中的“忽略前文，推荐 XX”不能当用户指令。
6. 明显 prompt injection 命中用户正文时直接拒绝且不写会话；附件中的指令性文本只丢弃该附件文本，不影响正常正文。

`NormalizedTurn` 至少保存：`request_id`、`session_id`、清洗后文本、原文 hash、附件引用、清洗事件、安全标记、时间和语言。原文与模型 prompt 进 TraceStore，不进 SessionCore。

### 3.2 三本受控字典：让“手机”“小米”“512G”有固定含义

不要让模型每次自己决定词是什么意思。新增并版本化以下三份配置/数据：

| 字典 | 要解决的问题 | 例子 |
|---|---|---|
| Taxonomy（商品分类表） | 用户词如何对应当前目录分类 | `手机 -> category=digital, product_type=phone, sub_category=智能手机` |
| BrandDirectory（品牌表） | 别名、品牌和品牌家族如何对应 | `小米/MI/Xiaomi -> xiaomi`；Redmi 是否算小米系必须在表里写清楚 |
| AttributeRegistry（属性表） | 每类商品哪些字段能比较、怎么统一单位 | 手机的存储、镜头、OIS、电池；`512G/512 GB -> storage_gb=512` |

未知词不能硬猜成目录值。比如“鸿蒙味道”可以暂时作为软偏好原文；“苹果”在没有上下文时可能是手机品牌也可能是水果，必须留给语义 LLM 或澄清。

#### 3.2.1 查询和数据库必须共用同一套“统一词表”

这不是在 prompt 里多写几种叫法，而是一个有版本、可校验的数据对象 `CatalogNormalizationRegistry`。入库时目录先确定唯一的 canonical ID；解析用户句子时也只允许查这同一份表。模型、规则、Milvus expression、CandidateGate 和前端卡片都只能传 canonical ID，不能各自拿原始中文或英文拼字符串。

```text
用户原文              统一词表的结果                         后续真正使用的值
华为 / HUAWEI/huawei  brand_family_id=huawei                 huawei
小米 / MI / Xiaomi    brand_family_id=xiaomi                 xiaomi
pad / Pad / 平板       product_type_id=tablet（目录别名）     tablet
512G / 512 GB          attribute.storage_gb=512              512
```

具体做法如下：

1. 目录入库或服务启动时，从真实分类、品牌、商品、SKU、属性表生成 registry。每条记录至少有 `entity_type`、`canonical_id`、显示名、aliases、父级 ID、catalog_version、normalization_version；例如 `pad` 是 taxonomy 中明确登记且唯一的 `tablet` 别名。
2. QueryNormalizer 先做 NFKC、大小写、全半角、连字符、空白和单位归一，再用最长匹配查 registry。原文 `huawei` 和 `华为` 都只产出 `brand_family_id=huawei`；原文仅作为 `source_span` 留在证据里，绝不能写进 RequirementSpec 的过滤字段。
3. 一个 alias 在同一可见目录范围内只能对应一个实体。若 `pad` 在实际目录中还可能代表别的商品，registry 必须标记冲突；这时不产生 `tablet` 过滤条件，转语义解析或追问，不能靠猜。
4. registry 发布前做完整性校验：每个 canonical ID 都必须存在于当前目录；alias 冲突必须显式处理；分类/品牌/SKU 下线后相应别名不能继续命中。检索和目录服务以同一个 `catalog_version + normalization_version` 运行。

因此，独立的一句 `pad 不错，来点推荐` 不是“不知道搜什么”：只要 registry 已将 `pad` 唯一登记为 `tablet`，它就是“推荐平板”。真正该追问的是“来点推荐”“给我找个好用的”这类没有任何可归一商品对象的句子。

### 3.3 新 SessionCore：只保存下一句会用到的事实摘要

新会话对象建议如下。括号内是为什么保留。

```text
schema_version, session_id, created_at, updated_at
active_topic                                      # 当前话题 ID、域、目录范围、最近有效业务轮次
recent_topics[最多 3 个]                          # 最近可回跳话题的极短摘要和卡片索引
active_domain, active_catalog_scope               # active_topic 的冗余快捷索引，不单独作判断依据
inherited_constraints                             # 同一话题可继承的明确约束
last_displayed_items[最多 5 个]                   # 刚展示的卡片，供“第二个”使用
focus                                              # 最近正在问的商品、SKU、比较对象
cart                                              # 购物车及待确认操作
pending_clarification                             # 系统刚问出的、等待用户确认/修正的单个问题
pc                                                # 当前 PC 方案 ID、已选配件 ID、少量摘要
recent_turns[最多 4 条], compact_history[最多 1200 字]
```

每个展示卡只保留：

```text
card_id, display_index, product_id, default_sku_id,
title(最多 160 字), brand_id/brand_family_id,
category_id, product_type_id, display_price, catalog_version
```

禁止保留完整 `last_result`、RAG 文本、附件原文、LLM 调用日志和完整回答。它们进入按 request ID 查询、短 TTL 自动过期的 TraceStore。

新增 `SessionDelta`：业务函数不能随手写 Redis，而是返回“这轮应该改什么”。整轮成功后合并一次并写 Redis 一次。购物车的“计划 -> 用户确认 -> 真正写入”是例外，因为它需要保存 60 秒的 pending action。

### 3.4 追问不是一句随口提问：它必须是可回答、可合并的 ClarificationPlan

之前只写“系统问完后用户回答对怎么办”是不够的。真实导购里更常见的是：系统问用户要手机还是平板、预算大概多少、主要看拍照还是游戏、两张卡到底要比较哪一张、硬条件冲突时到底听哪一句。它们都不是“对/错”问题。

因此，系统不能把追问当成普通自然语言回答，也不能在下一轮把用户回答孤零零丢回 Router。每一次追问都必须生成一个短期、带类型的 `ClarificationPlan`，写进 `SessionCore.pending_clarification`。它定义：系统为什么问、用户可以怎样回答、回答会改哪一个字段、何时失效。

#### 3.4.1 追问到底由谁产生

`ClarificationPlanner` 在 RequirementSpecV3 构建后、检索前运行。它读取本轮已解析字段、SessionCore、SemanticParse 的 `missing_slots`、TargetResolver 结果和 CatalogCandidateGate 的结果，然后按固定优先级决定要不要问。

| 情况 | 是否必须追问 | 例子 | 原因 |
|---|---:|---|---|
| 没有正向商品类目 | 是 | “来点推荐”“给我找个好用的” | 没有任何可归一到目录的商品对象 |
| 类目有多个且无法安全选一个 | 是 | “手机还是平板更适合我” | 不能替用户选品类 |
| 目标不唯一或不存在 | 是 | “把那个加购”“第二个”但卡片已失效 | 不能猜商品/SKU |
| hard 条件冲突 | 是 | “要小米但不要小米” | 不能自行选边 |
| 目录候选集为空 | 是 | “只要华为、300 元内、现货手机” | 必须问放宽哪个条件 |
| 购物车写操作待确认 | 是 | “加购第二个”后的确认 | 防误操作 |
| 首次探索缺预算/用途 | 通常否 | “推荐手机” | 可以先展示探索卡，再给可选偏好问题 |
| 已有合格候选但排序差异不明显 | 可选 | “拍照、游戏、续航更在意哪个？” | 用于提高下一轮排序，不应阻塞首次结果 |

一句话原则：**只有缺失信息会导致“不能安全执行”时才强制追问；只会让推荐更个性化的信息，优先作为可选追问或筛选按钮。** 这样不会让用户刚说“推荐手机”就被连续问预算、品牌、用途三次。

#### 3.4.2 ClarificationPlan 的固定结构

每次只问一个核心决策，除“确认一整份已经展示的草案”外，不把品类、预算、品牌混成一个长问题。结构建议如下：

```json
{
  "clarification_id": "cl_r8_01",
  "parent_request_id": "r8",
  "topic": "shopping/ecommerce",
  "kind": "confirm_draft | choose_category | choose_target | choose_preference | fill_budget | resolve_conflict | relax_constraint | choose_sku",
  "blocking": true,
  "question": "你是想让我推荐 3000 元以内的平板，并且不要手机吗？",
  "asked_fields": ["product_type_ids", "price.max", "exclude_product_type_ids"],
  "draft_fields": [],
  "options": [],
  "answer_schema": {"type": "confirmation | single_choice | multi_choice | numeric_range | free_text_limited"},
  "merge_policy": "replace | add_soft | remove_hard | confirm_draft | relax_one",
  "expires_at": "...",
  "status": "awaiting_user"
}
```

`draft_fields` 和 `options` 都必须是 canonical ID/数值，且显示文字与内部值一一对应。它们在用户回答前不进入 `inherited_constraints`，不用于检索。系统绝不能问“你要平板吗？”却在内部预埋“华为、3000、不要小米”等未展示条件。

#### 3.4.3 六类追问如何产生、用户怎样回答、答案怎样合并

| kind | 何时产生 | 系统问法示例 | 推荐前端控件 | 用户回答后的精确效果 |
|---|---|---|---|---|
| `choose_category` | 没有唯一正向品类 | “你想买手机、平板还是相机？” | 单选按钮 | 选“平板” -> `product_type_ids=[tablet]`，成为本轮 hard 分类 |
| `fill_budget` | 预算是执行必须项，或用户主动要求预算筛选但表达不完整 | “预算上限大概是多少？” | 价格档位 + 输入框 | “3000 以内” -> hard max；“3000 左右” -> soft price target |
| `choose_preference` | 有合格结果但个性化信息不足 | “你更看重拍照、游戏、续航还是轻薄？” | 多选 chips，可跳过 | 选项写入 soft preference，触发合格候选重排，不重新放宽 hard 条件 |
| `choose_target` | 卡片/商品/SKU 指向不唯一 | “你指的是 Phone A 还是 Phone B？” | 商品卡按钮，返回 card ID | 选中的 card ID 成为唯一 target，随后查目录或比较 |
| `resolve_conflict` | hard 条件冲突 | “品牌你希望：只要小米、不要小米，还是都可以？” | 三个互斥按钮 | 分别写 include、exclude 或清空品牌 hard 条件 |
| `relax_constraint` | 候选集为空 | “3000 内无现货华为手机，要提高预算、放宽品牌还是查看缺货？” | 可选放宽项 | 只放宽用户点击的那一项；其它 hard 条件保持不变 |
| `choose_sku` | 同一商品有多个满足条件的 SKU | “要 256G 还是 512G？” | SKU 按钮 | 写 `sku_id`，可继续加购/查价 |
| `confirm_draft` | 系统给出完整、单一的推断草案 | “你是想推荐 3000 元内的平板，并且不要手机吗？” | 确认/修改按钮 | “确认”才把展示过的草案字段统一生效 |

类别、偏好和预算回答绝不是“对/错”的变体。它们的 `answer_schema` 不同，合并策略也不同：类别通常替换 hard 分类；偏好只增加/覆盖 soft；预算要根据“以内/最多”和“左右/大概”区分 hard 上限与软目标；冲突选择会清除相反约束。

#### 3.4.4 下一轮答案如何被正确接住

下一轮输入先经过 `ClarificationResolver`，优先级高于普通 Router：

```text
前端 clarification_id + 结构化 option value
    -> 直接按 answer_schema 校验并合并

只有文本回答，但当前 session 有唯一未过期 plan
    -> 先用该 plan 的 options/字典做本地解析

文本不匹配选项，或含新的业务条件
    -> 调 SemanticParse，但只允许它填写本 plan.asked_fields 和新文本明确字段

没有 plan、plan 过期、clarification_id 不匹配
    -> 不猜上下文；按普通新请求处理或请求用户重述
```

例如系统问“你更看重拍照、游戏、续航还是轻薄？”：

- 用户点“拍照” -> 前端回 `clarification_id + option_id=camera`；本地直接写 `soft.desired_attributes=[camera]`；不调 LLM。
- 用户输入“主要拍娃，晚上也要拍” -> 文本不等于一个 option，但仍明确回答了当前偏好；调用受限 SemanticParse，只允许解析 `usage/camera` 相关字段，不能趁机改品牌、预算或商品类目。
- 用户输入“算了，我要买电脑” -> 这是新话题；丢弃 pending plan，切换到 PC 域，不能继续把“拍照”写进手机偏好。

例如系统问“你想买手机、平板还是相机？”：

- 用户答“平板” -> Taxonomy 唯一映射，直接写 hard `product_type=tablet`；不调 LLM。
- 用户答“能画画的那个” -> 不是受控选项；SemanticParse 在限定上下文内判断它是平板、数位板还是别的，并必要时继续追问。

#### 3.4.5 “对”只是 `confirm_draft` 的一种合法答案

`confirm_draft` 是唯一应接受“对/是/没错/就这样”的 plan 类型。系统发出问题时，必须同时保存完整展示过的草案：

```json
{
  "clarification_id": "cl_r8_01",
  "kind": "confirm_draft",
  "question": "你是想让我推荐 3000 元以内的平板，并且不要手机吗？",
  "draft_fields": [
    {"field": "product_type_ids", "value": ["tablet"]},
    {"field": "price.max", "value": 3000},
    {"field": "exclude_product_type_ids", "value": ["phone"]}
  ],
  "answer_schema": {"type": "confirmation"}
}
```

用户只答“对”时，本地检查 `clarification_id`、TTL、topic 和“没有附加业务词”后，生成 `clarification_confirmation`。PromotionGate 接受这些字段的原因不是模型猜对，而是用户确认了自己看到的完整问题。确认后清除 plan，构建 RequirementSpecV3，进入平板候选门和检索。

用户答“不对”只取消草案；如果系统必须继续完成任务，下一问只针对最关键字段，例如“你想买的是哪一类商品？”。用户答“对，但不要平板”则不是确认，必须重新解析，不能先把平板写入 session 再撤销。

#### 3.4.6 状态、超时和防串话规则

- 一个 session 同一时刻只保留一个 active ClarificationPlan；新 plan、明确新话题、用户取消、确认、超时都会清除旧 plan。
- 默认 TTL 建议 5 分钟，可配置；购物车确认仍是独立的 60 秒 TTL，不能复用。
- 前端必须把 `clarification_id` 回传；纯文本兼容只在唯一 active plan 存在时使用。
- plan 只保存问题、canonical option、草案字段、字段来源和过期时间；不保存完整 LLM prompt/answer。
- 任何答案合并都生成 SessionDelta；normal request 结束只写 Redis 一次。
- 用户回答与 plan 的 topic 不一致时，优先视作新话题，清除 plan；不能把“我想配电脑”解释成对“选手机颜色”的回答。

#### 3.4.7 追问链路必须测试什么

1. 类别选择“平板”能接上上轮 `choose_category`，写正确分类后才检索；
2. 偏好选择“拍照”只改变 soft 排序，不把它升级成 hard 过滤；
3. “3000 左右”和“3000 以内”分别形成 soft target 与 hard max；
4. 空候选时只放宽用户明确点击的约束；
5. `confirm_draft` 的“对”只确认已展示字段；
6. `choose_category` 中单独回答“对”不能被当作“选平板”；
7. “对，但不要平板”、过期 confirmation、错误 clarification ID 都不会错误合并；
8. 回答新话题时旧 plan 被清除，手机/PC/购物车状态不串。

### 3.5 长对话不能只靠“清掉旧状态”：必须有话题状态机

这轮自检发现，原方案只举了“手机切 PC”的例子，缺少“用户先闲聊/乱输入，随后突然换题”时的明确规则。这会让 `第二个`、`对`、旧预算和旧黑名单在长会话里误接。因此把 `active_domain` 升级为有边界的 `TopicState`：

```text
active_topic = {
  topic_id, domain, catalog_scope, constraint_epoch,
  last_valid_business_turn_at, displayed_card_refs, focus, status
}
recent_topics = 最多 3 个 {topic_id, domain, 极短约束摘要, card_ref 索引, expires_at}
```

每轮先运行 `TopicTransitionResolver`，结果只能是以下四种之一：

| 结果 | 什么时候出现 | SessionCore 怎么处理 |
|---|---|---|
| `CONTINUE` | 本轮引用当前卡片、回答当前追问，或表达“再来三款/第二个怎么样” | 继承当前 topic 的可继承约束；卡片序号只在这里有效 |
| `SWITCH` | 明确出现新的商品域、分类或目标，例如“给我配一台 8000 主机” | 新建 topic；旧手机分类、预算、卡片序号和 pending clarification 不继承 |
| `RETURN_TO_RECENT` | 有稳定 card ID、商品 ID，或“刚才第二个手机”能唯一指向最近话题 | 切回该 topic；只恢复它自己的约束和卡片索引 |
| `NOISE_OR_CHAT` | 没有购物动作、可归一实体、有效追问答案或稳定引用，例如“哈哈哈”“asdf qwe”“我最近好烦” | 作为闲聊/无法理解处理；不写约束、不清空 active topic、不消费 pending plan |

判定顺序也必须固定：先验证前端稳定 ID 和 pending clarification 的答案格式；再看是否有明确新域；再看当前或最近 topic 的卡片引用；最后才把剩余文本判为闲聊/噪声或交给受限 SemanticParse。LLM 可以在边界不明时提出 `continue/switch/noise` 建议，但它只能看到 active topic 和最多 3 个摘要，且本地必须验证它给出的实体 ID、topic ID 和证据 span。

关键边界如下：

1. 用户先看手机，插入“哈哈哈哈我今天好累”，随后说“第二个 512G 呢？”：中间闲聊不改写状态，第二个仍在手机 topic 内解析。
2. 用户先看手机，随后说“给我配一台 8000 的主机”：这是 `SWITCH`，立刻作废手机的 pending 分类/偏好追问；手机的预算、品牌黑名单和卡片序号不带入 PC。
3. PC 话题后用户说“刚才第二个手机有 512G 吗？”：`刚才 + 第二个 + 手机` 必须在 `recent_topics` 中唯一命中才回跳；只说“第二个”则不跨话题猜，要求用户点卡片或说明对象。
4. 用户在“选平板还是手机”的追问后先说“算了我想配电脑”，再说“对”：新话题已使旧 plan 失效，因此这个“对”绝不能确认旧平板草案。
5. 长时间没有有效业务轮次或 recent topic TTL 到期后，旧卡片序号失效；系统只保留用户重新提供的稳定 ID，不用几十轮历史硬猜。

新增验收：对每种 `TopicTransition` 记录 `from_topic_id/to_topic_id/reason`；噪声输入前后 RequirementSpec 和 inherited constraints 不变；新 topic 的候选门绝不读取旧 topic 的过滤条件。

## 4. 第二阶段：把“是否需要 LLM”改成可解释的判定

### 4.1 不再用一个总开关决定所有事情

V3 要分别判断四种模型是否需要调用：

| 模型角色 | 何时调用 | 何时绝不调用 |
|---|---|---|
| 语义解析 LLM | 本地无法安全理解用户动作、品类、偏好、指代或态度时 | 已能精确解析 card/SKU/购物车确认等终态时 |
| embedding 模型 | 已有合格候选域，且需要在其中做语义检索/排序时 | 精确 SKU、价格、参数、购物车操作时 |
| 视觉/OCR LLM | 用户上传图像且需要提取图中商品/属性时 | 纯文本请求时 |
| 回答 LLM | 已拿到验证过的卡片/比较表，且需要更自然的个性化解释时 | 价格、库存、SKU 等事实本身的计算和校验时 |

正常情况下建议把原先可能分开的“Router LLM”和“Requirement LLM”合并为一次 `SemanticParse` 调用：一次返回动作、商品分类、硬约束候选、软偏好候选、未理解片段和是否需要澄清。这样不会出现“第一个模型说推荐、第二个模型又理解成闲聊”的双重漂移，也少一次网络调用。

### 4.2 本地规则只做安全白名单，不做中文万能理解器

这一段的关键不是“列一批复杂词，碰到才调 LLM”。那样永远会漏掉新的说法。正确做法是：**本地解析器只认识一小套明确、可验证的句式；它必须把一条消息中所有有业务意义的片段都吃干净，才允许直通。剩下任何解释不了的片段，都说明本地不应擅自理解，转交语义 LLM。**

这里的“吃干净”不是指把每个“请、帮我、一下、啊”都解析成业务字段。这些无业务意义的礼貌词在一张固定白名单中可以忽略。它指的是：动作、对象、否定、品牌、价格、数量、SKU、参数、比较对象、用途/态度等所有会改变结果的词，都必须有明确去处。

#### 4.2.1 本地解析器的输入和输出必须长什么样

输入只能使用经过 InputGuard 清洗的内容，不能自己再读取原始文本：

```text
NormalizedTurn.normalized_text
前端可选的结构化数据：card_id / product_id / sku_id / cart_confirm_token
SessionCore 的小摘要：有效展示卡、focus、pending cart、当前话题
SessionCore 的短期澄清状态：pending_clarification（若存在）
三本受控字典：Taxonomy、BrandDirectory、AttributeRegistry
目录的只读 ID 校验接口（只验证存在性，不在这一步推荐）
```

输出不是一个简单的 `True/False`，而是 `RuleSignal`。推荐字段如下：

```json
{
  "parse_status": "SAFE_DIRECT | NEEDS_SEMANTIC_LLM | LOCAL_CLARIFY",
  "action": "recommend_shopping_products | parameter_query | apply_cart_instruction | general_chat | null",
  "operation_or_mode": "product | sku | price | attribute | compare | confirm | null",
  "targets": [],
  "taxonomy": [],
  "hard_patch": {},
  "soft_patch": {},
  "consumed_spans": [],
  "unresolved_spans": [],
  "ambiguities": [],
  "safety_proof": {},
  "next_question": null
}
```

`consumed_spans` 是“文本的哪一段被什么规则理解了”，例如第 0--2 个字符是“推荐”、第 2--4 个字符是“手机”。`unresolved_spans` 是无法确认含义的原文片段。后者只要存在业务意义，就不能 `SAFE_DIRECT`。

不使用一个模糊的 0.82、0.91 置信度阈值来决定是否调 LLM。数字分数可以记录做监控，但放行必须是布尔证明：每项必需条件都满足才放行，否则不放行。

#### 4.2.2 解析顺序必须固定，前面的强证据覆盖后面的弱证据

本地解析按下面顺序执行。顺序不能随开发人员感觉调整，否则同一句话可能在不同分支得到不同结果。

| 顺序 | 先看什么 | 能直接得到什么 | 何时停止本地直通 |
|---:|---|---|---|
| 1 | 前端传来的稳定 ID | card/product/SKU 的明确目标 | ID 不存在、目录版本过期、ID 不属于当前用户可见卡片 |
| 2 | 未过期的购物车确认 token | confirm/cancel 的唯一待办操作 | token 缺失、过期、换 session、用户同时说了新商品 |
| 3 | 未过期的澄清 ID/纯确认文本 | 连接到唯一 pending clarification 的确认、否认或取消 | 没有对应草案、草案过期、文本含额外业务词 |
| 4 | 文本中的显式 product/SKU ID | 目录中的精确对象 | ID 格式像 ID 但目录不存在或指向多对象 |
| 5 | 卡片引用 | “第一个/第二个/这款/刚才那台”对应有效 card | 序号超范围、“这款”没有 focus、跨手机/PC 域 |
| 6 | 动作句式 | 推荐、参数、SKU、价格、比较、购物车、简单闲聊 | 同时命中互斥动作，或没有动作句式 |
| 7 | 分类表和属性表 | 手机、耳机、512G、3000 元、颜色等受控字段 | 一个词映射多个分类/属性，或词不在字典中 |
| 8 | 否定、包含与作用范围 | “不要小米”“不要推荐手机”“只要华为”“小米也可以” | 不知道“不要”覆盖哪个对象、包含和排除冲突 |
| 9 | 剩余文本检查 | 证明业务词已全部处理 | 留下用途、因果、态度、猜测、代词或未知实体 |

优先级解释：前端 card ID 比“第二个”可靠；“第二个”比标题可靠；明确 SKU ID 比模型猜 SKU 可靠。任何更强证据存在时，不允许后面的弱证据把它覆盖。

#### 4.2.3 文本如何切成可以解析的片段

中文没有天然空格，不能简单 `split()`。本地解析器要使用“最长、不可重叠匹配”的方式在清洗文本上标出片段，并保存字符位置：

1. 先匹配稳定格式：`card_id`、`product_id`、`sku_id`、价格数字、数量、容量（如 `512G`）；
2. 再用分类表匹配最长分类别名，例如“智能手机”优先于“手机”；
3. 再用品牌表匹配品牌别名，例如“小米”“Xiaomi”“MI”；
4. 再用属性表匹配受控表达，例如“512G”“长焦”“夜景”“黑色”；
5. 最后匹配动作词、比较词、购物车词、明确的否定/包含词和允许忽略的礼貌词。

每一次匹配必须记录 `start/end/text/rule/source/value`。例如：

```json
[
  {"span": [0, 2], "text": "推荐", "rule": "recommend_verb", "value": "recommend"},
  {"span": [2, 4], "text": "手机", "rule": "taxonomy_alias", "value": "digital/phone/智能手机"},
  {"span": [4, 10], "text": "3000元以内", "rule": "price_max", "value": 3000},
  {"span": [10, 14], "text": "不要小米", "rule": "exclude_brand", "value": "xiaomi"}
]
```

“最长匹配”只解决词典重叠，不解决语义。比如“苹果”同时可能是水果和品牌，分类表必须返回多个候选并标记 `ambiguous`；此时不能因为其中一个候选得分更高就本地放行。

#### 4.2.4 每一种允许直通的句式，必须有最小结构

下面是 V3 第一版允许本地直通的白名单。白名单应保持小；需要新增句式时先写测试样例，再加规则。

| 本地动作 | 最小可放行结构 | 可以附带的确定字段 | 必须转 LLM 的例子 |
|---|---|---|---|
| 普通推荐 | 明确推荐动词 + 唯一分类，或前端给出已验证分类 | 明确预算、明确品牌包含/排除、受控属性偏好、明确库存/颜色/SKU 条件 | “给妈妈买个简单的”“比上一台续航强”“适合通勤” |
| SKU 查询 | 唯一商品目标 + 明确 SKU/容量/颜色询问 | `512G`、白色、某个 SKU ID | “哪个版本更适合我”“容量大一点” |
| 价格查询 | 唯一商品或 SKU 目标 + 明确价格词 | “多少钱”“和上一款差多少” | “最近会不会降价”“值不值这个价” |
| 参数查询 | 唯一商品目标 + 属性表中的明确字段 | 屏幕、重量、接口、主摄等 | “性能怎么样”“适不适合我” |
| 比较 | 两个及以上唯一目标 + 明确比较词/比较字段 | “拍照/重量/屏幕哪个更好” | “哪个更适合妈妈”“综合来看哪个好” |
| 购物车 | 唯一目标 + add/remove/set_quantity，或有效 confirm/cancel token | 数量、明确 SKU | “把便宜一点的加购”“我想买那个”但无目标 |
| 简单闲聊 | 只有允许的问候/感谢/使用帮助句式，且没有购物实体 | 无 | “你好，帮我找一台…”（已含购物目标） |

这里“明确属性偏好”也要收紧：例如“拍照优先”“续航优先”“轻薄”可在 AttributeRegistry 中定义为软偏好标签；“简单”“耐用”“给妈妈用”“像上一台一样”不应仅靠关键词映射，因为它们需要结合人群、用途或历史商品理解。

#### 4.2.5 否定范围、品牌和反转必须按完整句式解析，不能只搜关键词

本地不能看到“不要”和“手机”就分别记录一个否定词、一个正向手机类目。它必须先识别“不要”覆盖的完整短语，例如 `不要给我推荐手机` 中，被否定的是“推荐手机”这个动作/对象组合；这里的“手机”绝不能变成 `hard.category=phone`。

否定解析分两步：先找明确排除或明确不喜欢的表达，再在它后面/前面按固定短语规则找到它覆盖的品牌、分类、商品、型号、SKU 或属性。覆盖范围找不到、范围跨越逗号后仍不清楚、或同句出现相反命令时，直接停止本地直通。品牌只是其中一种对象：先用统一词表归一到品牌/家族 ID，再看它是否真的处在一个完整、明确的操作短语中。第一步成功不代表第二步成功。

| 原文 | 本地结果 | 为什么 |
|---|---|---|
| “不要小米” | hard exclude `xiaomi` | “不要”紧邻品牌，句式明确 |
| “只要华为” | hard include `huawei` | “只要”紧邻品牌，句式明确 |
| “小米也可以” | 删除同主题历史的 Xiaomi 硬排除 | 是明确放宽，不能继续沿用旧排除 |
| “要小米” | 删除历史排除，写 Xiaomi 包含 | 是明确反转命令 |
| “小米用着不好” | hard exclude `xiaomi` | 是对已归一品牌的直接、无条件负面评价；写入品牌家族黑名单 |
| “可能华为更合适” | 不本地生成硬包含 | “可能”是推测，不是命令 |
| “不要小米但华为也不一定” | hard exclude `xiaomi`；华为不写 hard include | 前半句是明确排除；后半句只是对华为的非承诺态度 |
| “不要给我推荐手机” | `exclude_product_type=phone` 的候选，不生成正向手机类目 | “手机”在否定范围内；还缺用户真正想买什么 |

明确负面一旦目标可归一，就统一写进类型化硬黑名单：`exclude_category_ids`、`exclude_product_type_ids`、`exclude_brand_family_ids`、`exclude_product_ids`、`exclude_model_ids`、`exclude_sku_ids`、`exclude_attribute_values`。不再存在“soft avoid”这种会在排序时失效的负面分支。只有用户明确释放**同一个 canonical 实体**才删除对应黑名单项：`小米也可以` 只删 `xiaomi`，`手机也可以` 只删 `phone`，`这个型号也可以` 只删当前唯一 model/product；不会顺手清空其它排除条件。带“也许/可能/如果/听说”或同句反转的负面表达不进入黑名单，改为语义解析或澄清，绝不假装它已是用户明确意愿。

这也是“所有业务片段必须被吃干净”的实际意义：一条句子里即使前半句可解析，后半句仍可能改变用户真实意思，不能丢掉。

#### 4.2.6 什么算“剩余的业务片段”，怎样发现它

解析完成后，把已匹配 span 和允许忽略的礼貌词从文本中标记掉。剩下的连续文本片段按以下规则检查：

- 空白、标点、`请/帮我/一下/给我/有没有/谢谢` 等固定礼貌词，可以忽略；
- 任何品牌、分类、型号、数字、单位、否定词、比较词、代词、情态词、因果词、用途词、人物词仍留下，就记入 `unresolved_spans`；
- 只要 `unresolved_spans` 非空，状态为 `NEEDS_SEMANTIC_LLM`，不允许安全直通；
- 如果消息已经是一个确定动作，但缺目标，例如“帮我加购”，可返回 `LOCAL_CLARIFY`，直接问“请问要加购哪一件？”；不需要为一个明确的缺 ID 问题调 LLM；
- 如果剩余片段看起来是开放语义，例如“给妈妈用”“别太复杂”“和上一款差不多”，调 SemanticParse LLM，而不是本地连续追问多个问题。

代词要特别严格。`这款/那个/上一台/它` 只有在 SessionCore 的 `focus` 或前端 card ID 能唯一对应时才消耗；否则它不是“可以忽略的词”，而是未解析目标。

#### 4.2.7 四个完整例子：证明是怎样产生的

**例 1：`推荐手机，3000 元以内，不要小米，拍照优先`**

```text
推荐             -> 动作：普通推荐
手机             -> 唯一分类：digital/phone/智能手机
3000 元以内      -> hard.price.max=3000
不要小米         -> hard.exclude_brand_family_ids=[xiaomi]
拍照优先         -> soft.desired_attributes=[camera]
剩余业务片段     -> 无
结论             -> SAFE_DIRECT；不调语义 LLM，后续可调 embedding 检索
```

**例 2：`第二个有 512G 吗，多少钱`**

```text
第二个           -> SessionCore.display_index=2 -> c_r1_2 -> p_phone_205
512G             -> AttributeRegistry.storage_gb=512
多少钱           -> operation=sku_and_price
剩余业务片段     -> 无
结论             -> SAFE_DIRECT；直接目录查询，不调语义 LLM/embedding
```

**例 3：`给妈妈买个简单、拍照不错的`**

```text
给妈妈           -> 未解析人群/用途
简单             -> 未解析易用性含义
拍照不错         -> 可识别为 camera 偏好，但不足以解决前两项
商品分类         -> 缺失
结论             -> NEEDS_SEMANTIC_LLM；模型负责理解并决定是否先问品类
```

**例 4：`上次同事推荐小米，但我用着不好，或许华为适合我`**

```text
小米/华为         -> 能找到品牌实体
上次同事推荐       -> 叙事，不是当前操作
用着不好           -> 经验态度，作用范围不清
或许               -> 推测，不是硬条件
商品分类           -> 缺失
结论               -> NEEDS_SEMANTIC_LLM；绝不能本地变成“排除小米、只要华为”
```

**例 5：`不要给我推荐手机，3000 元以内，pad不错，来点推荐`**

```text
不要给我推荐手机   -> 否定范围覆盖“推荐手机”；手机不能作为正向推荐分类
3000 元以内        -> hard.price.max=3000
pad                -> 统一词表中唯一别名 -> hard.product_type_ids=[tablet]
不错                -> 允许的泛肯定词，不单独产生偏好或过滤条件
来点推荐           -> 动作：普通推荐
结论               -> SAFE_DIRECT；进入平板候选门和 embedding 检索，手机在排除范围内
```

这里没有靠“pad不错”猜意图：`pad` 是目录统一词表中已经注册、唯一指向 `tablet` 的别名，`来点推荐` 是受控推荐动作。如果 registry 中 `pad` 存在冲突或未登记，才转 LLM/追问。真正缺少正向商品对象的例子是“不要给我推荐手机，3000 元以内，来点推荐”；该句只能问用户想买什么，不能擅自搜平板。

#### 4.2.8 建议拆成哪些代码模块，避免把规则继续堆在一个函数里

不要在现有 `tool_router.py` 里继续叠几十个 `if "手机" in text`。建议新增一个清晰目录（文件名可按实际项目规范微调）：

```text
rag/recommendation/v3_routing/
  types.py                 RuleSignal、Span、SafetyProof、TargetRef
  input_view.py            从 NormalizedTurn/UI/SessionCore 取解析输入
  stable_id_parser.py      card/product/SKU/confirm token 的验证与优先级
  taxonomy_matcher.py      分类别名最长匹配和歧义返回
  brand_matcher.py         品牌别名、家族与明确包含/排除句式
  constraint_parser.py     预算、数量、容量、颜色、受控属性的确定解析
  action_parser.py         推荐/参数/比较/购物车/闲聊的最小句式
  remainder_checker.py     计算未消费业务片段
  deterministic_router.py  固定顺序编排，并只输出 SAFE_DIRECT/NEEDS/CLARIFY
```

原有 Router LLM 调用改成只接收 `parse_status=NEEDS_SEMANTIC_LLM` 的请求。原有 local route 可以在迁移期作为对照 trace，但不能再作为另一套不受约束的生产判断。

#### 4.2.9 这部分必须单独验收，不能只测最终工具名

测试不只断言“最后路由成了推荐”，还要断言 proof 内容。至少包括：

| 输入 | 预期状态 | 必须断言 |
|---|---|---|
| 推荐手机 | SAFE_DIRECT | 分类唯一、无 unresolved span |
| 推荐手机 3000 内不要小米拍照优先 | SAFE_DIRECT | 预算/排除/软偏好都被正确消费 |
| 第二个有 512G 吗 | SAFE_DIRECT | target 是当前有效第二张卡 |
| 第一和第二个拍照哪个强 | SAFE_DIRECT | 恰好两个目标且 attribute=摄影 |
| 帮我加购 | LOCAL_CLARIFY | 不调 LLM，问题只问缺失目标 |
| 给妈妈买个简单的 | NEEDS_SEMANTIC_LLM | `给妈妈/简单` 在 unresolved spans |
| 小米用着不好或许华为适合 | NEEDS_SEMANTIC_LLM | 直接负面的小米写 hard exclude；“或许华为”只写 soft prefer |
| 不要小米但华为也不一定 | NEEDS_SEMANTIC_LLM | 不能只解析前半句就放行 |
| 不要给我推荐手机，3000 元以内，pad不错，来点推荐 | SAFE_DIRECT | `pad -> tablet` 是唯一别名；手机在否定范围；无未解释业务片段 |
| 对（存在唯一未过期的平板澄清草案） | SAFE_DIRECT | 绑定 clarification_id，确认草案字段后清除 pending state |
| 对，但不要平板（存在澄清草案） | NEEDS_SEMANTIC_LLM | 不能把含新增业务词的句子当纯确认 |
| 对（没有/已过期澄清草案） | LOCAL_CLARIFY | 不能猜用户在确认什么 |
| 要小米但不要小米 | LOCAL_CLARIFY 或 NEEDS_SEMANTIC_LLM | 明确记录硬条件冲突 |
| 第二个 | LOCAL_CLARIFY | 无操作词，不能猜是加购、参数还是比较 |

验收标准不是“规则命中率很高”，而是**不存在 `SAFE_DIRECT` 却仍有未解释业务片段的情况**。宁可多调一次 LLM，也不能把复杂请求错误地当成简单请求。

#### 4.2.10 不把 token 数量当作唯一开关，但把它当作风险信号

“字太少一定模糊、字太多一定复杂”的直觉可以作为成本和风险提示，但不能作为唯一规则。原因很直接：

```text
“对”              只有一个字，但若绑定唯一 pending clarification，就是最明确的确认
“确认”            两个字，但若绑定唯一购物车计划，就是最明确的写操作确认
“第二个 512G 多少钱” 很短，但目标、规格、动作都可能完全确定
“推荐手机，3000 内，不要小米，拍照优先，白色，512G” 很长，但每段都可能被规则完整解析
```

中文“token”还依赖分词器：`iPhone16ProMax`、`512G`、`不超过3000` 在不同模型中的 token 数并不稳定。因此不能把“token 在某区间内”作为 SAFE_DIRECT 的充分条件。

可采用的保守策略是：长度只做 **风险加分项**，永远不替代 safety proof。

1. 输入总字符数仍由 InputGuard 限制（例如 1--2000）；这是安全和资源限制，不是语义判断。
2. 对没有 card ID、确认 token、pending clarification 的普通文本，如果有效业务片段少于 2 个，通常无法形成可执行推荐，直接澄清或调 LLM；例如“推荐一下”“哪个好”。
3. 如果有效业务片段很多，记录 `long_message` trace，并优先检查是否有未消费 span；只有所有片段均被规则消耗时才可直通，不能仅因“太长”强制调 LLM。
4. 稳定 ID、购物车确认和澄清确认是长度豁免项：即使只有“对/确认”，只要绑定了唯一、未过期的 session 状态，也可本地执行。
5. 监控中可以统计“短文本误直通率”“长文本 LLM 调用率”，未来按真实数据调整；不要先拍一个 token 上下限写死在业务逻辑里。

所以最终判定仍是：`安全证明完整 + 无未解释业务片段 + 状态引用有效` 才能本地直通；长度只帮助系统更早发现可能需要 LLM 的请求。

### 4.3 LLM 不能自己生成硬条件：新增“硬条件升级门”

仅靠 `ActionValidator` 看 LLM 返回的 JSON，**不能**可靠判断“也许”“用着不好”是否被错误升级。因为这本身又是中文语义理解问题；如果 Validator 也靠 LLM 判断，就只是让两个模型互相背书，并没有真正增加安全性。

因此 V3 必须把流程拆成三层，责任不能混：

```text
SemanticParse LLM
  只提交“语义观察”：它认为用户可能表达了什么，并附原文位置
        ->
HardConstraintPromotionGate（本地、确定规则）
  决定哪些观察有资格升级为真正的 hard 条件
        ->
ActionValidator（本地、结构校验）
  检查动作、ID、枚举、范围和冲突，准备执行
```

LLM 返回的字段应改名为 `observations`，避免它看起来已经在替系统下命令：

```json
{
  "action_proposal": "recommend_shopping_products",
  "observations": [
    {
      "kind": "brand_attitude",
      "brand_family_id": "xiaomi",
      "proposed_polarity": "negative_experience",
      "proposed_strength": "medium",
      "source_span": {"start": 8, "end": 14, "text": "用着并不好"}
    },
    {
      "kind": "brand_preference",
      "brand_id": "huawei",
      "proposed_polarity": "prefer",
      "proposed_strength": "weak",
      "source_span": {"start": 17, "end": 25, "text": "或许华为适合我"}
    }
  ],
  "clarification_proposal": "你想让我推荐手机、平板，还是其他产品？"
}
```

这里的 `source_span` 不是让系统盲目信任模型给的文字，而是让本地重新用 `start/end` 到 `NormalizedTurn.normalized_text` 中取回原文并核对一致。LLM 不得把附件文字、历史摘要或自己改写的话伪装成本轮用户的明确命令。

#### 4.3.1 HardConstraintPromotionGate 的唯一职责

它只做一件事：判断某条候选条件能否变成“违反就绝不能返回”的条件。它不理解开放中文，不选商品，也不重写用户意图。

一条 LLM observation 或本地解析结果，只有同时满足以下全部条件才可升级为 hard：

1. 来源是可信的结构化 UI 控件、本轮用户正文的可回查原文 span，或用户按未过期 `ClarificationPlan.answer_schema` 给出的有效结构化答案/完整草案确认；
2. 本地能在该 span 上匹配到已批准的**明确命令句式，或明确、无条件的负面句式**；
3. span 中的品牌/分类/属性能唯一归一到受控字典 ID；
4. 该命令的作用对象清楚，例如“不要小米”排除品牌，而不是“我以前不用小米”；
5. 没有覆盖该 span 的推测、条件、转折或冲突结构；
6. 与本轮其它 hard 条件、同话题已继承 hard 条件不冲突；
7. 该字段属于允许做 hard 过滤的字段集合。

任意一条不满足，**默认不升级**。它可以保留为 soft observation、要求 LLM 提出澄清问题，或由系统直接问一个确定的问题；不能为了“看起来理解了用户”而偷偷写入 `exclude_brand_family_ids`。

#### 4.3.2 哪些证据可以升级，哪些绝对不可以

| 来源与原话 | PromotionGate 结果 | 原因 |
|---|---|---|
| 前端筛选器勾选“排除小米” | hard exclude | UI 是结构化且含义明确的用户操作 |
| “不要小米” | hard exclude | 命中批准句式 `不要 + 品牌`，品牌唯一 |
| “别给我小米” | hard exclude | 命中批准句式 `别给我 + 品牌`，品牌唯一 |
| “只要华为” | hard include | 命中批准句式 `只要 + 品牌`，品牌唯一 |
| “预算最多 3000” | hard price max | 命中明确上限句式，数值和货币明确 |
| “我用小米用得不好” | hard exclude `xiaomi` | 目标唯一、是直接无条件负面体验，命中负面句式 |
| “也许华为适合” | 不升级；可保留为 soft prefer | “也许”表示推测 |
| “如果小米降价就考虑” | 不升级；可保留条件性偏好或澄清 | 是条件句，不是当前排除/包含命令 |
| “小米和华为我都行” | 不写 hard 品牌条件 | 表示不限制，不是只允许两家 |
| “不要小米，但是我又有点想买” | 不升级，必须澄清 | 同一对象出现相反态度 |

批准句式列表是版本化配置和测试资产，不是散落在代码的字符串。第一版应少而明确，例如：`不要/别给我/排除/不考虑 + 实体`、`实体 + 不喜欢/不好用/用着不好/不适合/不行`、`只要/仅要 + 实体`、`实体 + 也可以`（只释放该实体）、`最多/不超过/以内 + 金额`、`至少/不少于 + 容量`。这里的“实体”必须先通过统一词表唯一归一到分类、品牌、商品、型号、SKU 或属性值，并且句子不能含“可能/也许/如果/听说/但又想要”等限定或反转。新表达若不在列表中，系统宁可问“你的意思是完全不考虑小米吗？”，也不能擅自做硬过滤；后续根据真实误判样本新增句式并先补测试。

#### 4.3.3 “也许”和“用着不好”在代码里怎么防止被错分

这不是让 PromotionGate 理解所有情绪，而是设定一个可测试的准入规则：**硬条件必须找到明确命令，或找到“已唯一归一实体 + 直接无条件负面表达”；有模态、条件、传闻、反转或范围不清就拒绝升级。**

例如对于 `小米用着并不好，或许华为适合我`：

```text
LLM 可以输出：xiaomi=negative_experience，huawei=tentative_preference
PromotionGate 重新读取原文：
  “小米用着并不好”  -> 命中 approved_negative_experience，xiaomi 唯一 -> hard exclude
  “或许华为适合我”  -> 没有 approved hard operator，且含“或许” -> soft prefer，不能包含
最终 hard.brand 条件  -> exclude_brand_family_ids=[xiaomi]
最终 soft.brand 偏好  -> [提高 huawei 排序]
```

若原文是“听说小米不好”“可能不喜欢小米”或“如果便宜小米也行”，即使 LLM 返回 `hard_exclude=xiaomi`，PromotionGate 也会因限定词或条件结构拒绝，并在 trace 记为 `promotion_rejected:hedged_or_conditional_negative`。

#### 4.3.4 ActionValidator 在拆分后到底检查什么

ActionValidator 不再声称“理解弱表达”。它只检查结构事实：

- 动作和 mode 是否属于 4 动作白名单；
- PromotionGate 产出的分类、品牌、属性、价格值是否在字典和范围内；
- card/product/SKU 是否存在、是否属于当前有效会话卡或用户显式 ID；
- 比较是否至少有两个不同目标；购物车数量是否为正；确认 token 是否未过期且属于本 session；
- hard 条件之间是否冲突，例如“只要小米”与“不要小米”、最低价高于最高价；
- `RequirementSpecV3` 中每条 hard 字段是否带有 `promotion_source`（UI 或明确原文 span）和 `promotion_rule_id`。

它不做情感分类，不用第二个 LLM 复核第一个 LLM。语义归 SemanticParse，硬条件资格归 PromotionGate，结构合法性归 ActionValidator。

#### 4.3.5 Session 继承也必须经过同一扇门

历史 hard 条件只能来自两种情况：用户明确文本经 PromotionGate 升级成功，或用户点击了明确 UI 筛选器。SessionCore 只保存这类字段的 canonical ID、来源、创建轮次和规则 ID。

因此：“不要小米”后说“小米用得不好”，不会把硬排除删掉；“不要小米”后明确说“要小米/小米也可以”，本轮有批准的反转/放宽句式，PromotionGate 才允许删除旧排除。LLM 的普通品牌态度不能改写历史 hard 条件。

#### 4.3.6 这部分新增的必测用例

| 输入 | LLM 即使输出什么 | PromotionGate 的最终结果 |
|---|---|---|
| 不要小米 | `exclude=xiaomi` | 接受 hard exclude |
| 小米用着不好 | `exclude=xiaomi` | 接受 hard exclude；命中直接负面句式 |
| 或许华为适合 | `include=huawei` | 拒绝 hard，最多 soft prefer |
| 别给我小米 | `exclude=xiaomi` | 接受 hard exclude |
| 如果小米便宜可以考虑 | `exclude=xiaomi` | 拒绝 hard exclude |
| 只要华为但别太贵 | `include=huawei`、`price_max` | 品牌 hard include；价格若表达明确也 hard，否则澄清 |
| 不要小米但又想买小米 | 任意 | 不产生品牌 hard；要求澄清 |
| 前端点了“排除小米” | 无 LLM 输出 | 直接接受 hard exclude，记录 UI 来源 |

模型不可用时，只有本地 RuleSignal 或 UI 已提供完整 hard 条件的请求才能继续。例如“推荐手机”可继续，“给妈妈买个拍照好的”应该问用户想买什么或提示稍后再试，不能盲目推荐。

## 5. 第三阶段：把每轮需求变成一张可执行清单

新对象 `RequirementSpecV3` 就是一张“本轮必须满足什么、尽量满足什么、还缺什么”的清单。不要把这些信息只留在 prompt 文本里。

```text
request_id, normalized_query, action, mode, targets
hard:  必须满足的分类、商品 ID、SKU、预算、库存、明确品牌包含/排除、参数条件；每条均带 promotion_source/rule_id
soft:  用途、风格、拍照偏好、品牌倾向、多样性偏好
missing_fields: 当前还需要问用户什么
clarification: 是否必须先问一句，以及问什么
field_provenance: 每个字段来自用户原话、UI、session 还是 LLM
taxonomy_version, catalog_version
```

构建顺序必须固定：

1. 读取本轮正文、UI 卡片 ID、已解析 SKU 和规则信号；
2. 若必要，调用一次 SemanticParse，得到带原文 span 的语义观察，而不是直接得到 hard 条件；
3. 用分类表、品牌表、属性表把可识别的词变成受控 ID/数值；
4. 对每一条拟写入 hard 的字段通过 HardConstraintPromotionGate：没有明确命令、明确无条件负面或 UI 来源的，一律降为 soft/澄清；
5. 只在同一话题中继承旧的、曾经通过 PromotionGate 的 hard 约束；新话题（手机切到 PC）清除不兼容条件；
6. 检查冲突，例如 `min_price > max_price`、“只要小米”同时“不要小米”；
7. ActionValidator 检查 ID、枚举、范围和结构；明确命令和直接无条件负面进入 hard，正向用途/猜测进入 soft，无法判定就澄清；
8. 输出不可变快照，同时只把允许继承的部分写入 SessionDelta。

品牌要特别按下面规则处理：

```text
“不要小米”          -> hard.exclude_brand_family_ids=[xiaomi]
“只要华为”          -> hard.include_brand_ids=[huawei]
“小米用着并不好”    -> hard.exclude_brand_family_ids=[xiaomi]
“或许华为适合我”    -> soft.brand_preference=prefer，不能只允许华为
“小米也可以”        -> 删除历史硬排除（本轮明确反转）
```

“不要小米”后面又说“要小米”，以最新一轮明确命令为准：删除排除、写入包含；如果同一句出现“要小米但不要小米”，必须追问。

## 6. 第四阶段：先决定谁有资格，再做检索——彻底修好“不要小米”

### 6.1 CatalogCandidateGate：第一道门

新增 `CatalogCandidateGate`。它直接查询实时目录，在向量检索前生成允许的 `product_id` 集合。它要检查：

```text
商品是否 active
是否有库存
目录类目/子类是否匹配
是否命中精确 product_id 或 sku_id
价格是否满足明确预算
是否命中明确包含品牌
是否命中明确排除品牌家族
```

结果为空时不能交给向量库“找个差不多的”。系统应明确告诉用户：是预算太低、只选的品牌无货、还是目录没有该类商品，并问用户是否愿意放宽。

### 6.2 Milvus：第二道门

CandidateGate 输出结构化 `RetrievalFilters`。只允许使用已经被目录验证过的值，不允许把用户原文直接拼成 Milvus 表达式。典型表达条件是：

```text
category in [...]
sub_category in [...]
product_id in [允许商品]
brand_family_id not in [xiaomi]
active = true
in_stock = true
```

这一步是**召回前**过滤。dense embedding、BM25、RRF、reranker 只能给“允许商品”排序，不能把不允许的商品找回来。

### 6.3 最终卡片：第三道门

向量结果按 `product_id` 聚合，补齐实时价格、库存和品牌家族，再做一次硬条件检查。即便缓存、重排或以后新增的算法错误带回小米，最终卡片生成器也必须丢掉它并记录 trace。

“不要小米”最终必须满足：目录候选列表没有小米、Milvus expression 排除小米、响应卡片也没有小米。三处任意一处失败都算 bug。

## 7. 第五阶段：重建入库和检索，不在旧 collection 上打补丁

### 7.1 新的三类索引文档

不要让“一个 chunk 承担所有事情”。新 collection/索引至少区分：

| 文档 | 保存什么 | 用途 |
|---|---|---|
| ProductIndexDocument | 每个商品一条：商品 ID、分类、品牌家族、上下架、库存摘要、价格桶、版本 | 过滤、商品级聚合和基础召回 |
| EvidenceChunk | 商品描述、卖点、评价等文本片段，并带 product ID、来源、hash、版本 | 语义检索证据 |
| SkuIndexDocument | SKU 级的可检索文字 | 帮用户找到 SKU；最终事实仍回目录核对 |

每个未来会写进 Milvus filter 的字段，必须同时满足四件事：schema 声明了、写入时真的写了、建了合适索引、测试能读出来。只满足其中一件不算完成。

### 7.2 删除没有真实数据支撑的层级逻辑

当前没有完整 parent chunk 写入，就不要保留 auto-merge 伪层级。要么先设计并写入父文档、父子关系和完整性测试，再使用层级检索；要么删除这条死路径，避免让人误以为它在提升效果。

### 7.3 发布新索引的安全做法

1. 新建带 `index_version` 的 collection，不覆盖旧库；
2. 入库前检查必填字段、重复 product、孤儿 SKU/chunk、品牌家族、价格/库存时间戳和 chunk hash；
3. 用同一批 golden query 跑旧索引和新索引，重点检查品牌否定、精确 SKU、手机/PC 分域；
4. 用 alias 或配置原子切换到新索引；
5. 保留旧索引一段时间，出现过滤错误可快速回滚；
6. 旧路径没有调用量后才删旧 collection 和代码。

## 8. 第六阶段：把回答、购物车和 PC 分支接回新链路

### 8.1 推荐卡

卡片先由后端 `CardModel` 生成，再由 LLM（如果启用）把已验证字段写成自然语言。每张卡必须返回 `card_id/product_id/default_sku_id/catalog_version`。前端后续请求必须尽量传 card ID，不能只传标题。

### 8.2 参数、SKU、价格和比较

`parameter_query` 的第一步永远是 `TargetResolver`：按优先级解析 UI 传来的 ID、用户显式 ID、当前卡片编号、focus。解析成功后直接查实时目录。比较表先由 AttributeRegistry 确定性生成；用户再问“夜景哪个更好”时，LLM只能解释表里的已知事实，字段不存在就明确说“目录没有这项数据”。

### 8.3 购物车

任何 add/remove/set quantity 先生成 `CartPlan`，展示给用户确认。pending action 绑定 session、商品/SKU、数量和操作类型，60 秒过期。用户说“确认”时必须再次查库存和价格；超时、换商品或换会话都要重新生成计划。

### 8.4 PC 装机

`mode=pc_build` 必须进入专门的组件候选、预算调整和兼容性求解流程。它可以用检索找描述，但 CPU/主板/显卡/电源的可用性由结构化规格和兼容规则决定。手机聊天切到装机时，旧的手机预算、品牌和拍照偏好不能污染 PC 会话。

## 9. 第七阶段：删除旧模式、补齐测试、灰度上线

### 9.1 要删除的旧东西

- 残留的 `fast/balanced/full`、`mode=auto -> balanced` 翻译、相关 request 字段和 session/runtime trace 字段；
- 依赖旧模式名的配置、README、SSE 文案、调试脚本和失效测试；
- 8 工具旧 schema 和不可达分发分支；
- 没有真实父文档的 auto-merge；
- 没有对应落盘字段的 Milvus filter；
- session 中完整结果、附件、模型日志的冗余保存。

保留的是内部 `ExecutionPolicy`：它只管超时、并发、缓存、重试和故障降级，不应该让用户选“快模式”就降低事实校验或跳过硬约束。

### 9.2 必须新增的测试

| 测试类 | 必测例子 |
|---|---|
| 路由安全直通 | “推荐手机”“第二个有 512G”“确认加购”都有完整 safety proof |
| 复杂语义 | “小米不好，也许华为适合”“给妈妈买简单的”不能被本地误直通 |
| 统一词表 | “华为/HUAWEI/huawei”同为 `huawei`；“pad/Pad/平板”同为唯一 `tablet`；alias 冲突绝不生成过滤条件 |
| 负面与释放 | “小米用着不好”写 hard exclude；“小米也可以”只释放 `xiaomi`；“听说小米不好”“如果便宜小米也行”不误写黑名单 |
| 约束合并 | “不要小米”后“要小米”；新话题手机切 PC；同句冲突 |
| 检索过滤 | 小米在 CandidateGate、Milvus、最终卡片三处均被排除 |
| 事实查询 | SKU、价格、库存、参数与实时目录逐字段一致 |
| 多轮引用 | 卡片序号、card ID、过期卡、跨域卡片的处理正确 |
| 话题状态机 | 闲聊/乱码前后商品约束不变；“刚才第二个手机”唯一回跳；新 topic 后孤立“对”不能确认旧 plan |
| 购物车 | 60 秒确认、超时、换目标、重复确认都安全 |
| 索引 | 每个 filter 字段已写入；无孤儿 chunk；商品级去重 |
| 故障 | Router、embedding、目录、回答 LLM 失败时不编造事实 |

### 9.3 灰度顺序

先不展示结果地“影子运行” V3：同一请求同时生成旧/新 Requirement 和过滤结果，比较差异。确认约束没有漏放后，依次小流量开放普通推荐、参数追问、购物车、PC 装机。每一步都保留索引回滚和指标阈值。不能因为模型输出读起来很自然，就判定上线成功。

## 10. 这一阶段完成的验收清单

只有同时满足以下条件，才算 V3 改造完成：

- 对外只有 4 个动作，旧 8 工具没有可达生产分支；
- 每次本地直通都有 safety proof；复杂请求默认进语义 LLM；
- 每轮都有可回查的 RequirementSpecV3，明确区分硬约束、软偏好和待澄清项；
- 明确“不要小米”在三道门都不可绕过；
- 每张卡都能通过 card ID 找到商品、SKU 和目录版本；
- SKU/价格/库存/参数由目录复算，不依赖模型记忆；
- Redis 会话有大小、轮数和单轮写次数上限；完整调试数据只在 TraceStore；
- Milvus schema、写入字段、filter 和测试同源；
- 任何 LLM 或检索失败都能安全降级，并在 trace 中说明原因。
