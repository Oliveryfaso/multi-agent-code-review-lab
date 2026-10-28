"""The twelve-case dry-run must exercise the frozen batch control path."""
import json
import shutil
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.evals.deepseek_policy_batch import CORE_IMPLEMENTATION_FILES
from macr.evals.deepseek_policy_batch import PolicyBatchError
import run_deepseek_policy_dryrun as dryrun


class PolicyBatchDryRunTests(unittest.TestCase):
    def test_invalid_dataset_does_not_create_output_root(self):
        root = fixture_root()
        dataset = root / "dataset"
        dataset.mkdir()
        (dataset / "cases.json").write_text('{"version":"wrong","cases":[]}',
                                             encoding="utf-8")
        project = root / "project"
        project.mkdir()
        out = root / "run"
        with patch.object(dryrun, "DATASET", dataset), patch.object(dryrun, "PROJECT", project):
            with self.assertRaises(SystemExit):
                dryrun.main(["--out", str(out)])
        self.assertFalse(out.exists())

    def test_oversized_neutral_dataset_is_rejected_before_output(self):
        root = fixture_root()
        dataset = root / "dataset"
        shutil.copytree(dryrun.DATASET, dataset,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
        case_file = dataset / "cases.json"
        payload = json.loads(case_file.read_text(encoding="utf-8"))
        payload["_padding"] = "x" * 70000
        case_file.write_text(json.dumps(payload), encoding="utf-8")
        project = root / "project"
        for relative in CORE_IMPLEMENTATION_FILES:
            path = project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# fixture code\n", encoding="utf-8")
        out = root / "oversized"
        with patch.object(dryrun, "DATASET", dataset), patch.object(dryrun, "PROJECT", project):
            with self.assertRaises(SystemExit):
                dryrun.main(["--out", str(out)])
        self.assertFalse(out.exists())

    def test_output_inside_project_is_rejected_before_creating_run(self):
        root = fixture_root()
        project = root / "project"
        project.mkdir()
        with patch.object(dryrun, "PROJECT", project):
            with self.assertRaises((SystemExit, PolicyBatchError)):
                dryrun.main(["--out", str(project / "run")])
        self.assertFalse((project / "run").exists())

    def test_twelve_case_dryrun_records_every_claim_and_outcome(self):
        root = fixture_root()
        dataset = root / "dataset"
        shutil.copytree(dryrun.DATASET, dataset,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
        project = root / "project"
        for relative in CORE_IMPLEMENTATION_FILES:
            path = project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# frozen offline test code\n", encoding="utf-8")
        out = root / "batch-dryrun"
        with patch.object(dryrun, "DATASET", dataset), patch.object(dryrun, "PROJECT", project):
            self.assertEqual(dryrun.main(["--out", str(out)]), 0)
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["case_count"], summary["completed_task_runs"],
                          summary["scripted_requests"], summary["model_calls_live"]),
                         (12, 48, 72, 0))
        self.assertEqual(summary.get("max_requests"), 84)
        registry = out / "control" / "registry"
        self.assertEqual(len(list(registry.glob("*.claim.json"))), 48)
        self.assertEqual(len(list(registry.glob("*.outcome.json"))), 48)
        self.assertTrue(all(row["status"] == "semantic_pending" for row in summary["rows"]))


if __name__ == "__main__":
    unittest.main()
