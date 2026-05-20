import unittest

from macr.costs import estimate_llm_cost, summarize_llm_costs
from macr.providers.base import LLMResponse


class CostTests(unittest.TestCase):
    def test_deepseek_cost_estimator_uses_cache_hit_and_miss_tokens(self):
        response = LLMResponse(
            content="ok",
            provider="deepseek",
            model="deepseek-v4-flash",
            usage={
                "prompt_tokens": 1000,
                "completion_tokens": 200,
                "prompt_cache_hit_tokens": 600,
                "prompt_cache_miss_tokens": 400,
            },
        )

        cost = estimate_llm_cost(response)

        self.assertEqual(cost["cache_hit_ratio"], 0.6)
        self.assertEqual(cost["prompt_cache_hit_tokens"], 600)
        self.assertGreater(cost["estimated_cost_usd"], 0)

    def test_summarize_llm_costs_aggregates_calls(self):
        calls = [
            {
                "cost": {
                    "estimated_cost_usd": 0.01,
                    "prompt_tokens": 100,
                    "prompt_cache_hit_tokens": 50,
                }
            },
            {
                "cost": {
                    "estimated_cost_usd": 0.02,
                    "prompt_tokens": 300,
                    "prompt_cache_hit_tokens": 150,
                }
            },
        ]

        summary = summarize_llm_costs(calls)

        self.assertEqual(summary["llm_call_count"], 2)
        self.assertEqual(summary["estimated_cost_usd"], 0.03)
        self.assertEqual(summary["cache_hit_ratio"], 0.5)


if __name__ == "__main__":
    unittest.main()
