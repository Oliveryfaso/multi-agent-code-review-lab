"""Trusted offline storage tests. Source is parsed, never imported/executed."""
from dataclasses import replace
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root, write_files
from macr.investigation import repair_io
from macr.investigation.records import ScopeProfile
from macr.investigation.scope import RepositoryScope, read_safe, resolve_allowed, open_directory, safe_parent, walk_entries
from macr.investigation.snapshots import SnapshotStore, SnapshotError
from macr.investigation.validation import ContractError
from macr.memory.atomic_file import atomic_write


class RepairIOTests(unittest.TestCase):
    def test_json_duplicate_nonfinite_depth_and_size_boundaries(self):
        self.assertEqual(repair_io.load_json('{"a":1}'), {'a':1})
        for raw in ('{"a":1,"a":2}', '{"a":NaN}', '[1,]'):
            with self.subTest(raw=raw), self.assertRaises(ContractError): repair_io.load_json(raw)
        with self.assertRaises(ContractError): repair_io.load_json('[[[0]]]', max_depth=1)
        with self.assertRaises(ContractError): repair_io.encode_bounded({'a':'x'*32}, 8)
        with self.assertRaises(ContractError): repair_io.bounded_json({'a':float('inf')})
        with self.assertRaises(ContractError): repair_io.bounded_json({'a':'中'},max_bytes=11)

    def test_syntax_compiles_without_executing_and_rejects_invalid_python(self):
        body=b'raise RuntimeError("must never run")\n'
        text, _ = repair_io._syntax(body, 'safe.py')
        self.assertEqual(text, body.decode())
        for raw in (b'def f(__debug__):\n    return True\n', b'break\n', b'await f()\n', b'x=\x00'):
            with self.subTest(raw=raw), self.assertRaises(ContractError): repair_io._syntax(raw,'bad.py')
        with self.assertRaises(ContractError): repair_io._syntax(b'x'*16385,'large.py')

    def test_test_names_and_hashes_are_deterministic(self):
        body=b'import unittest\nclass Checks(unittest.TestCase):\n    def test_b(self): pass\n    def test_a(self): pass\n'
        self.assertEqual(repair_io._test_names(body), ['Checks.test_a','Checks.test_b'])
        self.assertEqual(list(repair_io._hashes({'b.py':b'b','a.py':b'a'})),['a.py','b.py'])

    def test_safe_atomic_write_uses_private_files_and_rejects_aliases(self):
        root=fixture_root()
        repair_io.safe_write(root,'nested/record.json',b'{}',limit=2)
        self.assertEqual(read_safe(root,'nested/record.json',2),b'{}')
        self.assertEqual((root/'nested/record.json').stat().st_mode & 0o777,0o600)
        for path in ('../outside','/absolute','nested//bad'):
            with self.subTest(path=path),self.assertRaises(ContractError):repair_io.safe_write(root,path,b'x',limit=1)
        outside=fixture_root();(root/'alias').symlink_to(outside,target_is_directory=True)
        try:
            with self.assertRaises((ContractError,OSError)):repair_io.safe_write(root,'alias/escaped',b'x',limit=1)
        finally: (root/'alias').unlink()
        self.assertFalse((outside/'escaped').exists())

    def test_anchored_read_rejects_symlink_ancestor_and_nonregular_file(self):
        root=fixture_root();real=write_files(root/'real',{'a.py':'x=1\n'})
        scope=RepositoryScope().inventory(real,ScopeProfile())
        (root/'alias').symlink_to(real,target_is_directory=True)
        try:
            for operation in (lambda:read_safe(root/'alias','a.py',32),lambda:repair_io._root(root/'alias'),
                              lambda:resolve_allowed(root/'alias','a.py',scope)):
                with self.assertRaises((OSError,ContractError)):operation()
        finally: (root/'alias').unlink()
        with self.assertRaises((OSError,ContractError)):read_safe(root,'real',32)

    def test_atomic_dirfd_failure_retains_temporary_and_old_data(self):
        root=fixture_root();(root/'a').write_bytes(b'old')
        with open_directory(root) as fd:
            with patch('macr.memory.atomic_file.os.replace',side_effect=OSError('synthetic')):
                with self.assertRaises(OSError):atomic_write(Path('a'),b'new',dir_fd=fd)
            self.assertEqual(read_safe(root,'a',3),b'old')
            with self.assertRaises(ValueError):atomic_write(Path('nested/a'),b'x',dir_fd=fd)
        self.assertEqual(len(list(root.glob('a-*.tmp'))),1)

    def test_custom_allowed_root_snapshot_binds_capture_and_export(self):
        root=fixture_root();source=write_files(root/'source',{'a.py':'x=1\n'})
        allowed=root/'owned';allowed.mkdir()
        store=SnapshotStore(allowed/'snapshots',allowed_root=allowed)
        scope=RepositoryScope().inventory(source,ScopeProfile())
        bundle=store.capture(source,scope)
        store.verify(bundle.ref)
        self.assertEqual(store.read_verified(bundle.ref,'a.py'),b'x=1\n')
        store.export_verified(bundle.ref,allowed/'export')
        self.assertEqual((allowed/'export/a.py').read_bytes(),b'x=1\n')
        with self.assertRaises(SnapshotError):SnapshotStore(root/'other',allowed_root=allowed)
        with self.assertRaises(SnapshotError):store.export_verified(bundle.ref,root/'outside')
        (Path(bundle.ref.root_ref)/'a.py').write_bytes(b'x=2\n')
        with self.assertRaises(SnapshotError):store.verify(bundle.ref)

    def test_walk_profile_prunes_excluded_directories_without_reading_source(self):
        root=write_files(fixture_root(),{'a.py':'x=1','skip/hidden.py':'x=2'})
        profile=replace(ScopeProfile(),exclude_patterns=['skip'])
        names=[n for n,_,_ in walk_entries(root,profile)]
        self.assertIn('skip',names);self.assertNotIn('skip/hidden.py',names)

    def test_snapshot_scope_growth_stops_before_overbudget_file_write(self):
        root=fixture_root();source=write_files(root/'source',{'a.py':'x=1\n','b.py':'x=2\n'})
        profile=replace(ScopeProfile(),max_text_bytes=16)
        scope=RepositoryScope().inventory(source,profile)
        (source/'a.py').write_text('x=1\n'*4)
        store=SnapshotStore(fixture_root()/'snapshots')
        with self.assertRaises(SnapshotError):store.capture(source,scope)
        folder=next(store.root.iterdir())
        self.assertFalse((folder/'files/b.py').exists())


if __name__=='__main__':unittest.main()
