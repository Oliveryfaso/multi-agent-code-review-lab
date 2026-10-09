"""Lifecycle checks using only this run's newly created synthetic files."""
from contextvars import ContextVar
import importlib
import json
from pathlib import Path
import time
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

    def test_allocation_threshold_stops_before_tests_and_keeps_run(self):
        runner = self.runner()
        base = fixtures.fixture_root()
        called = []
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


if __name__ == '__main__':
    unittest.main()
