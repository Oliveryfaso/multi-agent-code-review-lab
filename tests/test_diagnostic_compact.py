"""Inert boundary tests; neither compact acceptance nor fixtures prove accuracy."""
import copy
from dataclasses import FrozenInstanceError
import json
import unittest

from macr.evals.diagnostic_compact import (
    CHECK_STATES, CompactScope, build_candidate_request, build_verification_request,
    parse_candidate, prepare_scope, protocol_schema, restore_scope, validate_verification,
)
from macr.providers.base import ProviderError
from macr.schemas import Evidence


class DiagnosticCompactTests(unittest.TestCase):
    def setUp(self):
        self.evidence = [Evidence('logic.py', 10, 11, 'Located implementation.',
            'def demo():\n    return 2\n', None, 'parse_ast')]
        self.scope = prepare_scope(self.evidence, case_id='case-a', attempt_id='attempt-a')
        self.request = build_candidate_request(self.scope, question='Does demo satisfy the requirement?',
            scenario='Every call returns 2.', assumptions=['Ordinary Python integers.'])
        self.wire = {'verdict': 'no_defect_supported', 'cause': 'The literal returned is 2.',
            'when': 'demo is called.', 'refs': ['e1'], 'unknowns': [], 'scope': self.scope.binding}
        self.checked = {'scope': self.scope.binding, 'checks': {'requirement': 'established',
            'trigger': 'reachable', 'behavior': 'satisfies', 'protection': 'absent',
            'counterevidence': 'refutes'}, 'refs': {'behavior': ['e1'], 'counterevidence': ['e1']},
            'reason': 'The literal return meets the complete stated domain.', 'unknowns': []}

    def candidate(self, **changes):
        return parse_candidate(json.dumps(self.wire | changes), self.scope)

    def checked_result(self, candidate=None, checked=None):
        return validate_verification(json.dumps(self.checked if checked is None else checked),
            self.candidate() if candidate is None else candidate, self.scope)

    def test_single_visible_position_can_produce_accepted_host_final(self):
        candidate = self.candidate()
        self.assertEqual(candidate['refs'], [self.scope.source[0].citation()])
        result = self.checked_result(candidate)
        self.assertTrue(result['accepted'])
        self.assertEqual(result['final'], candidate)
        self.assertEqual(result['assessment'], 'no_defect_supported')
        self.assertFalse(result['semantic_proof'])
        self.assertEqual(len(result['final']['refs']), 1)

    def test_exact_scope_binds_old_case_attempt_and_changed_body_including_empty_refs(self):
        unknown = self.wire | {'verdict': 'unknown', 'cause': None, 'when': None, 'refs': [],
                              'unknowns': ['The required callback implementation is absent.']}
        changed = copy.deepcopy(self.evidence)
        changed[0].snippet = 'def demo():\n    return 3\n'
        scopes = [prepare_scope(self.evidence, case_id='case-b', attempt_id='attempt-a'),
                  prepare_scope(self.evidence, case_id='case-a', attempt_id='attempt-b'),
                  prepare_scope(changed, case_id='case-a', attempt_id='attempt-a')]
        for scope in scopes:
            for answer in (self.wire, unknown):
                with self.subTest(binding=scope.binding, verdict=answer['verdict']):
                    with self.assertRaisesRegex(ProviderError, 'compact_scope_mismatch'):
                        parse_candidate(json.dumps(answer), scope)
        self.assertIsInstance(self.scope, CompactScope)
        with self.assertRaises(FrozenInstanceError):
            self.scope.source[0].body = 'Changed after dispatch.'

    def test_visible_table_is_complete_lines_with_recomputed_end(self):
        evidence = Evidence('hidden.py', 10, 12, 'A source prefix.',
            'first = 1\nsecond = 2\nreturn False\n', '', 'search_text')
        scope = prepare_scope([evidence], case_id='crop', attempt_id='one', max_body_chars=11)
        row = scope.source[0]
        self.assertEqual((row.line_start, row.line_end, row.body), (10, 10, 'first = 1\n'))
        self.assertTrue(row.citable)
        self.assertFalse(row.complete)
        wire = self.wire | {'scope': scope.binding}
        candidate = parse_candidate(json.dumps(wire), scope)
        self.assertEqual(candidate['refs'][0]['line_end'], 10)
        request = build_candidate_request(scope, question='What is supported?', scenario='Inspect visible lines.', assumptions=[])
        payload = json.loads(request.messages[-1]['content'])
        self.assertNotIn('return False', json.dumps(payload))
        candidate['refs'][0]['line_end'] = 12
        with self.assertRaisesRegex(ProviderError, 'compact_citation_invalid'):
            build_verification_request(request, scope, candidate)
        self.assertEqual(evidence.line_end, 12)

    def test_long_first_line_empty_and_mismatched_snippets_are_only_locators(self):
        examples = [Evidence('long.py', 1, 1, 'Long guard suffix.', 'x' * 100 + ' and guard()', None, 'search_text'),
                    Evidence('empty.py', 4, 8, 'AST location.', '', None, 'parse_ast'),
                    Evidence('partial.py', 1, 2, 'Incomplete original body.', 'one line only', None, 'search_text')]
        for example in examples:
            with self.subTest(file=example.file):
                scope = prepare_scope([example], case_id='locator', attempt_id='one', max_body_chars=40)
                self.assertFalse(scope.source[0].citable)
                self.assertEqual(scope.source[0].body, '')
                self.assertFalse(scope.source[0].complete)
                with self.assertRaises(ProviderError):
                    parse_candidate(json.dumps(self.wire | {'scope': scope.binding}), scope)
                unknown = self.wire | {'verdict': 'unknown', 'cause': None, 'when': None, 'refs': [],
                    'unknowns': ['The necessary source body is unavailable.'], 'scope': scope.binding}
                self.assertEqual(parse_candidate(json.dumps(unknown), scope)['refs'], [])

    def test_null_empty_symbol_and_different_tool_origins_are_not_merged(self):
        rows = [self.evidence[0], copy.deepcopy(self.evidence[0]), copy.deepcopy(self.evidence[0])]
        rows[1].symbol = ''
        rows[2].source_tool = 'search_text'
        scope = prepare_scope(rows + [copy.deepcopy(rows[0])], case_id='origins', attempt_id='one')
        self.assertEqual(len(scope.source), 3)
        candidate = parse_candidate(json.dumps(self.wire | {'scope': scope.binding, 'refs': ['e1', 'e2']}), scope)
        self.assertIsNone(candidate['refs'][0]['symbol'])
        self.assertEqual(candidate['refs'][1]['symbol'], '')
        self.assertNotEqual(scope.source[0].citation(), scope.source[2].citation())
        self.assertEqual(json.loads(json.dumps(scope.to_dict()))['source'][1]['symbol'], '')

    def test_duplicates_do_not_increase_refs_and_all_supplied_ids_are_checked(self):
        self.assertEqual(len(self.candidate(refs=['e1', 'e1'])['refs']), 1)
        for refs in (['e1', 'e99'], ['e1', 'e1', 'e1'], ['logic.py:10-11'], [{'file': 'logic.py'}]):
            with self.subTest(refs=refs), self.assertRaises(ProviderError):
                self.candidate(refs=refs)
        checked = copy.deepcopy(self.checked)
        checked['refs']['requirement'] = ['e99']
        with self.assertRaises(ProviderError):
            self.checked_result(checked=checked)

    def test_compact_null_and_text_limits_are_strict_without_silent_repairs(self):
        for changes in ({'cause': 'x' * 161}, {'when': 'x' * 81}, {'cause': ' '},
                        {'cause': None}, {'unknowns': ['Missing essential domain.']}):
            with self.subTest(changes=changes), self.assertRaises(ProviderError):
                self.candidate(**changes)
        self.assertEqual(len(self.candidate(cause='x' * 160, when='x' * 80)['cause']), 160)
        for gaps in ([], ['x' * 81], ['one', 'two', 'three'], [' ']):
            with self.subTest(gaps=gaps), self.assertRaises(ProviderError):
                self.candidate(verdict='unknown', cause=None, when=None, refs=[], unknowns=gaps)
        with self.assertRaises(ProviderError):
            self.candidate(verdict='unknown', unknowns=['Required dependency absent.'])

    def test_requests_expose_the_exact_schema_and_example_enums_without_hidden_authority(self):
        candidate = self.candidate()
        verification = build_verification_request(self.request, self.scope, candidate)
        for request, verifier in ((self.request, False), (verification, True)):
            payload = json.loads(request.messages[-1]['content'])
            self.assertEqual(request.response_schema, protocol_schema(self.scope, verifier=verifier))
            self.assertEqual(payload['contract']['keys'], request.response_schema['required'])
            self.assertEqual(payload['source'], [{'id': 'e1', 'file': 'logic.py', 'start': 10, 'end': 11,
                'body': 'def demo():\n    return 2\n', 'complete': True, 'citable': True}])
            self.assertNotIn('symbol', json.dumps(payload['source']))
            self.assertNotIn('source_tool', json.dumps(payload['source']))
            self.assertEqual((request.max_input_tokens, request.max_output_tokens, request.timeout_seconds), (1536, 512, 300))
            self.assertEqual(request.tools, [])
            self.assertNotIn('grammar', request.metadata)
            self.assertNotIn('expected_verdict', json.dumps(request.messages))
        payload = json.loads(verification.messages[-1]['content'])
        for name, states in CHECK_STATES.items():
            self.assertEqual(payload['contract']['checks'][name], list(states))
        self.assertEqual(set(payload['shape_example']['checks']), set(CHECK_STATES))
        self.assertNotIn('assessment', payload['shape_example'])
        self.assertNotIn('basis', json.dumps(payload['shape_example']))
        self.assertEqual(payload['candidate']['refs'], ['e1'])
        candidate_payload = json.loads(self.request.messages[-1]['content'])
        self.assertEqual([example['verdict'] for example in candidate_payload['shape_examples']],
                         ['supported_defect', 'no_defect_supported', 'unknown'])
        self.assertEqual(candidate_payload['contract']['cause']['max_chars'], 160)
        self.assertEqual(payload['contract']['reason_max_chars'], 96)
        self.assertEqual(payload['contract']['refs']['required_when']['behavior'], ['violates', 'satisfies'])

    def test_request_builder_refuses_swapped_scope_table(self):
        request = copy.deepcopy(self.request)
        payload = json.loads(request.messages[-1]['content'])
        payload['source'][0]['body'] = 'Hidden replacement.'
        request.messages[-1]['content'] = json.dumps(payload)
        with self.assertRaisesRegex(ProviderError, 'compact_scope_mismatch'):
            build_verification_request(request, self.scope, self.candidate())
        request = copy.deepcopy(self.request)
        payload = json.loads(request.messages[-1]['content'])
        payload['expected_verdict'] = 'no_defect_supported'
        request.messages[-1]['content'] = json.dumps(payload)
        with self.assertRaisesRegex(ProviderError, 'compact_input_invalid'):
            build_verification_request(request, self.scope, self.candidate())
        candidate = self.candidate()
        candidate['refs'][0]['line_start'] = True
        with self.assertRaisesRegex(ProviderError, 'compact_citation_invalid'):
            build_verification_request(self.request, self.scope, candidate)

    def test_persisted_scope_restores_only_with_complete_binding_and_strict_fields(self):
        payload = json.loads(json.dumps(self.scope.to_dict()))
        self.assertEqual(restore_scope(payload), self.scope)
        for field, value in [('case_id', 'case-b'), ('attempt_id', 'attempt-b')]:
            changed = copy.deepcopy(payload)
            changed[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ProviderError, 'compact_scope_mismatch'):
                restore_scope(changed)
        for field, value in [('body', 'def demo():\n    return 3\n'), ('symbol', ''), ('source_tool', 'search_text')]:
            changed = copy.deepcopy(payload)
            changed['source'][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ProviderError, 'compact_scope_mismatch'):
                restore_scope(changed)
        for change in ({'id': 'e2'}, {'line_end': 99}, {'citable': False}, {'extra': 'unseen'}):
            changed = copy.deepcopy(payload)
            changed['source'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ProviderError):
                restore_scope(changed)

    def test_defect_and_essential_unknown_are_both_accepted(self):
        defect = self.candidate(verdict='supported_defect')
        checked = copy.deepcopy(self.checked)
        checked['checks'].update(behavior='violates', protection='ineffective', counterevidence='does_not_refute')
        checked['refs']['protection'] = ['e1']
        self.assertTrue(self.checked_result(defect, checked)['accepted'])
        unknown = self.candidate(verdict='unknown', cause=None, when=None, refs=[],
                                 unknowns=['The callback behavior needed by demo is missing.'])
        checked['checks'].update(requirement='missing', behavior='unknown', protection='unknown', counterevidence='unknown')
        checked['refs'] = {}
        checked['unknowns'] = unknown['unknowns']
        self.assertEqual(self.checked_result(unknown, checked)['final']['verdict'], 'unknown')

    def test_refuting_a_defect_never_flips_it_to_healthy(self):
        candidate = self.candidate(verdict='supported_defect')
        original = copy.deepcopy(candidate)
        result = self.checked_result(candidate)
        self.assertEqual(result['status'], 'rejected')
        self.assertFalse(result['accepted'])
        self.assertIsNone(result['assessment'])
        self.assertIsNone(result['final'])
        self.assertEqual(candidate, original)

    def test_explicit_conflicting_states_reject_final_instead_of_becoming_unknown(self):
        for name, state in [('behavior', 'violates'), ('requirement', 'missing'), ('trigger', 'unknown')]:
            checked = copy.deepcopy(self.checked)
            checked['checks'][name] = state
            with self.subTest(name=name):
                result = self.checked_result(checked=checked)
                self.assertEqual(result['status'], 'rejected')
                self.assertIsNone(result['final'])
        unknown = self.candidate(verdict='unknown', cause=None, when=None, refs=[],
                                 unknowns=['An unrelated optional guard is unavailable.'])
        checked = copy.deepcopy(self.checked)
        checked['checks']['protection'] = 'unknown'
        checked['unknowns'] = unknown['unknowns']
        result = self.checked_result(unknown, checked)
        self.assertEqual(result['error_code'], 'verification_essential_gap_missing')
        self.assertIsNone(result['final'])

    def test_required_check_citations_and_keyed_containers_are_not_coerced(self):
        for name in ('behavior', 'counterevidence'):
            checked = copy.deepcopy(self.checked)
            checked['refs'].pop(name)
            with self.subTest(name=name), self.assertRaisesRegex(ProviderError, 'verification_citation_missing'):
                self.checked_result(checked=checked)
        malformed = [self.checked | {'checks': list(self.checked['checks'].values())},
                     self.checked | {'refs': ['e1']}, self.checked | {'reason': 'x' * 97},
                     self.checked | {'assessment': 'no_defect_supported'}]
        for checked in malformed:
            with self.subTest(checked=checked), self.assertRaises(ProviderError):
                self.checked_result(checked=checked)
        for content in ('{"scope":', '{"scope":"x","scope":"y"}', '{"scope":NaN}'):
            with self.subTest(content=content), self.assertRaises(ProviderError):
                parse_candidate(content, self.scope)
        with self.assertRaises(ProviderError):
            self.candidate(cause='\ud800')


if __name__ == '__main__':
    unittest.main()
