# MallMind 项目目录结构

测试时间：2026-06-07

---

## 顶层文件

| 文件 | 作用 |
|------|------|
| `.editorconfig` | 编辑器统一编码/缩进配置 |
| `.env` | 运行时环境变量（LLM key、Milvus、Redis 等） |
| `.env.example` | `.env` 模板，供新开发者参考 |
| `.gitignore` | Git 忽略规则 |
| `README.md` | 项目说明文档 |
| `check_product_schema.py` | 校验商品 JSON schema 完整性 |
| `docker-compose.yml` | Docker 编排（Milvus、Redis 等） |
| `frontend-redesign-home.png` | 前端设计稿截图 |
| `pytest.ini` | pytest 全局配置 |
| `requirements.txt` | Python 依赖清单 |

---

## `rag/` — 后端核心模块

| 子目录/文件 | 作用 |
|-------------|------|
| `rag/__init__.py` | 包初始化 |
| `rag/api/` | FastAPI 路由层（`routes/`、SSE、请求模型、运行时上下文） |
| `rag/ingestion/` | 商品数据导入 + RAG chunk 构建 |
| `rag/legacy/` | 旧版兼容代码 |
| `rag/recommendation/` | **核心推荐引擎**（tool_router、scorer、pipeline、structured_filter、session_state、llm_client 等） |
| `rag/retrieval/` | Milvus 向量检索 + 混合召回 |
| `rag/schemas/` | Pydantic 数据模型（RequirementSpec、Product、ScoreBreakdown 等） |
| `rag/storage/` | 会话状态存储（内存/Redis） |
| `rag/utils/` | 工具函数（错误处理、目录作用域等） |

### `rag/recommendation/` 核心文件

| 文件 | 作用 |
|------|------|
| `tool_router.py` | 路由层：本地规则 + LLM 路由 + guard 兜底 |
| `tool_handlers.py` | 工具执行：recommend / cart / compare / general_chat / pc_build |
| `recommendation_pipeline.py` | 主 pipeline：需求解析 → 检索 → 打分 → 排序 → 返回 |
| `structured_filter.py` | 结构化过滤（品类、价格、品牌排除等硬约束） |
| `scorer.py` | 多维打分（场景匹配、属性、价格、口碑等） |
| `package_builder.py` | 构建推荐结果卡片 + 对比表 |
| `llm_client.py` | OpenAI-compatible LLM 客户端（SensNova/DeepSeek/Ark） |
| `session_state.py` | 会话状态管理（购物车、历史推荐、多轮上下文） |
| `query_guards.py` | 查询守卫（缺品检测、预算越界、品类缺失等） |
| `explanation_builder.py` | 推荐理由生成（基于证据的解释） |

---

## `scripts/` — 工具脚本

| 文件 | 作用 |
|------|------|
| `eval_model_chain_ablation.py` | 消融评估（fast/rag/llm/full 各组对比） |
| `eval_full_chain_ablation.py` | 全链路消融评估 |
| `eval_user_scenarios.py` | 用户场景评估（单轮/多轮/对比/购物车） |
| `eval_retrieval.py` | RAG 检索质量评估 |
| `check_llm_provider.py` | 检测 LLM provider 连通性 |
| `check_embedding_provider.py` | 检测 embedding provider 连通性 |
| `check_vector_index_health.py` | 检测 Milvus 索引健康度 |
| `check_catalog.py` | 校验商品目录完整性 |
| `expand_product_catalog.py` | 扩充商品目录数据 |
| `import_ecommerce_dataset.py` | 导入电商数据集 |
| `build_pc_parts_dataset.py` | 构建 PC 配件数据集 |
| `validate_pc_dataset.py` | 校验 PC 数据集完整性 |
| `index_ecommerce_products.py` | 建立 Milvus 商品索引 |
| `rebuild_product_vector_index.py` | 重建向量索引 |
| `revert_products.py` | 回滚商品数据 |

---

## `tests/` — 测试代码

### 核心测试文件

| 文件 | 作用 |
|------|------|
| `test_agent_v1.py` | V1 交互式 Agent 测试（60 用例，基础对话/搜索/FAQ/评价/购物车） |
| `test_agent_v2.py` | V2 扩展测试（72 用例，品牌/排序/多轮/防幻觉/复合场景） |
| `test_agent_v1_combined.py` | V1 合并测试 |
| `test_agent_v1_supplement.py` | V1 补充测试 |
| `test_fail_cases.py` | **FAIL/PARTIAL 案例回归测试**（25 条） |
| `verify_fail_cases.py` | FAIL 案例验证脚本（SSE 解析 + 判定逻辑） |
| `verify_fix_43_59.py` | 修复验证脚本 |
| `test_tool_router.py` | 路由层单元测试 |
| `test_recommendation_app.py` | 推荐应用集成测试 |
| `test_recommendation_llm.py` | LLM 推荐流程测试 |
| `test_retrieval_eval.py` | 检索评估测试 |
| `test_adaptive_runtime.py` | 自适应运行时测试 |
| `test_runtime_mode.py` | 运行时模式测试 |
| `test_runtime_mode_api.py` | 运行时模式 API 测试 |
| `test_capability_challenge_eval.py` | 能力挑战评估测试 |
| `test_eval_model_chain_ablation.py` | 消融评估测试 |
| `test_session_context_memory.py` | 会话上下文记忆测试 |
| `test_session_state_store.py` | 会话状态存储测试 |
| `test_llm_provider_config.py` | LLM provider 配置测试 |
| `test_evidence_grounded_explanation.py` | 证据解释测试 |
| `test_multimodal_eval.py` | 多模态评估测试 |
| `test_image_retrieval.py` | 图片检索测试 |
| `test_embedding_provider.py` | Embedding provider 测试 |
| `test_embedding_sparse.py` | 稀疏 embedding 测试 |
| `test_milvus_manager.py` | Milvus 管理器测试 |
| `test_milvus_pipeline.py` | Milvus pipeline 测试 |
| `test_milvus_writer_stability.py` | Milvus 写入稳定性测试 |
| `test_pc_build.py` | PC 装机方案测试 |
| `test_pc_compatibility.py` | PC 兼容性测试 |
| `test_pc_dataset_validation.py` | PC 数据集校验测试 |
| `test_pc_parts_dataset_coverage.py` | PC 配件覆盖度测试 |
| `test_pc_build_recommendation.py` | PC 推荐测试 |
| `test_data_integrity.py` | 数据完整性测试 |
| `test_ecommerce_dataset_import.py` | 数据导入测试 |
| `test_product_chunks.py` | 商品 chunk 测试 |
| `test_project_structure.py` | 项目结构测试 |
| `test_backend_refactor_boundaries.py` | 后端重构边界测试 |
| `test_legacy_chat_cases.py` | 旧版聊天兼容测试 |
| `test_runtime_error_sanitization.py` | 运行时错误脱敏测试 |
| `test_retrieval_resilience.py` | 检索弹性测试 |

### Debug / 工具脚本

| 文件 | 作用 |
|------|------|
| `debug_api_flow.py` | API 流程调试 |
| `debug_exact.py` | 精确调试 |
| `debug_pipeline.py` | Pipeline 调试 |
| `debug_pipeline_llm.py` | Pipeline LLM 调试 |
| `debug_raw_events.py` | 原始事件调试 |
| `debug_session.py` | 会话调试 |
| `debug_single.py` | 单条用例调试 |
| `debug_sse.py` | SSE 流调试 |
| `run_batch.py` | 批量运行测试 |
| `quick_test59.py` | 快速测试 59 条 |
| `check_budget.py` | 预算解析检查 |
| `check_cats.py` | 品类检查 |
| `check_prices.py` | 价格检查 |
| `check_product_structure.py` | 商品结构检查 |
| `test_budget_fix.py` | 预算修复测试 |

### Fixtures

| 文件 | 作用 |
|------|------|
| `fixtures/capability_challenge_eval_cases.json` | 能力挑战评估用例集（43 条） |
| `fixtures/full_chain_eval_cases.json` | 全链路评估用例集 |

---

## `data/` — 数据目录

| 子目录/文件 | 作用 |
|-------------|------|
| `ecommerce_products/` | 电商商品 JSON 数据 |
| `jd_pc_products/` | 京东 PC 配件数据 |
| `jd_pc_parts_pipeline.py` | PC 配件数据处理脚本 |
| `parts.json` | PC 配件清单 |
| `parts_manifest.json` | 配件清单索引 |
| `image_vectors.json` | 商品图片向量 |
| `bm25_state.json` | BM25 索引状态 |
| `milvus/` | Milvus 数据目录 |

---

## `reports/` — 评估报告

| 文件 | 作用 |
|------|------|
| `README.md` | 评测报告索引，以及报告与 `tests/`、`scripts/` 的对应关系 |
| `rag_router_eval_report_20260710_123554.md` | 最新 RAG 检索与 LLM 路由合并分析 |
| `rag_router_eval_20260710.json` | 最新 RAG 检索与 LLM 路由原始评测数据 |

---

## `docs/` — 文档

| 文件 | 作用 |
|------|------|
| `backend_status.md` | 后端状态报告 |
| `onboarding-architecture.md` | 入门架构文档 |
| `project-audit.md` | 项目审计 |
| `session_state_Q.md` | 会话状态设计文档 |
| `pc-build-rules.md` | PC 装机规则 |
| `pc-build-challenges.md` | PC 装机挑战 |
| `multimodal-evaluation-and-resilience.md` | 多模态评估文档 |
| `android-native-app-plan.md` | Android 原生 App 计划 |
