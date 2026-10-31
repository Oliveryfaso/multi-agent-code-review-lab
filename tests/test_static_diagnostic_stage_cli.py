"""Default-off stage entry; credentials and provider dispatch stay behind approval."""
from contextlib import redirect_stderr, redirect_stdout
import importlib
import io
import json
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root

try:
    command = importlib.import_module('run_static_diagnostic_stage')
except ModuleNotFoundError:
    command = None


class StaticDiagnosticStageCLITests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(command, 'explicit default-off stage command is missing')
        self.root = fixture_root()

    def invoke(self, args):
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            code = command.main(args)
        return code, out.getvalue(), err.getvalue()

    def file(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value))
        return str(path)

    def test_disabled_execute_does_not_open_approval_or_read_credentials(self):
        with patch.object(command, 'preflight_stage') as preflight, \
             patch.object(command, '_credential_reader') as reader, \
             patch.object(command, 'execute_stage') as execute:
            code, out, err = self.invoke(['execute', '--control', str(self.root),
                '--approval', str(self.root / 'absent-private-approval.json')])
        self.assertEqual(code, 2)
        self.assertIn('static_stage_cli_live_disabled', err)
        self.assertEqual(out, '')
        preflight.assert_not_called(); reader.assert_not_called(); execute.assert_not_called()

    def test_preflight_never_reads_key_and_prints_only_safe_hashes(self):
        approval = self.file('approval.json', {'fixture': True})
        with patch.object(command, 'preflight_stage', return_value={
                'plan_sha256': 'a' * 64, 'private': 'private-sentinel'}) as preflight, \
             patch.object(command, '_credential_reader') as reader, \
             patch.object(command, 'execute_stage') as execute:
            code, out, err = self.invoke(['preflight', '--control', str(self.root), '--approval', approval])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out), {'plan_sha256': 'a' * 64, 'preflight': 'passed',
            'execution_enabled': False, 'model_calls_live': 0})
        self.assertNotIn('private-sentinel', out)
        self.assertEqual(preflight.call_count, 1); reader.assert_not_called(); execute.assert_not_called()

    def test_dry_run_accepts_one_envelope_per_cell_without_credential_reader(self):
        replies = self.file('replies.json', {'cell-a': {'choices': []}, 'cell-b': 'timeout'})
        with patch.object(command, 'run_dryrun', return_value={
                'actual_requests': 2, 'model_calls_live': 0, 'fixture_model_calls': 2,
                'stage_halted': True, 'private': 'private-sentinel'}) as run, \
             patch.object(command, '_credential_reader') as reader:
            code, out, _ = self.invoke(['dry-run', '--control', str(self.root), '--replies', replies])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.kwargs['scripted_by_cell']['cell-b'], 'timeout')
        self.assertNotIn('private-sentinel', out)
        self.assertEqual(json.loads(out)['model_calls_live'], 0)
        reader.assert_not_called()

    def test_script_rejects_lists_multiple_responses_and_unknown_literal_before_owner(self):
        for value in ({'cell-a': [{}]}, {'cell-a': 'retry'}, {'cell-a': 1}, {'': {}}):
            replies = self.file('bad.json', value)
            with self.subTest(value=value), patch.object(command, 'run_dryrun') as run:
                code, out, err = self.invoke(['dry-run', '--control', str(self.root), '--replies', replies])
            self.assertEqual(code, 2); self.assertEqual(out, '')
            self.assertIn('static_stage_cli_script_invalid', err); run.assert_not_called()

    def test_enabled_execute_passes_callback_without_consuming_it_and_redacts_summary(self):
        approval = self.file('approval.json', {'fixture': True})
        secret_reader = lambda: self.fail('entry consumed credentials before owner claimed the stage')
        with patch.object(command, 'preflight_stage', return_value={'plan_sha256': 'a' * 64}), \
             patch.object(command, '_credential_reader', return_value=secret_reader), \
             patch.object(command, 'execute_stage', return_value={
                'stage_halted': True, 'actual_requests': 1, 'model_calls_live': 1,
                'known_accrued_usd': '0.001', 'new_unknown_held_usd': '0',
                'response': 'private-sentinel', 'rows': [{'cell_id': 'a',
                    'status': 'failed', 'response': 'private-sentinel'}]}) as run:
            code, out, _ = self.invoke(['execute', '--control', str(self.root),
                '--approval', approval, '--enable-live'])
        self.assertEqual(code, 1)
        self.assertIs(run.call_args.kwargs['key_reader'], secret_reader)
        self.assertTrue(run.call_args.kwargs['enabled'])
        self.assertNotIn('private-sentinel', out)

    def test_arguments_and_input_errors_do_not_echo_values(self):
        code, out, err = self.invoke(['--private-sentinel'])
        self.assertEqual(code, 2); self.assertEqual(out, '')
        self.assertNotIn('private-sentinel', err)
        approval = self.root / 'duplicate.json'
        approval.write_text('{"x":1,"x":2}')
        with patch.object(command, 'preflight_stage') as preflight:
            code, out, err = self.invoke(['preflight', '--control', str(self.root),
                '--approval', str(approval)])
        self.assertEqual(code, 2); self.assertEqual(out, ''); preflight.assert_not_called()

    def test_help_is_inert(self):
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as end:
            command.main(['--help'])
        self.assertEqual(end.exception.code, 0)
