"""Offline boundary checks; synthetic responses do not measure model quality."""
import copy
import json
import unittest

from macr.evals.diagnostic_compare import OUTPUT_SCHEMA
from macr.evals.diagnostic_dataset import SYSTEM
from macr.evals.diagnostic_verification import (
    build_verification_request, run_pipeline, scope_sha256, validate_verification,
)
from macr.providers.base import ModelRequest, ModelResponse, ProviderError


class DiagnosticVerificationTests(unittest.TestCase):
    def setUp(self):
        self.ref = {'file': 'logic.py', 'start': 2, 'end': 2}
        self.scope = {'source': [{'file': 'logic.py', 'start': 1, 'end': 2,
                                 'body': 'def demo():\n    return 2\n'}],
                      'question': 'Does demo meet the requirement?', 'scenario': 'demo should return 2.',
                      'assumptions': ['Ordinary Python builtins.']}
        self.request = ModelRequest([{'role': 'system', 'content': SYSTEM},
                                     {'role': 'user', 'content': json.dumps(self.scope)}],
                                    response_schema=OUTPUT_SCHEMA, max_input_tokens=1536,
                                    max_output_tokens=384, metadata={'prompt_version': 'dev-v1'})
        self.candidate = {'verdict': 'no_defect_supported', 'cause': 'The literal return meets the requirement.',
                          'when': 'demo is called.', 'refs': [self.ref], 'unknowns': []}
        self.checked = {'checks': {
            'requirement': {'state': 'established', 'basis': 'The scenario requires return 2.', 'refs': []},
            'trigger': {'state': 'reachable', 'basis': 'Call demo().', 'refs': []},
            'behavior': {'state': 'satisfies', 'basis': 'The literal returned is 2.', 'refs': [self.ref]},
            'protection': {'state': 'absent', 'basis': 'No additional guard is needed.', 'refs': []},
            'counterevidence': {'state': 'refutes', 'basis': 'The literal matches the requirement.', 'refs': [self.ref]},
        }, 'assessment': 'no_defect_supported', 'unknowns': []}
        self.calls = []

    def invoke(self, response=None, **options):
        def call(request):
            self.calls.append(request)
            return response or ModelResponse(json.dumps(self.checked), usage_kind='measured',
                usage={'input_tokens': 120, 'output_tokens': 150}, latency_ms=7)
        return run_pipeline(self.request, json.dumps(self.candidate), call, **options)

    def test_accepted_final_is_compatible_candidate_and_not_semantic_proof(self):
        result = self.invoke()
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['final'], self.candidate)
        self.assertEqual(set(result['final']), set(OUTPUT_SCHEMA['required']))
        self.assertFalse(result['semantic_proof'])
        self.assertEqual(result['new_model_calls'], len(self.calls))
        self.assertEqual(result['new_model_calls'], 1)
        self.assertEqual(result['verification_attempts'], 1)
        self.assertEqual(json.loads(result['candidate_raw']), self.candidate)
        self.assertEqual(json.loads(result['verification_raw']), self.checked)
        self.assertEqual(result['verification_response']['usage']['output_tokens'], 150)

    def test_defect_and_unknown_are_legal_not_everything_abstained(self):
        self.candidate['verdict'] = self.checked['assessment'] = 'supported_defect'
        self.checked['checks']['behavior']['state'] = 'violates'
        self.checked['checks']['protection']['state'] = 'ineffective'
        self.checked['checks']['protection']['refs'] = [self.ref]
        self.checked['checks']['counterevidence']['state'] = 'does_not_refute'
        self.assertEqual(self.invoke()['status'], 'accepted')
        self.candidate.update(verdict='unknown', cause=None, when=None, refs=[], unknowns=['A dependency is missing.'])
        self.checked.update(assessment='unknown', unknowns=['A dependency is missing.'])
        self.checked['checks']['behavior'].update(state='unknown', refs=[])
        self.assertEqual(self.invoke()['status'], 'accepted')

    def test_explicit_violating_behavior_cannot_accept_healthy_candidate(self):
        self.checked['checks']['behavior']['state'] = 'violates'
        result = self.invoke()
        self.assertEqual(result['status'], 'validation_failed')
        self.assertEqual(result['error_code'], 'verification_cross_field_conflict')
        self.assertIsNone(result['final'])
        self.assertEqual(json.loads(result['candidate_raw'])['verdict'], 'no_defect_supported')

    def test_verifier_disagreement_never_flips_candidate(self):
        self.candidate['verdict'] = 'supported_defect'
        result = self.invoke()
        self.assertEqual(result['error_code'], 'verification_candidate_conflict')
        self.assertIsNone(result['final'])
        self.assertEqual(result['candidate']['verdict'], 'supported_defect')

    def test_defect_must_have_reachable_trigger_and_no_effective_guard_or_refutation(self):
        self.candidate['verdict'] = self.checked['assessment'] = 'supported_defect'
        self.checked['checks']['behavior']['state'] = 'violates'
        self.checked['checks']['counterevidence']['state'] = 'does_not_refute'
        for name, state in [('trigger', 'excluded'), ('protection', 'effective'), ('counterevidence', 'refutes')]:
            with self.subTest(name=name):
                modified = copy.deepcopy(self.checked)
                modified['checks'][name].update(state=state, refs=[self.ref])
                result = self.invoke(ModelResponse(json.dumps(modified)))
                self.assertEqual(result['error_code'], 'verification_cross_field_conflict')
                self.assertIsNone(result['final'])

    def test_unknown_requires_essential_gap_and_is_not_a_rejection_fallback(self):
        self.candidate.update(verdict='unknown', cause=None, when=None, refs=[], unknowns=['Missing evidence.'])
        self.checked.update(assessment='unknown', unknowns=['Missing evidence.'])
        self.assertEqual(self.invoke()['error_code'], 'verification_cross_field_conflict')
        self.checked['checks']['behavior']['state'] = 'unknown'
        self.checked['unknowns'] = []
        self.assertEqual(self.invoke()['error_code'], 'verification_cross_field_conflict')

    def test_invalid_candidate_is_rejected_before_dispatch(self):
        self.candidate['refs'][0]['end'] = 999
        result = self.invoke()
        self.assertEqual(result['new_model_calls'], 0)
        self.assertEqual(self.calls, [])
        self.assertEqual(result['error_code'], 'eval_citation_invalid')
        self.assertIsNone(result['final'])

    def test_definite_candidate_gaps_are_rejected_without_changing_historical_parser(self):
        self.candidate['unknowns'] = ['An essential condition is not established.']
        result = self.invoke()
        self.assertEqual(result['error_code'], 'verification_candidate_gaps')
        self.assertEqual(result['verification_attempts'], 0)
        self.assertEqual(result['new_model_calls'], 0)
        self.assertIsNone(result['final'])

    def test_every_verifier_reference_is_checked_even_if_unused(self):
        for ref in [self.ref | {'end': 999}, self.ref | {'file': 'other.py'},
                    self.ref | {'file': '../logic.py'}, self.ref | {'start': True},
                    self.ref | {'extra': 1}]:
            with self.subTest(ref=ref):
                modified = copy.deepcopy(self.checked)
                modified['checks']['requirement']['refs'] = [ref]
                result = self.invoke(ModelResponse(json.dumps(modified)))
                self.assertEqual(result['status'], 'validation_failed')
                self.assertIsNone(result['final'])

    def test_definite_behavior_must_be_cited_and_confidence_cannot_replace_checks(self):
        self.checked['checks']['behavior']['refs'] = []
        self.assertEqual(self.invoke()['error_code'], 'verification_citation_missing')
        self.checked['confidence'] = 1
        self.assertEqual(self.invoke()['error_code'], 'verification_contract_invalid')

    def test_provider_error_truncation_and_unexpected_callback_failure_stay_technical(self):
        def failure(request):
            raise ProviderError('local_timeout')
        result = run_pipeline(self.request, json.dumps(self.candidate), failure)
        self.assertEqual((result['status'], result['error_code']), ('technical_failure', 'local_timeout'))
        self.assertIsNone(result['new_model_calls'])
        self.assertEqual(result['verification_attempts'], 1)
        self.assertIsNone(result['final'])
        result = self.invoke(ModelResponse(json.dumps(self.checked), finish_reason='length'))
        self.assertEqual((result['status'], result['error_code']), ('technical_failure', 'model_output_incomplete'))
        self.assertEqual(json.loads(result['verification_raw']), self.checked)
        def unexpected(request):
            raise OSError('Private host value must not be persisted.')
        result = run_pipeline(self.request, json.dumps(self.candidate), unexpected)
        self.assertEqual(result['error_code'], 'verification_provider_failed')
        self.assertNotIn('Private host value', json.dumps(result))

    def test_invalid_verifier_json_is_validation_failure_without_retry(self):
        result = self.invoke(ModelResponse('not JSON'))
        self.assertEqual(result['status'], 'validation_failed')
        self.assertEqual(result['new_model_calls'], 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result['verification_raw'], 'not JSON')
        self.assertIsNone(result['final'])

    def test_observations_are_bound_host_context_and_cannot_leak_into_request(self):
        observations = {'scope_sha256': scope_sha256(self.request), 'origin': 'deterministic_check',
                        'checks': {'behavior': {'state': 'satisfies', 'basis': 'HOST_ONLY_SENTINEL', 'refs': [self.ref]}}}
        result = self.invoke(observations=observations)
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['observation_origin'], 'deterministic_check')
        self.assertNotIn('HOST_ONLY_SENTINEL', json.dumps(self.calls[0].messages))
        observations['checks']['behavior']['state'] = 'violates'
        result = self.invoke(observations=observations)
        self.assertEqual(result['error_code'], 'verification_observation_conflict')
        self.assertIsNone(result['final'])
        observations['scope_sha256'] = '0' * 64
        result = self.invoke(observations=observations)
        self.assertEqual(result['error_code'], 'verification_observation_binding_invalid')
        self.assertEqual(result['new_model_calls'], 0)

    def test_verification_builder_preserves_visible_scope_and_limits_without_dispatch(self):
        before = copy.deepcopy(self.request)
        request = build_verification_request(self.request, self.candidate, max_input_tokens=1664)
        self.assertEqual(json.loads(request.messages[-1]['content']), self.scope | {'candidate': self.candidate})
        self.assertEqual(request.max_output_tokens, 384)
        self.assertEqual(request.max_input_tokens, 1664)
        self.assertEqual(request.timeout_seconds, self.request.timeout_seconds)
        self.assertEqual(self.request, before)
        self.assertEqual(self.calls, [])

    def test_hidden_reference_fields_are_refused(self):
        self.scope['expected_verdict'] = 'no_defect_supported'
        self.request.messages[-1]['content'] = json.dumps(self.scope)
        result = self.invoke()
        self.assertEqual(result['error_code'], 'verification_input_invalid')
        self.assertEqual(result['new_model_calls'], 0)

    def test_basis_keywords_do_not_flip_or_certify_the_verdict(self):
        self.checked['checks']['behavior']['basis'] = 'BUG unknown defect healthy confidence 100%'
        self.candidate['cause'] = 'BUG unknown defect healthy confidence 100%'
        result = self.invoke()
        self.assertEqual(result['final']['verdict'], 'no_defect_supported')
        self.assertFalse(result['semantic_proof'])

    def test_direct_validator_does_not_invoke_a_model(self):
        result = validate_verification(json.dumps(self.checked), self.candidate, self.scope['source'])
        self.assertEqual(result, self.checked)
        self.assertEqual(self.calls, [])
        self.candidate['unknowns'] = ['Unresolved requirement.']
        with self.assertRaisesRegex(ProviderError, 'verification_candidate_gaps'):
            validate_verification(json.dumps(self.checked), self.candidate, self.scope['source'])


if __name__ == '__main__':
    unittest.main()
