# MallMind 外部模型链路组合评估报告

## 总览

- 报告状态：ok
- case 数：4
- 实验组：balanced_demo, full_no_query_expansion, full_llm_all
- embedding provider/model：dashscope / text-embedding-v4
- Milvus collection：mallmind_product_chunks_qwen_v1

## 本轮环境限制

- 表中的 LLM 指标按“调用尝试次数”和“实际使用率”拆开理解：`LLM` 是每 case 的 LLM 调用尝试数，`llm_*_used_rate` 在 JSON 中记录实际成功使用率。
- 表中的 embedding / Milvus 调用表示该组进入了 RAG 检索路径；如果 embedding provider 不可达，系统会回落到本地结构化商品库评分。

## 总览表

| 实验组 | cases | success | route | hit@5 | p@1 | violation | card | avg ms | p95 ms | timeout | LLM | embedding | Milvus |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| balanced_demo | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 0.875 | 11332.055 | 33820.160 | 0.000 | 1.000 | 1.000 | 1.000 |
| full_no_query_expansion | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 0.875 | 44099.715 | 64749.060 | 0.000 | 2.250 | 1.000 | 1.000 |
| full_llm_all | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 0.875 | 42070.785 | 53876.340 | 0.000 | 2.250 | 1.000 | 1.000 |

## 分段结果

| case 类型 | cases | success | route | hit@5 | p@1 | card | fallback | failed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 基础推荐 | 9 | 1.000 | 1.000 | 1.000 | 1.000 | 0.875 | 0.778 | 0 |
| guidance 幻觉 | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 0.875 | 1.000 | 0 |

## 最差 case 列表

### balanced_demo

- 无失败 case。

### full_llm_all

- 无失败 case。

### full_no_query_expansion

- 无失败 case。

## 结论建议

- 默认部署建议：优先使用 balanced_demo 作为 demo / 准生产默认模式，前提是 LLM 与 Milvus 环境稳定。
- CI 与降级建议：fast_baseline 适合作为稳定回归和外部依赖不可用时的兜底模式；不要把 fast 描述为使用 Milvus。
- full 建议：full_llm_all 更适合离线评估或高质量请求，不建议在没有延迟、成本和失败率监控前默认开启。
- query expansion 建议：默认关闭；只有当 A2/C2 对比证明召回收益明显且 query 漂移可控时再打开。
- guidance 建议：默认关闭或仅在商品卡片稳定后开启；开启后要持续检查 reason 是否被商品证据支撑。
- 本次 full 的 p95 延迟明显高于 balanced，线上默认开启 full 风险偏高。

## stability_eval

| group | cases | success | route | tool | violation | catalog_gap | error | no_500 | timeout | fallback | failed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| balanced_demo | 4 | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 | 1.000 | 0.000 | 1.000 | 0 |
| full_no_query_expansion | 4 | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 | 1.000 | 0.000 | 0.750 | 0 |
| full_llm_all | 4 | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 | 1.000 | 0.000 | 0.750 | 0 |

## capability_eval

| group | cases | llm_attempted | llm_success | llm_applied | router a/s/ap | parse a/s/ap | guidance a/s/ap | rag_attempted | embedding | milvus | nonempty | timeout | fallback | rag_n | rag_hit@5 | rag_p@1 | rag_mrr | rag_top1_changed | evidence_reason |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| balanced_demo | 4 | 1.000 | 0.250 | 0.250 | 0.000/0.000/0.000 | 1.000/0.250/0.250 | 0.000/0.000/0.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 | 4 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 |
| full_no_query_expansion | 4 | 1.000 | 1.000 | 1.000 | 0.250/0.250/0.250 | 1.000/0.250/0.250 | 1.000/1.000/1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 | 4 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 |
| full_llm_all | 4 | 1.000 | 1.000 | 1.000 | 0.250/0.250/0.250 | 1.000/0.250/0.250 | 1.000/1.000/1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 | 4 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 |

## vs_fast_delta

- No fast_baseline rows available for aligned comparison.

## RAG diagnostics

| group | case_id | status | retrieved | expected_rank_in_retrieval | rec_rank | fallback | timeout | evidence_card | evidence_reason |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| balanced_demo | cap_synonym_commute_noise_beans | 1 | p_digital_004,p_digital_007,p_digital_009,p_digital_011,p_digital_015,p_digital_018,p_digital_020,p_digital_022 | 2.000 | 1.000 | 0 | 0 | 1 | 1 |
| balanced_demo | cap_synonym_oily_non_stuffy_sunscreen | 1 | p_beauty_006,p_beauty_010,p_beauty_014,p_beauty_018,p_beauty_020,p_beauty_023,p_clothes_001,p_clothes_018,p_clothes_020,p_clothes_021,p_clothes_024,p_clothes_025 | 1.000 | 1.000 | 0 | 0 | 1 | 1 |
| balanced_demo | cap_synonym_parent_large_font_phone | 1 | p_digital_001,p_digital_002,p_digital_003,p_digital_009,p_digital_014,p_digital_015,p_digital_016,p_digital_017,p_digital_018,p_digital_020,p_digital_022,p_digital_025 | 2.000 | 1.000 | 0 | 0 | 1 | 1 |
| balanced_demo | cap_synonym_office_mild_coffee | 1 | p_digital_004,p_digital_005,p_digital_011,p_digital_013,p_digital_014,p_digital_016,p_digital_018,p_digital_020,p_digital_021,p_digital_022,p_digital_023,p_digital_024,p_food_001,p | 13.000 | 1.000 | 0 | 0 | 1 | 1 |
| full_no_query_expansion | cap_synonym_commute_noise_beans | 1 | p_digital_004,p_digital_007,p_digital_009,p_digital_011,p_digital_015,p_digital_018,p_digital_020,p_digital_022 | 2.000 | 1.000 | 0 | 0 | 1 | 1 |
| full_no_query_expansion | cap_synonym_oily_non_stuffy_sunscreen | 1 | p_beauty_006,p_beauty_010,p_beauty_014,p_beauty_018,p_beauty_020,p_beauty_023,p_clothes_001,p_clothes_018,p_clothes_020,p_clothes_021,p_clothes_024,p_clothes_025 | 1.000 | 1.000 | 0 | 0 | 1 | 1 |
| full_no_query_expansion | cap_synonym_parent_large_font_phone | 1 | p_digital_001,p_digital_002,p_digital_003,p_digital_009,p_digital_014,p_digital_015,p_digital_016,p_digital_017,p_digital_018,p_digital_020,p_digital_022,p_digital_025 | 2.000 | 1.000 | 0 | 0 | 1 | 1 |
| full_no_query_expansion | cap_synonym_office_mild_coffee | 1 | p_digital_004,p_digital_005,p_digital_011,p_digital_013,p_digital_014,p_digital_016,p_digital_018,p_digital_020,p_digital_021,p_digital_022,p_digital_023,p_digital_024,p_food_001,p | 13.000 | 1.000 | 0 | 0 | 1 | 1 |
| full_llm_all | cap_synonym_commute_noise_beans | 1 | p_digital_004,p_digital_007,p_digital_009,p_digital_011,p_digital_015,p_digital_018,p_digital_020,p_digital_022 | 2.000 | 1.000 | 0 | 0 | 1 | 1 |
| full_llm_all | cap_synonym_oily_non_stuffy_sunscreen | 1 | p_beauty_006,p_beauty_010,p_beauty_014,p_beauty_018,p_beauty_020,p_beauty_023,p_clothes_001,p_clothes_018,p_clothes_020,p_clothes_021,p_clothes_024,p_clothes_025 | 1.000 | 1.000 | 0 | 0 | 1 | 1 |
| full_llm_all | cap_synonym_parent_large_font_phone | 1 | p_digital_001,p_digital_002,p_digital_003,p_digital_009,p_digital_014,p_digital_015,p_digital_016,p_digital_017,p_digital_018,p_digital_020,p_digital_022,p_digital_025 | 2.000 | 1.000 | 0 | 0 | 1 | 1 |
| full_llm_all | cap_synonym_office_mild_coffee | 1 | p_digital_004,p_digital_005,p_digital_011,p_digital_013,p_digital_014,p_digital_016,p_digital_018,p_digital_020,p_digital_021,p_digital_022,p_digital_023,p_digital_024,p_food_001,p | 13.000 | 1.000 | 0 | 0 | 1 | 1 |

## LLM diagnostics

| group | case_id | router a/s/ap | local | llm | final | parse a/s/ap | guidance a/s/ap | changed_route | changed_rec | clarification |
| --- | --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: |
| balanced_demo | cap_synonym_commute_noise_beans | 0/0/0 | recommend_shopping_products | None | recommend_shopping_products | 1/1/1 | 0/0/0 | 0 | 0 | 0 |
| balanced_demo | cap_synonym_oily_non_stuffy_sunscreen | 0/0/0 | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | 0/0/0 | 0 | 0 | 0 |
| balanced_demo | cap_synonym_parent_large_font_phone | 0/0/0 | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | 0/0/0 | 0 | 0 | 0 |
| balanced_demo | cap_synonym_office_mild_coffee | 0/0/0 | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | 0/0/0 | 0 | 0 | 0 |
| full_no_query_expansion | cap_synonym_commute_noise_beans | 1/1/1 | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 1/1/1 | 1/1/1 | 0 | 0 | 0 |
| full_no_query_expansion | cap_synonym_oily_non_stuffy_sunscreen | 0/0/0 | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | 1/1/1 | 0 | 0 | 0 |
| full_no_query_expansion | cap_synonym_parent_large_font_phone | 0/0/0 | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | 1/1/1 | 0 | 0 | 0 |
| full_no_query_expansion | cap_synonym_office_mild_coffee | 0/0/0 | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | 1/1/1 | 0 | 0 | 0 |
| full_llm_all | cap_synonym_commute_noise_beans | 0/0/0 | recommend_shopping_products | None | recommend_shopping_products | 1/1/1 | 1/1/1 | 0 | 0 | 0 |
| full_llm_all | cap_synonym_oily_non_stuffy_sunscreen | 1/1/1 | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 1/0/0 | 1/1/1 | 0 | 0 | 0 |
| full_llm_all | cap_synonym_parent_large_font_phone | 0/0/0 | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | 1/1/1 | 0 | 0 | 0 |
| full_llm_all | cap_synonym_office_mild_coffee | 0/0/0 | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | 1/1/1 | 0 | 0 | 0 |

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
- A2_vs_B: 稳定性：balanced_demo 相对 rag_only 路由准确率变化 1.0，成功率变化 1.0，违规率变化 0.0。
- B_vs_D: 降级稳定性：degraded_success_rate=0，fallback_triggered_rate=0。
### capability_eval
- RAG_A2: rag_only: RAG 未有效测到，rag_attempted=0.0，embedding_success=0.0，milvus_success=0.0，retrieval_nonempty=0.0，timeout=0.0。
- RAG_B: balanced_demo: RAG 有效样本 4 个，有效 hit@5=1.0，有效 p@1=1.0。
- LLM_router_B: balanced_demo: LLM router not effectively exercised，不能据此判断 router LLM 贡献。
- LLM_router_B1: router_llm_only: LLM router not effectively exercised，不能据此判断 router LLM 贡献。
- LLM_overall_B_vs_C: 能力：full_llm_all 相对 balanced_demo llm_attempted 变化 0.0，llm_used 变化 0.75，llm_applied 变化 0.75。
- QueryExpansion_C_vs_C2: 能力：query expansion 只能在 RAG 有效样本上解释；full_no_query_expansion RAG 有效样本=4，full_llm_all RAG 有效样本=4。
