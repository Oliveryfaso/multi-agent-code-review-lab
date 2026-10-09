"""Explicit offline unittest selection with owned, bounded fixture output.

This is a test convenience guard, not a sandbox for untrusted test code.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import sys
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT / 'src'), str(PROJECT / 'tests'), str(PROJECT / 'scripts')]
from debugger_fixtures import RUNS_ROOT, fixture_session

MIB = 1024 * 1024
OFFLINE_MODULES = frozenset({
    'test_offline_tests', 'test_code_graph', 'test_repository_scope',
    'test_repository_snapshots', 'test_repository_index', 'test_context_reader',
    'test_static_readers', 'test_static_facts', 'test_contracts',
    'test_investigation_contracts', 'test_evidence_store', 'test_provider_contract',
    'test_check_results',
    'test_local_http_provider', 'test_investigation_budget', 'test_investigation_queue',
    'test_investigation_memory', 'test_run_store', 'test_run_recovery',
    'test_repo_map', 'test_patch_agent', 'test_patch_ranker',
})
OFFLINE_CASES = frozenset({
    'test_orchestrator.OrchestratorTests.test_canonical_graph_edge_kind_reaches_evidence_in_both_directions',
    'test_code_smell.CodeSmellAgentTests.test_appledouble_files_and_subtrees_are_filtered_before_read',
    'test_code_smell.CodeSmellAgentTests.test_real_python_syntax_error_is_still_reported',
    'test_static_baseline.StaticBaselineTests.test_real_cli_wrapper_runs_baseline_without_importing_source',
    'test_static_baseline.StaticBaselineTests.test_unseen_frames_do_not_create_unverified_citations',
    'test_static_baseline.StaticBaselineTests.test_strict_input_and_existing_output_fail_before_mutation',
})


class BoundedOutput(io.StringIO):
    def __init__(self, limit):
        super().__init__()
        self.limit, self.bytes_written = limit, 0

    def write(self, text):
        data = text.encode('utf-8', errors='replace')
        remaining = self.limit - self.bytes_written
        clipped = data[:remaining].decode('utf-8', errors='ignore')
        super().write(clipped)
        self.bytes_written += len(clipped.encode('utf-8'))
        if len(data) > remaining:
            raise RuntimeError('diagnostic_bytes_limit')
        return len(text)

    def writeln(self, text=''):
        self.write(text + '\n')


def tree_usage(root):
    """Count only this run, including directories and AppleDouble allocation."""
    usage = {'files': 0, 'logical_bytes': 0, 'allocated_bytes': 0}
    for directory, directories, files in os.walk(root, followlinks=False):
        for path in [Path(directory)] + [Path(directory) / name for name in files]:
            entry = path.lstat()
            if not (stat.S_ISDIR(entry.st_mode) or stat.S_ISREG(entry.st_mode)):
                raise RuntimeError('unexpected_fixture_entry')
            usage['allocated_bytes'] += entry.st_blocks * 512
            if stat.S_ISREG(entry.st_mode):
                usage['files'] += 1
                usage['logical_bytes'] += entry.st_size
        if any((Path(directory) / name).is_symlink() for name in directories):
            raise RuntimeError('unexpected_fixture_link')
    return usage


@contextmanager
def offline_context(tmp):
    from macr.investigation.snapshots import SnapshotStore
    from macr.investigation.index import RepositoryIndex
    from macr.tools.patch_verifier import PatchVerifierTool
    snapshot_init, index_init = SnapshotStore.__init__, RepositoryIndex.__init__
    verifier_init = PatchVerifierTool.__init__

    def snapshots(self, root=None):
        return snapshot_init(self, tmp / 'snapshots' if root is None else root)

    def indexes(self, store, cache_root=None):
        return index_init(self, store, tmp / 'index' if cache_root is None else cache_root)

    def verifier(self, profile=None, artifact_root=None):
        return verifier_init(self, profile, tmp / 'patch_checks' if artifact_root is None else artifact_root)

    with ExitStack() as stack:
        stack.enter_context(fixture_session(tmp))
        stack.enter_context(patch.object(SnapshotStore, '__init__', snapshots))
        stack.enter_context(patch.object(RepositoryIndex, '__init__', indexes))
        stack.enter_context(patch.object(PatchVerifierTool, '__init__', verifier))
        for target in ('subprocess.Popen', 'os.system', 'socket.create_connection',
                       'socket.socket.connect', 'socket.socket.connect_ex',
                       'urllib.request.urlopen', 'urllib.request.build_opener',
                       'http.client.HTTPConnection.request'):
            stack.enter_context(patch(target, side_effect=RuntimeError('native_dispatch_forbidden')))
        for name in ('fork', 'posix_spawn', 'posix_spawnp'):
            if hasattr(os, name):
                stack.enter_context(patch.object(os, name, side_effect=RuntimeError('native_dispatch_forbidden')))
        yield


def run_suite(suite, *, runs_root=RUNS_ROOT, timeout_seconds=120, max_allocated_bytes=64 * MIB):
    """Create one exclusive run; retain failures, clean only owned successful tmp."""
    if not 0 < timeout_seconds <= 120 or not 0 < max_allocated_bytes <= 64 * MIB:
        raise ValueError('offline limits may only be narrowed')
    runs_root = Path(runs_root).absolute()
    if runs_root != runs_root.resolve() or not runs_root.is_relative_to(RUNS_ROOT):
        raise ValueError('run output must stay under artifacts/test-runs without links')
    runs_root.mkdir(parents=True, exist_ok=True)
    root = runs_root / str(uuid4())
    root.mkdir()
    tmp = root / 'tmp'
    tmp.mkdir()
    owned = [(path.stat().st_dev, path.stat().st_ino) for path in (root, tmp)]
    started = time.monotonic()
    output = BoundedOutput(MIB)
    error = None
    peak = 0
    checking = False

    def check(*unused):
        nonlocal error, peak, checking
        if checking:
            return
        checking = True
        try:
            peak = max(peak, tree_usage(root)['allocated_bytes'])
            if time.monotonic() - started >= timeout_seconds:
                error = 'wall_seconds_limit'
            elif peak > max_allocated_bytes:
                error = 'allocated_bytes_limit'
            if error:
                raise RuntimeError(error)
        finally:
            checking = False

    class GuardedResult(unittest.TextTestResult):
        def startTest(self, test):
            check()
            super().startTest(test)

        def stopTest(self, test):
            super().stopTest(test)
            check()

    result = GuardedResult(output, True, 2)
    result.failfast = True
    expected = suite.countTestCases()
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    try:
        with offline_context(tmp), redirect_stdout(output), redirect_stderr(output):
            signal.signal(signal.SIGALRM, check)
            signal.setitimer(signal.ITIMER_REAL, min(1.0, timeout_seconds), min(1.0, timeout_seconds))
            check()
            suite.run(result)
            check()
    except (Exception, KeyboardInterrupt) as exc:
        error = error or str(exc) or type(exc).__name__
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0]:
            signal.setitimer(signal.ITIMER_REAL,
                             max(0.000001, previous_timer[0] - (time.monotonic() - started)), previous_timer[1])
    try:
        result.printErrors()
    except RuntimeError:
        error = error or 'diagnostic_bytes_limit'
    passed = not error and expected > 0 and result.testsRun == expected and result.wasSuccessful() and not result.skipped
    cleanup = 'retained_failure'
    if passed:
        def cleanup_error(function, path, info):
            # exFAT may remove a paired AppleDouble entry with its new fixture.
            if isinstance(info[1], FileNotFoundError) and Path(path).name.startswith('._'):
                return
            raise info[1]

        try:
            if root != root.resolve() or tmp != tmp.resolve() or owned != [
                    (path.stat().st_dev, path.stat().st_ino) for path in (root, tmp)]:
                raise RuntimeError('owned_tmp_identity_changed')
            shutil.rmtree(tmp, onerror=cleanup_error)
            cleanup = 'removed_owned_tmp'
        except OSError as exc:
            passed, error, cleanup = False, f'{type(exc).__name__}: {exc}', 'cleanup_failed'
        except RuntimeError as exc:
            passed, error, cleanup = False, str(exc), 'cleanup_refused'
    report = {
        'status': 'passed' if passed else 'failed', 'run_dir': str(root),
        'tests_expected': expected, 'tests_run': result.testsRun,
        'failed_tests': [test.id() for test, _ in result.failures + result.errors],
        'skipped': len(result.skipped), 'error': error, 'cleanup': cleanup,
        'elapsed_seconds': round(time.monotonic() - started, 6),
        'allocated_peak_bytes': peak, 'limits': {'wall_seconds': timeout_seconds,
        'allocated_bytes': max_allocated_bytes, 'record_bytes': 2 * MIB},
        'measurement': 'fixture_output_only_not_model_or_vm',
    }
    diagnostic = output.getvalue().encode('utf-8')
    payload = (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    if len(diagnostic) + len(payload) > 2 * MIB:
        raise RuntimeError(f'record_bytes_limit; retained run: {root}')
    for name, data in (('diagnostic.txt', diagnostic), ('summary.json', payload)):
        with (root / name).open('xb') as stream:
            stream.write(data)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tests', nargs='+', help='explicit allowlisted module, class or test name')
    args = parser.parse_args()
    for label in args.tests:
        parts = label.split('.')
        if not all(part.isidentifier() for part in parts) or (
                parts[0] not in OFFLINE_MODULES and label not in OFFLINE_CASES):
            parser.error(f'not in offline allowlist: {label}')
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(args.tests)
    if loader.errors:
        parser.error('\n'.join(loader.errors))
    report = run_suite(suite)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
