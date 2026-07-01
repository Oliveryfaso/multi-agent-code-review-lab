import unittest
from pathlib import Path

from macr.agents.orchestrator import Orchestrator
from macr.memory.trace_store import TraceStore


class OrchestratorTests(unittest.TestCase):
    def test_orchestrator_generates_evidence(self):
        repo = Path("sample_repos/sample_python_api")
        tmp_path = Path("traces/test_tmp")
        orchestrator = Orchestrator(trace_store=TraceStore(tmp_path))

        trace = orchestrator.run(repo, "这个接口在哪里鉴权？")

        self.assertIsNotNone(trace.answer)
        self.assertTrue(trace.answer.evidence)
        self.assertTrue(any(item.file == "backend/auth.py" for item in trace.answer.evidence))
        self.assertIn("routing", trace.board)
        self.assertIn("policy", trace.board)
        self.assertIn("repo_map", trace.board)
        self.assertTrue(any(call.get("router") for call in trace.tool_calls))
        self.assertTrue(trace.metrics["contract_validation"]["ok"])
        self.assertTrue(trace.metrics["final_review"]["ok"])
        self.assertIn("workflow", trace.metrics)
        self.assertGreaterEqual(trace.metrics["workflow"]["checkpoint_count"], 5)
        self.assertEqual(trace.metrics["workflow"]["graph"]["state_schema"], "Trace + AgentBoard + EvidenceStore + policy/checkpoints")
        self.assertIn("workflow", trace.board)
        self.assertIn("final_review", trace.board)
        evidence_payload = trace.board["evidence"][0]["payload"]
        self.assertEqual(evidence_payload["count"], len(evidence_payload["items"]))
        self.assertEqual(trace.state_timeline[0].name, "task_received")
        self.assertEqual(trace.state_timeline[-1].name, "completed")
        self.assertIn("contract_validated", [state.name for state in trace.state_timeline])
        self.assertTrue((tmp_path / "latest.json").exists())

    def test_orchestrator_runs_diff_review(self):
        repo = Path("sample_repos/sample_python_api")
        tmp_path = Path("traces/test_tmp")
        diff = """diff --git a/backend/payments.py b/backend/payments.py
--- a/backend/payments.py
+++ b/backend/payments.py
@@ -10,2 +10,4 @@
+def debug_charge(payload):
+    print(payload)
"""
        orchestrator = Orchestrator(trace_store=TraceStore(tmp_path))

        trace = orchestrator.run_diff_review(repo, diff)

        self.assertIn("pr_review", trace.board)
        self.assertIn("policy", trace.board)
        self.assertEqual(trace.metrics["diff_review"]["risk_level"], "medium")
        self.assertTrue(trace.metrics["contract_validation"]["ok"])
        self.assertTrue(trace.metrics["final_review"]["ok"])
        self.assertEqual(trace.metrics["workflow"]["mode"], "diff")
        self.assertGreaterEqual(trace.metrics["workflow"]["checkpoint_count"], 4)
        self.assertIn("workflow", trace.board)


if __name__ == "__main__":
    unittest.main()
