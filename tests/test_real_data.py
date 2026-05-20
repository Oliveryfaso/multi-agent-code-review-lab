import json
import tempfile
import unittest
from pathlib import Path

from macr.evals.real_data import prepare_real_eval


class RealDataImportTests(unittest.TestCase):
    def test_prepare_swe_bench_eval_extracts_patch_oracles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "swe.jsonl"
            output = root / "real.jsonl"
            repo_map = root / "repo_map.json"
            source.write_text(
                json.dumps(
                    {
                        "instance_id": "demo__repo-1",
                        "repo": "demo/repo",
                        "base_commit": "abc123",
                        "problem_statement": "Fix crash when payment payload is missing.",
                        "patch": (
                            "diff --git a/backend/payments.py b/backend/payments.py\n"
                            "@@ -1,3 +1,4 @@ def process_payment(payload):\n"
                            "-    return charge_card(payload)\n"
                            "+    return charge_card(payload)\n"
                        ),
                        "test_patch": (
                            "diff --git a/tests/test_payments.py b/tests/test_payments.py\n"
                            "@@ -1,2 +1,3 @@ def test_missing_payload():\n"
                            "+    assert True\n"
                        ),
                        "FAIL_TO_PASS": ["tests/test_payments.py::test_missing_payload"],
                    }
                )
                + "\n"
            )
            repo_map.write_text(json.dumps({"demo/repo": "local_repos/demo_repo"}))

            summary = prepare_real_eval("swe-bench", source, output, repo_map_path=repo_map)
            rows = [json.loads(line) for line in output.read_text().splitlines()]

        self.assertEqual(summary["case_count"], 1)
        self.assertEqual(rows[0]["repo"], "local_repos/demo_repo")
        self.assertIn("backend/payments.py", rows[0]["expected_files"])
        self.assertIn("tests/test_payments.py", rows[0]["expected_files"])
        self.assertIn("process_payment", rows[0]["expected_symbols"])
        self.assertIn("test_missing_payload", rows[0]["expected_symbols"])
        self.assertEqual(rows[0]["metadata"]["base_commit"], "abc123")

    def test_prepare_codesearchnet_eval_maps_docstring_to_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "csn.jsonl"
            output = root / "real.jsonl"
            source.write_text(
                json.dumps(
                    {
                        "repo": "demo/repo",
                        "path": "pkg/math.py",
                        "func_name": "pkg.math.add",
                        "docstring": "Add two numbers.",
                        "language": "python",
                    }
                )
                + "\n"
            )

            summary = prepare_real_eval("codesearchnet", source, output)
            rows = [json.loads(line) for line in output.read_text().splitlines()]

        self.assertEqual(summary["case_count"], 1)
        self.assertEqual(rows[0]["query"], "Add two numbers.")
        self.assertEqual(rows[0]["expected_files"], ["pkg/math.py"])
        self.assertEqual(rows[0]["expected_symbols"], ["add"])
        self.assertEqual(rows[0]["repo"], "external_repos/demo__repo")

    def test_prepare_github_issue_eval_preserves_manual_oracles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "issues.jsonl"
            output = root / "real.jsonl"
            source.write_text(
                json.dumps(
                    {
                        "id": "demo-42",
                        "repo": "demo/repo",
                        "title": "Payment status regression",
                        "body": "Users see 200 even when card_token is missing.",
                        "expected_files": ["backend/payments.py"],
                        "expected_symbols": ["process_payment", "charge_card"],
                        "issue_url": "https://github.com/demo/repo/issues/42",
                    }
                )
                + "\n"
            )

            summary = prepare_real_eval("github-issue", source, output)
            rows = [json.loads(line) for line in output.read_text().splitlines()]

        self.assertEqual(summary["case_count"], 1)
        self.assertEqual(rows[0]["task_type"], "real_github_issue")
        self.assertIn("Payment status regression", rows[0]["query"])
        self.assertEqual(rows[0]["expected_files"], ["backend/payments.py"])
        self.assertEqual(rows[0]["metadata"]["issue_url"], "https://github.com/demo/repo/issues/42")


if __name__ == "__main__":
    unittest.main()
