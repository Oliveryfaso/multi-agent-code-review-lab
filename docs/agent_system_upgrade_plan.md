# Agent System Upgrade Plan

更新时间：2026-05-20

## 1. 现状判断

当前系统已经完成了可运行 MVP：CLI、Web Review Workbench、DeepSeek Provider、Planner/Solver、Agent Board、Tool Router、Retrieval Critic、`rg + AST + Symbol Graph + Code Graph`、patch candidate ranking、patch verification、eval report 和 trace viewer 都能工作。

从“多 Agent 工程系统”的标准看，当前已经不再是单次 LLM 调用 Demo，而是具备了第一版结构化 Agent 协作：

- Agent 有固定共享工作区：`AgentBoard`。
- Tool Router 已独立成 Agent，并为每次工具调用记录 reason。
- Retrieval Critic 已形成检索评估和 query rewrite 闭环。
- Code Graph 已形成第一版结构化代码图检索能力。
- Patch Ranker 已形成多候选补丁验证和排序能力。
- Run State Timeline 已记录关键执行阶段。
- Board Contract Validator 已校验 Agent Board 关键 artifact 和 payload 字段。
- Final Review Agent 已汇总审核证据、置信度、工具状态、contract 和 patch verification，并支持 recovery / human review 标记。
- Monitor 区分硬失败和 `empty_recall`，避免把探索性 miss 误判为工具失败。

但下一阶段仍需要继续深化：

- Orchestrator 仍是同步 pipeline，尚未升级为显式状态图。
- Agent 之间已有 board artifact 和第一版 contract 校验，但还没有类型化 state graph runtime 和恢复机制。
- Code Graph RAG 已有轻量 AST 版本和本地持久化索引，但还没有全局模块摘要和跨语言 LSP 增强。
- Patch Ranker 已有启发式版本，但还没有风险模型、覆盖率信号和 test selection 策略。
- API 成本优化已有稳定 prompt 前缀、evidence 压缩和 DeepSeek 成本遥测，但还没有跨 provider 价格表和 repo-aware semantic cache。

因此下一阶段目标是把当前系统从“结构化多 Agent MVP”升级为“有类型化状态图运行时、有全局模块摘要、有恢复机制、有更强 verifier/ranker 信号”的 Agentic code review system。

## 2. 调研依据

### OpenAI Agents SDK

OpenAI Agents SDK 的核心概念包括 agents、handoffs、guardrails、sessions 和 tracing。这个方向说明工程化 Agent 不只是一次模型调用，而是要有工具、交接、状态和 trace。

可借鉴点：

- 每个 Agent 有明确职责和 instructions。
- Handoff 用于把任务转交给更合适的 Agent。
- Guardrails 用于输入/输出校验。
- Tracing 用于观察 agent run 的执行路径。

参考：<https://openai.github.io/openai-agents-python/>

### LangGraph

LangGraph 强调 durable execution、state、human-in-the-loop、memory 和可控工作流。它的核心启发是：复杂 Agent 应该显式建模状态流转，而不是把所有逻辑塞进一个 prompt。

可借鉴点：

- 用 state graph 表达 Agent 节点和边。
- 支持中断、恢复和长期运行。
- 把 memory 和 state 作为一等工程对象。

参考：<https://docs.langchain.com/oss/python/langgraph/overview>

### Microsoft AutoGen

AutoGen 的 AgentChat 强调多个 Agent 通过消息协作完成任务，包括 assistant、user proxy、tool agent、group chat 等模式。它适合参考多 Agent 对话协议和 agent-to-agent 消息格式。

可借鉴点：

- Agent 通过结构化 message 通信。
- 多 Agent 可由 group chat / manager 调度。
- 工具执行结果应该作为消息进入上下文，而不是只作为局部变量。

参考：<https://microsoft.github.io/autogen/>

### CrewAI

CrewAI 把 agent、task、process、crew 分开建模，强调角色、任务和流程。它的启发是每个 Agent 应该拥有固定责任边界和产物，而不是只在 Orchestrator 内部被动执行。

可借鉴点：

- Agent role / goal / backstory 可转化为工程中的 role / responsibility / artifact。
- Task 有 expected output。
- Process 可以 sequential 或 hierarchical。

参考：<https://docs.crewai.com/>

### MCP

Model Context Protocol 将 tools、resources、prompts 标准化。当前项目的工具接口应逐步向 MCP 风格靠拢，尤其是 tool schema、error schema 和 resource exposure。

可借鉴点：

- 工具需要稳定 schema。
- 代码库、trace、eval report 可以作为 resources。
- Prompt 也应版本化。

参考：<https://modelcontextprotocol.io/introduction>

### GraphRAG

Microsoft GraphRAG 把文档构造成图，并支持 local/global/drift search。对代码库 Agent 来说，Symbol Graph 就是天然的 code graph，后续可以把函数、类、文件、调用边、测试边、git 变更边统一成图。

可借鉴点：

- 不只做 top-k chunk 检索，而是把实体和关系建模。
- 对全局问题使用 graph summary，对局部问题使用邻域扩展。
- 对模糊问题做 drift search。

参考：<https://microsoft.github.io/graphrag/>

### Self-RAG / Corrective RAG

Self-RAG 和 Corrective RAG 都强调检索质量判断和自我纠错。当前项目的 `empty_recall` 和 evidence precision 可以升级为 retrieval evaluator，由 Monitor 或 Critic Agent 判断是否需要 query rewrite、symbol expansion 或 fallback。

参考：

- Self-RAG: <https://arxiv.org/abs/2310.11511>
- Corrective RAG: <https://arxiv.org/abs/2401.15884>

## 3. 升级后的工程目标

目标不是“调用一个大模型回答代码问题”，而是：

> 构建一个 evidence-first 的多 Agent 代码审查系统。每个 Agent 有固定责任区、固定输出 artifact，并通过共享 Agent Board 交换信息；最终输出 review result、证据、风险、修复建议、patch artifact 和 verifier 结果。

## 4. Agent Board 设计

当前已新增 `AgentBoard`，作为所有 Agent 的共享信息板。

分区：

- `task`: Orchestrator 写入用户任务、repo、运行模式。
- `plan`: Planner 写入 intent、risk、search terms、steps。
- `routing`: Tool Router 写入 tool call schedule 和每个调用的 reason。
- `retrieval`: Code Search / Git Agent 写入检索结果和空召回。
- `retrieval_critique`: Retrieval Critic 写入检索质量、空召回数量、建议改写词和纠错动作。
- `code_intelligence`: AST / Symbol Graph / 后续 LSP Agent 写入符号、引用、调用链。
- `code_graph`: Code Graph Agent 写入结构化代码图、局部邻域、调用边、测试引用边。
- `evidence`: Evidence Memory 写入排序后的证据集。
- `review`: Solver 写入最终 review result 和 confidence。
- `patch`: Patch Agent / Patch Ranker 写入候选 patch、source、target files、ranking 和选择理由。
- `verification`: Verifier 写入 apply/test 结果。
- `monitor`: Monitor 写入失败归因和优化建议。

每条 board item 格式：

```json
{
  "agent": "symbol_graph_agent",
  "kind": "symbol_graph",
  "title": "Symbol graph expansion",
  "payload": {},
  "created_at": "..."
}
```

## 5. 下一阶段实现路线

### Phase A：Agent Board 完整化

- 已完成：board schema、trace.board、viewer 展示。
- 下一步：让每个 Agent 从 board 读取输入，而不是只吃函数参数。
- 输出：Agent-to-Agent contract 文档和测试。

### Phase B：Tool Router 独立成 Agent

已完成第一版。

目标：

- 输入：Plan + Board state。
- 输出：tool call schedule。
- 策略：先精确检索，再 AST/Symbol，再 LSP，再 semantic fallback。

当前实现：

- 新增 `ToolRouterAgent`。
- 输出 `ToolCallSpec` schedule。
- Orchestrator 只执行 schedule，不再直接遍历 plan step。
- 每个 tool call trace 中记录 router action、inputs 和 reason。
- Agent Board 新增 `routing` 区块。

下一步：

- Router 接收 Critic feedback 后，根据 budget 和 priority 动态追加 query rewrite / symbol expansion。
- Router 支持 budget 和 priority。
- Router 输出可评测的 routing accuracy。

### Phase C：Retrieval Evaluator / Critic Agent

已完成第一版。

目标：

- 判断 evidence 是否足够。
- 对 `empty_recall` 做 query rewrite。
- 对低 symbol recall 做 symbol expansion。
- 对 hallucinated references 做拒绝输出。

当前实现：

- 新增 `RetrievalCriticAgent`。
- Critic 读取 query、Plan、tool calls、candidate files 和 evidence。
- 输出 `RetrievalCritique` artifact：`quality`、`empty_recall_count`、`candidate_file_count`、`evidence_count`、`suggested_terms`、`actions`、`rationale`。
- Orchestrator 在初始文本检索后调用 Critic，并把结果写入 Agent Board 的 `retrieval_critique` 区块。
- 当 Critic 判断需要 `query_rewrite` 时，Router 追加针对函数名、模块名和中文意图映射出的搜索任务。
- Trace Viewer 展示 `retrieval_critique`，让检索纠错过程可审计。

当前评测影响：

- `avg_expected_file_recall`: 0.942 -> 0.975
- `avg_expected_symbol_recall`: 0.892 -> 0.925
- `tool_call_failure_rate`: 0.0
- `empty_recall_rate`: 0.151，作为可优化 miss 指标，不计入硬失败。

下一步：

- Critic 从固定中文映射升级为轻量 query rewrite policy，可结合 LLM 但保留 deterministic fallback。
- Critic 对 evidence 做 precision / recall 粗估，拒绝没有证据支撑的 Solver 引用。
- Router 支持 Critic feedback 的 tool budget 和 priority，而不是无条件追加搜索。

### Phase D：Code Graph RAG

已完成第一版。

目标：

- 把文件、函数、类、测试、调用、import、git commit 建成 code graph。
- 支持 local graph expansion。
- 支持全局模块摘要。

当前实现：

- 新增 `CodeGraphTool`。
- 使用 Python AST 构建 `file`、`function`、`class` 节点。
- 记录 `defines`、`imports`、`calls`、`tests` 边。
- 对 matched symbols 生成 local neighborhood，包括 definitions、incoming edges、outgoing edges 和 related symbols。
- Orchestrator 在 Symbol Graph 后执行 Code Graph，并把结果写入 Agent Board 的 `code_graph` 区块。
- Evidence Memory 接收 `code_graph` 来源证据，用于调用链、影响面和相关测试解释。

当前评测影响：

- `avg_evidence_count`: 11.9 -> 12.0
- `empty_recall_rate`: 0.151 -> 0.137
- `tool_call_failure_rate`: 0.0

测试：

- `tests/test_code_graph.py`: passed
- `PYTHONPATH=src python3 -m unittest discover -s tests`: passed

### Phase E：Patch Candidate Ranking

已完成第一版。

目标：

- LLM 生成多个 patch candidate。
- Verifier 分别 apply/test。
- Patch Ranker 根据 apply、test、diff size、risk 打分。

当前实现：

- 新增 `PatchRankerAgent`。
- `PatchAgent` 支持 `propose_candidates`，可同时产出 LLM candidate 和 deterministic template candidate。
- Orchestrator 对每个 candidate 单独执行 patch verification。
- Patch Ranker 根据 `patch_apply_check`、`test_check`、diff size、target file count 和 source 稳定性打分。
- 选中的 patch 在 verification 中带 `candidate_ranking`，Trace Viewer 可展示排序依据。

试错修复：

- 初版 ranking 直接引用 candidate 的 verification dict，选中后把 ranking 写回 verification 会形成递归结构，导致 Agent Board 序列化失败。
- 修复方式：Patch Ranker 对 verification 做快照拷贝，并排除已有 `candidate_ranking`。

测试：

- `tests/test_patch_ranker.py`: passed
- `tests/test_patch_agent.py`: passed
- `PYTHONPATH=src python3 -m unittest discover -s tests`: passed

### Phase F：API Cost Optimization

已完成第一版。

目标：

- 降低真实 LLM API 使用成本。
- 不牺牲代码审查准确性。
- 让成本、cache hit ratio 和 token 使用进入 trace。

当前实现：

- 新增 `BASE_AGENT_SYSTEM_PREFIX`，Planner、Solver、Patch Agent 共享稳定 prompt 前缀，提高 DeepSeek Context Caching 命中机会。
- Solver 压缩 LLM evidence payload：最多 8 条 evidence，snippet 最多 180 字符。
- 新增 DeepSeek 成本估算：按 cache-hit input、cache-miss input、output token 分价估算。
- Orchestrator 在 `llm_calls` 中记录 `cost`。
- Monitor 汇总 `llm_cost`，输出 `estimated_cost_usd`、`cache_hit_ratio` 和优化建议。
- `llm-check` 输出成本估算，便于用户确认 API 配置和缓存表现。

选择理由：

- DeepSeek Context Caching 默认开启，且 response usage 暴露 cache hit/miss token，是当前项目最直接、低侵入的成本优化入口。
- 暂不做 semantic cache，因为代码审查结果依赖 repo revision 和 evidence，错误复用风险高。
- 暂不做多模型路由，因为当前真实 provider 只有 DeepSeek。

测试：

- `tests/test_costs.py`: passed
- `tests/test_prompt_optimization.py`: passed
- `PYTHONPATH=src python3 -m unittest discover -s tests`: passed

### Phase G：State Timeline + Artifact Contract Validation

已完成第一版。

目标：

- 提升工具可解释性，让用户知道一次 run 跑到哪一步。
- 提升工程可靠性，让缺失的 Agent artifact 能被自动发现。
- 为后续类型化 state graph runtime 做铺垫。

当前实现：

- 新增 `RunState` schema，Trace 中记录 `state_timeline`。
- Orchestrator 在关键节点记录状态：`task_received`、`planned`、`routed`、`evidence_built`、`solved`、`patching`、`contract_validated`、`completed`。
- 新增 `BoardContractValidator`。
- Validator 校验必需 board sections 和关键 payload 字段。
- 非 patch run 必须包含 task、plan、routing、retrieval、retrieval_critique、code_intelligence、code_graph、evidence、review。
- patch run 额外要求 patch 和 verification。
- Contract report 写入 `metrics.contract_validation`，Trace Viewer 展示 Contract 状态和 State Timeline。

选择理由：

- 直接引入完整 LangGraph 类 runtime 会增加复杂度；当前阶段先显式记录状态和校验 artifact，更符合项目体量。
- Contract validation 可以立刻提升测试价值和用户信任，避免“页面上看起来有内容，但关键 Agent 其实没产物”的问题。

测试：

- `tests/test_contracts.py`: passed
- `tests/test_orchestrator.py`: passed
- `tests/test_patch_agent.py`: passed
- `tests/test_viewer.py`: passed
- `PYTHONPATH=src python3 -m unittest discover -s tests`: passed

当前 eval：

- `task_success_rate`: 1.0
- `file_hit_rate`: 1.0
- `symbol_hit_rate`: 1.0
- `tool_call_failure_rate`: 0.0
- `empty_recall_rate`: 0.137

### Phase H：Persistent Code Graph Index + Visual Observability

已完成第一版。

目标：

- 直接提升重复运行效率，避免每次 run 都重新 AST 建图。
- 提升 Trace Viewer 的用户体验，让工程状态、Agent 协作和健康指标更直观。
- 让项目展示从“表格型 trace 页面”升级为“可观测工程面板”。

当前实现：

- `CodeGraphTool` 新增本地持久化缓存。
- 缓存目录为 `.macr_cache/code_graph/`，不写入被分析仓库。
- 缓存 key 使用 repo 绝对路径 hash。
- 缓存 fingerprint 使用 Python 文件路径、mtime、size。
- Code Graph tool result 返回 `cache.hit`、`cache.path`、`fingerprint` 和 `indexed_file_count`。
- `.macr_cache/` 已加入 `.gitignore`。
- Trace Viewer 新增 `Run Observability` 区块。
- Trace Viewer 新增 SVG `Agent Flow Map`，展示 task -> plan -> routing -> retrieval -> critic -> AST/Symbol -> graph -> evidence -> review -> patch -> verify -> monitor。
- Trace Viewer 新增 `Health Signals`，用动画进度条展示 tool ok、empty recall、evidence、states、contract、cache hit。
- Trace Viewer 新增工具健康概览，保留详细表格供审计。

选择理由：

- 对当前项目最直接的效率瓶颈是重复代码图构建，而不是 LLM 推理本身。
- 持久化索引对用户体感明确：重复运行更快，页面中也能看到 cache hit。
- 前端不引入复杂框架，保持 GitHub-first 工具属性；用原生 SVG/CSS 实现科学可视化和轻量动效。

测试：

- `tests/test_code_graph.py`: passed，覆盖 persistent cache miss -> hit。
- `tests/test_viewer.py`: passed，覆盖新版 Observability 区块。
- `PYTHONPATH=src python3 -m unittest discover -s tests`: passed。

当前 eval：

- `task_success_rate`: 1.0
- `file_hit_rate`: 1.0
- `symbol_hit_rate`: 1.0
- `tool_call_failure_rate`: 0.0
- `empty_recall_rate`: 0.137

### Phase I：Expanded Eval + Final Review Recovery

已完成第一版。

目标：

- 提高评测数据量和复杂度。
- 增加最终综合审核 Agent，避免多个 Agent 局部正确但整体结果不可靠。
- 对低置信度或审核失败场景做一次自动 recovery；仍失败时明确要求人工审核。

当前实现：

- 新增 `FinalReviewAgent`。
- 审核维度包括 solver confidence、evidence 数量、hard tool failure、empty recall、contract validation、patch apply/test、以及查询关键英文标识是否被证据覆盖。
- 若初审失败，Orchestrator 会执行一次 recovery search + AST recovery，并重新调用 Solver。
- 二次仍失败时，`trace.metrics.final_review.human_review_required=true`，CLI 输出需要用户回答的问题。
- CLI 新增 `--clarification`，可把人工补充作为下一轮输入。
- Eval Runner 新增 `final_review_pass_rate` 和 `human_review_required_rate`。
- Trace Viewer 首屏新增 `Final Audit`、`Human Review`，并在审核失败时展示 issue 和人工确认问题。
- 新增 `eval_sets/phase2_complex.jsonl`，包含 30 条复杂样例。
- 新增 `reports/phase2_complex_eval.md`。

Phase2 覆盖：

- 跨模块调用链。
- 测试到生产函数反推。
- patch planning。
- 全局状态变更。
- 用户可见状态码。
- OAuth/Redis/GraphQL 这类仓库中不存在的负例。

当前 Phase2 结果：

- `case_count`: 30
- `task_success_rate`: 1.0
- `file_hit_rate`: 1.0
- `symbol_hit_rate`: 1.0
- `avg_expected_file_recall`: 0.947
- `avg_expected_symbol_recall`: 0.913
- `final_review_pass_rate`: 0.9
- `human_review_required_rate`: 0.1

外部数据集路线：

- SWE-bench Lite 适合后续做真实 issue-to-patch 评测，但需要适配真实 repo checkout、依赖安装和测试隔离。
- CodeSearchNet 适合后续做 code search / query-to-function 检索评测，但它不是完整 code review agent benchmark。
- 当前阶段不直接引入大型外部数据集，避免把项目目标从工程工具变成 benchmark 复刻。

测试：

- `tests/test_final_reviewer.py`: passed
- `tests/test_orchestrator.py`: passed
- `PYTHONPATH=src python3 -m unittest discover -s tests`: passed

### Phase J：Real Data Eval Preparation

已完成第一版准备入口。

目标：

- 支持把公开真实数据集转换为本项目统一 eval JSONL。
- 先评估真实 issue / natural language query 的定位能力，再逐步升级到 patch verification。
- 避免在当前阶段直接引入大规模 repo checkout、依赖安装和测试隔离复杂度。

当前实现：

- 新增 `prepare-real-eval` CLI。
- 支持 `swe-bench`、`codesearchnet`、`github-issue` 三种输入。
- SWE-bench 导入时只把 `problem_statement` 给 Agent；`patch/test_patch` 仅用于离线提取 `expected_files`、`expected_symbols`、`test_selector`。
- CodeSearchNet 导入为 code search eval。
- GitHub issue JSONL 用于手工精选真实 issue。
- 新增文档 `docs/real_data_testing_plan.md`。
- 新增 `reports/real_data_showcase.md`，在主 README 和 Trace Viewer 中展示当前真实数据测试状态。

当前边界：

- 已完成外部数据格式 adapter smoke test。
- 已对本地 `pallets/markupsafe` checkout 执行 4 条 GitHub issue 风格真实外部 eval。
- MarkupSafe eval: `task_success_rate=1.0`、`final_review_pass_rate=1.0`、`tool_call_failure_rate=0.0`、`avg_code_smell_ratio=0.031`。
- 尚未执行完整 SWE-bench Lite benchmark。
- 完整外部 repo eval 需要先准备 `external_repos/` checkout 和 `external_data/repo_map.json`。

外部数据集选择：

- SWE-bench Lite：后续真实 issue-to-patch 主线。
- CodeSearchNet：后续检索召回专项测试。
- 手工 GitHub issue：当前最适合简历展示的小规模真实工程测试。

测试：

- `tests/test_real_data.py`: passed
- `PYTHONPATH=src python3 -m unittest discover -s tests`: passed

### 技术更新记录规范

### Phase K：Visual Output + Code Smell Agent

已完成第一版。

目标：

- 提升 CLI 和 Trace Viewer 输出的可读性，让结果更像工程分析报告，而不是普通 LLM 文本。
- 新增 code smell ratio 模块，量化“屎山代码”占比和维护性风险。
- 将 code smell 结果纳入 Agent Board、Monitor Metrics、Eval Report 和 Final Review。

当前实现：

- CLI 输出新增指标卡、ASCII confidence bar、证据来源分布、Top Evidence、Code Smell Risk。
- Trace Viewer 新增 `Quality & External Eval` 区块。
- Trace Viewer 用 donut chart 展示 code smell ratio，用 status rows 展示外部数据测试状态。
- 新增 `CodeSmellAgent`。
- 新增 `agent-review code-smell --repo ...`。
- Board 新增 `code_quality` 区块，Contract 要求 `code_quality:smell_report`。
- Final Review 会检查 code smell report 是否存在；高风险时输出审核问题。
- Eval Report 新增 `avg_code_smell_ratio` 和每个 case 的 code smell severity。

测试：

- `tests/test_code_smell.py`: passed
- `tests/test_viewer.py`: passed
- `PYTHONPATH=src python3 -m unittest discover -s tests`: `Ran 26 tests`，passed
- Phase1 eval: `avg_code_smell_ratio=0.0`
- Phase2 eval: `avg_code_smell_ratio=0.0`
- MarkupSafe external eval: `case_count=4`、`task_success_rate=1.0`、`final_review_pass_rate=1.0`、`avg_code_smell_ratio=0.031`

### Phase L：Testing Issue Log

已完成第一版。

目标：

- 把测试过程中发现的问题和修复过程沉淀为项目文档。
- 记录失败原因、影响、修复方案和修复后指标。
- 避免项目只展示最终成功结果，而缺少工程化排错过程。

当前实现：

- 原 `docs/testing_issue_log.md` 的详细问题记录已合并进 `docs/multi_agent_system_evolution.md`。
- `docs/testing_issue_log.md` 改为中文迁移说明，不再独立维护。
- README 入口合并为 `多 Agent 系统试错、技术演进与测试问题记录`。
- `docs/multi_agent_system_evolution.md` 增加详细的「测试问题与修复记录」章节。

已记录问题：

- `empty_recall` 展示误导。
- Trace Viewer 横向溢出和重点不突出。
- 缺少 Final Review。
- Phase1 eval 覆盖不足。
- 不存在功能负例容易被附近证据误答。
- 外部数据初期只有 adapter smoke。
- GitHub clone 沙箱网络问题。
- MarkupSafe localization 误触发 test runner。
- 外部英文 issue 词汇规划不足。
- Code Smell Agent 环境兼容和测试 fixture 问题。
- CLI-only 交互门槛偏高，普通用户缺少网页端 review 入口。

测试：

- 文档更新，不涉及运行时逻辑。
- 当前全量测试已随 Web Workbench、Python 3.13+ multipart 兼容、后台 job、上传安全和 GitHub PR 集成覆盖推进到 `Ran 37 tests`，passed。

后续凡是对 Agent 架构、检索路线、patch 生成、LLM Provider、评测指标、trace schema 或工具路由做重大调整，必须同步更新：

- `docs/multi_agent_system_evolution.md`：记录为什么改、原方案问题、替代方案、利弊、实验/测试结果。
- `docs/agent_system_upgrade_plan.md`：更新 Phase 状态、当前实现、下一步。
- `docs/multi_agent_system_evolution.md` 的「测试问题与修复记录」章节：记录测试暴露的问题、影响、修复和修复后指标。
- `README.md`：更新用户可运行能力、命令和最新指标。

每次重大技术更新至少包含：

- 背景问题
- 备选方案
- 最终选择
- 收益
- 代价
- 测试或 eval 结果
- 后续风险

### Phase M：Repo Map + Evidence Governance

已完成第一版。

目标：

- 降低单纯关键词检索带来的偶然性。
- 将重复 evidence、分散 confidence 合并和来源统计收口到统一组件。
- 让项目更接近工程化 code review agent，而不是直接调 LLM API。

参考方向：

- Aider 的 repo map 思路：先构造仓库级结构摘要，再把有限上下文预算给相关文件和符号。
- SWE-agent / OpenHands 的工程启发：Agent 不直接“猜答案”，而是通过明确工具接口、轨迹和可复现 runtime 完成任务。
- SWE-Edit 的方向：把查看代码和执行编辑拆成 Viewer / Editor 子 Agent，减少同一上下文里同时承担探索和补丁格式化的压力。
- PR-Agent / Qodo 的产品启发：输出要面向工程工作流，比如 review、improve、ask、summary，而不是只输出聊天文本。

当前实现：

- 新增 `RepoMapTool`，扫描 Python 文件并抽取 focus files、top symbols、函数签名和 import 摘要。
- Board 新增 `repo_map` 区块，Contract 新增 `repo_map:repository_map` 必填字段。
- Trace Viewer 新增 Repo Map 节点和 Repository Map 展开面板。
- 新增 `EvidenceStore`，统一 evidence 去重、置信度合并和 source counts。
- 清理 `CodeGraphTool` 中的不可达代码。

测试：

- `tests/test_repo_map.py`: passed
- `tests/test_evidence_store.py`: passed
- `PYTHONPATH=src python3 -m unittest discover -s tests`: `Ran 26 tests`，passed

下一步：

- 把 Repo Map 分数接入路由预算，区分 changed files、dependency files、test files。
- 拆出 ViewerAgent / EditorAgent：Viewer 只负责按预算提供代码片段，Editor 只负责基于计划生成 patch。
- 增加多语言 adapter：TypeScript / JavaScript 优先。

### Phase N：PR / Diff Review Workflow

已完成第一版。

目标：

- 把工具从代码问答进一步推进到真实工程工作流。
- 输入 unified diff，输出 PR 风格风险摘要、review comments、测试建议和 final audit。
- 保持低成本 deterministic review gate，不依赖 LLM API。

当前实现：

- 新增 `DiffReviewAgent`。
- 新增 CLI：`agent-review review-diff --repo ... --diff-file ...`。
- Board 新增 `pr_review` 区块。
- Contract 新增 diff review 模式。
- Trace Viewer 新增 `PR / Diff Review` 面板和 Agent Flow 节点。
- 新增 `RuntimeRecorder` / `RunContext`，先在 diff review path 使用。

规则覆盖：

- 高风险执行：`eval`、`exec`、`subprocess.Popen`、`shell=True`。
- 疑似硬编码 secret / token / api_key。
- 裸 `except`、`TODO/FIXME`、debug `print`、`time.sleep`。
- 大 hunk、缺测试、Python 变更测试建议。

测试：

- `tests/test_pr_reviewer.py`: passed
- `tests/test_orchestrator.py`: diff review path passed
- `tests/test_contracts.py`: diff review contract passed
- CLI smoke passed
- `PYTHONPATH=src python3 -m unittest discover -s tests`: `Ran 29 tests`，passed

下一步：

- 将 SARIF / GitHub review comment JSON 接到真实 CI 或 GitHub API。
- 将 changed files 注入 Repo Map / Code Graph，做影响面 review。
- 支持 `git diff` 作为默认输入，减少用户手动传 diff 文件。

### Phase O：Routing Policy / Agent Skipping

已完成第一版。

目标：

- 不让每条数据强制经过每个 Agent。
- 对明显能用规则解决的模块跳过 LLM/API。
- 让每次跳过都有 board artifact，能够解释成本和准确率取舍。

当前实现：

- 新增 `RoutingPolicyAgent`。
- Board 新增 `policy` 区块。
- Contract 新增 `policy:execution_policy` 必填字段。
- Trace Viewer Agent Flow 新增 `Policy` 节点。
- 普通低风险 code QA：默认 `mode=deterministic`，跳过 LLM solver。
- patch / bug / test failure：provider 可用时允许 LLM solver。
- diff review：跳过 planner、text search、AST、symbol graph、LLM solver，走 deterministic diff review。

测试：

- `tests/test_routing_policy.py`: passed
- orchestrator / contract policy board 覆盖 passed
- CLI ask smoke: contract ok
- CLI review-diff smoke: contract ok
- `PYTHONPATH=src python3 -m unittest discover -s tests`: `Ran 32 tests`，passed

下一步：

- 在 eval report 中增加 skipped step count、LLM avoided count、estimated saved cost。
- 增加预算策略：按风险等级控制 max tool calls、repo map focus files 和 LLM token budget。

### Phase P：PR Output Integration

已完成第一版。

目标：

- 让 `review-diff` 更接近真实 PR / CI 工具。
- 未传 `--diff-file` 时直接读取目标仓库 `git diff`。
- 支持机器可消费输出，便于后续接 GitHub Actions、SARIF upload 或 review comments。

当前实现：

- `review-diff --diff-file` 改为可选；默认执行 `git -C <repo> diff --no-ext-diff --unified=80`。
- 新增 `--format text|json|sarif|github`。
- 新增 `--output` 写入 JSON / SARIF / GitHub comments 文件。
- 新增 `pr_outputs.py`，把 diff review artifact 转换为 SARIF 2.1.0 和 GitHub Review Comments JSON。
- 新增 `.github/workflows/macr-review.yml`，在 PR 上生成 SARIF 和 GitHub Review Comments JSON。
- 新增 `scripts/post_github_review.py`，把 `--format github` 输出发布为 GitHub PR review comments。

测试：

- `tests/test_pr_outputs.py`: passed
- `tests/test_github_review.py`: passed
- SARIF CLI smoke passed
- GitHub comments CLI smoke passed
- 默认 `git diff` 输入 smoke passed
- `scripts/post_github_review.py --dry-run`: passed
- `PYTHONPATH=src python3 -m unittest discover -s tests`: `Ran 37 tests`，passed

下一步：

- 在真实 GitHub 仓库 PR 上验证权限、SARIF upload 和 review comments 的端到端效果。
- 处理 fork PR 的只读 token 降级策略。

### Phase Q：Web Review Workbench / 双入口交互

已完成第一版。

目标：

- 让用户不只依赖 CLI，也能在本地网页端完成 review 工作。
- 保持网页端和 CLI 复用同一套 `Orchestrator`、Agent Board、Final Review 和 trace schema。
- 网页端负责交互和可视化，CLI 负责自动化、CI 和可复现批处理。

当前实现：

- `agent-review view` 启动本地 Web Review Workbench + Trace Viewer。
- Web Workbench 支持上传代码 zip。
- 支持 `Codebase question / analysis`：上传 repo zip + 输入 query，后端运行 `Orchestrator.run`。
- 支持 `Diff review`：上传 repo zip + 粘贴或上传 unified diff，后端运行 `Orchestrator.run_diff_review`。
- 表单按模式展示输入区块，避免普通代码库问答和 diff review 的输入项互相干扰。
- Agent Flow Map 改为 Planning / Retrieval / Review / Delivery 分层泳道图，降低节点遮挡和裁剪风险。
- Web review 改为后台 job 执行，页面通过 `/jobs/<id>.json` 轮询状态，完成后自动刷新最新 trace。
- 上传内容解压到 `.macr_uploads/`，并对 zip entry 做路径穿越、symlink、文件数量和解压体积防护。
- 上传工作目录会在 job 结束后清理，历史 upload 目录按 TTL 清理。
- 运行完成后网页直接渲染最新 trace，包括 summary、Agent Flow、health signals、evidence、tool calls、PR review、code smell、final audit 和 eval report。

测试：

- `tests/test_viewer.py`: Workbench 渲染 passed
- `tests/test_viewer.py`: 恶意 zip 路径拒绝 passed
- `tests/test_viewer.py`: zip symlink / zip bomb 拒绝 passed
- `tests/test_viewer.py`: background job status 渲染 passed
- `PYTHONPATH=src python3 -m unittest discover -s tests`: `Ran 37 tests`，passed
- `PYTHONPYCACHEPREFIX=/private/tmp/macr_pycache PYTHONPATH=src python3 -m compileall -q src tests scripts`: passed

当前限制：

- 这是本地单机工作台，不是公网多人服务。
- 30 MB 上传限制适合 demo 和中小型 repo，不适合 monorepo。
- 当前是进程内后台线程和内存 job table；服务重启后 job 状态不会恢复。
- 还没有公网鉴权和系统级沙箱，默认定位仍是本地可信环境工具。

下一步：

- 将后台 job table 持久化，或切换到轻量任务队列。
- 增加更强的系统级隔离，例如容器或只读挂载。
- 给 Web Workbench 加示例 zip / 示例 diff，一键生成演示 trace。

## 6. 简历表达升级

中文：

> 将代码库问答 Demo 升级为 evidence-first 多 Agent 代码审查系统，设计 Agent Board 共享信息板和 Agent-to-Agent artifact 协议，使 Planner、Tool Router、Retrieval Critic、Code Search、AST/Symbol Graph、Code Graph、Solver、Patch、Patch Ranker、Verifier、Monitor 等 Agent 在固定责任区内交换结构化中间产物；基于 `rg + AST + Symbol Graph + Code Graph + DeepSeek + Verifier` 实现代码定位、证据归因、patch 候选生成、测试验证和离线评测，并提供 CLI 与本地 Web Review Workbench 双入口展示每次 run 的 plan、tool calls、evidence、patch ranking、patch verification 与 monitor metrics。

英文：

> Upgraded a codebase QA prototype into an evidence-first multi-agent code review system with a shared Agent Board and structured agent-to-agent artifacts. Implemented Planner, Tool Router, Retrieval Critic, Code Search, AST/Symbol Graph, Code Graph, Solver, Patch, Patch Ranker, Verifier, and Monitor agents with explicit responsibility sections, traceable intermediate outputs, patch candidate ranking, patch verification, offline evaluation, plus CLI and local Web Review Workbench entry points for plan/tool/evidence/verification observability.
