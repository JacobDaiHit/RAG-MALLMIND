# Android 原生客户端计划

后端已经转为传统电商导购。Android 第一版不需要重写推荐逻辑，只需要用原生体验接入 FastAPI，并把流式回复里的商品卡片渲染出来。

## 技术选型

- Kotlin
- Jetpack Compose
- Retrofit + OkHttp
- ViewModel + StateFlow
- Coil：加载 `/product-images/*` 商品图

## MVP 页面

1. 需求输入页
   - 输入文本购物需求。
   - 选择图片/PDF。
   - 示例：`推荐一款适合油皮的洗面奶，预算150以内`。

2. 需求审查页
   - 调 `POST /api/review-requirement`。
   - 展示类目、预算、偏好、否定条件、是否套装。
   - 有追问时让用户补充预算/场景/排除条件。

3. 流式推荐页
   - 用 OkHttp SSE 或普通流读取 `GET /api/stream-recommend`。
   - 渲染 `step` 时间线。
   - 收到 `plans` 或 `result` 后展示三套方案。

4. 商品卡片详情
   - 展示 `product_id/title/brand/category_name/sub_category/min_price/max_price/image_url/reason`。
   - 展示 FAQ、评价摘要和风险提示。

5. 购物车模拟页
   - 第一版可以本地维护 cart state。
   - 后续再接后端对话式加购/删除/下单接口。

## 推荐接口映射

### 需求审查

```http
POST /api/review-requirement
Content-Type: application/json

{
  "goal": "下周去三亚度假，帮我搭配一套从防晒到穿搭的方案，预算800以内",
  "attachments": []
}
```

重点字段：

```json
{
  "requirement": {
    "desired_categories": ["beauty", "clothing"],
    "price_max": 800,
    "need_bundle": true,
    "preferences": ["防晒", "旅行"]
  },
  "questions": []
}
```

### 流式推荐

```http
GET /api/stream-recommend?goal=...
Accept: text/event-stream
```

事件：

- `step`：流程节点。
- `requirement`：结构化需求。
- `catalog`：商品库数量。
- `plans`：可先渲染三套方案。
- `guidance`：解释和优化建议。
- `result`：最终完整结果。
- `done`：结束。

### 商品卡片模型

优先使用新字段：

```kotlin
data class ProductCard(
    val productId: String,
    val title: String,
    val brand: String,
    val category: String,
    val categoryName: String,
    val subCategory: String,
    val basePrice: Double,
    val minPrice: Double,
    val maxPrice: Double,
    val currency: String,
    val imageUrl: String,
    val description: String,
)
```

后端仍返回 `api_id/api_name/provider`，那是 Web 调试台兼容字段。Android 新代码不要依赖旧命名。

## Compose 页面拆分

```text
client/android/app/src/main/java/...
  data/
    ShoppingGuideApi.kt
    SseClient.kt
    dto/
      RequirementDto.kt
      RecommendationDto.kt
      ProductDto.kt
  ui/
    input/ShoppingInputScreen.kt
    review/RequirementReviewScreen.kt
    recommend/RecommendationStreamScreen.kt
    product/ProductCard.kt
    cart/CartScreen.kt
  viewmodel/
    ShoppingGuideViewModel.kt
```

## 体验要求

- 商品卡片必须显示真实商品图。
- 卡片里不要只放文本，至少包含标题、品牌、价格、推荐理由和图。
- 预算超限时展示后端风险提示，例如“没有严格满足 200 CNY 的商品”。
- 图片找货第一版可以先上传图片并显示后端解析摘要，后续再接 VLM 特征抽取。
- 购物车第一版可本地模拟，但价格/库存文案必须谨慎，不要宣称真实库存。

## Demo 优先级

1. 跑通文字需求到流式推荐。
2. 跑通商品卡片图片加载。
3. 跑通套装方案页。
4. 跑通图片附件解析摘要。
5. 再做购物车模拟。
