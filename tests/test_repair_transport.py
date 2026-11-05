"""Official HTTP boundary exercised only through intercepted urllib I/O."""
import io
import json
import pickle
import traceback
import urllib.error
import urllib.request
from unittest import TestCase
from unittest.mock import Mock, patch

from macr.providers.base import ProviderError
from macr.providers.deepseek import _ApprovedTransport, _NoRedirect
from macr.providers.repair_transport import OfficialRepairTransport


ENDPOINT = 'https://api.deepseek.com/chat/completions'
DUMMY = 'DUMMY-REPAIR-CREDENTIAL-NOT-A-REAL-KEY'
MIB = 1024 * 1024


class ControlledResponse:
    def __init__(self, body=b'{"ok":true}', *, headers=None):
        self.body = body
        self.headers = headers or {'x-request-id': 'a' * 32, 'ignored-header': DUMMY}
        self.status = 200
        self.read_bounds = []
        self.closed = False

    def read(self, size):
        self.read_bounds.append(size)
        return self.body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True


class RepairTransportTests(TestCase):
    def setUp(self):
        self.response = ControlledResponse()
        self.opener = Mock()
        self.opener.open.return_value = self.response
        self.build = patch('urllib.request.build_opener', return_value=self.opener).start()
        self.addCleanup(patch.stopall)
        patch('socket.socket.connect', side_effect=AssertionError('network forbidden')).start()
        self.reader = Mock(return_value=DUMMY)

    def invoke(self, transport=None, *, endpoint=ENDPOINT, body=b'{}', timeout=2.5, limit=128):
        transport = transport or OfficialRepairTransport(self.reader, allow_remote=True)
        return transport.post(endpoint, body, timeout, limit)

    def safe_error(self, error, code):
        self.assertIs(type(error), ProviderError)
        self.assertEqual(error.code, code)
        for value in (str(error), repr(error), repr(vars(error)),
                      ''.join(traceback.format_exception(type(error), error, error.__traceback__))):
            self.assertNotIn(DUMMY, value)
        self.assertIsNone(error.__context__)
        self.assertIsNone(error.__cause__)

    def test_disabled_never_reads_credentials_or_constructs_opener(self):
        transport = OfficialRepairTransport(self.reader)
        with self.assertRaises(ProviderError) as raised:
            self.invoke(transport)
        self.safe_error(raised.exception, 'remote_disabled')
        self.reader.assert_not_called()
        self.build.assert_not_called()
        self.assertIsNone(transport.response_metadata)

    def test_enabled_is_read_only_configuration_not_identity_attestation(self):
        for enabled in (False, True):
            transport = OfficialRepairTransport(self.reader, allow_remote=enabled)
            self.assertIs(transport.enabled, enabled)
            with self.assertRaises(AttributeError):
                transport.enabled = not enabled
        self.reader.assert_not_called()
        self.build.assert_not_called()

    def test_invalid_arguments_reject_before_credentials_or_http(self):
        invalid = [
            {'endpoint': 'http://api.deepseek.com/chat/completions'},
            {'endpoint': ENDPOINT + '/'}, {'endpoint': ENDPOINT + '?extra=1'},
            {'endpoint': 'https://api.deepseek.com:443/chat/completions'},
            {'endpoint': 'https://api.deepseek.com.evil.invalid/chat/completions'},
            {'endpoint': None}, {'body': b''}, {'body': b'x' * (MIB + 1)},
            {'body': '{}'}, {'body': bytearray(b'{}')},
            *({'timeout': value} for value in (0, -1, True, float('nan'),
                float('inf'), float('-inf'), 301, 10 ** 1000, '2')),
            *({'limit': value} for value in (0, -1, True, 1.5, MIB + 1, None)),
        ]
        for arguments in invalid:
            with self.subTest(arguments=list(arguments)), self.assertRaises(ProviderError) as raised:
                self.invoke(**arguments)
            self.safe_error(raised.exception, 'model_request_invalid')
        self.reader.assert_not_called()
        self.build.assert_not_called()

    def test_explicit_configuration_and_nonserializable_reader(self):
        for value in (1, 'yes', None):
            with self.subTest(value=value), self.assertRaises(ProviderError):
                OfficialRepairTransport(self.reader, allow_remote=value)
        with self.assertRaises(ProviderError):
            OfficialRepairTransport(None, allow_remote=True)
        transport = OfficialRepairTransport(self.reader, allow_remote=True)
        with self.assertRaises(TypeError):
            pickle.dumps(transport)
        self.assertNotIn(DUMMY, repr(transport))
        self.reader.assert_not_called()

    def test_real_request_uses_fixed_endpoint_bearer_no_proxy_and_no_redirect(self):
        instances = []
        def make_transport(key):
            instance = _ApprovedTransport(key)
            instances.append(instance)
            return instance
        transport = OfficialRepairTransport(self.reader, allow_remote=True)
        with patch('macr.providers.repair_transport._ApprovedTransport', side_effect=make_transport):
            self.assertEqual(self.invoke(transport), {'ok': True})
        request = self.opener.open.call_args.args[0]
        self.assertIsInstance(request, urllib.request.Request)
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.data, b'{}')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + DUMMY)
        self.assertEqual(request.get_header('Content-type'), 'application/json')
        self.assertEqual(self.opener.open.call_args.kwargs, {'timeout': 2.5})
        handlers = self.build.call_args.args
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], _NoRedirect)
        self.assertEqual(self.response.read_bounds, [129])
        self.assertTrue(self.response.closed)
        self.assertEqual(len(instances), 1)
        self.assertIsNone(instances[0]._api_key)
        self.assertNotIn('_api_key', vars(transport))
        self.assertNotIn(DUMMY, repr(vars(transport)))
        self.reader.assert_called_once_with()
        self.opener.open.assert_called_once()

    def test_inclusive_wire_and_timeout_bounds(self):
        self.assertEqual(self.invoke(body=b'x' * MIB, timeout=300, limit=MIB), {'ok': True})
        self.assertEqual(self.response.read_bounds, [MIB + 1])

    def test_metadata_is_safe_detached_and_reset_before_invalid_call(self):
        transport = OfficialRepairTransport(self.reader, allow_remote=True)
        self.invoke(transport)
        metadata = transport.response_metadata
        self.assertEqual(metadata['http_status'], 200)
        self.assertEqual(metadata['request_id'], 'a' * 32)
        self.assertNotIn(DUMMY, json.dumps(metadata))
        metadata['http_status'] = 599
        self.assertEqual(transport.response_metadata['http_status'], 200)
        with self.assertRaises(ProviderError):
            self.invoke(transport, timeout=0)
        self.assertIsNone(transport.response_metadata)
        self.assertEqual(self.reader.call_count, 1)

    def test_credential_errors_never_escape_or_open_http(self):
        for value in ('', ' ' + DUMMY, DUMMY + '\n', None, b'DUMMY', 'x' * 4097):
            with self.subTest(kind=type(value).__name__):
                self.reader.return_value = value
                with self.assertRaises(ProviderError) as raised:
                    self.invoke()
                self.safe_error(raised.exception, 'remote_config_missing')
        for error in (RuntimeError(DUMMY), ProviderError(DUMMY)):
            self.reader.side_effect = error
            with self.assertRaises(ProviderError) as raised:
                self.invoke()
            self.safe_error(raised.exception, 'remote_config_missing')
        self.build.assert_not_called()

    def test_http_error_closes_body_without_retaining_error_or_headers(self):
        raw_body = io.BytesIO(DUMMY.encode())
        error = urllib.error.HTTPError(ENDPOINT + DUMMY, 401, DUMMY,
                    {'x-request-id': DUMMY, 'secret-header': DUMMY}, raw_body)
        self.opener.open.side_effect = error
        transport = OfficialRepairTransport(self.reader, allow_remote=True)
        with self.assertRaises(ProviderError) as raised:
            self.invoke(transport)
        self.safe_error(raised.exception, 'model_transport')
        self.assertTrue(raw_body.closed)
        self.assertEqual(transport.response_metadata['http_status'], 401)
        self.assertNotIn(DUMMY, json.dumps(transport.response_metadata))
        self.assertNotIn('request_id', transport.response_metadata)
        self.opener.open.assert_called_once()

    def test_timeout_unknown_and_forged_provider_errors_are_sanitized(self):
        forged = ProviderError('model_transport', diagnostic={
            'schema': 'macr.provider.failure.v1', 'stage': 'http_status',
            'request_id_present': True, 'request_id': 'a' * 32})
        for error, code in ((TimeoutError(DUMMY), 'model_timeout'),
                (urllib.error.URLError(TimeoutError(DUMMY)), 'model_timeout'),
                (urllib.error.URLError(DUMMY), 'model_transport'),
                (RuntimeError(DUMMY), 'model_transport'),
                (ProviderError(DUMMY), 'model_transport'), (forged, 'model_transport')):
            self.opener.open.side_effect = error
            transport = OfficialRepairTransport(self.reader, allow_remote=True)
            with self.subTest(error=type(error).__name__), self.assertRaises(ProviderError) as raised:
                self.invoke(transport)
            self.safe_error(raised.exception, code)
            self.assertIsNone(transport.response_metadata)
            self.assertIsNone(raised.exception.diagnostic)

    def test_read_failure_also_clears_temporary_credential(self):
        instances = []
        def make_transport(key):
            instance = _ApprovedTransport(key); instances.append(instance); return instance
        self.response.read = Mock(side_effect=RuntimeError(DUMMY))
        transport = OfficialRepairTransport(self.reader, allow_remote=True)
        with patch('macr.providers.repair_transport._ApprovedTransport', side_effect=make_transport):
            with self.assertRaises(ProviderError) as raised:
                self.invoke(transport)
        self.safe_error(raised.exception, 'model_transport')
        self.assertIsNone(instances[0]._api_key)
        self.assertEqual(transport.response_metadata['http_status'], 200)

    def test_unknown_construction_metadata_and_http_close_failures_stay_sanitized(self):
        for boundary in ('opener', 'metadata', 'http_close'):
            self.build.side_effect = None
            self.opener.open.side_effect = None
            self.response.headers = {'x-request-id': 'a' * 32}
            if boundary == 'opener':
                self.build.side_effect = RuntimeError(DUMMY)
            elif boundary == 'metadata':
                headers = Mock()
                headers.get.side_effect = RuntimeError(DUMMY)
                self.response.headers = headers
            else:
                error = urllib.error.HTTPError(ENDPOINT, 503, DUMMY, {}, None)
                error.close = Mock(side_effect=RuntimeError(DUMMY))
                self.opener.open.side_effect = error
            transport = OfficialRepairTransport(self.reader, allow_remote=True)
            with self.subTest(boundary=boundary), self.assertRaises(ProviderError) as raised:
                self.invoke(transport)
            self.safe_error(raised.exception, 'model_transport')
            self.assertNotIn(DUMMY, json.dumps(transport.response_metadata))

    def test_redirect_and_response_bounds_use_existing_http_implementation(self):
        def redirect(request, timeout):
            self.build.call_args.args[1].redirect_request(request, None, 302, DUMMY, {}, 'https://example.invalid')
        self.opener.open.side_effect = redirect
        with self.assertRaises(ProviderError) as raised:
            self.invoke()
        self.safe_error(raised.exception, 'model_redirect_blocked')
        self.opener.open.side_effect = None
        self.response.body = b'x' * 129
        with self.assertRaises(ProviderError) as raised:
            self.invoke()
        self.safe_error(raised.exception, 'model_response_limit')
        self.assertEqual(self.response.read_bounds, [129])

    def test_each_post_reads_once_and_clears_prior_metadata_after_credential_failure(self):
        transport = OfficialRepairTransport(self.reader, allow_remote=True)
        self.invoke(transport); self.invoke(transport)
        self.assertEqual(self.reader.call_count, 2)
        self.assertEqual(self.opener.open.call_count, 2)
        self.reader.side_effect = RuntimeError(DUMMY)
        with self.assertRaises(ProviderError):
            self.invoke(transport)
        self.assertIsNone(transport.response_metadata)
        self.assertEqual(self.opener.open.call_count, 2)

    def test_current_credential_echo_in_strings_keys_lists_or_escaped_json_is_rejected(self):
        values = [
            {'summary': 'prefix ' + DUMMY + ' suffix'},
            {'nested': {'other': [False, {'summary': DUMMY}]}},
            {'nested': {'prefix-' + DUMMY: 'ordinary value'}},
        ]
        bodies = [json.dumps(value).encode() for value in values]
        bodies.append(b'{"summary":"' + DUMMY.replace('D', r'\u0044', 1).encode() + b'"}')
        for body in bodies:
            with self.subTest(body_length=len(body)):
                self.response.body = body
                transport = OfficialRepairTransport(self.reader, allow_remote=True)
                captured = captured_traceback = None
                try:
                    self.invoke(transport, limit=MIB)
                except ProviderError as error:
                    captured, captured_traceback = error, error.__traceback__
                self.assertIsNotNone(captured)
                self.safe_error(captured, 'model_response_credential_echo')
                self.assertIsNone(captured.response)
                self.assertIsNone(captured.billing_evidence)
                self.assertIsNone(captured.identity_evidence)
                self.assertNotIn(DUMMY, json.dumps(transport.response_metadata))
                frame = captured_traceback
                while frame:
                    if frame.tb_frame.f_code.co_name == 'post':
                        self.assertIsNone(frame.tb_frame.f_locals.get('result'))
                        self.assertIsNone(frame.tb_frame.f_locals.get('key'))
                        self.assertIsNone(frame.tb_frame.f_locals['temporary']._api_key)
                    frame = frame.tb_next

    def test_known_proposal_json_decoding_cannot_materialize_an_escaped_credential(self):
        encoded = json.dumps({'summary': DUMMY}).replace(DUMMY, DUMMY.replace('-', r'\u002d'))
        self.assertNotIn(DUMMY, encoded)
        self.response.body = json.dumps({'choices': [
            {'message': {'role': 'assistant', 'content': encoded}}]}).encode()
        with self.assertRaises(ProviderError) as raised:
            self.invoke(limit=MIB)
        self.safe_error(raised.exception, 'model_response_credential_echo')

    def test_nonmatching_content_is_preserved_and_tree_resource_limits_fail_closed(self):
        normal = {'choices': [{'message': {'role': 'assistant',
                    'content': json.dumps({'status': 'no_candidate', 'summary': 'Ordinary public text'})}}],
                  'usage': {'prompt_tokens': 10, 'completion_tokens': 5}}
        self.response.body = json.dumps(normal).encode()
        self.assertEqual(self.invoke(limit=MIB), normal)
        deep = b'{"nested":' + b'[' * 65 + b'0' + b']' * 65 + b'}'
        wide = json.dumps({'items': [None] * 65536}).encode()
        for body in (deep, wide):
            with self.subTest(body_length=len(body)):
                self.assertLessEqual(len(body), MIB)
                self.response.body = body
                with self.assertRaises(ProviderError) as raised:
                    self.invoke(limit=MIB)
                self.safe_error(raised.exception, 'model_response_limit')

    def test_uninspectable_known_proposal_content_is_rejected_without_a_raw_response(self):
        invalid = ['{', '{"summary":"one","summary":"two"}', '["ordinary"]',
                   '{"nested":' + '[' * 65 + '0' + ']' * 65 + '}',
                   json.dumps({'summary': 'é' * 33000}, ensure_ascii=False)]
        for content in invalid:
            with self.subTest(content_length=len(content)):
                self.response.body = json.dumps({'choices': [
                    {'message': {'role': 'assistant', 'content': content}}]}).encode()
                with self.assertRaises(ProviderError) as raised:
                    self.invoke(limit=MIB)
                self.safe_error(raised.exception, 'model_response_invalid')
                self.assertIsNone(raised.exception.response)
        for content in ('', None):
            data = {'choices': [{'message': {'role': 'assistant', 'content': content}}]}
            self.response.body = json.dumps(data).encode()
            self.assertEqual(self.invoke(limit=MIB), data)
