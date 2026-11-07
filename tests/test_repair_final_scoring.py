"""Authored final observations only. No staged source, tests or model are executed."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from macr import cli
from macr.evals import repair_comparison as comparison
from macr.evals.repair_final_scoring import prepare_final, import_final
import test_repair_scoring_stage as stage_tests


class FinalScoringTests(unittest.TestCase):
    def setUp(self):
        self.fixture = stage_tests.ScoringStageTests()
        self.fixture.setUp()
        self.root = self.fixture.root
        self.policy = {'schema':'macr.repair.final-policy.v1', 'tasks':[
            {'task_id':'development', 'kind':'defective', 'criteria_sha256':'a'*64,
             'hidden_test_plan':{'schema':'macr.repair.test-plan.v1', 'framework':'pytest',
                                 'nodeids':['checks.py::test_full_file']}}],
            'runner_sha256':'b'*64, 'environment_sha256':'c'*64, 'dependency_lock_sha256':'d'*64,
            'calibration_sha256':'e'*64, 'limits':{'cpu_seconds':10, 'memory_bytes':128*1024**2,
                'pids':16, 'wall_seconds':20, 'output_bytes':65536}}
        self.custodian = {'schema':'macr.repair.final-custodian.v1', 'custodian_id':'authored-fixture',
                          'protocol_sha256':'f'*64}
        spec = deepcopy(self.fixture.plan['spec'])
        spec['scoring'].update(rubric_sha256=comparison.result_digest(self.policy),
            custodian_commitment_sha256=comparison.result_digest(self.custodian))
        self.fixture.plan = comparison.compile_comparison(spec)
        self.fixture.plan_path.write_text(json.dumps(self.fixture.plan))
        self.policy_path = self.root/'policy.json'; self.policy_path.write_text(json.dumps(self.policy))
        self.custodian_path = self.root/'custodian.json'; self.custodian_path.write_text(json.dumps(self.custodian))
        self.record, self.transport = self.fixture.record()
        self.assertEqual(self.fixture.invoke(self.record)[0], 0)
        self.stage = self.root/'staged-direct_api'
        self.cell = next(cell for cell in self.fixture.plan['cells'] if cell['arm']=='direct_api')

    def request(self, **changes):
        args = dict(stage=self.stage, record=self.record, plan=self.fixture.plan,
            cell_id=self.cell['cell_id'], manifest=self.fixture.manifest, overlay_root=self.fixture.overlay,
            policy=self.policy, custodian=self.custodian)
        args.update(changes)
        return prepare_final(**args)

    def receipt(self, request):
        checks = []
        for check in request['checks']:
            outcome = 'failed' if check['variant']=='original' else 'passed'
            outcomes = dict.fromkeys(check['selectors'], outcome)
            checks.append({'check_id':check['check_id'], 'execution_status':'completed',
                'exit_code':1 if outcome=='failed' else 0,
                'test_counts':{outcome:len(outcomes),'executed':len(outcomes),'collected':len(outcomes)},
                'outcomes':outcomes, 'failure_kinds':dict.fromkeys(outcomes,'assertion') if outcome=='failed' else {},
                'limits_observed':{'cpu_ms':1,'memory_bytes':1024,'pids':1,'output_bytes':100},
                'output_truncated':False, 'cleanup_status':'closed', 'elapsed_ms':10})
        return {'schema':'macr.repair.final-receipt.v1', 'phase':'final',
            'request_sha256':request['request_sha256'], 'origin':'independent_custodian_reported',
            'execution':{field:request['policy'][field] for field in
                ('runner_sha256','environment_sha256','dependency_lock_sha256','calibration_sha256')},
            'checks':checks, 'preparation_ms':2, 'total_ms':2+10*len(checks)}

    def observe(self, request=None, receipt=None):
        request = request or self.request()
        return import_final(request, receipt or self.receipt(request), plan=self.fixture.plan)

    def test_complete_receipt_never_promotes_simulation_or_awards_repair(self):
        request = self.request(); before = deepcopy(request)
        final, rows, report = self.observe(request)
        self.assertEqual(request, before)
        self.assertTrue(final['original_hidden_assertion_failure'])
        self.assertTrue(final['candidate_all_required_tests_passed'])
        self.assertEqual(final['status'], 'pending_independent_review')
        self.assertEqual(rows[self.cell['cell_id']], request['generation_result'])
        self.assertEqual(rows[self.cell['cell_id']]['candidate_origin'], 'simulation')
        self.assertIsNone(rows[self.cell['cell_id']]['review'])
        self.assertFalse(final['scoring_credit_awarded'])
        self.assertFalse(final['provenance_authenticated'])
        self.assertFalse(final['automatic_retry_allowed'])
        self.assertEqual(report['planned_cells'],3)
        self.assertEqual(report['arms']['direct_api']['scored'],0)
        self.assertEqual(report['arms']['direct_api']['outcomes']['full_repair'],0)
        self.assertEqual(report['arms']['direct_api']['known_usd'],rows[self.cell['cell_id']]['known_usd'])
        self.assertEqual(report['final_scoring'][self.cell['cell_id']]['total_ms'],42)

    def test_final_request_separately_rebinds_public_and_hidden_with_selected_candidate(self):
        request = self.request()
        checks = {(check['suite'],check['variant']):check for check in request['checks']}
        self.assertEqual(len(checks),4)
        for variant in ('original','candidate'):
            self.assertNotEqual(checks['public',variant]['tree_sha256'],checks['hidden',variant]['tree_sha256'])
            self.assertEqual(checks['hidden',variant]['selectors'],['checks.py::test_full_file'])
        self.assertNotIn('SCORER_ONLY_NEVER_IMPORT',json.dumps(request))
        self.assertNotIn('test_full_file',json.dumps(self.transport.requests))
        record, _ = self.fixture.record('api_test_feedback',select_earlier=True)
        self.assertEqual(self.fixture.invoke(record,arm='api_test_feedback')[0],0)
        cell = next(c for c in self.fixture.plan['cells'] if c['arm']=='api_test_feedback')
        selected = self.request(stage=self.root/'staged-api_test_feedback',record=record,cell_id=cell['cell_id'])
        packet = json.loads((self.root/'staged-api_test_feedback/packet.json').read_text())
        self.assertNotEqual(packet['last_applied_source_id'],selected['candidate_source_id'])
        self.assertEqual(packet['result']['candidate_sha256'],selected['candidate_source_id'])

    def test_policy_custodian_environment_and_selector_cannot_be_retrofitted(self):
        for name, value in [('runner_sha256','1'*64),('calibration_sha256','2'*64),
                            ('environment_sha256','3'*64),('dependency_lock_sha256','4'*64)]:
            policy = deepcopy(self.policy); policy[name] = value
            with self.subTest(name=name),self.assertRaises(ValueError):self.request(policy=policy)
        policy = deepcopy(self.policy); policy['tasks'][0]['hidden_test_plan']['nodeids']=['other.py::test_hidden']
        with self.assertRaises(ValueError):self.request(policy=policy)
        custodian = deepcopy(self.custodian);custodian['custodian_id']='changed'
        with self.assertRaises(ValueError):self.request(custodian=custodian)
        policy = deepcopy(self.policy);policy['tasks'][0]['hidden_test_plan']['nodeids']=['missing.py::test_hidden']
        spec=deepcopy(self.fixture.plan['spec']);spec['scoring']['rubric_sha256']=comparison.result_digest(policy)
        with self.assertRaises(ValueError):self.request(policy=policy,plan=comparison.compile_comparison(spec))

    def test_stage_extra_files_changed_bytes_links_and_permissions_reject(self):
        paths = [self.stage/'candidate/conftest.py',self.stage/'unexpected.json']
        for path in paths:
            path.write_text('raise RuntimeError("NEVER_EXECUTE")')
            with self.subTest(path=path.name),self.assertRaises(ValueError):self.request()
            path.unlink()
        path=self.stage/'candidate/builder.py';body=path.read_bytes()
        path.write_bytes(body+b'\n# changed')
        with self.assertRaises(ValueError):self.request()
        path.write_bytes(body)
        path.chmod(0o644)
        with self.assertRaises(ValueError):self.request()
        path.chmod(0o600)
        other=self.root/'other.py';other.write_bytes(body)
        path.unlink();path.symlink_to(other)
        with self.assertRaises((OSError,ValueError)):self.request()
        path.unlink();os.link(other,path);path.chmod(0o600)
        with self.assertRaises(ValueError):self.request()

    def test_changed_staging_even_with_new_local_digest_and_swapped_cell_reject(self):
        path=self.stage/'staging.json';original=path.read_bytes()
        staging=json.loads(original);staging['candidate_origin']='model'
        staging['staging_sha256']=comparison.result_digest({k:v for k,v in staging.items() if k!='staging_sha256'})
        path.write_text(json.dumps(staging))
        with self.assertRaises(ValueError):self.request()
        path.write_bytes(original)
        other=next(c for c in self.fixture.plan['cells'] if c['arm']=='api_debugger')
        with self.assertRaises(ValueError):self.request(cell_id=other['cell_id'])

    def test_public_receipt_wrong_binding_duplicate_reordered_and_extra_gold_reject(self):
        request=self.request();base=self.receipt(request)
        mutations=[]
        for key,value in [('phase','public'),('schema','macr.repair.public-check-receipt.v1'),
                ('request_sha256','0'*64),('origin','isolated_execution'),('review',{'status':'locked'})]:
            bad=deepcopy(base);bad[key]=value;mutations.append(bad)
        bad=deepcopy(base);bad['checks'][1]=deepcopy(bad['checks'][0]);mutations.append(bad)
        bad=deepcopy(base);bad['checks'].reverse();mutations.append(bad)
        bad=deepcopy(base);bad['checks'][0]['outcomes']['unlisted.py::test_gold']='passed';mutations.append(bad)
        bad=deepcopy(base);bad['checks'][0]['test_counts']['failed']+=1;mutations.append(bad)
        bad=deepcopy(base);bad['checks'][0]['exit_code']=0;mutations.append(bad)
        bad=deepcopy(base);bad['total_ms']=0;mutations.append(bad)
        for index,bad in enumerate(mutations):
            with self.subTest(index=index),self.assertRaises(ValueError):self.observe(request,bad)

    def test_zero_skip_xfail_exception_collection_and_truncation_never_establish_pass(self):
        request=self.request()
        for category in ('skipped','xfailed','xpassed','error','zero','collection','truncated'):
            receipt=self.receipt(request);raw=receipt['checks'][-1];names=list(raw['outcomes'])
            if category in ('zero','collection'):
                raw.update(outcomes={},failure_kinds={},exit_code=5,
                    test_counts={'executed':0,'collected':0,'collection_errors':int(category=='collection')})
            elif category=='truncated':raw['output_truncated']=True
            else:
                raw['outcomes']=dict.fromkeys(names,category)
                raw['test_counts']={('errors' if category=='error' else category):len(names),
                    'executed':0 if category=='skipped' else len(names),'collected':len(names)}
                raw['exit_code']=1 if category in ('xpassed','error') else 0
            final,rows,report=self.observe(request,receipt)
            with self.subTest(category=category):
                self.assertEqual(final['status'],'final_verification_not_established')
                self.assertFalse(final['candidate_all_required_tests_passed'])
                self.assertEqual(report['planned_cells'],3)
                self.assertEqual(rows[self.cell['cell_id']],request['generation_result'])

    def test_direct_api_rejects_rehashed_empty_missing_duplicate_or_unbound_checks(self):
        request=self.request()
        variants=[]
        bad=deepcopy(request)
        for check in bad['checks']:
            check['selectors']=[]
        variants.append(bad)
        bad=deepcopy(request);bad['checks']=[];variants.append(bad)
        bad=deepcopy(request);bad['checks'][1]=deepcopy(bad['checks'][0]);variants.append(bad)
        bad=deepcopy(request);bad['checks'][1]['selectors']=['checks.py::test_other'];variants.append(bad)
        bad=deepcopy(request);bad['checks'][0]['check_id']='0'*64;variants.append(bad)
        bad=deepcopy(request);bad['task_kind']='healthy';variants.append(bad)
        bad=deepcopy(request);bad['execution_authorized']=True;variants.append(bad)
        for index,bad in enumerate(variants):
            bad['request_sha256']=comparison.result_digest({k:v for k,v in bad.items() if k!='request_sha256'})
            receipt=self.receipt(bad)
            with self.subTest(index=index),self.assertRaises(ValueError):self.observe(bad,receipt)

    def test_missing_model_identity_stop_is_shared_by_final_and_report(self):
        request=self.request();request['generation_result']['model_identity_sha256']=None
        request['request_sha256']=comparison.result_digest({k:v for k,v in request.items() if k!='request_sha256'})
        final,_,report=self.observe(request)
        self.assertTrue(report['must_stop'])
        self.assertTrue(final['must_stop'])
        self.assertFalse(final['final_verification_must_stop'])
        self.assertTrue(report['final_scoring'][self.cell['cell_id']]['must_stop'])

    def test_resource_overruns_and_unknown_measurements_are_not_a_test_pass(self):
        request=self.request()
        for field,value in [('cpu_ms',10001),('memory_bytes',128*1024**2+1),('pids',17),('output_bytes',65537)]:
            receipt=self.receipt(request);receipt['checks'][-1]['limits_observed'][field]=value
            final,_,_=self.observe(request,receipt)
            with self.subTest(field=field):self.assertFalse(final['candidate_all_required_tests_passed'])
        receipt=self.receipt(request);receipt['checks'][-1]['elapsed_ms']=20001;receipt['total_ms']=30000
        self.assertFalse(self.observe(request,receipt)[0]['candidate_all_required_tests_passed'])
        receipt=self.receipt(request);receipt['checks'][-1]['limits_observed']=None
        self.assertFalse(self.observe(request,receipt)[0]['candidate_all_required_tests_passed'])
        receipt=self.receipt(request);receipt['execution']['environment_sha256']='0'*64
        with self.assertRaises(ValueError):self.observe(request,receipt)

    def test_baseline_pass_or_exception_is_not_an_assertion_reproduction(self):
        request=self.request()
        for kind in ('passed','exception'):
            receipt=self.receipt(request);raw=receipt['checks'][1]
            if kind=='passed':
                raw['outcomes']=dict.fromkeys(raw['outcomes'],'passed');raw['failure_kinds']={}
                count=len(raw['outcomes']);raw['test_counts']={'passed':count,'executed':count,'collected':count}
                raw['exit_code']=0
            else:raw['failure_kinds']=dict.fromkeys(raw['outcomes'],'exception')
            final,_,_=self.observe(request,receipt)
            self.assertFalse(final['original_hidden_assertion_failure'])
            self.assertFalse(final['scoring_credit_awarded'])

    def test_timeout_unknown_and_cleanup_preserve_generation_and_halt_uncertainty(self):
        request=self.request()
        for state,cleanup in [('timeout','closed'),('outcome_unknown','closed'),('completed','outcome_unknown'),
                              ('blocked','closed'),('error','closed')]:
            receipt=self.receipt(request);raw=receipt['checks'][-1]
            raw.update(execution_status=state,cleanup_status=cleanup)
            if state!='completed':raw.update(outcomes={},failure_kinds={},test_counts=None,exit_code=None)
            final,rows,report=self.observe(request,receipt)
            with self.subTest(state=state,cleanup=cleanup):
                self.assertEqual(final['must_stop'],state=='outcome_unknown' or cleanup!='closed')
                self.assertEqual(report['must_stop'],final['must_stop'])
                self.assertEqual(rows[self.cell['cell_id']],request['generation_result'])
                self.assertFalse(final['scoring_credit_awarded'])
                self.assertEqual(report['arms']['direct_api']['scored'],0)

    def test_no_candidate_remains_missing(self):
        record,_=self.fixture.record('api_debugger',candidate=False)
        self.assertEqual(self.fixture.invoke(record,arm='api_debugger')[0],0)
        cell=next(c for c in self.fixture.plan['cells'] if c['arm']=='api_debugger')
        request=self.request(stage=self.root/'staged-api_debugger',record=record,cell_id=cell['cell_id'])
        self.assertEqual(len(request['checks']),2)
        self.assertIsNone(request['candidate_source_id'])
        final,_,report=self.observe(request)
        self.assertFalse(final['candidate_all_required_tests_passed'])
        self.assertEqual(report['arms']['api_debugger']['scored'],0)

    def invoke(self, operation, out, *, receipt=None):
        argv=['agent-review','repair-comparison',operation,'--plan',str(self.fixture.plan_path),
            '--stage',str(self.stage),'--record',str(self.record),'--cell',self.cell['cell_id'],
            '--overlay-manifest',str(self.fixture.manifest_path),'--overlay-root',str(self.fixture.overlay),
            '--policy',str(self.policy_path),'--custodian',str(self.custodian_path),'--out',str(out)]
        if receipt is not None:argv+=['--receipt',str(receipt)]
        output=io.StringIO()
        with patch.object(sys,'argv',argv),redirect_stdout(output):
            try:cli.main()
            except SystemExit as error:code=error.code
            else:code=0
        return code,json.loads(output.getvalue())

    def test_normal_cli_prepares_and_imports_without_dispatch_or_hidden_output(self):
        request=self.request();receipt_path=self.root/'receipt.json'
        receipt_path.write_text(json.dumps(self.receipt(request)))
        prepared=self.root/'prepared';out=self.root/'imported'
        with patch('subprocess.Popen',side_effect=AssertionError('no dispatch')):
            self.assertEqual(self.invoke('prepare-final',prepared)[0],0)
            code,summary=self.invoke('import-final',out,receipt=receipt_path)
        self.assertEqual(code,0);self.assertFalse(summary['scoring_credit_awarded'])
        self.assertFalse(summary['target_code_executed'])
        self.assertNotIn('test_full_file',json.dumps(summary))
        self.assertNotIn('SCORER_ONLY',json.dumps(summary))
        self.assertEqual(json.loads((prepared/'request.json').read_text()),request)
        self.assertEqual(json.loads((out/'results.json').read_text())[self.cell['cell_id']],request['generation_result'])
        self.assertEqual(self.invoke('import-final',out,receipt=receipt_path)[0],2)
        self.assertEqual(self.invoke('prepare-final',self.stage/'nested')[0],2)
        receipt_path.write_text(json.dumps({'phase':'public'}))
        bad=self.root/'bad-import'
        self.assertEqual(self.invoke('import-final',bad,receipt=receipt_path)[0],2)
        self.assertFalse(bad.exists())


if __name__ == '__main__':unittest.main()
