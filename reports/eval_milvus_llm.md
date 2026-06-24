# Retrieval Evaluation

## Main Summary

_Default main summary includes in_catalog_exact and in_catalog_attribute_gap cases. Ambiguous, catalog gap, and negative/impossible cases are reported separately._

- Total cases: 24
- Success cases: 24
- Metric-eligible cases: 24
- Empty results: 1 (0.042)
- Average latency: 4503.13 ms
- precision@1: 0.708 / precision@3: 0.569 / precision@5: 0.342 / precision@10: 0.171
- strict_recall@1: 0.238 / strict_recall@3: 0.477 / strict_recall@5: 0.477 / strict_recall@10: 0.477
- relaxed_recall@1: 0.193 / relaxed_recall@3: 0.438 / relaxed_recall@5: 0.438 / relaxed_recall@10: 0.438
- hit@1: 0.708 / hit@3: 0.917 / hit@5: 0.917 / hit@10: 0.917
- MRR: 0.799
- constraint_violation_rate: 0.042
- category_accuracy_top1: 0.958
- category_accuracy@1: 0.958 / category_accuracy@3: 0.958 / category_accuracy@5: 0.958 / category_accuracy@10: 0.958

## All Cases Summary

_Mixed summary across all cases; retrieval metrics exclude negative cases but include PC cases._

- Total cases: 30
- Success cases: 30
- Metric-eligible cases: 28
- Empty results: 3 (0.100)
- Average latency: 4330.99 ms
- precision@1: 0.679 / precision@3: 0.560 / precision@5: 0.336 / precision@10: 0.168
- strict_recall@1: 0.221 / strict_recall@3: 0.472 / strict_recall@5: 0.472 / strict_recall@10: 0.472
- relaxed_recall@1: 0.178 / relaxed_recall@3: 0.419 / relaxed_recall@5: 0.419 / relaxed_recall@10: 0.419
- hit@1: 0.679 / hit@3: 0.929 / hit@5: 0.929 / hit@10: 0.929
- MRR: 0.792
- constraint_violation_rate: 0.033
- category_accuracy_top1: 0.933
- category_accuracy@1: 0.933 / category_accuracy@3: 0.933 / category_accuracy@5: 0.933 / category_accuracy@10: 0.933

## Ecommerce Summary

- ecommerce_cases: 24
- hit@5: 0.917
- relaxed_recall@5: 0.438
- constraint_violation_rate: 0.042
- category_accuracy@5: 0.958
- avg_latency_ms: 4503.13

## PC Summary

- pc_cases: 4
- hit@5: 1.000
- relaxed_recall@5: 0.303
- constraint_violation_rate: 0.000
- category_accuracy@5: 1.000
- avg_latency_ms: 3935.48

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
- avg_latency_ms: 3056.34

## Worst Cases

| case | group | scope | query | expected ids | expected titles | returned ids | returned titles | top1 title | expected category | returned category | miss_stage_guess | miss reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| digital_phone_budget_002 | ecommerce | in_catalog_exact | 3500元以内的手机，适合学生党日常用 | p_digital_016 | OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256GB |  |  |  | digital |  | empty_result | empty result; strict recall@5 is 0; category mismatch |
| beauty_gift_009 | ecommerce | in_catalog_exact | 高端大牌护肤精华，预算800以内，送女朋友 | p_beauty_001, p_beauty_002, p_beauty_004, p_beauty_005 | 雅诗兰黛特润修护肌活精华露淡纹紧致保湿夜间修护抗初老精华30ml, 兰蔻小黑瓶全新精华肌底液修护维稳细腻毛孔提亮肤色30ml, 资生堂新红妍肌活精华露红腰子修护维稳强韧肌底精华50ml, 科颜氏新集焕白均衡亮肤淡斑精华液提亮肤色淡化斑点精华30ml | p_beauty_018, p_beauty_009, p_beauty_024 | The Ordinary烟酰胺10%锌1%精华液平衡油脂淡化毛孔提亮肤色30ml, 珀莱雅弹润透亮青春精华液双抗精华提亮肤色淡纹保湿精华50ml, 珀莱雅赋能鲜颜淡纹紧致精华液抗皱紧致提升弹润淡化细纹30ml | The Ordinary烟酰胺10%锌1%精华液平衡油脂淡化毛孔提亮肤色30ml | beauty | beauty, beauty, beauty | recall_or_candidate_generation_error | strict recall@5 is 0 |
| clothes_under_100_017 | ecommerce | in_catalog_exact | 100元以内的基础T恤，通勤穿 | p_clothes_001, p_clothes_020 | 优衣库 U AIRism 棉质宽松圆领短袖T恤 男装 基础纯色上衣, 迪卡侬 KALENJI 男子跑步速干T恤轻薄透气基础训练短袖上衣 | p_clothes_020, p_clothes_001, p_clothes_002 | 迪卡侬 KALENJI 男子跑步速干T恤轻薄透气基础训练短袖上衣, 优衣库 U AIRism 棉质宽松圆领短袖T恤 男装 基础纯色上衣, 优衣库 DRY-EX 超快干圆领短袖T恤 男装 运动训练上衣 | 迪卡侬 KALENJI 男子跑步速干T恤轻薄透气基础训练短袖上衣 | clothing | clothing, clothing, clothing | constraint_filter_error | constraint violation |
| vague_girlfriend_023 | ecommerce | in_catalog_exact | 送女朋友，不知道买什么，想要精致一点 | p_beauty_001, p_beauty_002, p_beauty_009, p_beauty_015, p_beauty_024 | 雅诗兰黛特润修护肌活精华露淡纹紧致保湿夜间修护抗初老精华30ml, 兰蔻小黑瓶全新精华肌底液修护维稳细腻毛孔提亮肤色30ml, 珀莱雅弹润透亮青春精华液双抗精华提亮肤色淡纹保湿精华50ml, 完美日记仿生膜精华唇釉丝绒哑光滋润显色持妆唇部彩妆3g, 珀莱雅赋能鲜颜淡纹紧致精华液抗皱紧致提升弹润淡化细纹30ml | p_beauty_022, p_digital_018, p_clothes_020, p_food_013 | 薇诺娜极润保湿面膜密集补水舒缓修护肌肤屏障涂抹式面膜75g, Apple AirPods Pro 3 主动降噪真无线蓝牙耳机 心率监测版, 迪卡侬 KALENJI 男子跑步速干T恤轻薄透气基础训练短袖上衣, 海天 金标生抽1.9L 瓶装酱油 家用厨房调味料酱香鲜味佐餐蘸料 | 薇诺娜极润保湿面膜密集补水舒缓修护肌肤屏障涂抹式面膜75g | beauty | beauty, digital, clothing, food | recall_or_candidate_generation_error | strict recall@5 is 0 |
| vague_student_024 | ecommerce | in_catalog_exact | 学生党预算有限，日常学习和通勤有什么推荐 | p_digital_011, p_digital_023, p_clothes_001, p_food_002, p_food_021 | 小米平板 8 Pro 12.1英寸高刷大屏影音娱乐学习办公平板电脑, 联想 ThinkBook 14+ 2026 全面屏高性能轻薄本商务学习笔记本电脑, 优衣库 U AIRism 棉质宽松圆领短袖T恤 男装 基础纯色上衣, 雀巢咖啡 1+2原味 三合一速溶咖啡粉100条装即冲奶香咖啡饮品, 康师傅 红烧牛肉面 方便面袋装 114g×5 袋 五连包整袋 | p_clothes_019, p_clothes_020, p_clothes_005 | Nike Heritage86 Futura Logo 经典刺绣棒球帽 可调节帽围, 迪卡侬 KALENJI 男子跑步速干T恤轻薄透气基础训练短袖上衣, 李宁 运动生活系列 男子连帽套头卫衣 基础Logo印花上衣 | Nike Heritage86 Futura Logo 经典刺绣棒球帽 可调节帽围 | digital, clothing, food | clothing, clothing, clothing | recall_or_candidate_generation_error | strict recall@5 is 0 |
| negative_too_low_phone_025 | negative | negative_or_impossible | 500元以内买一台拍照旗舰手机 |  |  |  |  |  | digital |  | empty_result | empty result; category mismatch |
| negative_no_product_026 | negative | negative_or_impossible | 我想买宠物猫粮和自动喂食器 |  |  |  |  |  |  |  | empty_result | empty result |
