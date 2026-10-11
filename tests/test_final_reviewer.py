import unittest
from dataclasses import replace
from unittest.mock import patch

from macr.agents.final_reviewer import FinalReviewAgent
from macr.agents.solver import SolverAgent
from macr.memory.evidence_store import EvidenceStore
from macr.schemas import AgentAnswer, Evidence, Plan, PlanStep


class FinalReviewAgentTests(unittest.TestCase):
    def setUp(self):
        for target in ('subprocess.Popen', 'socket.create_connection'):
            guard = patch(target, side_effect=AssertionError('review must remain offline'))
            guard.start()
            self.addCleanup(guard.stop)

    def test_final_reviewer_accepts_coherent_answer(self):
        evidence = [
            Evidence("backend/payments.py", 17, 27, "reason", symbol="process_payment", source_tool="parse_ast"),
            Evidence("backend/orders.py", 31, 36, "reason", symbol="mark_order_paid", source_tool="code_graph"),
            Evidence("backend/notifications.py", 10, 12, "reason", symbol="send_payment_receipt", source_tool="symbol_graph"),
        ]
        plan = Plan("code_qa", "low", [PlanStep("search", "text_search")], search_terms=["process_payment"])
        store = EvidenceStore()
        store.extend(evidence)
        answer, response = SolverAgent().solve("query", plan, store.items())

        report = FinalReviewAgent().review("query", plan, answer, store.items(), [], retry_count=0)

        self.assertIsNone(response)
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

    def test_same_location_from_three_tools_does_not_inflate_support(self):
        store = EvidenceStore()
        store.extend([
            Evidence('auth.py', 1, 5, 'auth source', source_tool='parse_ast'),
            Evidence('auth.py', 1, 5, 'auth callers', source_tool='code_graph'),
            Evidence('auth.py', 1, 5, 'GraphQL route', source_tool='symbol_graph'),
        ])
        plan = Plan('code_qa', 'low', [])
        answer, response = SolverAgent().solve('query', plan, store.items())
        reviewer = FinalReviewAgent()
        report = reviewer.review('query', plan, answer, store.items(), [])
        single = replace(answer, evidence=answer.evidence[:1])
        single_report = reviewer.review('query', plan, single, store.items(), [])
        coverage = reviewer.review('GraphQL route', plan, answer, store.items(), [])

        self.assertIsNone(response)
        self.assertEqual(len(store.items()), 3)
        self.assertFalse(report.ok)
        self.assertIn('evidence too thin: 1', report.issues)
        self.assertEqual(report.confidence, single_report.confidence)
        self.assertFalse(any('graphql' in issue for issue in coverage.issues))

    def test_empty_answer_references_do_not_borrow_retrieved_evidence(self):
        evidence = [Evidence(f'source{i}.py', 1, 2, 'retrieved') for i in range(3)]
        answer = AgentAnswer('uncited answer', [], 0.8, [])

        report = FinalReviewAgent().review('query', Plan('code_qa', 'low', []), answer, evidence, [])

        self.assertFalse(report.ok)
        self.assertIn('evidence too thin: 0', report.issues)

    def test_foreign_answer_references_do_not_count_as_support(self):
        evidence = [Evidence(f'source{i}.py', 1, 2, 'retrieved') for i in range(3)]
        foreign = [Evidence(f'foreign{i}.py', 1, 2, 'invented') for i in range(3)]
        answer = AgentAnswer('foreign citations', foreign, 0.8, [])

        report = FinalReviewAgent().review('query', Plan('code_qa', 'low', []), answer, evidence, [])

        self.assertFalse(report.ok)
        self.assertIn('evidence too thin: 0', report.issues)
        self.assertTrue(any('outside authoritative evidence' in issue for issue in report.issues))

    def test_matching_reference_uses_authoritative_text_for_coverage(self):
        evidence = [Evidence(f'auth{i}.py', 1, 2, 'auth source', source_tool='parse_ast') for i in range(3)]
        forged = [replace(item, reason='GraphQL resolver', snippet='graphql') for item in evidence]
        answer = AgentAnswer('claimed GraphQL support', forged, 0.8, [])

        report = FinalReviewAgent().review('GraphQL resolver', Plan('code_qa', 'low', []), answer, evidence, [])

        self.assertFalse(report.ok)
        self.assertTrue(any('graphql' in issue for issue in report.issues))
        self.assertFalse(any('outside authoritative evidence' in issue for issue in report.issues))


if __name__ == "__main__":
    unittest.main()
