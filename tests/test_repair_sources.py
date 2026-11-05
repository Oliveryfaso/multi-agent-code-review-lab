"""Owned fixed source strings only; read declarations without repository walking."""
from copy import deepcopy
from hashlib import sha256
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.investigation.validation import ContractError

try:
    from macr.investigation import repair_sources as sources
except ImportError:
    sources = None


class RepairSourcesTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(sources, 'declared committed source reader is missing')

    def hashes(self, files):
        return {path: sha256(body).hexdigest() for path, body in files.items()}

    def write(self, files):
        root = fixture_root()
        for relative, body in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        return root

    def test_only_committed_files_are_read_without_walking_or_importing_target(self):
        files = {'pkg/util.py': b'value = 2\r\n', 'app.py':
                 b'raise RuntimeError("owned source must never execute")\n'
                 b'import nonexistent_owned_fixture_dependency\nvalue = 1\n'}
        root = self.write({**files, 'README.md': b'not source', 'secrets/fake.py': b'NEVER READ',
                           'unrelated.py': b'not valid Python\x00', 'vendor/unused.py': b'also not valid'})
        expected = self.hashes(files); before = deepcopy(expected)
        with patch('os.scandir', side_effect=AssertionError('no scandir')), \
             patch('os.walk', side_effect=AssertionError('no walk')), \
             patch.object(sources, 'read_safe', wraps=sources.read_safe) as reader:
            result = sources.read_committed_sources(root, expected)
        self.assertEqual(result, files)
        self.assertEqual(expected, before)
        self.assertEqual([call.args[1] for call in reader.call_args_list], sorted(files))

    def test_entire_commitment_shape_and_path_authority_precede_any_root_or_file_read(self):
        valid = sha256(b'pass\n').hexdigest()
        invalid = (None, {}, [], {'a.py': 'not-a-hash'}, {'a.py': 'A' * 64},
                   {'a.py': 1}, {1: valid}, {f'a{i}.py': valid for i in range(33)},
                   {'a.py': valid, 'secrets/late.py': valid})
        for expected in invalid:
            with self.subTest(expected=expected), patch.object(sources, '_root') as root, \
                 patch.object(sources, 'read_safe') as reader:
                with self.assertRaises(ContractError): sources.read_committed_sources('never-opened', expected)
                root.assert_not_called(); reader.assert_not_called()

    def test_noncanonical_private_reserved_and_aliased_paths_fail_before_read(self):
        valid = sha256(b'pass\n').hexdigest()
        paths = ('../a.py', '/a.py', './a.py', 'pkg//a.py', 'pkg/a.py/', 'pkg\\a.py',
                 'a.py\x00', 'a.py\n', 'a.txt', '.git/a.py', '.aws/a.py', '.env-fixture.py',
                 'artifacts/a.py', 'vendor/secrets/a.py', '._a.py', 'a' * 4097 + '.py',
                 'caf\u0065\u0301.py', '\ud800.py')
        mappings = [{path: valid} for path in paths]
        mappings += [{path: valid for path in pair} for pair in (
            ('a.py', 'A.py'), ('Pkg/a.py', 'pkg/b.py'), ('a.py', 'a.py/b.py'))]
        for expected in mappings:
            with self.subTest(paths=list(expected)), patch.object(sources, 'read_safe') as reader:
                with self.assertRaises(ContractError): sources.read_committed_sources('never-opened', expected)
                reader.assert_not_called()

    def test_declared_entry_budget_matches_owned_record_without_scanning_raw_repo(self):
        files = {f'd{i:02}/a.py': b'pass\n' for i in range(32)}
        root = self.write(files)
        with patch('os.scandir', side_effect=AssertionError('no directory scan')):
            self.assertEqual(sources.read_committed_sources(root, self.hashes(files)), files)
        too_many_entries = dict(files)
        del too_many_entries['d31/a.py']
        too_many_entries['extra/nested/a.py'] = b'pass\n'
        with patch.object(sources, '_root') as opened, patch.object(sources, 'read_safe') as reader:
            with self.assertRaises(ContractError):
                sources.read_committed_sources('never-opened', self.hashes(too_many_entries))
            opened.assert_not_called(); reader.assert_not_called()

    def test_hash_drift_is_rechecked_and_rejected_before_source_parsing(self):
        files = {'app.py': b'value = 1\n'}
        root = self.write(files); expected = self.hashes(files)
        self.assertEqual(sources.read_committed_sources(root, expected), files)
        (root / 'app.py').write_bytes(b'value = 2\n')
        with patch.object(sources, 'validate_files') as syntax:
            with self.assertRaises(ContractError): sources.read_committed_sources(root, expected)
            syntax.assert_not_called()

    def test_source_file_total_bytes_lines_and_ast_limits_remain_bounded(self):
        full = b'#' + b'x' * 16382 + b'\n'
        exact = {f'a{i}.py': full for i in range(4)}
        self.assertEqual(sources.read_committed_sources(self.write(exact), self.hashes(exact)), exact)
        for files in ({'a.py': full + b'\n'}, {**exact, 'a4.py': b'pass\n'},
                      {'a.py': b'#\n' * 4097}, {'a.py': b'value = 1\n' * 600},
                      {'a.py': b'def invalid('}, {'a.py': b'break\n'}, {'a.py': b'\xff'}):
            with self.subTest(files_count=len(files)), self.assertRaises(ContractError):
                sources.read_committed_sources(self.write(files), self.hashes(files))
        lines = {'a.py': b'#\r\n' * 4096}
        self.assertEqual(sources.read_committed_sources(self.write(lines), self.hashes(lines)), lines)
        empty = {f'a{i}.py': b'' for i in range(32)}
        self.assertEqual(sources.read_committed_sources(self.write(empty), self.hashes(empty)), empty)

    def test_selected_symlinks_nonregular_leaves_and_ancestors_are_rejected(self):
        files = {'pkg/app.py': b'pass\n'}; expected = self.hashes(files)
        for kind in ('leaf', 'ancestor', 'root', 'directory'):
            with self.subTest(kind=kind):
                real = self.write(files); root = fixture_root()
                if kind == 'root':
                    alias = root / 'alias'; alias.symlink_to(real, target_is_directory=True)
                    root = alias
                elif kind == 'ancestor':
                    (root / 'pkg').symlink_to(real / 'pkg', target_is_directory=True)
                else:
                    (root / 'pkg').mkdir()
                    if kind == 'leaf': (root / 'pkg/app.py').symlink_to(real / 'pkg/app.py')
                    else: (root / 'pkg/app.py').mkdir()
                try:
                    with self.assertRaises((ContractError, OSError)): sources.read_committed_sources(root, expected)
                finally:
                    if kind == 'root': root.unlink()
                    elif kind == 'ancestor': (root / 'pkg').unlink()
                    elif kind == 'leaf': (root / 'pkg/app.py').unlink()

    def test_source_view_is_explicit_partial_coverage_and_rejects_other_modes(self):
        self.assertIsNone(sources.source_view(None))
        self.assertEqual(sources.source_view('declared_files'), {
            'mode': 'declared_files', 'coverage': 'partial_repository',
            'unlisted_files': 'not_read', 'dependency_completeness': 'not_verified'})
        for mode in ('', 'all', 'DECLARED_FILES', False, {}, []):
            with self.subTest(mode=mode), self.assertRaises(ContractError): sources.source_view(mode)


if __name__ == '__main__': unittest.main()
