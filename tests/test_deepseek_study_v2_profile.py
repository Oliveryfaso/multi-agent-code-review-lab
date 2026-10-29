"""Known development fixtures for the explicitly selected offline generation profile."""
import inspect
from pathlib import Path
import unittest

from debugger_fixtures import fixture_root
from test_deepseek_study_profile import development_pack, COMMITMENT
from macr.evals.deepseek_study_profile import prepare_study, load_study, digest, StudyError
from macr.evals.deepseek_policy_runner import _write_exclusive
from macr.evals.deepseek_study_report import build_study_report

PROFILE='study-8192-v2'
PROMPT='deepseek-study-8192-v2'
PROJECT=Path(__file__).resolve().parents[1]


class StudyV2ProfileTests(unittest.TestCase):
    def prepare(self, profile=PROFILE, count=1):
        self.assertIn('execution_profile',inspect.signature(prepare_study).parameters,
            'explicit offline execution profile is not available')
        root=fixture_root();development_pack(root,count=count)
        out=root.parent/(root.name+'-control')
        study=prepare_study(root,custodian=COMMITMENT,out=out,
            project_root=PROJECT,execution_profile=profile)
        return root,out,study

    def test_explicit_v2_freezes_cap_reservation_and_engineering_assumption(self):
        _,out,study=self.prepare()
        self.assertEqual(study['execution_profile'],PROFILE)
        self.assertEqual(study['prompt_version'],PROMPT)
        self.assertEqual(study['cell_limits']['max_output_per_request'],8192)
        self.assertEqual(study['cell_limits']['max_total_output_tokens'],24576)
        self.assertEqual(study['cell_limits']['planned_input_per_request'],8000)
        self.assertEqual(study['cell_limits']['max_total_input_tokens'],24000)
        price=study['price_snapshot']
        self.assertEqual(price['reservation_micro_usd'],330000)
        self.assertEqual(price['minimum_reservation_micro_usd'],324404)
        self.assertEqual(price['contract_input_envelope'],1048576)
        self.assertFalse(price['context_exact_provider_limit_verified'])
        self.assertFalse(study['remote_enabled'])
        self.assertEqual(study['tasks'][0]['manifest']['budget']['max_total_output_tokens'],24576)
        self.assertEqual(study['tasks'][0]['manifest']['prompt_version'],PROMPT)
        self.assertEqual(load_study(out),study)

    def test_default_study_keeps_the_original_contract(self):
        _,_,study=self.prepare(profile=None)
        self.assertNotIn('execution_profile',study)
        self.assertEqual(study['prompt_version'],'deepseek-policy-contracts-v3')
        self.assertEqual(study['cell_limits']['max_output_per_request'],2000)
        self.assertEqual(study['cell_limits']['max_total_output_tokens'],6000)
        self.assertEqual(study['price_snapshot']['context_tokens'],1000000)
        self.assertNotIn('minimum_reservation_micro_usd',study['price_snapshot'])

    def test_unknown_profile_is_rejected_before_output_creation(self):
        self.assertIn('execution_profile',inspect.signature(prepare_study).parameters)
        for profile in (False,'',{},'unapproved-16384'):
            root=fixture_root();development_pack(root,count=1)
            out=root.parent/(root.name+'-control')
            with self.assertRaises(ValueError):
                prepare_study(root,custodian=COMMITMENT,out=out,
                    project_root=PROJECT,execution_profile=profile)
            self.assertFalse(out.exists())

    def test_rehashed_frozen_contract_drift_is_not_accepted(self):
        for field in ('profile','output','reserve','envelope'):
            _,out,study=self.prepare()
            if field=='profile':study['execution_profile']='unapproved-16384'
            elif field=='output':study['cell_limits']['max_output_per_request']=16384
            elif field=='reserve':study['price_snapshot']['reservation_micro_usd']=1
            else:study['price_snapshot']['contract_input_envelope']=8000
            study['profile_sha256']=digest({key:value for key,value in study.items() if key!='profile_sha256'})
            (out/'study.json').unlink()
            _write_exclusive(out/'study.json',study,262144)
            with self.assertRaises(ValueError):load_study(out)

    def test_v2_report_caps_are_real_and_live_entry_refuses_before_claim(self):
        _,out,study=self.prepare(count=9)
        report=build_study_report(study,{})
        self.assertEqual(report['caps']['max_output_tokens_per_request'],8192)
        self.assertEqual(report['caps']['max_output_tokens_per_cell'],24576)
        self.assertEqual(report['planned_cells'],36)
        from macr.evals.deepseek_study_live import propose_study_live_approval
        with self.assertRaisesRegex(StudyError,'study_profile_live_disabled'):
            propose_study_live_approval(out)
        self.assertFalse((out/'execution.claim.json').exists())

    def test_rehashed_v2_cannot_drop_its_profile_tag_to_enter_legacy_live(self):
        _,out,study=self.prepare(count=9)
        del study['execution_profile']
        study['profile_sha256']=digest({key:value for key,value in study.items() if key!='profile_sha256'})
        (out/'study.json').unlink();_write_exclusive(out/'study.json',study,262144)
        from macr.evals.deepseek_study_live import propose_study_live_approval
        with self.assertRaises(ValueError):propose_study_live_approval(out)
        self.assertFalse((out/'execution.claim.json').exists())
