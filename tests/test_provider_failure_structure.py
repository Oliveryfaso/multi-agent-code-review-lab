import copy
import json
import unittest

from macr.providers.failure_evidence import (
    make_failure_diagnostic,
    validate_failure_diagnostic,
)


def response_with_call(call):
    return {'choices': [{'message': {'role': 'assistant', 'content': None,
            'tool_calls': [call]}, 'finish_reason': 'tool_calls'}]}


class ProviderFailureStructureTests(unittest.TestCase):
    def test_known_index_and_core_call_fields_report_only_kinds(self):
        secret = 'sk-SYNTHETIC-SECRET-NEVER-PERSIST'
        call = {'id': secret, 'type': secret,
                'function': {'name': secret, 'arguments': secret}, 'index': 67890}
        diagnostic = make_failure_diagnostic('assistant_message',
            data=response_with_call(call))
        self.assertEqual({key: diagnostic.get(key) for key in (
            'first_tool_call_kind', 'first_tool_id_kind', 'first_tool_type_kind',
            'first_tool_function_kind', 'first_tool_name_kind',
            'first_tool_arguments_kind', 'first_tool_index_kind')}, {
            'first_tool_call_kind': 'object', 'first_tool_id_kind': 'string',
            'first_tool_type_kind': 'string', 'first_tool_function_kind': 'object',
            'first_tool_name_kind': 'string', 'first_tool_arguments_kind': 'string',
            'first_tool_index_kind': 'integer'})
        encoded = json.dumps(diagnostic)
        self.assertNotIn(secret, encoded)
        self.assertNotIn('67890', encoded)

    def test_known_index_absence_and_invalid_core_fields_remain_distinct(self):
        cases = [
            ({'id': None, 'type': False, 'function': []},
             ('object', 'null', 'boolean', 'array', 'missing', 'missing')),
            ({'function': {'name': {'private': 'DO-NOT-PERSIST'}}, 'index': []},
             ('object', 'missing', 'missing', 'object', 'object', 'array')),
            ('CALL-PAYLOAD-DO-NOT-PERSIST',
             ('string', 'missing', 'missing', 'missing', 'missing', 'missing')),
        ]
        fields = ('first_tool_call_kind', 'first_tool_id_kind',
                  'first_tool_type_kind', 'first_tool_function_kind',
                  'first_tool_name_kind', 'first_tool_index_kind')
        for call, expected in cases:
            with self.subTest(shape=expected):
                diagnostic = make_failure_diagnostic('assistant_message',
                    data=response_with_call(call))
                self.assertEqual(tuple(diagnostic.get(key) for key in fields), expected)
                self.assertNotIn('DO-NOT-PERSIST', json.dumps(diagnostic))

    def test_unknown_extension_names_and_values_are_never_persisted(self):
        secret_name = 'sk-SYNTHETIC-EXTENSION-NAME'
        secret_value = 'SYNTHETIC-PAYLOAD-NEVER-PERSIST'
        ignored_cycle = []
        ignored_cycle.append(ignored_cycle)
        call = {'id': secret_value, 'type': 'function',
            'function': {'name': secret_value, 'arguments': '{}',
                         secret_name: ignored_cycle},
            secret_name: secret_value, 'index': {secret_name: secret_value}}
        data = response_with_call(call)
        data[secret_name] = ignored_cycle
        message = data['choices'][0]['message']
        message[secret_name] = secret_value
        message['content'] = secret_value
        message['reasoning_content'] = secret_value
        diagnostic = make_failure_diagnostic('assistant_message', data=data)
        self.assertEqual(diagnostic.get('first_tool_index_kind'), 'object')
        encoded = json.dumps(diagnostic)
        self.assertNotIn(secret_name, encoded)
        self.assertNotIn(secret_value, encoded)
        self.assertNotIn('private', encoded)
        self.assertLessEqual(len(encoded.encode('utf-8')), 4096)

    def test_fixed_validation_categories_locate_failed_predicates(self):
        codes = ('message_type', 'required_field', 'role_invalid', 'content_type',
            'reasoning_type', 'tool_calls_type', 'tool_call_count',
            'tool_call_shape', 'tool_call_id', 'tool_call_duplicate_id',
            'tool_call_type', 'tool_function_shape', 'tool_function_name',
            'tool_arguments_type', 'tool_arguments_json', 'duplicate_json_key',
            'response_json_invalid', 'metadata_limit')
        for code in codes:
            with self.subTest(code=code):
                diagnostic = make_failure_diagnostic('assistant_message',
                    validation_error=code)
                self.assertEqual(diagnostic, {'schema': 'macr.provider.failure.v1',
                    'stage': 'assistant_message', 'validation_error': code})
                self.assertEqual(validate_failure_diagnostic(diagnostic), diagnostic)

    def test_unapproved_validation_category_is_rejected_without_echo(self):
        for invalid in ('sk-SYNTHETIC-SECRET', '', True, 17, [], {}):
            with self.subTest(kind=type(invalid).__name__):
                with self.assertRaisesRegex(ValueError,
                        '^provider_failure_diagnostic_invalid$'):
                    make_failure_diagnostic('assistant_message',
                        validation_error=invalid)
                with self.assertRaisesRegex(ValueError,
                        '^provider_failure_diagnostic_invalid$'):
                    validate_failure_diagnostic({'schema': 'macr.provider.failure.v1',
                        'stage': 'assistant_message', 'validation_error': invalid})

    def test_legacy_record_remains_exact_and_independent(self):
        legacy = {'schema': 'macr.provider.failure.v1', 'stage': 'usage',
            'root_kind': 'object', 'choices_kind': 'array', 'choices_count': 1,
            'message_kind': 'object', 'role_kind': 'string', 'content_kind': 'null',
            'reasoning_kind': 'string', 'tool_calls_kind': 'array',
            'tool_calls_count': 1, 'tool_call_extra_fields': 1,
            'first_tool_arguments_kind': 'string', 'usage_kind': 'object',
            'usage_counters': {'prompt_tokens': 10, 'completion_tokens': 4},
            'usage_counter_types': {'prompt_tokens': 'integer',
                                    'completion_tokens': 'integer'}}
        expected = copy.deepcopy(legacy)
        validated = validate_failure_diagnostic(legacy)
        self.assertEqual(validated, expected)
        validated['usage_counters']['prompt_tokens'] = 99
        self.assertEqual(legacy, expected)
        self.assertNotIn('validation_error', make_failure_diagnostic('usage'))

    def test_schema_unknown_keys_oversize_and_arbitrary_shapes_are_rejected(self):
        cases = [
            {'schema': 'macr.provider.failure.v2', 'stage': 'usage'},
            {'schema': 'macr.provider.failure.v1', 'stage': 'usage',
             'sk-SYNTHETIC-SECRET-NAME': 'synthetic-value'},
            {'schema': 'macr.provider.failure.v1', 'stage': 'usage',
             'first_tool_index_kind': 'SYNTHETIC-LARGE-PAYLOAD' * 5000},
            {'schema': 'macr.provider.failure.v1', 'stage': 'usage',
             'first_tool_name_kind': {'name': 'SYNTHETIC-SECRET'}},
        ]
        for invalid in cases:
            with self.subTest(fields=tuple(invalid)):
                with self.assertRaisesRegex(ValueError,
                        '^provider_failure_diagnostic_invalid$'):
                    validate_failure_diagnostic(invalid)


if __name__ == '__main__':
    unittest.main()
