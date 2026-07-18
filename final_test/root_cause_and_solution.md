# Final Test：当前根因、已实施修复与下一步

## 1. 当前可信基线

- 最新原始结果：`results/v3_fixed_eval_20260718_171016.json`
- 最新可读结果：`results/v3_fixed_eval_20260718_171016.md`
- 环境：本机 FastAPI、DeepSeek `deepseek-chat`、DashScope embedding、Milvus。
- 范围：37 个场景、61 个实际 HTTP 请求轮；每次运行使用新的 session namespace，同一场景的多轮才共享会话。
- 结果：**61/61 通过（100%）**；`SAFE_DIRECT` 错误放行率为 **0%**；商品 ID、价格、SKU、库存一致率均为 **100%**。

这里的“通过”只表示这套固定自然语言集的端到端断言全部满足，不代表已经覆盖所有中文表达。本批有 1/51 个需要 SemanticParse 的请求发生 schema 重试并在第二次恢复，不能据此宣称已覆盖所有模型异常形式。

## 2. 本轮实际修复的根因

### A. 类型排除重复要求模型给字符坐标

**旧问题**：模型已经从本轮本地类型菜单选出 `phone`、`earbuds` 等排除 ID，却还要给每个 ID 附一份原句字符坐标 `exclude_type_evidences`。坐标稍有偏差，系统就澄清，尽管 ID 本身已经受本地菜单约束。

**已实施**：删除 `exclude_type_evidences`，包括 SemanticParse Prompt、`RecommendObservation`、pending 序列化和 `TypeResolutionGate`。本地仍拒绝候选菜单外 ID、重复 ID、目标与排除相同 ID；不会按字符串猜类型。

**验证**：

- “不要手机和耳机，推荐平板”；
- “篮球鞋、雨鞋、手机……都不要，给我推荐 pad”。

最新全量中两句均进入平板候选池，`exclude_type` 约束保持率为 100%。

### B. 开放式购物被错误追问

**旧问题**：“送女朋友礼物，不知道买什么”被当作“缺商品类别”，只能澄清，或把“礼物”误当目录外类别。

**已实施**：

1. `RecommendObservation.mode` 只允许 `product | explore`。它由本轮 `SemanticParser` 判断；本地只校验模式和执行字段，不用“送礼”关键词自行改模式。
2. `mode=explore` 不能携带具体目标类型；`mode=product` 才能进入普通单品类推荐。显式目录外请求如“推荐汽车/处方药”被 Prompt 要求保留为 `product + target_type_surface`，之后由本地返回 `catalog_scope_unsupported`，不能伪装成探索。
3. 新增 `CatalogExplorationPlanner`。它从真实目录构造类别 profile，先用目录文本的确定性字符 bigram 相关性和商品信息完整度挑方向，最多取 3 个不同父类；PC 部件类型 `pc_category:*` 不进入泛探索。每个方向随后独立经过 `CandidateGate → embedding/BM25/Milvus → 目录事实复核`，最多返回一张真实商品卡。
4. 探索结果和普通结果都通过 `recommendation_delta()` 写入轻量 SessionCore。用户下一轮说“平板/篮球鞋”时，SemanticParser 产出 `mode=product`，不会继续沿用探索执行状态；问“第二个多少钱”则只复用已生成 CardRef。

**当前边界**：`CatalogExplorationPlanner` 不根据“女朋友”等词推断护肤、衣服或其他偏好；它只是用跨类别真实商品帮助用户收敛。PC 部件在没有兼容信息时不作为泛探索首选；用户明确提出装机或升级配件时，仍走原 PC 链路。

### C. 购物车“第一个”有两个并列字段

**旧问题**：`card_references=[1]` 表示刚推荐卡片的第一个商品，而 `cart_reference=1` 表示购物车第一行。两个字段同时暴露给模型，容易填错或同时填入。

**已实施**：用一个不可拆分的 `CartTargetRef` 替换双字段：

```json
{"source": "card", "rank": 1}
```

- 加入商品：`operation=add`，只能 `source=card`；
- 删除/改数量：`operation=remove/set_quantity`，只能 `source=cart`；
- 查看/清空：不能携带目标；
- 来源不明或动作与来源冲突：发澄清，绝不猜测或修改购物车。

**验证**：完整固定集覆盖 `add → confirm → set_quantity → confirm → remove → confirm → clear → confirm`；数量和 CardRef 约束保持率均为 100%。

### D. 外部模型偶尔返回 action schema 外字段

**旧问题**：严格 decoder 正确拒绝多余字段或非法枚举，但一次偶发输出会直接得到 `semantic_llm_invalid`。

**已实施**：

1. decoder 继续拒绝 extra field 和非法枚举，不忽略、不自动修正。
2. 仅在模型**已返回 JSON、但 action schema 解码失败**时，最多重调一次 SemanticParser；第二次使用相同候选菜单和会话摘要，额外附一句短修复指令。
3. 超时、网络错误、目录读取失败、PromotionGate 拒绝不重试；绝不发生第三次调用。
4. `SemanticParseResult.attempts` 逐次记录 `attempt`、`outcome`、安全原因、耗时和 token；SSE `v3_routing.semantic_attempts` 与 final_test 原始 JSON 保留这些证据。

**本批观测**：61 轮中，51 轮需要 SemanticParse；其中 1 轮（“我要一台剪辑视频用的电脑，预算 9000”）首次输出非法枚举，`schema_enum_invalid` 后第二次成功。schema 重试率为 1.96%，重试后恢复可执行率为 100%，单请求最大尝试次数为 2。单元测试另构造“第一次 extra field、第二次合法”，验证不会出现第三次调用。

### E. 重跑评测会复用旧会话

**发现过程**：第一次全量重跑中，“我要一台剪辑视频用的电脑”偶发直接推荐而不是追问。原始 trace 显示该请求一开始就带有旧商品卡；根因不是路由，而是 `runner.py` 固定使用 `final-eval-<case_id>`，重复运行会读取上次的内存 SessionCore。

**已实施**：runner 每次生成 `run_id`，会话改为 `final-eval-<run_id>-<case_id>`。因此同一 case 内的多轮仍共享状态，但不同运行完全隔离。最终 61/61 报告使用该隔离机制生成。

## 3. 当前仍应继续关注的事项

1. **本地直通覆盖率很低。** 最新固定集中约 4.92% 请求走 `SAFE_DIRECT`，这符合“优先降低错误直通”的策略，但首事件平均约 5.56 秒，主要由外部语义模型造成。
2. **探索方向质量需要独立评测。** 当前确保目录真实、库存/预算/排除条件正确和类别多样化；尚未建立人工偏好或点击反馈指标，不能把“跨类别”等同于“用户喜欢”。
3. **schema 重试需要故障注入扩展。** 当前只做了单元级模拟；未来应在 mock provider 和可控测试模型上统计首次合法率、重试修复率、两次失败率与额外 token。
4. **Redis 故障恢复、Milvus 故障降级、并发正确率仍由独立故障/并发测试覆盖。** 普通成功全量集不会伪造这些指标。

## 4. 本轮验证命令

```powershell
python -m pytest tests/test_v3_semantic_parse.py tests/test_v3_cart.py tests/test_v3_api.py final_test -q
python final_test/runner.py --base-url http://127.0.0.1:8000
```

最终真实 HTTP 报告已只保留最新一套。以后改 Prompt、Session、候选词表、PromotionGate、CandidateGate 或检索逻辑后，必须以新的隔离 session 全量结果覆盖本文件的“当前可信基线”。
