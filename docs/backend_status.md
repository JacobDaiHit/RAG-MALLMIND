# MallMind 后端链路自检报告

本文档基于当前仓库代码与脚本现状整理，目标是帮助比赛交付前判断后端主链路是否已经收束。内容以代码实际行为为准，不把调试接口、兼容接口和未来计划混写成“已完成能力”。

## 1. 当前后端主入口

当前正式推荐的主链路是：

- `POST /api/chat/stream`

该接口负责：

- runtime mode 选择
- tool router 路由
- 附件预处理
- 普通商品推荐 / 对比 / 购物车 / PC 装机 handler 分发
- SSE 流式输出

当前仍保留的相关接口：

- `POST /api/chat`
  - 仍保留
  - 用途：legacy 兼容，返回旧的非流式结构
  - 不应作为新客户端主入口
- `POST /api/recommend`
  - 仍保留
  - 用途：非流式测试 / smoke check / 单次完整返回
  - 不是主对话入口
- `GET /api/stream-recommend`
  - 仍保留
  - 用途：调试 `RecommendationGraph` 的事件流
  - 不应作为正式业务入口
- legacy 兼容逻辑
  - 仍存在于 `rag/api/routes/legacy_chat_compat.py`
  - 仅建议用于兼容旧脚本、测试或调试

结论：主链路已经基本收束到 `POST /api/chat/stream`。

## 2. SSE 事件链路

`POST /api/chat/stream` 当前实际可能返回的事件类型包括：

- `runtime_mode`
- `tool_call`
- `delta`
- `progress`
- `attachment_analysis`
- `validation_error`
- `intent_route`
- `product_cards`
- `candidate_scope`
- `comparison_table`
- `follow_up_questions`
- `result`
- `cart`
- `pc_build_plan`
- `done`

说明：

- 普通商品推荐链路会覆盖大部分事件。
- 购物车链路通常返回 `delta`、`cart`、`done`。
- 对比链路会返回 `intent_route`、`comparison_table`、`result`、`done`。
- PC 装机链路会返回 `intent_route`、若干 `delta`、`pc_build_plan`、`done`。

## 3. 工具路由链路

当前 tool router 位于 `rag/recommendation/tool_router.py`，特征比较明确：

- 存在本地规则路由：有
- LLM 路由：可选
- guard / validate 纠偏：有

实际流程：

1. 先执行本地规则路由 `local_route_tool_call(...)`
2. 在允许时再尝试 LLM 路由 `try_llm_route_tool_call(...)`
3. 最终经过 `validate_and_guard_tool_call(...)` 做后端纠偏
4. 路由结果写入 `routing_trace`

当前能路由到的能力：

- 普通商品推荐：稳定链路
- 商品对比：稳定链路
- 购物车操作：稳定链路
- PC 整机方案：稳定链路
- 单个 PC 配件查询：稳定链路，但本质仍走商品推荐 handler，`catalog_scope=pc_parts`
- 普通聊天：稳定链路
- 图片 / 附件分析：不是独立 tool name，而是推荐上下文增强链路

稳定性判断：

- 本地规则 + guard 校正属于稳定链路
- LLM 路由是可选增强，不是主依赖
- 因为路由最终会被 guard 覆盖，所以主业务面不依赖“让 LLM 自己决定”

## 4. RAG / Milvus 链路

商品 chunk 构建：

- 入口：`rag/ingestion/product_chunks.py`
- 数据来源：普通商品库 + JD PC 配件库
- chunk 类型包括：
  - `profile`
  - `sku`
  - `faq`
  - `review`

Milvus 支持情况：

- 支持 Milvus：是
- 支持 dense retrieval：是
- 支持 sparse retrieval：是
- 支持 hybrid retrieval：是
- 支持 rerank / postprocess / auto-merge：是，可按配置启用

当前检索策略：

- 优先尝试 hybrid retrieval
- 失败时回退到 dense retrieval
- query expansion 可选，包含 base / step-back / HyDE 变体

Milvus 关闭或异常时的降级：

- `RECOMMENDATION_ENABLE_MILVUS=false` 时直接禁用 Milvus 证据层
- Milvus 端口不可达时返回 `unavailable`
- collection 不存在时返回 `no_collection`
- 超时会返回 `timeout`
- 无论以上哪种情况，推荐主链路仍会回落到结构化商品库评分，不会直接中断

如何重建索引：

```bash
python scripts/index_ecommerce_products.py --rebuild
```

如何 dry-run：

```bash
python scripts/index_ecommerce_products.py --dry-run
```

如何确认 RAG 是否真的启用：

- 看 `/api/runtime/diagnostics`
- 看推荐结果 `trace.milvus_retrieval`
- 看 `runtime_mode` 事件里的 `use_milvus_retrieval`
- 看推荐阶段的 `progress` / `result.trace`

## 5. 普通商品推荐链路

数据来源：

- `data/ecommerce_products/products.json`
- API 层也会把 PC 配件按 scope 合并进统一 catalog，但普通商品推荐默认仍以 `ecommerce` scope 为主

商品卡片字段当前主要包括：

- `product_id`
- `title`
- `name`
- `brand`
- `category`
- `category_name`
- `sub_category`
- `price`
- `price_range`
- `currency`
- `image_url`
- `stock_status`
- `stock_quantity`
- `rating_avg`
- `review_count`
- `reason`
- `score`
- `source`
- `selected_sku_id`

避免编造商品 / 价格 / 库存的方式：

- 推荐结果只从本地 catalog 中挑选
- 商品卡片由 `package_builder.py` 基于已有结构化数据组装
- Milvus 只作为证据增强层，不负责凭空生成商品
- Milvus 关闭时仍回到本地结构化评分，不会改成“自由生成卡片”

多轮上下文：

- 支持
- 依赖 `session_state.py`
- 会记录推荐历史、tool call、topic memory、购物车和 PC 方案上下文

对比 / 购物车：

- 支持
- 对比可由主对话路由触发，也有独立接口
- 购物车可由主对话触发，也保留独立动作接口

## 6. PC 整机推荐链路

数据来源与定位：

- 主路径：`data/jd_pc_products/products.json`
- 还有辅助 `parts.json` / `manifest.json`
- 这是本地 Demo seed，不是实时京东库存源

图片情况：

- PC 配件当前不提供商品图片，以文本规格卡片展示

结构化兼容性硬校验：

- 已实现
- 代码在 `rag/recommendation/pc_compatibility.py`

当前已检查的兼容性包括：

- CPU / 主板 socket
- 内存类型
- 主板版型 / 机箱
- 显卡长度 / 机箱
- 散热器高度 / 机箱
- 冷排尺寸 / 机箱
- 散热器 socket 支持
- 散热能力 / CPU TDP
- 电源功率
- CPU 无核显且无独显
- Wi-Fi 偏好告警

当前仍需继续完善的字段面：

- 不同品牌规格字段的一致性仍依赖数据集质量
- 某些兼容性检查的边界仍取决于 `standardized_specs` 完整度
- 机箱、电源、风道、接口等更细颗粒度规则还有扩展空间

## 7. 多模态 / 附件链路

当前支持情况：

- 支持附件类型：图片
- 不支持：PDF 等其他文件类型
- 是否依赖 VLM：是，视觉解析能力依赖 `VISION_MODEL` 或 `MULTIMODAL_MODEL`

实际行为：

- 若运行模式关闭 vision，会返回 `vision_skipped_by_runtime_mode`
- 若视觉模型未配置，会保留图片元数据并返回显式降级状态
- 图片分析结果会注入推荐上下文，用于 goal 理解与可选图片召回

定位判断：

- 图片找货 / 图片导购属于实验性增强能力
- 普通文本导购仍是主链路
- 不能把 PC 整机推荐包装成“图片推荐能力”
- 由于 PC 配件当前无商品图片，PC 方案链路本身不应被表述成图片找货

## 8. 运行模式

当前运行模式定义在 `rag/recommendation/runtime_mode.py`：

- `auto`
- `fast`
- `balanced`
- `full`

实际策略：

- `auto` 会在策略层归一到 `balanced`
- 测试环境、LLM 未配置或系统降级时，会优先落到 `fast`

各模式能力：

- `fast`
  - LLM：关闭
  - Milvus：关闭
  - vision：关闭
  - 适合：本地快速演示、测试、弱依赖环境
- `balanced`
  - router LLM：关闭
  - requirement LLM：仅在 LLM 已配置时开启
  - guidance LLM：关闭
  - Milvus：开启
  - vision：关闭
  - 适合：普通导购主链路
- `full`
  - router LLM：开启
  - requirement LLM：开启
  - guidance LLM：开启
  - Milvus：开启
  - vision：开启
  - query expansion：开启
  - 适合：带图片、需要更强解释或更深检索的演示

默认模式：

- 外部请求默认是 `auto`
- 当前系统通常会选择 `balanced`

## 9. 部署状态

本地运行命令：

```bash
python scripts/run_recommendation_api.py
```

或：

```bash
uvicorn rag.api.recommendation_app:app --reload --host 0.0.0.0 --port 8000
```

环境变量：

- `HOST` / `PORT`：可配置
- `APP_ENV`：可配置
- `DATABASE_URL`：生产环境下必须显式设置
- `REDIS_URL`：可选
- `MILVUS_HOST` / `MILVUS_PORT` / `MILVUS_COLLECTION`：可选增强
- LLM / VLM 相关 key 和 model：按部署目标配置

中间件 / 存储是否必需：

- Redis：可选
- Milvus：可选
- PostgreSQL：当前不是所有模式都强依赖，但如果部署链路要用数据库，生产必须显式配置

Android 后续对接建议：

- base URL：部署后的后端服务地址
- 主接口：`POST /api/chat/stream`

## 10. 已知风险和下一步

当前真实风险：

- README 之前明显滞后，本次已按代码重写
- PC 配件数据是 Demo seed，不代表实时京东库存
- PC 配件当前无图片
- Web 前端是调试台，后续仍需要原生 Android 客户端
- RAG / Milvus 需要部署时显式启用并重建索引
- `/api/chat`、`/api/recommend`、`/api/stream-recommend` 仍有兼容 / 调试属性
- shell 中出现的中文显示异常不一定是文件真实乱码，修改用户可见中文时应谨慎
- `legacy_chat_compat.py` 中存在较多旧兼容逻辑，继续长期演进前最好单独收口
- 图片找货能力当前更适合做增强演示，不宜当成核心主承诺

建议的下一步：

1. 比赛前固定一套运行模式和环境变量方案
2. 在目标部署环境中实际重建一次 Milvus 索引
3. 用 Android 原生端直接接入 `POST /api/chat/stream`
4. 继续清洗 PC 数据字段一致性与兼容性边界
