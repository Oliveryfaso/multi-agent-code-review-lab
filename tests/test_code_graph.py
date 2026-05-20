import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from macr.tools.code_graph import CodeGraphTool


class CodeGraphTests(unittest.TestCase):
    def test_code_graph_builds_call_and_test_edges(self):
        result = CodeGraphTool().run(
            Path("sample_repos/sample_python_api"),
            ["process_payment"],
            ["backend/payments.py", "tests/test_auth.py"],
        )

        self.assertTrue(result.ok)
        neighborhoods = result.data["neighborhoods"]
        payment = next(item for item in neighborhoods if item["symbol"] == "process_payment")
        outgoing_targets = {edge.get("to_symbol") for edge in payment["outgoing"]}
        incoming_sources = {edge.get("from_symbol") for edge in payment["incoming"]}

        self.assertIn("charge_card", outgoing_targets)
        self.assertIn("test_process_payment_rejects_invalid_payload", incoming_sources)

    def test_code_graph_reuses_persistent_cache(self):
        with TemporaryDirectory() as tmp:
            tool = CodeGraphTool(cache_dir=Path(tmp))
            repo = Path("sample_repos/sample_python_api")
            first = tool.run(repo, ["process_payment"])
            second = tool.run(repo, ["process_payment"])

        self.assertFalse(first.data["cache"]["hit"])
        self.assertTrue(second.data["cache"]["hit"])
        self.assertEqual(first.data["cache"]["fingerprint"], second.data["cache"]["fingerprint"])


if __name__ == "__main__":
    unittest.main()
