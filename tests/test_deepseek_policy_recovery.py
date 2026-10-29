"""Conservative retained-unknown recovery using synthetic receipts only."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from decimal import Decimal
import shutil
from types import SimpleNamespace
import unittest

from debugger_fixtures import fixture_root
from macr.evals.deepseek_policy_batch import (_digest, _ticket, _write_new,
    claim_policy_cell, record_policy_cell, prepare_policy_batch)
from macr.evals.deepseek_policy_recovery import (RecoveryError,
    _identity, _logical, build_recovery_plan, claim_recovery, validate_recovery_plan)
from macr.evals.deepseek_policy_manifest import prepare_policy_manifest
from macr.evals.deepseek_policy_runner import ARM_REQUESTS, run_policy_arm
from macr.evals.deepseek_policy_spend import (PolicySpendError,
    initialize_spend_ledger, mark_spend_unknown, reserve_spend, settle_spend,
    spend_state)
from macr.providers.deepseek import DeepSeekProvider
from test_deepseek_policy_runner import FakeTransport, UNKNOWN, call, reply
import test_deepseek_policy_spend as spend_fixtures


def _run_fixture_cell(control, batch, cell, answers, *, unknown=False):
    ticket = claim_policy_cell(control, task_id=cell["task_id"], repeat=cell["repeat"],
                               arm=cell["arm"], out=cell["out"])
    for decision, answer in enumerate(answers, 1):
        reserve_spend(control, batch_sha256=batch["batch_sha256"],
            cell_id=cell["cell_id"], decision=decision,
            max_output_tokens=6000 // ARM_REQUESTS[cell["arm"]])
        if unknown:
            mark_spend_unknown(control, batch_sha256=batch["batch_sha256"],
                cell_id=cell["cell_id"], decision=decision)
        else:
            settle_spend(control, batch_sha256=batch["batch_sha256"],
                cell_id=cell["cell_id"], decision=decision, raw_usage=answer["usage"])
    run_policy_arm(cell["manifest_dir"], dataset_root=batch["dataset_root"],
        arm=cell["arm"], provider=DeepSeekProvider(model="deepseek-flash",
            transport=FakeTransport(*answers)), out=cell["out"])
    return record_policy_cell(control, ticket)


def make_recovery_source(root, *, dataset_root=None, project_root=None, ancestor_requests=1):
    """Return a fully durable 5-request stopped lineage for integration tests.

    Optional roots let live-scope tests reuse the public 18-file synthetic set.
    All model responses remain literal fixtures, with zero live dispatch.
    """
    factory = spend_fixtures.CarryForwardTests(methodName="runTest")
    factory.root = root

    def make_batch(name, seed=41):
        if dataset_root is None:
            return factory.make_batch(name, count=12, seed=seed)
        dataset = Path(dataset_root)
        base = root / name
        manifests = base / "manifests"
        manifests.mkdir(parents=True, mode=0o700)
        directories = []
        for case in json.loads((dataset / "cases.json").read_text())["cases"]:
            directory = manifests / case["id"]
            prepare_policy_manifest(case, dataset_root=dataset, out=directory,
                                    model=spend_fixtures.MODEL)
            directories.append(directory)
        outputs = base / "outputs"
        outputs.mkdir(mode=0o700)
        control = base / "control"
        batch = prepare_policy_batch(directories, dataset_root=dataset,
            project_root=project_root, control_root=control, output_root=outputs,
            seed=seed, implementation_version="synthetic-recovery-source")
        return control, batch

    assert ancestor_requests in (1, 3)
    previous, old_batch = make_batch("previous", seed=41 if ancestor_requests == 1 else 38)
    initialize_spend_ledger(previous, batch_sha256=old_batch["batch_sha256"])
    invalid = reply("{}")
    invalid["usage"] = {"prompt_tokens": 357, "completion_tokens": 1616,
                        "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 357}
    if ancestor_requests == 1:
        answers = (invalid,)
    else:
        answers = (reply(None, calls=[call("search-1", "search", {"query": "ok"})]),
                   reply(None, calls=[call("search-2", "search", {"query": "return"})]),
                   reply(None, calls=[call("bad-finish", "finish", {"diagnosis": {"verdict": "bad"}})]))
    _run_fixture_cell(previous, old_batch, old_batch["cells"][0], answers)
    current, batch = make_batch("current")
    initialize_spend_ledger(current, batch_sha256=batch["batch_sha256"],
        predecessor_control_root=previous, predecessor_batch_sha256=old_batch["batch_sha256"])
    assert [cell["arm"] for cell in batch["cells"][:3]] == ["A", "C", "D"]
    _run_fixture_cell(current, batch, batch["cells"][0], (reply(json.dumps(UNKNOWN)),))
    _run_fixture_cell(current, batch, batch["cells"][1],
        (reply(json.dumps(UNKNOWN)), reply('{"status":"accept","coverage":"complete","reason":"Fixture"}')))
    invalid = reply(None)
    del invalid["choices"][0]["message"]["content"]
    invalid["usage"] = None
    _run_fixture_cell(current, batch, batch["cells"][2], (invalid,), unknown=True)
    return SimpleNamespace(root=root, previous=previous, old_batch=old_batch,
        current=current, batch=batch, dataset=Path(batch["dataset_root"]),
        project=Path(batch["project_root"]))


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        fixture = make_recovery_source(fixture_root())
        self.__dict__.update(fixture.__dict__)
        self.before = self.source_files()
        self.target = self.root / "recovered"
        self.plan = build_recovery_plan(self.current,
            predecessor_batch_sha256=self.batch["batch_sha256"],
            target_control_root=self.target / "control",
            target_output_root=self.target / "outputs", recovery_version="recovery-test-v1")

    def source_files(self):
        return {str(path): sha256(path.read_bytes()).hexdigest()
                for base in (self.previous.parent, self.current.parent)
                for path in base.rglob("*") if path.is_file()}

    def confirmation(self, batch_sha256):
        return {"schema": "macr.deepseek.policy.recovery.confirmation.v1", "granted": True,
            "approval_ref": "synthetic-human-confirmation", "plan_sha256": self.plan["plan_sha256"],
            "batch_sha256": batch_sha256, "action": "retain_unknown_and_continue_unattempted",
            "max_requests": 84, "max_usd": "3.000000"}

    def recovered(self):
        self.target.mkdir(mode=0o700)
        outputs = self.target / "outputs"
        outputs.mkdir(mode=0o700)
        batch = prepare_policy_batch([row["manifest_dir"] for row in self.batch["manifests"]],
            dataset_root=self.dataset, project_root=self.project,
            control_root=self.target / "control", output_root=outputs, seed=0,
            implementation_version=self.plan["recovery_version"], recovery_plan=self.plan)
        return self.target / "control", batch

    def test_preview_carries_both_generations_without_changing_unknown_or_old_bytes(self):
        self.assertEqual(self.plan["opening"], {"request_count": 5,
            "spent_usd": "0.002089", "retained_unknown_usd": "0.330000"})
        self.assertEqual((len(self.plan["eligible_cells"]), self.plan["max_new_requests"]), (45, 78))
        self.assertEqual(len(self.plan["excluded_cells"]), 3)
        self.assertEqual(validate_recovery_plan(self.plan), self.plan)
        self.assertEqual(spend_state(self.current)["state"], "halted_unknown")
        self.assertEqual(self.before, self.source_files())
        self.assertFalse((self.root / "cumulative-recovery-lock.json").exists())

    def test_missing_or_mismatched_confirmation_refuses_before_ledger_or_lock(self):
        control, batch = self.recovered()
        with self.assertRaises(PolicySpendError) as ordinary:
            initialize_spend_ledger(control, batch_sha256=batch["batch_sha256"])
        self.assertEqual(ordinary.exception.code, "spend_recovery_missing")
        for confirmation in (None, self.confirmation("0" * 64),
                             self.confirmation(batch["batch_sha256"]) | {"granted": False}):
            with self.assertRaises((RecoveryError, PolicySpendError)):
                initialize_spend_ledger(control, batch_sha256=batch["batch_sha256"],
                    recovery_plan=self.plan, recovery_confirmation=confirmation)
            self.assertFalse((control / "spend").exists())
            self.assertFalse((self.root / "cumulative-recovery-lock.json").exists())

    def test_retained_hold_is_not_spend_and_a_new_unknown_still_halts(self):
        control, batch = self.recovered()
        initialize_spend_ledger(control, batch_sha256=batch["batch_sha256"],
            recovery_plan=self.plan, recovery_confirmation=self.confirmation(batch["batch_sha256"]))
        state = spend_state(control)
        self.assertEqual((state["state"], state["request_count"], state["spent_usd"],
                          state["reserved_usd"], state["retained_unknown_usd"]),
                         ("open", 5, "0.002089", "0.330000", "0.330000"))
        cell = batch["cells"][0]
        claim_policy_cell(control, task_id=cell["task_id"], repeat=1, arm=cell["arm"], out=cell["out"])
        reserve_spend(control, batch_sha256=batch["batch_sha256"],
            cell_id=cell["cell_id"], decision=1, max_output_tokens=2000)
        mark_spend_unknown(control, batch_sha256=batch["batch_sha256"], cell_id=cell["cell_id"], decision=1)
        state = spend_state(control)
        self.assertEqual((state["state"], state["request_count"], state["reserved_usd"],
                          state["retained_unknown_usd"]), ("halted_unknown", 6, "0.660000", "0.330000"))
        self.assertEqual(self.before, self.source_files())

    def initialized(self):
        control, batch = self.recovered()
        initialize_spend_ledger(control, batch_sha256=batch["batch_sha256"],
            recovery_plan=self.plan, recovery_confirmation=self.confirmation(batch["batch_sha256"]))
        return control, batch

    def ledger_requests(self, control, batch):
        """Exercise ledger accounting without any transport or model runner."""
        for cell in batch["cells"]:
            _write_new(control / "registry" / (cell["cell_id"] + ".claim.json"),
                       _ticket(batch, cell), 4096)
            for decision in range(1, ARM_REQUESTS[cell["arm"]] + 1):
                yield dict(batch_sha256=batch["batch_sha256"], cell_id=cell["cell_id"],
                           decision=decision)

    def test_all_78_new_requests_retain_hold_and_finish_with_83_cumulative_requests(self):
        control, batch = self.initialized()
        requests = list(self.ledger_requests(control, batch))
        self.assertEqual(len(requests), 78)
        old_ids = {cell["cell_id"] for cell in self.batch["cells"]}
        self.assertTrue(all(cell["cell_id"] not in old_ids for cell in batch["cells"]))
        for args in requests:
            reserve_spend(control, max_output_tokens=2000, **args)
            settle_spend(control, raw_usage={"prompt_tokens": 1, "completion_tokens": 1}, **args)
        state = spend_state(control)
        self.assertEqual((state["state"], state["request_count"], state["batch_request_count"],
                          state["carried_request_count"], state["reserved_usd"]),
                         ("open", 83, 78, 5, "0.330000"))
        with self.assertRaises(PolicySpendError) as replay:
            reserve_spend(control, max_output_tokens=2000, **requests[-1])
        self.assertEqual(replay.exception.code, "spend_duplicate_request")
        self.assertEqual(self.before, self.source_files())

    def test_retained_unknown_is_included_in_every_usd_reservation_after_success(self):
        control, batch = self.initialized()
        requests = self.ledger_requests(control, batch)
        for _ in range(8):
            args = next(requests)
            reserve_spend(control, max_output_tokens=2000, **args)
            settle_spend(control, raw_usage={"prompt_tokens": 990000, "completion_tokens": 1}, **args)
        state = spend_state(control)
        self.assertEqual((state["spent_usd"], state["retained_unknown_usd"]),
                         ("2.378105", "0.330000"))
        self.assertLessEqual(Decimal(state["spent_usd"]) + Decimal(state["reserved_usd"]), Decimal("3"))
        self.assertGreater(Decimal(state["spent_usd"]) + Decimal(state["reserved_usd"]) + Decimal("0.33"), Decimal("3"))
        args = next(requests)
        with self.assertRaises(PolicySpendError) as ceiling:
            reserve_spend(control, max_output_tokens=2000, **args)
        self.assertEqual(ceiling.exception.code, "spend_budget_exhausted")
        self.assertEqual(spend_state(control)["request_count"], 13)

    def test_global_84_ceiling_counts_a_larger_settled_ancestor_and_retained_unknown(self):
        fixture = make_recovery_source(fixture_root(), ancestor_requests=3)
        self.__dict__.update(fixture.__dict__)
        self.target = self.root / "recovered"
        self.plan = build_recovery_plan(self.current,
            predecessor_batch_sha256=self.batch["batch_sha256"],
            target_control_root=self.target / "control", target_output_root=self.target / "outputs",
            recovery_version="request-ceiling-test")
        self.assertEqual(self.plan["opening"]["request_count"], 7)
        control, batch = self.initialized()
        requests = self.ledger_requests(control, batch)
        for _ in range(77):
            args = next(requests)
            reserve_spend(control, max_output_tokens=2000, **args)
            settle_spend(control, raw_usage={"prompt_tokens": 1, "completion_tokens": 1}, **args)
        self.assertEqual(spend_state(control)["request_count"], 84)
        with self.assertRaises(PolicySpendError) as ceiling:
            reserve_spend(control, max_output_tokens=2000, **next(requests))
        self.assertEqual(ceiling.exception.code, "spend_request_ceiling")
        self.assertEqual(spend_state(control)["batch_request_count"], 77)

    def test_confirmation_cannot_be_used_twice_or_copied_to_reset_budget(self):
        control, batch = self.recovered()
        initialize_spend_ledger(control, batch_sha256=batch["batch_sha256"],
            recovery_plan=self.plan, recovery_confirmation=self.confirmation(batch["batch_sha256"]))
        with self.assertRaises(PolicySpendError):
            initialize_spend_ledger(control, batch_sha256=batch["batch_sha256"],
                recovery_plan=self.plan, recovery_confirmation=self.confirmation(batch["batch_sha256"]))
        copied = self.root / "copied" / "control"
        shutil.copytree(control, copied)
        with self.assertRaises((RecoveryError, PolicySpendError)):
            spend_state(copied)
        source_copy = self.root / "elsewhere" / "current" / "control"
        shutil.copytree(self.current, source_copy)
        with self.assertRaises(RecoveryError):
            build_recovery_plan(source_copy, predecessor_batch_sha256=self.batch["batch_sha256"],
                target_control_root=self.root / "other" / "control",
                target_output_root=self.root / "other" / "outputs", recovery_version="copy")

    def test_crash_after_confirmation_consumption_blocks_same_and_different_target(self):
        control, batch = self.recovered()
        claim_recovery(control, batch, self.plan, self.confirmation(batch["batch_sha256"]))
        with self.assertRaises(PolicySpendError) as crash:
            initialize_spend_ledger(control, batch_sha256=batch["batch_sha256"],
                recovery_plan=self.plan, recovery_confirmation=self.confirmation(batch["batch_sha256"]))
        self.assertEqual(crash.exception.code, "recovery_already_claimed")
        self.assertFalse((control / "spend").exists())
        self.target = self.root / "different-target"
        self.plan = build_recovery_plan(self.current,
            predecessor_batch_sha256=self.batch["batch_sha256"],
            target_control_root=self.target / "control", target_output_root=self.target / "outputs",
            recovery_version="different-target")
        control, batch = self.recovered()
        with self.assertRaises(PolicySpendError) as duplicate:
            initialize_spend_ledger(control, batch_sha256=batch["batch_sha256"],
                recovery_plan=self.plan, recovery_confirmation=self.confirmation(batch["batch_sha256"]))
        self.assertEqual(duplicate.exception.code, "recovery_already_claimed")
        self.assertFalse((control / "spend").exists())

    def test_source_drift_and_subset_injection_are_rejected(self):
        changed = deepcopy(self.plan)
        changed["eligible_cells"].append(changed["excluded_cells"][0])
        changed["plan_sha256"] = _digest({key: value for key, value in changed.items()
                                         if key != "plan_sha256"})
        with self.assertRaises(RecoveryError):
            validate_recovery_plan(changed)
        path = self.current / "spend" / "policy.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaises(RecoveryError):
            validate_recovery_plan(self.plan)

    def test_new_preview_rejects_a_rehashed_source_manifest_from_after_the_stopped_batch(self):
        path = Path(self.batch["manifests"][0]["manifest_dir"]) / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["task"]["question"] += " A later, different question."
        manifest["manifest_sha256"] = _digest({key: value for key, value in manifest.items()
                                             if key != "manifest_sha256"})
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(RecoveryError):
            build_recovery_plan(self.current, predecessor_batch_sha256=self.batch["batch_sha256"],
                target_control_root=self.target / "control", target_output_root=self.target / "outputs",
                recovery_version="altered-source")


class SecondRecoveryTests(unittest.TestCase):
    def setUp(self):
        first = RecoveryTests(methodName='runTest')
        first.setUp()
        self.factory = first
        self.root = first.root
        self.source, self.source_batch = first.initialized()
        self.failed = self.source_batch['cells'][0]
        _run_fixture_cell(self.source, self.source_batch, self.failed,
                          (TimeoutError(),), unknown=True)
        self.target = self.root / 'second-recovery'

    def preview(self, target=None):
        target = target or self.target
        return build_recovery_plan(self.source,
            predecessor_batch_sha256=self.source_batch['batch_sha256'],
            target_control_root=target / 'control', target_output_root=target / 'outputs',
            recovery_version='synthetic-recovery-v2')

    def confirmation(self, plan, batch):
        return {'schema': 'macr.deepseek.policy.recovery.confirmation.v2',
            'granted': True, 'approval_ref': 'second-human-confirmation',
            'plan_sha256': plan['plan_sha256'], 'batch_sha256': batch['batch_sha256'],
            'action': 'retain_unknown_and_continue_unattempted',
            'max_requests': 84, 'max_usd': '3.000000'}

    def prepared(self, target=None):
        target = target or self.target
        plan = self.preview(target)
        target.mkdir(mode=0o700)
        outputs = target / 'outputs'
        outputs.mkdir(mode=0o700)
        batch = prepare_policy_batch(
            [row['manifest_dir'] for row in self.source_batch['manifests']],
            dataset_root=self.factory.dataset, project_root=self.factory.project,
            control_root=target / 'control', output_root=outputs, seed=41,
            implementation_version=plan['recovery_version'], recovery_plan=plan)
        return target / 'control', batch, plan

    def initialized(self):
        control, batch, plan = self.prepared()
        initialize_spend_ledger(control, batch_sha256=batch['batch_sha256'],
            recovery_plan=plan, recovery_confirmation=self.confirmation(plan, batch))
        return control, batch, plan

    def frozen(self):
        return {str(path): sha256(path.read_bytes()).hexdigest()
            for base in (self.factory.previous.parent, self.factory.current.parent,
                         self.source.parent)
            for path in base.rglob('*.json')} | {
            'first-lock': sha256((self.root / 'cumulative-recovery-lock.json').read_bytes()).hexdigest()}

    def test_second_preview_preserves_six_requests_two_holds_and_logical_partition(self):
        before = self.frozen()
        plan = self.preview()
        self.assertEqual(plan['schema'], 'macr.deepseek.policy.recovery.v2')
        self.assertEqual(plan['opening'], {'request_count': 6, 'spent_usd': '0.002089',
                                         'retained_unknown_usd': '0.660000'})
        self.assertEqual(len(plan['eligible_cells']), 44)
        self.assertEqual(len(plan['excluded_cells']), 4)
        self.assertEqual(plan['max_new_requests'], 78 - ARM_REQUESTS[self.failed['arm']])
        eligible = {_identity(row) for row in plan['eligible_cells']}
        excluded = {_identity(row) for row in plan['excluded_cells']}
        self.assertFalse(eligible & excluded)
        self.assertEqual(len(eligible | excluded), 48)
        self.assertEqual(validate_recovery_plan(plan), plan)
        self.assertEqual(self.frozen(), before)

    def test_old_confirmation_and_duplicate_second_successor_do_not_reset_allowance(self):
        control, batch, plan = self.prepared()
        old = self.factory.confirmation(batch['batch_sha256'])
        old['plan_sha256'] = plan['plan_sha256']
        for invalid in (old, self.confirmation(plan, batch) |
                        {'approval_ref': old['approval_ref']}):
            with self.assertRaises(PolicySpendError):
                initialize_spend_ledger(control, batch_sha256=batch['batch_sha256'],
                    recovery_plan=plan, recovery_confirmation=invalid)
        self.assertFalse((self.root / 'cumulative-recovery-2-lock.json').exists())
        initialize_spend_ledger(control, batch_sha256=batch['batch_sha256'],
            recovery_plan=plan, recovery_confirmation=self.confirmation(plan, batch))
        money = spend_state(control)
        self.assertEqual((money['request_count'], money['reserved_usd'], money['spent_usd']),
                         (6, '0.660000', '0.002089'))
        other, other_batch, other_plan = self.prepared(self.root / 'duplicate-second')
        with self.assertRaises(PolicySpendError) as blocked:
            initialize_spend_ledger(other, batch_sha256=other_batch['batch_sha256'],
                recovery_plan=other_plan,
                recovery_confirmation=self.confirmation(other_plan, other_batch))
        self.assertEqual(blocked.exception.code, 'recovery_already_claimed')
        self.assertFalse((other / 'spend').exists())

    def test_new_unknown_holds_another_reservation_and_third_recovery_is_rejected(self):
        before = self.frozen()
        control, batch, plan = self.initialized()
        _run_fixture_cell(control, batch, batch['cells'][0], (TimeoutError(),), unknown=True)
        money = spend_state(control)
        self.assertEqual((money['state'], money['request_count'], money['reserved_usd']),
                         ('halted_unknown', 7, '0.990000'))
        with self.assertRaises(RecoveryError):
            build_recovery_plan(control, predecessor_batch_sha256=batch['batch_sha256'],
                target_control_root=self.root / 'third' / 'control',
                target_output_root=self.root / 'third' / 'outputs', recovery_version='third')
        self.assertEqual(self.frozen(), before)

    def test_crash_consumes_second_confirmation_and_copy_cannot_recover_it(self):
        control, batch, plan = self.prepared()
        claim_recovery(control, batch, plan, self.confirmation(plan, batch))
        with self.assertRaises(PolicySpendError) as blocked:
            initialize_spend_ledger(control, batch_sha256=batch['batch_sha256'],
                recovery_plan=plan, recovery_confirmation=self.confirmation(plan, batch))
        self.assertEqual(blocked.exception.code, 'recovery_already_claimed')
        self.assertFalse((control / 'spend').exists())
        copied = self.root / 'copy' / 'control'
        shutil.copytree(self.source, copied)
        with self.assertRaises(RecoveryError):
            build_recovery_plan(copied,
                predecessor_batch_sha256=self.source_batch['batch_sha256'],
                target_control_root=self.root / 'elsewhere' / 'control',
                target_output_root=self.root / 'elsewhere' / 'outputs', recovery_version='copy')

    def test_first_lock_byte_drift_and_logical_replay_are_rejected(self):
        plan = self.preview()
        changed = deepcopy(plan)
        changed['eligible_cells'].append(changed['excluded_cells'][0])
        changed['plan_sha256'] = _digest({k:v for k,v in changed.items() if k != 'plan_sha256'})
        with self.assertRaises(RecoveryError):
            validate_recovery_plan(changed)
        first_lock = self.root / 'cumulative-recovery-lock.json'
        first_lock.write_bytes(first_lock.read_bytes() + b' ')
        with self.assertRaises(RecoveryError):
            validate_recovery_plan(plan)


class KnownContractRecoveryTests(unittest.TestCase):
    def setUp(self):
        second = SecondRecoveryTests(methodName='runTest')
        second.setUp()
        self.factory = second
        self.root = second.root
        self.source, self.source_batch, _ = second.initialized()
        self.failed = self.source_batch['cells'][0]
        _run_fixture_cell(self.source, self.source_batch, self.failed, (reply('{}'),))
        self.target = self.root / 'known-contract-continuation'

    def preview(self, target=None):
        target = target or self.target
        return build_recovery_plan(self.source,
            predecessor_batch_sha256=self.source_batch['batch_sha256'],
            target_control_root=target / 'control', target_output_root=target / 'outputs',
            recovery_version='known-contract-continuation-v1')

    def prepared(self, target=None):
        target = target or self.target
        plan = self.preview(target)
        target.mkdir(mode=0o700)
        outputs = target / 'outputs'
        outputs.mkdir(mode=0o700)
        batch = prepare_policy_batch(
            [row['manifest_dir'] for row in self.source_batch['manifests']],
            dataset_root=self.factory.factory.dataset,
            project_root=self.factory.factory.project,
            control_root=target / 'control', output_root=outputs, seed=41,
            implementation_version=plan['recovery_version'], recovery_plan=plan,
            cell_failure_policy=plan['cell_failure_policy'])
        return target / 'control', batch, plan

    def confirmation(self, plan, batch):
        return {'schema': 'macr.deepseek.policy.recovery.confirmation.v3',
            'granted': True, 'approval_ref': 'original-unattempted-scope-confirmed',
            'plan_sha256': plan['plan_sha256'], 'batch_sha256': batch['batch_sha256'],
            'action': 'retain_unknown_and_continue_unattempted',
            'max_requests': 84, 'max_usd': '3.000000'}

    def test_known_source_continuation_retains_budget_and_excludes_every_claim(self):
        old = spend_state(self.source)
        plan = self.preview()
        self.assertEqual(plan['schema'], 'macr.deepseek.policy.recovery.v3')
        self.assertEqual(plan['cell_failure_policy'], 'record_known_contract_failure_v1')
        self.assertEqual(plan['opening'], {'request_count': old['request_count'],
            'spent_usd': old['spent_usd'], 'retained_unknown_usd': '0.660000'})
        self.assertEqual(len(plan['eligible_cells']), 43)
        self.assertEqual(len(plan['excluded_cells']), 5)
        self.assertNotIn(_logical(self.failed), plan['eligible_cells'])
        self.assertEqual(validate_recovery_plan(plan), plan)
        control, batch, plan = self.prepared()
        initialize_spend_ledger(control, batch_sha256=batch['batch_sha256'],
            recovery_plan=plan, recovery_confirmation=self.confirmation(plan, batch))
        new = spend_state(control)
        self.assertEqual((new['request_count'], new['spent_usd'], new['reserved_usd']),
                         (old['request_count'], old['spent_usd'], '0.660000'))
        self.assertEqual(new['state'], 'open')

    def test_known_continuation_cannot_be_claimed_twice_or_copied(self):
        control, batch, plan = self.prepared()
        initialize_spend_ledger(control, batch_sha256=batch['batch_sha256'],
            recovery_plan=plan, recovery_confirmation=self.confirmation(plan, batch))
        other, other_batch, other_plan = self.prepared(self.root / 'second-known-target')
        with self.assertRaises(PolicySpendError) as blocked:
            initialize_spend_ledger(other, batch_sha256=other_batch['batch_sha256'],
                recovery_plan=other_plan,
                recovery_confirmation=self.confirmation(other_plan, other_batch))
        self.assertEqual(blocked.exception.code, 'recovery_already_claimed')
        copied = self.root / 'copied-known' / 'control'
        shutil.copytree(control, copied)
        with self.assertRaises(PolicySpendError):
            spend_state(copied)

    def test_new_unknown_or_missing_received_identity_cannot_be_treated_as_known_source(self):
        response_path = Path(self.failed['out']) / 'decision-01.response.json'
        response = json.loads(response_path.read_text())
        response['system_fingerprint'] = None
        response_path.write_text(json.dumps(response))
        with self.assertRaises(RecoveryError):
            self.preview()

    def test_first_and_second_lock_drift_and_policy_injection_do_not_reclassify_source(self):
        plan = self.preview()
        changed = deepcopy(plan)
        changed['cell_failure_policy'] = 'ignore_all_failures'
        changed['plan_sha256'] = _digest({k:v for k,v in changed.items() if k != 'plan_sha256'})
        with self.assertRaises(RecoveryError):
            validate_recovery_plan(changed)
        lock = self.root / 'cumulative-recovery-2-lock.json'
        lock.write_bytes(lock.read_bytes() + b' ')
        with self.assertRaises(RecoveryError):
            validate_recovery_plan(plan)

    def test_crash_consumes_known_slot_and_a_v3_source_cannot_extend_again(self):
        from unittest.mock import patch
        control, batch, plan = self.prepared()
        claim_recovery(control, batch, plan, self.confirmation(plan, batch))
        self.assertTrue((self.root / 'cumulative-known-contract-lock.json').exists())
        with self.assertRaises(PolicySpendError) as blocked:
            initialize_spend_ledger(control, batch_sha256=batch['batch_sha256'],
                recovery_plan=plan, recovery_confirmation=self.confirmation(plan, batch))
        self.assertEqual(blocked.exception.code, 'recovery_already_claimed')
        self.assertFalse((control / 'spend').exists())
        with patch('macr.evals.deepseek_policy_spend._policy',
                   side_effect=AssertionError('depth_rejection_must_precede_policy')):
            with self.assertRaises(RecoveryError):
                build_recovery_plan(control, predecessor_batch_sha256=batch['batch_sha256'],
                    target_control_root=self.root / 'fourth' / 'control',
                    target_output_root=self.root / 'fourth' / 'outputs', recovery_version='fourth')


if __name__ == "__main__":
    unittest.main()
