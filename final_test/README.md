# MallMind V3 固定评测体系

这个目录不是“把已有单元测试再跑一遍”。它把固定的自然语言用例、真实 SSE 请求采集、指标计算、并发压力和故障契约放在同一个版本化目录中。一次报告必须同时保存原始事件和计算结果，才能回答“为什么这个分数是这样”，而不是只说“测试通过”。

## 目录与执行方式

| 文件 | 用途 |
| --- | --- |
| `fixtures/fixed_eval_cases.json` | 固定自然语言集：路由、否定/反转、约束、卡片事实、购物车、PC、多轮和提示注入。 |
| `root_cause_and_solution.md` | 最新真实评测的结论、逐条根因和下一步解决方案；旧批次不在这里堆积。 |
| `coverage_matrix.md` | 根 README 声明的每项能力对应到哪些 case，便于审查是否存在测试盲区。 |
| `runner.py` | 对真实 `POST /api/chat/stream` 逐条请求，写 JSON 原始证据和 Markdown 指标报告。 |
| `fixtures/concurrency_cases.json`、`concurrency.py` | 用真实 HTTP 并发请求检查跨请求正确率。 |
| `test_metrics.py` | 验证指标公式，重点防止 False Accept Rate 算错。 |
| `test_fixture_contract.py` | 防止固定集缺少动作、拒绝、澄清或安全直通标签。 |
| `test_fault_contracts.py` | 直接构造 Milvus 故障、过期卡片和 Redis 网络故障，验证不能静默越权。 |

`runner.py` 每次都会生成新的 `run_id`，并将它加入每个 case 的 `session_id`。这样同一 case 的多轮对话
仍能保留商品卡、待澄清和购物车状态；不同 case 或不同批次不会读到旧运行留下的会话状态。

先启动真实服务、配置外部 Chat 模型、embedding 和 Milvus，再运行：

```powershell
python final_test/runner.py --base-url http://127.0.0.1:8000
python final_test/concurrency.py --base-url http://127.0.0.1:8000
```

离线检查不会调用外部服务：

```powershell
python -m pytest final_test -q
```

## 指标口径

### 路由

- **Intent Accuracy**：有标注动作的 turn 中，`v3_routing.action` 与固定答案一致的比例。
- **不支持请求召回率**：固定答案为 `catalog_scope_unsupported` 的请求中，实际以该原因拒绝的比例。
- **闲聊误判成购物**：标注为闲聊却没有走 `general_chat` 的比例。
- **购物误判成闲聊**：标注为购物却走 `general_chat` 的比例。

### 本地直通

最重要的指标是：

```text
False Accept Rate = 错误但被 SAFE_DIRECT 放行的请求数 / SAFE_DIRECT 总请求数
```

这里的“错误”至少包含：固定集明确标注不能直通、动作不对、或应保持的约束没有保住。它比本地直通覆盖率更重要：系统宁可多问一次，也不应把复杂句子错误地当成简单句子执行。

同时报告：`SAFE_DIRECT` 覆盖率、LLM 回退比例、错误拒绝率与歧义句追问率。

### 约束与事实

固定集会逐项检查品牌包含/排除、价格上限/下限/目标价、目标与排除类型、数量、卡片引用、两卡比较和电脑购买形式。推荐卡会与本地目录逐项核验商品 ID、价格和 SKU；两卡事实表还核验库存。被排除品牌再次出现在商品卡中会单独计数。

### 工程

真实 SSE 运行分别记录首个可显示事件延迟（通常是无结论的 `progress(stage=understanding)`）、首个业务结果
延迟（澄清、错误、文本、商品卡、事实、购物车或 PC 结果）和总响应时间，以及每请求模型调用次数和供应商实际返回的 token 数。一次正常
`SemanticParser` 是一次外部 Chat 调用；只有首次 JSON action schema 不合法才允许第二次修复调用，第二次仍失败、
网络失败或超时都不会再试。报告会单列 schema 重试率、修复成功率和最大尝试次数，不能把第二次调用藏进“平均一次”。
Milvus 不可用时，测试检查它是否只丢失检索证据、仍按已筛选目录排序，而不是扩大候选集。

`Redis` 网络异常目前的真实行为是**显式失败，不自动切到内存会话**；因此普通报告中的“Redis 故障恢复”显示 `N/A`，不会伪造成功率。`test_fault_contracts.py` 保证它至少不会静默把异常当成空会话。若未来实现 Redis fallback 或重试，再把故障注入运行结果写入该指标。

并发正确率只由 `concurrency.py` 填写，普通串行固定集不会假装测过并发。

## 解读原则

- 单元测试全绿只代表代码满足了写下的断言；固定集报告才反映真实自然语言、真实外部模型和真实向量库下的表现。
- 当前最新真实报告是 `results/v3_fixed_eval_20260718_171016.md`：61/61 通过；其中一次 schema 修复重试成功，最大尝试次数为 2。下一次全量运行覆盖它，旧报告不在目录中累积。
- `results/first_visible_event_smoke_20260718.md` 是随后新增即时 progress 的两条真实请求验证；它只证明
  首个可显示事件已前置，不能替代 61 请求轮的路由、事实或稳定延迟结论。
- 没有分母的指标必须显示 `N/A`，不能显示 `0%` 或 `100%`。
- 每次修改 grammar、SemanticParse prompt、目录词表、PromotionGate、CandidateGate、Milvus collection 或会话逻辑后，都应重新跑完整固定集，并保留生成的 JSON/Markdown 报告。
- 固定集发现问题后，先更新 `root_cause_and_solution.md`：写清用户原句、实际链路、根因、改动点和验收命令，再开始改生产链路。新全量结果覆盖旧结果，不在此目录堆积历史报告。
