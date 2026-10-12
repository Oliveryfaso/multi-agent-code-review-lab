import copy
import io
import json
import unittest
import urllib.error
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.providers.base import ModelBudget, ModelRequest, ModelResponse, ProviderCapabilities, ProviderError
from macr.providers.profiles import ModelProfile, load_profile, validate_profile
from macr.providers.local_http import LocalHTTPProvider, LoopbackTransport, probe_service


def fixture_profile(**changes):
    values = dict(
        profile_id='fixture-local', model_id='fixture-model', model_revision='fixture-revision-1',
        tokenizer_revision='fixture-tokenizer-1', chat_template_version='fixture-template-1',
        runtime='fixture-runtime', runtime_version='fixture-runtime-1', quantization='fixture-format',
        endpoint='http://127.0.0.1:9876/v1/chat/completions', context_limit=256, output_limit=32,
        sampling={'temperature': 0.0, 'top_p': 1.0}, timeout_seconds=2.0,
        capabilities=ProviderCapabilities(),
    )
    values.update(changes)
    return ModelProfile(**values)


def completion(content='{"ok":true}', usage=None):
    result = {'model': 'fixture-model', 'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]}
    if usage is not None:
        result['usage'] = usage
    return result


class FakeTransport:
    def __init__(self, response=None, error=None, callback=None):
        self.response = completion() if response is None else response
        self.error, self.callback, self.calls = error, callback, []
        self.bodies = []

    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        self.bodies.append(body)
        self.calls.append((endpoint, json.loads(body), timeout_seconds, max_response_bytes))
        if self.callback:
            self.callback()
        if self.error:
            raise self.error
        return copy.deepcopy(self.response)


class LocalHTTPProviderTests(unittest.TestCase):
    def request(self, **changes):
        values = dict(messages=[{'role': 'user', 'content': 'fixture'}], max_input_tokens=64,
                      max_output_tokens=16, timeout_seconds=1.0,
                      metadata={'input_token_upper_bound': 12})
        values.update(changes)
        return ModelRequest(**values)

    def assert_code(self, code, fn):
        with self.assertRaises(ProviderError) as error:
            fn()
        self.assertEqual(error.exception.code, code)
        return error.exception

    def test_loopback_endpoint_and_profile_validation(self):
        for endpoint in ('http://example.com:9876/v1/chat/completions',
                         'http://127.0.0.1.example.com:9876/v1/chat/completions',
                         'http://user:pass@127.0.0.1:9876/v1/chat/completions',
                         'http://127.0.0.1:9876/v1/chat/completions?next=remote',
                         'http://127.0.0.1:9876/v1/chat/completions#fragment',
                         'http://127.0.0.1:9876/other',
                         'http://127.0.0.1/v1/chat/completions',
                         'http://127.0.0.1:9876/v1/chat/completions\n'):
            self.assert_code('model_profile_invalid', lambda endpoint=endpoint: validate_profile(fixture_profile(endpoint=endpoint)))
        for endpoint in ('http://localhost:9876/v1/chat/completions', 'http://[::1]:9876/v1/chat/completions'):
            validate_profile(fixture_profile(endpoint=endpoint))
        for changes in ({'context_limit': True}, {'output_limit': 300}, {'timeout_seconds': float('nan')},
                        {'model_revision': 'main'}, {'tokenizer_revision': 'latest'},
                        {'sampling': {'temperature': -1}}, {'sampling': {'extra_environment': 'fixture'}},
                        {'capabilities': ProviderCapabilities(usage=1)}):
            self.assert_code('model_profile_invalid', lambda changes=changes: validate_profile(fixture_profile(**changes)))

    def test_complete_uses_sync_contract_and_fixed_profile(self):
        transport = FakeTransport(completion(usage={'prompt_tokens': 9, 'completion_tokens': 4, 'total_tokens': 13}))
        profile = fixture_profile(capabilities=ProviderCapabilities(json_mode=True, usage=True))
        provider = LocalHTTPProvider(profile, transport=transport)
        with patch.dict('os.environ', {'HTTP_PROXY': 'http://example.invalid', 'OPENAI_API_KEY': 'fixture-value'}), \
                patch.object(Path, 'read_text', side_effect=AssertionError('must not discover env/config')):
            response = provider.complete(self.request(response_schema={'type': 'object'}))
        self.assertIsInstance(response, ModelResponse)
        self.assertEqual((response.provider, response.model, response.usage_kind), ('local_http', 'fixture-model', 'measured'))
        self.assertEqual((response.usage['input_tokens'], response.usage['output_tokens']), (9, 4))
        endpoint, payload, timeout, limit = transport.calls[0]
        self.assertEqual(endpoint, profile.endpoint)
        self.assertFalse(payload['stream'])
        self.assertEqual(payload['max_tokens'], 16)
        self.assertEqual(payload['temperature'], 0.0)
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        self.assertEqual(payload['metadata']['chat_template_version'], profile.chat_template_version)
        self.assertEqual(payload['metadata']['tokenizer_revision'], profile.tokenizer_revision)
        self.assertNotIn('input_token_upper_bound', payload['metadata'])
        self.assertEqual((timeout, limit), (1.0, 1024 * 1024))
        self.assertEqual(response.metrics['prefill_tokens_per_second'], None)
        self.assertEqual(response.metrics['token_bound_source'], 'caller_supplied')
        profile.sampling['temperature'] = 1.5
        provider.complete(self.request())
        self.assertEqual(transport.calls[-1][1]['temperature'], 0.0)

    def test_unknown_capabilities_usage_and_schema_are_not_fabricated(self):
        transport = FakeTransport()
        provider = LocalHTTPProvider(fixture_profile(), transport=transport)
        response = provider.complete(self.request(response_schema={'type': 'object'}))
        self.assertNotIn('response_format', transport.calls[0][1])
        self.assertEqual((response.usage_kind, response.usage), ('unknown', {}))
        self.assertFalse(provider.capabilities.json_mode)
        self.assertFalse(provider.capabilities.cancel)
        self.assertFalse(provider.capabilities.health)
        self.assertEqual(len(transport.calls), 1)
        report = probe_service(fixture_profile())
        self.assertFalse(report['ready'])
        self.assertFalse(report['attested'])
        self.assertEqual(report['source'], 'configuration_only')
        simulated = probe_service(fixture_profile(), transport=FakeTransport(), input_token_upper_bound=12)
        self.assertFalse(simulated['ready'])
        self.assertFalse(simulated['attested'])
        self.assertEqual(simulated['source'], 'injected_transport')

    def test_missing_accounting_overflow_and_invalid_counter_do_not_dispatch(self):
        transport = FakeTransport()
        provider = LocalHTTPProvider(fixture_profile(), transport=transport)
        self.assert_code('model_token_accounting_unavailable', lambda: provider.complete(self.request(metadata={})))
        self.assert_code('model_context_overflow', lambda: provider.complete(self.request(metadata={'input_token_upper_bound': 65})))
        self.assert_code('model_context_overflow', lambda: provider.complete(self.request(max_input_tokens=250)))
        self.assert_code('model_output_limit', lambda: provider.complete(self.request(max_output_tokens=33)))
        self.assert_code('model_token_accounting_unavailable', lambda: provider.complete(self.request(metadata={'input_token_upper_bound': True})))
        self.assertEqual(transport.calls, [])
        counter = lambda request, profile: 10
        response = LocalHTTPProvider(fixture_profile(), transport=transport, token_counter=counter).complete(self.request(metadata={}))
        self.assertEqual(response.metrics['token_bound_source'], 'injected_counter')
        bad_counter = lambda request, profile: -1
        self.assert_code('model_token_accounting_unavailable', lambda: LocalHTTPProvider(fixture_profile(), transport=transport, token_counter=bad_counter).complete(self.request()))

    def test_explicit_tool_response_is_data_not_execution(self):
        call = {'id': 'fixture-call', 'type': 'function',
                'function': {'name': 'read_context', 'arguments': '{"file":"fixture.py"}'}}
        data = {'model': 'fixture-model', 'choices': [{'message': {'content': None, 'tool_calls': [call]}, 'finish_reason': 'tool_calls'}]}
        tools = [{'type': 'function', 'function': {'name': 'read_context', 'parameters': {'type': 'object'}}}]
        transport = FakeTransport(data)
        response = LocalHTTPProvider(fixture_profile(), transport=transport).complete(self.request(tools=tools))
        self.assertEqual(response.content, '')
        self.assertEqual(response.tool_calls, [call])
        self.assertEqual(len(transport.calls), 1)
        self.assert_code('model_response_invalid', lambda: LocalHTTPProvider(fixture_profile(), transport=FakeTransport(data)).complete(self.request()))

    def test_partial_usage_is_unknown_and_profile_model_mismatch_is_invalid(self):
        response = LocalHTTPProvider(fixture_profile(), transport=FakeTransport(completion(usage={'prompt_tokens': 9}))).complete(self.request())
        self.assertEqual((response.usage, response.usage_kind), ({}, 'unknown'))
        data = completion(); data['model'] = 'different-model'
        self.assert_code('model_response_invalid', lambda: LocalHTTPProvider(fixture_profile(), transport=FakeTransport(data)).complete(self.request()))

    def test_timeout_empty_invalid_json_and_invalid_response_have_no_hidden_retry(self):
        transport = FakeTransport(error=TimeoutError('fixture-sensitive-value'))
        error = self.assert_code('model_timeout', lambda: LocalHTTPProvider(fixture_profile(), transport=transport).complete(self.request()))
        self.assertNotIn('fixture-sensitive-value', str(error))
        self.assertEqual(len(transport.calls), 1)
        for data in ({}, {'choices': []}, completion(''), completion('not JSON'),
                     completion(usage={'prompt_tokens': True, 'completion_tokens': 2}),
                     completion(usage={'prompt_tokens': 2, 'completion_tokens': 17})):
            with self.subTest(data=data):
                self.assert_code('model_response_invalid' if data != completion('not JSON') else 'model_output_invalid',
                                 lambda data=data: LocalHTTPProvider(fixture_profile(), transport=FakeTransport(data)).complete(self.request(response_schema={'type': 'object'})))
        self.assert_code('model_output_invalid', lambda: LocalHTTPProvider(fixture_profile(), transport=FakeTransport(completion('[]'))).complete(self.request(response_schema={'type': 'object'})))

    def test_declared_cancel_health_are_not_implemented_capabilities(self):
        profile = fixture_profile(capabilities=ProviderCapabilities(json_mode=True, usage=True, cancel=True, health=True))
        provider = LocalHTTPProvider(profile, transport=FakeTransport())
        self.assertFalse(provider.capabilities.cancel)
        self.assertFalse(provider.capabilities.health)
        response = provider.complete(self.request())
        self.assertEqual(response.metrics['chat_template_version'], profile.chat_template_version)
        self.assertEqual(response.metrics['profile_id'], profile.profile_id)
        simulated = probe_service(profile, transport=FakeTransport(completion(usage={'prompt_tokens': 9, 'completion_tokens': 4})), input_token_upper_bound=12)
        self.assertTrue(simulated['sample_response_valid'])
        self.assertFalse(simulated['ready'])

    def test_incomplete_json_generation_is_distinct_from_invalid_finished_output(self):
        for finish in ('length', 'unknown'):
            for content in ('{"ok":', '{"ok":true}'):
                with self.subTest(finish=finish, content=content):
                    data = completion(content)
                    data['choices'][0]['finish_reason'] = finish
                    transport = FakeTransport(data)
                    self.assert_code('model_output_incomplete', lambda:
                        LocalHTTPProvider(fixture_profile(), transport=transport).complete(
                            self.request(response_schema={'type': 'object'})))
                    self.assertEqual(len(transport.calls), 1)
        data = completion('{"ok":', usage={'prompt_tokens': 13, 'completion_tokens': 4})
        data['choices'][0]['finish_reason'] = 'length'
        self.assert_code('model_response_invalid', lambda:
            LocalHTTPProvider(fixture_profile(), transport=FakeTransport(data)).complete(
                self.request(response_schema={'type': 'object'})))
        self.assert_code('model_output_invalid', lambda:
            LocalHTTPProvider(fixture_profile(), transport=FakeTransport(completion('{"ok":'))).complete(
                self.request(response_schema={'type': 'object'})))

    def test_trusted_output_failures_preserve_raw_finish_and_measured_cost(self):
        for content, finish, code, kind in (
                ('{"ok":', 'length', 'model_output_incomplete', 'incomplete'),
                ('', 'length', 'model_output_incomplete', 'incomplete'),
                ('not JSON', 'stop', 'model_output_invalid', 'invalid_json'),
                ('{"ok":true,"ok":false}', 'stop', 'model_output_invalid', 'invalid_json'),
                ('[]', 'stop', 'model_output_invalid', 'invalid_contract')):
            with self.subTest(content=content):
                data = completion(content, usage={'prompt_tokens': 9, 'completion_tokens': 4, 'total_tokens': 13})
                data['choices'][0]['finish_reason'] = finish
                transport = FakeTransport(data)
                error = self.assert_code(code, lambda:
                    LocalHTTPProvider(fixture_profile(), transport=transport).complete(
                        self.request(response_schema={'type': 'object'})))
                self.assertEqual(error.failure_kind, kind)
                self.assertEqual(error.response.content, content)
                self.assertEqual(error.response.finish_reason, finish)
                self.assertEqual(error.response.usage, {'input_tokens': 9, 'output_tokens': 4})
                self.assertEqual(error.response.usage_kind, 'measured')
                self.assertEqual(error.response.metrics['schema_validation'], 'caller_owned')
                self.assertEqual(len(transport.calls), 1)

    def test_untrusted_envelope_or_unknown_transport_does_not_fabricate_failure_response(self):
        for data in (completion('{"ok":', usage={'prompt_tokens': 13, 'completion_tokens': 4}),
                     completion('{"ok":', usage={'prompt_tokens': 9, 'completion_tokens': True}),
                     completion('{"ok":') | {'model': 'foreign-model'}):
            with self.subTest(data=data):
                error = self.assert_code('model_response_invalid', lambda:
                    LocalHTTPProvider(fixture_profile(), transport=FakeTransport(data)).complete(
                        self.request(response_schema={'type': 'object'})))
                self.assertIsNone(error.response)
                self.assertEqual(error.failure_kind, 'invalid_contract')
        error = self.assert_code('model_timeout', lambda:
            LocalHTTPProvider(fixture_profile(), transport=FakeTransport(error=TimeoutError())).complete(self.request()))
        self.assertIsNone(error.response)
        self.assertEqual(error.failure_kind, 'transport')
        error = self.assert_code('model_output_invalid', lambda:
            LocalHTTPProvider(fixture_profile(), transport=FakeTransport(completion('not JSON'))).complete(
                self.request(response_schema={'type': 'object'})))
        self.assertEqual((error.response.usage, error.response.usage_kind), ({}, 'unknown'))

    def test_explicit_frozen_budget_dispatches_once_and_overflow_dispatches_zero(self):
        profile = fixture_profile(context_limit=2048, output_limit=512, timeout_seconds=300)
        transport = FakeTransport()
        provider = LocalHTTPProvider(profile, transport=transport, token_counter=lambda request, profile: 25)
        original = ModelRequest([{'role': 'user', 'content': 'fixture'}])
        self.assert_code('model_output_limit', lambda: provider.complete(original))
        self.assertEqual(transport.calls, [])
        request = ModelBudget(1536, 512, 300).apply(original)
        provider.complete(request)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0][1]['max_tokens'], 512)
        self.assertEqual(transport.calls[0][2], 300)
        for request in (ModelBudget(1537, 512, 300).apply(original),
                        ModelBudget(1536, 513, 300).apply(original)):
            self.assert_code('model_context_overflow' if request.max_output_tokens == 512 else 'model_output_limit',
                             lambda request=request: provider.complete(request))
        self.assertEqual(len(transport.calls), 1)

    def test_wrong_nested_shape_remains_caller_owned_and_keeps_response(self):
        request = self.request(response_schema={'type': 'object', 'required': ['answer', 'evidence', 'next_steps']})
        response = LocalHTTPProvider(fixture_profile(), transport=FakeTransport(completion('{"wrong":"shape"}'))).complete(request)
        self.assertEqual(response.content, '{"wrong":"shape"}')
        self.assertEqual(response.metrics['schema_validation'], 'caller_owned')

    def test_solver_replay_uses_production_response_boundary_and_retains_failures(self):
        from macr.agents.orchestrator import Orchestrator
        from macr.agents.solver import SolverAgent
        from macr.memory.board import AgentBoard
        from macr.schemas import Evidence, Plan, Trace
        evidence = [Evidence('access.py', 1, 1, 'fixture', 'return staff or paid',
                             symbol='allowed', source_tool='parse_ast')]
        citation = {key: getattr(evidence[0], key) for key in
                    ('file', 'line_start', 'line_end', 'symbol', 'source_tool')}
        success = json.dumps({'answer': 'Inspect the OR condition.', 'evidence': [citation], 'next_steps': []})
        plan = Plan('bug_investigation', 'low', [])
        profile = fixture_profile(context_limit=2048, output_limit=512, timeout_seconds=300,
                                  capabilities=ProviderCapabilities(json_mode=True, usage=True))
        budget = ModelBudget(1536, 512, 300)
        for content, finish, transport_error, expected in (
                (success, 'stop', None, None),
                ('{"answer":', 'length', None, 'model_output_incomplete'),
                ('not JSON', 'stop', None, 'model_output_invalid'),
                ('{"wrong":"shape"}', 'stop', None, 'solver_output_invalid'),
                ('', 'stop', TimeoutError(), 'model_timeout')):
            with self.subTest(expected=expected):
                data = completion(content, usage={'prompt_tokens': 1052, 'completion_tokens': 384,
                                                   'total_tokens': 1436})
                data['choices'][0]['finish_reason'] = finish
                transport = FakeTransport(data, error=transport_error)
                provider = LocalHTTPProvider(profile, transport=transport,
                                             token_counter=lambda request, profile: 1052)
                solver = SolverAgent(provider)
                answer, response = solver.solve('Inspect the access requirement.', plan,
                                                evidence, model_budget=budget)
                self.assertEqual(answer.error_type, expected)
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(transport.calls[0][1]['max_tokens'], 512)
                if transport_error is not None:
                    self.assertIsNone(response)
                    self.assertEqual(answer.status, 'failed')
                else:
                    self.assertEqual(response.content, content)
                    self.assertEqual(response.finish_reason, finish)
                    self.assertEqual((response.usage_kind, response.usage),
                                     ('measured', {'input_tokens': 1052, 'output_tokens': 384}))
                    self.assertEqual(answer.status, 'completed' if expected is None else 'failed')
                # Exercise the production recording method without filesystem or retrieval work.
                trace = Trace('fixture', 'fixture', answer=answer)
                board, attempts = AgentBoard(), []
                Orchestrator.__new__(Orchestrator)._record_solver_attempt(
                    trace, board, solver, response, 'solver', True, attempts)
                self.assertEqual(len(trace.llm_calls), 1)
                record = trace.llm_calls[0]
                if transport_error is not None:
                    self.assertEqual((record['usage'], record['usage_kind'], record['finish_reason']),
                                     ({}, 'unknown', None))
                    self.assertIsNone(board.to_dict()['solver_attempts'][0]['payload']['raw_output'])
                else:
                    self.assertEqual((record['usage'], record['usage_kind'], record['finish_reason']),
                                     (response.usage, 'measured', finish))
                    self.assertEqual(board.to_dict()['solver_attempts'][0]['payload']['raw_output'], content)

    def test_invalid_profile_encoding_is_sanitized(self):
        self.assert_code('model_profile_invalid', lambda: validate_profile(fixture_profile(model_revision='\ud800')))

    def test_single_inflight_request_and_lock_released_after_error(self):
        transport = FakeTransport()
        provider = LocalHTTPProvider(fixture_profile(), transport=transport)
        transport.callback = lambda: self.assert_code('model_busy', lambda: provider.complete(self.request()))
        provider.complete(self.request())
        self.assertEqual(len(transport.calls), 1)
        transport.callback = None
        transport.error = TimeoutError()
        self.assert_code('model_timeout', lambda: provider.complete(self.request()))
        transport.error = None
        provider.complete(self.request())

    def test_profile_loader_is_explicit_bounded_and_rejects_unknown_fields(self):
        root = fixture_root()
        path = root / 'model-profile.json'
        path.write_text(json.dumps(asdict(fixture_profile())))
        loaded = load_profile(path)
        self.assertEqual(loaded, fixture_profile())
        raw = json.loads(path.read_text()); raw['credentials'] = 'fixture'
        path.write_text(json.dumps(raw))
        self.assert_code('model_profile_invalid', lambda: load_profile(path))
        path.write_text('{"profile_id":"fixture","profile_id":"duplicate"}')
        self.assert_code('model_profile_invalid', lambda: load_profile(path))
        path.write_text(' ' * 65537)
        self.assert_code('model_profile_invalid', lambda: load_profile(path))
        self.assert_code('model_profile_invalid', lambda: load_profile(Path('/tmp/undeclared-profile.json')))

    def test_cli_llm_check_local_and_existing_mock_rules(self):
        from macr import cli
        root = fixture_root(); path = root / 'model-profile.json'
        path.write_text(json.dumps(asdict(fixture_profile())))
        output = io.StringIO()
        with patch('sys.argv', ['agent-review', 'llm-check', '--provider', 'local', '--model-profile', str(path), '--input-token-upper-bound', '12']), \
                patch('macr.providers.local_http.LoopbackTransport', return_value=FakeTransport()), \
                patch('sys.stdout', output):
            cli.main()
        result = json.loads(output.getvalue())
        self.assertEqual(result['provider'], 'local_http')
        self.assertEqual(result['usage_kind'], 'unknown')
        self.assertEqual(result['metrics']['token_bound_source'], 'caller_supplied')
        self.assert_code('model_profile_missing', lambda: cli._provider('local'))
        self.assertIsNone(cli._provider('rules'))
        self.assertEqual(cli._provider('mock').name, 'mock')
        self.assert_code('remote_disabled', lambda: cli._provider('deepseek'))
        with patch('sys.argv', ['agent-review', 'llm-check', '--provider', 'local', '--model-profile', str(path)]), \
                patch('macr.providers.local_http.LoopbackTransport', return_value=FakeTransport()) as factory:
            with self.assertRaises(SystemExit) as error:
                cli.main()
            self.assertEqual(error.exception.code, 'model_token_accounting_unavailable')
            self.assertEqual(factory.return_value.calls, [])


class DiagnosticSchemaDecodingTests(unittest.TestCase):
    def setUp(self):
        from macr.evals.diagnostic_compact import build_candidate_request, parse_candidate, prepare_scope
        from macr.schemas import Evidence
        self.profile = fixture_profile(runtime='llama.cpp', runtime_version='b11521',
            context_limit=2048, output_limit=512, timeout_seconds=300,
            sampling={'seed': 0, 'temperature': 0, 'top_p': 1},
            capabilities=ProviderCapabilities(json_mode=True, usage=True))
        self.budget = ModelBudget(1536, 512, 300)
        self.scope = prepare_scope([Evidence('logic.py', 1, 2, 'Located implementation.',
            'def demo():\n    return 2\n', None, 'parse_ast')], case_id='schema-case', attempt_id='schema-attempt')
        self.task = {'question': 'Does demo meet the requirement?',
                     'scenario': 'Every call returns 2.', 'assumptions': []}
        self.request = build_candidate_request(self.scope, **self.task)
        self.wire = {'verdict': 'no_defect_supported', 'cause': 'The literal returned is 2.',
            'when': 'demo is called.', 'refs': ['e1'], 'unknowns': [], 'scope': self.scope.binding}
        self.candidate = parse_candidate(json.dumps(self.wire), self.scope)
        self.checked = {'scope': self.scope.binding, 'coverage': 'complete',
            'checks': {'requirement': {'state': 'established', 'refs': []},
                       'trigger': {'state': 'reachable', 'refs': []},
                       'behavior': {'state': 'satisfies', 'refs': ['e1']},
                       'protection': {'state': 'absent', 'refs': []},
                       'counterevidence': {'state': 'refutes', 'refs': ['e1']}},
            'reason': 'The literal return meets the stated domain.', 'unknowns': []}

    def provider(self, raw, *, enabled=True, finish='stop'):
        data = completion(raw, usage={'prompt_tokens': 9, 'completion_tokens': 4, 'total_tokens': 13})
        data['choices'][0]['finish_reason'] = finish
        transport = FakeTransport(data)
        transport.requests = []
        def count(request, profile):
            transport.requests.append(copy.deepcopy(request))
            return 9
        return LocalHTTPProvider(self.profile, transport=transport, token_counter=count,
                                 diagnostic_schema_decoding=enabled), transport

    def test_candidate_and_verifier_wire_differ_only_by_full_response_schema(self):
        from macr.agents.solver import SolverAgent
        from macr.evals.diagnostic_compact import protocol_schema
        captures = []
        for enabled in (False, True):
            provider, transport = self.provider(json.dumps(self.wire), enabled=enabled)
            solver = SolverAgent(provider)
            candidate, response, error, request = solver.diagnostic_candidate(
                self.scope, **self.task, model_budget=self.budget)
            self.assertIsNone(error)
            self.assertEqual(candidate, self.candidate)
            self.assertEqual(request.response_schema, protocol_schema(self.scope))
            transport.response = completion(json.dumps(self.checked),
                usage={'prompt_tokens': 9, 'completion_tokens': 4, 'total_tokens': 13})
            checked, response, error, verification_request = solver.diagnostic_verification(
                self.scope, request, candidate, model_budget=self.budget)
            self.assertIsNone(error)
            self.assertTrue(checked['accepted'])
            self.assertEqual(checked['final'], candidate)
            self.assertFalse(checked['semantic_proof'])
            self.assertEqual(len(transport.calls), 2)
            if enabled:
                for call, actual_request in zip(transport.calls, transport.requests):
                    self.assertEqual(call[1]['response_format']['json_schema']['schema'], actual_request.response_schema)
                self.assertEqual(asdict(verification_request), asdict(transport.requests[-1]))
                for body, actual_request in zip(transport.bodies, transport.requests):
                    self.assertIsInstance(body, bytes)
                    self.assertIn(b'"schema": ' + json.dumps(actual_request.response_schema).encode(), body)
            captures.append(copy.deepcopy(transport.calls))
        schemas = [protocol_schema(self.scope), protocol_schema(self.scope, verifier=True)]
        self.assertNotEqual(*schemas)
        for index, schema in enumerate(schemas):
            generic, typed = captures[0][index], captures[1][index]
            self.assertEqual(generic[1].pop('response_format'), {'type': 'json_object'})
            self.assertEqual(typed[1].pop('response_format'),
                             {'type': 'json_schema', 'json_schema': {'schema': schema}})
            self.assertEqual(generic, typed)

    def test_opt_in_requires_exact_bool_frozen_runtime_and_declared_json_mode(self):
        for enabled in (None, 0, 1, 'true'):
            with self.subTest(enabled=enabled), self.assertRaisesRegex(ProviderError, 'model_request_invalid'):
                LocalHTTPProvider(self.profile, diagnostic_schema_decoding=enabled)
        for profile in (replace(self.profile, runtime='other-runtime'),
                        replace(self.profile, runtime_version='b5083'),
                        replace(self.profile, capabilities=ProviderCapabilities())):
            transport = FakeTransport()
            with self.subTest(profile=profile), self.assertRaisesRegex(ProviderError, 'model_schema_decoding_unsupported'):
                LocalHTTPProvider(profile, transport=transport, diagnostic_schema_decoding=True)
            self.assertEqual(transport.calls, [])

    def test_metadata_cannot_enable_typed_generic_or_misroute_a_schema(self):
        from macr.evals.diagnostic_compact import VERSION, LEGACY_VERSION, protocol_schema
        for enabled, role in ((False, 'diagnostic_candidate'), (True, 'solver'), (True, None)):
            request = copy.deepcopy(self.request)
            request.metadata.update(role=role, diagnostic_schema_decoding=True)
            provider, transport = self.provider(json.dumps(self.wire), enabled=enabled)
            provider.complete(request)
            self.assertEqual(transport.calls[0][1]['response_format'], {'type': 'json_object'})
        for schema, metadata, tools in (
                (None, {'role': 'diagnostic_candidate', 'diagnostic_protocol': VERSION}, []),
                ({}, {'role': 'diagnostic_candidate', 'diagnostic_protocol': VERSION}, []),
                ({'type': 'object'}, {'role': 'diagnostic_candidate', 'diagnostic_protocol': VERSION}, []),
                ({'type': 'array'}, {'role': 'diagnostic_candidate', 'diagnostic_protocol': VERSION}, []),
                (self.request.response_schema | {'type': 'array'},
                 {'role': 'diagnostic_candidate', 'diagnostic_protocol': VERSION}, []),
                (self.request.response_schema | {'properties': {}},
                 {'role': 'diagnostic_candidate', 'diagnostic_protocol': VERSION}, []),
                (self.request.response_schema, {'role': 'diagnostic_candidate', 'diagnostic_protocol': LEGACY_VERSION}, []),
                (protocol_schema(self.scope, version=LEGACY_VERSION),
                 {'role': 'diagnostic_candidate', 'diagnostic_protocol': VERSION}, []),
                (self.request.response_schema, {'role': 'diagnostic_verifier', 'diagnostic_protocol': VERSION}, []),
                (self.request.response_schema, {'role': 'diagnostic_candidate', 'diagnostic_protocol': 'foreign'}, []),
                (self.request.response_schema, {'role': 'diagnostic_candidate', 'diagnostic_protocol': VERSION}, [{}])):
            request = replace(self.request, response_schema=schema, metadata=metadata, tools=tools)
            provider, transport = self.provider(json.dumps(self.wire))
            with self.subTest(schema=schema, metadata=metadata), self.assertRaisesRegex(
                    ProviderError, 'model_schema_decoding_request_invalid'):
                provider.complete(request)
            self.assertEqual(transport.calls, [])
            self.assertEqual(transport.requests, [])
            self.assertEqual(transport.bodies, [])
        provider, transport = self.provider(json.dumps(self.wire))
        transport.error = ProviderError('model_http_error')
        request = replace(self.request, metadata=self.request.metadata | {'role': 'diagnostic_candidate'})
        with self.assertRaisesRegex(ProviderError, 'model_http_error'):
            provider.complete(request)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0][1]['response_format']['type'], 'json_schema')

    def test_v3_complete_long_text_passes_while_explicit_legacy_v2_keeps_limits(self):
        from macr.agents.solver import SolverAgent
        from macr.evals.diagnostic_compact import LEGACY_VERSION, VERSION
        wire = self.wire | {'cause': 'The literal 2 satisfies the stated requirement for every invocation. ' * 10,
                            'when': 'For every allowed invocation, demo returns the same literal 2. ' * 6}
        checked = self.checked | {'reason': 'The complete visible return matches every stated allowed invocation. ' * 2}
        self.assertGreater(len(wire['cause']), 600)
        self.assertGreater(len(wire['when']), 300)
        self.assertGreater(len(checked['reason']), 96)
        for version in (LEGACY_VERSION, VERSION):
            provider, transport = self.provider(json.dumps(wire))
            solver = SolverAgent(provider)
            candidate, response, error, request = solver.diagnostic_candidate(
                self.scope, **self.task, model_budget=self.budget, version=version)
            self.assertEqual(response.content, json.dumps(wire))
            self.assertIn(b'"schema": ' + json.dumps(request.response_schema).encode(), transport.bodies[0])
            if version == LEGACY_VERSION:
                self.assertIsNone(candidate)
                self.assertEqual(error['error_type'], 'compact_contract_invalid')
                self.assertEqual(len(transport.calls), 1)
                continue
            self.assertIsNone(error)
            transport.response = completion(json.dumps(checked), usage={'prompt_tokens': 9, 'completion_tokens': 4})
            result, response, error, verifier = solver.diagnostic_verification(
                self.scope, request, candidate, model_budget=self.budget)
            self.assertIsNone(error)
            self.assertEqual(result['final'], candidate)
            self.assertEqual(response.content, json.dumps(checked))
            self.assertIn(b'"schema": ' + json.dumps(verifier.response_schema).encode(), transport.bodies[1])
            self.assertFalse(result['semantic_proof'])

    def test_no_citable_scope_sends_only_the_legal_unknown_union(self):
        from macr.agents.solver import SolverAgent
        from macr.evals.diagnostic_compact import prepare_scope
        scope = prepare_scope([], case_id='empty-scope', attempt_id='empty-attempt')
        raw = json.dumps({'verdict': 'unknown', 'cause': None, 'when': None, 'refs': [],
                         'unknowns': ['The task needs implementation evidence that is unavailable.'],
                         'scope': scope.binding})
        provider, transport = self.provider(raw)
        candidate, response, error, request = SolverAgent(provider).diagnostic_candidate(
            scope, **self.task, model_budget=self.budget)
        self.assertIsNone(error)
        self.assertEqual(candidate['verdict'], 'unknown')
        self.assertEqual(len(request.response_schema['oneOf']), 1)
        self.assertEqual(request.response_schema['oneOf'][0]['properties']['verdict']['const'], 'unknown')
        self.assertIn(b'"schema": ' + json.dumps(request.response_schema).encode(), transport.bodies[0])

    def test_typed_candidate_keeps_shared_validation_and_failed_raw(self):
        from macr.agents.solver import SolverAgent
        for changes, finish, expected in (
                ({'extra': True}, 'stop', 'compact_contract_invalid'),
                ({'refs': {}}, 'stop', 'compact_contract_invalid'),
                ({'verdict': 'healthy'}, 'stop', 'compact_contract_invalid'),
                ({'cause': ''}, 'stop', 'compact_contract_invalid'),
                ({'scope': '0' * 16}, 'stop', 'compact_scope_mismatch'),
                ({'refs': ['foreign']}, 'stop', 'compact_contract_invalid'),
                ({}, 'length', 'model_output_incomplete')):
            raw = json.dumps(self.wire | changes)
            provider, transport = self.provider(raw, finish=finish)
            candidate, response, error, request = SolverAgent(provider).diagnostic_candidate(
                self.scope, **self.task, model_budget=self.budget)
            with self.subTest(changes=changes, finish=finish):
                self.assertIsNone(candidate)
                self.assertEqual(error['error_type'], expected)
                self.assertEqual(response.content, raw)
                self.assertEqual(response.finish_reason, finish)
                self.assertEqual((response.usage_kind, response.usage),
                                 ('measured', {'input_tokens': 9, 'output_tokens': 4}))
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(transport.calls[0][1]['response_format']['type'], 'json_schema')

    def test_typed_verifier_keeps_structure_source_coverage_and_state_gates(self):
        from macr.agents.solver import SolverAgent
        missing_coverage = copy.deepcopy(self.checked)
        missing_coverage.pop('coverage')
        bad_enum = copy.deepcopy(self.checked)
        bad_enum['checks']['trigger']['state'] = 'possible'
        conflict = copy.deepcopy(self.checked)
        conflict['checks']['behavior']['state'] = 'violates'
        foreign = copy.deepcopy(self.checked)
        foreign['checks']['behavior']['refs'] = ['foreign']
        missing_ref = copy.deepcopy(self.checked)
        missing_ref['checks']['behavior']['refs'] = []
        for checked, finish, expected, parsed in (
                (self.checked | {'verdict': 'no_defect_supported'}, 'stop', 'compact_contract_invalid', False),
                (self.checked | {'checks': []}, 'stop', 'compact_contract_invalid', False),
                (bad_enum, 'stop', 'compact_contract_invalid', False),
                (missing_coverage, 'stop', 'compact_contract_invalid', False),
                (self.checked | {'coverage': 'incomplete'}, 'stop', 'verification_task_incomplete', True),
                (self.checked | {'scope': '0' * 16}, 'stop', 'compact_scope_mismatch', False),
                (foreign, 'stop', 'compact_contract_invalid', False),
                (missing_ref, 'stop', 'compact_contract_invalid', False),
                (conflict, 'stop', 'verification_cross_field_conflict', True),
                (self.checked | {'reason': ''}, 'stop', 'compact_contract_invalid', False),
                (self.checked, 'length', 'model_output_incomplete', False)):
            raw = json.dumps(checked)
            provider, transport = self.provider(raw, finish=finish)
            result, response, error, request = SolverAgent(provider).diagnostic_verification(
                self.scope, self.request, self.candidate, model_budget=self.budget)
            with self.subTest(expected=expected, finish=finish):
                if parsed:
                    self.assertIsNone(error)
                    self.assertFalse(result['accepted'])
                    self.assertIsNone(result['final'])
                    self.assertEqual(result['error_code'], expected)
                else:
                    self.assertIsNone(result)
                    self.assertEqual(error['error_type'], expected)
                self.assertEqual(response.content, raw)
                self.assertEqual(response.finish_reason, finish)
                self.assertEqual((response.usage_kind, response.usage),
                                 ('measured', {'input_tokens': 9, 'output_tokens': 4}))
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(transport.calls[0][1]['response_format']['type'], 'json_schema')


class TransportBoundaryTests(unittest.TestCase):
    def assert_code(self, code, fn):
        with self.assertRaises(ProviderError) as error:
            fn()
        self.assertEqual(error.exception.code, code)

    def test_default_transport_ignores_proxy_and_auth_and_blocks_redirect(self):
        class Response:
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, bound): return b'{"choices":[]}'
        opener = unittest.mock.Mock()
        opener.open.return_value = Response()
        captured = []
        def builder(*handlers):
            captured.extend(handlers); return opener
        with patch('urllib.request.build_opener', side_effect=builder), \
                patch.dict('os.environ', {'HTTP_PROXY': 'http://example.invalid', 'OPENAI_API_KEY': 'fixture'}):
            result = LoopbackTransport().post('http://localhost:9876/v1/chat/completions', b'{}', 1, 1024)
        self.assertEqual(result, {'choices': []})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'http://127.0.0.1:9876/v1/chat/completions')
        self.assertFalse(request.has_header('Authorization'))
        proxy = next(h for h in captured if isinstance(h, __import__('urllib.request', fromlist=['ProxyHandler']).ProxyHandler))
        self.assertEqual(proxy.proxies, {})
        redirect = next(h for h in captured if hasattr(h, 'redirect_request'))
        self.assert_code('model_redirect_blocked', lambda: redirect.redirect_request(None, None, 302, None, None, 'http://example.invalid'))

    def test_error_body_limit_timeout_and_content_length_are_bounded(self):
        opener = unittest.mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError('fixture', 400, 'fixture', {}, io.BytesIO(b'x' * 1025))
        with patch('urllib.request.build_opener', return_value=opener):
            self.assert_code('model_response_limit', lambda: LoopbackTransport().post('http://127.0.0.1:9876/v1/chat/completions', b'{}', 1, 1024))
        class TimedBody(io.BytesIO):
            def read(self, bound): raise TimeoutError('fixture')
        opener.open.side_effect = urllib.error.HTTPError('fixture', 400, 'fixture', {}, TimedBody())
        with patch('urllib.request.build_opener', return_value=opener):
            self.assert_code('model_timeout', lambda: LoopbackTransport().post('http://127.0.0.1:9876/v1/chat/completions', b'{}', 1, 1024))
        class Response:
            headers = {'Content-Length': '1025'}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, bound): raise AssertionError('oversized declared body must not be read')
        opener.open.side_effect = None; opener.open.return_value = Response()
        with patch('urllib.request.build_opener', return_value=opener):
            self.assert_code('model_response_limit', lambda: LoopbackTransport().post('http://127.0.0.1:9876/v1/chat/completions', b'{}', 1, 1024))

    def test_response_limit_malformed_encoding_duplicate_json_and_http_errors(self):
        class Response:
            headers = {}
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, bound): return self.body[:bound]
        for body, code in ((b'x' * 1025, 'model_response_limit'), (b'', 'model_response_invalid'),
                           (b'\xff', 'model_response_invalid'), (b'{"choices":[],"choices":[]}', 'model_response_invalid'),
                           (b'{"value":NaN}', 'model_response_invalid')):
            opener = unittest.mock.Mock(); opener.open.return_value = Response(body)
            with patch('urllib.request.build_opener', return_value=opener):
                self.assert_code(code, lambda: LoopbackTransport().post('http://127.0.0.1:9876/v1/chat/completions', b'{}', 1, 1024))
        opener = unittest.mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError('fixture', 400, 'fixture', {}, io.BytesIO(b'{"error":{"code":"context_length_exceeded"}}'))
        with patch('urllib.request.build_opener', return_value=opener):
            self.assert_code('model_context_overflow', lambda: LoopbackTransport().post('http://127.0.0.1:9876/v1/chat/completions', b'{}', 1, 1024))
        opener.open.side_effect = urllib.error.URLError(TimeoutError('fixture'))
        with patch('urllib.request.build_opener', return_value=opener):
            self.assert_code('model_timeout', lambda: LoopbackTransport().post('http://127.0.0.1:9876/v1/chat/completions', b'{}', 1, 1024))
