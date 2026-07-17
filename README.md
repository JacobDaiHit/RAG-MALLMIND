# MallMind V3

MallMind 是一个以本地商品目录为事实源的电商导购服务。当前生产入口只有 V3 链路：不再提供 `fast/balanced/full` 模式、8 工具 Router、旧 `/api/chat` 或 `/api/recommend` 兼容接口。

## 当前链路

```text
POST /api/chat/stream（纯文本）
  -> InputGuard + NormalizedTurn
  -> V3Router：完整 SafetyProof 才本地直通
  -> 否则本地生成 A/B/C 类型候选，并只调用一次 SemanticParse LLM
  -> ComputerPurchaseKindValidator / TypeResolutionGate / PromotionGate / ClarificationPlan / catalog scope reject
  -> CandidateGate（目录品类、类型排除、上架、库存、预算、品牌黑名单）
  -> V3 Milvus evidence retrieval（只在 allowlist 内检索）
  -> 目录事实排序 / 商品卡 / SessionDelta
```

对外动作分为六类：

- `recommend_shopping_products`：商品推荐。
- `parameter_query`：刚展示的商品卡参数、SKU 或价格事实查询。
- `apply_cart_instruction`：计划 → 用户确认 → 真实购物车变更。
- `general_chat`：非购物闲聊，不写任何业务状态。
- `generate_pc_build_plan`：按明确预算与用途生成首套兼容 PC 方案。
- `edit_pc_build_plan` / `compare_pc_build_plans`：只通过短期方案版本引用调整预算、替换单个配件或比较当前/上一套；不允许 LLM 填写配件 ID。

明确的“不要小米”会被统一为 `brand_family_id=xiaomi`，在目录 CandidateGate、Milvus 召回前 expression 和最终商品卡三处排除。LLM 不能输出可执行的 product ID、SKU ID、目录价格或库存。

## SemanticParse 契约

SemanticParse 使用紧凑目录能力表和本轮 A/B/C 类型候选，而不注入完整商品、SKU、品牌或目录类型表。模型只生成语义观察；目录词表、类型候选、原句证据、价格证据、CardRef、PC 方案版本和真实商品事实均由本地模块校验。`semantic-parse-v5` 增加 `computer_purchase_kind` 与原句 evidence；它不能直接写入 `RequirementSpecV3.product_type_ids`，也不能直接调用 PC 求解器。

- 明确的推荐/购买/寻找商品请求，即使对象不在目录中，也必须输出 `recommend_shopping_products` 与用户商品名；不能因为目录未覆盖而输出 `general_chat`；
- 若模型仍把“推荐 + 无目录类目”误标为 `general_chat`，路由层会进行确定性拒绝，返回 `catalog_scope_unsupported`；
- `general_chat` 仅用于不涉及购物、商品卡、购物车或 PC 装机的问候和知识聊天；
- 推荐类型必须先通过 TypeResolutionGate：目标/排除候选均要属于本轮 A/B/C 菜单并能回指原句；候选外 ID、伪造证据和目标/排除冲突均澄清或拒绝；
- 无候选且无目录正式精确匹配的明确商品名返回 `catalog_scope_unsupported`；
- 推荐链路中 CandidateGate 的 allowlist 为空时，同样返回 `catalog_scope_unsupported`；若 allowlist 非空但 Milvus 无证据，则按目录事实降级，不能伪装为目录范围问题；
- 普通“加入购物车”缺少目标商品卡时返回 `cart_target_unresolved`，不误报为目录范围问题。
- “送礼物”“比较这两个”等具有商品意图却缺少可执行字段的请求会写入短期 ClarificationPlan；下一轮只合并该计划，不把全文对话塞进模型。
- 首次说“电脑/主机 + 用途 + 预算”并不等于要装机：`computer_purchase_kind=unknown` 时先问清是笔记本、成品台式机还是按预算装机，且不调用 Milvus 或 PC 求解器；只在本轮原句明确出现“配/组/装一台、攒机、装机、DIY、配置单”等受控证据时才允许 `desktop_build -> generate_pc_build_plan`。
- 用户在该澄清后只答“配台主机”或“笔记本”时，SessionCore 只合并未过期的预算、用途和待确认购买形式；改问“推荐篮球鞋”等新话题不继承旧电脑条件。明确选择成品台式机但目录没有对应类型时，才返回 `catalog_scope_unsupported`。
- PC SessionCore 最多保存 `current/previous` 两份目录验证后的方案 ID、预算、用途和 TTL；显卡等单配件替换会锁定其余旧配件并重新跑兼容校验，无法在原预算内安全完成时明确返回无兼容方案。

图片附件暂未迁移到 V3；聊天入口会明确拒绝附件请求，绝不会回退到旧多模态链路。

## 启动

1. 创建 `.env`（不要提交密钥）：

```env
LLM_PROVIDER=dashscope
DASHSCOPE_API_KEY=your_dashscope_api_key_here
EMBEDDING_PROVIDER=dashscope
EMBEDDING_MODEL=text-embedding-v4
V3_RETRIEVAL_ENABLED=true
MILVUS_HOST=127.0.0.1
MILVUS_PORT=19530
MILVUS_V3_COLLECTION=mallmind_product_evidence_v3
MILVUS_V3_BM25_STATE_PATH=data/bm25_state_v3.json
SESSION_BACKEND=memory
# 生产环境：SESSION_BACKEND=redis，并配置 REDIS_URL
```

2. 启动 Milvus：

```powershell
docker compose up -d milvus-standalone
docker ps
```

确认容器健康且 `127.0.0.1:19530` 可访问。

3. 建立 V3 向量库。先 dry-run 检查，再全量重建：

```powershell
python scripts/index_ecommerce_products.py --v3 --dry-run
python scripts/index_ecommerce_products.py --v3 --rebuild --batch-size 10
python scripts/check_vector_index_health.py --collection mallmind_product_evidence_v3 --expected-count 884 --count-tolerance 0
```

`--v3` 会写入独立 collection `mallmind_product_evidence_v3`，同时写独立 BM25 状态 `data/bm25_state_v3.json`。不要用旧 collection 或旧 `data/bm25_state.json` 替代它；两者的稀疏向量坐标并不共用。

PC 切片会在 metadata 中写入 `canonical_product_key`；CandidateGate 也会按同一 key 保留一个目录商品，避免同一型号的 `_v2/_revN` 数据版本同时成为候选卡。更新 PC 数据或该规则后必须执行上述 `--rebuild`，不能向旧 collection 追加。

4. 启动 API：

```powershell
uvicorn rag.api.recommendation_app:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000/`。

## API

- `POST /api/chat/stream`：V3 SSE 聊天入口。
- `POST /api/cart/actions`：创建 V3 购物车待确认计划。
- `POST /api/cart/confirm`：确认或取消唯一未过期的计划。
- `POST /api/products/compare`：从当前本地目录读取多商品事实。
- `GET /api/health`：基础设施、模型和目录健康信息。

示例：

```json
{
  "session_id": "demo-1",
  "message": "推荐 5000 元以内、小米以外的手机，拍照优先"
}
```

## 测试

不配置外部模型即可运行 V3 合约测试：

```powershell
python -m pytest tests/test_v3_api.py tests/test_v3_cart.py tests/test_v3_pc_executor.py tests/test_v3_semantic_parse.py tests/test_v3_routing.py tests/test_v3_milvus_ingestion.py tests/test_embedding_sparse.py tests/test_milvus_writer_stability.py -q
```

这些测试检查 SafetyProof、语义提升、品牌别名/排除、澄清状态、卡片事实引用、购物车幂等确认、PC 求解输入以及 V3 Milvus 预过滤。外部 LLM 与 Milvus 的真实服务验证需要上述 `.env`、Docker 和索引都可用。
