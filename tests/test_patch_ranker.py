import unittest

from macr.agents.patch_ranker import PatchRankerAgent
from macr.schemas import PatchArtifact
from dataclasses import asdict
from macr.investigation.records import CheckResult


class PatchRankerTests(unittest.TestCase):
    def test_ranker_prefers_verified_small_patch(self):
        bad = PatchArtifact(
            summary="bad",
            diff="not a patch",
            target_files=["backend/payments.py"],
            source="llm",
            verification={"patch_apply_check": "failed", "test_check": "skipped"},
        )
        good = PatchArtifact(
            summary="good",
            diff="--- a/backend/payments.py\n+++ b/backend/payments.py\n@@ -1 +1 @@\n-a\n+b\n",
            target_files=["backend/payments.py"],
            source="template",
            verification={"patch_apply_check": "passed", "test_check": "passed", "test_result": asdict(CheckResult("check-1", "completed", "passed", exit_code=0, test_counts={"passed": 1}))},
        )

        selected, ranking = PatchRankerAgent().choose([bad, good])

        self.assertIs(selected, good)
        self.assertEqual(ranking[0]["source"], "template")
        self.assertIn("tests_passed", ranking[0]["reasons"])

    def test_legacy_pass_label_does_not_reward_unexecuted_tests(self):
        candidate = PatchArtifact("apply only", "diff", ["a.py"], verification={"patch_apply_check": "passed", "test_check": "passed"})
        ranking = PatchRankerAgent().rank([candidate])
        self.assertNotIn("tests_passed", ranking[0]["reasons"])

    def test_no_applicable_candidate_is_not_selected(self):
        candidate = PatchArtifact("bad", "diff", ["a.py"], verification={"patch_apply_check": "failed"})
        with self.assertRaises(ValueError):
            PatchRankerAgent().choose([candidate])


if __name__ == "__main__":
    unittest.main()
