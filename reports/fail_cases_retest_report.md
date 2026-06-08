# 失败案例复测报告（增强版）

测试时间：2026-06-08 00:19:13
测试服务器：http://127.0.0.1:8000

## 测试结果汇总

| # | 类别 | 输入 | 工具调用 | 商品数 | 根因 | 品牌过滤? | 排除Nike? |
|---|------|------|---------|--------|------|----------|----------|
| 124 | FAIL_品牌过滤 | 华为品牌的商品有哪些 | recommend_shopping_products(query=华为品牌的商 | 3 | LLM_CAPABILITY | ✓ |   |
| 130 | FAIL_品牌过滤 | 华为Pura 90 Pro 的详细信息 | recommend_shopping_products(query=华为Pura | 1 | LLM_CAPABILITY | ✓ |   |
| 146 | FAIL_排除品牌 | 看看运动鞋，不要Nike的 | recommend_shopping_products(query=看看运动鞋， | 3 | LLM_CAPABILITY |   | ✓ |
| 165 | FAIL_防幻觉 | 你们有卖 PS5 吗 | recommend_shopping_products(query=你们有卖 P | 0 | PROMPT_ISSUE |   |   |
| 167 | FAIL_防幻觉 | 三星Galaxy S30怎么样 | recommend_shopping_products(query=三星Gala | 0 | PIPELINE_BUG |   |   |
| 168 | FAIL_防幻觉 | 有没有一百万以上的商品 | recommend_shopping_products(query=有没有一百万 | 0 | PIPELINE_BUG |   |   |
| 128 | FAIL_过度拦截 | 最贵的商品是什么 | recommend_shopping_products(query=最贵的商品是 | 4 | GUARD_OVER拦截 |   |   |
| 148 | FAIL_购物车 | 帮我把 iPhone 17 Pro 加到购物车 | recommend_shopping_products(query=iPhone | 2 | CART_FALLBACK |   |   |
| 152 | FAIL_购物车 | 把华为耳机数量改成2 | apply_cart_instruction(query=把华为耳机数量改成2, | 0 | PIPELINE_BUG |   |   |
| 123 | PARTIAL_排序 | 所有商品按价格从低到高排列 | recommend_shopping_products(query=所有商品按价 | 4 | LLM_CAPABILITY |   |   |
| 131 | PARTIAL_兜底 | 小米17 Ultra 有几个版本 | recommend_shopping_products(query=小米17 U | 1 | PIPELINE_BUG |   |   |
| 133 | PARTIAL_过度拦截 | AirPods Pro 3 支持心率监测吗 | recommend_shopping_products(query=AirPod | 0 | GUARD_OVER拦截 |   |   |
| 141 | PARTIAL_追问 | 这款耳机有差评吗 | recommend_shopping_products(query=这款耳机有差 | 2 | CONTEXT_MISSING |   |   |
| 149 | PARTIAL_品牌过滤 | 我要买华为Pura 90 Pro，黑色的 | recommend_shopping_products(query=我要买华为P | 1 | LLM_CAPABILITY | ✓ |   |
| 150 | PARTIAL_购物车 | 看看我的购物车 | apply_cart_instruction(query=看看我的购物车, bu | 0 | PIPELINE_BUG |   |   |
| 151 | PARTIAL_购物车 | 把第一个去掉 | apply_cart_instruction(query=把第一个去掉, bud | 0 | GUARD_OVER拦截 |   |   |
| 157 | PARTIAL_多轮 | 续航怎么样 | general_chat(query=续航怎么样, budget=None, c | 0 | CONTEXT_MISSING |   |   |
| 164 | PARTIAL_多轮 | 都不要，看看别的 | general_chat(query=都不要，看看别的, budget=None | 0 | CONTEXT_MISSING |   |   |
| 170 | PARTIAL_语义 | 高端护肤品送妈妈，预算3000 | recommend_shopping_products(query=高端护肤品送 | 3 | LLM_CAPABILITY |   |   |

## LLM 字段提取覆盖率

| # | 输入 | 预期字段 | 提取 | 缺失 | 覆盖率 | LLM 原始 args |
|---|------|---------|------|------|--------|--------------|
| 124 | 华为品牌的商品有哪些 | brands | brands | - | 100% | {"query": "华为品牌的商品有哪些", "budget": null, "category": "", "usage": [], "preference |
| 130 | 华为Pura 90 Pro 的详细信息 | brands | brands | - | 100% | {"query": "华为Pura 90 Pro 的详细信息", "budget": null, "category": "", "usage": [], "p |
| 146 | 看看运动鞋，不要Nike的 | exclude_brands | exclude_brands | - | 100% | {"query": "看看运动鞋，不要Nike的", "budget": null, "category": "运动鞋", "usage": [], "pref |
| 123 | 所有商品按价格从低到高排列 | sort_order | sort_order | - | 100% | {"query": "所有商品按价格从低到高排列", "budget": null, "category": "", "usage": [], "prefere |
| 149 | 我要买华为Pura 90 Pro，黑色的 | brands | brands | - | 100% | {"query": "我要买华为Pura 90 Pro，黑色的", "budget": null, "category": "手机", "usage": [], |
| 170 | 高端护肤品送妈妈，预算3000 | budget | budget | - | 100% | {"query": "高端护肤品送妈妈，预算3000", "budget": 3000.0, "category": "护肤", "usage": [], "p |

**总体字段覆盖率: 100.0% (6/6)**

## 根因分类汇总

| 根因类型 | 数量 | 说明 |
|---------|------|------|
| LLM_CAPABILITY | 6 | 7B 模型无法稳定输出 brands/sort_order/price 等字段 |
| PIPELINE_BUG | 5 | pipeline 逻辑缺陷（如预算过滤、品牌硬过滤缺失） |
| GUARD_OVER拦截 | 3 | hallucination guard 过度拦截合法购物查询 |
| CONTEXT_MISSING | 3 | 多轮对话上下文未正确注入 LLM prompt |
| PROMPT_ISSUE | 1 | prompt 模板不够清晰或示例不足 |
| CART_FALLBACK | 1 | 购物车操作在无历史上下文时缺少 fallback |

## 详细分析

### #124 [FAIL_品牌过滤] "华为品牌的商品有哪些"

- **预期**: 仅返回华为品牌商品
- **根因**: LLM_CAPABILITY — 7B 模型无法稳定输出 brands/sort_order/price 等字段
- **工具调用**: recommend_shopping_products(query=华为品牌的商品有哪些, budget=None, category=, usage=[]) [conf=0.9, src=llm]
- **LLM 原始 arguments**:
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
- **字段提取**: 预期=['brands'], 提取=['brands'], 缺失=[]
- **商品卡片** (3):
  - 华为HUAWEI FreeBuds Pro 5 主动降噪真无线蓝牙耳机高解析音质 (品牌: 华为, 价格: ?)
  - 华为HUAWEI MatePad Pro Max 12.6英寸高刷屏多任务办公平板电脑 (品牌: 华为, 价格: ?)
  - 华为 MatePad Pro 13.2英寸 柔性OLED高刷屏轻办公创作旗舰平板电脑 (品牌: 华为, 价格: ?)
- **回复**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 华为HUAWEI FreeBuds Pro 5 主动降噪真无线蓝牙耳机高解析音质，参考价约 1499 CNY。下面保留了候选商品卡片，你可以继续对比或加入购物车。
- **品牌过滤检查**: PASS
- **路由来源**: llm
  - Local route: recommend_shopping_products
  - LLM route: recommend_shopping_products

### #130 [FAIL_品牌过滤] "华为Pura 90 Pro 的详细信息"

- **预期**: 返回华为Pura 90 Pro
- **根因**: LLM_CAPABILITY — 7B 模型无法稳定输出 brands/sort_order/price 等字段
- **工具调用**: recommend_shopping_products(query=华为Pura 90 Pro 的详细信息, budget=None, category=, usage=[]) [conf=0.9, src=llm]
- **LLM 原始 arguments**:
  ```json
  {
  "query": "华为Pura 90 Pro 的详细信息",
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
  "must_have_terms": [
    "Pura 90 Pro"
  ],
  "excluded_terms": [],
  "target_sub_categories": []
}
  ```
- **字段提取**: 预期=['brands'], 提取=['brands'], 缺失=[]
- **商品卡片** (1):
  - 华为HUAWEI Pura 90 Pro 超感光影像曲面屏轻薄旗舰手机12+256GB (品牌: 华为, 价格: ?)
- **回复**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 华为HUAWEI Pura 90 Pro 超感光影像曲面屏轻薄旗舰手机12+256GB，参考价约 6999 CNY。下面保留了候选商品卡片，你可以继续对比或加入购物车。
- **品牌过滤检查**: PASS
- **路由来源**: llm
  - Local route: recommend_shopping_products
  - LLM route: recommend_shopping_products

### #146 [FAIL_排除品牌] "看看运动鞋，不要Nike的"

- **预期**: 不返回Nike商品
- **根因**: LLM_CAPABILITY — 7B 模型无法稳定输出 brands/sort_order/price 等字段
- **工具调用**: recommend_shopping_products(query=看看运动鞋，不要Nike的, budget=None, category=运动鞋, usage=[]) [conf=0.9, src=llm]
- **LLM 原始 arguments**:
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
  "quantity": null,
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
- **字段提取**: 预期=['exclude_brands'], 提取=['exclude_brands'], 缺失=[]
- **商品卡片** (3):
  - 特步 160X 6.0 PRO 碳板竞速跑鞋马拉松训练比赛轻量跑步鞋 (品牌: 特步, 价格: ?)
  - 安踏 KT9 氮科技实战篮球鞋中帮缓震支撑外场内场训练比赛鞋 (品牌: 安踏, 价格: ?)
  - Merrell MOAB 3 GTX 男子防水登山徒步鞋稳定支撑户外多地形鞋 (品牌: 迈乐, 价格: ?)
- **回复**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 特步 160X 6.0 PRO 碳板竞速跑鞋马拉松训练比赛轻量跑步鞋，参考价约 999 CNY。下面保留了候选商品卡片，你可以继续对比或加入购物车。
- **Nike排除检查**: PASS
- **路由来源**: llm
  - Local route: recommend_shopping_products
  - LLM route: recommend_shopping_products

### #165 [FAIL_防幻觉] "你们有卖 PS5 吗"

- **预期**: 诚实告知没有PS5
- **根因**: PROMPT_ISSUE — prompt 模板不够清晰或示例不足
- **工具调用**: recommend_shopping_products(query=你们有卖 PS5 吗, budget=None, category=, usage=[]) [conf=0.9, src=llm]
- **LLM 原始 arguments**:
  ```json
  {
  "query": "你们有卖 PS5 吗",
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
  "sort_order": null,
  "price_min": null,
  "price_max": null,
  "must_have_terms": [
    "PS5"
  ],
  "excluded_terms": [],
  "target_sub_categories": []
}
  ```
- **回复**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。这次没有找到足够贴合的商品，可以换个预算、品类或关键词再试。
- **路由来源**: llm
  - Local route: general_chat
  - LLM route: recommend_shopping_products

### #167 [FAIL_防幻觉] "三星Galaxy S30怎么样"

- **预期**: 诚实告知没有该商品
- **根因**: PIPELINE_BUG — pipeline 逻辑缺陷（如预算过滤、品牌硬过滤缺失）
- **工具调用**: recommend_shopping_products(query=三星Galaxy S30怎么样, budget=None, category=手机, usage=[]) [conf=0.9, src=llm]
- **LLM 原始 arguments**:
  ```json
  {
  "query": "三星Galaxy S30怎么样",
  "budget": null,
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
  "brands": [
    "三星"
  ],
  "exclude_brands": [],
  "sort_order": null,
  "price_min": null,
  "price_max": null,
  "must_have_terms": [
    "Galaxy S30"
  ],
  "excluded_terms": [],
  "target_sub_categories": []
}
  ```
- **回复**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。当前商品库缺少可推荐候选，无法生成完整购物建议。
- **路由来源**: llm
  - Local route: recommend_shopping_products
  - LLM route: recommend_shopping_products

### #168 [FAIL_防幻觉] "有没有一百万以上的商品"

- **预期**: 诚实告知没有超高价商品
- **根因**: PIPELINE_BUG — pipeline 逻辑缺陷（如预算过滤、品牌硬过滤缺失）
- **工具调用**: recommend_shopping_products(query=有没有一百万以上的商品, budget=None, category=, usage=[]) [conf=0.9, src=llm]
- **LLM 原始 arguments**:
  ```json
  {
  "query": "有没有一百万以上的商品",
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
  "sort_order": null,
  "price_min": 1000000.0,
  "price_max": null,
  "must_have_terms": [],
  "excluded_terms": [],
  "target_sub_categories": []
}
  ```
- **回复**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。当前商品库缺少可推荐候选，无法生成完整购物建议。
- **路由来源**: llm
  - Local route: recommend_shopping_products
  - LLM route: recommend_shopping_products

### #128 [FAIL_过度拦截] "最贵的商品是什么"

- **预期**: 推荐最贵的商品
- **根因**: GUARD_OVER拦截 — hallucination guard 过度拦截合法购物查询
- **工具调用**: recommend_shopping_products(query=最贵的商品是什么, budget=None, category=, usage=[]) [conf=0.9, src=llm]
- **LLM 原始 arguments**:
  ```json
  {
  "query": "最贵的商品是什么",
  "budget": null,
  "category": "",
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
  "exclude_brands": [],
  "sort_order": "price_desc",
  "price_min": null,
  "price_max": null,
  "must_have_terms": [],
  "excluded_terms": [],
  "target_sub_categories": []
}
  ```
- **商品卡片** (4):
  - vivo Pad 6 Pro 12.1英寸高刷全面屏学习娱乐多任务办公平板电脑 (品牌: vivo, 价格: ?)
  - 优衣库 U AIRism 棉质宽松圆领短袖T恤 男装 基础纯色上衣 (品牌: 优衣库, 价格: ?)
  - 薇诺娜舒敏保湿特护霜敏感肌修护屏障舒缓干痒保湿面霜50g (品牌: 薇诺娜, 价格: ?)
  - 李锦记 特级草菇老抽 1.65L 瓶装酱油 家庭厨房调味品 (品牌: 李锦记, 价格: ?)
- **回复**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 vivo Pad 6 Pro 12.1英寸高刷全面屏学习娱乐多任务办公平板电脑，参考价约 3299 CNY。下面保留了候选商品卡片，你可以继续对比或加入购物车。
- **路由来源**: llm
  - Local route: general_chat
  - LLM route: recommend_shopping_products

### #148 [FAIL_购物车] "帮我把 iPhone 17 Pro 加到购物车"

- **预期**: 应先推荐再操作
- **根因**: CART_FALLBACK — 购物车操作在无历史上下文时缺少 fallback
- **工具调用**: recommend_shopping_products(query=iPhone 17 Pro, budget=None, category=, usage=[]) [conf=0.9, src=llm]
- **LLM 原始 arguments**:
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
- **商品卡片** (2):
  - Apple iPhone 17 Pro 6.3英寸 A19 Pro 256GB 全网通旗舰手机 (品牌: Apple 苹果, 价格: ?)
  - Apple iPhone 17 Pro Max 6.9英寸 A19 Pro 256GB 大屏旗舰手机 (品牌: Apple 苹果, 价格: ?)
- **回复**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 Apple iPhone 17 Pro 6.3英寸 A19 Pro 256GB 全网通旗舰手机，参考价约 8999 CNY。下面保留了候选商品卡片，你可以继续对比或加入购物车。
- **路由来源**: llm
  - Local route: recommend_shopping_products
  - LLM route: recommend_shopping_products

### #152 [FAIL_购物车] "把华为耳机数量改成2"

- **预期**: 仅修改华为耳机数量
- **根因**: PIPELINE_BUG — pipeline 逻辑缺陷（如预算过滤、品牌硬过滤缺失）
- **工具调用**: apply_cart_instruction(query=把华为耳机数量改成2, budget=None, category=耳机, usage=[]) [conf=0.9, src=llm]
- **LLM 原始 arguments**:
  ```json
  {
  "query": "把华为耳机数量改成2",
  "budget": null,
  "category": "耳机",
  "usage": [],
  "preferences": {},
  "product_ids": [],
  "catalog_scope": "combined",
  "compare_with_previous": false,
  "quantity": 2,
  "action": "update_quantity",
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
- **回复**:
  > 没有找到可操作的商品，请先推荐商品或指定 product_id。
- **品牌过滤检查**: FAIL
- **路由来源**: llm
  - Local route: recommend_shopping_products
  - LLM route: apply_cart_instruction

### #123 [PARTIAL_排序] "所有商品按价格从低到高排列"

- **预期**: 商品按价格排序
- **根因**: LLM_CAPABILITY — 7B 模型无法稳定输出 brands/sort_order/price 等字段
- **工具调用**: recommend_shopping_products(query=所有商品按价格从低到高排列, budget=None, category=, usage=[]) [conf=0.9, src=llm]
- **LLM 原始 arguments**:
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
- **字段提取**: 预期=['sort_order'], 提取=['sort_order'], 缺失=[]
- **商品卡片** (4):
  - 李锦记 特级草菇老抽 1.65L 瓶装酱油 家庭厨房调味品 (品牌: 李锦记, 价格: ?)
  - 薇诺娜舒敏保湿特护霜敏感肌修护屏障舒缓干痒保湿面霜50g (品牌: 薇诺娜, 价格: ?)
  - 优衣库 U AIRism 棉质宽松圆领短袖T恤 男装 基础纯色上衣 (品牌: 优衣库, 价格: ?)
  - vivo Pad 6 Pro 12.1英寸高刷全面屏学习娱乐多任务办公平板电脑 (品牌: vivo, 价格: ?)
- **回复**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 李锦记 特级草菇老抽 1.65L 瓶装酱油 家庭厨房调味品，参考价约 9 CNY。下面保留了候选商品卡片，你可以继续对比或加入购物车。
- **路由来源**: llm
  - Local route: recommend_shopping_products
  - LLM route: recommend_shopping_products

### #131 [PARTIAL_兜底] "小米17 Ultra 有几个版本"

- **预期**: 识别并正确兜底
- **根因**: PIPELINE_BUG — pipeline 逻辑缺陷（如预算过滤、品牌硬过滤缺失）
- **工具调用**: recommend_shopping_products(query=小米17 Ultra 有几个版本, budget=None, category=, usage=[]) [conf=0.9, src=llm]
- **LLM 原始 arguments**:
  ```json
  {
  "query": "小米17 Ultra 有几个版本",
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
    "小米"
  ],
  "exclude_brands": [],
  "sort_order": null,
  "price_min": null,
  "price_max": null,
  "must_have_terms": [
    "小米17 Ultra"
  ],
  "excluded_terms": [],
  "target_sub_categories": []
}
  ```
- **商品卡片** (1):
  - 小米 17 Ultra 2K高刷屏潜望长焦澎湃芯片影像旗舰5G手机12+256GB (品牌: 小米, 价格: ?)
- **回复**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 小米 17 Ultra 2K高刷屏潜望长焦澎湃芯片影像旗舰5G手机12+256GB，参考价约 7499 CNY。下面保留了候选商品卡片，你可以继续对比或加入购物车。
- **路由来源**: llm
  - Local route: recommend_shopping_products
  - LLM route: recommend_shopping_products

### #133 [PARTIAL_过度拦截] "AirPods Pro 3 支持心率监测吗"

- **预期**: 应走recommend工具
- **根因**: GUARD_OVER拦截 — hallucination guard 过度拦截合法购物查询
- **工具调用**: recommend_shopping_products(query=AirPods Pro 3 支持心率监测吗, budget=None, category=耳机, usage=[]) [conf=0.9, src=llm]
- **LLM 原始 arguments**:
  ```json
  {
  "query": "AirPods Pro 3 支持心率监测吗",
  "budget": null,
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
  "brands": [
    "Apple"
  ],
  "exclude_brands": [],
  "sort_order": null,
  "price_min": null,
  "price_max": null,
  "must_have_terms": [
    "AirPods Pro 3"
  ],
  "excluded_terms": [],
  "target_sub_categories": []
}
  ```
- **回复**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。当前商品库缺少可推荐候选，无法生成完整购物建议。
- **路由来源**: llm
  - Local route: general_chat
  - LLM route: recommend_shopping_products

### #141 [PARTIAL_追问] "这款耳机有差评吗"

- **预期**: 应追问是哪款耳机
- **根因**: CONTEXT_MISSING — 多轮对话上下文未正确注入 LLM prompt
- **工具调用**: recommend_shopping_products(query=这款耳机有差评吗, budget=None, category=耳机, usage=[]) [conf=0.86, src=guard]
- **LLM 原始 arguments**:
  ```json
  {
  "query": "这款耳机有差评吗",
  "budget": null,
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
  "price_max": null,
  "must_have_terms": [],
  "excluded_terms": [],
  "target_sub_categories": []
}
  ```
- **商品卡片** (2):
  - 华为HUAWEI FreeBuds Pro 5 主动降噪真无线蓝牙耳机高解析音质 (品牌: 华为, 价格: ?)
  - Apple AirPods Pro 3 主动降噪真无线蓝牙耳机 心率监测版 (品牌: Apple 苹果, 价格: ?)
- **回复**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 华为HUAWEI FreeBuds Pro 5 主动降噪真无线蓝牙耳机高解析音质，参考价约 1499 CNY。下面保留了候选商品卡片，你可以继续对比或加入购物车。
- **路由来源**: guard
  - Local route: recommend_shopping_products

### #149 [PARTIAL_品牌过滤] "我要买华为Pura 90 Pro，黑色的"

- **预期**: 返回华为品牌商品
- **根因**: LLM_CAPABILITY — 7B 模型无法稳定输出 brands/sort_order/price 等字段
- **工具调用**: recommend_shopping_products(query=我要买华为Pura 90 Pro，黑色的, budget=None, category=手机, usage=[]) [conf=0.95, src=llm]
- **LLM 原始 arguments**:
  ```json
  {
  "query": "我要买华为Pura 90 Pro，黑色的",
  "budget": null,
  "category": "手机",
  "usage": [],
  "preferences": {
    "color": "黑色"
  },
  "product_ids": [],
  "catalog_scope": "ecommerce",
  "compare_with_previous": false,
  "quantity": null,
  "action": "add_to_cart",
  "topic": "",
  "need_full_pc_build": false,
  "brands": [
    "华为"
  ],
  "exclude_brands": [],
  "sort_order": null,
  "price_min": null,
  "price_max": null,
  "must_have_terms": [
    "Pura 90 Pro"
  ],
  "excluded_terms": [],
  "target_sub_categories": []
}
  ```
- **字段提取**: 预期=['brands'], 提取=['brands'], 缺失=[]
- **商品卡片** (1):
  - 华为HUAWEI Pura 90 Pro 超感光影像曲面屏轻薄旗舰手机12+256GB (品牌: 华为, 价格: ?)
- **回复**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 华为HUAWEI Pura 90 Pro 超感光影像曲面屏轻薄旗舰手机12+256GB，参考价约 6999 CNY。下面保留了候选商品卡片，你可以继续对比或加入购物车。
- **品牌过滤检查**: PASS
- **路由来源**: llm
  - Local route: recommend_shopping_products
  - LLM route: recommend_shopping_products

### #150 [PARTIAL_购物车] "看看我的购物车"

- **预期**: 显示购物车内容
- **根因**: PIPELINE_BUG — pipeline 逻辑缺陷（如预算过滤、品牌硬过滤缺失）
- **工具调用**: apply_cart_instruction(query=看看我的购物车, budget=None, category=, usage=[]) [conf=0.95, src=llm]
- **LLM 原始 arguments**:
  ```json
  {
  "query": "看看我的购物车",
  "budget": null,
  "category": "",
  "usage": [],
  "preferences": {},
  "product_ids": [],
  "catalog_scope": "combined",
  "compare_with_previous": false,
  "quantity": null,
  "action": "view_cart",
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
- **回复**:
  > 没有找到可操作的商品，请先推荐商品或指定 product_id。
- **路由来源**: llm
  - Local route: recommend_shopping_products
  - LLM route: apply_cart_instruction

### #151 [PARTIAL_购物车] "把第一个去掉"

- **预期**: 应走apply_cart_instruction
- **根因**: GUARD_OVER拦截 — hallucination guard 过度拦截合法购物查询
- **工具调用**: apply_cart_instruction(query=把第一个去掉, budget=None, category=, usage=[]) [conf=0.9, src=llm]
- **LLM 原始 arguments**:
  ```json
  {
  "query": "把第一个去掉",
  "budget": null,
  "category": "",
  "usage": [],
  "preferences": {},
  "product_ids": [],
  "catalog_scope": "combined",
  "compare_with_previous": false,
  "quantity": 1,
  "action": "remove_first",
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
- **回复**:
  > 没有找到可操作的商品，请先推荐商品或指定 product_id。
- **路由来源**: llm
  - Local route: general_chat
  - LLM route: apply_cart_instruction

### #157 [PARTIAL_多轮] "续航怎么样"

- **预期**: 理解上下文，回答上轮商品
- **根因**: CONTEXT_MISSING — 多轮对话上下文未正确注入 LLM prompt
- **工具调用**: general_chat(query=续航怎么样, budget=None, category=, usage=[]) [conf=0.9, src=llm]
- **LLM 原始 arguments**:
  ```json
  {
  "query": "续航怎么样",
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
  "sort_order": null,
  "price_min": null,
  "price_max": null,
  "must_have_terms": [],
  "excluded_terms": [],
  "target_sub_categories": []
}
  ```
- **回复**:
  > 您问的是哪款产品的续航呀？是手机、耳机还是其他电子产品呢？告诉我具体型号，我帮您查查真实用户的续航反馈！
- **路由来源**: llm
  - Local route: recommend_shopping_products
  - LLM route: general_chat

### #164 [PARTIAL_多轮] "都不要，看看别的"

- **预期**: 排除已推荐商品
- **根因**: CONTEXT_MISSING — 多轮对话上下文未正确注入 LLM prompt
- **工具调用**: general_chat(query=都不要，看看别的, budget=None, category=, usage=[]) [conf=0.9, src=llm]
- **LLM 原始 arguments**:
  ```json
  {
  "query": "都不要，看看别的",
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
  "sort_order": null,
  "price_min": null,
  "price_max": null,
  "must_have_terms": [],
  "excluded_terms": [],
  "target_sub_categories": []
}
  ```
- **回复**:
  > 好的，没问题！您可以随时告诉我您想看什么品类，比如“我想找一双运动鞋”或者“帮我看看有什么好吃的零食”，我会立刻为您挑选推荐！
- **路由来源**: llm
  - Local route: recommend_shopping_products
  - LLM route: general_chat

### #170 [PARTIAL_语义] "高端护肤品送妈妈，预算3000"

- **预期**: 推荐高端护肤品
- **根因**: LLM_CAPABILITY — 7B 模型无法稳定输出 brands/sort_order/price 等字段
- **工具调用**: recommend_shopping_products(query=高端护肤品送妈妈，预算3000, budget=3000.0, category=护肤, usage=[]) [conf=0.86, src=guard]
- **LLM 原始 arguments**:
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
- **字段提取**: 预期=['budget'], 提取=['budget'], 缺失=[]
- **商品卡片** (3):
  - 薇诺娜舒敏保湿特护霜敏感肌修护屏障舒缓干痒保湿面霜50g (品牌: 薇诺娜, 价格: ?)
  - 巴黎欧莱雅新多重防护隔离露水感轻薄高倍防晒修护提亮30ml (品牌: 巴黎欧莱雅, 价格: ?)
  - The Ordinary烟酰胺10%锌1%精华液平衡油脂淡化毛孔提亮肤色30ml (品牌: The Ordinary, 价格: ?)
- **回复**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 薇诺娜舒敏保湿特护霜敏感肌修护屏障舒缓干痒保湿面霜50g，参考价约 89 CNY。下面保留了候选商品卡片，你可以继续对比或加入购物车。
- **路由来源**: guard
  - Local route: recommend_shopping_products
