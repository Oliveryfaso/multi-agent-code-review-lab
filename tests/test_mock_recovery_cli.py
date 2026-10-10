"""Explicit mock-only CLI dispatch, using managed synthetic fixtures only."""
import builtins
import importlib.metadata
import io
import json
import os
import sqlite3
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from unittest.mock import patch
from uuid import uuid4

from debugger_fixtures import fixture_root, write_files
from macr import cli
from macr.investigation import mock_recovery as recovery
from macr.providers.mock import MockLLMProvider


UNKNOWN_EXPLANATION = (
    'The simulated external outcome is unknown. Automatic replay is disabled; '
    'no external action was performed.')


class MockRecoveryCLITests(unittest.TestCase):
    def setUp(self):
        self.base = fixture_root()
        guard = patch.object(recovery, 'RUNS_ROOT', self.base)
        guard.start()
        self.addCleanup(guard.stop)
        self.root = self.new_root()
        self.provider_requests = []
        self.provider_tracing_enabled = []
        self.provider_environment = []
        complete = MockLLMProvider.complete

        def observed_complete(provider, request):
            from langsmith.utils import tracing_is_enabled
            self.provider_requests.append(request)
            self.provider_tracing_enabled.append(tracing_is_enabled())
            self.provider_environment.append({key: os.environ.get(key) for key in (
                'LANGSMITH_TRACING', 'LANGSMITH_TRACING_V2',
                'LANGCHAIN_TRACING', 'LANGCHAIN_TRACING_V2',
                'LANGSMITH_OTEL_ENABLED', 'LANGSMITH_OTEL_ONLY',
                'LANGSMITH_API_KEY', 'LANGCHAIN_API_KEY')})
            return complete(provider, request)

        guard = patch.object(MockLLMProvider, 'complete', observed_complete)
        guard.start()
        self.addCleanup(guard.stop)
        self.forbidden = []
        for target in (
                'macr.memory.run_store.RunStore.__init__',
                'macr.memory.run_store.RunStore.append',
                'macr.memory.run_store.RunStore.checkpoint',
                'macr.memory.sqlite_run_store.SQLiteRunStore.__init__',
                'macr.memory.sqlite_run_store.SQLiteRunStore.append',
                'macr.memory.sqlite_run_store.SQLiteRunStore.checkpoint',
                'macr.agents.orchestrator.Orchestrator.__init__',
                'macr.cli._provider',
                'socket.create_connection', 'socket.socket.connect',
                'subprocess.Popen'):
            guard = patch(target, side_effect=AssertionError('real dispatch forbidden'))
            self.forbidden.append(guard.start())
            self.addCleanup(guard.stop)
        original_import = builtins.__import__

        def no_local_provider(name, *args, **kwargs):
            if name == 'macr.providers.local_http' or name.startswith('macr.tools.'):
                raise AssertionError('real provider/tool import forbidden')
            return original_import(name, *args, **kwargs)

        guard = patch('builtins.__import__', side_effect=no_local_provider)
        guard.start()
        self.addCleanup(guard.stop)
        self.addCleanup(self.assert_forbidden_not_called)

    def assert_forbidden_not_called(self):
        for forbidden in self.forbidden:
            forbidden.assert_not_called()

    def new_root(self):
        root = self.base / ('cli-' + uuid4().hex)
        self.assertFalse(root.exists())
        return root

    def invoke_cli(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with patch('sys.argv', ['agent-review', *args]), \
                redirect_stdout(output), redirect_stderr(errors):
            try:
                cli.main()
                code = 0
            except SystemExit as error:
                code = error.code
        self.assertLessEqual(len(output.getvalue().encode('utf-8')), 8192)
        return code, output.getvalue(), errors.getvalue()

    def mock_cli(self, operation, *extra, root=None):
        code, output, errors = self.invoke_cli(
            'mock-recovery', operation, '--root', str(root or self.root), *extra)
        self.assertEqual(errors, '')
        report = json.loads(output)
        self.assertIs(report['mock_only'], True)
        self.assertIs(report['production_resume'], False)
        self.assertIs(report['dispatch_permitted'], False)
        self.assertEqual(report['state_authority'], 'langgraph_sqlite')
        self.assertEqual(report['model_calls'], 0)
        self.assertEqual(report['tool_calls'], 0)
        return code, report

    def assert_steps(self, report, completed, executed, provider_calls):
        self.assertEqual(report['completed_mock_steps'], completed)
        self.assertEqual(report['executed_mock_steps_this_call'], executed)
        self.assertEqual(report['mock_provider_calls_this_call'], provider_calls)

    def test_normal_cli_uses_fixed_mock_provider_and_single_state_authority(self):
        code, report = self.mock_cli('start', '--text', 'synthetic cli evidence')
        self.assertEqual(code, 0)
        self.assertEqual(report['status'], 'completed')
        self.assert_steps(report, {'evidence': 1, 'report': 1},
                          {'evidence': 1, 'report': 1}, 1)
        self.assertEqual(len(self.provider_requests), 1)
        self.assertEqual(self.provider_requests[0].tools, [])
        self.assertEqual(report['result']['input']['text'], 'synthetic cli evidence')
        names = {path.name for path in self.root.iterdir()
                 if not path.name.startswith('._')}
        self.assertLessEqual(names, recovery.FILES)
        with closing(sqlite3.connect(self.root / 'checkpoints.sqlite3')) as connection:
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'").fetchall()
        self.assertEqual(set(tables), {('checkpoints',), ('writes',)})
        self.assertFalse((self.root / 'checkpoint.json').exists())
        self.assertFalse((self.root / 'runs.sqlite3').exists())

    def test_cli_preserves_leading_dash_text_when_forwarding_to_mock_entry(self):
        code, report = self.mock_cli('start', '--text=-synthetic')
        self.assertEqual(code, 0)
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(report['result']['input']['text'], '-synthetic')
        self.assert_steps(report, {'evidence': 1, 'report': 1},
                          {'evidence': 1, 'report': 1}, 1)
        self.assertEqual(len(self.provider_requests), 1)

    def test_cli_rejection_after_mock_invoke_retains_actual_provider_call_count(self):
        complete = MockLLMProvider.complete

        def unexpected_tool_result(provider, request):
            response = complete(provider, request)
            response.tool_calls = [{'name': 'synthetic-disallowed-tool'}]
            return response

        with patch.object(MockLLMProvider, 'complete', unexpected_tool_result):
            code, report = self.mock_cli('start')
        self.assertEqual(code, 2)
        self.assertEqual(report['status'], 'rejected')
        self.assertEqual(report['error'], 'mock_provider_invalid')
        self.assertEqual(report['mock_provider_calls_this_call'], 1)
        self.assertEqual(len(self.provider_requests), 1)
        self.assertEqual(self.provider_requests[0].tools, [])
        self.assertEqual(report['model_calls'], 0)
        self.assertEqual(report['tool_calls'], 0)

    def test_paused_cli_status_and_new_invocation_resume_do_not_repeat_evidence(self):
        code, paused = self.mock_cli('start', '--scenario', 'pause')
        self.assertEqual(code, 2)
        self.assertEqual(paused['status'], 'paused')
        self.assert_steps(paused, {'evidence': 1, 'report': 0},
                          {'evidence': 1, 'report': 0}, 0)
        database = self.root / 'checkpoints.sqlite3'
        before = database.read_bytes()
        code, status = self.mock_cli('status')
        self.assertEqual(code, 2)
        self.assertEqual(status['status'], 'paused')
        self.assert_steps(status, {'evidence': 1, 'report': 0},
                          {'evidence': 0, 'report': 0}, 0)
        self.assertEqual(database.read_bytes(), before)
        code, completed = self.mock_cli('resume')
        self.assertEqual(code, 0)
        self.assertEqual(completed['status'], 'completed')
        self.assert_steps(completed, {'evidence': 1, 'report': 1},
                          {'evidence': 0, 'report': 1}, 1)
        self.assertEqual(len(self.provider_requests), 1)

    def test_repeated_cli_resume_uses_saved_result_without_provider_or_state_write(self):
        self.mock_cli('start', '--scenario', 'pause')
        _, completed = self.mock_cli('resume')
        before = (self.root / 'checkpoints.sqlite3').read_bytes()
        for _ in range(2):
            code, repeated = self.mock_cli('resume')
            self.assertEqual(code, 0)
            self.assertEqual(repeated['status'], 'completed')
            self.assertIs(repeated['cached_result'], True)
            self.assertEqual(repeated['result'], completed['result'])
            self.assert_steps(repeated, {'evidence': 1, 'report': 1},
                              {'evidence': 0, 'report': 0}, 0)
        self.assertEqual(len(self.provider_requests), 1)
        self.assertEqual((self.root / 'checkpoints.sqlite3').read_bytes(), before)

    def test_unknown_cli_explains_stop_and_refuses_automatic_replay(self):
        code, blocked = self.mock_cli('start', '--scenario', 'unknown')
        self.assertEqual(code, 2)
        self.assertEqual(blocked['status'], 'blocked')
        self.assertEqual(blocked['explanation'], UNKNOWN_EXPLANATION)
        self.assert_steps(blocked, {'evidence': 1, 'report': 0},
                          {'evidence': 1, 'report': 0}, 0)
        before = (self.root / 'checkpoints.sqlite3').read_bytes()
        code, status = self.mock_cli('status')
        self.assertEqual(code, 2)
        self.assertEqual(status['status'], 'blocked')
        self.assertEqual(status['explanation'], UNKNOWN_EXPLANATION)
        self.assert_steps(status, {'evidence': 1, 'report': 0},
                          {'evidence': 0, 'report': 0}, 0)
        for _ in range(2):
            code, rejected = self.mock_cli('resume')
            self.assertEqual(code, 2)
            self.assertEqual(rejected['status'], 'rejected')
            self.assertEqual(rejected['error'], 'outcome_unknown')
            self.assertEqual(rejected['explanation'], UNKNOWN_EXPLANATION)
            self.assertEqual(rejected['mock_provider_calls_this_call'], 0)
        self.assertEqual(self.provider_requests, [])
        self.assertEqual((self.root / 'checkpoints.sqlite3').read_bytes(), before)

    def test_dependency_missing_is_rejected_before_creating_owned_root(self):
        with patch.object(recovery.importlib.metadata, 'version',
                          side_effect=importlib.metadata.PackageNotFoundError('langgraph')):
            code, rejected = self.mock_cli('start')
        self.assertEqual(code, 2)
        self.assertEqual(rejected['status'], 'rejected')
        self.assertEqual(rejected['error'], 'dependency_unavailable')
        self.assertFalse(self.root.exists())
        self.assertEqual(self.provider_requests, [])

    def test_dependency_version_drift_is_rejected_before_creating_owned_root(self):
        with patch.object(recovery.importlib.metadata, 'version', return_value='0.0.0'):
            code, rejected = self.mock_cli('start')
        self.assertEqual(code, 2)
        self.assertEqual(rejected['status'], 'rejected')
        self.assertEqual(rejected['error'], 'dependency_version_incompatible')
        self.assertFalse(self.root.exists())
        self.assertEqual(self.provider_requests, [])

    def test_cli_rejects_provider_backend_profile_and_wrong_operation_parameters(self):
        for operation, extra in (
                ('start', ('--provider', 'local-http')),
                ('start', ('--backend', 'sqlite')),
                ('start', ('--model-profile', 'unapproved.json')),
                ('resume', ('--text', 'replacement input')),
                ('status', ('--scenario', 'normal'))):
            with self.subTest(operation=operation, extra=extra):
                with patch.object(recovery, '_dependencies',
                                  side_effect=AssertionError('invalid CLI reached dependencies')):
                    code, output, errors = self.invoke_cli(
                        'mock-recovery', operation, '--root', str(self.root), *extra)
                self.assertEqual(code, 2)
                self.assertEqual(output, '')
                self.assertIn('unrecognized arguments', errors)
                self.assertFalse(self.root.exists())
        self.assertEqual(self.provider_requests, [])

    def test_cli_read_or_resume_missing_root_does_not_create_state(self):
        for operation in ('status', 'resume'):
            with self.subTest(operation=operation):
                code, report = self.mock_cli(operation)
                self.assertEqual(code, 2)
                self.assertEqual(report['status'], 'rejected')
                self.assertFalse(self.root.exists())
        self.assertEqual(self.provider_requests, [])

    def test_cli_corrupt_database_is_rejected_without_repair_or_provider_call(self):
        self.mock_cli('start', '--scenario', 'pause')
        database = self.root / 'checkpoints.sqlite3'
        broken = b'not a SQLite database\n' * 8
        database.write_bytes(broken)
        for operation in ('status', 'resume'):
            with self.subTest(operation=operation):
                code, report = self.mock_cli(operation)
                self.assertEqual(code, 2)
                self.assertEqual(report['status'], 'rejected')
                self.assertEqual(report['error'], 'checkpoint_invalid')
                self.assertEqual(database.read_bytes(), broken)
        self.assertEqual(self.provider_requests, [])

    def test_cli_schema_conflict_is_rejected_without_repair_or_provider_call(self):
        self.mock_cli('start', '--scenario', 'pause')
        database = self.root / 'checkpoints.sqlite3'
        with closing(sqlite3.connect(database)) as connection:
            connection.execute('CREATE TABLE conflicting_state (next_step TEXT)')
            connection.commit()
        before = database.read_bytes()
        for operation in ('status', 'resume'):
            with self.subTest(operation=operation):
                code, report = self.mock_cli(operation)
                self.assertEqual(code, 2)
                self.assertEqual(report['status'], 'rejected')
                self.assertEqual(report['error'], 'schema_incompatible')
                self.assertEqual(database.read_bytes(), before)
        self.assertEqual(self.provider_requests, [])

    def test_cli_incompatible_workflow_rejects_old_state_without_migration(self):
        self.mock_cli('start', '--scenario', 'pause')
        database = self.root / 'checkpoints.sqlite3'
        before = database.read_bytes()
        with patch.object(recovery, 'WORKFLOW_VERSION', 'mock-v1'):
            for operation in ('status', 'resume'):
                with self.subTest(operation=operation):
                    code, report = self.mock_cli(operation)
                    self.assertEqual(code, 2)
                    self.assertEqual(report['status'], 'rejected')
                    self.assertEqual(database.read_bytes(), before)
        self.assertEqual(self.provider_requests, [])

    def test_dependencies_and_mock_execution_disable_telemetry_and_restore_environment(self):
        settings = {
            'LANGSMITH_TRACING': 'true', 'LANGSMITH_TRACING_V2': 'true',
            'LANGCHAIN_TRACING': 'true', 'LANGCHAIN_TRACING_V2': 'true',
            'LANGSMITH_OTEL_ENABLED': 'true', 'LANGSMITH_OTEL_ONLY': 'true',
            'LANGSMITH_API_KEY': 'synthetic-test-key',
            'LANGCHAIN_API_KEY': 'synthetic-test-key',
        }
        observed = []
        dependencies = recovery._dependencies

        def observed_dependencies():
            observed.append({key: os.environ.get(key) for key in settings})
            return dependencies()

        with patch.dict(os.environ, settings), \
                patch.object(recovery, '_dependencies', side_effect=observed_dependencies):
            code, _ = self.mock_cli('start')
            self.assertEqual(code, 0)
            for key, value in settings.items():
                self.assertEqual(os.environ.get(key), value)
        self.assertTrue(observed)
        for values in observed:
            for key in settings:
                expected = '' if key.endswith('API_KEY') else 'false'
                self.assertEqual(values[key], expected)
        self.assertEqual(len(self.provider_requests), 1)
        self.assertEqual(self.provider_tracing_enabled, [False])
        self.assertEqual(len(self.provider_environment), 1)
        for key in settings:
            expected = '' if key.endswith('API_KEY') else 'false'
            self.assertEqual(self.provider_environment[0][key], expected)

    def reject_optional_imports(self):
        imported = []
        original_import = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name == 'langgraph' or name.startswith(('langgraph.', 'langsmith.')) \
                    or name == 'langsmith':
                imported.append(name)
                raise AssertionError('default CLI imported optional recovery dependency')
            return original_import(name, *args, **kwargs)

        return imported, patch('builtins.__import__', side_effect=guarded)

    def test_default_command_help_does_not_load_optional_dependencies(self):
        imported, guard = self.reject_optional_imports()
        with guard, patch.object(recovery, '_dependencies',
                                 side_effect=AssertionError('default CLI entered recovery')):
            for arguments in (('--help',), ('ask', '--help'), ('patch', '--help'),
                              ('review-diff', '--help'),
                              ('static-baseline', '--help')):
                with self.subTest(arguments=arguments):
                    code, output, errors = self.invoke_cli(*arguments)
                    self.assertEqual(code, 0)
                    self.assertIn('usage:', output)
                    self.assertEqual(errors, '')
        self.assertEqual(imported, [])
        self.assertFalse(self.root.exists())
        self.assertEqual(self.provider_requests, [])

    def test_static_baseline_executes_without_optional_dependencies_or_source_execution(self):
        write_files(self.base / 'source', {'entry.py':
            "raise RuntimeError('ANALYSED SOURCE MUST NOT EXECUTE')\n"
            'def entry():\n    return 1\n'})
        input_path = self.base / 'input.json'
        input_path.write_text(json.dumps({
            'symptom': 'Synthetic reported failure', 'symbol_names': ['entry'],
            'reported_traceback': [{'file': 'entry.py', 'line': 3}]}))
        imported, guard = self.reject_optional_imports()
        with guard, patch.object(recovery, '_dependencies',
                                 side_effect=AssertionError('baseline entered recovery')):
            code, output, errors = self.invoke_cli(
                'static-baseline', '--input', str(input_path),
                '--out', str(self.base / 'baseline-out'), '--json')
        report = json.loads(output)
        self.assertEqual(code, 0)
        self.assertEqual(errors, '')
        self.assertEqual(report['model_calls'], 0)
        self.assertIs(report['repository_code_executed'], False)
        self.assertEqual(report['answer']['verdict'], 'unknown')
        self.assertEqual(imported, [])
        self.assertFalse(self.root.exists())
        self.assertEqual(self.provider_requests, [])


if __name__ == '__main__':
    unittest.main()
