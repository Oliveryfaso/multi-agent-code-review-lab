"""Synthetic response/review fixtures, never a real model accuracy measurement."""
import copy
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.evals.diagnostic_compare import prepare_comparison, record_response, compare_results, save_comparison
from macr.evals.diagnostic_dataset import load_dataset
from macr.investigation.scope import read_safe
from macr.memory.run_store import encoded
from macr.providers.base import ModelRequest
from macr.providers.local_http import LocalHTTPProvider, _object
from test_local_http_provider import FakeTransport, completion, fixture_profile

PROJECT = Path(__file__).resolve().parents[1]
DATASET = PROJECT / 'evals' / 'diagnostic-holdout-v1'

# Hand judgments of the supplied source/scenario, not a text or verdict scorer.
# Tuple order is mechanism, trigger, citation support, abstention reason, explanation.
ACCEPTED_REVIEWS = {
    'c01': ('supported', 'supported', 'supported', 'unknown',
        'Line 2 excludes upper; the closed-interval call contains(8, 2, 8) on line 6 must include 8.'),
    'c02': ('supported', 'supported', 'supported', 'unknown',
        'Lines 2-3 return None before empty-list indexing; line 4 reads the last item without mutation.'),
    'c03': ('unknown', 'unknown', 'supported', 'supported',
        'Line 2 delegates to codec.encode; its unavailable ordering implementation or contract is the needed evidence.'),
    'c04': ('supported', 'supported', 'supported', 'unknown',
        'Line 2 retains the same items list; the later append on line 8 changes the saved value read on line 9.'),
    'c05': ('supported', 'supported', 'supported', 'unknown',
        'Lines 2-3 create separate dicts; line 10 replaces one entry, and integer values cannot be mutated.'),
    'c06': ('unknown', 'unknown', 'supported', 'supported',
        'Line 2 applies the supplied predicate, whose unavailable rule determines whether exactly positive integers remain.'),
    'c07': ('supported', 'supported', 'supported', 'unknown',
        'Line 1 creates one default list; line 2 appends red then blue across the omitted-argument calls on lines 7-8.'),
    'c08': ('supported', 'supported', 'supported', 'unknown',
        'Line 2 creates a new list in both branches, so line 3 changes only that result for all shown calls.'),
    'c09': ('unknown', 'unknown', 'supported', 'supported',
        'Line 2 multiplies ttl by 60; the missing deployed unit declaration decides whether this is the required conversion.'),
    'c10': ('supported', 'supported', 'supported', 'unknown',
        'Line 2 subtracts the absolute argument; line 6 passes 25, yielding 55 instead of the required 60.'),
    'c11': ('supported', 'supported', 'supported', 'unknown',
        'Line 2 uses the exact bound string; line 6 explicitly supplies empty separator, while omission uses line 1 colon.'),
    'c12': ('unknown', 'unknown', 'supported', 'supported',
        'Line 2 indexes by an injected integer; its missing value or range decides safety for the three-element list.'),
}

REJECT_REVIEWS = {
    ('c01', 0): ('partial', 'partial', 'partial', 'unknown',
        'An edge-case label names neither the strict upper comparison on line 2 nor the concrete failure at value 8.'),
    ('c01', 1): ('contradicted', 'contradicted', 'contradicted', 'unknown',
        'The scenario requires a closed interval; line 2 excludes the upper endpoint, so all endpoints are not handled.'),
    ('c02', 0): ('contradicted', 'contradicted', 'contradicted', 'unknown',
        'Lines 2-3 return before index -1 for []; the claimed IndexError on the empty call cannot occur.'),
    ('c02', 1): ('unknown', 'unknown', 'partial', 'contradicted',
        'The scenario fixes ordinary integer lists; the guard and final-element read are supplied, so items behavior is not missing.'),
    ('c03', 0): ('unknown', 'unknown', 'unknown', 'unknown',
        'The call on line 2 supplies no codec ordering behavior; insertion order and the claimed trigger are invented.'),
    ('c03', 1): ('unknown', 'unknown', 'partial', 'partial',
        'More context does not name the missing codec.encode key-order behavior needed to decide the deployed requirement.'),
    ('c04', 0): ('contradicted', 'contradicted', 'contradicted', 'unknown',
        'The outer dict on line 2 holds the existing list reference; it does not copy that list before the later append.'),
    ('c04', 1): ('contradicted', 'contradicted', 'contradicted', 'unknown',
        'snapshot only returns a dict on line 2; the append occurs later on line 8, not when snapshot is called.'),
    ('c05', 0): ('contradicted', 'contradicted', 'contradicted', 'unknown',
        'The allowed values are immutable integers; replacing an entry in one newly created dict does not modify original.'),
    ('c05', 1): ('contradicted', 'contradicted', 'contradicted', 'unknown',
        'dict(row) is shallow, and nested mutable values are excluded; the claim about all possible values exceeds the scope.'),
    ('c06', 0): ('contradicted', 'unknown', 'contradicted', 'unknown',
        'filter uses the callback result on line 2, not an automatic positive-integer rule; that callback contract is absent.'),
    ('c06', 1): ('unknown', 'unknown', 'unknown', 'unknown',
        'No predicate body is supplied; whether it accepts negatives and produces the claimed failure is unavailable.'),
    ('c07', 0): ('contradicted', 'contradicted', 'contradicted', 'unknown',
        'list(labels) copies the return on line 3, but does not reset the shared default mutated on line 2.'),
    ('c07', 1): ('contradicted', 'contradicted', 'contradicted', 'unknown',
        'The default is a valid list and append accepts the stated strings; the shown calls retain earlier state rather than fail by type.'),
    ('c08', 0): ('contradicted', 'contradicted', 'contradicted', 'unknown',
        'Line 2 copies original before line 3 append, so attach("c", original) leaves the supplied list unchanged.'),
    ('c08', 1): ('unknown', 'unknown', 'partial', 'contradicted',
        'Ordinary list behavior and immutable strings are fixed by the scenario; both allocation branches are fully supplied.'),
    ('c09', 0): ('unknown', 'unknown', 'unknown', 'unknown',
        'The unit schema is missing; line 2 cannot establish the assumption that ttl is already in seconds.'),
    ('c09', 1): ('unknown', 'unknown', 'unknown', 'unknown',
        'The unit schema is missing; line 2 also cannot establish the assumption that ttl is in minutes.'),
    ('c10', 0): ('contradicted', 'contradicted', 'contradicted', 'unknown',
        'Line 6 binds percent=25 normally; line 2 returns 80-25=55, so the claimed default 10 and result 70 are false.'),
    ('c10', 1): ('contradicted', 'contradicted', 'contradicted', 'unknown',
        'The requirement removes 25 percent of 80, which is 20; subtracting the absolute 25 on line 2 yields the wrong total.'),
    ('c11', 0): ('contradicted', 'contradicted', 'contradicted', 'unknown',
        'The source has no truthiness fallback; the explicit empty string in line 6 is used by line 2 unchanged.'),
    ('c11', 1): ('unknown', 'unknown', 'partial', 'contradicted',
        'The separator is an ordinary immutable string, with stable bindings and complete concatenation source on line 2.'),
    ('c12', 0): ('contradicted', 'contradicted', 'contradicted', 'unknown',
        'The scenario explicitly injects ACTIVE_REGION before the call, contradicting the claimed unbound-name failure.'),
    ('c12', 1): ('contradicted', 'contradicted', 'contradicted', 'unknown',
        'A three-item list accepts -3 through 2; an arbitrary integer such as 3 does not become safe merely by its type.'),
}


class DiagnosticHoldoutReplayTests(unittest.TestCase):
    def setUp(self):
        self.cases, self.reference = load_dataset(DATASET / 'cases.json', DATASET / 'reference.json', timeout_seconds=180)
        self.answers = _object(read_safe(PROJECT, 'evals/diagnostic-holdout-v1/reference.json', 64 * 1024))['cases']
        self.profiles = [fixture_profile(profile_id='scripted-a', context_limit=2048, output_limit=384, timeout_seconds=180),
            fixture_profile(profile_id='scripted-b', model_id='fixture-model-b',
                context_limit=2048, output_limit=384, timeout_seconds=180)]

    def review(self, row, judgment):
        mechanism, trigger, citation, abstention, reason = judgment
        return {'record_sha256': row['record_sha256'], 'mechanism': mechanism, 'trigger': trigger,
            'citation_support': citation, 'abstention_reason': abstention, 'reason': reason,
            'evidence_refs': row['answer']['refs'] if citation == 'supported' else []}

    def capture(self, out, profile, case, answer):
        raw = completion(encoded(answer).decode(), {'prompt_tokens': 20, 'completion_tokens': 8, 'total_tokens': 28})
        raw['model'] = profile.model_id
        transport = FakeTransport(raw)
        counter = lambda request, configured: 20  # Synthetic adapter admission only; not a real tokenizer estimate.
        response = LocalHTTPProvider(profile, transport=transport, token_counter=counter).complete(ModelRequest(**case['request']))
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0][1]['messages'], case['request']['messages'])
        self.assertEqual(transport.calls[0][2], 180)
        self.assertEqual(response.metrics['token_bound_source'], 'injected_counter')
        self.assertNotIn('input_token_upper_bound', case['request']['metadata'])
        digest = lambda value: hashlib.sha256(encoded(value)).hexdigest()
        proof = {'request_sha256': digest(case['request']), 'profile_sha256': digest(asdict(profile)),
            'response_sha256': digest(asdict(response)), 'http_status': 200,
            'input_tokens': 20, 'output_tokens': 8, 'release': True, 'idle': True, 'remove_waiting': True}
        row = record_response(out, profile.profile_id, case['id'], response=response, proof=proof, provenance='scripted')
        self.assertEqual(row['failure_class'], 'semantic_pending')
        self.assertEqual(row['proof'], proof)
        return row

    def prepare_one(self, case):
        out = fixture_root() / 'comparison'
        reference = {'version': self.reference['version'], 'cases': {case['id']: self.reference['cases'][case['id']]},
            'topics': {case['id']: self.reference['topics'][case['id']]},
            'rubric': {case['id']: self.reference['rubric'][case['id']]}}
        result = prepare_comparison(out, [case], self.profiles[:1],
            prompt_version=case['request']['metadata']['prompt_version'],
            dataset_version=reference['version'], reference=reference)
        self.assertEqual((result['cells'], result['new_model_calls']), (1, 0))
        return out

    def test_real_dataset_cli_provider_record_and_category_report_without_model_calls(self):
        import compare_diagnostics
        out = fixture_root() / 'comparison'
        profiles_path = fixture_root() / 'profiles.json'
        profiles_path.write_bytes(encoded([asdict(profile) for profile in self.profiles]))
        output = io.StringIO()
        with patch('sys.argv', ['compare_diagnostics', '--dataset', str(DATASET / 'cases.json'),
                '--reference', str(DATASET / 'reference.json'), '--profiles', str(profiles_path), '--out', str(out)]), \
                patch('sys.stdout', output), \
                patch.object(compare_diagnostics, 'record_response', side_effect=AssertionError('preparation cannot capture')):
            self.assertEqual(compare_diagnostics.main(), 0)
        result = json.loads(output.getvalue())
        self.assertEqual((result['prepared'], result['cells'], result['new_model_calls']), (True, 24, 0))
        manifest = _object(read_safe(out, 'manifest.json', 128 * 1024))
        self.assertEqual(manifest['cases'], self.cases)
        self.assertEqual(_object(read_safe(out, 'state.json', 64 * 1024))['rows'], [])
        reviews = {}
        for profile in self.profiles:
            for case in self.cases:
                answer = copy.deepcopy(self.answers[case['id']]['accepted_shape_witness'])
                judgment = ACCEPTED_REVIEWS[case['id']]
                if profile.profile_id == 'scripted-b' and case['id'] == 'c01':
                    answer.update(cause='The top endpoint is rejected because the second comparison uses less-than.',
                        when='Passing value 8 with limits 2 and 8 gives false; this closed interval includes 8.')
                if profile.profile_id == 'scripted-b' and case['id'] == 'c03':
                    answer['refs'] = []
                    judgment = ('unknown', 'unknown', 'unknown', 'supported',
                        'No citation is claimed; the refusal identifies the absent deployed codec key-order contract.')
                row = self.capture(out, profile, case, answer)
                reviews[row['cell']] = self.review(row, judgment)
        report = compare_results(out, reviews=reviews)
        self.assertEqual(len(report['cases']), 24)
        for model in report['models']:
            self.assertEqual({key: model[key] for key in ('expected', 'recorded', 'captured_responses',
                'quality_cases', 'strict_json_passes', 'contract_passes', 'semantic_reviewed', 'diagnostic_passes')},
                dict.fromkeys(('expected', 'recorded', 'captured_responses', 'quality_cases',
                    'strict_json_passes', 'contract_passes', 'semantic_reviewed', 'diagnostic_passes'), 12))
            self.assertEqual((model['not_attempted'], model['infrastructure'], model['format'],
                model['semantic'], model['semantic_pending']), (0, 0, 0, 0, 0))
            self.assertEqual(model['diagnostic_rate'], 1)
            for category in model['categories']['expected_verdict'].values():
                self.assertEqual((category['expected'], category['diagnostic_passes']), (4, 4))
            self.assertEqual(sum(category['diagnostic_passes'] for category in model['categories']['topic'].values()), 12)
        paths = save_comparison(out, report)
        self.assertEqual(_object(Path(paths['json']).read_bytes()), report)
        self.assertIn('expected_verdict:unknown', Path(paths['markdown']).read_text())
        self.assertFalse(report['real_model_accuracy_measured'])
        self.assertEqual(report['new_model_calls'], 0)
        print(json.dumps({'fixture': 'holdout_full_chain', 'prepared_cells': 24, 'scripted_adapter_responses': 24,
            'strict_json_passes': 24, 'contract_passes': 24, 'manual_fixture_review_passes': 24,
            'new_model_calls': 0, 'real_model_accuracy_measured': False}))

    def test_each_reference_rejection_remains_semantic_failure_after_structural_acceptance(self):
        reject_keys = {(case['id'], index) for case in self.cases
            for index in range(len(self.answers[case['id']]['reject_examples']))}
        self.assertEqual(reject_keys, set(REJECT_REVIEWS))
        rejected = 0
        for case in self.cases:
            for index, example in enumerate(self.answers[case['id']]['reject_examples']):
                with self.subTest(case=case['id'], reject=index):
                    out = self.prepare_one(case)
                    row = self.capture(out, self.profiles[0], case, example['answer'])
                    report = compare_results(out, reviews={row['cell']: self.review(row, REJECT_REVIEWS[(case['id'], index)])})
                    model = report['models'][0]
                    self.assertEqual((model['strict_json_passes'], model['contract_passes'], model['semantic_reviewed'],
                        model['semantic'], model['diagnostic_passes'], model['format'], model['infrastructure']),
                        (1, 1, 1, 1, 0, 0, 0))
                    self.assertEqual(model['diagnostic_rate'], 0)
                    self.assertFalse(report['cases'][0]['diagnostic_passed'])
                    self.assertEqual(report['cases'][0]['failure_class'], 'semantic')
                    save_comparison(out, report)
                    rejected += 1
        self.assertEqual(rejected, 24)
        print(json.dumps({'fixture': 'holdout_rejections', 'reference_reject_examples': rejected,
            'strict_json_passes': 24, 'contract_passes': 24, 'manual_fixture_semantic_failures': 24,
            'new_model_calls': 0, 'real_model_accuracy_measured': False}))

    def test_correct_verdict_wrong_trigger_and_generic_no_citation_abstention_still_fail(self):
        for case_id, changes, judgment in (
            ('c10', {'when': 'discount(80, percent=25) returns 70 because the default 10 is used.'},
                ('supported', 'contradicted', 'partial', 'unknown',
                 'The absolute subtraction mechanism is right, but the actual explicit argument on line 6 yields 55, not 70.')),
            ('c03', {'refs': [], 'unknowns': ['More context is needed.']},
                ('unknown', 'unknown', 'unknown', 'partial',
                 'No citation is claimed; the generic refusal omits the specific codec key-order behavior that is absent.'))):
            with self.subTest(case=case_id):
                case = next(case for case in self.cases if case['id'] == case_id)
                answer = copy.deepcopy(self.answers[case_id]['accepted_shape_witness'])
                answer.update(changes)
                out = self.prepare_one(case)
                row = self.capture(out, self.profiles[0], case, answer)
                report = compare_results(out, reviews={row['cell']: self.review(row, judgment)})
                self.assertTrue(report['cases'][0]['verdict_correct'])
                self.assertEqual(report['cases'][0]['failure_class'], 'semantic')
                self.assertEqual((report['models'][0]['contract_passes'], report['models'][0]['diagnostic_passes']), (1, 0))


if __name__ == '__main__':
    unittest.main()
