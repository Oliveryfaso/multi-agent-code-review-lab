"""Synthetic structural checks; these tests do not attest native compatibility."""

import copy
import json
import unittest

from macr.evals.diagnosis_constraints import (
    VERDICTS, build_optional_response_format, diagnosis_gbnf, diagnosis_schema,
)


def matches(schema, value):
    """Test only the documented JSON-schema subset used by this candidate.

    This deliberately small oracle is not a production validator or a native
    schema converter. It keeps the module and its tests dependency-free.
    """
    if 'oneOf' in schema:
        return sum(matches(branch, value) for branch in schema['oneOf']) == 1
    if 'const' in schema and value != schema['const']:
        return False
    kind = schema.get('type')
    types = {'object': dict, 'array': list, 'string': str, 'integer': int,
             'null': type(None)}
    if kind == 'integer':
        # JSON Schema treats an integral JSON number as an integer. The host
        # parser additionally requires a parsed Python int, not a float/bool.
        if not (type(value) is int or type(value) is float and value.is_integer()):
            return False
    elif kind is not None and type(value) is not types[kind]:
        return False
    if kind == 'object':
        properties = schema['properties']
        if not set(schema['required']) <= set(value):
            return False
        if schema['additionalProperties'] is False and set(value) - set(properties):
            return False
        return all(matches(properties[key], item) for key, item in value.items())
    if kind == 'array':
        return (schema.get('minItems', 0) <= len(value) <= schema['maxItems']
                and all(matches(schema['items'], item) for item in value))
    if kind == 'string':
        return schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', float('inf'))
    if kind == 'integer':
        return value >= schema['minimum']
    return True


class DiagnosisConstraintTests(unittest.TestCase):
    def answer(self, verdict='supported_defect'):
        answer = {'verdict': verdict, 'cause': 'A synthetic reason.',
                  'when': 'A synthetic condition.',
                  'refs': [{'file': 'logic.py', 'start': 1, 'end': 2}],
                  'unknowns': []}
        if verdict == 'unknown':
            answer.update(cause=None, when=None, refs=[], unknowns=['A supplied dependency is absent.'])
        return answer

    def test_each_verdict_has_a_mutually_exclusive_complete_branch(self):
        schema = diagnosis_schema()
        self.assertEqual(len(schema['oneOf']), 3)
        for verdict in VERDICTS:
            with self.subTest(verdict=verdict):
                answer = self.answer(verdict)
                self.assertTrue(matches(schema, answer))
                self.assertEqual(sum(matches(branch, answer) for branch in schema['oneOf']), 1)
                self.assertEqual(set(answer), set(schema['oneOf'][0]['required']))

    def test_invalid_verdict_types_and_values(self):
        for value in ['defect', '', None, True, 1, [], {}]:
            with self.subTest(value=value):
                answer = self.answer(); answer['verdict'] = value
                self.assertFalse(matches(diagnosis_schema(), answer))

    def test_fields_are_required_and_extra_fields_refused(self):
        for verdict in VERDICTS:
            for field in self.answer(verdict):
                with self.subTest(verdict=verdict, field=field):
                    answer = self.answer(verdict); del answer[field]
                    self.assertFalse(matches(diagnosis_schema(), answer))
            answer = self.answer(verdict); answer['confidence'] = 1
            self.assertFalse(matches(diagnosis_schema(), answer))

    def test_nonunknown_requires_nonempty_string_cause_and_when(self):
        for verdict in VERDICTS[:2]:
            for field, limit in [('cause', 600), ('when', 300)]:
                for value in [None, '', 1, True, [], {}, 'x' * (limit + 1)]:
                    with self.subTest(verdict=verdict, field=field, value=value):
                        answer = self.answer(verdict); answer[field] = value
                        self.assertFalse(matches(diagnosis_schema(), answer))
                answer = self.answer(verdict); answer[field] = 'x' * limit
                self.assertTrue(matches(diagnosis_schema(), answer))

    def test_unknown_requires_null_cause_when_and_nonempty_gaps(self):
        for field in ['cause', 'when']:
            for value in ['', 'A proposed reason.', 1, True, [], {}]:
                answer = self.answer('unknown'); answer[field] = value
                self.assertFalse(matches(diagnosis_schema(), answer))
        answer = self.answer('unknown'); answer['unknowns'] = []
        self.assertFalse(matches(diagnosis_schema(), answer))

    def test_refs_are_zero_to_three_for_unknown_and_one_to_three_otherwise(self):
        for verdict in VERDICTS:
            for count in range(5):
                with self.subTest(verdict=verdict, count=count):
                    answer = self.answer(verdict)
                    answer['refs'] = [{'file': 'logic.py', 'start': n + 1, 'end': n + 1}
                                      for n in range(count)]
                    self.assertEqual(matches(diagnosis_schema(), answer),
                                     (0 if verdict == 'unknown' else 1) <= count <= 3)
            for value in [None, {}, '', 1]:
                answer = self.answer(verdict); answer['refs'] = value
                self.assertFalse(matches(diagnosis_schema(), answer))

    def test_reference_fields_types_and_positive_integer_boundaries(self):
        base = self.answer()['refs'][0]
        bad = [{}, base | {'extra': 1}, {'start': 1, 'end': 2},
               base | {'file': ''}, base | {'file': None}, base | {'file': 7}]
        for field in ['start', 'end']:
            bad.extend(base | {field: value} for value in [0, -1, None, True, 1.5, '1'])
        for ref in bad:
            with self.subTest(ref=ref):
                answer = self.answer(); answer['refs'] = [ref]
                self.assertFalse(matches(diagnosis_schema(), answer))
        answer = self.answer(); answer['refs'] = [base | {'start': 1, 'end': 999999}]
        self.assertTrue(matches(diagnosis_schema(), answer))

    def test_gap_count_type_and_length_boundaries(self):
        for verdict in VERDICTS:
            for count in range(8):
                answer = self.answer(verdict); answer['unknowns'] = ['x'] * count
                self.assertEqual(matches(diagnosis_schema(), answer),
                                 (1 if verdict == 'unknown' else 0) <= count <= 6)
            for value in ['', None, 1, True, [], {}, 'x' * 201]:
                answer = self.answer(verdict); answer['unknowns'] = [value]
                self.assertFalse(matches(diagnosis_schema(), answer))
            answer = self.answer(verdict); answer['unknowns'] = ['x' * 200]
            self.assertTrue(matches(diagnosis_schema(), answer))

    def test_source_and_trim_checks_are_deliberately_left_to_host(self):
        examples = [self.answer() | {'cause': '   '},
                    self.answer() | {'when': '\t'},
                    self.answer('unknown') | {'unknowns': ['\n']},
                    self.answer() | {'refs': [{'file': '../outside.py', 'start': 1, 'end': 2}]},
                    self.answer() | {'refs': [{'file': '/outside.py', 'start': 1, 'end': 2}]},
                    self.answer() | {'refs': [{'file': 'logic.py', 'start': 2, 'end': 1}]},
                    self.answer() | {'refs': [{'file': 'logic.py', 'start': 1.0, 'end': 2}]},
                    self.answer() | {'refs': self.answer()['refs'] * 2},
                    self.answer() | {'refs': [{'file': 'logic.py', 'start': 1, 'end': 999999}]}]
        from macr.evals.diagnostic_compare import _parse
        from macr.providers.base import ProviderError
        source = [{'file': 'logic.py', 'start': 1, 'end': 2, 'body': '1: x = 1\n2: y = x'}]
        for answer in examples:
            with self.subTest(answer=answer):
                self.assertTrue(matches(diagnosis_schema(), answer))
                with self.assertRaises(ProviderError):
                    _parse(json.dumps(answer), source)

    def test_schema_preserves_host_accepted_synthetic_cases(self):
        from macr.evals.diagnostic_compare import _parse
        source = [{'file': 'logic.py', 'start': 1, 'end': 2, 'body': '1: x = 1\n2: y = x'}]
        for verdict in VERDICTS:
            answer = self.answer(verdict)
            self.assertTrue(matches(diagnosis_schema(), answer))
            self.assertEqual(_parse(json.dumps(answer), source), answer)

    def test_optional_wrapper_is_fresh_serializable_and_not_enabled_by_default(self):
        from macr.evals.diagnostic_compare import OUTPUT_SCHEMA
        before = copy.deepcopy(OUTPUT_SCHEMA)
        wrapper = build_optional_response_format()
        self.assertEqual(wrapper, {'type': 'json_schema', 'json_schema': {'schema': diagnosis_schema()}})
        self.assertEqual(json.loads(json.dumps(wrapper)), wrapper)
        wrapper['json_schema']['schema']['oneOf'][0]['properties']['cause']['maxLength'] = 1
        self.assertEqual(diagnosis_schema()['oneOf'][0]['properties']['cause']['maxLength'], 600)
        self.assertEqual(OUTPUT_SCHEMA, before)
        self.assertNotEqual(OUTPUT_SCHEMA, diagnosis_schema())

    def test_gbnf_candidate_retains_unknown_and_bounded_structural_rules(self):
        grammar = diagnosis_gbnf()
        self.assertTrue(grammar.startswith('root ::= ws (supported-defect | no-defect-supported | unknown) ws\n'))
        self.assertIn('unknown ::= ', grammar)
        self.assertIn('optional-refs', grammar)
        self.assertIn('required-gaps', grammar)
        for bound in [600, 300, 200]:
            self.assertIn(f'json-char{{1,{bound}}}', grammar)
        self.assertIn('positive-integer ::= [1-9] [0-9]*', grammar)
        self.assertNotIn('expected_verdict', grammar)
        self.assertEqual(diagnosis_gbnf(), grammar)


if __name__ == '__main__':
    unittest.main()
