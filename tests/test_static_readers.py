import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
from debugger_fixtures import fixture_root, write_files
from macr.investigation.records import ScopeProfile
from macr.investigation.scope import RepositoryScope
from macr.investigation.snapshots import SnapshotStore, SnapshotError
from macr.tools.git_tool import GitTool
from macr.tools.ast_tool import PythonAstTool
from macr.tools.search import TextSearchTool


class StaticReaderTests(unittest.TestCase):
    def test_history_is_frozen_bounded_and_path_validated(self):
        self.assertTrue(hasattr(GitTool, 'read_history'), 'frozen history API missing')
        root = write_files(fixture_root(), {'.git/HEAD': 'fixture metadata'})
        commit = 'a' * 40
        with patch('macr.tools.git_tool.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'abc fixture\n', '')) as run:
            result = GitTool().read_history(root, commit, ['a.py'], 2)
            self.assertTrue(result.ok)
            argv = run.call_args.args[0]
            self.assertIn(commit, argv)
            self.assertNotIn('HEAD', argv)
            self.assertEqual(run.call_args.kwargs['timeout'], 5)
            self.assertIn('--no-pager', argv)
            self.assertFalse(GitTool().read_history(root, 'HEAD', ['a.py'], 2).ok)
            self.assertFalse(GitTool().read_history(root, commit, ['../escape'], 2).ok)
            self.assertEqual(run.call_count, 1)
        with patch('macr.tools.git_tool.subprocess.run', side_effect=subprocess.TimeoutExpired('git', 5)):
            self.assertEqual(GitTool().read_history(root, commit, [], 2).error_type, 'tool_timeout')

    def test_ast_and_search_consume_bound_bytes(self):
        root = write_files(fixture_root(), {'a.py': 'class Box:\n    def send(self):\n        return 1\n'})
        store = SnapshotStore(fixture_root() / 'snapshots')
        bundle = store.capture(root, RepositoryScope().inventory(root, ScopeProfile()))
        write_files(root, {'a.py': 'UNRELATED\n'})
        ast_result = PythonAstTool().run(root, 'a.py', [], snapshot=bundle, store=store)
        self.assertIn('Box.send', {s['qualified_name'] for s in ast_result.data['symbols']})
        search = TextSearchTool().run(root, 'return 1', snapshot=bundle, store=store)
        self.assertEqual(search.data['matches'][0]['line'], 3)
        (Path(bundle.ref.root_ref) / 'a.py').write_text('changed\n')
        self.assertFalse(TextSearchTool().run(root, 'changed', snapshot=bundle, store=store).ok)

    def test_verify_reads_manifest_once_per_pass(self):
        root = write_files(fixture_root(), {'a.py': 'x=1\n', 'b.py': 'y=2\n'})
        store = SnapshotStore(fixture_root() / 'snapshots')
        bundle = store.capture(root, RepositoryScope().inventory(root, ScopeProfile()))
        with patch.object(store, '_manifest', wraps=store._manifest) as manifest:
            store.verify(bundle.ref)
            self.assertEqual(manifest.call_count, 1)
