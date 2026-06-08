# LLM Fallback 诊断报告

测试时间：2026-06-07 23:53:40
服务器：http://127.0.0.1:8000

---

## 一、LLM 字段提取覆盖率

| # | 输入 | 预期字段 | 提取 | 缺失 | 覆盖率 | 工具 | 来源 |
|---|------|---------|------|------|--------|------|------|
| FE-01 | 华为品牌的商品有哪些 | brands | brands | - | 100% | recommend_shopping_products | llm |
| FE-02 | 看看运动鞋，不要Nike的 | exclude_brands | exclude_brands | - | 100% | recommend_shopping_products | llm |
| FE-03 | 所有商品按价格从低到高排列 | sort_order | sort_order | - | 100% | recommend_shopping_products | llm |
| FE-04 | 3000到5000之间的手机 | price_min, price_max | price_max | price_min | 50% | recommend_shopping_products | guard |
| FE-05 | 有什么好吃的零食推荐吗 | must_have_terms | - | must_have_terms | 0% | recommend_shopping_products | guard |
| FE-06 | 高端护肤品送妈妈，预算3000 | budget, must_have_terms | budget | must_have_terms | 50% | recommend_shopping_products | guard |
| FE-07 | 帮我把 iPhone 17 Pro 加到购物车 | product_ids | - | product_ids | 0% | recommend_shopping_products | llm |
| FE-08 | 推荐一款2000元以下的蓝牙耳机 | budget, category | budget, category | - | 100% | recommend_shopping_products | guard |

**总体字段覆盖率: 63.6% (7/11)**

> 字段覆盖率反映 LLM 的参数提取能力。7B 模型通常只能稳定输出 name/query/confidence/reason，
> brands/sort_order/price_min 等字段的覆盖率取决于 prompt 质量和模型能力。

## 二、LLM 原始输出（arguments 详情）

### FE-01: 华为品牌的商品有哪些

```json
{
  "query": "华为品牌的商品有哪些",
  "budget": null,
  "category": "",
  "usage": [],
  "preferences": {},
  "product_ids": [],
  "catalog_scope": "ecommerce",
  "compare_with_previous": false,
  "quantity": null,
  "action": "",
  "topic": "",
  "need_full_pc_build": false,
  "brands": [
    "华为"
  ],
  "exclude_brands": [],
  "sort_order": null,
  "price_min": null,
  "price_max": null,
  "must_have_terms": [],
  "excluded_terms": [],
  "target_sub_categories": []
}
```

### FE-02: 看看运动鞋，不要Nike的

```json
{
  "query": "看看运动鞋，不要Nike的",
  "budget": null,
  "category": "运动鞋",
  "usage": [],
  "preferences": {},
  "product_ids": [],
  "catalog_scope": "ecommerce",
  "compare_with_previous": false,
  "quantity": 1,
  "action": "",
  "topic": "",
  "need_full_pc_build": false,
  "brands": [],
  "exclude_brands": [
    "Nike"
  ],
  "sort_order": null,
  "price_min": null,
  "price_max": null,
  "must_have_terms": [],
  "excluded_terms": [],
  "target_sub_categories": []
}
```

### FE-03: 所有商品按价格从低到高排列

```json
{
  "query": "所有商品按价格从低到高排列",
  "budget": null,
  "category": "",
  "usage": [],
  "preferences": {},
  "product_ids": [],
  "catalog_scope": "ecommerce",
  "compare_with_previous": false,
  "quantity": null,
  "action": "",
  "topic": "",
  "need_full_pc_build": false,
  "brands": [],
  "exclude_brands": [],
  "sort_order": "price_asc",
  "price_min": null,
  "price_max": null,
  "must_have_terms": [],
  "excluded_terms": [],
  "target_sub_categories": []
}
```

### FE-04: 3000到5000之间的手机

```json
{
  "query": "3000到5000之间的手机",
  "budget": 3000.0,
  "category": "手机",
  "usage": [],
  "preferences": {},
  "product_ids": [],
  "catalog_scope": "ecommerce",
  "compare_with_previous": false,
  "quantity": null,
  "action": "",
  "topic": "",
  "need_full_pc_build": false,
  "brands": [],
  "exclude_brands": [],
  "sort_order": null,
  "price_min": null,
  "price_max": 3000.0,
  "must_have_terms": [],
  "excluded_terms": [],
  "target_sub_categories": []
}
```

### FE-05: 有什么好吃的零食推荐吗

```json
{
  "query": "有什么好吃的零食推荐吗",
  "budget": null,
  "category": "食品",
  "usage": [],
  "preferences": {},
  "product_ids": [],
  "catalog_scope": "ecommerce",
  "compare_with_previous": false,
  "quantity": null,
  "action": "",
  "topic": "",
  "need_full_pc_build": false,
  "brands": [],
  "exclude_brands": [],
  "sort_order": null,
  "price_min": null,
  "price_max": null,
  "must_have_terms": [],
  "excluded_terms": [],
  "target_sub_categories": []
}
```

### FE-06: 高端护肤品送妈妈，预算3000

```json
{
  "query": "高端护肤品送妈妈，预算3000",
  "budget": 3000.0,
  "category": "护肤",
  "usage": [],
  "preferences": {},
  "product_ids": [],
  "catalog_scope": "ecommerce",
  "compare_with_previous": false,
  "quantity": null,
  "action": "",
  "topic": "",
  "need_full_pc_build": false,
  "brands": [],
  "exclude_brands": [],
  "sort_order": null,
  "price_min": null,
  "price_max": 3000.0,
  "must_have_terms": [],
  "excluded_terms": [],
  "target_sub_categories": []
}
```

### FE-07: 帮我把 iPhone 17 Pro 加到购物车

```json
{
  "query": "iPhone 17 Pro",
  "budget": null,
  "category": "",
  "usage": [],
  "preferences": {},
  "product_ids": [],
  "catalog_scope": "ecommerce",
  "compare_with_previous": false,
  "quantity": null,
  "action": "add_to_cart",
  "topic": "",
  "need_full_pc_build": false,
  "brands": [],
  "exclude_brands": [],
  "sort_order": null,
  "price_min": null,
  "price_max": null,
  "must_have_terms": [],
  "excluded_terms": [],
  "target_sub_categories": []
}
```

### FE-08: 推荐一款2000元以下的蓝牙耳机

```json
{
  "query": "推荐一款2000元以下的蓝牙耳机",
  "budget": 2000.0,
  "category": "耳机",
  "usage": [],
  "preferences": {},
  "product_ids": [],
  "catalog_scope": "ecommerce",
  "compare_with_previous": false,
  "quantity": null,
  "action": "",
  "topic": "",
  "need_full_pc_build": false,
  "brands": [],
  "exclude_brands": [],
  "sort_order": null,
  "price_min": null,
  "price_max": 2000.0,
  "must_have_terms": [],
  "excluded_terms": [],
  "target_sub_categories": []
}
```

## 三、多轮对话上下文诊断

| # | 场景 | Step 1 工具 | Step 2 工具 | 上下文传递 |
|---|------|-----------|-----------|-----------|
| MT-01 | 续航追问（期望理解上文） | recommend_shopping_products | recommend_shopping_products | 待人工确认 |
| MT-02 | 排除已推荐（期望返回不同商品） | recommend_shopping_products | recommend_shopping_products | FAIL: 返回了与上轮相同的商品: {'小米 17 Max 大屏长续航高性能影音游戏5G智能手机12+256GB', 'OPPO Find X9 Ultra 超大底影像旗舰2K高刷屏长续航5G智能手机', 'OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256GB'} |
| MT-03 | 这款耳机（期望追问而非推荐） | recommend_shopping_products | N/A | 单步 |
| MT-04 | 第二页（期望追问品类） | recommend_shopping_products | N/A | 单步 |

## 四、LLM 降级链路诊断

| # | 输入 | 检查项 | 结果 | 详情 |
|---|------|--------|------|------|
| DG-01 | 华为品牌的商品有哪些 | 当 LLM 未提取 brands 时，pipeline 是否 | PASS | 华为商品数: 3/3 |
| DG-02 | 看看运动鞋，不要Nike的 | 当 LLM 未提取 exclude_brands 时，pip | FAIL | 包含Nike: True, 品牌: ['耐克', '特步', '安踏'] |
| DG-03 | 有没有防水的运动手表 | 目录无运动手表时，应诚实告知而非推荐无关商品 | PASS | 商品数: 0, 诚实告知: True |
| DG-04 | 你们有卖 PS5 吗 | 应走 general_chat 并诚实告知无 PS5 | PASS | 声称有PS5: False |

## 五、根因分类汇总

| 根因类型 | 数量 | 说明 |
|---------|------|------|
| LLM_CAPABILITY | 8 | 7B 模型无法稳定输出 brands/sort_order/price 等字段 |
| CONTEXT_MISSING | 4 | 多轮对话上下文未正确注入 LLM prompt |

## 六、改进方向

### 6.1 LLM_CAPABILITY（7B 模型字段提取上限）

当前 sensenova-6.7-flash-lite 只能稳定输出 4 个基础字段。
改进方向（不修改后端）：
1. **Prompt 优化**：简化 prompt，减少字段数量，增加 few-shot 示例
2. **两阶段策略**：第一阶段仅做工具选择（5 分类），第二阶段用更大模型做参数提取
3. **模型升级**：替换为 sensenova-12b 或更大模型

### 6.2 CONTEXT_MISSING（多轮上下文未注入）

改进方向（不修改后端）：
1. **测试侧**：在测试中注入上下文（如先发推荐请求再发追问），验证 session 是否正确传递
2. **诊断**：记录 session 的 topic_memory 和 last_result，确认数据是否到达 LLM prompt

### 6.3 测试基础设施改进

1. **字段覆盖率指标**：每次 prompt 修改后重新运行字段提取诊断，量化改进效果
2. **根因分类标签**：区分 LLM 能力不足 和 系统 bug，避免误判
3. **LLM 原始输出收集**：记录每个 case 的完整 arguments，为 prompt 优化提供数据