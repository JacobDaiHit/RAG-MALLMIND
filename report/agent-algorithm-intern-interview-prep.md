# Agent 算法实习生面试准备：基于当前 MallMind 项目

更新时间：2026-07-09

这份文档按截图里的岗位要求整理：LLM 应用、RAG、Tool Use、Workflow、多模态、评测、工程落地、安全和问题分析。回答尽量贴合当前项目真实状态，避免把还没接上的能力说成已经上线。

## 一、项目怎么讲

一句话版本：

> 我做的是一个面向电商导购和 PC 装机的本地可审计 Agent 后端。它把用户输入路由成不同工具调用，再结合本地商品库、可选 Milvus RAG、结构化评分、LLM 需求解析和多轮 session memory，输出商品卡、对比表、购物车动作或 PC 整机方案。

两分钟版本：

> 项目后端是 FastAPI，主入口是 `/api/chat/stream`。请求进来后先做输入清洗和 prompt injection 检测，然后根据 runtime mode 决定是否启用 LLM、Milvus、视觉解析和 query expansion。接着进入工具路由层，路由器会先跑本地规则，再在允许时尝试 LLM JSON 路由，最后通过校验层修正明显冲突，比如购物车意图会强制走购物车工具。工具层分轻量工具和重工具：轻量工具处理购物车、闲聊、对比、SKU 和参数查询；重工具处理商品推荐和 PC 装机。推荐链路会把 query 解析成 RequirementSpec，再加载本地 catalog，做结构化过滤、可选 RAG 证据召回、RRF 融合、评分排序、商品卡构造、事实校验和自然语言回答。所有推荐商品都来自本地数据，LLM 主要做理解和表达，不直接编造商品。

项目关键词：

- `LLM 应用`：工具路由、需求解析、guidance、证据解释、附件理解、自然语言回答。
- `RAG`：Milvus dense+sparse hybrid retrieval、BM25 sparse、RRF 融合；query expansion、rerank、auto-merge 是受配置和 runtime policy 控制的可选能力。
- `Agent / Tool Use`：`route_shopping_tool_call()` 输出 tool name + arguments，再由 handler 执行。
- `Workflow`：chat stream 是一条显式流水线，PC 装机是结构化规划工作流。
- `Memory`：session 保存 topic memory、last result、cart、PC build history、recent turns summary。
- `Evaluation`：仓库里有 capability challenge、full-chain ablation、retrieval eval、legacy cases、cart tests、PC compatibility tests。

## 二、岗位要求映射

| 岗位要求 | 当前项目可讲内容 | 需要诚实补充 |
| --- | --- | --- |
| LLM 应用与优化 | OpenAI-compatible client、router/parse/guidance/explanation 多调用点、超时和降级 | `llm_gateway.py` 还没真正接入主链路 |
| 信息抽取 / 文本理解 | `parse_requirement()`、规则解析 + LLM JSON parse、附件 OCR/visual terms | schema 主要服务电商导购，不是通用 IE |
| 检索 / RAG | Milvus hybrid retrieval、BM25 sparse、RRF、vector rescue、evidence boost | RAG 是增强层，需要用评测证明收益 |
| 决策规划 | PC 装机组合搜索、兼容性检查、预算约束、工具路由 | 不是强化学习规划 |
| 多模态理解 | 图片附件 VLM 解析、视觉 query terms、image vector retrieval | 没做 GUI grounding |
| Agent / Tool Use | 工具定义、路由、校验、handler 分发、SSE 事件输出 | 工具不是外部 OS 工具，而是领域业务工具 |
| Workflow 系统 | 主聊天链路、推荐链路、PC 链路、购物车确认链路 | graph workflow 只在 debug endpoint |
| Evaluation | 多套 pytest 和脚本评测，链路消融 | 还可以补自动 benchmark dashboard |
| Browser Agent / DOM | 当前项目没有主做浏览器自动化 | 可以讲未来如何接 Playwright 做商品页面采集或 UI 自动回归 |
| SFT/RL | 当前没有训练和 RL | 可以讲项目更偏应用层 Agent + RAG 工程 |

## 三、面试高频问题与参考回答

### Q1：整体介绍一下你的项目，从用户输入到最终输出，中间完整链路是什么？

回答：

> 主入口是 FastAPI 的 `/api/chat/stream`。请求进来后先做输入清洗和注入检测，然后从 `session_state` 取会话，`runtime_context` 根据 mode、附件和 LLM 配置决定是否启用 LLM/RAG/视觉能力。接着 `tool_router` 做工具路由，输出类似 `recommend_shopping_products`、`compare_products`、`apply_cart_instruction`、`generate_pc_build_plan` 的 tool call。轻量工具直接执行，推荐和 PC 方案这类重工具会先准备附件和上下文，再进入对应 handler。推荐链路会解析需求、加载 catalog、可选 Milvus RAG、结构化过滤、评分排序、生成商品卡和对比表，最后通过 SSE 返回 `tool_call`、`product_cards`、`result`、`done` 等事件。

### Q2：为什么说它是 Agent，而不只是一个普通推荐 API？

回答：

> 因为它不是单个固定函数。它有一个 harness：输入清洗、runtime policy、工具路由、工具参数、handler 执行、session memory、错误降级和 trace。LLM 不是直接生成最终答案，而是参与路由、解析和表达。真正的动作由工具完成，比如推荐、对比、购物车、PC 装机。这个结构更接近 domain agent。

### Q3：你的工具系统是怎么做的？新工具从定义到调用的流程是什么？

回答：

> 工具先在 `tool_router.py` 的允许工具集合和 LLM router prompt/schema 里定义名称和参数；本地规则路由也要能识别这个工具。主入口拿到 tool call 后，在 `chat.py` 里根据工具名分发。轻量工具加到 `_LIGHTWEIGHT_TOOLS` 并在 `_dispatch_lightweight()` 里接 handler；重工具要走 `prepare_recommendation_context()` 后单独处理。最后 handler 负责返回 SSE 事件，并更新 session/topic memory。

### Q4：LLM 工具路由失败时怎么办？

回答：

> 当前不是让 LLM 单独决定路由，而是先算本地规则路由，再在 runtime 允许时尝试 LLM router。LLM 超时、网络错误、JSON 解析失败或熔断时，直接回落本地规则。之后还有 `validate_tool_call()` 做校验和纠偏，比如 LLM 把“加入购物车”误判成推荐时，会强制改成 `apply_cart_instruction`。

### Q5：你的 Agent 支持不同模型后端吗？

回答：

> 支持 OpenAI-compatible 形式的多个后端。`llm_client.py` 里有 `build_llm_provider_config()`，支持 `ark`、`deepseek`、`mimo`、`openai_compatible`，通过环境变量解析 base_url、api_key、model、fast_model。上层调用只用 `OpenAICompatibleChatClient.chat_text/chat_json/chat_completion`，不会直接关心具体供应商。不同模型的差异主要体现在是否支持 JSON mode、模型名、base URL 和错误返回。

### Q6：如果要接入更多模型服务，你会怎么抽象请求格式、响应结构、错误码和重试？

回答：

> 我会把 provider config、request adapter、response parser、error classifier 分开。当前项目已经有 `LLMCallReport` 记录 status code、latency、error、preview，也有 `LLMClientError` 和 sanitize。下一步应该把散落的 `OpenAICompatibleChatClient()` 调用迁移到 `LLMGateway.call(caller_name, messages)`，按 caller 配置模型、超时、温度、max tokens、并发和熔断。错误码统一映射成 `timeout`、`network_error`、`json_invalid`、`provider_error`、`config_error`，让上层只处理标准错误。

### Q7：`llm_gateway.py` 在项目里是什么状态？

回答：

> 它是一个已经设计好的统一 LLM 调用层，但当前生产主链路还没迁移过去。真实调用点仍然直接实例化 `OpenAICompatibleChatClient()`，比如 router、parse、guidance、response、explanation、attachment。面试时我会诚实讲这是技术债：设计有了，测试也有，但为了避免大范围回归风险，还没把所有调用点切过去。

### Q8：LLM 输出 JSON 不稳定怎么办？

回答：

> 当前项目的做法是 prompt 明确要求 JSON，然后通过 `chat_json()` 和 `extract_json_object()` 提取 JSON object。解析失败会记录 `llm_json_invalid`，再回退到规则解析或模板输出。比如需求解析失败不会让推荐链路中断，而是用 `parse_requirement_rule_based()` 的结果继续走。

### Q9：你怎么防止 LLM 编造商品、价格和库存？

回答：

> 关键是把 LLM 限制在“理解和表达”，不让它作为商品事实源。推荐商品来自本地 catalog，商品卡由 `package_builder.product_card_from_component()` 从 `ApiProduct` 生成。回答生成后还有 `fact_check_result()` 检查 product_id 是否在 catalog 里。自然语言回答也会做事实约束，只允许引用商品卡里的标题、价格、预算等字段。

### Q10：RAG 在你的项目里解决什么问题？

回答：

> RAG 主要解决商品详情证据不足和语义召回问题。结构化过滤适合预算、品类、品牌、库存这类硬条件，但用户表达经常是“通勤舒服”“油皮可用”“剪辑用显卡”这种语义条件。Milvus 召回商品 chunk 后，会把证据挂到 product_id 上，后续 scorer 用 evidence boost 和 evidence reasons 改善排序与解释。

### Q11：你的 RAG 具体怎么实现？

回答：

> 离线侧把商品转成 chunks，生成 dense embedding 和 BM25 sparse vector，写入 Milvus。在线侧 `retrieval.retrieve_requirement_evidence()` 对每个品类做 hybrid retrieve，dense+sparse 一起查，拿回 product_id 和文本证据。之后 `retrieval_fusion.fuse_candidates()` 把规则候选和向量召回候选用 RRF 融合，再由 `scorer.score_products()` 综合场景、属性、价格、口碑、库存、SKU、详情质量和 evidence signal 排序。

### Q12：RAG 失败会不会影响推荐？

回答：

> 不会中断主链路。RAG 是增强层，`retrieve_evidence_with_timeout()` 有超时保护；Milvus 不可用、超时或后处理失败时，会记录 trace，然后回到结构化 catalog scoring。这个设计牺牲了一些召回增强，但保证服务可用。

### Q13：RAG、Memory、Context Engineering 三者有什么区别？在项目里怎么配合？

回答：

> RAG 是从外部知识库取事实证据，比如商品详情 chunk；Memory 是跨轮保存用户当前任务状态，比如 last_result、cart、topic_memory、pc_build_history；Context Engineering 是决定本轮给模型和工具什么上下文，比如附件解析结果、上一轮商品、当前 catalog_scope、runtime policy。项目里 RAG 负责事实，Memory 负责连续对话，Context Engineering 负责把这些信息整理成当前工具能用的输入。

### Q14：你项目里的上下文治理怎么做？

回答：

> 主要在 session 层。`ShoppingSession` 保存 `last_result`、`last_requirement`、`topic_memory`、`recent_turns`、`recent_turns_summary`、购物车和 PC 方案历史。`session_context.record_turn()` 会限制 recent turns 长度，溢出的轮次压成一个 1200 字以内的 summary。`merge_requirement_memory()` 会把预算、偏好、品类等字段合并，同时在检测到新话题时重置上下文。

### Q15：上下文压缩之后怎么判断没有破坏任务？

回答：

> 当前项目不是通用 Coding Agent 的复杂压缩系统，但有基本校验思路：压缩后仍要保留当前 topic、last_result product_ids、预算、品类、关键偏好、购物车和 PC build history。实际执行上，可以通过回归测试验证“上一轮推荐后说第一个加入购物车”“上一套 PC 方案加 1000 预算”这类依赖上下文的 case 是否仍正确。

### Q16：如果记忆里有过时或错误信息怎么办？

回答：

> 当前项目用话题切换逻辑处理一部分污染。`should_start_new_product_topic()` 和 `merge_requirement_memory()` 会在用户从跑鞋切到手机、从 PC 切到护肤这类明显新话题时重置需求上下文。对于错误记忆，更稳的方案是把 memory 分成事实型和推断型：商品事实只从 catalog/RAG 来，LLM 推断只作为 soft signal，并且每轮路由后用当前 query 重新校验 topic。

### Q17：多轮执行中如何避免重复读状态或重复调用工具？

回答：

> 项目里主要靠 session 的 `last_result`、`topic_memory`、`pc_build_history` 和 `llm_call_log` 做状态复用与观测。例如对比时如果本轮没有 product_ids，会从上一轮推荐结果取；PC 追问会从历史方案取 baseline。重复工具调用目前还没有全局去重调度器，更多是业务层按上下文复用。后续可以加 request-level cache，按 normalized query + tool + catalog_scope 去重。

### Q18：购物车链路怎么设计？有什么风险？

回答：

> 购物车 v2 设计成“计划 + 确认”。`handle_cart_v2()` 解析 add/remove/set_quantity/clear，add/remove/set_quantity 会生成 `pending_cart_action` 和 `cart_confirmation`，再由 `/api/cart/confirm` 真正写入；pending plan 已经保存到 session，推荐后 `action=add_to_cart` 也已经改成确认计划。当前风险主要是 `/api/cart/actions` 仍是直接写 API，`clear` 也直接执行；另外消歧失败时存在“首项兜底”，误操作风险要继续收敛。

### Q19：PC 装机链路和普通推荐有什么不同？

回答：

> 普通推荐是 catalog filter + scoring；PC 装机是结构化组合搜索。`pc_build.generate_pc_build_plan()` 会从 CPU、GPU、主板、内存、电源等分组里组合候选，再用 `pc_compatibility.check_pc_build_compatibility()` 检查 socket、内存代际、功耗、机箱尺寸等硬约束。这个链路更像一个约束满足和规划问题，LLM 不参与核心兼容性判断。

### Q20：场景化推荐是怎么做的？

回答：

> 场景信息会进入 `RequirementSpec.scenario`、`occasion`、`target_user`、`preferences` 和 `must_have_terms`。商品侧有 `supported_scenarios`、tags、best_for、FAQ 和详情文本。评分时 `score_scenario_match()` 会结合品类、子类、品牌、场景词和 RAG evidence 加分。它目前是轻量场景打分，不是完整场景知识图谱，所以旅行/通勤/送礼这类场景可以影响排序，但复杂跨品类套餐仍需要更强的场景到品类映射。

### Q21：多模态能力怎么接入？

回答：

> 用户传图片附件时，`attachments.py` 会规范化附件、限制大小，并在 runtime 允许时调用视觉模型。视觉模型返回 OCR 文本、可见品类、品牌、型号、颜色、场景和 visual query terms。这些信息会拼进推荐 query，同时 `image_retrieval.py` 会把图片转成简单图像向量，从本地 image vector index 找相似商品，作为额外 evidence 融入推荐。

### Q22：你做了 GUI grounding 或 Browser Agent 吗？

回答：

> 当前项目没有做 GUI grounding 或浏览器自动操作，主要是后端 Agent/RAG/Tool Workflow。如果要补浏览器方向，我会先用 Playwright 做两件事：第一是商品详情页采集，把 DOM 中的标题、价格、SKU、评价抽成结构化 catalog；第二是端到端 UI 回归，自动提交聊天请求并断言 SSE 事件、商品卡和购物车状态。

### Q23：项目里的评测体系有哪些？

回答：

> 有单元测试和链路评测两类。单元测试覆盖路由、session、购物车、PC 兼容性、embedding provider、Milvus pipeline、错误脱敏。链路评测脚本包括 capability challenge、full chain ablation、model chain ablation、retrieval eval、用户场景评估等，指标会看 tool route、Hit@5、P@1、MRR、RAG 是否调用、LLM parse 是否成功、fallback 原因和延迟。

### Q24：怎么判断一次改动真的让 Agent 变强了？

回答：

> 不能只看 demo，要看消融指标。比如改 RAG，就比较 fast baseline、rag_only、full_llm_all 的 Hit@5/P@1/MRR 和延迟；改 router，就看 expected_tool 命中率和误路由 case；改购物车，就看多轮上下文和确认状态是否稳定。还要检查失败类型有没有从“错误结果”变成“可解释降级”。

### Q25：如果模型或工具调用超时，系统怎么恢复？

回答：

> LLM 调用通常用 `run_with_hard_timeout()` 包一层，超时后记录 `llm_timeout`，上层使用规则结果或模板结果。RAG 用 `ThreadPoolExecutor` 加 timeout，超时则返回 `RetrievalEvidence(status="timeout")` 并走结构化评分。SSE 外层有 `safe_stream()`，未捕获异常也会转成用户可理解的 `error` 事件和 `done`。

### Q26：权限、安全和审计怎么做？

回答：

> 当前项目后端主要做了输入层和输出层安全：prompt injection 检测、最大输入长度、生产模式错误脱敏、诊断接口需要 debug 或 admin token、产品写接口默认关闭、敏感字段如 api key/base URL/model/path 会在 public trace 中隐藏。审计方面有 trace、routing_trace、fact_check、llm_call_log。若做成团队内部工具，还需要补文件/命令权限沙箱、操作审批、用户级审计日志和密钥托管。

### Q27：Agent Harness 和单纯调用 LLM API 的区别是什么？

回答：

> 单纯调用 LLM API 是一次 prompt 到一次 response。Agent Harness 是把 LLM 放进一个可控执行环境：有输入校验、工具 schema、路由策略、状态管理、错误恢复、观测日志和结果校验。这个项目里 LLM 只是 harness 的一个组件，最终结果要经过 catalog、RAG、filter、scorer 和 fact check。

### Q28：你怎么设计 prompt？

回答：

> 项目里 prompt 的目标都比较窄：router prompt 只输出工具 JSON，parse prompt 只输出 RequirementSpec 相关字段，attachment prompt 只输出 OCR/visual attributes，explanation prompt 只能基于 evidence 解释。我的原则是让 prompt 输出结构化结果，并且每个 prompt 都有规则 fallback 或结果校验，不把业务正确性完全交给模型。

### Q29：如果 LLM router 和规则 router 冲突，你相信谁？

回答：

> 当前做法不是无条件相信某一方，而是按风险做纠偏。LLM 成功时优先采用，但 `validate_tool_call()` 会看本地规则和明显关键词。比如闲聊信号被 LLM 判成推荐会降级；明确购物词被 LLM 判成推荐会强制改购物车。这样既利用 LLM 理解复杂表达，又保留确定性 guard。

### Q30：商品推荐的排序公式是什么？

回答：

> 排序由 `scorer.py` 做。基础维度包括 `scenario_match`、`attribute_match`、`price_fit`、`reputation_fit`、`availability_fit`、`sku_fit`、`detail_quality`。权重会根据预算、组合推荐、对比需求、多模态需求动态调整。RAG evidence 会影响 evidence boost 和部分场景/属性匹配分，但硬约束如品类、库存、排除品牌先在 structured filter 处理。

### Q31：为什么不用纯向量召回直接推荐？

回答：

> 电商推荐有很多硬约束，纯向量召回容易把语义相关但预算、库存、品牌排除不满足的商品召回来。当前设计是结构化过滤先保证硬条件，再用向量召回做补充，最后 RRF 融合。即便 vector rescue 允许部分软约束候选回流，也必须通过 hard constraint passed ids，避免 RAG 绕过硬约束。

### Q32：如果用户说“推荐一套旅行装备”，你的项目现在会怎么处理？有什么不足？

回答：

> 如果 parser 能识别出多个品类和 `need_bundle=true`，系统会按多个 category 分别过滤、评分并组一个 shopping bundle。问题是当前场景到品类映射还不够强，用户只说“旅行装备”时，不一定能稳定推出防晒、行李箱、鞋服、耳机等多个品类。后续可以加场景 ontology：场景 -> 必备品类 -> 可选品类 -> 预算分配 -> 组合约束。

### Q33：你怎么看 SFT/RL 在这个项目里的作用？

回答：

> 当前项目没有做 SFT/RL，更偏应用层 Agent + RAG 工程。如果要引入训练，优先不是训练生成回答，而是训练或微调 router/requirement parser，让它稳定输出工具名和结构化字段。RL 可以用于排序策略或多轮澄清策略，但前提是要有点击、加购、成交或人工偏好数据。

### Q34：如果让你把这个项目扩成团队内部工具，你会怎么做？

回答：

> 我会先把主链路收敛，明确 `/api/chat/stream` 是唯一业务入口；再把 LLM 调用迁移到 Gateway；接着加权限系统、密钥管理、审计日志、配置中心和评测流水线。对于数据侧，要把商品库和向量索引构建变成可重复 pipeline，支持版本号和回滚。对于质量侧，每次改 router、RAG 或 scoring 都自动跑 benchmark。

### Q35：如果大型仓库里要跨模块修改，你怎么避免 Agent 一上来乱改？

回答：

> 我会先做架构索引：入口、调用图、数据模型、配置、测试。然后把任务映射到链路上的具体节点，比如这次文档任务就是入口、router、handler、recommendation pipeline、retrieval、session、LLM client、dead code。实现前列出影响面，修改后跑最小验证。对于代码修改，还要保护用户已有改动，不做无关重构。

### Q36：项目里最值得讲的工程亮点是什么？

回答：

> 第一，LLM 不直接决定事实，所有商品来自本地 catalog，并有 fact check。第二，工具路由是 LLM + 规则 + validation guard，不是单点依赖模型。第三，RAG 是可降级增强层，Milvus 不可用时仍能返回结构化推荐。第四，PC 装机是结构化兼容性规划，体现了把业务规则和 LLM 分工。第五，session memory 支持多轮商品、购物车和 PC 方案追问。

### Q37：项目里你会主动承认的不足是什么？

回答：

> 我会说三点：第一，LLM Gateway 设计好了但没迁移，调用点还分散；第二，购物车确认主链路已经修过，但 `/api/cart/actions`、`clear` 和“首项兜底”仍需要收敛；第三，场景化推荐还偏关键词和轻量打分，不是完整场景规划。这样回答反而更可信，也能自然引出我的后续优化方案。

### Q38：如果面试官问你平时怎么用 AI 辅助开发，你怎么回答？

回答：

> 我一般先让 AI 帮我读代码和建立调用图，但关键判断会回到实际代码和测试。实现时我会把需求拆成入口、数据模型、业务逻辑、错误处理、测试和文档几块，让 AI 做局部修改，再用 `rg`、pytest 和接口脚本验证。对 AI 输出我会重点 review：有没有改错链路、有没有绕过现有抽象、有没有引入不可控 LLM 幻觉、有没有漏掉降级和测试。

### Q39：如果要求你解释“本地可审计”，你怎么说？

回答：

> 本地可审计指的是关键业务事实和决策依据可以追到本地数据和代码规则。商品来自 JSON catalog，PC 配件来自本地 parts 数据，RAG evidence 来自本地索引 chunk，推荐分数有 score table 和 reasons，路由有 routing_trace，结果有 fact_check。LLM 可以辅助理解，但不能单独成为商品事实来源。

### Q40：如果问你下一步优化路线，你怎么排序？

回答：

> 我会按风险和收益排：第一继续收敛购物车直接写旁路和高风险兜底；第二收敛主链路和 legacy/debug 旁路；第三把 LLM Gateway 真的接入；第四建立场景化推荐评测集；第五用消融指标验证 RAG/query expansion/guidance 的收益；第六再考虑更复杂的 Browser Agent、SFT 或 RL。

## 四、几个不要夸大的点

- 不要说当前项目已经是通用 Coding Agent；它是电商导购领域 Agent。
- 不要说所有 LLM 调用都走了 `LLMGateway`；当前没有。
- 不要说做了 SFT/RL；当前没有训练链路。
- 不要说做了 GUI grounding 或 Browser Agent；当前没有。
- 不要说场景化推荐已经很强；当前是轻量场景打分和多品类组包。
- 不要说聊天记录落 SQL 数据库；当前 session 主路径是 memory/Redis。

## 五、可以主动展示的代码锚点

| 能力 | 代码位置 |
| --- | --- |
| 主聊天链路 | `rag/api/routes/chat.py` |
| Runtime mode | `rag/api/runtime_context.py` |
| 工具路由 | `rag/recommendation/tool_router.py` |
| 工具执行 | `rag/recommendation/tool_handlers.py` |
| 推荐主入口 | `rag/recommendation/recommendation_pipeline.py` |
| 推荐组包 | `rag/recommendation/package_builder.py` |
| 结构化过滤 | `rag/recommendation/structured_filter.py` |
| 打分排序 | `rag/recommendation/scorer.py` |
| RAG 检索 | `rag/recommendation/retrieval.py` |
| 候选融合 | `rag/recommendation/retrieval_fusion.py` |
| LLM 客户端 | `rag/recommendation/llm_client.py` |
| 多模态附件 | `rag/api/attachments.py` |
| 图片检索 | `rag/recommendation/image_retrieval.py` |
| 会话记忆 | `rag/recommendation/session_state.py`、`rag/recommendation/session_context.py` |
| PC 装机 | `rag/recommendation/pc_build.py`、`rag/recommendation/pc_session_flow.py` |
| 错误脱敏 | `rag/utils/runtime_errors.py` |
| SSE 安全输出 | `rag/api/sse.py` |
