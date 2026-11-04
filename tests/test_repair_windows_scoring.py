"""Authored large-source scoring fixtures; staged source is never executed."""
from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from test_repair_entry import InterceptedTransport
from macr import cli
from macr.evals import repair_comparison as comparison
from macr.investigation import repair_entry as entry, repair_loop as loop
from macr.providers.repair_binding import RepairProviderBinding, development_config


class WindowedScoringTests(unittest.TestCase):
    def setUp(self):
        self.root = fixture_root()
        self.head = b'def quote():\r\n    return 1\r\n'
        self.files = {'builder.py':self.head + b''.join(
            ('padding_%04d = %d\r\n' % (n,n)).encode() for n in range(900))
            + b'AUTHORED_OMITTED_SENTINEL = "never import this source"\r\n',
            'checks.py':b'def test_quote():\r\n    assert False\r\n',
            'helper.py':b'OMITTED_HELPER = "whole helper bytes"\n'}
        self.selection = {'schema':'macr.repair.source-windows.v1','files':{
            'builder.py':[{'start_line':1,'end_line':2}],
            'checks.py':[{'start_line':1,'end_line':2}]}}
        self.test_plan = {'schema':'macr.repair.test-plan.v1','framework':'pytest',
                          'nodeids':['checks.py::test_quote']}
        self.issue = 'Authored quote issue: return two.'
        self.config = development_config()
        self.repo = self.root/'repo';self.repo.mkdir()
        for path,body in self.files.items(): (self.repo/path).write_bytes(body)
        self.overlay = self.root/'scorer-only';(self.overlay/'development').mkdir(parents=True)
        self.overlay_body = (b'raise RuntimeError("SCORER_ONLY_NEVER_IMPORT")\r\n'
            + b'# complete authored evaluator file\r\n' * 1100 + b'# exact final bytes')
        (self.overlay/'development/checks.py').write_bytes(self.overlay_body)
        self.manifest = {'schema':'macr.repair.scoring-overlay.v1','tasks':[
            {'task_id':'development','files':[{'path':'checks.py','bytes':len(self.overlay_body),
                'sha256':sha256(self.overlay_body).hexdigest()}]}]}
        self.spec = json.loads((Path(__file__).resolve().parents[1]/
            'examples/repair_comparison_three_arm_development.json').read_text())
        self.spec['tasks'] = [{'task_id':'development','snapshot_sha256':self.source_id(self.files),
            'issue_sha256':loop.digest(self.issue),
            'visible_tests_sha256':self.source_id({'checks.py':self.files['checks.py']}),
            'initial_context_sha256':self.context_id()}]
        self.spec['model']['configuration_sha256'] = loop.digest(self.config)
        self.spec['model']['output_contract_sha256'] = loop.digest(loop.output_contract('v6'))
        limits = self.config['limits']
        self.spec['limits'] = {'max_requests':limits['max_requests'],
            'input_tokens':limits['max_total_input_tokens'],
            'output_tokens':limits['max_total_output_tokens'],'max_usd':limits['max_usd']}
        self.spec['scoring']['hidden_tests_sha256'] = comparison.result_digest(self.manifest)
        self.plan = comparison.compile_comparison(self.spec)
        self.cell = next(c for c in self.plan['cells'] if c['arm']=='direct_api')

    @staticmethod
    def source_id(files):
        return comparison.result_digest({p:sha256(b).hexdigest() for p,b in files.items()})

    def context_id(self):
        # Hand-declared windows, line counts and omissions are independent of
        # the production projection/context implementation under test.
        projection = {'schema':'macr.repair.source-window-projection.v1','files':{}}
        for path,count,text,omitted in (
            ('builder.py',903,self.head,[{'start_line':3,'end_line':903}]),
            ('checks.py',2,self.files['checks.py'],[]),
            ('helper.py',1,None,[{'start_line':1,'end_line':1}])):
            body = self.files[path]
            windows = [] if text is None else [{'start_line':1,'end_line':2,
                'line_numbers':[1,2],'text':text.decode(),'sha256':sha256(text).hexdigest()}]
            projection['files'][path] = {'sha256':sha256(body).hexdigest(),'bytes':len(body),
                'line_count':count,'windows':windows,'omitted_ranges':omitted}
        return comparison.result_digest({'schema':'macr.repair.windowed-initial-context.v1',
            'source_files':{p:sha256(b).hexdigest() for p,b in self.files.items()},
            'issue_sha256':loop.digest(self.issue),'source_windows':projection,
            'source_scope':'declared_files','test_plan':self.test_plan})

    def proposal(self, base=None, *, old='return 1', new='return 2'):
        return {'status':'candidate','base_content_id':base or self.source_id(self.files),
            'summary':'Authored candidate; no execution.','selected_content_id':None,
            'edits':[{'file':'builder.py','old':old,'new':new}]}

    def row(self, final=None, candidate=None, requests=1):
        row = comparison._empty(self.plan['spec']['tasks'][0],self.plan['spec'])
        row.update(status='answer_recorded',candidate_origin='simulation',
            verification_origin='unavailable',verdict='defect' if candidate else 'unknown',
            requests=requests,input_tokens=100,output_tokens=50,
            model_identity_sha256='a'*64,final_output_sha256=comparison.result_digest(final),
            candidate_sha256=self.source_id(candidate) if candidate else None)
        return row

    def packet(self, row, **kwargs):
        values = dict(original_files=self.files,editable_paths=['builder.py'],
            protected_paths=['checks.py'],source_scope='declared_files',
            source_windows=self.selection,test_plan=self.test_plan)
        values.update(kwargs)
        return comparison.score_packet(self.plan,self.cell['cell_id'],row,**values)

    def invoke(self, *args):
        output = io.StringIO()
        with patch.object(sys,'argv',['agent-review',*map(str,args)]),redirect_stdout(output):
            try: cli.main()
            except SystemExit as error: code = error.code
            else: code = 0
        return code,json.loads(output.getvalue()) if output.getvalue() else {}

    def record(self, *, arm='direct_api', selected=False):
        task = {'schema':'macr.repair.task.v4','task_id':'development','issue':self.issue,
            'source_files':{p:sha256(b).hexdigest() for p,b in self.files.items()},
            'editable_paths':['builder.py'],'test_paths':['checks.py'],
            'test_plan':self.test_plan,'source_scope':'declared_files','source_windows':self.selection}
        task_path=self.root/'task.json';task_path.write_text(json.dumps(task))
        model_path=self.root/'model.json';model_path.write_text(json.dumps(self.config))
        replies = [self.proposal(),{'status':'finalize_current','summary':'Retain without execution.','edits':[]}]
        if selected:
            fixed = dict(self.files);fixed['builder.py'] = self.files['builder.py'].replace(b'return 1',b'return 2')
            replies = [self.proposal(),self.proposal(old='return 2',new='return 1'),
                {'status':'select_candidate','selected_content_id':self.source_id(fixed),
                 'summary':'Select without rerun.','edits':[]}]
        # Real provider encoding/parsing is exercised with a socket-free transport.
        # Base/evidence bindings must come from the current request each round.
        for reply in replies: reply.pop('base_content_id',None)
        transport=InterceptedTransport(replies)
        provider=RepairProviderBinding(self.config,transport=transport)
        record = self.root/'record'
        with patch.object(entry,'_make_provider',return_value=provider):
            code,result=self.invoke('repair','--repo',self.repo,'--task',task_path,
                '--model-config',model_path,'--out',record,'--strategy',arm,'--proposal-contract','v6')
        self.assertEqual(code,0 if arm=='direct_api' else 2,result)
        return record,transport

    def stage(self, record, *, plan=None, arm='direct_api'):
        plan=self.plan if plan is None else plan
        path=self.root/'plan.json';path.write_text(json.dumps(plan))
        manifest_path=self.root/'overlay.json';manifest_path.write_text(json.dumps(self.manifest))
        cell=next(c for c in plan['cells'] if c['arm']==arm)
        out=self.root/'staged'
        code,result=self.invoke('repair-comparison','stage-scoring','--plan',path,'--record',record,
            '--cell',cell['cell_id'],'--overlay-manifest',manifest_path,'--overlay-root',self.overlay,'--out',out)
        return code,result,out

    def test_single_edit_exports_complete_original_inventory_and_pending_candidate(self):
        final = self.proposal()
        fixed = dict(self.files);fixed['builder.py'] = self.files['builder.py'].replace(b'return 1',b'return 2')
        packet = self.packet(self.row(final,fixed),edits=final['edits'],final_output=final)
        self.assertGreater(len(self.files['builder.py']),16384)
        self.assertEqual(packet['candidate_files'],{p:b.decode() for p,b in fixed.items()})
        self.assertEqual(packet['candidate_files']['checks.py'].encode(),self.files['checks.py'])
        self.assertEqual(packet['source_windows'],self.selection)
        self.assertEqual(packet['original_source_id'],self.source_id(self.files))
        self.assertIsNone(packet['result']['review'])
        self.assertFalse(packet['semantic_truth_validated'])
        report = comparison.build_report(self.plan,{self.cell['cell_id']:packet['result']})
        for stats in report['arms'].values():
            self.assertEqual(stats['scored'],0)
            self.assertEqual(stats['completion_bounds']['observed_successes'],0)

    def test_changed_window_or_test_plan_cannot_graft_onto_frozen_initial_context(self):
        row = comparison._empty(self.plan['spec']['tasks'][0],self.plan['spec'])
        changed = deepcopy(self.selection);changed['files']['builder.py'][0]['end_line'] = 3
        bad_plan = deepcopy(self.test_plan);bad_plan['schema'] = 'macr.repair.test-plan.v2'
        for kwargs in ({'source_windows':changed},{'test_plan':bad_plan},
                       {'source_scope':None},{'source_scope':'strict_inventory'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.packet(row,**kwargs)

    def test_omitted_edits_and_partial_protected_tests_are_rejected(self):
        final = self.proposal(old='AUTHORED_OMITTED_SENTINEL',new='UNSEEN_CHANGE')
        fixed = dict(self.files);fixed['builder.py'] = self.files['builder.py'].replace(
            b'AUTHORED_OMITTED_SENTINEL',b'UNSEEN_CHANGE')
        for chain in (False,True):
            kwargs = {'proposal_chain':[final]} if chain else {'edits':final['edits']}
            with self.subTest(chain=chain), self.assertRaises(ValueError):
                self.packet(self.row(final,fixed),final_output=final,**kwargs)
        partial = deepcopy(self.selection);partial['files']['checks.py'][0]['end_line'] = 1
        with self.assertRaises(ValueError):
            self.packet(comparison._empty(self.plan['spec']['tasks'][0],self.plan['spec']),
                        source_windows=partial)

    def test_selected_prior_candidate_retains_full_bytes_without_validation_credit(self):
        first = self.proposal()
        fixed = dict(self.files);fixed['builder.py'] = self.files['builder.py'].replace(b'return 1',b'return 2')
        second = self.proposal(self.source_id(fixed),old='return 2',new='return 1')
        selected = {'status':'select_candidate','base_content_id':self.source_id(self.files),
            'selected_content_id':self.source_id(fixed),'summary':'Select without rerun.','edits':[]}
        packet = self.packet(self.row(selected,fixed,3),final_output=selected,
                             proposal_chain=[first,second,selected])
        self.assertEqual(packet['candidate_files'],{p:b.decode() for p,b in fixed.items()})
        self.assertEqual(packet['last_applied_source_id'],self.source_id(self.files))
        self.assertNotEqual(packet['last_applied_source_id'],packet['result']['candidate_sha256'])
        self.assertIsNone(packet['result']['verification_receipt_sha256'])
        self.assertIsNone(packet['result']['review'])

    def test_visible_edit_that_exceeds_next_projection_budget_is_rejected(self):
        final=self.proposal(new="return '"+'x'*16340+"'")
        fixed=dict(self.files)
        fixed['builder.py']=self.files['builder.py'].replace(b'return 1',final['edits'][0]['new'].encode())
        for chain in (False,True):
            kwargs={'proposal_chain':[final]} if chain else {'edits':final['edits']}
            with self.subTest(chain=chain), self.assertRaises(ValueError):
                self.packet(self.row(final,fixed),final_output=final,**kwargs)

    def test_unrun_packet_is_candidate_absent_and_all_planned_arms_remain_unscored(self):
        row = comparison._empty(self.plan['spec']['tasks'][0],self.plan['spec'])
        packet = self.packet(row)
        self.assertIsNone(packet['candidate_files'])
        self.assertEqual(packet['result']['requests'],0)
        report = comparison.build_report(self.plan,{self.cell['cell_id']:packet['result']})
        self.assertEqual(report['planned_cells'],3)
        for stats in report['arms'].values():
            self.assertEqual(stats['statuses']['not_attempted'],1)
            self.assertEqual(stats['scored'],0)
            self.assertEqual(stats['completion_bounds']['unresolved'],1)

    def test_normal_cli_stages_complete_full_original_and_candidate_with_whole_overlay(self):
        record,transport=self.record()
        before=loop.verify_loop(record)
        code,result,out=self.stage(record)
        self.assertEqual(code,0,result)
        fixed=self.files['builder.py'].replace(b'return 1',b'return 2')
        for variant,body in (('original',self.files['builder.py']),('candidate',fixed)):
            self.assertEqual((out/variant/'builder.py').read_bytes(),body)
            self.assertEqual((out/variant/'helper.py').read_bytes(),self.files['helper.py'])
            self.assertEqual((out/variant/'checks.py').read_bytes(),self.overlay_body)
        packet=json.loads((out/'packet.json').read_text())
        self.assertEqual(packet['candidate_files']['checks.py'].encode(),self.files['checks.py'])
        self.assertEqual(packet['source_windows'],self.selection)
        self.assertEqual((record/'sources/0/checks.py').read_bytes(),self.files['checks.py'])
        self.assertEqual(loop.verify_loop(record),before)
        staging=json.loads((out/'staging.json').read_text())
        self.assertFalse(staging['tests_executed']);self.assertFalse(staging['scoring_credit_awarded'])
        self.assertEqual(staging['execution_status'],'not_performed')
        self.assertIsNone(packet['result']['review'])
        self.assertNotIn('AUTHORED_OMITTED_SENTINEL',json.dumps(transport.requests))
        self.assertNotIn('SCORER_ONLY_NEVER_IMPORT',json.dumps(transport.requests))

    def test_normal_cli_stages_selected_earlier_candidate_and_keeps_it_unverified(self):
        record,_=self.record(selected=True)
        code,result,out=self.stage(record)
        self.assertEqual(code,0,result)
        self.assertEqual((out/'candidate/builder.py').read_bytes(),
            self.files['builder.py'].replace(b'return 1',b'return 2'))
        packet=json.loads((out/'packet.json').read_text())
        self.assertNotEqual(packet['last_applied_source_id'],packet['result']['candidate_sha256'])
        self.assertEqual(packet['verification_receipt']['selection']['verification_status'],'not_rerun')
        self.assertIsNone(packet['result']['review'])

    def test_missing_verification_stages_full_original_without_candidate_or_credit(self):
        record,transport=self.record(arm='api_debugger')
        code,result,out=self.stage(record,arm='api_debugger')
        self.assertEqual(code,0,result)
        self.assertEqual(transport.requests,[])
        self.assertEqual((out/'original/builder.py').read_bytes(),self.files['builder.py'])
        self.assertFalse((out/'candidate').exists())
        packet=json.loads((out/'packet.json').read_text())
        self.assertEqual(packet['result']['status'],'verification_unavailable')
        self.assertEqual(packet['result']['requests'],0)
        self.assertIsNone(packet['result']['review'])

    def test_normal_cli_rejects_frozen_initial_context_mismatch_before_output_claim(self):
        record,_=self.record()
        bad=deepcopy(self.spec);bad['tasks'][0]['initial_context_sha256']='0'*64
        code,result,out=self.stage(record,plan=comparison.compile_comparison(bad))
        self.assertEqual(code,2,result)
        self.assertFalse(out.exists())
