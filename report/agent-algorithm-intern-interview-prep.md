# Agent 算法实习生面试准备：基于当前 MallMind 项目

更新时间：2026-07-09

这份文档按“面试官会追问”的方式准备。回答尽量口语化，不把没做完的能力说成已经上线。项目当前更准确的定位是：电商导购领域 Agent 后端，不是通用 Coding Agent。

## 1. 项目整体怎么讲

**面试官：先整体介绍一下你的项目。**

回答：

> 这个项目是一个电商导购 Agent 后端。用户可以用自然语言说“推荐防晒”“把第二款加入购物车”“帮我配一台游戏电脑”。后端会先判断用户想做什么，也就是路由到推荐、购物车、对比、闲聊、PC 装机这些工具；然后工具再去执行具体动作。推荐商品不是 LLM 编出来的，而是从本地商品库和可选 Milvus 检索证据里挑出来的。LLM 主要负责理解用户意图、抽结构化参数、生成导购话术。

可以补一句更工程化的：

> 主入口是 `/api/chat/stream`，它会用 SSE 持续返回 `runtime_mode`、`tool_call`、`progress`、`product_cards`、`cart_confirmation`、`result`、`done` 这些事件。

**追问：从用户输入到返回结果，完整链路是什么？**

回答：

> 用户请求先进入 `chat_stream()`。这个函数会调用 `sanitize_input()` 做输入清洗，再用 `get_session()` 拿到会话。然后 `build_runtime_policy()` 决定这轮要不要用 LLM、Milvus、视觉模型。接着 `route_shopping_tool_call()` 先跑本地规则路由，再尝试 LLM JSON 路由，最后 `validate_tool_call()` 做兜底纠偏。确定工具后，如果是购物车、闲聊、对比这类轻量工具，就直接进 handler；如果是推荐或 PC 装机，会先用 `prepare_recommendation_context()` 合并多轮上下文和附件，再进推荐或装机链路。

**追问：你说它是 Agent，不就是几个 if else 吗？**

回答：

> 我理解 Agent 不一定要很玄，它至少要有“理解任务、选择工具、执行工具、维护状态、处理失败”的 harness。这个项目里有工具 schema、LLM/规则混合路由、session memory、购物车确认、RAG 可降级、事实校验和 SSE trace。它不是把用户问题直接丢给 LLM 回答，而是让 LLM 帮忙做路由和理解，真正的商品推荐、购物车修改、PC 兼容性检查都由代码工具执行。

## 2. 路由系统怎么讲

**面试官：你的工具路由怎么做？**

回答：

> 路由在 `tool_router.py`。先用 `local_route_tool_call()` 做规则判断，比如看到“加入购物车”就倾向购物车，看到“对比”就倾向对比，看到“装机/配电脑”就倾向 PC 整机。然后如果 runtime 允许，会用 `try_llm_route_tool_call()` 让 LLM 输出一个 JSON tool call。最后 `validate_tool_call()` 再检查工具名、预算、类目、购物车强信号，防止 LLM 明显误判。

**追问：为什么要规则 + LLM 两套？**

回答：

> 规则稳定，适合兜底和处理高风险动作，比如购物车删除、清空。LLM 更适合理解复杂表达，比如“油皮夏天通勤用的防晒”“把理肤泉那款加入购物车”。所以我让规则先算一个本地结果，LLM 再尝试更细的理解，最后 guard 层兜底。这样比完全相信 LLM 更稳。

**追问：`validate_tool_call()` 具体兜什么底？**

回答：

> 它不再调用 LLM，只做确定性校验。比如 LLM 返回不存在的工具名，就降级到 `general_chat`；预算超过 50 万会裁剪；品牌列表太长会截断；如果用户明显是闲聊但 LLM 判成推荐，会改回本地规则；如果用户明显说“加入购物车/删除购物车”，无论 LLM 怎么判，都会强制改成 `apply_cart_instruction`。

## 3. 推荐链路怎么讲

**面试官：推荐链路里系统怎么挑商品？**

回答：

> 推荐入口在 `handle_recommend()`，它先校验需求，再调用 `recommend_shopping_products()`。如果 router 已经抽出了参数，比如类目、预算、品牌，就通过 `_requirement_from_args_v2()` 转成 `RequirementSpec`；如果没有，就走 `parse_requirement()`，用规则加可选 LLM 解析。真正挑商品在 `build_recommendation_result()`：先加载对应商品库，再做结构化过滤，必要时调用 Milvus 检索商品证据，然后融合候选、打分排序、生成商品卡。

**追问：打分排序看哪些维度？**

回答：

> 核心在 `scorer.py::score_products()`。它会看场景匹配、属性匹配、价格适配、口碑、库存、SKU、详情质量，还有 RAG evidence 的加分。硬条件一般先在 `filter_products_for_requirement()` 里处理，比如预算、品类、排除品牌；向量检索只是补充召回和解释证据，不应该绕过硬约束。

**追问：LLM 会不会编商品？**

回答：

> 不会让它当商品事实源。商品卡来自本地 catalog，`build_product_cards()` 从真实商品对象构造卡片。返回前还会经过 `fact_check_result()` 校验商品 ID 和价格。LLM 主要做三件事：理解需求、生成导购话术、基于已有证据解释为什么推荐。

**追问：为什么有时候 `parse_requirement()` 没走 LLM？**

回答：

> 主 `/api/chat/stream` 里 router 已经给了结构化参数，所以 `recommend_shopping_products()` 常常直接用 `_requirement_from_args_v2()`。这不是 bug，而是避免重复解析。旁路接口或没有 router arguments 的情况下才更依赖 `parse_requirement()`。

## 4. RAG / Milvus 怎么讲

**面试官：RAG 在这个项目里做什么？**

回答：

> RAG 不是主推荐的唯一依据，而是证据增强层。结构化商品库适合处理预算、品类、品牌这些硬条件，但用户经常说“油皮”“通勤”“缓震”“剪辑”，这些是语义条件。Milvus 检索商品详情 chunk，把相关证据挂到 product_id 上，后面排序和解释可以用这些 evidence。

**追问：Milvus 挂了怎么办？**

回答：

> `retrieve_evidence_with_timeout()` 有超时和失败兜底。Milvus 不可用时会返回带状态的 `RetrievalEvidence`，主链路继续走结构化过滤和评分。所以 RAG 提升效果，但不是可用性的单点。

**追问：为什么不用纯向量召回？**

回答：

> 电商推荐有很多硬约束，比如预算、库存、排除品牌。纯向量可能语义很像，但价格或品类不满足。当前设计是规则过滤保证底线，向量检索补充语义召回，再通过 `fuse_candidates()` 和 `score_products()` 融合排序。

## 5. 购物车链路怎么讲

**面试官：用户说“把第二款加入购物车”，系统怎么知道第二款是谁？**

回答：

> 推荐成功后，`remember_recommendation()` 会把上一轮 `product_cards` 保存到 session。下一轮用户说“第二款”时，router 的 user prompt 会注入上一轮可见商品卡，里面带 `product_id/title/brand/price`。LLM 如果判断是购物车工具，可以把 `operation=add`、`target_product_index=2`、`product_ids=[...]` 传给下游。下游的 `_resolve_product_for_cart()` 会再用服务端 session 校验，按用户实际看到的 `product_cards` 顺序解析。

**追问：如果 LLM 给的 product_id 和“第二款”冲突怎么办？**

回答：

> 现在服务端会更相信用户原文里的序号和服务端保存的可见商品卡顺序。也就是说，如果 LLM 同时给了 `target_product_index=2` 和一个不一致的 `product_id`，`_resolve_product_for_cart()` 会按第二张商品卡重新解析，防止加错。

**追问：购物车会直接写吗？**

回答：

> 主聊天链路不会直接写。`handle_cart_v2()` 会先生成一个 `pending_cart_action`，通过 `cart_confirmation` 发给前端。用户确认后，`/api/cart/confirm` 才会调用 `apply_cart_instruction()` 真正修改 `session.cart`。不过 `/api/cart/actions` 这个直接写旁路还存在，`clear` 当前也是直接执行，这是我会继续收敛的风险点。

**追问：删除购物车某个商品怎么定位？**

回答：

> 删除和改数量会优先从当前 cart 里定位。LLM 可以传 `operation=remove` 和 `target_product_id`，但服务端会检查这个 ID 必须在当前购物车里。没有 ID 时，就按品牌/标题模糊匹配、序号、上一项引用来解析；再不行才会走兜底或追问。

## 6. 多轮上下文怎么讲

**面试官：你的多轮记忆具体保存了什么？**

回答：

> 主要在 `ShoppingSession`。它保存 `last_goal`、`last_requirement`、`last_result`、`cart`、`pending_cart_action`、`topic_memory`、`recent_queries`、`current`、`pc_build_history` 等。简单理解就是：上一轮推荐了什么、用户当前话题是什么、购物车里有什么、PC 方案是什么。

**追问：上下文怎么参与下一轮请求？**

回答：

> 路由时 `_build_router_user_prompt()` 会注入当前累积状态、最近 query、上一轮商品卡、当前购物车。推荐前 `prepare_recommendation_context()` 会调用 `build_contextual_goal()`，判断用户是不是追问。如果是追问，会把上一轮目标和新约束拼起来；如果是新话题，就重开。

**追问：怎么避免旧上下文污染？**

回答：

> 现在有一些话题切换规则，比如从 PC 装机切到护肤、从跑鞋切到手机，会尽量重置推荐上下文。PC 单配件场景也会避免继承旧的 `sub_category` 和 `must_have_terms`。但这块还不是完美的长期记忆系统，更偏短期 session memory。

## 7. 闲聊、对比、参数这些链路怎么讲

**面试官：除了推荐和购物车，还有哪些工具？**

回答：

> 轻量工具都在 `tool_handlers.py`。`handle_general_chat()` 负责闲聊和系统说明；`handle_compare_v2()` 做商品对比；`handle_parameter_query()` 回答具体参数；`handle_sku_query()` 回答同一商品不同 SKU 差异；`handle_price_comparison()` 处理价格确认。这些工具不走完整推荐组包，所以响应更快。

**追问：闲聊也用 LLM 吗？**

回答：

> 会尝试用。`_generate_general_chat_llm_response()` 让 LLM 用简短口吻回答；如果 LLM 不可用或失败，就走 `_generate_general_chat_fallback()` 模板。它不会去推荐商品，也不会改购物车。

**追问：对比没有传 product_ids 怎么办？**

回答：

> `handle_compare_v2()` 会尝试从上一轮推荐结果里取商品 ID，比如用户刚看了三款防晒，下一句说“这几款对比一下”，它可以复用上一轮 `last_result`。

## 8. PC 装机链路怎么讲

**面试官：PC 装机和普通推荐有什么区别？**

回答：

> 单个显卡、CPU、电源推荐还是走普通推荐链路，只是 `catalog_scope=pc_parts`。但完整装机需求，比如“7000 元游戏主机”，会路由到 `generate_pc_build_plan`，进入 `handle_pc_build()`，再调用 `pc_session_flow.build_pc_plan_for_message()` 和 `pc_build.generate_pc_build_plan()`。它会组合 CPU、GPU、主板、内存、电源、机箱、散热等，并做兼容性检查。

**追问：LLM 会决定兼容性吗？**

回答：

> 不会。兼容性是代码规则判断，比如 socket、内存代际、功耗、电源、机箱尺寸。LLM 可以帮忙理解用户说“更安静”“预算加到一万”，但不直接判断硬件兼容。

## 9. 多模态怎么讲

**面试官：图片输入怎么处理？**

回答：

> 附件处理在 `attachments.py`。`prepare_attachments_for_recommendation()` 会规范化附件。如果 runtime 允许视觉模型，`analyze_image_attachment()` 会用视觉 LLM 提取 OCR、商品品类、品牌、型号、颜色、场景等线索。然后 `goal_with_attachment_context()` 把这些线索拼进推荐目标。

**追问：你做 GUI grounding 了吗？**

回答：

> 当前没有。这个项目的多模态是图片/截图理解，不是浏览器 GUI grounding。可以讲未来会用 Playwright 做商品页采集和 UI 回归，但不能说当前已经做了 Browser Agent。

## 10. 模型后端和 LLM 抽象怎么讲

**面试官：支持换模型吗？**

回答：

> 支持 OpenAI-compatible 风格的后端。`llm_client.py` 会从环境变量读取 provider、base_url、api_key、model、fast_model。上层大多数地方通过 `OpenAICompatibleChatClient.chat_json()` 或 `chat_text()` 调用。

**追问：`llm_gateway.py` 是不是统一入口？**

回答：

> 不是当前主链路统一入口。它更像一个已经设计好的迁移目标，里面有统一 caller 配置、超时、并发、熔断的想法。但现在 router、parse、guidance、explanation、attachment、general chat 仍然直接实例化 `OpenAICompatibleChatClient`。这个要诚实说是技术债。

**追问：如果让你重构模型调用，你怎么做？**

回答：

> 我会把调用点迁到 `LLMGateway.call(caller_name, messages)`，让 router、parse、guidance、explanation 分别有自己的模型、温度、max tokens、超时和并发限制。同时把错误统一成 `timeout`、`json_invalid`、`network_error`、`provider_error`，上层只处理标准错误。

## 11. 评测怎么讲

**面试官：你怎么证明改动让系统变好了？**

回答：

> 不能只看 demo，要看 case 和指标。比如 router 看工具命中率和误路由；RAG 看 Hit@5、P@1、MRR、延迟；购物车看多轮“第一款/第二款/品牌名”是否选对，确认后 cart 是否正确；PC 看兼容性和预算是否满足。

**追问：现在测试还有问题吗？**

回答：

> 有。购物车相关测试当前是通过的，但 `tests/test_tool_router.py` 里还有旧断言，比如旧 trace 字段和旧 category 语义，这说明测试和真实代码合同有漂移。这个不能硬让代码迎合旧测试，应该按当前路由合同重写测试。

**追问：场景化推荐怎么评？**

回答：

> 我会做一组场景 case，比如“油皮夏天防晒”“篮球实战鞋缓震”“学生党手机”“750W 金牌电源”。看 top1/top3 是否同品类、是否满足核心场景词、是否违反预算或排除品牌。现在场景化推荐还偏轻量打分，不应该夸大。

## 12. 安全和审计怎么讲

**面试官：你怎么防 prompt injection？**

回答：

> 入口 `sanitize_input()` 会调用 `detect_injection()` 做基础拦截。LLM prompt 里也有 defense prefix/suffix，要求模型只输出 JSON 或只基于证据解释。更重要的是业务事实不从 LLM 来，商品和价格要回到 catalog 校验。

**追问：内部工具化的话要补什么？**

回答：

> 要补权限和审计。比如谁能调用产品写接口，谁能清空购物车，谁能看诊断接口；API key 要托管；每次工具调用要有用户、时间、参数、结果、失败原因。当前项目有 trace 和错误脱敏，但还不是完整企业权限系统。

## 13. 项目不足怎么讲

**面试官：这个项目现在最大的问题是什么？**

回答：

> 我会说三个。第一，LLM 调用点还分散，`llm_gateway.py` 没真正接入主链路。第二，购物车主确认链路已经改善，但 `/api/cart/actions` 仍然是直接写旁路，`clear` 也直接执行。第三，场景化推荐还不是完整场景规划，更多是规则、轻量场景词和 RAG evidence 的融合。

**追问：下一步优先做什么？**

回答：

> 我会先收敛高风险链路：购物车所有写操作统一确认，取消“首项兜底”或改成追问；然后重写 router 测试；再做场景化推荐 benchmark；最后再考虑把 LLM 调用迁到 Gateway。

## 14. 平时怎么用 AI 辅助开发

**面试官：你平时怎么用 AI 做开发？**

回答：

> 我会先让 AI 帮我读代码、画调用图、找入口和影响面，但最后判断一定回到源码和测试。实现时我会把任务拆成：入口、数据结构、业务逻辑、错误处理、测试、文档。改完后用 `rg` 查调用关系，用 pytest 和接口 smoke 验证。对 AI 生成的代码，我重点看有没有改错主链路、有没有绕开已有抽象、有没有把 LLM 当事实源、有没有漏掉降级。

**追问：这次你怎么查项目链路？**

回答：

> 我没有按旧文档写，而是从 `/api/chat/stream` 开始查实际 import 和函数调用：`chat.py`、`tool_router.py`、`tool_handlers.py`、`recommendation_pipeline.py`、`package_builder.py`、`session_state.py`。尤其是发现 runtime mode 不是几套独立链路，而是一条主链路上的能力开关；购物车也不是直接写，而是计划 + 确认。

## 15. 不要夸大的点

- 不要说这是通用 Coding Agent；它是电商导购领域 Agent。
- 不要说所有 LLM 调用都走 `LLMGateway`；当前没有。
- 不要说做了 SFT/RL；当前没有训练链路。
- 不要说做了 GUI grounding 或 Browser Agent；当前没有。
- 不要说场景化推荐已经很强；当前还需要评测集证明。
- 不要说聊天记录落 SQL；当前主 session 是 memory/Redis。
- 不要说 Milvus 是硬依赖；它是可降级增强层。

## 16. 代码锚点速查

| 能力 | 代码位置 |
| --- | --- |
| 主聊天入口 | `rag/api/routes/chat.py::chat_stream` |
| SSE 保护 | `rag/api/sse.py::safe_stream` |
| Runtime policy | `rag/api/runtime_context.py::build_runtime_policy` |
| 工具路由 | `rag/recommendation/tool_router.py::route_shopping_tool_call` |
| 路由纠偏 | `rag/recommendation/tool_router.py::validate_tool_call` |
| 推荐 handler | `rag/recommendation/tool_handlers.py::handle_recommend` |
| 购物车 handler | `rag/recommendation/tool_handlers.py::handle_cart_v2` |
| 推荐主入口 | `rag/recommendation/recommendation_pipeline.py::recommend_shopping_products` |
| 推荐组包 | `rag/recommendation/package_builder.py::build_recommendation_result` |
| 结构化过滤 | `rag/recommendation/structured_filter.py::filter_products_for_requirement` |
| 打分排序 | `rag/recommendation/scorer.py::score_products` |
| RAG 检索 | `rag/recommendation/retrieval.py::retrieve_requirement_evidence` |
| 候选融合 | `rag/recommendation/retrieval_fusion.py::fuse_candidates` |
| 会话记忆 | `rag/recommendation/session_state.py::ShoppingSession` |
| PC 装机 | `rag/recommendation/pc_build.py::generate_pc_build_plan` |
| 附件理解 | `rag/api/attachments.py::prepare_attachments_for_recommendation` |
