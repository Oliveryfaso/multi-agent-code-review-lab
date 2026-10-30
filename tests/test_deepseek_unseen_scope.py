"""Authored input-seal fixtures; no actual R/U material or paid execution."""
import copy
import importlib
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from test_deepseek_study_profile import COMMITMENT, development_pack
from test_deepseek_comparison_pilot import prepare_regression, scripted
from macr.evals.deepseek_study_profile import digest

PROJECT=Path(__file__).resolve().parents[1]
V2='study-8192-v2'
CANDIDATE='study-evidence-v1'


class UnseenScopeTests(unittest.TestCase):
    def api(self):
        pilot=importlib.import_module('macr.evals.deepseek_comparison_pilot')
        for name in ('freeze_unseen_inputs','load_unseen_inputs','finalize_unseen_pilot'):
            self.assertTrue(callable(getattr(pilot,name,None)),
                f'the separate unseen-input API {name} is not implemented')
        return pilot

    def freeze(self,pilot):
        root=fixture_root();pack=development_pack(root,9)
        pack['pack_id']='authored-unseen-scope-contract-fixture-not-blind-data'
        (root/'pack.json').write_text(json.dumps(pack))
        out=root.parent/(root.name+'-scope')
        scope=pilot.freeze_unseen_inputs(root,custodian=COMMITMENT,out=out,
            project_root=PROJECT,seed=20261030)
        return root,out,scope

    def synthetic_live_receipt(self):
        # Test only the finalization writer boundary; this does not qualify real R.
        root=fixture_root()
        return root,{'control_root':str(root),'plan_sha256':'a'*64,
            'summary_sha256':'b'*64,'provenance':'approved_live','pack_sha256':'c'*64}

    def test_freeze_needs_no_regression_and_creates_only_a_bound_input_scope(self):
        pilot=self.api()
        with patch.object(pilot,'_regression_receipt',side_effect=AssertionError(
                'input freeze attempted to read a regression receipt')), patch(
                'macr.providers.deepseek._ApprovedTransport',side_effect=AssertionError(
                'input freeze constructed a live transport')):
            _,out,scope=self.freeze(pilot)
        self.assertEqual(scope['schema'],'macr.deepseek.pilot.unseen-scope.v1')
        self.assertEqual(scope['budget'],{'max_requests':54,'max_usd':'1.500000'})
        self.assertFalse(scope['remote_enabled'])
        self.assertEqual(scope['execution_prerequisite'],{
            'phase':'R','requires_real':True,'requires_eligible':True})
        self.assertNotIn('plan_sha256',scope)
        self.assertNotIn('regression_receipt',scope)
        self.assertEqual(len(scope['scope_sha256']),64)
        self.assertEqual(len(scope['cells']),18)
        first=[]
        for index in range(0,18,2):
            pair=scope['cells'][index:index+2]
            self.assertEqual(pair[0]['task_id'],pair[1]['task_id'])
            self.assertEqual({c['profile'] for c in pair},{V2,CANDIDATE})
            self.assertEqual({c['strategy'] for c in pair},{'adaptive_read_3'})
            first.append(pair[0]['profile'])
        self.assertEqual(sorted((first.count(V2),first.count(CANDIDATE))),[4,5])
        self.assertEqual(pilot.load_unseen_inputs(out),scope)
        for name in ('pilot.json','execution.claim.json','retention.json'):
            self.assertFalse((out/name).exists())

    def test_rehashed_budget_order_source_or_copied_path_is_not_a_new_authorized_scope(self):
        pilot=self.api()
        for mutation in ('budget','order','source','copy'):
            with self.subTest(mutation=mutation):
                root,out,scope=self.freeze(pilot)
                if mutation=='source':
                    (root/'snapshots/dev0/helper.py').write_text('def double(x):\n    return x * 99\n')
                elif mutation=='copy':
                    duplicate=out.parent/(out.name+'-copied')
                    shutil.copytree(out,duplicate);out=duplicate
                else:
                    value=copy.deepcopy(scope)
                    if mutation=='budget':value['budget']['max_usd']='2.000000'
                    else:value['cells']=list(reversed(value['cells']))
                    value['scope_sha256']=digest({k:v for k,v in value.items() if k!='scope_sha256'})
                    (out/'unseen-scope.json').write_text(json.dumps(value))
                with self.assertRaises((ValueError,OSError)):
                    pilot.load_unseen_inputs(out)
                self.assertFalse((out/'execution.claim.json').exists())

    def test_input_scope_is_not_an_execution_plan_and_cannot_reach_a_key(self):
        pilot=self.api();_,out,scope=self.freeze(pilot)
        approval={'authorized':True,'confirmation_ref':'synthetic-no-live-permission',
            'scope_sha256':scope['scope_sha256']}
        with self.assertRaises((ValueError,OSError)):
            pilot.preflight_approved_pilot(out,approval)
        with self.assertRaises((ValueError,OSError)):
            pilot.execute_approved_pilot(out,approval,enabled=True,
                key_reader=lambda:self.fail('an input scope reached a credential reader'))
        self.assertFalse((out/'execution.claim.json').exists())

    def test_qualified_fake_regression_cannot_finalize_an_unseen_execution_plan(self):
        pilot=self.api()
        _,regression,rplan=prepare_regression(pilot)
        summary=pilot.run_pilot_dryrun(regression,scripted_by_cell=scripted(rplan))
        self.assertTrue(summary['promotion_gate']['eligible'])
        self.assertNotEqual(summary['provenance'],'approved_live')
        _,out,_=self.freeze(pilot)
        with self.assertRaises((ValueError,OSError)):
            pilot.finalize_unseen_pilot(out,regression_control=regression)
        self.assertFalse((out/'pilot.json').exists())
        self.assertFalse((out/'execution.claim.json').exists())

    def test_finalization_writer_binds_the_sealed_scope_to_the_supplied_receipt(self):
        pilot=self.api();_,out,scope=self.freeze(pilot)
        regression,receipt=self.synthetic_live_receipt()
        with patch.object(pilot,'_regression_receipt',return_value=receipt), patch(
                'macr.providers.deepseek._ApprovedTransport',side_effect=AssertionError(
                'metadata finalization constructed a live transport')):
            plan=pilot.finalize_unseen_pilot(out,regression_control=regression)
            self.assertEqual(pilot.load_pilot(out),plan)
        self.assertEqual(plan['unseen_scope_sha256'],scope['scope_sha256'])
        self.assertEqual(plan['regression_receipt'],receipt)
        self.assertEqual(plan['phase'],'U')
        for key in ('cells','profiles','sources','budget','model','endpoint','cell_limits'):
            self.assertEqual(plan[key],scope[key])
        self.assertEqual(len(plan['plan_sha256']),64)
        self.assertTrue((out/'pilot.json').exists())
        self.assertFalse((out/'execution.claim.json').exists())
        self.assertFalse((out/'retention.json').exists())

    def test_second_finalization_does_not_replace_the_first_receipt_binding(self):
        pilot=self.api();_,out,_=self.freeze(pilot)
        regression,receipt=self.synthetic_live_receipt()
        with patch.object(pilot,'_regression_receipt',return_value=receipt):
            pilot.finalize_unseen_pilot(out,regression_control=regression)
            first=(out/'pilot.json').read_bytes()
            with self.assertRaises((ValueError,OSError)):
                pilot.finalize_unseen_pilot(out,regression_control=regression)
        self.assertEqual((out/'pilot.json').read_bytes(),first)

    def test_rehashed_plan_cannot_drop_or_replace_its_original_scope_binding(self):
        pilot=self.api()
        for mutation in ('drop','replace'):
            with self.subTest(mutation=mutation):
                _,out,_=self.freeze(pilot);regression,receipt=self.synthetic_live_receipt()
                with patch.object(pilot,'_regression_receipt',return_value=receipt):
                    plan=pilot.finalize_unseen_pilot(out,regression_control=regression)
                    if mutation=='drop':del plan['unseen_scope_sha256']
                    else:plan['unseen_scope_sha256']='0'*64
                    plan['plan_sha256']=digest({k:v for k,v in plan.items() if k!='plan_sha256'})
                    (out/'pilot.json').write_text(json.dumps(plan))
                    with self.assertRaises((ValueError,OSError)):
                        pilot.load_pilot(out)
                self.assertFalse((out/'execution.claim.json').exists())
