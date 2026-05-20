import unittest

from macr.agents.final_reviewer import FinalReviewAgent
from macr.schemas import AgentAnswer, Evidence, Plan, PlanStep


class FinalReviewAgentTests(unittest.TestCase):
    def test_final_reviewer_accepts_coherent_answer(self):
        evidence = [
            Evidence("backend/payments.py", 17, 27, "reason", symbol="process_payment", source_tool="parse_ast"),
            Evidence("backend/orders.py", 31, 36, "reason", symbol="mark_order_paid", source_tool="code_graph"),
            Evidence("backend/notifications.py", 10, 12, "reason", symbol="send_payment_receipt", source_tool="symbol_graph"),
        ]
        answer = AgentAnswer("See backend/payments.py:17", evidence, 0.8, [])
        plan = Plan("code_qa", "low", [PlanStep("search", "text_search")], search_terms=["process_payment"])

        report = FinalReviewAgent().review("query", plan, answer, evidence, [], retry_count=0)

        self.assertTrue(report.ok)
        self.assertFalse(report.retry_recommended)
        self.assertFalse(report.human_review_required)

    def test_final_reviewer_requests_retry_then_human_review(self):
        plan = Plan("code_qa", "low", [PlanStep("search", "text_search")], search_terms=["unknown"])
        answer = AgentAnswer("not sure", [], 0.2, [])

        first = FinalReviewAgent().review("unknown behavior", plan, answer, [], [], retry_count=0)
        second = FinalReviewAgent().review("unknown behavior", plan, answer, [], [], retry_count=1)

        self.assertFalse(first.ok)
        self.assertTrue(first.retry_recommended)
        self.assertFalse(first.human_review_required)
        self.assertTrue(second.human_review_required)
        self.assertTrue(second.questions)

    def test_final_reviewer_flags_uncovered_external_terms(self):
        evidence = [
            Evidence("backend/auth.py", 1, 5, "auth evidence", symbol="login", source_tool="parse_ast"),
            Evidence("backend/server.py", 1, 5, "handler evidence", symbol="login_handler", source_tool="parse_ast"),
            Evidence("tests/test_auth.py", 1, 5, "test evidence", symbol="test_login_success", source_tool="parse_ast"),
        ]
        answer = AgentAnswer("maybe auth", evidence, 0.8, [])
        plan = Plan("code_qa", "low", [PlanStep("search", "text_search")], search_terms=["graphql"])

        report = FinalReviewAgent().review("GraphQL profile mutation 在哪里？", plan, answer, evidence, [], retry_count=1)

        self.assertFalse(report.ok)
        self.assertTrue(report.human_review_required)
        self.assertTrue(any("graphql" in issue for issue in report.issues))


if __name__ == "__main__":
    unittest.main()
