import unittest

from macr.contracts import BoardContractValidator


class ContractTests(unittest.TestCase):
    def test_validator_accepts_minimum_non_patch_board(self):
        board = {
            "task": [{"agent": "orchestrator", "kind": "user_query", "payload": {"query": "q", "repo_path": "r", "generate_patch": False}}],
            "plan": [{"agent": "planner", "kind": "execution_plan", "payload": {"intent": "code_qa", "risk_level": "low", "search_terms": [], "steps": []}}],
            "policy": [{"agent": "policy", "kind": "execution_policy", "payload": {"mode": "deterministic", "use_llm_planner": False, "use_llm_solver": False, "executed_steps": [], "skipped_steps": []}}],
            "routing": [{"agent": "router", "kind": "tool_schedule", "payload": {"calls": []}}],
            "repo_map": [{"agent": "repo_map", "kind": "repository_map", "payload": {"total_python_files": 1, "mapped_files": 1, "focus_files": [], "symbols": []}}],
            "retrieval": [{"agent": "search", "kind": "tool_result", "payload": {"summary": "ok"}}],
            "retrieval_critique": [{"agent": "critic", "kind": "retrieval_quality", "payload": {"quality": "good", "empty_recall_count": 0, "candidate_file_count": 1, "evidence_count": 1, "actions": [], "rationale": "ok"}}],
            "code_intelligence": [{"agent": "ast", "kind": "symbols", "payload": {"summary": "ok"}}],
            "code_graph": [{"agent": "graph", "kind": "local_graph", "payload": {"ok": True, "summary": "ok", "node_count": 1, "edge_count": 0, "neighborhoods": []}}],
            "code_quality": [{"agent": "quality", "kind": "smell_report", "payload": {"smell_ratio": 0.1, "severity": "low", "hotspots": [], "suggestions": []}}],
            "evidence": [{"agent": "memory", "kind": "evidence_set", "payload": {"count": 1, "items": []}}],
            "review": [{"agent": "solver", "kind": "final_review", "payload": {"answer": "ok", "confidence": 0.9, "next_steps": []}}],
        }

        report = BoardContractValidator().validate(board)

        self.assertTrue(report.ok)
        self.assertEqual(report.violations, [])

    def test_validator_reports_missing_required_section_and_payload_field(self):
        board = {
            "task": [{"agent": "orchestrator", "kind": "user_query", "payload": {"query": "q"}}],
        }

        report = BoardContractValidator().validate(board)

        self.assertFalse(report.ok)
        messages = [violation.message for violation in report.violations]
        self.assertIn("required board section is missing", messages)
        self.assertIn("payload missing `repo_path`", messages)

    def test_validator_accepts_diff_review_board(self):
        board = {
            "policy": [{"agent": "policy", "kind": "execution_policy", "payload": {"mode": "deterministic", "use_llm_planner": False, "use_llm_solver": False, "executed_steps": [], "skipped_steps": []}}],
            "repo_map": [{"agent": "repo_map", "kind": "repository_map", "payload": {"total_python_files": 1, "mapped_files": 1, "focus_files": [], "symbols": []}}],
            "code_quality": [{"agent": "quality", "kind": "smell_report", "payload": {"smell_ratio": 0.0, "severity": "low", "hotspots": [], "suggestions": []}}],
            "pr_review": [{"agent": "diff", "kind": "diff_review", "payload": {"risk_level": "low", "changed_files": [], "comments": [], "test_suggestions": [], "summary": "ok"}}],
            "final_review": [{"agent": "audit", "kind": "audit_report", "payload": {"ok": True}}],
        }

        report = BoardContractValidator().validate(board, diff_review=True)

        self.assertTrue(report.ok)


if __name__ == "__main__":
    unittest.main()
