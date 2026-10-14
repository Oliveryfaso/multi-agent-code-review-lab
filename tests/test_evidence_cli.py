"""CLI contracts with collect mocked; no child analysis or native monitoring."""
import argparse
import contextlib
import importlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from macr.investigation.evidence_profiles import EvidenceProfile
from macr.investigation.evidence_records import StaticEvidenceReport

class EvidenceCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='macr-cli-')
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.out = self.root / 'new-run'
        self.base = ['--repo', str(self.repo), '--out', str(self.out), '--question', 'PRIVATE_QUESTION', '--entry', 'app.py:run', '--json']
        self.addCleanup(self.temp.cleanup)

    def invoke(self, flags=None, report=None):
        from macr import evidence_cli
        from macr.investigation import evidence_supervisor
        parser = argparse.ArgumentParser()
        subs = parser.add_subparsers(dest='command')
        evidence_cli.add_parser(subs)
        args = parser.parse_args(['evidence-locate'] + (self.base if flags is None else flags))
        stdout, stderr = io.StringIO(), io.StringIO()
        def collected(request):
            if report is not None:
                return report(request) if callable(report) else report
            return StaticEvidenceReport(request, 'partial', run={'status': 'partial', 'publication': 'uncommitted'}, limits={'effective': request.profile.to_dict(), 'exceeded': []})
        with patch.object(evidence_supervisor, 'collect_evidence', side_effect=collected) as collect, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = evidence_cli.run(args)
        return code, stdout.getvalue(), stderr.getvalue(), collect

    def test_outer_help_contains_new_command(self):
        from macr import cli
        out = io.StringIO()
        with patch.object(sys, 'argv', ['agent-review', '--help']), contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as stopped:
            cli.main()
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn('evidence-locate', out.getvalue())

    def test_one_json_and_only_public_collect(self):
        code, out, err, collect = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)['status'], 'partial')
        self.assertEqual(collect.call_count, 1)
        self.assertEqual(err, '')
        self.assertFalse(self.out.exists())

    def test_precedence_raised_limits_and_trace(self):
        config = self.root / 'budget.json'
        config.write_text('{"max_entries":10,"max_body_bytes":999999,"max_question_bytes":20000}')
        question = self.root / 'question.txt'; question.write_text('q' * 9000)
        trace = self.root / 'trace.txt'; trace.write_text('File "app.py", line 3')
        flags = ['--repo', str(self.repo), '--out', str(self.out), '--question-file', str(question), '--symbol', 'run', '--traceback-file', str(trace), '--profile', 'larger', '--budget-file', str(config), '--limit', 'max_entries=12', '--limit', 'active_seconds=300.5', '--json']
        code, out, err, collect = self.invoke(flags)
        request = collect.call_args.args[0]
        self.assertEqual(code, 1)
        self.assertEqual(request.profile.name, 'larger')
        self.assertEqual(request.profile.max_entries, 12)
        self.assertEqual(request.profile.max_body_bytes, 999999)
        self.assertEqual(request.profile.active_seconds, 300.5)
        self.assertEqual(request.question, 'q' * 9000)
        self.assertEqual(request.traceback, trace.read_text())

    def test_invalid_numeric_nondispatch(self):
        for value in ('unknown=3', 'max_entries=true', 'max_entries=1.5', 'max_entries=0', 'active_seconds=nan', 'max_entries=-1', 'name=standard', 'max_entries', 'max_entries=2=3'):
            with self.subTest(value=value):
                code, out, err, collect = self.invoke(self.base + ['--limit', value])
                self.assertEqual(code, 2)
                self.assertEqual(json.loads(out)['status'], 'rejected')
                self.assertEqual(collect.call_count, 0)
                self.assertNotIn(value, err)
                self.assertNotIn('PRIVATE_QUESTION', err)

    def test_strict_budget_nondispatch(self):
        for text in ('{"max_entries":2,"max_entries":3}', '{"max_entries":true}', '{"unknown":1}', '{"active_seconds":NaN}', '[]', '{"exclude_patterns":[["x"]]}'):
            with self.subTest(text=text):
                config = self.root / 'budget.json'; config.write_text(text)
                code, out, err, collect = self.invoke(self.base + ['--budget-file', str(config)])
                self.assertEqual(code, 2)
                self.assertEqual(collect.call_count, 0)
                self.assertEqual(json.loads(out)['status'], 'rejected')
                self.assertNotIn(text, err)

    def test_invalid_entry_and_count_before_dispatch(self):
        for extra in (['--entry', '../outside.py'], ['--symbol', 'bad symbol'], ['--entry', 'app.py:0'], ['--entry', 'x.py'] * 6):
            code, out, err, collect = self.invoke(self.base + extra)
            self.assertIn(code, (2, 3))
            self.assertEqual(collect.call_count, 0)
            self.assertFalse(self.out.exists())

    def test_auxiliary_symlink_directory_protected_and_bounds(self):
        plain = self.root / 'plain.txt'; plain.write_text('q' * 20)
        link = self.root / 'link.txt'; link.symlink_to(plain)
        private = self.root / '.codex'; private.mkdir(); secret = private / 'input.txt'; secret.write_text('secret')
        for path, expected in ((link, 3), (self.repo, 3), (secret, 3), (plain, 2)):
            flags = ['--repo', str(self.repo), '--out', str(self.out), '--question-file', str(path), '--entry', 'app.py', '--limit', 'max_question_bytes=10', '--json']
            code, out, err, collect = self.invoke(flags)
            self.assertEqual(code, expected)
            self.assertEqual(collect.call_count, 0)
            self.assertFalse(self.out.exists())

    def test_utf8_input_strict(self):
        question = self.root / 'question.txt'; question.write_bytes(b'\xff')
        flags = ['--repo', str(self.repo), '--out', str(self.out), '--question-file', str(question), '--entry', 'app.py', '--json']
        code, out, err, collect = self.invoke(flags)
        self.assertEqual(code, 2); self.assertEqual(collect.call_count, 0)

    def test_exit_matrix_and_diagnostic_privacy(self):
        cases = [('completed', [], 0), ('partial', [], 1), ('rejected', [{'code':'input_invalid'}], 2), ('rejected', [{'code':'forbidden_path'}], 3), ('failed', [{'code':'unsupported_environment'}], 3), ('failed', [{'code':'snapshot_unstable'}], 3), ('failed', [{'code':'worker_failed'}], 4), ('cancelled', [{'code':'cancelled'}], 130)]
        for status, errors, expected in cases:
            def report(request):
                return StaticEvidenceReport(request, status, source_version={'content_id':'test'} if status == 'completed' else None, errors=errors)
            code, out, err, collect = self.invoke(report=report)
            self.assertEqual(code, expected)
            self.assertEqual(json.loads(out)['status'], status)
            self.assertNotIn('PRIVATE_QUESTION', err)
            self.assertNotIn(str(self.root), err)

    def test_unknown_closure_is_internal_even_cancelled(self):
        code, out, err, _ = self.invoke(report=lambda req: StaticEvidenceReport(req, 'cancelled', run={'worker_started':True, 'worker_exit_confirmed':False}, errors=[{'code':'cancelled'}]))
        self.assertEqual(code, 4)

    def test_tiny_cap_empty_stdout_honest_boundary(self):
        def report(req):
            return StaticEvidenceReport(None, 'failed', run={'failure_detail_status':'report_cap_unrepresentable'}, errors=[{'code':'unsupported_environment'}, {'code':'output_limit_unrepresentable'}], limits={'effective':req.profile.to_dict()})
        code, out, err, _ = self.invoke(self.base + ['--limit', 'max_report_bytes=1'], report=report)
        self.assertEqual(code, 4)
        self.assertEqual(out, '')
        self.assertIn('output_limit_unrepresentable', err)

    def test_render_overflow_never_enlarges_cap(self):
        def report(req):
            return StaticEvidenceReport(None, 'failed', errors=[{'code':'worker_failed'}], limits={'effective':req.profile.to_dict()}, run={'padding':'x' * 1000})
        code, out, err, _ = self.invoke(self.base + ['--limit', 'max_report_bytes=20'], report=report)
        self.assertEqual(code, 4); self.assertEqual(out, '')

    def test_postcommit_output_interrupt_retains_authority(self):
        from macr import evidence_cli
        from macr.investigation import evidence_supervisor
        report = StaticEvidenceReport(None, 'failed', run={'publication':'committed'}, errors=[{'code':'worker_failed'}])
        parser = argparse.ArgumentParser(); subs = parser.add_subparsers(); evidence_cli.add_parser(subs)
        args = parser.parse_args(['evidence-locate'] + self.base)
        with patch.object(evidence_supervisor, 'collect_evidence', return_value=report), patch('builtins.print', side_effect=KeyboardInterrupt), patch.object(sys, 'stderr', io.StringIO()):
            self.assertEqual(evidence_cli.run(args), 130)
        self.assertEqual(report.run['publication'], 'committed')

    def test_aux_raised_decode_bound(self):
        config = self.root / 'budget.json'; config.write_text(' ' * 140000 + '{"max_entries":7}')
        code, out, err, collect = self.invoke(self.base + ['--limit', 'max_input_bytes=200000', '--budget-file', str(config)])
        self.assertEqual(code, 1); self.assertEqual(collect.call_args.args[0].profile.max_entries, 7)

    def test_terminal_summary_escapes_controls(self):
        flags = [flag for flag in self.base if flag != '--json']
        code, out, err, _ = self.invoke(flags)
        self.assertEqual(code, 1)
        self.assertIn('not_performed', out)
        self.assertNotIn('PRIVATE_QUESTION', out)

    # These tests target retained budget authority and CLI publication boundaries.
    def test_budget_file_cap_retained_on_question_rejection(self):
        config = self.root / 'budget.json'; config.write_text('{"max_report_bytes":1}')
        flags = ['--repo', str(self.repo), '--out', str(self.out), '--question', '', '--entry', 'app.py', '--budget-file', str(config), '--json']
        code, out, err, collect = self.invoke(flags)
        self.assertEqual(out, '')
        self.assertEqual(code, 4)
        self.assertEqual(collect.call_count, 0)

    def test_compact_representable_json_after_pretty_overflow(self):
        def report(req):
            return StaticEvidenceReport(None, 'failed', errors=[{'code':'worker_failed'}], limits={'effective': {'max_report_bytes':650}})
        code, out, err, collect = self.invoke(report=report)
        self.assertTrue(out)
        self.assertLessEqual(len(out.encode()), 650)
        self.assertEqual(json.loads(out)['status'], 'failed')

    def test_terminal_known_cap_honored(self):
        code, out, err, _ = self.invoke([flag for flag in self.base if flag != '--json'] + ['--limit', 'max_markdown_bytes=1'])
        self.assertEqual(out, ''); self.assertEqual(code, 4)

    def test_eval_lazy_imports_keep_orchestrator(self):
        import ast
        from macr import cli
        tree = ast.parse(Path(cli.__file__).read_text())
        branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If) and ast.unparse(node.test) == "args.command == 'eval'")
        imports = [node for node in branch.body if isinstance(node, ast.ImportFrom)]
        self.assertTrue(any(alias.name == 'Orchestrator' for node in imports for alias in node.names))

class RelativeAPIIntakeTests(unittest.TestCase):
    def test_relative_roots_cloned_before_control_and_reopen_elsewhere(self):
        import os
        from types import SimpleNamespace
        from macr.investigation import evidence_supervisor as supervisor
        from macr.investigation.evidence_records import EvidenceRequest
        from macr.investigation.evidence_workspace import EvidenceWorkspace
        with tempfile.TemporaryDirectory(prefix='macr-api-path-') as folder:
            root = Path(folder); (root / 'repo').mkdir(); (root / 'trusted-cwd').mkdir()
            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                req = EvidenceRequest('repo', 'out', 'question', ['a.py'], profile=EvidenceProfile.select(overrides={'min_free_bytes':1}))
                process = SimpleNamespace(pid=999001, returncode=4, poll=lambda:4, wait=lambda timeout=None:4, stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=io.BytesIO())
                captured = {}
                def communication(process, payload, budget, monitor, workspace):
                    raw = json.loads(payload); captured.update(raw['request'])
                    os.chdir(root / 'trusted-cwd')
                    try:
                        reopened = EvidenceWorkspace.reopen(raw['request']['repo'], raw['request']['out'], req.profile, raw['token'])
                        self.assertEqual(reopened.identity, workspace.identity)
                    finally: os.chdir(root)
                    return json.dumps({'schema_version':1, 'kind':'worker_failure', 'error':{'code':'worker_failed','field':'unit','stage':'unit','exceeded':None},'usage':{}}).encode()
                with patch.object(supervisor, '_observe_pressure'), patch.object(supervisor.subprocess, 'Popen', return_value=process), patch.object(supervisor, '_communicate', side_effect=communication):
                    report = supervisor.collect_evidence(req)
                self.assertEqual(captured['repo'], str(root / 'repo'))
                self.assertEqual(captured['out'], str(root / 'out'))
                self.assertEqual((req.repo, req.out), ('repo', 'out'))
                self.assertIsNot(report.request, req)
                self.assertEqual(report.request.repo, str(root / 'repo'))
                self.assertFalse((root / 'out/COMMITTED').exists())
            finally: os.chdir(original_cwd)

if __name__ == '__main__':
    unittest.main()
