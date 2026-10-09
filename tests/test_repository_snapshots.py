import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from debugger_fixtures import fixture_root, write_files
from macr.investigation.records import ScopeProfile, TaskSpec
from macr.investigation.scope import RepositoryScope

try:
    from macr.investigation.snapshots import SnapshotStore, SnapshotError
except ModuleNotFoundError:
    SnapshotStore = SnapshotError = None


class RepositorySnapshotTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(SnapshotStore, "content-bound snapshot store missing")
        self.root = write_files(fixture_root(), {"a.py": "x=1\n", ".env": "DO_NOT_READ"})
        self.scope = RepositoryScope().inventory(self.root, ScopeProfile())
        self.store = SnapshotStore(fixture_root() / "snapshots")

    def test_canonical_capture_and_same_size_restored_mtime_drift(self):
        bundle = self.store.capture(self.root, self.scope)
        second = self.store.capture(self.root, self.scope)
        self.assertEqual(bundle.ref.content_id, second.ref.content_id)
        self.assertNotEqual(bundle.ref.snapshot_id, second.ref.snapshot_id)
        self.assertEqual(self.store.read_verified(bundle.ref, "a.py"), b"x=1\n")
        file = Path(bundle.ref.root_ref) / "a.py"
        stamp = file.stat()
        file.write_bytes(b"x=2\n")
        os.utime(file, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        with self.assertRaises(SnapshotError) as caught:
            self.store.verify(bundle.ref)
        self.assertEqual(caught.exception.code, "stale_snapshot")

    def test_capture_race_does_not_publish_stable_snapshot(self):
        with patch("macr.investigation.snapshots.read_safe", side_effect=[b"x=1\n", b"x=2\n"]):
            with self.assertRaises(SnapshotError) as caught:
                self.store.capture(self.root, self.scope)
        self.assertEqual(caught.exception.code, "snapshot_unstable")

    def test_manifest_scope_identity_and_export_are_bound(self):
        bundle = self.store.capture(self.root, self.scope)
        self.assertEqual(set(bundle.files), {"a.py"})
        exported = fixture_root() / "export"
        manifest = self.store.export_verified(bundle.ref, exported)
        self.assertEqual([f["path"] for f in manifest.files], ["a.py"])
        self.assertEqual((exported / "a.py").read_bytes(), b"x=1\n")
        with self.assertRaises(SnapshotError):
            self.store.verify(replace(bundle.ref, content_id="wrong"))
        with self.assertRaises(SnapshotError):
            self.store.verify(replace(bundle.ref, scope_version="other"))
        with self.assertRaises(SnapshotError):
            self.store.read_verified(bundle.ref, ".env")

    def test_excluded_files_never_read_and_capture_limits_rechecked(self):
        original = Path.read_bytes
        def checked(path):
            self.assertNotEqual(path.name, ".env")
            return original(path)
        with patch.object(Path, "read_bytes", checked):
            self.store.capture(self.root, self.scope)
        profile = replace(ScopeProfile(), max_python_lines=2)
        scope = RepositoryScope().inventory(self.root, profile)
        (self.root / "a.py").write_text("\n\n\n\n")  # same bytes, new scope line count
        with self.assertRaises(SnapshotError) as caught:
            self.store.capture(self.root, scope)
        self.assertEqual(caught.exception.code, "scope_limit")

    def test_prepare_attaches_snapshot_without_repository_execution(self):
        task = TaskSpec("run-1", "scan")
        # Default store is under this project's artifacts.
        bundle = RepositoryScope().prepare(task, self.root, ScopeProfile())
        self.assertEqual(task.snapshot_ref, bundle.ref)
        self.assertGreater(bundle.scope.bytes_read, 0)

    def test_changed_inventory_rejects_old_scope(self):
        (self.root / "new.py").write_text("x=2\n")
        with self.assertRaises(SnapshotError) as caught:
            self.store.capture(self.root, self.scope)
        self.assertEqual(caught.exception.code, "snapshot_unstable")
