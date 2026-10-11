import copy
import hashlib
import io
import json
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath
from dataclasses import asdict, replace
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.memory.run_store import encoded
from macr.providers.base import ModelRequest, ModelResponse, ProviderError
from macr.providers.local_http import LocalHTTPProvider
from test_local_http_provider import FakeTransport, completion, fixture_profile


class DiagnosticCompareTests(unittest.TestCase):
    def setUp(self):
        from macr.evals import diagnostic_compare
        self.compare = diagnostic_compare
        self.root = fixture_root() / 'comparison'
        self.profiles = [fixture_profile(profile_id='model-a'), fixture_profile(profile_id='model-b')]
        self.cases = []
        for name in ('defect', 'healthy', 'unknown'):
            bodies = {'defect': '1: def value(items):\n2:     return items.pop(0) + items[0]',
                      'healthy': '1: def eligible(age):\n2:     return age >= 18',
                      'unknown': '1: def value(key, resolver):\n2:     return resolver(key)'}
            source = {'source': [{'file': 'logic.py', 'start': 1, 'end': 2,
                                  'body': bodies[name]}]
                      }
            request = ModelRequest([{'role': 'system', 'content': 'Diagnose only supplied source.'},
                                    {'role': 'user', 'content': json.dumps(source)}],
                                   response_schema=self.compare.OUTPUT_SCHEMA,
                                   max_input_tokens=64, max_output_tokens=16, timeout_seconds=1,
                                   metadata={'prompt_version': 'fixture-prompt-v1', 'input_token_upper_bound': 12})
            self.cases.append({'id': name, 'request': asdict(request)})
        self.reference = {'version': 'fixture-rubric-v1', 'cases': {
            'defect': 'supported_defect', 'healthy': 'no_defect_supported', 'unknown': 'unknown'}}

    def prepare(self):
        return self.compare.prepare_comparison(self.root, self.cases, self.profiles,
            prompt_version='fixture-prompt-v1', dataset_version='fixture-dataset-v1', reference=self.reference)

    def response(self, verdict='supported_defect', **changes):
        answer = {'verdict': verdict, 'cause': 'The pop consumes the only item.',
                  'when': 'One item is consumed before the subsequent read.',
                  'refs': [{'file': 'logic.py', 'start': 1, 'end': 2}], 'unknowns': []}
        if verdict == 'no_defect_supported':
            answer.update(cause='The inclusive comparison implements the stated age policy.',
                          when='The supplied ordinary integer age is 18.')
        if verdict == 'unknown':
            answer.update(cause=None, when=None, unknowns=['The external callback body is absent.'])
        values = dict(content=json.dumps(answer), model='fixture-model', provider='local_http',
                      usage={'input_tokens': 9, 'output_tokens': 4}, usage_kind='measured', latency_ms=10)
        values.update(changes)
        return ModelResponse(**values)

    def proof(self, profile, case, response):
        digest = lambda value: hashlib.sha256(encoded(value)).hexdigest()
        return {'request_sha256': digest(case['request']), 'profile_sha256': digest(asdict(profile)),
                'response_sha256': digest(asdict(response)), 'http_status': 200,
                'input_tokens': 9, 'output_tokens': 4, 'release': True, 'idle': True, 'remove_waiting': True}

    def record(self, profile=0, case=0, response=None, proof=None):
        response = response or self.response()
        return self.compare.record_response(self.root, self.profiles[profile].profile_id,
            self.cases[case]['id'], response=response,
            proof=proof or self.proof(self.profiles[profile], self.cases[case], response), provenance='scripted')

    def review(self, row, **changes):
        value = {'record_sha256': row['record_sha256'], 'mechanism': 'supported',
                 'trigger': 'supported', 'citation_support': 'supported', 'abstention_reason': 'unknown',
                 'evidence_refs': row['answer']['refs'], 'reason': 'Independent fixture judgment.'}
        value.update(changes)
        return value

    def test_profiles_inputs_parameters_and_reference_are_separately_frozen(self):
        result = self.prepare()
        self.assertEqual(result['cells'], 6)
        manifest = json.loads((self.root / 'manifest.json').read_text())
        self.assertEqual(manifest['profiles'][0], asdict(self.profiles[0]))
        self.assertEqual(manifest['cases'], self.cases)
        self.assertNotIn('reference', json.dumps(manifest['cases']))
        self.assertEqual(json.loads((self.root / 'reference.json').read_text()), self.reference)
        changed = copy.deepcopy(self.profiles[1].sampling); changed['temperature'] = 0.2
        self.profiles[1] = replace(self.profiles[1], sampling=changed)
        new_root = fixture_root() / 'invalid'
        with self.assertRaises(ValueError):
            self.compare.prepare_comparison(new_root, self.cases, self.profiles,
                prompt_version='fixture-prompt-v1', dataset_version='v1', reference=self.reference)
        self.assertFalse(new_root.exists())

    def test_offline_timeout_is_bounded_by_300_seconds_and_each_profile(self):
        self.profiles = [replace(profile, timeout_seconds=180) for profile in self.profiles]
        for case in self.cases:
            case['request']['timeout_seconds'] = 180
        self.assertEqual(self.prepare()['new_model_calls'], 0)
        self.root = fixture_root() / 'timeout-five-minutes'
        self.profiles = [replace(profile, timeout_seconds=300) for profile in self.profiles]
        for case in self.cases:
            case['request']['timeout_seconds'] = 300
        self.assertEqual(self.prepare()['new_model_calls'], 0)
        self.root = fixture_root() / 'timeout-too-long'
        self.cases[0]['request']['timeout_seconds'] = 301
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.root.exists())
        self.cases[0]['request']['timeout_seconds'] = 300
        self.profiles = [replace(profile, timeout_seconds=60) for profile in self.profiles]
        self.root = fixture_root() / 'timeout-exceeds-profile'
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.root.exists())

    def test_real_provider_adapter_is_reused_with_inert_transport(self):
        self.prepare()
        content = self.response().content
        transport = FakeTransport(completion(content, {'prompt_tokens': 9, 'completion_tokens': 4, 'total_tokens': 13}))
        provider = LocalHTTPProvider(self.profiles[0], transport=transport)
        response = provider.complete(ModelRequest(**self.cases[0]['request']))
        row = self.record(response=response)
        self.assertEqual(row['failure_class'], 'semantic_pending')
        self.assertEqual(len(transport.calls), 1)
        self.assertFalse(row['diagnostic_passed'])

    def test_parsing_and_correct_verdict_do_not_replace_causal_review(self):
        self.prepare(); row = self.record()
        pending = self.compare.compare_results(self.root)
        self.assertEqual(pending['models'][0]['diagnostic_passes'], 0)
        reviews = {'model-a/defect': self.review(row, mechanism='partial')}
        report = self.compare.compare_results(self.root, reviews=reviews)
        self.assertEqual(report['cases'][0]['failure_class'], 'semantic')
        self.assertFalse(report['cases'][0]['diagnostic_passed'])

    def test_defect_healthy_and_unknown_can_pass_only_independent_bound_review(self):
        self.prepare(); reviews = {}
        for model in range(2):
            for case, verdict in enumerate(self.reference['cases'].values()):
                row = self.record(model, case, self.response(verdict))
                reviews[f'model-{chr(97 + model)}/{self.cases[case]["id"]}'] = self.review(row,
                    abstention_reason='supported' if verdict == 'unknown' else 'unknown')
        report = self.compare.compare_results(self.root, reviews=reviews)
        self.assertEqual([m['diagnostic_passes'] for m in report['models']], [3, 3])
        self.assertEqual(report['new_model_calls'], 0)
        self.assertFalse(report['real_model_accuracy_measured'])
        paths = self.compare.save_comparison(self.root, report)
        self.assertIn('scripted', Path(paths['markdown']).read_text())

    def test_report_paths_are_json_strings_before_summary_writes(self):
        self.prepare(); self.record()
        paths=self.compare.save_comparison(self.root,self.compare.compare_results(self.root))
        self.assertTrue(all(type(value) is str for value in paths.values()),'report paths must serialize without caller conversion')
        self.assertEqual(json.loads(encoded({'comparison':paths}))['comparison'],paths)
        for folder in [PurePosixPath('/fixture/project artifacts'),PureWindowsPath('C:/fixture/project artifacts')]:
            refs=self.compare.report_paths(folder)
            self.assertEqual(json.loads(encoded(refs))['json'],str(folder/'comparison.json'))

    def test_service_summary_counts_preflight_failure_as_not_started(self):
        one={'success':True,'server_start_attempts':1,'generation_requests':3,'process':{'pid':42}}
        blocked={'success':False,'server_start_attempts':0,'generation_requests':0,'process':{},'failure':'bind conflict'}
        report=self.compare.summarize_services([one,blocked],planned_services=2)
        self.assertEqual((report['services_started'],report['generation_requests'],report['not_started_services']),(1,3,1))
        self.assertTrue(report['second_service_cancelled']);self.assertFalse(report['success'])
        self.assertTrue(self.compare.summarize_services([one],planned_services=2)['second_service_cancelled'])
        failed_spawn=blocked|{'server_start_attempts':1}
        report=self.compare.summarize_services([failed_spawn],planned_services=1)
        self.assertEqual((report['service_start_attempts'],report['services_started']),(1,0))
        with self.assertRaises(ValueError):self.compare.summarize_services([blocked|{'generation_requests':1}],planned_services=1)

    def test_infrastructure_record_is_not_a_generated_or_quality_case(self):
        self.prepare()
        self.compare.record_response(self.root,'model-a','defect',error=ProviderError('service_preflight_failed'),provenance='scripted')
        report=self.compare.compare_results(self.root);model=report['models'][0]
        self.assertEqual((model['recorded'],model['captured_responses'],model['quality_cases']),(1,0,0))
        self.assertIsNone(model['diagnostic_rate'])
        paths=self.compare.save_comparison(self.root,report)
        self.assertIn('N/A',Path(paths['markdown']).read_text())

    def test_pending_review_has_no_rate_and_format_failure_keeps_its_real_denominator(self):
        self.prepare();row=self.record()
        self.assertIsNone(self.compare.compare_results(self.root)['models'][0]['diagnostic_rate'])
        self.record(case=1,response=self.response(content='{}'))
        report=self.compare.compare_results(self.root,reviews={'model-a/defect':self.review(row,mechanism='partial')})
        model=report['models'][0]
        self.assertEqual((model['captured_responses'],model['quality_cases']),(2,2))
        self.assertEqual(model['diagnostic_rate'],0)

    def test_categories_keep_structure_semantics_infrastructure_and_unattempted_separate(self):
        names = ('passed', 'semantic-failed', 'pending', 'syntax', 'contract', 'citation', 'infra', 'unattempted')
        verdicts = ('supported_defect', 'no_defect_supported', 'unknown', 'supported_defect',
                    'no_defect_supported', 'unknown', 'supported_defect', 'no_defect_supported')
        self.cases = [dict(copy.deepcopy(self.cases[index % 3]), id=name) for index, name in enumerate(names)]
        self.profiles = self.profiles[:1]
        self.reference['cases'] = dict(zip(names, verdicts))
        self.reference['topics'] = dict(zip(names, ('boundary', 'boundary', 'mutation', 'mutation',
            'mutation', 'missing-dependency', 'missing-dependency', 'boundary')))
        self.prepare()
        passed = self.record(response=self.response())
        failed = self.record(case=1, response=self.response('no_defect_supported'))
        self.record(case=2, response=self.response('unknown'))
        self.record(case=3, response=self.response(content='{"verdict":'))
        self.record(case=4, response=self.response(content='[]'))
        self.record(case=5, response=self.response('unknown',
            content=self.response('unknown').content.replace('"end": 2', '"end": 99')))
        self.record(case=6, response=self.response(content='malformed'), proof={'http_status': 200})
        report = self.compare.compare_results(self.root, reviews={'model-a/passed': self.review(passed),
            'model-a/semantic-failed': self.review(failed, mechanism='partial')})
        model = report['models'][0]
        self.assertEqual((model['expected'], model['recorded'], model['captured_responses'],
            model['not_attempted'], model['quality_cases']), (8, 7, 7, 1, 6))
        self.assertEqual((model['strict_json_passes'], model['contract_passes'], model['semantic_reviewed'],
            model['diagnostic_passes'], model['semantic_pending'], model['format'], model['infrastructure']),
            (5, 3, 2, 1, 1, 3, 1))
        self.assertEqual((model['strict_json_rate'], model['contract_rate']), (5 / 6, 3 / 6))
        self.assertIsNone(model['diagnostic_rate'])
        defect = model['categories']['expected_verdict']['supported_defect']
        self.assertEqual((defect['quality_cases'], defect['diagnostic_rate'], defect['infrastructure']), (2, .5, 1))
        unknown = model['categories']['expected_verdict']['unknown']
        self.assertEqual((unknown['contract_passes'], unknown['semantic_pending']), (1, 1))
        self.assertIsNone(unknown['diagnostic_rate'])
        boundary = model['categories']['topic']['boundary']
        self.assertEqual((boundary['expected'], boundary['not_attempted'], boundary['diagnostic_rate']), (3, 1, .5))
        self.assertEqual(report['cases'][4]['strict_json_valid'], True)
        self.assertEqual(report['cases'][4]['contract_valid'], False)
        self.assertIsNone(report['cases'][6]['strict_json_valid'])
        self.assertEqual(report['cases'][6]['semantic_review'], 'not_applicable')
        markdown = Path(self.compare.save_comparison(self.root, report)['markdown']).read_text()
        self.assertIn('expected_verdict:unknown', markdown)
        self.assertIn('topic:missing-dependency', markdown)

    def test_topics_are_complete_validated_and_bound_only_to_the_reference(self):
        for topics in ({'defect': 'boundary'},
            {'defect': 'boundary', 'healthy': 'boundary', 'unknown': 'dependency', 'dangling': 'boundary'},
            {'defect': 'boundary', 'healthy': 'boundary', 'unknown': '../dependency'}):
            self.root = fixture_root() / 'bad-topics'
            self.reference['topics'] = topics
            with self.assertRaises(ValueError):
                self.prepare()
            self.assertFalse(self.root.exists())
        self.root = fixture_root() / 'valid-topics'
        self.reference['topics'] = {'defect': 'mutation', 'healthy': 'boundary', 'unknown': 'dependency'}
        self.prepare()
        manifest = json.loads((self.root / 'manifest.json').read_text())
        self.assertEqual(manifest['cases'], self.cases)
        self.assertNotIn('topics', json.dumps(manifest['cases']))
        self.record()
        reference = json.loads((self.root / 'reference.json').read_text())
        reference['topics']['defect'] = 'boundary'
        (self.root / 'reference.json').write_text(json.dumps(reference))
        with self.assertRaises(ValueError):
            self.compare.compare_results(self.root)

    def test_old_captures_keep_hashes_and_get_strict_json_metrics_from_raw_content(self):
        self.prepare()
        row = self.record(response=self.response(content='{}'))
        stored = json.loads((self.root / 'model-a--defect.json').read_text())
        self.assertNotIn('strict_json_valid', stored)
        report = self.compare.compare_results(self.root)
        self.assertEqual(report['cases'][0]['record_sha256'], row['record_sha256'])
        self.assertTrue(report['cases'][0]['strict_json_valid'])
        self.assertFalse(report['cases'][0]['contract_valid'])
        self.assertNotIn('topic', report['models'][0]['categories'])
        self.assertEqual(report['models'][0]['diagnostic_rate'], 0)
        self.assertEqual(report['models'][1]['not_attempted'], 3)
        for content in ('{"x":1,"x":2}', '{"x":NaN}', 'true trailing'):
            self.assertFalse(self.compare._strict_json(content))

    def test_format_failure_with_bound_release_proof_continues_without_repair(self):
        self.prepare()
        row = self.record(response=self.response(content='{"verdict":'))
        self.assertEqual((row['failure_class'], row['error_code']), ('format', 'model_output_invalid'))
        raw = json.loads((self.root / 'model-a--defect.json').read_text())
        self.assertEqual(raw['response']['content'], '{"verdict":')
        self.record(case=1)
        with self.assertRaises(ValueError): self.record(case=1)

    def test_contract_citation_and_truncation_failures_remain_distinct(self):
        self.prepare()
        cases = [self.response(content='{}'),
                 self.response(content=self.response().content.replace('"end": 2', '"end": 99')),
                 self.response(finish_reason='length')]
        codes = ['eval_contract_invalid', 'eval_citation_invalid', 'model_output_incomplete']
        for case, (response, code) in enumerate(zip(cases, codes)):
            self.assertEqual(self.record(case=case, response=response)['error_code'], code)

    def test_health_and_native_proof_failures_stop_the_whole_batch(self):
        self.prepare()
        row = self.compare.record_response(self.root, 'model-a', 'defect',
            error=ProviderError('model_health_unexpected'), provenance='scripted')
        self.assertEqual(row['failure_class'], 'infrastructure')
        with self.assertRaises(ValueError): self.record(case=1)
        self.root = fixture_root() / 'missing-proof'; self.prepare()
        row = self.record(proof={'http_status': 200})
        self.assertEqual(row['error_code'], 'native_proof_unverified')
        with self.assertRaises(ValueError): self.record(case=1)
        self.root = fixture_root() / 'unknown-terminal'; self.prepare()
        row = self.record(response=self.response(finish_reason='unknown'))
        self.assertEqual(row['failure_class'], 'infrastructure')
        with self.assertRaises(ValueError): self.record(case=1)

    def test_input_reference_review_and_usage_drift_cannot_get_a_pass(self):
        self.prepare(); row = self.record()
        review = self.review(row); review['record_sha256'] = '0' * 64
        with self.assertRaises(ValueError): self.compare.compare_results(self.root, reviews={'model-a/defect': review})
        (self.root / 'reference.json').write_text('{}')
        with self.assertRaises(ValueError): self.compare.compare_results(self.root)
        self.root = fixture_root() / 'usage-drift'; self.prepare()
        row = self.record(response=self.response(usage={'input_tokens': 10, 'output_tokens': 4}))
        self.assertEqual(row['failure_class'], 'infrastructure')
        self.root = fixture_root() / 'input-drift'; self.prepare()
        path = self.root / 'manifest.json'
        manifest = json.loads(path.read_text()); manifest['cases'][0]['request']['messages'][0]['content'] += 'changed'
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError): self.record()
        self.root = fixture_root() / 'response-drift'; self.prepare(); self.record()
        path = self.root / 'model-a--defect.json'
        raw = json.loads(path.read_text()); raw['response']['content'] += 'changed'
        path.write_text(json.dumps(raw))
        with self.assertRaises(ValueError): self.compare.compare_results(self.root)

    def test_write_failure_leaves_uncertain_cell_and_prevents_any_replay(self):
        self.prepare()
        from macr.memory.atomic_file import atomic_write
        def fail(path, data):
            if path.name == 'model-a--defect.json': raise OSError('fixture write failure')
            return atomic_write(path, data)
        with patch.object(self.compare, 'atomic_write', side_effect=fail):
            with self.assertRaises(OSError): self.record()
        with self.assertRaises(ValueError): self.record()
        report = self.compare.compare_results(self.root)
        self.assertEqual(report['uncertain_cell'], 'model-a/defect')
        self.assertEqual((report['models'][0]['not_attempted'], report['models'][0]['uncertain_cells']), (2, 1))

    def test_initial_state_write_failure_and_unretained_adapter_output_stop(self):
        self.prepare()
        with patch.object(self.compare, 'atomic_write', side_effect=OSError('fixture')):
            with self.assertRaises(OSError): self.record()
        with self.assertRaises(ValueError): self.record()
        self.root = fixture_root() / 'adapter-error'; self.prepare()
        row = self.compare.record_response(self.root, 'model-a', 'defect',
            error=ProviderError('model_output_invalid'), provenance='scripted')
        self.assertEqual(row['failure_class'], 'infrastructure')
        with self.assertRaises(ValueError): self.record(case=1)

    def test_wrong_verdict_and_fabricated_report_are_rejected(self):
        self.prepare(); self.record()
        row = self.record(case=1)
        reviews = {'model-a/healthy': self.review(row)}
        report = self.compare.compare_results(self.root, reviews=reviews)
        self.assertTrue(report['cases'][1]['healthy_false_alarm'])
        self.assertFalse(report['cases'][1]['diagnostic_passed'])
        report['models'][0]['diagnostic_passes'] = 99
        with self.assertRaises(ValueError): self.compare.save_comparison(self.root, report)

    def test_new_output_and_record_limit_checked_before_writes(self):
        self.prepare()
        with self.assertRaises(ValueError): self.prepare()
        with self.assertRaises(ValueError):
            self.compare.prepare_comparison(fixture_root() / 'too-large',
                self.cases * 5, self.profiles, prompt_version='fixture-prompt-v1',
                dataset_version='v1', reference=self.reference)
        huge = copy.deepcopy(self.cases)
        user = json.loads(huge[0]['request']['messages'][-1]['content'])
        user['source'][0]['body'] = 'x' * (1024 * 1024)
        huge[0]['request']['messages'][-1]['content'] = json.dumps(user)
        out = fixture_root() / 'huge-input'
        with self.assertRaises(ValueError):
            self.compare.prepare_comparison(out, huge, self.profiles,
                prompt_version='fixture-prompt-v1', dataset_version='v1', reference=self.reference)
        self.assertFalse(out.exists())
        oversized = self.response(metrics={'fixture': 'x' * (128 * 1024)})
        with self.assertRaises(ValueError): self.record(response=oversized)
        self.assertFalse((self.root / 'model-a--defect.claim').exists())

    def test_offline_command_consumes_only_explicit_scripted_bundle(self):
        import compare_diagnostics
        results = []
        for profile in self.profiles:
            for case in self.cases:
                response = self.response(self.reference['cases'][case['id']])
                results.append({'profile_id': profile.profile_id, 'case_id': case['id'],
                    'response': asdict(response), 'error_code': None, 'proof': self.proof(profile, case, response),
                    'provenance': 'scripted'})
        bundle = {'cases': self.cases, 'profiles': [asdict(p) for p in self.profiles],
            'prompt_version': 'fixture-prompt-v1', 'dataset_version': 'fixture-dataset-v1',
            'reference': self.reference, 'results': results, 'reviews': {}}
        path = fixture_root() / 'replay.json'; path.write_text(json.dumps(bundle))
        output = io.StringIO()
        with patch('sys.argv', ['compare_diagnostics', '--input', str(path), '--out', str(self.root)]), patch('sys.stdout', output):
            self.assertEqual(compare_diagnostics.main(), 0)
        report = json.loads(output.getvalue())
        self.assertEqual((report['recorded_responses'], report['new_model_calls'], report['diagnostic_passes']), (6, 0, 0))
        self.assertFalse(report['real_model_accuracy_measured'])

    def test_dataset_command_only_prepares_and_never_records_responses(self):
        import compare_diagnostics
        root = fixture_root()
        profiles = root / 'profiles.json'
        profiles.write_text(json.dumps([asdict(profile) for profile in self.profiles]))
        cases_path, reference_path = root / 'cases.json', root / 'reference.json'
        arguments = ['compare_diagnostics', '--dataset', str(cases_path), '--reference', str(reference_path),
            '--profiles', str(profiles), '--out', str(self.root)]
        output = io.StringIO()
        with patch('macr.evals.diagnostic_dataset.load_dataset', return_value=(self.cases, self.reference)) as load, \
                patch('sys.argv', arguments), patch('sys.stdout', output), \
                patch.object(compare_diagnostics, 'record_response', side_effect=AssertionError('no response authority')):
            self.assertEqual(compare_diagnostics.main(), 0)
        load.assert_called_once_with(cases_path, reference_path, timeout_seconds=self.profiles[0].timeout_seconds)
        result = json.loads(output.getvalue())
        self.assertEqual((result['prepared'], result['cells'], result['new_model_calls']), (True, 6, 0))
        self.assertEqual(json.loads((self.root / 'state.json').read_text())['rows'], [])
        self.assertEqual(json.loads((self.root / 'manifest.json').read_text())['cases'], self.cases)

    def test_dataset_command_requires_separate_reference_and_profiles(self):
        import compare_diagnostics
        for arguments in (['--dataset', 'cases.json'], ['--input', 'bundle.json', '--profiles', 'profiles.json'],
            ['--input', 'bundle.json', '--dataset', 'cases.json']):
            with patch('sys.argv', ['compare_diagnostics', *arguments, '--out', str(self.root)]), \
                    patch('sys.stderr', io.StringIO()):
                with self.assertRaises(SystemExit) as failure:
                    compare_diagnostics.main()
            self.assertEqual(failure.exception.code, 2)
            self.assertFalse(self.root.exists())

    def test_dataset_command_rejects_profile_json_duplicates_and_trailing_fields(self):
        import compare_diagnostics
        profiles = fixture_root() / 'profiles.json'
        values = json.dumps([asdict(profile) for profile in self.profiles])
        for malformed in (values.replace('"profile_id": "model-a"',
            '"profile_id": "model-a", "profile_id": "model-a"'), values + ', "extra": 1'):
            profiles.write_text(malformed)
            with patch('macr.evals.diagnostic_dataset.load_dataset', return_value=(self.cases, self.reference)), \
                    patch('sys.argv', ['compare_diagnostics', '--dataset', 'cases.json', '--reference', 'reference.json',
                        '--profiles', str(profiles), '--out', str(self.root)]), patch('sys.stderr', io.StringIO()):
                with self.assertRaises(SystemExit) as failure:
                    compare_diagnostics.main()
            self.assertEqual(failure.exception.code, 2)
            self.assertFalse(self.root.exists())
