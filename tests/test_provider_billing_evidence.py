import unittest

from macr.providers import base


def billing_evidence():
    return {
        'schema': 'macr.provider.billing.v1',
        'provider': 'deepseek',
        'model': 'deepseek-flash',
        'request_sha256': 'a' * 64,
        'max_output_tokens': 200,
        'raw_usage': {
            'prompt_tokens': 100,
            'completion_tokens': 20,
            'total_tokens': 120,
            'prompt_cache_hit_tokens': 30,
            'prompt_cache_miss_tokens': 70,
            'prompt_tokens_details': {'cached_tokens': 30},
            'completion_tokens_details': {'reasoning_tokens': 15},
        },
        'latency_ms': 9,
    }


class BillingEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(base, 'validate_billing_evidence', None)),
                        'independent billing evidence validator missing')

    def assert_invalid(self, value):
        with self.assertRaises(ValueError) as error:
            base.validate_billing_evidence(value)
        self.assertEqual(str(error.exception), 'provider_billing_evidence_invalid')
        self.assertNotIsInstance(error.exception, base.ProviderError)

    def test_complete_evidence_retains_canonical_counters_in_an_independent_copy(self):
        value = billing_evidence()
        result = base.validate_billing_evidence(value)
        self.assertEqual(result, billing_evidence())
        self.assertIsNot(result, value)
        self.assertIsNot(result['raw_usage'], value['raw_usage'])
        self.assertIsNot(result['raw_usage']['prompt_tokens_details'],
                         value['raw_usage']['prompt_tokens_details'])
        value['raw_usage']['prompt_tokens_details']['cached_tokens'] = 99
        self.assertEqual(result['raw_usage']['prompt_tokens_details'], {'cached_tokens': 30})
        result['raw_usage']['completion_tokens_details']['reasoning_tokens'] = 1
        self.assertEqual(value['raw_usage']['completion_tokens_details'], {'reasoning_tokens': 15})

    def test_positive_usage_and_scalar_limits_include_their_boundaries(self):
        for prompt, output, cap, latency in ((1, 1, 1, 0),
                (1_000_000, 2 ** 31 - 1, 2 ** 31 - 1, 2 ** 31 - 1)):
            with self.subTest(prompt=prompt, output=output):
                value = billing_evidence()
                value.update(max_output_tokens=cap, latency_ms=latency, model='x' * 256)
                value['raw_usage'] = {'prompt_tokens': prompt, 'completion_tokens': output}
                self.assertEqual(base.validate_billing_evidence(value), value)

    def test_envelope_requires_only_the_exact_named_fields(self):
        self.assert_invalid(None)
        self.assert_invalid([])
        for name in billing_evidence():
            with self.subTest(missing=name):
                value = billing_evidence()
                del value[name]
                self.assert_invalid(value)
        for name, invalid_values in {
            'schema': ('macr.provider.failure.v1', None, True),
            'provider': ('mock', None, True),
            'model': ('', 'x' * 257, None, True, 12),
            'request_sha256': ('', 'a' * 63, 'a' * 65, 'A' * 64, 'g' * 64,
                               'a' * 63 + '\n', None, True, 12),
            'max_output_tokens': (0, -1, True, 2.5, '200', None, 2 ** 31),
            'latency_ms': (-1, True, 2.5, '9', None, 2 ** 31),
            'raw_usage': (None, [], 12),
        }.items():
            for invalid in invalid_values:
                with self.subTest(field=name, invalid=invalid):
                    value = billing_evidence()
                    value[name] = invalid
                    self.assert_invalid(value)

    def test_required_counters_must_be_positive_integers_within_caps(self):
        for counter in ('prompt_tokens', 'completion_tokens'):
            for invalid in (None, True, False, -1, 0, 1.5, '1', [], {}):
                with self.subTest(counter=counter, invalid=invalid):
                    value = billing_evidence()
                    value['raw_usage'] = {'prompt_tokens': 100, 'completion_tokens': 20}
                    value['raw_usage'][counter] = invalid
                    self.assert_invalid(value)
            value = billing_evidence()
            del value['raw_usage'][counter]
            self.assert_invalid(value)
        for usage in ({'prompt_tokens': 1_000_001, 'completion_tokens': 20},
                      {'prompt_tokens': 100, 'completion_tokens': 201}, {}):
            with self.subTest(usage=usage):
                value = billing_evidence()
                value['raw_usage'] = usage
                self.assert_invalid(value)

    def test_optional_counters_and_nested_details_are_strictly_typed(self):
        locations = (
            ('total_tokens',), ('prompt_cache_hit_tokens',), ('prompt_cache_miss_tokens',),
            ('prompt_tokens_details', 'cached_tokens'),
            ('completion_tokens_details', 'reasoning_tokens'),
        )
        for location in locations:
            for invalid in (None, True, -1, 1.5, '1', [], {}, 2 ** 63):
                with self.subTest(location=location, invalid=invalid):
                    value = billing_evidence()
                    target = value['raw_usage']
                    for name in location[:-1]:
                        target = target[name]
                    target[location[-1]] = invalid
                    self.assert_invalid(value)
        for detail in ('prompt_tokens_details', 'completion_tokens_details'):
            for invalid in (None, True, 1, '1', []):
                with self.subTest(detail=detail, invalid=invalid):
                    value = billing_evidence()
                    value['raw_usage'][detail] = invalid
                    self.assert_invalid(value)

    def test_total_cache_and_reasoning_relations_remain_consistent(self):
        for changes in (
                {'total_tokens': 119},
                {'prompt_cache_hit_tokens': 101},
                {'prompt_cache_miss_tokens': 101},
                {'prompt_cache_miss_tokens': 69},
                {'prompt_tokens_details': {'cached_tokens': 101}},
                {'prompt_tokens_details': {'cached_tokens': 29}},
                {'completion_tokens_details': {'reasoning_tokens': 21}}):
            with self.subTest(changes=changes):
                value = billing_evidence()
                value['raw_usage'].update(changes)
                self.assert_invalid(value)

    def test_zero_optional_counters_and_empty_canonical_details_are_valid(self):
        value = billing_evidence()
        value['raw_usage'] = {
            'prompt_tokens': 100, 'completion_tokens': 20,
            'prompt_cache_hit_tokens': 0, 'prompt_cache_miss_tokens': 100,
            'prompt_tokens_details': {'cached_tokens': 0},
            'completion_tokens_details': {'reasoning_tokens': 0},
        }
        self.assertEqual(base.validate_billing_evidence(value), value)
        value['raw_usage']['prompt_tokens_details'] = {}
        value['raw_usage']['completion_tokens_details'] = {}
        self.assertEqual(base.validate_billing_evidence(value), value)

    def test_unknown_extension_values_are_rejected_without_inspection_or_copying(self):
        class Sentinel:
            def __deepcopy__(self, memo):
                raise AssertionError('extension copied')
            def __str__(self):
                raise AssertionError('extension stringified')
            def __repr__(self):
                raise AssertionError('extension represented')
            def __eq__(self, other):
                raise AssertionError('extension compared')

        for location in ((), ('raw_usage',), ('raw_usage', 'prompt_tokens_details'),
                         ('raw_usage', 'completion_tokens_details')):
            with self.subTest(location=location):
                value = billing_evidence()
                target = value
                for name in location:
                    target = target[name]
                target['private_extension'] = Sentinel()
                self.assert_invalid(value)
        value = billing_evidence()
        value['raw_usage']['prompt_tokens'] = Sentinel()
        self.assert_invalid(value)

    def test_provider_error_carries_billing_without_manufacturing_a_response(self):
        value = billing_evidence()
        error = base.ProviderError('model_response_invalid', billing_evidence=value)
        self.assertIsNone(error.response)
        self.assertIsNone(error.diagnostic)
        self.assertEqual(error.failure_kind, 'invalid_contract')
        self.assertEqual(str(error), 'model_response_invalid')
        value['model'] = 'caller mutation'
        value['raw_usage']['completion_tokens_details']['reasoning_tokens'] = 0
        first_read = error.billing_evidence
        self.assertEqual(first_read, billing_evidence())
        first_read['request_sha256'] = 'b' * 64
        first_read['raw_usage']['prompt_tokens_details']['cached_tokens'] = 0
        second_read = error.billing_evidence
        self.assertEqual(second_read, billing_evidence())
        self.assertIsNot(first_read, second_read)
        self.assertIsNot(first_read['raw_usage'], second_read['raw_usage'])

    def test_provider_error_validates_billing_independently_of_existing_response_behavior(self):
        response = base.ModelResponse('retained answer')
        diagnostic = {'schema': 'macr.provider.failure.v1', 'stage': 'assistant_message'}
        error = base.ProviderError('model_response_invalid', response=response,
                                   diagnostic=diagnostic, billing_evidence=billing_evidence())
        self.assertIs(error.response, response)
        self.assertEqual(error.diagnostic, diagnostic)
        self.assertEqual(error.billing_evidence, billing_evidence())
        self.assertIsNone(base.ProviderError('model_timeout').billing_evidence)
        invalid = billing_evidence()
        invalid['raw_usage']['completion_tokens'] = 0
        with self.assertRaisesRegex(ValueError, '^provider_billing_evidence_invalid$'):
            base.ProviderError('model_response_invalid', billing_evidence=invalid)


if __name__ == '__main__':
    unittest.main()
