"""Natural issue -> static proposal -> explicit scoped repair, no execution."""
from contextlib import redirect_stdout
from hashlib import sha256
import io
import json
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root,write_files
from macr import cli
from macr.investigation import scope,repair_entry,repair_loop as loop
from macr.providers.repair_binding import RepairProviderBinding,development_config
from test_repair_entry import InterceptedTransport


SOURCES={
 'cache.py':'raise RuntimeError("discovery must not execute")\n'
     'def cached_preview(cache_key,now):\n'
     '    if now < 0:\n        raise ValueError("preview cache entry expired")\n    return cache_key\n',
 'render.py':'from cache import cached_preview\n'
     'def render_preview(image,now):\n    return cached_preview(image.cache_key,now)\n',
 'archive.py':'# preview cache entry expired\n'*40+'def archive_record(record):\n    return record\n',
 'tests/public.py':'raise RuntimeError("public tests must not execute")\nimport unittest\n'
     'class PublicChecks(unittest.TestCase):\n    def test_preview(self):\n        assert True\n',
 'README.md':'preview cache entry expired NONPY_MUST_NOT_READ\n',
 'CONFIG.JSON':'{"message":"preview cache entry expired NONPY_MUST_NOT_READ"}',
 'oracles/hidden.py':'raise RuntimeError("HIDDEN_MUST_NOT_READ")\n',
}
ISSUE='After a preview lookup, the UI reports "preview cache entry expired". Which implementation files need inspection?'


class RepairLocateEntryTests(unittest.TestCase):
    def setUp(self):
        self.root=fixture_root();self.repo=write_files(self.root/'authorized-source',SOURCES)
        self.issue=self.root/'issue.txt';self.issue.write_text(ISSUE)
        self.out=self.root/'localization'
        self.command=str(Path(cli.__file__).resolve().parents[2]/'cli/agent_review.py')

    def invoke(self,*extra):
        args=[self.command,'repair-locate','--repo',str(self.repo),'--issue-file',str(self.issue),
              '--exclude','tests/**','--out',str(self.out),*map(str,extra)]
        stream=io.StringIO()
        with patch('sys.argv',args),redirect_stdout(stream):
            with self.assertRaises(SystemExit) as done:runpy.run_path(self.command,run_name='__main__')
        return done.exception.code,json.loads(stream.getvalue()) if stream.getvalue() else {}

    def test_normal_cli_reads_only_authorized_python_and_returns_no_edit_authority(self):
        real_read=scope.read_safe;reads=[]
        def observed(root,path,limit):
            if Path(root)==self.repo:reads.append(path)
            return real_read(root,path,limit)
        with patch.object(scope,'read_safe',side_effect=observed):
            code,result=self.invoke()
        self.assertEqual(code,0)
        self.assertEqual(result['status'],'candidates_proposed')
        proposal=json.loads((self.out/'proposal.json').read_text())
        self.assertEqual(proposal['candidates'][0]['path'],'cache.py')
        self.assertIn('cache.py',proposal['source_files'])
        self.assertFalse(proposal['task_ready']);self.assertEqual(proposal['model_calls'],0)
        self.assertFalse(proposal['target_code_executed'])
        self.assertNotIn('editable_paths',proposal);self.assertNotIn('test_paths',proposal)
        self.assertFalse((self.out/'task.json').exists())
        self.assertTrue(reads);self.assertLessEqual(set(reads),{'cache.py','render.py','archive.py'})
        self.assertNotIn('HIDDEN_MUST_NOT_READ',json.dumps(proposal))
        self.assertNotIn('NONPY_MUST_NOT_READ',json.dumps(proposal))
        self.assertEqual((self.out.stat().st_mode&0o777),0o700)
        self.assertTrue(all(p.stat().st_mode&0o777==0o600 for p in self.out.rglob('*') if p.is_file()))

    def test_unmatched_issue_is_inconclusive_without_alphabetic_candidates(self):
        self.issue.write_text('Authentication credentials produce an invalid signature.')
        code,result=self.invoke()
        self.assertEqual(code,0)
        proposal=json.loads((self.out/'proposal.json').read_text())
        self.assertEqual(proposal['status'],'inconclusive')
        self.assertEqual(proposal['candidates'],[])
        self.assertIsNone(proposal['source_files'])

    def test_explicit_windowed_preparation_preserves_large_original_without_claiming_task_readiness(self):
        from macr.investigation.repair_windows import project_source_windows
        large=SOURCES['cache.py']+'# authored padding, never executed\n'*800
        (self.repo/'cache.py').write_text(large)
        code,result=self.invoke('--windowed-input')
        self.assertEqual(code,0)
        proposal=json.loads((self.out/'proposal.json').read_text())
        self.assertIsNone(proposal['source_files'])
        prepared=proposal['windowed_source']
        self.assertEqual(prepared['source_files']['cache.py'],sha256(large.encode()).hexdigest())
        self.assertEqual(prepared['source_windows']['schema'],'macr.repair.source-windows.v1')
        full={p:(self.repo/p).read_bytes() for p in prepared['source_files']}
        view=project_source_windows(full,prepared['source_windows'])
        self.assertEqual(prepared['projection'],view)
        self.assertTrue(view['files']['cache.py']['omitted_ranges'])
        self.assertTrue(result['windowed_input_proposed'])
        self.assertFalse(result['task_ready']);self.assertFalse(result['target_code_executed'])
        self.assertEqual(result['model_calls'],0)

    def test_confirmed_candidates_enter_existing_repair_without_hidden_context(self):
        self.assertEqual(self.invoke()[0],0)
        proposal=json.loads((self.out/'proposal.json').read_text())
        sources=dict(proposal['source_files'])
        sources['tests/public.py']=sha256(SOURCES['tests/public.py'].encode()).hexdigest()
        task={'schema':'macr.repair.task.v3','source_scope':'declared_files','test_plan':None,
            'task_id':'preview-development','issue':ISSUE,'source_files':sources,
            'editable_paths':['cache.py'],'test_paths':['tests/public.py']}
        task_file=self.root/'approved-task.json';task_file.write_text(json.dumps(task))
        model=development_config();model_file=self.root/'model.json';model_file.write_text(json.dumps(model))
        record=self.root/'repair-record'
        transport=InterceptedTransport([{'status':'no_candidate','summary':'Authored response, not evidence of repair.','edits':[]}])
        provider=RepairProviderBinding(model,transport=transport)
        args=[self.command,'repair','--repo',str(self.repo),'--task',str(task_file),'--model-config',str(model_file),
              '--out',str(record),'--strategy','direct_api','--proposal-contract','v6']
        with patch('sys.argv',args),patch.object(repair_entry,'_make_provider',return_value=provider),redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as done:runpy.run_path(self.command,run_name='__main__')
        self.assertEqual(done.exception.code,0)
        payload=json.loads(transport.requests[0]['messages'][1]['content'])
        self.assertEqual(set(payload['files']),set(sources))
        self.assertEqual(payload['source_view']['coverage'],'partial_repository')
        self.assertNotIn('HIDDEN_MUST_NOT_READ',json.dumps(payload))
        self.assertFalse(loop.verify_loop(record)['repaired'])

    def test_invalid_issue_existing_output_or_source_overlap_never_overwrites(self):
        for raw in (b'',b'x'*2049,b'\xff',b'bad\0issue'):
            self.issue.write_bytes(raw)
            self.assertEqual(self.invoke()[0],2)
            self.assertFalse(self.out.exists())
        self.issue.write_text(ISSUE)
        self.out=self.repo/'forbidden-output'
        self.assertEqual(self.invoke()[0],2);self.assertFalse(self.out.exists())
        self.out=self.root/'existing';self.out.mkdir();(self.out/'keep').write_text('unchanged')
        self.assertEqual(self.invoke()[0],2)
        self.assertEqual((self.out/'keep').read_text(),'unchanged')
