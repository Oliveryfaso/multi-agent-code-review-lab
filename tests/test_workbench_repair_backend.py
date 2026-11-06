"""Workbench execution opt-in contracts; authored receipts never run targets."""
import json
from unittest import TestCase
from unittest.mock import Mock, patch

from macr.investigation import repair_loop as loop
from macr.investigation.validation import ContractError
from macr.providers.repair_binding import RepairProviderBinding
from test_repair_backend_verifier import FixtureBackend
import test_workbench_repair as fixtures


class WorkbenchRepairBackendTests(TestCase):
    def setUp(self):
        self.h = fixtures.WorkbenchRepairTests('runTest')
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.names = loop.test_manifest(self.h.files, ['checks.py'])

    def verifier(self, backend):
        return loop.BackendVerifier(backend, backend.public_result,
            backend_id='authored-public-backend', execution_profile='authored-public-v1')

    def test_default_rejects_backend_before_availability_or_dispatch(self):
        backend = FixtureBackend(self.names)
        verifier = self.verifier(backend)
        factory = Mock(return_value=verifier)
        owner = self.h.owner(repair_verifier_factory=factory)
        with patch.object(verifier, 'available', side_effect=AssertionError('must_not_probe')) as available:
            run = self.h.finish(owner, self.h.payload())
        self.assertEqual((run['state'], run['repair_status']), ('failed', 'rejected'))
        self.assertEqual(run['summary']['provider_requests'], 0)
        self.assertEqual(run['summary']['error'], 'repair_runtime_forbidden')
        factory.assert_called_once_with()
        available.assert_not_called()
        self.assertEqual((backend.calls, backend.reads), ([], []))

    def test_enabled_backend_pass_keeps_provenance_and_reopens_without_runtime(self):
        backend = FixtureBackend(self.names)
        transport = fixtures.AuthoredTransport([self.h.candidate()])
        owner = self.h.owner(repair_execution_enabled=True,
            repair_verifier_factory=lambda: self.verifier(backend),
            repair_provider_factory=lambda config: RepairProviderBinding(config, transport=transport))
        run = self.h.finish(owner, self.h.payload())
        self.assertEqual((run['state'], run['repair_status']), ('completed', 'public_tests_reported_passed'))
        result = owner.repair_result(run['id'])
        self.assertEqual(result['report']['verification_origin'], 'backend_reported_unattested')
        self.assertEqual(result['summary']['verification_origin'], 'backend_reported_unattested')
        self.assertIsNone(result['report']['target_code_executed'])
        self.assertIsNone(result['summary']['target_code_executed'])
        self.assertFalse(result['report']['repaired'])
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual(len(backend.reads), 2)
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual((self.h.repo / 'builder.py').read_bytes(), self.h.files['builder.py'])
        self.assertNotIn('DUMMY-PRIVATE-RAW-LOG-REFERENCE', json.dumps(result))
        registry = json.loads((self.h.root / 'repair-registry.json').read_bytes())
        self.assertEqual(registry['runs'][run['id']]['runtime'], {
            'schema': 'macr.workbench.repair-runtime.v1', 'remote_requested': False,
            'execution_enabled': True, 'spend_plan_sha256': None, 'spend_cell': None})
        owner.shutdown()
        self.h.repo.rename(self.h.base / 'moved-source')
        self.h.task_path.unlink()
        self.h.config_path.unlink()
        factory = Mock(side_effect=AssertionError('history_must_not_construct_backend'))
        reopened = self.h.owner(repair_execution_enabled=False, repair_verifier_factory=factory)
        again = reopened.repair_result(run['id'])
        self.assertEqual(again['report'], result['report'])
        self.assertEqual(again['proposals'], result['proposals'])
        factory.assert_not_called()
        self.assertEqual(len(backend.calls), 2)

    def test_enabled_missing_incapable_and_noncontract_backends_cannot_dispatch(self):
        incapable = FixtureBackend(self.names, capable=False)
        for verifier in (None, self.verifier(incapable)):
            with self.subTest(verifier=type(verifier).__name__):
                owner = self.h.owner(repair_execution_enabled=True, repair_verifier_factory=lambda: verifier)
                run = self.h.finish(owner, self.h.payload())
                self.assertEqual((run['state'], run['repair_status']), ('blocked', 'verification_unavailable'))
                self.assertEqual(run['summary']['provider_requests'], 0)
                self.assertFalse(owner.repair_result(run['id'])['report']['repaired'])
                owner.shutdown()
        self.assertEqual((incapable.calls, incapable.reads), ([], []))
        class DerivedVerifier(loop.BackendVerifier):
            def available(self):
                raise AssertionError('subclass_must_not_probe')
        for verifier in (object(), DerivedVerifier(incapable, incapable.public_result,
                backend_id='authored-derived', execution_profile='authored-public-v1')):
            with self.subTest(invalid_verifier=type(verifier).__name__):
                owner = self.h.owner(repair_execution_enabled=True, repair_verifier_factory=lambda: verifier)
                run = self.h.finish(owner, self.h.payload())
                self.assertEqual((run['state'], run['repair_status']), ('failed', 'rejected'))
                self.assertEqual(run['summary']['error'], 'repair_runtime_forbidden')
                owner.shutdown()
        self.assertEqual((incapable.calls, incapable.reads), ([], []))

    def test_direct_self_review_never_constructs_backend(self):
        factory = Mock(side_effect=AssertionError('direct_must_not_construct_backend'))
        owner = self.h.owner(repair_execution_enabled=True, repair_verifier_factory=factory)
        run = self.h.finish(owner, self.h.payload('direct_api'))
        self.assertEqual((run['state'], run['repair_status']), ('blocked', 'provider_disabled'))
        self.assertEqual(run['summary']['provider_requests'], 0)
        self.assertFalse(run['summary']['target_code_executed'])
        factory.assert_not_called()

    def test_backend_unknown_quarantines_with_zero_model_requests_across_restart(self):
        backend = FixtureBackend(self.names, ('raise',))
        owner = self.h.owner(repair_execution_enabled=True,
            repair_verifier_factory=lambda: self.verifier(backend))
        run = self.h.finish(owner, self.h.payload())
        self.assertEqual((run['state'], run['repair_status']), ('unknown', 'verification_outcome_unknown'))
        self.assertEqual(run['summary']['provider_requests'], 0)
        self.assertEqual(run['summary']['known_usd'], '0.000000')
        self.assertEqual(run['summary']['unknown_reserved_usd'], '0.000000')
        result = owner.repair_result(run['id'])
        self.assertEqual(result['report']['checks'][0]['result']['execution_status'], 'outcome_unknown')
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(backend.reads, [])
        self.assertNotIn('DUMMY-PRIVATE-ERROR-MUST-NOT-PERSIST', json.dumps(result))
        with self.assertRaises(ContractError) as caught:
            owner.start_repair(self.h.payload())
        self.assertEqual(caught.exception.code, 'repair_outcome_unknown')
        owner.shutdown()
        factory = Mock(side_effect=AssertionError('must_not_resume'))
        reopened = self.h.owner(repair_verifier_factory=factory)
        self.assertEqual(reopened.repair_result(run['id'])['state']['state'], 'unknown')
        with self.assertRaises(ContractError):
            reopened.start_repair(self.h.payload())
        factory.assert_not_called()
        self.assertEqual(len(backend.calls), 1)

    def test_legacy_run_without_runtime_remains_disabled_and_readable(self):
        owner = self.h.owner()
        run = self.h.finish(owner, self.h.payload())
        owner.shutdown()
        path = self.h.root / 'repair-registry.json'
        registry = json.loads(path.read_bytes())
        registry['runs'][run['id']].pop('runtime', None)
        path.write_text(json.dumps(registry))
        factory = Mock(side_effect=AssertionError('legacy_history_must_not_dispatch'))
        reopened = self.h.owner(repair_verifier_factory=factory)
        result = reopened.repair_result(run['id'])
        self.assertEqual(result['report']['status'], 'verification_unavailable')
        self.assertFalse(result['report']['target_code_executed'])
        self.assertEqual(result['summary']['provider_requests'], 0)
        factory.assert_not_called()

    def test_missing_or_disabled_runtime_cannot_adopt_a_backend_report(self):
        backend = FixtureBackend(self.names, ('error',))
        owner = self.h.owner(repair_execution_enabled=True,
            repair_verifier_factory=lambda: self.verifier(backend))
        run = self.h.finish(owner, self.h.payload())
        self.assertEqual(run['repair_status'], 'verification_failed')
        owner.shutdown()
        path = self.h.root / 'repair-registry.json'
        original = json.loads(path.read_bytes())
        for mutation in ('remove', 'disable'):
            with self.subTest(mutation=mutation):
                registry = json.loads(json.dumps(original))
                if mutation == 'remove':
                    registry['runs'][run['id']].pop('runtime')
                else:
                    registry['runs'][run['id']]['runtime']['execution_enabled'] = False
                path.write_text(json.dumps(registry))
                reopened = None
                try:
                    with self.assertRaises(ContractError):
                        reopened = self.h.owner()
                        reopened.repair_result(run['id'])
                finally:
                    if reopened is not None:
                        reopened.shutdown()
        path.write_text(json.dumps(original))
        self.assertEqual(len(backend.calls), 1)
