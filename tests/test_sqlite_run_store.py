import sqlite3
import unittest
from copy import deepcopy
from dataclasses import asdict
from unittest.mock import patch

from debugger_fixtures import FakeClock, fixture_root
from macr.investigation.budget import BudgetLedger
from macr.investigation.records import ActionResult, ActionUsage, RunEvent
from macr.investigation.validation import ContractError
from macr.memory.run_store import RunStore, StorageError
from macr.workflow import CodeReviewWorkflow
from test_investigation_budget import tool_action
from test_run_store import stored_fixture

try:
    from macr.memory.sqlite_run_store import SQLiteRunStore
except ModuleNotFoundError:
    SQLiteRunStore = None


class SQLiteRunStoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(SQLiteRunStore, 'SQLite compatibility adapter missing')
        legacy, self.state = stored_fixture()
        self.root, self.snapshots = legacy.root, legacy.snapshots
        legacy.close()
        self.store = SQLiteRunStore(self.root, snapshots=self.snapshots)
        self.addCleanup(lambda: self.store.close())

    def reopen(self):
        self.store.close()
        self.store = SQLiteRunStore(self.root, snapshots=self.snapshots)
        return self.store

    def reserve(self):
        self.store.checkpoint(self.state)
        ledger = BudgetLedger.restore(self.state.budget, clock=FakeClock())
        action = tool_action(1)
        reservation = ledger.reserve(action, ActionUsage(tool_calls=1, bytes_read=100))
        self.store.append(RunEvent(1, 'run1', 'reservation', {
            'action': asdict(action), 'reservation': asdict(reservation),
            'budget': asdict(ledger.view()),
        }, action.action_id))
        return ledger, action

    def test_roundtrip_reopen_and_existing_workflow_read_adapter(self):
        ref = self.store.checkpoint(self.state)
        self.assertEqual(ref.sequence, 0)
        self.assertEqual(asdict(self.reopen().load('run1').state), asdict(self.state))
        self.store.append(RunEvent(1, 'run1', 'phase', {'phase': 'verifying'}))
        recovered = CodeReviewWorkflow().read_investigation(self.reopen(), 'run1')
        self.assertEqual(recovered.state.phase, 'verifying')
        self.assertEqual([event.sequence for event in self.store.read_events('run1')], [1])
        self.assertFalse((self.root / 'run1').exists())
        self.assertLessEqual((self.root / 'runs.sqlite3').stat().st_size, self.store.MAX_BYTES)

    def test_transaction_rolls_back_event_when_state_update_fails(self):
        self.store.checkpoint(self.state)
        self.store._connection.set_authorizer(
            lambda operation, table, *args: sqlite3.SQLITE_DENY
            if operation == sqlite3.SQLITE_UPDATE and table == 'runs' else sqlite3.SQLITE_OK)
        with self.assertRaises(StorageError):
            self.store.append(RunEvent(1, 'run1', 'phase', {'phase': 'verifying'}))
        with self.assertRaises(StorageError):
            self.store.append(RunEvent(1, 'run1', 'phase', {'phase': 'verifying'}))
        self.reopen()
        self.assertEqual(self.store.load('run1').state.phase, 'investigating')
        self.assertEqual(self.store.read_events('run1'), [])

    def test_commit_ack_loss_reopens_consumed_event_and_rejects_duplicate(self):
        self.store.checkpoint(self.state)
        def lose_ack():
            self.store._connection.execute('COMMIT')
            raise sqlite3.OperationalError('fixture lost commit acknowledgement')
        event = RunEvent(1, 'run1', 'phase', {'phase': 'verifying'})
        with patch.object(self.store, '_commit', side_effect=lose_ack):
            with self.assertRaises(StorageError):
                self.store.append(event)
        self.reopen()
        self.assertEqual(self.store.load('run1').state.phase, 'verifying')
        with self.assertRaises(ContractError):
            self.store.append(event)
        with self.assertRaises(ContractError):
            self.store.append(RunEvent(1, 'run1', 'phase', {'phase': 'investigating'}))
        self.assertEqual(len(self.store.read_events('run1')), 1)

    def test_interruption_before_commit_rolls_back_and_blocks_writes(self):
        self.store.checkpoint(self.state)
        with patch.object(self.store, '_commit', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.store.append(RunEvent(1, 'run1', 'phase', {'phase': 'verifying'}))
        with self.assertRaises(StorageError):
            self.store.checkpoint(self.state)
        self.reopen()
        self.assertEqual(self.store.load('run1').state.phase, 'investigating')
        self.assertEqual(self.store.read_events('run1'), [])

    def test_uncertain_action_survives_reopen_without_budget_reset(self):
        self.reserve()
        recovered = self.reopen().load('run1')
        self.assertEqual(recovered.uncertain_actions, ['a1'])
        self.assertEqual(recovered.state.budget.reserved.tool_calls, 1)
        self.assertEqual(recovered.state.budget.reserved.bytes_read, 100)
        self.assertNotIn('a1', recovered.state.completed)
        with self.assertRaisesRegex(ContractError, 'checkpoint_rewind'):
            self.store.checkpoint(self.state)

    def test_completed_action_budget_is_settled_once(self):
        ledger, action = self.reserve()
        usage = ActionUsage(tool_calls=1, bytes_read=7)
        result = ActionResult(action.action_id, 'completed', usage=usage)
        budget = ledger.settle(action.action_id, usage)
        payload = {'result': asdict(result), 'budget': asdict(budget)}
        self.store.append(RunEvent(2, 'run1', 'result', payload, action.action_id))
        recovered = self.reopen().load('run1')
        self.assertEqual(recovered.uncertain_actions, [])
        self.assertEqual(recovered.state.budget.used.tool_calls, 1)
        with self.assertRaises(ContractError):
            self.store.append(RunEvent(3, 'run1', 'result', payload, action.action_id))
        self.assertEqual(self.store.load('run1').state.budget.used.bytes_read, 7)

    def test_second_writer_rejected_and_sqlite_busy_fails_closed(self):
        self.store.checkpoint(self.state)
        with self.assertRaises(StorageError):
            SQLiteRunStore(self.root, snapshots=self.snapshots)
        with sqlite3.connect(self.root / 'runs.sqlite3', isolation_level=None) as competitor:
            competitor.execute('BEGIN IMMEDIATE')
            with self.assertRaises(StorageError):
                self.store.append(RunEvent(1, 'run1', 'phase', {'phase': 'verifying'}))
            competitor.execute('ROLLBACK')
        with self.assertRaises(StorageError):
            self.store.append(RunEvent(1, 'run1', 'phase', {'phase': 'verifying'}))
        self.reopen()
        self.assertEqual(self.store.read_events('run1'), [])

    def test_domain_validation_still_rejects_snapshot_and_profile_drift(self):
        invalid = deepcopy(self.state)
        invalid.task.model_profile = 'unapproved'
        with self.assertRaises(ContractError):
            self.store.checkpoint(invalid)
        self.store.checkpoint(self.state)
        from pathlib import Path
        (Path(self.state.task.snapshot_ref.root_ref) / 'a.py').write_text('changed\n')
        with self.assertRaises(ContractError):
            self.store.load('run1')

    def test_corrupt_event_rejected(self):
        self.store.checkpoint(self.state)
        self.store.append(RunEvent(1, 'run1', 'phase', {'phase': 'verifying'}))
        self.store._connection.execute("UPDATE events SET payload=? WHERE sequence=1", (b'{bad',))
        with self.assertRaises(StorageError):
            self.store.load('run1')
        with self.assertRaises(StorageError):
            self.store.checkpoint(self.state)

    def test_backend_roots_are_not_mixed_or_migrated(self):
        self.store.checkpoint(self.state)
        before = (self.root / 'runs.sqlite3').read_bytes()
        self.store.close()
        with self.assertRaisesRegex(StorageError, 'backend_mismatch'):
            RunStore(self.root, snapshots=self.snapshots)
        self.assertEqual((self.root / 'runs.sqlite3').read_bytes(), before)
        legacy, state = stored_fixture()
        self.addCleanup(legacy.close)
        path = legacy.checkpoint(state).path
        from pathlib import Path
        previous = Path(path).read_bytes()
        legacy.close()
        with self.assertRaisesRegex(StorageError, 'backend_mismatch'):
            SQLiteRunStore(legacy.root, snapshots=legacy.snapshots)
        self.assertEqual(Path(path).read_bytes(), previous)

    def test_database_symlink_and_schema_version_are_rejected(self):
        self.store.checkpoint(self.state)
        self.store._connection.execute('PRAGMA user_version=99')
        self.store.close()
        with self.assertRaises(StorageError):
            SQLiteRunStore(self.root, snapshots=self.snapshots)
        other = fixture_root()
        link = other / 'runs.sqlite3'
        link.symlink_to(self.root / 'runs.sqlite3')
        self.addCleanup(link.unlink)
        with self.assertRaises(StorageError):
            SQLiteRunStore(other, snapshots=self.snapshots)

    def test_database_full_rolls_back_to_last_complete_state(self):
        self.store.close()
        class SmallStore(SQLiteRunStore):
            MAX_BYTES = 64 * 1024
        self.store = SmallStore(fixture_root(), snapshots=self.snapshots)
        self.root = self.store.root
        self.store.checkpoint(self.state)
        last = 0
        for sequence in range(1, 301):
            phase = 'verifying' if sequence % 2 else 'investigating'
            try:
                self.store.append(RunEvent(sequence, 'run1', 'phase', {'phase': phase}))
            except StorageError:
                break
            last = sequence
        else:
            self.fail('bounded database did not hit capacity')
        self.assertGreater(last, 0)
        self.store.close()
        self.store = SmallStore(self.root, snapshots=self.snapshots)
        self.assertEqual(len(self.store.read_events('run1')), last)
        expected = 'verifying' if last % 2 else 'investigating'
        self.assertEqual(self.store.load('run1').state.phase, expected)
        self.assertLessEqual((self.root / 'runs.sqlite3').stat().st_size, 64 * 1024)

    def test_checkpoint_ahead_of_journal_is_rejected(self):
        self.store.checkpoint(self.state)
        self.store.append(RunEvent(1, 'run1', 'phase', {'phase': 'verifying'}))
        self.store._connection.execute('DELETE FROM events')
        with self.assertRaisesRegex(StorageError, 'checkpoint_ahead_of_journal'):
            self.store.load('run1')

    def test_deleted_database_path_blocks_open_connection(self):
        self.store.checkpoint(self.state)
        (self.root / 'runs.sqlite3').unlink()
        with self.assertRaisesRegex(StorageError, 'database_identity_changed'):
            self.store.load('run1')
        with self.assertRaises(StorageError):
            self.store.checkpoint(self.state)

    def test_read_snapshot_blocks_commit_between_state_and_journal(self):
        self.store.checkpoint(self.state)
        competitor = sqlite3.connect(self.root / 'runs.sqlite3', isolation_level=None, timeout=0)
        self.addCleanup(competitor.close)
        outcomes = []
        def between_reads(statement):
            if statement.startswith('SELECT sequence,payload') and not outcomes:
                competitor.execute('BEGIN IMMEDIATE')
                competitor.execute('UPDATE runs SET sequence=1')
                try:
                    competitor.execute('COMMIT')
                    outcomes.append('committed')
                except sqlite3.OperationalError:
                    outcomes.append('blocked')
                    competitor.execute('ROLLBACK')
        self.store._connection.set_trace_callback(between_reads)
        try:
            self.assertEqual(self.store.load('run1').state.phase, 'investigating')
        finally:
            self.store._connection.set_trace_callback(None)
        self.assertEqual(outcomes, ['blocked'])
        self.store.append(RunEvent(1, 'run1', 'phase', {'phase': 'verifying'}))

    def test_same_event_from_two_threads_is_committed_once(self):
        from concurrent.futures import ThreadPoolExecutor
        self.store.checkpoint(self.state)
        def append():
            try:
                self.store.append(RunEvent(1, 'run1', 'phase', {'phase': 'verifying'}))
                return 'committed'
            except ContractError:
                return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(lambda _: append(), range(2))), ['committed', 'rejected'])
        self.assertEqual(len(self.store.read_events('run1')), 1)

    def test_same_version_alien_schema_is_rejected_without_rewrite(self):
        self.store.checkpoint(self.state)
        self.store._connection.execute('CREATE TABLE alien (value TEXT)')
        self.store.close()
        before = (self.root / 'runs.sqlite3').read_bytes()
        with self.assertRaisesRegex(StorageError, 'database_schema_invalid'):
            SQLiteRunStore(self.root, snapshots=self.snapshots)
        self.assertEqual((self.root / 'runs.sqlite3').read_bytes(), before)
