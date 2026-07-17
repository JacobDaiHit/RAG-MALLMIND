# V3 当前质量结论与有效报告

本目录只保留能反映当前 V3 生产链路的报告。评测入口是 `POST /api/chat/stream`；结果不是只看模型文本，而是检查 SSE 路由、澄清、目录事实、CandidateGate、检索证据与 SessionCore 的多轮传递。

## 最新结论

2026-07-17 已完成真实全量回归：**full-chain fixture 26/26 通过，共 30 个用户回合**。每一回合均调用 DeepSeek `deepseek-chat`；9 个实际产生普通商品卡的推荐回合均使用 DashScope embedding 和本机 Milvus，并保留 `filter_expression` 作为检索前 allowlist 证据。

| 文件 | 用途 |
|---|---|
| [v3_full_chain_batch_01_20260716.md](v3_full_chain_eval_batches/v3_full_chain_batch_01_20260716.md) | 当前最完整的人工可读全量结果、环境、覆盖范围和关键边界。 |
| `v3_full_chain_batch_01_20260716.json` | 同次运行的机器可读逐回合 SSE、路由、CandidateGate、Milvus expression 与断言。 |
| [full_chain_fixture_audit_20260717.md](full_chain_fixture_audit_20260717.md) | 重跑前对 26 条 fixture 的目的审计；说明哪些旧预期已按 V3 行为修正。 |
| `pc_purchase_form_smoke_20260717.json` | PC 购买形式的专项真实回归原始证据；已被上面的全量回归覆盖，但保留作窄范围排障样本。 |
| [pc_purchase_form_disambiguation_plan.md](pc_purchase_form_disambiguation_plan.md) | PC “买笔记本/成品机/配主机”边界的设计和已实施约束。 |

## 当前请求实际经过的链路

```text
POST /api/chat/stream
 -> InputGuard / NormalizedTurn
 -> SafetyProof（仅封闭 grammar 可本地直通）
 -> 未直通：A/B/C 本地类型候选 + 一次 SemanticParse（外部 Chat）
 -> ComputerPurchaseKindValidator / TypeResolutionGate / PromotionGate
 -> ClarificationPlan，或可执行 RequirementSpecV3
 -> 普通推荐：CandidateGate allowlist -> embedding + Milvus -> 目录商品卡
 -> PC：目录兼容求解器 / 方案版本编辑或比较
 -> SessionDelta（卡片引用、澄清、购物车确认、current/previous PC plan）
 -> SSE 响应
```

LLM 只输出语义观察：动作、类别候选、原句 evidence、品牌/预算候选、CardRef 或 PC 操作。类别 canonical ID、商品 ID、SKU、价格、库存、候选过滤与真实副作用均由本地目录和确定性模块拥有。

## 本轮重点验证

- **明确装机不误走普通推荐**：`配一台游戏主机` 进入 PC 兼容求解器；只有“游戏主机”或“RTX 4070”而没有明确装机表达时仍先澄清购买形式。
- **短答能续接但不串话**：`剪辑电脑 9000 -> 配台主机` 继承预算与用途生成方案；`-> 笔记本` 进入普通商品检索；换到“推荐篮球鞋”不会继承上一话题的电脑条件。
- **目录展示名容错仍 fail-closed**：模型若复制候选菜单展示名 `笔记本电脑` 而不是内部 ID，只能在它是目录中唯一精确名称且确实在本轮候选集时归一；候选外 ID、模糊别名和伪造 evidence 都继续澄清或拒绝。
- **目录范围错误统一**：明确推荐汽车、处方药或无目录对应类型时，以 `catalog_scope_unsupported` 闭环；不伪造商品卡，也不把它伪装成泛聊。

## 当前已知边界与下一步

1. 类型候选 B/C 当前使用本地中文 bigram 检索，适合当前目录规模。若目录类型增加到数千并出现明显召回缺口，应先新增离线类型评测集，再评估是否加入一次 query embedding 的类型重排；不能把全目录类型或商品明细塞进 prompt。
2. PC 求解器目前不把“必须 RTX 4070”解释为可执行型号硬约束；这类请求仍只能完成购买形式澄清。要支持它，需要独立实现目录型号 canonical、约束类型、兼容求解和结果断言，不能让 LLM 直接传配件 ID。
3. 当前测试环境仍有两个非功能性告警：`numexpr` 版本低于 pandas 建议值、FastAPI `on_event` 弃用；它们不影响本次 26/26 结果，但应在依赖与应用生命周期维护中处理。

## 可复现命令

```powershell
# 先确认 Docker Milvus 与外部模型环境变量可用
python scripts\eval_v3_full_chain.py --start 0 --size 26 `
  --output reports\v3_full_chain_eval_batches\v3_full_chain_batch_01_20260716.json

# 结构化契约与主要 V3 回归
python -m pytest tests\test_v3_full_chain_fixture.py tests\test_v3_semantic_parse.py `
  tests\test_v3_api.py tests\test_v3_cart.py tests\test_v3_pc_executor.py `
  tests\test_v3_routing.py tests\test_v3_milvus_ingestion.py `
  tests\test_embedding_sparse.py tests\test_milvus_writer_stability.py -q
```
