import unittest
from debugger_fixtures import fixture_root, write_files
from macr.tools.repo_map import RepoMapTool


class RepoMapTests(unittest.TestCase):
    def test_repo_map_selects_focus_files_and_symbols(self):
        root = write_files(fixture_root(), {"backend/payments.py": "def process_payment(payload):\n    return payload\n"})
        result = RepoMapTool().run(root, ["process_payment"])
        self.assertTrue(result.ok)
        self.assertEqual(result.data["total_python_files"], 1)
        self.assertIn("process_payment(payload)", {s["signature"] for f in result.data["focus_files"] for s in f["symbols"]})

    def test_repo_map_exposes_qualified_names_and_parse_errors(self):
        root = write_files(fixture_root(), {"a.py": "class A:\n    def f(self): pass\n", "bad.py": "def broken(:\n"})
        result = RepoMapTool().run(root, ["f"])
        self.assertIn("A.f", {s.get("qualified_name") for s in result.data["symbols"]})
        self.assertEqual(result.data["coverage"]["parsed"], 1)
        self.assertEqual(result.data["errors"][0]["code"], "parse_error")
