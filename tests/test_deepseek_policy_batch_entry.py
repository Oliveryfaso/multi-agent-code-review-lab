"""Explicit offline batch entry: claim before one fixture run, then close from artifacts."""
import json
import unittest

from debugger_fixtures import fixture_root
from macr.evals.deepseek_policy_batch import (CORE_IMPLEMENTATION_FILES,
    PolicyBatchError, claim_policy_cell, prepare_policy_batch)
from macr.evals.deepseek_policy_batch_entry import run_next_offline_policy_cell
from macr.evals.deepseek_policy_manifest import prepare_policy_manifest
from macr.providers.deepseek import DeepSeekProvider
from test_deepseek_policy_runner import FakeTransport, MODEL, UNKNOWN, call, reply


class PolicyBatchEntryTests(unittest.TestCase):
    def setUp(self):
        self.root = fixture_root()
        self.dataset = self.root / "dataset"
        snapshot = self.dataset / "snapshots" / "p01"
        snapshot.mkdir(parents=True)
        (snapshot / "logic.py").write_text("def gate(x):\n    return x > 0\n", encoding="utf-8")
        (self.dataset / "gold.json").write_text('{"secret":"GOLD_NEVER_SEND"}', encoding="utf-8")
        (self.dataset / "blind-review-format.json").write_text('{"rubric":"RUBRIC_NEVER_SEND"}',
                                                                  encoding="utf-8")
        self.case = {"id": "p01", "snapshot_root": "snapshots/p01",
            "question": "Does gate satisfy the scenario?", "scenario": "Call gate(0).",
            "assumptions": [], "authorized_files": ["logic.py"],
            "initial_evidence_files": ["logic.py"], "stratum": "full_context",
            "evidence_parity": "equal", "entrypoint": {"file": "logic.py", "symbol": "gate"}}
        manifest = self.root / "manifests" / "p01"
        manifest.parent.mkdir()
        prepare_policy_manifest(self.case, dataset_root=self.dataset, out=manifest, model=MODEL)
        project = self.root / "project"
        for relative in CORE_IMPLEMENTATION_FILES:
            path = project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# fixture implementation\n", encoding="utf-8")
        self.outputs = self.root / "outputs"
        self.outputs.mkdir()
        self.control = self.root / "control"
        self.batch = prepare_policy_batch([manifest], dataset_root=self.dataset,
            project_root=project, control_root=self.control, output_root=self.outputs,
            seed=7, implementation_version="offline-entry-v1")

    def _transport(self, arm):
        answer = json.dumps(UNKNOWN)
        if arm in ("A", "B"):
            return FakeTransport(reply(answer))
        if arm == "C":
            return FakeTransport(reply(answer), reply(json.dumps({"status": "accept",
                "coverage": "complete", "reason": "scripted fixture"})))
        return FakeTransport(reply(None, calls=[call("finish-1", "finish",
            {"diagnosis": UNKNOWN})]))

    def _provider(self, transport, **options):
        return DeepSeekProvider(model=MODEL["model_id"], transport=transport, **options)

    def _run(self, cell, transport, **options):
        return run_next_offline_policy_cell(self.control, dataset_root=self.dataset,
            task_id=cell["task_id"], repeat=cell["repeat"], arm=cell["arm"],
            out=cell["out"], provider=self._provider(transport, **options))

    def test_claimed_fixture_run_closes_from_real_receipts_and_rejects_repeat(self):
        first, second = self.batch["cells"][:2]
        transport = self._transport(first["arm"])
        completed = self._run(first, transport)
        self.assertEqual(completed["result"]["status"], "semantic_pending")
        self.assertEqual(completed["outcome"]["state"], "completed")
        self.assertEqual(completed["result"]["model_calls_live"], 0)
        self.assertNotIn("GOLD_NEVER_SEND", json.dumps(transport.posts))
        self.assertNotIn("RUBRIC_NEVER_SEND", json.dumps(transport.posts))
        with self.assertRaises(PolicyBatchError):
            self._run(first, self._transport(first["arm"]))
        self.assertEqual(self._run(second, self._transport(second["arm"]))["outcome"]["state"],
                         "completed")

    def test_unknown_fixture_response_halts_whole_batch_without_retry(self):
        first, second = self.batch["cells"][:2]
        transport = FakeTransport(TimeoutError())
        completed = self._run(first, transport)
        self.assertEqual(completed["result"]["status"], "remote_outcome_unknown")
        self.assertEqual(completed["outcome"]["state"], "batch_halted_unknown")
        self.assertEqual(len(transport.posts), 1)
        with self.assertRaises(PolicyBatchError):
            self._run(second, self._transport(second["arm"]))

    def test_remote_configuration_is_rejected_before_claim(self):
        first = self.batch["cells"][0]
        for options in ({"allow_remote": True}, {"base_url": "https://elsewhere.example"},
                        {"api_key": "KEY_SENTINEL_NEVER_LOG"}):
            with self.subTest(options=tuple(options)):
                transport = self._transport(first["arm"])
                with self.assertRaises(PolicyBatchError):
                    self._run(first, transport, **options)
                self.assertEqual(transport.posts, [])
        self.assertFalse(list((self.control / "registry").iterdir()))
        ticket = claim_policy_cell(self.control, task_id=first["task_id"],
            repeat=first["repeat"], arm=first["arm"], out=first["out"])
        self.assertNotIn("KEY_SENTINEL_NEVER_LOG", json.dumps(ticket))


if __name__ == "__main__":
    unittest.main()
