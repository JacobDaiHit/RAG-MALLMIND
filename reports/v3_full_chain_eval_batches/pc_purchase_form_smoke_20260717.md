# 电脑购买形式真实回归（2026-07-17）

## 范围与环境

- 执行入口：`POST /api/chat/stream`，通过 `scripts/eval_v3_full_chain.py`；
- Chat：真实 DeepSeek `deepseek-chat`，每个用户回合只调用一次 SemanticParse；
- 推荐检索：真实 DashScope embedding + 本机 Milvus V3 collection；
- 结果文件：[pc_purchase_form_smoke_20260717.json](pc_purchase_form_smoke_20260717.json)；
- 结论：**5/5 通过**。这是 PC/电脑购买形式专项回归，不是整个 full-chain fixture 的全量结果。

## 实际结果

| 案例 | 实际外部语义结果 | 最终行为 | embedding / Milvus | 结果 |
|---|---|---|---|---|
| `7000 元左右配一台游戏主机，主要玩 3A` | `desktop_build` | `generate_pc_build_plan` | 不适用；只进 PC 目录求解器 | 通过 |
| `我要一台剪辑视频用的电脑，预算 9000` | `unknown` | `computer_purchase_kind_unresolved` 澄清 | 未调用 | 通过 |
| 上例后答“配台主机” | `unknown -> desktop_build` | 合并 9000 元和剪辑用途，进入 `generate_pc_build_plan` | 不适用；只进 PC 目录求解器 | 通过 |
| 上例后答“笔记本” | `unknown -> laptop` | 合并 9000 元和剪辑用途，进入普通推荐 | 已调用，`retrieval.status=ok` | 通过 |
| `我要一台带 RTX 4070 的游戏主机，预算 8000` | 最终被本地证据校验收敛为购买形式未确认 | `computer_purchase_kind_unresolved` 澄清 | 未调用 | 通过 |

最后一条刻意验证边界：RTX 4070 和“游戏主机”都不能证明用户是要买成品机还是要自己配机；并且 PC 求解器当前也不把模型输出的显卡型号当作可执行硬约束。因此系统选择澄清，不伪造装机方案。

## 本轮暴露并修复的两个问题

第一次专项运行是 3/5：

1. 明确“配一台游戏主机”已被模型标为 `desktop_build`，但 `ClarificationPolicy` 又按 `commerce_intent=recommend` 错误追问商品类别。现已规定 PC action 不进入“普通推荐缺类型”分支。
2. “带 RTX 4070 的游戏主机”被模型标为 `desktop_build`。现已要求 PC build evidence 必须命中集中配置的明确装机短语（配/组/装一台、攒机、装机、DIY、配置单）；该检查只验证模型已经给出的 evidence，不在本地猜 action。

修复后覆盖重跑同一 5 条，得到本报告的 5/5。

## 仍未完成

- 尚未在这次修复后重跑完整 fixture；普通商品、购物车、PC 编辑/比较等历史用例不能据此声称已重新验证。
- “RTX 4070 必须包含在方案中”尚未成为 `RequirementSpecV3` 的目录验证约束。若要支持它，应单独增加受控 PC 配件偏好字段、目录候选验证和求解器约束测试，不能让 SemanticParse 直接输出显卡 product ID。
