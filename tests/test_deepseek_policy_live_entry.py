"""No-network contract tests for the separate, explicitly approved pilot entry."""
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.evals.deepseek_policy_batch import (CORE_IMPLEMENTATION_FILES,
    PolicyBatchError, prepare_policy_batch)
from macr.evals.deepseek_policy_batch_entry import run_next_offline_policy_cell
from macr.evals.deepseek_policy_live_entry import (LiveEntryError,
    prepare_approved_policy_batch, run_next_approved_policy_cell)
from macr.evals.deepseek_policy_live_scope import (LiveApproval, validate_live_scope,
    write_log_policy)
from macr.evals.deepseek_policy_manifest import prepare_policy_manifest
from macr.evals.deepseek_policy_spend import spend_state
from macr.providers.deepseek import DeepSeekProvider
from test_deepseek_policy_runner import FakeTransport, UNKNOWN, call, reply


PROJECT = Path(__file__).resolve().parents[1]
DATASET = PROJECT / "evals" / "deepseek-policy-dev-v1"
MODEL = {"model_id": "deepseek-flash", "provider": "deepseek",
         "thinking": True, "reasoning_effort": "high"}


class LiveEntryTests(unittest.TestCase):
    def test_model_drift_keeps_reservation_instead_of_pricing_as_approved_model(self):
        self.prepare()
        bad = reply(json.dumps(UNKNOWN))
        bad['model'] = 'different-model'
        completed, _ = self.run_first(bad)
        money = spend_state(self.control)
        self.assertEqual((money['state'], money['spent_usd'], money['reserved_usd']),
                         ('halted_unknown', '0.000000', '0.330000'))
        result = completed['result']
        self.assertEqual((result['status'], result['requests_attempted'], result['tool_reads']),
                         ('failed', 1, 0))
        self.assertIsNone(result['answer'])
        self.assertEqual(result['requests'][0]['error_code'], 'model_identity_drift')
        self.assertEqual(completed['outcome']['state'], 'batch_halted_model_drift')
        self.assertEqual(money['request_count'], 1)

    def test_invalid_assistant_settles_bound_usage_but_stops_before_another_key_read(self):
        self.prepare()
        bad = reply(json.dumps(UNKNOWN))
        bad['choices'][0]['message']['role'] = 'invalid-role'
        completed, _ = self.run_first(bad)
        money = spend_state(self.control)
        self.assertEqual((money['state'], money['spent_usd'], money['reserved_usd']),
                         ('open', '0.000014', '0.000000'))
        result = completed['result']
        self.assertEqual((result['status'], result['requests_attempted'], result['tool_reads']),
                         ('failed', 1, 0))
        self.assertIsNone(result['answer'])
        evidence = result['requests'][0]['response']
        self.assertEqual(evidence['state'], 'unknown')
        self.assertIsNone(evidence['raw_usage'])
        self.assertEqual(evidence['billing_evidence']['raw_usage']['prompt_tokens'], 12)
        self.assertEqual(completed['outcome']['state'], 'batch_halted_contract')
        next_cell = self.batch['cells'][1]
        reads = []
        with self.assertRaises(LiveEntryError):
            run_next_approved_policy_cell(self.control, dataset_root=DATASET,
                task_id=next_cell['task_id'], repeat=next_cell['repeat'], arm=next_cell['arm'],
                out=next_cell['out'], approval=self.approval,
                key_reader=lambda: reads.append(True) or self.key,
                scripted_replies=(reply(json.dumps(UNKNOWN)),))
        self.assertEqual(reads, [])
        self.assertEqual(spend_state(self.control)['request_count'], 1)

    def test_unbound_billing_evidence_keeps_full_reservation(self):
        import macr.providers.deepseek as adapter
        self.prepare()
        bad = reply(json.dumps(UNKNOWN))
        bad['choices'][0]['message']['role'] = 'invalid-role'
        original = adapter._billing_evidence
        def wrong_binding(*args, **kwargs):
            return original(*args, **kwargs) | {'request_sha256': 'f' * 64}
        with patch.object(adapter, '_billing_evidence', side_effect=wrong_binding):
            completed, _ = self.run_first(bad)
        money = spend_state(self.control)
        self.assertEqual((money['state'], money['reserved_usd'], money['spent_usd']),
                         ('halted_unknown', '0.330000', '0.000000'))
        evidence = completed['result']['requests'][0]['response']
        self.assertNotIn('billing_evidence', evidence)
        self.assertTrue(evidence['billing_evidence_rejected'])

    def test_real_dispatch_uses_approved_predecessor_and_refuses_mismatch_before_key(self):
        self.prepare()
        approved = replace(self.approval, predecessor_batch_sha256="a" * 64)
        first = self.batch["cells"][0]
        state = spend_state(self.control)
        state.update(carried_request_count=2, carried_batch_sha256="b" * 64)
        reads = []
        with patch("macr.evals.deepseek_policy_live_entry.spend_state", return_value=state):
            with self.assertRaises(LiveEntryError) as blocked:
                run_next_approved_policy_cell(self.control, dataset_root=DATASET,
                    task_id=first["task_id"], repeat=1, arm=first["arm"],
                    out=first["out"], approval=approved,
                    key_reader=lambda: reads.append(True) or self.key)
        self.assertEqual(blocked.exception.code, "live_cumulative_budget_missing")
        self.assertEqual(reads, [])
        # Persistent carry/usage verification is tested by the spend module.
        # This fixture checks that the real entry wires any explicitly approved
        # settled predecessor to the transport boundary, rather than a private constant.
        state["carried_batch_sha256"] = "a" * 64
        (self.outputs / "retention.json").unlink()
        write_log_policy(validate_live_scope(approved, self.batch, dataset_root=DATASET))
        with patch("macr.evals.deepseek_policy_live_entry.spend_state", return_value=state), patch(
                "macr.evals.deepseek_policy_live_entry._ApprovedTransport",
                side_effect=RuntimeError("fixture-stop-before-post")):
            with self.assertRaisesRegex(RuntimeError, "fixture-stop-before-post"):
                run_next_approved_policy_cell(self.control, dataset_root=DATASET,
                    task_id=first["task_id"], repeat=1, arm=first["arm"],
                    out=first["out"], approval=approved,
                    key_reader=lambda: reads.append(True) or self.key)
        self.assertEqual(reads, [True])
        self.assertFalse(list((self.control / "registry").iterdir()))

    def test_prepare_refuses_predecessor_outside_approved_scope(self):
        approved = replace(self.approval, predecessor_batch_sha256="a" * 64)
        with self.assertRaises(LiveEntryError) as blocked:
            prepare_approved_policy_batch(self.control, dataset_root=DATASET,
                approval=approved, predecessor_control_root=self.root / "previous" / "control",
                predecessor_batch_sha256="b" * 64)
        self.assertEqual(blocked.exception.code, "live_predecessor_scope_mismatch")
        self.assertFalse((self.control / "spend").exists())
        self.assertFalse((self.outputs / "retention.json").exists())

    def test_transport_metadata_survives_wrapping_and_unknown_reserve_is_kept(self):
        self.prepare()
        bad = reply(None)
        del bad["choices"][0]["message"]["content"]
        bad['usage'] = None  # No independent usage proof: the hold must remain.

        class Wire:
            response_metadata = {"schema": "macr.provider.failure.v1",
                "stage": "http_status", "http_status": 200, "response_bytes": 123}

            def post(self, *args):
                return bad

        with patch("macr.evals.deepseek_policy_live_entry._ScriptedWire", return_value=Wire()):
            completed, _ = self.run_first(bad)
        response = completed["result"]["requests"][0]["response"]
        diagnostic = response["failure_diagnostic"]
        self.assertEqual((response["state"], diagnostic["stage"],
                          diagnostic["http_status"], diagnostic["response_bytes"]),
                         ("unknown", "assistant_message", 200, 123))
        self.assertEqual((spend_state(self.control)["state"],
                          spend_state(self.control)["reserved_usd"]),
                         ("halted_unknown", "0.330000"))

    def test_real_dispatch_requires_verified_carry_before_key_input(self):
        self.prepare()
        first = self.batch["cells"][0]
        reads = []
        with self.assertRaises(LiveEntryError) as blocked:
            run_next_approved_policy_cell(self.control, dataset_root=DATASET,
                task_id=first["task_id"], repeat=1, arm=first["arm"],
                out=first["out"], approval=self.approval,
                key_reader=lambda: reads.append(True) or self.key)
        self.assertEqual(blocked.exception.code, "live_cumulative_budget_missing")
        self.assertEqual(reads, [])
        self.assertFalse(list((self.control / "registry").iterdir()))

    def setUp(self):
        self.root = fixture_root()
        cases = json.loads((DATASET / "cases.json").read_text(encoding="utf-8"))["cases"]
        self.cases = {case["id"]: case for case in cases}
        manifests = self.root / "manifests"
        manifests.mkdir(mode=0o700)
        manifest_dirs = []
        sources = []
        for case in cases:
            directory = manifests / case["id"]
            prepare_policy_manifest(case, dataset_root=DATASET, out=directory,
                                    model=MODEL)
            manifest_dirs.append(directory)
            sources.extend(case["snapshot_root"] + "/" + name
                           for name in case["authorized_files"])
        self.outputs = self.root / "outputs"
        self.outputs.mkdir(mode=0o700)
        self.control = self.root / "control"
        project = self.root / "project"
        for relative in CORE_IMPLEMENTATION_FILES:
            source = PROJECT / relative
            target = project / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        self.batch = prepare_policy_batch(manifest_dirs, dataset_root=DATASET,
            project_root=project, control_root=self.control, output_root=self.outputs,
            seed=41, implementation_version="live-fake-contract-v1")
        self.approval = LiveApproval(granted=True, approval_ref="DUMMY_APPROVAL_TEST_ONLY",
            batch_sha256=self.batch["batch_sha256"], model_id="deepseek-flash",
            endpoint="https://api.deepseek.com/chat/completions", max_requests=84,
            max_usd="3.00", price_snapshot_id="flash-peak-20261028",
            price_checked_on="2026-10-28", approved_sources=tuple(sorted(sources)),
            log_root=self.outputs, retention_days=14)
        self.key = "DUMMY_KEY_NEVER_PERSIST"

    def prepare(self, approval=None):
        return prepare_approved_policy_batch(self.control, dataset_root=DATASET,
            approval=self.approval if approval is None else approval)

    def run_first(self, answer, *, approval=None, read_key=None):
        first = self.batch["cells"][0]
        reads = []

        def key_reader():
            reads.append(True)
            return self.key

        completed = run_next_approved_policy_cell(self.control, dataset_root=DATASET,
            task_id=first["task_id"], repeat=first["repeat"], arm=first["arm"],
            out=first["out"], approval=self.approval if approval is None else approval,
            key_reader=read_key or key_reader, scripted_replies=(answer,))
        return completed, reads

    def test_fake_one_cell_has_durable_usd_receipt_and_no_secret_or_gold(self):
        self.prepare()
        first = self.batch["cells"][0]
        self.assertEqual(first["arm"], "A")
        answer = reply(json.dumps(UNKNOWN))
        completed, reads = self.run_first(answer)
        self.assertEqual(reads, [True])
        self.assertEqual(completed["result"]["status"], "semantic_pending")
        self.assertEqual(completed["outcome"]["state"], "completed")
        self.assertEqual(completed["result"]["model_calls_live"], 0)
        self.assertEqual(completed["result"]["provenance"], "approved_live_fake")
        self.assertEqual(completed["result"]["requests_attempted"], 1)
        self.assertEqual(completed["result"]["requests"][0]["request"]["model"],
                         "deepseek-flash")
        state = spend_state(self.control)
        self.assertEqual((state["state"], state["request_count"], state["reserved_usd"]),
                         ("open", 1, "0.000000"))
        self.assertEqual(state["spent_usd"], "0.000014")
        artifacts = "".join(path.read_text(encoding="utf-8") for path in
                            self.root.rglob("*.json") if "manifests" not in path.parts)
        self.assertNotIn(self.key, artifacts)
        self.assertNotIn("gold.json", json.dumps(completed["result"]["requests"]))

    def test_invalid_approval_or_price_prevents_key_read_and_claim(self):
        attempts = []

        def reader():
            attempts.append(True)
            return self.key

        for approval in (replace(self.approval, granted=False),
                         replace(self.approval, endpoint="https://elsewhere.invalid/chat/completions"),
                         replace(self.approval, price_checked_on="2024-01-01")):
            with self.subTest(approval=approval.endpoint):
                with self.assertRaises(LiveEntryError):
                    self.prepare(approval)
        self.assertFalse(list((self.control / "registry").iterdir()))
        with self.assertRaises(LiveEntryError):
            self.run_first(reply(json.dumps(UNKNOWN)), read_key=reader)
        self.assertEqual(attempts, [])

    def test_nonliteral_fake_input_cannot_dispatch_or_read_key(self):
        self.prepare()
        first = self.batch["cells"][0]
        attempts = []

        def reader():
            attempts.append(True)
            return self.key

        with self.assertRaises(LiveEntryError):
            run_next_approved_policy_cell(self.control, dataset_root=DATASET,
                task_id=first["task_id"], repeat=first["repeat"], arm=first["arm"],
                out=first["out"], approval=self.approval, key_reader=reader,
                scripted_replies=(object(),))
        self.assertEqual(attempts, [])
        self.assertFalse(list((self.control / "registry").iterdir()))

    def test_budget_blocks_before_claim_or_wire_dispatch(self):
        self.approval = replace(self.approval, max_usd="0.32")
        self.prepare()
        with self.assertRaises(LiveEntryError) as blocked:
            self.run_first(reply(json.dumps(UNKNOWN)))
        self.assertEqual(blocked.exception.code, "live_budget_exhausted")
        self.assertFalse(list((self.control / "registry").iterdir()))

    def test_transport_unknown_retains_full_reservation_and_stops_batch(self):
        self.prepare()
        completed, _ = self.run_first(TimeoutError())
        self.assertEqual(completed["result"]["requests_attempted"], 1)
        self.assertEqual(completed["result"]["status"], "remote_outcome_unknown")
        self.assertEqual(completed["outcome"]["state"], "batch_halted_unknown")
        self.assertEqual(spend_state(self.control)["state"], "halted_unknown")
        self.assertEqual(spend_state(self.control)["reserved_usd"], "0.330000")
        with self.assertRaises(LiveEntryError):
            self.run_first(reply(json.dumps(UNKNOWN)))

    def test_untrusted_or_missing_usage_halts_with_reservation(self):
        self.prepare()
        bad = reply(json.dumps(UNKNOWN))
        bad.pop("usage")
        completed, _ = self.run_first(bad)
        self.assertEqual(completed["result"]["requests_attempted"], 1)
        self.assertEqual(completed["outcome"]["state"], "batch_halted_unknown")
        self.assertEqual(spend_state(self.control)["reserved_usd"], "0.330000")
        response = completed["result"]["requests"][0]["response"]
        self.assertEqual((response["state"], response["model"],
                          response["system_fingerprint"]),
                         ("received", "deepseek-flash", "fixture-fingerprint"))

    def test_fixture_entry_cannot_mix_into_approved_live_batch(self):
        self.prepare()
        first = self.batch["cells"][0]
        provider = DeepSeekProvider(model="deepseek-flash",
            transport=FakeTransport(reply(json.dumps(UNKNOWN))))
        with self.assertRaises(PolicyBatchError) as blocked:
            run_next_offline_policy_cell(self.control, dataset_root=DATASET,
                task_id=first["task_id"], repeat=first["repeat"], arm=first["arm"],
                out=first["out"], provider=provider)
        self.assertEqual(blocked.exception.code, "batch_live_policy_active")
        self.assertFalse(list((self.control / "registry").iterdir()))

    def test_fake_sequence_covers_four_arms_and_metered_tool_transcript(self):
        self.prepare()
        seen = set()
        requests = 0
        for cell in self.batch["cells"]:
            if len(seen) == 4:
                break
            arm = cell["arm"]
            answer = reply(json.dumps(UNKNOWN))
            if arm == "C":
                answers = (answer, reply(json.dumps({"status": "accept",
                    "coverage": "complete", "reason": "fixture only"})))
            elif arm == "D":
                symbol = self.cases[cell["task_id"]]["entrypoint"]["symbol"]
                answers = (reply(None, calls=[call("read-1", "search", {"query": symbol})]),
                    reply(None, calls=[call("finish-2", "finish",
                        {"diagnosis": UNKNOWN})]))
            else:
                answers = (answer,)
            completed = run_next_approved_policy_cell(self.control, dataset_root=DATASET,
                task_id=cell["task_id"], repeat=cell["repeat"], arm=arm,
                out=cell["out"], approval=self.approval,
                key_reader=lambda: self.key, scripted_replies=answers)
            self.assertEqual(completed["outcome"]["state"], "completed")
            self.assertEqual(completed["result"]["status"], "semantic_pending")
            self.assertEqual(completed["result"]["requests_attempted"], len(answers))
            requests += len(answers)
            seen.add(arm)
        self.assertEqual(seen, {"A", "B", "C", "D"})
        self.assertEqual(spend_state(self.control)["request_count"], requests)


if __name__ == "__main__":
    unittest.main()
