import copy
import json
import unittest

from macr.providers.response_normalization import (
    ResponseNormalizationError,
    decode_provider_json,
    normalize_assistant_message,
)


CALL = {'id': 'call_1', 'type': 'function',
        'function': {'name': 'inspect_file', 'arguments': '{ "path": "fixture.py" }'}}


class DeepSeekResponseNormalizationTests(unittest.TestCase):
    def assert_rejected(self, code, operation):
        with self.assertRaises(ResponseNormalizationError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)
        return caught.exception

    def normalize(self, message):
        return normalize_assistant_message(message, tool_names={'inspect_file'})

    def test_preserves_exact_assistant_strings_and_optional_fields(self):
        message = {'role': 'assistant', 'content': '  answer\n',
                   'reasoning_content': ' exact reasoning\n\t',
                   'tool_calls': [copy.deepcopy(CALL)]}
        result = self.normalize(message)
        self.assertEqual(result, {'role': 'assistant', 'content': '  answer\n',
            'reasoning_content': ' exact reasoning\n\t',
            'tool_calls': [{'id': 'call_1', 'type': 'function',
                'function': {'name': 'inspect_file',
                             'arguments': '{ "path": "fixture.py" }'}}]})
        self.assertEqual(self.normalize({'role': 'assistant', 'content': None}),
                         {'role': 'assistant', 'content': None})
        self.assertEqual(self.normalize({'role': 'assistant', 'content': '',
                                        'reasoning_content': None, 'tool_calls': []}),
                         {'role': 'assistant', 'content': '',
                          'reasoning_content': None, 'tool_calls': []})

    def test_discards_arbitrary_metadata_at_every_supported_level(self):
        sentinel = 'SENSITIVE_EXTENSION_SENTINEL'
        call = copy.deepcopy(CALL)
        call.update({'index': 0, 'metadata': {'private': sentinel}, sentinel: [sentinel]})
        call['function'].update({'future_extension': {'value': sentinel}, sentinel: sentinel})
        message = {'role': 'assistant', 'content': None, 'tool_calls': [call],
                   'metadata': {'account': sentinel}, sentinel: {'nested': sentinel}}
        result = self.normalize(message)
        self.assertEqual(result, {'role': 'assistant', 'content': None,
                                 'tool_calls': [CALL]})
        self.assertNotIn(sentinel, json.dumps(result))

    def test_does_not_traverse_or_copy_ignored_metadata(self):
        ignored = {}
        ignored['self'] = ignored
        call = copy.deepcopy(CALL)
        call['ignored'] = ignored
        call['function']['ignored'] = ignored
        result = self.normalize({'role': 'assistant', 'content': 'answer',
                                 'ignored': ignored, 'tool_calls': [call]})
        self.assertEqual(json.loads(json.dumps(result)),
                         {'role': 'assistant', 'content': 'answer', 'tool_calls': [CALL]})

    def test_canonical_tool_call_containers_are_copy_isolated(self):
        message = {'role': 'assistant', 'content': None, 'tool_calls': [copy.deepcopy(CALL)]}
        result = self.normalize(message)
        message['tool_calls'][0]['function']['arguments'] = '{}'
        self.assertEqual(result['tool_calls'][0]['function']['arguments'],
                         '{ "path": "fixture.py" }')
        result['tool_calls'][0]['id'] = 'replacement'
        self.assertEqual(message['tool_calls'][0]['id'], 'call_1')

    def test_rejects_missing_or_malformed_required_message_fields(self):
        cases = [([], 'message_type'), (None, 'message_type'),
                 ({'content': ''}, 'required_field'),
                 ({'role': 'assistant'}, 'required_field'),
                 ({'role': 'user', 'content': ''}, 'role_invalid'),
                 ({'role': ['assistant'], 'content': ''}, 'role_invalid'),
                 ({'role': 'assistant', 'content': 0}, 'content_type'),
                 ({'role': 'assistant', 'content': []}, 'content_type'),
                 ({'role': 'assistant', 'content': '', 'reasoning_content': {}}, 'reasoning_type')]
        for message, code in cases:
            with self.subTest(code=code):
                self.assert_rejected(code, lambda: self.normalize(message))

    def test_tool_calls_must_be_a_list_when_present(self):
        for calls in (None, {}, '', (), False):
            with self.subTest(kind=type(calls).__name__):
                self.assert_rejected('tool_calls_type', lambda: self.normalize(
                    {'role': 'assistant', 'content': None, 'tool_calls': calls}))

    def test_accepts_64_calls_but_rejects_65(self):
        calls = [dict(copy.deepcopy(CALL), id=f'call_{index}') for index in range(65)]
        message = {'role': 'assistant', 'content': None, 'tool_calls': calls[:64]}
        self.assertEqual(len(self.normalize(message)['tool_calls']), 64)
        message['tool_calls'] = calls
        self.assert_rejected('tool_call_count', lambda: self.normalize(message))

    def test_rejects_malformed_action_fields(self):
        cases = [(None, 'tool_call_shape'), ({}, 'required_field')]
        for key in ('id', 'type', 'function'):
            call = copy.deepcopy(CALL)
            del call[key]
            cases.append((call, 'required_field'))
        for identifier in ('', 'x' * 257, 3, None):
            cases.append((dict(copy.deepcopy(CALL), id=identifier), 'tool_call_id'))
        for kind in ('custom', None, ['function']):
            cases.append((dict(copy.deepcopy(CALL), type=kind), 'tool_call_type'))
        for function in (None, [], ''):
            cases.append((dict(copy.deepcopy(CALL), function=function), 'tool_function_shape'))
        for key in ('name', 'arguments'):
            call = copy.deepcopy(CALL)
            del call['function'][key]
            cases.append((call, 'required_field'))
        for call, code in cases:
            with self.subTest(code=code):
                self.assert_rejected(code, lambda: self.normalize(
                    {'role': 'assistant', 'content': None, 'tool_calls': [call]}))

    def test_rejects_duplicate_call_ids(self):
        self.assert_rejected('tool_call_duplicate_id', lambda: self.normalize(
            {'role': 'assistant', 'content': None,
             'tool_calls': [copy.deepcopy(CALL), copy.deepcopy(CALL)]}))

    def test_function_name_must_be_a_bounded_declared_identifier(self):
        for name in ('', 'undeclared', 'bad-name', '1bad', 'é', 'x' * 129, None, ['inspect_file']):
            with self.subTest(kind=type(name).__name__):
                call = copy.deepcopy(CALL)
                call['function']['name'] = name
                self.assert_rejected('tool_function_name', lambda: self.normalize(
                    {'role': 'assistant', 'content': None, 'tool_calls': [call]}))
        call = copy.deepcopy(CALL)
        call['id'] = 'i' * 256
        call['function']['name'] = 'n' * 128
        result = normalize_assistant_message(
            {'role': 'assistant', 'content': None, 'tool_calls': [call]},
            tool_names={'n' * 128})
        self.assertEqual(result['tool_calls'], [call])

    def test_declared_hyphenated_and_leading_digit_function_names_remain_valid(self):
        for name in ('read-file', '9lookup'):
            with self.subTest(name=name):
                call = copy.deepcopy(CALL)
                call['function']['name'] = name
                message = {'role': 'assistant', 'content': None, 'tool_calls': [call]}
                self.assertEqual(normalize_assistant_message(message, tool_names={name}), message)
                self.assert_rejected('tool_function_name', lambda:
                    normalize_assistant_message(message, tool_names={'inspect_file'}))

    def test_declaring_a_name_does_not_allow_unicode_spaces_or_other_punctuation(self):
        for name in ('é', 'read file', 'read.file', 'read/file', 'read:file', 'read\nfile'):
            with self.subTest(name=name):
                call = copy.deepcopy(CALL)
                call['function']['name'] = name
                self.assert_rejected('tool_function_name', lambda:
                    normalize_assistant_message(
                        {'role': 'assistant', 'content': None, 'tool_calls': [call]},
                        tool_names={name}))

    def test_arguments_must_be_an_object_json_string_without_duplicate_keys(self):
        cases = [({}, 'tool_arguments_type'), (None, 'tool_arguments_type'),
                 ('[]', 'tool_arguments_json'), ('null', 'tool_arguments_json'),
                 ('true', 'tool_arguments_json'), ('"text"', 'tool_arguments_json'),
                 ('', 'tool_arguments_json'), ('{"x":NaN}', 'tool_arguments_json'),
                 ('{"x":Infinity}', 'tool_arguments_json'),
                 ('{"x":1e999}', 'tool_arguments_json'),
                 ('{"private":1,"private":2}', 'duplicate_json_key'),
                 ('{"outer":{"private":1,"private":2}}', 'duplicate_json_key')]
        for arguments, code in cases:
            with self.subTest(code=code):
                call = copy.deepcopy(CALL)
                call['function']['arguments'] = arguments
                self.assert_rejected(code, lambda: self.normalize(
                    {'role': 'assistant', 'content': None, 'tool_calls': [call]}))

    def test_argument_limit_counts_utf8_bytes_and_preserves_boundary_string(self):
        arguments = '{"x":"' + 'é' * 32764 + '"}'
        self.assertEqual(len(arguments.encode('utf-8')), 65536)
        call = copy.deepcopy(CALL)
        call['function']['arguments'] = arguments
        result = self.normalize({'role': 'assistant', 'content': None, 'tool_calls': [call]})
        self.assertEqual(result['tool_calls'][0]['function']['arguments'], arguments)
        call['function']['arguments'] = arguments + ' '
        self.assert_rejected('tool_arguments_type', lambda: self.normalize(
            {'role': 'assistant', 'content': None, 'tool_calls': [call]}))

    def test_decoder_accepts_utf8_json_object_from_text_or_bytes(self):
        for raw in ('{"answer":"é","nested":{"x":[1,null,true]}}',
                    b'{"answer":"ok"}'):
            with self.subTest(kind=type(raw).__name__):
                result = decode_provider_json(raw)
                self.assertIs(type(result), dict)
                self.assertIn('answer', result)

    def test_decoder_rejects_invalid_json_nonfinite_numbers_and_input_shapes(self):
        for raw in ('[]', 'null', 'true', '1', '"text"', '{',
                    '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}',
                    '{"x":1e999}', '{"x":-1e999}', b'{"x":"\xff"}',
                    None, {}, 1, bytearray(b'{}')):
            with self.subTest(kind=type(raw).__name__):
                self.assert_rejected('response_json_invalid', lambda: decode_provider_json(raw))

    def test_decoder_rejects_duplicate_keys_even_inside_unknown_metadata(self):
        for raw in ('{"role":"assistant","role":"tool"}',
                    '{"choices":[{"message":{"content":"a","content":"b"}}]}',
                    '{"metadata":{"private-key-sentinel":1,"private-key-sentinel":2}}'):
            with self.subTest():
                error = self.assert_rejected('duplicate_json_key', lambda: decode_provider_json(raw))
                self.assertNotIn('private-key-sentinel', repr(error))

    def test_errors_never_echo_provider_field_names_or_values(self):
        private = 'PRIVATE_KEY_OR_VALUE_SENTINEL'
        raw = json.dumps({'metadata': {private: 1}})[:-2] + ',"' + private + '":2}}'
        error = self.assert_rejected('duplicate_json_key', lambda: decode_provider_json(raw))
        self.assertNotIn(private, repr(error))
        with self.assertRaises(ValueError) as caught:
            ResponseNormalizationError(private)
        self.assertNotIn(private, str(caught.exception))


if __name__ == '__main__':
    unittest.main()
