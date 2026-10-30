"""Probe CLI boundaries using owned fixtures, dummy credentials, and no transport."""
import contextlib
import getpass
import importlib
import io
import json
import unittest
import warnings
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.evals.deepseek_study_profile import StudyError

try:
    core = importlib.import_module('macr.evals.deepseek_tool_name_probe')
except ModuleNotFoundError:
    core = None
try:
    cli = importlib.import_module('run_deepseek_tool_name_probe')
except ModuleNotFoundError:
    cli = None


class TTY(io.StringIO):
    def isatty(self):
        return True


class ProbeCliTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(core, 'the explicit probe core is not implemented')
        self.assertIsNotNone(cli, 'the explicit probe CLI is not implemented')
        self.root = fixture_root()

    def file(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def env(self, content, name='.env'):
        path = self.root / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding='utf-8')
        return path

    def invoke(self, args, *, stdin=None, stderr=None):
        out = io.StringIO()
        err = stderr if stderr is not None else io.StringIO()
        with (contextlib.redirect_stdout(out), contextlib.redirect_stderr(err),
              patch.object(cli.sys, 'stdin', stdin if stdin is not None else io.StringIO())):
            code = cli.main([str(value) for value in args])
        return code, out.getvalue(), err.getvalue()

    def approval(self, *, source=None):
        return self.file('approval.json', {'authorized': True,
            'credential_source': source or {'kind': 'terminal'}})

    def test_execute_disabled_before_approval_preflight_or_key(self):
        with (patch.object(cli, 'read_json') as read,
              patch.object(cli, 'preflight_probe') as preflight,
              patch.object(cli, 'execute_probe') as execute,
              patch.object(cli, '_read_key_without_echo') as terminal,
              patch.object(cli, '_read_project_key') as project):
            result = self.invoke(['execute', '--control', self.root,
                '--approval', self.root / 'missing.json'])
        self.assertEqual(result, (2, '', 'probe_cli_live_disabled\n'))
        for operation in (read, preflight, execute, terminal, project):
            operation.assert_not_called()

    def test_unapproved_preflight_stops_before_key_or_dispatch(self):
        approval = self.file('approval.json', {'authorized': False})
        with (patch.object(cli, 'preflight_probe',
                side_effect=StudyError('probe_approval_missing')) as preflight,
              patch.object(cli, 'execute_probe') as execute,
              patch.object(cli, '_read_key_without_echo') as terminal,
              patch.object(cli, '_read_project_key') as project):
            result = self.invoke(['execute', '--control', self.root,
                '--approval', approval, '--enable-live'])
        self.assertEqual(result, (2, '', 'probe_approval_missing\n'))
        preflight.assert_called_once_with(self.root, {'authorized': False})
        for operation in (execute, terminal, project):
            operation.assert_not_called()

    def test_terminal_source_requires_both_ttys_before_callback(self):
        approval = self.approval()
        with (patch.object(cli, 'preflight_probe', return_value={}),
              patch.object(cli, 'execute_probe') as execute,
              patch.object(cli, '_read_key_without_echo') as key):
            for stdin, stderr in ((TTY(), io.StringIO()), (io.StringIO(), TTY()),
                                  (io.StringIO(), io.StringIO())):
                with self.subTest(stdin_tty=stdin.isatty(), stderr_tty=stderr.isatty()):
                    result = self.invoke(['execute', '--control', self.root,
                        '--approval', approval, '--enable-live'], stdin=stdin, stderr=stderr)
                    self.assertEqual(result, (2, '', 'probe_cli_interactive_tty_required\n'))
        execute.assert_not_called()
        key.assert_not_called()

    def test_offline_metadata_commands_do_not_read_credentials(self):
        control = self.root / 'control'
        inputs = self.file('inputs.json', {'schema': 'synthetic-probe-inputs'})
        plan = {'plan_sha256': 'a' * 64, 'control_root': str(control), 'planned_cells': 2,
            'execution_enabled': False, 'model_calls_live': 0}
        approval = self.approval()
        source = {'kind': 'project_env', 'path': str(self.root / 'unread.env')}
        proposal = {'authorized': False, 'plan_sha256': 'a' * 64,
            'credential_source': source, 'max_requests': 2}
        with (patch.object(cli, 'prepare_probe', return_value=plan) as prepare,
              patch.object(cli, 'propose_probe_approval', return_value=proposal) as propose,
              patch.object(cli, 'preflight_probe', return_value=plan) as preflight,
              patch.object(cli, 'execute_probe') as execute,
              patch.object(cli, '_read_key_without_echo') as terminal,
              patch.object(cli, '_read_project_key') as project):
            code, out, err = self.invoke(['prepare', '--inputs', inputs, '--out', control])
            self.assertEqual((code, json.loads(out), err), (0, plan, ''))
            prepare.assert_called_once_with(inputs, out=control, project_root=cli.PROJECT)
            for extra, expected_source in (([], None),
                    (['--project-env', source['path']], source)):
                code, out, err = self.invoke(['proposal', '--control', control, *extra])
                self.assertEqual((code, json.loads(out), err), (0, proposal, ''))
                self.assertEqual(propose.call_args.args, (control,))
                self.assertEqual(propose.call_args.kwargs, {'credential_source': expected_source})
            code, out, err = self.invoke(['preflight', '--control', control, '--approval', approval])
        self.assertEqual((code, json.loads(out), err), (0, {'plan_sha256': 'a' * 64,
            'preflight': 'passed', 'execution_enabled': False, 'model_calls_live': 0}, ''))
        preflight.assert_called_once_with(control, {'authorized': True,
            'credential_source': {'kind': 'terminal'}})
        for operation in (execute, terminal, project):
            operation.assert_not_called()

    def test_project_reader_is_bound_to_approval_and_delayed_until_core_callback(self):
        path = self.env('DEEPSEEK_API_KEY=DUMMY_PROJECT_CREDENTIAL\n')
        approved = {'authorized': True, 'credential_source': {'kind': 'project_env', 'path': str(path)}}
        approval = self.file('approval.json', approved)
        events = []
        original_read = cli.read_json
        def read(*args, **kwargs):
            events.append('approval')
            return original_read(*args, **kwargs)
        def preflight(control, value):
            self.assertEqual(value, approved)
            events.append('preflight')
            return {}
        def reader(value):
            self.assertEqual(value, path)
            events.append('key')
            return 'DUMMY_PROJECT_CREDENTIAL'
        def execute(control, value, **kwargs):
            self.assertEqual(events, ['approval', 'preflight'])
            self.assertEqual((control, value, kwargs['enabled']), (self.root, approved, True))
            events.append('core_claim')
            self.assertEqual(kwargs['key_reader'](), 'DUMMY_PROJECT_CREDENTIAL')
            return {'model_calls_live': 1, 'batch_halted': False,
                'answer': 'DUMMY_PROJECT_CREDENTIAL',
                'requests': [{'Authorization': 'Bearer DUMMY_PROJECT_CREDENTIAL'}]}
        with (patch.object(cli, 'read_json', side_effect=read),
              patch.object(cli, 'preflight_probe', side_effect=preflight),
              patch.object(cli, 'execute_probe', side_effect=execute) as dispatch,
              patch.object(cli, '_read_project_key', side_effect=reader) as key,
              patch.object(cli, '_read_key_without_echo') as terminal):
            code, out, err = self.invoke(['execute', '--control', self.root,
                '--approval', approval, '--enable-live'])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(events, ['approval', 'preflight', 'core_claim', 'key'])
        self.assertNotIn('DUMMY_PROJECT_CREDENTIAL', out + err)
        dispatch.assert_called_once()
        key.assert_called_once_with(path)
        terminal.assert_not_called()

    def test_terminal_no_echo_reader_is_reused_and_delayed_until_core_callback(self):
        live = importlib.import_module('run_deepseek_study_live')
        self.assertIs(cli._read_key_without_echo, live._read_key_without_echo)
        approval = self.approval()
        def execute(*args, **kwargs):
            key.assert_not_called()
            self.assertEqual(kwargs['key_reader'](), 'DUMMY_TERMINAL_CREDENTIAL')
            return {'model_calls_live': 1, 'batch_halted': False,
                'source': 'DUMMY_TERMINAL_CREDENTIAL'}
        with (patch.object(cli, 'preflight_probe', return_value={}),
              patch.object(cli, '_read_key_without_echo', return_value='DUMMY_TERMINAL_CREDENTIAL') as key,
              patch.object(cli, 'execute_probe', side_effect=execute) as dispatch):
            code, out, err = self.invoke(['execute', '--control', self.root,
                '--approval', approval, '--enable-live'], stdin=TTY(), stderr=TTY())
        self.assertEqual((code, err), (0, ''))
        self.assertNotIn('DUMMY_TERMINAL_CREDENTIAL', out + err)
        key.assert_called_once_with()
        dispatch.assert_called_once()

    def test_getpass_fallback_does_not_consume_input_or_reveal_details(self):
        approval = self.approval()
        stdin = TTY('DUMMY_UNREAD_INPUT\n')
        def warning_then_input(*args, **kwargs):
            warnings.warn('DUMMY_PRIVATE_TERMINAL_DETAIL', getpass.GetPassWarning)
            return input()
        def execute(*args, **kwargs):
            kwargs['key_reader']()
            self.fail('no-echo failure must stop dispatch')
        with (patch.object(cli, 'preflight_probe', return_value={}),
              patch.object(cli, 'execute_probe', side_effect=execute) as dispatch,
              patch.object(getpass, 'getpass', side_effect=warning_then_input)):
            result = self.invoke(['execute', '--control', self.root,
                '--approval', approval, '--enable-live'], stdin=stdin, stderr=TTY())
        self.assertEqual(result, (2, '', 'study_live_no_echo_input_unavailable\n'))
        self.assertEqual(stdin.tell(), 0)
        dispatch.assert_called_once()

    def test_live_and_dryrun_outputs_expose_only_safe_summary(self):
        approval = self.approval(source={'kind': 'project_env', 'path': str(self.root / 'unread.env')})
        scripts = self.file('scripts.json', {'cell-1': ['timeout']})
        result = {'plan_sha256': 'b' * 64, 'model_calls_live': 0, 'batch_halted': True,
            'answer': 'DUMMY_RAW_ANSWER', 'requests': ['DUMMY_RAW_REQUEST'],
            'source': 'DUMMY_SOURCE_TEXT', 'reasoning': 'DUMMY_REASONING_TEXT',
            'unsafe_extra': 'DUMMY_UNREVIEWED_FIELD', 'rows': [{'cell_id': 'cell-1',
                'status': 'failed', 'answer': 'DUMMY_NESTED_ANSWER',
                'requests': ['DUMMY_NESTED_REQUEST'], 'source': 'DUMMY_NESTED_SOURCE',
                'reasoning': 'DUMMY_NESTED_REASONING'}]}
        with (patch.object(cli, 'preflight_probe', return_value={}),
              patch.object(cli, 'execute_probe', return_value=result),
              patch.object(cli, 'run_probe_dryrun', return_value=result),
              patch.object(cli, '_read_project_key') as key):
            for args, expected_code in ((['execute', '--control', self.root,
                    '--approval', approval, '--enable-live'], 1),
                    (['dry-run', '--control', self.root, '--scripts', scripts], 0)):
                with self.subTest(command=args[0]):
                    code, out, err = self.invoke(args)
                    self.assertEqual((code, err), (expected_code, ''))
                    summary = json.loads(out)
                    self.assertEqual(summary['model_calls_live'], 0)
                    self.assertTrue(summary['batch_halted'])
                    self.assertNotIn('DUMMY_', out + err)
        key.assert_not_called()

    def test_scripted_replies_are_only_available_to_dryrun(self):
        scripts = self.file('scripts.json', {'cell-1': [{'literal': 'fixture'}, 'timeout']})
        with (patch.object(cli, 'run_probe_dryrun', return_value={
                'model_calls_live': 0, 'batch_halted': False}) as dryrun,
              patch.object(cli, 'execute_probe') as execute,
              patch.object(cli, '_read_project_key') as key):
            code, out, err = self.invoke(['dry-run', '--control', self.root, '--scripts', scripts])
            self.assertEqual((code, json.loads(out), err),
                (0, {'model_calls_live': 0, 'batch_halted': False}, ''))
            dryrun.assert_called_once_with(self.root,
                scripted_by_cell={'cell-1': ({'literal': 'fixture'}, 'timeout')})
            for extra in (['--scripts', scripts], ['--api-key', 'DUMMY_ARG_CREDENTIAL'],
                          ['--project-env', self.root / 'dummy.env']):
                result = self.invoke(['execute', '--control', self.root,
                    '--approval', self.root / 'approval.json', '--enable-live', *extra])
                self.assertEqual(result, (2, '', 'probe_cli_arguments_invalid\n'))
        execute.assert_not_called()
        key.assert_not_called()

    def test_invalid_or_duplicate_script_json_is_not_dispatched(self):
        with patch.object(cli, 'run_probe_dryrun') as dryrun:
            for value in ({'cell-1': 'timeout'}, {'cell-1': []}, []):
                with self.subTest(value=value):
                    scripts = self.file('scripts.json', value)
                    result = self.invoke(['dry-run', '--control', self.root, '--scripts', scripts])
                    self.assertEqual(result, (2, '', 'probe_cli_script_invalid\n'))
            scripts = self.env('{"cell-1":["timeout"],"cell-1":["timeout"]}', 'scripts.json')
            result = self.invoke(['dry-run', '--control', self.root, '--scripts', scripts])
            self.assertEqual(result, (2, '', 'study_json_invalid\n'))
        dryrun.assert_not_called()

    def test_fixed_error_codes_and_interrupt_never_retry_or_leak(self):
        for error, expected in ((StudyError('probe_approval_missing'), 'probe_approval_missing'),
                (OSError('DUMMY_PRIVATE_IO_DETAIL'), 'probe_cli_io_invalid')):
            with self.subTest(error=type(error).__name__):
                with patch.object(cli, 'propose_probe_approval', side_effect=error) as proposal:
                    result = self.invoke(['proposal', '--control', self.root])
                self.assertEqual(result, (2, '', expected + '\n'))
                proposal.assert_called_once()
        approval = self.approval(source={'kind': 'project_env', 'path': str(self.root / 'unread.env')})
        with (patch.object(cli, 'preflight_probe', return_value={}),
              patch.object(cli, 'execute_probe', side_effect=KeyboardInterrupt) as execute,
              patch.object(cli, '_read_project_key') as key):
            result = self.invoke(['execute', '--control', self.root,
                '--approval', approval, '--enable-live'])
        self.assertEqual(result, (130, '', 'probe_cli_interrupted_claim_retained\n'))
        execute.assert_called_once()
        key.assert_not_called()

    def test_project_reader_accepts_only_dummy_valid_service_values(self):
        for model in ('deepseek-flash', 'deepseek-v4-flash'):
            with self.subTest(model=model):
                path = self.env('# synthetic settings\nDEEPSEEK_API_KEY=DUMMY_VALID_KEY\n'
                    'DEEPSEEK_BASE_URL=https://api.deepseek.com/\nDEEPSEEK_MODEL=' + model + '\n'
                    'UNRELATED_SETTING=DUMMY_IGNORED_VALUE\n')
                self.assertEqual(cli._read_project_key(path), 'DUMMY_VALID_KEY')
        path = self.env('DEEPSEEK_API_KEY=' + 'x' * 4096 + '\n'
            'DEEPSEEK_BASE_URL=https://api.deepseek.com\nDEEPSEEK_MODEL=deepseek-flash\n')
        self.assertEqual(cli._read_project_key(path), 'x' * 4096)

    def test_project_reader_rejects_symlink_nonregular_and_unavailable_sources(self):
        regular = self.env('DEEPSEEK_API_KEY=DUMMY_VALID_KEY\n')
        link = self.root / 'linked' / '.env'
        link.parent.mkdir()
        link.symlink_to(regular)
        self.addCleanup(link.unlink)
        directory = self.root / 'directory' / '.env'
        directory.mkdir(parents=True)
        for path, code in ((link, 'probe_cli_key_source_invalid'),
                (directory, 'probe_cli_key_source_invalid'),
                (self.root / 'missing' / '.env', 'probe_cli_key_source_unavailable')):
            with self.subTest(kind=path.parent.name):
                with self.assertRaises(StudyError) as error:
                    cli._read_project_key(path)
                self.assertEqual(error.exception.code, code)
                self.assertNotIn('DUMMY_VALID_KEY', str(error.exception))

    def test_project_reader_rejects_malformed_oversize_mismatched_or_invalid_values(self):
        valid = ('DEEPSEEK_API_KEY=DUMMY_KEY\nDEEPSEEK_BASE_URL=https://api.deepseek.com\n'
            'DEEPSEEK_MODEL=deepseek-flash\n')
        cases = [
            ('DEEPSEEK_API_KEY=DUMMY_ONE\nDEEPSEEK_API_KEY=DUMMY_TWO\n', 'source_invalid'),
            ('DEEPSEEK_API_KEY=DUMMY_KEY\nDEEPSEEK_BASE_URL=https://api.deepseek.com\n'
                'DEEPSEEK_BASE_URL=https://api.deepseek.com\n', 'source_invalid'),
            ('DEEPSEEK_API_KEY=DUMMY_KEY\nDEEPSEEK_MODEL=deepseek-flash\n'
                'DEEPSEEK_MODEL=deepseek-flash\n', 'source_invalid'),
            (b'DEEPSEEK_API_KEY=DUMMY_KEY\n\xff', 'source_invalid'),
            ('#' + 'x' * 32768, 'source_invalid'),
            (valid.replace('https://api.deepseek.com', 'https://example.invalid'), 'service_mismatch'),
            (valid.replace('https://api.deepseek.com', 'https://api.deepseek.com/extra'), 'service_mismatch'),
            (valid.replace('https://api.deepseek.com', 'https://api.deepseek.com//'), 'service_mismatch'),
            (valid.replace('deepseek-flash', 'other-model'), 'service_mismatch'),
            (valid.replace('DUMMY_KEY', ''), 'invalid'),
            (valid.replace('DUMMY_KEY', 'DUMMY KEY'), 'invalid'),
            (valid.replace('DUMMY_KEY', 'DUMMY_非ASCII'), 'invalid'),
            (valid.replace('DUMMY_KEY', 'x' * 4097), 'invalid'),
        ]
        for number, (content, suffix) in enumerate(cases):
            with self.subTest(case=number):
                path = self.env(content)
                with self.assertRaises(StudyError) as error:
                    cli._read_project_key(path)
                self.assertEqual(error.exception.code, 'probe_cli_key_' + suffix)
                self.assertNotIn('DUMMY_', str(error.exception))


if __name__ == '__main__':
    unittest.main()
