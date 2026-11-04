"""Handwritten pytest-shaped text; no pytest import or target execution."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root, write_files
from repair_fixtures import SOURCES, OLD, NEW
from test_repair_entry import InterceptedTransport
from macr import cli
from macr.investigation import repair_entry as entry, repair_loop as loop
from macr.investigation.validation import ContractError
from macr.providers.repair_binding import RepairProviderBinding, development_config


CHECKS='''import pytest
raise RuntimeError("test text must never execute")
@pytest.fixture
def order():
    return (1000, 2, 25, 500)
def test_delivery(order):
    assert len(order) == 4
class TestInvoice:
    def test_total(self, order):
        assert order[0] == 1000
'''


class PytestRepairEntryTests(unittest.TestCase):
    def setUp(self):
        self.root=fixture_root()
        self.files={p:text.encode() for p,text in SOURCES.items()}
        self.files['checks.py']=CHECKS.encode()
        self.repo=write_files(self.root/'source',{p:b.decode() for p,b in self.files.items()})
        self.plan={'schema':'macr.repair.test-plan.v1','framework':'pytest',
            'nodeids':['checks.py::test_delivery','checks.py::TestInvoice::test_total']}
        self.task={'schema':'macr.repair.task.v2','task_id':'handwritten-pytest',
            'issue':'Discount excludes delivery.', 'source_files':loop._hashes(self.files),
            'editable_paths':['app.py','builder.py','records.py'],
            'test_paths':['checks.py'],'test_plan':deepcopy(self.plan)}
        self.task_file=self.root/'task.json';self.task_file.write_text(json.dumps(self.task))
        self.config=development_config();self.config_file=self.root/'config.json'
        self.config_file.write_text(json.dumps(self.config));self.out=self.root/'run'

    def invoke(self,strategy='api_debugger',replies=(),verifier=None):
        transport=InterceptedTransport(list(replies))
        provider=RepairProviderBinding(self.config,transport=transport)
        args=[str(Path(cli.__file__).resolve().parents[2]/'cli/agent_review.py'),'repair',
            '--repo',str(self.repo),'--task',str(self.task_file),'--model-config',str(self.config_file),
            '--out',str(self.out),'--strategy',strategy,'--proposal-contract','v6']
        output=io.StringIO()
        with patch('sys.argv',args),patch.object(entry,'_make_provider',return_value=provider), \
             patch.object(entry,'_verification_backend',return_value=verifier),redirect_stdout(output):
            with self.assertRaises(SystemExit) as done:
                runpy.run_path(args[0],run_name='__main__')
        return done.exception.code,json.loads(output.getvalue()),transport

    def observation(self,files,*,failed=False):
        return {'source_id':loop.source_id(files),'execution_status':'completed',
            'outcomes':{node:'failed' if failed else 'passed' for node in self.plan['nodeids']}}

    def test_normal_entry_uses_exact_pytest_spec_and_still_stops_without_backend(self):
        self.assertEqual(entry.load_task(self.repo,self.task_file),self.task)
        code,result,transport=self.invoke()
        self.assertEqual((code,result['status'],len(transport.requests)),(2,'verification_unavailable',0))
        report=loop.verify_loop(self.out)
        self.assertEqual(report['checks'][0]['spec']['argv_profile'],'pytest')
        self.assertEqual(report['checks'][0]['spec']['selectors'],self.plan['nodeids'])
        self.assertFalse(report['target_code_executed']);self.assertFalse(report['repaired'])
        self.assertEqual((self.repo/'checks.py').read_bytes(),CHECKS.encode())

    def test_v2_declared_names_route_through_normal_entry_without_fabricated_collection(self):
        body=CHECKS.replace('class TestInvoice:', 'class InvoiceCase:').replace('def test_total(', 'def testTotal(')
        self.files['checks.py']=body.encode();(self.repo/'checks.py').write_bytes(self.files['checks.py'])
        self.plan={'schema':'macr.repair.test-plan.v2','framework':'pytest',
                   'nodeids':['checks.py::InvoiceCase::testTotal']}
        self.task.update(source_files=loop._hashes(self.files),test_plan=self.plan)
        self.task_file.write_text(json.dumps(self.task))
        self.assertEqual(entry.load_task(self.repo,self.task_file),self.task)
        code,result,transport=self.invoke()
        self.assertEqual((code,result['status'],len(transport.requests)),(2,'verification_unavailable',0))
        report=loop.verify_loop(self.out)
        self.assertEqual(report['checks'][0]['spec']['selectors'],self.plan['nodeids'])
        self.assertFalse(report['target_code_executed']);self.assertFalse(report['repaired'])
        self.out=self.root/'v2-direct-record'
        code,result,transport=self.invoke('direct_api',replies=[
            {'status':'no_candidate','summary':'Authored abstention, not a model result.','edits':[]}])
        self.assertEqual(code,0)
        payload=json.loads(transport.requests[0]['messages'][1]['content'])
        self.assertEqual(payload['test_plan'],self.plan)
        self.assertEqual(payload['files']['checks.py'],body)
        self.assertEqual(loop._read(self.out,'claim.json')['config']['test_plan'],self.plan)
        self.assertFalse(result['repaired']);self.assertFalse(result['target_code_executed'])

    def test_all_three_arms_share_full_tests_and_plan_with_unchanged_budgets(self):
        contexts=[];results={}
        fixed=dict(self.files);fixed['builder.py']=fixed['builder.py'].replace(OLD.encode(),NEW.encode())
        candidate={'status':'candidate','summary':'Authored edit, not model evidence.',
                   'edits':[{'file':'builder.py','old':OLD,'new':NEW}]}
        finish={'status':'finalize_current','summary':'Retain without another check.','edits':[]}
        for strategy in ('direct_api','api_test_feedback','api_debugger'):
            self.out=self.root/strategy
            verifier=loop.ReplayVerifier([self.observation(self.files,failed=True),self.observation(fixed)])
            code,result,transport=self.invoke(strategy,[candidate,finish],verifier)
            self.assertEqual(code,0)
            request=json.loads(transport.requests[0]['messages'][1]['content'])
            self.assertEqual(request['files']['checks.py'],CHECKS)
            self.assertEqual(request['test_plan'],self.plan)
            contexts.append({key:request[key] for key in ('files','test_plan')})
            report=loop.verify_loop(self.out)
            self.assertFalse(report['repaired']);self.assertFalse(report['target_code_executed'])
            self.assertEqual(loop._read(self.out,'claim.json')['config']['max_rounds'],3)
            self.assertEqual((self.repo/'checks.py').read_bytes(),CHECKS.encode())
            results[strategy]=report['provider_requests']
        self.assertTrue(all(value==contexts[0] for value in contexts))
        self.assertEqual(results,{'direct_api':2,'api_test_feedback':2,'api_debugger':1})

    def test_plan_is_bound_in_paid_scope_and_common_initial_context(self):
        first=entry.spend_binding_sha256(self.task,'direct_api',self.config,proposal_contract='v6')
        versioned=deepcopy(self.task);versioned['test_plan']['schema']='macr.repair.test-plan.v2'
        self.assertNotEqual(first,entry.spend_binding_sha256(versioned,'direct_api',self.config,proposal_contract='v6'))
        self.assertNotEqual(entry.initial_context_id(self.files,self.task['issue'],test_plan=self.plan),
            entry.initial_context_id(self.files,self.task['issue'],test_plan=versioned['test_plan']))
        changed=deepcopy(self.task);changed['test_plan']['nodeids'].reverse()
        self.assertNotEqual(first,entry.spend_binding_sha256(changed,'direct_api',self.config,proposal_contract='v6'))
        self.assertNotEqual(entry.initial_context_id(self.files,self.task['issue'],test_plan=self.plan),
                            entry.initial_context_id(self.files,self.task['issue']))

    def test_unknown_node_and_large_whole_file_are_rejected_without_truncation(self):
        for number in range(2):
            self.out=self.root/('invalid-'+str(number))
            if number==0:self.task['test_plan']['nodeids']=['checks.py::test_missing']
            else:
                self.task['test_plan']=self.plan
                body=b'#'+b'x'*16384+b'\n'+CHECKS.encode()
                (self.repo/'checks.py').write_bytes(body)
                self.task['source_files']['checks.py']=loop._hashes({'checks.py':body})['checks.py']
            self.task_file.write_text(json.dumps(self.task))
            code,result,transport=self.invoke()
            self.assertEqual((code,result['status'],len(transport.requests)),(2,'rejected',0))
            self.assertFalse(self.out.exists())
        self.assertEqual((self.repo/'checks.py').read_bytes(),body)

    def test_saved_check_cannot_be_relabelled_as_unittest_even_after_rehashing(self):
        _,_,_=self.invoke()
        report=loop.verify_loop(self.out)
        saved=loop._read(self.out,'checks/0/receipt.json')
        saved['spec']['argv_profile']='unittest';report['checks'][0]=saved
        loop._write(self.out,'checks/0/receipt.json',saved)
        report['artifact_hashes']=loop._file_hashes(self.out,skip=('report.json',))
        loop._write(self.out,'report.json',report)
        with self.assertRaises(ContractError):loop.verify_loop(self.out)

    def test_task_v2_requires_a_valid_plan_before_paid_scope_or_source_admission(self):
        for value in (None,{},[],{'schema':'macr.repair.test-plan.v1','framework':'unittest','nodeids':[]}):
            with self.subTest(plan=value):
                invalid=deepcopy(self.task);invalid['test_plan']=value
                self.task_file.write_text(json.dumps(invalid))
                with self.assertRaises(ContractError):entry.load_task(self.repo,self.task_file)
                with self.assertRaises(ContractError):
                    entry.spend_binding_sha256(invalid,'direct_api',self.config,proposal_contract='v6')
        self.assertFalse(self.out.exists())

    def test_selected_candidate_handoff_retains_plan_and_rejects_legacy_context(self):
        from macr.evals.repair_comparison import compile_comparison
        fixed=dict(self.files);fixed['builder.py']=fixed['builder.py'].replace(OLD.encode(),NEW.encode())
        candidate={'status':'candidate','summary':'Authored fixture change.',
                   'edits':[{'file':'builder.py','old':OLD,'new':NEW}]}
        selected={'status':'select_candidate','summary':'Select without a fresh run.',
                  'selected_content_id':loop.source_id(fixed),'edits':[]}
        code,result,_=self.invoke('api_test_feedback',[candidate,selected],loop.ReplayVerifier([
            self.observation(self.files,failed=True),self.observation(fixed)]))
        self.assertEqual((code,result['status']),(0,'candidate_selected_unverified'))
        project=Path(cli.__file__).resolve().parents[2]
        spec=json.loads((project/'examples/repair_comparison_development.json').read_text())
        spec.update(schema='macr.repair-comparison.spec.v2',contrast='feedback_and_public_pass_stop_policy')
        spec['tasks']=[{'task_id':self.task['task_id'],'snapshot_sha256':loop.source_id(self.files),
            'issue_sha256':loop.digest(self.task['issue']),
            'visible_tests_sha256':loop.source_id({'checks.py':self.files['checks.py']}),
            'initial_context_sha256':entry.initial_context_id(self.files,self.task['issue'],test_plan=self.plan)}]
        spec['model']['configuration_sha256']=loop.digest(self.config)
        spec['model']['output_contract_sha256']=loop.digest(loop.output_contract('v6'))
        limits=self.config['limits'];spec['limits']={'max_requests':limits['max_requests'],
            'input_tokens':limits['max_total_input_tokens'],'output_tokens':limits['max_total_output_tokens'],
            'max_usd':limits['max_usd']}
        plan=compile_comparison(spec)
        cell=next(c for c in plan['cells'] if c['arm']=='api_test_feedback')
        packet=entry.score_handoff(self.out,plan,cell['cell_id'])
        self.assertEqual(packet['test_plan'],self.plan)
        self.assertNotIn('spec',packet['verification_receipt'])
        self.assertEqual(packet['verification_receipt']['execution_status'],'not_performed')
        self.assertFalse(packet['semantic_truth_validated']);self.assertIsNone(packet['result']['review'])
        spec['tasks'][0]['initial_context_sha256']=entry.initial_context_id(self.files,self.task['issue'])
        with self.assertRaises(ContractError):entry.score_handoff(self.out,compile_comparison(spec),cell['cell_id'])

    def test_official_mode_with_pytest_never_opens_ledger_or_key_without_backend(self):
        import time
        ledger=self.root/'spend-ledger'
        plan={'schema':'macr.repair.spend-plan.v1','run_id':'own-pytest-test',
            'ledger_root':str(ledger),'configuration_sha256':loop.digest(self.config),
            'output_contract_sha256':loop.digest(loop.output_contract('v6')),
            'max_requests':3,'max_usd':'1.000000','max_total_input_tokens':24576,
            'max_total_output_tokens':3072,'expires_at':int(time.time())+600,
            'cells':[{'cell_id':'one','binding_sha256':entry.spend_binding_sha256(
                self.task,'api_debugger',self.config,proposal_contract='v6')}]}
        plan_file=self.root/'spend-plan.json';plan_file.write_text(json.dumps(plan))
        args=['agent-review','repair','--repo',str(self.repo),'--task',str(self.task_file),
            '--model-config',str(self.config_file),'--out',str(self.out),
            '--strategy','api_debugger','--proposal-contract','v6','--enable-remote',
            '--spend-plan',str(plan_file),'--spend-cell','one']
        output=io.StringIO()
        with patch('sys.argv',args),redirect_stdout(output), \
             patch.object(entry,'_read_runtime_credential',side_effect=AssertionError('no Key')) as key, \
             patch.object(entry,'_make_runtime_provider',side_effect=AssertionError('no HTTP')) as live:
            with self.assertRaises(SystemExit) as done:cli.main()
        result=json.loads(output.getvalue())
        self.assertEqual((done.exception.code,result['status'],result['provider_requests']),
                         (2,'verification_unavailable',0))
        self.assertEqual(result['spend_status'],'not_opened_no_verifier')
        self.assertFalse(ledger.exists());key.assert_not_called();live.assert_not_called()
