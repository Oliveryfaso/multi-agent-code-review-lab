"""Whole-stage CLI boundaries with synthetic metadata and no live transport."""
import contextlib
import importlib
import io
import json
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.evals.deepseek_study_profile import StudyError

try:
    core = importlib.import_module('macr.evals.deepseek_value_stage')
except ModuleNotFoundError:
    core = None
try:
    cli = importlib.import_module('run_deepseek_value_stage')
except ModuleNotFoundError:
    cli = None


class TTY(io.StringIO):
    def isatty(self):
        return True


class ValueCliTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(core, 'the whole-stage core is not implemented')
        self.assertIsNotNone(cli, 'the whole-stage CLI is not implemented')
        self.root = fixture_root()

    def file(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def approval(self, source=None):
        return self.file('approval.json', {'authorized': True,
            'credential_source': source or {'kind': 'terminal'}})

    def invoke(self, args, *, stdin=None, stderr=None):
        out = io.StringIO()
        err = stderr if stderr is not None else io.StringIO()
        with (contextlib.redirect_stdout(out), contextlib.redirect_stderr(err),
              patch.object(cli.sys, 'stdin', stdin if stdin is not None else io.StringIO())):
            code = cli.main([str(value) for value in args])
        return code, out.getvalue(), err.getvalue()

    def scripts(self):
        return self.file('scripts.json', {'S0': {'dev-finish': [{'literal': 'fixture'}],
            'dev-ask': ['timeout']}, 'S1': {'cell-1': ['timeout']}})

    def test_execute_disabled_before_approval_preflight_or_credentials(self):
        with (patch.object(cli, 'read_json') as read,
              patch.object(cli, 'preflight_value_stage') as preflight,
              patch.object(cli, 'execute_value_stage') as execute,
              patch.object(cli, '_read_project_key') as project,
              patch.object(cli, '_read_key_without_echo') as terminal):
            result = self.invoke(['execute', '--control', self.root,
                '--approval', self.root / 'absent.json'])
        self.assertEqual(result, (2, '', 'value_cli_live_disabled\n'))
        for operation in (read, preflight, execute, project, terminal):
            operation.assert_not_called()

    def test_unapproved_preflight_stops_before_source_selection_or_dispatch(self):
        approval = self.file('approval.json', {'authorized': False})
        with (patch.object(cli, 'preflight_value_stage',
                side_effect=StudyError('value_approval_missing')) as preflight,
              patch.object(cli, 'execute_value_stage') as execute,
              patch.object(cli, '_read_project_key') as project,
              patch.object(cli, '_read_key_without_echo') as terminal):
            result = self.invoke(['execute', '--control', self.root,
                '--approval', approval, '--enable-live'])
        self.assertEqual(result, (2, '', 'value_approval_missing\n'))
        preflight.assert_called_once_with(self.root, {'authorized': False})
        for operation in (execute, project, terminal):
            operation.assert_not_called()

    def test_terminal_source_requires_stdin_and_stderr_tty(self):
        approval = self.approval()
        with (patch.object(cli, 'preflight_value_stage', return_value={}),
              patch.object(cli, 'execute_value_stage') as execute,
              patch.object(cli, '_read_key_without_echo') as key):
            for stdin, stderr in ((TTY(), io.StringIO()), (io.StringIO(), TTY()),
                                  (io.StringIO(), io.StringIO())):
                with self.subTest(stdin_tty=stdin.isatty(), stderr_tty=stderr.isatty()):
                    result = self.invoke(['execute', '--control', self.root,
                        '--approval', approval, '--enable-live'], stdin=stdin, stderr=stderr)
                    self.assertEqual(result, (2, '', 'value_cli_interactive_tty_required\n'))
        execute.assert_not_called()
        key.assert_not_called()

    def test_prepare_proposal_and_preflight_are_metadata_only(self):
        custodian = {'schema': 'macr.deepseek.study.custodian.v1', 'version': 'dummy',
            'gold_sha256': 'a' * 64, 'rubric_sha256': 'b' * 64,
            'qualification_statement': 'Synthetic hash-only fixture, not research qualification.'}
        record = self.file('custodian.json', custodian)
        pack = self.root / 'neutral'
        control = self.root / 'control'
        plan = {'plan_sha256': 'c' * 64, 'control_root': str(control),
            'max_requests': 60, 'max_usd': '2.000000', 'budgets': {
                'S0': {'max_requests': 6, 'max_usd': '0.500000'},
                'S1': {'max_requests': 54, 'max_usd': '1.500000'}},
            'question': 'DUMMY_UNSAFE_QUESTION', 'source': 'DUMMY_UNSAFE_SOURCE'}
        source = {'kind': 'project_env', 'path': str(self.root / '.env')}
        proposal = {'authorized': False, 'plan_sha256': 'c' * 64,
            'credential_source': source, 'max_requests': 60, 'max_usd': '2.000000'}
        approval = self.approval()
        with (patch.object(cli, 'prepare_value_stage', return_value=plan) as prepare,
              patch.object(cli, 'propose_value_approval', return_value=proposal) as propose,
              patch.object(cli, 'preflight_value_stage', return_value=plan) as preflight,
              patch.object(cli, 'execute_value_stage') as execute,
              patch.object(cli, '_read_project_key') as project,
              patch.object(cli, '_read_key_without_echo') as terminal):
            for extra, seed in (([], 20261030), (['--seed', '17'], 17)):
                code, out, err = self.invoke(['prepare', '--pack-root', pack,
                    '--custodian', record, '--out', control, *extra])
                self.assertEqual((code, err), (0, ''))
                summary = json.loads(out)
                self.assertEqual(summary['plan_sha256'], 'c' * 64)
                self.assertEqual(summary['control_root'], str(control))
                self.assertEqual((summary['max_requests'], summary['max_usd']), (60, '2.000000'))
                self.assertEqual(summary['budgets'], plan['budgets'])
                self.assertFalse(summary['execution_enabled'])
                self.assertEqual(summary['model_calls_live'], 0)
                self.assertNotIn('DUMMY_UNSAFE', out)
                self.assertEqual(prepare.call_args.args, (pack,))
                self.assertEqual(prepare.call_args.kwargs,
                    {'custodian': custodian, 'out': control, 'project_root': cli.PROJECT, 'seed': seed})
            for extra, expected in (([], None), (['--project-env', source['path']], source)):
                code, out, err = self.invoke(['proposal', '--control', control, *extra])
                self.assertEqual((code, json.loads(out), err), (0, proposal, ''))
                self.assertEqual(propose.call_args.args, (control,))
                self.assertEqual(propose.call_args.kwargs, {'credential_source': expected})
            code, out, err = self.invoke(['preflight', '--control', control, '--approval', approval])
        self.assertEqual((code, json.loads(out), err), (0, {'plan_sha256': 'c' * 64,
            'preflight': 'passed', 'execution_enabled': False, 'model_calls_live': 0}, ''))
        preflight.assert_called_once()
        for operation in (execute, project, terminal):
            operation.assert_not_called()

    def test_project_reader_reuses_probe_helper_and_is_delayed_until_core_claim(self):
        probe_cli = importlib.import_module('run_deepseek_tool_name_probe')
        self.assertIs(cli._read_project_key, probe_cli._read_project_key)
        path = self.root / '.env'
        approved = {'authorized': True, 'credential_source': {'kind': 'project_env', 'path': str(path)}}
        approval = self.file('approval.json', approved)
        events = []
        original_read = cli.read_json
        def read(*args, **kwargs):
            events.append('approval')
            return original_read(*args, **kwargs)
        def preflight(control, value):
            self.assertEqual((control, value), (self.root, approved))
            events.append('preflight')
            return {}
        def reader(value):
            self.assertEqual(value, path)
            events.append('key')
            return 'DUMMY_VALUE_CREDENTIAL'
        def execute(control, value, **kwargs):
            self.assertEqual(events, ['approval', 'preflight'])
            self.assertEqual((control, value, kwargs['enabled']), (self.root, approved, True))
            events.append('core_claim')
            self.assertEqual(kwargs['key_reader'](), 'DUMMY_VALUE_CREDENTIAL')
            return {'stage_halted': False, 'S0': None, 'S1': None,
                'answer': 'DUMMY_VALUE_CREDENTIAL'}
        with (patch.object(cli, 'read_json', side_effect=read),
              patch.object(cli, 'preflight_value_stage', side_effect=preflight),
              patch.object(cli, 'execute_value_stage', side_effect=execute) as dispatch,
              patch.object(cli, '_read_project_key', side_effect=reader) as key,
              patch.object(cli, '_read_key_without_echo') as terminal):
            code, out, err = self.invoke(['execute', '--control', self.root,
                '--approval', approval, '--enable-live'])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(events, ['approval', 'preflight', 'core_claim', 'key'])
        self.assertNotIn('DUMMY_VALUE_CREDENTIAL', out + err)
        dispatch.assert_called_once()
        key.assert_called_once_with(path)
        terminal.assert_not_called()

    def test_terminal_helper_is_reused_and_fixed_reader_failure_is_not_retried(self):
        probe_cli = importlib.import_module('run_deepseek_tool_name_probe')
        self.assertIs(cli._read_key_without_echo, probe_cli._read_key_without_echo)
        approval = self.approval()
        stdin = TTY('DUMMY_UNREAD_INPUT\n')
        def execute(*args, **kwargs):
            key.assert_not_called()
            kwargs['key_reader']()
            self.fail('failed no-echo input must stop execution')
        with (patch.object(cli, 'preflight_value_stage', return_value={}),
              patch.object(cli, '_read_key_without_echo',
                side_effect=StudyError('study_live_no_echo_input_unavailable')) as key,
              patch.object(cli, 'execute_value_stage', side_effect=execute) as dispatch):
            result = self.invoke(['execute', '--control', self.root,
                '--approval', approval, '--enable-live'], stdin=stdin, stderr=TTY())
        self.assertEqual(result, (2, '', 'study_live_no_echo_input_unavailable\n'))
        self.assertEqual(stdin.tell(), 0)
        key.assert_called_once_with()
        dispatch.assert_called_once()

    def test_nested_script_arrays_are_forwarded_only_to_zero_call_dryrun(self):
        scripts = self.scripts()
        result = {'stage_halted': False, 'S0': {'model_calls_live': 0}, 'S1': {'model_calls_live': 0}}
        with (patch.object(cli, 'run_value_dryrun', return_value=result) as dryrun,
              patch.object(cli, 'execute_value_stage') as execute,
              patch.object(cli, '_read_project_key') as project,
              patch.object(cli, '_read_key_without_echo') as terminal):
            code, out, err = self.invoke(['dry-run', '--control', self.root, '--scripts', scripts])
        self.assertEqual((code, json.loads(out), err), (0, result, ''))
        dryrun.assert_called_once_with(self.root, scripted_by_cell={'S0': {
            'dev-finish': ({'literal': 'fixture'},), 'dev-ask': ('timeout',)},
            'S1': {'cell-1': ('timeout',)}})
        for operation in (execute, project, terminal):
            operation.assert_not_called()

    def test_execute_cannot_accept_scripts_credentials_or_source_overrides(self):
        with (patch.object(cli, 'read_json') as read,
              patch.object(cli, 'execute_value_stage') as execute,
              patch.object(cli, '_read_project_key') as key):
            for extra in (['--scripts', self.root / 'scripts.json'],
                    ['--api-key', 'DUMMY_ARG_CREDENTIAL'],
                    ['--project-env', self.root / '.env']):
                with self.subTest(flag=extra[0]):
                    result = self.invoke(['execute', '--control', self.root,
                        '--approval', self.root / 'approval.json', '--enable-live', *extra])
                    self.assertEqual(result, (2, '', 'value_cli_arguments_invalid\n'))
        for operation in (read, execute, key):
            operation.assert_not_called()

    def test_live_and_fake_stage_output_strip_raw_content_recursively(self):
        approval = self.approval({'kind': 'project_env', 'path': str(self.root / '.env')})
        scripts = self.scripts()
        phase = {'model_calls_live': 0, 'batch_halted': True,
            'answer': 'DUMMY_RAW_ANSWER', 'source': 'DUMMY_RAW_SOURCE',
            'requests': ['DUMMY_RAW_REQUEST'], 'reasoning': 'DUMMY_RAW_REASONING',
            'accounting': {'request_count': 1, 'key': 'DUMMY_ACCOUNTING_KEY'},
            'rows': [{'task_id': 'case-1', 'strategy': 'direct_initial_3', 'status': 'failed',
                'question': 'DUMMY_ROW_QUESTION', 'answer': 'DUMMY_ROW_ANSWER',
                'source': 'DUMMY_ROW_SOURCE', 'requests': ['DUMMY_ROW_REQUEST'],
                'observation': {'flow_followed': False, 'answer': 'DUMMY_OBSERVATION_ANSWER'}}]}
        result = {'plan_sha256': 'd' * 64, 'stage_halted': True, 'S0': phase,
            'S1': None, 'S1_not_attempted_cells': 18, 'unknown': 'DUMMY_UNKNOWN_FIELD',
            'known_accrued_usd': '0.000588', 'new_unknown_held_usd': '0.000000',
            'S0_gate': {'qualified': False, 'source': 'DUMMY_GATE_SOURCE'},
            'accounting': {'request_count': 1, 'key': 'DUMMY_TOP_ACCOUNTING_KEY'}}
        with (patch.object(cli, 'preflight_value_stage', return_value={}),
              patch.object(cli, 'execute_value_stage', return_value=result),
              patch.object(cli, 'run_value_dryrun', return_value=result),
              patch.object(cli, '_read_project_key') as key):
            for args, expected in ((['execute', '--control', self.root,
                    '--approval', approval, '--enable-live'], 1),
                    (['dry-run', '--control', self.root, '--scripts', scripts], 0)):
                with self.subTest(command=args[0]):
                    code, out, err = self.invoke(args)
                    summary = json.loads(out)
                    self.assertEqual((code, err), (expected, ''))
                    self.assertTrue(summary['stage_halted'])
                    self.assertIsNone(summary['S1'])
                    self.assertEqual(summary['S1_not_attempted_cells'], 18)
                    self.assertEqual(summary['known_accrued_usd'], '0.000588')
                    self.assertEqual(summary['new_unknown_held_usd'], '0.000000')
                    self.assertEqual(summary['S0_gate'], {'qualified': False})
                    self.assertEqual(summary['accounting'], {'request_count': 1})
                    self.assertEqual(summary['S0']['accounting'], {'request_count': 1})
                    self.assertNotIn('DUMMY_', out + err)
        key.assert_not_called()

    def test_invalid_script_shapes_and_duplicate_json_are_not_dispatched(self):
        invalid = [{}, {'S0': [], 'S1': {}}, {'S0': {}, 'S1': 'timeout'},
            {'S0': {'dev-finish': []}, 'S1': {}},
            {'S0': {'dev-finish': ['timeout'] * 4}, 'S1': {}},
            {'S0': {}, 'S1': {}, 'other': {}}]
        with patch.object(cli, 'run_value_dryrun') as dryrun:
            for number, value in enumerate(invalid):
                with self.subTest(case=number):
                    scripts = self.file('scripts.json', value)
                    result = self.invoke(['dry-run', '--control', self.root, '--scripts', scripts])
                    self.assertEqual(result, (2, '', 'value_cli_script_invalid\n'))
            scripts = self.root / 'scripts.json'
            scripts.write_text('{"S0":{},"S0":{},"S1":{}}', encoding='utf-8')
            self.assertEqual(self.invoke(['dry-run', '--control', self.root, '--scripts', scripts]),
                (2, '', 'study_json_invalid\n'))
        dryrun.assert_not_called()

    def test_invalid_credential_sources_stop_after_preflight_without_key(self):
        with (patch.object(cli, 'preflight_value_stage', return_value={}),
              patch.object(cli, 'execute_value_stage') as execute,
              patch.object(cli, '_read_project_key') as project,
              patch.object(cli, '_read_key_without_echo') as terminal):
            for source in (None, {}, {'kind': 'other'}, {'kind': 'project_env', 'path': 'relative/.env'}):
                with self.subTest(source=source):
                    approval = self.file('approval.json', {'authorized': True, 'credential_source': source})
                    result = self.invoke(['execute', '--control', self.root,
                        '--approval', approval, '--enable-live'])
                    self.assertEqual(result, (2, '', 'value_cli_credential_source_invalid\n'))
        for operation in (execute, project, terminal):
            operation.assert_not_called()

    def test_errors_and_interrupt_have_fixed_codes_and_no_retry(self):
        for error, expected in ((StudyError('value_approval_missing'), 'value_approval_missing'),
                (OSError('DUMMY_PRIVATE_IO_DETAIL'), 'value_cli_io_invalid'),
                (ValueError('DUMMY_PRIVATE_INPUT_DETAIL'), 'value_cli_input_invalid')):
            with self.subTest(error=type(error).__name__):
                with patch.object(cli, 'propose_value_approval', side_effect=error) as propose:
                    result = self.invoke(['proposal', '--control', self.root])
                self.assertEqual(result, (2, '', expected + '\n'))
                propose.assert_called_once()
        approval = self.approval({'kind': 'project_env', 'path': str(self.root / '.env')})
        with (patch.object(cli, 'preflight_value_stage', return_value={}),
              patch.object(cli, 'execute_value_stage', side_effect=KeyboardInterrupt) as execute,
              patch.object(cli, '_read_project_key') as key):
            result = self.invoke(['execute', '--control', self.root,
                '--approval', approval, '--enable-live'])
        self.assertEqual(result, (130, '', 'value_cli_interrupted_claim_retained\n'))
        execute.assert_called_once()
        key.assert_not_called()


if __name__ == '__main__':
    unittest.main()
