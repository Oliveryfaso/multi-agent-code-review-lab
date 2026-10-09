import unittest
from dataclasses import asdict
from unittest.mock import patch
from debugger_fixtures import FakeClock
from macr.investigation.records import RunEvent, ActionUsage, ActionResult
from macr.investigation.budget import BudgetLedger
from macr.investigation.validation import ContractError
from test_investigation_budget import tool_action
from test_run_store import RunStore, StorageError, stored_fixture


class RunRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(RunStore, 'durable recovery missing')

    def test_crash_after_persisted_reservation_preserves_uncertainty(self):
        store, state = stored_fixture()
        self.addCleanup(store.close)
        store.checkpoint(state)
        ledger = BudgetLedger.restore(state.budget, clock=FakeClock())
        action = tool_action(1)
        reservation = ledger.reserve(action, ActionUsage(tool_calls=1, bytes_read=100))
        store.append(RunEvent(1, 'run1', 'reservation', {'action': asdict(action), 'reservation': asdict(reservation), 'budget': asdict(ledger.view())}, action.action_id))
        recovered = store.load('run1')
        self.assertEqual(recovered.uncertain_actions, ['a1'])
        self.assertIn('a1', recovered.state.inflight)
        self.assertNotIn('a1', recovered.state.completed)
        self.assertEqual(recovered.state.budget.reserved.tool_calls, 1)
        with self.assertRaises(ContractError):
            ledger.reserve(action, ActionUsage(tool_calls=1))
        self.assertEqual(store.load('run1').uncertain_actions, ['a1'])

    def test_completed_event_is_accounted_once(self):
        store, state = stored_fixture()
        self.addCleanup(store.close)
        store.checkpoint(state)
        ledger = BudgetLedger.restore(state.budget, clock=FakeClock())
        action = tool_action(1)
        reservation = ledger.reserve(action, ActionUsage(tool_calls=1))
        store.append(RunEvent(1, 'run1', 'reservation', {'action': asdict(action), 'reservation': asdict(reservation), 'budget': asdict(ledger.view())}, 'a1'))
        budget = ledger.settle('a1', ActionUsage(tool_calls=1))
        result = ActionResult('a1', 'completed', usage=ActionUsage(tool_calls=1))
        event = RunEvent(2, 'run1', 'result', {'result': asdict(result), 'budget': asdict(budget)}, 'a1')
        store.append(event)
        recovered = store.load('run1')
        self.assertEqual(recovered.state.budget.used.tool_calls, 1)
        self.assertEqual(recovered.uncertain_actions, [])
        with self.assertRaises(ContractError):
            store.append(RunEvent(3, 'run1', 'result', event.payload, 'a1'))

    def test_fsync_replace_and_single_writer_failures_block_new_work(self):
        store, state = stored_fixture()
        self.addCleanup(store.close)
        store.checkpoint(state)
        with self.assertRaises(StorageError):
            RunStore(store.root, snapshots=store.snapshots)
        with patch('macr.memory.run_store.os.fsync', side_effect=OSError('fixture flush failure')):
            with self.assertRaises(StorageError):
                store.append(RunEvent(1, 'run1', 'phase', {'phase': 'verifying'}))
        with self.assertRaises(StorageError):
            store.checkpoint(state)
        other, state2 = stored_fixture()
        self.addCleanup(other.close)
        other.checkpoint(state2)
        state2.phase = 'verifying'
        with patch('macr.memory.run_store.os.replace', side_effect=OSError('fixture replace failure')):
            with self.assertRaises(StorageError):
                other.checkpoint(state2)
        self.assertEqual(other.load('run1').state.phase, 'investigating')
