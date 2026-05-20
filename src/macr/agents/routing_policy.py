from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from macr.schemas import Plan


@dataclass
class RoutingDecision:
    step: str
    action: str
    reason: str


@dataclass
class ExecutionPolicy:
    mode: str
    use_llm_planner: bool
    use_llm_solver: bool
    executed_steps: list[RoutingDecision] = field(default_factory=list)
    skipped_steps: list[RoutingDecision] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "use_llm_planner": self.use_llm_planner,
            "use_llm_solver": self.use_llm_solver,
            "executed_steps": [item.__dict__ for item in self.executed_steps],
            "skipped_steps": [item.__dict__ for item in self.skipped_steps],
        }


class RoutingPolicyAgent:
    """Decides which agents are worth running for the current task."""

    LLM_SOLVER_INTENTS = {"bug_investigation", "test_failure_analysis", "patch_planning", "light_patch"}

    def decide(
        self,
        plan: Plan,
        *,
        provider_available: bool,
        llm_planner_enabled: bool,
        generate_patch: bool,
        test_selector: str | None,
    ) -> ExecutionPolicy:
        wanted_tools = {step.tool_family for step in plan.steps}
        use_llm_solver = provider_available and (
            generate_patch
            or plan.risk_level == "high"
            or plan.intent in self.LLM_SOLVER_INTENTS
        )
        policy = ExecutionPolicy(
            mode="agentic" if plan.intent in self.LLM_SOLVER_INTENTS or generate_patch else "deterministic",
            use_llm_planner=provider_available and llm_planner_enabled,
            use_llm_solver=use_llm_solver,
        )
        for step in ("repo_map", "text_search", "ast", "symbol_graph", "code_graph", "code_quality", "solver", "final_review", "monitor"):
            if step in {"repo_map", "code_quality", "solver", "final_review", "monitor"}:
                policy.executed_steps.append(RoutingDecision(step, "execute", "core artifact required for traceability"))
            elif step in wanted_tools or (step == "code_graph" and "symbol_graph" in wanted_tools):
                policy.executed_steps.append(RoutingDecision(step, "execute", "planner requested code evidence"))
            else:
                policy.skipped_steps.append(RoutingDecision(step, "skip", "planner did not request this tool family"))
        if "git" in wanted_tools:
            policy.executed_steps.append(RoutingDecision("git", "execute", "change history may improve investigation"))
        else:
            policy.skipped_steps.append(RoutingDecision("git", "skip", "not needed for low-risk localization or code QA"))
        if "test_runner" in wanted_tools and test_selector:
            policy.executed_steps.append(RoutingDecision("test_runner", "execute", "explicit test selector provided"))
        elif "test_runner" in wanted_tools:
            policy.skipped_steps.append(RoutingDecision("test_runner", "skip", "test runner requested but no test selector was provided"))
        else:
            policy.skipped_steps.append(RoutingDecision("test_runner", "skip", "planner did not request tests"))
        if generate_patch:
            policy.executed_steps.append(RoutingDecision("patch_agent", "execute", "patch generation explicitly requested"))
        else:
            policy.skipped_steps.append(RoutingDecision("patch_agent", "skip", "question-only run does not need patch generation"))
        if policy.use_llm_solver:
            policy.executed_steps.append(RoutingDecision("llm_solver", "execute", "LLM likely improves synthesis for riskier task"))
        else:
            reason = "no provider configured" if not provider_available else "rules and code evidence are sufficient for this task"
            policy.skipped_steps.append(RoutingDecision("llm_solver", "skip", reason))
        return policy

    def diff_review_policy(self, *, provider_available: bool = False) -> ExecutionPolicy:
        policy = ExecutionPolicy(
            mode="deterministic_diff_review",
            use_llm_planner=False,
            use_llm_solver=False,
            executed_steps=[
                RoutingDecision("repo_map", "execute", "repository context helps interpret diff scope"),
                RoutingDecision("diff_review_agent", "execute", "unified diff can be reviewed with deterministic rules"),
                RoutingDecision("code_quality", "execute", "maintainability context is part of final audit"),
                RoutingDecision("final_review", "execute", "cross-agent audit is required before output"),
                RoutingDecision("monitor", "execute", "run metrics are required for observability"),
            ],
            skipped_steps=[
                RoutingDecision("planner", "skip", "diff already defines the task shape"),
                RoutingDecision("text_search", "skip", "diff review uses changed lines as primary input"),
                RoutingDecision("ast", "skip", "first-pass diff review does not need full AST parsing"),
                RoutingDecision("symbol_graph", "skip", "first-pass diff review does not need graph expansion"),
                RoutingDecision("llm_solver", "skip", "deterministic rules are cheaper and sufficient for first-pass review" if provider_available else "no provider configured"),
            ],
        )
        return policy
