# Retrieval Evaluation

## Main Summary

_Default main summary includes in_catalog_exact and in_catalog_attribute_gap cases. Ambiguous, catalog gap, and negative/impossible cases are reported separately._

- Total cases: 24
- Success cases: 24
- Metric-eligible cases: 24
- Empty results: 0 (0.000)
- Average latency: 10.70 ms
- precision@1: 0.833 / precision@3: 0.667 / precision@5: 0.400 / precision@10: 0.200
- strict_recall@1: 0.274 / strict_recall@3: 0.568 / strict_recall@5: 0.568 / strict_recall@10: 0.568
- relaxed_recall@1: 0.256 / relaxed_recall@3: 0.550 / relaxed_recall@5: 0.550 / relaxed_recall@10: 0.550
- hit@1: 0.833 / hit@3: 1.000 / hit@5: 1.000 / hit@10: 1.000
- MRR: 0.917
- constraint_violation_rate: 0.000
- category_accuracy_top1: 1.000
- category_accuracy@1: 1.000 / category_accuracy@3: 1.000 / category_accuracy@5: 1.000 / category_accuracy@10: 1.000

## All Cases Summary

_Mixed summary across all cases; retrieval metrics exclude negative cases but include PC cases._

- Total cases: 30
- Success cases: 30
- Metric-eligible cases: 28
- Empty results: 2 (0.067)
- Average latency: 9.56 ms
- precision@1: 0.857 / precision@3: 0.655 / precision@5: 0.393 / precision@10: 0.196
- strict_recall@1: 0.287 / strict_recall@3: 0.561 / strict_recall@5: 0.561 / strict_recall@10: 0.561
- relaxed_recall@1: 0.253 / relaxed_recall@3: 0.522 / relaxed_recall@5: 0.522 / relaxed_recall@10: 0.522
- hit@1: 0.857 / hit@3: 1.000 / hit@5: 1.000 / hit@10: 1.000
- MRR: 0.929
- constraint_violation_rate: 0.000
- category_accuracy_top1: 0.967
- category_accuracy@1: 0.967 / category_accuracy@3: 0.967 / category_accuracy@5: 0.967 / category_accuracy@10: 0.967

## Ecommerce Summary

- ecommerce_cases: 24
- hit@5: 1.000
- relaxed_recall@5: 0.550
- constraint_violation_rate: 0.000
- category_accuracy@5: 1.000
- avg_latency_ms: 10.70

## PC Summary

- pc_cases: 4
- hit@5: 1.000
- relaxed_recall@5: 0.353
- constraint_violation_rate: 0.000
- category_accuracy@5: 1.000
- avg_latency_ms: 7.34

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
- avg_latency_ms: 0.30

## Worst Cases

| case | group | scope | query | expected ids | expected titles | returned ids | returned titles | top1 title | expected category | returned category | miss_stage_guess | miss reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| beauty_gift_009 | ecommerce | in_catalog_exact | 高端大牌护肤精华，预算800以内，送女朋友 | p_beauty_001, p_beauty_002, p_beauty_004, p_beauty_005 | 雅诗兰黛特润修护肌活精华露淡纹紧致保湿夜间修护抗初老精华30ml, 兰蔻小黑瓶全新精华肌底液修护维稳细腻毛孔提亮肤色30ml, 资生堂新红妍肌活精华露红腰子修护维稳强韧肌底精华50ml, 科颜氏新集焕白均衡亮肤淡斑精华液提亮肤色淡化斑点精华30ml | p_beauty_018, p_beauty_009, p_beauty_024 | The Ordinary烟酰胺10%锌1%精华液平衡油脂淡化毛孔提亮肤色30ml, 珀莱雅弹润透亮青春精华液双抗精华提亮肤色淡纹保湿精华50ml, 珀莱雅赋能鲜颜淡纹紧致精华液抗皱紧致提升弹润淡化细纹30ml | The Ordinary烟酰胺10%锌1%精华液平衡油脂淡化毛孔提亮肤色30ml | beauty | beauty, beauty, beauty | recall_or_candidate_generation_error | strict recall@5 is 0 |
| vague_girlfriend_023 | ecommerce | in_catalog_exact | 送女朋友，不知道买什么，想要精致一点 | p_beauty_001, p_beauty_002, p_beauty_009, p_beauty_015, p_beauty_024 | 雅诗兰黛特润修护肌活精华露淡纹紧致保湿夜间修护抗初老精华30ml, 兰蔻小黑瓶全新精华肌底液修护维稳细腻毛孔提亮肤色30ml, 珀莱雅弹润透亮青春精华液双抗精华提亮肤色淡纹保湿精华50ml, 完美日记仿生膜精华唇釉丝绒哑光滋润显色持妆唇部彩妆3g, 珀莱雅赋能鲜颜淡纹紧致精华液抗皱紧致提升弹润淡化细纹30ml | p_beauty_025, p_digital_019, p_clothes_001, p_food_018 | 花西子螺黛生花眉笔细节点描顺滑显色防晕染持久双头眉笔0.08g, vivo Pad 6 Pro 12.1英寸高刷全面屏学习娱乐多任务办公平板电脑, 优衣库 U AIRism 棉质宽松圆领短袖T恤 男装 基础纯色上衣, 李锦记 特级草菇老抽 1.65L 瓶装酱油 家庭厨房调味品 | 花西子螺黛生花眉笔细节点描顺滑显色防晕染持久双头眉笔0.08g | beauty | beauty, digital, clothing, food | recall_or_candidate_generation_error | strict recall@5 is 0 |
| negative_too_low_phone_025 | negative | negative_or_impossible | 500元以内买一台拍照旗舰手机 |  |  |  |  |  | digital |  | empty_result | empty result; category mismatch |
| negative_no_product_026 | negative | negative_or_impossible | 我想买宠物猫粮和自动喂食器 |  |  |  |  |  |  |  | empty_result | empty result |
