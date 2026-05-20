from __future__ import annotations

import json
from pathlib import Path

from macr.agents.orchestrator import Orchestrator


class EvalRunner:
    def __init__(self, orchestrator: Orchestrator | None = None) -> None:
        self.orchestrator = orchestrator or Orchestrator()

    def run(self, eval_file: Path, repo_root: Path, report_path: Path) -> dict[str, float]:
        cases = [json.loads(line) for line in eval_file.read_text().splitlines() if line.strip()]
        results = []
        for case in cases:
            repo_path = repo_root / case["repo"]
            test_selector = case.get("test_selector") if case.get("run_tests") else None
            trace = self.orchestrator.run(repo_path, case["query"], test_selector)
            evidence_files = {item.file for item in trace.answer.evidence} if trace.answer else set()
            evidence_symbols = {item.symbol for item in trace.answer.evidence if item.symbol} if trace.answer else set()
            expected_files = set(case.get("expected_files", []))
            expected_symbols = set(case.get("expected_symbols", []))
            file_hit = not expected_files or bool(expected_files & evidence_files)
            symbol_hit = not expected_symbols or bool(expected_symbols & evidence_symbols)
            file_recall = self._set_recall(expected_files, evidence_files)
            symbol_recall = self._set_recall(expected_symbols, evidence_symbols)
            success = file_hit and symbol_hit and (not case.get("must_have_evidence") or bool(evidence_files))
            final_review = trace.metrics.get("final_review", {})
            code_smell = trace.metrics.get("code_smell", {})
            results.append(
                {
                    "id": case["id"],
                    "success": success,
                    "file_hit": file_hit,
                    "symbol_hit": symbol_hit,
                    "file_recall": file_recall,
                    "symbol_recall": symbol_recall,
                    "evidence_count": len(trace.answer.evidence) if trace.answer else 0,
                    "tool_call_count": len(trace.tool_calls),
                    "failed_tool_calls": trace.metrics.get("failed_tool_calls", 0),
                    "empty_recall_count": trace.metrics.get("empty_recall_count", 0),
                    "final_review_ok": final_review.get("ok", False),
                    "human_review_required": final_review.get("human_review_required", False),
                    "code_smell_ratio": float(code_smell.get("smell_ratio") or 0),
                    "code_smell_severity": code_smell.get("severity", "n/a"),
                }
            )
        metrics = {
            "case_count": len(results),
            "task_success_rate": self._rate(results, "success"),
            "file_hit_rate": self._rate(results, "file_hit"),
            "symbol_hit_rate": self._rate(results, "symbol_hit"),
            "avg_expected_file_recall": self._avg(results, "file_recall"),
            "avg_expected_symbol_recall": self._avg(results, "symbol_recall"),
            "avg_evidence_count": self._avg(results, "evidence_count"),
            "avg_tool_calls": self._avg(results, "tool_call_count"),
            "tool_call_failure_rate": round(
                sum(item["failed_tool_calls"] for item in results) / max(1, sum(item["tool_call_count"] for item in results)),
                3,
            ),
            "empty_recall_rate": round(
                sum(item["empty_recall_count"] for item in results) / max(1, sum(item["tool_call_count"] for item in results)),
                3,
            ),
            "final_review_pass_rate": self._rate(results, "final_review_ok"),
            "human_review_required_rate": self._rate(results, "human_review_required"),
            "avg_code_smell_ratio": self._avg(results, "code_smell_ratio"),
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(self._markdown(metrics, results))
        return metrics

    def _rate(self, results: list[dict], key: str) -> float:
        if not results:
            return 0.0
        return round(sum(1 for result in results if result[key]) / len(results), 3)

    def _avg(self, results: list[dict], key: str) -> float:
        if not results:
            return 0.0
        return round(sum(float(result[key]) for result in results) / len(results), 3)

    def _set_recall(self, expected: set[str], actual: set[str]) -> float:
        if not expected:
            return 1.0
        return round(len(expected & actual) / len(expected), 3)

    def _markdown(self, metrics: dict[str, float], results: list[dict]) -> str:
        lines = [
            "# Eval Report",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
        ]
        lines.extend(f"| {key} | {value} |" for key, value in metrics.items())
        lines.extend(
            [
                "",
                "## Cases",
                "",
                "| ID | Success | Final Review | Human Review | Code Smell | File Hit | Symbol Hit | File Recall | Symbol Recall | Evidence | Tool Calls | Empty Recall | Failed Tools |",
                "| --- | --- | --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        lines.extend(
            (
                f"| {item['id']} | {item['success']} | {item['final_review_ok']} | {item['human_review_required']} | {item['code_smell_ratio']} ({item['code_smell_severity']}) | {item['file_hit']} | {item['symbol_hit']} | "
                f"{item['file_recall']} | {item['symbol_recall']} | {item['evidence_count']} | "
                f"{item['tool_call_count']} | {item['empty_recall_count']} | {item['failed_tool_calls']} |"
            )
            for item in results
        )
        return "\n".join(lines) + "\n"
