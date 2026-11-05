import json
import stat
import unittest
from pathlib import Path
from debugger_fixtures import fixture_root, write_files
from macr.investigation.evidence_budget import BudgetExceeded, EvidenceBudget
from macr.investigation.evidence_profiles import EvidenceProfile
from macr.investigation.records import IndexQuery, ScopeProfile
from macr.investigation.scope import RepositoryScope
from macr.investigation.snapshots import SnapshotStore, SnapshotError
from macr.investigation.validation import ContractError

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


class OwnedRepositoryIndexTests(unittest.TestCase):
    def setUp(self):
        self.allowed = fixture_root() / "owned-output"
        self.allowed.mkdir(mode=0o700)
        self.source = write_files(fixture_root(), {
            "module.py": "raise AssertionError('analysed source must not execute')\n\ndef locate():\n    return 1\n",
        })
        self.store = SnapshotStore(self.allowed / "snapshots", allowed_root=self.allowed)
        self.bundle = self.store.capture(self.source, RepositoryScope().inventory(self.source, ScopeProfile()))

    def test_owned_storage_retains_physical_lines_and_explicit_index_budget(self):
        # A form feed inside a string is not a Python physical-line boundary.
        (self.source / 'module.py').write_bytes(b'value = "a\x0cb"\r\ndef locate():\r\n    return value\r\n')
        profile = EvidenceProfile.select(overrides={'max_symbols': 1, 'min_free_bytes': 1})
        budget = EvidenceBudget(profile)
        store = SnapshotStore(self.allowed / 'budget-snapshots', allowed_root=self.allowed,
                              profile=profile, budget=budget)
        scope = RepositoryScope().inventory(self.source, profile.scope_profile(), budget=budget)
        bundle = store.capture(self.source, scope)
        self.assertEqual(bundle.scope.eligible[0].lines, 3)
        self.assertEqual(bundle.files['module.py'].entry.lines, 3)
        index = RepositoryIndex(store, self.allowed / 'budget-index', allowed_root=self.allowed,
                                profile=profile, budget=budget)
        self.assertIs(index.profile, profile)
        self.assertIs(index.budget, budget)
        with self.assertRaises(BudgetExceeded) as caught:
            index.build(bundle)
        self.assertEqual(caught.exception.field, 'max_symbols')

    def test_owned_cache_build_query_and_fresh_index_cache_hit(self):
        cache_root = self.allowed / "derived" / "index"
        index = RepositoryIndex(self.store, cache_root, allowed_root=self.allowed)
        summary = index.build(self.bundle)
        self.assertEqual((summary.coverage.eligible, summary.coverage.parsed), (1, 1))
        self.assertFalse(index.cache["hit"])
        self.assertEqual([s.qualified_name for s in index.query(self.bundle.ref, IndexQuery(terms=["locate"])).symbols], ["locate"])
        cache_path = Path(index.cache["path"])
        first_bytes = cache_path.read_bytes()
        payload = json.loads(first_bytes)
        self.assertEqual(set(payload), {"schema_version", "parser", "content_id", "symbols", "relations", "errors", "payload_sha256"})
        self.assertEqual(stat.S_IMODE(cache_path.stat().st_mode), 0o600)
        for directory in (self.allowed, cache_root.parent, cache_root):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        cached = RepositoryIndex(self.store, cache_root, allowed_root=self.allowed)
        cached.build(self.bundle)
        self.assertTrue(cached.cache["hit"])
        self.assertEqual([s.qualified_name for s in cached.query(self.bundle.ref, IndexQuery(terms=["locate"])).symbols], ["locate"])
        self.assertEqual(cache_path.read_bytes(), first_bytes)

    def test_owned_cache_requires_the_snapshot_store_allowed_root(self):
        other = fixture_root()
        with self.assertRaises(ContractError):
            RepositoryIndex(self.store, other / "index", allowed_root=other)
        self.assertFalse((other / "index").exists())

    def test_owned_cache_rejects_escape_and_parent_alias_without_mutation(self):
        outside = fixture_root()
        sentinel = write_files(outside, {"sentinel": "unchanged"}) / "sentinel"
        for root in (outside / "index", self.allowed / ".." / "owned-output" / "index"):
            with self.subTest(root=root), self.assertRaises(ContractError):
                RepositoryIndex(self.store, root, allowed_root=self.allowed)
        self.assertEqual(sentinel.read_text(), "unchanged")
        self.assertFalse((outside / "index").exists())
        self.assertFalse((self.allowed / "index").exists())

    def test_owned_cache_rejects_symlink_root_alias_without_mutation(self):
        actual = self.allowed / "actual"
        actual.mkdir(mode=0o700)
        sentinel = write_files(actual, {"sentinel": "unchanged"}) / "sentinel"
        alias = self.allowed / "alias"
        alias.symlink_to(actual, target_is_directory=True)
        try:
            with self.assertRaises(ContractError):
                RepositoryIndex(self.store, alias / "index", allowed_root=self.allowed)
            self.assertEqual(sentinel.read_text(), "unchanged")
            self.assertFalse((actual / "index").exists())
        finally:
            alias.unlink()

    def test_owned_cache_rejects_protected_output(self):
        with self.assertRaises(ContractError):
            RepositoryIndex(self.store, self.allowed / ".aws" / "index", allowed_root=self.allowed)
        self.assertFalse((self.allowed / ".aws").exists())

    def test_owned_cache_rejects_symlink_leaf_without_overwriting_sentinel(self):
        index = RepositoryIndex(self.store, self.allowed / "index", allowed_root=self.allowed)
        index.build(self.bundle)
        path = Path(index.cache["path"])
        sentinel = fixture_root() / "sentinel.json"
        sentinel.write_bytes(path.read_bytes())
        original = sentinel.read_bytes()
        path.unlink()
        path.symlink_to(sentinel)
        try:
            with self.assertRaises(ContractError):
                index.build(self.bundle)
            self.assertTrue(path.is_symlink())
            self.assertEqual(sentinel.read_bytes(), original)
        finally:
            path.unlink()
