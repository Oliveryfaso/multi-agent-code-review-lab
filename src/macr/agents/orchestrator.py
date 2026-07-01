from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from macr.agents.code_smell import CodeSmellAgent
from macr.agents.critic import RetrievalCriticAgent
from macr.agents.final_reviewer import FinalReviewAgent
from macr.agents.monitor import MonitorAgent
from macr.agents.patcher import PatchAgent
from macr.agents.patch_ranker import PatchRankerAgent
from macr.agents.planner import LlmPlanner, RuleBasedPlanner
from macr.agents.pr_reviewer import DiffReviewAgent
from macr.agents.router import ToolRouterAgent
from macr.agents.routing_policy import RoutingPolicyAgent
from macr.agents.solver import SolverAgent
from macr.contracts import BoardContractValidator
from macr.costs import estimate_llm_cost
from macr.memory.board import AgentBoard
from macr.memory.evidence_store import EvidenceStore
from macr.memory.trace_store import TraceStore
from macr.providers.base import LLMProvider, LLMResponse
from macr.runtime import RunContext, RuntimeRecorder
from macr.schemas import Evidence, RunState, Trace
from macr.tools.ast_tool import PythonAstTool
from macr.tools.code_graph import CodeGraphTool
from macr.tools.git_tool import GitTool
from macr.tools.patch_verifier import PatchVerifierTool
from macr.tools.repo_map import RepoMapTool
from macr.tools.search import TextSearchTool
from macr.tools.symbol_graph import SymbolGraphTool
from macr.tools.test_runner import TestRunnerTool
from macr.workflow import CodeReviewWorkflow, WorkflowCheckpoint


class Orchestrator:
    def __init__(
        self,
        trace_store: TraceStore | None = None,
        provider: LLMProvider | None = None,
        use_llm_planner: bool = False,
    ) -> None:
        self.provider = provider
        self.use_llm_planner = use_llm_planner
        self.rule_planner = RuleBasedPlanner()
        self.planner = LlmPlanner(provider, self.rule_planner) if provider and use_llm_planner else self.rule_planner
        self.search = TextSearchTool()
        self.repo_map = RepoMapTool()
        self.ast = PythonAstTool()
        self.symbol_graph = SymbolGraphTool()
        self.code_graph = CodeGraphTool()
        self.code_smell = CodeSmellAgent()
        self.git = GitTool()
        self.tests = TestRunnerTool()
        self.router = ToolRouterAgent()
        self.policy_agent = RoutingPolicyAgent()
        self.critic = RetrievalCriticAgent()
        self.solver = SolverAgent(provider=provider)
        self.final_reviewer = FinalReviewAgent()
        self.patcher = PatchAgent(provider=provider)
        self.pr_reviewer = DiffReviewAgent()
        self.patch_ranker = PatchRankerAgent()
        self.patch_verifier = PatchVerifierTool()
        self.monitor = MonitorAgent()
        self.contracts = BoardContractValidator()
        self.trace_store = trace_store or TraceStore(Path("traces"))
        self.workflow = CodeReviewWorkflow()

    def run(self, repo_path: Path, query: str, test_selector: str | None = None) -> Trace:
        return self._run(repo_path, query, test_selector, generate_patch=False)

    def run_patch(
        self,
        repo_path: Path,
        query: str,
        test_selector: str | None = None,
        prefer_llm_patch: bool = False,
    ) -> Trace:
        return self._run(repo_path, query, test_selector, generate_patch=True, prefer_llm_patch=prefer_llm_patch)

    def run_diff_review(self, repo_path: Path, diff_text: str) -> Trace:
        repo_path = repo_path.resolve()
        trace = Trace(query="Review provided unified diff", repo_path=str(repo_path))
        context = RunContext(repo_path=repo_path, query=trace.query, trace=trace, board=AgentBoard())
        recorder = RuntimeRecorder(context.trace)
        board = context.board
        checkpoints: list[WorkflowCheckpoint] = []
        policy = self.policy_agent.diff_review_policy(provider_available=bool(self.provider))
        recorder.record("task_received", "completed", "Diff review task accepted", {"diff_chars": len(diff_text)})
        board.post(
            "task",
            "orchestrator",
            "diff_review_request",
            "Incoming diff review task",
            {"repo_path": str(repo_path), "diff_chars": len(diff_text), "generate_patch": False},
        )
        board.post(
            "policy",
            "routing_policy_agent",
            "execution_policy",
            "Diff review routing policy",
            policy.to_dict(),
        )
        self._post_workflow_graph(board, mode="diff", policy=policy)
        checkpoints.append(
            self._workflow_checkpoint(
                "policy",
                "completed",
                "Diff review policy skipped planner/search and routed to repository map.",
                trace,
                board,
                next_nodes=["repo_map"],
                metrics={"skipped_steps": len(policy.skipped_steps)},
            )
        )
        repo_map_result = self.repo_map.run(repo_path, [])
        trace.tool_calls.append(self._tool_record(repo_map_result))
        board.post(
            "repo_map",
            "repo_map_agent",
            "repository_map",
            "Repository structure for diff review",
            repo_map_result.data if isinstance(repo_map_result.data, dict) else {"summary": repo_map_result.summary},
        )
        recorder.record(
            "repo_mapped",
            "completed",
            "Repo Map Agent summarized repository before diff review",
            {"mapped_files": repo_map_result.data.get("mapped_files", 0) if isinstance(repo_map_result.data, dict) else 0},
        )
        checkpoints.append(
            self._workflow_checkpoint(
                "repo_map",
                "completed",
                "Repository map checkpoint persisted before PR diff review.",
                trace,
                board,
                next_nodes=["pr_review"],
            )
        )
        review_report = self.pr_reviewer.review(diff_text)
        board.post(
            "pr_review",
            "diff_review_agent",
            "diff_review",
            "Deterministic diff review",
            review_report,
        )
        recorder.record(
            "diff_reviewed",
            "completed",
            "Diff Review Agent produced risk summary and comments",
            {
                "risk_level": review_report["risk_level"],
                "changed_file_count": review_report["changed_file_count"],
                "comment_count": len(review_report["comments"]),
            },
        )
        checkpoints.append(
            self._workflow_checkpoint(
                "pr_review",
                "completed",
                "Diff review artifact is available for final audit.",
                trace,
                board,
                next_nodes=["code_quality", "final_review"],
                metrics={"comment_count": len(review_report["comments"]), "risk_level": review_report["risk_level"]},
            )
        )
        smell_report = self.code_smell.analyze(repo_path)
        board.post(
            "code_quality",
            "code_smell_agent",
            "smell_report",
            "Repository maintainability context for diff review",
            smell_report,
        )
        contract_report = self.contracts.validate(board.to_dict(), diff_review=True)
        final_report = {
            "ok": review_report["risk_level"] != "high" and contract_report.ok,
            "confidence": 0.82 if review_report["risk_level"] != "high" else 0.66,
            "issues": [comment["message"] for comment in review_report["comments"] if comment["severity"] in {"high", "medium"}][:8],
            "retry_recommended": False,
            "human_review_required": review_report["risk_level"] == "high" or not contract_report.ok,
            "questions": self._diff_review_questions(review_report, contract_report.to_dict()),
        }
        board.post(
            "final_review",
            "final_review_agent",
            "audit_report",
            "Diff review final audit",
            final_report,
        )
        checkpoints.append(
            self._workflow_checkpoint(
                "final_review",
                "completed",
                "Final audit checkpoint completed for diff review.",
                trace,
                board,
                next_nodes=["monitor"],
                metrics={"human_review_required": final_report["human_review_required"]},
            )
        )
        workflow_summary = self.workflow.summarize(mode="diff", policy=policy, checkpoints=checkpoints)
        trace.board = board.to_dict()
        trace.metrics["code_smell"] = smell_report
        trace.metrics["workflow"] = workflow_summary
        trace.metrics = self.monitor.summarize(trace)
        trace.metrics["code_smell"] = smell_report
        trace.metrics["contract_validation"] = contract_report.to_dict()
        trace.metrics["final_review"] = final_report
        trace.metrics["diff_review"] = {
            "risk_level": review_report["risk_level"],
            "changed_file_count": review_report["changed_file_count"],
            "comment_count": len(review_report["comments"]),
            "test_suggestion_count": len(review_report["test_suggestions"]),
        }
        trace.metrics["workflow"] = workflow_summary
        board.post("monitor", "monitor", "run_metrics", "Monitor summary", trace.metrics)
        trace.board = board.to_dict()
        self._post_workflow_checkpoints(board, checkpoints)
        trace.board = board.to_dict()
        recorder.record("completed", "completed", "Diff review trace saved with board artifacts")
        self.trace_store.save(trace)
        return trace

    def _run(
        self,
        repo_path: Path,
        query: str,
        test_selector: str | None = None,
        generate_patch: bool = False,
        prefer_llm_patch: bool = False,
    ) -> Trace:
        repo_path = repo_path.resolve()
        trace = Trace(query=query, repo_path=str(repo_path))
        board = AgentBoard()
        checkpoints: list[WorkflowCheckpoint] = []
        self._state(trace, "task_received", "completed", "User task accepted", {"generate_patch": generate_patch})
        board.post(
            "task",
            "orchestrator",
            "user_query",
            "Incoming task",
            {"query": query, "repo_path": str(repo_path), "generate_patch": generate_patch},
        )
        if isinstance(self.planner, LlmPlanner):
            plan, planner_response = self.planner.plan(query)
            if planner_response:
                trace.llm_calls.append(self._llm_record(planner_response, role="planner"))
        else:
            plan = self.planner.plan(query)
        trace.plan = plan
        self._state(trace, "planned", "completed", "Planner produced execution plan", {"intent": plan.intent, "steps": len(plan.steps)})
        board.post(
            "plan",
            "planner",
            "execution_plan",
            "Planner output",
            {
                "intent": plan.intent,
                "risk_level": plan.risk_level,
                "search_terms": plan.search_terms,
                "steps": [{"goal": step.goal, "tool_family": step.tool_family, "query": step.query} for step in plan.steps],
            },
        )
        policy = self.policy_agent.decide(
            plan,
            provider_available=bool(self.provider),
            llm_planner_enabled=self.use_llm_planner,
            generate_patch=generate_patch,
            test_selector=test_selector,
        )
        board.post(
            "policy",
            "routing_policy_agent",
            "execution_policy",
            "Agent execution and LLM gating policy",
            policy.to_dict(),
        )
        self._state(
            trace,
            "policy_decided",
            "completed",
            "Routing Policy Agent decided which agents and API calls are justified",
            {
                "mode": policy.mode,
                "use_llm_solver": policy.use_llm_solver,
                "skipped_steps": len(policy.skipped_steps),
            },
        )
        self._post_workflow_graph(board, mode="patch" if generate_patch else "ask", policy=policy)
        checkpoints.append(
            self._workflow_checkpoint(
                "policy",
                "completed",
                "Policy checkpoint records execute/skip decisions before tool routing.",
                trace,
                board,
                next_nodes=["repo_map", "router"],
                metrics={"executed_steps": len(policy.executed_steps), "skipped_steps": len(policy.skipped_steps)},
            )
        )
        evidence = EvidenceStore()
        candidate_files: set[str] = set()
        file_match_lines: dict[str, set[int]] = {}
        repo_map_result = self.repo_map.run(repo_path, plan.search_terms)
        trace.tool_calls.append(self._tool_record(repo_map_result))
        if repo_map_result.ok and isinstance(repo_map_result.data, dict):
            focus_files = repo_map_result.data.get("focus_files", [])
            for item in focus_files[:8]:
                if item.get("score", 0) > 0:
                    candidate_files.add(item["file"])
        board.post(
            "repo_map",
            "repo_map_agent",
            "repository_map",
            "Repository structure and focus files",
            repo_map_result.data if isinstance(repo_map_result.data, dict) else {"summary": repo_map_result.summary},
        )
        self._state(
            trace,
            "repo_mapped",
            "completed",
            "Repo Map Agent summarized repository symbols and focus files",
            {
                "mapped_files": repo_map_result.data.get("mapped_files", 0) if isinstance(repo_map_result.data, dict) else 0,
                "focus_files": len(repo_map_result.data.get("focus_files", [])) if isinstance(repo_map_result.data, dict) else 0,
            },
        )
        checkpoints.append(
            self._workflow_checkpoint(
                "repo_map",
                "completed",
                "Repository map checkpoint captured focus files before retrieval.",
                trace,
                board,
                next_nodes=["router", "retrieval"],
            )
        )
        schedule = self.router.route(plan, board, test_selector)
        self._state(trace, "routed", "completed", "Tool Router produced schedule", {"tool_calls_planned": len(schedule)})
        checkpoints.append(
            self._workflow_checkpoint(
                "router",
                "completed",
                "Tool Router checkpoint captured the planned tool schedule.",
                trace,
                board,
                next_nodes=self._next_nodes_from_schedule(schedule),
                metrics={"tool_calls_planned": len(schedule)},
            )
        )

        for spec in schedule:
            if spec.action == "search_text":
                self._execute_text_search(repo_path, spec, trace, board, candidate_files, file_match_lines, evidence)
            elif spec.action == "parse_candidate_files":
                critique = self.critic.critique(query, plan, trace.tool_calls, candidate_files, evidence)
                board.post(
                    "retrieval_critique",
                    "retrieval_critic",
                    "retrieval_quality",
                    "Retrieval quality assessment",
                    {
                        "quality": critique.quality,
                        "empty_recall_count": critique.empty_recall_count,
                        "candidate_file_count": critique.candidate_file_count,
                        "evidence_count": critique.evidence_count,
                        "suggested_terms": critique.suggested_terms,
                        "actions": critique.actions,
                        "rationale": critique.rationale,
                    },
                )
                if "query_rewrite" in critique.actions:
                    for term in critique.suggested_terms[:3]:
                        rewrite_spec = self.router.make_search_spec(term, f"RetrievalCritic suggested rewrite `{term}`")
                        self._execute_text_search(repo_path, rewrite_spec, trace, board, candidate_files, file_match_lines, evidence)
                for rel_file in sorted(candidate_files)[: spec.inputs.get("candidate_limit", 8)]:
                    if not rel_file.endswith(".py"):
                        continue
                    result = self.ast.run(repo_path, rel_file, plan.search_terms, sorted(file_match_lines.get(rel_file, set())))
                    trace.tool_calls.append(self._tool_record(result, spec))
                    board.post(
                        "code_intelligence",
                        "ast_agent",
                        "symbols",
                        f"AST symbols for {rel_file}",
                        {
                            "ok": result.ok,
                            "summary": result.summary,
                            "symbols": result.data.get("symbols", []) if isinstance(result.data, dict) else [],
                        },
                    )
                    for item in result.data.get("symbols", []) if result.ok else []:
                        reason = f"AST found relevant {item['kind']} `{item['name']}`"
                        if item.get("matched_by") == "focus_line":
                            reason = f"AST mapped matched lines to {item['kind']} `{item['name']}`"
                        confidence = 0.79 if item.get("matched_by") == "term" else 0.7
                        evidence.add(
                            Evidence(
                                file=rel_file,
                                line_start=item["line_start"],
                                line_end=item["line_end"],
                                reason=reason,
                                symbol=item["name"],
                                source_tool="parse_ast",
                                confidence=confidence,
                            )
                        )
            elif spec.action == "build_symbol_graph":
                result = self.symbol_graph.run(repo_path, plan.search_terms, sorted(candidate_files)[: spec.inputs.get("candidate_limit", 12)])
                trace.tool_calls.append(self._tool_record(result, spec))
                board.post(
                    "code_intelligence",
                    "symbol_graph_agent",
                    "symbol_graph",
                    "Symbol graph expansion",
                    {
                        "ok": result.ok,
                        "summary": result.summary,
                        "matched_symbols": result.data.get("matched_symbols", []) if isinstance(result.data, dict) else [],
                    },
                )
                for symbol in result.data.get("matched_symbols", []) if result.ok else []:
                    for definition in symbol.get("definitions", [])[:3]:
                        evidence.add(
                            Evidence(
                                file=definition["file"],
                                line_start=definition["line_start"],
                                line_end=definition["line_end"],
                                reason=f"Symbol graph found {definition['kind']} `{symbol['symbol']}`",
                                symbol=symbol["symbol"],
                                source_tool="symbol_graph",
                                confidence=0.76,
                            )
                        )
                    for reference in symbol.get("references", [])[:3]:
                        evidence.add(
                            Evidence(
                                file=reference["file"],
                                line_start=reference["line"],
                                line_end=reference["line"],
                                reason=f"Symbol graph found reference to `{symbol['symbol']}`",
                                symbol=symbol["symbol"],
                                source_tool="symbol_graph",
                                confidence=0.68,
                            )
                        )
            elif spec.action == "build_code_graph":
                result = self.code_graph.run(repo_path, plan.search_terms, sorted(candidate_files)[: spec.inputs.get("candidate_limit", 16)])
                trace.tool_calls.append(self._tool_record(result, spec))
                board.post(
                    "code_graph",
                    "code_graph_agent",
                    "local_graph",
                    "Code graph neighborhoods",
                    {
                        "ok": result.ok,
                        "summary": result.summary,
                        "node_count": result.data.get("node_count", 0) if isinstance(result.data, dict) else 0,
                        "edge_count": result.data.get("edge_count", 0) if isinstance(result.data, dict) else 0,
                        "neighborhoods": result.data.get("neighborhoods", [])[:8] if isinstance(result.data, dict) else [],
                    },
                )
                for neighborhood in result.data.get("neighborhoods", []) if result.ok else []:
                    symbol = neighborhood.get("symbol")
                    for definition in neighborhood.get("definitions", [])[:2]:
                        evidence.add(
                            Evidence(
                                file=definition["file"],
                                line_start=definition["line_start"],
                                line_end=definition["line_end"],
                                reason=f"Code graph located {definition['kind']} `{symbol}` and its local neighborhood",
                                symbol=symbol,
                                source_tool="code_graph",
                                confidence=0.78,
                            )
                        )
                    for edge in neighborhood.get("incoming", [])[:2]:
                        evidence.add(
                            Evidence(
                                file=edge["file"],
                                line_start=edge["line"],
                                line_end=edge["line"],
                                reason=f"Code graph found {edge['relation']} edge `{edge.get('from_symbol')}` -> `{symbol}`",
                                symbol=symbol,
                                source_tool="code_graph",
                                confidence=0.72,
                            )
                        )
                    for edge in neighborhood.get("outgoing", [])[:2]:
                        evidence.add(
                            Evidence(
                                file=edge["file"],
                                line_start=edge["line"],
                                line_end=edge["line"],
                                reason=f"Code graph found {edge['relation']} edge `{symbol}` -> `{edge.get('to_symbol')}`",
                                symbol=symbol,
                                source_tool="code_graph",
                                confidence=0.72,
                            )
                        )
            elif spec.action == "git_log":
                result = self.git.run(repo_path, sorted(candidate_files)[: spec.inputs.get("candidate_limit", 5)])
                trace.tool_calls.append(self._tool_record(result, spec))
                board.post("retrieval", "git_agent", "git_log", "Recent changes", {"summary": result.summary, "router_reason": spec.reason, "data": result.data})
            elif spec.action == "run_tests" and test_selector:
                result = self.tests.run(repo_path, spec.inputs.get("test_selector", test_selector))
                trace.tool_calls.append(self._tool_record(result, spec))
                board.post("verification", "verifier", "test_result", "Test runner output", {"ok": result.ok, "summary": result.summary, "data": result.data})

        self._state(
            trace,
            "evidence_built",
            "completed",
            "Retrieval and code intelligence produced evidence",
            {"candidate_files": len(candidate_files), "evidence_count": len(evidence), "tool_calls": len(trace.tool_calls)},
        )
        checkpoints.append(
            self._workflow_checkpoint(
                "code_graph",
                "completed",
                "Evidence checkpoint captured retrieval, AST, symbol graph, and code graph outputs.",
                trace,
                board,
                next_nodes=["code_quality", "solver"],
                metrics={"candidate_files": len(candidate_files), "evidence_count": len(evidence)},
            )
        )
        smell_report = self.code_smell.analyze(repo_path)
        board.post(
            "code_quality",
            "code_smell_agent",
            "smell_report",
            "Maintainability risk and code smell ratio",
            smell_report,
        )
        self._state(
            trace,
            "code_quality_checked",
            "completed",
            "Code Smell Agent computed maintainability risk",
            {
                "smell_ratio": smell_report["smell_ratio"],
                "severity": smell_report["severity"],
                "hotspots": len(smell_report["hotspots"]),
            },
        )
        solver = self.solver if policy.use_llm_solver else SolverAgent()
        trace.answer, llm_response = solver.solve(query, plan, evidence.items())
        if llm_response:
            trace.llm_calls.append(self._llm_record(llm_response, role="solver"))
        retry_count = 0
        preliminary_review = self.final_reviewer.review(query, plan, trace.answer, evidence.items(), trace.tool_calls, retry_count=retry_count)
        if preliminary_review.retry_recommended:
            retry_count = 1
            recovery_terms = self.final_reviewer.recovery_terms(query, plan, preliminary_review.issues)
            self._state(
                trace,
                "recovery",
                "running",
                "Final Review Agent requested evidence recovery",
                {"issues": preliminary_review.issues, "terms": recovery_terms},
            )
            board.post(
                "recovery",
                "final_review_agent",
                "recovery_request",
                "Evidence recovery requested",
                {"issues": preliminary_review.issues, "terms": recovery_terms, "questions": preliminary_review.questions},
            )
            self._recover_evidence(repo_path, recovery_terms, plan, trace, board, candidate_files, file_match_lines, evidence)
            trace.answer, llm_response = solver.solve(query, plan, evidence.items())
            if llm_response:
                trace.llm_calls.append(self._llm_record(llm_response, role="solver_retry"))
            board.post(
                "recovery",
                "orchestrator",
                "recovery_result",
                "Evidence recovery completed",
                {"evidence_count": len(evidence), "answer_confidence": trace.answer.confidence if trace.answer else 0},
            )
            self._state(
                trace,
                "recovery",
                "completed",
                "Evidence recovery and solver retry completed",
                {"evidence_count": len(evidence), "answer_confidence": trace.answer.confidence if trace.answer else 0},
            )
        self._state(
            trace,
            "solved",
            "completed",
            "Solver produced evidence-backed review",
            {"answer_evidence_count": len(trace.answer.evidence) if trace.answer else 0},
        )
        checkpoints.append(
            self._workflow_checkpoint(
                "solver",
                "completed",
                "Solver checkpoint captured the evidence-backed answer.",
                trace,
                board,
                next_nodes=["final_review", "patch" if generate_patch else "monitor"],
                metrics={"answer_confidence": trace.answer.confidence if trace.answer else 0},
            )
        )
        board.post(
            "evidence",
            "evidence_memory",
            "evidence_set",
            "Ranked evidence",
            {
                "count": len(evidence),
                "source_counts": evidence.source_counts(),
                "items": evidence.to_payload_items(),
            },
        )
        board.post(
            "review",
            "solver",
            "final_review",
            "Evidence-backed answer",
            {
                "answer": trace.answer.answer if trace.answer else "",
                "confidence": trace.answer.confidence if trace.answer else 0,
                "next_steps": trace.answer.next_steps if trace.answer else [],
            },
        )
        if generate_patch:
            self._state(trace, "patching", "running", "Patch Agent generating candidates")
            patch_candidates, patch_response = self.patcher.propose_candidates(
                repo_path,
                query,
                plan,
                trace.answer.evidence if trace.answer else evidence.items(),
                prefer_llm=prefer_llm_patch,
            )
            if patch_response:
                trace.llm_calls.append(self._llm_record(patch_response, role="patcher"))
            for index, candidate in enumerate(patch_candidates):
                board.post(
                    "patch",
                    "patch_agent",
                    "patch_candidate",
                    f"Patch candidate {index + 1}",
                    {
                        "summary": candidate.summary,
                        "source": candidate.source,
                        "target_files": candidate.target_files,
                        "diff_preview": candidate.diff[:2000],
                    },
                )
                verify_result = self.patch_verifier.run(repo_path, candidate.diff, test_selector)
                candidate.verification = verify_result.data
                trace.tool_calls.append(self._tool_record(verify_result))
            trace.patch, patch_ranking = self.patch_ranker.choose(patch_candidates)
            trace.patch.verification["candidate_ranking"] = patch_ranking
            board.post(
                "patch",
                "patch_ranker",
                "patch_ranking",
                "Patch candidate ranking",
                {"ranking": patch_ranking, "selected_source": trace.patch.source},
            )
            board.post(
                "verification",
                "verifier",
                "patch_verification",
                "Patch verification",
                {"verification": trace.patch.verification, "source": trace.patch.source},
            )
            self._state(
                trace,
                "patching",
                "completed",
                "Patch candidates verified and ranked",
                {"candidate_count": len(patch_candidates), "selected_source": trace.patch.source},
            )
            checkpoints.append(
                self._workflow_checkpoint(
                    "patch",
                    "completed",
                    "Patch checkpoint captured candidate ranking and verification.",
                    trace,
                    board,
                    next_nodes=["final_review"],
                    metrics={"candidate_count": len(patch_candidates), "selected_source": trace.patch.source},
                )
            )
        pre_monitor_board = board.to_dict()
        contract_report = self.contracts.validate(pre_monitor_board, generate_patch=generate_patch)
        self._state(
            trace,
            "contract_validated",
            "completed" if contract_report.ok else "failed",
            "Agent Board contract validation completed",
            {"violation_count": len(contract_report.violations)},
        )
        trace.board = pre_monitor_board
        trace.metrics["code_smell"] = smell_report
        final_report = self.final_reviewer.review(
            query,
            plan,
            trace.answer,
            trace.answer.evidence if trace.answer else evidence.items(),
            trace.tool_calls,
            board=pre_monitor_board,
            patch=trace.patch,
            contract_report=contract_report.to_dict(),
            retry_count=retry_count,
        )
        if final_report.human_review_required and trace.answer:
            trace.answer.next_steps = list(dict.fromkeys([*final_report.questions, *trace.answer.next_steps]))
            trace.answer.answer = (
                f"{trace.answer.answer} 最终审核仍发现不确定点，建议人工确认后再采纳。"
            )
        board.post(
            "final_review",
            "final_review_agent",
            "audit_report",
            "Cross-agent final audit",
            asdict(final_report),
        )
        checkpoints.append(
            self._workflow_checkpoint(
                "final_review",
                "completed" if final_report.ok else "needs_review",
                "Final audit checkpoint completed before monitor summary.",
                trace,
                board,
                next_nodes=["monitor"],
                metrics={"confidence": final_report.confidence, "human_review_required": final_report.human_review_required},
            )
        )
        workflow_summary = self.workflow.summarize(
            mode="patch" if generate_patch else "ask",
            policy=policy,
            checkpoints=checkpoints,
        )
        trace.metrics["code_smell"] = smell_report
        trace.metrics["workflow"] = workflow_summary
        trace.metrics = self.monitor.summarize(trace)
        trace.metrics["code_smell"] = smell_report
        trace.metrics["contract_validation"] = contract_report.to_dict()
        trace.metrics["final_review"] = asdict(final_report)
        trace.metrics["workflow"] = workflow_summary
        board.post("monitor", "monitor", "run_metrics", "Monitor summary", trace.metrics)
        trace.board = board.to_dict()
        self._post_workflow_checkpoints(board, checkpoints)
        trace.board = board.to_dict()
        self._state(trace, "completed", "completed", "Trace saved with metrics and board artifacts")
        self.trace_store.save(trace)
        return trace

    def _execute_text_search(
        self,
        repo_path: Path,
        spec,
        trace: Trace,
        board: AgentBoard,
        candidate_files: set[str],
        file_match_lines: dict[str, set[int]],
        evidence: EvidenceStore,
    ) -> None:
        term = spec.inputs["query"]
        result = self.search.run(repo_path, term)
        trace.tool_calls.append(self._tool_record(result, spec))
        board.post(
            "retrieval",
            "code_search",
            "tool_result",
            f"search_text `{term}`",
            {
                "ok": result.ok,
                "summary": result.summary,
                "error_type": result.error_type,
                "router_reason": spec.reason,
                "matches": result.data.get("matches", [])[:5] if isinstance(result.data, dict) else [],
            },
        )
        for item in result.data.get("matches", []) if result.ok else []:
            candidate_files.add(item["file"])
            file_match_lines.setdefault(item["file"], set()).add(item["line"])
            evidence.add(
                Evidence(
                    file=item["file"],
                    line_start=item["line"],
                    line_end=item["line"],
                    reason=f"text search matched `{term}`",
                    snippet=item["text"],
                    source_tool="search_text",
                    confidence=0.65,
                )
            )

    def _recover_evidence(
        self,
        repo_path: Path,
        terms: list[str],
        plan,
        trace: Trace,
        board: AgentBoard,
        candidate_files: set[str],
        file_match_lines: dict[str, set[int]],
        evidence: EvidenceStore,
    ) -> None:
        existing_queries = {
            call.get("router", {}).get("inputs", {}).get("query")
            for call in trace.tool_calls
            if call.get("tool") == "search_text"
        }
        for term in terms:
            if term in existing_queries:
                continue
            spec = self.router.make_search_spec(term, f"FinalReviewAgent recovery search `{term}`")
            self._execute_text_search(repo_path, spec, trace, board, candidate_files, file_match_lines, evidence)
        for rel_file in sorted(candidate_files)[:8]:
            if not rel_file.endswith(".py"):
                continue
            result = self.ast.run(repo_path, rel_file, plan.search_terms, sorted(file_match_lines.get(rel_file, set())))
            trace.tool_calls.append(self._tool_record(result))
            board.post(
                "code_intelligence",
                "ast_agent",
                "symbols",
                f"Recovery AST symbols for {rel_file}",
                {
                    "ok": result.ok,
                    "summary": result.summary,
                    "symbols": result.data.get("symbols", []) if isinstance(result.data, dict) else [],
                },
            )
            for item in result.data.get("symbols", []) if result.ok else []:
                evidence.add(
                    Evidence(
                        file=rel_file,
                        line_start=item["line_start"],
                        line_end=item["line_end"],
                        reason=f"Recovery AST found {item['kind']} `{item['name']}`",
                        symbol=item["name"],
                        source_tool="parse_ast",
                        confidence=0.68,
                    )
                )

    def _post_workflow_graph(self, board: AgentBoard, *, mode: str, policy) -> None:
        board.post(
            "workflow",
            "orchestrator",
            "graph_spec",
            "LangGraph-style workflow graph and routing decisions",
            self.workflow.graph_payload(mode=mode, policy=policy),
        )

    def _post_workflow_checkpoints(self, board: AgentBoard, checkpoints: list[WorkflowCheckpoint]) -> None:
        board.post(
            "workflow",
            "orchestrator",
            "checkpoints",
            "Workflow checkpoints persisted during this run",
            {"items": [checkpoint.__dict__ for checkpoint in checkpoints], "count": len(checkpoints)},
        )

    def _workflow_checkpoint(
        self,
        node: str,
        status: str,
        detail: str,
        trace: Trace,
        board: AgentBoard,
        *,
        next_nodes: list[str] | None = None,
        metrics: dict | None = None,
    ) -> WorkflowCheckpoint:
        return self.workflow.checkpoint(
            node=node,
            status=status,
            detail=detail,
            board=board.to_dict(),
            state_count=len(trace.state_timeline),
            next_nodes=next_nodes or [],
            metrics=metrics or {},
        )

    def _next_nodes_from_schedule(self, schedule: list) -> list[str]:
        mapping = {
            "search_text": "retrieval",
            "parse_candidate_files": "code_intelligence",
            "build_symbol_graph": "code_graph",
            "build_code_graph": "code_graph",
            "git_log": "retrieval",
            "run_tests": "verification",
        }
        nodes = [mapping.get(getattr(spec, "action", ""), getattr(spec, "tool_family", "tool")) for spec in schedule]
        return list(dict.fromkeys(nodes))

    def _diff_review_questions(self, review_report: dict, contract_report: dict) -> list[str]:
        questions: list[str] = []
        if review_report.get("risk_level") == "high":
            questions.append("High-risk diff findings were detected. Should this change be split or manually security-reviewed before merge?")
        if not review_report.get("test_files") and any(not self.pr_reviewer._is_test_file(path) for path in review_report.get("changed_files", [])):
            questions.append("No test file changes were detected. Which targeted test command should validate this diff?")
        if not contract_report.get("ok"):
            questions.append("Agent Board contract validation failed. Should the run be treated as incomplete?")
        return questions

    def _tool_record(self, result, spec=None) -> dict:
        record = {
            "ok": result.ok,
            "tool": result.tool,
            "summary": result.summary,
            "latency_ms": result.latency_ms,
            "error_type": result.error_type,
            "message": result.message,
            "retryable": result.retryable,
            "suggested_fallback": result.suggested_fallback,
            "data": result.data,
        }
        if spec:
            record["router"] = {
                "tool_family": spec.tool_family,
                "action": spec.action,
                "reason": spec.reason,
                "inputs": spec.inputs,
            }
        return record

    def _llm_record(self, response: LLMResponse, role: str) -> dict:
        return {
            "role": role,
            "provider": response.provider,
            "model": response.model,
            "latency_ms": response.latency_ms,
            "usage": response.usage,
            "cost": estimate_llm_cost(response),
            "finish_reason": response.finish_reason,
            "tool_call_count": len(response.tool_calls),
        }

    def _state(self, trace: Trace, name: str, status: str, detail: str = "", metrics: dict | None = None) -> None:
        trace.state_timeline.append(
            RunState(
                name=name,
                status=status,
                detail=detail,
                metrics=metrics or {},
            )
        )
