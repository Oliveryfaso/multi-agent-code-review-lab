import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from macr.api.evidence_workbench import Workbench
from macr.investigation.evidence_records import StaticEvidenceReport
from macr.investigation.scope import read_safe
from macr.investigation.evidence_budget import BudgetExceeded
from macr.investigation.validation import ContractError
from evidence_ui_fixtures import publish_fixture_request, failed_budget_fixture


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='macr-ui-owner-', dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'workbench'
        self.repo = self.base / 'source'
        self.repo.mkdir()
        (self.repo / 'a.py').write_text('def entry():\n    return 1\n')

    def payload(self):
        return {'intent_id': str(uuid4()), 'repo': str(self.repo), 'question': 'Read entry',
                'entries': ['a.py:entry'], 'symbols': [], 'traceback': '', 'profile_name': 'standard'}

    def cancelled(self, request, *, cancelled):
        deadline = time.monotonic() + 2
        while not cancelled() and time.monotonic() < deadline:
            time.sleep(.005)
        return StaticEvidenceReport(request, 'cancelled', run={'worker_started': False, 'worker_exit_confirmed': None,
            'publication': 'uncommitted'}, errors=[{'code': 'cancelled', 'stage': 'input_validation'}])

    def test_second_owner_rejected_and_named_lock_replacement_quarantines(self):
        owner = Workbench(self.root)
        self.addCleanup(owner.shutdown)
        with self.assertRaises(ContractError) as caught:
            Workbench(self.root)
        self.assertEqual(caught.exception.code, 'workbench_busy')
        lock = self.root / 'owner.lock'
        lock.rename(self.root / 'old.lock')
        lock.write_bytes(b'')
        with self.assertRaises(ContractError):
            owner.start(self.payload())

    def test_repeated_intent_resolves_to_single_run_and_conflicts_reject(self):
        with patch('macr.investigation.evidence_supervisor.collect_evidence', self.cancelled):
            owner = Workbench(self.root)
            self.addCleanup(owner.shutdown)
            payload = self.payload()
            first = owner.start(payload)
            self.assertEqual(owner.start(payload)['id'], first['id'])
            self.assertEqual(len(owner.history()['items']), 1)
            altered = dict(payload, question='different')
            with self.assertRaises(ContractError) as caught:
                owner.start(altered)
            self.assertEqual(caught.exception.code, 'intent_conflict')
            self.assertEqual(owner.by_intent(payload['intent_id'])['id'], first['id'])
            owner.cancel(first['id'])

    def test_cancel_requests_event_then_waits_for_terminal_state(self):
        with patch('macr.investigation.evidence_supervisor.collect_evidence', self.cancelled):
            owner = Workbench(self.root)
            self.addCleanup(owner.shutdown)
            run = owner.start(self.payload())
            reply = owner.cancel(run['id'])
            self.assertTrue(reply['cancel_requested'])
            owner.shutdown()
            self.assertEqual(owner.state(run['id'])['analysis_status'], 'cancelled')
        recovered = Workbench(self.root)
        self.addCleanup(recovered.shutdown)
        self.assertEqual(recovered.state(run['id'])['analysis_status'], 'cancelled')
        self.assertFalse(recovered.state(run['id'])['worker_started'])

    def test_unknown_escaped_failure_remains_isolated_after_restart(self):
        def escaped(*args, **kwargs):
            raise RuntimeError('escaped supervisor failure')
        with patch('macr.investigation.evidence_supervisor.collect_evidence', escaped):
            owner = Workbench(self.root)
            run = owner.start(self.payload())
            owner.shutdown()
        recovered = Workbench(self.root)
        self.addCleanup(recovered.shutdown)
        self.assertEqual(recovered.state(run['id'])['state'], 'closure_unknown')
        with self.assertRaises(ContractError) as caught:
            recovered.start(self.payload())
        self.assertEqual(caught.exception.code, 'closure_unknown')

    def test_protected_repo_and_invalid_input_do_not_create_run(self):
        owner = Workbench(self.root)
        self.addCleanup(owner.shutdown)
        for payload in (dict(self.payload(), question=' '), dict(self.payload(), repo=str(self.base / '.ssh')),
                        dict(self.payload(), profile_name='custom'), dict(self.payload(), entries=[], traceback='')):
            with self.assertRaises(ContractError):
                owner.start(payload)
        self.assertEqual(owner.history()['items'], [])

    def test_symlink_data_root_is_rejected_without_touching_destination(self):
        other = self.base / 'other'
        other.mkdir()
        self.root.symlink_to(other, target_is_directory=True)
        with self.assertRaises(ContractError):
            Workbench(self.root)
        self.assertEqual(list(other.iterdir()), [])

    def test_registry_extra_fields_fail_closed(self):
        owner = Workbench(self.root)
        owner.shutdown()
        path = self.root / 'registry.json'
        raw = json.loads(path.read_bytes())
        raw['extra'] = True
        path.write_text(json.dumps(raw))
        with self.assertRaises(ContractError) as caught:
            Workbench(self.root)
        self.assertEqual(caught.exception.code, 'registry_invalid')

    def test_completed_core_outcome_and_history_verification_failure_are_separate(self):
        with patch('macr.investigation.evidence_supervisor.collect_evidence', publish_fixture_request):
            owner = Workbench(self.root)
            with patch('macr.api.evidence_workbench.read_committed', side_effect=ContractError('history_budget_exceeded','max_io_bytes')):
                run = owner.start(self.payload())
                owner.thread.join(2)
            state = owner.state(run['id'])
            self.assertEqual(state['analysis_status'], 'completed')
            self.assertEqual(state['publication'], 'committed')
            self.assertEqual(state['verification_status'], 'budget_exceeded')
            self.assertTrue(state['worker_exit_confirmed'])
            self.assertIn('report', owner.report(run['id']))
            self.assertEqual(owner.state(run['id'])['state'], 'ready')
            self.assertEqual(owner.state(run['id'])['verification_status'], 'passed')
            owner.shutdown()

    def test_ready_report_export_and_cancel_after_commit_preserve_original(self):
        with patch('macr.investigation.evidence_supervisor.collect_evidence', publish_fixture_request):
            owner = Workbench(self.root)
            self.addCleanup(owner.shutdown)
            run = owner.start(self.payload())
            owner.thread.join(2)
            self.assertEqual(owner.state(run['id'])['state'], 'ready')
            before = owner.report(run['id'])['json_bytes']
            self.assertEqual(owner.cancel(run['id'])['state'], 'ready')
            self.assertEqual(owner.report(run['id'])['json_bytes'], before)

    def test_cancel_between_verification_and_final_save_preserves_ready(self):
        ready, release = threading.Event(), threading.Event()
        owner = Workbench(self.root)
        self.addCleanup(owner.shutdown)
        original = owner.mutex

        class PauseAfterReady:
            paused = False

            def __enter__(self):
                original.acquire()
                return self

            def __exit__(self, *args):
                pause = (threading.current_thread() is owner.thread and not self.paused
                         and any(item['state'] == 'ready' for item in owner.registry['runs'].values()))
                if pause:
                    self.paused = True
                original.release()
                if pause:
                    ready.set()
                    if not release.wait(2):
                        raise RuntimeError('fixture_pause_timeout')

        owner.mutex = PauseAfterReady()
        with patch('macr.investigation.evidence_supervisor.collect_evidence', publish_fixture_request):
            run = owner.start(self.payload())
            try:
                self.assertTrue(ready.wait(2))
                reply = owner.cancel(run['id'])
            finally:
                release.set()
                owner.thread.join(2)
            self.assertEqual(reply['state'], 'ready')
            self.assertIsNone(owner.active)
            self.assertFalse(reply['cancel_requested'])
            self.assertEqual(owner.state(run['id'])['state'], 'ready')

    def test_commit_binding_survives_verifying_registry_save_failure(self):
        owner = Workbench(self.root)
        self.addCleanup(owner.shutdown)
        original = owner._save
        failed = []

        def transient():
            if not failed and any(item['state'] == 'verifying' for item in owner.registry['runs'].values()):
                failed.append(True)
                raise ContractError('storage_unavailable', 'verifying_registry')
            return original()

        with patch('macr.investigation.evidence_supervisor.collect_evidence', publish_fixture_request), patch.object(owner, '_save', transient):
            run = owner.start(self.payload())
            owner.thread.join(2)
        self.assertEqual(len(failed), 1)
        self.assertEqual(owner.state(run['id'])['publication'], 'committed')
        self.assertTrue(owner.state(run['id'])['worker_exit_confirmed'])
        self.assertEqual(owner.state(run['id'])['state'], 'ready')
        self.assertIn('report', owner.report(run['id']))
        owner.shutdown()
        recovered = Workbench(self.root)
        self.addCleanup(recovered.shutdown)
        self.assertIn('report', recovered.report(run['id']))

    def test_closed_committed_unbound_verification_failure_recovers_without_dispatch(self):
        with patch('macr.investigation.evidence_supervisor.collect_evidence', publish_fixture_request):
            owner = Workbench(self.root)
            run = owner.start(self.payload())
            owner.thread.join(2)
        raw = owner.registry['runs'][run['id']]
        raw.update(state='verification_failed', verification_status='unavailable', binding=None, error='storage_unavailable')
        owner._save()
        owner.shutdown()
        with patch('macr.investigation.evidence_supervisor.collect_evidence', side_effect=AssertionError('recovery must not dispatch')):
            recovered = Workbench(self.root)
        self.addCleanup(recovered.shutdown)
        self.assertEqual(recovered.state(run['id'])['state'], 'ready')
        self.assertIn('report', recovered.report(run['id']))

    def test_closed_verified_run_is_terminal_when_final_storage_check_fails(self):
        owner = Workbench(self.root)
        self.addCleanup(owner.shutdown)
        initial = owner._allocation()
        with patch('macr.investigation.evidence_supervisor.collect_evidence', publish_fixture_request), \
                patch.object(owner, '_allocation', side_effect=[initial, ContractError('storage_unavailable', 'final_storage')]):
            run = owner.start(self.payload())
            owner.thread.join(2)
        state = owner.state(run['id'])
        self.assertIsNone(owner.active)
        self.assertTrue(state['worker_exit_confirmed'])
        self.assertEqual(state['state'], 'ready')
        self.assertEqual(state['verification_status'], 'passed')
        self.assertEqual(state['error'], 'storage_unavailable')
        with self.assertRaises(ContractError):
            owner.start(self.payload())

    def test_verified_history_read_survives_registry_write_failure(self):
        with patch('macr.investigation.evidence_supervisor.collect_evidence', publish_fixture_request):
            owner = Workbench(self.root)
            self.addCleanup(owner.shutdown)
            run = owner.start(self.payload())
            owner.thread.join(2)
        original = owner.report(run['id'])['json_bytes']
        with patch.object(owner, '_save', side_effect=ContractError('storage_unavailable', 'registry')):
            saved = owner.report(run['id'])
            self.assertEqual(saved['json_bytes'], original)
            self.assertEqual(saved['state']['verification_status'], 'passed')
            self.assertEqual(saved['state_persistence'], 'unavailable')
            captured = Path(saved['report']['source_version']['snapshot_ref']['root_ref']) / 'a.py'
            captured.write_bytes(captured.read_bytes() + b'x')
            with self.assertRaises(ContractError) as caught:
                owner.report(run['id'])
            self.assertEqual(caught.exception.code, 'history_corrupt')

    def test_final_envelope_shares_the_complete_read_budget(self):
        with patch('macr.investigation.evidence_supervisor.collect_evidence', publish_fixture_request):
            owner = Workbench(self.root)
            self.addCleanup(owner.shutdown)
            run = owner.start(self.payload())
            owner.thread.join(2)
        reads = []

        def tracked(root, relative, limit, **kwargs):
            data = read_safe(root, relative, limit, **kwargs)
            reads.append((relative, kwargs.get('budget'), len(data)))
            return data

        with patch('macr.api.evidence_reader.read_safe', tracked), patch('macr.api.evidence_workbench.read_safe', tracked):
            saved = owner.report(run['id'])
        final = next(item for item in reads if item[0].startswith('envelopes/'))
        self.assertIsNotNone(final[1])
        self.assertTrue(all(item[1] is final[1] for item in reads))
        self.assertEqual(saved['read_usage']['max_io_bytes'], sum(item[2] for item in reads))

    def test_final_envelope_is_checked_against_the_same_deadline(self):
        with patch('macr.investigation.evidence_supervisor.collect_evidence', publish_fixture_request):
            owner = Workbench(self.root)
            self.addCleanup(owner.shutdown)
            run = owner.start(self.payload())
            owner.thread.join(2)

        def expire(root, relative, limit, **kwargs):
            if relative.startswith('envelopes/') and kwargs.get('budget') is not None:
                kwargs['budget'].deadline = kwargs['budget'].started - 1
            return read_safe(root, relative, limit, **kwargs)

        with patch('macr.api.evidence_reader.read_safe', expire), patch('macr.api.evidence_workbench.read_safe', expire):
            with self.assertRaises(ContractError) as caught:
                owner.report(run['id'])
        self.assertEqual(caught.exception.code, 'history_budget_exceeded')

    def test_orphan_empty_envelope_is_rejected_on_reopen(self):
        owner = Workbench(self.root)
        owner.shutdown()
        (self.root / 'envelopes/x-0.json').write_bytes(b'')
        with self.assertRaises(ContractError) as caught:
            Workbench(self.root)
        self.assertEqual(caught.exception.code, 'registry_invalid')

    def test_empty_metadata_scan_has_a_deadline(self):
        owner = Workbench(self.root)
        self.addCleanup(owner.shutdown)
        budget = owner._budget()
        budget.deadline = budget.started - 1
        with patch.object(owner, '_budget', return_value=budget):
            with self.assertRaises(BudgetExceeded) as caught:
                owner._metadata_bytes()
        self.assertEqual(caught.exception.field, 'active_seconds')

    def test_single_run_reservation_refuses_before_dispatch_when_retained_budget_cannot_fit(self):
        owner = Workbench(self.root)
        self.addCleanup(owner.shutdown)
        with patch('macr.api.evidence_workbench.ROOT_LIMIT', 512 * 1024 * 1024):
            with self.assertRaises(ContractError) as caught:
                owner.start(self.payload())
        self.assertEqual(caught.exception.code, 'workbench_storage_limit')
        self.assertEqual(owner.history()['items'], [])

    def test_budget_failure_details_reach_state_history_and_recovery_without_report(self):
        with patch('macr.investigation.evidence_supervisor.collect_evidence', failed_budget_fixture):
            owner = Workbench(self.root)
            self.addCleanup(owner.shutdown)
            run = owner.start(self.payload())
            owner.thread.join(2)
        expected = {'code': 'scope_limit', 'field': 'max_metadata_entries',
                    'stage': 'supervisor_storage', 'effective': 20000, 'observed': 20001}
        self.assertEqual(owner.state(run['id'])['analysis_failure'], expected)
        self.assertEqual(owner.history()['items'][0]['analysis_failure'], expected)
        self.assertEqual(owner.state(run['id'])['state'], 'failed')
        self.assertEqual(owner.state(run['id'])['publication'], 'uncommitted')
        with self.assertRaises(ContractError): owner.report(run['id'])
        owner.shutdown()
        with patch('macr.investigation.evidence_supervisor.collect_evidence', side_effect=AssertionError('no recovery dispatch')):
            recovered = Workbench(self.root)
        self.addCleanup(recovered.shutdown)
        self.assertEqual(recovered.state(run['id'])['analysis_failure'], expected)

    def test_untrusted_persisted_failure_summary_is_bounded_and_old_records_recover(self):
        with patch('macr.investigation.evidence_supervisor.collect_evidence', failed_budget_fixture):
            owner = Workbench(self.root)
            run = owner.start(self.payload())
            owner.thread.join(2)
        owner.shutdown()
        original = json.loads((self.root / 'registry.json').read_bytes())
        self.assertIn('analysis_failure', original['runs'][run['id']])
        expected = original['runs'][run['id']]['analysis_failure']
        for altered in (dict(expected, stage='x' * 1024), dict(expected, observed=True), dict(expected, effective=1)):
            record = json.loads(json.dumps(original))
            record['runs'][run['id']]['analysis_failure'] = altered
            (self.root / 'registry.json').write_text(json.dumps(record))
            with self.assertRaises(ContractError) as caught: Workbench(self.root)
            self.assertEqual(caught.exception.code, 'registry_invalid')
        original['runs'][run['id']].pop('analysis_failure')
        (self.root / 'registry.json').write_text(json.dumps(original))
        recovered = Workbench(self.root)
        self.addCleanup(recovered.shutdown)
        self.assertIsNone(recovered.state(run['id'])['analysis_failure'])
        self.assertEqual(recovered.state(run['id'])['state'], 'failed')

    def test_explicit_capacity_preserves_other_limits_report_and_recovery_binding(self):
        from macr.investigation.evidence_profiles import EvidenceProfile
        original = EvidenceProfile.select('standard').to_dict()
        expected = original | {'max_metadata_entries': 100000, 'max_metadata_bytes': 16 * 1024 * 1024}
        payload = self.payload() | {'profile_name': 'standard-capacity'}
        with patch('macr.investigation.evidence_supervisor.collect_evidence', publish_fixture_request):
            owner = Workbench(self.root); self.addCleanup(owner.shutdown)
            run = owner.start(payload); owner.thread.join(2)
        self.assertEqual(run['profile_name'], 'standard-capacity')
        self.assertEqual(run['effective_profile'], expected)
        self.assertEqual(owner.report(run['id'])['report']['request']['profile'], expected)
        self.assertEqual(owner.report(run['id'])['receipt']['profile'], expected)
        self.assertEqual(EvidenceProfile.select('standard').to_dict(), original)
        owner.shutdown()
        with patch('macr.investigation.evidence_supervisor.collect_evidence', side_effect=AssertionError('no recovery worker')):
            recovered = Workbench(self.root)
        self.assertEqual(recovered.state(run['id'])['profile_name'], 'standard-capacity')
        self.assertEqual(recovered.state(run['id'])['effective_profile'], expected)
        recovered.shutdown()
        record = json.loads((self.root / 'registry.json').read_bytes())
        record['runs'][run['id']]['request']['profile']['max_metadata_entries'] += 1
        (self.root / 'registry.json').write_text(json.dumps(record))
        with self.assertRaises(ContractError) as caught: Workbench(self.root)
        self.assertEqual(caught.exception.code, 'registry_invalid')
