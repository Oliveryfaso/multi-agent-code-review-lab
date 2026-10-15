"""Bounded trusted static-evidence fixtures; no native analysis or target execution."""
from contextlib import ExitStack, contextmanager
import importlib.abc
import importlib.machinery
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / 'src'
NAMES = ('test_static_evidence', 'test_evidence_cli', 'test_evidence_cli_trust',
         'test_evidence_cli_transport', 'test_evidence_packaging',
         'test_static_evidence_frames', 'test_static_evidence_frame_completeness',
         'test_evidence_worker_timing')
EXPECTED = 109
LIMIT = 64 * 1024 * 1024


class ProductOrigins(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'macr' or fullname.startswith('macr.'):
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
            if spec is None or not spec.origin or not Path(spec.origin).resolve().is_relative_to(SOURCE):
                raise RuntimeError('unexpected_product_origin')
            return spec
        return None


def fixture_allocation(root):
    """Count only owned temporary fixtures with lstat, without following links."""
    allocated = 0
    def scan_error(error):
        if not isinstance(error, FileNotFoundError):
            raise error
    for parent, directories, files in os.walk(root, followlinks=False, onerror=scan_error):
        directories[:] = [name for name in directories if not (Path(parent) / name).is_symlink()]
        for path in [Path(parent)] + [Path(parent) / name for name in files]:
            try:
                allocated += path.lstat().st_blocks * 512
            except FileNotFoundError:
                pass  # owned same-process fixtures may finish their cleanup
        for name in os.listdir(parent):
            path = Path(parent) / name
            try:
                if path.is_symlink() and name not in files:
                    allocated += path.lstat().st_blocks * 512
            except FileNotFoundError:
                pass
    return allocated


def main():
    started = time.monotonic()
    deadline = started + 120
    attempts = []

    def blocked(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError('native_dispatch_forbidden')

    previous_handler = signal.getsignal(signal.SIGALRM)

    def expired(*args):
        raise RuntimeError('wall_seconds_limit')

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 120)
    finder = ProductOrigins()
    sys.meta_path.insert(0, finder)
    sys.path[:0] = [str(SOURCE), str(PROJECT / 'tests'), str(PROJECT / 'scripts')]
    try:
        with ExitStack() as contexts:
            for target in ('subprocess.Popen', 'os.system', 'socket.create_connection',
                           'socket.socket.connect', 'socket.socket.connect_ex',
                           'urllib.request.urlopen', 'http.client.HTTPConnection.request',
                           'resource.getrusage', 'ctypes.CDLL', 'ctypes.PyDLL'):
                contexts.enter_context(patch(target, side_effect=blocked))
            for name in ('fork', 'forkpty', 'posix_spawn', 'posix_spawnp', 'execv', 'execve',
                         'execl', 'execlp', 'execvp', 'execvpe', 'spawnv', 'spawnve', 'spawnvp', 'spawnvpe'):
                if hasattr(os, name):
                    contexts.enter_context(patch.object(os, name, side_effect=blocked))
            import debugger_fixtures
            import run_offline_tests as runner
            from macr.investigation import evidence_supervisor
            contexts.enter_context(patch.object(evidence_supervisor, '_host_pressure', side_effect=blocked))
            contexts.enter_context(patch.object(evidence_supervisor, '_worker_rss', side_effect=blocked))
            runs = PROJECT / 'artifacts/test-runs' / ('static-evidence-' + str(uuid4()))
            debugger_fixtures.PROJECT = PROJECT
            debugger_fixtures.RUNS_ROOT = runs
            runner.RUNS_ROOT = runs
            original_temp = tempfile.TemporaryDirectory
            private = contexts.enter_context(original_temp(
                prefix='macr-static-evidence-pure-', dir=Path(tempfile.gettempdir()).resolve()))
            shared_usage = runner.tree_usage
            def all_owned_usage(root):
                if time.monotonic() >= deadline:
                    raise RuntimeError('wall_seconds_limit')
                usage = shared_usage(root)
                usage['allocated_bytes'] += fixture_allocation(private)
                if time.monotonic() >= deadline:
                    raise RuntimeError('wall_seconds_limit')
                return usage
            contexts.enter_context(patch.object(runner, 'tree_usage', all_owned_usage))

            @contextmanager
            def pure_context(tmp):
                def owned_temp(*args, **kwargs):
                    kwargs['dir'] = private
                    return original_temp(*args, **kwargs)
                with debugger_fixtures.fixture_session(tmp), patch.object(tempfile, 'TemporaryDirectory', owned_temp):
                    yield

            contexts.enter_context(patch.object(runner, 'offline_context', pure_context))
            loader = unittest.TestLoader()
            suite = loader.loadTestsFromNames(NAMES)
            if loader.errors or suite.countTestCases() != EXPECTED:
                raise RuntimeError('fixed_test_selection')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('wall_seconds_limit')
            report = runner.run_suite(suite, runs_root=runs, timeout_seconds=remaining, max_allocated_bytes=LIMIT)
            origins = {}
            for name, module in tuple(sys.modules.items()):
                if name == 'macr' or name.startswith('macr.'):
                    location = getattr(module, '__file__', None)
                    if not location or not Path(location).resolve().is_relative_to(SOURCE):
                        raise RuntimeError('unexpected_loaded_product_origin')
                    origins[name] = location
            accepted = report['status'] == 'passed' and report['tests_run'] == EXPECTED and not attempts
            receipt = {'tests': list(NAMES), 'tests_expected': EXPECTED, 'test_report': report,
                       'product_origins': origins, 'forbidden_attempts': len(attempts),
                       'owned_fixture_root': private, 'fixture_physical_allocation_included': True,
                       'elapsed_seconds': time.monotonic() - started,
                       'execution_status': 'requires_successful_post_record_allocation_and_deadline_check'}
            payload = (json.dumps(receipt, indent=2) + '\n').encode()
            if len(payload) > 512 * 1024:
                raise RuntimeError('receipt_bytes_limit')
            with (Path(report['run_dir']) / 'static-evidence-receipt.json').open('xb') as stream:
                stream.write(payload)
            final_usage = runner.tree_usage(runs)
            if final_usage['allocated_bytes'] > LIMIT or time.monotonic() >= deadline:
                raise RuntimeError('post_record_resource_limit')
            print(json.dumps({'status': 'passed' if accepted else 'failed', 'tests_run': report['tests_run'],
                              'run_dir': report['run_dir'], 'allocated_peak_bytes': report['allocated_peak_bytes'],
                              'final_usage': final_usage, 'forbidden_attempts': len(attempts)}))
            return 0 if accepted else 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        sys.meta_path.remove(finder)


if __name__ == '__main__':
    if not sys.dont_write_bytecode or not sys.flags.isolated:
        raise SystemExit('Run with python -I -S -B scripts/run_static_evidence_tests.py')
    raise SystemExit(main())
