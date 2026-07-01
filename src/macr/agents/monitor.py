from __future__ import annotations

from macr.costs import summarize_llm_costs
from macr.schemas import Trace


class MonitorAgent:
    def summarize(self, trace: Trace) -> dict[str, object]:
        tool_calls = trace.tool_calls
        empty = [call for call in tool_calls if call.get("error_type") == "empty_recall"]
        failed = [call for call in tool_calls if not call.get("ok") and call.get("error_type") != "empty_recall"]
        retryable_miss = [call for call in empty if call.get("retryable")]
        evidence_count = len(trace.answer.evidence) if trace.answer else 0
        suggestions: list[str] = []
        if empty:
            suggestions.append("add query rewriting or symbol expansion for empty recall cases")
        if failed:
            suggestions.append("inspect failed tool inputs and add fallback routing")
        if evidence_count == 0:
            suggestions.append("lower confidence and ask a clarification question when evidence is missing")
        llm_cost = summarize_llm_costs(trace.llm_calls)
        if llm_cost["llm_call_count"] and llm_cost["cache_hit_ratio"] < 0.2:
            suggestions.append("keep stable prompt prefixes and compact evidence payloads to improve provider cache hits")
        code_smell = trace.metrics.get("code_smell", {}) if isinstance(trace.metrics, dict) else {}
        if code_smell.get("severity") == "high":
            suggestions.append("treat high code smell ratio as a refactor-before-feature risk")
        workflow = trace.metrics.get("workflow", {}) if isinstance(trace.metrics, dict) else {}
        return {
            "tool_call_count": len(tool_calls),
            "failed_tool_calls": len(failed),
            "empty_recall_count": len(empty),
            "retryable_miss_count": len(retryable_miss),
            "evidence_count": evidence_count,
            "llm_cost": llm_cost,
            "code_smell": code_smell,
            "workflow_checkpoint_count": workflow.get("checkpoint_count", 0),
            "suggestions": suggestions,
        }
