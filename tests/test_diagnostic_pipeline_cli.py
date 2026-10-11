import io
import json
from dataclasses import asdict
from pathlib import Path
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.evals.diagnostic_dataset import load_dataset
from macr.providers.base import ModelResponse
import verify_diagnostic


class DiagnosticPipelineCliTests(unittest.TestCase):
    def bundle(self, *, error=None):
        project = Path(__file__).resolve().parents[1]
        cases, reference = load_dataset(project / 'evals/diagnostic-development-v2/cases.json',
                                        project / 'evals/diagnostic-development-v2/reference.json')
        candidate = reference['rubric']['dv01']['accepted_shape_witness']
        states = {'requirement': 'established', 'trigger': 'reachable', 'behavior': 'violates',
                  'protection': 'absent', 'counterevidence': 'not_found'}
        check = {'checks': {key: {'state': state, 'basis': 'Visible OR admits the mixed boolean pair.',
                                 'refs': candidate['refs']} for key, state in states.items()},
                 'assessment': 'supported_defect', 'unknowns': []}
        response = ModelResponse(content=json.dumps(check))
        path = fixture_root() / 'capture.json'
        path.write_text(json.dumps({'request': cases[0]['request'], 'candidate_raw': json.dumps(candidate),
                                   'verification_response': None if error else asdict(response),
                                   'error_code': error, 'observations': None}))
        return path

    def call(self, path, out):
        with patch('sys.argv', ['verify_diagnostic.py', '--input', str(path), '--out', str(out)]), patch('sys.stdout', io.StringIO()):
            return verify_diagnostic.main()

    def test_real_replay_entry_keeps_raw_capture_and_has_zero_dispatch(self):
        path = self.bundle()
        out = fixture_root() / 'replay'
        self.assertEqual(self.call(path, out), 0)
        report = json.loads((out / 'verification.json').read_text())
        self.assertEqual(report['candidate_raw'], json.loads(path.read_text())['candidate_raw'])
        self.assertEqual(report['new_model_calls'], 0)
        self.assertFalse(report['semantic_proof'])
        self.assertEqual(report['final']['verdict'], 'supported_defect')
        with self.assertRaises(SystemExit):
            self.call(path, out)

    def test_captured_provider_failure_is_a_failure_with_no_final(self):
        out = fixture_root() / 'failure'
        self.assertEqual(self.call(self.bundle(error='model_timeout'), out), 1)
        report = json.loads((out / 'verification.json').read_text())
        self.assertEqual(report['status'], 'technical_failure')
        self.assertIsNone(report['final'])
        self.assertEqual(report['new_model_calls'], 0)
