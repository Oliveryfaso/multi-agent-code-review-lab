"""Persistent local USD reservations for a future fake-tested policy pilot."""
import json
from hashlib import sha256
import shutil
import unittest
from types import SimpleNamespace

from debugger_fixtures import fixture_root
from macr.evals.deepseek_policy_batch import (CORE_IMPLEMENTATION_FILES,
    _ticket, _write_new, claim_policy_cell, prepare_policy_batch, record_policy_cell)
from macr.evals.deepseek_policy_manifest import prepare_policy_manifest
from macr.evals.deepseek_policy_spend import (PRICE_SNAPSHOT, PolicySpendError,
    initialize_spend_ledger, mark_spend_unknown, reserve_spend, settle_spend,
    spend_state)
from macr.evals.deepseek_policy_runner import ARM_REQUESTS, run_policy_arm
from macr.providers.deepseek import DeepSeekProvider
from macr.providers.base import ConversationRequest
from macr.evals.deepseek_policy_live_entry import _MeteredDeepSeekProvider, _ScriptedWire
from test_deepseek_policy_runner import FakeTransport, UNKNOWN, call, reply


MODEL = {"model_id": "deepseek-flash", "provider": "deepseek",
         "thinking": True, "reasoning_effort": "high"}


class SpendLedgerTests(unittest.TestCase):
    def setUp(self):
        self.root = fixture_root()
        dataset = self.root / "dataset"
        source = dataset / "snapshots" / "p01"
        source.mkdir(parents=True)
        (source / "logic.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        (dataset / "gold.json").write_text('{"secret":"gold-not-for-model"}', encoding="utf-8")
        (dataset / "blind-review-format.json").write_text('{"rubric":"blind"}', encoding="utf-8")
        case = {"id": "p01", "snapshot_root": "snapshots/p01", "question": "Is ok true?",
                "scenario": "Call ok().", "assumptions": [], "authorized_files": ["logic.py"],
                "initial_evidence_files": ["logic.py"], "stratum": "full_context",
                "evidence_parity": "equal", "entrypoint": {"file": "logic.py", "symbol": "ok"}}
        manifest = self.root / "manifests" / "p01"
        manifest.parent.mkdir()
        prepare_policy_manifest(case, dataset_root=dataset, out=manifest, model=MODEL)
        project = self.root / "project"
        for relative in CORE_IMPLEMENTATION_FILES:
            path = project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# frozen test implementation\n", encoding="utf-8")
        outputs = self.root / "outputs"
        outputs.mkdir()
        self.control = self.root / "control"
        self.batch = prepare_policy_batch([manifest], dataset_root=dataset,
            project_root=project, control_root=self.control, output_root=outputs,
            seed=0, implementation_version="offline-spend-test-v1")
        self.cell = self.batch["cells"][0]
        self.assertEqual(self.cell["arm"], "D")
        claim_policy_cell(self.control, task_id=self.cell["task_id"],
            repeat=self.cell["repeat"], arm=self.cell["arm"], out=self.cell["out"])

    def initialize(self, **options):
        return initialize_spend_ledger(self.control,
            batch_sha256=self.batch["batch_sha256"], **options)

    def reserve(self, decision=1, **options):
        return reserve_spend(self.control, batch_sha256=self.batch["batch_sha256"],
            cell_id=self.cell["cell_id"], decision=decision, max_output_tokens=2000,
            **options)

    def settle(self, usage, decision=1):
        return settle_spend(self.control, batch_sha256=self.batch["batch_sha256"],
            cell_id=self.cell["cell_id"], decision=decision, raw_usage=usage)

    def test_price_contract_and_durable_reservation_then_measured_settlement(self):
        policy = self.initialize()
        self.assertEqual(policy["price_snapshot"], PRICE_SNAPSHOT)
        self.assertEqual(policy["max_requests"], 84)
        self.assertEqual(policy["max_usd"], "3.000000")
        reserved = self.reserve()
        receipt = self.control / "spend" / "entries" / (self.cell["cell_id"] + "-01.reservation.json")
        self.assertEqual(json.loads(receipt.read_text()), reserved)
        self.assertEqual(reserved["reserved_usd"], "0.330000")
        self.assertEqual(spend_state(self.control)["state"], "awaiting_outcome")
        settled = self.settle({"prompt_tokens": 1000, "completion_tokens": 100,
            "total_tokens": 1100, "prompt_cache_hit_tokens": 400,
            "prompt_cache_miss_tokens": 600,
            "completion_tokens_details": {"reasoning_tokens": 25}})
        self.assertEqual(settled["state"], "settled")
        self.assertEqual(settled["charged_usd"], "0.000303")
        self.assertEqual(settled["usage"]["prompt_cache_hit_tokens"], 400)
        state = spend_state(self.control)
        self.assertEqual((state["state"], state["spent_usd"], state["reserved_usd"]),
                         ("open", "0.000303", "0.000000"))
        self.assertEqual(self.reserve(2)["decision"], 2)

    def test_unknown_transport_keeps_full_reservation_and_halts_all_later_requests(self):
        self.initialize()
        self.reserve()
        outcome = mark_spend_unknown(self.control, batch_sha256=self.batch["batch_sha256"],
            cell_id=self.cell["cell_id"], decision=1)
        self.assertEqual(outcome["state"], "unknown")
        state = spend_state(self.control)
        self.assertEqual((state["state"], state["reserved_usd"]), ("halted_unknown", "0.330000"))
        with self.assertRaises(PolicySpendError) as blocked:
            self.reserve(2)
        self.assertEqual(blocked.exception.code, "spend_halted_unknown")
        with self.assertRaises(PolicySpendError):
            self.settle({"prompt_tokens": 10, "completion_tokens": 10})

    def test_missing_usage_keeps_reservation_and_stops(self):
        self.initialize()
        self.reserve()
        outcome = self.settle({})
        self.assertEqual(outcome["state"], "usage_unknown")
        self.assertEqual(spend_state(self.control)["reserved_usd"], "0.330000")
        with self.assertRaises(PolicySpendError):
            self.reserve(2)

    def test_inconsistent_cache_split_keeps_reservation_and_stops(self):
        self.initialize()
        self.reserve()
        outcome = self.settle({"prompt_tokens": 10, "completion_tokens": 2,
                               "prompt_cache_hit_tokens": 4,
                               "prompt_cache_miss_tokens": 7})
        self.assertEqual(outcome["state"], "usage_unknown")
        self.assertEqual(spend_state(self.control)["state"], "halted_unknown")

    def test_missing_cache_split_is_charged_as_all_miss(self):
        self.initialize()
        self.reserve()
        result = self.settle({"prompt_tokens": 1000, "completion_tokens": 100})
        self.assertEqual(result["charged_usd"], "0.000420")

    def test_usage_above_reserved_output_cap_halts_instead_of_undercharging(self):
        self.initialize()
        self.reserve()
        result = self.settle({"prompt_tokens": 1, "completion_tokens": 2001})
        self.assertEqual(result["state"], "usage_unknown")
        self.assertEqual(spend_state(self.control)["reserved_usd"], "0.330000")

    def test_zero_usage_is_not_accepted_as_a_free_remote_request(self):
        self.initialize()
        self.reserve()
        result = self.settle({"prompt_tokens": 0, "completion_tokens": 0})
        self.assertEqual(result["state"], "usage_unknown")

    def test_persisted_usage_recomputes_charge_and_does_not_copy_extra_fields(self):
        self.initialize()
        self.reserve()
        self.settle({"prompt_tokens": 1000, "completion_tokens": 100,
                     "untrusted_extra": "secret-sentinel-do-not-persist"})
        path = self.control / "spend" / "entries" / (
            self.cell["cell_id"] + "-01.outcome.json")
        self.assertNotIn("secret-sentinel", path.read_text())
        row = json.loads(path.read_text())
        self.assertEqual(row["usage"]["cache_basis"], "all_miss")
        row["charged_usd"] = "0.000000"
        path.write_text(json.dumps(row), encoding="utf-8")
        with self.assertRaises(PolicySpendError) as drift:
            spend_state(self.control)
        self.assertEqual(drift.exception.code, "spend_entries_invalid")
        row["charged_usd"] = "0.000420"
        row["state"] = ["settled"]
        path.write_text(json.dumps(row), encoding="utf-8")
        with self.assertRaises(PolicySpendError) as malformed:
            spend_state(self.control)
        self.assertEqual(malformed.exception.code, "spend_entries_invalid")

    def test_budget_rejects_before_recording(self):
        self.initialize(max_usd="0.32", max_requests=1)
        with self.assertRaises(PolicySpendError) as budget:
            self.reserve()
        self.assertEqual(budget.exception.code, "spend_budget_exhausted")
        self.assertFalse(list((self.control / "spend" / "entries").iterdir()))

    def test_pilot_usd_ceiling_cannot_be_raised_locally(self):
        with self.assertRaises(PolicySpendError) as ceiling:
            self.initialize(max_usd="3.01")
        self.assertEqual(ceiling.exception.code, "spend_budget_invalid")

    def test_request_ceiling_rejects_after_first_settlement(self):
        self.initialize(max_requests=1)
        self.reserve()
        self.settle({"prompt_tokens": 1, "completion_tokens": 1})
        with self.assertRaises(PolicySpendError) as ceiling:
            self.reserve(2)
        self.assertEqual(ceiling.exception.code, "spend_request_ceiling")

    def test_duplicate_and_pending_request_are_blocked(self):
        self.initialize()
        self.reserve()
        with self.assertRaises(PolicySpendError) as duplicate:
            self.reserve()
        self.assertEqual(duplicate.exception.code, "spend_duplicate_request")
        with self.assertRaises(PolicySpendError) as pending:
            self.reserve(2)
        self.assertEqual(pending.exception.code, "spend_request_pending")

    def test_output_cap_rejected_before_recording(self):
        self.initialize()
        with self.assertRaises(PolicySpendError) as cap:
            reserve_spend(self.control, batch_sha256=self.batch["batch_sha256"],
                          cell_id=self.cell["cell_id"], decision=1,
                          max_output_tokens=6001)
        self.assertEqual(cap.exception.code, "spend_output_cap")

    def test_wrong_batch_or_price_or_unclaimed_cell_is_refused(self):
        with self.assertRaises(PolicySpendError) as price:
            self.initialize(price_snapshot=PRICE_SNAPSHOT | {"checked_date": "2025-01-01"})
        self.assertEqual(price.exception.code, "spend_price_snapshot_invalid")
        self.initialize()
        with self.assertRaises(PolicySpendError) as wrong:
            reserve_spend(self.control, batch_sha256="0" * 64,
                cell_id=self.cell["cell_id"], decision=1, max_output_tokens=2000)
        self.assertEqual(wrong.exception.code, "spend_batch_mismatch")
        with self.assertRaises(PolicySpendError) as unclaimed:
            reserve_spend(self.control, batch_sha256=self.batch["batch_sha256"],
                cell_id="f" * 32, decision=1, max_output_tokens=2000)
        self.assertEqual(unclaimed.exception.code, "spend_cell_unclaimed")


class CarryForwardTests(unittest.TestCase):
    def setUp(self):
        self.root = fixture_root()
        self.previous, self.old_batch = self.make_batch("previous")
        cell = self.old_batch["cells"][0]
        ticket = claim_policy_cell(self.previous, task_id=cell["task_id"],
            repeat=cell["repeat"], arm=cell["arm"], out=cell["out"])
        initialize_spend_ledger(self.previous, batch_sha256=self.old_batch["batch_sha256"])
        reserve_spend(self.previous, batch_sha256=self.old_batch["batch_sha256"],
            cell_id=cell["cell_id"], decision=1, max_output_tokens=2000)
        response = reply("{}")
        response["usage"] = {"prompt_tokens": 357, "completion_tokens": 1616,
                             "prompt_cache_hit_tokens": 0,
                             "prompt_cache_miss_tokens": 357}
        run_policy_arm(cell["manifest_dir"], dataset_root=self.root/"previous"/"dataset",
            arm=cell["arm"], provider=DeepSeekProvider(model="deepseek-flash",
                transport=FakeTransport(response)), out=cell["out"])
        settle_spend(self.previous, batch_sha256=self.old_batch["batch_sha256"],
            cell_id=cell["cell_id"], decision=1, raw_usage=response["usage"])
        self.assertEqual(record_policy_cell(self.previous, ticket)["state"],
                         "batch_halted_unknown")
        self.before = self.old_files()
        self.current, self.batch = self.make_batch("current")
        self.cell = self.batch["cells"][0]
        claim_policy_cell(self.current, task_id=self.cell["task_id"],
            repeat=self.cell["repeat"], arm=self.cell["arm"], out=self.cell["out"])

    def make_batch(self, name, count=1, seed=0):
        base = self.root / name
        dataset, project = base / "dataset", base / "project"
        dataset.mkdir(parents=True)
        (dataset / "gold.json").write_text('{}', encoding="utf-8")
        (dataset / "blind-review-format.json").write_text('{}', encoding="utf-8")
        manifests = base / "manifests"
        manifests.mkdir()
        directories = []
        for number in range(1, count + 1):
            case_id = f"p{number:02d}"
            source = dataset / "snapshots" / case_id
            source.mkdir(parents=True)
            (source / "logic.py").write_text("def ok():\n    return True\n", encoding="utf-8")
            case = {"id": case_id, "snapshot_root": "snapshots/"+case_id,
                "question": "Is ok true?", "scenario": "Call ok().", "assumptions": [],
                "authorized_files": ["logic.py"], "initial_evidence_files": ["logic.py"],
                "stratum": "full_context", "evidence_parity": "equal",
                "entrypoint": {"file": "logic.py", "symbol": "ok"}}
            directory = manifests / case_id
            prepare_policy_manifest(case, dataset_root=dataset, out=directory, model=MODEL)
            directories.append(directory)
        for relative in CORE_IMPLEMENTATION_FILES:
            path = project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# frozen fixture\n", encoding="utf-8")
        outputs = base / "outputs"
        outputs.mkdir(mode=0o700)
        control = base / "control"
        batch = prepare_policy_batch(directories, dataset_root=dataset, project_root=project,
            control_root=control, output_root=outputs, seed=seed, implementation_version="carry-test")
        return control, batch

    def old_files(self):
        return {str(path.relative_to(self.previous.parent)): sha256(path.read_bytes()).hexdigest()
                for path in self.previous.parent.rglob('*') if path.is_file()}

    def initialize(self, **options):
        return initialize_spend_ledger(self.current, batch_sha256=self.batch["batch_sha256"],
            predecessor_control_root=self.previous,
            predecessor_batch_sha256=self.old_batch["batch_sha256"], **options)

    def reserve(self, decision=1):
        return reserve_spend(self.current, batch_sha256=self.batch["batch_sha256"],
            cell_id=self.cell["cell_id"], decision=decision, max_output_tokens=2000)

    def test_carry_is_derived_persistent_and_old_files_unchanged(self):
        policy = self.initialize()
        self.assertEqual(policy["carry_forward"]["spent_usd"], "0.002047")
        state = spend_state(self.current)
        self.assertEqual((state["request_count"], state["spent_usd"],
                          state["carried_request_count"], state["batch_request_count"],
                          state["carried_batch_sha256"]),
                         (1, "0.002047", 1, 0, self.old_batch["batch_sha256"]))
        self.reserve()
        reopened = spend_state(str(self.current))
        self.assertEqual((reopened["request_count"], reopened["reserved_usd"]),
                         (2, "0.330000"))
        with self.assertRaises(PolicySpendError) as pending:
            self.reserve(2)
        self.assertEqual(pending.exception.code, "spend_request_pending")
        self.assertEqual(self.before, self.old_files())

    def test_combined_usd_exact_reservation_boundary(self):
        self.initialize(max_usd="0.332047")
        self.reserve()
        settle_spend(self.current, batch_sha256=self.batch["batch_sha256"],
            cell_id=self.cell["cell_id"], decision=1,
            raw_usage={"prompt_tokens": 1, "completion_tokens": 1})
        self.assertEqual(spend_state(self.current)["spent_usd"], "0.002049")
        with self.assertRaises(PolicySpendError) as budget:
            self.reserve(2)
        self.assertEqual(budget.exception.code, "spend_budget_exhausted")

    def test_carry_and_actual_metered_fake_wire_settle_together(self):
        self.initialize()
        scope = SimpleNamespace(batch_sha256=self.batch["batch_sha256"],
            model_id="deepseek-flash", endpoint="https://api.deepseek.com/chat/completions")
        provider = _MeteredDeepSeekProvider(control_root=self.current, scope=scope,
            cell_id=self.cell["cell_id"], arm=self.cell["arm"], simulated=True,
            inner_transport=_ScriptedWire((reply(json.dumps(UNKNOWN)),)))
        provider.converse(ConversationRequest(messages=[{"role": "user", "content": "fixture"}],
            thinking={"type": "enabled"}, reasoning_effort="high",
            max_input_tokens=8000,
            max_output_tokens=6000 // ARM_REQUESTS[self.cell["arm"]]))
        state = spend_state(self.current)
        self.assertEqual((state["request_count"], state["batch_request_count"],
                          state["carried_request_count"], state["spent_usd"],
                          state["reserved_usd"]), (2, 1, 1, "0.002061", "0.000000"))
        self.assertEqual(provider.transport.live_attempts, 0)

    def test_combined_84_requests_rejects_85th_before_recording(self):
        self.current, self.batch = self.make_batch("many", count=12)
        self.initialize()
        attempted = 0
        for cell in self.batch["cells"]:
            _write_new(self.current/"registry"/(cell["cell_id"]+".claim.json"),
                       _ticket(self.batch, cell), 4096)
            for decision in range(1, ARM_REQUESTS[cell["arm"]] + 1):
                kwargs = dict(batch_sha256=self.batch["batch_sha256"],
                    cell_id=cell["cell_id"], decision=decision)
                if attempted == 83:
                    with self.assertRaises(PolicySpendError) as ceiling:
                        reserve_spend(self.current, max_output_tokens=2000, **kwargs)
                    self.assertEqual(ceiling.exception.code, "spend_request_ceiling")
                    self.assertEqual(spend_state(self.current)["request_count"], 84)
                    return
                reserve_spend(self.current, max_output_tokens=2000, **kwargs)
                settle_spend(self.current, raw_usage={"prompt_tokens": 1,
                    "completion_tokens": 1}, **kwargs)
                attempted += 1
        self.fail("fixture did not reach the global boundary")

    def test_old_evidence_drift_or_deleted_request_pair_blocks_read(self):
        self.initialize()
        paths = sorted((self.previous/"spend"/"entries").iterdir())
        original = {path: path.read_bytes() for path in paths}
        for path in paths:
            path.unlink()
        with self.assertRaises(PolicySpendError):
            spend_state(self.current)
        for path, data in original.items():
            path.write_bytes(data)
        policy_path = self.previous/"spend"/"policy.json"
        policy_path.write_text(policy_path.read_text()+" ", encoding="utf-8")
        with self.assertRaises(PolicySpendError):
            self.reserve()

    def test_pending_unknown_unhalted_and_self_predecessor_refused(self):
        previous_outcome = next((self.previous/"spend"/"entries").glob("*.outcome.json"))
        original = previous_outcome.read_bytes()
        previous_outcome.unlink()
        with self.assertRaises(PolicySpendError):
            self.initialize()
        previous_outcome.write_bytes(original)
        row = json.loads(original)
        row.update(state="unknown", charged_usd="0.000000", usage=None)
        previous_outcome.write_text(json.dumps(row), encoding="utf-8")
        with self.assertRaises(PolicySpendError):
            self.initialize()
        previous_outcome.write_bytes(original)
        with self.assertRaises(PolicySpendError):
            initialize_spend_ledger(self.current, batch_sha256=self.batch["batch_sha256"],
                predecessor_control_root=self.current,
                predecessor_batch_sha256=self.batch["batch_sha256"])

    def test_second_successor_and_carry_chain_are_refused(self):
        self.initialize()
        other, batch = self.make_batch("other")
        with self.assertRaises(PolicySpendError):
            initialize_spend_ledger(other, batch_sha256=batch["batch_sha256"],
                predecessor_control_root=self.previous,
                predecessor_batch_sha256=self.old_batch["batch_sha256"])
        with self.assertRaises(PolicySpendError):
            initialize_spend_ledger(other, batch_sha256=batch["batch_sha256"],
                predecessor_control_root=self.current,
                predecessor_batch_sha256=self.batch["batch_sha256"])

    def test_successor_lock_drift_blocks_every_read(self):
        self.initialize()
        lock = self.root/"cumulative-successor-lock.json"
        row = json.loads(lock.read_text())
        row["successor_control_root"] = str(self.root/"another"/"control")
        lock.write_text(json.dumps(row), encoding="utf-8")
        with self.assertRaises(PolicySpendError):
            spend_state(self.current)

    def test_copied_old_control_cannot_move_the_authorization_lock_root(self):
        copied = self.root/"elsewhere"/"previous"/"control"
        shutil.copytree(self.previous, copied)
        with self.assertRaises(PolicySpendError):
            initialize_spend_ledger(self.current, batch_sha256=self.batch["batch_sha256"],
                predecessor_control_root=copied,
                predecessor_batch_sha256=self.old_batch["batch_sha256"])
        self.assertFalse((copied.parent.parent/"cumulative-successor-lock.json").exists())

    def test_old_ledger_usage_must_match_the_verified_provider_response(self):
        path = next((self.previous/"spend"/"entries").glob("*.outcome.json"))
        row = json.loads(path.read_text())
        row["usage"]["completion_tokens"] = 1615
        row["charged_usd"] = "0.002046"
        path.write_text(json.dumps(row), encoding="utf-8")
        with self.assertRaises(PolicySpendError) as blocked:
            self.initialize()
        self.assertEqual(blocked.exception.code, "spend_predecessor_usage_mismatch")

    def test_legitimately_completed_predecessor_is_not_a_stopped_batch(self):
        control, batch = self.make_batch("completed")
        cell = batch["cells"][0]
        ticket = claim_policy_cell(control, task_id=cell["task_id"],
            repeat=cell["repeat"], arm=cell["arm"], out=cell["out"])
        initialize_spend_ledger(control, batch_sha256=batch["batch_sha256"])
        reserve_spend(control, batch_sha256=batch["batch_sha256"],
            cell_id=cell["cell_id"], decision=1, max_output_tokens=2000)
        response = reply(None, calls=[call("finish-1", "finish", {"diagnosis": UNKNOWN})])
        run_policy_arm(cell["manifest_dir"], dataset_root=control.parent/"dataset",
            arm=cell["arm"], provider=DeepSeekProvider(model="deepseek-flash",
                transport=FakeTransport(response)), out=cell["out"])
        settle_spend(control, batch_sha256=batch["batch_sha256"],
            cell_id=cell["cell_id"], decision=1, raw_usage=response["usage"])
        self.assertEqual(record_policy_cell(control, ticket)["state"], "completed")
        with self.assertRaises(PolicySpendError) as blocked:
            initialize_spend_ledger(self.current, batch_sha256=self.batch["batch_sha256"],
                predecessor_control_root=control, predecessor_batch_sha256=batch["batch_sha256"])
        self.assertEqual(blocked.exception.code, "spend_predecessor_not_stopped")


if __name__ == "__main__":
    unittest.main()
