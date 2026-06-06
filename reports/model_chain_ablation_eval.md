# MallMind 外部模型链路组合评估报告

## 总览

- 报告状态：ok
- case 数：12
- 实验组：fast_baseline, rag_only, balanced_demo
- embedding provider/model：dashscope / text-embedding-v4
- Milvus collection：mallmind_product_chunks_qwen_v1

## 本轮环境限制

- 表中的 LLM 指标按“调用尝试次数”和“实际使用率”拆开理解：`LLM` 是每 case 的 LLM 调用尝试数，`llm_*_used_rate` 在 JSON 中记录实际成功使用率。
- 表中的 embedding / Milvus 调用表示该组进入了 RAG 检索路径；如果 embedding provider 不可达，系统会回落到本地结构化商品库评分。

## 总览表

| 实验组 | cases | success | route | hit@5 | p@1 | violation | card | avg ms | p95 ms | timeout | LLM | embedding | Milvus |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fast_baseline | 15 | 1.000 | 1.000 | 1.000 | 0.875 | 0.000 | 0.856 | 898.371 | 2603.270 | 0.000 | 0.000 | 0.000 | 0.000 |
| rag_only | 15 | 1.000 | 1.000 | 1.000 | 0.875 | 0.000 | 0.856 | 7554.571 | 20027.230 | 0.267 | 0.000 | 1.000 | 1.000 |
| balanced_demo | 15 | 1.000 | 1.000 | 1.000 | 0.875 | 0.000 | 0.856 | 2475.657 | 6616.750 | 0.000 | 0.933 | 1.000 | 1.000 |

## 分段结果

| case 类型 | cases | success | route | hit@5 | p@1 | card | fallback | failed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 基础推荐 | 6 | 1.000 | 1.000 | 1.000 | 1.000 | 0.875 | 1.000 | 0 |
| 条件筛选 | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 0.875 | 1.000 | 0 |
| guidance 幻觉 | 3 | 1.000 | 1.000 | 1.000 | 0.000 | 0.875 | 1.000 | 0 |
| 多轮 | 12 | 1.000 | 1.000 | 0.000 | 0.000 | 0.875 | 0.667 | 0 |
| PC 整机 | 9 | 1.000 | 1.000 | 0.000 | 0.000 | 0.781 | 0.667 | 0 |
| PC 单配件 | 12 | 1.000 | 1.000 | 1.000 | 1.000 | 0.875 | 1.000 | 0 |

## 最差 case 列表

### balanced_demo

- 无失败 case。

### fast_baseline

- 无失败 case。

### rag_only

- 无失败 case。

## 结论建议

- 默认部署建议：优先使用 balanced_demo 作为 demo / 准生产默认模式，前提是 LLM 与 Milvus 环境稳定。
- CI 与降级建议：fast_baseline 适合作为稳定回归和外部依赖不可用时的兜底模式；不要把 fast 描述为使用 Milvus。
- full 建议：full_llm_all 更适合离线评估或高质量请求，不建议在没有延迟、成本和失败率监控前默认开启。
- query expansion 建议：默认关闭；只有当 A2/C2 对比证明召回收益明显且 query 漂移可控时再打开。
- guidance 建议：默认关闭或仅在商品卡片稳定后开启；开启后要持续检查 reason 是否被商品证据支撑。
- fast 仍有可用结果，说明规则 + 本地 catalog scoring 可以承担基础兜底。

## stability_eval

| group | cases | success | route | tool | violation | catalog_gap | error | no_500 | timeout | fallback | failed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fast_baseline | 15 | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 | 1.000 | 0.000 | 1.000 | 0 |
| rag_only | 15 | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 | 1.000 | 0.267 | 1.000 | 0 |
| balanced_demo | 15 | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 | 1.000 | 0.000 | 0.533 | 0 |

## capability_eval

| group | cases | llm_attempted | llm_success | llm_applied | router a/s/ap | parse a/s/ap | guidance a/s/ap | rag_attempted | embedding | milvus | nonempty | timeout | fallback | rag_n | rag_hit@5 | rag_p@1 | rag_mrr | rag_top1_changed | evidence_reason | llm_timeout | llm_json_invalid | llm_provider_error |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fast_baseline | 15 | 0.000 | 0.000 | 0.000 | 0.000/0.000/0.000 | 0.000/0.000/0.000 | 0.000/0.000/0.000 | 0.000 | 0.000 | 0.000 | 0.467 | 0.000 | 0.000 | 0 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | N/A | N/A | N/A |
| rag_only | 15 | 0.000 | 0.000 | 0.000 | 0.000/0.000/0.000 | 0.000/0.000/0.000 | 0.000/0.000/0.000 | 1.000 | 0.733 | 0.267 | 0.733 | 0.267 | 0.267 | 4 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | N/A | N/A | N/A |
| balanced_demo | 15 | 0.800 | 0.400 | 0.133 | 0.400/0.400/0.133 | 0.533/0.000/0.000 | 0.000/0.000/0.000 | 1.000 | 1.000 | 0.533 | 1.000 | 0.000 | 0.000 | 8 | 1.000 | 0.875 | 0.938 | 0.000 | 1.000 | N/A | N/A | N/A |

## vs_fast_delta

| case_id | fast_top1 | balanced_top1 | balanced_win | c2_top1 | c2_win | full_top1 | full_win | fast_rank | balanced_rank | c2_rank | full_rank |
| --- | --- | --- | ---: | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| ecom_basketball_shoes_cushion_1000 | p_clothes_012 | p_clothes_012 | 0 |  | 0 |  | 0 | 1.000 | 1.000 | N/A | N/A |
| ecom_office_coffee | p_food_002 | p_food_002 | 0 |  | 0 |  | 0 | 2.000 | 2.000 | N/A | N/A |
| ecom_phone_under_5000_exclude_xiaomi | p_digital_016 | p_digital_016 | 0 |  | 0 |  | 0 | 1.000 | 1.000 | N/A | N/A |
| ecom_sunscreen_oily_summer | p_beauty_006 | p_beauty_006 | 0 |  | 0 |  | 0 | 1.000 | 1.000 | N/A | N/A |
| pc_build_7000_3a | pc_seed_cpu_intel_core_i5_13400f | pc_seed_cpu_intel_core_i5_13400f | 0 |  | 0 |  | 0 | N/A | N/A | N/A | N/A |
| pc_build_multiturn_adjust_t1 | pc_seed_cpu_intel_core_i5_13400f | pc_seed_cpu_intel_core_i5_13400f | 0 |  | 0 |  | 0 | N/A | N/A | N/A | N/A |
| pc_build_multiturn_adjust_t2 | pc_seed_cpu_intel_core_i7_14700f | pc_seed_cpu_intel_core_i7_14700f | 0 |  | 0 |  | 0 | N/A | N/A | N/A | N/A |
| pc_build_multiturn_adjust_t3 | pc_seed_cpu_amd_ryzen_5_5600g | pc_seed_cpu_amd_ryzen_5_5600g | 0 |  | 0 |  | 0 | N/A | N/A | N/A | N/A |
| pc_build_multiturn_adjust_t4 | pc_seed_cpu_amd_ryzen_5_5600g | pc_seed_cpu_amd_ryzen_5_5600g | 0 |  | 0 |  | 0 | N/A | N/A | N/A | N/A |
| pc_build_rtx4070_8000 | pc_seed_cpu_intel_core_i3_12100f | pc_seed_cpu_intel_core_i3_12100f | 0 |  | 0 |  | 0 | N/A | N/A | N/A | N/A |
| pc_build_video_edit_9000 | pc_seed_cpu_intel_core_i3_12100f | pc_seed_cpu_intel_core_i3_12100f | 0 |  | 0 |  | 0 | N/A | N/A | N/A | N/A |
| pc_part_2tb_nvme_ssd | pc_seed_ssd_wd_black_sn850x_2tb | pc_seed_ssd_wd_black_sn850x_2tb | 0 |  | 0 |  | 0 | 1.000 | 1.000 | N/A | N/A |
| pc_part_750w_gold_psu | pc_seed_psu_super_flower_leadex_g_750w | pc_seed_psu_super_flower_leadex_g_750w | 0 |  | 0 |  | 0 | 1.000 | 1.000 | N/A | N/A |
| pc_part_b760_ddr5_motherboard | pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5 | pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5 | 0 |  | 0 |  | 0 | 1.000 | 1.000 | N/A | N/A |
| pc_part_long_gpu_case | pc_seed_case_phanteks_p360a | pc_seed_case_phanteks_p360a | 0 |  | 0 |  | 0 | 1.000 | 1.000 | N/A | N/A |

## RAG diagnostics

| group | case_id | status | retrieved | expected_rank_in_retrieval | rec_rank | fallback | timeout | evidence_card | evidence_reason |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| fast_baseline | pc_build_7000_3a | 0 | pc_cpu_pc_seed_cpu_intel_core_i5_13400f,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_gpu_pc_seed_gpu_nvidia_geforce_rtx_4060_ti_8g,pc_memory_pc_seed_memory_kings | N/A | N/A | 0 | 0 | 1 | 0 |
| fast_baseline | pc_build_video_edit_9000 | 0 | pc_cpu_pc_seed_cpu_intel_core_i3_12100f,pc_motherboard_pc_seed_motherboard_asus_rog_strix_b760_g_gaming_wifi,pc_gpu_pc_seed_gpu_zotac_geforce_rtx_4070_super_x_gaming,pc_memory_pc_s | N/A | N/A | 0 | 0 | 1 | 0 |
| fast_baseline | pc_build_rtx4070_8000 | 0 | pc_cpu_pc_seed_cpu_intel_core_i3_12100f,pc_motherboard_pc_seed_motherboard_asus_rog_strix_b760_g_gaming_wifi,pc_gpu_pc_seed_gpu_maxsun_geforce_rtx_4070_icraft_oc12g,pc_memory_pc_se | N/A | N/A | 0 | 0 | 1 | 0 |
| fast_baseline | pc_build_multiturn_adjust_t1 | 0 | pc_cpu_pc_seed_cpu_intel_core_i5_13400f,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_gpu_pc_seed_gpu_nvidia_geforce_rtx_4060_ti_8g,pc_memory_pc_seed_memory_kings | N/A | N/A | 0 | 0 | 1 | 0 |
| fast_baseline | pc_build_multiturn_adjust_t2 | 0 | pc_cpu_pc_seed_cpu_intel_core_i7_14700f,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_gpu_pc_seed_gpu_sapphire_radeon_rx_7600_8g,pc_memory_pc_seed_memory_kingston | N/A | N/A | 0 | 0 | 1 | 0 |
| fast_baseline | pc_build_multiturn_adjust_t3 | 0 | pc_cpu_pc_seed_cpu_amd_ryzen_5_5600g,pc_motherboard_pc_seed_motherboard_asus_prime_a520m_k,pc_gpu_pc_seed_gpu_zotac_geforce_rtx_4060_ti_16g_twin_edge,pc_memory_pc_seed_memory_kingb | N/A | N/A | 0 | 0 | 1 | 0 |
| fast_baseline | pc_build_multiturn_adjust_t4 | 0 | pc_cpu_pc_seed_cpu_amd_ryzen_5_5600g,pc_motherboard_pc_seed_motherboard_asus_prime_a520m_k,pc_gpu_pc_seed_gpu_zotac_geforce_rtx_4060_ti_16g_twin_edge,pc_memory_pc_seed_memory_kingb | N/A | N/A | 0 | 0 | 1 | 0 |
| rag_only | ecom_sunscreen_oily_summer | 0 |  | N/A | 1.000 | 1 | 1 | 0 | 0 |
| rag_only | ecom_phone_under_5000_exclude_xiaomi | 0 |  | N/A | 1.000 | 1 | 1 | 0 | 0 |
| rag_only | ecom_basketball_shoes_cushion_1000 | 0 |  | N/A | 1.000 | 1 | 1 | 0 | 0 |
| rag_only | ecom_office_coffee | 0 |  | N/A | 2.000 | 1 | 1 | 0 | 0 |
| rag_only | pc_part_b760_ddr5_motherboard | 1 | pc_motherboard_pc_seed_motherboard_asus_rog_strix_b760_g_gaming_wifi,pc_motherboard_pc_seed_motherboard_asus_rog_strix_b760_g_gaming_wifi_v3,pc_motherboard_pc_seed_motherboard_giga | 3.000 | 1.000 | 0 | 0 | 1 | 1 |
| rag_only | pc_part_750w_gold_psu | 1 | pc_psu_pc_seed_psu_cooler_master_mwe_550_bronze,pc_psu_pc_seed_psu_cooler_master_mwe_550_bronze_v3,pc_psu_pc_seed_psu_great_wall_gx850_atx3_0,pc_psu_pc_seed_psu_great_wall_gx850_at | 6.000 | 1.000 | 0 | 0 | 1 | 1 |
| rag_only | pc_part_2tb_nvme_ssd | 1 | pc_storage_pc_seed_ssd_samsung_990_pro_2tb,pc_storage_pc_seed_ssd_samsung_990_pro_2tb_rev2,pc_storage_pc_seed_ssd_samsung_990_pro_2tb_rev3,pc_storage_pc_seed_ssd_samsung_990_pro_2t | 1.000 | 1.000 | 0 | 0 | 1 | 1 |
| rag_only | pc_part_long_gpu_case | 1 | pc_case_pc_seed_case_fractal_design_north,pc_case_pc_seed_case_fractal_design_north_v2,pc_case_pc_seed_case_fractal_design_north_v3,pc_case_pc_seed_case_fractal_design_north_v4,pc_ | 1.000 | 1.000 | 0 | 0 | 1 | 1 |
| rag_only | pc_build_7000_3a | 0 | pc_cpu_pc_seed_cpu_intel_core_i5_13400f,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_gpu_pc_seed_gpu_nvidia_geforce_rtx_4060_ti_8g,pc_memory_pc_seed_memory_kings | N/A | N/A | 0 | 0 | 1 | 0 |
| rag_only | pc_build_video_edit_9000 | 0 | pc_cpu_pc_seed_cpu_intel_core_i3_12100f,pc_motherboard_pc_seed_motherboard_asus_rog_strix_b760_g_gaming_wifi,pc_gpu_pc_seed_gpu_zotac_geforce_rtx_4070_super_x_gaming,pc_memory_pc_s | N/A | N/A | 0 | 0 | 1 | 0 |
| rag_only | pc_build_rtx4070_8000 | 0 | pc_cpu_pc_seed_cpu_intel_core_i3_12100f,pc_motherboard_pc_seed_motherboard_asus_rog_strix_b760_g_gaming_wifi,pc_gpu_pc_seed_gpu_maxsun_geforce_rtx_4070_icraft_oc12g,pc_memory_pc_se | N/A | N/A | 0 | 0 | 1 | 0 |
| rag_only | pc_build_multiturn_adjust_t1 | 0 | pc_cpu_pc_seed_cpu_intel_core_i5_13400f,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_gpu_pc_seed_gpu_nvidia_geforce_rtx_4060_ti_8g,pc_memory_pc_seed_memory_kings | N/A | N/A | 0 | 0 | 1 | 0 |
| rag_only | pc_build_multiturn_adjust_t2 | 0 | pc_cpu_pc_seed_cpu_intel_core_i7_14700f,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_gpu_pc_seed_gpu_sapphire_radeon_rx_7600_8g,pc_memory_pc_seed_memory_kingston | N/A | N/A | 0 | 0 | 1 | 0 |
| rag_only | pc_build_multiturn_adjust_t3 | 0 | pc_cpu_pc_seed_cpu_amd_ryzen_5_5600g,pc_motherboard_pc_seed_motherboard_asus_prime_a520m_k,pc_gpu_pc_seed_gpu_zotac_geforce_rtx_4060_ti_16g_twin_edge,pc_memory_pc_seed_memory_kingb | N/A | N/A | 0 | 0 | 1 | 0 |
| rag_only | pc_build_multiturn_adjust_t4 | 0 | pc_cpu_pc_seed_cpu_amd_ryzen_5_5600g,pc_motherboard_pc_seed_motherboard_asus_prime_a520m_k,pc_gpu_pc_seed_gpu_zotac_geforce_rtx_4060_ti_16g_twin_edge,pc_memory_pc_seed_memory_kingb | N/A | N/A | 0 | 0 | 1 | 0 |
| balanced_demo | ecom_sunscreen_oily_summer | 1 | p_beauty_006,p_beauty_010,p_beauty_017,p_beauty_018,p_beauty_020,p_beauty_023 | 1.000 | 1.000 | 0 | 0 | 1 | 1 |
| balanced_demo | ecom_phone_under_5000_exclude_xiaomi | 1 | p_digital_001,p_digital_006,p_digital_008,p_digital_009,p_digital_010,p_digital_011,p_digital_014,p_digital_015,p_digital_016,p_digital_017,p_digital_020 | 9.000 | 1.000 | 0 | 0 | 1 | 1 |
| balanced_demo | ecom_basketball_shoes_cushion_1000 | 1 | p_clothes_008,p_clothes_011,p_clothes_012,p_clothes_013 | 2.000 | 1.000 | 0 | 0 | 1 | 1 |
| balanced_demo | ecom_office_coffee | 1 | p_digital_005,p_digital_007,p_digital_010,p_digital_014,p_digital_016,p_digital_018,p_digital_019,p_digital_021,p_digital_022,p_digital_023,p_digital_024,p_food_001,p_food_002,p_fo | 12.000 | 2.000 | 0 | 0 | 1 | 1 |
| balanced_demo | pc_part_b760_ddr5_motherboard | 1 | pc_motherboard_pc_seed_motherboard_asus_rog_strix_b760_g_gaming_wifi,pc_motherboard_pc_seed_motherboard_asus_rog_strix_b760_g_gaming_wifi_v3,pc_motherboard_pc_seed_motherboard_giga | 3.000 | 1.000 | 0 | 0 | 1 | 1 |
| balanced_demo | pc_part_750w_gold_psu | 1 | pc_psu_pc_seed_psu_cooler_master_mwe_550_bronze,pc_psu_pc_seed_psu_cooler_master_mwe_550_bronze_v3,pc_psu_pc_seed_psu_great_wall_gx850_atx3_0,pc_psu_pc_seed_psu_great_wall_gx850_at | 6.000 | 1.000 | 0 | 0 | 1 | 1 |
| balanced_demo | pc_part_2tb_nvme_ssd | 1 | pc_storage_pc_seed_ssd_samsung_990_pro_2tb,pc_storage_pc_seed_ssd_samsung_990_pro_2tb_rev2,pc_storage_pc_seed_ssd_samsung_990_pro_2tb_rev3,pc_storage_pc_seed_ssd_samsung_990_pro_2t | 1.000 | 1.000 | 0 | 0 | 1 | 1 |
| balanced_demo | pc_part_long_gpu_case | 1 | pc_case_pc_seed_case_fractal_design_north,pc_case_pc_seed_case_fractal_design_north_v2,pc_case_pc_seed_case_fractal_design_north_v3,pc_case_pc_seed_case_fractal_design_north_v4,pc_ | 1.000 | 1.000 | 0 | 0 | 1 | 1 |
| balanced_demo | pc_build_7000_3a | 0 | pc_cpu_pc_seed_cpu_intel_core_i5_13400f,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_gpu_pc_seed_gpu_nvidia_geforce_rtx_4060_ti_8g,pc_memory_pc_seed_memory_kings | N/A | N/A | 0 | 0 | 1 | 0 |
| balanced_demo | pc_build_video_edit_9000 | 0 | pc_cpu_pc_seed_cpu_intel_core_i3_12100f,pc_motherboard_pc_seed_motherboard_asus_rog_strix_b760_g_gaming_wifi,pc_gpu_pc_seed_gpu_zotac_geforce_rtx_4070_super_x_gaming,pc_memory_pc_s | N/A | N/A | 0 | 0 | 1 | 0 |
| balanced_demo | pc_build_rtx4070_8000 | 0 | pc_cpu_pc_seed_cpu_intel_core_i3_12100f,pc_motherboard_pc_seed_motherboard_asus_rog_strix_b760_g_gaming_wifi,pc_gpu_pc_seed_gpu_maxsun_geforce_rtx_4070_icraft_oc12g,pc_memory_pc_se | N/A | N/A | 0 | 0 | 1 | 0 |
| balanced_demo | pc_build_multiturn_adjust_t1 | 0 | pc_cpu_pc_seed_cpu_intel_core_i5_13400f,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_gpu_pc_seed_gpu_nvidia_geforce_rtx_4060_ti_8g,pc_memory_pc_seed_memory_kings | N/A | N/A | 0 | 0 | 1 | 0 |
| balanced_demo | pc_build_multiturn_adjust_t2 | 0 | pc_cpu_pc_seed_cpu_intel_core_i7_14700f,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_gpu_pc_seed_gpu_sapphire_radeon_rx_7600_8g,pc_memory_pc_seed_memory_kingston | N/A | N/A | 0 | 0 | 1 | 0 |
| balanced_demo | pc_build_multiturn_adjust_t3 | 0 | pc_cpu_pc_seed_cpu_amd_ryzen_5_5600g,pc_motherboard_pc_seed_motherboard_asus_prime_a520m_k,pc_gpu_pc_seed_gpu_zotac_geforce_rtx_4060_ti_16g_twin_edge,pc_memory_pc_seed_memory_kingb | N/A | N/A | 0 | 0 | 1 | 0 |
| balanced_demo | pc_build_multiturn_adjust_t4 | 0 | pc_cpu_pc_seed_cpu_amd_ryzen_5_5600g,pc_motherboard_pc_seed_motherboard_asus_prime_a520m_k,pc_gpu_pc_seed_gpu_zotac_geforce_rtx_4060_ti_16g_twin_edge,pc_memory_pc_seed_memory_kingb | N/A | N/A | 0 | 0 | 1 | 0 |

## LLM diagnostics

| group | case_id | router a/s/ap | router_failure | local | llm | final | parse a/s/ap | parse_failure | guidance a/s/ap | guidance_failure | changed_route | changed_rec | clarification |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: |
| balanced_demo | ecom_sunscreen_oily_summer | 1/1/1 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | ecom_phone_under_5000_exclude_xiaomi | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | ecom_basketball_shoes_cushion_1000 | 1/1/1 |  | recommend_shopping_products | recommend_shopping_products | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | ecom_office_coffee | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | pc_part_b760_ddr5_motherboard | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | pc_part_750w_gold_psu | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | pc_part_2tb_nvme_ssd | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | pc_part_long_gpu_case | 0/0/0 |  | recommend_shopping_products | None | recommend_shopping_products | 1/0/0 | rule_parse_sufficient_or_auto_ | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | pc_build_7000_3a | 0/0/0 |  | generate_pc_build_plan | None | generate_pc_build_plan | 0/0/0 |  | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | pc_build_video_edit_9000 | 1/1/0 |  | generate_pc_build_plan | recommend_shopping_products | generate_pc_build_plan | 0/0/0 |  | 0/0/0 |  | 1 | 0 | 0 |
| balanced_demo | pc_build_rtx4070_8000 | 0/0/0 |  | generate_pc_build_plan | None | generate_pc_build_plan | 0/0/0 |  | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | pc_build_multiturn_adjust_t1 | 0/0/0 |  | generate_pc_build_plan | None | generate_pc_build_plan | 0/0/0 |  | 0/0/0 |  | 0 | 0 | 0 |
| balanced_demo | pc_build_multiturn_adjust_t2 | 1/1/0 |  | generate_pc_build_plan | apply_cart_instruction | generate_pc_build_plan | 0/0/0 |  | 0/0/0 |  | 1 | 0 | 0 |
| balanced_demo | pc_build_multiturn_adjust_t3 | 1/1/0 |  | generate_pc_build_plan | general_chat | generate_pc_build_plan | 0/0/0 |  | 0/0/0 |  | 1 | 0 | 0 |
| balanced_demo | pc_build_multiturn_adjust_t4 | 1/1/0 |  | generate_pc_build_plan | compare_products | generate_pc_build_plan | 0/0/0 |  | 0/0/0 |  | 1 | 0 | 0 |

## capability_challenge_assessment

- main_metrics_identical: True
- balanced_demo top1 wins vs fast: 0 (none)
- Main recommendation metrics are still identical across compared groups; this run did not prove recommendation uplift over fast_baseline.
- vs_fast_delta found no top1 recommendation wins; use this as evidence that current challenge cases or current chain weighting still do not expose business uplift.
- RAG was effectively retrieved on some rows, but the evidence did not change top1 outcomes enough to improve main metrics.

## capability/stability conclusions

### stability_eval
- A_vs_A2: 稳定性：rag_only 相对 fast_baseline 成功率变化 0.0，错误率变化 0.0，fallback 变化 0.0。
- A2_vs_B: 稳定性：balanced_demo 相对 rag_only 路由准确率变化 0.0，成功率变化 0.0，违规率变化 0.0。
- B_vs_D: 降级稳定性：degraded_success_rate=0，fallback_triggered_rate=0.0。
### capability_eval
- RAG_A2: rag_only: RAG 有效样本 4 个，有效 hit@5=1.0，有效 p@1=1.0。
- RAG_B: balanced_demo: RAG 有效样本 8 个，有效 hit@5=1.0，有效 p@1=0.875。
- LLM_router_B: balanced_demo: router attempted=0.4，success=0.4，applied=0.1333。
- LLM_router_B1: router_llm_only: LLM router not effectively exercised，不能据此判断 router LLM 贡献。
- LLM_overall_B_vs_C: 能力：full_llm_all 相对 balanced_demo llm_attempted 变化 -0.8，llm_used 变化 -0.4，llm_applied 变化 -0.1333。
- QueryExpansion_C_vs_C2: 能力：query expansion 只能在 RAG 有效样本上解释；full_no_query_expansion RAG 有效样本=0，full_llm_all RAG 有效样本=0。
