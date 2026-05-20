import unittest
from types import SimpleNamespace

from macr.pr_outputs import to_github_comments, to_sarif


class PrOutputTests(unittest.TestCase):
    def test_formats_github_comments_and_sarif(self):
        trace = SimpleNamespace(
            board={
                "pr_review": [
                    {
                        "payload": {
                            "risk_level": "high",
                            "changed_files": ["backend/payments.py"],
                            "summary": "risk",
                            "test_suggestions": ["run tests"],
                            "comments": [
                                {
                                    "file": "backend/payments.py",
                                    "line": 12,
                                    "severity": "high",
                                    "category": "code_risk",
                                    "message": "dangerous execution path",
                                    "evidence": "eval(cmd)",
                                }
                            ],
                        }
                    }
                ]
            }
        )

        github = to_github_comments(trace)
        sarif = to_sarif(trace)

        self.assertEqual(github[0]["path"], "backend/payments.py")
        self.assertEqual(github[0]["line"], 12)
        self.assertEqual(sarif["runs"][0]["results"][0]["level"], "error")
        self.assertEqual(sarif["runs"][0]["results"][0]["ruleId"], "code_risk")


if __name__ == "__main__":
    unittest.main()
