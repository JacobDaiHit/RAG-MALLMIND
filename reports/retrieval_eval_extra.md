# Retrieval Evaluation

## Main Summary

_Default main summary includes in_catalog_exact and in_catalog_attribute_gap cases. Ambiguous, catalog gap, and negative/impossible cases are reported separately._

- Total cases: 34
- Success cases: 34
- Metric-eligible cases: 34
- Empty results: 0 (0.000)
- Average latency: 14.09 ms
- precision@1: 0.882 / precision@3: 0.716 / precision@5: 0.429 / precision@10: 0.215
- strict_recall@1: 0.436 / strict_recall@3: 0.799 / strict_recall@5: 0.799 / strict_recall@10: 0.799
- relaxed_recall@1: 0.293 / relaxed_recall@3: 0.697 / relaxed_recall@5: 0.697 / relaxed_recall@10: 0.697
- hit@1: 0.882 / hit@3: 0.971 / hit@5: 0.971 / hit@10: 0.971
- MRR: 0.922
- constraint_violation_rate: 0.029
- category_accuracy_top1: 1.000
- category_accuracy@1: 1.000 / category_accuracy@3: 1.000 / category_accuracy@5: 1.000 / category_accuracy@10: 1.000

## All Cases Summary

_Mixed summary across all cases; retrieval metrics exclude negative cases but include PC cases._

- Total cases: 50
- Success cases: 50
- Metric-eligible cases: 40
- Empty results: 10 (0.200)
- Average latency: 15.76 ms
- precision@1: 0.850 / precision@3: 0.700 / precision@5: 0.420 / precision@10: 0.210
- strict_recall@1: 0.396 / strict_recall@3: 0.748 / strict_recall@5: 0.748 / strict_recall@10: 0.748
- relaxed_recall@1: 0.284 / relaxed_recall@3: 0.672 / relaxed_recall@5: 0.672 / relaxed_recall@10: 0.672
- hit@1: 0.850 / hit@3: 0.975 / hit@5: 0.975 / hit@10: 0.975
- MRR: 0.908
- constraint_violation_rate: 0.020
- category_accuracy_top1: 0.940
- category_accuracy@1: 0.940 / category_accuracy@3: 0.940 / category_accuracy@5: 0.940 / category_accuracy@10: 0.940

## Ecommerce Summary

- ecommerce_cases: 28
- hit@5: 0.963
- relaxed_recall@5: 0.668
- constraint_violation_rate: 0.036
- category_accuracy@5: 0.964
- avg_latency_ms: 16.23

## PC Summary

- pc_cases: 13
- hit@5: 1.000
- relaxed_recall@5: 0.679
- constraint_violation_rate: 0.000
- category_accuracy@5: 1.000
- avg_latency_ms: 25.33

## Ambiguous Summary

- ambiguous_cases: 6
- hit@5: 1.000
- relaxed_recall@5: 0.528
- constraint_violation_rate: 0.000
- category_accuracy@5: 1.000
- avg_latency_ms: 47.03

## Catalog Gap Summary

- catalog_gap_cases: 1
- catalog_gap_empty_result_rate: 1.000
- catalog_gap_no_recommendation_rate: 1.000
- catalog_gap_violation_rate: 0.000
- catalog_gap_returned_any_rate: 0.000
- constraint_violation_rate: 0.000
- category_accuracy@5: 0.000
- avg_latency_ms: 22.44

## Negative Summary

- negative_cases: 9
- negative_empty_result_rate: 1.000
- negative_no_recommendation_rate: 1.000
- negative_violation_rate: 0.000
- negative_returned_any_rate: 0.000
- constraint_violation_rate: 0.000
- category_accuracy@5: 0.778
- avg_latency_ms: 0.45

## Worst Cases

| case | group | scope | query | expected ids | expected titles | returned ids | returned titles | top1 title | expected category | returned category | miss_stage_guess | miss reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| extra_phone_android_photo_001 | ecommerce | in_catalog_exact | 三千多拍照好的安卓手机 | p_digital_016 | OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256GB | p_digital_016, p_digital_008, p_digital_015 | OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256GB, 小米 17 Ultra 2K高刷屏潜望长焦澎湃芯片影像旗舰5G手机12+256GB, OPPO Find X9 Ultra 超大底影像旗舰2K高刷屏长续航5G智能手机 | OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256GB | digital | digital, digital, digital | constraint_filter_error | constraint violation |
| extra_clothes_outdoor_jacket_019 | ecommerce | catalog_gap | 推荐户外防风外套 |  |  |  |  |  | clothing |  | empty_result | empty result; category mismatch |
| extra_cross_tablet_coffee_023 | ecommerce | in_catalog_exact | 学习平板和办公室咖啡一起推荐 | p_digital_011, p_digital_019, p_digital_025, p_food_001, p_food_022, p_food_023 | 小米平板 8 Pro 12.1英寸高刷大屏影音娱乐学习办公平板电脑, vivo Pad 6 Pro 12.1英寸高刷全面屏学习娱乐多任务办公平板电脑, Apple iPad Air 11英寸 2026款 M4 芯片 128GB Wi‑Fi 轻薄学习娱乐平板电脑, 三顿半 数字星球系列 超即溶精品咖啡1-6号 18颗装精品速溶咖啡, 三顿半 冷萃超即溶 黑咖啡 6颗装 冷泡即溶精品速溶咖啡 | p_digital_021, p_food_002 | 华为 MatePad Pro 13.2英寸 柔性OLED高刷屏轻办公创作旗舰平板电脑, 雀巢咖啡 1+2原味 三合一速溶咖啡粉100条装即冲奶香咖啡饮品 | 华为 MatePad Pro 13.2英寸 柔性OLED高刷屏轻办公创作旗舰平板电脑 | digital, food | digital, food | recall_or_candidate_generation_error | strict recall@5 is 0 |
| extra_pc_build_game_025 | pc | in_catalog_ambiguous | 6000元左右游戏主机 | pc_gpu_pc_seed_gpu_asus_dual_geforce_rtx_4060_o8g, pc_gpu_pc_seed_gpu_nvidia_geforce_rtx_4060_ti_8g, pc_cpu_pc_seed_cpu_intel_core_i5_13400f, pc_cpu_pc_seed_cpu_amd_ryzen_5_7500f | ASUS Dual GeForce RTX 4060 O8G 显卡, NVIDIA GeForce RTX 4060 Ti 8G 显卡, Intel Core i5-13400F 处理器, AMD Ryzen 5 7500F 处理器 | pc_cpu_pc_seed_cpu_amd_ryzen_5_5600, pc_gpu_pc_seed_gpu_sapphire_radeon_rx_7600_8g, pc_motherboard_pc_seed_motherboard_asus_prime_a520m_k, pc_memory_pc_seed_memory_kingbank_silver_ddr4_16gb_3200, pc_storage_pc_seed_ssd_kioxia_exceria_g2_rc2 | AMD Ryzen 5 5600 处理器, Sapphire Radeon RX 7600 8G 显卡, ASUS PRIME A520M-K 主板, Kingbank Silver DDR4 16GB 3200 内存套装, Kioxia EXCERIA G2 RC20 500GB NVMe SSD | AMD Ryzen 5 5600 处理器 | pc_gpu, pc_cpu | pc_cpu, pc_gpu, pc_motherboard, pc_memory, pc_storage | recall_or_candidate_generation_error | strict recall@5 is 0 |
| extra_negative_cat_food_035 | negative | negative_or_impossible | 想买猫粮 |  |  |  |  |  |  |  | empty_result | empty result |
| extra_negative_fridge_036 | negative | negative_or_impossible | 冰箱推荐 |  |  |  |  |  |  |  | empty_result | empty result |
| extra_negative_medicine_037 | negative | negative_or_impossible | 处方药怎么买 |  |  |  |  |  |  |  | empty_result | empty result |
| extra_negative_4090_budget_038 | negative | negative_or_impossible | 500元买 RTX 4090 |  |  |  |  |  | pc_gpu |  | empty_result | empty result; category mismatch |
| extra_negative_phone_budget_039 | negative | negative_or_impossible | 100元旗舰手机 |  |  |  |  |  | digital |  | empty_result | empty result; category mismatch |
| extra_negative_ev_040 | negative | negative_or_impossible | 买一辆电动车 |  |  |  |  |  |  |  | empty_result | empty result |
| extra_negative_washer_048 | negative | negative_or_impossible | 洗衣机推荐 |  |  |  |  |  |  |  | empty_result | empty result |
| extra_negative_cold_medicine_049 | negative | negative_or_impossible | 感冒药怎么买 |  |  |  |  |  |  |  | empty_result | empty result |
| extra_negative_motorcycle_050 | negative | negative_or_impossible | 摩托车通勤买哪款 |  |  |  |  |  |  |  | empty_result | empty result |
