import unittest
from pathlib import Path
from debugger_fixtures import fixture_root, write_files
from macr.investigation.records import IndexQuery, ScopeProfile
from macr.investigation.scope import RepositoryScope
from macr.investigation.snapshots import SnapshotStore, SnapshotError

try:
    from macr.investigation.index import RepositoryIndex
except ModuleNotFoundError:
    RepositoryIndex = None


def indexed_fixture():
    root = write_files(fixture_root(), {
        "a.py": "def send():\n    return 'a'\n\nclass One:\n    def send(self):\n        return 'one'\n\ndef outer():\n    def send():\n        return 'nested'\n    return send()\n",
        "b.py": "def send():\n    return 'b'\n\ndef invoke(obj):\n    return obj.send()\n\ndef use(send):\n    return send()\n",
        "c.py": "from a import send as target\n\ndef run():\n    return target()\n",
        "tests/test_demo.py": "from c import run\ndef test_run():\n    assert run() == 'a'\n",
        "bad.py": "def broken(:\n", ".env": "DO_NOT_READ"})
    store = SnapshotStore(fixture_root() / "snapshots")
    bundle = store.capture(root, RepositoryScope().inventory(root, ScopeProfile()))
    return root, store, bundle


class RepositoryIndexTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(RepositoryIndex, "qualified snapshot-bound index missing")
        self.root, self.store, self.bundle = indexed_fixture()
        self.index = RepositoryIndex(self.store, cache_root=fixture_root())

    def test_qualified_names_relations_and_parse_failure_coverage(self):
        summary = self.index.build(self.bundle)
        self.assertEqual((summary.coverage.eligible, summary.coverage.indexed, summary.coverage.parsed), (5, 5, 4))
        self.assertEqual([e.code for e in summary.errors], ["parse_error"])
        symbols = self.index.query(self.bundle.ref, IndexQuery(terms=["send"], limit=100)).symbols
        self.assertEqual(len({s.symbol_id for s in symbols}), 4)
        self.assertIn("outer.send", {s.qualified_name for s in symbols})
        all_symbols = self.index.query(self.bundle.ref, IndexQuery(limit=100)).symbols
        by_id = {s.symbol_id: (s.file, s.qualified_name) for s in all_symbols}
        relations = self.index.query(self.bundle.ref, IndexQuery(kind="relations", limit=100)).relations
        edges = [(by_id[e.source_id], by_id.get(e.target_ref), e.resolution) for e in relations if e.relation_type in ("calls", "tests")]
        self.assertIn((("c.py", "run"), ("a.py", "send"), "resolved_import"), edges)
        self.assertIn((("a.py", "outer"), ("a.py", "outer.send"), "resolved_local"), edges)
        self.assertTrue(any(source == ("b.py", "invoke") and target is None and level == "unresolved" for source, target, level in edges))
        self.assertTrue(any(source == ("b.py", "use") and target is None for source, target, level in edges))

    def test_pagination_cache_and_snapshot_drift(self):
        self.index.build(self.bundle)
        self.assertFalse(self.index.cache["hit"])
        self.index.build(self.bundle)
        self.assertTrue(self.index.cache["hit"])
        ids, cursor = [], 0
        while True:
            page = self.index.query(self.bundle.ref, IndexQuery(cursor=cursor, limit=2))
            ids.extend(s.symbol_id for s in page.symbols)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), len(self.index.query(self.bundle.ref, IndexQuery(limit=100)).symbols))
        (Path(self.bundle.ref.root_ref) / "b.py").write_text("changed\n")
        with self.assertRaises(SnapshotError):
            self.index.query(self.bundle.ref, IndexQuery())
