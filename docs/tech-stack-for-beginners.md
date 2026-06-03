# 技术栈速读

这份文档给第一次接手项目的同学看。当前项目目标是“传统电商智能导购”，不是旧版 API 模型商城。

## FastAPI

后端入口是 `rag/api/recommendation_app.py`。它负责：

- 商品列表：`GET /api/products`
- 需求解析：`POST /api/review-requirement`
- 流式推荐：`GET /api/stream-recommend`
- 商品图片：`/product-images/{filename}`

启动：

```bash
python scripts/run_recommendation_api.py
```

## Pydantic

`rag/schemas/recommendation.py` 定义所有输入输出结构。重点模型：

- `RequirementSpec`：用户购物需求结构化结果。
- `ApiProduct`：传统电商商品。类名保留旧名字，但现在代表商品。
- `RecommendationPlan`：一套推荐方案。
- `RecommendationResult`：最终返回给客户端的完整结果。

Android 新代码优先使用 `product_id/title/brand/image_url`，不要继续依赖 `api_id/api_name/provider`。

## 数据集

原始压缩包：

```text
data/ecommerce_agent_dataset_供参考.zip
```

规范化结果：

```text
data/ecommerce_products/products.json
data/ecommerce_products/images/
```

重新导入：

```bash
python scripts/import_ecommerce_dataset.py
```

## RAG 与推荐

第一版推荐不强依赖向量库，使用结构化商品详情、FAQ、评价和规则评分即可跑通。

关键文件：

- `recommendation_pipeline.py`：解析“我想买什么”。
- `product_loader.py`：读取商品库。
- `scorer.py`：给商品打分。
- `package_builder.py`：组装三套方案。
- `cost_estimator.py`：汇总 SKU 总价。

评分维度：

```text
场景匹配 + 属性匹配 + 价格适配 + 评价口碑 + 上架状态 + SKU/图片完整度 + FAQ/详情证据
```

## SSE

`/api/stream-recommend` 返回 `text/event-stream`。客户端会收到：

```text
step
requirement
catalog
plans
guidance
result
done
```

Android 可以用 OkHttp 自己解析 SSE，也可以先用普通同步 `/api/recommend` 跑通页面。

## Milvus

Milvus 目前默认关闭：

```text
RECOMMENDATION_ENABLE_MILVUS=false
```

后续如果要增强 RAG，可把商品详情、FAQ、评价写入向量库，再让 `retrieval.py` 做证据召回。当前最小闭环不需要它。

## 测试

```bash
pytest
```

集成测试默认跳过：

- `RUN_MILVUS_TESTS=1`
- `RUN_PDF_TESTS=1`

日常开发主要看：

- `tests/test_data_integrity.py`
- `tests/test_ecommerce_dataset_import.py`
- `tests/test_recommendation_llm.py`
- `tests/test_recommendation_app.py`
