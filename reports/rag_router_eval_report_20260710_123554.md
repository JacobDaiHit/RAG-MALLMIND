# RAG 与 LLM 路由评测报告

**评测时间:** 2026-07-10 12:35:54 +08:00  
**评测环境:** Windows / PowerShell / `D:\github\.venv\Scripts\python.exe`  
**代码基准:** 当前工作区代码，未为本次评测修改业务代码  
**原始结果:** `reports/rag_eval_20260710_122738.json`、`reports/router_llm_eval_20260710_123340.json`

## 1. RAG 检索评测

### 1.1 数据与命令

- 数据集: `tests/eval/golden_retrieval_cases.jsonl`
- Case 数: 30 条，其中主评测集 24 条，另含 PC 配件 4 条、negative/impossible 2 条
- 运行方式: 启用 Milvus/混合检索，关闭 LLM parse，用当前规则解析 + Milvus evidence + 排序链路隔离评测 RAG 检索质量
- 命令:

```powershell
D:\github\.venv\Scripts\python.exe scripts\eval_retrieval.py --cases tests\eval\golden_retrieval_cases.jsonl --with-milvus --output reports\rag_eval_20260710_122738.json --markdown reports\rag_eval_20260710_122738.md --top-k 1,3,5,10
```

### 1.2 核心指标

| 范围 | Cases | P@1 | Hit@5 | MRR | Avg Latency | P50 Latency | P95 Latency | Max Latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 主评测集 | 24 | 0.792 | 0.958 | 0.875 | 891.92 ms | - | - | - |
| 全部可评测集 | 30 | 0.750 | 0.964 | 0.857 | 748.64 ms | 266.21 ms | 2154.32 ms | 8572.19 ms |

补充指标:

- Milvus/RAG 状态: `ok`
- Evidence total hits: 540
- Embedding: `dashscope` / `text-embedding-v4` / 1024 dim
- 主评测集 `category_accuracy@5`: 1.000
- 主评测集 `constraint_violation_rate`: 0.042
- 全部集 `constraint_violation_rate`: 0.067

### 1.3 主要问题样本

| Case | Query | 现象 | 说明 |
| --- | --- | --- | --- |
| `vague_girlfriend_023` | 送女朋友，不知道买什么，想要精致一点 | Hit@5 = 0 | 结果跨 beauty/digital/clothing/food 发散，泛化礼物意图召回不稳定 |
| `clothes_under_100_017` | 100元以内的基础T恤，通勤穿 | Hit@5 = 1，但 constraint violation | 返回结果命中相关商品，但约束检查认为存在价格/条件违规 |
| `pc_gpu_4060_028` | 3000元以内的游戏显卡，优先RTX 4060或4060 Ti | Hit@5 = 1，但 constraint violation | Top2 混入 RTX 4070，预算/型号约束仍需更硬的后过滤 |
| `digital_phone_photo_001` | 推荐一款拍照好的旗舰手机，不要小米 | 最高延迟 8572.19 ms | 命中正确，但单 case 明显慢，疑似 embedding/Milvus 首次调用或外部请求抖动 |

### 1.4 注意事项

- 我先尝试过 `--with-milvus --use-llm` 全量评测，但 240 秒仍未完成，已停止该进程；因此正式 RAG 指标采用关闭 LLM parse 的检索本体评测。
- 这组 RAG 指标适合回答“当前商品召回、Milvus evidence、排序链路质量如何”，不代表完整聊天接口加上 LLM parse/guidance 后的端到端耗时。

## 2. LLM 路由准确率评测

### 2.1 数据与入口

- 数据集: `tests/fixtures/full_chain_eval_cases.json` + 12 条补充边界工具 case
- Case 数: 39 条
- 覆盖工具: 推荐、PC 整机、购物车、对比、参数查询、SKU 查询、价格比较、闲聊
- 入口: 复刻后端 `rag/api/routes/chat.py` 的实际链路，即 `route_shopping_tool_call()` 后接 `validate_tool_call()`
- LLM 配置: `openai_compatible` / `deepseek-chat` / host `api.deepseek.com`

### 2.2 核心指标

| 指标 | 结果 |
| --- | ---: |
| 后端最终路由准确率 | 1.000 |
| validate 前 raw route 准确率 | 0.974 |
| LLM router attempted rate | 1.000 |
| LLM router success rate | 0.692 |
| LLM 原始提议准确率（仅成功返回的 LLM case） | 1.000 |
| 边界工具 case 数 | 21 |
| 边界工具最终路由准确率 | 1.000 |
| 边界工具 raw route 准确率 | 0.952 |
| 边界工具 LLM 原始提议准确率（仅成功返回） | 1.000 |
| Avg latency | 2144.10 ms |
| P50 latency | 2074.55 ms |
| P95 latency | 2694.14 ms |
| Max latency | 3198.91 ms |

### 2.3 按工具拆分

| Expected tool | Cases | Final acc | Raw acc | LLM success cases | LLM proposed acc |
| --- | ---: | ---: | ---: | ---: | ---: |
| `recommend_shopping_products` | 18 | 1.000 | 1.000 | 13 | 1.000 |
| `generate_pc_build_plan` | 8 | 1.000 | 1.000 | 8 | 1.000 |
| `apply_cart_instruction` | 5 | 1.000 | 0.800 | 1 | 1.000 |
| `compare_products` | 3 | 1.000 | 1.000 | 2 | 1.000 |
| `general_chat` | 2 | 1.000 | 1.000 | 0 | - |
| `parameter_query` | 1 | 1.000 | 1.000 | 1 | 1.000 |
| `sku_detail` | 1 | 1.000 | 1.000 | 1 | 1.000 |
| `price_comparison` | 1 | 1.000 | 1.000 | 1 | 1.000 |

### 2.4 观察结论

- 当前最终路由准确率很好，39 条全部命中，原因是后端不是裸信 LLM，而是 `route_shopping_tool_call()` 失败后 fallback 到 local rules，再由 `validate_tool_call()` 做购物车等安全纠偏。
- LLM router 的稳定性一般: 39 条里 12 条失败，失败原因全部是 `llm_json_invalid`。这说明模型不是主要“选错工具”，而是经常没有返回符合当前 Pydantic/JSON schema 的结果。
- 购物车链路能被兜住: `删除购物车里的OPPO` 的 raw route 是 `recommend_shopping_products`，但最终被 `validate_tool_call()` 纠正为 `apply_cart_instruction`。
- `general_chat` 两条 case 的 LLM 均未成功返回合法 JSON，最终完全依靠规则 fallback 命中。

## 3. 总结

- RAG 主集表现: Hit@5 = 0.958、P@1 = 0.792、MRR = 0.875，整体可用；主要短板在泛化礼物场景和部分价格/型号硬约束。
- 检索延迟: 平均 748.64 ms，但存在 8.57s 长尾，需要关注首次 embedding/Milvus 调用、外部 embedding 抖动或连接复用。
- LLM 路由: 最终准确率 1.000，但 LLM 合法输出成功率只有 0.692；当前系统靠 fallback/validation 才稳定。
- 优先建议: 对 router LLM 加更强 JSON 修复/重试或 JSON mode 约束；对 RAG 加强 hard constraint post-filter，并单独优化礼物/泛场景 query 的召回与重排。
