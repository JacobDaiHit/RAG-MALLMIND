# Data Directory

当前只保留传统电商导购数据。

```text
ecommerce_agent_dataset_供参考.zip  原始课题商品数据压缩包
ecommerce_products/products.json    规范化商品目录，共 100 个商品
ecommerce_products/images/          商品主图，共 100 张
ecommerce_products/manifest.json    导入统计
```

旧 API 商城数据已经下架删除，包括 `api_products`、`api_docs`、`price_rules`、旧 `raw` 文档和旧 Milvus 本地状态。

重新生成规范化商品目录：

```bash
python scripts/import_ecommerce_dataset.py
```








