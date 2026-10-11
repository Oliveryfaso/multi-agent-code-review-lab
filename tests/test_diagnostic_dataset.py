import copy
import json
import unittest
from pathlib import Path

from debugger_fixtures import fixture_root


class DiagnosticDatasetTests(unittest.TestCase):
    def setUp(self):
        self.root = fixture_root() / 'dataset'
        self.root.mkdir()
        self.case = {'id': 'c01', 'source': [{'file': 'logic.py', 'start': 1,
            'end': 2, 'body': 'def demo():\n    return 2\n'}], 'question': 'Does this meet the requirement?',
            'scenario': 'demo() should return 2.', 'assumptions': ['Ordinary Python builtins.']}
        answer = {'verdict': 'no_defect_supported', 'cause': 'The literal return matches the supplied requirement.',
                  'when': 'The no-argument demo() is called.', 'refs': [{'file': 'logic.py', 'start': 2, 'end': 2}], 'unknowns': []}
        self.data = {'version': 'dataset-v1', 'prompt_version': 'dataset-v1', 'cases': [self.case]}
        self.reference = {'version': 'dataset-v1', 'cases': {'c01': {
            'category': 'no_defect_supported', 'topic': 'boundary', 'mechanism': 'REFERENCE_ONLY_SENTINEL',
            'trigger': 'Call demo().', 'missing_evidence': [],
            'evidence_roles': [{'file': 'logic.py', 'start': 2, 'end': 2, 'role': 'return'}],
            'accepted_shape_witness': answer, 'reject_examples': [],
            'safe_probe': {'function': 'demo', 'args': [], 'expected_return': 2}}}}

    def load(self):
        from macr.evals.diagnostic_dataset import load_dataset
        for name, value in [('cases.json', self.data), ('reference.json', self.reference)]:
            (self.root / name).write_text(json.dumps(value))
        return load_dataset(self.root / 'cases.json', self.root / 'reference.json')

    def test_reference_never_enters_requests_and_no_text_oracle(self):
        cases, reference = self.load()
        request = cases[0]['request']
        self.assertNotIn('REFERENCE_ONLY_SENTINEL', json.dumps(request))
        self.assertEqual(set(json.loads(request['messages'][-1]['content'])),
                         {'source', 'question', 'scenario', 'assumptions'})
        self.assertEqual(reference['cases'], {'c01': 'no_defect_supported'})
        self.assertEqual(reference['topics'], {'c01': 'boundary'})
        self.assertEqual(reference['rubric'], self.reference['cases'])
        self.reference['cases']['c01']['accepted_shape_witness']['cause'] = 'Another valid explanation.'
        changed, _ = self.load()
        self.assertEqual(cases, changed)

    def test_complete_human_rubric_is_frozen_and_drift_is_refused(self):
        from macr.evals.diagnostic_compare import prepare_comparison, compare_results
        from test_local_http_provider import fixture_profile
        cases, reference = self.load()
        profile = fixture_profile(context_limit=2048, output_limit=384, timeout_seconds=60)
        out = self.root / 'comparison'
        prepare_comparison(out, cases, [profile], prompt_version='dataset-v1',
                           dataset_version='dataset-v1', reference=reference)
        frozen = json.loads((out / 'reference.json').read_text())
        self.assertEqual(frozen['rubric'], self.reference['cases'])
        frozen['rubric']['c01']['mechanism'] = 'Rewritten scoring basis.'
        (out / 'reference.json').write_text(json.dumps(frozen))
        with self.assertRaisesRegex(ValueError, 'reference_drift'):
            compare_results(out)

    def test_partial_or_mismatched_rubric_is_refused_before_freeze(self):
        from macr.evals.diagnostic_compare import prepare_comparison
        from test_local_http_provider import fixture_profile
        cases, reference = self.load()
        profile = fixture_profile(context_limit=2048, output_limit=384, timeout_seconds=60)
        for change in ('missing', 'wrong_category', 'wrong_topic', 'extra'):
            broken = copy.deepcopy(reference)
            if change == 'missing': broken['rubric'].pop('c01')
            if change == 'wrong_category': broken['rubric']['c01']['category'] = 'unknown'
            if change == 'wrong_topic': broken['rubric']['c01']['topic'] = 'other'
            if change == 'extra': broken['rubric']['c01']['answer_key'] = 'unexpected'
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'rubric_invalid'):
                prepare_comparison(self.root / change, cases, [profile], prompt_version='dataset-v1',
                                   dataset_version='dataset-v1', reference=broken)
            self.assertFalse((self.root / change).exists())

    def test_template_policy_cannot_differ_between_new_profiles(self):
        from dataclasses import replace
        from macr.evals.diagnostic_compare import prepare_comparison
        from test_local_http_provider import fixture_profile
        cases, reference = self.load()
        profile = fixture_profile(context_limit=2048, output_limit=384, timeout_seconds=60)
        other = replace(profile, profile_id='other', chat_template_version='different-template-v1')
        out = self.root / 'different-template'
        with self.assertRaisesRegex(ValueError, 'comparison_parameters_differ'):
            prepare_comparison(out, cases, [profile, other], prompt_version='dataset-v1',
                               dataset_version='dataset-v1', reference=reference)
        self.assertFalse(out.exists())

    def test_rejects_answer_or_category_on_input_side(self):
        for name in ('category', 'accepted_shape_witness', 'expected_verdict'):
            with self.subTest(name=name):
                self.data['cases'][0][name] = 'leak'
                with self.assertRaises(ValueError): self.load()
                self.data['cases'][0].pop(name)

    def test_missing_extra_and_inconsistent_reference_are_refused(self):
        original = copy.deepcopy(self.reference)
        for change in ('missing', 'extra', 'wrong_version', 'wrong_witness'):
            self.reference = copy.deepcopy(original)
            if change == 'missing': self.reference['cases'].pop('c01')
            if change == 'extra': self.reference['cases']['c02'] = copy.deepcopy(original['cases']['c01'])
            if change == 'wrong_version': self.reference['version'] = 'other-v1'
            if change == 'wrong_witness': self.reference['cases']['c01']['accepted_shape_witness']['verdict'] = 'supported_defect'
            with self.subTest(change=change), self.assertRaises(ValueError): self.load()

    def test_bad_source_ranges_and_citations_are_refused(self):
        original = copy.deepcopy(self.data)
        for change in ('wrong_length', 'path_escape', 'boolean_line'):
            self.data = copy.deepcopy(original)
            source = self.data['cases'][0]['source'][0]
            if change == 'wrong_length': source['end'] = 3
            if change == 'path_escape': source['file'] = '../logic.py'
            if change == 'boolean_line': source['start'] = True
            with self.subTest(change=change), self.assertRaises(ValueError): self.load()
        self.data = original
        self.reference['cases']['c01']['evidence_roles'][0]['end'] = 3
        with self.assertRaises(ValueError): self.load()

    def test_unknown_requires_specific_reference_evidence_without_executing_probe(self):
        row = self.reference['cases']['c01']
        row.update(category='unknown', mechanism=None, trigger=None, missing_evidence=['The caller object is absent.'],
                   safe_probe=None, accepted_shape_witness={'verdict': 'unknown', 'cause': None, 'when': None,
                                                          'refs': [], 'unknowns': ['Caller object is absent.']})
        _, reference = self.load()
        self.assertEqual(reference['cases']['c01'], 'unknown')
        row['missing_evidence'] = []
        with self.assertRaises(ValueError): self.load()

    def test_bounded_read_duplicate_keys_and_external_paths_are_refused(self):
        from macr.evals.diagnostic_dataset import load_dataset
        self.load()
        path = self.root / 'cases.json'
        path.write_bytes(b' ' * (64 * 1024 + 1))
        with self.assertRaises(ValueError): load_dataset(path, self.root / 'reference.json')
        path.write_text('{"version":"v1","version":"v2"}')
        with self.assertRaises(ValueError): load_dataset(path, self.root / 'reference.json')
        with self.assertRaises(ValueError): load_dataset(Path('/tmp/cases.json'), self.root / 'reference.json')

    def test_proposed_timeout_is_explicit_and_bounded_without_execution(self):
        from macr.evals.diagnostic_dataset import load_dataset
        cases, _ = self.load()
        self.assertEqual(cases[0]['request']['timeout_seconds'], 60)
        cases, _ = load_dataset(self.root / 'cases.json', self.root / 'reference.json', timeout_seconds=180)
        self.assertEqual(cases[0]['request']['timeout_seconds'], 180)
        for timeout in (True, 0, 181, float('nan'), float('inf')):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                load_dataset(self.root / 'cases.json', self.root / 'reference.json', timeout_seconds=timeout)


if __name__ == '__main__':
    unittest.main()
