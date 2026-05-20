import unittest

from macr.agents.solver import SolverAgent
from macr.prompts import BASE_AGENT_SYSTEM_PREFIX, system_prompt
from macr.schemas import AgentAnswer, Evidence, Plan, PlanStep


class PromptOptimizationTests(unittest.TestCase):
    def test_system_prompt_has_stable_cacheable_prefix(self):
        planner = system_prompt("planner instruction")
        solver = system_prompt("solver instruction")

        self.assertTrue(planner.startswith(BASE_AGENT_SYSTEM_PREFIX))
        self.assertTrue(solver.startswith(BASE_AGENT_SYSTEM_PREFIX))
        self.assertGreater(len(BASE_AGENT_SYSTEM_PREFIX.split()), 64)

    def test_solver_compacts_evidence_payload(self):
        evidence = [
            Evidence(
                file="backend/payments.py",
                line_start=index,
                line_end=index,
                reason="reason",
                symbol=f"symbol_{index}",
                source_tool="search_text",
                snippet="x" * 1000,
            )
            for index in range(12)
        ]
        solver = SolverAgent()
        compacted = solver._compact_evidence(evidence)

        self.assertEqual(len(compacted), 8)
        self.assertEqual(compacted[0].snippet, "x" * 1000)
        response_prompt = solver._generate_with_llm
        self.assertTrue(callable(response_prompt))


if __name__ == "__main__":
    unittest.main()
