import unittest

from macr.agents.patch_ranker import PatchRankerAgent
from macr.schemas import PatchArtifact


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
            verification={"patch_apply_check": "passed", "test_check": "passed"},
        )

        selected, ranking = PatchRankerAgent().choose([bad, good])

        self.assertIs(selected, good)
        self.assertEqual(ranking[0]["source"], "template")
        self.assertIn("tests_passed", ranking[0]["reasons"])


if __name__ == "__main__":
    unittest.main()
