"""Deterministic synthetic allocation/write races; no native services."""
import os
from pathlib import Path
from threading import Event, RLock, Thread, current_thread
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.memory import atomic_file


class AtomicAllocationTests(unittest.TestCase):
    def setUp(self):
        self.root = fixture_root()
        self.target = self.root / 'state.json'
        self.target.write_bytes(b'old')

    def start_writer(self, ready, release, done, errors):
        fsync = os.fsync

        def paused_fsync(fd):
            fsync(fd)
            ready.set()
            if not release.wait(2):
                raise RuntimeError('synthetic writer barrier timed out')

        def write():
            try:
                atomic_file.atomic_write(self.target, b'new complete state')
            except Exception as error:
                errors.append(error)
            finally:
                done.set()

        return paused_fsync, Thread(target=write, name='synthetic-atomic-writer')

    def test_uncoordinated_enumeration_reproduces_missing_atomic_temp(self):
        ready, release, done = Event(), Event(), Event()
        errors = []
        fsync, writer = self.start_writer(ready, release, done, errors)
        walk = os.walk

        def replacement_after_enumeration(root, **kwargs):
            for directory, directories, files in walk(root, **kwargs):
                if Path(directory) == self.root:
                    self.assertTrue(any(name.endswith('.tmp') for name in files))
                    release.set()
                    self.assertTrue(done.wait(2))
                yield directory, directories, files

        with patch.object(atomic_file.os, 'fsync', side_effect=fsync):
            writer.start()
            try:
                self.assertTrue(ready.wait(2))
                with self.assertRaises(FileNotFoundError):
                    for directory, _, files in replacement_after_enumeration(self.root):
                        for name in files:
                            (Path(directory) / name).lstat()
            finally:
                release.set()
                writer.join(2)
        self.assertFalse(writer.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.target.read_bytes(), b'new complete state')

    def test_shared_snapshot_blocks_replace_until_enumerated_temp_is_counted(self):
        ready, release, done, rename_attempted, replaced = [Event() for _ in range(5)]
        snapshot_entered = Event()
        errors = []
        fsync, writer = self.start_writer(ready, release, done, errors)
        lock, walk, replace = RLock(), os.walk, os.replace

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

        def replacement(source, target):
            replaced.set()
            return replace(source, target)

        def pause_after_enumeration(root, **kwargs):
            for directory, directories, files in walk(root, **kwargs):
                if Path(directory) == self.root:
                    self.assertTrue(snapshot_entered.is_set(), 'scanner must hold the shared lock')
                    self.assertTrue(any(name.endswith('.tmp') for name in files))
                    release.set()
                    self.assertTrue(rename_attempted.wait(2))
                    self.assertFalse(replaced.is_set())
                    self.assertFalse(done.is_set())
                yield directory, directories, files

        with patch.object(atomic_file, '_NAMESPACE_LOCK', ObservedLock()), \
             patch.object(atomic_file.os, 'fsync', side_effect=fsync), \
             patch.object(atomic_file.os, 'walk', side_effect=pause_after_enumeration), \
             patch.object(atomic_file.os, 'replace', side_effect=replacement):
            writer.start()
            try:
                self.assertTrue(ready.wait(2))
                expected_files = list(self.root.iterdir())
                expected_logical_bytes = sum(path.lstat().st_size for path in expected_files)
                usage = atomic_file.tree_usage(self.root)
                self.assertGreaterEqual(usage['files'], 2)
                self.assertEqual(usage['files'], len(expected_files))
                self.assertEqual(usage['logical_bytes'], expected_logical_bytes)
            finally:
                release.set()
                writer.join(2)
        self.assertFalse(writer.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(replaced.is_set())
        self.assertEqual(self.target.read_bytes(), b'new complete state')
        self.assertFalse(any(path.name.endswith('.tmp') and not path.name.startswith('._')
                             for path in self.root.iterdir()))

    def test_disappearing_stable_file_is_not_ignored(self):
        original = Path.lstat

        def missing(path, *args, **kwargs):
            if path == self.target:
                path.unlink()
            return original(path, *args, **kwargs)

        with patch.object(Path, 'lstat', missing):
            with self.assertRaises(FileNotFoundError):
                atomic_file.tree_usage(self.root)

    def test_disappearing_enumerated_directory_is_not_ignored(self):
        child = self.root / 'stable-directory'
        child.mkdir()
        walk = os.walk

        def missing_child(root, **kwargs):
            for directory, directories, files in walk(root, **kwargs):
                if Path(directory) == self.root:
                    self.assertIn(child.name, directories)
                    child.rmdir()
                yield directory, directories, files

        with patch.object(atomic_file.os, 'walk', side_effect=missing_child):
            with self.assertRaises(FileNotFoundError):
                atomic_file.tree_usage(self.root)

    def test_missing_root_and_walk_failure_are_not_zero_usage(self):
        with self.assertRaises(FileNotFoundError):
            atomic_file.tree_usage(self.root / 'missing')

        def denied_walk(root, **kwargs):
            kwargs['onerror'](PermissionError('synthetic denied subtree'))
            return iter(())

        with patch.object(atomic_file.os, 'walk', side_effect=denied_walk):
            with self.assertRaises(PermissionError):
                atomic_file.tree_usage(self.root)

    def test_file_directory_and_root_links_are_rejected(self):
        outside = fixture_root()
        payload = outside / 'keep.txt'
        payload.write_text('unchanged')
        for destination in (payload, outside):
            link = self.root / 'link'
            link.symlink_to(destination)
            with self.assertRaisesRegex(RuntimeError, 'unexpected_fixture_(entry|link)'):
                atomic_file.tree_usage(self.root)
            link.unlink()
        root_link = outside / 'root-link'
        root_link.symlink_to(self.root)
        try:
            with self.assertRaisesRegex(RuntimeError, 'unexpected_fixture_entry'):
                atomic_file.tree_usage(root_link)
        finally:
            root_link.unlink()
        self.assertEqual(payload.read_text(), 'unchanged')

    def test_failed_write_temp_and_appledouble_remain_counted(self):
        with patch.object(atomic_file.os, 'fsync', side_effect=OSError('synthetic fsync failure')):
            with self.assertRaisesRegex(OSError, 'synthetic fsync failure'):
                atomic_file.atomic_write(self.target, b'retained failed bytes')
        (self.root / '._state.json').write_bytes(b'AppleDouble fixture')
        paths = [self.root, *self.root.iterdir()]
        usage = atomic_file.tree_usage(self.root)
        self.assertGreaterEqual(usage['files'], 3)
        self.assertEqual(usage['files'], len(paths) - 1)
        self.assertEqual(usage['logical_bytes'], sum(path.lstat().st_size for path in paths[1:]))
        self.assertEqual(usage['allocated_bytes'], sum(path.lstat().st_blocks * 512 for path in paths))
        self.assertEqual(self.target.read_bytes(), b'old')
        failed = [path for path in self.root.glob('*.tmp') if not path.name.startswith('._')]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].read_bytes(), b'retained failed bytes')


if __name__ == '__main__':
    unittest.main()
