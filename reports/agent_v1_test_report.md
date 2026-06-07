# MallMind Agent v1 测试报告

## 测试时间：2026-06-07 13:48:48
## 使用模型：unknown
## 服务器：http://127.0.0.1:8000
## 总用例数：9

## 结果汇总

| # | 类别 | 输入 | 工具调用链 | 商品卡数 | 耗时ms | 事件流 | 回复摘要 |
|---|------|------|-----------|---------|--------|--------|---------|
| 21 | 精准搜索 | 有没有2000到5000的护肤品 | recommend_shopping_products(query=有没有2000到5000的护肤品, budget=2... | 0 | 534 | runtime_mode,tool_call,delta,progress,progress,progress | 我先按你的需求筛一遍商品库，优先找最相关的真实商品。这次没有找到足够贴合的商品，可以换个预算、品类或... |
| 34 | 口碑查询 | 差评多吗 | general_chat(query=差评多吗, budget=None, category=, usage=[]) [... | 0 | 1619 | runtime_mode,tool_call,delta,done | 这个问题有点宽泛呢，您是指哪款商品呀？告诉我具体品类或名称，我帮您查查口碑和评价情况～ |
| 43 | 购物车 | 不要第一个了 | apply_cart_instruction(query=推荐一款手机，帮我加到购物车。用户追问：不要第一个了, bud... | 0 | 18 | runtime_mode,tool_call,delta,cart,done | 已将 OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256GB 加入购物... |
| 53 | 多轮对话 | 给我看看零食 | recommend_shopping_products(query=给我看看零食, budget=None, categ... | 3 | 770 | runtime_mode,tool_call,delta,progress,progress,progress | 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 OPPO Reno 16 Pro 轻... |
| 54 | 边界异常 | 推荐500元以下的手机 | recommend_shopping_products(query=推荐500元以下的手机, budget=500.0,... | 0 | 328 | runtime_mode,tool_call,delta,progress,progress,progress | 我先按你的需求筛一遍商品库，优先找最相关的真实商品。这次没有找到足够贴合的商品，可以换个预算、品类或... |
| 56 | 边界异常 | 对比手机和洗面奶 | general_chat(query=对比手机和洗面奶, budget=None, category=手机, usage... | 0 | 1798 | runtime_mode,tool_call,delta,done | 这两者品类跨度有点大呢！手机属于数码电子产品，洗面奶则是个人护理用品，它们的功能和使用场景完全不同。... |
| 57 | 边界异常 | 。。。 | general_chat(query=。。。, budget=None, category=, usage=[]) [c... | 0 | 1467 | runtime_mode,tool_call,delta,done | 您好！我是您的智能导购助手，可以帮您搜索商品、推荐好物、对比参数以及管理购物车。有什么购物需求随时告... |
| 58 | 边界异常 |  | 无工具调用 | 0 | 91 |  |  |
| 59 | 组合调用 | 推荐手机，直接帮我加到购物车 | recommend_shopping_products(query=推荐手机，直接帮我加到购物车, budget=Non... | 3 | 1209 | runtime_mode,tool_call,delta,progress,progress,progress | 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 OPPO Reno 16 Pro 轻... |

## 详细分析

### #21 [精准搜索] "有没有2000到5000的护肤品"

- **预期**: recommend_shopping_products(min_price=2000,max_price=5000)
- **运行时模式**: balanced
- **路由决策**: "balanced"
- **工具调用链**: recommend_shopping_products(query=有没有2000到5000的护肤品, budget=2000.0, category=护肤, usage=[]) [conf=0.86, src=guard]
- **对比表**: 已生成
- **回复全文**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。这次没有找到足够贴合的商品，可以换个预算、品类或关键词再试。
- **耗时**: 534ms

### #34 [口碑查询] "差评多吗"

- **预期**: recommend_shopping_products → 评价分析
- **运行时模式**: balanced
- **路由决策**: "balanced"
- **工具调用链**: general_chat(query=差评多吗, budget=None, category=, usage=[]) [conf=0.9, src=llm]
- **回复全文**:
  > 这个问题有点宽泛呢，您是指哪款商品呀？告诉我具体品类或名称，我帮您查查口碑和评价情况～
- **耗时**: 1619ms

### #43 [购物车] "不要第一个了"

- **预期**: remove_from_cart
- **运行时模式**: balanced
- **路由决策**: "balanced"
- **工具调用链**: apply_cart_instruction(query=推荐一款手机，帮我加到购物车。用户追问：不要第一个了, budget=None, category=, usage=[]) [conf=0.9, src=followup_guard]
- **购物车**: 1 件
- **回复全文**:
  > 已将 OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256GB 加入购物车，数量 1。
- **耗时**: 18ms

### #53 [多轮对话] "给我看看零食"

- **预期**: 话题切换
- **运行时模式**: balanced
- **路由决策**: "balanced"
- **工具调用链**: recommend_shopping_products(query=给我看看零食, budget=None, category=食品, usage=[]) [conf=0.86, src=guard]
- **商品卡片** (3): ['OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256', '小米 17 Max 大屏长续航高性能影音游戏5G智能手机12+256GB', 'OPPO Find X9 Ultra 超大底影像旗舰2K高刷屏长续航5G智能手机']
- **对比表**: 已生成
- **回复全文**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256GB，参考价约 3299 CNY。下面保留了候选商品卡片，你可以继续对比或加入购物车。
- **耗时**: 770ms

### #54 [边界异常] "推荐500元以下的手机"

- **预期**: 过滤后可能无结果→诚实回答
- **运行时模式**: balanced
- **路由决策**: "balanced"
- **工具调用链**: recommend_shopping_products(query=推荐500元以下的手机, budget=500.0, category=手机, usage=[]) [conf=0.86, src=guard]
- **对比表**: 已生成
- **回复全文**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。这次没有找到足够贴合的商品，可以换个预算、品类或关键词再试。
- **耗时**: 328ms

### #56 [边界异常] "对比手机和洗面奶"

- **预期**: 提示跨品类无法对比
- **运行时模式**: balanced
- **路由决策**: "balanced"
- **工具调用链**: general_chat(query=对比手机和洗面奶, budget=None, category=手机, usage=[]) [conf=0.9, src=llm]
- **回复全文**:
  > 这两者品类跨度有点大呢！手机属于数码电子产品，洗面奶则是个人护理用品，它们的功能和使用场景完全不同。如果您是想了解具体哪款手机和哪款洗面奶，可以告诉我品牌和型号，我帮您详细对比哦！
- **耗时**: 1798ms

### #57 [边界异常] "。。。"

- **预期**: 友好提示请输入问题
- **运行时模式**: balanced
- **路由决策**: "balanced"
- **工具调用链**: general_chat(query=。。。, budget=None, category=, usage=[]) [conf=0.9, src=llm]
- **回复全文**:
  > 您好！我是您的智能导购助手，可以帮您搜索商品、推荐好物、对比参数以及管理购物车。有什么购物需求随时告诉我哦！
- **耗时**: 1467ms

### #58 [边界异常] ""

- **预期**: 空消息→提示
- **运行时模式**: 
- **路由决策**: ""
- **工具调用链**: 无工具调用
- **回复全文**:
  > 
- **耗时**: 91ms
- **错误**: HTTP 请求失败: HTTP Error 400: Bad Request

### #59 [组合调用] "推荐手机，直接帮我加到购物车"

- **预期**: recommend → add_to_cart
- **运行时模式**: balanced
- **路由决策**: "balanced"
- **工具调用链**: recommend_shopping_products(query=推荐手机，直接帮我加到购物车, budget=None, category=手机, usage=[]) [conf=0.9, src=llm]
- **商品卡片** (3): ['OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256', '小米 17 Max 大屏长续航高性能影音游戏5G智能手机12+256GB', 'OPPO Find X9 Ultra 超大底影像旗舰2K高刷屏长续航5G智能手机']
- **对比表**: 已生成
- **购物车**: 1 件
- **回复全文**:
  > 我先按你的需求筛一遍商品库，优先找最相关的真实商品。我优先推荐 OPPO Reno 16 Pro 轻薄人像摄影高刷屏快充5G智能手机12+256GB，参考价约 3299 CNY。下面保留了候选商品卡片，你可以继续对比或加入购物车。
- **耗时**: 1209ms
