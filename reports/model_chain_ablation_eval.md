# MallMind 外部模型链路组合评估报告

## 总览

- 报告状态：failed
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
| fast_baseline | 27 | 0.630 | 0.889 | 0.375 | 0.250 | 0.000 | 0.853 | 554.231 | 4302.500 | 0.000 | 0.000 | 0.000 | 0.000 |
| rag_only | 27 | 0.630 | 0.889 | 0.375 | 0.250 | 0.000 | 0.853 | 726.751 | 4045.530 | 0.000 | 0.000 | 1.000 | 1.000 |
| balanced_demo | 27 | 0.630 | 0.889 | 0.375 | 0.250 | 0.000 | 0.853 | 644.437 | 4005.820 | 0.000 | 0.963 | 1.000 | 1.000 |
| router_llm_only | 27 | 0.630 | 0.889 | 0.375 | 0.250 | 0.000 | 0.853 | 535.872 | 4075.980 | 0.000 | 0.000 | 0.000 | 0.000 |
| parse_llm_only | 27 | 0.630 | 0.889 | 0.375 | 0.250 | 0.000 | 0.853 | 544.048 | 4017.710 | 0.000 | 0.963 | 0.000 | 0.000 |
| full_llm_all | 27 | 0.630 | 0.889 | 0.375 | 0.250 | 0.000 | 0.853 | 681.627 | 4050.840 | 0.000 | 1.593 | 1.000 | 1.000 |
| full_no_guidance | 27 | 0.630 | 0.889 | 0.375 | 0.250 | 0.000 | 0.853 | 659.537 | 4294.380 | 0.000 | 0.963 | 1.000 | 1.000 |
| full_no_query_expansion | 27 | 0.630 | 0.889 | 0.375 | 0.250 | 0.000 | 0.853 | 664.397 | 4135.370 | 0.000 | 1.593 | 1.000 | 1.000 |
| timeout_fallback | 27 | 0.963 | 0.889 | 0.375 | 0.250 | 0.000 | 0.853 | 574.815 | 4193.520 | 0.481 | 1.593 | 1.000 | 1.000 |

## 分段结果

| case 类型 | cases | success | route | hit@5 | p@1 | card | fallback | failed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 模糊推荐 | 27 | 0.667 | 1.000 | 0.000 | 0.000 | 0.828 | 0.741 | 9 |
| 基础推荐 | 18 | 0.556 | 1.000 | 0.500 | 0.500 | 0.875 | 1.000 | 8 |
| 对比 | 9 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 | 0.222 | 0 |
| 条件筛选 | 9 | 1.000 | 1.000 | 1.000 | 1.000 | 0.875 | 1.000 | 0 |
| guidance 幻觉 | 9 | 1.000 | 1.000 | 1.000 | 0.000 | 0.875 | 1.000 | 0 |
| 多轮 | 36 | 0.333 | 0.250 | 0.000 | 0.000 | 0.875 | 0.611 | 24 |
| 负例/catalog gap | 27 | 0.704 | 1.000 | 0.000 | 0.000 | 0.000 | 1.000 | 8 |
| PC 整机 | 27 | 1.000 | 1.000 | 0.000 | 0.000 | 0.781 | 0.222 | 0 |
| PC 单配件 | 36 | 0.111 | 1.000 | 0.000 | 0.000 | 0.875 | 1.000 | 32 |
| query expansion 风险 | 9 | 1.000 | 1.000 | 0.000 | 0.000 | 0.000 | 1.000 | 0 |
| 路由边界 | 36 | 1.000 | 1.000 | 0.000 | 0.000 | 0.875 | 0.417 | 0 |

## 消融对比结论

- A_vs_A2：RAG/Milvus 贡献：hit@5 变化 0.0，precision@1 变化 0.0，平均延迟变化 172.5208ms。
- A2_vs_B：LLM router+parse 贡献：路由准确率变化 0.0，成功率变化 0.0，LLM calls/case 变化 0.963。
- B_vs_C：full 能力上限与代价：hit@5 变化 0.0，p95 延迟变化 45.02ms，失败率变化 0.0。
- C_vs_C1：guidance 贡献与风险：卡片准确率变化 0.0，guidance 使用率变化 0.0。
- C_vs_C2：query expansion 贡献与漂移风险：hit@5 变化 0.0，约束违规率变化 0.0。
- B_vs_D：降级能力：degraded_success_rate=0.963，fallback_triggered_rate=0.6296。

## 最差 case 列表

### balanced_demo

| case_id | query | expected | actual route | recommended | retrieved | failure | reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ecom_basketball_shoes_cushion_1000 | 推荐一双篮球实战鞋，缓震好，预算 1000 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_012", "p_clothes_011", "p_clothes_013"], "category": "clothing"} | recommend_shopping_products | p_clothes_007 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_clothes_007'] |
| pc_part_b760_ddr5_motherboard | 推荐一块 B760 DDR5 主板 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5", "pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5"], "category": "pc_motherboard"} | recommend_shopping_products | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5', 'pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2', 'pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3'] |
| pc_part_750w_gold_psu | 推荐一个 750W 金牌全模组电源 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_psu_super_flower_leadex_g_750w"], "category": "pc_psu"} | recommend_shopping_products | pc_psu_pc_seed_psu_super_flower_leadex_g_750w,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_psu_pc_seed_psu_super_flower_leadex_g_750w', 'pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2', 'pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3'] |
| pc_part_2tb_nvme_ssd | 推荐一块 2TB NVMe SSD | {"tool": "recommend_shopping_products", "ids": ["pc_seed_ssd_samsung_990_pro_2tb", "pc_seed_ssd_wd_black_sn850x_2tb"], "category": "pc_storage"} | recommend_shopping_products | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_storage_pc_seed_ssd_wd_black_sn850x_2tb', 'pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2', 'pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3'] |
| pc_part_long_gpu_case | 推荐一个能装长显卡的机箱 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_case_fractal_design_north", "pc_seed_case_phanteks_p360a", "pc_seed_case_nzxt_h5_flow"], "category": "pc_case"} | recommend_shopping_products | pc_case_pc_seed_case_phanteks_p360a,pc_case_pc_seed_case_phanteks_p360a_v2,pc_case_pc_seed_case_phanteks_p360a_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_case_pc_seed_case_phanteks_p360a', 'pc_case_pc_seed_case_phanteks_p360a_v2', 'pc_case_pc_seed_case_phanteks_p360a_v3'] |
| pc_build_multiturn_adjust_t2 | 把显卡换强一点 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | recommend_shopping_products | pc_gpu_pc_seed_gpu_integrated_no_discrete_gpu_use_cpu_igpu,pc_gpu_pc_seed_gpu_asrock_radeon_rx_6500_xt_4gb,pc_gpu_pc_seed_gpu_asrock_radeon_rx_6500_xt_4gb_edition_1 |  | wrong_route | 期望路由 generate_pc_build_plan，实际 recommend_shopping_products |
| pc_build_multiturn_adjust_t3 | 预算降到 6000 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | recommend_shopping_products | p_beauty_007,p_digital_019,p_clothes_001,p_food_018 |  | wrong_route | 期望路由 generate_pc_build_plan，实际 recommend_shopping_products |
| pc_build_multiturn_adjust_t4 | 上一套和现在这套差别在哪里 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | compare_products |  |  | wrong_route | 期望路由 generate_pc_build_plan，实际 compare_products |

### fast_baseline

| case_id | query | expected | actual route | recommended | retrieved | failure | reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ecom_basketball_shoes_cushion_1000 | 推荐一双篮球实战鞋，缓震好，预算 1000 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_012", "p_clothes_011", "p_clothes_013"], "category": "clothing"} | recommend_shopping_products | p_clothes_007 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_clothes_007'] |
| pc_part_b760_ddr5_motherboard | 推荐一块 B760 DDR5 主板 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5", "pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5"], "category": "pc_motherboard"} | recommend_shopping_products | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5', 'pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2', 'pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3'] |
| pc_part_750w_gold_psu | 推荐一个 750W 金牌全模组电源 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_psu_super_flower_leadex_g_750w"], "category": "pc_psu"} | recommend_shopping_products | pc_psu_pc_seed_psu_super_flower_leadex_g_750w,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_psu_pc_seed_psu_super_flower_leadex_g_750w', 'pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2', 'pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3'] |
| pc_part_2tb_nvme_ssd | 推荐一块 2TB NVMe SSD | {"tool": "recommend_shopping_products", "ids": ["pc_seed_ssd_samsung_990_pro_2tb", "pc_seed_ssd_wd_black_sn850x_2tb"], "category": "pc_storage"} | recommend_shopping_products | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_storage_pc_seed_ssd_wd_black_sn850x_2tb', 'pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2', 'pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3'] |
| pc_part_long_gpu_case | 推荐一个能装长显卡的机箱 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_case_fractal_design_north", "pc_seed_case_phanteks_p360a", "pc_seed_case_nzxt_h5_flow"], "category": "pc_case"} | recommend_shopping_products | pc_case_pc_seed_case_phanteks_p360a,pc_case_pc_seed_case_phanteks_p360a_v2,pc_case_pc_seed_case_phanteks_p360a_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_case_pc_seed_case_phanteks_p360a', 'pc_case_pc_seed_case_phanteks_p360a_v2', 'pc_case_pc_seed_case_phanteks_p360a_v3'] |
| pc_build_multiturn_adjust_t2 | 把显卡换强一点 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | recommend_shopping_products | pc_gpu_pc_seed_gpu_integrated_no_discrete_gpu_use_cpu_igpu,pc_gpu_pc_seed_gpu_asrock_radeon_rx_6500_xt_4gb,pc_gpu_pc_seed_gpu_asrock_radeon_rx_6500_xt_4gb_edition_1 |  | wrong_route | 期望路由 generate_pc_build_plan，实际 recommend_shopping_products |
| pc_build_multiturn_adjust_t3 | 预算降到 6000 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | recommend_shopping_products | p_beauty_007,p_digital_019,p_clothes_001,p_food_018 |  | wrong_route | 期望路由 generate_pc_build_plan，实际 recommend_shopping_products |
| pc_build_multiturn_adjust_t4 | 上一套和现在这套差别在哪里 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | compare_products |  |  | wrong_route | 期望路由 generate_pc_build_plan，实际 compare_products |

### full_llm_all

| case_id | query | expected | actual route | recommended | retrieved | failure | reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ecom_basketball_shoes_cushion_1000 | 推荐一双篮球实战鞋，缓震好，预算 1000 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_012", "p_clothes_011", "p_clothes_013"], "category": "clothing"} | recommend_shopping_products | p_clothes_007 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_clothes_007'] |
| pc_part_b760_ddr5_motherboard | 推荐一块 B760 DDR5 主板 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5", "pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5"], "category": "pc_motherboard"} | recommend_shopping_products | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5', 'pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2', 'pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3'] |
| pc_part_750w_gold_psu | 推荐一个 750W 金牌全模组电源 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_psu_super_flower_leadex_g_750w"], "category": "pc_psu"} | recommend_shopping_products | pc_psu_pc_seed_psu_super_flower_leadex_g_750w,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_psu_pc_seed_psu_super_flower_leadex_g_750w', 'pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2', 'pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3'] |
| pc_part_2tb_nvme_ssd | 推荐一块 2TB NVMe SSD | {"tool": "recommend_shopping_products", "ids": ["pc_seed_ssd_samsung_990_pro_2tb", "pc_seed_ssd_wd_black_sn850x_2tb"], "category": "pc_storage"} | recommend_shopping_products | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_storage_pc_seed_ssd_wd_black_sn850x_2tb', 'pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2', 'pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3'] |
| pc_part_long_gpu_case | 推荐一个能装长显卡的机箱 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_case_fractal_design_north", "pc_seed_case_phanteks_p360a", "pc_seed_case_nzxt_h5_flow"], "category": "pc_case"} | recommend_shopping_products | pc_case_pc_seed_case_phanteks_p360a,pc_case_pc_seed_case_phanteks_p360a_v2,pc_case_pc_seed_case_phanteks_p360a_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_case_pc_seed_case_phanteks_p360a', 'pc_case_pc_seed_case_phanteks_p360a_v2', 'pc_case_pc_seed_case_phanteks_p360a_v3'] |
| pc_build_multiturn_adjust_t2 | 把显卡换强一点 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | recommend_shopping_products | pc_gpu_pc_seed_gpu_integrated_no_discrete_gpu_use_cpu_igpu,pc_gpu_pc_seed_gpu_asrock_radeon_rx_6500_xt_4gb,pc_gpu_pc_seed_gpu_asrock_radeon_rx_6500_xt_4gb_edition_1 |  | wrong_route | 期望路由 generate_pc_build_plan，实际 recommend_shopping_products |
| pc_build_multiturn_adjust_t3 | 预算降到 6000 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | recommend_shopping_products | p_beauty_007,p_digital_019,p_clothes_001,p_food_018 |  | wrong_route | 期望路由 generate_pc_build_plan，实际 recommend_shopping_products |
| pc_build_multiturn_adjust_t4 | 上一套和现在这套差别在哪里 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | compare_products |  |  | wrong_route | 期望路由 generate_pc_build_plan，实际 compare_products |

### full_no_guidance

| case_id | query | expected | actual route | recommended | retrieved | failure | reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ecom_basketball_shoes_cushion_1000 | 推荐一双篮球实战鞋，缓震好，预算 1000 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_012", "p_clothes_011", "p_clothes_013"], "category": "clothing"} | recommend_shopping_products | p_clothes_007 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_clothes_007'] |
| pc_part_b760_ddr5_motherboard | 推荐一块 B760 DDR5 主板 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5", "pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5"], "category": "pc_motherboard"} | recommend_shopping_products | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5', 'pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2', 'pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3'] |
| pc_part_750w_gold_psu | 推荐一个 750W 金牌全模组电源 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_psu_super_flower_leadex_g_750w"], "category": "pc_psu"} | recommend_shopping_products | pc_psu_pc_seed_psu_super_flower_leadex_g_750w,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_psu_pc_seed_psu_super_flower_leadex_g_750w', 'pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2', 'pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3'] |
| pc_part_2tb_nvme_ssd | 推荐一块 2TB NVMe SSD | {"tool": "recommend_shopping_products", "ids": ["pc_seed_ssd_samsung_990_pro_2tb", "pc_seed_ssd_wd_black_sn850x_2tb"], "category": "pc_storage"} | recommend_shopping_products | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_storage_pc_seed_ssd_wd_black_sn850x_2tb', 'pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2', 'pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3'] |
| pc_part_long_gpu_case | 推荐一个能装长显卡的机箱 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_case_fractal_design_north", "pc_seed_case_phanteks_p360a", "pc_seed_case_nzxt_h5_flow"], "category": "pc_case"} | recommend_shopping_products | pc_case_pc_seed_case_phanteks_p360a,pc_case_pc_seed_case_phanteks_p360a_v2,pc_case_pc_seed_case_phanteks_p360a_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_case_pc_seed_case_phanteks_p360a', 'pc_case_pc_seed_case_phanteks_p360a_v2', 'pc_case_pc_seed_case_phanteks_p360a_v3'] |
| pc_build_multiturn_adjust_t2 | 把显卡换强一点 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | recommend_shopping_products | pc_gpu_pc_seed_gpu_integrated_no_discrete_gpu_use_cpu_igpu,pc_gpu_pc_seed_gpu_asrock_radeon_rx_6500_xt_4gb,pc_gpu_pc_seed_gpu_asrock_radeon_rx_6500_xt_4gb_edition_1 |  | wrong_route | 期望路由 generate_pc_build_plan，实际 recommend_shopping_products |
| pc_build_multiturn_adjust_t3 | 预算降到 6000 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | recommend_shopping_products | p_beauty_007,p_digital_019,p_clothes_001,p_food_018 |  | wrong_route | 期望路由 generate_pc_build_plan，实际 recommend_shopping_products |
| pc_build_multiturn_adjust_t4 | 上一套和现在这套差别在哪里 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | compare_products |  |  | wrong_route | 期望路由 generate_pc_build_plan，实际 compare_products |

### full_no_query_expansion

| case_id | query | expected | actual route | recommended | retrieved | failure | reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ecom_basketball_shoes_cushion_1000 | 推荐一双篮球实战鞋，缓震好，预算 1000 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_012", "p_clothes_011", "p_clothes_013"], "category": "clothing"} | recommend_shopping_products | p_clothes_007 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_clothes_007'] |
| pc_part_b760_ddr5_motherboard | 推荐一块 B760 DDR5 主板 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5", "pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5"], "category": "pc_motherboard"} | recommend_shopping_products | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5', 'pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2', 'pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3'] |
| pc_part_750w_gold_psu | 推荐一个 750W 金牌全模组电源 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_psu_super_flower_leadex_g_750w"], "category": "pc_psu"} | recommend_shopping_products | pc_psu_pc_seed_psu_super_flower_leadex_g_750w,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_psu_pc_seed_psu_super_flower_leadex_g_750w', 'pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2', 'pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3'] |
| pc_part_2tb_nvme_ssd | 推荐一块 2TB NVMe SSD | {"tool": "recommend_shopping_products", "ids": ["pc_seed_ssd_samsung_990_pro_2tb", "pc_seed_ssd_wd_black_sn850x_2tb"], "category": "pc_storage"} | recommend_shopping_products | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_storage_pc_seed_ssd_wd_black_sn850x_2tb', 'pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2', 'pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3'] |
| pc_part_long_gpu_case | 推荐一个能装长显卡的机箱 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_case_fractal_design_north", "pc_seed_case_phanteks_p360a", "pc_seed_case_nzxt_h5_flow"], "category": "pc_case"} | recommend_shopping_products | pc_case_pc_seed_case_phanteks_p360a,pc_case_pc_seed_case_phanteks_p360a_v2,pc_case_pc_seed_case_phanteks_p360a_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_case_pc_seed_case_phanteks_p360a', 'pc_case_pc_seed_case_phanteks_p360a_v2', 'pc_case_pc_seed_case_phanteks_p360a_v3'] |
| pc_build_multiturn_adjust_t2 | 把显卡换强一点 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | recommend_shopping_products | pc_gpu_pc_seed_gpu_integrated_no_discrete_gpu_use_cpu_igpu,pc_gpu_pc_seed_gpu_asrock_radeon_rx_6500_xt_4gb,pc_gpu_pc_seed_gpu_asrock_radeon_rx_6500_xt_4gb_edition_1 |  | wrong_route | 期望路由 generate_pc_build_plan，实际 recommend_shopping_products |
| pc_build_multiturn_adjust_t3 | 预算降到 6000 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | recommend_shopping_products | p_beauty_007,p_digital_019,p_clothes_001,p_food_018 |  | wrong_route | 期望路由 generate_pc_build_plan，实际 recommend_shopping_products |
| pc_build_multiturn_adjust_t4 | 上一套和现在这套差别在哪里 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | compare_products |  |  | wrong_route | 期望路由 generate_pc_build_plan，实际 compare_products |

### parse_llm_only

| case_id | query | expected | actual route | recommended | retrieved | failure | reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ecom_basketball_shoes_cushion_1000 | 推荐一双篮球实战鞋，缓震好，预算 1000 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_012", "p_clothes_011", "p_clothes_013"], "category": "clothing"} | recommend_shopping_products | p_clothes_007 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_clothes_007'] |
| pc_part_b760_ddr5_motherboard | 推荐一块 B760 DDR5 主板 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5", "pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5"], "category": "pc_motherboard"} | recommend_shopping_products | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5', 'pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2', 'pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3'] |
| pc_part_750w_gold_psu | 推荐一个 750W 金牌全模组电源 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_psu_super_flower_leadex_g_750w"], "category": "pc_psu"} | recommend_shopping_products | pc_psu_pc_seed_psu_super_flower_leadex_g_750w,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_psu_pc_seed_psu_super_flower_leadex_g_750w', 'pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2', 'pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3'] |
| pc_part_2tb_nvme_ssd | 推荐一块 2TB NVMe SSD | {"tool": "recommend_shopping_products", "ids": ["pc_seed_ssd_samsung_990_pro_2tb", "pc_seed_ssd_wd_black_sn850x_2tb"], "category": "pc_storage"} | recommend_shopping_products | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_storage_pc_seed_ssd_wd_black_sn850x_2tb', 'pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2', 'pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3'] |
| pc_part_long_gpu_case | 推荐一个能装长显卡的机箱 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_case_fractal_design_north", "pc_seed_case_phanteks_p360a", "pc_seed_case_nzxt_h5_flow"], "category": "pc_case"} | recommend_shopping_products | pc_case_pc_seed_case_phanteks_p360a,pc_case_pc_seed_case_phanteks_p360a_v2,pc_case_pc_seed_case_phanteks_p360a_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_case_pc_seed_case_phanteks_p360a', 'pc_case_pc_seed_case_phanteks_p360a_v2', 'pc_case_pc_seed_case_phanteks_p360a_v3'] |
| pc_build_multiturn_adjust_t2 | 把显卡换强一点 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | recommend_shopping_products | pc_gpu_pc_seed_gpu_integrated_no_discrete_gpu_use_cpu_igpu,pc_gpu_pc_seed_gpu_asrock_radeon_rx_6500_xt_4gb,pc_gpu_pc_seed_gpu_asrock_radeon_rx_6500_xt_4gb_edition_1 |  | wrong_route | 期望路由 generate_pc_build_plan，实际 recommend_shopping_products |
| pc_build_multiturn_adjust_t3 | 预算降到 6000 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | recommend_shopping_products | p_beauty_007,p_digital_019,p_clothes_001,p_food_018 |  | wrong_route | 期望路由 generate_pc_build_plan，实际 recommend_shopping_products |
| pc_build_multiturn_adjust_t4 | 上一套和现在这套差别在哪里 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | compare_products |  |  | wrong_route | 期望路由 generate_pc_build_plan，实际 compare_products |

### rag_only

| case_id | query | expected | actual route | recommended | retrieved | failure | reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ecom_basketball_shoes_cushion_1000 | 推荐一双篮球实战鞋，缓震好，预算 1000 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_012", "p_clothes_011", "p_clothes_013"], "category": "clothing"} | recommend_shopping_products | p_clothes_007 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_clothes_007'] |
| pc_part_b760_ddr5_motherboard | 推荐一块 B760 DDR5 主板 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5", "pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5"], "category": "pc_motherboard"} | recommend_shopping_products | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5', 'pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2', 'pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3'] |
| pc_part_750w_gold_psu | 推荐一个 750W 金牌全模组电源 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_psu_super_flower_leadex_g_750w"], "category": "pc_psu"} | recommend_shopping_products | pc_psu_pc_seed_psu_super_flower_leadex_g_750w,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_psu_pc_seed_psu_super_flower_leadex_g_750w', 'pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2', 'pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3'] |
| pc_part_2tb_nvme_ssd | 推荐一块 2TB NVMe SSD | {"tool": "recommend_shopping_products", "ids": ["pc_seed_ssd_samsung_990_pro_2tb", "pc_seed_ssd_wd_black_sn850x_2tb"], "category": "pc_storage"} | recommend_shopping_products | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_storage_pc_seed_ssd_wd_black_sn850x_2tb', 'pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2', 'pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3'] |
| pc_part_long_gpu_case | 推荐一个能装长显卡的机箱 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_case_fractal_design_north", "pc_seed_case_phanteks_p360a", "pc_seed_case_nzxt_h5_flow"], "category": "pc_case"} | recommend_shopping_products | pc_case_pc_seed_case_phanteks_p360a,pc_case_pc_seed_case_phanteks_p360a_v2,pc_case_pc_seed_case_phanteks_p360a_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_case_pc_seed_case_phanteks_p360a', 'pc_case_pc_seed_case_phanteks_p360a_v2', 'pc_case_pc_seed_case_phanteks_p360a_v3'] |
| pc_build_multiturn_adjust_t2 | 把显卡换强一点 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | recommend_shopping_products | pc_gpu_pc_seed_gpu_integrated_no_discrete_gpu_use_cpu_igpu,pc_gpu_pc_seed_gpu_asrock_radeon_rx_6500_xt_4gb,pc_gpu_pc_seed_gpu_asrock_radeon_rx_6500_xt_4gb_edition_1 |  | wrong_route | 期望路由 generate_pc_build_plan，实际 recommend_shopping_products |
| pc_build_multiturn_adjust_t3 | 预算降到 6000 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | recommend_shopping_products | p_beauty_007,p_digital_019,p_clothes_001,p_food_018 |  | wrong_route | 期望路由 generate_pc_build_plan，实际 recommend_shopping_products |
| pc_build_multiturn_adjust_t4 | 上一套和现在这套差别在哪里 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | compare_products |  |  | wrong_route | 期望路由 generate_pc_build_plan，实际 compare_products |

### router_llm_only

| case_id | query | expected | actual route | recommended | retrieved | failure | reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ecom_basketball_shoes_cushion_1000 | 推荐一双篮球实战鞋，缓震好，预算 1000 | {"tool": "recommend_shopping_products", "ids": ["p_clothes_012", "p_clothes_011", "p_clothes_013"], "category": "clothing"} | recommend_shopping_products | p_clothes_007 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['p_clothes_007'] |
| pc_part_b760_ddr5_motherboard | 推荐一块 B760 DDR5 主板 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_motherboard_gigabyte_b760m_aorus_elite_ax_ddr5", "pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5"], "category": "pc_motherboard"} | recommend_shopping_products | pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2,pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5', 'pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v2', 'pc_motherboard_pc_seed_motherboard_msi_pro_b760m_a_wifi_ddr5_v3'] |
| pc_part_750w_gold_psu | 推荐一个 750W 金牌全模组电源 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_psu_super_flower_leadex_g_750w"], "category": "pc_psu"} | recommend_shopping_products | pc_psu_pc_seed_psu_super_flower_leadex_g_750w,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2,pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_psu_pc_seed_psu_super_flower_leadex_g_750w', 'pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v2', 'pc_psu_pc_seed_psu_super_flower_leadex_g_750w_v3'] |
| pc_part_2tb_nvme_ssd | 推荐一块 2TB NVMe SSD | {"tool": "recommend_shopping_products", "ids": ["pc_seed_ssd_samsung_990_pro_2tb", "pc_seed_ssd_wd_black_sn850x_2tb"], "category": "pc_storage"} | recommend_shopping_products | pc_storage_pc_seed_ssd_wd_black_sn850x_2tb,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2,pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_storage_pc_seed_ssd_wd_black_sn850x_2tb', 'pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev2', 'pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev3'] |
| pc_part_long_gpu_case | 推荐一个能装长显卡的机箱 | {"tool": "recommend_shopping_products", "ids": ["pc_seed_case_fractal_design_north", "pc_seed_case_phanteks_p360a", "pc_seed_case_nzxt_h5_flow"], "category": "pc_case"} | recommend_shopping_products | pc_case_pc_seed_case_phanteks_p360a,pc_case_pc_seed_case_phanteks_p360a_v2,pc_case_pc_seed_case_phanteks_p360a_v3 |  | final_recommendation_miss | Top 结果未命中期望商品，推荐=['pc_case_pc_seed_case_phanteks_p360a', 'pc_case_pc_seed_case_phanteks_p360a_v2', 'pc_case_pc_seed_case_phanteks_p360a_v3'] |
| pc_build_multiturn_adjust_t2 | 把显卡换强一点 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | recommend_shopping_products | pc_gpu_pc_seed_gpu_integrated_no_discrete_gpu_use_cpu_igpu,pc_gpu_pc_seed_gpu_asrock_radeon_rx_6500_xt_4gb,pc_gpu_pc_seed_gpu_asrock_radeon_rx_6500_xt_4gb_edition_1 |  | wrong_route | 期望路由 generate_pc_build_plan，实际 recommend_shopping_products |
| pc_build_multiturn_adjust_t3 | 预算降到 6000 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | recommend_shopping_products | p_beauty_007,p_digital_019,p_clothes_001,p_food_018 |  | wrong_route | 期望路由 generate_pc_build_plan，实际 recommend_shopping_products |
| pc_build_multiturn_adjust_t4 | 上一套和现在这套差别在哪里 | {"tool": "generate_pc_build_plan", "ids": [], "category": null} | compare_products |  |  | wrong_route | 期望路由 generate_pc_build_plan，实际 compare_products |

### timeout_fallback

| case_id | query | expected | actual route | recommended | retrieved | failure | reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ambiguous_gift_girlfriend | 送女朋友礼物 | {"tool": "recommend_shopping_products", "ids": [], "category": "beauty"} | recommend_shopping_products |  |  | script_or_chain_error | InvalidGoalError: 未识别到有效购物场景，请描述想买什么、预算、用途或偏好。 |

## 结论建议

- 默认部署建议：优先使用 balanced_demo 作为 demo / 准生产默认模式，前提是 LLM 与 Milvus 环境稳定。
- CI 与降级建议：fast_baseline 适合作为稳定回归和外部依赖不可用时的兜底模式；不要把 fast 描述为使用 Milvus。
- full 建议：full_llm_all 更适合离线评估或高质量请求，不建议在没有延迟、成本和失败率监控前默认开启。
- query expansion 建议：默认关闭；只有当 A2/C2 对比证明召回收益明显且 query 漂移可控时再打开。
- guidance 建议：默认关闭或仅在商品卡片稳定后开启；开启后要持续检查 reason 是否被商品证据支撑。
- fast 仍有可用结果，说明规则 + 本地 catalog scoring 可以承担基础兜底。
- 后续修复优先级：先看 wrong_route 和 negative_guard_failed，再看 final_recommendation_miss；前两类更可能影响演示稳定性。
