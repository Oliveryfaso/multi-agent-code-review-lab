"""Actual snapshot/index/capsule paths with literal offline model envelopes."""
import copy
import importlib
import json
import unittest
from dataclasses import asdict
from unittest.mock import patch

from debugger_fixtures import fixture_root, write_files
from macr.evals.diagnostic_compact import restore_scope, validate_candidate_context
from macr.investigation.index import RepositoryIndex
from macr.investigation.records import ScopeProfile
from macr.investigation.scope import RepositoryScope
from macr.investigation.snapshots import SnapshotStore
from macr.investigation.validation import ContractError
from macr.providers.base import ModelBudget, ModelRequest
from macr.providers.deepseek import DeepSeekProvider

try:
    packet = importlib.import_module('macr.investigation.diagnostic_packet')
except ModuleNotFoundError:
    packet = None

FILES = {
    'entry.py': "raise RuntimeError('source must never run')\nfrom policy import allow as permitted\ndef start():\n    return permitted(staff=True, paid=False)\n",
    'policy.py': 'def allow(*, staff, paid):\n    return staff or paid\n',
}
BUDGET = ModelBudget(8192, 2048, 30)


def reply(content, *, finish='stop'):
    return {'model': 'deepseek-flash', 'system_fingerprint': 'fp_offline',
        'choices': [{'finish_reason': finish, 'message': {'role': 'assistant',
            'content': content, 'reasoning_content': 'literal offline reasoning'}}],
        'usage': {'prompt_tokens': 100, 'completion_tokens': 40, 'total_tokens': 140,
            'prompt_cache_hit_tokens': 50, 'prompt_cache_miss_tokens': 50,
            'completion_tokens_details': {'reasoning_tokens': 20}}}


class DiagnosticPacketTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(packet, 'static diagnostic packet core is not implemented')
        for name in ('subprocess.Popen', 'socket.create_connection', 'urllib.request.build_opener'):
            guard = patch(name, side_effect=AssertionError('offline path must not execute or contact a service'))
            guard.start(); self.addCleanup(guard.stop)

    def capture(self, files=None):
        root = fixture_root()
        repo = write_files(fixture_root(), FILES if files is None else files)
        store = SnapshotStore(root / 'snapshots', allowed_root=root)
        bundle = store.capture(repo, RepositoryScope().inventory(repo, ScopeProfile()))
        return bundle, RepositoryIndex(store, root / 'index', allowed_root=root)

    def options(self, **changes):
        return {'entries': ['entry.py:start'], 'question': 'Does start satisfy the supplied access policy?',
            'scenario': 'Access requires staff and paid both true.', 'assumptions': [],
            'budget': BUDGET} | changes

    def prepare(self, files=None, **changes):
        bundle, index = self.capture(files)
        return packet.prepare_pair(bundle, index, **self.options(**changes)), bundle, index

    def answer(self, prepared, **changes):
        scope = prepared['scope']
        return {'verdict': 'supported_defect', 'cause': 'The supplied OR permits a mixed boolean pair.',
            'when': 'start supplies staff=True and paid=False.', 'refs': [scope['source'][0]['id']],
            'unknowns': [], 'scope': scope['scope_sha256'][:16]} | changes

    def test_real_cross_file_binding_stable_capsule_and_identical_full_source(self):
        prepared, bundle, _ = self.prepare()
        again, other_bundle, _ = self.prepare()
        self.assertNotEqual(bundle.ref.snapshot_id, other_bundle.ref.snapshot_id)
        self.assertEqual(prepared['packet_sha256'], again['packet_sha256'])
        self.assertEqual(prepared['scope'], again['scope'])
        bindings = prepared['packet']['bindings']
        self.assertEqual(len(bindings), 1)
        self.assertEqual([a['literal']['value'] for a in bindings[0]['arguments']], [True, False])
        self.assertFalse(prepared['packet']['data_flow_proven'])
        self.assertFalse(prepared['packet']['repository_code_executed'])
        self.assertNotIn(bundle.ref.snapshot_id, json.dumps(prepared['packet']))
        self.assertNotIn(str(bundle.ref.root_ref), json.dumps(prepared['requests']))
        direct, evidence = [prepared['requests'][name] for name in ('direct', 'evidence')]
        self.assertEqual(direct['messages'][-1], evidence['messages'][-1])
        self.assertEqual(direct['response_schema'], evidence['response_schema'])
        self.assertEqual(direct['messages'][0], evidence['messages'][0])
        self.assertEqual(len(evidence['messages']), len(direct['messages']) + 1)
        payload = json.loads(direct['messages'][-1]['content'])
        self.assertEqual({s['file']: s['body'] for s in payload['source']}, FILES)
        for request in (direct, evidence):
            validate_candidate_context(ModelRequest(**request), restore_scope(prepared['scope']))
        self.assertGreaterEqual(prepared['timing_seconds']['source_scope'], 0)
        for name in ('index', 'collect', 'normalize', 'preparation_total'):
            self.assertIn(name, prepared['timing_seconds'])

    def test_task_and_budget_changes_bind_scope_without_modifying_raw_source(self):
        original, _, _ = self.prepare()
        for change in ({'question': 'A different requested property?'}, {'scenario': 'Different policy.'},
                       {'assumptions': ['One extra explicit assumption.']},
                       {'budget': ModelBudget(8192, 1024, 30)}):
            changed, _, _ = self.prepare(**change)
            self.assertNotEqual(changed['scope']['scope_sha256'], original['scope']['scope_sha256'])
            self.assertEqual(changed['initial_source_sha256'], original['initial_source_sha256'])

    def test_missing_rebound_and_truncated_facts_keep_full_source_and_unknowns(self):
        missing, _, _ = self.prepare(entries=['entry.py:absent'])
        self.assertTrue(missing['packet']['selection_gaps'])
        self.assertEqual(missing['packet']['bindings'], [])
        rebound = FILES | {'entry.py': 'from policy import allow\nallow = lambda **kwargs: True\ndef start():\n    return allow(staff=True, paid=False)\n'}
        changed, _, _ = self.prepare(rebound)
        self.assertTrue(changed['packet']['unknowns'])
        self.assertEqual(changed['packet']['bindings'], [])
        large = FILES | {'entry.py': 'from policy import allow\ndef start():\n    """' + 'x' * 1500 + '"""\n    return allow(staff=True, paid=False)\n'}
        limited, _, _ = self.prepare(large)
        self.assertTrue(limited['packet']['truncations'])
        self.assertEqual(limited['packet']['facts'], [])
        self.assertEqual(limited['packet']['bindings'], [])
        source = json.loads(limited['requests']['direct']['messages'][-1]['content'])['source']
        self.assertEqual({s['file']: s['body'] for s in source}, large)

    def test_input_and_complete_source_caps_fail_without_silent_omission(self):
        for files in ({f'f{i}.py': 'x = 1\n' for i in range(13)},
                      {'entry.py': '#' * 16385}, {'entry.py': ''}):
            with self.subTest(kind=len(files)), self.assertRaises(ContractError):
                self.prepare(files)
        for changes in ({'question': 'x' * 4097}, {'scenario': 'x' * 4097},
                        {'assumptions': ['x'] * 13}, {'entries': ['entry.py:start'] * 7},
                        {'entries': ['../entry.py:start']}, {'budget': asdict(BUDGET)}):
            with self.subTest(changes=list(changes)), self.assertRaises(ContractError):
                self.prepare(**changes)

    def test_literal_converse_v3_accepts_long_text_without_success_grade_or_live_calls(self):
        prepared, bundle, index = self.prepare()
        content = json.dumps(self.answer(prepared, cause='c' * 701, when='w' * 351))
        replies = {'direct': reply(content), 'evidence': reply(content)}
        original = copy.deepcopy(replies)
        result = packet.run_offline_pair(bundle, index, **self.options(), replies=replies)
        self.assertEqual((result['planned_cells'], result['fixture_model_calls'], result['model_calls_live']), (2, 2, 0))
        self.assertEqual(replies, original)
        self.assertEqual(result['provenance'], 'scripted_offline_fixture')
        self.assertEqual([row['status'] for row in result['rows']], ['semantic_pending'] * 2)
        for row in result['rows']:
            self.assertEqual(row['request']['thinking'], {'type': 'enabled'})
            self.assertEqual(row['request']['reasoning_effort'], 'high')
            self.assertEqual(row['response']['raw_usage']['completion_tokens_details']['reasoning_tokens'], 20)
            self.assertEqual(row['response']['system_fingerprint'], 'fp_offline')
            self.assertEqual(len(row['answer']['cause']), 701)
            self.assertEqual(row['answer']['cause'], 'c' * 701)
            self.assertIn('quality_warnings', row)
            self.assertIn({'field': 'cause', 'actual_chars': 701, 'preferred_chars': 160},
                          row['quality_warnings'])

    def test_forged_scope_refs_duplicate_fields_and_oversize_are_failures_not_repairs(self):
        prepared, bundle, index = self.prepare()
        good = self.answer(prepared)
        variants = [json.dumps(good | {'scope': '0' * 16}), json.dumps(good | {'refs': ['forged']}),
                    json.dumps(good)[:-1] + ',"scope":"duplicate"}',
                    json.dumps(good | {'cause': 'x' * 8192})]
        for raw in variants:
            result = packet.run_offline_pair(bundle, index, **self.options(),
                replies={'direct': reply(raw), 'evidence': reply(json.dumps(good))})
            self.assertEqual(len(result['rows']), 2)
            self.assertEqual(result['rows'][0]['status'], 'failed')
            self.assertIsNone(result['rows'][0]['answer'])
            self.assertEqual(result['rows'][0]['quality_warnings'], [])
            self.assertEqual(result['rows'][1]['status'], 'semantic_pending')
            self.assertEqual(result['fixture_model_calls'], 2)

    def test_failure_preserves_billing_identity_and_still_accounts_two_arms(self):
        prepared, bundle, index = self.prepare()
        incomplete = reply('', finish='length')
        incomplete['usage'].update(completion_tokens=2048, total_tokens=2148)
        incomplete['usage']['completion_tokens_details']['reasoning_tokens'] = 2048
        result = packet.run_offline_pair(bundle, index, **self.options(),
            replies={'direct': incomplete, 'evidence': reply(json.dumps(self.answer(prepared)))})
        self.assertEqual(result['rows'][0]['status'], 'failed')
        self.assertEqual(result['rows'][0]['error_code'], 'model_output_incomplete')
        self.assertEqual(result['rows'][0]['response']['model'], 'deepseek-flash')
        self.assertEqual(result['rows'][0]['response']['raw_usage']['completion_tokens'], 2048)
        self.assertEqual(result['rows'][1]['status'], 'semantic_pending')
        self.assertEqual(result['fixture_model_calls'], 2)

    def test_no_arbitrary_transport_replies_or_order_and_snapshot_drift_before_dispatch(self):
        prepared, bundle, index = self.prepare()
        good = reply(json.dumps(self.answer(prepared)))
        for replies, order in [({'direct': lambda: good, 'evidence': good}, ('direct', 'evidence')),
                               ({'direct': [good], 'evidence': good}, ('direct', 'evidence')),
                               ({'direct': good, 'evidence': good}, ('direct', 'direct'))]:
            with self.assertRaises(ContractError):
                packet.run_offline_pair(bundle, index, **self.options(), replies=replies, order=order)
        captured = __import__('pathlib').Path(bundle.ref.root_ref) / 'policy.py'
        captured.write_text('def allow(**kwargs):\n    return True\n')
        with self.assertRaises(ContractError), patch.object(DeepSeekProvider, 'converse', side_effect=AssertionError('drift dispatched')):
            packet.run_offline_pair(bundle, index, **self.options(), replies={'direct': good, 'evidence': good})

    def test_invalid_unicode_stays_fixed_contract_error_before_any_model_call(self):
        for changes in ({'assumptions': ['\ud800']}, {'entries': ['entry.py:\ud800']}):
            try:
                self.prepare(**changes)
            except Exception as error:
                self.assertIsInstance(error, ContractError)
            else:
                self.fail('invalid Unicode input was accepted')
        with self.assertRaises(ContractError):
            self.prepare(assumptions=['\x00' * 4096] * 3)

    def test_drift_after_response_and_unexpected_tools_preserve_two_cell_failures(self):
        prepared, bundle, index = self.prepare()
        content = json.dumps(self.answer(prepared))
        tool_reply = reply(None, finish='tool_calls')
        tool_reply['choices'][0]['message']['tool_calls'] = [{'id': 'call_1', 'type': 'function',
            'function': {'name': 'finish', 'arguments': '{}'}}]
        result = packet.run_offline_pair(bundle, index, **self.options(),
            replies={'direct': tool_reply, 'evidence': reply(content)})
        self.assertEqual([r['status'] for r in result['rows']], ['failed', 'semantic_pending'])
        original = DeepSeekProvider.converse
        def mutate_after_response(provider, request):
            response = original(provider, request)
            (__import__('pathlib').Path(bundle.ref.root_ref) / 'policy.py').write_text('changed bytes\n')
            return response
        with patch.object(DeepSeekProvider, 'converse', mutate_after_response):
            result = packet.run_offline_pair(bundle, index, **self.options(),
                replies={'direct': reply(content), 'evidence': reply(content)})
        self.assertEqual([r['status'] for r in result['rows']], ['failed', 'not_attempted'])
        self.assertIsNone(result['rows'][0]['answer'])
        self.assertEqual(result['rows'][0]['response']['model'], 'deepseek-flash')
        self.assertEqual((result['planned_cells'], result['fixture_model_calls']), (2, 1))
        self.assertTrue(result['halted'])

    def test_second_arm_wire_preflight_failure_keeps_completed_first_response(self):
        from macr.providers.base import ConversationRequest
        assumptions = ['é' * 2000] * 5
        prepared, bundle, index = self.prepare(assumptions=assumptions)
        provider = DeepSeekProvider(model='deepseek-flash', allow_remote=False)
        sizes = {arm: len(provider.encode_conversation_request(ConversationRequest(
            **prepared['requests'][arm], thinking={'type': 'enabled'}, reasoning_effort='high')))
            for arm in ('direct', 'evidence')}
        self.assertLessEqual(sizes['direct'], 65536, sizes)
        self.assertGreater(sizes['evidence'], 65536, sizes)
        good = reply(json.dumps(self.answer(prepared)))
        try:
            result = packet.run_offline_pair(bundle, index, **self.options(assumptions=assumptions),
                replies={'direct': good, 'evidence': good})
        except ContractError as error:
            self.fail(f'second preflight discarded completed response: {error.code}')
        self.assertEqual([r['status'] for r in result['rows']], ['semantic_pending', 'failed'])
        self.assertEqual(result['rows'][1]['error_code'], 'diagnostic_packet_request_limit')
        self.assertEqual(result['rows'][1]['fixture_model_calls'], 0)
        self.assertEqual(result['rows'][0]['response']['raw_usage']['prompt_tokens'], 100)
        self.assertEqual((result['planned_cells'], result['fixture_model_calls']), (2, 1))
