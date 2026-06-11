# `rag/` 文件夹死代码审计报告

**日期：** 2026-06-11  
**范围：** `rag/` 目录只读分析（共 55 个 Python 文件）  
**方法：** 3 个并行 Explore 代理对每个定义进行全项目交叉引用（包括生产代码、测试、脚本和非 rag 模块），逐一定位死代码。

---

## 1. 总览

| 严重程度 | 数量 | 说明 |
|----------|------|------|
| **🔴 高** | 4 个死文件/包 | 整个模块零生产导入 |
| **🔴 高** | 1 条死导入链 | 3 层 遗留→shim→零生产使用 |
| **🟡 中** | 20+ 个死函数/方法 | 已定义但在生产中从未调用 |
| **🟡 中** | 3 个死导入 | `recommendation_app.py` 中未被引用的导入 |
| **🟡 中** | 3 条重复代码链 | 同一函数在 2–4 处定义 |
| **🟢 低** | 2 个仅离线使用的模块 | 只在批处理脚本中使用，运行中 API 不加载 |
| **🟢 低** | 7 个已导出但极少使用的项 | `__init__.py` 中零外部消费者或仅有测试引用的项 |

预估死代码行数（不含重复项）：**约 400 行**，分布在 6 个文件中。

---

## 2. 死文件与死包

### 2.1 `rag/retrieval/` — 整个包（🔴 高）

- **文件：** `rag/retrieval/__init__.py`
- **内容：** 仅一行 docstring：`"""Retrieval and vector-store management services."""`
- **状态：** 项目中**没有任何地方**导入 `rag.retrieval` 或 `rag.retrieval.*`——生产代码、测试、脚本中均无。
- **结论：** 从未实现的占位包。可以安全删除。

### 2.2 `rag/api/response_utils.py` — 整个文件（🔴 高）

- **文件：** `rag/api/response_utils.py`（33 行）
- **内容：**
  - `model_to_dict()`（第 5 行）——与 `rag/api/app_context.py:25` 中的规范版本重复
  - `sse_event()`（第 15 行）——与 `rag/api/sse.py:11` 中的规范版本重复
  - `validation_error_events()`（第 22 行）——无重复，但从未被调用
- **状态：** 项目中**没有任何地方**导入此文件。
- **结论：** 重构遗留产物。所有 3 个函数均不可达。可以安全删除。

### 2.3 `rag/legacy/tools.py` — 整个模块（🔴 高）

- **文件：** `rag/legacy/tools.py`（56 行）
- **内容：** `search_product_evidence`、`get_last_rag_context`、`_set_last_rag_context`、`reset_tool_call_guards`、`set_rag_step_queue`、`emit_rag_step`
- **状态：** 已自行标注为弃用（导入时发出 `DeprecationWarning`）。**零生产导入。** 整个项目中仅有的引用：
  1. `rag/utils/tools.py`（重导出 shim，本身也已死——见 §2.4）
  2. `tests/test_backend_refactor_boundaries.py` 第 27 行（仅检查 `hasattr(tools, "search_product_evidence")`）
- **结论：** 已弃用，无人使用。真正的实现位于 `rag/recommendation/tool_router.py` 和 `rag/recommendation/tool_handlers.py`。可以安全删除（如保留边界测试需更新）。

### 2.4 `rag/utils/tools.py` — 整个模块（🔴 高）

- **文件：** `rag/utils/tools.py`（8 行）
- **内容：** 仅一行重导出：`from rag.legacy.tools import *  # noqa: F401,F403`
- **Docstring：** *"此模块仅保留供旧脚本或实验使用。"*
- **状态：** 仅被 `tests/test_backend_refactor_boundaries.py` 第 25 行导入。无生产导入。
- **结论：** 死 shim。可与 `rag/legacy/` 一起安全删除。

---

## 3. 死函数与方法

### 3.1 `rag/recommendation/tool_router.py`

| 行号 | 名称 | 死因 |
|------|------|------|
| 133 | `ROUTED_CALL_SCHEMA`（模块常量） | 从未被其他模块导入或引用 |
| 847 | `normalize_tool_arguments()` | 从未被调用；Pydantic 验证器通过 `RoutedToolCall.model_validate()` 处理规范化 |
| 973 | `merge_route_arguments()` | 仅在 `tests/diag_mimo_raw.py` 中调用（测试专用，非生产） |
| 1163 | `_should_compare_products()` | 从未被调用；路由逻辑使用 `_looks_like_compare_request()` |

### 3.2 `rag/recommendation/cost_estimator.py`

| 行号 | 名称 | 死因 |
|------|------|------|
| 85 | `product_currency()` | 从未被调用 |
| 89 | `pricing_confidence()` | 从未被调用 |
| 93 | `pricing_rule()` | 从未被调用 |

### 3.3 `rag/recommendation/scorer.py`

| 行号 | 名称 | 死因 |
|------|------|------|
| 561 | `average()` | 仅内部使用的工具函数；未被任何外部模块导入 |
| 577 | `score_modality_fit()` | 代码库中从未被调用 |

### 3.4 `rag/recommendation/pc_types.py`

| 行号 | 名称 | 死因 |
|------|------|------|
| 80 | `legacy_pc_component_type()` | 被 `rag/recommendation/pc_build.py:15` 导入，但在任何函数体中**从未被调用**——导入语句为死代码 |

### 3.5 `rag/recommendation/pc_build.py`

| 行号 | 名称 | 死因 |
|------|------|------|
| 660 | `role_name()` | 单行包装函数；从未被调用；未从 `__init__.py` 导出 |

### 3.6 `rag/recommendation/session_state.py`

| 行号 | 名称 | 死因 |
|------|------|------|
| 89 | `InMemorySessionStore.set()` | 委托给 `save()`；从未被调用——所有调用者直接使用 `save()` |
| 191 | `reset_session()` | 仅在 `tests/test_session_state_store.py` 中调用（测试专用） |
| 199 | `clear_session()` | 从未被调用；无 API 端点会删除会话 |

### 3.7 `rag/recommendation/session_context.py`

| 行号 | 名称 | 死因 |
|------|------|------|
| 101 | `session_context_for_llm()` | 仅在 `tests/test_session_context_memory.py` 中调用（测试专用）；LLM 路由器通过 `_build_router_user_prompt()` 自行构建上下文 |

### 3.8 `rag/recommendation/recommendation_graph.py`

| 行号 | 名称 | 死因 |
|------|------|------|
| 78 | `RecommendationGraph.run()` | `stream()` 的同步替代方法；从未被调用 |

### 3.9 `rag/recommendation/package_builder.py`

| 行号 | 名称 | 死因 |
|------|------|------|
| 878 | `average()` | 仅内部使用的工具函数；与 `scorer.average()` 逻辑重复；未被外部导入 |

### 3.10 `rag/recommendation/recommendation_pipeline.py`

| 行号 | 名称 | 死因 |
|------|------|------|
| 1122 | `model_to_dict()` | 同一函数的第三份副本（规范版本位于 `app_context.py:25`）；仅在本文件内部使用 |

### 3.11 `rag/utils/rag_utils.py`

| 行号 | 名称 | 死因 |
|------|------|------|
| 147 | `retrieve_documents()` | 完整的 Milvus 检索封装（约 63 行）；从未被任何生产代码、脚本或测试调用。生产环境使用 `rag/recommendation/retrieval.py` 中的 `EvidenceRetriever` |

### 3.12 `rag/api/routes/common.py`

| 行号 | 名称 | 死因 |
|------|------|------|
| 25 | `has_image_data()` | 仅在测试文件中导入（`test_runtime_mode.py`、`test_runtime_mode_api.py`）；无路由使用 |
| 41 | `is_test_env()` | 同上——仅测试导入 |
| 46 | `system_degraded()` | **完全未被使用**，连测试中都没有 |

> **注意：** 此模块中有两个函数**正在被使用**：`request_product_ids()`（被 `chat.py`、`legacy_chat_compat.py` 导入）和 `stream_llm_enabled()`（被 `recommend.py`、`chat.py` 导入）。

---

## 4. 死导入

### 4.1 `rag/api/recommendation_app.py`

以下 3 个导入语句被引入，但在文件主体中**从未被引用**：

| 行号 | 导入名称 | 来源模块 |
|------|--------|---------------|
| 25 | `goal_with_attachment_context` | `rag.api.attachments` |
| 26 | `normalize_attachments` | `rag.api.attachments` |
| 38 | `parse_adjustment_amount` | `rag.recommendation.pc_session_flow` |

这些导入增加了不必要的启动时模块加载开销。

### 4.2 `rag/recommendation/pc_build.py`

| 行号 | 导入名称 | 状态 |
|------|--------|--------|
| 15 | 从 `rag.recommendation.pc_types` 导入 `legacy_pc_component_type` | 已导入但从未被调用（见 §3.4） |

---

## 5. 重复定义

### 5.1 `model_to_dict()` — 3 份副本

| 位置 | 状态 |
|----------|--------|
| `rag/api/app_context.py:25` | **规范版本** — 被 6+ 个文件导入 |
| `rag/api/response_utils.py:5` | **已死** — 位于死文件中（见 §2.2） |
| `rag/recommendation/recommendation_pipeline.py:1122` | **仅内部使用** — 返回类型注解略有不同；仅在本文件内使用 |

### 5.2 `dedupe_strings()` — 4 份副本

| 位置 | 状态 |
|----------|--------|
| `rag/api/app_context.py:90` | 活跃 — 被路由使用 |
| `rag/api/text_utils.py:4` | 活跃 — 被 `products.py` 使用 |
| `rag/recommendation/input_preprocessor.py:109` | 活跃 — 内部使用 |
| `rag/recommendation/recommendation_pipeline.py:1111` | 活跃 — 内部使用 |

全部四份副本均在活跃使用——建议合并为一个共享工具函数。

### 5.3 `_parse_positive_int()` — 2 份副本

| 位置 | 状态 |
|----------|--------|
| `rag/ingestion/embedding.py:30` | 被嵌入提供者使用 |
| `rag/storage/milvus_client.py:428` | 被 Milvus 客户端配置使用 |

---

## 6. 死导出链

### 6.1 3 层遗留代码链

```
rag/legacy/tools.py           ← 已弃用，0 生产导入
    ↓ （被重导出至）
rag/utils/tools.py            ← 重导出 shim，0 生产导入
    ↓ （唯一的消费者）
tests/test_backend_refactor_boundaries.py  ← 单个测试断言
```

替代此链条的真正实现位于：
- `rag/recommendation/tool_router.py` — LLM 辅助工具路由
- `rag/recommendation/tool_handlers.py` — 流式 SSE 处理器

**结论：** 整个链条在生产环境中已死。应同时删除 `rag/legacy/tools.py` 和 `rag/utils/tools.py`。

---

## 7. 仅离线使用的模块

以下文件**从未在运行中的 API 应用中加载**，仅由离线批处理脚本或测试导入：

| 文件 | 使用方 | API 启动时未加载 |
|------|---------|-------------------|
| `rag/ingestion/product_chunks.py` | `scripts/index_ecommerce_products.py`、`scripts/rebuild_product_vector_index.py`、1 个测试 | ✅ 已确认 |
| `rag/storage/milvus_writer.py` | 仅批处理摄入脚本和测试 | ✅ 已确认 |

这些并非严格意义上的“死代码”——它们服务于离线/索引工作流——但在 **API 运行时属于死代码**。建议考虑移至顶层的 `scripts/` 或 `ingestion/` 包中。

---

## 8. 已导出但极少使用的项

`rag/recommendation/__init__.py` 中零外部消费者或仅有测试引用的项：

| 导出项 | 来源 | 外部使用情况 |
|--------|--------|----------------|
| `recommend_shopping_bundle` | `recommendation_pipeline.py:270` | 仅 `scripts/recommend_api_stack.py` |
| `recommend_api_stack` | `recommendation_pipeline.py:264` | 仅 `tests/test_recommendation_llm.py` |
| `ProductCatalogError` | `product_loader.py:34` | 所有使用者直接从 `product_loader` 导入，而非通过 `rag.recommendation` |
| `ProductScore` | `scorer.py:25` | 仅在 `scorer.py` / `package_builder.py` 内部使用 |
| `BASE_WEIGHTS` | `scorer.py:13` | 仅在 `scorer.py` 内部使用 |
| `build_preprocessed_goal` | `input_preprocessor.py:84` | 仅 `tests/test_recommendation_llm.py` + 内部使用 |
| `clean_text` | `input_preprocessor.py:98` | 仅内部 + 测试使用 |

建议从公共 `__init__.py` API 中移除，以减少命名空间污染。

---

## 9. 清理候选清单

### 第一梯队：可直接删除（零风险）

| # | 项目 | 类型 | 行数 |
|---|------|------|-----|
| 1 | `rag/retrieval/` | 包 | ~3 |
| 2 | `rag/api/response_utils.py` | 文件 | ~33 |
| 3 | `rag/legacy/tools.py` | 文件 | ~56 |
| 4 | `rag/utils/tools.py` | 文件 | ~8 |
| 5 | `pc_types.py:80` 中的 `legacy_pc_component_type()` + `pc_build.py:15` 中的死导入 | 函数 + 导入 | ~5 |
| 6 | `pc_build.py:660` 中的 `role_name()` | 函数 | ~2 |
| 7 | `cost_estimator.py` 中的 `product_currency()`、`pricing_confidence()`、`pricing_rule()` | 3 个函数 | ~12 |
| 8 | `scorer.py:577` 中的 `score_modality_fit()` | 函数 | ~4 |
| 9 | `recommendation_graph.py:78` 中的 `RecommendationGraph.run()` | 方法 | ~28 |
| 10 | `session_state.py:199` 中的 `clear_session()` | 函数 | ~3 |
| 11 | `session_state.py:89` 中的 `InMemorySessionStore.set()` | 方法 | ~3 |
| 12 | `tool_router.py:133` 中的 `ROUTED_CALL_SCHEMA` | 常量 | ~12 |
| 13 | `tool_router.py:847` 中的 `normalize_tool_arguments()` | 函数 | ~20 |
| 14 | `tool_router.py:1163` 中的 `_should_compare_products()` | 函数 | ~13 |
| 15 | `recommendation_app.py` 中的死导入（第 25-26、38 行） | 3 个导入 | ~3 |

### 第二梯队：需要同步更新测试

| # | 项目 | 备注 |
|---|------|-------|
| 16 | `tool_router.py:973` 中的 `merge_route_arguments()` | 仅被 `tests/diag_mimo_raw.py` 使用——删除或移入测试文件 |
| 17 | `session_context.py:101` 中的 `session_context_for_llm()` | 仅被 `tests/test_session_context_memory.py` 使用 |
| 18 | `session_state.py:191` 中的 `reset_session()` | 仅被 `tests/test_session_state_store.py` 使用 |
| 19 | `routes/common.py` 中的 `has_image_data()`、`is_test_env()` | 仅被测试文件使用——确认测试中是否仍然必要 |

### 第三梯队：合并去重（减少冗余，非严格意义上的"死代码"）

| # | 项目 | 操作 |
|---|------|--------|
| 20 | `recommendation_pipeline.py:1122` 中的 `model_to_dict()` | 替换为 `app_context.model_to_dict` 导入 |
| 21 | `dedupe_strings()`（4 份副本） | 合并为一个共享工具函数 |
| 22 | `_parse_positive_int()`（2 份副本） | 合并为一个共享工具函数 |
| 23 | `scorer.py:561` 和 `package_builder.py:878` 中的 `average()` | 移至共享工具模块 |
| 24 | `rag_utils.py:147` 中的 `retrieve_documents()` | 移除或重新安置——无任何消费者 |

---

## 10. 验证方式

清理后验证无破坏性影响，请运行以下命令：

```bash
# 1. 完整测试套件
pytest tests/ -v

# 2. 导入检查——确认所有 rag 模块无错误加载
python -c "import rag; import rag.api; import rag.recommendation; print('OK')"

# 3. API 启动冒烟测试
python scripts/run_recommendation_api.py &
sleep 3
curl http://localhost:8000/api/health
kill %1
```

如果移除了第一梯队中的项目，以下测试可能需要更新：
- `tests/test_backend_refactor_boundaries.py` — 从 `rag.utils.tools` 导入并检查 `rag.legacy.tools` 属性
- `tests/diag_mimo_raw.py` — 调用 `merge_route_arguments()`
- `tests/test_session_context_memory.py` — 调用 `session_context_for_llm()`
- `tests/test_session_state_store.py` — 调用 `reset_session()`
- `tests/test_runtime_mode.py`、`tests/test_runtime_mode_api.py` — 导入 `has_image_data()`、`is_test_env()`

---

*报告完。*
