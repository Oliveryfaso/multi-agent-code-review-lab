"""Synthetic generation-profile contracts; no providers or study data."""
import importlib
import importlib.util
import unittest
from unittest.mock import patch

from macr.evals.deepseek_policy_spend import _normalized_charge, _usage_evidence


class StudyGenerationTests(unittest.TestCase):
    def generation(self):
        name = 'macr.evals.deepseek_study_generation'
        self.assertIsNotNone(importlib.util.find_spec(name),
            'versioned generation profile is missing')
        return importlib.import_module(name)

    def test_legacy_contract_is_fresh_and_preserves_exact_values(self):
        module = self.generation()
        expected = {'name':None,'prompt_version':'deepseek-policy-contracts-v3',
            'max_output_per_request':2000,'max_total_output_tokens':6000,
            'planned_input_per_request':8000,'max_total_input_tokens':24000,
            'context_input_envelope':1000000,'reservation_micro_usd':330000,
            'minimum_reservation_micro_usd':302400}
        first = module.get_generation()
        self.assertEqual(first, expected)
        first['max_output_per_request'] = 8192
        second = module.get_generation(None)
        self.assertEqual(second, expected)
        self.assertIsNot(first, second)

    def test_v2_contract_is_fresh_and_derives_8192_bound(self):
        module = self.generation()
        self.assertEqual(module.V2_PROFILE, 'study-8192-v2')
        self.assertEqual(module.V2_PROMPT_VERSION, 'deepseek-study-8192-v2')
        expected = {'name':'study-8192-v2','prompt_version':'deepseek-study-8192-v2',
            'max_output_per_request':8192,'max_total_output_tokens':24576,
            'planned_input_per_request':8000,'max_total_input_tokens':24000,
            'context_input_envelope':1048576,'reservation_micro_usd':330000,
            'minimum_reservation_micro_usd':324404}
        first = module.get_generation(module.V2_PROFILE)
        self.assertEqual(first, expected)
        self.assertEqual(first['max_total_output_tokens'],3*first['max_output_per_request'])
        self.assertGreaterEqual(first['reservation_micro_usd'],
            first['minimum_reservation_micro_usd'])
        first['prompt_version'] = 'changed'
        self.assertEqual(module.get_generation(module.V2_PROFILE),expected)

    def test_only_none_or_exact_v2_name_is_accepted(self):
        module = self.generation()
        for value in ('','legacy','study-2000-v1','study-8192-v3','STUDY-8192-V2',
                ' study-8192-v2','study-8192-v2\n',True,False,1,8192,None.__class__,
                [],{},object()):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaisesRegex(ValueError,'study_generation_profile_invalid'):
                    module.get_generation(value)

    def test_reservation_uses_integer_ceil_and_rejects_old_reserve_at_16384(self):
        module = self.generation()
        minimum = module.minimum_reservation_micro_usd
        self.assertEqual(minimum(1,context_input_envelope=1),2)
        self.assertEqual(minimum(2000,context_input_envelope=1000000),302400)
        self.assertEqual(minimum(4096),319488)
        self.assertEqual(minimum(8192),324404)
        self.assertEqual(minimum(16384),334234)
        self.assertGreater(minimum(16384),330000)
        self.assertEqual(minimum(393216),786432)
        self.assertIs(type(minimum(8192)),int)

    def test_reservation_output_bounds_are_strict_positive_integers(self):
        minimum = self.generation().minimum_reservation_micro_usd
        for value in (None,True,False,0,-1,1.0,8192.0,'8192',393217,2**31,[],{}):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaisesRegex(ValueError,'study_generation_budget_invalid'):
                    minimum(value)

    def test_reservation_context_envelope_is_explicit_and_bounded(self):
        minimum = self.generation().minimum_reservation_micro_usd
        self.assertEqual(minimum(8192,context_input_envelope=1000000),309831)
        self.assertEqual(minimum(8192,context_input_envelope=1048576),324404)
        for value in (None,True,False,0,-1,1.0,1048576.0,'1048576',1048577,2**31,[],{}):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaisesRegex(ValueError,'study_generation_budget_invalid'):
                    minimum(8192,context_input_envelope=value)

    def test_reservation_validator_refuses_underfunding_and_non_integer_values(self):
        module = self.generation()
        self.assertTrue(callable(getattr(module,'validate_reservation_micro_usd',None)),
            'reservation adequacy validator is missing')
        validate = module.validate_reservation_micro_usd
        self.assertEqual(validate(8192,330000),330000)
        self.assertEqual(validate(16384,340000),340000)
        self.assertEqual(validate(8192,324404),324404)
        for cap,reserve in ((16384,330000),(8192,324403),(8192,True),
                (8192,False),(8192,0),(8192,-1),(8192,330000.0),
                (8192,'330000'),(8192,None)):
            with self.subTest(cap=cap,reserve_type=type(reserve).__name__):
                with self.assertRaisesRegex(ValueError,'study_generation_reservation_invalid'):
                    validate(cap,reserve)

    def test_v2_reservation_tracks_the_calculated_minimum_without_changing_legacy(self):
        module = self.generation()
        # Simulate a larger approved generation envelope without adding a live profile.
        with patch.object(module,'minimum_reservation_micro_usd',return_value=334234):
            self.assertEqual(module.get_generation(module.V2_PROFILE)['reservation_micro_usd'],340000)
        self.assertEqual(module.get_generation()['reservation_micro_usd'],330000)

    def test_larger_cost_envelope_does_not_widen_legacy_usage_acceptance(self):
        module = self.generation()
        cap = module.get_generation(module.V2_PROFILE)['max_output_per_request']
        self.assertEqual(module.get_generation(module.V2_PROFILE)['context_input_envelope'],1048576)
        self.assertIsNotNone(_usage_evidence({'prompt_tokens':1000000,
            'completion_tokens':cap},cap))
        self.assertIsNone(_usage_evidence({'prompt_tokens':1000001,
            'completion_tokens':cap},cap))
        normalized = {'prompt_tokens':1000001,'completion_tokens':cap,
            'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':1000001,
            'cache_basis':'all_miss','reasoning_tokens':cap}
        self.assertIsNone(_normalized_charge(normalized,cap))


if __name__ == '__main__':
    import json
    from run_offline_tests import run_suite
    result = run_suite(unittest.defaultTestLoader.loadTestsFromTestCase(StudyGenerationTests))
    print(json.dumps(result))
    raise SystemExit(0 if result['status']=='passed' else 1)
