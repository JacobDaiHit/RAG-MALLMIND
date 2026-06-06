# MallMind

MallMind 是一个面向比赛演示的电商智能导购 Demo，后端基于 FastAPI，围绕本地商品库、流式对话、结构化推荐、可选 RAG 检索与 PC 装机链路组织。当前仓库的主交付物是后端服务与 Web 调试台；Android 原生客户端仍属于后续计划，不在本仓库内。

## 当前核心能力

- 自然语言导购对话：已实现。主入口为 `POST /api/chat/stream`。
- SSE 流式返回：已实现。可持续输出进度、文本、商品卡片、对比表、购物车、PC 方案等事件。
- 普通商品推荐：已实现。基于本地商品库做结构化筛选与评分，不编造商品卡片。
- 商品对比：已实现。支持推荐结果内对比，也支持独立 `POST /api/products/compare`。
- 购物车闭环：已实现。支持流式对话路由到购物车，也保留 `POST /api/cart/actions`。
- PC 整机配置方案：已实现。使用本地 PC 配件数据集生成结构化方案。
- PC 兼容性硬校验：已实现。包含 socket、内存类型、机箱兼容、散热兼容、电源功率、显卡输出等检查。
- Milvus / RAG 商品证据检索：已实现为可选增强层。未启用或不可达时会降级到结构化商品库评分。
- 多轮会话状态：已实现。用于购物车、推荐追问、PC 装机连续调整。
- 图片附件分析：已实现为可选能力。当前仅接收图片附件，依赖视觉模型配置。
- 图片找货：已实现为实验性增强。通过本地 `data/image_vectors.json` 做商品图相似召回。
- Web 调试台：已实现。用于后端调试和比赛演示，不是最终原生客户端。

## 系统主链路

```text
用户输入 / 图片附件
  -> FastAPI POST /api/chat/stream
  -> runtime mode 选择（auto -> fast / balanced / full）
  -> tool router（本地规则优先，可选 LLM 路由，再经过 guard 校正）
  -> 对应 handler
     - 普通商品推荐
     - 商品对比
     - 购物车操作
     - PC 整机方案
     - 普通聊天
  -> 可选附件分析 / 可选 Milvus 检索 / 结构化评分 / 兼容性校验
  -> SSE 返回 delta、progress、product_cards、comparison_table、pc_build_plan、cart、done 等事件
```

后续 Android 原生端应直接对接 `POST /api/chat/stream`，而不是旧的兼容接口。

## 后端接口

主要接口如下：

- `POST /api/chat/stream`：当前主链路，客户端首选接入点。
- `POST /api/chat`：legacy 兼容接口，返回旧的非流式结构。
- `POST /api/recommend`：非流式测试 / smoke check 接口，不是主业务入口。
- `GET /api/stream-recommend`：图式推荐调试流，不建议客户端接入。
- `GET /health`
- `GET /api/health`
- `GET /api/runtime/diagnostics`：仅 debug 或带管理员 token 时开放。
- `GET /api/llm/diagnose`
- `GET /api/products`
- `GET /api/products/{product_id}`
- `POST /api/products/compare`
- `POST /api/cart/actions`
- `POST /api/pc-build/generate`
- `POST /api/analyze-attachments`

兼容 / 调试接口说明：

- `/api/chat`：保留给旧脚本、测试和兼容调用。
- `/api/recommend`：保留给单次完整推荐返回场景。
- `/api/stream-recommend`：主要用于观察 `RecommendationGraph` 事件，不应作为正式对话入口。

## SSE 事件协议

`POST /api/chat/stream` 当前会按代码实际输出以下事件类型：

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

不同 handler 不会输出全部事件。例如购物车链路通常只返回 `delta`、`cart`、`done`。

## 数据集说明

### 普通电商商品库

- 主路径：`data/ecommerce_products/products.json`
- 由本地结构化商品数据组成
- 包含商品基础信息、SKU、FAQ、评论、价格、库存、图片路径等字段
- 商品卡片会从真实本地数据中组装，不会为不存在的商品补造标题、价格或库存

### PC 配件数据

- 主路径：`data/jd_pc_products/products.json`
- 还包含若干 `parts.json` / `manifest.json` 等辅助数据
- 覆盖 CPU、主板、显卡、内存、存储、电源、机箱、散热器等类型
- 当前 PC 配件不提供商品图片，展示时以文本规格卡片 / 方案明细为主
- 价格是 Demo 标价，不代表实时京东价格
- 不依赖实时 JD 页面，也不应被解释成实时库存系统
- 规格字段来自本地结构化数据，推荐链路不会自行编造兼容性参数

## RAG / Milvus

- 商品 chunk 构建入口：`rag/ingestion/product_chunks.py`
- 索引脚本：`python scripts/index_ecommerce_products.py --dry-run`
- 重建索引：`python scripts/index_ecommerce_products.py --rebuild`
- 当前支持 dense / sparse / hybrid retrieval
- `MilvusManager.hybrid_retrieve(...)` 使用 dense + sparse 检索融合
- 检索后可选 rerank / auto-merge / postprocess
- query expansion 仅在相应 runtime mode 与配置开启时使用
- Milvus 未启用、不可达、无 collection 或超时时，会降级到本地结构化商品库评分

可用的验证方式：

- `GET /api/runtime/diagnostics`
- 推荐结果 `trace`
- `/api/chat/stream` 的 `runtime_mode`、`progress`、`result.trace`

## 运行方式

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

```powershell
copy .env.example .env
```

至少需要关注：

```env
APP_ENV=development
HOST=0.0.0.0
PORT=8000

MODEL=
BASE_URL=
OPENAI_API_KEY=

RECOMMENDATION_STREAM_USE_LLM=false
RECOMMENDATION_ENABLE_MILVUS=false
RECOMMENDATION_QUERY_EXPANSION=false

VISION_MODEL=
MULTIMODAL_MODEL=
DATABASE_URL=
REDIS_URL=redis://localhost:6379/0
```

说明：

- 不配 LLM 时，系统仍可跑 `fast` 路径。
- `APP_ENV=production` 时，`DATABASE_URL` 现在必须显式设置。
- Redis、Milvus、PostgreSQL 都不是所有模式下的硬前置，但生产部署要按实际链路补齐。

### 3. 启动后端

启动前请先激活虚拟环境

```bash
d:\github\.venv\Scripts\Activate.ps1
```

项目内已有脚本：

```bash
python scripts/run_recommendation_api.py
```

也可以直接：

```bash
uvicorn rag.api.recommendation_app:app --reload --host 0.0.0.0 --port 8000
```

或者
```bash
$env:PORT="8011"; $env:HOST="127.0.0.1"; python scripts\run_recommendation_api.py  
```

其中：

- `HOST` 可配置
- `PORT` 可配置
- `scripts/run_recommendation_api.py` 默认端口是 `8011`
- `uvicorn` 示例默认端口是 `8000`

### 4. 打开 Web 调试台

```text
http://127.0.0.1:8000/
```

或脚本默认端口：

```text
http://127.0.0.1:8011/
```

## 数据校验与索引构建

项目当前已有这些脚本：

```bash
python scripts/validate_pc_dataset.py --strict
python scripts/index_ecommerce_products.py --dry-run
python scripts/index_ecommerce_products.py --rebuild
```

说明：

- `validate_pc_dataset.py` 会检查配件覆盖、兼容字段和 PC 图片残留字段
- `index_ecommerce_products.py --dry-run` 只构建 chunk，不写入 Milvus
- `--rebuild` 会重建集合与 BM25 状态，适合重新发版前执行

## 测试

项目里测试分为“稳定回归测试”和“业务评估报告”两类。前者用于 CI 判断代码是否崩，后者用于暴露推荐链路、数据集和能力边界，不一定要求全部 case 通过。

### 1. 快速本地回归

用于开发时确认核心 Python 单测是否通过。建议显式指定 `--basetemp`，避免 Windows 默认临时目录权限问题。

```bash
python -m pytest tests -q --basetemp .pytest_tmp/pytest_all
```

如果只想验证新增的典型用户场景评估脚本结构：

```bash
python -m pytest tests/test_user_scenarios_eval.py -q --basetemp .pytest_tmp/user_scenarios
```

### 2. 数据集校验

用于确认本地 PC 配件数据、兼容字段和基础数据结构没有破坏。适合 CI 和发版前执行。

```bash
python scripts/validate_pc_dataset.py --strict
```

### 3. 商品索引 dry-run

用于确认普通电商商品能正确构建 RAG chunk，但不写入 Milvus。适合没有外部服务的本地检查。

```bash
python scripts/index_ecommerce_products.py --dry-run
```

需要重建 Milvus 索引时再运行：

```bash
python scripts/index_ecommerce_products.py --rebuild
```

### 4. 典型用户场景评估

用于覆盖比赛说明中的典型用户场景，包括单轮推荐、条件筛选、多轮细化、对比、主动澄清、反选约束、跨类目组合、购物车 CRUD 和多模态边界。该脚本会先做 catalog probe，把结果区分为业务失败、数据集缺口和能力边界。

默认命令不走外部大模型，`use_llm=False`，`runtime_mode=balanced`，适合稳定 CI：

```bash
python scripts/eval_user_scenarios.py --output-json reports/user_scenarios_eval.json --output-md reports/user_scenarios_eval.md
```

如果要测试 full 链路和外部大模型，可显式开启：

```bash
python scripts/eval_user_scenarios.py --use-llm --runtime-mode full --output-json reports/user_scenarios_eval.json --output-md reports/user_scenarios_eval.md
```

说明：

- `failed` 表示当前业务行为和验收口径不一致，通常需要修代码。
- `catalog_gap` / `budget_catalog_gap` 表示当前商品库缺品或预算内缺货，不应算普通代码错误。
- `capability_gap` / `capability_partial` 表示当前能力未实现或只实现了部分能力，例如真实图片语义理解。

### 5. RAG / 检索评估

用于评估商品召回、约束违例、catalog gap 和 negative case 的检索质量。默认更适合离线评估。

```bash
python scripts/eval_retrieval.py --output reports/retrieval_eval.json --markdown reports/retrieval_eval.md
```

如果要验证 Milvus 集合和向量索引健康：

```bash
python scripts/check_vector_index_health.py --output reports/vector_index_health.json
```

### 6. 模型链路消融评估

用于比较 fast / rag_only / balanced_demo / full 等模式下，路由、RAG、LLM 解析和最终链路的贡献。依赖 Milvus 和 LLM 环境，适合在外部服务就绪后运行。

**快速验证（推荐先跑，约 3 分钟，12 case × 3 组）：**

```bash
python scripts/eval_model_chain_ablation.py \
  --cases tests/fixtures/capability_challenge_eval_cases.json \
  --groups fast_baseline,rag_only,balanced_demo \
  --limit 12 \
  --output reports/capability_challenge_quick.json \
  --markdown reports/capability_challenge_quick.md
```

**核心 5 组中等规模（约 8 分钟，20 case × 5 组）：**

```bash
python scripts/eval_model_chain_ablation.py \
  --cases tests/fixtures/capability_challenge_eval_cases.json \
  --groups fast_baseline,rag_only,balanced_demo,router_llm_only,parse_llm_only \
  --limit 20 \
  --output reports/capability_challenge_20.json \
  --markdown reports/capability_challenge_20.md
```

**完整 9 组全量评估（约 30 分钟，40 case × 9 组）：**

```bash
python scripts/eval_model_chain_ablation.py \
  --cases tests/fixtures/capability_challenge_eval_cases.json \
  --groups all \
  --output reports/capability_challenge_eval.json \
  --markdown reports/capability_challenge_eval.md
```

**只跑单个 case 调试：**

```bash
python scripts/eval_model_chain_ablation.py \
  --cases tests/fixtures/capability_challenge_eval_cases.json \
  --case-id cap_synonym_commute_noise_beans \
  --groups fast_baseline,rag_only,balanced_demo \
  --output reports/capability_challenge_single.json \
  --markdown reports/capability_challenge_single.md
```

**禁用 router LLM 跑（隔离 RAG + parse 贡献）：**

```bash
python scripts/eval_model_chain_ablation.py \
  --cases tests/fixtures/capability_challenge_eval_cases.json \
  --groups balanced_demo --limit 12 \
  --disable-router-llm \
  --output reports/capability_challenge_no_router.json \
  --markdown reports/capability_challenge_no_router.md
```

关注指标（打开生成的 `.md` 报告）：
- `capability_eval` 表：`rag_top1_changed`（RAG 是否改变 top1）、`hit@5`/`p@1`（rag_only vs fast_baseline 差值）
- `llm_timeout` / `llm_json_invalid` / `llm_provider_error`（LLM 调用失败分类）
- `LLM diagnostics` 表：`router_failure` / `parse_failure` / `guidance_failure`（各环节失败原因）
- `vs_fast_delta` 表：`balanced_win`（balanced 相对 fast 的 top1 胜出 case）

### 7. 比赛演示前建议组合

无外部服务的稳定检查：

```bash
python scripts/validate_pc_dataset.py --strict
python scripts/index_ecommerce_products.py --dry-run
python scripts/eval_user_scenarios.py --output-json reports/user_scenarios_eval.json --output-md reports/user_scenarios_eval.md
python -m pytest tests -q --basetemp .pytest_tmp/pytest_all
```

接近线上增强链路的检查：

```bash
python scripts/index_ecommerce_products.py --rebuild
python scripts/check_vector_index_health.py --output reports/vector_index_health.json
python scripts/eval_user_scenarios.py --use-llm --runtime-mode full --output-json reports/user_scenarios_eval.json --output-md reports/user_scenarios_eval.md
python scripts/eval_model_chain_ablation.py --cases tests/fixtures/capability_challenge_eval_cases.json --groups all --output reports/capability_challenge_eval.json --markdown reports/capability_challenge_eval.md
```

部分能力若涉及 LLM、Milvus、Redis 或数据库连通性，是否完全通过会受本地环境影响。

## 部署说明

服务部署时建议明确配置：

- `HOST=0.0.0.0`
- `PORT`
- `APP_ENV`
- `DATABASE_URL`
- `REDIS_URL`
- `MILVUS_HOST`
- `MILVUS_PORT`
- `MILVUS_COLLECTION`
- `OPENAI_API_KEY` / `ARK_API_KEY`
- `BASE_URL`
- `MODEL`
- `VISION_MODEL` / `MULTIMODAL_MODEL`

补充说明：

- SSE 反向代理时需要关闭 response buffering。
- 生产环境不应依赖本地默认数据库连接串。
- Android 原生端后续应配置为直接访问部署后的 `/api/chat/stream`。

## Android 原生端计划

后续计划中的原生客户端方向为：

- Kotlin
- Jetpack Compose
- Retrofit / OkHttp
- ViewModel
- 直接对接 `POST /api/chat/stream`

当前仓库里的 Web 页面只是调试台，不是最终比赛主客户端。

## 已知边界

- Demo 数据不代表实时库存和实时价格。
- PC 配件当前无商品图片。
- 图片附件链路当前只接收图片，不接收 PDF 等其他附件。
- 某些 LLM 能力依赖环境变量，未配置时会自动降级。
- Milvus、Redis、PostgreSQL 是否必需取决于运行模式和部署目标。
- `/api/chat`、`/api/recommend`、`/api/stream-recommend` 仍保留用于兼容或调试。
- shell 中看到的中文显示异常不一定是文件本身乱码，排查时需要结合编码与脚本环境判断。

## 未来展望

- 增加 Kotlin 原生 Android App
- 完成服务器部署与稳定的外网 base URL
- 做更清晰的 RAG trace / diagnostics 面板
- 将商品数据接入更真实的库存 / 价格来源
- 继续清洗和扩充 PC 配件数据
- 将图片找货、语音输入等能力作为可选加分项继续完善

## 补充文档

- 后端链路自检报告：`docs/backend_status.md`
