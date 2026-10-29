"""Scripted contract fixtures, never unseen cases or live model calls."""
import importlib.util
import json
from pathlib import Path
import unittest

from debugger_fixtures import fixture_root
from macr.evals.deepseek_policy_manifest import DEFAULT_BUDGET, prepare_policy_manifest
from macr.evals.deepseek_policy_runner import PolicyRunError
from macr.providers.deepseek import DeepSeekProvider
from test_deepseek_policy_runner import MODEL, UNKNOWN, FakeTransport, call, reply


class StudyRunnerTests(unittest.TestCase):
    def setUp(self):
        self.root = fixture_root()
        source = self.root / 'snapshots' / 'fixture'
        source.mkdir(parents=True)
        (source / 'entry.py').write_text('from helper import twice\ndef run(value):\n    return twice(value)\n')
        (source / 'helper.py').write_text('def twice(value):\n    return value * 2\n')
        self.case = {'id': 'contract-fixture', 'snapshot_root': 'snapshots/fixture',
            'question': 'What does the authorized function imply?', 'scenario': 'Integer inputs.',
            'assumptions': ['Only inspect static Python source.'],
            'authorized_files': ['entry.py', 'helper.py'], 'initial_evidence_files': ['entry.py'],
            'stratum': 'retrieval_required', 'evidence_parity': 'unequal_by_design',
            'entrypoint': {'file': 'entry.py', 'symbol': 'run'}}
        self.manifest_dir = self.root / 'manifest'
        self.manifest = prepare_policy_manifest(self.case, dataset_root=self.root,
            out=self.manifest_dir, model=MODEL)

    def plan(self):
        from macr.evals.deepseek_study_plan import build_fixed_plan
        return build_fixed_plan(self.manifest, self.root)

    def run_cell(self, strategy, transport, *, name='run', **callbacks):
        from macr.evals.deepseek_study_runner import run_study_cell
        return run_study_cell(self.manifest_dir, dataset_root=self.root,
            strategy=strategy, provider=DeepSeekProvider(model=MODEL['model_id'], transport=transport),
            out=self.root / name, fixed_plan=self.plan(), **callbacks)

    def test_runner_entry_exists(self):
        self.assertIsNotNone(importlib.util.find_spec('macr.evals.deepseek_study_runner'))

    def test_private_core_keeps_approved_fake_provenance_and_public_fixture_guard(self):
        from macr.evals import deepseek_study_runner as runner
        self.assertTrue(callable(getattr(runner, '_run_study_cell_core', None)))
        transport = FakeTransport(reply(None, calls=[call('end', 'finish', {'diagnosis': UNKNOWN})]))
        provider = DeepSeekProvider(model=MODEL['model_id'], transport=transport, allow_remote=True)
        with self.assertRaisesRegex(PolicyRunError, 'live_dispatch_disabled'):
            runner.run_study_cell(self.manifest_dir, dataset_root=self.root,
                strategy='adaptive_read_3', provider=provider, out=self.root/'public', fixed_plan=self.plan())
        result = runner._run_study_cell_core(self.manifest_dir, dataset_root=self.root,
            strategy='adaptive_read_3', provider=provider, out=self.root/'core', fixed_plan=self.plan(),
            provenance='approved_live_fake')
        self.assertEqual(result['provenance'], 'approved_live_fake')
        self.assertEqual(result['model_calls_live'], 0)
        self.assertEqual(result['status'], 'semantic_pending')

    def test_initial_evidence_is_byte_identical_and_every_request_cap_is_2000(self):
        initial_messages = []
        hashes = []
        for number, strategy in enumerate(('direct_initial_3', 'fixed_read_3', 'adaptive_read_3')):
            responses = ([reply(None, calls=[call('end', 'finish', {'diagnosis': UNKNOWN})])]
                if strategy == 'adaptive_read_3' else [reply(json.dumps(UNKNOWN)) for _ in range(3)])
            transport = FakeTransport(*responses)
            result = self.run_cell(strategy, transport, name='parity-' + str(number))
            initial_messages.append(transport.posts[0]['messages'][1]['content'])
            hashes.append(result['initial_evidence_sha256'])
            self.assertEqual(result['status'], 'semantic_pending')
            self.assertTrue(all(row['max_tokens'] == 2000 for row in transport.posts))
            self.assertEqual(result['model_calls_live'], 0)
        self.assertEqual(len(set(initial_messages)), 1)
        self.assertEqual(len(set(hashes)), 1)
        payload = json.loads(initial_messages[0])
        self.assertEqual(payload['authorized_files'], ['entry.py', 'helper.py'])
        self.assertNotIn('helper.py', [row['file'] for row in payload['source']])

    def test_direct_three_steps_preserve_reasoning_and_use_final_without_selection(self):
        first = {'verdict': 'no_defect_supported', 'cause': 'Static fixture.',
            'when': 'All integer inputs.', 'refs': [{'file':'entry.py','start':1,'end':3}], 'unknowns': []}
        responses = [reply(json.dumps(first)), reply(json.dumps(UNKNOWN)), reply(json.dumps(UNKNOWN))]
        responses[0]['choices'][0]['message']['reasoning_content'] = 'Fixture reasoning history.'
        transport = FakeTransport(*responses)
        result = self.run_cell('direct_initial_3', transport)
        self.assertEqual(result['requests_attempted'], 3)
        self.assertEqual(result['first_answer'], first)
        self.assertEqual(result['answer'], UNKNOWN)
        self.assertEqual(result['review_status'], 'pending')
        self.assertFalse(result['semantic_proof'])
        self.assertEqual(result['tool_reads'], 0)
        self.assertEqual(transport.posts[1]['messages'][2]['reasoning_content'], 'Fixture reasoning history.')
        self.assertIn('counterexamples', transport.posts[1]['messages'][-1]['content'])
        self.assertIn('final', transport.posts[2]['messages'][-1]['content'])
        self.assertTrue(all('tools' not in row for row in transport.posts))

    def test_full_reference_adds_authorized_source_without_replacing_e0(self):
        transport = FakeTransport(*[reply(json.dumps(UNKNOWN)) for _ in range(3)])
        result = self.run_cell('direct_full_3', transport)
        messages = transport.posts[0]['messages']
        self.assertNotIn('helper.py', [r['file'] for r in json.loads(messages[1]['content'])['source']])
        self.assertIn('helper.py', [r['file'] for r in json.loads(messages[2]['content'])['additional_source']])
        self.assertEqual(result['first_answer'], UNKNOWN)
        self.assertEqual(result['tool_reads'], 0)
        self.assertIn('Diagnosis JSON Schema:', messages[0]['content'])
        schema, _ = json.JSONDecoder().raw_decode(messages[0]['content'].split('Diagnosis JSON Schema:\n', 1)[1])
        self.assertEqual(schema['properties']['cause']['maxLength'], 600)
        self.assertEqual(schema['properties']['when']['maxLength'], 300)

    def test_fixed_reads_are_host_evidence_and_independent_of_model_answer(self):
        transport = FakeTransport(*[reply(json.dumps(UNKNOWN)) for _ in range(3)])
        result = self.run_cell('fixed_read_3', transport)
        self.assertEqual(result['tool_reads'], 1)
        event = result['tool_events'][0]
        self.assertEqual(event['name'], 'read_symbol')
        self.assertEqual(event['result']['tool'], 'read_symbol')
        self.assertTrue(event['result']['snippets'])
        second = transport.posts[1]['messages']
        self.assertFalse(any(row['role'] == 'tool' for row in second))
        evidence = [json.loads(row['content']) for row in second if row['role']=='user' and 'fixed_evidence' in row['content']]
        self.assertEqual(evidence[0]['fixed_evidence'], [event['result']])
        self.assertEqual(result['fixed_plan']['sha256'], self.plan()['sha256'])
        self.assertGreater(result['fixed_plan']['scanned_bytes'], 0)

    def test_invalid_first_answer_fails_cell_and_never_runs_self_repair(self):
        for i, strategy in enumerate(('direct_initial_3','direct_full_3','fixed_read_3')):
            transport = FakeTransport(reply(json.dumps(UNKNOWN | {'unknowns':['x'*201]})), reply(json.dumps(UNKNOWN)))
            result = self.run_cell(strategy, transport, name='invalid-'+str(i))
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['error_code'], 'eval_contract_invalid')
            self.assertEqual(len(transport.posts), 1)
            self.assertIsNone(result['answer'])
            self.assertIsNone(result['first_answer'])

    def test_adaptive_tool_history_and_terminal_on_third_decision(self):
        transport = FakeTransport(
            reply(None, calls=[call('read-1','search', {'query':'twice'})]),
            reply(None, calls=[call('read-2','read_symbol',{'file':'helper.py','symbol':'twice'})]),
            reply(None, calls=[call('finish-1','finish',{'diagnosis':UNKNOWN})]))
        result = self.run_cell('adaptive_read_3', transport)
        self.assertEqual((result['requests_attempted'],result['tool_reads']), (3,2))
        self.assertEqual(result['status'], 'semantic_pending')
        history = transport.posts[2]['messages']
        self.assertEqual(len([row for row in history if row['role']=='tool']), 2)
        self.assertTrue(all(row.get('reasoning_content') for row in history if row['role']=='assistant'))
        self.assertIsNone(result['first_answer'])
        self.assertNotIn('response_format', transport.posts[0])

    def test_third_read_fails_without_executing_it(self):
        transport = FakeTransport(*[reply(None,calls=[call('read-'+str(i),'search',{'query':str(i)})]) for i in range(3)])
        result = self.run_cell('adaptive_read_3', transport)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_code'], 'third_decision_must_terminate')
        self.assertEqual(result['tool_reads'], 2)

    def test_adaptive_duplicate_read_is_failure_without_repeated_read(self):
        transport = FakeTransport(reply(None,calls=[call('r1','search',{'query':'twice'})]),
            reply(None,calls=[call('r2','search',{'query':'twice'})]))
        result = self.run_cell('adaptive_read_3', transport)
        self.assertEqual(result['error_code'], 'duplicate_read_tool')
        self.assertEqual(result['tool_reads'], 1)

    def test_ask_user_is_pending_abstention_and_never_consults_human(self):
        transport = FakeTransport(reply(None,calls=[call('gap','ask_user',{'gap':'External allowed input policy is absent.'})]))
        result = self.run_cell('adaptive_read_3',transport)
        self.assertEqual(result['status'],'ask_user_terminated')
        self.assertEqual(result['review_status'],'pending')
        self.assertEqual(result['task_resolution'],'unresolved')
        self.assertEqual(result['requests_attempted'],1)

    def test_callbacks_observe_exact_hash_and_provider_response_even_contract_fails(self):
        seen = []
        transport = FakeTransport(reply('{broken'))
        result = self.run_cell('direct_initial_3',transport,
            admit=lambda request_hash: seen.append(('admit',request_hash)),
            settle=lambda request_hash, **kwargs: seen.append(('settle',request_hash,kwargs)))
        self.assertEqual([r[0] for r in seen], ['admit','settle'])
        self.assertEqual(seen[0][1],result['requests'][0]['request_sha256'])
        self.assertEqual(seen[0][1],seen[1][1])
        self.assertIsNotNone(seen[1][2]['response'])
        self.assertEqual(result['requests'][0]['response']['raw_usage']['prompt_tokens'],12)
        self.assertEqual(result['status'],'failed')

    def test_unknown_usage_stops_immediately_and_retains_evidence(self):
        response=reply(json.dumps(UNKNOWN)); response.pop('usage')
        transport=FakeTransport(response,reply(json.dumps(UNKNOWN)))
        result=self.run_cell('direct_initial_3',transport)
        self.assertEqual(result['status'],'remote_outcome_unknown')
        self.assertEqual(result['requests_attempted'],1)
        self.assertIsNotNone(result['requests'][0]['response'])
        self.assertTrue((self.root/'run'/'decision-01.response.json').exists())

    def test_transport_unknown_settles_error_once_and_never_retries(self):
        rows=[]
        transport=FakeTransport(TimeoutError(),reply(json.dumps(UNKNOWN)))
        result=self.run_cell('direct_initial_3',transport,
            settle=lambda request_hash, **kwargs: rows.append(kwargs))
        self.assertEqual(result['status'],'remote_outcome_unknown')
        self.assertEqual(len(transport.posts),1)
        self.assertEqual(len(rows),1)
        self.assertIsNotNone(rows[0]['error'])

    def test_remote_provider_and_wrong_model_reject_before_output(self):
        from macr.evals.deepseek_study_runner import run_study_cell
        for provider in (DeepSeekProvider(model=MODEL['model_id'],allow_remote=True),
                         DeepSeekProvider(model='other',transport=FakeTransport())):
            with self.assertRaises(PolicyRunError):
                run_study_cell(self.manifest_dir,dataset_root=self.root,
                    strategy='direct_initial_3',provider=provider,out=self.root/'forbidden',fixed_plan=self.plan())
            self.assertFalse((self.root/'forbidden').exists())

    def test_exclusive_output_prevents_second_dispatch(self):
        self.run_cell('direct_initial_3',FakeTransport(*[reply(json.dumps(UNKNOWN)) for _ in range(3)]))
        transport=FakeTransport(reply(json.dumps(UNKNOWN)))
        with self.assertRaises(PolicyRunError):
            self.run_cell('direct_initial_3',transport)
        self.assertEqual(transport.posts,[])

    def test_drift_and_measured_budget_overrun_stop_further_dispatch(self):
        for i,reason in enumerate(('fingerprint','model','input','output')):
            response=reply(json.dumps(UNKNOWN))
            if reason=='fingerprint': response['system_fingerprint']='different'
            if reason=='model': response['model']='other'
            if reason=='input': response['usage'].update(prompt_tokens=8001,prompt_cache_miss_tokens=8001)
            if reason=='output': response['usage']['completion_tokens']=2001
            answers=[reply(json.dumps(UNKNOWN)),response,reply(json.dumps(UNKNOWN))]
            transport=FakeTransport(*answers)
            result=self.run_cell('direct_initial_3',transport,name='drift-'+str(i))
            self.assertEqual(result['status'],'failed')
            self.assertEqual(len(transport.posts),2)


    def test_review_packet_retains_neutral_question_and_visible_source(self):
        transport = FakeTransport(*[reply(json.dumps(UNKNOWN)) for _ in range(3)])
        result = self.run_cell('direct_full_3', transport)
        self.assertEqual(result['question'], self.case['question'])
        self.assertEqual(result['scenario'], self.case['scenario'])
        self.assertEqual(result['assumptions'], self.case['assumptions'])
        self.assertEqual({r['file'] for r in result['source']}, {'entry.py','helper.py'})

    def test_fixed_plan_tampering_is_rejected_before_output_or_dispatch(self):
        from macr.evals.deepseek_study_runner import run_study_cell
        plan = self.plan()
        plan['groups'] = [[], []]
        transport = FakeTransport(reply(json.dumps(UNKNOWN)))
        with self.assertRaisesRegex(PolicyRunError, '^fixed_plan_drift$'):
            run_study_cell(self.manifest_dir, dataset_root=self.root, strategy='fixed_read_3',
                provider=DeepSeekProvider(model=MODEL['model_id'],transport=transport),
                out=self.root/'tampered',fixed_plan=plan)
        self.assertFalse((self.root/'tampered').exists())
        self.assertEqual(transport.posts, [])

    def test_provider_assistant_failure_keeps_independent_billing_evidence(self):
        malformed = reply(json.dumps(UNKNOWN))
        malformed['choices'][0]['message']['role'] = 'user'
        transport = FakeTransport(malformed)
        result = self.run_cell('direct_initial_3', transport)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['requests'][0]['response']['billing_evidence']['raw_usage']['prompt_tokens'],12)
        self.assertEqual(result['reservations'][0]['state'], 'failed_known_usage')
        self.assertEqual(result['requests_attempted'],1)


    def test_settlement_stop_keeps_received_response_and_outcome_before_policy_parse(self):
        response = reply('{policy-output-is-invalid')
        transport = FakeTransport(response)
        def stop(*args, **kwargs):
            raise PolicyRunError('study_halted_unknown')
        result = self.run_cell('direct_initial_3', transport, settle=stop)
        self.assertEqual(result['error_code'], 'study_halted_unknown')
        self.assertEqual(result['requests_attempted'], 1)
        self.assertEqual(result['requests'][0]['response']['raw_usage']['prompt_tokens'], 12)
        self.assertEqual(result['reservations'][0]['state'], 'completed')
        self.assertIsNone(result['answer'])
        receipt = json.loads((self.root/'run'/'decision-01.response.json').read_text())
        self.assertEqual(receipt['assistant_message']['content'], '{policy-output-is-invalid')
        outcome = json.loads((self.root/'run'/'decision-01.outcome.json').read_text())
        self.assertEqual(outcome['state'], 'completed')
        self.assertEqual(outcome['error_code'], 'study_halted_unknown')

    def test_settlement_stop_keeps_provider_failure_billing_and_unknown_reservation(self):
        malformed = reply(json.dumps(UNKNOWN))
        malformed['choices'][0]['message']['role'] = 'user'
        def stop(*args, **kwargs):
            raise PolicyRunError('study_halted_unknown')
        for number, response in enumerate((malformed, TimeoutError())):
            transport = FakeTransport(response)
            result = self.run_cell('direct_initial_3', transport, settle=stop,
                name='settlement-error-' + str(number))
            self.assertEqual(result['error_code'], 'study_halted_unknown')
            self.assertEqual(result['requests_attempted'], 1)
            receipt = result['requests'][0]['response']
            self.assertIsNotNone(receipt)
            expected_state = 'failed_known_usage' if number == 0 else 'reserved_unknown_outcome'
            self.assertEqual(result['reservations'][0]['state'], expected_state)
            if number == 0:
                self.assertEqual(receipt['billing_evidence']['raw_usage']['prompt_tokens'],12)
            else:
                self.assertEqual(receipt['state'], 'unknown')
                self.assertIsNone(result['reservations'][0]['usage'])
            path = self.root/('settlement-error-' + str(number))/'decision-01.outcome.json'
            self.assertEqual(json.loads(path.read_text())['state'], expected_state)


if __name__ == '__main__':
    unittest.main()
