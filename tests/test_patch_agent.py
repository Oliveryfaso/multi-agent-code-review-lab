from pathlib import Path
import unittest

from macr.agents.orchestrator import Orchestrator
from macr.memory.trace_store import TraceStore
from macr.providers.base import LLMResponse


class BadPatchProvider:
    name = "bad-patch"

    def complete_sync(self, messages, tools=None, response_schema=None, metadata=None):
        return LLMResponse(
            content='{"summary":"bad diff","target_files":["backend/payments.py"],"diff":"not a patch"}',
            provider=self.name,
            model="bad-patch-model",
        )


class PatchAgentTests(unittest.TestCase):
    def test_patch_artifact_verifies_without_mutating_repo(self):
        repo = Path("sample_repos/sample_python_api")
        original = (repo / "backend/payments.py").read_text()
        trace = Orchestrator(trace_store=TraceStore(Path("traces/test_tmp"))).run_patch(
            repo,
            "银行卡扣款失败会在哪里返回 402？请给出最小修复",
            "tests",
        )

        self.assertIsNotNone(trace.patch)
        self.assertIn("retryable", trace.patch.diff)
        self.assertEqual(trace.patch.verification["patch_apply_check"], "passed")
        self.assertEqual(trace.patch.verification["test_check"], "passed")
        self.assertEqual((repo / "backend/payments.py").read_text(), original)

    def test_llm_patch_falls_back_to_template_when_invalid(self):
        repo = Path("sample_repos/sample_python_api")
        trace = Orchestrator(
            trace_store=TraceStore(Path("traces/test_tmp")),
            provider=BadPatchProvider(),
        ).run_patch(
            repo,
            "银行卡扣款失败会在哪里返回 402？请给出最小修复",
            "tests",
            prefer_llm_patch=True,
        )

        self.assertIsNotNone(trace.patch)
        self.assertEqual(trace.patch.source, "template")
        self.assertIn("retryable", trace.patch.diff)
        self.assertEqual(trace.patch.verification["patch_apply_check"], "passed")
        ranking = trace.patch.verification["candidate_ranking"]
        self.assertEqual(ranking[0]["source"], "template")
        self.assertTrue(any(item["source"] == "llm" for item in ranking))
        self.assertIn("patch_ranking", [item["kind"] for item in trace.board["patch"]])
        self.assertTrue(trace.metrics["contract_validation"]["ok"])
        self.assertIn("patching", [state.name for state in trace.state_timeline])


if __name__ == "__main__":
    unittest.main()
