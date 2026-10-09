import unittest
from pathlib import Path

from debugger_fixtures import PROJECT, fixture_root, write_files
from macr.agents.patcher import PatchAgent
from macr.agents.patch_ranker import PatchRankerAgent
from macr.investigation.records import BackendCapabilities, CheckResult, ExecutionProfile, Limits, SnapshotRef
from macr.providers.base import LLMResponse
from macr.schemas import Plan
from macr.tools.patch_verifier import PatchVerifierTool


class BadPatchProvider:
    name = "bad-patch"

    def complete(self, request):
        return LLMResponse(content='{"summary":"bad diff","target_files":["backend/payments.py"],"diff":"not a patch"}', provider=self.name, model="fake-contract-only")


class FixtureBackend:
    """Protocol fixture only: does not run tests or actually apply patches."""
    def capabilities(self):
        names = ("network_disabled", "readonly_input", "isolated_writes", "secrets_absent", "cpu_limit", "memory_limit", "pid_limit", "wall_limit", "output_limit", "process_tree_stop")
        return BackendCapabilities(**{name: True for name in names}, environment_ref="fake-contract-only")

    def run(self, check, snapshot):
        if check.purpose == "patch_apply":
            diff = (PROJECT / "artifacts" / check.input_artifacts[0]).read_text()
            valid = "@@" in diff
            return CheckResult(check.check_id, "completed", "passed" if valid else "failed", exit_code=0 if valid else 1, environment_ref="fake-contract-only")
        return CheckResult(check.check_id, "completed", "passed", exit_code=0, test_counts={"passed": 1}, environment_ref="fake-contract-only")


class PatchAgentTests(unittest.TestCase):
    def setUp(self):
        self.repo = write_files(fixture_root(), {"backend/payments.py": 'def payment(charge):\n    if charge["error"]:\n        return {"status": 402, "error": charge["error"]}\n', "backend/orders.py": 'def calculate_total(prices, item_id, quantity):\n    return prices.get(item_id, 0) * quantity\n'})
        self.ref = SnapshotRef("snapshot-1", "fixture-content", "fixture-manifest", str(self.repo))
        self.profile = ExecutionProfile("fake-only", "unittest", Limits(1, 1024, 4, 1, 1024))
        self.verifier = PatchVerifierTool(self.profile)

    def test_patch_artifact_verifies_without_mutating_repo(self):
        original = (self.repo / "backend/payments.py").read_text()
        artifact = PatchAgent().template_patch(self.repo, "402")
        result = self.verifier.run(self.repo, artifact.diff, "tests", backend=FixtureBackend(), snapshot=self.ref)
        self.assertIn("retryable", artifact.diff)
        self.assertEqual(result.data["test_check"], "passed")
        self.assertFalse(result.data["repaired"])
        self.assertEqual(result.data["test_result"]["environment_ref"], "fake-contract-only")
        self.assertEqual((self.repo / "backend/payments.py").read_text(), original)

    def test_llm_patch_falls_back_to_template_when_invalid(self):
        candidates, _ = PatchAgent(BadPatchProvider()).propose_candidates(self.repo, "402", Plan("patch", "low", []), [], prefer_llm=True)
        for candidate in candidates:
            candidate.verification = self.verifier.run(self.repo, candidate.diff, backend=FixtureBackend(), snapshot=self.ref).data
        selected, ranking = PatchRankerAgent().choose(candidates)
        self.assertEqual(selected.source, "template")
        self.assertTrue(any(row["source"] == "llm" for row in ranking))
        self.assertNotIn("tests_passed", ranking[0]["reasons"])
        self.assertEqual(selected.verification["test_check"], "not_applicable")

    def test_second_legacy_template_stays_available(self):
        artifact = PatchAgent().template_patch(self.repo, "calculate_total")
        self.assertIn("quantity <= 0", artifact.diff)
        self.assertEqual(artifact.source, "template")


if __name__ == "__main__":
    unittest.main()
