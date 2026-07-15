# 评测报告索引

当前目录只保留最新且可复用的评测结果，不保存每轮调参、失败复测和临时诊断产物。

## 当前保留

| 文件 | 内容 | 对应代码与数据 |
| --- | --- | --- |
| `rag_router_eval_report_20260710_123554.md` | RAG 检索与 LLM 路由的合并分析、指标和问题样本 | `scripts/eval_retrieval.py`、`tests/eval/golden_retrieval_cases.jsonl`、`rag/recommendation/tool_router.py`、`rag/api/routes/chat.py` |
| `rag_router_eval_20260710.json` | 上述评测的原始 JSON，按 `retrieval` / `router` 两部分归并 | 同上；路由回归覆盖 `tests/test_tool_router.py` 与 `tests/fixtures/full_chain_eval_cases.json` |

## 评测脚本关系

- RAG 检索评测：`scripts/eval_retrieval.py`，输出应作为合并报告的 `retrieval` 部分。
- 典型用户场景评测：`scripts/eval_user_scenarios.py`，覆盖单轮、多轮、对比、澄清和购物车 CRUD；运行时将 JSON/Markdown 输出到 `.pytest_tmp/`。
- 模型链路消融：`scripts/eval_model_chain_ablation.py`，覆盖 fast、RAG、LLM 和 full 组合；不同规模使用同一对 `.pytest_tmp/model_chain_ablation.json/.md`，避免生成多份同类报告。
- 边界/多轮接口测试：`tests/run_bound_test.py`、`tests/run_bound_test_v2.py`；历史原始对话中包含服务未启动产生的连接失败数据，已删除。
- Milvus 健康检查：`scripts/check_vector_index_health.py`；历史结果曾出现与真实业务 hybrid retrieval 不一致的误报，未作为当前有效报告保留。

重新生成报告时，先确认后端、Milvus 和 LLM 配置可用；临时结果写入 `.pytest_tmp/`，最终结果再归并为一份 Markdown 分析和一份 JSON 原始数据。
