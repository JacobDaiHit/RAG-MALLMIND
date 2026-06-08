# MallMind Agent v2 扩展测试报告

## 测试时间：2026-06-08 08:23:51
## 使用模型：unknown
## 服务器：http://127.0.0.1:8000
## 总用例数：9

## 结果汇总

| # | 类别 | 输入 | 工具调用链 | 商品卡数 | 耗时ms | 事件流 | 回复摘要 |
|---|------|------|-----------|---------|--------|--------|---------|
| 156 | 多轮对话 | 推荐一款手机 | recommend_shopping_products(query=推荐一款手机, budget=None, categ... | 3 | 57 | runtime_mode,tool_call,delta,progress,progress,progress | 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 OPPO Reno 16 Pro 轻... |
| 157 | 多轮对话 | 续航怎么样 | recommend_shopping_products(query=推荐一款手机。用户追问：续航怎么样, budget=... | 3 | 29 | runtime_mode,tool_call,delta,progress,progress,progress | 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 小米 17 Max 大屏长续航高性能... |
| 158 | 多轮对话 | 有没有更便宜的 | recommend_shopping_products(query=有没有更便宜的, budget=None, cate... | 1 | 56 | runtime_mode,tool_call,delta,progress,progress,progress | 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 OPPO Reno 16 Pro 轻... |
| 159 | 多轮对话 | 换零食看看吧 | recommend_shopping_products(query=换零食看看吧, budget=None, categ... | 3 | 31 | runtime_mode,tool_call,delta,progress,progress,progress | 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 李锦记 特级草菇老抽 1.65L 瓶... |
| 160 | 多轮对话 | 第一个不错，帮我加购物车 | apply_cart_instruction(query=第一个不错，帮我加购物车, budget=None, cate... | 0 | 7 | runtime_mode,tool_call,delta,cart,done | 已将 李锦记 特级草菇老抽 1.65L 瓶装酱油 家庭厨房调味品 加入购物车，数量 1。 |
| 161 | 多轮对话 | 还有别的推荐吗 | recommend_shopping_products(query=还有别的推荐吗, budget=None, cate... | 3 | 32 | runtime_mode,tool_call,delta,progress,progress,progress | 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 李锦记 特级草菇老抽 1.65L 瓶... |
| 162 | 多轮对话 | 对比一下这两款耳机 | compare_products(query=对比一下这两款耳机, budget=None, category=耳机, ... | 0 | 18 | runtime_mode,tool_call,intent_route,comparison_table,result,done |  |
| 163 | 多轮对话 | 推荐一款手机 | recommend_shopping_products(query=推荐一款手机, budget=None, categ... | 3 | 46 | runtime_mode,tool_call,delta,progress,progress,progress | 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 OPPO Reno 16 Pro 轻... |
| 164 | 多轮对话 | 都不要，看看别的 | recommend_shopping_products(query=推荐一款手机。用户追问：都不要，看看别的, budg... | 3 | 32 | runtime_mode,tool_call,delta,progress,progress,progress | 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 OPPO Reno 16 Pro 轻... |

## 详细分析

### #156 [多轮对话] "推荐一款手机"

- **预期**: 第1轮: recommend → 手机+CARD
- **运行时模式**: balanced
- **工具调用链**: recommend_shopping_products(query=推荐一款手机, budget=None, category=手机, usage=[]) [conf=0.86, src=guard]
- **商品卡片** (3): ['OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256', '小米 17 Max 大屏长续航高性能影音游戏5G智能手机12+256GB', 'OPPO Find X9 Ultra 超大底影像旗舰2K高刷屏长续航5G智能手机']
- **对比表**: 已生成
- **回复全文**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256GB，参考价约 3299 CNY。下面保留了候选商品卡片，你可以继续对比或加入购物车。
- **耗时**: 57ms

### #157 [多轮对话] "续航怎么样"

- **预期**: 理解上下文=iPhone 17 Pro, 续航FAQ
- **运行时模式**: balanced
- **工具调用链**: recommend_shopping_products(query=推荐一款手机。用户追问：续航怎么样, budget=None, category=, usage=[]) [conf=0.9, src=followup_guard]
- **商品卡片** (3): ['小米 17 Max 大屏长续航高性能影音游戏5G智能手机12+256GB', 'OPPO Find X9 Ultra 超大底影像旗舰2K高刷屏长续航5G智能手机', 'vivo X300 Ultra 旗舰影像2K护眼屏长续航高性能5G智能手机']
- **对比表**: 已生成
- **回复全文**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 小米 17 Max 大屏长续航高性能影音游戏5G智能手机12+256GB，参考价约 6499 CNY。下面保留了候选商品卡片，你可以继续对比或加入购物车。
- **耗时**: 29ms

### #158 [多轮对话] "有没有更便宜的"

- **预期**: 理解上下文=手机, 推荐更便宜手机
- **运行时模式**: balanced
- **工具调用链**: recommend_shopping_products(query=有没有更便宜的, budget=None, category=, usage=[]) [conf=0.84, src=guard]
- **商品卡片** (1): ['OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256']
- **对比表**: 已生成
- **回复全文**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256GB，参考价约 3299 CNY。下面保留了候选商品卡片，你可以继续对比或加入购物车。
- **耗时**: 56ms

### #159 [多轮对话] "换零食看看吧"

- **预期**: 话题切换: recommend → 零食+CARD
- **运行时模式**: balanced
- **工具调用链**: recommend_shopping_products(query=换零食看看吧, budget=None, category=食品, usage=[]) [conf=0.86, src=guard]
- **商品卡片** (3): ['李锦记 特级草菇老抽 1.65L 瓶装酱油 家庭厨房调味品', '农夫山泉 东方树叶 无糖乌龙茶饮料500ml 瓶装茶饮低糖替代方案', '海天 金标生抽1.9L 瓶装酱油 家用厨房调味料酱香鲜味佐餐蘸料']
- **对比表**: 已生成
- **回复全文**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 李锦记 特级草菇老抽 1.65L 瓶装酱油 家庭厨房调味品，参考价约 9 CNY。下面保留了候选商品卡片，你可以继续对比或加入购物车。
- **耗时**: 31ms

### #160 [多轮对话] "第一个不错，帮我加购物车"

- **预期**: 理解第一个=零食, 加购物车
- **运行时模式**: balanced
- **工具调用链**: apply_cart_instruction(query=第一个不错，帮我加购物车, budget=None, category=, usage=[]) [conf=0.8, src=rules]
- **购物车**: 1 件
- **回复全文**:
  > 已将 李锦记 特级草菇老抽 1.65L 瓶装酱油 家庭厨房调味品 加入购物车，数量 1。
- **耗时**: 7ms

### #161 [多轮对话] "还有别的推荐吗"

- **预期**: 继续推荐零食/护肤品
- **运行时模式**: balanced
- **工具调用链**: recommend_shopping_products(query=还有别的推荐吗, budget=None, category=, usage=[]) [conf=0.7, src=rules]
- **商品卡片** (3): ['李锦记 特级草菇老抽 1.65L 瓶装酱油 家庭厨房调味品', '农夫山泉 东方树叶 无糖乌龙茶饮料500ml 瓶装茶饮低糖替代方案', '海天 金标生抽1.9L 瓶装酱油 家用厨房调味料酱香鲜味佐餐蘸料']
- **对比表**: 已生成
- **回复全文**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 李锦记 特级草菇老抽 1.65L 瓶装酱油 家庭厨房调味品，参考价约 9 CNY。下面保留了候选商品卡片，你可以继续对比或加入购物车。
- **耗时**: 32ms

### #162 [多轮对话] "对比一下这两款耳机"

- **预期**: compare_products → 耳机对比表
- **运行时模式**: balanced
- **工具调用链**: compare_products(query=对比一下这两款耳机, budget=None, category=耳机, usage=[]) [conf=0.88, src=guard]
- **对比表**: 已生成
- **回复全文**:
  > 
- **耗时**: 18ms

### #163 [多轮对话] "推荐一款手机"

- **预期**: 第1轮(新session)
- **运行时模式**: balanced
- **工具调用链**: recommend_shopping_products(query=推荐一款手机, budget=None, category=手机, usage=[]) [conf=0.86, src=guard]
- **商品卡片** (3): ['OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256', '小米 17 Max 大屏长续航高性能影音游戏5G智能手机12+256GB', 'OPPO Find X9 Ultra 超大底影像旗舰2K高刷屏长续航5G智能手机']
- **对比表**: 已生成
- **回复全文**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256GB，参考价约 3299 CNY。下面保留了候选商品卡片，你可以继续对比或加入购物车。
- **耗时**: 46ms

### #164 [多轮对话] "都不要，看看别的"

- **预期**: 理解不满意, 推荐其他手机
- **运行时模式**: balanced
- **工具调用链**: recommend_shopping_products(query=推荐一款手机。用户追问：都不要，看看别的, budget=None, category=, usage=[]) [conf=0.9, src=followup_guard]
- **商品卡片** (3): ['OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256', '小米 17 Max 大屏长续航高性能影音游戏5G智能手机12+256GB', 'OPPO Find X9 Ultra 超大底影像旗舰2K高刷屏长续航5G智能手机']
- **对比表**: 已生成
- **回复全文**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256GB，参考价约 3299 CNY。下面保留了候选商品卡片，你可以继续对比或加入购物车。
- **耗时**: 32ms
