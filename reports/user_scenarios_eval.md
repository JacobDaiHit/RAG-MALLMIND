# MallMind 典型用户场景评估报告

## 总览

- 总 case 数：11
- ok：8
- failed：0
- suspicious：0
- not_applicable：3
- catalog_gap：2
- capability_gap：1
- business_failed：0

## 关键通过率

- RAG 适用场景通过率：1.0000
- 多轮状态通过率：0.0000
- 反选约束通过率：1.0000
- 跨类目组合通过率：1.0000
- 购物车状态通过率：1.0000

## 按难度汇总

| 分组 | total | ok | failed | suspicious | not_applicable | catalog_gap | capability_gap | business_failed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| advanced | 4 | 3 | 0 | 0 | 1 | 0 | 1 | 0 |
| basic | 4 | 3 | 0 | 0 | 1 | 1 | 0 | 0 |
| intermediate | 3 | 2 | 0 | 0 | 1 | 1 | 0 | 0 |

## 按场景类型汇总

| 分组 | total | ok | failed | suspicious | not_applicable | catalog_gap | capability_gap | business_failed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| cart_crud | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| conditional_filter | 2 | 1 | 0 | 0 | 1 | 1 | 0 | 0 |
| multimodal_photo_search | 1 | 0 | 0 | 0 | 1 | 0 | 1 | 0 |
| multiturn_refinement | 1 | 0 | 0 | 0 | 1 | 1 | 0 | 0 |
| negative_constraints | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| proactive_clarification | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| product_comparison | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| scenario_bundle_recommendation | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| single_turn_fuzzy_recommendation | 2 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |

## failed case 明细

无。

## suspicious case 明细

无。

## catalog_gap 明细

| case_id | status | failure_type | reason | probe | recommended |
| --- | --- | --- | --- | --- | --- |
| basic_pdf_example_under_200_earphones | not_applicable | budget_catalog_gap | 目标商品存在，但预算过滤后为空，不作为业务失败。 | budget_catalog_gap | - |
| intermediate_running_shoes_multiturn | not_applicable | budget_catalog_gap | 目标商品存在，但预算过滤后为空，不作为业务失败。 | budget_catalog_gap | - |
| advanced_photo_same_jacket | not_applicable | capability_gap | 当前没有真实图片语义理解，且 catalog 中没有明确外套/冲锋衣；这是能力/数据边界，不算业务回归。 | catalog_gap | - |

## capability_gap 明细

| case_id | status | failure_type | reason | probe | recommended |
| --- | --- | --- | --- | --- | --- |
| advanced_photo_same_jacket | not_applicable | capability_gap | 当前没有真实图片语义理解，且 catalog 中没有明确外套/冲锋衣；这是能力/数据边界，不算业务回归。 | catalog_gap | - |

## 多模态能力边界说明

当前评估不把 `[image]` 视为真实视觉语义输入，也不伪造“同款外套”识别成功。若 catalog 没有外套/冲锋衣，合格输出应是 catalog_gap / capability_gap，而不是用户外裤、背包、帽子冒充外套。

## Catalog 摘要

- ecommerce 商品数：100
- combined 商品数：342
- ecommerce 类目分布：{"beauty": 25, "clothing": 25, "digital": 25, "food": 25}

## 下一步建议

catalog_gap / budget_catalog_gap 反映数据集覆盖不足，不应算代码错误；后续可补洗面奶、蓝牙耳机、预算内跑鞋、外套/冲锋衣等商品。
多模态同款识别目前是能力边界，应先接入真实视觉语义理解，再把该类 case 转为业务通过项。
