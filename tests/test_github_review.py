import unittest

from scripts.post_github_review import build_review_payload


class GithubReviewTests(unittest.TestCase):
    def test_build_review_payload_filters_invalid_comments(self):
        payload = build_review_payload(
            [
                {"path": "src/app.py", "line": 12, "side": "RIGHT", "body": "Check this branch."},
                {"path": "src/skip.py", "body": "missing line"},
            ],
            commit_id="abc123",
        )

        self.assertEqual(payload["event"], "COMMENT")
        self.assertEqual(payload["commit_id"], "abc123")
        self.assertEqual(len(payload["comments"]), 1)
        self.assertEqual(payload["comments"][0]["path"], "src/app.py")
        self.assertEqual(payload["comments"][0]["line"], 12)


if __name__ == "__main__":
    unittest.main()
