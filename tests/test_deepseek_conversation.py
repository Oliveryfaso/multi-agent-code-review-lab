import copy
import json
import unittest
from unittest.mock import patch
import urllib.error
from hashlib import sha256

from macr.providers import base
from macr.providers.deepseek import DeepSeekProvider


CALL = {'id': 'call_1', 'type': 'function',
        'function': {'name': 'inspect_file', 'arguments': '{"path":"fixture.py"}'}}
TOOLS = [{'type': 'function', 'function': {'name': 'inspect_file',
          'parameters': {'type': 'object', 'properties': {}}}}]


class FakeTransport:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.calls = payload, error, []

    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        self.calls.append((endpoint, json.loads(body), timeout_seconds, max_response_bytes))
        if self.error:
            raise self.error
        return self.payload


def completion(content='answer', *, calls=None, finish='stop', reasoning='fixture reasoning'):
    message = {'role': 'assistant', 'content': content, 'reasoning_content': reasoning}
    if calls is not None:
        message['tool_calls'] = copy.deepcopy(calls)
    return {'choices': [{'message': message, 'finish_reason': finish}],
            'model': 'fixture-deepseek', 'system_fingerprint': 'fp_fixture',
            'usage': {'prompt_tokens': 10, 'completion_tokens': 4, 'total_tokens': 14,
                      'prompt_cache_hit_tokens': 6, 'prompt_cache_miss_tokens': 4,
                      'prompt_tokens_details': {'cached_tokens': 6},
                      'completion_tokens_details': {'reasoning_tokens': 3}}}


class DeepSeekConversationTests(unittest.TestCase):
    def test_wire_metadata_is_removed_before_strict_validation_and_replay(self):
        sentinel = 'EXTENSION_VALUE_MUST_NOT_REPLAY'
        data = completion(None, calls=[CALL], finish='tool_calls')
        data['choices'][0]['message']['annotations'] = [{'private': sentinel}]
        data['choices'][0]['message']['tool_calls'][0]['index'] = 0
        data['choices'][0]['message']['tool_calls'][0]['function']['vendor_metadata'] = sentinel
        provider = DeepSeekProvider(model='fixture-deepseek', transport=FakeTransport(data))
        response = provider.converse(self.request())
        self.assertEqual(response.tool_calls, [CALL])
        self.assertEqual(response.assistant_message['reasoning_content'], 'fixture reasoning')
        self.assertNotIn(sentinel, json.dumps(response.assistant_message))
        replay = base.ConversationRequest(messages=[{'role': 'user', 'content': 'fixture'},
            response.assistant_message, {'role': 'tool', 'tool_call_id': 'call_1', 'content': '{}'}],
            tools=TOOLS)
        wire = provider.encode_conversation_request(replay)
        self.assertNotIn(sentinel.encode(), wire)
        self.assertNotIn(b'vendor_metadata', wire)

    def test_invalid_assistant_preserves_independent_request_bound_billing_usage(self):
        class Wire(FakeTransport):
            def post(self, endpoint, body, timeout_seconds, max_response_bytes):
                self.body = body
                return super().post(endpoint, body, timeout_seconds, max_response_bytes)
        data = completion()
        data['choices'][0]['message']['role'] = 'invalid-role'
        data['unrelated_metadata'] = 'VALUE_NEVER_BILLED_OR_LOGGED'
        wire = Wire(data)
        provider = DeepSeekProvider(model='fixture-deepseek', transport=wire)
        error = self.assert_code('model_response_invalid', lambda: provider.converse(self.request()))
        self.assertIsNone(error.response)
        evidence = getattr(error, 'billing_evidence', None)
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence['request_sha256'], sha256(wire.body).hexdigest())
        self.assertEqual((evidence['model'], evidence['raw_usage']['prompt_tokens'],
                          evidence['raw_usage']['completion_tokens']), ('fixture-deepseek', 10, 4))
        self.assertNotIn('VALUE_NEVER', json.dumps(evidence))

    def test_transport_uncertainty_cannot_supply_a_trusted_response_or_billing(self):
        partial = DeepSeekProvider(transport=FakeTransport(completion())).converse(self.request())
        for code in ('model_timeout', 'model_transport'):
            with self.subTest(code=code):
                error = self.assert_code(code, lambda: DeepSeekProvider(transport=FakeTransport(
                    error=base.ProviderError(code, response=partial))).converse(self.request()))
                self.assertIsNone(error.response)
                self.assertIsNone(error.billing_evidence)

    def test_http_duplicate_fields_never_become_a_billable_envelope(self):
        from macr.providers.deepseek import _ApprovedTransport
        raw = b'{"usage":{"prompt_tokens":10,"prompt_tokens":100,"completion_tokens":4},"model":"fixture-deepseek","choices":[]}'
        class Reply:
            status = 200
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return raw[:limit]
        class Opener:
            def open(self, *args, **kwargs): return Reply()
        with patch('macr.providers.deepseek.urllib.request.build_opener', return_value=Opener()):
            error = self.assert_code('model_response_invalid', lambda: _ApprovedTransport('DUMMY').post(
                'https://api.deepseek.com/chat/completions', b'{}', 1, 4096))
        self.assertEqual(error.diagnostic['stage'], 'json_decode')
        self.assertEqual(error.diagnostic['validation_error'], 'duplicate_json_key')
        self.assertIsNone(error.response)
        self.assertIsNone(error.billing_evidence)

    def test_failure_diagnostic_is_bounded_validated_and_not_aliased(self):
        from macr.providers.failure_evidence import validate_failure_diagnostic
        evidence = {'schema': 'macr.provider.failure.v1', 'stage': 'usage'}
        error = base.ProviderError('model_response_invalid', diagnostic=evidence)
        evidence['stage'] = 'transport'
        self.assertEqual(error.diagnostic['stage'], 'usage')
        observed = error.diagnostic
        observed['content'] = 'SENTINEL_NEVER_LOG'
        self.assertNotIn('content', error.diagnostic)
        for invalid in (observed, {'schema': 'future', 'stage': 'usage'},
                        {'schema': 'macr.provider.failure.v1', 'stage': 'arbitrary'},
                        {'schema': 'macr.provider.failure.v1', 'stage': 'usage',
                         'http_status': True},
                        {'schema': 'macr.provider.failure.v1', 'stage': 'usage',
                         'request_id': 'sk-SENTINEL_NEVER_LOG'}):
            with self.subTest(invalid=tuple(invalid)):
                with self.assertRaises(ValueError):
                    validate_failure_diagnostic(invalid)
                with self.assertRaises(ValueError):
                    base.ProviderError('model_response_invalid', diagnostic=invalid)

    def test_http_request_id_never_keeps_credential_echo_or_substring(self):
        from macr.providers.failure_evidence import http_failure_metadata
        token = '1234567890abcdef' * 2
        secret = 'sk-' + token + 'abcdef1234567890'
        for identifier, key in ((token, token), (token.upper(), secret),
                ('chatcmpl-' + token, secret), ('abcdef1234567890' + token[:16],
                 'sk-abcdef1234567890' + token[:16]),
                (token, 'abcdef')):
            with self.subTest(wrapper=identifier.startswith('chatcmpl-')):
                evidence = http_failure_metadata('http_status', status=200,
                    request_id=identifier, secret=key)
                self.assertTrue(evidence['request_id_present'])
                self.assertNotIn('request_id', evidence)

    def test_invalid_provider_shapes_have_distinct_safe_failure_stages(self):
        cases = []
        cases.append(([], 'response_envelope'))
        cases.append(({'choices': []}, 'response_envelope'))
        data = completion()
        del data['choices'][0]['message']['content']
        cases.append((data, 'assistant_message'))
        data = completion(None, calls=[CALL], finish='tool_calls')
        data['choices'][0]['message']['tool_calls'][0]['type'] = 'unsupported-action'
        cases.append((data, 'assistant_message'))
        data = completion()
        data['usage']['prompt_tokens'] = 'not an integer'
        cases.append((data, 'usage'))
        data = completion()
        data['model'] = 17
        cases.append((data, 'response_contract'))
        data = completion()
        data['choices'][0]['finish_reason'] = 'future_unknown'
        cases.append((data, 'response_contract'))
        for data, stage in cases:
            with self.subTest(stage=stage):
                error = self.assert_code('model_response_invalid', lambda: DeepSeekProvider(
                    transport=FakeTransport(data)).converse(self.request()))
                self.assertIsNone(error.response)
                self.assertEqual(error.diagnostic['stage'], stage)
                self.assertLessEqual(len(json.dumps(error.diagnostic).encode()), 4096)

    def test_diagnostics_exclude_raw_contents_unknown_keys_and_error_text(self):
        sentinel = 'SENTINEL_KEY_AND_PRIVATE_TEXT_NEVER_LOG'
        data = completion(content=sentinel, reasoning=sentinel)
        data['model'] = sentinel
        data['id'] = sentinel
        data['system_fingerprint'] = sentinel
        data[sentinel] = sentinel
        data['choices'][0]['message'][sentinel] = sentinel
        data['choices'][0]['message']['tool_calls'] = [copy.deepcopy(CALL)]
        data['choices'][0]['message']['tool_calls'][0]['function']['arguments'] = sentinel
        error = self.assert_code('model_response_invalid', lambda: DeepSeekProvider(
            transport=FakeTransport(data)).converse(self.request()))
        encoded = json.dumps(error.diagnostic)
        self.assertNotIn(sentinel, encoded)
        self.assertNotIn('arguments', error.diagnostic)
        self.assertEqual(error.diagnostic['request_id_present'], True)
        self.assertNotIn('request_id', error.diagnostic)
        data = {'error': {'message': sentinel, 'code': sentinel}, sentinel: sentinel}
        error = self.assert_code('model_http_error', lambda: DeepSeekProvider(
            transport=FakeTransport(data)).converse(self.request()))
        self.assertNotIn(sentinel, json.dumps(error.diagnostic))

    def test_finish_consistency_keeps_valid_response_and_separate_diagnostic(self):
        error = self.assert_code('model_response_invalid', lambda: DeepSeekProvider(
            transport=FakeTransport(completion(None, calls=[CALL], finish='stop'))
            ).converse(self.request()))
        self.assertIsInstance(error.response, base.ConversationResponse)
        self.assertEqual(error.diagnostic['stage'], 'finish_consistency')
        self.assertEqual(error.diagnostic['finish_reason'], 'stop')
        self.assertEqual(error.diagnostic['tool_calls_count'], 1)

    def test_transport_decode_and_http_status_keep_safe_metadata_only(self):
        from macr.providers.deepseek import _ApprovedTransport
        identifier = '12345678-1234-1234-1234-123456789abc'
        class Reply:
            status = 200
            headers = {'x-request-id': identifier,
                       'Authorization': 'SENTINEL_NEVER_LOG'}
            def __init__(self, body):
                self.body = body
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, bound):
                return self.body[:bound]
        class Opener:
            def __init__(self, answer):
                self.answer = answer
            def open(self, *args, **kwargs):
                if isinstance(self.answer, BaseException):
                    raise self.answer
                return self.answer
        for raw in (b'{SENTINEL_NEVER_LOG', b'\xff'):
            with self.subTest(raw_kind=raw[:1]), patch(
                    'macr.providers.deepseek.urllib.request.build_opener',
                    return_value=Opener(Reply(raw))):
                transport = _ApprovedTransport('DUMMY_KEY_NEVER_LOG')
                error = self.assert_code('model_response_invalid', lambda: transport.post(
                    'https://api.deepseek.com/chat/completions', b'{}', 1, 1024))
                self.assertEqual(error.diagnostic['stage'], 'json_decode')
                self.assertEqual(error.diagnostic['http_status'], 200)
                self.assertEqual(error.diagnostic['response_bytes'], len(raw))
                self.assertEqual(error.diagnostic['request_id'], identifier)
                self.assertNotIn('SENTINEL', json.dumps(error.diagnostic))
        class UnreadBody:
            read_count = 0
            closed = False
            def read(self, *args):
                self.read_count += 1
                raise AssertionError('error body must not be read')
            def close(self):
                self.closed = True
        body = UnreadBody()
        http_error = urllib.error.HTTPError('https://api.deepseek.com', 400,
            'SENTINEL_NEVER_LOG', {'x-request-id': identifier}, body)
        with patch('macr.providers.deepseek.urllib.request.build_opener',
                   return_value=Opener(http_error)):
            error = self.assert_code('model_transport', lambda: _ApprovedTransport(
                'DUMMY_KEY_NEVER_LOG').post('https://api.deepseek.com/chat/completions',
                                          b'{}', 1, 1024))
        self.assertEqual(error.diagnostic['stage'], 'http_status')
        self.assertEqual(error.diagnostic['http_status'], 400)
        self.assertNotIn('SENTINEL', json.dumps(error.diagnostic))
        self.assertEqual(body.read_count, 0)
        self.assertTrue(body.closed)
        transport = _ApprovedTransport('DUMMY_KEY_NEVER_LOG')
        with patch('macr.providers.deepseek.urllib.request.build_opener',
                   side_effect=[Opener(Reply(b'{}')), Opener(TimeoutError())]):
            transport.post('https://api.deepseek.com/chat/completions', b'{}', 1, 1024)
            self.assertEqual(transport.response_metadata['http_status'], 200)
            error = self.assert_code('model_timeout', lambda: transport.post(
                'https://api.deepseek.com/chat/completions', b'{}', 1, 1024))
        self.assertEqual(error.diagnostic['stage'], 'transport')
        self.assertNotIn('http_status', error.diagnostic)
        self.assertNotIn('request_id', error.diagnostic)
        self.assertIsNone(transport.response_metadata)

    def setUp(self):
        self.assertTrue(hasattr(base, 'ConversationRequest'), 'opt-in conversation contract missing')

    def request(self, messages=None, **kwargs):
        return base.ConversationRequest(
            messages if messages is not None else [{'role': 'user', 'content': 'fixture'}],
            tools=kwargs.pop('tools', copy.deepcopy(TOOLS)), timeout_seconds=2, **kwargs)

    def assert_code(self, code, action):
        with self.assertRaises(base.ProviderError) as error:
            action()
        self.assertEqual(error.exception.code, code)
        self.assertEqual(str(error.exception), code)
        return error.exception

    def test_tool_message_replays_reasoning_and_nested_usage_without_aliasing(self):
        wire = completion(None, calls=[CALL], finish='tool_calls')
        transport = FakeTransport(wire)
        provider = DeepSeekProvider(transport=transport, model='fixture-deepseek')
        response = provider.converse(self.request())
        self.assertIsInstance(response, base.ConversationResponse)
        self.assertIsInstance(response, base.ModelResponse)
        self.assertEqual(response.contract_version, base.CONVERSATION_CONTRACT_VERSION)
        self.assertEqual(response.content, '')
        self.assertEqual(response.assistant_message, wire['choices'][0]['message'])
        self.assertEqual(response.raw_usage, wire['usage'])
        self.assertEqual(response.usage['input_tokens'], 10)
        self.assertEqual(response.usage['output_tokens'], 4)
        self.assertEqual(response.usage_kind, 'measured')
        self.assertEqual((response.model, response.system_fingerprint, response.finish_reason),
                         ('fixture-deepseek', 'fp_fixture', 'tool_calls'))
        messages = [{'role': 'user', 'content': 'fixture'}, response.assistant_message,
                    {'role': 'tool', 'tool_call_id': 'call_1', 'content': 'fixture result'}]
        provider.converse(self.request(messages))
        self.assertEqual(transport.calls[1][1]['messages'], messages)
        wire['choices'][0]['message']['tool_calls'][0]['function']['name'] = 'changed'
        wire['usage']['prompt_tokens_details']['cached_tokens'] = 0
        self.assertEqual(response.tool_calls[0]['function']['name'], 'inspect_file')
        self.assertEqual(response.raw_usage['prompt_tokens_details']['cached_tokens'], 6)

    def test_explicit_version_and_legacy_contract_stay_separate(self):
        transport = FakeTransport(completion())
        provider = DeepSeekProvider(transport=transport)
        self.assert_code('model_request_invalid', lambda: provider.converse(
            base.ModelRequest([{'role': 'user', 'content': 'fixture'}])))
        self.assert_code('model_request_invalid', lambda: provider.converse(
            self.request(contract_version='conversation.future')))
        self.assertEqual(transport.calls, [])
        legacy = provider.complete(base.ModelRequest([{'role': 'user', 'content': 'fixture'}]))
        self.assertIs(type(legacy), base.ModelResponse)
        self.assert_code('model_request_invalid', lambda: base.validate_request(
            base.ModelRequest([{'role': 'assistant', 'content': None, 'tool_calls': [CALL]}])))

    def test_bad_message_shapes_fail_before_transport(self):
        invalid = [
            [{'role': 'user', 'content': 'fixture', 'unexpected': 'value'}],
            [{'role': 'tool', 'content': 'orphan', 'tool_call_id': 'call_1'}],
            [{'role': 'assistant', 'content': None, 'tool_calls': [CALL]}],
            [{'role': 'assistant', 'content': None, 'tool_calls': [CALL]},
             {'role': 'tool', 'content': 'wrong', 'tool_call_id': 'call_other'}],
            [{'role': 'assistant', 'content': None, 'tool_calls': [CALL]},
             {'role': 'user', 'content': 'interrupt'}],
            [{'role': 'assistant', 'content': None, 'tool_calls': [CALL]},
             {'role': 'tool', 'content': 'once', 'tool_call_id': 'call_1'},
             {'role': 'tool', 'content': 'twice', 'tool_call_id': 'call_1'}],
            [{'role': 'assistant', 'content': None, 'tool_calls': [CALL, CALL]}],
            [{'role': 'assistant', 'content': '', 'reasoning_content': {}}],
            [{'role': 'assistant', 'content': '', 'tool_calls': 'invalid'}],
            [{'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': 'x', 'type': 'function', 'function': {'name': 'inspect_file', 'arguments': {}}}]}],
            [{'role': 'tool', 'content': '', 'tool_call_id': 7}],
        ]
        for messages in invalid:
            with self.subTest(messages=messages):
                transport = FakeTransport(completion())
                self.assert_code('model_request_invalid', lambda: DeepSeekProvider(
                    transport=transport).converse(self.request(messages)))
                self.assertEqual(transport.calls, [])

    def test_parallel_tool_results_can_arrive_in_either_order(self):
        second = copy.deepcopy(CALL)
        second['id'] = 'call_2'
        messages = [{'role': 'user', 'content': 'fixture'},
                    {'role': 'assistant', 'content': '', 'reasoning_content': 'retain all reasoning',
                     'tool_calls': [CALL, second]},
                    {'role': 'tool', 'content': '', 'tool_call_id': 'call_2'},
                    {'role': 'tool', 'content': 'result', 'tool_call_id': 'call_1'}]
        transport = FakeTransport(completion())
        DeepSeekProvider(transport=transport).converse(self.request(messages))
        self.assertEqual(transport.calls[0][1]['messages'], messages)

    def test_empty_tool_content_is_valid_and_argument_schema_stays_caller_owned(self):
        call = copy.deepcopy(CALL)
        call['function']['arguments'] = '{"unknown_to_schema":true}'
        for content in (None, ''):
            with self.subTest(content=content):
                response = DeepSeekProvider(transport=FakeTransport(
                    completion(content, calls=[call], finish='tool_calls'))).converse(self.request())
                self.assertEqual(response.assistant_message['content'], content)
                self.assertEqual(response.tool_calls[0]['function']['arguments'], '{"unknown_to_schema":true}')

    def test_invalid_or_duplicate_argument_json_is_rejected_before_tool_use(self):
        for arguments, code in (('{unfinished', 'tool_arguments_json'),
                                ('{"path":"a","path":"b"}', 'duplicate_json_key')):
            with self.subTest(code=code):
                call = copy.deepcopy(CALL)
                call['function']['arguments'] = arguments
                error = self.assert_code('model_response_invalid', lambda: DeepSeekProvider(
                    transport=FakeTransport(completion(None, calls=[call], finish='tool_calls'))
                    ).converse(self.request()))
                self.assertIsNone(error.response)
                self.assertEqual(error.diagnostic['validation_error'], code)

    def test_blank_final_and_unsolicited_tool_calls_fail_explicitly(self):
        for content in (None, '', ' \n'):
            with self.subTest(content=content):
                error = self.assert_code('model_response_invalid', lambda: DeepSeekProvider(
                    transport=FakeTransport(completion(content))).converse(self.request()))
                self.assertIsInstance(error.response, base.ConversationResponse)
        self.assert_code('model_response_invalid', lambda: DeepSeekProvider(transport=FakeTransport(
            completion(None, calls=[CALL], finish='tool_calls'))).converse(self.request(tools=[])))

    def test_truncation_and_interruption_retain_failure_evidence(self):
        for reason in ('length', 'content_filter', 'insufficient_system_resource', 'aborted'):
            with self.subTest(reason=reason):
                error = self.assert_code('model_output_incomplete', lambda: DeepSeekProvider(
                    transport=FakeTransport(completion('{partial', finish=reason))).converse(self.request()))
                self.assertEqual(error.failure_kind, 'incomplete')
                self.assertEqual(error.response.content, '{partial')
                self.assertEqual(error.response.finish_reason, reason)
                self.assertEqual(error.response.raw_usage['completion_tokens_details']['reasoning_tokens'], 3)
        error = self.assert_code('model_output_incomplete', lambda: DeepSeekProvider(
            transport=FakeTransport(completion(None, calls=[CALL], finish='length'))).converse(self.request()))
        self.assertEqual(error.response.assistant_message['content'], None)
        self.assertEqual(error.response.tool_calls, [CALL])

    def test_failure_envelope_validates_the_conversational_fields(self):
        response = DeepSeekProvider(transport=FakeTransport(completion())).converse(self.request())
        for field, value in [('contract_version', 'future'), ('system_fingerprint', 7),
                             ('assistant_message', {'role': 'assistant', 'content': 'changed'}),
                             ('raw_usage', {'untrusted': 'payload'}), ('tool_calls', [CALL]),
                             ('usage', {'input_tokens': 0, 'output_tokens': 0})]:
            with self.subTest(field=field):
                invalid = copy.deepcopy(response)
                setattr(invalid, field, value)
                self.assert_code('provider_contract', lambda: base.ProviderError(
                    'model_output_incomplete', response=invalid))

    def test_malformed_wire_fields_and_usage_are_rejected(self):
        invalid = [None, [], {'choices': []}]
        for path, value in [
            (('choices',), [completion()['choices'][0], completion()['choices'][0]]),
            (('choices', 0, 'message', 'role'), 'tool'),
            (('choices', 0, 'message', 'content'), False),
            (('choices', 0, 'message', 'content'), '\ud800'),
            (('choices', 0, 'message', 'reasoning_content'), {}),
            (('choices', 0, 'message', 'tool_calls'), [{}]),
            (('choices', 0, 'finish_reason'), 'future_unknown'),
            (('model',), 7), (('model',), None), (('model',), '\ud800'),
            (('system_fingerprint',), 'x' * 257),
            (('usage',), []), (('usage', 'prompt_tokens'), True),
            (('usage', 'total_tokens'), {'untrusted': 1}),
            (('usage', 'completion_tokens'), -1),
            (('usage', 'total_tokens'), 99),
            (('usage', 'prompt_cache_miss_tokens'), 9),
            (('usage', 'prompt_tokens_details'), []),
            (('usage', 'completion_tokens_details', 'reasoning_tokens'), 2 ** 100),
            (('usage', 'completion_tokens_details', 'reasoning_tokens'), 5),
        ]:
            data = completion()
            target = data
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            invalid.append(data)
        missing_model = completion()
        del missing_model['model']
        invalid.append(missing_model)
        for data in invalid:
            with self.subTest(data=data):
                self.assert_code('model_response_invalid', lambda: DeepSeekProvider(
                    transport=FakeTransport(data)).converse(self.request()))

    def test_unknown_usage_is_not_fabricated_and_arbitrary_usage_is_discarded(self):
        data = completion()
        data['usage'] = {'prompt_tokens': 10, 'untrusted': {'large': 'payload'},
                         'prompt_tokens_details': {'cached_tokens': 6, 'untrusted': 'payload'}}
        response = DeepSeekProvider(transport=FakeTransport(data)).converse(self.request())
        self.assertEqual((response.usage_kind, response.usage), ('unknown', {}))
        self.assertEqual(response.raw_usage, {'prompt_tokens': 10,
                         'prompt_tokens_details': {'cached_tokens': 6}})

    def test_transport_failure_and_remote_default_never_expose_error_text(self):
        with patch('macr.providers.deepseek._ApprovedTransport', side_effect=AssertionError('network forbidden')):
            self.assert_code('remote_disabled', lambda: DeepSeekProvider().converse(self.request()))
        for failure, code in [(TimeoutError('private detail'), 'model_timeout'),
                              (RuntimeError('private detail'), 'model_transport')]:
            self.assert_code(code, lambda: DeepSeekProvider(transport=FakeTransport(
                error=failure)).converse(self.request()))
        self.assert_code('model_http_error', lambda: DeepSeekProvider(transport=FakeTransport(
            {'error': {'message': 'private server detail'}})).converse(self.request()))

    def test_request_limits_and_json_mode_reach_fake_transport(self):
        transport = FakeTransport(completion())
        provider = DeepSeekProvider(transport=transport, timeout_seconds=1, model='configured-model')
        provider.converse(self.request(max_input_tokens=100, max_output_tokens=20,
                                      response_schema={'type': 'object'}))
        endpoint, payload, timeout, bound = transport.calls[0]
        self.assertEqual(endpoint, 'https://api.deepseek.com/chat/completions')
        self.assertEqual(payload['model'], 'configured-model')
        self.assertEqual(payload['max_tokens'], 20)
        self.assertFalse(payload['stream'])
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        self.assertEqual((timeout, bound), (1, 1024 * 1024))
        for values in ({'max_input_tokens': True}, {'max_output_tokens': 0},
                       {'tools': [{'non_json': float('nan')}]}):
            with self.subTest(values=values):
                invalid_transport = FakeTransport(completion())
                self.assert_code('model_request_invalid', lambda: DeepSeekProvider(
                    transport=invalid_transport).converse(self.request(**values)))
                self.assertEqual(invalid_transport.calls, [])

    def test_explicit_thinking_configuration_reaches_transport_without_model_inference(self):
        self.assertIn('thinking', base.ConversationRequest.__dataclass_fields__)
        self.assertIn('reasoning_effort', base.ConversationRequest.__dataclass_fields__)
        transport = FakeTransport(completion())
        provider = DeepSeekProvider(transport=transport, model='manifest-selected-model')
        provider.converse(self.request(thinking={'type': 'enabled'}, reasoning_effort='high'))
        payload = transport.calls[0][1]
        self.assertEqual(payload['model'], 'manifest-selected-model')
        self.assertEqual(payload['thinking'], {'type': 'enabled'})
        self.assertEqual(payload['reasoning_effort'], 'high')
        self.assertNotIn('temperature', payload)
        with patch('macr.providers.deepseek._ApprovedTransport', side_effect=AssertionError('network forbidden')):
            self.assert_code('remote_disabled', lambda: DeepSeekProvider(model='deepseek-flash').converse(
                self.request(thinking={'type': 'enabled'}, reasoning_effort='high')))

    def test_optional_thinking_settings_are_not_fabricated(self):
        transport = FakeTransport(completion())
        DeepSeekProvider(transport=transport).converse(self.request())
        payload = transport.calls[0][1]
        self.assertNotIn('temperature', payload)
        self.assertNotIn('thinking', payload)
        self.assertNotIn('reasoning_effort', payload)
        for settings in ({'thinking': {'type': 'enabled'}}, {'thinking': {'type': 'disabled'}},
                         {'reasoning_effort': 'none'}, {'reasoning_effort': 'low'},
                         {'reasoning_effort': 'high'}, {'reasoning_effort': 'max'}):
            with self.subTest(settings=settings):
                transport = FakeTransport(completion())
                DeepSeekProvider(transport=transport).converse(self.request(**settings))
                emitted = {name: transport.calls[0][1][name] for name in ('thinking', 'reasoning_effort')
                           if name in transport.calls[0][1]}
                self.assertEqual(emitted, settings)

    def test_invalid_or_conflicting_thinking_settings_fail_before_transport(self):
        self.assertIn('thinking', base.ConversationRequest.__dataclass_fields__)
        for settings in ({'thinking': 'enabled'}, {'thinking': {}},
                         {'thinking': {'type': 'enabled', 'extra': 'unbound'}},
                         {'thinking': {'type': True}}, {'thinking': {'type': 'unknown'}},
                         {'reasoning_effort': True}, {'reasoning_effort': []},
                         {'reasoning_effort': 'medium'}, {'reasoning_effort': 'ultra'},
                         {'thinking': {'type': 'enabled'}, 'reasoning_effort': 'none'},
                         {'thinking': {'type': 'disabled'}, 'reasoning_effort': 'high'}):
            with self.subTest(settings=settings):
                transport = FakeTransport(completion())
                self.assert_code('model_request_invalid', lambda: DeepSeekProvider(
                    transport=transport).converse(self.request(**settings)))
                self.assertEqual(transport.calls, [])


if __name__ == '__main__':
    unittest.main()
