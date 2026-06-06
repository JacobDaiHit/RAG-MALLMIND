# MallMind 外部模型链路组合评估报告

## 总览

- 报告状态：ok
- case 数：24
- 实验组：fast_baseline, rag_only, balanced_demo, router_llm_only, parse_llm_only, full_llm_all, full_no_guidance, full_no_query_expansion, timeout_fallback
- embedding provider/model：dashscope / text-embedding-v4
- Milvus collection：mallmind_product_chunks_qwen_v1

## 本轮环境限制

- 表中的 LLM 指标按“调用尝试次数”和“实际使用率”拆开理解：`LLM` 是每 case 的 LLM 调用尝试数，`llm_*_used_rate` 在 JSON 中记录实际成功使用率。
- 表中的 embedding / Milvus 调用表示该组进入了 RAG 检索路径；如果 embedding provider 不可达，系统会回落到本地结构化商品库评分。

## 总览表

| 实验组 | cases | success | route | hit@5 | p@1 | violation | card | avg ms | p95 ms | timeout | LLM | embedding | Milvus |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fast_baseline | 27 | 1.000 | 1.000 | 1.000 | 0.875 | 0.000 | 0.854 | 2035.780 | 10942.770 | 0.000 | 0.000 | 0.000 | 0.000 |
| rag_only | 27 | 1.000 | 1.000 | 1.000 | 0.875 | 0.000 | 0.854 | 8366.984 | 20036.670 | 0.296 | 0.000 | 0.667 | 0.667 |
| balanced_demo | 27 | 1.000 | 1.000 | 1.000 | 0.875 | 0.000 | 0.854 | 5735.443 | 18372.770 | 0.000 | 0.741 | 0.667 | 0.667 |
| router_llm_only | 27 | 1.000 | 1.000 | 1.000 | 0.875 | 0.000 | 0.854 | 3870.084 | 16221.230 | 0.000 | 0.037 | 0.000 | 0.000 |
| parse_llm_only | 27 | 1.000 | 1.000 | 1.000 | 0.875 | 0.000 | 0.854 | 3192.472 | 13074.240 | 0.000 | 0.593 | 0.000 | 0.000 |
| full_llm_all | 27 | 1.000 | 1.000 | 1.000 | 0.875 | 0.000 | 0.854 | 22288.240 | 43060.720 | 0.407 | 1.185 | 0.667 | 0.667 |
| full_no_guidance | 27 | 1.000 | 1.000 | 1.000 | 0.875 | 0.000 | 0.854 | 11713.525 | 20136.140 | 0.407 | 0.593 | 0.667 | 0.667 |
| full_no_query_expansion | 27 | 1.000 | 1.000 | 1.000 | 0.875 | 0.000 | 0.854 | 12706.715 | 26764.950 | 0.000 | 1.185 | 0.667 | 0.667 |
| timeout_fallback | 27 | 1.000 | 1.000 | 1.000 | 0.875 | 0.000 | 0.854 | 3533.592 | 8152.970 | 0.407 | 1.185 | 0.667 | 0.667 |

## 分段结果

| case 类型 | cases | success | route | hit@5 | p@1 | card | fallback | failed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 模糊推荐 | 27 | 1.000 | 1.000 | 0.000 | 0.000 | 0.828 | 0.889 | 0 |
| 基础推荐 | 18 | 1.000 | 1.000 | 1.000 | 1.000 | 0.875 | 0.944 | 0 |
| 对比 | 9 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 | 0.333 | 0 |
| 条件筛选 | 9 | 1.000 | 1.000 | 1.000 | 1.000 | 0.875 | 0.889 | 0 |
| guidance 幻觉 | 9 | 1.000 | 1.000 | 1.000 | 0.000 | 0.875 | 0.889 | 0 |
| 多轮 | 36 | 1.000 | 1.000 | 0.000 | 0.000 | 0.875 | 0.361 | 0 |
| 负例/catalog gap | 27 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 | 0.889 | 0 |
| PC 整机 | 27 | 1.000 | 1.000 | 0.000 | 0.000 | 0.781 | 0.296 | 0 |
| PC 单配件 | 36 | 1.000 | 1.000 | 1.000 | 1.000 | 0.875 | 0.917 | 0 |
| query expansion 风险 | 9 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 | 0.778 | 0 |
| 路由边界 | 36 | 1.000 | 1.000 | 0.000 | 0.000 | 0.875 | 0.389 | 0 |

## 最差 case 列表

### balanced_demo

- 无失败 case。

### fast_baseline

- 无失败 case。

### full_llm_all

- 无失败 case。

### full_no_guidance

- 无失败 case。

### full_no_query_expansion

- 无失败 case。

### parse_llm_only

- 无失败 case。

### rag_only

- 无失败 case。

### router_llm_only

- 无失败 case。

### timeout_fallback

- 无失败 case。

## 结论建议

- 默认部署建议：优先使用 balanced_demo 作为 demo / 准生产默认模式，前提是 LLM 与 Milvus 环境稳定。
- CI 与降级建议：fast_baseline 适合作为稳定回归和外部依赖不可用时的兜底模式；不要把 fast 描述为使用 Milvus。
- full 建议：full_llm_all 更适合离线评估或高质量请求，不建议在没有延迟、成本和失败率监控前默认开启。
- query expansion 建议：默认关闭；只有当 A2/C2 对比证明召回收益明显且 query 漂移可控时再打开。
- guidance 建议：默认关闭或仅在商品卡片稳定后开启；开启后要持续检查 reason 是否被商品证据支撑。
- 本次 full 的 p95 延迟明显高于 balanced，线上默认开启 full 风险偏高。
- fast 仍有可用结果，说明规则 + 本地 catalog scoring 可以承担基础兜底。

## stability_eval

| group | cases | success | route | tool | violation | catalog_gap | error | no_500 | timeout | fallback | failed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fast_baseline | 27 | 1.000 | 1.000 | N/A | 0.000 | 1.000 | 0.000 | N/A | 0.000 | 1.000 | 0 |
| rag_only | 27 | 1.000 | 1.000 | N/A | 0.000 | 1.000 | 0.000 | N/A | 0.296 | 1.000 | 0 |
| balanced_demo | 27 | 1.000 | 1.000 | N/A | 0.000 | 1.000 | 0.000 | N/A | 0.000 | 0.741 | 0 |
| router_llm_only | 27 | 1.000 | 1.000 | N/A | 0.000 | 1.000 | 0.000 | N/A | 0.000 | 0.741 | 0 |
| parse_llm_only | 27 | 1.000 | 1.000 | N/A | 0.000 | 1.000 | 0.000 | N/A | 0.000 | 0.593 | 0 |
| full_llm_all | 27 | 1.000 | 1.000 | N/A | 0.000 | 1.000 | 0.000 | N/A | 0.407 | 0.481 | 0 |
| full_no_guidance | 27 | 1.000 | 1.000 | N/A | 0.000 | 1.000 | 0.000 | N/A | 0.407 | 0.593 | 0 |
| full_no_query_expansion | 27 | 1.000 | 1.000 | N/A | 0.000 | 1.000 | 0.000 | N/A | 0.000 | 0.148 | 0 |
| timeout_fallback | 27 | 1.000 | 1.000 | N/A | 0.000 | 1.000 | 0.000 | N/A | 0.407 | 0.593 | 0 |

## capability_eval

| group | cases | llm_attempted | llm_success | llm_applied | router a/s/ap | parse a/s/ap | guidance a/s/ap | rag_attempted | embedding | milvus | nonempty | timeout | fallback | rag_n | rag_hit@5 | rag_p@1 | rag_mrr | rag_top1_changed | evidence_reason |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fast_baseline | 27 | 0.000 | 0.000 | 0.000 | 0.000/0.000/0.000 | N/A/N/A/N/A | N/A/N/A/N/A | 0.000 | 0.000 | 0.000 | 0.259 | 0.000 | 0.000 | 0 | 0.000 | 0.000 | N/A | N/A | N/A |
| rag_only | 27 | 0.000 | 0.000 | 0.000 | 0.000/0.000/0.000 | N/A/N/A/N/A | N/A/N/A/N/A | 0.667 | 0.370 | 0.111 | 0.370 | 0.296 | 0.630 | 3 | 0.000 | 0.000 | N/A | N/A | N/A |
| balanced_demo | 27 | 0.630 | 0.185 | 0.148 | 0.148/0.148/0.111 | N/A/N/A/N/A | N/A/N/A/N/A | 0.667 | 0.667 | 0.407 | 0.667 | 0.000 | 0.333 | 11 | 1.000 | 0.875 | N/A | N/A | N/A |
| router_llm_only | 27 | 0.037 | 0.037 | 0.037 | 0.037/0.037/0.037 | N/A/N/A/N/A | N/A/N/A/N/A | 0.000 | 0.000 | 0.000 | 0.259 | 0.000 | 0.000 | 0 | 0.000 | 0.000 | N/A | N/A | N/A |
| parse_llm_only | 27 | 0.593 | 0.074 | 0.074 | 0.000/0.000/0.000 | N/A/N/A/N/A | N/A/N/A/N/A | 0.000 | 0.000 | 0.000 | 0.259 | 0.000 | 0.000 | 0 | 0.000 | 0.000 | N/A | N/A | N/A |
| full_llm_all | 27 | 0.593 | 0.444 | 0.444 | 0.000/0.000/0.000 | N/A/N/A/N/A | N/A/N/A/N/A | 0.667 | 0.259 | 0.000 | 0.259 | 0.407 | 0.741 | 0 | 0.000 | 0.000 | N/A | N/A | N/A |
| full_no_guidance | 27 | 0.593 | 0.037 | 0.037 | 0.000/0.000/0.000 | N/A/N/A/N/A | N/A/N/A/N/A | 0.667 | 0.259 | 0.000 | 0.259 | 0.407 | 0.741 | 0 | 0.000 | 0.000 | N/A | N/A | N/A |
| full_no_query_expansion | 27 | 0.593 | 0.481 | 0.481 | 0.000/0.000/0.000 | N/A/N/A/N/A | N/A/N/A/N/A | 0.667 | 0.667 | 0.407 | 0.667 | 0.000 | 0.333 | 11 | 1.000 | 0.875 | N/A | N/A | N/A |
| timeout_fallback | 27 | 0.593 | 0.000 | 0.000 | 0.000/0.000/0.000 | N/A/N/A/N/A | N/A/N/A/N/A | 0.667 | 0.259 | 0.000 | 0.259 | 0.407 | 0.741 | 0 | 0.000 | 0.000 | N/A | N/A | N/A |

## vs_fast_delta

- No fast_baseline rows available for aligned comparison.

## RAG diagnostics

- No RAG-attempted rows.

## LLM diagnostics

- No LLM-attempted rows.

## capability_challenge_assessment

- main_metrics_identical: True
- balanced_demo top1 wins vs fast: 0 (none)
- full_no_query_expansion top1 wins vs fast: 0 (none)
- full_llm_all top1 wins vs fast: 0 (none)
- Main recommendation metrics are still identical across compared groups; this run did not prove recommendation uplift over fast_baseline.
- vs_fast_delta found no top1 recommendation wins; use this as evidence that current challenge cases or current chain weighting still do not expose business uplift.
- RAG was effectively retrieved on some rows, but the evidence did not change top1 outcomes enough to improve main metrics.

## capability/stability conclusions

### stability_eval
- A_vs_A2: 稳定性：rag_only 相对 fast_baseline 成功率变化 0.0，错误率变化 0.0，fallback 变化 0.0。
- A2_vs_B: 稳定性：balanced_demo 相对 rag_only 路由准确率变化 0.0，成功率变化 0.0，违规率变化 0.0。
- B_vs_D: 降级稳定性：degraded_success_rate=1.0，fallback_triggered_rate=0.5926。
### capability_eval
- RAG_A2: rag_only: RAG 有效样本 3 个，有效 hit@5=0.0，有效 p@1=0.0。
- RAG_B: balanced_demo: RAG 有效样本 11 个，有效 hit@5=1.0，有效 p@1=0.875。
- LLM_router_B: balanced_demo: router attempted=0.1481，success=0.1481，applied=0.1111。
- LLM_router_B1: router_llm_only: router attempted=0.037，success=0.037，applied=0.037。
- LLM_overall_B_vs_C: 能力：full_llm_all 相对 balanced_demo llm_attempted 变化 -0.037，llm_used 变化 0.2592，llm_applied 变化 0.2963。
- QueryExpansion_C_vs_C2: 能力：query expansion 只能在 RAG 有效样本上解释；full_no_query_expansion RAG 有效样本=11，full_llm_all RAG 有效样本=0。
