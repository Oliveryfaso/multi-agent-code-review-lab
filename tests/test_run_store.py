import json
import unittest
from dataclasses import asdict
from pathlib import Path
from debugger_fixtures import fixture_root, write_files, FakeClock
from macr.investigation.records import *
from macr.investigation.scope import RepositoryScope
from macr.investigation.snapshots import SnapshotStore
from macr.investigation.validation import ContractError
from macr.investigation.budget import BudgetLedger
from macr.investigation.queue import InvestigationQueue
from test_investigation_budget import tool_action
try:
    from macr.memory.run_store import RunStore, StorageError
except ModuleNotFoundError:
    RunStore = StorageError = None


def stored_fixture():
    root = write_files(fixture_root(), {'a.py': 'def send():\n    return 1\n'})
    snapshots = SnapshotStore(fixture_root() / 'snapshots')
    bundle = snapshots.capture(root, RepositoryScope().inventory(root, ScopeProfile()))
    state = InvestigationState(TaskSpec('run1', 'scan', snapshot_ref=bundle.ref), phase='investigating', scope=bundle.scope)
    state.evidence['e1'] = EvidenceRecord('e1', bundle.ref.snapshot_id, 'returns one', Location('a.py', 1, 2), 'return 1')
    state.hypotheses['h1'] = HypothesisRecord('h1', 'fixture', support_ids=['e1'])
    queue = InvestigationQueue()
    queue.push(tool_action(9), 'breadth', 2)
    state.queue = queue.snapshot()
    state.messages = [RoleMessage('m1', 1, 'investigator', 'falsifier', evidence_ids=['e1'], hypothesis_ids=['h1'])]
    state.cursors = {'falsifier': 0}
    state.checks = {'c1': CheckResult('c1', 'blocked')}
    state.budget = BudgetLedger(clock=FakeClock()).view()
    return RunStore(fixture_root() / 'runs', snapshots=snapshots), state


class RunStoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(RunStore, 'durable run store missing')

    def test_full_checkpoint_roundtrip_and_identity_rejection(self):
        store, state = stored_fixture()
        self.addCleanup(store.close)
        checkpoint = store.checkpoint(state)
        self.assertEqual(asdict(store.load('run1').state), asdict(state))
        envelope = json.loads(Path(checkpoint.path).read_text())
        envelope['state']['task']['model_profile'] = 'unapproved'
        Path(checkpoint.path).write_text(json.dumps(envelope))
        with self.assertRaises(ContractError):
            store.load('run1')

    def test_partial_tail_keeps_last_complete_event_and_original_bytes(self):
        store, state = stored_fixture()
        self.addCleanup(store.close)
        store.checkpoint(state)
        self.assertEqual(store.append(RunEvent(1, 'run1', 'phase', {'phase': 'verifying'})), 1)
        path = store.root / 'run1' / 'events.jsonl'
        with path.open('ab') as stream:
            stream.write(b'{"partial":')
        before = path.read_bytes()
        recovered = store.load('run1')
        self.assertEqual(recovered.state.phase, 'verifying')
        self.assertEqual(path.read_bytes(), before)
        self.assertIn('event_tail_incomplete', [e.code for e in recovered.errors])
        with self.assertRaises(StorageError):
            store.append(RunEvent(2, 'run1', 'phase', {'phase': 'investigating'}))

    def test_stale_snapshot_and_schema_fail_closed(self):
        store, state = stored_fixture()
        self.addCleanup(store.close)
        store.checkpoint(state)
        (Path(state.task.snapshot_ref.root_ref) / 'a.py').write_text('changed\n')
        with self.assertRaises(ContractError):
            store.load('run1')
        store2, state2 = stored_fixture()
        self.addCleanup(store2.close)
        path = Path(store2.checkpoint(state2).path)
        data = json.loads(path.read_text())
        data['state']['schema_version'] = 99
        path.write_text(json.dumps(data))
        with self.assertRaises(ContractError):
            store2.load('run1')

    def test_legacy_payload_stays_unverified(self):
        store, state = stored_fixture()
        self.addCleanup(store.close)
        path = write_files(fixture_root(), {'old.json': '{"test_status":"passed"}'}) / 'old.json'
        legacy = store.read_legacy(path)
        self.assertEqual(legacy['validation_status'], 'legacy_unverified')
        self.assertEqual(legacy['payload']['test_status'], 'passed')
        self.assertNotIn('verified', legacy)
