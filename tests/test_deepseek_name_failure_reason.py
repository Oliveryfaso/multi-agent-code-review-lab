"""Fixed, nontext tool-name diagnostics; no provider or task credentials."""
import copy
import json
import unittest

from macr.providers.base import ConversationRequest, ProviderError
from macr.providers.deepseek import DeepSeekProvider
from macr.providers.failure_evidence import validate_failure_diagnostic
from macr.providers.response_normalization import (
    ResponseNormalizationError, normalize_assistant_message)


def message(name):
    return {'role': 'assistant', 'content': None, 'tool_calls': [{
        'id': 'fixture-call', 'type': 'function', 'index': 0,
        'function': {'name': name, 'arguments': '{}'}}]}


class FixtureTransport:
    def __init__(self, name):
        self.payload = {'model': 'deepseek-flash', 'system_fingerprint': 'fp-fixture',
            'choices': [{'message': message(name), 'finish_reason': 'tool_calls'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12,
                'prompt_cache_hit_tokens': 0, 'prompt_cache_miss_tokens': 10,
                'completion_tokens_details': {'reasoning_tokens': 1}}}
        self.calls = 0

    def post(self, *args):
        self.calls += 1
        return copy.deepcopy(self.payload)


class DeepSeekNameFailureReasonTests(unittest.TestCase):
    def test_rejection_code_stays_compatible_but_reason_distinguishes_three_predicates(self):
        for name, reason in ((None, 'type'), (False, 'type'), ([], 'type'),
                (' finish', 'format'), ('functions.finish', 'format'),
                ('x' * 129, 'format'), ('read_symbol', 'not_declared'),
                ('unknown_valid_name', 'not_declared')):
            with self.subTest(reason=reason, kind=type(name).__name__):
                with self.assertRaises(ResponseNormalizationError) as caught:
                    normalize_assistant_message(message(name), tool_names={'finish', 'ask_user'})
                self.assertEqual(caught.exception.code, 'tool_function_name')
                self.assertEqual(str(caught.exception), 'tool_function_name')
                self.assertEqual(getattr(caught.exception, 'tool_name_reason', None), reason)

    def test_reason_does_not_trim_alias_or_relax_other_valid_declared_names(self):
        for name in ('finish', 'ask_user', 'read-file', '9lookup'):
            with self.subTest(name=name):
                observed = normalize_assistant_message(message(name), tool_names={name})
                self.assertEqual(observed['tool_calls'][0]['function']['name'], name)
                self.assertNotIn('index', observed['tool_calls'][0])

    def test_provider_retains_billing_identity_and_only_a_finite_tool_set_label(self):
        private = 'PRIVATE_UNDECLARED_NAME_SENTINEL'
        for names, label in ((('finish', 'ask_user'), 'terminal_only'),
                             (('inspect_file',), 'other'), ((), 'none')):
            with self.subTest(label=label):
                wire = FixtureTransport(private)
                tools = [{'type': 'function', 'function': {'name': name,
                    'parameters': {'type': 'object', 'properties': {}}}} for name in names]
                request = ConversationRequest(messages=[{'role': 'user', 'content': 'synthetic'}],
                    tools=tools, max_output_tokens=128)
                with self.assertRaises(ProviderError) as caught:
                    DeepSeekProvider(model='deepseek-flash', transport=wire).converse(request)
                error = caught.exception
                self.assertEqual(error.code, 'model_response_invalid')
                self.assertEqual(error.diagnostic.get('validation_error'), 'tool_function_name')
                self.assertEqual(error.diagnostic.get('tool_name_reason'), 'not_declared')
                self.assertEqual(error.diagnostic.get('declared_tool_set'), label)
                self.assertEqual(error.identity_evidence['model_state'], 'validated')
                self.assertEqual(error.identity_evidence['fingerprint_state'], 'validated')
                self.assertEqual(error.billing_evidence['raw_usage']['completion_tokens'], 2)
                self.assertNotIn(private, json.dumps(error.diagnostic))
                self.assertEqual(wire.calls, 1)

    def test_schema_accepts_fixed_reason_and_local_context_with_legacy_error(self):
        original = {'schema': 'macr.provider.failure.v1', 'stage': 'assistant_message',
            'validation_error': 'tool_function_name', 'tool_name_reason': 'format',
            'declared_tool_set': 'terminal_only', 'policy_decision': 3}
        self.assertEqual(validate_failure_diagnostic(original), original)
        legacy = {'schema': 'macr.provider.failure.v1', 'stage': 'assistant_message',
                  'validation_error': 'tool_function_name'}
        self.assertEqual(validate_failure_diagnostic(legacy), legacy)
        self.assertNotIn('tool_name_reason', validate_failure_diagnostic(legacy))

    def test_schema_rejects_unbounded_or_inconsistent_context_without_echo(self):
        baseline = {'schema': 'macr.provider.failure.v1', 'stage': 'assistant_message',
                    'validation_error': 'tool_function_name'}
        for key, bad in (('tool_name_reason', 'PRIVATE_REASON_SENTINEL'),
                ('tool_name_reason', True), ('declared_tool_set', 'PRIVATE_SET_SENTINEL'),
                ('declared_tool_set', []), ('policy_decision', True),
                ('policy_decision', 0), ('policy_decision', 4)):
            with self.subTest(key=key, kind=type(bad).__name__):
                with self.assertRaisesRegex(ValueError, '^provider_failure_diagnostic_invalid$'):
                    validate_failure_diagnostic(baseline | {key: bad})
        with self.assertRaisesRegex(ValueError, '^provider_failure_diagnostic_invalid$'):
            validate_failure_diagnostic(baseline | {
                'validation_error': 'role_invalid', 'tool_name_reason': 'type'})


if __name__ == '__main__':
    unittest.main()
