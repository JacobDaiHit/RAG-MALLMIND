# MallMind full 链路消融评估报告

## 总览

| 指标 | 值 |
| --- | ---: |
| 总 case 数 | 27 |
| failed | 5 |
| suspicious | 0 |
| RAG 适用 case 数 | 8 |
| RAG 不适用 case 数 | 19 |
| rag_chain_valid_rate | 1.0000 |
| full_chain_valid_rate | 0.0000 |
| latency avg / p50 / p95 | 1111.73 / 214.94 / 4750.93 ms |

## 四组模式对比

| mode | cases | P@1 | Hit@5 | route | RAG | Milvus | raw hit | LLM parse | rag valid | full valid | failed | suspicious | avg ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| rag_only | 27 | 0.222 | 0.296 | 0.852 | 0.518 | 0.518 | 0.519 | 0.000 | 1.000 | 0.000 | 5 | 0 | 1111.73 |

## 评估桶汇总

| bucket | cases | RAG applicable | rag valid | route | failed | suspicious | note |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| in_catalog_ecommerce_rag | 4 | 4 | 1.000 | 1.000 | 0 | 0 | 电商正例检索链路 |
| pc_part_rag | 4 | 4 | 1.000 | 1.000 | 0 | 0 | PC 单配件检索链路 |
| pc_build_structured | 3 | 0 | 0.000 | 1.000 | 0 | 0 | 结构化装机规划，不默认要求 Milvus |
| negative_guard | 4 | 0 | 0.000 | 1.000 | 0 | 0 | 负例/安全/不支持品类 guard |
| ambiguous_llm_needed | 3 | 0 | 0.000 | 1.000 | 1 | 0 | 模糊需求，rag_only 下多为口径不适用 |
| route_boundary | 5 | 0 | 0.000 | 0.800 | 1 | 0 | 路由边界与会话指令 |
| multiturn_session | 4 | 0 | 0.000 | 0.250 | 3 | 0 | 多轮会话，rag_only 下仅记录路由/澄清 |

## ecommerce summary

- case_count: 7
- precision@1: 0.286
- hit@5: 0.571
- no_match_accuracy: 1.000
- route_accuracy: 1.000
- latency_avg_ms: 1922.9

## pc_parts summary

- case_count: 4
- precision@1: 1.000
- hit@5: 1.000
- no_match_accuracy: 1.000
- route_accuracy: 1.000
- latency_avg_ms: 213.26

## pc_build summary

- case_count: 7
- precision@1: 0.000
- hit@5: 0.000
- no_match_accuracy: 1.000
- route_accuracy: 0.571
- latency_avg_ms: 1956.41

## negative/no-match summary

- case_count: 4
- precision@1: 0.000
- hit@5: 0.000
- no_match_accuracy: 1.000
- route_accuracy: 1.000
- latency_avg_ms: 5.73

## failed/suspicious case 明细

| case | mode | status | tool | RAG | raw | after | source | rag_reason | LLM(router/parse/enhance) | failed_reason |
| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |
| pc_build_multiturn_adjust_t2 | rag_only | failed | recommend_shopping_products | 1 | 36 | 12 | rag_evidence | not_applicable | 0/0/0 | tool route 不符合预期 |
| pc_build_multiturn_adjust_t3 | rag_only | failed | recommend_shopping_products | 1 | 144 | 48 | rag_evidence | not_applicable | 0/0/0 | tool route 不符合预期 |
| pc_build_multiturn_adjust_t4 | rag_only | failed | recommend_shopping_products | 0 | 0 | 0 | structured_catalog | not_applicable | 0/0/0 | 未识别到有效购物场景，请描述想买什么、预算、用途或偏好。 |
| ambiguous_gift_girlfriend | rag_only | failed | recommend_shopping_products | 0 | 0 | 0 | structured_catalog | not_applicable | 0/0/0 | 未识别到有效购物场景，请描述想买什么、预算、用途或偏好。 |
| route_compare_products | rag_only | failed | recommend_shopping_products | 1 | 144 | 48 | rag_evidence | not_applicable | 0/0/0 | tool route 不符合预期 |

## 问题分类

### RAG 链路问题

- 无

### 路由问题

| case | mode | bucket | status | tool | reason |
| --- | --- | --- | --- | --- | --- |
| pc_build_multiturn_adjust_t2 | rag_only | multiturn_session | failed | recommend_shopping_products | tool route 不符合预期 |
| pc_build_multiturn_adjust_t3 | rag_only | multiturn_session | failed | recommend_shopping_products | tool route 不符合预期 |
| pc_build_multiturn_adjust_t4 | rag_only | multiturn_session | failed | recommend_shopping_products | 未识别到有效购物场景，请描述想买什么、预算、用途或偏好。 |
| route_compare_products | rag_only | route_boundary | failed | recommend_shopping_products | tool route 不符合预期 |

### 负例 guard 问题

- 无

### PC build 结构化规划问题

- 无

### 评估口径不适用项

| case | mode | bucket | status | tool | reason |
| --- | --- | --- | --- | --- | --- |
| pc_build_7000_3a | rag_only | pc_build_structured | ok | generate_pc_build_plan | not_applicable |
| pc_build_video_edit_9000 | rag_only | pc_build_structured | ok | generate_pc_build_plan | not_applicable |
| pc_build_rtx4070_8000 | rag_only | pc_build_structured | ok | generate_pc_build_plan | not_applicable |
| pc_build_multiturn_adjust_t1 | rag_only | multiturn_session | ok | generate_pc_build_plan | not_applicable |
| pc_build_multiturn_adjust_t2 | rag_only | multiturn_session | failed | recommend_shopping_products | tool route 不符合预期 |
| pc_build_multiturn_adjust_t3 | rag_only | multiturn_session | failed | recommend_shopping_products | tool route 不符合预期 |
| pc_build_multiturn_adjust_t4 | rag_only | multiturn_session | failed | recommend_shopping_products | 未识别到有效购物场景，请描述想买什么、预算、用途或偏好。 |
| negative_missing_outdoor_jacket | rag_only | negative_guard | ok | recommend_shopping_products | not_applicable |
| negative_impossible_iphone_budget | rag_only | negative_guard | ok | recommend_shopping_products | not_applicable |
| negative_unsupported_car | rag_only | negative_guard | ok | recommend_shopping_products | not_applicable |
| negative_prescription_drug | rag_only | negative_guard | ok | recommend_shopping_products | not_applicable |
| ambiguous_gift_girlfriend | rag_only | ambiguous_llm_needed | failed | recommend_shopping_products | 未识别到有效购物场景，请描述想买什么、预算、用途或偏好。 |
| ambiguous_summer_things | rag_only | ambiguous_llm_needed | ok | recommend_shopping_products | not_applicable |
| ambiguous_value_digital | rag_only | ambiguous_llm_needed | ok | recommend_shopping_products | not_applicable |
| route_general_hello | rag_only | route_boundary | ok | general_chat | not_applicable |
| route_compare_products | rag_only | route_boundary | failed | recommend_shopping_products | tool route 不符合预期 |
| route_add_cart | rag_only | route_boundary | ok | apply_cart_instruction | not_applicable |
| route_clear_cart | rag_only | route_boundary | ok | apply_cart_instruction | not_applicable |
| route_session_second_price | rag_only | route_boundary | ok | recommend_shopping_products | not_applicable |


## PC 单配件 final_recommendation_miss 诊断

| case | mode | failure_type | expected | normalized expected | retrieved top10 | normalized retrieved top10 | recommended top10 | normalized recommended top10 | normalized match | rec category | retrieved has expected | component/spec match | candidates before/after | score top |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- | ---: | --- | --- | --- |
| pc_part_b760_ddr5_motherboard | rag_only | normalized_id_match | pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5,pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5 / pc_motherboard | pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5,pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5 | pc_motherboard_pc_seed_motherboard_asus_rog_strix_b760_g_gaming_wifi,pc_motherboard_pc_seed_motherboard_asus_rog_strix_b760_g_gaming_wifi_v3,pc_motherboard_pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5,pc_motherboard_pc_seed_mother | pc_seed_motherboard_asus_rog_strix_b760_g_gaming_wifi,pc_seed_motherboard_asus_rog_strix_b760_g_gaming_wifi,pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5,pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5,pc_seed_motherboard_gi | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3,pc_motherboard_pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5 | pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5 | 1 | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5:pc_motherboard/PRO B760M-A WIFI DDR5, pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3:pc_motherboard/PRO B760M-A WIFI DDR5 V3, pc_motherboard_pc_seed_motherboard_g | 1 | 1/1 | 242/9 | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5:0.7837; pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3:0.7708; pc_motherboard_pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5:0.7676 |
| pc_part_750w_gold_psu | rag_only | normalized_id_match | pc_seed_psu_super_flower_leadex_g_750w / pc_psu | pc_seed_psu_super_flower_leadex_g_750w | pc_psu_pc_seed_psu_cooler_master_mwe_550_bronze,pc_psu_pc_seed_psu_cooler_master_mwe_550_bronze_v3,pc_psu_pc_seed_psu_great_wall_gx850_atx3_0,pc_psu_pc_seed_psu_great_wall_gx850_atx3_0_v4,pc_psu_pc_seed_psu_great_wall_gx850_atx3_0_v5,pc_psu | pc_seed_psu_cooler_master_mwe_550_bronze,pc_seed_psu_cooler_master_mwe_550_bronze,pc_seed_psu_great_wall_gx850_atx3_0,pc_seed_psu_great_wall_gx850_atx3_0,pc_seed_psu_great_wall_gx850_atx3_0,pc_seed_psu_super_flower_leadex_g_750w,pc_seed_psu | pc_psu_pc_seed_psu_super_flower_leadex_g_750w,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3 | pc_seed_psu_super_flower_leadex_g_750w,pc_seed_psu_super_flower_leadex_g_750w,pc_seed_psu_super_flower_leadex_g_750w | 1 | pc_psu_pc_seed_psu_super_flower_leadex_g_750w:pc_psu/LEADEX G 750W, pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2:pc_psu/LEADEX G 750W V2, pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3:pc_psu/LEADEX G 750W V3 | 1 | 1/1 | 242/5 | pc_psu_pc_seed_psu_super_flower_leadex_g_750w:0.7737; pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2:0.7461; pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3:0.7186 |
| pc_part_2tb_nvme_ssd | rag_only | normalized_id_match | pc_seed_ssd_samsung_990_pro_2tb,pc_seed_ssd_wd_black_sn850x_2tb / pc_storage | pc_seed_ssd_samsung_990_pro_2tb,pc_seed_ssd_wd_black_sn850x_2tb | pc_storage_pc_seed_ssd_samsung_990_pro_2tb,pc_storage_pc_seed_ssd_samsung_990_pro_2tb_rev2,pc_storage_pc_seed_ssd_samsung_990_pro_2tb_rev3,pc_storage_pc_seed_ssd_samsung_990_pro_2tb_rev4,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb,pc_storage | pc_seed_ssd_samsung_990_pro_2tb,pc_seed_ssd_samsung_990_pro_2tb,pc_seed_ssd_samsung_990_pro_2tb,pc_seed_ssd_samsung_990_pro_2tb,pc_seed_ssd_wd_black_sn850x_2tb,pc_seed_ssd_wd_black_sn850x_2tb,pc_seed_ssd_wd_black_sn850x_2tb,pc_seed_ssd_wd_b | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3 | pc_seed_ssd_wd_black_sn850x_2tb,pc_seed_ssd_wd_black_sn850x_2tb,pc_seed_ssd_wd_black_sn850x_2tb | 1 | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb:pc_storage/BLACK SN850X 2TB, pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2:pc_storage/BLACK SN850X 2TB Rev2, pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3:pc_storage/BLACK SN850X 2TB Rev3 | 1 | 1/1 | 242/10 | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb:0.7738; pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2:0.7599; pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3:0.7462 |
| pc_part_long_gpu_case | rag_only | normalized_id_match | pc_seed_case_fractal_design_north,pc_seed_case_phanteks_p360a,pc_seed_case_nzxt_h5_flow / pc_case | pc_seed_case_fractal_design_north,pc_seed_case_phanteks_p360a,pc_seed_case_nzxt_h5_flow | pc_case_pc_seed_case_fractal_design_north,pc_case_pc_seed_case_fractal_design_north_v2,pc_case_pc_seed_case_fractal_design_north_v3,pc_case_pc_seed_case_fractal_design_north_v4,pc_case_pc_seed_case_nzxt_h5_flow,pc_case_pc_seed_case_phanteks | pc_seed_case_fractal_design_north,pc_seed_case_fractal_design_north,pc_seed_case_fractal_design_north,pc_seed_case_fractal_design_north,pc_seed_case_nzxt_h5_flow,pc_seed_case_phanteks_p360a,pc_seed_case_phanteks_p360a,pc_seed_case_phanteks_ | pc_case_pc_seed_case_phanteks_p360a,pc_case_pc_seed_case_phanteks_p360a_v2,pc_case_pc_seed_case_phanteks_p360a_v3 | pc_seed_case_phanteks_p360a,pc_seed_case_phanteks_p360a,pc_seed_case_phanteks_p360a | 1 | pc_case_pc_seed_case_phanteks_p360a:pc_case/P360A, pc_case_pc_seed_case_phanteks_p360a_v2:pc_case/P360A V2, pc_case_pc_seed_case_phanteks_p360a_v3:pc_case/P360A V3 | 1 | 1/1 | 242/12 | pc_case_pc_seed_case_phanteks_p360a:0.9338; pc_case_pc_seed_case_phanteks_p360a_v2:0.9259; pc_case_pc_seed_case_phanteks_p360a_v3:0.9181 |

## 链路真实性结论

RAG-only 结论：未通过。Milvus 使用率 0.518，RAG 链路有效率 1.000。LLM parse 使用率为 0.000，在 rag_only 模式下这是预期值。
