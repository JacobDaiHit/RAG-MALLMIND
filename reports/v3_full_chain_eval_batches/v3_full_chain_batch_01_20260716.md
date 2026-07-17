# V3 full-chain 真实全量回归（2026-07-17）

## 结果

本报告覆盖 `tests/fixtures/full_chain_eval_cases.json` 的全部 26 条用例、共 30 个用户回合：**26/26 通过，0 失败**。

执行命令：

```powershell
python scripts\eval_v3_full_chain.py --start 0 --size 26 --output reports\v3_full_chain_eval_batches\v3_full_chain_batch_01_20260716.json
```

执行时间：2026-07-17 20:16:55 +08:00。评测经真实 `/api/chat/stream` SSE 入口运行，不是 mock：

- 30/30 回合都有 `semantic_provider=openai_compatible`、`semantic_model=deepseek-chat`，即都真实调用了外部 Chat；
- 10 个普通商品推荐回合中，9 个实际产生商品卡，且 9/9 都有 DashScope embedding 与本机 Milvus 的 `retrieval.status=ok` 和 allowlist `filter_expression`；
- 其余推荐回合是预期的澄清、目录范围拒绝或零候选闭环，不应调用 embedding/Milvus；PC 建方案、编辑和比较走目录兼容求解器，也不走普通商品检索。

## 覆盖分组

| 范围 | 用例数 | 结论 |
|---|---:|---|
| 商品与 PC 单配件推荐（Case 1--8） | 8 | 8/8：语义解析、CandidateGate、embedding、Milvus 和商品卡均闭环。 |
| PC 购买形式与方案版本（Case 9--14） | 6 | 6/6：明确装机进入求解器；模糊“剪辑电脑”先澄清；“配台主机/笔记本”可继承上一轮预算和用途；预算调整与方案比较均通过。 |
| 目录范围、预算空候选（Case 15--18） | 4 | 4/4：目录外请求统一 `catalog_scope_unsupported`，预算过滤无候选不产生虚构商品卡。 |
| 澄清、商品卡引用、购物车和泛聊（Case 19--26） | 8 | 8/8：缺类别、缺 CardRef、购物车目标缺失均得到稳定澄清；正常泛聊不误入导购。 |

## 本轮修复后验证的两个边界

1. `7000 元左右配一台游戏主机，主要玩 3A`：模型观察为 `desktop_build`，其证据可以是“游戏主机”；本地校验额外检查同一原句是否有集中配置的明确装机短语“配一台”。因此不会因模型没有把两个语义片段塞进同一 evidence span 而错误澄清，也不会把只有“游戏主机”的请求擅自当作装机。
2. 追问 `笔记本`：模型偶尔会从候选菜单复制展示名 `笔记本电脑`，而非内部 ID `sub_category:笔记本电脑`。TypeResolutionGate 现在只允许把**精确目录展示名**归一为同一轮候选集内的 canonical ID；不是模糊别名、不会接受候选集外的模型输出。该回合真实返回数码商品卡，并有 Milvus allowlist 检索证据。

## 评测契约

本轮运行前已审计 fixture，详见 [full_chain_fixture_audit_20260717.md](../full_chain_fixture_audit_20260717.md)。多轮 case 不再只检查最后一句：`expected_turns` 逐回合校验 action、澄清/拒绝原因、推荐类别、外部 Chat 调用，以及推荐时的 embedding/Milvus 证据。

机器可读的逐回合 SSE 结果、路由、CandidateGate、检索表达式与断言均在同名 JSON 文件中。
