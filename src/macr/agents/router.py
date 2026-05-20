from __future__ import annotations

from macr.memory.board import AgentBoard
from macr.schemas import Plan, ToolCallSpec


class ToolRouterAgent:
    """Turns planner intent and board state into executable tool call specs."""

    def route(self, plan: Plan, board: AgentBoard, test_selector: str | None = None) -> list[ToolCallSpec]:
        specs: list[ToolCallSpec] = []
        wanted = [step.tool_family for step in plan.steps[: plan.max_tool_calls]]
        if "text_search" in wanted:
            for term in plan.search_terms[:5]:
                specs.append(
                    ToolCallSpec(
                        tool_family="text_search",
                        action="search_text",
                        reason=f"Planner requested exact recall for `{term}`",
                        inputs={"query": term},
                    )
                )
        if "ast" in wanted:
            specs.append(
                ToolCallSpec(
                    tool_family="ast",
                    action="parse_candidate_files",
                    reason="Parse candidate Python files from text retrieval hits",
                    inputs={"candidate_limit": 8},
                )
            )
        if "symbol_graph" in wanted:
            specs.append(
                ToolCallSpec(
                    tool_family="symbol_graph",
                    action="build_symbol_graph",
                    reason="Expand definitions and references around retrieved symbols",
                    inputs={"candidate_limit": 12},
                )
            )
            specs.append(
                ToolCallSpec(
                    tool_family="code_graph",
                    action="build_code_graph",
                    reason="Build local code graph neighborhoods for calls, imports, and test references",
                    inputs={"candidate_limit": 16},
                )
            )
        if "git" in wanted:
            specs.append(
                ToolCallSpec(
                    tool_family="git",
                    action="git_log",
                    reason="Inspect recent changes around candidate files",
                    inputs={"candidate_limit": 5},
                )
            )
        if "test_runner" in wanted and test_selector:
            specs.append(
                ToolCallSpec(
                    tool_family="test_runner",
                    action="run_tests",
                    reason="Verify behavior with the requested test selector",
                    inputs={"test_selector": test_selector},
                )
            )
        board.post(
            "routing",
            "tool_router",
            "tool_schedule",
            "Tool Router schedule",
            {"calls": [self._spec_to_dict(spec) for spec in specs]},
        )
        return specs

    def make_search_spec(self, query: str, reason: str) -> ToolCallSpec:
        return ToolCallSpec(
            tool_family="text_search",
            action="search_text",
            reason=reason,
            inputs={"query": query},
        )

    def _spec_to_dict(self, spec: ToolCallSpec) -> dict:
        return {
            "tool_family": spec.tool_family,
            "action": spec.action,
            "reason": spec.reason,
            "inputs": spec.inputs,
        }
