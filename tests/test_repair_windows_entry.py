"""Large complete originals through the real CLI, never imported or executed."""
from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root, write_files
from macr import cli
from macr.investigation import repair_entry as entry, repair_loop as loop
from macr.investigation.validation import ContractError
from macr.providers.repair_binding import RepairProviderBinding, development_config
from test_repair_entry import InterceptedTransport


def window_files():
    large = ('def compute(value):\n    return value + 1\n\n'
        + ''.join(f'VALUE_{i} = {i}\n' for i in range(900))
        + '# Padding keeps this self-authored source beyond the old byte limit.\n' * 180
        + 'OMITTED_SENTINEL = "unshown original bytes"\n'
        + 'raise RuntimeError("target must never execute")')
    return {'module.py':large.encode(),
        'helper.py':b'OMITTED_HELPER_SENTINEL = 7\n',
        'checks.py':b'import unittest\n\nclass Checks(unittest.TestCase):\n    def test_public(self):\n        self.assertTrue(True)\n'}


def window_task(files):
    return {'schema':'macr.repair.task.v4','task_id':'window-development',
        'issue':'Inspect the visible compute implementation; these are authored development materials.',
        'source_scope':'declared_files','test_plan':None,'source_files':loop._hashes(files),
        'editable_paths':['module.py'],'test_paths':['checks.py'],
        'source_windows':{'schema':'macr.repair.source-windows.v1','files':{
            'module.py':[{'start_line':1,'end_line':3}],
            'checks.py':[{'start_line':1,'end_line':5}]}}}


class WindowedRepairEntryTests(unittest.TestCase):
    def setUp(self):
        self.root=fixture_root();self.files=window_files()
        self.repo=write_files(self.root/'repo',{p:b.decode() for p,b in self.files.items()})
        self.task=window_task(self.files);self.task_path=self.root/'task.json'
        self.model=development_config();self.model_path=self.root/'model.json'
        self.model_path.write_text(json.dumps(self.model));self.out=self.root/'record'

    def invoke(self,replies,*,arm='direct_api',verifier=None):
        self.task_path.write_text(json.dumps(self.task))
        transport=InterceptedTransport(replies)
        provider=RepairProviderBinding(self.model,transport=transport)
        command=str(Path(cli.__file__).resolve().parents[2]/'cli/agent_review.py')
        argv=[command,'repair','--repo',str(self.repo),'--task',str(self.task_path),
            '--model-config',str(self.model_path),'--out',str(self.out),
            '--strategy',arm,'--proposal-contract','v6']
        output=io.StringIO()
        with patch('sys.argv',argv),patch.object(entry,'_make_provider',return_value=provider), \
                patch.object(entry,'_verification_backend',return_value=verifier),redirect_stdout(output):
            with self.assertRaises(SystemExit) as result:runpy.run_path(command,run_name='__main__')
        return result.exception.code,json.loads(output.getvalue()),transport

    def proposal(self,old='return value + 1',new='return value + 2'):
        return {'status':'candidate','summary':'Authored local edit, not a model repair.',
            'edits':[{'file':'module.py','old':old,'new':new}]}

    def observation(self,files):
        # Exact full-inventory digest; synthetic failure, never executed tests.
        return {'source_id':loop.digest(loop._hashes(files)),'execution_status':'completed',
            'outcomes':{'checks.py::Checks.test_public':'failed'}}

    def test_normal_cli_preserves_full_original_and_candidate_without_sending_omitted_text(self):
        code,result,transport=self.invoke([self.proposal(),
            {'status':'finalize_current','summary':'Keep this unverified candidate.','edits':[]}])
        self.assertEqual((code,result['status']),(0,'candidate_finalized'))
        expected=self.files['module.py'].replace(b'return value + 1',b'return value + 2')
        self.assertEqual((self.out/'sources/0/module.py').read_bytes(),self.files['module.py'])
        self.assertEqual((self.out/'sources/1/module.py').read_bytes(),expected)
        self.assertEqual((self.repo/'module.py').read_bytes(),self.files['module.py'])
        self.assertGreater(len(expected),16384)
        for wire in transport.requests:
            payload=json.loads(wire['messages'][1]['content'])
            self.assertEqual(payload['files'],{})
            view=payload['source_windows']['files']['module.py']
            self.assertGreater(view['bytes'],16384)
            self.assertEqual(view['windows'][0]['line_numbers'],[1,2,3])
            self.assertTrue(view['omitted_ranges'])
        self.assertNotIn('OMITTED_SENTINEL',json.dumps(transport.requests))
        self.assertNotIn('OMITTED_HELPER_SENTINEL',json.dumps(transport.requests))
        report=loop.verify_loop(self.out)
        self.assertFalse(report['target_code_executed']);self.assertFalse(report['repaired'])

    def test_line_changing_edit_projects_current_bytes_and_line_numbers_in_second_request(self):
        new='result = value + 2\n    return result'
        code,result,transport=self.invoke([self.proposal(new=new),
            {'status':'finalize_current','summary':'Keep current bytes.','edits':[]}])
        self.assertEqual(code,0)
        first,second=[json.loads(w['messages'][1]['content']) for w in transport.requests]
        self.assertNotEqual(first['base_content_id'],second['base_content_id'])
        window=second['source_windows']['files']['module.py']['windows'][0]
        self.assertEqual(window['text'],'def compute(value):\n    result = value + 2\n    return result\n')
        self.assertEqual(window['line_numbers'],[1,2,3])
        changed=self.files['module.py'].replace(b'return value + 1',new.encode())
        self.assertEqual(second['source_windows']['files']['module.py']['sha256'],sha256(changed).hexdigest())

    def test_normal_cli_v2_edits_visible_duplicate_instead_of_hidden_first_match(self):
        original = self.files['module.py']
        hidden = b'#    return value + 1\n'
        self.files['module.py'] = hidden + original
        (self.repo/'module.py').write_bytes(self.files['module.py'])
        self.task = window_task(self.files)
        self.task['source_windows']['schema'] = 'macr.repair.source-windows.v2'
        self.task['source_windows']['files']['module.py'] = [{'start_line':3,'end_line':3}]
        old, new = '    return value + 1\n', '    return value + 2\n'
        code,result,transport = self.invoke([self.proposal(old=old,new=new),
            {'status':'finalize_current','summary':'Retain authored bytes, not a verified repair.','edits':[]}])
        self.assertEqual((code,result['status']),(0,'candidate_finalized'))
        expected = hidden + original.replace(old.encode(),new.encode(),1)
        self.assertEqual((self.out/'sources/1/module.py').read_bytes(),expected)
        self.assertEqual((self.repo/'module.py').read_bytes(),hidden+original)
        first,second = [json.loads(w['messages'][1]['content']) for w in transport.requests]
        self.assertEqual(first['source_windows']['edit_policy'],'unique_shown_span')
        self.assertEqual(first['source_windows']['files']['module.py']['windows'][0]['text'],old)
        self.assertEqual(second['source_windows']['files']['module.py']['windows'][0]['text'],new)
        self.assertNotIn('OMITTED_SENTINEL',json.dumps(transport.requests))
        self.assertFalse(loop.verify_loop(self.out)['repaired'])

    def test_unshown_edit_fails_and_never_materializes_a_candidate(self):
        reply=self.proposal(old='OMITTED_SENTINEL = "unshown original bytes"',new='OMITTED_SENTINEL = "changed"')
        code,result,transport=self.invoke([reply])
        self.assertEqual((code,result['status']),(2,'proposal_invalid'))
        self.assertEqual(len(transport.requests),1)
        self.assertFalse((self.out/'sources/1').exists())
        self.assertEqual(loop.verify_loop(self.out)['final_source_id'],loop.digest(loop._hashes(self.files)))

    def test_full_original_profile_does_not_expand_the_advertised_edit_text_limit(self):
        replacement='return value + 2\n'+'# inserted authored comment\n'*800
        self.assertGreater(len(replacement.encode()),16384)
        code,result,transport=self.invoke([self.proposal(new=replacement),
            {'status':'finalize_current','summary':'This oversized edit must never be accepted.','edits':[]}])
        self.assertEqual((code,result['status']),(2,'proposal_invalid'))
        self.assertEqual(len(transport.requests),1)
        self.assertFalse((self.out/'sources/1').exists())

    def test_all_three_arms_share_initial_windows_and_identical_model_limits(self):
        changed=dict(self.files);changed['module.py']=changed['module.py'].replace(b'return value + 1',b'return value + 2')
        views=[];limits=[]
        for arm in ('direct_api','api_test_feedback','api_debugger'):
            self.out=self.root/arm
            verifier=loop.ReplayVerifier([self.observation(self.files),self.observation(changed)])
            code,result,transport=self.invoke([self.proposal(),
                {'status':'finalize_current','summary':'Keep current unverified bytes.','edits':[]}],arm=arm,verifier=verifier)
            self.assertEqual(code,0)
            payload=json.loads(transport.requests[0]['messages'][1]['content'])
            views.append((payload['files'],payload['source_windows'],payload['issue'],payload['base_content_id']))
            intent=loop._read(self.out,'rounds/1/intent.json')['request']
            limits.append((intent['max_input_tokens'],intent['max_output_tokens'],self.model['limits']))
            self.assertEqual(result['provider_requests'],2)
            self.assertEqual(verifier.calls,0 if arm=='direct_api' else 2)
        self.assertTrue(all(v==views[0] for v in views))
        self.assertTrue(all(v==limits[0] for v in limits))

    def test_invalid_selection_and_legacy_large_input_fail_before_claim_or_dispatch(self):
        bad=[{}, {'module.py':[{'start_line':0,'end_line':3}]},
            {'module.py':[{'start_line':1,'end_line':3},{'start_line':3,'end_line':4}]},
            {'module.py':[{'start_line':1,'end_line':99999}]},
            {'outside.py':[{'start_line':1,'end_line':1}]}]
        for n,files in enumerate(bad):
            self.out=self.root/('invalid-'+str(n));self.task=window_task(self.files)
            self.task['source_windows']['files']=files
            code,result,transport=self.invoke([])
            self.assertEqual(code,2);self.assertEqual(transport.requests,[]);self.assertFalse(self.out.exists())
        self.task=window_task(self.files);self.task['schema']='macr.repair.task.v3';del self.task['source_windows']
        self.out=self.root/'legacy-large'
        code,result,transport=self.invoke([])
        self.assertEqual(code,2);self.assertEqual(transport.requests,[]);self.assertFalse(self.out.exists())

    def test_rehashed_selection_tamper_and_repeated_launch_are_rejected(self):
        self.assertEqual(self.invoke([{'status':'no_candidate','summary':'No candidate.','edits':[]}])[0],0)
        code,result,transport=self.invoke([])
        self.assertEqual(code,2);self.assertEqual(transport.requests,[])
        claim=loop._read(self.out,'claim.json');claim['config']['source_windows']['files']['module.py'][0]['end_line']=2
        loop._write(self.out,'claim.json',claim)
        report=loop._read(self.out,'report.json');report['source_windows']=deepcopy(claim['config']['source_windows'])
        report['config_sha256']=loop.digest(claim['config'])
        report['artifact_hashes']=loop._file_hashes(self.out,skip=('report.json',))
        loop._write(self.out,'report.json',report)
        with self.assertRaises(ContractError):loop.verify_loop(self.out)

    def test_missing_verifier_remains_an_honest_stop_with_full_sources_retained(self):
        for version in ('v1','v2'):
            with self.subTest(version=version):
                self.out=self.root/('missing-verifier-'+version)
                self.task['source_windows']['schema']='macr.repair.source-windows.'+version
                code,result,transport=self.invoke([],arm='api_debugger')
                self.assertEqual((code,result['status'],result['provider_requests']),(2,'verification_unavailable',0))
                self.assertEqual(transport.requests,[])
                self.assertEqual((self.out/'sources/0/module.py').read_bytes(),self.files['module.py'])
                self.assertFalse(loop.verify_loop(self.out)['repaired'])
