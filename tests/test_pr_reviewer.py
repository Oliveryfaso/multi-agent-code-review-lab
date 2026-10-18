import unittest

from macr.agents.pr_reviewer import DiffReviewAgent


class DiffReviewAgentTests(unittest.TestCase):
    def secret_comments(self, line):
        diff = "\n".join(("diff --git a/config.py b/config.py", "--- a/config.py",
                          "+++ b/config.py", "@@ -0,0 +1 @@", "+" + line))
        return [comment for comment in DiffReviewAgent().review(diff)["comments"]
                if comment["message"] == "possible hardcoded secret"]

    def test_generated_or_referenced_credentials_are_not_hardcoded(self):
        for name in ("password", "secret", "token", "api_key"):
            for value in ("str(uuid4())", "load_from_environment()", "existing_value"):
                for line in (f"{name} = {value}", f"SimpleNamespace({name}={value})"):
                    with self.subTest(line=line):
                        self.assertEqual(self.secret_comments(line), [])

    def test_nonempty_literal_credentials_still_raise_high_risk(self):
        for name in ("password", "secret", "token", "api_key", "API_KEY"):
            for value in (repr("embedded-value"), '"embedded-value"',
                          '"""embedded-value"""', "'''embedded-value'''",
                          'r"""embedded-value"""', 'b"embedded-value"',
                          '"""', "'''", '"" "embedded-value"',
                          "123456", "0xABCDEF", "1e10", ".123"):
                for line in (f"{name} = {value}", f"SimpleNamespace({name}={value})"):
                    with self.subTest(line=line):
                        comments = self.secret_comments(line)
                        self.assertEqual(len(comments), 1)
                        self.assertEqual(comments[0]["severity"], "high")

    def test_empty_credential_placeholders_are_not_embedded_secrets(self):
        for name in ("password", "secret", "token", "api_key"):
            for value in ('""', "''", '""""""', "''''''", 'r""', 'b""', "None"):
                for line in (f"{name} = {value}", f"SimpleNamespace({name}={value})"):
                    with self.subTest(line=line):
                        self.assertEqual(self.secret_comments(line), [])

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
