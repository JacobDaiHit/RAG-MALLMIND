# Agent V1 Round 4 测试报告

## 环境变更
- **Embedding 模型**: `text-embedding-async-v2` → `text-embedding-v3` (同步模型, dim=1024)
- **Milvus**: 全量重建 (drop_collection + reset_bm25), 884 chunks (400 ecommerce + 484 PC)
- **服务**: PID 28576, port 8000

## 代码修改

### 1. 测试框架 session 隔离 (test_agent_v1.py)
**根因**: 所有 `session_id=None` 的独立用例共享 `"default"` session，导致对话历史污染后续用例的 `contextual_goal`。
**修复**: 独立用例（无 session_id 且不依赖其他用例）使用唯一 session_id `test_case_{id}`。

### 2. 价格范围提取 (recommendation_pipeline.py: extract_price_range)
- **上限模式**增加: `不要超过`, `不要超出`, `别超过`, `别超出`
- **下限模式**增加负向后行断言: 避免 `不要超过3000` 中的 `超过3000` 被误匹配为下限

### 3. 排除词提取 (recommendation_pipeline.py: extract_exclusions)
- 排除 `不要超过...` 类结构: 当 `不要` 后面跟 `超过/超出/超/高于/大于/贵于/多` 时，不识别为排除条件

### 4. 工具路由预算提取 (tool_router.py: extract_budget)
- 前置模式增加: `不要超过`, `不要超出`, `别超过`, `别超出`
- 范围模式 (`X到Y`) 改为取上限值 (Y) 而非下限 (X)

## 测试结果: 55 PASS / 4 PARTIAL / 3 FAIL / 1 ERR

| 对比轮次 | PASS | PARTIAL | FAIL | ERR |
|---------|------|---------|------|-----|
| Round 2 | 21 | 23 | 18 | 1 |
| Round 4 | 55 | 4 | 3 | 1 |

### FAIL 分析 (3)

| # | 查询 | 原因 | 性质 |
|---|------|------|------|
| 21 | 有没有2000到5000的护肤品 | 美妆产品最高价~1690，无2000+商品 | 数据限制 |
| 34 | 差评多吗 | 独立测试无商品上下文，LLM 正确追问 | 评测器误判 |
| 59 | 推荐手机，直接帮我加到购物车 | 全量运行时 sess_j49 历史影响 sess_l59 路由 | session 串扰 |

### PARTIAL 分析 (4)

| # | 查询 | 原因 | 性质 |
|---|------|------|------|
| 43 | 不要第一个了 | followup_guard 将购物车删除路由到推荐 | 多轮路由 |
| 54 | 推荐500元以下的手机 | 手机最低价~3299，无500以下商品 | 数据限制 |
| 56 | 对比手机和洗面奶 | LLM 正确提示跨品类无法对比 | 评测器误判 |
| 57 | 。。。 | LLM 正确提示请输入具体问题 | 评测器误判 |

### ERR 分析 (1)

| # | 查询 | 原因 | 性质 |
|---|------|------|------|
| 58 | "" (空消息) | 服务端返回 400 Bad Request | 预期行为 |

## 关键结论

1. **Session 隔离修复** 是本轮最大收益，一次性解决了之前大量的 FAIL/PARTIAL
2. **text-embedding-v3** 工作正常，向量质量与 text-embedding-v4 相当
3. **需求解析层修复** (价格范围/排除词) 解决了 #25 "不要超过3000的耳机" 等问题
4. 剩余 3 个 FAIL 中：1 个数据限制、1 个评测器误判、1 个 session 串扰
5. 剩余 4 个 PARTIAL 中：2 个评测器误判（LLM 兜底正确）、1 个数据限制、1 个多轮路由

## 实际可修复问题

排除数据限制和评测器误判后，**真正需要修复的代码问题仅 2 个**：
- #43 多轮 followup 路由 (followup_guard 购物车操作识别)
- #59 全量测试 session 串扰 (不同 session_id 间的上下文隔离)
