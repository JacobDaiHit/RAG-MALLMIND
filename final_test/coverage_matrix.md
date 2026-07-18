# 固定评测能力覆盖矩阵

本表把根 README 声明的 V3 能力逐项落到固定 case，避免只测“推荐成功”而遗漏会话、拒绝和副作用。一个 case 可以覆盖多项能力；所有 ID 都在 `fixtures/fixed_eval_cases.json` 中。

| README 能力或边界 | 固定 case |
| --- | --- |
| 本地 `SAFE_DIRECT` 推荐、预算、品牌排除 | `direct_phone_price_brand_exclusion`、`direct_phone_include_huawei`、`card_price_fact_multiturn` |
| 复杂中文交给 SemanticParse，而非错误直通 | `semantic_basketball_shoes_surface`、`semantic_not_only_xiaomi`、`semantic_narrative_brand_preference` |
| 类型目标、排除类型、多品类条件 | `direct_tablet_type_release`、`semantic_multiple_type_exclusions`、`mixed_negative_noise_target_tablet` |
| 价格上界、下界、目标价 | `direct_phone_price_brand_exclusion`、`semantic_phone_price_lower_bound`、`semantic_sunscreen_target_budget` |
| 品牌包含、品牌排除、后续解除排除 | `direct_phone_include_huawei`、`semantic_phone_no_xiaomi_photo_battery`、`brand_blacklist_release_multiturn` |
| 目录外商品、无合格候选 | `unsupported_car_is_not_chat`、`unsupported_prescription_drug_is_not_chat`、`no_candidate_after_hard_constraints` |
| 闲聊与购物边界 | `general_hello`、`general_knowledge_question`、`unsupported_car_is_not_chat` |
| 商品卡价格、SKU、参数、两卡比较 | `card_price_fact_multiturn`、`card_sku_fact_multiturn`、`card_parameter_fact_multiturn`、`card_compare_multiturn` |
| 开放式探索与具体类别收敛 | `open_exploration_gift`、`exploration_then_concrete_type`、`pc_purchase_form_clarification_then_desktop`、`pc_purchase_form_clarification_then_laptop` |
| 长对话改话题不串条件 | `exploration_topic_switch_does_not_inherit` |
| 购物车查看、计划、确认、取消、改数量、删除、清空 | `cart_view_empty`、`cart_plan_and_confirm_multiturn`、`cart_plan_cancel`、`cart_set_quantity_remove_and_clear`、`cart_without_target_clarifies` |
| PC 首次装机、预算调整、单配件替换、方案比较 | `pc_explicit_build`、`pc_edit_and_compare`、`pc_component_replacement`、`pc_compare_without_two_versions_clarifies` |
| “买电脑”与“配主机”的区分 | `pc_purchase_form_clarification_then_desktop`、`pc_purchase_form_clarification_then_laptop` |
| 附件不回退旧链路 | `attachment_rejected_without_legacy_fallback` |
| 提示注入在编排前拦截 | `prompt_injection_blocked_before_orchestration` |
| Milvus 故障、过期卡片、Redis 网络异常 | `test_fault_contracts.py`（故障注入不依赖外部服务） |
| 并发独立会话正确性 | `fixtures/concurrency_cases.json` + `concurrency.py` |
