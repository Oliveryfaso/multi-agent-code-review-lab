import unittest

from macr.agents.planner import RuleBasedPlanner
from macr.agents.routing_policy import RoutingPolicyAgent


class RoutingPolicyTests(unittest.TestCase):
    def test_skips_llm_solver_for_low_risk_code_qa_even_with_provider(self):
        plan = RuleBasedPlanner().plan("这个接口在哪里鉴权？")

        policy = RoutingPolicyAgent().decide(
            plan,
            provider_available=True,
            llm_planner_enabled=False,
            generate_patch=False,
            test_selector=None,
        )

        self.assertFalse(policy.use_llm_solver)
        self.assertTrue(any(item.step == "llm_solver" and item.action == "skip" for item in policy.skipped_steps))

    def test_uses_llm_solver_for_patch_task_when_provider_exists(self):
        plan = RuleBasedPlanner().plan("修复支付失败问题")

        policy = RoutingPolicyAgent().decide(
            plan,
            provider_available=True,
            llm_planner_enabled=True,
            generate_patch=True,
            test_selector="tests/test_auth.py",
        )

        self.assertTrue(policy.use_llm_solver)
        self.assertTrue(any(item.step == "patch_agent" and item.action == "execute" for item in policy.executed_steps))


if __name__ == "__main__":
    unittest.main()
