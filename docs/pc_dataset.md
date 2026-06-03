# PC 配件数据与整机推荐说明

`data/jd_pc_products/` 是比赛 Demo 使用的可审计结构化 PC 配件样例库，不是实时京东库存、价格或图片接口。当前 PC 配件数据不含真实商品图片，因此 PC 整机推荐以文本卡片、规格字段、兼容性检查和推荐理由展示。

普通电商商品库已有图片字段可以继续使用；本说明只约束 PC 配件数据和 PC 整机推荐链路。

## 字段约定

- `id` / `part_id`: 本地样例库中的配件标识。
- `component_type` / `part_type`: 统一映射到 `pc_cpu`、`pc_gpu`、`pc_motherboard`、`pc_memory`、`pc_storage`、`pc_psu`、`pc_case`、`pc_cooler`。
- `title`、`brand`、`model`: 展示和推荐理由中的商品名称、品牌、型号，必须来自本地数据。
- `price_cny` / `price`: Demo 标价，`currency` 当前为 `CNY`，不代表实时售价。
- `standardized_specs` / `specs`: 结构化兼容性字段。
- `selling_points`、`limitations`、`recommendation_text`: RAG chunk 和推荐解释使用的文本证据。

关键兼容字段包括：

- CPU: `socket`、`cores`、`threads`、`tdp_w`、`integrated_graphics`。
- 主板: `socket`、`form_factor`、`memory_type`、`wifi`。
- 内存: `memory_type`、`capacity_gb`、`speed_mhz`。
- 显卡: `length_mm`、`power_w`、`recommended_psu_w`。
- 电源: `wattage_w`。
- 机箱: 支持主板板型、显卡限长、CPU 散热限高、电源限长、水冷冷排支持。
- 散热器: 支持 socket、`cooler_type`、`cooling_capacity_w`；风冷需要高度，水冷需要冷排尺寸。

## 校验与索引

```bash
python scripts/validate_pc_dataset.py
python scripts/validate_pc_dataset.py --strict
python scripts/validate_pc_dataset.py --json
python scripts/validate_pc_dataset.py --root data/jd_pc_products
```

校验报告写入：

```text
data/reports/pc_dataset_report.json
```

重建商品证据索引：

```bash
python scripts/index_ecommerce_products.py --rebuild
```

比赛前快速验收可先跑：

```bash
python scripts/index_ecommerce_products.py --dry-run
pytest tests/test_pc_dataset_validation.py tests/test_pc_compatibility.py tests/test_pc_build_recommendation.py
```

## 推荐链路

PC 整机推荐采用“结构化硬兼容校验 + RAG 证据解释”：

1. 从本地 PC 配件库读取候选。
2. 统一组件类型并去除 V2/V3 等近重复主推荐候选。
3. 对每套候选执行硬兼容校验。
4. 硬校验通过后，再按预算、用途、性能、外观、低噪、升级空间和证据完整度做软评分。
5. 返回 `pc_build_plan.parts`、`compatibility`、`evidence` 和 `trace`。

LLM 不负责判断硬兼容。LLM 不可用时，规则链路仍可生成基础整机方案。推荐结果中的标题、品牌、型号、价格和规格来自本地数据。

## 图片策略

PC 配件不返回 `image_url`、`screenshot_path`、`screenshots`，也不生成占位图片路径。前端和后续 Android 应按文本卡片、规格摘要、兼容性检查结果展示 PC 配件。

普通电商商品的图片能力保留。

## 已知边界

- 价格为 Demo 标价，不代表实时售价。
- PC 配件暂不提供商品图片。
- 部分 `product_url` / `sku` 可能为空。
- `manifest.json` 中可能保留历史生成记录，运行时加载器不依赖 Windows 绝对路径。
- 真实生产环境需要接入实时商品、库存、价格、图片和售后接口。
