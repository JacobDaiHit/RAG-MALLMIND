# PC 首次需求：先分清“买什么形式的电脑”，再决定是否装机

> 文档性质：待评审施工方案。本次只改方案，不改运行代码、不改 prompt、不重跑评测。

## 1. 先说结论

`我要一台剪辑视频用的电脑，预算 9000` **不能直接进入 PC 装机求解器**。

这句话只说清了用途和预算，没有说清用户要的是：

1. 笔记本；
2. 品牌成品台式机；
3. 让系统按预算配一台台式主机。

三种商品形态的后续链路完全不同。系统不能为了少问一句就默认第三种。正确结果是一次澄清，而不是普通商品推荐，也不是 `catalog_scope_unsupported`。

## 2. 现在为什么会出错

当前一次 SemanticParse 会把“电脑”这种宽泛目标判成普通 `recommend_shopping_products`。随后普通商品类型确认没有找到可直接执行的目标，链路就在进入 PC 模块前返回 `catalog_scope_unsupported`。

这里错的不是“没有找到商品”本身，而是系统太早把“电脑”解释成了某一种购买形式：

```text
用户只说“剪辑电脑”
-> 系统擅自当作普通商品推荐
-> 普通类型确认失败
-> 错报为目录范围不支持
```

实际含义应是：用户的目标还不完整，需要用户选购买形式。

## 3. 新规则：明确装机才进入 PC 装机

| 用户表达 | 系统理解 | 下一步 |
|---|---|---|
| `7000 元配一台游戏主机`、`帮我组台电脑`、`给我出配置单` | 明确要自己配台式主机 | `generate_pc_build_plan`，进入 PC 兼容求解器 |
| `推荐一台剪辑笔记本`、`想买游戏本` | 明确要笔记本 | 普通商品推荐，走笔记本商品卡检索 |
| `推荐一台联想成品台式机` | 明确要成品台式机 | 目录有该类商品则普通推荐；目录没有则 `catalog_scope_unsupported` |
| `我要一台剪辑视频用的电脑，预算 9000` | 购买形式不明确 | 生成澄清问题，不能检索商品，也不能调用 PC 求解器 |

“配、组、装、DIY、配置单、攒机”等词不是本地关键词路由器。它们只是 SemanticParse 在理解原句时可以使用的强信号；最终 action 仍由一次 SemanticParse 输出，并由本地一致性检查确认。

## 4. SemanticParse 要多输出什么

在现有语义观察结果中增加一个只表达用户意图、不可直接执行的字段：

```text
computer_purchase_kind:
  desktop_build       # 用户明确要配/装/组台式主机
  laptop              # 用户明确要笔记本
  prebuilt_desktop    # 用户明确要买成品台式机
  unknown             # 只说电脑/主机，无法确定购买形式
```

字段所有权要清楚：

- SemanticParse 只输出观察结果和原句证据；
- `PromotionGate` / 新的购买形式校验器只确认它与原句、action 是否一致；
- `RequirementSpecV3` 只在形式已经确认后写入可执行约束；
- 商品目录决定是否真的有笔记本或成品台式机；
- PC 求解器只接受 `desktop_build`，绝不接受 `unknown`。

模型不新增商品 ID、SKU、价格、库存或配件 ID 的输出权限。

## 5. 具体链路

### 5.1 用户一开始就说清楚要装机

```text
“7000 元配一台游戏主机，主要玩 3A”
-> SemanticParse：computer_purchase_kind=desktop_build
-> action=generate_pc_build_plan，预算/用途带原句证据
-> 一致性校验通过
-> RequirementSpecV3 写入预算、用途
-> PC 求解器读取真实目录配件并做兼容校验
-> 返回方案或明确说明目录中无可行方案
```

这里不产生商品卡式的笔记本/整机推荐，也不经过普通商品类型的 Milvus 召回。

### 5.2 用户明确要笔记本

```text
“推荐一台 9000 元以内、剪辑视频用的笔记本”
-> SemanticParse：computer_purchase_kind=laptop
-> action=recommend_shopping_products
-> 类型确认到目录中的笔记本类型
-> CandidateGate 用预算、库存等条件过滤
-> Milvus 只在笔记本候选中召回并生成真实商品卡
```

如果目录没有笔记本候选，才在 CandidateGate 过滤完成且商品卡为 0 时返回 `catalog_scope_unsupported`。

### 5.3 用户没有说清楚：本题的 9000 元剪辑电脑

```text
“我要一台剪辑视频用的电脑，预算 9000”
-> SemanticParse：computer_purchase_kind=unknown
-> action 不能进入普通推荐或 PC 装机执行
-> ClarificationPlan 保存用途=视频剪辑、预算=9000、待确认字段=购买形式
-> 回复一个短问题
```

推荐问题必须根据当前目录能力生成，例如：

```text
你想买笔记本，还是让我按 9000 元给你配一台台式主机？
```

只有当目录确实有“成品台式机”能力时，才把它列为第三个选项；不能把目录没有的商品形式说成可推荐。

### 5.4 下一轮只回答“配台主机”或“笔记本”时如何接上

SessionCore 只保存最小的待办信息：

```text
pending_clarification.kind = computer_purchase_kind
pending_clarification.base_requirement = {用途: 视频剪辑, 预算上限: 9000}
pending_clarification.ttl = 配置中的澄清有效期
```

用户回答 `配台主机`：

```text
-> 识别为对 pending clarification 的回答
-> 合并上一轮已验证的用途和预算
-> computer_purchase_kind=desktop_build
-> generate_pc_build_plan
-> PC 求解器执行
```

用户回答 `笔记本`：

```text
-> 合并上一轮用途和预算
-> computer_purchase_kind=laptop
-> recommend_shopping_products
-> 笔记本类型检索和商品卡召回
```

如果用户回答“成品台式机”，系统先检查该目录能力；没有对应商品类型时才返回 `catalog_scope_unsupported`。如果澄清已过期或用户突然换话题，则不合并旧预算/用途，重新解析新句子。

## 6. 本地一致性检查只做什么，不做什么

新增的 `ComputerPurchaseKindValidator` 只检查内部自相矛盾，不能替用户猜：

| 模型输出 | 校验结果 |
|---|---|
| `desktop_build` + `generate_pc_build_plan` | 可以继续 |
| `laptop` + `recommend_shopping_products` | 可以继续 |
| `unknown` + 任意可执行推荐/装机 action | 转 ClarificationPlan |
| `desktop_build` + 普通推荐 action | 直接转澄清，不能自行改成装机，也不再调用第二次 LLM |
| `laptop` + PC build action | 直接转澄清，不能自行改成普通推荐，也不再调用第二次 LLM |

它不根据“电脑”“剪辑”“9000”三个词把请求自动改成装机；不进行关键词兜底；也不把“没有普通商品类型”误写成“目录不支持”。

## 7. Prompt 要怎样改，才不会更长更乱

只替换目前“推荐、购买、寻找商品一律用普通推荐”的宽泛规则，增加三条短规则和三个对照例子：

```text
1. 用户明确说配/装/组台式主机、DIY 或要配置单：computer_purchase_kind=desktop_build，使用 PC build。
2. 用户明确说笔记本或成品台式机：填写相应 purchase kind，使用普通商品推荐。
3. 用户只说“电脑/主机”但未说明买哪种形式：purchase kind=unknown，填写缺失字段，不得执行推荐或装机。

例：配一台 7000 游戏主机 -> PC build。
例：推荐一台剪辑笔记本 -> 普通推荐。
例：剪辑电脑 9000 -> unknown，澄清购买形式。
```

不需要把大量品牌、类型、电脑知识塞进 prompt；目录中实际支持哪些类型仍由本地目录和 CandidateGate 判断。

## 8. 计划改哪些模块

1. `semantic_parse` 的类型、JSON schema 和精简 prompt：增加 `computer_purchase_kind`、其 evidence 和 `unknown` 的缺失字段约定。
2. 购买形式验证模块：新增一个职责单一的 `ComputerPurchaseKindValidator`，只做 action 与购买形式的一致性校验。
3. `ClarificationPlan` 与 `SessionCore`：支持保存“待确认购买形式”以及已确认的预算、用途，允许下一轮短回答合并。
4. 编排器：`unknown` 优先进入澄清；`desktop_build` 才进 PC executor；`laptop` / 已确认的 `prebuilt_desktop` 才进普通推荐链路。
5. `CandidateGate`：仅在用户已明确选择普通商品形式、并完成真实目录过滤后，0 商品卡才给 `catalog_scope_unsupported`。
6. 测试 fixture 与报告：Case 9 保持“应进入 PC build”；Case 10 的正确期望改为“先澄清，下一轮选择后再执行”。同时记录外部模型是否真的调用、输出的 purchase kind、最终 action 与澄清状态。

不保留“电脑 + 预算 + 用途 = PC build”的旧判断，也不增加旧 Router 与新 Router 双重决定 action 的桥接代码。

## 9. 验收用例

至少新增并运行下列真实全链路用例：

1. `7000 元左右配一台游戏主机，主要玩 3A`：外部模型调用一次，进入 PC 求解器。
2. `推荐一台 9000 元以内剪辑视频用的笔记本`：外部模型调用一次，进入笔记本 CandidateGate 和 Milvus。
3. `我要一台剪辑视频用的电脑，预算 9000`：外部模型调用一次，只生成澄清；Milvus 和 PC 求解器均不调用。
4. 上例后回复 `配台主机`：不丢失 9000 和视频剪辑，进入 PC 求解器。
5. 上例后回复 `笔记本`：不丢失 9000 和视频剪辑，进入笔记本检索。
6. `想要一台 9000 元成品台式机`：目录有该类型则推荐；没有则在确认形式后返回 `catalog_scope_unsupported`。
7. 澄清期间用户改说 `顺便推荐个篮球鞋`：清除旧的电脑澄清，不携带 9000 元和剪辑用途。

验收重点不是“PC build 命中率越高越好”，而是：不把笔记本、成品机和装机混在一起；不在信息不够时乱执行；且用户补一句短回答后能正确继承上一轮的信息。

## 10. 对现有 full-chain Case 的改动

- Case 9 的原句含“配一台游戏主机”，属于明确装机，预期仍为 `generate_pc_build_plan`。
- Case 10 的原句只说“剪辑视频用的电脑”，预期必须从“直接出 PC 方案”改为 `ClarificationPlan(computer_purchase_kind)`。
- 为 Case 10 增加两个后续回合 fixture：`配台主机` 和 `笔记本`，分别验证两条真实执行链路。

这样修完后，测试报告里的失败原因会更准确：Case 10 若没问清就装机是错误；若正确产生澄清则是通过，不再把它算作 PC 失败。
