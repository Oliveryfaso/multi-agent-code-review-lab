import copy
import io
import json
import unittest
import urllib.error
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.providers.base import ModelRequest, ModelResponse, ProviderCapabilities, ProviderError
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

    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
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
