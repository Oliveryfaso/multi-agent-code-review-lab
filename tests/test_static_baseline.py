"""Read-only source analysis and real CLI dispatch; no analysed code is imported."""
import io
import json
import runpy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from debugger_fixtures import PROJECT, fixture_root, write_files

try:
    from macr.evals.static_baseline import run_baseline
except ModuleNotFoundError:
    run_baseline = None



class StaticBaselineTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(run_baseline, 'offline baseline entry missing')


    def test_real_cli_wrapper_runs_baseline_without_importing_source(self):
        root = fixture_root()
        write_files(root / 'source', {'entry.py':
            "raise RuntimeError('ANALYSED SOURCE MUST NOT EXECUTE')\n"
            'def start_run():\n    return compute(4)\n'
            'def compute(value):\n    return value / 0\n'})
        spec = {'symptom': 'Synthetic division failure; not executed.', 'symbol_names': ['start_run'],
                'reported_traceback': [{'file': 'entry.py', 'line': 5}]}
        (root / 'input.json').write_text(json.dumps(spec))
        output = io.StringIO()
        args = ['agent-review', 'static-baseline', '--input', str(root / 'input.json'),
                '--out', str(root / 'out'), '--json']
        with patch.object(sys, 'argv', args), patch.object(sys, 'stdout', output), \
             patch('subprocess.Popen', side_effect=AssertionError('no execution')):
            runpy.run_path(str(PROJECT / 'cli/agent_review.py'), run_name='__main__')
        report = json.loads(output.getvalue())
        self.assertEqual(report['answer']['refs'][0], {'file': 'entry.py', 'start': 5, 'end': 5})
        self.assertEqual(report['answer']['verdict'], 'unknown')
        self.assertTrue((root / 'out/report.json').is_file())


    def test_unseen_frames_do_not_create_unverified_citations(self):
        root = fixture_root()
        write_files(root / 'source', {'entry.py': 'def entry():\n    return 1\n'})
        (root / 'input.json').write_text(json.dumps({'symptom': 'Unverified report', 'symbol_names': ['entry'],
            'reported_traceback': [{'file': 'absent.py', 'line': 9}, {'file': 'entry.py', 'line': 99}]}))
        report = run_baseline(root / 'input.json', root / 'out')
        self.assertEqual(len(report['rejected_frames']), 2)
        self.assertEqual(report['answer']['refs'], [{'file': 'entry.py', 'start': 1, 'end': 2}])

    def test_strict_input_and_existing_output_fail_before_mutation(self):
        root = fixture_root()
        write_files(root / 'source', {'entry.py': 'def entry():\n    return 1\n'})
        path = root / 'input.json'
        for body in ['{"symptom":"a","symptom":"b"}',
                     json.dumps({'symptom': 'x', 'symbol_names': ['entry'], 'reported_traceback': [], 'answer': 'oracle'}),
                     json.dumps({'symptom': 'x', 'symbol_names': ['entry'],
                                 'reported_traceback': [{'file': 'entry.py', 'line': True}]}),
                     json.dumps({'symptom': 'x', 'symbol_names': ['entry'],
                                 'reported_traceback': [{'file': '../outside.py', 'line': 1}]})]:
            with self.subTest(body=body):
                path.write_text(body)
                with self.assertRaises(ValueError):
                    run_baseline(path, root / 'out')
                self.assertFalse((root / 'out').exists())
        path.write_text(json.dumps({'symptom': 'x', 'symbol_names': ['entry'], 'reported_traceback': []}))
        out = root / 'out';out.mkdir();(out / 'old-record').write_text('retain')
        with self.assertRaises(ValueError):
            run_baseline(path, out)
        self.assertEqual((out / 'old-record').read_text(), 'retain')
