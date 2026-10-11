"""Synthetic responses through the real static preview; no model or source execution."""
import json
import unittest
from dataclasses import asdict
from pathlib import Path

from debugger_fixtures import fixture_root, write_files
from macr.evals import diagnostic_compare
from macr.investigation.index import RepositoryIndex
from macr.investigation.records import ScopeProfile
from macr.investigation.scope import RepositoryScope
from macr.investigation.snapshots import SnapshotStore
from macr.investigation.static_locate import PROMPT_VERSION, _proposal, locate_static
from macr.investigation.validation import ContractError
from macr.providers.base import ModelRequest, ModelResponse, ProviderError
from test_local_http_provider import fixture_profile


# New development examples, never imported or executed as repository code.
DEVELOPMENT_SOURCES = {
    'dev_unicode_codepoints_healthy':
        "raise RuntimeError('development source must never execute')\n"
        'def codepoint_count(text):\n    return len(text)\n',
    'dev_unicode_bytes_defect':
        "raise RuntimeError('development source must never execute')\n"
        'def codepoint_count(text):\n    return len(text.encode("utf-8"))\n',
    'dev_tariff_unit_absent':
        "raise RuntimeError('development source must never execute')\n"
        'def quote(item, tariff):\n    return tariff.price(item)\n',
}


class SyntheticProvider:
    def __init__(self, answer, during_dispatch=None):
        self.content = answer if isinstance(answer, str) else json.dumps(answer)
        self.during_dispatch = during_dispatch
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if self.during_dispatch:
            self.during_dispatch()
        return ModelResponse(self.content, provider='synthetic', model='no-model')


class StaticLocateAbstentionTests(unittest.TestCase):
    def candidate(self):
        return {'claim': 'Synthetic hypothesis, not a verified diagnosis.',
                'trigger_conditions': ['The reported input takes the first branch.'],
                'locations': [{'file': 'entry.py', 'start': 2, 'end': 5}], 'gaps': []}

    def abstention(self):
        return {'status': 'insufficient_evidence', 'claim': None,
                'trigger_conditions': [], 'locations': [],
                'gaps': ['Reported runtime inputs are missing; static branches do not establish the failure trigger.']}

    def bundle(self, body=None):
        source = write_files(fixture_root(), {'entry.py': body if body is not None else
            "raise RuntimeError('analysed source must never execute')\n"
            'def start(flag):\n    if flag:\n        return 1\n    return 2\n'})
        store = SnapshotStore(fixture_root() / 'snapshots')
        bundle = store.capture(source, RepositoryScope().inventory(source, ScopeProfile()))
        return bundle, RepositoryIndex(store, fixture_root())

    def preview(self, answer, during_dispatch=None, *, body=None, symbol='start'):
        bundle, index = self.bundle(body)
        provider = SyntheticProvider(answer, (lambda: during_dispatch(bundle)) if during_dispatch else None)
        report = locate_static(bundle, index, provider, symptom='Synthetic failure, not reproduced.',
                               symbol_names=[symbol], input_token_upper_bound=128)
        self.assertEqual(len(provider.requests), 1, 'no repair, retry, or fallback')
        self.assertEqual(report['model_calls'], 1)
        self.assertFalse(report['repository_code_executed'])
        self.assertFalse(report['full_repository_verified'])
        return report, bundle, provider

    def development_source(self, case_id):
        lines = DEVELOPMENT_SOURCES[case_id].splitlines()
        return [{'file': 'entry.py', 'start': 2, 'end': 3,
                 'body': '\n'.join(f'{line}: {lines[line - 1]}' for line in (2, 3))}]

    def test_development_unicode_pair_rejects_duplicate_refs_without_repair(self):
        pair = (
            ('dev_unicode_codepoints_healthy', 'no_defect_supported',
             'len(text) counts Unicode code points as specified.'),
            ('dev_unicode_bytes_defect', 'supported_defect',
             'Encoding before len counts UTF-8 bytes instead of Unicode code points.'),
        )
        for case_id, verdict, cause in pair:
            with self.subTest(case_id=case_id):
                answer = {'verdict': verdict, 'cause': cause,
                          'when': "The supplied str is 'é': one code point, two UTF-8 bytes.",
                          'refs': [{'file': 'entry.py', 'start': 2, 'end': 3}], 'unknowns': []}
                source = self.development_source(case_id)
                self.assertEqual(diagnostic_compare._parse(json.dumps(answer), source), answer)
                duplicate = {**answer, 'refs': answer['refs'] * 2}
                self.assertEqual(diagnostic_compare._parse(json.dumps(duplicate), source), answer)
                self.assertEqual(len(duplicate['refs']), 2, 'original output remains unchanged')
                proposal = {'claim': cause, 'trigger_conditions': [answer['when']],
                            'locations': duplicate['refs'], 'gaps': []}
                report, _, _ = self.preview(proposal, body=DEVELOPMENT_SOURCES[case_id],
                                            symbol='codepoint_count')
                self.assertEqual(report['error_code'], 'citation_invalid')
                self.assertIsNone(report['hypothesis'])
                self.assertEqual(report['citations'], [])

    def test_development_legal_unknown_remains_semantic_pending_without_review(self):
        case_id = 'dev_tariff_unit_absent'
        answer = {'verdict': 'unknown', 'cause': None, 'when': None, 'refs': [],
                  'unknowns': ['The external tariff.price implementation and its pricing unit are absent.']}
        source = self.development_source(case_id)
        self.assertEqual(diagnostic_compare._parse(json.dumps(answer), source), answer)
        request = ModelRequest(
            [{'role': 'system', 'content': 'Diagnose only supplied development source.'},
             {'role': 'user', 'content': json.dumps({'source': source})}],
            response_schema=diagnostic_compare.OUTPUT_SCHEMA,
            max_input_tokens=64, max_output_tokens=16, timeout_seconds=1,
            metadata={'prompt_version': 'development-boundary-v1', 'input_token_upper_bound': 12})
        case = {'id': case_id, 'request': asdict(request)}
        profile = fixture_profile()
        root = fixture_root() / 'comparison'
        diagnostic_compare.prepare_comparison(
            root, [case], [profile], prompt_version='development-boundary-v1',
            dataset_version='development-boundary-v1',
            reference={'version': 'development-boundary-v1', 'cases': {case_id: 'unknown'}})
        response = ModelResponse(json.dumps(answer), model=profile.model_id, provider='synthetic',
                                 usage={'input_tokens': 9, 'output_tokens': 4}, usage_kind='measured')
        proof = {'request_sha256': diagnostic_compare._digest(case['request']),
                 'profile_sha256': diagnostic_compare._digest(asdict(profile)),
                 'response_sha256': diagnostic_compare._digest(asdict(response)),
                 'http_status': 200, 'input_tokens': 9, 'output_tokens': 4,
                 'release': True, 'idle': True, 'remove_waiting': True}
        diagnostic_compare.record_response(root, profile.profile_id, case_id,
                                           response=response, proof=proof, provenance='scripted')
        report = diagnostic_compare.compare_results(root)
        row, metrics = report['cases'][0], report['models'][0]
        self.assertTrue(row['contract_valid'])
        self.assertTrue(row['verdict_correct'])
        self.assertEqual(row['failure_class'], 'semantic_pending')
        self.assertEqual(row['semantic_review'], 'pending')
        self.assertFalse(row['diagnostic_passed'])
        self.assertEqual(metrics['diagnostic_passes'], 0)
        self.assertIsNone(metrics['diagnostic_rate'])
        self.assertEqual(report['new_model_calls'], 0)
        self.assertFalse(report['real_model_accuracy_measured'])

    def test_development_missing_tariff_unit_creates_no_static_hypothesis(self):
        gap = 'The external tariff.price implementation and its pricing unit are absent.'
        answer = {'status': 'insufficient_evidence', 'claim': None,
                  'trigger_conditions': [], 'locations': [], 'gaps': [gap]}
        report, _, _ = self.preview(answer, body=DEVELOPMENT_SOURCES['dev_tariff_unit_absent'],
                                    symbol='quote')
        self.assertEqual(report['status'], 'inconclusive')
        self.assertIsNone(report['error_code'])
        self.assertIsNone(report['hypothesis'])
        self.assertEqual(report['citations'], [])
        self.assertEqual(report['trigger_conditions'], [])
        self.assertIn(gap, report['gaps'])
        self.assertNotIn('diagnostic_passed', report)

    def test_legacy_and_explicit_candidates_preserve_source_evidence_without_grade_promotion(self):
        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                answer = self.candidate()
                if explicit:
                    answer['status'] = 'candidate'
                report, bundle, _ = self.preview(answer)
                self.assertEqual(report['status'], 'candidate')
                self.assertIsNone(report['error_code'])
                self.assertEqual(report['hypothesis']['status'], 'candidate')
                citation = report['citations'][0]
                self.assertEqual(citation['snapshot_id'], bundle.ref.snapshot_id)
                self.assertEqual(citation['excerpt'], 'def start(flag):\n    if flag:\n        return 1\n    return 2\n')
                self.assertEqual(report['hypothesis']['support_ids'], [citation['evidence_id']])
                self.assertNotIn('diagnostic_passed', report)

    def test_legal_abstention_preserves_gaps_without_creating_a_hypothesis(self):
        answer = self.abstention()
        report, _, _ = self.preview(answer)
        self.assertEqual(report['status'], 'inconclusive')
        self.assertIsNone(report['error_code'])
        self.assertIsNone(report['hypothesis'])
        self.assertEqual(report['citations'], [])
        self.assertEqual(report['trigger_conditions'], [])
        self.assertIn(answer['gaps'][0], report['gaps'])
        self.assertTrue(any(gap.startswith('static_unknown:') for gap in report['gaps']))

    def test_malformed_abstentions_are_rejected_instead_of_repaired(self):
        invalid = [
            {**self.abstention(), 'claim': 'A guessed cause'},
            {**self.abstention(), 'trigger_conditions': ['a guessed trigger']},
            {**self.abstention(), 'locations': self.candidate()['locations']},
            {**self.abstention(), 'gaps': []},
            {**self.abstention(), 'gaps': ['  ']},
            {**self.abstention(), 'status': 'healthy'},
            {**self.abstention(), 'extra': 'ignored'},
            {**self.abstention(), 'gaps': ['missing'] * 7},
            {**self.abstention(), 'gaps': ['missing'] + ['x' * 1001]},
            json.dumps(self.abstention()).replace('"claim": null', '"claim": null, "claim": null'),
            json.dumps(self.abstention()).replace('null', 'NaN', 1),
        ]
        for answer in invalid:
            with self.subTest(answer=answer), self.assertRaises(ContractError) as caught:
                _proposal(answer if isinstance(answer, str) else json.dumps(answer))
            self.assertEqual(caught.exception.code, 'model_output_invalid')
        report, _, _ = self.preview(invalid[0])
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['error_code'], 'model_output_invalid')
        self.assertIsNone(report['hypothesis'])
        self.assertEqual(report['citations'], [])

    def test_duplicate_and_noncontract_locations_are_rejected(self):
        answer = self.candidate()
        answer['locations'] *= 2
        report, _, _ = self.preview(answer)
        self.assertEqual(report['error_code'], 'citation_invalid')
        self.assertIsNone(report['hypothesis'])
        self.assertEqual(report['citations'], [])
        for extra in ({'schema_version': 1}, {'symbol_id': None}, {'extra': True}):
            answer = self.candidate()
            answer['locations'][0].update(extra)
            with self.subTest(extra=extra), self.assertRaises(ContractError) as caught:
                _proposal(json.dumps(answer))
            self.assertEqual(caught.exception.code, 'model_output_invalid')

    def test_source_bounds_paths_and_line_types_remain_strict(self):
        for location, code in [
            ({'file': 'unseen.py', 'start': 2, 'end': 5}, 'citation_invalid'),
            ({'file': 'entry.py', 'start': 1, 'end': 5}, 'citation_invalid'),
            ({'file': 'entry.py', 'start': 2, 'end': 6}, 'citation_invalid'),
            ({'file': '../entry.py', 'start': 2, 'end': 5}, 'citation_invalid'),
            ({'file': 'entry.py', 'start': 5, 'end': 2}, 'citation_invalid'),
            ({'file': 'entry.py', 'start': True, 'end': 5}, 'model_output_invalid'),
        ]:
            with self.subTest(location=location):
                answer = self.candidate(); answer['locations'] = [location]
                report, _, _ = self.preview(answer)
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['error_code'], code)
                self.assertIsNone(report['hypothesis'])
                self.assertEqual(report['citations'], [])

    def test_candidate_and_abstention_both_reject_snapshot_drift_during_dispatch(self):
        def change_snapshot(bundle):
            (Path(bundle.ref.root_ref) / 'entry.py').write_text('changed synthetic snapshot\n')
        for answer in (self.candidate(), self.abstention()):
            with self.subTest(answer=answer):
                report, _, _ = self.preview(answer, change_snapshot)
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['error_code'], 'stale_snapshot')
                self.assertIsNone(report['hypothesis'])
                self.assertEqual(report['citations'], [])

    def test_prepare_only_versions_the_contract_without_dispatch(self):
        bundle, index = self.bundle()
        provider = SyntheticProvider(self.abstention())
        report = locate_static(bundle, index, provider, symptom='Synthetic report.',
                               symbol_names=['start'], input_token_upper_bound=128, prepare_only=True)
        self.assertEqual(report['status'], 'prepared')
        self.assertEqual(report['model_calls'], 0)
        self.assertEqual(provider.requests, [])
        self.assertEqual(PROMPT_VERSION, 'static-locate-preview-facts-v2')
        self.assertEqual(report['model_request']['metadata']['prompt_version'], PROMPT_VERSION)
        self.assertIn('insufficient_evidence', report['model_request']['messages'][0]['content'])
        self.assertEqual(report['model_request']['response_schema']['required'],
                         ['claim', 'trigger_conditions', 'locations', 'gaps'])
