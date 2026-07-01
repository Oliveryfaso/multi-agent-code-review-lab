from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from macr.schemas import utc_now


@dataclass
class WorkflowNode:
    name: str
    owner: str
    description: str
    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    uses_llm: bool = False
    skippable: bool = False


@dataclass
class WorkflowEdge:
    source: str
    target: str
    condition: str = "always"


@dataclass
class WorkflowCheckpoint:
    node: str
    status: str
    detail: str
    board_sections: list[str]
    state_count: int
    next_nodes: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)


class CodeReviewWorkflow:
    """LangGraph-style workflow contract without binding the core tool to a framework."""

    POLICY_NODE_MAP = {
        "repo_map": "repo_map",
        "text_search": "retrieval",
        "ast": "code_intelligence",
        "symbol_graph": "code_graph",
        "code_graph": "code_graph",
        "code_quality": "code_quality",
        "git": "retrieval",
        "test_runner": "verification",
        "solver": "solver",
        "final_review": "final_review",
        "monitor": "monitor",
        "patch_agent": "patch",
        "llm_solver": "solver",
        "diff_review_agent": "pr_review",
    }

    def __init__(self) -> None:
        self.nodes = [
            WorkflowNode("task", "orchestrator", "Normalize input and create run state.", writes=["task"]),
            WorkflowNode("planner", "planner", "Classify intent and produce a bounded execution plan.", reads=["task"], writes=["plan"], uses_llm=True, skippable=True),
            WorkflowNode("policy", "routing_policy_agent", "Decide which agents, tools, and API calls are justified.", reads=["plan"], writes=["policy"]),
            WorkflowNode("router", "tool_router", "Turn plan and policy into explicit tool call specs.", reads=["plan", "policy"], writes=["routing"], skippable=True),
            WorkflowNode("repo_map", "repo_map_agent", "Summarize repository structure and focus files.", reads=["task", "plan"], writes=["repo_map"]),
            WorkflowNode("retrieval", "code_search", "Run exact text search and recovery searches.", reads=["plan", "repo_map"], writes=["retrieval"], skippable=True),
            WorkflowNode("retrieval_critic", "retrieval_critic", "Grade recall quality and request query rewrite when needed.", reads=["retrieval"], writes=["retrieval_critique"], skippable=True),
            WorkflowNode("code_intelligence", "ast_agent", "Extract functions, classes, imports, and line-level evidence.", reads=["retrieval"], writes=["code_intelligence"], skippable=True),
            WorkflowNode("code_graph", "code_graph_agent", "Expand definitions, references, calls, imports, and test links.", reads=["code_intelligence"], writes=["code_graph"], skippable=True),
            WorkflowNode("code_quality", "code_smell_agent", "Measure maintainability risk and code smell ratio.", reads=["repo_map"], writes=["code_quality"]),
            WorkflowNode("solver", "solver", "Synthesize an evidence-backed answer.", reads=["evidence", "code_quality"], writes=["review"], uses_llm=True, skippable=True),
            WorkflowNode("pr_review", "diff_review_agent", "Review unified diff with deterministic PR rules.", reads=["task", "repo_map"], writes=["pr_review"], skippable=True),
            WorkflowNode("final_review", "final_review_agent", "Audit cross-agent consistency, confidence, evidence, and escalation.", reads=["review", "pr_review", "code_quality"], writes=["final_review"]),
            WorkflowNode("patch", "patch_agent", "Generate and rank patch candidates when requested.", reads=["review"], writes=["patch"], uses_llm=True, skippable=True),
            WorkflowNode("verification", "verifier", "Validate tests or patch application results.", reads=["patch"], writes=["verification"], skippable=True),
            WorkflowNode("monitor", "monitor", "Summarize health, cost, failure modes, and workflow completeness.", reads=["trace"], writes=["monitor"]),
        ]
        self.edges = [
            WorkflowEdge("task", "planner", "ask_or_patch_mode"),
            WorkflowEdge("task", "policy", "diff_review_mode"),
            WorkflowEdge("planner", "policy"),
            WorkflowEdge("policy", "router", "non_diff_mode"),
            WorkflowEdge("policy", "repo_map"),
            WorkflowEdge("router", "retrieval", "text_search_scheduled"),
            WorkflowEdge("repo_map", "pr_review", "diff_review_mode"),
            WorkflowEdge("retrieval", "retrieval_critic", "retrieval_executed"),
            WorkflowEdge("retrieval_critic", "retrieval", "query_rewrite_requested"),
            WorkflowEdge("retrieval_critic", "code_intelligence", "candidate_files_found"),
            WorkflowEdge("code_intelligence", "code_graph", "symbol_graph_requested"),
            WorkflowEdge("code_graph", "code_quality"),
            WorkflowEdge("pr_review", "code_quality"),
            WorkflowEdge("code_quality", "solver", "non_diff_mode"),
            WorkflowEdge("code_quality", "final_review", "diff_review_mode"),
            WorkflowEdge("solver", "final_review"),
            WorkflowEdge("final_review", "retrieval", "recovery_requested"),
            WorkflowEdge("final_review", "patch", "patch_requested"),
            WorkflowEdge("patch", "verification"),
            WorkflowEdge("verification", "final_review", "patch_verified"),
            WorkflowEdge("final_review", "monitor"),
        ]

    def graph_payload(self, *, mode: str, policy: Any | None = None) -> dict[str, Any]:
        executed, skipped = self._policy_status(policy)
        node_payloads = []
        for node in self.nodes:
            status = "pending"
            reason = ""
            if node.name in executed:
                status = "execute"
                reason = executed[node.name]
            elif node.name in skipped:
                status = "skip"
                reason = skipped[node.name]
            elif mode == "diff" and node.name in {"planner", "router", "retrieval", "retrieval_critic", "code_intelligence", "code_graph", "solver", "patch", "verification"}:
                status = "skip"
                reason = "diff review routes directly from policy/repo map to PR review"
            node_payloads.append({**asdict(node), "decision": status, "decision_reason": reason})
        return {
            "mode": mode,
            "state_schema": "Trace + AgentBoard + EvidenceStore + policy/checkpoints",
            "nodes": node_payloads,
            "edges": [asdict(edge) for edge in self.edges],
        }

    def checkpoint(
        self,
        *,
        node: str,
        status: str,
        detail: str,
        board: dict[str, list[dict[str, Any]]],
        state_count: int,
        next_nodes: list[str] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> WorkflowCheckpoint:
        return WorkflowCheckpoint(
            node=node,
            status=status,
            detail=detail,
            board_sections=sorted(board.keys()),
            state_count=state_count,
            next_nodes=next_nodes or [],
            metrics=metrics or {},
        )

    def summarize(self, *, mode: str, policy: Any | None, checkpoints: list[WorkflowCheckpoint]) -> dict[str, Any]:
        payload = self.graph_payload(mode=mode, policy=policy)
        executed = [node for node in payload["nodes"] if node["decision"] == "execute"]
        skipped = [node for node in payload["nodes"] if node["decision"] == "skip"]
        return {
            "mode": mode,
            "node_count": len(payload["nodes"]),
            "edge_count": len(payload["edges"]),
            "executed_node_count": len(executed),
            "skipped_node_count": len(skipped),
            "checkpoint_count": len(checkpoints),
            "checkpoints": [asdict(item) for item in checkpoints],
            "graph": payload,
        }

    def _policy_status(self, policy: Any | None) -> tuple[dict[str, str], dict[str, str]]:
        executed: dict[str, str] = {}
        skipped: dict[str, str] = {}
        if not policy:
            return executed, skipped
        for item in getattr(policy, "executed_steps", []):
            node = self.POLICY_NODE_MAP.get(getattr(item, "step", ""), getattr(item, "step", ""))
            executed[node] = getattr(item, "reason", "")
        for item in getattr(policy, "skipped_steps", []):
            node = self.POLICY_NODE_MAP.get(getattr(item, "step", ""), getattr(item, "step", ""))
            if node not in executed:
                skipped[node] = getattr(item, "reason", "")
        executed.setdefault("task", "core run state")
        executed.setdefault("policy", "core routing decision")
        return executed, skipped
