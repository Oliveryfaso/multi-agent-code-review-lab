"""Known output-budget identities survive in-process worker failure control."""
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from macr.investigation.evidence_budget import BudgetExceeded, EvidenceBudget
from macr.investigation.evidence_profiles import EvidenceProfile
from macr.investigation.evidence_records import EvidenceRequest, StaticEvidenceReport
from macr.investigation.evidence_workspace import EvidenceWorkspace
from macr.investigation import evidence_supervisor as supervisor, evidence_worker as worker

class OutputTransportTests(unittest.TestCase):
    def test_tiny_log_preserves_exact_field_in_worker_control(self):
        from macr.investigation import static_evidence
        with tempfile.TemporaryDirectory(prefix='macr-cli-control-') as folder:
            root=Path(folder); (root/'repo').mkdir()
            profile=EvidenceProfile.select(overrides={'max_log_bytes':1,'min_free_bytes':1})
            request=EvidenceRequest(str(root/'repo'),str(root/'out'),'question',['a.py'],profile=profile)
            ws=EvidenceWorkspace(request.repo,request.out,profile)
            budget=EvidenceBudget(profile)
            payload=json.dumps({'request':request.to_dict(),'token':ws.token,'deadline':budget.deadline,'started':budget.started,'initial_usage':{}}).encode()
            stdout=SimpleNamespace(buffer=io.BytesIO());stdin=SimpleNamespace(buffer=io.BytesIO(payload))
            report=StaticEvidenceReport(request,'partial')
            with patch.object(worker.sys,'stdin',stdin),patch.object(worker.sys,'stdout',stdout),patch.object(static_evidence,'prepare_evidence',return_value=report),patch.object(static_evidence,'validate_report_sources',return_value=report):
                code=worker.main(profile.max_input_bytes)
            self.assertEqual(code,4)
            envelope=json.loads(stdout.buffer.getvalue())
            try:error=supervisor._worker_failure(envelope,budget)
            except Exception as caught:error=caught
            self.assertIsInstance(error,BudgetExceeded)
            self.assertEqual(error.field,'max_log_bytes')
            self.assertEqual(error.limit,1)
            self.assertEqual(error.observed,len(b'stage=written_pending status=partial\n'))
            self.assertEqual(error.stage,'workspace_write')
            self.assertFalse((root/'out/COMMITTED').exists())

    def test_marker_encode_uses_actual_control_budget_field(self):
        with tempfile.TemporaryDirectory(prefix='macr-marker-control-') as folder:
            root=Path(folder);(root/'repo').mkdir()
            profile=EvidenceProfile.select(overrides={'max_control_bytes':1,'min_free_bytes':1})
            with self.assertRaises(BudgetExceeded) as stopped:
                EvidenceWorkspace(root/'repo',root/'out',profile)
            self.assertEqual(stopped.exception.field,'max_control_bytes')
            self.assertEqual(stopped.exception.limit,1)

if __name__=='__main__':unittest.main()

class OutputCollisionTests(unittest.TestCase):
    def test_existing_out_is_boundary_exit_without_worker_or_mutation(self):
        from macr import evidence_cli
        with tempfile.TemporaryDirectory(prefix='macr-output-collision-') as folder:
            root=Path(folder);(root/'repo').mkdir();(root/'out').mkdir()
            marker=root/'out/retained.txt';marker.write_bytes(b'original')
            req=EvidenceRequest(str(root/'repo'),str(root/'out'),'question',['a.py'],profile=EvidenceProfile.select(overrides={'min_free_bytes':1}))
            with patch.object(supervisor,'_observe_pressure'):
                report=supervisor.collect_evidence(req)
            self.assertEqual(evidence_cli._exit_code(report),3)
            self.assertEqual(report.errors[0]['code'],'forbidden_path')
            self.assertEqual(report.errors[0]['field'],'output_exists')
            self.assertFalse(report.run['worker_started'])
            self.assertEqual(marker.read_bytes(),b'original')
            self.assertEqual(sorted(path.name for path in (root/'out').iterdir()),['retained.txt'])
