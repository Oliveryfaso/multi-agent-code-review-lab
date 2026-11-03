import copy
import json
import unittest
from hashlib import sha256

from macr.providers import base
from macr.providers.deepseek import DeepSeekProvider
from macr.providers.response_normalization import decode_provider_json


def identity_evidence():
    return {'schema': 'macr.provider.identity.v1', 'provider': 'deepseek',
            'request_sha256': 'a' * 64, 'requested_model': 'deepseek-flash',
            'model': 'deepseek-flash', 'model_state': 'validated',
            'system_fingerprint': 'fp-synthetic', 'fingerprint_state': 'validated'}


def response_data():
    return {'model': 'deepseek-flash', 'system_fingerprint': 'fp-synthetic',
            'usage': {'prompt_tokens': 10, 'completion_tokens': 2},
            'choices': [{'finish_reason': 'tool_calls', 'message': {
                'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'call_1', 'type': 'function', 'function': {
                        'name': 'inspect_file', 'arguments': '{synthetic-invalid-json'}}]}}]}


class FixtureTransport:
    def __init__(self, data):
        self.data, self.body = data, None

    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        self.body = body
        return copy.deepcopy(self.data)


class IdentityEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(base, 'validate_identity_evidence', None)),
                        'independent identity evidence validator missing')

    def assert_invalid(self, evidence):
        with self.assertRaisesRegex(ValueError, '^provider_identity_evidence_invalid$'):
            base.validate_identity_evidence(evidence)

    def test_validator_retains_only_the_exact_bounded_identity_schema(self):
        evidence = identity_evidence()
        result = base.validate_identity_evidence(evidence)
        self.assertEqual(result, identity_evidence())
        self.assertIsNot(result, evidence)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode('utf-8')), 4096)
        for name in evidence:
            missing = dict(evidence)
            del missing[name]
            with self.subTest(missing=name):
                self.assert_invalid(missing)
        self.assert_invalid(dict(evidence, content='DO-NOT-RETAIN'))
        self.assert_invalid(None)
        self.assert_invalid([])

    def test_validator_rejects_invalid_scalar_types_and_request_binding(self):
        for name, values in {
            'schema': (None, True, 'macr.provider.identity.v2'),
            'provider': (None, True, 'mock'),
            'request_sha256': (None, True, 'a' * 63, 'a' * 65, 'A' * 64, 'g' * 64),
            'requested_model': (None, True, '', '  ', 'x' * 257, '\ud800'),
            'model': (None, True, '', '  ', 'x' * 257, '\ud800', 'another-model'),
            'model_state': (None, True, [], 'unknown'),
            'system_fingerprint': (None, True, 42, [], {}, '', ' \n', 'x' * 257, '\ud800'),
            'fingerprint_state': (None, True, [], 'mismatch'),
        }.items():
            for invalid in values:
                with self.subTest(field=name, kind=type(invalid).__name__):
                    self.assert_invalid(dict(identity_evidence(), **{name: invalid}))

    def test_validator_requires_null_for_nonvalidated_states(self):
        for state in ('missing', 'invalid', 'mismatch'):
            evidence = dict(identity_evidence(), model=None, model_state=state)
            self.assertEqual(base.validate_identity_evidence(evidence), evidence)
            self.assert_invalid(dict(evidence, model='untrusted-model'))
        for state in ('missing', 'invalid'):
            evidence = dict(identity_evidence(), system_fingerprint=None, fingerprint_state=state)
            self.assertEqual(base.validate_identity_evidence(evidence), evidence)
            self.assert_invalid(dict(evidence, system_fingerprint='untrusted-fingerprint'))

    def test_validator_accepts_utf8_text_at_the_character_boundary(self):
        evidence = dict(identity_evidence(), requested_model='😀' * 256,
                        model='😀' * 256, system_fingerprint='😀' * 256)
        self.assertEqual(base.validate_identity_evidence(evidence), evidence)

    def test_validator_rejects_json_escaping_beyond_the_total_byte_limit(self):
        self.assert_invalid(dict(identity_evidence(), requested_model='\x00' * 256,
                                 model='\x00' * 256, system_fingerprint='\x00' * 256))

    def test_provider_error_validates_and_returns_independent_identity_copies(self):
        original = identity_evidence()
        error = base.ProviderError('model_response_invalid', identity_evidence=original)
        original['system_fingerprint'] = 'changed'
        first = error.identity_evidence
        self.assertEqual(first, identity_evidence())
        first['model_state'] = 'invalid'
        self.assertEqual(error.identity_evidence, identity_evidence())
        self.assertIsNot(first, error.identity_evidence)
        self.assertIsNone(error.response)
        self.assertIsNone(error.billing_evidence)
        self.assertIsNone(base.ProviderError('model_timeout').identity_evidence)
        with self.assertRaisesRegex(ValueError, '^provider_identity_evidence_invalid$'):
            base.ProviderError('model_response_invalid', identity_evidence={})


class DeepSeekIdentityRetentionTests(unittest.TestCase):
    def request(self):
        return base.ConversationRequest(messages=[{'role': 'user', 'content': 'synthetic fixture'}],
            tools=[{'type': 'function', 'function': {'name': 'inspect_file',
                    'parameters': {'type': 'object'}}}], max_output_tokens=20)

    def fail(self, data):
        transport = FixtureTransport(data)
        provider = DeepSeekProvider(model='deepseek-flash', transport=transport)
        with self.assertRaises(base.ProviderError) as caught:
            provider.converse(self.request())
        self.assertTrue(hasattr(caught.exception, 'identity_evidence'),
                        'failure identity evidence missing')
        return caught.exception, transport

    def test_invalid_tool_arguments_retain_actual_identity_and_independent_billing(self):
        error, transport = self.fail(response_data())
        expected = dict(identity_evidence(), request_sha256=sha256(transport.body).hexdigest())
        self.assertEqual(error.identity_evidence, expected)
        self.assertEqual(error.diagnostic['validation_error'], 'tool_arguments_json')
        self.assertIsNone(error.response)
        self.assertEqual(error.billing_evidence['raw_usage'],
                         {'prompt_tokens': 10, 'completion_tokens': 2})
        self.assertNotIn('synthetic-invalid-json', json.dumps(error.identity_evidence))

    def test_fingerprint_presence_type_size_and_encoding_are_independent_of_model(self):
        cases = [('missing', None, 'missing'), ('present', None, 'invalid'),
                 ('present', 42, 'invalid'), ('present', True, 'invalid'),
                 ('present', [], 'invalid'), ('present', {}, 'invalid'),
                 ('present', '', 'invalid'), ('present', ' \n', 'invalid'),
                 ('present', 'x' * 257, 'invalid'), ('present', '\ud800', 'invalid'),
                 ('present', '😀' * 256, 'validated')]
        for presence, value, state in cases:
            with self.subTest(presence=presence, kind=type(value).__name__, state=state):
                data = response_data()
                if presence == 'missing':
                    del data['system_fingerprint']
                else:
                    data['system_fingerprint'] = value
                error, _ = self.fail(data)
                self.assertEqual(error.identity_evidence['fingerprint_state'], state)
                self.assertEqual(error.identity_evidence['system_fingerprint'],
                                 value if state == 'validated' else None)
                self.assertEqual(error.identity_evidence['model_state'], 'validated')
                self.assertIsNotNone(error.billing_evidence)

    def test_model_states_preserve_a_valid_fingerprint_independently(self):
        cases = [('missing', None, 'missing'), ('present', None, 'invalid'),
                 ('present', 42, 'invalid'), ('present', True, 'invalid'),
                 ('present', '', 'invalid'), ('present', ' \n', 'invalid'),
                 ('present', 'x' * 257, 'invalid'), ('present', '\ud800', 'invalid'),
                 ('present', 'another-model', 'mismatch')]
        for presence, value, state in cases:
            with self.subTest(presence=presence, state=state, kind=type(value).__name__):
                data = response_data()
                if presence == 'missing':
                    del data['model']
                else:
                    data['model'] = value
                error, _ = self.fail(data)
                self.assertEqual(error.identity_evidence['model_state'], state)
                self.assertIsNone(error.identity_evidence['model'])
                self.assertEqual(error.identity_evidence['system_fingerprint'], 'fp-synthetic')
                self.assertEqual(error.identity_evidence['fingerprint_state'], 'validated')
                self.assertIsNone(error.billing_evidence)

    def test_unknown_usage_does_not_discard_identity(self):
        data = response_data()
        del data['usage']
        error, _ = self.fail(data)
        self.assertEqual(error.identity_evidence['system_fingerprint'], 'fp-synthetic')
        self.assertEqual(error.identity_evidence['model_state'], 'validated')
        self.assertIsNone(error.billing_evidence)

    def test_finish_usage_and_response_contract_failures_retain_identity(self):
        for stage in ('finish_reason', 'usage', 'response_contract', 'incomplete', 'finish_consistency'):
            with self.subTest(stage=stage):
                data = response_data()
                choice = data['choices'][0]
                choice['message'] = {'role': 'assistant', 'content': 'answer'}
                choice['finish_reason'] = 'stop'
                if stage == 'finish_reason':
                    choice['finish_reason'] = None
                elif stage == 'usage':
                    data['usage'] = {'prompt_tokens': 'invalid', 'completion_tokens': 2}
                elif stage == 'response_contract':
                    data['system_fingerprint'] = 42
                elif stage == 'incomplete':
                    choice['finish_reason'] = 'length'
                else:
                    choice['finish_reason'] = 'tool_calls'
                error, _ = self.fail(data)
                self.assertEqual(error.identity_evidence['model_state'], 'validated')
                self.assertEqual(error.identity_evidence['system_fingerprint'],
                                 None if stage == 'response_contract' else 'fp-synthetic')

    def test_invalid_envelopes_never_supply_identity(self):
        invalid = [None, [], dict(response_data(), error=None),
                   dict(response_data(), choices=[]), dict(response_data(), choices=[{} , {}]),
                   dict(response_data(), choices=[None])]
        for data in invalid:
            with self.subTest(kind=type(data).__name__):
                error, _ = self.fail(data)
                self.assertIsNone(error.identity_evidence)

    def test_request_model_and_hash_bind_to_frozen_wire_body(self):
        provider = DeepSeekProvider(model='deepseek-flash')

        class MutatingTransport(FixtureTransport):
            def post(self, *args):
                provider.model = 'mutated-after-request'
                return super().post(*args)

        transport = MutatingTransport(response_data())
        provider.transport = transport
        with self.assertRaises(base.ProviderError) as caught:
            provider.converse(self.request())
        self.assertTrue(hasattr(caught.exception, 'identity_evidence'),
                        'failure identity evidence missing')
        evidence = caught.exception.identity_evidence
        self.assertEqual(evidence['requested_model'], 'deepseek-flash')
        self.assertEqual(evidence['model'], 'deepseek-flash')
        self.assertEqual(evidence['model_state'], 'validated')
        self.assertEqual(evidence['request_sha256'], sha256(transport.body).hexdigest())

    def test_malformed_json_transport_never_supplies_identity(self):
        class MalformedTransport:
            def post(self, *args):
                return decode_provider_json(b'{"model":"deepseek-flash",')

        with self.assertRaises(base.ProviderError) as caught:
            DeepSeekProvider(model='deepseek-flash', transport=MalformedTransport()).converse(self.request())
        self.assertTrue(hasattr(caught.exception, 'identity_evidence'),
                        'failure identity evidence missing')
        self.assertIsNone(caught.exception.identity_evidence)
        self.assertIsNone(caught.exception.billing_evidence)

    def test_transport_errors_cannot_forge_identity_or_billing(self):
        self.assertTrue(callable(getattr(base, 'validate_identity_evidence', None)),
                        'independent identity evidence validator missing')
        for code in ('model_transport', 'model_response_invalid'):
            with self.subTest(code=code):
                forged = base.ProviderError(code, identity_evidence=identity_evidence(),
                    billing_evidence={'schema': 'macr.provider.billing.v1', 'provider': 'deepseek',
                        'model': 'deepseek-flash', 'request_sha256': 'a' * 64,
                        'max_output_tokens': 20, 'raw_usage': {
                            'prompt_tokens': 10, 'completion_tokens': 2}, 'latency_ms': 0})

                class ForgedTransport:
                    def post(self, *args):
                        raise forged

                with self.assertRaises(base.ProviderError) as caught:
                    DeepSeekProvider(model='deepseek-flash', transport=ForgedTransport()).converse(self.request())
                self.assertEqual(caught.exception.code, code)
                self.assertIsNone(caught.exception.identity_evidence)
                self.assertIsNone(caught.exception.billing_evidence)


if __name__ == '__main__':
    unittest.main()
