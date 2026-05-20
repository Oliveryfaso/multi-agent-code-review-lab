import unittest
from pathlib import Path

from macr.tools.repo_map import RepoMapTool


class RepoMapTests(unittest.TestCase):
    def test_repo_map_selects_focus_files_and_symbols(self):
        result = RepoMapTool().run(Path("sample_repos/sample_python_api"), ["process_payment"])

        self.assertTrue(result.ok)
        focus_files = result.data["focus_files"]
        self.assertTrue(any(item["file"] == "backend/payments.py" for item in focus_files))
        signatures = {
            symbol["signature"]
            for item in focus_files
            for symbol in item["symbols"]
        }
        self.assertIn("process_payment(payload)", signatures)


if __name__ == "__main__":
    unittest.main()
