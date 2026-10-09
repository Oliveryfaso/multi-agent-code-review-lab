import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from macr.investigation.records import BackendCapabilities, CheckResult, CheckSpec, Limits, SnapshotRef
from macr.tools.test_runner import TestRunnerTool
from macr.tools.patch_verifier import PatchVerifierTool

try:
    from macr.execution.results import classify_check
except ModuleNotFoundError:
    classify_check = None


class FakeBackend:
    def __init__(self, result, ready=True):
        self.result, self.ready = result, ready
        self.requests = []

    def capabilities(self):
        return BackendCapabilities(**{name: self.ready for name in ("network_disabled", "readonly_input", "isolated_writes", "secrets_absent", "cpu_limit", "memory_limit", "pid_limit", "wall_limit", "output_limit", "process_tree_stop")})

    def run(self, check, snapshot):
        self.requests.append(check)
        self.result.check_id = check.check_id
        return self.result

    def inspect(self, check_id):
        return "outcome_unknown"

    def stop(self, check_id):
        return "outcome_unknown"


class CheckResultTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(classify_check, "true check status classifier missing")
        self.check = CheckSpec("check-1", limits=Limits(1, 1024, 4, 1, 1024))

    def observation(self, **kwargs):
        return {"execution_status": "completed", "exit_code": 0, **kwargs}

    def test_unexecuted_zero_skipped_unknown_never_pass(self):
        cases = [({"execution_status": "blocked"}, "not_applicable"),
                 (self.observation(test_counts={"passed": 0, "failed": 0, "skipped": 0}), "zero_tests"),
                 (self.observation(test_counts={"passed": 0, "failed": 0, "skipped": 2}), "skipped"),
                 (self.observation(), "unknown"),
                 (self.observation(test_counts={"passed": 1, "errors": 1}), "failed"),
                 ({"execution_status": "timeout", "test_counts": {"passed": 1}}, "unknown")]
        for raw, expected in cases:
            with self.subTest(expected=expected):
                result = classify_check(raw, self.check)
                self.assertEqual(result.observed_status, expected)
                self.assertNotEqual(result.observed_status, "passed")

    def test_positive_counts_and_reproduction_prediction(self):
        result = classify_check(self.observation(test_counts={"passed": 2, "failed": 0, "skipped": 1}), self.check)
        self.assertEqual(result.observed_status, "passed")
        self.check.prediction = {"observed_status": "failed"}
        reproduced = classify_check(self.observation(exit_code=1, test_counts={"passed": 0, "failed": 1}), self.check)
        self.assertEqual(reproduced.observed_status, "failed")
        self.assertEqual(reproduced.expectation_result, "matches")

    def test_invalid_counts_are_unknown_not_pass(self):
        for counts in ({"passed": True}, {"passed": -1}, {"passed": 1, "executed": 0}):
            self.assertNotEqual(classify_check(self.observation(test_counts=counts), self.check).observed_status, "passed")

    def test_missing_backend_never_spawns_or_copies(self):
        with patch("subprocess.run", side_effect=AssertionError("host execution forbidden")), patch("shutil.copytree", side_effect=AssertionError("raw copy forbidden")):
            result = TestRunnerTool().run(Path("untrusted-do-not-read"), "tests")
            self.assertFalse(result.ok)
            self.assertEqual(result.error_type, "environment_missing")
            patch_result = PatchVerifierTool().run(Path("untrusted-do-not-read"), "not a patch", "tests")
            self.assertFalse(patch_result.ok)
            self.assertEqual(patch_result.data["test_check"], "not_applicable")

    def test_backend_capability_gate_and_normalization(self):
        ref = SnapshotRef("snapshot-1", "content-1", "manifest.json", "snapshot")
        fake_pass = CheckResult("check-1", "completed", "passed", exit_code=0, test_counts={"passed": 0})
        backend = FakeBackend(fake_pass)
        result = TestRunnerTool().run_check(ref, self.check, backend)
        self.assertEqual(result.observed_status, "zero_tests")
        blocked = FakeBackend(fake_pass, ready=False)
        self.assertEqual(TestRunnerTool().run_check(ref, self.check, blocked).execution_status, "blocked")
        self.assertEqual(blocked.requests, [])
