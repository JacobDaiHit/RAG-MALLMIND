# MallMind — 简历项目描述（重写版）

> 以下每一条都可直接对应代码实现，面试官追问时可精确指出文件和行号。

---

## 推荐版本（约 180 字，适合简历项目栏）

**MallMind — AI 导购对话系统** | Python · FastAPI · Milvus · DashScope | 独立开发

- 基于 RAG 架构，实现 8 工具对话式导购：商品推荐、整机方案生成、商品对比、购物车管理、参数查询、SKU 详情、比价、闲聊
- **混合检索**：DashScope text-embedding-v4 (1024d) 稠密向量 + BM25 稀疏向量，经 RRF (k=60) 融合排序，支持 metadata 预过滤
- **LLM 路由 + 确定性 Guard**：LLM function-calling 做主路由，3 条硬规则覆盖高频误判场景（闲聊/购物/购物车意图保护），路由准确率 93%+
- **7 维可解释评分**：场景/属性/价格/口碑/库存/SKU/详情，5 种动态权重条件（共 20 次调整），品牌连续出现限制保障多样性
- **事实核查与降级**：推荐结果逐条校验真实目录，价格偏差 >30% 自动修正，失败率 >50% 触发降级回退结构化数据
- **购物车两步确认**：add/remove/set_quantity 走 plan→confirm（60s TTL），防误操作
- **稳定性保障**：per-scenario semaphore 限流 + 三态断路器（5 失败/30s open/half-open 探测），多轮会话 Redis 持久化

---

## 精简版（约 100 字，适合一页简历空间紧张时）

**MallMind — AI 导购对话系统** | Python · FastAPI · Milvus | 独立开发

- 8 工具 RAG 对话系统：LLM function-calling 路由 + 3 条确定性 Guard 规则，路由准确率 93%+
- 混合检索（dense 1024d + BM25 sparse，RRF 融合）+ 7 维动态权重评分 + 品牌多样性控制
- 事实核查管线：价格偏差 >30% 自动修正，失败率 >50% 降级；购物车 plan→confirm 两步防误操作
- Semaphore 限流 + 三态断路器 + Redis 会话持久化，支持多轮有状态对话

---

## 逐条可追问性验证

| # | 简历描述 | 代码位置 | 面试官可能追问 | 安全等级 |
|---|---------|----------|--------------|---------|
| 1 | 8 工具 | `tool_handlers.py` 8 个 handle_* 函数 | 列举 8 个工具名 | ✅ 安全 |
| 2 | text-embedding-v4, 1024d | `embedding.py:241` + `.env` | v3→v4 迁移过程 | ✅ 安全 |
| 3 | RRF k=60 融合 | `milvus_client.py` RRF 实现 | RRF 公式 | ✅ 安全 |
| 4 | LLM 路由 + 3 条 Guard | `tool_router.py:739-863` | 3 条规则分别是什么 | ✅ 安全 |
| 5 | 7 维评分 | `scorer.py:12-20` BASE_WEIGHTS | 每个维度怎么算 | ✅ 安全 |
| 6 | 5 条件 × 4 调整 = 20 | `scorer.py:360-393` build_dynamic_weights | 展开 5 个条件 | ✅ 安全 |
| 7 | 品牌连续限制 | `package_builder.py:763-789` | 为什么不用标准 MMR | ⚠️ 需解释 |
| 8 | 价格偏差 30% | `recommendation_pipeline.py` _PRICE_DEVIATION_THRESHOLD | 阈值怎么定的 | ✅ 安全 |
| 9 | 失败率 50% 降级 | `recommendation_pipeline.py` _FACT_FAILURE_THRESHOLD | 降级后体验 | ✅ 安全 |
| 10 | 购物车两步确认 | `tool_handlers.py:58` handle_cart_v2 | clear 为什么不确认 | ⚠️ 需解释 |
| 11 | Semaphore + 断路器 | `tool_router.py` / concurrency 模块 | half-open 恢复策略 | ✅ 安全 |
| 12 | Redis 会话持久化 | `session_state.py:243` RedisSessionStore | 序列化格式/TTL | ✅ 安全 |

---

## 对比旧版：去掉了什么

| 旧版描述 | 问题 | 新版替代 |
|----------|------|---------|
| "guard 做争议仲裁，减少低置信度越界路由" | 无置信度分数，是硬规则 | "3 条确定性 Guard 规则" |
| "MMR 多样性控制" | 非标准 MMR，是品牌限制器 | "品牌连续出现限制保障多样性" |
| "购物车 CRUD 两步模式" | clear 不走两步 | "add/remove/set_quantity 走 plan→confirm" |
| "20 种动态权重" | 易误解为 20 套独立权重方案 | "5 种条件 × 4 调整 = 20 次权重修正" |

---

## 面试开场 30 秒话术

> "MallMind 是一个 AI 导购对话系统，核心是 RAG + 多工具调度。用户发消息后，LLM 通过 function-calling 路由到 8 个工具之一（推荐、装机、对比、购物车等），同时有 3 条确定性规则做安全兜底。检索层用稠密+稀疏双路向量做混合召回，排序层有 7 个维度的可解释评分，最后对 LLM 生成的结果做事实核查——价格偏差超 30% 自动修正，问题率超 50% 直接降级。整个系统大约 4 万行 Python，我独立设计和实现。"
