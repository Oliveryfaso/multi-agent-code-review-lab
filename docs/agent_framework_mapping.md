# Agent Framework Mapping

This document maps Multi-Agent Code Review Lab to common agent frameworks and online agent development platforms.

The project intentionally does not depend on a heavy agent framework in its core runtime. It keeps the orchestration explicit so that routing, evidence collection, tool failures, recovery, cost, and final audit behavior remain easy to inspect and test.

## Concept Mapping

| Project Concept | Equivalent In Common Frameworks | Notes |
| --- | --- | --- |
| `Orchestrator` + `CodeReviewWorkflow` | LangGraph graph runner / CrewAI Flow / AutoGen manager | Coordinates the run, records graph node decisions, and persists checkpoints. |
| `AgentBoard` | Shared graph state / conversation memory / workflow state | Stores structured artifacts from each agent instead of relying on hidden prompt context. |
| `RoutingPolicyAgent` | Conditional graph edges / guardrail router / workflow branching | Skips unnecessary agents and avoids LLM/API calls when deterministic logic is enough. |
| `WorkflowCheckpoint` | LangGraph checkpoint / durable execution event | Captures node status, board sections, state count, next nodes, and metrics at key boundaries. |
| `ToolRouterAgent` | Tool selection node / tool-use agent | Produces an explicit tool schedule with reasons. |
| `RetrievalCriticAgent` | Corrective RAG evaluator / retrieval grader | Detects weak recall, empty search results, and missing evidence. |
| `FinalReviewAgent` | Output guardrail / reviewer agent / evaluator node | Audits confidence, evidence, board contract, code smell, and verification before final output. |
| `Run State Timeline` | Trace spans / graph events / observability timeline | Records what happened, in which order, and why. |
| `Web Review Workbench` | Local dev console / workflow runner UI | Runs code review jobs and visualizes trace, evidence, and health signals. |

## LangChain / LangGraph

If someone says "LongChain", they usually mean **LangChain**. For stateful multi-agent workflows, the closer comparison is **LangGraph**.

LangGraph-style mapping now implemented in the project:

- Nodes: Planner, Routing Policy, Tool Router, Retrieval Critic, Code Graph, Solver, Final Review, Monitor.
- State: `AgentBoard`, evidence list, trace metrics, current plan.
- Edges: deterministic transitions plus policy-based skip decisions.
- Checkpoints: policy, repository map, router, evidence/code graph, solver, patch, final review.
- Human-in-the-loop: Final Review can mark `human_review_required` and return questions.

Why this project does not directly use LangGraph yet:

- The current workflow is still small enough to keep explicit.
- Tests can assert exact board artifacts without framework indirection.
- It avoids coupling the core review logic to one ecosystem while preserving a LangGraph-compatible state/node/checkpoint shape.

Potential integration path:

1. Keep current agents and tools unchanged.
2. Wrap each existing workflow node as a LangGraph node.
3. Use `Trace + AgentBoard + EvidenceStore` as graph state.
4. Map current checkpoints to LangGraph persistence/checkpointer events.
5. Keep trace output compatible with the existing viewer.

Reference: LangGraph emphasizes stateful, controllable agent workflows and durable execution concepts in its official docs: <https://docs.langchain.com/oss/python/langgraph/overview>

## OpenAI Agents SDK

OpenAI Agents SDK maps naturally to:

- Agents: Planner, Solver, Patch Agent, Final Review Agent.
- Handoffs: Planner hands off to routing/search/review/patch stages.
- Guardrails: Board Contract Validator and Final Review Agent.
- Tracing: Run State Timeline and tool-call records.
- Tools: `rg`, AST parser, symbol graph, git, test runner, patch verifier.

Current project choice:

- The provider layer keeps DeepSeek, mock provider, and future providers separate from orchestration.
- This avoids making the code review system dependent on one model vendor.

Potential integration path:

- Use OpenAI Agents SDK for LLM-heavy Planner/Solver/Patch stages.
- Keep deterministic retrieval, AST, Symbol Graph, Code Graph, and verifier as local tools.
- Export trace spans into the existing `TraceViewer`.

Reference: OpenAI Agents SDK supports tools, handoffs, guardrails, streamed runs, and tracing: <https://platform.openai.com/docs/guides/agents-sdk/>

## AutoGen / Microsoft Agent Framework

AutoGen is conversation-oriented: agents collaborate through messages. Microsoft Agent Framework extends this direction with state, middleware, telemetry, MCP clients, and graph-based workflows.

Project mapping:

- `AgentBoard` replaces a free-form group chat with structured artifacts.
- Tool calls are explicit `ToolCallSpec` objects instead of natural-language tool requests.
- Final Review is closer to a supervisor agent.
- Monitor Agent is similar to telemetry and middleware hooks.

Why the project uses structured artifacts instead of group chat:

- Code review needs file paths, line numbers, symbols, tests, and patch verification.
- Free-form agent chat is harder to evaluate and easier to hallucinate.
- Structured artifacts make trace and eval metrics easier to compute.

Reference: AutoGen AgentChat is a high-level API for multi-agent applications, while Microsoft Agent Framework includes state management, middleware, telemetry, MCP clients, and graph-based workflows: <https://microsoft.github.io/autogen/> and <https://learn.microsoft.com/en-us/agent-framework/overview/>

## CrewAI

CrewAI maps well to role/task/process thinking:

- Agent roles: Planner, Critic, Solver, Verifier, Monitor.
- Tasks: code localization, evidence collection, risk review, patch verification.
- Process: sequential with deterministic branches and audit gates.
- Flow: Web/CLI run pipeline.

Why this project does not use CrewAI directly:

- CrewAI is useful for role/task orchestration, but this code review tool needs lower-level control over exact tools, evidence schemas, and contract validation.
- The project can still describe agents in a CrewAI-like role/task format without depending on CrewAI runtime.

Reference: CrewAI describes agents, crews, tasks, processes, and flows for coordinating multi-agent automations: <https://docs.crewai.com/en/introduction>

## MCP

MCP is highly relevant to this project because the current tools are already close to MCP-style resources/tools:

- Resources: repository map, trace JSON, eval report, code graph index.
- Tools: text search, AST parse, symbol graph, git diff, test runner, patch verifier.
- Prompts: planner prompt, solver prompt, patch prompt.

Potential integration path:

1. Expose repo map, trace, and eval reports as MCP resources.
2. Expose `rg`, AST, symbol graph, test runner, and patch verifier as MCP tools.
3. Keep the current CLI/Web runner as the local orchestration layer.

This would make the project usable from MCP-compatible coding agents while preserving local deterministic behavior.

Reference: MCP standardizes tools, resources, and prompts for model-context integration: <https://modelcontextprotocol.io/introduction>

## Online Agent Development Platforms

Online platforms such as Dify, LangSmith, and LangGraph Platform are useful references, but they solve different parts of the problem.

| Platform Type | Examples | How It Relates To This Project |
| --- | --- | --- |
| Workflow builder | Dify Workflow / Chatflow | Similar to Web Review Workbench, but this project keeps workflow logic in code for reproducibility. |
| Observability / eval | LangSmith | Similar to Trace Viewer, run metrics, eval reports, and tool-call tracing. |
| Managed graph deployment | LangGraph Platform | Potential future option if the local graph is migrated to LangGraph. |
| No/low-code agent builder | Dify, AutoGen Studio-style tools | Useful for prototyping, but less precise for code-review evidence schemas. |

Reference: Dify provides workflow/chatflow, RAG, agent, model management, and observability capabilities: <https://docs.dify.ai/en/use-dify/getting-started/key-concepts>. LangSmith focuses on tracing, debugging, evaluation, and monitoring LLM/agent applications: <https://docs.langchain.com/langsmith/reference-overview>.

## Practical Positioning

This project can be described as:

> A local-first, framework-agnostic multi-agent code review system. It implements the core ideas found in LangGraph, Agents SDK, AutoGen, CrewAI, MCP, and agent observability platforms, but keeps the code review workflow explicit and testable.

The important engineering point is not whether a specific framework is used. The important point is that the project has:

- explicit agent responsibilities,
- structured shared state,
- deterministic tools,
- evidence-first retrieval,
- routing and skip policy,
- final audit,
- trace and eval metrics,
- local Web/CLI workflow,
- GitHub PR integration.

## Future Integration Options

Recommended order:

1. Add MCP server exports for tools/resources.
2. Add optional LangSmith-compatible trace export.
3. Add a LangGraph adapter that wraps the existing agents without rewriting them.
4. Add Dify/low-code integration only as a demo bridge, not as the core runtime.
