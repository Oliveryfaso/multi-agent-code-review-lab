"""Owned product repair fixtures; source strings are data, never executed."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from macr.api.evidence_workbench import Workbench
from macr.investigation import repair_loop as loop
from macr.investigation.validation import ContractError
from macr.providers.base import ProviderError
from macr.providers.repair_binding import RepairProviderBinding, development_config
from repair_fixtures import SOURCES, OLD, NEW


class AuthoredTransport:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        wire = json.loads(body)
        self.requests.append(wire)
        view = json.loads(wire['messages'][1]['content'])
        proposal = {key: view[key] for key in ('base_content_id', 'hypothesis_id', 'evidence_ids')}
        if view.get('proposal_contract') == 'v6':
            proposal['selected_content_id'] = None
        reply = self.replies.pop(0)
        proposal.update(reply(view) if callable(reply) else reply)
        return {'id': 'authored-fixture', 'model': wire['model'], 'system_fingerprint': 'authored-fixture',
                'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant',
                    'content': json.dumps(proposal), 'reasoning_content': 'authored fixture'}}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}}


class WorkbenchRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='macr-repair-web-', dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / 'source'
        self.repo.mkdir()
        self.files = {name: text.encode() for name, text in SOURCES.items()}
        for name, body in self.files.items():
            (self.repo / name).write_bytes(body)
        self.task = {'schema': 'macr.repair.task.v1', 'task_id': 'authored-invoice',
                     'issue': 'Delivery must not be discounted.', 'source_files': loop._hashes(self.files),
                     'editable_paths': ['builder.py'], 'test_paths': ['checks.py']}
        self.task_path, self.config_path = self.base / 'task.json', self.base / 'model.json'
        self.task_path.write_text(json.dumps(self.task))
        self.config_path.write_text(json.dumps(development_config()))
        self.root = self.base / 'owned'

    def owner(self, **kwargs):
        owner = Workbench(self.root, **kwargs)
        self.addCleanup(owner.shutdown)
        return owner

    def payload(self, strategy='api_debugger'):
        return {'intent_id': str(uuid4()), 'repo': str(self.repo), 'task': str(self.task_path),
                'model_config': str(self.config_path), 'strategy': strategy, 'proposal_contract': 'v6'}

    def finish(self, owner, payload):
        run = owner.start_repair(payload)
        owner.thread.join(5)
        self.assertFalse(owner.thread.is_alive())
        return owner.repair_state(run['id'])

    def observation(self, files, failed=()):
        names = loop.test_manifest(files, ['checks.py'])
        return {'source_id': loop.source_id(files), 'execution_status': 'completed',
                'outcomes': {name: 'failed' if name in failed else 'passed' for name in names}}

    def candidate(self):
        return {'status': 'candidate', 'summary': 'Exclude delivery from the discount.',
                'edits': [{'file': 'builder.py', 'old': OLD, 'new': NEW}]}

    def test_default_real_entry_retains_honest_blocked_results_and_reopens_without_target(self):
        owner = self.owner()
        for strategy, status in (('api_debugger', 'verification_unavailable'), ('direct_api', 'provider_disabled')):
            run = self.finish(owner, self.payload(strategy))
            self.assertEqual((run['state'], run['repair_status']), ('blocked', status))
            result = owner.repair_result(run['id'])
            self.assertEqual(result['summary']['provider_requests'], 0)
            self.assertFalse(result['summary']['remote_enabled'])
            self.assertFalse(result['report']['repaired'])
            self.assertFalse(result['report']['target_code_executed'])
            self.assertEqual(result['proposals'], [])
        owner.shutdown()
        self.repo.rename(self.base / 'moved-source')
        self.task_path.unlink()
        self.config_path.unlink()
        reopened = self.owner()
        self.assertEqual(reopened.repair_result(run['id'])['report']['status'], 'provider_disabled')
        self.assertEqual(len(reopened.repair_history()['items']), 2)

    def test_real_loop_uses_authored_transport_and_replay_then_returns_verified_edits(self):
        transport = AuthoredTransport([self.candidate()])
        names = loop.test_manifest(self.files, ['checks.py'])
        fixed = dict(self.files)
        fixed['builder.py'] = fixed['builder.py'].replace(OLD.encode(), NEW.encode())
        replay = loop.ReplayVerifier([self.observation(self.files, names[:1]), self.observation(fixed)])
        owner = self.owner(repair_provider_factory=lambda config: RepairProviderBinding(config, transport=transport),
                           repair_verifier_factory=lambda: replay)
        run = self.finish(owner, self.payload())
        result = owner.repair_result(run['id'])
        self.assertEqual(result['report']['status'], 'simulation_passed')
        self.assertTrue(result['report']['simulation'])
        self.assertFalse(result['report']['repaired'])
        self.assertEqual(result['proposals'][0]['edits'], self.candidate()['edits'])
        self.assertEqual(result['proposals'][0]['candidate_source_id'], result['report']['rounds'][0]['candidate_source_id'])
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual((self.repo / 'builder.py').read_bytes(), self.files['builder.py'])

    def test_candidate_selection_keeps_not_rerun_status(self):
        def select(view):
            return {'status': 'select_candidate', 'summary': 'Retain the accepted source.', 'edits': [],
                    'selected_content_id': view['attempt_history']['entries'][0]['candidate_content_id']}
        transport = AuthoredTransport([self.candidate(), select])
        owner = self.owner(repair_provider_factory=lambda config: RepairProviderBinding(config, transport=transport))
        run = self.finish(owner, self.payload('direct_api'))
        result = owner.repair_result(run['id'])
        self.assertEqual(result['report']['status'], 'candidate_selected_unverified')
        self.assertEqual(result['summary']['final_verification_status'], 'not_rerun')
        self.assertEqual(result['proposals'][1]['status'], 'select_candidate')
        self.assertEqual(result['report']['final_selection']['accepted_round'], 1)
        owner.shutdown()
        registry = self.root / 'repair-registry.json'
        original = json.loads(registry.read_bytes())
        self.repo.rename(self.base / 'moved-source')
        self.task_path.unlink()
        self.config_path.unlink()
        for field in ('selected_content_id', 'final_verification_status', 'verification_origin',
                      'historical_verification_origin', 'candidate_origin'):
            for mutation in ('remove', 'change'):
                with self.subTest(field=field, mutation=mutation):
                    tampered = json.loads(json.dumps(original))
                    summary = tampered['runs'][run['id']]['summary']
                    if mutation == 'remove':
                        del summary[field]
                    else:
                        summary[field] = 'tampered'
                    registry.write_text(json.dumps(tampered))
                    reopened = self.owner()
                    try:
                        with self.assertRaises(ContractError) as caught:
                            reopened.repair_result(run['id'])
                        self.assertEqual(caught.exception.code, 'history_corrupt')
                    finally:
                        reopened.shutdown()
        registry.write_text(json.dumps(original))
        reopened = self.owner()
        self.assertEqual(reopened.repair_result(run['id'])['summary']['final_verification_status'], 'not_rerun')

    def test_duplicate_intent_is_one_dispatch_and_changed_payload_conflicts(self):
        entered, release = threading.Event(), threading.Event()
        def provider(config):
            entered.set()
            if not release.wait(3):
                raise RuntimeError('authored_fixture_deadline')
            return RepairProviderBinding(config)
        owner = self.owner(repair_provider_factory=provider)
        payload = self.payload('direct_api')
        run = owner.start_repair(payload)
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(2))
        self.assertEqual(owner.start_repair(payload)['id'], run['id'])
        self.assertEqual(owner.repair_by_intent(payload['intent_id'])['id'], run['id'])
        with self.assertRaises(ContractError) as caught:
            owner.start_repair(dict(payload, strategy='api_debugger'))
        self.assertEqual(caught.exception.code, 'intent_conflict')
        with self.assertRaises(ContractError) as caught:
            owner.start({'intent_id': str(uuid4()), 'repo': str(self.repo), 'question': 'Read source',
                         'entries': ['builder.py'], 'symbols': [], 'traceback': '', 'profile_name': 'standard'})
        self.assertEqual(caught.exception.code, 'workbench_busy')
        release.set()
        owner.thread.join(5)
        self.assertEqual(len(owner.repair_history()['items']), 1)

    def test_request_cannot_grant_runtime_or_choose_an_output(self):
        owner = self.owner()
        for field, value in (('enable_remote', True), ('backend', 'host'), ('key', 'dummy'),
                             ('out', str(self.base / 'other')), ('spend_plan', '/plan')):
            with self.assertRaises(ContractError):
                owner.start_repair(self.payload() | {field: value})
        with self.assertRaises(ContractError):
            owner.start_repair(dict(self.payload(), repo=str(self.root)))
        self.assertEqual(owner.repair_history()['items'], [])
        owner.repairs.verifier_factory = lambda: object()
        run = self.finish(owner, self.payload())
        self.assertEqual((run['state'], run['repair_status']), ('failed', 'rejected'))
        self.assertEqual(run['summary']['provider_requests'], 0)
        self.assertFalse((self.root / 'repairs' / run['id']).exists())

    def test_result_corruption_is_withheld_and_never_regenerated(self):
        owner = self.owner()
        run = self.finish(owner, self.payload())
        output = self.root / 'repairs' / run['id']
        original = (output / 'report.json').read_bytes()
        (output / 'report.json').write_bytes(original + b' ')
        with self.assertRaises(ContractError):
            owner.repair_result(run['id'])
        self.assertEqual(owner.repair_state(run['id'])['state'], 'failed')
        self.assertEqual((output / 'report.json').read_bytes(), original + b' ')

    def test_incomplete_saved_run_is_not_restarted_and_blocks_admission(self):
        owner = self.owner()
        run = self.finish(owner, self.payload())
        owner.shutdown()
        registry = self.root / 'repair-registry.json'
        saved = json.loads(registry.read_bytes())
        saved['runs'][run['id']].update(state='running', binding=None, summary=None, exit_code=None,
                                       repair_status=None, reservation=1024)
        registry.write_text(json.dumps(saved))
        reopened = self.owner()
        self.assertEqual(reopened.repair_state(run['id'])['state'], 'unknown')
        with self.assertRaises(ContractError) as caught:
            reopened.start_repair(self.payload())
        self.assertEqual(caught.exception.code, 'closure_unknown')

    def test_repair_reservation_and_metadata_share_parent_limits(self):
        owner = self.owner()
        with patch.object(owner, '_allocation', return_value=2 * 1024 * 1024 * 1024):
            with self.assertRaises(ContractError) as caught:
                owner.start_repair(self.payload())
        self.assertEqual(caught.exception.code, 'workbench_storage_limit')
        run = self.finish(owner, self.payload())
        repair_bytes = (self.root / 'repair-registry.json').stat().st_size
        self.assertGreaterEqual(owner._metadata_bytes(), repair_bytes)
        self.assertTrue(owner.repair_state(run['id'])['result_available'])

    def test_invalid_proposal_is_not_exposed_as_an_accepted_edit(self):
        reply = self.candidate()
        reply['edits'][0]['file'] = 'checks.py'
        transport = AuthoredTransport([reply])
        owner = self.owner(repair_provider_factory=lambda config: RepairProviderBinding(config, transport=transport))
        run = self.finish(owner, self.payload('direct_api'))
        result = owner.repair_result(run['id'])
        self.assertEqual(result['report']['status'], 'proposal_invalid')
        self.assertEqual(result['state']['state'], 'failed')
        self.assertTrue(result['state']['result_available'])
        self.assertEqual(result['proposals'], [])
        class UncertainTransport:
            def post(self, *args):
                raise ProviderError('authored_transport_unknown')
        owner.repairs.provider_factory = lambda config: RepairProviderBinding(config, transport=UncertainTransport())
        unknown = self.finish(owner, self.payload('direct_api'))
        self.assertEqual((unknown['state'], unknown['repair_status']), ('unknown', 'provider_outcome_unknown'))
        self.assertNotEqual(unknown['summary']['unknown_reserved_usd'], '0.000000')
        self.assertTrue(owner.repair_result(unknown['id'])['state']['result_available'])
        with self.assertRaises(ContractError) as caught:
            owner.start_repair(self.payload())
        self.assertEqual(caught.exception.code, 'repair_outcome_unknown')
        owner.shutdown()
        reopened = self.owner()
        self.assertEqual(reopened.repair_result(unknown['id'])['state']['state'], 'unknown')
        with self.assertRaises(ContractError):
            reopened.start_repair(self.payload())
