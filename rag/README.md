# `rag/` 当前目录说明

这里不是“旧 RAG 模块的集合”，而是当前 MallMind V3 后端的全部业务代码。请求入口固定为 `api/recommendation_app.py`，推荐执行权固定在 `recommendation/v3/`；没有第二套 Router、旧 Requirement Builder 或旧检索 pipeline。

```text
rag/
├── api/                 HTTP、SSE 和产品/反馈辅助接口
│   ├── recommendation_app.py  FastAPI ASGI 入口、启动预热、健康检查
│   ├── routes/chat.py         /api/chat/stream、购物车确认、商品卡比较
│   └── attachments.py         保留的多模态观察工具；当前 V3 明确拒绝附件，尚未接入
├── ingestion/           目录切片、dense embedding、BM25 状态
├── recommendation/
│   ├── product_loader.py      商品/PC 目录事实唯一读取入口
│   ├── pc_build.py            PC 兼容求解器
│   ├── session_state.py       Redis/内存运行时 SessionCore 存储适配器
│   └── v3/                    唯一的导购链路
│       ├── config.py          集中、版本化的规则和策略表
│       ├── registry.py        从目录构建品牌/品类 canonical 词表
│       ├── router.py          SafetyProof 本地直通判定
│       ├── semantic_parse.py  一次外部 Chat 语义观察
│       ├── promotion.py       hard constraint 提升为 RequirementSpecV3
│       ├── candidate_gate.py  检索前真实目录 allowlist
│       ├── retrieval.py       allowlist 内 Milvus 证据检索
│       ├── *_executor.py      商品事实、推荐和 PC 方案执行
│       └── session.py         轻量多轮状态及 SessionDelta
├── schemas/             目录 JSON 的 Pydantic 事实模型，不放路由状态
├── security/            提示注入检测与安全包装
├── storage/             Milvus 连接与写入器
└── utils/               仅保留公共错误/trace 脱敏工具
```

已删除的目录职责：旧 generic RAG auto-merge/rerank、PostgreSQL parent chunk、Redis 通用缓存、旧 catalog scope 适配、旧推荐结果/评分数据模型，以及它们的诊断入口。它们均未被当前 API、V3、入库脚本或测试调用。

## 当前请求主路径

```text
HTTP /api/chat/stream
 -> InputGuard + NormalizedTurn
 -> V3Router / SafetyProof
 -> 未直通：本地类型候选 + 一次 SemanticParse
 -> 购买形式、类型、价格和品牌的确定性校验
 -> ClarificationPlan 或 RequirementSpecV3
 -> CandidateGate -> embedding + Milvus -> 目录商品卡
    或 PC solver / cart confirmation / catalog fact query
 -> SessionDelta -> SSE
```
