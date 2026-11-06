from contextlib import ExitStack, redirect_stdout, redirect_stderr
import io
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from macr.cli import main


class WorkbenchCLITests(unittest.TestCase):
    def test_help_is_static_and_does_not_bind_or_create_data(self):
        output = io.StringIO()
        with patch('sys.argv', ['agent-review', 'evidence-ui', '--help']), \
                patch('socket.socket.bind', side_effect=AssertionError('help must not listen')), \
                patch('macr.investigation.repair_entry._read_runtime_credential', side_effect=AssertionError('help must not read credential')) as reader:
            with redirect_stdout(output), self.assertRaises(SystemExit) as caught:
                main()
        self.assertEqual(caught.exception.code, 0)
        for flag in ('--data-root', '--enable-remote-repair', '--repair-spend-plan',
                     '--repair-spend-cell', '--enable-repair-verification'):
            self.assertIn(flag, output.getvalue())
        reader.assert_not_called()

    def arguments(self, *, remote=False, execution=False):
        return SimpleNamespace(data_root=Path('/authored/owned-workbench'), enable_remote_repair=remote,
            repair_spend_plan=Path('/authored/spend-plan.json') if remote else None,
            repair_spend_cell='approved-cell' if remote else None,
            enable_repair_verification=execution)

    def fake_service(self, stack, events, *, bind_error=False, shutdown_error=False):
        from macr.api import evidence_http as http
        workbench = Mock()
        workbench.begin_shutdown.side_effect = lambda: events.append('begin-shutdown')
        def shutdown():
            events.append('workbench-shutdown')
            if shutdown_error:
                raise OSError('authored cleanup failure')
        workbench.shutdown.side_effect = shutdown
        factory = stack.enter_context(patch.object(http, 'Workbench',
            side_effect=lambda *a, **kw: events.append('workbench') or workbench))
        server = Mock(origin='http://127.0.0.1:1', access_token='A' * 43)
        server.serve_forever.side_effect = lambda **kw: events.append('serve')
        server.server_close.side_effect = lambda: events.append('server-close')
        def bind(*args):
            events.append('bind')
            if bind_error:
                raise OSError('authored local bind failure')
            return server
        listener = stack.enter_context(patch.object(http, 'make_server', side_effect=bind))
        stack.enter_context(patch.object(http.signal, 'signal', return_value=http.signal.SIG_DFL))
        stack.enter_context(redirect_stdout(io.StringIO()))
        stack.enter_context(redirect_stderr(io.StringIO()))
        return factory, listener

    def test_partial_remote_flags_fail_before_runtime_owner_or_listener(self):
        from macr.api import evidence_http as http
        options = ('--enable-remote-repair', '--repair-spend-plan', '--repair-spend-cell')
        for selected in ((0,), (1,), (2,), (0, 1), (0, 2), (1, 2)):
            with self.subTest(selected=selected), ExitStack() as stack:
                argv = ['agent-review', 'evidence-ui']
                for index in selected:
                    argv.append(options[index])
                    if index == 1:
                        argv.append('/authored/spend-plan.json')
                    if index == 2:
                        argv.append('approved-cell')
                reader = stack.enter_context(patch('macr.investigation.repair_entry._read_runtime_credential'))
                runtime = stack.enter_context(patch('macr.api.repair_runtime.RepairRuntime'))
                owner = stack.enter_context(patch.object(http, 'Workbench'))
                listener = stack.enter_context(patch.object(http, 'make_server'))
                stack.enter_context(patch('sys.argv', argv))
                stack.enter_context(redirect_stderr(io.StringIO()))
                with self.assertRaises(SystemExit) as caught:
                    main()
                self.assertEqual(caught.exception.code, 4)
                reader.assert_not_called()
                runtime.assert_not_called()
                owner.assert_not_called()
                listener.assert_not_called()

    def test_normal_cli_startup_keeps_remote_and_execution_disabled(self):
        events = []
        with ExitStack() as stack:
            owner, listener = self.fake_service(stack, events)
            reader = stack.enter_context(patch('macr.investigation.repair_entry._read_runtime_credential'))
            runtime = stack.enter_context(patch('macr.api.repair_runtime.RepairRuntime'))
            stack.enter_context(patch('sys.argv', ['agent-review', 'evidence-ui', '--data-root', '/authored/owned-workbench']))
            with self.assertRaises(SystemExit) as caught:
                main()
        self.assertEqual(caught.exception.code, 0)
        self.assertEqual(owner.call_args.kwargs['repair_execution_enabled'], False)
        self.assertIsNone(owner.call_args.kwargs['repair_runtime'])
        reader.assert_not_called()
        runtime.assert_not_called()
        listener.assert_called_once()
        self.assertEqual(events, ['workbench', 'bind', 'serve', 'begin-shutdown', 'server-close', 'workbench-shutdown'])

    def test_remote_prepare_is_foreground_before_bind_and_close_survives_failure(self):
        from macr.api import evidence_http as http
        from macr.investigation.repair_verification import BackendVerifier
        from test_repair_backend_verifier import FixtureBackend
        for execution, bind_error, shutdown_error in ((False, False, False), (True, False, False),
                                                     (False, True, False), (False, False, True)):
            with self.subTest(execution=execution, bind_error=bind_error, shutdown_error=shutdown_error), ExitStack() as stack:
                events = []
                owner, listener = self.fake_service(stack, events, bind_error=bind_error, shutdown_error=shutdown_error)
                reader = Mock(return_value='DUMMY-WORKBENCH-CREDENTIAL-NOT-A-KEY')
                runtime = Mock()
                runtime.prepare.side_effect = lambda: events.append(('prepare', threading.get_ident())) or reader()
                runtime.close.side_effect = lambda: events.append('runtime-close')
                constructor = stack.enter_context(patch('macr.api.repair_runtime.RepairRuntime', return_value=runtime))
                backend = FixtureBackend(['checks.py::Authored.test_public'])
                verifier = BackendVerifier(backend, backend.public_result,
                    backend_id='authored-cli-backend', execution_profile='authored-public-v1')
                def factory():
                    events.append('verifier-factory')
                    return verifier
                try:
                    code = http.run_cli(self.arguments(remote=True, execution=execution),
                        repair_verifier_factory=factory, credential_reader=reader)
                except OSError:
                    self.assertTrue(shutdown_error)
                    code = 4
                self.assertEqual(code, 4 if bind_error or shutdown_error else 0)
                constructor.assert_called_once_with(Path('/authored/spend-plan.json'), 'approved-cell', credential_reader=reader)
                self.assertIs(owner.call_args.kwargs['repair_runtime'], runtime)
                self.assertEqual(owner.call_args.kwargs['repair_execution_enabled'], execution)
                self.assertEqual(events.count(('prepare', threading.get_ident())), 1)
                self.assertLess(events.index(('prepare', threading.get_ident())), events.index('bind'))
                self.assertEqual(events.count('verifier-factory'), int(execution))
                if execution:
                    self.assertLess(events.index('verifier-factory'), events.index(('prepare', threading.get_ident())))
                reader.assert_called_once_with()
                runtime.prepare.assert_called_once_with()
                runtime.close.assert_called_once_with()
                self.assertEqual(events[-2:], ['workbench-shutdown', 'runtime-close'])
                self.assertEqual((backend.calls, backend.reads), ([], []))

    def test_verification_opt_in_requires_qualified_exact_backend_before_credential(self):
        from macr.api import evidence_http as http
        from macr.investigation.repair_verification import BackendVerifier
        from test_repair_backend_verifier import FixtureBackend
        backend = FixtureBackend(['checks.py::Authored.test_public'], capable=False)
        unavailable = BackendVerifier(backend, backend.public_result,
            backend_id='authored-incapable', execution_profile='authored-public-v1')
        class DerivedVerifier(BackendVerifier):
            def available(self):
                raise AssertionError('subclass must not be probed')
        derived = DerivedVerifier(backend, backend.public_result,
            backend_id='authored-derived', execution_profile='authored-public-v1')
        for value in (None, object(), unavailable, derived):
            with self.subTest(verifier=type(value).__name__), ExitStack() as stack:
                events = []
                owner, listener = self.fake_service(stack, events)
                reader = Mock(side_effect=AssertionError('unqualified must not read credential'))
                runtime = stack.enter_context(patch('macr.api.repair_runtime.RepairRuntime'))
                factory = Mock(return_value=value)
                error=io.StringIO()
                with redirect_stderr(error):
                    code = http.run_cli(self.arguments(remote=True, execution=True),
                        repair_verifier_factory=factory, credential_reader=reader)
                self.assertEqual(code, 4)
                self.assertIn('verification_unavailable',error.getvalue())
                factory.assert_called_once_with()
                reader.assert_not_called()
                runtime.return_value.prepare.assert_not_called()
                owner.assert_not_called()
                listener.assert_not_called()
        self.assertEqual((backend.calls, backend.reads), ([], []))

    def test_missing_or_unsupported_data_root_is_sanitized(self):
        error = io.StringIO()
        with redirect_stderr(error), patch('sys.argv', ['agent-review', 'evidence-ui', '--data-root', '/dev/null/unsupported']):
            with self.assertRaises(SystemExit) as caught:
                main()
        self.assertEqual(caught.exception.code, 4)
        self.assertNotIn('/dev/null', error.getvalue())
