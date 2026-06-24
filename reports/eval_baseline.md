# Retrieval Evaluation

## Main Summary

_Default main summary includes in_catalog_exact and in_catalog_attribute_gap cases. Ambiguous, catalog gap, and negative/impossible cases are reported separately._

- Total cases: 24
- Success cases: 24
- Metric-eligible cases: 24
- Empty results: 0 (0.000)
- Average latency: 706.86 ms
- precision@1: 0.667 / precision@3: 0.486 / precision@5: 0.292 / precision@10: 0.146
- strict_recall@1: 0.235 / strict_recall@3: 0.440 / strict_recall@5: 0.440 / strict_recall@10: 0.440
- relaxed_recall@1: 0.218 / relaxed_recall@3: 0.404 / relaxed_recall@5: 0.404 / relaxed_recall@10: 0.404
- hit@1: 0.667 / hit@3: 0.958 / hit@5: 0.958 / hit@10: 0.958
- MRR: 0.812
- constraint_violation_rate: 0.083
- category_accuracy_top1: 0.917
- category_accuracy@1: 0.917 / category_accuracy@3: 1.000 / category_accuracy@5: 1.000 / category_accuracy@10: 1.000

## All Cases Summary

_Mixed summary across all cases; retrieval metrics exclude negative cases but include PC cases._

- Total cases: 30
- Success cases: 30
- Metric-eligible cases: 28
- Empty results: 2 (0.067)
- Average latency: 594.90 ms
- precision@1: 0.643 / precision@3: 0.464 / precision@5: 0.279 / precision@10: 0.139
- strict_recall@1: 0.219 / strict_recall@3: 0.404 / strict_recall@5: 0.404 / strict_recall@10: 0.404
- relaxed_recall@1: 0.199 / relaxed_recall@3: 0.369 / relaxed_recall@5: 0.369 / relaxed_recall@10: 0.369
- hit@1: 0.643 / hit@3: 0.893 / hit@5: 0.893 / hit@10: 0.893
- MRR: 0.768
- constraint_violation_rate: 0.067
- category_accuracy_top1: 0.900
- category_accuracy@1: 0.900 / category_accuracy@3: 0.967 / category_accuracy@5: 0.967 / category_accuracy@10: 0.967

## Ecommerce Summary

- ecommerce_cases: 24
- hit@5: 0.958
- relaxed_recall@5: 0.404
- constraint_violation_rate: 0.083
- category_accuracy@5: 1.000
- avg_latency_ms: 706.86

## PC Summary

- pc_cases: 4
- hit@5: 0.500
- relaxed_recall@5: 0.157
- constraint_violation_rate: 0.000
- category_accuracy@5: 1.000
- avg_latency_ms: 220.11

## Ambiguous Summary

- ambiguous_cases: 0
- hit@5: 0.000
- relaxed_recall@5: 0.000
- constraint_violation_rate: 0.000
- category_accuracy@5: 0.000
- avg_latency_ms: 0.00

## Catalog Gap Summary

- catalog_gap_cases: 0
- catalog_gap_empty_result_rate: 0.000
- catalog_gap_no_recommendation_rate: 0.000
- catalog_gap_violation_rate: 0.000
- catalog_gap_returned_any_rate: 0.000
- constraint_violation_rate: 0.000
- category_accuracy@5: 0.000
- avg_latency_ms: 0.00

## Negative Summary

- negative_cases: 2
- negative_empty_result_rate: 1.000
- negative_no_recommendation_rate: 1.000
- negative_violation_rate: 0.000
- negative_returned_any_rate: 0.000
- constraint_violation_rate: 0.000
- category_accuracy@5: 0.500
- avg_latency_ms: 0.88

## Worst Cases

| case | group | scope | query | expected ids | expected titles | returned ids | returned titles | top1 title | expected category | returned category | miss_stage_guess | miss reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| digital_phone_budget_002 | ecommerce | in_catalog_exact | 3500元以内的手机，适合学生党日常用 | p_digital_016 | OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256GB | p_digital_016, p_digital_005 | OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256GB, 华为HUAWEI MatePad Pro Max 12.6英寸高刷屏多任务办公平板电脑 | OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256GB | digital | digital, digital | constraint_filter_error | constraint violation |
| beauty_gift_009 | ecommerce | in_catalog_exact | 高端大牌护肤精华，预算800以内，送女朋友 | p_beauty_001, p_beauty_002, p_beauty_004, p_beauty_005 | 雅诗兰黛特润修护肌活精华露淡纹紧致保湿夜间修护抗初老精华30ml, 兰蔻小黑瓶全新精华肌底液修护维稳细腻毛孔提亮肤色30ml, 资生堂新红妍肌活精华露红腰子修护维稳强韧肌底精华50ml, 科颜氏新集焕白均衡亮肤淡斑精华液提亮肤色淡化斑点精华30ml | p_beauty_018, p_beauty_009, p_beauty_025 | The Ordinary烟酰胺10%锌1%精华液平衡油脂淡化毛孔提亮肤色30ml, 珀莱雅弹润透亮青春精华液双抗精华提亮肤色淡纹保湿精华50ml, 花西子螺黛生花眉笔细节点描顺滑显色防晕染持久双头眉笔0.08g | The Ordinary烟酰胺10%锌1%精华液平衡油脂淡化毛孔提亮肤色30ml | beauty | beauty, beauty, beauty | recall_or_candidate_generation_error | strict recall@5 is 0 |
| clothes_under_100_017 | ecommerce | in_catalog_exact | 100元以内的基础T恤，通勤穿 | p_clothes_001, p_clothes_020 | 优衣库 U AIRism 棉质宽松圆领短袖T恤 男装 基础纯色上衣, 迪卡侬 KALENJI 男子跑步速干T恤轻薄透气基础训练短袖上衣 | p_clothes_020, p_clothes_002 | 迪卡侬 KALENJI 男子跑步速干T恤轻薄透气基础训练短袖上衣, 优衣库 DRY-EX 超快干圆领短袖T恤 男装 运动训练上衣 | 迪卡侬 KALENJI 男子跑步速干T恤轻薄透气基础训练短袖上衣 | clothing | clothing, clothing | constraint_filter_error | constraint violation |
| vague_girlfriend_023 | ecommerce | in_catalog_exact | 送女朋友，不知道买什么，想要精致一点 | p_beauty_001, p_beauty_002, p_beauty_009, p_beauty_015, p_beauty_024 | 雅诗兰黛特润修护肌活精华露淡纹紧致保湿夜间修护抗初老精华30ml, 兰蔻小黑瓶全新精华肌底液修护维稳细腻毛孔提亮肤色30ml, 珀莱雅弹润透亮青春精华液双抗精华提亮肤色淡纹保湿精华50ml, 完美日记仿生膜精华唇釉丝绒哑光滋润显色持妆唇部彩妆3g, 珀莱雅赋能鲜颜淡纹紧致精华液抗皱紧致提升弹润淡化细纹30ml | p_beauty_007, p_digital_016, p_clothes_020, p_food_013 | 薇诺娜舒敏保湿特护霜敏感肌修护屏障舒缓干痒保湿面霜50g, OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256GB, 迪卡侬 KALENJI 男子跑步速干T恤轻薄透气基础训练短袖上衣, 海天 金标生抽1.9L 瓶装酱油 家用厨房调味料酱香鲜味佐餐蘸料 | 薇诺娜舒敏保湿特护霜敏感肌修护屏障舒缓干痒保湿面霜50g | beauty | beauty, digital, clothing, food | recall_or_candidate_generation_error | strict recall@5 is 0 |
| negative_too_low_phone_025 | negative | negative_or_impossible | 500元以内买一台拍照旗舰手机 |  |  |  |  |  | digital |  | empty_result | empty result; category mismatch |
| negative_no_product_026 | negative | negative_or_impossible | 我想买宠物猫粮和自动喂食器 |  |  |  |  |  |  |  | empty_result | empty result |
| pc_storage_029 | pc | in_catalog_exact | 电脑升级2TB高速固态硬盘，适合游戏和剪辑 | pc_storage_pc_seed_ssd_samsung_990_pro_2tb, pc_storage_pc_seed_ssd_wd_black_sn850x_2tb | Samsung 990 PRO 2TB NVMe SSD, WD BLACK SN850X 2TB NVMe SSD | pc_storage_pc_seed_ssd_zhitai_tiplus7100_1tb_rev4, pc_storage_pc_seed_ssd_zhitai_tiplus7100_1tb_rev3, pc_storage_pc_seed_ssd_wd_black_sn850x_2tb_rev4 | ZHITAI TiPlus7100 1TB Rev4 NVMe SSD, ZHITAI TiPlus7100 1TB Rev3 NVMe SSD, WD BLACK SN850X 2TB Rev4 NVMe SSD | ZHITAI TiPlus7100 1TB Rev4 NVMe SSD | pc_storage | pc_storage, pc_storage, pc_storage | recall_or_candidate_generation_error | strict recall@5 is 0 |
| pc_memory_030 | pc | in_catalog_exact | 32GB内存，DDR5，适合新平台装机 | pc_memory_pc_seed_memory_kingston_fury_beast_ddr5_32gb_6000, pc_memory_pc_seed_memory_g_skill_trident_z5_rgb_ddr5_32gb_6400 | Kingston FURY Beast DDR5 32GB 6000 内存套装, G.SKILL Trident Z5 RGB DDR5 32GB 6400 内存套装 | pc_memory_pc_seed_memory_crucial_ddr5_16gb_5600_kit_2, pc_memory_pc_seed_memory_crucial_ddr5_16gb_5600_kit_3, pc_memory_pc_seed_memory_crucial_ddr5_16gb_5600_kit_4 | Crucial DDR5 16GB 5600 Kit 2 内存套装, Crucial DDR5 16GB 5600 Kit 3 内存套装, Crucial DDR5 16GB 5600 Kit 4 内存套装 | Crucial DDR5 16GB 5600 Kit 2 内存套装 | pc_memory | pc_memory, pc_memory, pc_memory | recall_or_candidate_generation_error | strict recall@5 is 0 |
