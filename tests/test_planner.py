import unittest

from macr.agents.planner import RuleBasedPlanner


class PlannerTests(unittest.TestCase):
    def test_planner_maps_chinese_auth_query_to_terms(self):
        plan = RuleBasedPlanner().plan("登录失败为什么还能访问接口？")

        self.assertEqual(plan.intent, "bug_investigation")
        self.assertIn("login", plan.search_terms)
        self.assertIn("auth", plan.search_terms)


if __name__ == "__main__":
    unittest.main()
