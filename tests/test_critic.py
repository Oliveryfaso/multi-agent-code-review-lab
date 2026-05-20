import unittest

from macr.agents.critic import RetrievalCriticAgent
from macr.schemas import Evidence, Plan, PlanStep


class RetrievalCriticTests(unittest.TestCase):
    def test_critic_suggests_rewrite_for_empty_recall(self):
        plan = Plan(
            intent="code_qa",
            risk_level="low",
            steps=[PlanStep("search", "text_search")],
            search_terms=["call", "reference"],
        )
        critique = RetrievalCriticAgent().critique(
            "process_payment 的调用链涉及哪些模块？",
            plan,
            [
                {"ok": False, "error_type": "empty_recall", "retryable": True},
                {"ok": False, "error_type": "empty_recall", "retryable": True},
            ],
            set(),
            [],
        )

        self.assertEqual(critique.quality, "poor")
        self.assertIn("query_rewrite", critique.actions)
        self.assertIn("process_payment", critique.suggested_terms)

    def test_critic_accepts_good_retrieval(self):
        plan = Plan(
            intent="code_qa",
            risk_level="low",
            steps=[PlanStep("search", "text_search")],
            search_terms=["process_payment"],
        )
        critique = RetrievalCriticAgent().critique(
            "process_payment",
            plan,
            [{"ok": True, "error_type": None}],
            {"backend/payments.py"},
            [Evidence("backend/payments.py", 1, 1, "symbol", symbol="process_payment")],
        )

        self.assertEqual(critique.quality, "good")
        self.assertEqual(critique.actions, [])


if __name__ == "__main__":
    unittest.main()
