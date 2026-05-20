import unittest

from macr.agents.pr_reviewer import DiffReviewAgent


class DiffReviewAgentTests(unittest.TestCase):
    def test_flags_high_risk_diff_and_missing_tests(self):
        diff = """diff --git a/backend/payments.py b/backend/payments.py
--- a/backend/payments.py
+++ b/backend/payments.py
@@ -1,2 +1,5 @@
+API_TOKEN = "secret"
+def run(cmd):
+    return eval(cmd)
"""

        report = DiffReviewAgent().review(diff)

        self.assertEqual(report["risk_level"], "high")
        self.assertEqual(report["changed_files"], ["backend/payments.py"])
        self.assertTrue(any(comment["severity"] == "high" for comment in report["comments"]))
        self.assertIn("add or run targeted tests for changed production files", report["test_suggestions"])


if __name__ == "__main__":
    unittest.main()
