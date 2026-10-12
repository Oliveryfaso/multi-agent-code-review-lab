"""Inert boundary tests; neither compact acceptance nor fixtures prove accuracy."""
import copy
from dataclasses import FrozenInstanceError, asdict
import hashlib
import json
import unittest
from functools import partial

from macr.evals import diagnostic_compact as compact_v3

from macr.evals.diagnostic_compact import (
    CHECK_STATES, LEGACY_VERSION as VERSION, REQUIREMENT_PRESERVING_PROMPT, CompactScope,
    build_candidate_request, build_verification_request,
    candidate_quality_warnings,
    parse_candidate, prepare_scope, protocol_schema, restore_scope, validate_verification,
)
from macr.providers.base import ProviderError
from macr.schemas import Evidence

# Existing retained/development witnesses exercise the explicit historical v2 protocol.
protocol_schema = partial(protocol_schema, version=VERSION)
parse_candidate = partial(parse_candidate, version=VERSION)
validate_verification = partial(validate_verification, version=VERSION)
build_candidate_request = partial(build_candidate_request, version=VERSION)


class DiagnosticCompactTests(unittest.TestCase):
    def setUp(self):
        self.evidence = [Evidence('logic.py', 10, 11, 'Located implementation.',
            'def demo():\n    return 2\n', None, 'parse_ast')]
        self.scope = prepare_scope(self.evidence, case_id='case-a', attempt_id='attempt-a')
        self.request = build_candidate_request(self.scope, question='Does demo satisfy the requirement?',
            scenario='Every call returns 2.', assumptions=['Ordinary Python integers.'])
        self.wire = {'verdict': 'no_defect_supported', 'cause': 'The literal returned is 2.',
            'when': 'demo is called.', 'refs': ['e1'], 'unknowns': [], 'scope': self.scope.binding}
        self.checked = {'scope': self.scope.binding, 'coverage': 'complete',
            'checks': {'requirement': 'established',
            'trigger': 'reachable', 'behavior': 'satisfies', 'protection': 'absent',
            'counterevidence': 'refutes'}, 'refs': {'behavior': ['e1'], 'counterevidence': ['e1']},
            'reason': 'The literal return meets the complete stated domain.', 'unknowns': []}

    def candidate(self, **changes):
        return parse_candidate(json.dumps(self.wire | changes), self.scope)

    def checked_result(self, candidate=None, checked=None):
        return validate_verification(json.dumps(self.checked if checked is None else checked),
            self.candidate() if candidate is None else candidate, self.scope)

    def test_candidate_prompt_variant_appends_only_and_preserves_baseline_and_verifier(self):
        task = dict(question='Does demo satisfy the requirement?', scenario='Every call returns 2.',
                    assumptions=['Ordinary Python integers.'])
        baseline = build_candidate_request(self.scope, **task, prompt_variant='baseline')
        self.assertEqual(baseline, self.request)
        self.assertNotIn('diagnostic_prompt_variant', baseline.metadata)
        variant = build_candidate_request(self.scope, **task, prompt_variant='requirement_preserving')
        self.assertEqual(variant.messages[0]['content'],
                         baseline.messages[0]['content'] + '\n\n' + REQUIREMENT_PRESERVING_PROMPT)
        comparison = asdict(variant)
        comparison['messages'][0]['content'] = baseline.messages[0]['content']
        self.assertEqual(comparison['metadata'].pop('diagnostic_prompt_variant'), 'requirement_preserving')
        self.assertEqual(comparison, asdict(baseline))
        checks = [build_verification_request(request, self.scope, self.candidate())
                  for request in (baseline, variant)]
        self.assertEqual(checks[0].messages, checks[1].messages)
        self.assertEqual(checks[0].response_schema, checks[1].response_schema)

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
        self.assertEqual(len(self.candidate(refs=['e1', 'e1', 'e1'])['refs']), 1)
        for refs in (['e1', 'e99'], ['e1'] * 4, ['logic.py:10-11'], [{'file': 'logic.py'}]):
            with self.subTest(refs=refs), self.assertRaises(ProviderError):
                self.candidate(refs=refs)
        checked = copy.deepcopy(self.checked)
        checked['refs']['requirement'] = ['e99']
        with self.assertRaises(ProviderError):
            self.checked_result(checked=checked)

    def test_compact_null_and_text_limits_are_strict_without_silent_repairs(self):
        for changes in ({'cause': 'x' * 601}, {'when': 'x' * 301}, {'cause': ' '},
                        {'cause': None}, {'unknowns': ['Missing essential domain.']}):
            with self.subTest(changes=changes), self.assertRaises(ProviderError):
                self.candidate(**changes)
        candidate = self.candidate(cause='x' * 600, when='x' * 300)
        self.assertEqual((len(candidate['cause']), len(candidate['when'])), (600, 300))
        verification = build_verification_request(self.request, self.scope, candidate)
        self.assertEqual(json.loads(verification.messages[-1]['content'])['candidate']['cause'], 'x' * 600)
        self.assertEqual(self.checked_result(candidate)['final'], candidate)
        for gaps in ([], ['x' * 201], ['one', 'two', 'three'], [' ']):
            with self.subTest(gaps=gaps), self.assertRaises(ProviderError):
                self.candidate(verdict='unknown', cause=None, when=None, refs=[], unknowns=gaps)
        with self.assertRaises(ProviderError):
            self.candidate(verdict='unknown', unknowns=['Required dependency absent.'])

    def test_preferred_lengths_report_quality_separately_without_changing_candidate(self):
        self.assertEqual(candidate_quality_warnings(self.candidate(cause='x' * 160, when='x' * 80)), [])
        candidate = self.candidate(cause='x' * 161, when='x' * 81)
        original = copy.deepcopy(candidate)
        self.assertEqual(candidate_quality_warnings(candidate), [
            {'field': 'cause', 'actual_chars': 161, 'preferred_chars': 160},
            {'field': 'when', 'actual_chars': 81, 'preferred_chars': 80}])
        self.assertEqual(candidate, original)
        unknown = self.candidate(verdict='unknown', cause=None, when=None, refs=[], unknowns=['x' * 200])
        self.assertEqual(candidate_quality_warnings(unknown), [
            {'field': 'unknowns[0]', 'actual_chars': 200, 'preferred_chars': 80}])
        checked = self.checked | {'checks': {name: 'unknown' if 'unknown' in states else 'missing'
            for name, states in CHECK_STATES.items()}, 'refs': {}, 'unknowns': ['x' * 200]}
        verification = build_verification_request(self.request, self.scope, unknown)
        self.assertEqual(json.loads(verification.messages[-1]['content'])['candidate']['unknowns'], ['x' * 200])
        self.assertEqual(self.checked_result(unknown, checked)['final'], unknown)

    def test_candidate_three_refs_do_not_expand_per_check_refs_or_gap_limit(self):
        rows = [Evidence(f'part{index}.py', 1, 1, 'Separate source.', f'v{index} = 2\n', None, 'parse_ast')
                for index in range(3)]
        scope = prepare_scope(rows, case_id='three', attempt_id='one')
        candidate = parse_candidate(json.dumps(self.wire | {'refs': ['e1', 'e2', 'e3'], 'scope': scope.binding}), scope)
        self.assertEqual(len(candidate['refs']), 3)
        request = build_candidate_request(scope, question='What values are assigned?',
            scenario='Each visible assignment uses 2.', assumptions=[])
        verification = build_verification_request(request, scope, candidate)
        payload = json.loads(verification.messages[-1]['content'])
        self.assertEqual(payload['candidate']['refs'], ['e1', 'e2', 'e3'])
        self.assertEqual(payload['contract']['refs']['max_per_array'], 2)
        self.assertEqual(payload['contract']['unknowns']['max_items'], 2)
        checked = self.checked | {'scope': scope.binding, 'refs': {'behavior': ['e1', 'e2'], 'counterevidence': ['e3']}}
        self.assertEqual(validate_verification(json.dumps(checked), candidate, scope)['final'], candidate)
        checked['refs']['behavior'].append('e3')
        with self.assertRaisesRegex(ProviderError, 'compact_contract_invalid'):
            validate_verification(json.dumps(checked), candidate, scope)

    def test_coverage_is_required_and_incomplete_rejects_without_repairing_candidate(self):
        missing = copy.deepcopy(self.checked)
        missing.pop('coverage')
        for checked in (missing, self.checked | {'coverage': 'unknown'}, self.checked | {'coverage': True}):
            with self.subTest(checked=checked), self.assertRaisesRegex(ProviderError, 'compact_contract_invalid'):
                self.checked_result(checked=checked)
        candidate = self.candidate()
        original = copy.deepcopy(candidate)
        result = self.checked_result(candidate, self.checked | {'coverage': 'incomplete',
            'reason': 'The requested caller path is omitted.'})
        self.assertEqual((result['status'], result['error_code']), ('rejected', 'verification_task_incomplete'))
        self.assertFalse(result['accepted'])
        self.assertIsNone(result['final'])
        self.assertIsNone(result['assessment'])
        self.assertEqual(candidate, original)
        # Coverage does not relax the source or keyed-state contracts.
        with self.assertRaises(ProviderError):
            self.checked_result(candidate, self.checked | {'coverage': 'incomplete',
                'refs': {'behavior': ['e99'], 'counterevidence': ['e1']}})

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
        self.assertEqual(candidate_payload['contract']['cause']['max_chars'], 600)
        self.assertEqual(candidate_payload['contract']['cause']['preferred_chars'], 160)
        self.assertEqual(candidate_payload['contract']['when']['max_chars'], 300)
        self.assertEqual(candidate_payload['contract']['when']['preferred_chars'], 80)
        self.assertEqual(candidate_payload['contract']['unknowns'],
                         {'max_items': 2, 'max_chars_each': 200, 'preferred_chars_each': 80})
        self.assertEqual(payload['contract']['coverage'], ['complete', 'incomplete'])
        self.assertEqual(payload['shape_example']['coverage'], 'complete')
        self.assertIn('coverage', verification.response_schema['required'])
        self.assertEqual(self.request.metadata['diagnostic_protocol'], VERSION)
        self.assertEqual(payload['contract']['reason_max_chars'], 96)
        self.assertEqual(payload['contract']['refs']['required_when']['behavior'], ['violates', 'satisfies'])
        for phrase in ('caller path', 'parameters', 'actual result and expected result',
                       'known path and necessary missing guarantee', 'Preferred lengths'):
            self.assertIn(phrase, self.request.messages[0]['content'])
        for phrase in ('question/scenario', 'Set incomplete', 'not proof', 'do not repair'):
            self.assertIn(phrase, verification.messages[0]['content'])

    def test_verifier_explains_conditional_protection_refs_without_task_specific_answers(self):
        paragraph = (
            'When checks.protection is effective or ineffective, include refs.protection with 1–2 allowed IDs '
            'identifying the visible protective mechanism. Even if an ID already appears in refs.behavior, '
            'repeat it under refs.protection when it supports both checks. Do not invent a guard or an ID. '
            'Keep absent/unknown when the evidence supports those states; this instruction does not require '
            'a protection reference for them.')
        before = copy.deepcopy(self.request)
        candidate = self.candidate()
        original = copy.deepcopy(candidate)
        verification = build_verification_request(self.request, self.scope, candidate)
        self.assertTrue(verification.messages[0]['content'].endswith('\n\n' + paragraph))
        self.assertEqual(verification.messages[0]['content'].count(paragraph), 1)
        self.assertNotIn(paragraph, self.request.messages[0]['content'])
        for answer in ('e1', ' OR ', 'supported_defect', 'no_defect_supported', 'demo'):
            self.assertNotIn(answer, paragraph)
        payload = json.loads(verification.messages[-1]['content'])
        self.assertEqual(payload['contract']['refs']['required_when']['protection'], ['effective', 'ineffective'])
        self.assertEqual(verification.response_schema['properties']['refs']['required'], [])
        self.assertEqual(verification.response_schema, protocol_schema(self.scope, verifier=True))
        self.assertEqual(self.request, before)
        self.assertEqual(candidate, original)

    def test_request_builder_refuses_swapped_scope_table(self):
        old_request = copy.deepcopy(self.request)
        old_request.metadata['diagnostic_protocol'] = 'diagnostic-compact-v1'
        with self.assertRaisesRegex(ProviderError, 'compact_protocol_mismatch'):
            build_verification_request(old_request, self.scope, self.candidate())
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


class DevelopmentReplayTests(unittest.TestCase):
    """Known dev01 content only; scripted checks do not measure model accuracy."""

    RAW = '''{
  "verdict": "supported_defect",
  "cause": "The function `allowed` incorrectly returns `staff or paid` instead of requiring both `staff` and `paid` to be `True`, violating the stated domain that access requires both staff and paid to be True.",
  "when": "The input (True, False) leads to access being granted, which contradicts the requirement that both staff and paid must be True.",
  "refs": ["e1"],
  "unknowns": [],
  "scope": "93df631e2006a56c"
}'''

    def setUp(self):
        evidence = [
            Evidence('access.py', 1, 3, 'Known development source.',
                'def allowed(staff, paid):\n'
                '    """Permit access only when staff and paid are both true."""\n'
                '    return staff or paid\n', 'allowed', 'parse_ast'),
            Evidence('access.py', 5, 6, 'Known development caller.',
                'def access_status(staff, paid):\n'
                '    return "granted" if allowed(staff, paid) else "denied"\n', 'access_status', 'parse_ast'),
            Evidence('access.py', 8, 9, 'Known development entry.',
                'def mixed_access():\n    return access_status(True, False)\n', 'mixed_access', 'parse_ast')]
        self.scope = prepare_scope(evidence, case_id='438d09d6-8af2-4ae1-b875-9b757f831aa5',
                                   attempt_id='41814965-0eb5-435f-a872-2182bb85341f')
        self.request = build_candidate_request(self.scope,
            question='allowed staff paid access_status: investigate possible access bug for boolean inputs; '
                'access requires both staff and paid True. Explain the supplied caller path and cite only retrieved evidence.',
            scenario='Assess the supplied functions, their caller path, and the input domain stated in the question.',
            assumptions=[])
        self.checked = {'scope': self.scope.binding, 'coverage': 'incomplete',
            'checks': {'requirement': 'established', 'trigger': 'reachable', 'behavior': 'violates',
                       'protection': 'absent', 'counterevidence': 'not_found'},
            'refs': {'behavior': ['e1']}, 'reason': 'The required mixed_access and access_status caller path is omitted.',
            'unknowns': []}

    def test_retained_exact_raw_enters_verifier_with_warnings_but_incomplete_has_no_final(self):
        self.assertEqual(len(self.RAW.encode()), 456)
        self.assertEqual(hashlib.sha256(self.RAW.encode()).hexdigest(),
                         'bbc7b555f32674a5ae288d585a30db5bc204696020900069bf9aab72da5a980c')
        self.assertEqual(self.scope.scope_sha256,
                         '93df631e2006a56c1006abc916c4c446f5fafadf6e931bbe7703527d4f01301b')
        raw = json.loads(self.RAW)
        candidate = parse_candidate(self.RAW, self.scope)
        original = copy.deepcopy(candidate)
        self.assertEqual(candidate_quality_warnings(candidate), [
            {'field': 'cause', 'actual_chars': 198, 'preferred_chars': 160},
            {'field': 'when', 'actual_chars': 127, 'preferred_chars': 80}])
        verification = build_verification_request(self.request, self.scope, candidate)
        supplied = json.loads(verification.messages[-1]['content'])['candidate']
        self.assertEqual(supplied, raw)
        result = validate_verification(json.dumps(self.checked), candidate, self.scope)
        self.assertEqual((result['status'], result['error_code']), ('rejected', 'verification_task_incomplete'))
        self.assertIsNone(result['final'])
        self.assertEqual(candidate, original)
        self.assertEqual(json.loads(self.RAW), raw)

    def test_complete_caller_candidate_preserves_parameters_and_actual_expected_with_three_refs(self):
        wire = json.loads(self.RAW) | {
            'cause': 'allowed uses OR while the policy requires both staff and paid.',
            'when': 'mixed_access calls access_status(True, False), which calls allowed(True, False)=True; '
                    'access_status returns granted, but the policy expects denied.',
            'refs': ['e1', 'e2', 'e3']}
        candidate = parse_candidate(json.dumps(wire), self.scope)
        verification = build_verification_request(self.request, self.scope, candidate)
        self.assertEqual(json.loads(verification.messages[-1]['content'])['candidate'], wire)
        self.assertEqual(candidate_quality_warnings(candidate), [
            {'field': 'when', 'actual_chars': len(wire['when']), 'preferred_chars': 80}])
        checked = self.checked | {'coverage': 'complete',
            'reason': 'OR grants the mixed pair; the candidate explains every caller and the denied requirement.'}
        result = validate_verification(json.dumps(checked), candidate, self.scope)
        self.assertEqual(result['final'], candidate)
        self.assertEqual(len(result['final']['refs']), 3)
        self.assertFalse(result['semantic_proof'])

    def test_synthetic_healthy_complete_domain_and_unknown_needed_dependency_fit_protocol(self):
        examples = [
            (Evidence('progress.py', 1, 2, 'Synthetic source.',
                'def finished(done, total):\n    return done >= total\n', 'finished', 'parse_ast'),
             'For nonnegative integers, is finished correct when done equals or exceeds total?',
             'The completion requirement includes done > total.',
             {'verdict': 'no_defect_supported',
              'cause': 'The >= comparison implements the completion requirement for nonnegative integers.',
              'when': 'finished(4, 3) returns True as expected; equality also returns True, and done < total is False.',
              'refs': ['e1'], 'unknowns': []},
             {'requirement': 'established', 'trigger': 'reachable', 'behavior': 'satisfies',
              'protection': 'absent', 'counterevidence': 'refutes'},
             {'behavior': ['e1'], 'counterevidence': ['e1']},
             'The allowed domain includes done > total; the candidate covers both boundaries.'),
            (Evidence('callbacks.py', 1, 2, 'Synthetic source.',
                'def invoke(value, callback):\n    return callback(value)\n', 'invoke', 'parse_ast'),
             'Does invoke preserve a positive integer value?',
             'The callback implementation and its value-preservation contract are unavailable.',
             {'verdict': 'unknown', 'cause': None, 'when': None, 'refs': ['e1'],
              'unknowns': ['invoke(3, callback) forwards 3; the callback implementation or a preservation guarantee '
                           'is necessary to decide whether its returned value remains 3.']},
             {'requirement': 'established', 'trigger': 'reachable', 'behavior': 'unknown',
              'protection': 'unknown', 'counterevidence': 'unknown'}, {},
             'The callback return behavior is necessary and absent; the known path is stated.')]
        for evidence, question, scenario, wire, states, refs, reason in examples:
            with self.subTest(verdict=wire['verdict']):
                scope = prepare_scope([evidence], case_id='synthetic', attempt_id=evidence.symbol)
                candidate = parse_candidate(json.dumps(wire | {'scope': scope.binding}), scope)
                request = build_candidate_request(scope, question=question, scenario=scenario, assumptions=[])
                supplied = json.loads(build_verification_request(request, scope, candidate).messages[-1]['content'])
                self.assertEqual(supplied['candidate'], wire | {'scope': scope.binding})
                checked = {'scope': scope.binding, 'coverage': 'complete', 'checks': states,
                           'refs': refs, 'reason': reason, 'unknowns': wire['unknowns']}
                result = validate_verification(json.dumps(checked), candidate, scope)
                self.assertEqual(result['final'], candidate)
                self.assertFalse(result['semantic_proof'])


class V3DiagnosticCompactTests(unittest.TestCase):
    def setUp(self):
        self.scope = prepare_scope([Evidence('logic.py', 10, 11, 'Located implementation.',
            'def demo():\n    return 2\n', None, 'parse_ast')], case_id='case-a', attempt_id='attempt-a')
        self.wire = {'verdict': 'no_defect_supported', 'cause': 'The literal returned is 2.',
            'when': 'demo is called.', 'refs': ['e1'], 'unknowns': [], 'scope': self.scope.binding}
        self.checked = {'scope': self.scope.binding, 'coverage': 'complete', 'checks': {
            'requirement': {'state': 'established', 'refs': []},
            'trigger': {'state': 'reachable', 'refs': []},
            'behavior': {'state': 'satisfies', 'refs': ['e1']},
            'protection': {'state': 'absent', 'refs': []},
            'counterevidence': {'state': 'not_found', 'refs': []}},
            'reason': 'The returned literal satisfies the complete stated domain.', 'unknowns': []}

    def parse(self, wire=None, scope=None, **options):
        return compact_v3.parse_candidate(json.dumps(self.wire if wire is None else wire),
                                           self.scope if scope is None else scope, **options)

    def verify(self, checked=None, candidate=None, scope=None):
        return compact_v3.validate_verification(json.dumps(self.checked if checked is None else checked),
            self.parse() if candidate is None else candidate, self.scope if scope is None else scope)

    def test_three_states_keep_original_candidate_and_require_human_semantic_review(self):
        for verdict in compact_v3.VERDICTS:
            with self.subTest(verdict=verdict):
                wire = self.wire | {'verdict': verdict}
                checked = copy.deepcopy(self.checked)
                if verdict == 'supported_defect':
                    wire.update(cause='The literal is 2 although this case requires 3.', when='Call demo expecting 3.')
                    checked['checks']['behavior']['state'] = 'violates'
                elif verdict == 'unknown':
                    wire.update(cause=None, when=None, refs=[], unknowns=['The required return guarantee is absent.'])
                    checked['checks']['behavior'] = {'state': 'unknown', 'refs': []}
                    checked['unknowns'] = wire['unknowns']
                original = copy.deepcopy(wire)
                candidate = self.parse(wire)
                result = self.verify(checked, candidate)
                self.assertEqual(result['final'], candidate)
                self.assertEqual(wire, original)
                self.assertFalse(result['semantic_proof'])
                self.assertTrue(result['accepted'])

    def test_top_level_branches_reject_inconsistent_candidate_shapes(self):
        unknown = self.wire | {'verdict': 'unknown', 'cause': None, 'when': None,
                               'refs': [], 'unknowns': ['A necessary guarantee is absent.']}
        bad = [unknown | {'cause': 'A guessed explanation.'}, unknown | {'when': 'A guessed trigger.'},
               unknown | {'unknowns': []}, self.wire | {'unknowns': ['Unresolved guarantee.']},
               self.wire | {'refs': []}, self.wire | {'cause': None}, self.wire | {'when': ' '},
               self.wire | {'verdict': 'healthy'}, self.wire | {'extra': True}]
        for wire in bad:
            with self.subTest(wire=wire), self.assertRaises(ProviderError):
                self.parse(wire)
        schema = compact_v3.protocol_schema(self.scope)
        self.assertEqual(set(schema), {'oneOf'})
        self.assertEqual([branch['properties']['verdict']['const'] for branch in schema['oneOf']],
                         list(compact_v3.VERDICTS))
        self.assertTrue(all(list(branch['properties'])[0] == 'verdict' for branch in schema['oneOf']))

    def test_complete_long_explanations_do_not_flow_through_legacy_field_limits(self):
        cause = 'The source returns the literal independently of the supplied domain. ' * 11
        when = 'A call follows this complete visible function and returns its literal. ' * 6
        candidate = self.parse(self.wire | {'cause': cause, 'when': when})
        reason = 'The visible literal matches every call in the stated domain, with no extra assumption. ' * 5
        result = self.verify(self.checked | {'reason': reason}, candidate)
        self.assertEqual((result['final']['cause'], result['final']['when'], result['reason']), (cause, when, reason))
        unknown_wire = self.wire | {'verdict': 'unknown', 'cause': None, 'when': None, 'refs': [],
            'unknowns': ['The caller reaches an external dependency whose required return relation is absent. ' * 4]}
        unknown = self.parse(unknown_wire)
        checked = copy.deepcopy(self.checked)
        checked['checks']['behavior'] = {'state': 'unknown', 'refs': []}
        checked['unknowns'] = unknown['unknowns']
        self.assertEqual(self.verify(checked, unknown)['final'], unknown)
        self.assertTrue(candidate_quality_warnings(candidate))
        with self.assertRaises(ProviderError):
            self.parse(self.wire | {'cause': cause}, version=VERSION)

    def test_total_bytes_empty_text_unicode_and_scope_still_fail(self):
        explanation = '源码中的常量对所有允许输入保持不变。' * 100
        valid_content = json.dumps(self.wire | {'cause': explanation}, ensure_ascii=False)
        self.assertLess(len(valid_content.encode('utf-8')), 8192)
        self.assertEqual(compact_v3.parse_candidate(valid_content, self.scope)['cause'], explanation)
        content = json.dumps(self.wire | {'cause': '界' * 2800}, ensure_ascii=False)
        self.assertLess(len(content), 8192)
        self.assertGreater(len(content.encode('utf-8')), 8192)
        with self.assertRaises(ProviderError):
            compact_v3.parse_candidate(content, self.scope)
        for wire in [self.wire | {'cause': '界' * 3000}, self.wire | {'cause': '\ud800'},
                     self.wire | {'cause': ' '}, self.wire | {'scope': 'other-attempt'},
                     self.wire | {'refs': ['e99']}]:
            with self.subTest(field=list(wire)), self.assertRaises(ProviderError):
                self.parse(wire)
        for reason in [' ', '\ud800', '界' * 3000]:
            with self.subTest(reason_length=len(reason)), self.assertRaises(ProviderError):
                self.verify(self.checked | {'reason': reason})

    def test_each_required_ref_state_is_local_and_cannot_hide_empty_or_foreign_refs(self):
        schema = compact_v3.protocol_schema(self.scope, verifier=True)
        self.assertNotIn('refs', schema['properties'])
        for name, states in compact_v3.REQUIRED_REF_STATES.items():
            for state in states:
                for refs in [[], ['e99']]:
                    checked = copy.deepcopy(self.checked)
                    checked['checks'][name] = {'state': state, 'refs': refs}
                    with self.subTest(name=name, state=state, refs=refs), self.assertRaises(ProviderError):
                        self.verify(checked)
                row = next(branch for branch in schema['properties']['checks']['properties'][name]['oneOf']
                           if branch['properties']['state']['const'] == state)
                self.assertEqual(row['properties']['refs']['minItems'], 1)
                self.assertEqual(row['properties']['refs']['maxItems'], 2)
                checked = copy.deepcopy(self.checked)
                checked['checks'][name] = {'state': state, 'refs': ['e1']}
                self.assertTrue(compact_v3._shape(checked, schema))
                # A coherent state result is accepted; an explicit conflict is rejected after parsing.
                self.assertIn(self.verify(checked)['status'], ('accepted', 'rejected'))

    def test_coverage_missing_invalid_or_incomplete_never_publishes_final(self):
        for checked in [{k: v for k, v in self.checked.items() if k != 'coverage'},
                        self.checked | {'coverage': 'unknown'}]:
            with self.assertRaises(ProviderError):
                self.verify(checked)
        candidate = self.parse()
        result = self.verify(self.checked | {'coverage': 'incomplete'}, candidate)
        self.assertEqual(result['error_code'], 'verification_task_incomplete')
        self.assertIsNone(result['final'])
        self.assertEqual(candidate, self.parse())
        checked = copy.deepcopy(self.checked)
        checked['checks']['behavior']['state'] = 'violates'
        self.assertEqual(self.verify(checked)['error_code'], 'verification_cross_field_conflict')

    def test_v3_local_rows_reject_old_flat_refs_unknown_enum_and_fabricated_basis(self):
        invalid = []
        for row in ['satisfies', {'state': 'certain', 'refs': ['e1']},
                    {'state': True, 'refs': ['e1']}, {'state': 'satisfies'},
                    {'state': 'satisfies', 'refs': 'e1'},
                    {'state': 'satisfies', 'refs': ['e1'], 'basis': 'Invented per-check prose.'}]:
            checked = copy.deepcopy(self.checked)
            checked['checks']['behavior'] = row
            invalid.append(checked)
        invalid.append(self.checked | {'refs': {'behavior': ['e1']}})
        for checked in invalid:
            with self.subTest(row=checked['checks']['behavior']), self.assertRaises(ProviderError):
                self.verify(checked)

    def test_zero_and_locator_only_scope_has_no_impossible_reference_branch(self):
        scopes = [prepare_scope([], case_id='zero', attempt_id='one'),
                  prepare_scope([Evidence('logic.py', 1, 3, 'Only a locator.', '', None, 'parse_ast')],
                                case_id='locator', attempt_id='one')]
        for scope in scopes:
            with self.subTest(scope=scope.case_id):
                schema = compact_v3.protocol_schema(scope)
                self.assertEqual(len(schema['oneOf']), 1)
                self.assertEqual(schema['oneOf'][0]['properties']['verdict']['const'], 'unknown')
                wire = self.wire | {'scope': scope.binding, 'verdict': 'unknown', 'cause': None,
                    'when': None, 'refs': [], 'unknowns': ['The source guarantee necessary for this task is absent.']}
                candidate = self.parse(wire, scope)
                checked = {'scope': scope.binding, 'coverage': 'complete', 'checks': {
                    name: {'state': 'unknown' if 'unknown' in states else 'missing', 'refs': []}
                    for name, states in CHECK_STATES.items()}, 'reason': 'The necessary implementation guarantee is absent.',
                    'unknowns': wire['unknowns']}
                self.assertEqual(self.verify(checked, candidate, scope)['final'], candidate)
                with self.assertRaises(ProviderError):
                    self.parse(self.wire | {'scope': scope.binding}, scope)
                verifier = compact_v3.protocol_schema(scope, verifier=True)
                for name, row in verifier['properties']['checks']['properties'].items():
                    for branch in row['oneOf']:
                        self.assertNotIn(branch['properties']['state']['const'],
                                         compact_v3.REQUIRED_REF_STATES.get(name, ()))
                        self.assertEqual(branch['properties']['refs']['maxItems'], 0)

    def test_v3_builders_expose_valid_examples_and_soft_lengths_only(self):
        request = compact_v3.build_candidate_request(self.scope, question='Does demo satisfy the requirement?',
            scenario='Every call returns 2.', assumptions=['Ordinary Python integers.'],
            prompt_variant='requirement_preserving')
        verification = compact_v3.build_verification_request(request, self.scope, self.parse())
        for req, verifier in [(request, False), (verification, True)]:
            self.assertEqual(req.metadata['diagnostic_protocol'], compact_v3.VERSION)
            self.assertNotIn('maxLength', json.dumps(req.response_schema))
            self.assertNotIn('max_chars', req.messages[-1]['content'])
            payload = json.loads(req.messages[-1]['content'])
            samples = [payload['shape_example']] if verifier else payload['shape_examples']
            self.assertTrue(all(compact_v3._shape(sample, req.response_schema) for sample in samples))
            self.assertEqual((req.max_input_tokens, req.max_output_tokens, req.timeout_seconds), (1536, 512, 300))
        self.assertTrue(request.messages[0]['content'].endswith(REQUIREMENT_PRESERVING_PROMPT))
        self.assertNotIn('basis', json.dumps(json.loads(verification.messages[-1]['content'])['shape_example']))

    def test_legacy_v2_exact_request_schema_messages_and_limits_remain_reproducible(self):
        from macr.memory.run_store import encoded
        request = build_candidate_request(self.scope, question='Does demo satisfy the requirement?',
            scenario='Every call returns 2.', assumptions=['Ordinary Python integers.'])
        candidate = parse_candidate(json.dumps(self.wire), self.scope)
        verifier = compact_v3.build_verification_request(request, self.scope, candidate)
        # Frozen from 0b3b7b3 using this existing generic fixture, not an evaluation oracle.
        self.assertEqual(hashlib.sha256(encoded(asdict(request))).hexdigest(),
                         '569bb61b75443fc4db57d5e84ea4b686be686379a7feaea00d8aa69f445122f3')
        self.assertEqual(hashlib.sha256(encoded(asdict(verifier))).hexdigest(),
                         'a159548b1a0b9647950ef67d431a8d195076f510eb04c267c937eab2a3a82a47')
        self.assertEqual(verifier.metadata['diagnostic_protocol'], VERSION)
        with self.assertRaisesRegex(ProviderError, 'compact_protocol_mismatch'):
            compact_v3.build_verification_request(request, self.scope, candidate, version=compact_v3.VERSION)
        with self.assertRaisesRegex(ProviderError, 'compact_protocol_mismatch'):
            compact_v3.parse_candidate(json.dumps(self.wire), self.scope, version='unrecognized')


class DiagnosticFactContextTests(unittest.TestCase):
    def setUp(self):
        self.scope = prepare_scope([Evidence('policy.py', 1, 2, 'Complete visible source.',
            'def gate(flag, policy):\n    return flag and policy()\n', 'gate', 'parse_ast')],
            case_id='fact-case', attempt_id='fact-attempt')
        self.task = {'question': 'Does gate meet the supplied contract?',
                     'scenario': 'Assess boolean flag inputs under the supplied dependency contract.',
                     'assumptions': ['The deployed dependency is fixed and returns bool.']}

    def test_fact_checks_is_opt_in_and_default_request_remains_exact(self):
        from macr.memory.run_store import encoded
        original = compact_v3.build_candidate_request(self.scope, **self.task)
        # Frozen on the published baseline before adding the opt-in API.
        self.assertEqual(hashlib.sha256(encoded(asdict(original))).hexdigest(),
                         '6dcc1bbbd9324ea0cca14aa87cbe802f6837015ea0b7b0d5dd7455bc951ca73f')
        enabled = compact_v3.build_candidate_request(self.scope, **self.task, fact_checks=True)
        disabled = compact_v3.build_candidate_request(self.scope, **self.task, fact_checks=False)
        self.assertEqual(original, disabled)
        self.assertEqual(enabled.response_schema, original.response_schema)
        self.assertNotIn('observations', json.loads(original.messages[-1]['content']))
        self.assertIn('observations', json.loads(enabled.messages[-1]['content']))

    def candidate(self):
        return compact_v3.parse_candidate(json.dumps({'verdict': 'unknown', 'cause': None,
            'when': None, 'refs': [], 'unknowns': ['The necessary dependency return guarantee is absent.'],
            'scope': self.scope.binding}), self.scope)

    def test_fact_candidate_example_control_keeps_defaults_and_identical_verifier(self):
        from macr.memory.run_store import encoded
        request = compact_v3.build_candidate_request(self.scope, **self.task, fact_checks=True)
        payload = json.loads(request.messages[-1]['content'])
        self.assertTrue(payload['shape_examples'])
        # Keep the published fact-enabled default; omission remains an explicit experiment.
        self.assertEqual(hashlib.sha256(encoded(asdict(request))).hexdigest(),
                         'd15334a03bf576310a95e5c7189fcf56ddf5d4fc5192337d2d0c741d7cf18ee6')
        controlled = copy.deepcopy(request)
        payload['shape_examples'] = []
        controlled.messages[-1]['content'] = encoded(payload).decode()
        for candidate_request in (request, controlled):
            compact_v3.validate_candidate_context(candidate_request, self.scope)
        verifier = compact_v3.build_verification_request(request, self.scope, self.candidate())
        controlled_verifier = compact_v3.build_verification_request(controlled, self.scope, self.candidate())
        self.assertEqual(encoded(asdict(verifier)), encoded(asdict(controlled_verifier)))

    def test_fact_packet_and_exact_task_are_shared_without_changing_output_protocol(self):
        request = compact_v3.build_candidate_request(self.scope, **self.task, fact_checks=True,
                                                     prompt_variant='requirement_preserving')
        verifier = compact_v3.build_verification_request(request, self.scope, self.candidate())
        before, after = [json.loads(value.messages[-1]['content']) for value in (request, verifier)]
        self.assertEqual(before['observations'], after['observations'])
        self.assertEqual(before['source'], after['source'])
        self.assertEqual({name: after[name] for name in self.task}, self.task)
        self.assertEqual(request.response_schema, compact_v3.protocol_schema(self.scope))
        self.assertEqual(verifier.response_schema, compact_v3.protocol_schema(self.scope, verifier=True))
        self.assertEqual(request.metadata['diagnostic_observation_version'], 'visible_syntax_v1')
        self.assertEqual(request.metadata['diagnostic_observation_sha256'],
                         verifier.metadata['diagnostic_observation_sha256'])
        self.assertTrue(request.messages[0]['content'].endswith(REQUIREMENT_PRESERVING_PROMPT))
        self.assertFalse(after['observations']['repository_code_executed'])
        self.assertFalse(after['observations']['semantic_proof'])

    def test_fact_context_missing_mutated_or_misdeclared_binding_is_refused(self):
        original = compact_v3.build_candidate_request(self.scope, **self.task, fact_checks=True)
        for mutation in ('rows', 'task', 'missing_packet', 'missing_version', 'digest', 'source', 'hidden'):
            with self.subTest(mutation=mutation):
                request = copy.deepcopy(original)
                payload = json.loads(request.messages[-1]['content'])
                if mutation == 'rows':
                    payload['observations']['rows'][0]['ref'] = 'foreign'
                elif mutation == 'task':
                    payload['scenario'] = 'A substituted requirement.'
                elif mutation == 'missing_packet':
                    del payload['observations']
                elif mutation == 'missing_version':
                    del request.metadata['diagnostic_observation_version']
                elif mutation == 'digest':
                    request.metadata['diagnostic_observation_sha256'] = '0' * 64
                elif mutation == 'source':
                    payload['source'][0]['body'] = 'def gate():\n    return True\n'
                else:
                    payload['expected_verdict'] = 'unknown'
                request.messages[-1]['content'] = json.dumps(payload)
                with self.assertRaises(ProviderError):
                    compact_v3.build_verification_request(request, self.scope, self.candidate())
        for value in (None, 1, 'enabled'):
            with self.assertRaisesRegex(ProviderError, 'compact_observation_mode_invalid'):
                compact_v3.build_candidate_request(self.scope, **self.task, fact_checks=value)
        with self.assertRaisesRegex(ProviderError, 'compact_protocol_mismatch'):
            compact_v3.build_candidate_request(self.scope, **self.task, version=VERSION, fact_checks=True)

    def test_incomplete_and_invalid_syntax_are_observations_not_verdict_or_proof(self):
        for body, end, size, reason in [('def broken(\n', 1, 1200, 'source_syntax_unavailable'),
                ('first = 1\nsecond = 2\n', 2, 10, 'incomplete_or_locator')]:
            with self.subTest(reason=reason):
                scope = prepare_scope([Evidence('partial.py', 1, end, 'Captured source.', body)],
                    case_id='partial', attempt_id='one', max_body_chars=size)
                request = compact_v3.build_candidate_request(scope, **self.task, fact_checks=True)
                packet = json.loads(request.messages[-1]['content'])['observations']
                self.assertEqual(packet['rows'][0]['unsupported'], reason)
                self.assertNotIn('verdict', packet)
                self.assertFalse(packet['semantic_proof'])


if __name__ == '__main__':
    unittest.main()
