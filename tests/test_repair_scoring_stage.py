"""Only authored text and intercepted providers; never import staged tests."""
from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root, write_files
from repair_fixtures import SOURCES, OLD, NEW
from test_repair_entry import InterceptedTransport
from macr import cli
from macr.evals import repair_comparison as comparison
from macr.investigation import repair_entry as entry, repair_loop as loop
from macr.providers.repair_binding import RepairProviderBinding, development_config


class ScoringStageTests(unittest.TestCase):
    def setUp(self):
        self.root = fixture_root()
        self.files = {p: text.encode() for p, text in SOURCES.items()}
        self.repo = write_files(self.root/'repo', SOURCES)
        self.overlay = self.root/'scorer-only'; self.overlay.mkdir()
        self.body = (b'raise RuntimeError("SCORER_ONLY_NEVER_IMPORT")\r\n'
                     + b'# full, unmodified test material\r\n' * 1100
                     + b'def test_full_file():\r\n    assert False\r\n# exact final bytes')
        folder = self.overlay/'development'; folder.mkdir()
        (folder/'checks.py').write_bytes(self.body)
        self.manifest = {'schema':'macr.repair.scoring-overlay.v1', 'tasks':[
            {'task_id':'development', 'files':[{'path':'checks.py',
              'bytes':len(self.body), 'sha256':sha256(self.body).hexdigest()}]}]}
        self.manifest_path = self.root/'overlay.json'
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.config = development_config()
        spec = json.loads((Path(__file__).resolve().parents[1]/
                           'examples/repair_comparison_three_arm_development.json').read_text())
        self.issue = 'Public authored invoice issue.'
        spec['tasks'] = [{'task_id':'development', 'snapshot_sha256':loop.source_id(self.files),
            'issue_sha256':loop.digest(self.issue),
            'visible_tests_sha256':loop.source_id({'checks.py':self.files['checks.py']}),
            'initial_context_sha256':entry.initial_context_id(self.files,self.issue)}]
        spec['model']['configuration_sha256'] = loop.digest(self.config)
        spec['model']['output_contract_sha256'] = loop.digest(loop.output_contract('v6'))
        limits = self.config['limits']
        spec['limits'] = {'max_requests':limits['max_requests'],
            'input_tokens':limits['max_total_input_tokens'],
            'output_tokens':limits['max_total_output_tokens'],'max_usd':limits['max_usd']}
        spec['scoring']['hidden_tests_sha256'] = comparison.result_digest(self.manifest)
        self.plan = comparison.compile_comparison(spec)
        self.plan_path = self.root/'plan.json'; self.plan_path.write_text(json.dumps(self.plan))

    def record(self, arm='direct_api', *, candidate=True, select_earlier=False):
        replies = ([{'status':'candidate','summary':'Authored proposal, no real model.',
            'edits':[{'file':'builder.py','old':OLD,'new':NEW}]},
            {'status':'finalize_current','summary':'Retain authored candidate.','edits':[]}]
            if candidate else [{'status':'insufficient_evidence','summary':'No candidate.', 'edits':[]}])
        if select_earlier:
            replies = [replies[0], {'status':'candidate','summary':'Authored regression.',
                'edits':[{'file':'builder.py','old':NEW,'new':OLD}]}]
            class SelectingTransport(InterceptedTransport):
                def post(inner,endpoint,body,timeout_seconds,max_response_bytes):
                    if len(inner.requests)==2:
                        request = json.loads(json.loads(body)['messages'][1]['content'])
                        selected = request['attempt_history']['entries'][0]['candidate_content_id']
                        inner.replies.append({'status':'select_candidate','selected_content_id':selected,
                            'summary':'Retain historical candidate, not a fresh pass.','edits':[]})
                    return super().post(endpoint,body,timeout_seconds,max_response_bytes)
            transport = SelectingTransport(replies)
        else:
            transport = InterceptedTransport(replies)
        provider = RepairProviderBinding(self.config,transport=transport)
        names = loop.test_manifest(self.files,['checks.py'])
        fixed = dict(self.files); fixed['builder.py'] = fixed['builder.py'].replace(OLD.encode(),NEW.encode())
        verifier = loop.ReplayVerifier([{'source_id':loop.source_id(files),
            'execution_status':'completed', 'outcomes':dict.fromkeys(names,'failed')}
            for files in (self.files,fixed)])
        root = self.root/('record-'+arm)
        loop.run_repair_loop(self.repo,self.issue,editable_paths=['builder.py'],test_paths=['checks.py'],
            provider=provider,verifier=verifier,out=root,task_id='development',
            strategy=arm,proposal_contract='v6')
        return root, transport

    def invoke(self, record, *, arm='direct_api', out=None, manifest=None):
        cell = next(c for c in self.plan['cells'] if c['arm']==arm)
        argv = ['agent-review','repair-comparison','stage-scoring','--plan',str(self.plan_path),
            '--record',str(record),'--cell',cell['cell_id'],
            '--overlay-manifest',str(manifest or self.manifest_path),
            '--overlay-root',str(self.overlay),'--out',str(out or self.root/('staged-'+arm))]
        output = io.StringIO()
        with patch.object(sys,'argv',argv),redirect_stdout(output):
            try: cli.main()
            except SystemExit as error: code = error.code
            else: code = 0
        return code,json.loads(output.getvalue()) if output.getvalue() else {}

    def test_normal_entry_stages_complete_bytes_for_all_arms_without_model_exposure(self):
        rows = {}
        for arm in ('direct_api','api_test_feedback','api_debugger'):
            record, transport = self.record(arm)
            before = loop.verify_loop(record)
            code, result = self.invoke(record,arm=arm)
            self.assertEqual(code,0)
            out = self.root/('staged-'+arm)
            for variant in ('original','candidate'):
                self.assertEqual((out/variant/'checks.py').read_bytes(),self.body)
                expected = self.files['builder.py'] if variant=='original' else self.files['builder.py'].replace(OLD.encode(),NEW.encode())
                self.assertEqual((out/variant/'builder.py').read_bytes(),expected)
            packet = json.loads((out/'packet.json').read_text())
            staged = json.loads((out/'staging.json').read_text())
            self.assertEqual(packet,entry.score_handoff(record,self.plan,packet['cell']['cell_id']))
            rows[packet['cell']['cell_id']] = packet['result']
            self.assertEqual(staged['overlay_sha256'],comparison.result_digest(self.manifest))
            self.assertEqual(staged['candidate_origin'],'simulation')
            self.assertFalse(staged['tests_executed']); self.assertFalse(staged['scoring_credit_awarded'])
            self.assertEqual(staged['test_collection_status'],'not_assessed')
            self.assertEqual(result['model_calls'],0)
            self.assertNotIn('SCORER_ONLY_NEVER_IMPORT',json.dumps(transport.requests))
            self.assertNotIn('scoring',json.dumps(comparison.generation_view(self.plan,'development')))
            self.assertEqual(loop.verify_loop(record),before)
            self.assertEqual((self.repo/'checks.py').read_bytes(),self.files['checks.py'])
        report = comparison.build_report(self.plan,rows)
        self.assertEqual(report['planned_cells'],3)
        self.assertTrue(all(arm['scored']==0 for arm in report['arms'].values()))

    def test_absent_candidate_is_not_replaced_by_original_or_a_passing_score(self):
        record,_ = self.record(candidate=False)
        self.assertEqual(self.invoke(record)[0],0)
        out = self.root/'staged-direct_api'
        self.assertFalse((out/'candidate').exists())
        staged = json.loads((out/'staging.json').read_text())
        self.assertIsNone(staged['candidate_files'])
        self.assertIsNone(json.loads((out/'packet.json').read_text())['result']['review'])

    def test_post_freeze_overlay_change_and_in_place_outputs_reject_before_claim(self):
        record,_ = self.record()
        old = (self.overlay/'development/checks.py').read_bytes()
        (self.overlay/'development/checks.py').write_bytes(old+b'# changed')
        self.assertEqual(self.invoke(record)[0],2)
        self.assertFalse((self.root/'staged-direct_api').exists())
        (self.overlay/'development/checks.py').write_bytes(old)
        for parent in (record,self.overlay):
            out = parent/'do-not-write'
            self.assertEqual(self.invoke(record,out=out)[0],2)
            self.assertFalse(out.exists())
        bad = deepcopy(self.manifest);bad['tasks'][0]['files'][0]['sha256']='0'*64
        self.manifest_path.write_text(json.dumps(bad))
        self.assertEqual(self.invoke(record)[0],2)
        self.assertFalse((self.root/'staged-direct_api').exists())

    def test_existing_output_and_rehashed_record_tampering_never_overwrite(self):
        record,_ = self.record()
        out = self.root/'staged-direct_api';out.mkdir();(out/'keep').write_bytes(b'keep')
        self.assertEqual(self.invoke(record)[0],2)
        self.assertEqual((out/'keep').read_bytes(),b'keep')
        (record/'sources/0/builder.py').write_bytes(b'print("tampered")\n')
        self.assertEqual(self.invoke(record,out=self.root/'tampered')[0],2)
        self.assertFalse((self.root/'tampered').exists())

    def test_selected_history_stages_selected_content_without_transferring_old_pass(self):
        record,_ = self.record(select_earlier=True)
        self.assertEqual(self.invoke(record)[0],0)
        out = self.root/'staged-direct_api'
        packet = json.loads((out/'packet.json').read_text())
        staged = json.loads((out/'staging.json').read_text())
        self.assertEqual((out/'candidate/builder.py').read_bytes(),
            self.files['builder.py'].replace(OLD.encode(),NEW.encode()))
        self.assertNotEqual(packet['last_applied_source_id'],packet['result']['candidate_sha256'])
        self.assertEqual(packet['verification_receipt']['selection']['verification_status'],'not_rerun')
        self.assertFalse(staged['generation_receipt_applies_to_overlay'])
        self.assertEqual(staged['execution_status'],'not_performed')

    def test_partial_write_stays_failed_and_cannot_be_reused_as_complete_bundle(self):
        from macr.evals import repair_scoring_stage as stage
        record,_ = self.record()
        real_write = stage.safe_write
        count = 0
        def fail_after_first(*args,**kwargs):
            nonlocal count
            count += 1
            if count==2: raise OSError('authored write failure')
            return real_write(*args,**kwargs)
        with patch.object(stage,'safe_write',side_effect=fail_after_first):
            self.assertEqual(self.invoke(record)[0],2)
        out = self.root/'staged-direct_api'
        self.assertTrue(out.exists());self.assertFalse((out/'staging.json').exists())
        self.assertEqual(self.invoke(record)[0],2)
        self.assertFalse((out/'staging.json').exists())

    def test_case_and_unicode_root_aliases_are_not_independent_output_roots(self):
        record,_ = self.record()
        for index,(left,right) in enumerate((('scorer-only','SCORER-ONLY'),
                                             ('scor\u00e9r','score\u0301r'))):
            source = self.root/left;source.mkdir(exist_ok=True)
            self.overlay = source
            folder = source/'development';folder.mkdir(exist_ok=True)
            (folder/'checks.py').write_bytes(self.body)
            alias = self.root/right;alias.mkdir(exist_ok=True)
            out = alias/('rejected-'+str(index))
            self.assertEqual(self.invoke(record,out=out)[0],2)
            self.assertFalse(out.exists())

    def test_cyclic_path_alias_returns_safe_rejection_instead_of_raw_traceback(self):
        record,_ = self.record()
        a,b = self.root/'cycle-a',self.root/'cycle-b'
        a.symlink_to(b);b.symlink_to(a)
        try:
            self.overlay = a
            code,result = self.invoke(record)
            self.assertEqual(code,2)
            self.assertEqual(result['status'],'rejected')
            self.assertNotIn('cycle-',json.dumps(result))
            self.assertFalse((self.root/'staged-direct_api').exists())
        finally:
            a.unlink();b.unlink()

    def test_v2_explicit_declared_path_mapping_is_retained_without_rewriting_bytes(self):
        self.manifest['schema'] = 'macr.repair.scoring-overlay.v2'
        item = self.manifest['tasks'][0]['files'][0]
        item.update(path='tests/checks.py',declared_path='tests//checks.py')
        folder = self.overlay/'development/tests';folder.mkdir()
        (folder/'checks.py').write_bytes(self.body)
        self.manifest_path.write_text(json.dumps(self.manifest))
        spec = deepcopy(self.plan['spec'])
        spec['scoring']['hidden_tests_sha256'] = comparison.result_digest(self.manifest)
        self.plan = comparison.compile_comparison(spec)
        self.plan_path.write_text(json.dumps(self.plan))
        record,transport = self.record()
        self.assertEqual(self.invoke(record)[0],0)
        out = self.root/'staged-direct_api'
        staged = json.loads((out/'staging.json').read_text())
        self.assertEqual(staged['overlays'][0]['path'],'tests/checks.py')
        self.assertEqual(staged['overlays'][0]['declared_path'],'tests//checks.py')
        self.assertEqual((out/'candidate/tests/checks.py').read_bytes(),self.body)
        self.assertEqual((out/'candidate/checks.py').read_bytes(),self.files['checks.py'])
        self.assertNotIn('declared_path',json.dumps(transport.requests))
        self.assertNotIn('SCORER_ONLY_NEVER_IMPORT',json.dumps(transport.requests))
