# MallMind full 链路消融评估报告

## 总览

| 指标 | 值 |
| --- | ---: |
| 总 case 数 | 108 |
| failed | 32 |
| suspicious | 38 |
| RAG 适用 case 数 | 16 |
| RAG 不适用 case 数 | 92 |
| rag_chain_valid_rate | 0.0000 |
| full_chain_valid_rate | 0.0000 |
| latency avg / p50 / p95 | 756.76 / 20.16 / 4999.75 ms |

## 四组模式对比

| mode | cases | P@1 | Hit@5 | route | RAG | Milvus | raw hit | LLM parse | rag valid | full valid | failed | suspicious | avg ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fast_no_llm_no_rag | 27 | 0.222 | 0.259 | 0.889 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 4 | 0 | 719.59 |
| rag_only | 27 | 0.222 | 0.259 | 0.889 | 0.481 | 0.481 | 0.000 | 0.000 | 0.000 | 0.000 | 12 | 0 | 870.81 |
| llm_only | 27 | 0.222 | 0.259 | 0.889 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 4 | 23 | 651.51 |
| full | 27 | 0.222 | 0.259 | 0.889 | 0.481 | 0.481 | 0.000 | 0.000 | 0.000 | 0.000 | 12 | 15 | 785.11 |

## 评估桶汇总

| bucket | cases | RAG applicable | rag valid | route | failed | suspicious | note |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| in_catalog_ecommerce_rag | 16 | 8 | 0.000 | 1.000 | 8 | 4 | 电商正例检索链路 |
| pc_part_rag | 16 | 8 | 0.000 | 1.000 | 8 | 4 | PC 单配件检索链路 |
| pc_build_structured | 12 | 0 | 0.000 | 1.000 | 0 | 6 | 结构化装机规划，不默认要求 Milvus |
| negative_guard | 16 | 0 | 0.000 | 1.000 | 0 | 8 | 负例/安全/不支持品类 guard |
| ambiguous_llm_needed | 12 | 0 | 0.000 | 1.000 | 4 | 4 | 模糊需求，rag_only 下多为口径不适用 |
| route_boundary | 20 | 0 | 0.000 | 1.000 | 0 | 10 | 路由边界与会话指令 |
| multiturn_session | 16 | 0 | 0.000 | 0.250 | 12 | 2 | 多轮会话，rag_only 下仅记录路由/澄清 |

## ecommerce summary

- case_count: 28
- precision@1: 0.286
- hit@5: 0.429
- no_match_accuracy: 1.000
- route_accuracy: 1.000
- latency_avg_ms: 240.57

## pc_parts summary

- case_count: 16
- precision@1: 1.000
- hit@5: 1.000
- no_match_accuracy: 1.000
- route_accuracy: 1.000
- latency_avg_ms: 126.2

## pc_build summary

- case_count: 28
- precision@1: 0.000
- hit@5: 0.000
- no_match_accuracy: 1.000
- route_accuracy: 0.571
- latency_avg_ms: 2581.29

## negative/no-match summary

- case_count: 16
- precision@1: 0.000
- hit@5: 0.000
- no_match_accuracy: 1.000
- route_accuracy: 1.000
- latency_avg_ms: 3.42

## failed/suspicious case 明细

| case | mode | status | tool | RAG | raw | after | source | rag_reason | LLM(router/parse/enhance) | failed_reason |
| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |
| pc_build_multiturn_adjust_t2 | fast_no_llm_no_rag | failed | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | tool route 不符合预期 |
| pc_build_multiturn_adjust_t3 | fast_no_llm_no_rag | failed | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | tool route 不符合预期 |
| pc_build_multiturn_adjust_t4 | fast_no_llm_no_rag | failed | compare_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | tool route 不符合预期 |
| ambiguous_gift_girlfriend | fast_no_llm_no_rag | failed | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 未识别到有效购物场景，请描述想买什么、预算、用途或偏好。 |
| ecom_sunscreen_oily_summer | rag_only | failed | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | retrieval_error | 0/0/0 | RAG/Milvus 检索错误: retrieval_failed |
| ecom_phone_under_5000_exclude_xiaomi | rag_only | failed | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | retrieval_error | 0/0/0 | RAG/Milvus 检索错误: retrieval_failed |
| ecom_basketball_shoes_cushion_1000 | rag_only | failed | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | retrieval_error | 0/0/0 | RAG/Milvus 检索错误: retrieval_failed |
| ecom_office_coffee | rag_only | failed | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | retrieval_error | 0/0/0 | RAG/Milvus 检索错误: retrieval_failed |
| pc_part_b760_ddr5_motherboard | rag_only | failed | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | retrieval_error | 0/0/0 | RAG/Milvus 检索错误: retrieval_failed |
| pc_part_750w_gold_psu | rag_only | failed | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | retrieval_error | 0/0/0 | RAG/Milvus 检索错误: retrieval_failed |
| pc_part_2tb_nvme_ssd | rag_only | failed | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | retrieval_error | 0/0/0 | RAG/Milvus 检索错误: retrieval_failed |
| pc_part_long_gpu_case | rag_only | failed | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | retrieval_error | 0/0/0 | RAG/Milvus 检索错误: retrieval_failed |
| pc_build_multiturn_adjust_t2 | rag_only | failed | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | not_applicable | 0/0/0 | tool route 不符合预期 |
| pc_build_multiturn_adjust_t3 | rag_only | failed | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | not_applicable | 0/0/0 | tool route 不符合预期 |
| pc_build_multiturn_adjust_t4 | rag_only | failed | compare_products | 0 | 0 | 0 | structured_catalog | not_applicable | 0/0/0 | tool route 不符合预期 |
| ambiguous_gift_girlfriend | rag_only | failed | recommend_shopping_products | 0 | 0 | 0 | structured_catalog | not_applicable | 0/0/0 | 未识别到有效购物场景，请描述想买什么、预算、用途或偏好。 |
| ecom_sunscreen_oily_summer | llm_only | suspicious | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| ecom_phone_under_5000_exclude_xiaomi | llm_only | suspicious | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| ecom_basketball_shoes_cushion_1000 | llm_only | suspicious | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| ecom_office_coffee | llm_only | suspicious | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| pc_part_b760_ddr5_motherboard | llm_only | suspicious | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| pc_part_750w_gold_psu | llm_only | suspicious | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| pc_part_2tb_nvme_ssd | llm_only | suspicious | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| pc_part_long_gpu_case | llm_only | suspicious | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| pc_build_7000_3a | llm_only | suspicious | generate_pc_build_plan | 0 | 0 | 8 | rag_evidence |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| pc_build_video_edit_9000 | llm_only | suspicious | generate_pc_build_plan | 0 | 0 | 8 | rag_evidence |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| pc_build_rtx4070_8000 | llm_only | suspicious | generate_pc_build_plan | 0 | 0 | 8 | rag_evidence |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| pc_build_multiturn_adjust_t1 | llm_only | suspicious | generate_pc_build_plan | 0 | 0 | 8 | rag_evidence |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| pc_build_multiturn_adjust_t2 | llm_only | failed | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | tool route 不符合预期 |
| pc_build_multiturn_adjust_t3 | llm_only | failed | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | tool route 不符合预期 |
| pc_build_multiturn_adjust_t4 | llm_only | failed | compare_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | tool route 不符合预期 |
| negative_missing_outdoor_jacket | llm_only | suspicious | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| negative_impossible_iphone_budget | llm_only | suspicious | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| negative_unsupported_car | llm_only | suspicious | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| negative_prescription_drug | llm_only | suspicious | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| ambiguous_gift_girlfriend | llm_only | failed | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 未识别到有效购物场景，请描述想买什么、预算、用途或偏好。 |
| ambiguous_summer_things | llm_only | suspicious | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| ambiguous_value_digital | llm_only | suspicious | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| route_general_hello | llm_only | suspicious | general_chat | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| route_compare_products | llm_only | suspicious | compare_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| route_add_cart | llm_only | suspicious | apply_cart_instruction | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| route_clear_cart | llm_only | suspicious | apply_cart_instruction | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| route_session_second_price | llm_only | suspicious | recommend_shopping_products | 0 | 0 | 0 | structured_catalog |  | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| ecom_sunscreen_oily_summer | full | failed | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | retrieval_error | 0/0/0 | RAG/Milvus 检索错误: retrieval_failed |
| ecom_phone_under_5000_exclude_xiaomi | full | failed | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | retrieval_error | 0/0/0 | RAG/Milvus 检索错误: retrieval_failed |
| ecom_basketball_shoes_cushion_1000 | full | failed | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | retrieval_error | 0/0/0 | RAG/Milvus 检索错误: retrieval_failed |
| ecom_office_coffee | full | failed | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | retrieval_error | 0/0/0 | RAG/Milvus 检索错误: retrieval_failed |
| pc_part_b760_ddr5_motherboard | full | failed | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | retrieval_error | 0/0/0 | RAG/Milvus 检索错误: retrieval_failed |
| pc_part_750w_gold_psu | full | failed | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | retrieval_error | 0/0/0 | RAG/Milvus 检索错误: retrieval_failed |
| pc_part_2tb_nvme_ssd | full | failed | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | retrieval_error | 0/0/0 | RAG/Milvus 检索错误: retrieval_failed |
| pc_part_long_gpu_case | full | failed | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | retrieval_error | 0/0/0 | RAG/Milvus 检索错误: retrieval_failed |
| pc_build_7000_3a | full | suspicious | generate_pc_build_plan | 0 | 0 | 8 | rag_evidence | not_applicable | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| pc_build_video_edit_9000 | full | suspicious | generate_pc_build_plan | 0 | 0 | 8 | rag_evidence | not_applicable | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| pc_build_rtx4070_8000 | full | suspicious | generate_pc_build_plan | 0 | 0 | 8 | rag_evidence | not_applicable | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| pc_build_multiturn_adjust_t1 | full | suspicious | generate_pc_build_plan | 0 | 0 | 8 | rag_evidence | not_applicable | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| pc_build_multiturn_adjust_t2 | full | failed | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | not_applicable | 0/0/0 | tool route 不符合预期 |
| pc_build_multiturn_adjust_t3 | full | failed | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | not_applicable | 0/0/0 | tool route 不符合预期 |
| pc_build_multiturn_adjust_t4 | full | failed | compare_products | 0 | 0 | 0 | structured_catalog | not_applicable | 0/0/0 | tool route 不符合预期 |
| negative_missing_outdoor_jacket | full | suspicious | recommend_shopping_products | 0 | 0 | 0 | structured_catalog | not_applicable | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| negative_impossible_iphone_budget | full | suspicious | recommend_shopping_products | 0 | 0 | 0 | structured_catalog | not_applicable | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| negative_unsupported_car | full | suspicious | recommend_shopping_products | 0 | 0 | 0 | structured_catalog | not_applicable | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| negative_prescription_drug | full | suspicious | recommend_shopping_products | 0 | 0 | 0 | structured_catalog | not_applicable | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| ambiguous_gift_girlfriend | full | failed | recommend_shopping_products | 0 | 0 | 0 | structured_catalog | not_applicable | 0/0/0 | 未识别到有效购物场景，请描述想买什么、预算、用途或偏好。 |
| ambiguous_summer_things | full | suspicious | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | not_applicable | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| ambiguous_value_digital | full | suspicious | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | not_applicable | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| route_general_hello | full | suspicious | general_chat | 0 | 0 | 0 | structured_catalog | not_applicable | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| route_compare_products | full | suspicious | compare_products | 0 | 0 | 0 | structured_catalog | not_applicable | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| route_add_cart | full | suspicious | apply_cart_instruction | 0 | 0 | 0 | structured_catalog | not_applicable | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| route_clear_cart | full | suspicious | apply_cart_instruction | 0 | 0 | 0 | structured_catalog | not_applicable | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| route_session_second_price | full | suspicious | recommend_shopping_products | 1 | 0 | 0 | structured_catalog_fallback_after_rag | not_applicable | 0/0/0 | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |

## 问题分类

### RAG 链路问题

| case | mode | bucket | status | tool | reason |
| --- | --- | --- | --- | --- | --- |
| ecom_sunscreen_oily_summer | rag_only | in_catalog_ecommerce_rag | failed | recommend_shopping_products | RAG/Milvus 检索错误: retrieval_failed |
| ecom_phone_under_5000_exclude_xiaomi | rag_only | in_catalog_ecommerce_rag | failed | recommend_shopping_products | RAG/Milvus 检索错误: retrieval_failed |
| ecom_basketball_shoes_cushion_1000 | rag_only | in_catalog_ecommerce_rag | failed | recommend_shopping_products | RAG/Milvus 检索错误: retrieval_failed |
| ecom_office_coffee | rag_only | in_catalog_ecommerce_rag | failed | recommend_shopping_products | RAG/Milvus 检索错误: retrieval_failed |
| pc_part_b760_ddr5_motherboard | rag_only | pc_part_rag | failed | recommend_shopping_products | RAG/Milvus 检索错误: retrieval_failed |
| pc_part_750w_gold_psu | rag_only | pc_part_rag | failed | recommend_shopping_products | RAG/Milvus 检索错误: retrieval_failed |
| pc_part_2tb_nvme_ssd | rag_only | pc_part_rag | failed | recommend_shopping_products | RAG/Milvus 检索错误: retrieval_failed |
| pc_part_long_gpu_case | rag_only | pc_part_rag | failed | recommend_shopping_products | RAG/Milvus 检索错误: retrieval_failed |
| ecom_sunscreen_oily_summer | full | in_catalog_ecommerce_rag | failed | recommend_shopping_products | RAG/Milvus 检索错误: retrieval_failed |
| ecom_phone_under_5000_exclude_xiaomi | full | in_catalog_ecommerce_rag | failed | recommend_shopping_products | RAG/Milvus 检索错误: retrieval_failed |
| ecom_basketball_shoes_cushion_1000 | full | in_catalog_ecommerce_rag | failed | recommend_shopping_products | RAG/Milvus 检索错误: retrieval_failed |
| ecom_office_coffee | full | in_catalog_ecommerce_rag | failed | recommend_shopping_products | RAG/Milvus 检索错误: retrieval_failed |
| pc_part_b760_ddr5_motherboard | full | pc_part_rag | failed | recommend_shopping_products | RAG/Milvus 检索错误: retrieval_failed |
| pc_part_750w_gold_psu | full | pc_part_rag | failed | recommend_shopping_products | RAG/Milvus 检索错误: retrieval_failed |
| pc_part_2tb_nvme_ssd | full | pc_part_rag | failed | recommend_shopping_products | RAG/Milvus 检索错误: retrieval_failed |
| pc_part_long_gpu_case | full | pc_part_rag | failed | recommend_shopping_products | RAG/Milvus 检索错误: retrieval_failed |

### 路由问题

| case | mode | bucket | status | tool | reason |
| --- | --- | --- | --- | --- | --- |
| pc_build_multiturn_adjust_t2 | fast_no_llm_no_rag | multiturn_session | failed | recommend_shopping_products | tool route 不符合预期 |
| pc_build_multiturn_adjust_t3 | fast_no_llm_no_rag | multiturn_session | failed | recommend_shopping_products | tool route 不符合预期 |
| pc_build_multiturn_adjust_t4 | fast_no_llm_no_rag | multiturn_session | failed | compare_products | tool route 不符合预期 |
| pc_build_multiturn_adjust_t2 | rag_only | multiturn_session | failed | recommend_shopping_products | tool route 不符合预期 |
| pc_build_multiturn_adjust_t3 | rag_only | multiturn_session | failed | recommend_shopping_products | tool route 不符合预期 |
| pc_build_multiturn_adjust_t4 | rag_only | multiturn_session | failed | compare_products | tool route 不符合预期 |
| pc_build_multiturn_adjust_t2 | llm_only | multiturn_session | failed | recommend_shopping_products | tool route 不符合预期 |
| pc_build_multiturn_adjust_t3 | llm_only | multiturn_session | failed | recommend_shopping_products | tool route 不符合预期 |
| pc_build_multiturn_adjust_t4 | llm_only | multiturn_session | failed | compare_products | tool route 不符合预期 |
| pc_build_multiturn_adjust_t2 | full | multiturn_session | failed | recommend_shopping_products | tool route 不符合预期 |
| pc_build_multiturn_adjust_t3 | full | multiturn_session | failed | recommend_shopping_products | tool route 不符合预期 |
| pc_build_multiturn_adjust_t4 | full | multiturn_session | failed | compare_products | tool route 不符合预期 |

### 负例 guard 问题

| case | mode | bucket | status | tool | reason |
| --- | --- | --- | --- | --- | --- |
| negative_missing_outdoor_jacket | llm_only | negative_guard | suspicious | recommend_shopping_products | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| negative_impossible_iphone_budget | llm_only | negative_guard | suspicious | recommend_shopping_products | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| negative_unsupported_car | llm_only | negative_guard | suspicious | recommend_shopping_products | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| negative_prescription_drug | llm_only | negative_guard | suspicious | recommend_shopping_products | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| negative_missing_outdoor_jacket | full | negative_guard | suspicious | recommend_shopping_products | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| negative_impossible_iphone_budget | full | negative_guard | suspicious | recommend_shopping_products | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| negative_unsupported_car | full | negative_guard | suspicious | recommend_shopping_products | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| negative_prescription_drug | full | negative_guard | suspicious | recommend_shopping_products | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |

### PC build 结构化规划问题

| case | mode | bucket | status | tool | reason |
| --- | --- | --- | --- | --- | --- |
| pc_build_7000_3a | llm_only | pc_build_structured | suspicious | generate_pc_build_plan | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| pc_build_video_edit_9000 | llm_only | pc_build_structured | suspicious | generate_pc_build_plan | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| pc_build_rtx4070_8000 | llm_only | pc_build_structured | suspicious | generate_pc_build_plan | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| pc_build_7000_3a | full | pc_build_structured | suspicious | generate_pc_build_plan | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| pc_build_video_edit_9000 | full | pc_build_structured | suspicious | generate_pc_build_plan | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |
| pc_build_rtx4070_8000 | full | pc_build_structured | suspicious | generate_pc_build_plan | 配置要求启用 LLM，但 router/parse/enhancement 均未显示 LLM 调用 |

### 评估口径不适用项

| case | mode | bucket | status | tool | reason |
| --- | --- | --- | --- | --- | --- |
| ecom_sunscreen_oily_summer | fast_no_llm_no_rag | in_catalog_ecommerce_rag | ok | recommend_shopping_products | rag_not_applicable |
| ecom_phone_under_5000_exclude_xiaomi | fast_no_llm_no_rag | in_catalog_ecommerce_rag | ok | recommend_shopping_products | rag_not_applicable |
| ecom_basketball_shoes_cushion_1000 | fast_no_llm_no_rag | in_catalog_ecommerce_rag | ok | recommend_shopping_products | rag_not_applicable |
| ecom_office_coffee | fast_no_llm_no_rag | in_catalog_ecommerce_rag | ok | recommend_shopping_products | rag_not_applicable |
| pc_part_b760_ddr5_motherboard | fast_no_llm_no_rag | pc_part_rag | ok | recommend_shopping_products | rag_not_applicable |
| pc_part_750w_gold_psu | fast_no_llm_no_rag | pc_part_rag | ok | recommend_shopping_products | rag_not_applicable |
| pc_part_2tb_nvme_ssd | fast_no_llm_no_rag | pc_part_rag | ok | recommend_shopping_products | rag_not_applicable |
| pc_part_long_gpu_case | fast_no_llm_no_rag | pc_part_rag | ok | recommend_shopping_products | rag_not_applicable |
| pc_build_7000_3a | fast_no_llm_no_rag | pc_build_structured | ok | generate_pc_build_plan | rag_not_applicable |
| pc_build_video_edit_9000 | fast_no_llm_no_rag | pc_build_structured | ok | generate_pc_build_plan | rag_not_applicable |
| pc_build_rtx4070_8000 | fast_no_llm_no_rag | pc_build_structured | ok | generate_pc_build_plan | rag_not_applicable |
| pc_build_multiturn_adjust_t1 | fast_no_llm_no_rag | multiturn_session | ok | generate_pc_build_plan | rag_not_applicable |
| pc_build_multiturn_adjust_t2 | fast_no_llm_no_rag | multiturn_session | failed | recommend_shopping_products | tool route 不符合预期 |
| pc_build_multiturn_adjust_t3 | fast_no_llm_no_rag | multiturn_session | failed | recommend_shopping_products | tool route 不符合预期 |
| pc_build_multiturn_adjust_t4 | fast_no_llm_no_rag | multiturn_session | failed | compare_products | tool route 不符合预期 |
| negative_missing_outdoor_jacket | fast_no_llm_no_rag | negative_guard | ok | recommend_shopping_products | rag_not_applicable |
| negative_impossible_iphone_budget | fast_no_llm_no_rag | negative_guard | ok | recommend_shopping_products | rag_not_applicable |
| negative_unsupported_car | fast_no_llm_no_rag | negative_guard | ok | recommend_shopping_products | rag_not_applicable |
| negative_prescription_drug | fast_no_llm_no_rag | negative_guard | ok | recommend_shopping_products | rag_not_applicable |
| ambiguous_gift_girlfriend | fast_no_llm_no_rag | ambiguous_llm_needed | failed | recommend_shopping_products | 未识别到有效购物场景，请描述想买什么、预算、用途或偏好。 |
| ambiguous_summer_things | fast_no_llm_no_rag | ambiguous_llm_needed | ok | recommend_shopping_products | rag_not_applicable |
| ambiguous_value_digital | fast_no_llm_no_rag | ambiguous_llm_needed | ok | recommend_shopping_products | rag_not_applicable |
| route_general_hello | fast_no_llm_no_rag | route_boundary | ok | general_chat | rag_not_applicable |
| route_compare_products | fast_no_llm_no_rag | route_boundary | ok | compare_products | rag_not_applicable |
| route_add_cart | fast_no_llm_no_rag | route_boundary | ok | apply_cart_instruction | rag_not_applicable |
| route_clear_cart | fast_no_llm_no_rag | route_boundary | ok | apply_cart_instruction | rag_not_applicable |
| route_session_second_price | fast_no_llm_no_rag | route_boundary | ok | recommend_shopping_products | rag_not_applicable |
| pc_build_7000_3a | rag_only | pc_build_structured | ok | generate_pc_build_plan | not_applicable |
| pc_build_video_edit_9000 | rag_only | pc_build_structured | ok | generate_pc_build_plan | not_applicable |
| pc_build_rtx4070_8000 | rag_only | pc_build_structured | ok | generate_pc_build_plan | not_applicable |
| pc_build_multiturn_adjust_t1 | rag_only | multiturn_session | ok | generate_pc_build_plan | not_applicable |
| pc_build_multiturn_adjust_t2 | rag_only | multiturn_session | failed | recommend_shopping_products | tool route 不符合预期 |
| pc_build_multiturn_adjust_t3 | rag_only | multiturn_session | failed | recommend_shopping_products | tool route 不符合预期 |
| pc_build_multiturn_adjust_t4 | rag_only | multiturn_session | failed | compare_products | tool route 不符合预期 |
| negative_missing_outdoor_jacket | rag_only | negative_guard | ok | recommend_shopping_products | not_applicable |
| negative_impossible_iphone_budget | rag_only | negative_guard | ok | recommend_shopping_products | not_applicable |
| negative_unsupported_car | rag_only | negative_guard | ok | recommend_shopping_products | not_applicable |
| negative_prescription_drug | rag_only | negative_guard | ok | recommend_shopping_products | not_applicable |
| ambiguous_gift_girlfriend | rag_only | ambiguous_llm_needed | failed | recommend_shopping_products | 未识别到有效购物场景，请描述想买什么、预算、用途或偏好。 |
| ambiguous_summer_things | rag_only | ambiguous_llm_needed | ok | recommend_shopping_products | not_applicable |


## PC 单配件 final_recommendation_miss 诊断

| case | mode | failure_type | expected | normalized expected | retrieved top10 | normalized retrieved top10 | recommended top10 | normalized recommended top10 | normalized match | rec category | retrieved has expected | component/spec match | candidates before/after | score top |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- | ---: | --- | --- | --- |
| pc_part_b760_ddr5_motherboard | fast_no_llm_no_rag | normalized_id_match | pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5,pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5 / pc_motherboard | pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5,pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5 |  |  | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3 | pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5 | 1 | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5:pc_motherboard/PRO B760M-A WIFI DDR5, pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2:pc_motherboard/PRO B760M-A WIFI DDR5 V2, pc_motherboard_pc_seed_motherboard_m | 0 | 1/1 | 242/9 | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5:0.7625; pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2:0.756; pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3:0.7496 |
| pc_part_750w_gold_psu | fast_no_llm_no_rag | normalized_id_match | pc_seed_psu_super_flower_leadex_g_750w / pc_psu | pc_seed_psu_super_flower_leadex_g_750w |  |  | pc_psu_pc_seed_psu_super_flower_leadex_g_750w,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3 | pc_seed_psu_super_flower_leadex_g_750w,pc_seed_psu_super_flower_leadex_g_750w,pc_seed_psu_super_flower_leadex_g_750w | 1 | pc_psu_pc_seed_psu_super_flower_leadex_g_750w:pc_psu/LEADEX G 750W, pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2:pc_psu/LEADEX G 750W V2, pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3:pc_psu/LEADEX G 750W V3 | 0 | 1/1 | 242/5 | pc_psu_pc_seed_psu_super_flower_leadex_g_750w:0.7625; pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2:0.735; pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3:0.7075 |
| pc_part_2tb_nvme_ssd | fast_no_llm_no_rag | normalized_id_match | pc_seed_ssd_samsung_990_pro_2tb,pc_seed_ssd_wd_black_sn850x_2tb / pc_storage | pc_seed_ssd_samsung_990_pro_2tb,pc_seed_ssd_wd_black_sn850x_2tb |  |  | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3 | pc_seed_ssd_wd_black_sn850x_2tb,pc_seed_ssd_wd_black_sn850x_2tb,pc_seed_ssd_wd_black_sn850x_2tb | 1 | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb:pc_storage/BLACK SN850X 2TB, pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2:pc_storage/BLACK SN850X 2TB Rev2, pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3:pc_storage/BLACK SN850X 2TB Rev3 | 0 | 1/1 | 242/10 | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb:0.7625; pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2:0.7488; pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3:0.735 |
| pc_part_long_gpu_case | fast_no_llm_no_rag | normalized_id_match | pc_seed_case_fractal_design_north,pc_seed_case_phanteks_p360a,pc_seed_case_nzxt_h5_flow / pc_case | pc_seed_case_fractal_design_north,pc_seed_case_phanteks_p360a,pc_seed_case_nzxt_h5_flow |  |  | pc_case_pc_seed_case_phanteks_p360a,pc_case_pc_seed_case_phanteks_p360a_v2,pc_case_pc_seed_case_phanteks_p360a_v3 | pc_seed_case_phanteks_p360a,pc_seed_case_phanteks_p360a,pc_seed_case_phanteks_p360a | 1 | pc_case_pc_seed_case_phanteks_p360a:pc_case/P360A, pc_case_pc_seed_case_phanteks_p360a_v2:pc_case/P360A V2, pc_case_pc_seed_case_phanteks_p360a_v3:pc_case/P360A V3 | 0 | 1/1 | 242/12 | pc_case_pc_seed_case_phanteks_p360a:0.9225; pc_case_pc_seed_case_phanteks_p360a_v2:0.9147; pc_case_pc_seed_case_phanteks_p360a_v3:0.9069 |
| pc_part_b760_ddr5_motherboard | rag_only | normalized_id_match | pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5,pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5 / pc_motherboard | pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5,pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5 |  |  | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3 | pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5 | 1 | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5:pc_motherboard/PRO B760M-A WIFI DDR5, pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2:pc_motherboard/PRO B760M-A WIFI DDR5 V2, pc_motherboard_pc_seed_motherboard_m | 0 | 1/1 | 242/9 | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5:0.7625; pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2:0.756; pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3:0.7496 |
| pc_part_750w_gold_psu | rag_only | normalized_id_match | pc_seed_psu_super_flower_leadex_g_750w / pc_psu | pc_seed_psu_super_flower_leadex_g_750w |  |  | pc_psu_pc_seed_psu_super_flower_leadex_g_750w,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3 | pc_seed_psu_super_flower_leadex_g_750w,pc_seed_psu_super_flower_leadex_g_750w,pc_seed_psu_super_flower_leadex_g_750w | 1 | pc_psu_pc_seed_psu_super_flower_leadex_g_750w:pc_psu/LEADEX G 750W, pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2:pc_psu/LEADEX G 750W V2, pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3:pc_psu/LEADEX G 750W V3 | 0 | 1/1 | 242/5 | pc_psu_pc_seed_psu_super_flower_leadex_g_750w:0.7625; pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2:0.735; pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3:0.7075 |
| pc_part_2tb_nvme_ssd | rag_only | normalized_id_match | pc_seed_ssd_samsung_990_pro_2tb,pc_seed_ssd_wd_black_sn850x_2tb / pc_storage | pc_seed_ssd_samsung_990_pro_2tb,pc_seed_ssd_wd_black_sn850x_2tb |  |  | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3 | pc_seed_ssd_wd_black_sn850x_2tb,pc_seed_ssd_wd_black_sn850x_2tb,pc_seed_ssd_wd_black_sn850x_2tb | 1 | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb:pc_storage/BLACK SN850X 2TB, pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2:pc_storage/BLACK SN850X 2TB Rev2, pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3:pc_storage/BLACK SN850X 2TB Rev3 | 0 | 1/1 | 242/10 | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb:0.7625; pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2:0.7488; pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3:0.735 |
| pc_part_long_gpu_case | rag_only | normalized_id_match | pc_seed_case_fractal_design_north,pc_seed_case_phanteks_p360a,pc_seed_case_nzxt_h5_flow / pc_case | pc_seed_case_fractal_design_north,pc_seed_case_phanteks_p360a,pc_seed_case_nzxt_h5_flow |  |  | pc_case_pc_seed_case_phanteks_p360a,pc_case_pc_seed_case_phanteks_p360a_v2,pc_case_pc_seed_case_phanteks_p360a_v3 | pc_seed_case_phanteks_p360a,pc_seed_case_phanteks_p360a,pc_seed_case_phanteks_p360a | 1 | pc_case_pc_seed_case_phanteks_p360a:pc_case/P360A, pc_case_pc_seed_case_phanteks_p360a_v2:pc_case/P360A V2, pc_case_pc_seed_case_phanteks_p360a_v3:pc_case/P360A V3 | 0 | 1/1 | 242/12 | pc_case_pc_seed_case_phanteks_p360a:0.9225; pc_case_pc_seed_case_phanteks_p360a_v2:0.9147; pc_case_pc_seed_case_phanteks_p360a_v3:0.9069 |
| pc_part_b760_ddr5_motherboard | llm_only | normalized_id_match | pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5,pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5 / pc_motherboard | pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5,pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5 |  |  | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3 | pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5 | 1 | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5:pc_motherboard/PRO B760M-A WIFI DDR5, pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2:pc_motherboard/PRO B760M-A WIFI DDR5 V2, pc_motherboard_pc_seed_motherboard_m | 0 | 1/1 | 242/9 | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5:0.7625; pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2:0.756; pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3:0.7496 |
| pc_part_750w_gold_psu | llm_only | normalized_id_match | pc_seed_psu_super_flower_leadex_g_750w / pc_psu | pc_seed_psu_super_flower_leadex_g_750w |  |  | pc_psu_pc_seed_psu_super_flower_leadex_g_750w,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3 | pc_seed_psu_super_flower_leadex_g_750w,pc_seed_psu_super_flower_leadex_g_750w,pc_seed_psu_super_flower_leadex_g_750w | 1 | pc_psu_pc_seed_psu_super_flower_leadex_g_750w:pc_psu/LEADEX G 750W, pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2:pc_psu/LEADEX G 750W V2, pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3:pc_psu/LEADEX G 750W V3 | 0 | 1/1 | 242/5 | pc_psu_pc_seed_psu_super_flower_leadex_g_750w:0.7625; pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2:0.735; pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3:0.7075 |
| pc_part_2tb_nvme_ssd | llm_only | normalized_id_match | pc_seed_ssd_samsung_990_pro_2tb,pc_seed_ssd_wd_black_sn850x_2tb / pc_storage | pc_seed_ssd_samsung_990_pro_2tb,pc_seed_ssd_wd_black_sn850x_2tb |  |  | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3 | pc_seed_ssd_wd_black_sn850x_2tb,pc_seed_ssd_wd_black_sn850x_2tb,pc_seed_ssd_wd_black_sn850x_2tb | 1 | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb:pc_storage/BLACK SN850X 2TB, pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2:pc_storage/BLACK SN850X 2TB Rev2, pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3:pc_storage/BLACK SN850X 2TB Rev3 | 0 | 1/1 | 242/10 | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb:0.7625; pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2:0.7488; pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3:0.735 |
| pc_part_long_gpu_case | llm_only | normalized_id_match | pc_seed_case_fractal_design_north,pc_seed_case_phanteks_p360a,pc_seed_case_nzxt_h5_flow / pc_case | pc_seed_case_fractal_design_north,pc_seed_case_phanteks_p360a,pc_seed_case_nzxt_h5_flow |  |  | pc_case_pc_seed_case_phanteks_p360a,pc_case_pc_seed_case_phanteks_p360a_v2,pc_case_pc_seed_case_phanteks_p360a_v3 | pc_seed_case_phanteks_p360a,pc_seed_case_phanteks_p360a,pc_seed_case_phanteks_p360a | 1 | pc_case_pc_seed_case_phanteks_p360a:pc_case/P360A, pc_case_pc_seed_case_phanteks_p360a_v2:pc_case/P360A V2, pc_case_pc_seed_case_phanteks_p360a_v3:pc_case/P360A V3 | 0 | 1/1 | 242/12 | pc_case_pc_seed_case_phanteks_p360a:0.9225; pc_case_pc_seed_case_phanteks_p360a_v2:0.9147; pc_case_pc_seed_case_phanteks_p360a_v3:0.9069 |
| pc_part_b760_ddr5_motherboard | full | normalized_id_match | pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5,pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5 / pc_motherboard | pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5,pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5 |  |  | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3 | pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5 | 1 | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5:pc_motherboard/PRO B760M-A WIFI DDR5, pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2:pc_motherboard/PRO B760M-A WIFI DDR5 V2, pc_motherboard_pc_seed_motherboard_m | 0 | 1/1 | 242/9 | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5:0.7625; pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2:0.756; pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3:0.7496 |
| pc_part_750w_gold_psu | full | normalized_id_match | pc_seed_psu_super_flower_leadex_g_750w / pc_psu | pc_seed_psu_super_flower_leadex_g_750w |  |  | pc_psu_pc_seed_psu_super_flower_leadex_g_750w,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3 | pc_seed_psu_super_flower_leadex_g_750w,pc_seed_psu_super_flower_leadex_g_750w,pc_seed_psu_super_flower_leadex_g_750w | 1 | pc_psu_pc_seed_psu_super_flower_leadex_g_750w:pc_psu/LEADEX G 750W, pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2:pc_psu/LEADEX G 750W V2, pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3:pc_psu/LEADEX G 750W V3 | 0 | 1/1 | 242/5 | pc_psu_pc_seed_psu_super_flower_leadex_g_750w:0.7625; pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2:0.735; pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3:0.7075 |
| pc_part_2tb_nvme_ssd | full | normalized_id_match | pc_seed_ssd_samsung_990_pro_2tb,pc_seed_ssd_wd_black_sn850x_2tb / pc_storage | pc_seed_ssd_samsung_990_pro_2tb,pc_seed_ssd_wd_black_sn850x_2tb |  |  | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3 | pc_seed_ssd_wd_black_sn850x_2tb,pc_seed_ssd_wd_black_sn850x_2tb,pc_seed_ssd_wd_black_sn850x_2tb | 1 | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb:pc_storage/BLACK SN850X 2TB, pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2:pc_storage/BLACK SN850X 2TB Rev2, pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3:pc_storage/BLACK SN850X 2TB Rev3 | 0 | 1/1 | 242/10 | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb:0.7625; pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2:0.7488; pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3:0.735 |
| pc_part_long_gpu_case | full | normalized_id_match | pc_seed_case_fractal_design_north,pc_seed_case_phanteks_p360a,pc_seed_case_nzxt_h5_flow / pc_case | pc_seed_case_fractal_design_north,pc_seed_case_phanteks_p360a,pc_seed_case_nzxt_h5_flow |  |  | pc_case_pc_seed_case_phanteks_p360a,pc_case_pc_seed_case_phanteks_p360a_v2,pc_case_pc_seed_case_phanteks_p360a_v3 | pc_seed_case_phanteks_p360a,pc_seed_case_phanteks_p360a,pc_seed_case_phanteks_p360a | 1 | pc_case_pc_seed_case_phanteks_p360a:pc_case/P360A, pc_case_pc_seed_case_phanteks_p360a_v2:pc_case/P360A V2, pc_case_pc_seed_case_phanteks_p360a_v3:pc_case/P360A V3 | 0 | 1/1 | 242/12 | pc_case_pc_seed_case_phanteks_p360a:0.9225; pc_case_pc_seed_case_phanteks_p360a_v2:0.9147; pc_case_pc_seed_case_phanteks_p360a_v3:0.9069 |

## 链路真实性结论

综合消融结论：fast_no_llm_no_rag: RAG 0.000, full 0.000, failed 4；rag_only: RAG 0.000, full 0.000, failed 12；llm_only: RAG 0.000, full 0.000, failed 4；full: RAG 0.000, full 0.000, failed 12。
