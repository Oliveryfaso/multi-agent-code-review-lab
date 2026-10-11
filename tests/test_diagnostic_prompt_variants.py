import copy
import unittest

from macr.evals.diagnostic_dataset import SYSTEM
from macr.evals.diagnostic_compare import OUTPUT_SCHEMA
from macr.evals.diagnostic_prompt_variants import apply_prompt_variant, EVIDENCE_FIRST_FIELDS
from macr.providers.base import ModelRequest, ProviderError


class DiagnosticPromptVariantTests(unittest.TestCase):
    def setUp(self):
        self.request = ModelRequest([{'role': 'system', 'content': SYSTEM},
                                     {'role': 'user', 'content': 'VISIBLE_SOURCE_SENTINEL'}],
                                    response_schema=OUTPUT_SCHEMA, max_input_tokens=1536,
                                    max_output_tokens=384, metadata={'prompt_version': 'dev-v1'})

    def test_baseline_and_cross_field_candidate_requests_are_identical(self):
        self.assertEqual(apply_prompt_variant(self.request, 'baseline'), self.request)
        self.assertEqual(apply_prompt_variant(self.request, 'cross_field'), self.request)

    def test_neutral_changes_only_system_prompt_with_same_verdict_and_bounds(self):
        result = apply_prompt_variant(self.request, 'neutral')
        self.assertTrue(result.messages[0]['content'].startswith(SYSTEM))
        self.assertIn('Give all three verdicts equal consideration', result.messages[0]['content'])
        result.messages[0] = copy.deepcopy(self.request.messages[0])
        self.assertEqual(result, self.request)

    def test_evidence_first_changes_only_recommended_field_order(self):
        result = apply_prompt_variant(self.request, 'evidence_first')
        self.assertIn('cause, when, refs, unknowns, verdict, in that order.', result.messages[0]['content'])
        self.assertEqual(EVIDENCE_FIRST_FIELDS, ('cause', 'when', 'refs', 'unknowns', 'verdict'))
        result.messages[0] = copy.deepcopy(self.request.messages[0])
        self.assertEqual(result, self.request)

    def test_request_is_never_mutated_and_unknown_variant_is_rejected(self):
        before = copy.deepcopy(self.request)
        for variant in ('baseline', 'neutral', 'evidence_first', 'cross_field'):
            result = apply_prompt_variant(self.request, variant)
            result.messages[-1]['content'] = 'Changed copy.'
            self.assertEqual(self.request, before)
        with self.assertRaisesRegex(ProviderError, 'diagnostic_variant_invalid'):
            apply_prompt_variant(self.request, 'everything_at_once')
        self.request.messages[0]['content'] = 'An unrelated prompt.'
        with self.assertRaisesRegex(ProviderError, 'diagnostic_variant_invalid'):
            apply_prompt_variant(self.request, 'neutral')


if __name__ == '__main__':
    unittest.main()
