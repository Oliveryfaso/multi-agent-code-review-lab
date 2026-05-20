# Multi-Agent Code Review Lab 项目大纲

更新时间：2026-05-19

## 1. 项目一句话

构建一个面向小到中型代码库的多 Agent 协作平台：用户提出代码理解、问题定位、测试失败分析或轻量修复请求后，系统通过 Planner、Tool Router、Retrieval Critic、Code Search、AST/Symbol、Code Graph、LSP、Patch Ranker、Verifier 和 Monitor 等 Agent 协作，输出带证据、可复现、可评测的结果。

## 2. 项目边界

### 最终呈现形态

这个项目面向本地代码审查、仓库理解和 Agent 工作流评测，因此最终以可运行的 GitHub 工程工具为主，网页展示为辅。

主交付物：

- GitHub repo：完整代码、README、架构文档、运行命令、样例输出。
- CLI 工具：支持本地分析代码库、运行单条任务、批量跑 eval。
- Trace 文件：每次 Agent 调用、工具调用、证据链、失败回退都能落盘。
- Eval report：用 Markdown/HTML 输出指标、失败类型和样例分析。
- 测试与样例代码库：证明工具可复现，不只是页面展示。

辅助交付物：

- 本地 Web trace viewer：查看 Agent 路由、工具调用、证据片段和评测结果。
- Web UI 只用于展示工程过程，不做营销型 landing page。
- 网页不承担核心能力，核心能力必须通过 CLI 和 repo 代码可验证。

### 做什么

- 代码库问答：解释某个功能在哪里实现、调用链如何流转。
- Bug 定位：根据用户描述定位可能的文件、函数和原因。
- 测试失败分析：根据失败日志找到相关代码和 root cause。
- 轻量修复建议：生成小范围 patch 计划，并用测试验证。
- Agent 评测：记录 trace，评估成功率、证据质量、工具调用质量和成本。

### 暂时不做什么

- 不复刻 SWE-bench，不追求 benchmark SOTA。
- 不做完全自动化大规模代码生成。
- 不把整个代码库直接塞进长上下文。
- 不依赖单一向量检索回答代码问题。
- 第一阶段不支持任意语言，先做 Python；第二阶段再加 TypeScript。

## 3. 目标用户问题

第一阶段优先覆盖这些问题：

```text
这个接口在哪里鉴权？
登录失败为什么还能访问接口？
这个函数被哪些地方调用？
这段 pytest 失败日志对应哪个模块的问题？
帮我找出最近一次改动里可能导致测试失败的地方。
给我一个最小修复方案，并说明需要跑哪些测试。
```

## 4. 总体架构

```text
CLI / Local Web Trace Viewer
  -> API Server
  -> Orchestrator
      -> Planner Agent
      -> Tool Router
          -> Text Search Tool: rg
          -> AST Tool: Python ast / tree-sitter
          -> Symbol Graph Tool
          -> Code Graph Tool
          -> LSP Tool
          -> Git Tool
          -> Test Runner Tool
          -> Type Check Tool
          -> Semantic Search Tool
      -> Retrieval Critic Agent
      -> Evidence Memory
      -> Solver Agent
      -> Patch Agent
      -> Patch Ranker Agent
      -> Verifier Agent
      -> Monitor Agent
  -> Trace Store
  -> Eval Runner
  -> Report
```

## 5. Agent 设计

### 5.1 Orchestrator

职责：

- 管理一次用户任务的完整生命周期。
- 调用 Planner 生成计划。
- 调用 Tool Router 执行工具。
- 聚合 evidence。
- 控制最大轮次、最大工具调用数和失败回退。
- 写入 trace。

第一阶段实现建议：

- 先做同步流程。
- 每个任务最多 12 次工具调用。
- 每个失败工具最多 retry 1 次。

### 5.2 Planner Agent

职责：

- 判断任务类型。
- 生成步骤计划。
- 估计风险等级。
- 指定需要的工具族。

任务类型：

- `code_qa`
- `bug_investigation`
- `test_failure_analysis`
- `patch_planning`
- `light_patch`

输出示例：

```json
{
  "intent": "bug_investigation",
  "risk_level": "medium",
  "steps": [
    {
      "goal": "find auth and login entrypoints",
      "tool_family": "text_search"
    },
    {
      "goal": "inspect function definitions and imports",
      "tool_family": "ast"
    },
    {
      "goal": "check recent changes around auth",
      "tool_family": "git"
    }
  ],
  "max_tool_calls": 12
}
```

初期无 LLM API 时：

- 用 `RuleBasedPlanner` 根据关键词生成 plan。
- 后续替换成 LLM Planner，但保持输出 schema 不变。

### 5.3 Tool Router

职责：

- 根据 plan 选择具体工具。
- 判断工具失败后是否 fallback。
- 控制上下文长度和成本。

路由规则：

- 文件名、符号名、错误日志：优先 `rg`。
- 函数、类、import、调用关系：用 AST。
- 定义、引用、类型信息：用 LSP。
- 最近引入的问题：用 git log/diff/blame。
- 修复验证：用 test runner / type checker。
- 用户描述模糊或没有关键词：少量 semantic search。

### 5.4 Retrieval Critic Agent

职责：

- 判断初始检索是否足够支撑回答。
- 对 `empty_recall` 做 query rewrite。
- 对 symbol-level evidence 薄弱场景触发 symbol expansion。
- 把检索质量、建议搜索词和纠错动作写入 Agent Board。

输出示例：

```json
{
  "quality": "partial",
  "empty_recall_count": 2,
  "suggested_terms": ["process_payment", "mark_order_paid"],
  "actions": ["query_rewrite", "symbol_expansion"],
  "rationale": "multiple exploratory queries had empty recall"
}
```

### 5.5 Code Search Agent

工具：

- `rg`
- 文件名索引
- 符号名索引

输出：

- 命中文件。
- 命中行号。
- 片段内容。
- 命中原因。
- 召回置信度。

### 5.6 AST / Symbol Agent

第一阶段 Python：

- 使用 Python 标准库 `ast`。
- 抽取 function、class、import、call expression。
- 生成轻量 Symbol Graph。

第二阶段 TypeScript：

- 使用 `ts-morph` 或 TypeScript compiler API。

输出：

- 文件符号表。
- 函数调用边。
- import/export 关系。
- 关键函数起止行。

### 5.7 Code Graph Agent

职责：

- 构建 file / function / class / import / call / test reference 图。
- 对命中的 symbol 做 local neighborhood expansion。
- 为调用链、影响面、相关测试问题提供图证据。
- 作为传统 RAG 的替代/补充：代码结构优先，语义检索只作为 fallback。

第一版实现：

- 使用 Python AST 建图。
- 记录 `defines`、`imports`、`calls`、`tests` 边。
- 把 matched symbol 的 definition、incoming edge、outgoing edge 写入 Agent Board。

### 5.8 LSP Agent

第二阶段接入。

Python 可选：

- `pyright`
- `jedi-language-server`

TypeScript 可选：

- `typescript-language-server`

能力：

- go-to-definition。
- find-references。
- hover/type info。
- diagnostics。

### 5.9 Evidence Memory

短期记忆：

- 用户目标。
- 当前 plan。
- 已检查文件。
- 工具结果摘要。
- 被排除假设。

长期记忆：

- 项目结构摘要。
- 模块职责摘要。
- 历史 trace。
- 历史失败类型。

存储：

- SQLite: trace、任务、评测结果。
- JSONL: 原始工具调用日志。
- 后续可加向量库：LanceDB / Chroma / SQLite VSS。

### 5.10 Solver Agent

职责：

- 根据 evidence 生成最终回答。
- 每个结论必须绑定证据。
- 给出置信度。
- 明确不确定项。

回答格式：

```json
{
  "answer": "...",
  "evidence": [
    {
      "file": "backend/src/server.py",
      "line_start": 120,
      "line_end": 146,
      "reason": "login endpoint validates password here"
    }
  ],
  "confidence": 0.82,
  "next_steps": ["run auth smoke test"]
}
```

### 5.11 Patch Agent

第三阶段接入。

职责：

- 只做小范围修改。
- 先输出修改计划，再生成 patch。
- 不在证据不足时强行改代码。
- 标注风险和验证命令。

### 5.12 Patch Ranker Agent

职责：

- 接收多个 patch candidate。
- 对每个 candidate 执行 apply/test verification。
- 根据可应用性、测试结果、diff 大小、影响文件数和来源稳定性打分。
- 选择最高分候选，并把 ranking 写入 trace。

第一版打分：

- `patch_apply_check=passed` 高权重。
- `test_check=passed` 高权重。
- 小 diff、单文件修改加分。
- apply/test 失败强扣分。
- deterministic template 有轻微稳定性加分。

### 5.13 Verifier Agent

职责：

- 选择最小测试集合。
- 运行 pytest、lint、typecheck 或 smoke test。
- 解析失败日志。
- 给 Patch Agent 一次受限 retry 机会。

### 5.14 Monitor Agent

职责：

- 汇总 trace。
- 识别失败模式。
- 给出优化建议。

监控指标：

- 工具调用失败率。
- 检索为空率。
- 文件命中率。
- 符号命中率。
- 证据缺失率。
- 幻觉引用率。
- 测试通过率。
- P50/P95 latency。
- token cost。

## 6. MCP / Tool 接口设计

所有工具统一输入输出，方便后续替换为 MCP Server。

### 6.1 工具列表

```text
search_text(query, path_glob, max_results)
parse_ast(file_path, language)
find_symbol(symbol_name)
build_symbol_graph(root_path)
lsp_definition(file_path, line, column)
lsp_references(file_path, line, column)
git_diff(base_ref)
git_log(path, max_commits)
git_blame(file_path, line_start, line_end)
run_tests(test_selector)
run_typecheck()
semantic_search(query, top_k)
```

### 6.2 统一成功格式

```json
{
  "ok": true,
  "tool": "search_text",
  "data": {},
  "summary": "Found 5 matches in 3 files",
  "latency_ms": 38
}
```

### 6.3 统一错误格式

```json
{
  "ok": false,
  "tool": "find_symbol",
  "error_type": "empty_recall",
  "message": "No matching symbols found",
  "retryable": true,
  "suggested_fallback": "semantic_search"
}
```

## 7. LLM Provider 设计

你后续再提供 LLM API。为了不阻塞项目，第一阶段先定义接口。

```python
class LLMProvider:
    async def complete(
        self,
        messages,
        tools=None,
        response_schema=None,
        metadata=None,
    ):
        ...
```

Provider：

- `MockLLMProvider`: 初期默认。
- `OpenAIProvider`: 后续接入。
- `QwenProvider`: 后续接入。
- `DeepSeekProvider`: 后续接入。

统一记录：

- provider。
- model。
- prompt_version。
- input_tokens。
- output_tokens。
- latency_ms。
- tool_calls。
- finish_reason。
- estimated_cost。

## 8. 检索策略

默认顺序：

1. `rg` 精确文本召回。
2. AST/Symbol Graph 结构分析。
3. LSP 定义和引用。
4. git 查最近变更。
5. embedding search 只用于模糊查询补充。

召回不好时的优化：

- Query rewriting：把自然语言转成符号、文件、错误关键词。
- Multi-query：同一意图生成多个搜索词。
- Symbol expansion：从命中文件继续扩展 import、调用者、被调用者。
- Hybrid rerank：精确命中优先，embedding 补充。
- Evidence threshold：证据不足时澄清或降置信度。

## 9. 评测设计

### 9.1 评测集规模

Phase 1：20 条。

Phase 2：50 条。

Phase 4：100 条。

### 9.2 任务类型

- 代码定位。
- 调用链解释。
- Bug root cause。
- 测试失败分析。
- 轻量 patch。

### 9.3 指标

- `task_success_rate`
- `file_hit_rate`
- `symbol_hit_rate`
- `evidence_precision`
- `tool_call_success_rate`
- `empty_recall_rate`
- `hallucinated_reference_rate`
- `patch_apply_rate`
- `test_pass_rate`
- `latency_p50_p95`
- `token_cost_per_task`

### 9.4 评测样例格式

```json
{
  "id": "auth_001",
  "repo": "sample_python_api",
  "query": "登录失败为什么还能访问接口？",
  "task_type": "bug_investigation",
  "expected_files": ["backend/src/server.py"],
  "expected_symbols": ["login", "require_auth"],
  "must_have_evidence": true
}
```

## 10. 推荐技术栈

后端：

- Python。
- FastAPI。
- SQLite。
- Pydantic。
- pytest。

工具：

- `ripgrep`。
- Python `ast`。
- `git` CLI。
- 后续：`pyright` / `jedi-language-server`。

前端：

- 第一阶段不做复杂 UI，优先 CLI + Markdown eval report。
- 第二阶段做本地 Web trace viewer，用于展示 Agent trace、工具调用、证据链和评测结果。
- 不做营销型网站，网页只服务于工程过程可视化。

目录建议：

```text
Multi-Agent-Code-Review-Lab/
  README.md
  docs/
  src/
    agents/
    tools/
    providers/
    memory/
    evals/
    api/
  cli/
  web/
  sample_repos/
  eval_sets/
  traces/
  reports/
  tests/
```

## 11. 阶段路线

### Phase 0：项目骨架

目标：

- 建 Python 包结构。
- 定义 Agent、Tool、Provider、Trace schema。
- 实现 `MockLLMProvider`。

交付：

- CLI 可接收 query。
- 生成一条完整 mock trace。
- README 写清楚安装、运行和样例输出。

### Phase 1：只读代码库 Agent

目标：

- 接入 `rg`。
- 接入 Python AST。
- 接入轻量 Symbol Graph。
- 接入 git diff/log。
- 输出带证据回答。

交付：

- 支持代码定位和 bug investigation。
- 20 条 eval case，覆盖 auth、order、payment、notification、test failure 和 call graph。
- Markdown eval report。
- GitHub 上可直接查看样例 trace 和评测结果。

### Phase 2：Symbol Graph + LSP

目标：

- 强化 Symbol Graph 的 caller/callee 展开。
- 接入 LSP definition/references。
- 支持调用链解释。

交付：

- 50 条 eval case。
- file hit、symbol hit、evidence precision 指标。

### Phase 3：Patch + Verifier

目标：

- Patch Agent 生成小范围修改。
- Verifier Agent 在临时副本中验证 `git apply --check`、应用补丁并运行 pytest/unittest。
- 解析失败日志。
- 默认输出 patch artifact，不直接修改目标仓库。

交付：

- 轻量修复闭环。
- patch apply rate 和 test pass rate 指标。
- `patches/*.patch` 可作为可复现 patch artifact。

### Phase 4：Monitor + Dashboard

目标：

- Monitor Agent 汇总失败类型。
- prompt version 对比。
- provider 对比。
- 本地 Web trace viewer 展示任务过程和评测报告。

交付：

- 100 条 eval case。
- CLI eval report 作为主输出。
- Web trace viewer 作为辅助可视化。
