"""Real comparison entry points with synthetic captures and coordinated writes."""
import json
import os
from pathlib import Path
from threading import Event, RLock, Thread, current_thread
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.memory import atomic_file
import test_diagnostic_compare as comparison_fixtures


class DiagnosticAllocationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = comparison_fixtures.DiagnosticCompareTests()
        self.fixture.setUp()
        self.compare, self.root = self.fixture.compare, self.fixture.root

    def test_actual_entries_share_the_atomic_writer_module_and_lock(self):
        self.assertIs(self.compare.atomic_write, atomic_file.atomic_write)
        self.assertIs(self.compare.tree_usage, atomic_file.tree_usage)
        self.assertIs(self.compare.atomic_write.__globals__, atomic_file.__dict__)
        self.assertIs(self.compare.tree_usage.__globals__, atomic_file.__dict__)
        self.assertEqual(self.fixture.prepare()['new_model_calls'], 0)
        row = self.fixture.record()
        self.assertEqual(row['failure_class'], 'semantic_pending')
        report = self.compare.compare_results(self.root)
        paths = self.compare.save_comparison(self.root, report)
        self.assertEqual(json.loads(Path(paths['json']).read_text()), report)
        self.assertTrue(Path(paths['markdown']).is_file())
        self.assertEqual(report['new_model_calls'], 0)

    def test_record_response_snapshot_blocks_a_real_atomic_replacement(self):
        self.fixture.prepare()
        target = self.root / 'synthetic-observation.json'
        target.write_bytes(b'old')
        ready, release, done, rename_attempted, replaced, snapshot_entered = [Event() for _ in range(6)]
        errors = []
        lock, walk, fsync, replace = RLock(), os.walk, os.fsync, os.replace
        triggered = False

        def write():
            try:
                atomic_file.atomic_write(target, b'new complete observation')
            except Exception as error:
                errors.append(error)
            finally:
                done.set()

        writer = Thread(target=write, name='synthetic-comparison-writer')

        class ObservedLock:
            writer_entries = 0

            def __enter__(self):
                if current_thread() is writer:
                    self.writer_entries += 1
                    if self.writer_entries == 2:
                        rename_attempted.set()
                lock.acquire()
                if current_thread() is not writer:
                    snapshot_entered.set()

            def __exit__(self, *unused):
                lock.release()

        def paused_fsync(fd):
            fsync(fd)
            if current_thread() is writer:
                ready.set()
                if not release.wait(2):
                    raise RuntimeError('synthetic writer barrier timed out')

        def observed_replace(source, destination):
            if current_thread() is writer:
                replaced.set()
            return replace(source, destination)

        def replacement_after_enumeration(root, **kwargs):
            nonlocal triggered
            for directory, directories, files in walk(root, **kwargs):
                if Path(directory) == self.root and not triggered:
                    triggered = True
                    self.assertTrue(snapshot_entered.is_set())
                    self.assertTrue(any(name.startswith(target.name + '-') and name.endswith('.tmp')
                                        for name in files))
                    release.set()
                    self.assertTrue(rename_attempted.wait(2))
                    self.assertFalse(replaced.is_set())
                    self.assertFalse(done.is_set())
                yield directory, directories, files

        with patch.object(atomic_file, '_NAMESPACE_LOCK', ObservedLock()), \
             patch.object(atomic_file.os, 'fsync', side_effect=paused_fsync), \
             patch.object(atomic_file.os, 'replace', side_effect=observed_replace), \
             patch.object(atomic_file.os, 'walk', side_effect=replacement_after_enumeration):
            writer.start()
            try:
                self.assertTrue(ready.wait(2))
                row = self.fixture.record()
                self.assertTrue(triggered, 'record_response must use the strict snapshot')
            finally:
                release.set()
                writer.join(2)
        self.assertFalse(writer.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(replaced.is_set())
        self.assertEqual(target.read_bytes(), b'new complete observation')
        state = json.loads((self.root / 'state.json').read_text())
        self.assertEqual(state['rows'], [{'cell': row['cell'], 'record_sha256': row['record_sha256']}])
        self.assertIsNone(state['pending'])
        self.assertFalse(state['stopped'])

    def test_prepare_comparison_rejects_an_enumerated_file_disappearing(self):
        stable = self.root / 'stable-file.json'
        walk = os.walk

        def disappearing_file(root, **kwargs):
            if Path(root) == self.root:
                stable.write_bytes(b'stable fixture')
            for directory, directories, files in walk(root, **kwargs):
                if Path(directory) == self.root:
                    self.assertIn(stable.name, files)
                    stable.unlink()
                yield directory, directories, files

        with patch.object(atomic_file.os, 'walk', side_effect=disappearing_file):
            with self.assertRaises(FileNotFoundError):
                self.fixture.prepare()
        self.assertTrue(self.root.is_dir())
        self.assertFalse((self.root / 'manifest.json').exists())

    def test_record_response_missing_file_fails_without_replay_or_row(self):
        self.fixture.prepare()
        stable = self.root / 'stable-file.json'
        stable.write_bytes(b'stable fixture')
        before = (self.root / 'state.json').read_bytes()
        lstat = Path.lstat

        def disappearing_file(path, *args, **kwargs):
            if path == stable:
                stable.unlink()
            return lstat(path, *args, **kwargs)

        with patch.object(Path, 'lstat', disappearing_file):
            with self.assertRaises(FileNotFoundError):
                self.fixture.record()
        self.assertEqual((self.root / 'state.json').read_bytes(), before)
        self.assertTrue((self.root / 'model-a--defect.claim').is_file())
        self.assertFalse((self.root / 'model-a--defect.json').exists())
        with self.assertRaisesRegex(ValueError, 'comparison_consumed_or_stopped'):
            self.fixture.record()

    def test_report_markdown_snapshot_rejects_a_disappearing_file(self):
        self.fixture.prepare()
        self.fixture.record()
        report = self.compare.compare_results(self.root)
        stable = self.root / 'stable-file.json'
        stable.write_bytes(b'stable fixture')
        calls = 0
        scan, lstat = atomic_file.tree_usage, Path.lstat

        def disappearing_file(path, *args, **kwargs):
            if path == stable and calls == 2:
                stable.unlink()
            return lstat(path, *args, **kwargs)

        def observed_scan(root):
            nonlocal calls
            calls += 1
            return scan(root)

        with patch.object(self.compare, 'tree_usage', side_effect=observed_scan), \
             patch.object(Path, 'lstat', disappearing_file):
            with self.assertRaises(FileNotFoundError):
                self.compare.save_comparison(self.root, report)
        reports = list(self.root.glob('report-*'))
        self.assertEqual(len(reports), 1)
        self.assertTrue((reports[0] / 'comparison.json').is_file())
        self.assertFalse((reports[0] / 'comparison.md').exists())

    def test_record_response_rejects_nested_links_without_following_them(self):
        self.fixture.prepare()
        outside = fixture_root()
        payload = outside / 'unchanged.txt'
        payload.write_bytes(b'unchanged')
        link = self.root / 'linked-directory'
        link.symlink_to(outside)
        try:
            with self.assertRaisesRegex(RuntimeError, 'unexpected_fixture_link'):
                self.fixture.record()
        finally:
            link.unlink()
        self.assertEqual(payload.read_bytes(), b'unchanged')


if __name__ == '__main__':
    unittest.main()
