"""Lifecycle checks using only this run's newly created synthetic files."""
from contextvars import ContextVar
from contextlib import nullcontext
import importlib
import json
from pathlib import Path
import signal
import stat
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import debugger_fixtures as fixtures


def synthetic_suite(*actions):
    class SyntheticTest(unittest.TestCase):
        def runTest(self):
            self.action(self)

    cases = []
    for action in actions:
        case = SyntheticTest()
        case.action = action
        cases.append(case)
    return unittest.TestSuite(cases)


class OfflineLifecycleTests(unittest.TestCase):
    def runner(self):
        self.assertTrue((fixtures.PROJECT / 'scripts/run_offline_tests.py').is_file(),
                        'bounded offline entry is missing')
        return importlib.import_module('run_offline_tests')

    def test_unmanaged_fixtures_reject_before_writing(self):
        with patch.object(fixtures, '_ACTIVE_ROOT', ContextVar('unmanaged', default=None), create=True), \
             patch.object(Path, 'mkdir') as mkdir:
            try:
                fixtures.fixture_root()
            except RuntimeError:
                pass
            else:
                self.fail('fixture_root must reject an unmanaged run')
            mkdir.assert_not_called()

    def test_session_allocates_unique_children_and_restores_previous_root(self):
        outer = fixtures.fixture_root()
        first = fixtures.fixture_root()
        with fixtures.fixture_session(outer):
            child = fixtures.fixture_root()
            self.assertEqual(child.parent, outer)
        following = fixtures.fixture_root()
        self.assertEqual(first.parent, following.parent)
        self.assertNotEqual(first, following)

    def test_session_rejects_existing_project_directory_before_writing(self):
        with patch.object(Path, 'mkdir') as mkdir:
            with self.assertRaises(ValueError):
                with fixtures.fixture_session(fixtures.PROJECT / 'tests'):
                    fixtures.fixture_root()
            mkdir.assert_not_called()

    def test_success_cleans_only_its_tmp_and_runs_do_not_overlap(self):
        runner = self.runner()
        base = fixtures.fixture_root()
        sentinel = base / 'preexisting.txt'
        sentinel.write_text('keep synthetic sentinel', encoding='utf-8')

        def action(case):
            path = fixtures.write_files(fixtures.fixture_root(), {'tiny.py': 'x = 1\n'})
            case.assertEqual((path / 'tiny.py').read_text(), 'x = 1\n')

        one = runner.run_suite(synthetic_suite(action), runs_root=base / 'runs')
        two = runner.run_suite(synthetic_suite(action), runs_root=base / 'runs')
        self.assertEqual((one['status'], two['status']), ('passed', 'passed'))
        self.assertNotEqual(one['run_dir'], two['run_dir'])
        for report in (one, two):
            root = Path(report['run_dir'])
            self.assertFalse((root / 'tmp').exists())
            self.assertEqual(json.loads((root / 'summary.json').read_text())['status'], 'passed')
        self.assertEqual(sentinel.read_text(), 'keep synthetic sentinel')

    def test_first_run_creates_missing_artifact_parents(self):
        runner = self.runner()
        base = fixtures.fixture_root()
        report = runner.run_suite(synthetic_suite(lambda case: case.assertTrue(True)),
                                  runs_root=base / 'fresh-project/artifacts/test-runs')
        self.assertEqual(report['status'], 'passed')

    def test_failure_keeps_locatable_fixture_and_diagnostic(self):
        runner = self.runner()
        base = fixtures.fixture_root()

        def action(case):
            fixtures.write_files(fixtures.fixture_root(), {'evidence.txt': 'synthetic failure'})
            case.fail('intentional synthetic failure')

        report = runner.run_suite(synthetic_suite(action), runs_root=base / 'runs')
        root = Path(report['run_dir'])
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(len(list((root / 'tmp').rglob('evidence.txt'))), 1)
        self.assertIn('intentional synthetic failure', (root / 'diagnostic.txt').read_text())
        self.assertTrue(report['failed_tests'])

    def test_native_dispatch_is_blocked_and_default_stores_stay_in_tmp(self):
        runner = self.runner()
        base = fixtures.fixture_root()

        def action(case):
            import socket
            import subprocess
            from macr.investigation.snapshots import SnapshotStore
            from macr.investigation.index import RepositoryIndex
            from macr.tools.patch_verifier import PatchVerifierTool
            with case.assertRaises(RuntimeError):
                subprocess.Popen(['never-execute'])
            with case.assertRaises(RuntimeError):
                socket.create_connection(('127.0.0.1', 1))
            active = fixtures.fixture_root().parent
            store = SnapshotStore()
            index = RepositoryIndex(store)
            case.assertTrue(store.root.is_relative_to(active))
            case.assertTrue(index.cache_root.is_relative_to(active))
            case.assertTrue(PatchVerifierTool().root.is_relative_to(active))

        report = runner.run_suite(synthetic_suite(action), runs_root=base / 'runs')
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['limits']['allocated_bytes'], 64 * runner.MIB)

    def test_owned_store_wrappers_forward_profile_budget_and_confine_authority(self):
        runner = self.runner()
        from macr.investigation.snapshots import SnapshotStore
        from macr.investigation.index import RepositoryIndex
        base = fixtures.fixture_root()
        allowed = base / 'allowed'
        allowed.mkdir()
        profile, budget = object(), object()
        calls = []

        def snapshot_init(self, root=None, **kwargs):
            calls.append(('snapshot', root, kwargs))

        def index_init(self, store, cache_root=None, **kwargs):
            calls.append(('index', cache_root, kwargs))

        with patch.object(SnapshotStore, '__init__', snapshot_init), \
             patch.object(RepositoryIndex, '__init__', index_init), \
             runner.offline_context(base):
            store = SnapshotStore(allowed / 'snapshots', allowed_root=allowed,
                                  profile=profile, budget=budget)
            RepositoryIndex(store, allowed_root=allowed, profile=profile, budget=budget)
            self.assertEqual(calls, [
                ('snapshot', allowed / 'snapshots',
                 {'allowed_root': allowed, 'profile': profile, 'budget': budget}),
                ('index', None, {'allowed_root': allowed, 'profile': profile, 'budget': budget}),
            ])
            for constructor, arguments in ((SnapshotStore, ()), (RepositoryIndex, (store,))):
                with self.assertRaisesRegex(RuntimeError, 'authority must stay inside managed output'):
                    constructor(*arguments, allowed_root=base.parent,
                                profile=profile, budget=budget)
            self.assertEqual(len(calls), 2)

    def test_explicit_128mib_budget_keeps_native_dispatch_blocked(self):
        runner = self.runner()
        base = fixtures.fixture_root()

        def action(case):
            import socket
            import subprocess
            fixtures.write_files(fixtures.fixture_root(), {'tiny.txt': 'tiny'})
            with case.assertRaisesRegex(RuntimeError, 'native_dispatch_forbidden'):
                subprocess.Popen(['never-execute'])
            with case.assertRaisesRegex(RuntimeError, 'native_dispatch_forbidden'):
                socket.create_connection(('127.0.0.1', 1))

        # Exercise the boundary without physically allocating 128 MiB.
        with patch.object(runner, 'tree_usage', return_value={
                'files': 1, 'logical_bytes': 4, 'allocated_bytes': 128 * runner.MIB}):
            report = runner.run_suite(synthetic_suite(action), runs_root=base / 'runs',
                                      max_allocated_bytes=128 * runner.MIB)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['limits']['allocated_bytes'], 128 * runner.MIB)
        self.assertEqual(report['allocated_peak_bytes'], 128 * runner.MIB)
        self.assertEqual(report['cleanup'], 'removed_owned_tmp')

    def test_budget_above_128mib_rejects_before_mutation_or_dispatch(self):
        runner = self.runner()
        base = fixtures.fixture_root()
        called = []
        with patch.object(Path, 'mkdir') as mkdir, \
             patch.object(runner, 'offline_context') as context, \
             patch('subprocess.Popen') as process, \
             patch('socket.create_connection') as socket:
            with self.assertRaisesRegex(ValueError, 'allocated bytes <= 128 MiB'):
                runner.run_suite(synthetic_suite(lambda case: called.append(True)),
                                 runs_root=base / 'runs',
                                 max_allocated_bytes=128 * runner.MIB + 1)
            mkdir.assert_not_called()
            context.assert_not_called()
            process.assert_not_called()
            socket.assert_not_called()
        self.assertFalse(called)

    def test_allocation_threshold_stops_before_tests_and_keeps_run(self):
        runner = self.runner()
        base = fixtures.fixture_root()
        called = []
        # Empty-directory st_blocks may be zero on APFS. Inject a measured usage
        # above the cap so this guard test does not depend on filesystem layout.
        with patch.object(runner, 'tree_usage', return_value={
                'files': 0, 'logical_bytes': 0, 'allocated_bytes': 2}):
            report = runner.run_suite(synthetic_suite(lambda case: called.append(True)),
                                      runs_root=base / 'runs', max_allocated_bytes=1)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['error'], 'allocated_bytes_limit')
        self.assertFalse(called)
        self.assertTrue((Path(report['run_dir']) / 'tmp').is_dir())

    def test_deadline_stops_later_test_and_output_buffer_is_bounded(self):
        runner = self.runner()
        base = fixtures.fixture_root()
        called = []
        report = runner.run_suite(synthetic_suite(lambda case: time.sleep(0.08),
                                                 lambda case: called.append(True)),
                                  runs_root=base / 'runs', timeout_seconds=0.02)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['error'], 'wall_seconds_limit')
        self.assertFalse(called)
        output = runner.BoundedOutput(16)
        with self.assertRaises(RuntimeError):
            output.write('x' * 32)
        self.assertLessEqual(len(output.getvalue().encode('utf-8')), 16)

    def test_resource_meter_counts_links_without_following_their_targets(self):
        runner = self.runner()
        from macr.memory.atomic_file import tree_usage as production_tree_usage
        root, outside = fixtures.fixture_root(), fixtures.fixture_root()
        (root / 'tiny.txt').write_bytes(b'tiny')
        (outside / 'unmetered.txt').write_bytes(b'x' * 8192)
        links = [root / name for name in ('file-link', 'directory-link', 'cycle-a', 'cycle-b')]
        links[0].symlink_to(outside / 'unmetered.txt')
        links[1].symlink_to(outside, target_is_directory=True)
        links[2].symlink_to(links[3]); links[3].symlink_to(links[2])
        try:
            with self.assertRaisesRegex(RuntimeError, 'unexpected_fixture_(entry|link)'):
                production_tree_usage(root)
            usage = runner.tree_usage(root)
            self.assertEqual(usage['files'], 1)
            self.assertEqual(usage['logical_bytes'], 4)
            self.assertEqual(usage['allocated_bytes'], sum(
                path.lstat().st_blocks * 512 for path in [root, root / 'tiny.txt', *links]))
        finally:
            for link in links: link.unlink()

    def test_fixture_meter_holds_the_atomic_namespace_lock_during_enumeration(self):
        runner = self.runner()
        from macr.memory import atomic_file
        root = fixtures.fixture_root()
        (root / 'tiny.txt').write_bytes(b'tiny')
        real_scandir = runner.os.scandir
        state = SimpleNamespace(depth=0, scans=0)

        class ObservedLock:
            def __enter__(self):
                state.depth += 1

            def __exit__(self, *unused):
                state.depth -= 1

        def scandir(path):
            self.assertGreater(state.depth, 0, 'fixture meter must share the atomic writer lock')
            state.scans += 1
            return real_scandir(path)

        with patch.object(atomic_file, '_NAMESPACE_LOCK', ObservedLock()), \
             patch.object(runner.os, 'scandir', scandir):
            self.assertEqual(runner.tree_usage(root)['logical_bytes'], 4)
        self.assertGreater(state.scans, 0)
        self.assertEqual(state.depth, 0)

    def test_fixture_meter_rejects_a_directory_swapped_to_a_link_before_open(self):
        runner = self.runner()
        root, outside = fixtures.fixture_root(), fixtures.fixture_root()
        child = root / 'child'
        child.mkdir()
        (outside / 'unmetered.txt').write_bytes(b'x' * 8192)
        real_open, real_close = runner.os.open, runner.os.close
        explicit_root_fd = None
        swapped = []

        def open_directory(path, flags, *args, **kwargs):
            nonlocal explicit_root_fd
            if path == child.name and explicit_root_fd is not None and kwargs.get('dir_fd') == explicit_root_fd:
                child.rmdir()
                child.symlink_to(outside, target_is_directory=True)
                swapped.append(True)
            fd = real_open(path, flags, *args, **kwargs)
            if path == root and kwargs.get('dir_fd') is None:
                explicit_root_fd = fd
            return fd

        def close_directory(fd):
            nonlocal explicit_root_fd
            if fd == explicit_root_fd:
                explicit_root_fd = None
            return real_close(fd)

        try:
            with patch.object(runner.os, 'open', open_directory), \
                 patch.object(runner.os, 'close', close_directory):
                with self.assertRaises(OSError):
                    runner.tree_usage(root)
            self.assertEqual(swapped, [True])
            self.assertEqual((outside / 'unmetered.txt').stat().st_size, 8192)
        finally:
            if child.is_symlink():
                child.unlink()

    def test_timer_meter_allows_a_live_negative_symlink_fixture(self):
        runner = self.runner()
        base = fixtures.fixture_root()

        def action(case):
            root = fixtures.fixture_root()
            a, b = root / 'cycle-a', root / 'cycle-b'
            a.symlink_to(b); b.symlink_to(a)
            try:
                # Force the production timer callback while the links exist,
                # rather than depending on host speed or an arbitrary sleep.
                signal.raise_signal(signal.SIGALRM)
                case.assertTrue(a.is_symlink() and b.is_symlink())
            finally:
                a.unlink(); b.unlink()

        report = runner.run_suite(synthetic_suite(action), runs_root=base / 'runs')
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['cleanup'], 'removed_owned_tmp')

    def test_resource_meter_still_rejects_a_link_root(self):
        runner = self.runner()
        root, parent = fixtures.fixture_root(), fixtures.fixture_root()
        alias = parent / 'root-link'; alias.symlink_to(root, target_is_directory=True)
        try:
            with self.assertRaisesRegex(RuntimeError, 'unexpected_fixture_entry'):
                runner.tree_usage(alias)
        finally:
            alias.unlink()

    def test_resource_meter_rejects_special_entry_metadata(self):
        runner = self.runner()
        # No real FIFO exists for the outer timer to encounter. Only the
        # explicit inner scan sees this synthetic, nonexistent directory.
        sentinel = fixtures.fixture_root() / 'metadata-only-not-created'
        real_lstat, real_scandir = Path.lstat, runner.os.scandir
        real_open, real_fstat, real_close = runner.os.open, runner.os.fstat, runner.os.close
        synthetic_fd = -1
        directory = SimpleNamespace(st_mode=stat.S_IFDIR, st_blocks=0, st_dev=0, st_ino=0)
        special = SimpleNamespace(stat=lambda *, follow_symlinks:
                                  SimpleNamespace(st_mode=stat.S_IFIFO, st_blocks=0))

        def lstat(path, *args, **kwargs):
            return directory if path == sentinel else real_lstat(path, *args, **kwargs)

        def scandir(path):
            return nullcontext(iter([special])) if path == synthetic_fd else real_scandir(path)

        def open_directory(path, *args, **kwargs):
            return synthetic_fd if path == sentinel else real_open(path, *args, **kwargs)

        def fstat(fd):
            return directory if fd == synthetic_fd else real_fstat(fd)

        def close(fd):
            if fd != synthetic_fd:
                return real_close(fd)

        with patch.object(Path, 'lstat', lstat), \
             patch.object(runner.os, 'open', open_directory), \
             patch.object(runner.os, 'fstat', fstat), \
             patch.object(runner.os, 'close', close), \
             patch.object(runner.os, 'scandir', scandir):
            with self.assertRaisesRegex(RuntimeError, 'unexpected_fixture_entry'):
                runner.tree_usage(sentinel)


if __name__ == '__main__':
    unittest.main()
