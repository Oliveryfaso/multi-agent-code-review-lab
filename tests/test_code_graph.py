import os
import unittest
from debugger_fixtures import fixture_root, write_files
from macr.tools.code_graph import CodeGraphTool


def graph_fixture():
    return write_files(fixture_root(), {"backend/gateway.py": "def charge_card(payload):\n    return 1\n", "backend/payments.py": "from backend.gateway import charge_card\ndef process_payment(payload):\n    return charge_card(payload)\n", "tests/test_auth.py": "from backend.payments import process_payment\ndef test_process_payment_rejects_invalid_payload():\n    assert process_payment({}) == 1\n"})


class CodeGraphTests(unittest.TestCase):
    def test_code_graph_builds_call_and_test_edges(self):
        result = CodeGraphTool(cache_dir=fixture_root()).run(graph_fixture(), ["process_payment"], ["backend/payments.py", "tests/test_auth.py"])
        self.assertTrue(result.ok)
        payment = next(n for n in result.data["neighborhoods"] if n["symbol"] == "process_payment")
        self.assertIn("charge_card", {e["to_symbol"] for e in payment["outgoing"]})
        self.assertIn("test_process_payment_rejects_invalid_payload", {e["from_symbol"] for e in payment["incoming"]})
        self.assertTrue(all("resolution" in edge for edge in result.data["edges"]))

    def test_code_graph_reuses_content_bound_cache(self):
        tool, root = CodeGraphTool(cache_dir=fixture_root()), graph_fixture()
        first, second = tool.run(root, ["process_payment"]), tool.run(root, ["process_payment"])
        self.assertFalse(first.data["cache"]["hit"])
        self.assertTrue(second.data["cache"]["hit"])
        self.assertEqual(first.data["cache"]["fingerprint"], second.data["cache"]["fingerprint"])
        file = root / "backend/gateway.py"
        stamp = file.stat()
        file.write_text(file.read_text().replace("return 1", "return 2"))
        os.utime(file, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        changed = tool.run(root, ["process_payment"])
        self.assertFalse(changed.data["cache"]["hit"])
        self.assertNotEqual(first.data["cache"]["fingerprint"], changed.data["cache"]["fingerprint"])
