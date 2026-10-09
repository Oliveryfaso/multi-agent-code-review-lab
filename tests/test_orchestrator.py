import unittest
from pathlib import Path
from unittest.mock import patch

from debugger_fixtures import fixture_root
from test_code_graph import graph_fixture

from macr.agents.orchestrator import Orchestrator
from macr.memory.trace_store import TraceStore


class OrchestratorTests(unittest.TestCase):
    def test_orchestrator_generates_evidence(self):
        repo = Path("sample_repos/sample_python_api")
        tmp_path = fixture_root() / "traces"
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
        tmp_path = fixture_root() / "traces"
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


    def test_canonical_graph_edge_kind_reaches_evidence_in_both_directions(self):
        root = graph_fixture()
        store = TraceStore(fixture_root() / 'traces')
        orchestrator = Orchestrator(trace_store=store)
        forbidden = AssertionError('static graph regression must remain offline')
        with patch('subprocess.Popen', side_effect=forbidden), \
             patch('socket.create_connection', side_effect=forbidden):
            trace = orchestrator.run(root, 'process_payment')
        graph = trace.board['code_graph'][0]['payload']
        payment = next(n for n in graph['neighborhoods'] if n['symbol'] == 'process_payment')
        self.assertTrue(any(e['kind'] == 'calls' and e['to_symbol'] == 'charge_card' for e in payment['outgoing']))
        self.assertTrue(any(e['kind'] == 'tests' for e in payment['incoming']))
        items = trace.board['evidence'][0]['payload']['items']
        outgoing = next(e for e in items if e['source_tool'] == 'code_graph'
                        and 'calls edge `process_payment` -> `charge_card`' in e['reason'])
        incoming = next(e for e in items if e['source_tool'] == 'code_graph'
                        and 'tests edge `test_process_payment_rejects_invalid_payload` -> `process_payment`' in e['reason'])
        self.assertEqual((outgoing['file'], outgoing['line_start']), ('backend/payments.py', 3))
        self.assertEqual((incoming['file'], incoming['line_start']), ('tests/test_auth.py', 3))
        self.assertTrue(trace.metrics['contract_validation']['ok'])
        self.assertEqual(trace.state_timeline[-1].name, 'completed')


if __name__ == "__main__":
    unittest.main()
