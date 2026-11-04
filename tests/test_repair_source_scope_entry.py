"""Scoped real-entry tests with authored text; no target import or execution."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root, write_files
from repair_fixtures import SOURCES
from test_repair_entry import InterceptedTransport
from macr import cli
from macr.investigation import repair_entry as entry, repair_loop as loop
from macr.providers.repair_binding import RepairProviderBinding, development_config


class ScopedRepairEntryTests(unittest.TestCase):
    def setUp(self):
        self.root=fixture_root();self.files={p:t.encode() for p,t in SOURCES.items()}
        self.repo=write_files(self.root/'checkout',SOURCES)
        (self.repo/'README.md').write_text('An ordinary real checkout has more files.\n')
        (self.repo/'oracles').mkdir();(self.repo/'oracles/hidden.py').write_text('HIDDEN_NOT_READ=1\n')
        (self.repo/'vendor').mkdir()
        for n in range(90): (self.repo/'vendor'/f'unrelated{n}.py').write_text('raise RuntimeError("UNLISTED_NOT_READ")\n')
        self.task={'schema':'macr.repair.task.v3','source_scope':'declared_files','test_plan':None,
            'task_id':'scoped-development','issue':'Public authored invoice issue.',
            'source_files':loop._hashes(self.files),'editable_paths':['builder.py'],'test_paths':['checks.py']}
        self.task_path=self.root/'task.json';self.task_path.write_text(json.dumps(self.task))
        self.config=development_config();self.config_path=self.root/'model.json'
        self.config_path.write_text(json.dumps(self.config));self.out=self.root/'record'

    def invoke(self,arm='direct_api',*,verifier=None,before_capture=None):
        transport=InterceptedTransport([{'status':'no_candidate','summary':'Authored response, not a model result.','edits':[]}])
        provider=RepairProviderBinding(self.config,transport=transport)
        def build(config):
            if before_capture: before_capture()
            return provider
        command=str(Path(cli.__file__).resolve().parents[2]/'cli/agent_review.py')
        argv=[command,'repair','--repo',str(self.repo),'--task',str(self.task_path),
            '--model-config',str(self.config_path),'--out',str(self.out),
            '--strategy',arm,'--proposal-contract','v6']
        output=io.StringIO()
        with patch('sys.argv',argv),patch.object(entry,'_make_provider',side_effect=build), \
             patch.object(entry,'_verification_backend',return_value=verifier),redirect_stdout(output):
            with self.assertRaises(SystemExit) as result:runpy.run_path(command,run_name='__main__')
        return result.exception.code,json.loads(output.getvalue()),transport

    def test_v3_normal_checkout_does_not_force_unrelated_files_into_the_source_view(self):
        from macr.investigation import repair_sources as sources
        real_read=sources.read_safe;reads=[]
        def observe(root,path,limit):
            reads.append(path)
            return real_read(root,path,limit)
        with patch.object(sources,'read_safe',side_effect=observe):
            code,result,transport=self.invoke()
        self.assertEqual(code,0)
        self.assertEqual(reads,sorted(self.files)*2)
        self.assertEqual(result['status'],'no_candidate')
        request=json.loads(transport.requests[0]['messages'][1]['content'])
        self.assertEqual(request['files'],SOURCES)
        self.assertEqual(request['source_view']['coverage'],'partial_repository')
        self.assertEqual(request['source_view']['unlisted_files'],'not_read')
        self.assertEqual(result['source_view'],request['source_view'])
        self.assertNotIn('UNLISTED_NOT_READ',json.dumps(transport.requests))
        report=loop.verify_loop(self.out)
        self.assertFalse(report['target_code_executed']);self.assertFalse(report['repaired'])
        self.assertEqual(loop._read(self.out,'claim.json')['config']['source_scope'],'declared_files')
        self.assertEqual(set(loop._files(self.out/'sources/0')),set(self.files))

    def test_legacy_inventory_is_still_closed_and_v3_scope_is_explicit(self):
        for fields in (
                dict(self.task,schema='macr.repair.task.v3',source_scope='all_files'),
                {k:v for k,v in self.task.items() if k!='source_scope'},
                {k:v for k,v in self.task.items() if k!='test_plan'},
                {k:v for k,v in self.task.items() if k not in ('source_scope','test_plan')}|{'schema':'macr.repair.task.v1'}):
            self.task_path.write_text(json.dumps(fields))
            code,result,transport=self.invoke()
            self.assertEqual(code,2);self.assertEqual(len(transport.requests),0)
            self.assertFalse(self.out.exists())

    def test_changed_committed_source_is_rejected_again_at_capture_before_dispatch(self):
        code,result,transport=self.invoke(before_capture=lambda:(self.repo/'builder.py').write_text('changed=1\n'))
        self.assertEqual(code,2);self.assertEqual(len(transport.requests),0)
        self.assertFalse(self.out.exists());self.assertFalse(result['target_code_executed'])

    def test_v3_explicit_pytest_plan_and_partial_view_both_reach_the_request(self):
        self.task['test_plan']={'schema':'macr.repair.test-plan.v2','framework':'pytest',
            'nodeids':['checks.py::InvoiceChecks::test_no_discount']}
        self.task_path.write_text(json.dumps(self.task))
        code,result,transport=self.invoke()
        self.assertEqual(code,0)
        request=json.loads(transport.requests[0]['messages'][1]['content'])
        self.assertEqual(request['test_plan'],self.task['test_plan'])
        self.assertEqual(request['source_view']['coverage'],'partial_repository')
        report=loop.verify_loop(self.out)
        self.assertEqual(report['checks'][0]['spec']['argv_profile'],'pytest')
        self.assertEqual(report['checks'][0]['spec']['selectors'],self.task['test_plan']['nodeids'])
        self.assertFalse(result['target_code_executed'])

    def test_output_case_alias_is_rejected_before_writing_into_the_checkout(self):
        alias=self.root/'CHECKOUT';alias.mkdir(exist_ok=True)
        self.out=alias/'forbidden-record'
        code,result,transport=self.invoke()
        self.assertEqual(code,2);self.assertEqual(len(transport.requests),0)
        self.assertFalse(self.out.exists())

    def test_scoped_context_and_spend_are_distinct_from_unlabelled_complete_snapshot(self):
        legacy={k:v for k,v in self.task.items() if k not in ('source_scope','test_plan')}
        legacy['schema']='macr.repair.task.v1'
        self.assertNotEqual(entry.spend_binding_sha256(self.task,'direct_api',self.config,proposal_contract='v6'),
                            entry.spend_binding_sha256(legacy,'direct_api',self.config,proposal_contract='v6'))
        plain=entry.initial_context_id(self.files,self.task['issue'])
        scoped=entry.initial_context_id(self.files,self.task['issue'],source_scope='declared_files')
        self.assertNotEqual(plain,scoped)

    def test_all_three_arms_share_the_same_scoped_view_and_pending_handoff(self):
        from macr.evals import repair_comparison as comparison
        spec=json.loads((Path(__file__).resolve().parents[1]/'examples/repair_comparison_three_arm_development.json').read_text())
        spec['tasks']=[{'task_id':self.task['task_id'],'snapshot_sha256':loop.source_id(self.files),
            'issue_sha256':loop.digest(self.task['issue']),
            'visible_tests_sha256':loop.source_id({'checks.py':self.files['checks.py']}),
            'initial_context_sha256':entry.initial_context_id(self.files,self.task['issue'],source_scope='declared_files')}]
        spec['model']['configuration_sha256']=loop.digest(self.config)
        spec['model']['output_contract_sha256']=loop.digest(loop.output_contract('v6'))
        caps=self.config['limits'];spec['limits']={'max_requests':caps['max_requests'],
            'input_tokens':caps['max_total_input_tokens'],'output_tokens':caps['max_total_output_tokens'],'max_usd':caps['max_usd']}
        plan=comparison.compile_comparison(spec);views=[]
        names=loop.test_manifest(self.files,['checks.py'])
        for arm in ('direct_api','api_test_feedback','api_debugger'):
            self.out=self.root/arm
            verifier=loop.ReplayVerifier([{'source_id':loop.source_id(self.files),
                'execution_status':'completed','outcomes':dict.fromkeys(names,'failed')}])
            code,result,transport=self.invoke(arm,verifier=verifier)
            self.assertEqual(code,0)
            payload=json.loads(transport.requests[0]['messages'][1]['content'])
            views.append((payload['files'],payload['source_view']))
            cell=next(c for c in plan['cells'] if c['arm']==arm)
            packet=entry.score_handoff(self.out,plan,cell['cell_id'])
            self.assertEqual(packet['source_view'],payload['source_view'])
            self.assertIsNone(packet['result']['review'])
            self.assertEqual(packet['result']['requests'],1)
            unlabelled=deepcopy(spec)
            unlabelled['tasks'][0]['initial_context_sha256']=entry.initial_context_id(self.files,self.task['issue'])
            from macr.investigation.validation import ContractError
            with self.assertRaises(ContractError):
                entry.score_handoff(self.out,comparison.compile_comparison(unlabelled),cell['cell_id'])
        self.assertTrue(all(view==views[0] for view in views))

    def test_scoped_admission_does_not_turn_internal_record_into_an_open_inventory(self):
        from macr.investigation.validation import ContractError
        self.assertEqual(self.invoke()[0],0)
        (self.out/'sources/0/unlisted.py').write_text('raise RuntimeError("record must not execute")\n')
        report=loop._read(self.out,'report.json')
        report['artifact_hashes']=loop._file_hashes(self.out,skip=('report.json',))
        loop._write(self.out,'report.json',report)
        with self.assertRaises(ContractError):loop.verify_loop(self.out)

    def test_legacy_report_cannot_graft_an_unbound_partial_source_claim(self):
        from macr.investigation.validation import ContractError
        self.task={k:v for k,v in self.task.items() if k not in ('source_scope','test_plan')}
        self.task['schema']='macr.repair.task.v1';self.task_path.write_text(json.dumps(self.task))
        self.repo=write_files(self.root/'curated',SOURCES)
        self.assertEqual(self.invoke()[0],0)
        report=loop._read(self.out,'report.json')
        report['source_view']={'mode':'declared_files','coverage':'partial_repository',
            'unlisted_files':'not_read','dependency_completeness':'not_verified'}
        loop._write(self.out,'report.json',report)
        with self.assertRaises(ContractError):loop.verify_loop(self.out)
