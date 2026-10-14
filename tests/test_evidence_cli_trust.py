"""Actual trust-code producers, pure CLI classification; no real process/native query."""
import argparse
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from macr import evidence_cli
from macr.investigation.evidence_budget import EvidenceBudget
from macr.investigation.evidence_profiles import EvidenceProfile
from macr.investigation.evidence_records import EvidenceRequest, StaticEvidenceReport
from macr.investigation.evidence_workspace import EvidenceWorkspace
from macr.investigation.records import ScopeManifest
from macr.investigation.scope import RepositoryScope
from macr.investigation.snapshots import SnapshotStore, SnapshotError
from macr.investigation import evidence_supervisor as supervisor

class TrustExitTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='macr-trust-cli-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.repo=self.root/'repo';self.repo.mkdir()
        (self.repo/'a.py').write_bytes(b'def a():\n    return 1\n')
        self.profile=EvidenceProfile.select(overrides={'min_free_bytes':1})
        self.request=EvidenceRequest(str(self.repo),str(self.root/'out'),'private reading question',['a.py'],profile=self.profile)

    def failure(self, code, *, run=None):
        return StaticEvidenceReport(self.request,'rejected',run=run or {'publication':'uncommitted'},errors=[{'code':code}]).validate()

    def test_real_saved_byte_mismatch_maps_stale_snapshot_to_exit3(self):
        workspace=EvidenceWorkspace(self.repo,self.root/'capture',self.profile)
        store=SnapshotStore(workspace.snapshots_root,allowed_root=workspace.root,profile=self.profile,budget=workspace.budget)
        scope=RepositoryScope().inventory(self.repo,self.profile.scope_profile(),budget=workspace.budget)
        captured=store.capture(self.repo,scope)
        (Path(captured.ref.root_ref)/'a.py').write_bytes(b'def a():\n    return 2\n')
        with self.assertRaises(SnapshotError) as caught:store.read_verified(captured.ref,'a.py')
        self.assertEqual(caught.exception.code,'stale_snapshot')
        self.assertEqual(evidence_cli._exit_code(self.failure(caught.exception.code)),3)

    def test_real_scope_case_collision_producer_maps_to_exit3(self):
        scope=ScopeManifest(profile=self.profile.scope_profile())
        scope_reader=RepositoryScope();names={}
        scope_reader._add_entry(scope,names,self.repo,scope.profile,'a.py','file',22)
        scope_reader._add_entry(scope,names,self.repo,scope.profile,'A.py','file',22)
        self.assertEqual(len(scope.errors),1)
        self.assertEqual(scope.errors[0].code,'case_collision')
        self.assertEqual(evidence_cli._exit_code(self.failure(scope.errors[0].code)),3)

    def test_unknown_started_closure_overrides_trust_exit(self):
        for code in ('stale_snapshot','case_collision'):
            for confirmed in (None,False):
                with self.subTest(code=code,confirmed=confirmed):
                    report=self.failure(code,run={'worker_started':True,'worker_exit_confirmed':confirmed})
                    self.assertEqual(evidence_cli._exit_code(report),4)

    def test_storage_oserror_stays_internal_exit4(self):
        closure={'starts':0,'pid':None,'returncode':None,'exit_confirmed':None}
        report=supervisor._failure(self.request,OSError('PRIVATE storage failure'),EvidenceBudget(self.profile),closure)
        self.assertEqual(report.errors[0]['code'],'storage_unavailable')
        self.assertEqual(evidence_cli._exit_code(report),4)

    def test_trust_envelope_uses_exit3_without_raw_diagnostics(self):
        parser=argparse.ArgumentParser();subs=parser.add_subparsers();evidence_cli.add_parser(subs)
        args=parser.parse_args(['evidence-locate','--repo',str(self.repo),'--out',str(self.root/'out'),'--question','private reading question','--entry','a.py','--json'])
        for code in ('stale_snapshot','case_collision'):
            stdout=io.StringIO();stderr=io.StringIO()
            with patch.object(supervisor,'collect_evidence',return_value=self.failure(code)),contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
                exit_code=evidence_cli.run(args)
            self.assertEqual(exit_code,3)
            self.assertIn('code='+code,stderr.getvalue())
            self.assertNotIn('private reading question',stderr.getvalue())
            self.assertNotIn(str(self.root),stderr.getvalue())
            self.assertEqual(__import__('json').loads(stdout.getvalue())['errors'][0]['code'],code)

if __name__=='__main__':unittest.main()
