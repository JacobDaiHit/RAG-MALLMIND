# MallMind 外部模型链路组合评估报告

## 总览

- 报告状态：failed
- case 数：43
- 实验组：fast_baseline, rag_only, balanced_demo, router_llm_only, parse_llm_only, full_llm_all, full_no_guidance, full_no_query_expansion
- embedding provider/model：dashscope / text-embedding-v4
- Milvus collection：mallmind_product_chunks_qwen_v1

## 本轮环境限制

- 表中的 LLM 指标按“调用尝试次数”和“实际使用率”拆开理解：`LLM` 是每 case 的 LLM 调用尝试数，`llm_*_used_rate` 在 JSON 中记录实际成功使用率。
- 表中的 embedding / Milvus 调用表示该组进入了 RAG 检索路径；如果 embedding provider 不可达，系统会回落到本地结构化商品库评分。

## 总览表

| 实验组 | cases | success | route | hit@5 | p@1 | violation | card | avg ms | p95 ms | timeout | LLM | embedding | Milvus |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fast_baseline | 43 | 0.767 | 0.837 | 0.700 | 0.667 | 0.000 | 0.875 | 301.580 | 2842.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| rag_only | 43 | 0.767 | 0.837 | 0.700 | 0.667 | 0.000 | 0.875 | 738.697 | 2859.470 | 0.000 | 0.000 | 0.698 | 0.698 |
| balanced_demo | 43 | 0.907 | 0.954 | 0.933 | 0.867 | 0.000 | 0.875 | 2005.643 | 5030.790 | 0.000 | 1.395 | 0.861 | 0.861 |
| router_llm_only | 43 | 0.861 | 0.954 | 0.867 | 0.800 | 0.000 | 0.875 | 788.659 | 4085.990 | 0.000 | 0.535 | 0.000 | 0.000 |
| parse_llm_only | 43 | 0.791 | 0.837 | 0.733 | 0.700 | 0.000 | 0.875 | 878.434 | 3891.720 | 0.000 | 0.744 | 0.000 | 0.000 |
| full_llm_all | 43 | 0.907 | 0.954 | 0.933 | 0.867 | 0.000 | 0.875 | 8616.852 | 15364.960 | 0.000 | 2.558 | 0.861 | 0.861 |
| full_no_guidance | 43 | 0.907 | 0.954 | 0.933 | 0.867 | 0.000 | 0.875 | 2497.572 | 5388.110 | 0.000 | 1.698 | 0.861 | 0.861 |
| full_no_query_expansion | 43 | 0.907 | 0.954 | 0.933 | 0.867 | 0.000 | 0.875 | 9423.409 | 16220.600 | 0.000 | 2.558 | 0.861 | 0.861 |

## 分段结果

| case 类型 | cases | success | route | hit@5 | p@1 | card | fallback | failed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 基础推荐 | 168 | 0.821 | 0.893 | 0.789 | 0.727 | 0.875 | 0.941 | 30 |
| 条件筛选 | 32 | 0.906 | 0.906 | 0.906 | 0.750 | 0.875 | 0.969 | 3 |
| guidance 幻觉 | 24 | 0.667 | 1.000 | 0.667 | 0.667 | 0.875 | 1.000 | 8 |
| 多轮 | 32 | 1.000 | 1.000 | 0.000 | 0.000 | 0.875 | 0.250 | 0 |
| 负例/catalog gap | 32 | 0.688 | 0.688 | 0.000 | 0.000 | 0.000 | 0.562 | 10 |
| PC 单配件 | 56 | 1.000 | 1.000 | 1.000 | 1.000 | 0.875 | 1.000 | 0 |

## 最差 case 列表

### balanced_demo

| case_id | query | expected | actual route | recommended | retrieved | failure | reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cap_bundle_tablet_coffee | 学习平板和办公室咖啡一起推荐 | {"tool": "recommend_shopping_products", "ids": ["p_digital_011", "p_digital_019", "p_digital_025", "p_food_001", "p_food_022", "p_food_023", "p_digital_005"], "category": null} | recommend_shopping_products | p_digital_021,p_food_002 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_digital_021', 'p_food_002'] |
| cap_rag_review_dry_skin_cream | 哪款面霜评价里更适合干皮？ | {"tool": "recommend_shopping_products", "ids": ["p_beauty_007", "p_beauty_012", "p_beauty_022"], "category": null} | recommend_shopping_products | p_beauty_008 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_beauty_008'] |
| cap_negative_4090_budget | 500 元买 RTX 4090 | {"tool": "recommend_shopping_products", "ids": [], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_negative_medicine | 处方药怎么买 | {"tool": "recommend_shopping_products", "ids": [], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |

### fast_baseline

| case_id | query | expected | actual route | recommended | retrieved | failure | reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cap_synonym_commute_noise_beans | 通勤降噪豆，音质好一点 | {"tool": "recommend_shopping_products", "ids": ["p_digital_007", "p_digital_018"], "category": null} | recommend_shopping_products | p_clothes_001,p_clothes_020,p_clothes_023 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_clothes_001', 'p_clothes_020', 'p_clothes_023'] |
| cap_synonym_training_cushion_basketball | 训练穿的缓震篮球鞋，别太重 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_011", "p_clothes_012", "p_clothes_013"], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_attr_daily_running_cushion | 日常训练跑步鞋，缓震好一点 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_007", "p_clothes_008", "p_clothes_009", "p_clothes_010"], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_multi_basketball_cushion_light_1000 | 1000 内，缓震好，别太重的篮球实战鞋 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_011", "p_clothes_012", "p_clothes_013"], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_multi_no_nike_sportswear | 运动裤或运动上衣，不要耐克 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_002", "p_clothes_004", "p_clothes_005", "p_clothes_020", "p_clothes_023", "p_clothes_022"], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_gift_parents_digital | 送父母一个实用数码产品 | {"tool": "recommend_shopping_products", "ids": [], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_bundle_beginner_running | 开始跑步训练，帮我配一套入门装备 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_007", "p_clothes_008", "p_clothes_009", "p_clothes_010", "p_food_005", "p_food_025", "p_food_006"], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_bundle_tablet_coffee | 学习平板和办公室咖啡一起推荐 | {"tool": "recommend_shopping_products", "ids": ["p_digital_011", "p_digital_019", "p_digital_025", "p_food_001", "p_food_022", "p_food_023", "p_digital_005"], "category": null} | recommend_shopping_products | p_digital_021,p_food_002 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_digital_021', 'p_food_002'] |

### full_llm_all

| case_id | query | expected | actual route | recommended | retrieved | failure | reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cap_bundle_tablet_coffee | 学习平板和办公室咖啡一起推荐 | {"tool": "recommend_shopping_products", "ids": ["p_digital_011", "p_digital_019", "p_digital_025", "p_food_001", "p_food_022", "p_food_023", "p_digital_005"], "category": null} | recommend_shopping_products | p_digital_021,p_food_002 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_digital_021', 'p_food_002'] |
| cap_rag_review_dry_skin_cream | 哪款面霜评价里更适合干皮？ | {"tool": "recommend_shopping_products", "ids": ["p_beauty_007", "p_beauty_012", "p_beauty_022"], "category": null} | recommend_shopping_products | p_beauty_008 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_beauty_008'] |
| cap_negative_4090_budget | 500 元买 RTX 4090 | {"tool": "recommend_shopping_products", "ids": [], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_negative_medicine | 处方药怎么买 | {"tool": "recommend_shopping_products", "ids": [], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |

### full_no_guidance

| case_id | query | expected | actual route | recommended | retrieved | failure | reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cap_bundle_tablet_coffee | 学习平板和办公室咖啡一起推荐 | {"tool": "recommend_shopping_products", "ids": ["p_digital_011", "p_digital_019", "p_digital_025", "p_food_001", "p_food_022", "p_food_023", "p_digital_005"], "category": null} | recommend_shopping_products | p_digital_021,p_food_002 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_digital_021', 'p_food_002'] |
| cap_rag_review_dry_skin_cream | 哪款面霜评价里更适合干皮？ | {"tool": "recommend_shopping_products", "ids": ["p_beauty_007", "p_beauty_012", "p_beauty_022"], "category": null} | recommend_shopping_products | p_beauty_008 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_beauty_008'] |
| cap_negative_4090_budget | 500 元买 RTX 4090 | {"tool": "recommend_shopping_products", "ids": [], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_negative_medicine | 处方药怎么买 | {"tool": "recommend_shopping_products", "ids": [], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |

### full_no_query_expansion

| case_id | query | expected | actual route | recommended | retrieved | failure | reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cap_bundle_tablet_coffee | 学习平板和办公室咖啡一起推荐 | {"tool": "recommend_shopping_products", "ids": ["p_digital_011", "p_digital_019", "p_digital_025", "p_food_001", "p_food_022", "p_food_023", "p_digital_005"], "category": null} | recommend_shopping_products | p_digital_021,p_food_002 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_digital_021', 'p_food_002'] |
| cap_rag_review_dry_skin_cream | 哪款面霜评价里更适合干皮？ | {"tool": "recommend_shopping_products", "ids": ["p_beauty_007", "p_beauty_012", "p_beauty_022"], "category": null} | recommend_shopping_products | p_beauty_008 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_beauty_008'] |
| cap_negative_4090_budget | 500 元买 RTX 4090 | {"tool": "recommend_shopping_products", "ids": [], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_negative_medicine | 处方药怎么买 | {"tool": "recommend_shopping_products", "ids": [], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |

### parse_llm_only

| case_id | query | expected | actual route | recommended | retrieved | failure | reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cap_synonym_training_cushion_basketball | 训练穿的缓震篮球鞋，别太重 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_011", "p_clothes_012", "p_clothes_013"], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_attr_daily_running_cushion | 日常训练跑步鞋，缓震好一点 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_007", "p_clothes_008", "p_clothes_009", "p_clothes_010"], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_multi_basketball_cushion_light_1000 | 1000 内，缓震好，别太重的篮球实战鞋 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_011", "p_clothes_012", "p_clothes_013"], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_multi_no_nike_sportswear | 运动裤或运动上衣，不要耐克 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_002", "p_clothes_004", "p_clothes_005", "p_clothes_020", "p_clothes_023", "p_clothes_022"], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_gift_parents_digital | 送父母一个实用数码产品 | {"tool": "recommend_shopping_products", "ids": [], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_bundle_beginner_running | 开始跑步训练，帮我配一套入门装备 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_007", "p_clothes_008", "p_clothes_009", "p_clothes_010", "p_food_005", "p_food_025", "p_food_006"], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_bundle_tablet_coffee | 学习平板和办公室咖啡一起推荐 | {"tool": "recommend_shopping_products", "ids": ["p_digital_011", "p_digital_019", "p_digital_025", "p_food_001", "p_food_022", "p_food_023", "p_digital_005"], "category": null} | recommend_shopping_products | p_digital_021,p_food_002 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_digital_021', 'p_food_002'] |
| cap_rag_review_dry_skin_cream | 哪款面霜评价里更适合干皮？ | {"tool": "recommend_shopping_products", "ids": ["p_beauty_007", "p_beauty_012", "p_beauty_022"], "category": null} | recommend_shopping_products | p_beauty_008 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_beauty_008'] |

### rag_only

| case_id | query | expected | actual route | recommended | retrieved | failure | reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cap_synonym_commute_noise_beans | 通勤降噪豆，音质好一点 | {"tool": "recommend_shopping_products", "ids": ["p_digital_007", "p_digital_018"], "category": null} | recommend_shopping_products | p_clothes_001,p_clothes_020,p_clothes_023 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_clothes_001', 'p_clothes_020', 'p_clothes_023'] |
| cap_synonym_training_cushion_basketball | 训练穿的缓震篮球鞋，别太重 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_011", "p_clothes_012", "p_clothes_013"], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_attr_daily_running_cushion | 日常训练跑步鞋，缓震好一点 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_007", "p_clothes_008", "p_clothes_009", "p_clothes_010"], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_multi_basketball_cushion_light_1000 | 1000 内，缓震好，别太重的篮球实战鞋 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_011", "p_clothes_012", "p_clothes_013"], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_multi_no_nike_sportswear | 运动裤或运动上衣，不要耐克 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_002", "p_clothes_004", "p_clothes_005", "p_clothes_020", "p_clothes_023", "p_clothes_022"], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_gift_parents_digital | 送父母一个实用数码产品 | {"tool": "recommend_shopping_products", "ids": [], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_bundle_beginner_running | 开始跑步训练，帮我配一套入门装备 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_007", "p_clothes_008", "p_clothes_009", "p_clothes_010", "p_food_005", "p_food_025", "p_food_006"], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_bundle_tablet_coffee | 学习平板和办公室咖啡一起推荐 | {"tool": "recommend_shopping_products", "ids": ["p_digital_011", "p_digital_019", "p_digital_025", "p_food_001", "p_food_022", "p_food_023", "p_digital_005"], "category": null} | recommend_shopping_products | p_digital_021,p_food_002 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_digital_021', 'p_food_002'] |

### router_llm_only

| case_id | query | expected | actual route | recommended | retrieved | failure | reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cap_synonym_commute_noise_beans | 通勤降噪豆，音质好一点 | {"tool": "recommend_shopping_products", "ids": ["p_digital_007", "p_digital_018"], "category": null} | recommend_shopping_products | p_clothes_001,p_clothes_020,p_clothes_023 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_clothes_001', 'p_clothes_020', 'p_clothes_023'] |
| cap_bundle_beginner_running | 开始跑步训练，帮我配一套入门装备 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_007", "p_clothes_008", "p_clothes_009", "p_clothes_010", "p_food_005", "p_food_025", "p_food_006"], "category": null} | recommend_shopping_products | p_beauty_007,p_digital_018,p_clothes_020,p_food_018 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_beauty_007', 'p_digital_018', 'p_clothes_020', 'p_food_018'] |
| cap_bundle_tablet_coffee | 学习平板和办公室咖啡一起推荐 | {"tool": "recommend_shopping_products", "ids": ["p_digital_011", "p_digital_019", "p_digital_025", "p_food_001", "p_food_022", "p_food_023", "p_digital_005"], "category": null} | recommend_shopping_products | p_digital_021,p_food_002 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_digital_021', 'p_food_002'] |
| cap_rag_review_dry_skin_cream | 哪款面霜评价里更适合干皮？ | {"tool": "recommend_shopping_products", "ids": ["p_beauty_007", "p_beauty_012", "p_beauty_022"], "category": null} | recommend_shopping_products | p_beauty_008 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_beauty_008'] |
| cap_negative_4090_budget | 500 元买 RTX 4090 | {"tool": "recommend_shopping_products", "ids": [], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |
| cap_negative_medicine | 处方药怎么买 | {"tool": "recommend_shopping_products", "ids": [], "category": null} | general_chat |  |  | wrong_route | 期望路由 recommend_shopping_products，实际 general_chat |

## 结论建议

- 默认部署建议：优先使用 balanced_demo 作为 demo / 准生产默认模式，前提是 LLM 与 Milvus 环境稳定。
- CI 与降级建议：fast_baseline 适合作为稳定回归和外部依赖不可用时的兜底模式；不要把 fast 描述为使用 Milvus。
- full 建议：full_llm_all 更适合离线评估或高质量请求，不建议在没有延迟、成本和失败率监控前默认开启。
- query expansion 建议：默认关闭；只有当 A2/C2 对比证明召回收益明显且 query 漂移可控时再打开。
- guidance 建议：默认关闭或仅在商品卡片稳定后开启；开启后要持续检查 reason 是否被商品证据支撑。
- 本次 full 的 p95 延迟明显高于 balanced，线上默认开启 full 风险偏高。
- fast 仍有可用结果，说明规则 + 本地 catalog scoring 可以承担基础兜底。
- 后续修复优先级：先看 wrong_route 和 negative_guard_failed，再看 final_recommendation_miss；前两类更可能影响演示稳定性。

## stability_eval

| group | cases | success | route | tool | violation | catalog_gap | error | no_500 | timeout | fallback | failed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fast_baseline | 43 | 0.767 | 0.837 | 0.837 | 0.000 | 1.000 | 0.000 | 1.000 | 0.000 | 1.000 | 10 |
| rag_only | 43 | 0.767 | 0.837 | 0.837 | 0.000 | 1.000 | 0.000 | 1.000 | 0.000 | 1.000 | 10 |
| balanced_demo | 43 | 0.907 | 0.954 | 0.954 | 0.000 | 0.500 | 0.000 | 1.000 | 0.000 | 0.861 | 4 |
| router_llm_only | 43 | 0.861 | 0.954 | 0.954 | 0.000 | 0.500 | 0.000 | 1.000 | 0.000 | 0.861 | 6 |
| parse_llm_only | 43 | 0.791 | 0.837 | 0.837 | 0.000 | 1.000 | 0.000 | 1.000 | 0.000 | 0.744 | 9 |
| full_llm_all | 43 | 0.907 | 0.954 | 0.954 | 0.000 | 0.500 | 0.000 | 1.000 | 0.000 | 0.767 | 4 |
| full_no_guidance | 43 | 0.907 | 0.954 | 0.954 | 0.000 | 0.500 | 0.000 | 1.000 | 0.000 | 0.861 | 4 |
| full_no_query_expansion | 43 | 0.907 | 0.954 | 0.954 | 0.000 | 0.500 | 0.000 | 1.000 | 0.000 | 0.767 | 4 |

## capability_eval

| group | cases | llm_attempted | llm_success | llm_applied | router a/s/ap | parse a/s/ap | guidance a/s/ap | rag_attempted | embedding | milvus | nonempty | timeout | fallback | rag_n | rag_hit@5 | rag_p@1 | rag_mrr | rag_top1_changed | evidence_reason | llm_timeout | llm_json_invalid | llm_provider_error |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fast_baseline | 43 | 0.000 | 0.000 | 0.000 | 0.000/0.000/0.000 | 0.000/0.000/0.000 | 0.000/0.000/0.000 | 0.000 | 0.000 | 0.000 | 0.093 | 0.000 | 0.000 | 0 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | N/A | N/A | N/A |
| rag_only | 43 | 0.000 | 0.000 | 0.000 | 0.000/0.000/0.000 | 0.000/0.000/0.000 | 0.000/0.000/0.000 | 0.698 | 0.093 | 0.000 | 0.093 | 0.000 | 0.907 | 0 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | N/A | N/A | N/A |
| balanced_demo | 43 | 1.000 | 0.628 | 0.209 | 0.535/0.535/0.000 | 0.861/0.209/0.209 | 0.000/0.000/0.000 | 0.861 | 0.093 | 0.000 | 0.093 | 0.000 | 0.907 | 0 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | N/A | N/A | N/A |
| router_llm_only | 43 | 0.535 | 0.535 | 0.000 | 0.535/0.535/0.000 | 0.000/0.000/0.000 | 0.000/0.000/0.000 | 0.000 | 0.000 | 0.000 | 0.093 | 0.000 | 0.000 | 0 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | N/A | N/A | N/A |
| parse_llm_only | 43 | 0.744 | 0.163 | 0.163 | 0.000/0.000/0.000 | 0.744/0.163/0.163 | 0.000/0.000/0.000 | 0.000 | 0.000 | 0.000 | 0.093 | 0.000 | 0.000 | 0 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | N/A | N/A | N/A |
| full_llm_all | 43 | 1.000 | 1.000 | 0.861 | 0.837/0.837/0.000 | 0.861/0.209/0.209 | 0.861/0.861/0.861 | 0.861 | 0.093 | 0.000 | 0.093 | 0.000 | 0.907 | 0 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | N/A | N/A | N/A |
| full_no_guidance | 43 | 1.000 | 0.861 | 0.209 | 0.837/0.837/0.000 | 0.861/0.209/0.209 | 0.000/0.000/0.000 | 0.861 | 0.093 | 0.000 | 0.093 | 0.000 | 0.907 | 0 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | N/A | N/A | N/A |
| full_no_query_expansion | 43 | 1.000 | 1.000 | 0.861 | 0.837/0.837/0.000 | 0.861/0.209/0.209 | 0.861/0.861/0.861 | 0.861 | 0.093 | 0.000 | 0.093 | 0.000 | 0.907 | 0 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | N/A | N/A | N/A |

## vs_fast_delta

| case_id | fast_top1 | balanced_top1 | balanced_win | c2_top1 | c2_win | full_top1 | full_win | fast_rank | balanced_rank | c2_rank | full_rank |
| --- | --- | --- | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| cap_attr_black_coffee_low_sugar | p_food_002 | p_food_002 | 0 | p_food_002 | 0 | p_food_002 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| cap_attr_daily_running_cushion |  | p_clothes_007 | 0 | p_clothes_007 | 0 | p_clothes_007 | 0 | N/A | 1.000 | 1.000 | 1.000 |
| cap_attr_light_student_laptop | p_digital_004 | p_digital_004 | 0 | p_digital_004 | 0 | p_digital_004 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| cap_attr_low_sugar_office_tea | p_food_003 | p_food_003 | 0 | p_food_003 | 0 | p_food_003 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| cap_attr_sensitive_repair_cream | p_beauty_007 | p_beauty_007 | 0 | p_beauty_007 | 0 | p_beauty_007 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| cap_bundle_beginner_running |  | p_clothes_007 | 0 | p_clothes_007 | 0 | p_clothes_007 | 0 | N/A | 1.000 | 1.000 | 1.000 |
| cap_bundle_sanya_summer | p_beauty_007 | p_beauty_019 | 0 | p_beauty_019 | 0 | p_beauty_010 | 0 | N/A | N/A | N/A | N/A |
| cap_bundle_school_start | p_beauty_007 | p_beauty_007 | 0 | p_beauty_007 | 0 | p_beauty_007 | 0 | N/A | N/A | N/A | N/A |
| cap_bundle_sport_drink | p_clothes_007 | p_clothes_007 | 0 | p_clothes_007 | 0 | p_clothes_007 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| cap_bundle_tablet_coffee | p_digital_021 | p_digital_021 | 0 | p_digital_021 | 0 | p_digital_021 | 0 | N/A | N/A | N/A | N/A |
| cap_gift_beauty_lip_serum | p_beauty_018 | p_beauty_018 | 0 | p_beauty_018 | 0 | p_beauty_018 | 0 | 2.000 | 2.000 | 2.000 | 2.000 |
| cap_gift_colleague_snack_box | p_food_010 | p_food_010 | 0 | p_food_010 | 0 | p_food_010 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| cap_gift_girlfriend_open |  |  | 0 |  | 0 |  | 0 | N/A | N/A | N/A | N/A |
| cap_gift_parents_digital |  | p_digital_022 | 0 | p_digital_022 | 0 | p_digital_022 | 0 | N/A | N/A | N/A | N/A |
| cap_gift_roommate_birthday |  |  | 0 |  | 0 |  | 0 | N/A | N/A | N/A | N/A |
| cap_multi_basketball_cushion_light_1000 |  | p_clothes_012 | 0 | p_clothes_012 | 0 | p_clothes_012 | 0 | N/A | 1.000 | 1.000 | 1.000 |
| cap_multi_no_apple_office_pc | p_digital_004 | p_digital_004 | 0 | p_digital_004 | 0 | p_digital_004 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| cap_multi_no_nike_sportswear |  | p_clothes_001 | 0 | p_clothes_001 | 0 | p_clothes_001 | 0 | N/A | 2.000 | 2.000 | 2.000 |
| cap_multi_oily_summer_sunscreen_200 | p_beauty_006 | p_beauty_006 | 0 | p_beauty_006 | 0 | p_beauty_006 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| cap_multi_phone_no_xiaomi_photo_battery_4000 | p_digital_016 | p_digital_016 | 0 | p_digital_016 | 0 | p_digital_016 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| cap_negative_4090_budget |  |  | 0 |  | 0 |  | 0 | N/A | N/A | N/A | N/A |
| cap_negative_fridge |  |  | 0 |  | 0 |  | 0 | N/A | N/A | N/A | N/A |
| cap_negative_medicine |  |  | 0 |  | 0 |  | 0 | N/A | N/A | N/A | N/A |
| cap_negative_pet_food |  |  | 0 |  | 0 |  | 0 | N/A | N/A | N/A | N/A |
| cap_pc_2tb_fast_nvme | pc_seed_ssd_wd_black_sn850x_2tb | pc_seed_ssd_wd_black_sn850x_2tb | 0 | pc_seed_ssd_wd_black_sn850x_2tb | 0 | pc_seed_ssd_wd_black_sn850x_2tb | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| cap_pc_750w_gold_atx30_psu | pc_seed_psu_super_flower_leadex_g_750w | pc_seed_psu_super_flower_leadex_g_750w | 0 | pc_seed_psu_super_flower_leadex_g_750w | 0 | pc_seed_psu_super_flower_leadex_g_750w | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| cap_pc_b760_ddr5_wifi | pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5 | pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5 | 0 | pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5 | 0 | pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| cap_pc_build_multiturn_hard_t1 | pc_seed_cpu_intel_core_i5_13400f | pc_seed_cpu_intel_core_i5_13400f | 0 | pc_seed_cpu_intel_core_i5_13400f | 0 | pc_seed_cpu_intel_core_i5_13400f | 0 | N/A | N/A | N/A | N/A |
| cap_pc_build_multiturn_hard_t2 | pc_seed_cpu_intel_core_i7_14700f | pc_seed_cpu_intel_core_i7_14700f | 0 | pc_seed_cpu_intel_core_i7_14700f | 0 | pc_seed_cpu_intel_core_i7_14700f | 0 | N/A | N/A | N/A | N/A |
| cap_pc_build_multiturn_hard_t3 | pc_seed_cpu_amd_ryzen_5_5600g | pc_seed_cpu_amd_ryzen_5_5600g | 0 | pc_seed_cpu_amd_ryzen_5_5600g | 0 | pc_seed_cpu_amd_ryzen_5_5600g | 0 | N/A | N/A | N/A | N/A |
| cap_pc_build_multiturn_hard_t4 | pc_seed_cpu_amd_ryzen_5_5600g | pc_seed_cpu_amd_ryzen_5_5600g | 0 | pc_seed_cpu_amd_ryzen_5_5600g | 0 | pc_seed_cpu_amd_ryzen_5_5600g | 0 | N/A | N/A | N/A | N/A |
| cap_pc_cpu_ryzen7 | pc_seed_cpu_amd_ryzen_7_7700 | pc_seed_cpu_amd_ryzen_7_7700 | 0 | pc_seed_cpu_amd_ryzen_7_7700 | 0 | pc_seed_cpu_amd_ryzen_7_7700 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| cap_pc_ddr5_64gb_editing | pc_seed_memory_corsair_vengeance_ddr5_64gb_6000 | pc_seed_memory_corsair_vengeance_ddr5_64gb_6000 | 0 | pc_seed_memory_corsair_vengeance_ddr5_64gb_6000 | 0 | pc_seed_memory_corsair_vengeance_ddr5_64gb_6000 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| cap_pc_gpu_4070_ti | pc_seed_gpu_colorful_geforce_rtx_4070_ti_super_16g | pc_seed_gpu_colorful_geforce_rtx_4070_ti_super_16g | 0 | pc_seed_gpu_colorful_geforce_rtx_4070_ti_super_16g | 0 | pc_seed_gpu_colorful_geforce_rtx_4070_ti_super_16g | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| cap_pc_long_gpu_case | pc_seed_case_phanteks_p360a | pc_seed_case_phanteks_p360a | 0 | pc_seed_case_phanteks_p360a | 0 | pc_seed_case_phanteks_p360a | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| cap_rag_review_dry_skin_cream | p_beauty_008 | p_beauty_008 | 0 | p_beauty_008 | 0 | p_beauty_008 | 0 | N/A | N/A | N/A | N/A |
| cap_rag_shoe_review_cushion | p_clothes_012 | p_clothes_012 | 0 | p_clothes_012 | 0 | p_clothes_012 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| cap_rag_sunscreen_less_oily |  | p_beauty_006 | 0 | p_beauty_006 | 0 | p_beauty_006 | 0 | N/A | 1.000 | 1.000 | 1.000 |
| cap_synonym_commute_noise_beans | p_clothes_001 | p_digital_007 | 0 | p_digital_007 | 0 | p_digital_007 | 0 | N/A | 1.000 | 1.000 | 1.000 |
| cap_synonym_office_mild_coffee | p_food_002 | p_food_002 | 0 | p_food_002 | 0 | p_food_002 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| cap_synonym_oily_non_stuffy_sunscreen | p_beauty_006 | p_beauty_006 | 0 | p_beauty_006 | 0 | p_beauty_006 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| cap_synonym_parent_large_font_phone | p_digital_009 | p_digital_009 | 0 | p_digital_009 | 0 | p_digital_009 | 0 | 1.000 | 1.000 | 1.000 | 1.000 |
| cap_synonym_training_cushion_basketball |  | p_clothes_012 | 0 | p_clothes_012 | 0 | p_clothes_012 | 0 | N/A | 1.000 | 1.000 | 1.000 |

## RAG diagnostics

| group | case_id | status | retrieved | expected_rank_in_retrieval | rec_rank | fallback | timeout | evidence_card | evidence_reason |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| fast_baseline | cap_pc_build_multiturn_hard_t1 | 0 | pc_cpu_pc_seed_cpu_intel_core_i5_13400f,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_gpu_pc_seed_gpu_nvidia_geforce_rtx_4060_ti_8g,pc_memory_pc_seed_memory_kings | N/A | N/A | 0 | 0 | 1 | 0 |
| fast_baseline | cap_pc_build_multiturn_hard_t2 | 0 | pc_cpu_pc_seed_cpu_intel_core_i7_14700f,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_gpu_pc_seed_gpu_sapphire_radeon_rx_7600_8g,pc_memory_pc_seed_memory_kingston | N/A | N/A | 0 | 0 | 1 | 0 |
| fast_baseline | cap_pc_build_multiturn_hard_t3 | 0 | pc_cpu_pc_seed_cpu_amd_ryzen_5_5600g,pc_motherboard_pc_seed_motherboard_asus_prime_a520m_k,pc_gpu_pc_seed_gpu_zotac_geforce_rtx_4060_ti_16g_twin_edge,pc_memory_pc_seed_memory_kingb | N/A | N/A | 0 | 0 | 1 | 0 |
| fast_baseline | cap_pc_build_multiturn_hard_t4 | 0 | pc_cpu_pc_seed_cpu_amd_ryzen_5_5600g,pc_motherboard_pc_seed_motherboard_asus_prime_a520m_k,pc_gpu_pc_seed_gpu_zotac_geforce_rtx_4060_ti_16g_twin_edge,pc_memory_pc_seed_memory_kingb | N/A | N/A | 0 | 0 | 1 | 0 |
| rag_only | cap_synonym_commute_noise_beans | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| rag_only | cap_synonym_oily_non_stuffy_sunscreen | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_synonym_parent_large_font_phone | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_synonym_office_mild_coffee | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_synonym_training_cushion_basketball | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| rag_only | cap_attr_sensitive_repair_cream | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_attr_low_sugar_office_tea | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_attr_light_student_laptop | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_attr_daily_running_cushion | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| rag_only | cap_attr_black_coffee_low_sugar | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_multi_phone_no_xiaomi_photo_battery_4000 | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_multi_basketball_cushion_light_1000 | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| rag_only | cap_multi_oily_summer_sunscreen_200 | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_multi_no_apple_office_pc | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_multi_no_nike_sportswear | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| rag_only | cap_gift_girlfriend_open | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| rag_only | cap_gift_roommate_birthday | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| rag_only | cap_gift_parents_digital | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| rag_only | cap_gift_colleague_snack_box | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_gift_beauty_lip_serum | 0 |  | N/A | 2.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_bundle_sanya_summer | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| rag_only | cap_bundle_school_start | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| rag_only | cap_bundle_beginner_running | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| rag_only | cap_bundle_tablet_coffee | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| rag_only | cap_bundle_sport_drink | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_pc_b760_ddr5_wifi | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_pc_long_gpu_case | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_pc_750w_gold_atx30_psu | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_pc_2tb_fast_nvme | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_pc_ddr5_64gb_editing | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_pc_gpu_4070_ti | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_pc_cpu_ryzen7 | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_pc_build_multiturn_hard_t1 | 0 | pc_cpu_pc_seed_cpu_intel_core_i5_13400f,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_gpu_pc_seed_gpu_nvidia_geforce_rtx_4060_ti_8g,pc_memory_pc_seed_memory_kings | N/A | N/A | 0 | 0 | 1 | 0 |
| rag_only | cap_pc_build_multiturn_hard_t2 | 0 | pc_cpu_pc_seed_cpu_intel_core_i7_14700f,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_gpu_pc_seed_gpu_sapphire_radeon_rx_7600_8g,pc_memory_pc_seed_memory_kingston | N/A | N/A | 0 | 0 | 1 | 0 |
| rag_only | cap_pc_build_multiturn_hard_t3 | 0 | pc_cpu_pc_seed_cpu_amd_ryzen_5_5600g,pc_motherboard_pc_seed_motherboard_asus_prime_a520m_k,pc_gpu_pc_seed_gpu_zotac_geforce_rtx_4060_ti_16g_twin_edge,pc_memory_pc_seed_memory_kingb | N/A | N/A | 0 | 0 | 1 | 0 |
| rag_only | cap_pc_build_multiturn_hard_t4 | 0 | pc_cpu_pc_seed_cpu_amd_ryzen_5_5600g,pc_motherboard_pc_seed_motherboard_asus_prime_a520m_k,pc_gpu_pc_seed_gpu_zotac_geforce_rtx_4060_ti_16g_twin_edge,pc_memory_pc_seed_memory_kingb | N/A | N/A | 0 | 0 | 1 | 0 |
| rag_only | cap_rag_review_dry_skin_cream | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| rag_only | cap_rag_sunscreen_less_oily | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| rag_only | cap_rag_shoe_review_cushion | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| rag_only | cap_negative_pet_food | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| rag_only | cap_negative_fridge | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| rag_only | cap_negative_4090_budget | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| rag_only | cap_negative_medicine | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| balanced_demo | cap_synonym_commute_noise_beans | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_synonym_oily_non_stuffy_sunscreen | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_synonym_parent_large_font_phone | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_synonym_office_mild_coffee | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_synonym_training_cushion_basketball | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_attr_sensitive_repair_cream | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_attr_low_sugar_office_tea | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_attr_light_student_laptop | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_attr_daily_running_cushion | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_attr_black_coffee_low_sugar | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_multi_phone_no_xiaomi_photo_battery_4000 | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_multi_basketball_cushion_light_1000 | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_multi_oily_summer_sunscreen_200 | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_multi_no_apple_office_pc | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_multi_no_nike_sportswear | 0 |  | N/A | 2.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_gift_girlfriend_open | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| balanced_demo | cap_gift_roommate_birthday | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| balanced_demo | cap_gift_parents_digital | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| balanced_demo | cap_gift_colleague_snack_box | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_gift_beauty_lip_serum | 0 |  | N/A | 2.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_bundle_sanya_summer | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| balanced_demo | cap_bundle_school_start | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| balanced_demo | cap_bundle_beginner_running | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_bundle_tablet_coffee | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| balanced_demo | cap_bundle_sport_drink | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_pc_b760_ddr5_wifi | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_pc_long_gpu_case | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_pc_750w_gold_atx30_psu | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_pc_2tb_fast_nvme | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_pc_ddr5_64gb_editing | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_pc_gpu_4070_ti | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_pc_cpu_ryzen7 | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_pc_build_multiturn_hard_t1 | 0 | pc_cpu_pc_seed_cpu_intel_core_i5_13400f,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_gpu_pc_seed_gpu_nvidia_geforce_rtx_4060_ti_8g,pc_memory_pc_seed_memory_kings | N/A | N/A | 0 | 0 | 1 | 0 |
| balanced_demo | cap_pc_build_multiturn_hard_t2 | 0 | pc_cpu_pc_seed_cpu_intel_core_i7_14700f,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_gpu_pc_seed_gpu_sapphire_radeon_rx_7600_8g,pc_memory_pc_seed_memory_kingston | N/A | N/A | 0 | 0 | 1 | 0 |
| balanced_demo | cap_pc_build_multiturn_hard_t3 | 0 | pc_cpu_pc_seed_cpu_amd_ryzen_5_5600g,pc_motherboard_pc_seed_motherboard_asus_prime_a520m_k,pc_gpu_pc_seed_gpu_zotac_geforce_rtx_4060_ti_16g_twin_edge,pc_memory_pc_seed_memory_kingb | N/A | N/A | 0 | 0 | 1 | 0 |
| balanced_demo | cap_pc_build_multiturn_hard_t4 | 0 | pc_cpu_pc_seed_cpu_amd_ryzen_5_5600g,pc_motherboard_pc_seed_motherboard_asus_prime_a520m_k,pc_gpu_pc_seed_gpu_zotac_geforce_rtx_4060_ti_16g_twin_edge,pc_memory_pc_seed_memory_kingb | N/A | N/A | 0 | 0 | 1 | 0 |
| balanced_demo | cap_rag_review_dry_skin_cream | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| balanced_demo | cap_rag_sunscreen_less_oily | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_rag_shoe_review_cushion | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| balanced_demo | cap_negative_pet_food | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| balanced_demo | cap_negative_fridge | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| balanced_demo | cap_negative_4090_budget | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| balanced_demo | cap_negative_medicine | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| router_llm_only | cap_pc_build_multiturn_hard_t1 | 0 | pc_cpu_pc_seed_cpu_intel_core_i5_13400f,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_gpu_pc_seed_gpu_nvidia_geforce_rtx_4060_ti_8g,pc_memory_pc_seed_memory_kings | N/A | N/A | 0 | 0 | 1 | 0 |
| router_llm_only | cap_pc_build_multiturn_hard_t2 | 0 | pc_cpu_pc_seed_cpu_intel_core_i7_14700f,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_gpu_pc_seed_gpu_sapphire_radeon_rx_7600_8g,pc_memory_pc_seed_memory_kingston | N/A | N/A | 0 | 0 | 1 | 0 |
| router_llm_only | cap_pc_build_multiturn_hard_t3 | 0 | pc_cpu_pc_seed_cpu_amd_ryzen_5_5600g,pc_motherboard_pc_seed_motherboard_asus_prime_a520m_k,pc_gpu_pc_seed_gpu_zotac_geforce_rtx_4060_ti_16g_twin_edge,pc_memory_pc_seed_memory_kingb | N/A | N/A | 0 | 0 | 1 | 0 |
| router_llm_only | cap_pc_build_multiturn_hard_t4 | 0 | pc_cpu_pc_seed_cpu_amd_ryzen_5_5600g,pc_motherboard_pc_seed_motherboard_asus_prime_a520m_k,pc_gpu_pc_seed_gpu_zotac_geforce_rtx_4060_ti_16g_twin_edge,pc_memory_pc_seed_memory_kingb | N/A | N/A | 0 | 0 | 1 | 0 |
| parse_llm_only | cap_pc_build_multiturn_hard_t1 | 0 | pc_cpu_pc_seed_cpu_intel_core_i5_13400f,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_gpu_pc_seed_gpu_nvidia_geforce_rtx_4060_ti_8g,pc_memory_pc_seed_memory_kings | N/A | N/A | 0 | 0 | 1 | 0 |
| parse_llm_only | cap_pc_build_multiturn_hard_t2 | 0 | pc_cpu_pc_seed_cpu_intel_core_i7_14700f,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_gpu_pc_seed_gpu_sapphire_radeon_rx_7600_8g,pc_memory_pc_seed_memory_kingston | N/A | N/A | 0 | 0 | 1 | 0 |
| parse_llm_only | cap_pc_build_multiturn_hard_t3 | 0 | pc_cpu_pc_seed_cpu_amd_ryzen_5_5600g,pc_motherboard_pc_seed_motherboard_asus_prime_a520m_k,pc_gpu_pc_seed_gpu_zotac_geforce_rtx_4060_ti_16g_twin_edge,pc_memory_pc_seed_memory_kingb | N/A | N/A | 0 | 0 | 1 | 0 |
| parse_llm_only | cap_pc_build_multiturn_hard_t4 | 0 | pc_cpu_pc_seed_cpu_amd_ryzen_5_5600g,pc_motherboard_pc_seed_motherboard_asus_prime_a520m_k,pc_gpu_pc_seed_gpu_zotac_geforce_rtx_4060_ti_16g_twin_edge,pc_memory_pc_seed_memory_kingb | N/A | N/A | 0 | 0 | 1 | 0 |
| full_llm_all | cap_synonym_commute_noise_beans | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| full_llm_all | cap_synonym_oily_non_stuffy_sunscreen | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| full_llm_all | cap_synonym_parent_large_font_phone | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| full_llm_all | cap_synonym_office_mild_coffee | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| full_llm_all | cap_synonym_training_cushion_basketball | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| full_llm_all | cap_attr_sensitive_repair_cream | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| full_llm_all | cap_attr_low_sugar_office_tea | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| full_llm_all | cap_attr_light_student_laptop | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| full_llm_all | cap_attr_daily_running_cushion | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| full_llm_all | cap_attr_black_coffee_low_sugar | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| full_llm_all | cap_multi_phone_no_xiaomi_photo_battery_4000 | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| full_llm_all | cap_multi_basketball_cushion_light_1000 | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| full_llm_all | cap_multi_oily_summer_sunscreen_200 | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| full_llm_all | cap_multi_no_apple_office_pc | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| full_llm_all | cap_multi_no_nike_sportswear | 0 |  | N/A | 2.000 | 1 | 0 | 0 | 0 |
| full_llm_all | cap_gift_girlfriend_open | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| full_llm_all | cap_gift_roommate_birthday | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| full_llm_all | cap_gift_parents_digital | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| full_llm_all | cap_gift_colleague_snack_box | 0 |  | N/A | 1.000 | 1 | 0 | 0 | 0 |
| full_llm_all | cap_gift_beauty_lip_serum | 0 |  | N/A | 2.000 | 1 | 0 | 0 | 0 |
| full_llm_all | cap_bundle_sanya_summer | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |
| full_llm_all | cap_bundle_school_start | 0 |  | N/A | N/A | 1 | 0 | 0 | 0 |

## LLM diagnostics

| group | case_id | router a/s/ap | router_failure | local | llm | final | parse a/s/ap | parse_failure | guidance a/s/ap | guidance_failure | changed_route | changed_rec | clarification |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: |
| balanced_demo | cap_synonym_commute_noise_beans | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 1/1/1 |  | 0/0/0 |  | 0 | 1 | 0 |
| balanced_demo | cap_synonym_oily_non_stuffy_sunscreen | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_synonym_parent_large_font_phone | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_synonym_office_mild_coffee | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_synonym_training_cushion_basketball | 1/1/0 |  | general_chat | recommend_shopping_products | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 1 | 0 | 0 |
| balanced_demo | cap_attr_sensitive_repair_cream | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_attr_low_sugar_office_tea | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 1/1/1 |  | 0/0/0 |  | 0 | 1 | 0 |
| balanced_demo | cap_attr_light_student_laptop | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_attr_daily_running_cushion | 1/1/0 |  | general_chat | recommend_shopping_products | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 1 | 0 | 0 |
| balanced_demo | cap_attr_black_coffee_low_sugar | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_multi_phone_no_xiaomi_photo_battery_4000 | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/1/1 |  | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_multi_basketball_cushion_light_1000 | 1/1/0 |  | general_chat | recommend_shopping_products | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 1 | 0 | 0 |
| balanced_demo | cap_multi_oily_summer_sunscreen_200 | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_multi_no_apple_office_pc | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_multi_no_nike_sportswear | 1/1/0 |  | general_chat | recommend_shopping_products | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 1 | 0 | 0 |
| balanced_demo | cap_gift_girlfriend_open | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/1/1 |  | 0/0/0 |  | 0 | 0 | 1 |
| balanced_demo | cap_gift_roommate_birthday | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/1/1 |  | 0/0/0 |  | 0 | 0 | 1 |
| balanced_demo | cap_gift_parents_digital | 1/1/0 |  | general_chat | recommend_shopping_products | recommend_shopping_products | 1/1/1 |  | 0/0/0 |  | 1 | 1 | 0 |
| balanced_demo | cap_gift_colleague_snack_box | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_gift_beauty_lip_serum | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_bundle_sanya_summer | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 1/1/1 |  | 0/0/0 |  | 0 | 1 | 0 |
| balanced_demo | cap_bundle_school_start | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_bundle_beginner_running | 1/1/0 |  | general_chat | recommend_shopping_products | recommend_shopping_products | 1/1/1 |  | 0/0/0 |  | 1 | 1 | 0 |
| balanced_demo | cap_bundle_tablet_coffee | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_bundle_sport_drink | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_pc_b760_ddr5_wifi | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_pc_long_gpu_case | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_pc_750w_gold_atx30_psu | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_pc_2tb_fast_nvme | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/1/1 |  | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_pc_ddr5_64gb_editing | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_pc_gpu_4070_ti | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_pc_cpu_ryzen7 | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_pc_build_multiturn_hard_t1 | 1/1/0 |  | generate_pc_build_plan | generate_pc_build_plan | generate_pc_build_plan | 0/0/0 |  | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_pc_build_multiturn_hard_t2 | 1/1/0 |  | generate_pc_build_plan | recommend_shopping_products | generate_pc_build_plan | 0/0/0 |  | 0/0/0 |  | 1 | 0 | 0 |
| balanced_demo | cap_pc_build_multiturn_hard_t3 | 1/1/0 |  | generate_pc_build_plan | recommend_shopping_products | generate_pc_build_plan | 0/0/0 |  | 0/0/0 |  | 1 | 0 | 0 |
| balanced_demo | cap_pc_build_multiturn_hard_t4 | 1/1/0 |  | generate_pc_build_plan | compare_products | generate_pc_build_plan | 0/0/0 |  | 0/0/0 |  | 1 | 0 | 0 |
| balanced_demo | cap_rag_review_dry_skin_cream | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_rag_sunscreen_less_oily | 1/1/0 |  | compare_products | recommend_shopping_products | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 1 | 0 | 0 |
| balanced_demo | cap_rag_shoe_review_cushion | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_negative_pet_food | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_negative_fridge | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | cap_negative_4090_budget | 1/1/0 |  | recommend_shopping_products | general_chat | general_chat | 0/0/0 |  | 0/0/0 |  | 1 | 0 | 0 |
| balanced_demo | cap_negative_medicine | 1/1/0 |  | recommend_shopping_products | general_chat | general_chat | 0/0/0 |  | 0/0/0 |  | 1 | 0 | 0 |
| router_llm_only | cap_synonym_commute_noise_beans | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_synonym_oily_non_stuffy_sunscreen | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_synonym_parent_large_font_phone | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_synonym_office_mild_coffee | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_synonym_training_cushion_basketball | 1/1/0 |  | general_chat | recommend_shopping_products | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 1 | 0 | 0 |
| router_llm_only | cap_attr_sensitive_repair_cream | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_attr_low_sugar_office_tea | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_attr_light_student_laptop | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_attr_daily_running_cushion | 1/1/0 |  | general_chat | recommend_shopping_products | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 1 | 0 | 0 |
| router_llm_only | cap_attr_black_coffee_low_sugar | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_multi_phone_no_xiaomi_photo_battery_4000 | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_multi_basketball_cushion_light_1000 | 1/1/0 |  | general_chat | recommend_shopping_products | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 1 | 0 | 0 |
| router_llm_only | cap_multi_oily_summer_sunscreen_200 | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_multi_no_apple_office_pc | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_multi_no_nike_sportswear | 1/1/0 |  | general_chat | recommend_shopping_products | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 1 | 0 | 0 |
| router_llm_only | cap_gift_girlfriend_open | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_gift_roommate_birthday | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_gift_parents_digital | 1/1/0 |  | general_chat | recommend_shopping_products | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 1 | 0 | 0 |
| router_llm_only | cap_gift_colleague_snack_box | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_gift_beauty_lip_serum | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_bundle_sanya_summer | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_bundle_school_start | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_bundle_beginner_running | 1/1/0 |  | general_chat | recommend_shopping_products | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 1 | 0 | 0 |
| router_llm_only | cap_bundle_tablet_coffee | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_bundle_sport_drink | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_pc_b760_ddr5_wifi | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_pc_long_gpu_case | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_pc_750w_gold_atx30_psu | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_pc_2tb_fast_nvme | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_pc_ddr5_64gb_editing | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_pc_gpu_4070_ti | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_pc_cpu_ryzen7 | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_pc_build_multiturn_hard_t1 | 1/1/0 |  | generate_pc_build_plan | generate_pc_build_plan | generate_pc_build_plan | 0/0/0 |  | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_pc_build_multiturn_hard_t2 | 1/1/0 |  | generate_pc_build_plan | recommend_shopping_products | generate_pc_build_plan | 0/0/0 |  | 0/0/0 |  | 1 | 0 | 0 |
| router_llm_only | cap_pc_build_multiturn_hard_t3 | 1/1/0 |  | generate_pc_build_plan | recommend_shopping_products | generate_pc_build_plan | 0/0/0 |  | 0/0/0 |  | 1 | 0 | 0 |
| router_llm_only | cap_pc_build_multiturn_hard_t4 | 1/1/0 |  | generate_pc_build_plan | compare_products | generate_pc_build_plan | 0/0/0 |  | 0/0/0 |  | 1 | 0 | 0 |
| router_llm_only | cap_rag_review_dry_skin_cream | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_rag_sunscreen_less_oily | 1/1/0 |  | compare_products | recommend_shopping_products | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 1 | 0 | 0 |
| router_llm_only | cap_rag_shoe_review_cushion | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_negative_pet_food | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_negative_fridge | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 0/0/0 | llm_disabled_by_runtime_policy | 0/0/0 |  | 0 | 0 | 0 |
| router_llm_only | cap_negative_4090_budget | 1/1/0 |  | recommend_shopping_products | general_chat | general_chat | 0/0/0 |  | 0/0/0 |  | 1 | 0 | 0 |
| router_llm_only | cap_negative_medicine | 1/1/0 |  | recommend_shopping_products | general_chat | general_chat | 0/0/0 |  | 0/0/0 |  | 1 | 0 | 0 |
| parse_llm_only | cap_synonym_commute_noise_beans | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/1/1 |  | 0/0/0 |  | 0 | 1 | 0 |
| parse_llm_only | cap_synonym_oily_non_stuffy_sunscreen | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_synonym_parent_large_font_phone | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_synonym_office_mild_coffee | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_attr_sensitive_repair_cream | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_attr_low_sugar_office_tea | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/1/1 |  | 0/0/0 |  | 0 | 1 | 0 |
| parse_llm_only | cap_attr_light_student_laptop | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_attr_black_coffee_low_sugar | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_multi_phone_no_xiaomi_photo_battery_4000 | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/1/1 |  | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_multi_oily_summer_sunscreen_200 | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_multi_no_apple_office_pc | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_gift_girlfriend_open | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/1/1 |  | 0/0/0 |  | 0 | 0 | 1 |
| parse_llm_only | cap_gift_roommate_birthday | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/1/1 |  | 0/0/0 |  | 0 | 0 | 1 |
| parse_llm_only | cap_gift_colleague_snack_box | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_gift_beauty_lip_serum | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_bundle_sanya_summer | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/1/1 |  | 0/0/0 |  | 0 | 1 | 0 |
| parse_llm_only | cap_bundle_school_start | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_bundle_tablet_coffee | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_bundle_sport_drink | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_pc_b760_ddr5_wifi | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_pc_long_gpu_case | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_pc_750w_gold_atx30_psu | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_pc_2tb_fast_nvme | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/1/1 |  | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_pc_ddr5_64gb_editing | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_pc_gpu_4070_ti | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_pc_cpu_ryzen7 | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_rag_review_dry_skin_cream | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_rag_shoe_review_cushion | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_negative_pet_food | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_negative_fridge | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_negative_4090_budget | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| parse_llm_only | cap_negative_medicine | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| full_llm_all | cap_synonym_commute_noise_beans | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 1/1/1 |  | 1/1/1 |  | 0 | 1 | 0 |
| full_llm_all | cap_synonym_oily_non_stuffy_sunscreen | 1/1/0 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 1/1/1 |  | 0 | 0 | 0 |

## capability_challenge_assessment

- main_metrics_identical: False
- balanced_demo top1 wins vs fast: 0 (none)
- full_no_query_expansion top1 wins vs fast: 0 (none)
- full_llm_all top1 wins vs fast: 0 (none)
- vs_fast_delta found no top1 recommendation wins; use this as evidence that current challenge cases or current chain weighting still do not expose business uplift.

## capability/stability conclusions

### stability_eval
- A_vs_A2: 稳定性：rag_only 相对 fast_baseline 成功率变化 0.0，错误率变化 0.0，fallback 变化 0.0。
- A2_vs_B: 稳定性：balanced_demo 相对 rag_only 路由准确率变化 0.1163，成功率变化 0.1396，违规率变化 0.0。
- B_vs_D: 降级稳定性：degraded_success_rate=0，fallback_triggered_rate=0.0。
### capability_eval
- RAG_A2: rag_only: RAG 未有效测到，rag_attempted=0.6977，embedding_success=0.093，milvus_success=0.0，retrieval_nonempty=0.093，timeout=0.0。
- RAG_B: balanced_demo: RAG 未有效测到，rag_attempted=0.8605，embedding_success=0.093，milvus_success=0.0，retrieval_nonempty=0.093，timeout=0.0。
- LLM_router_B: balanced_demo: router attempted=0.5349，success=0.5349，applied=0.0。
- LLM_router_B1: router_llm_only: router attempted=0.5349，success=0.5349，applied=0.0。
- LLM_overall_B_vs_C: 能力：full_llm_all 相对 balanced_demo llm_attempted 变化 0.0，llm_used 变化 0.3721，llm_applied 变化 0.6512。
- QueryExpansion_C_vs_C2: 能力：query expansion 只能在 RAG 有效样本上解释；full_no_query_expansion RAG 有效样本=0，full_llm_all RAG 有效样本=0。
