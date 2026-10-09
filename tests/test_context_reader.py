import unittest
from macr.investigation.records import ContextRequest, IndexQuery, Location
from macr.investigation.validation import ContractError
from test_repository_index import RepositoryIndex, indexed_fixture

try:
    from macr.investigation.context import ContextReader
except ModuleNotFoundError:
    ContextReader = None


class ContextReaderTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(ContextReader, "verified context reader missing")
        self.root, self.store, self.bundle = indexed_fixture()
        self.index = RepositoryIndex(self.store)
        self.index.build(self.bundle)
        self.reader = ContextReader(self.store, self.index)

    def test_body_and_citation_bind_to_consumed_snapshot_bytes(self):
        symbol = next(s for s in self.index.query(self.bundle.ref, IndexQuery(limit=100)).symbols if s.file == "c.py" and s.qualified_name == "run")
        pack = self.reader.read(self.bundle.ref, ContextRequest([Location("c.py", 3, 4, symbol.symbol_id)]))
        self.assertIn("return target()", pack.snippets[0]["text"])
        self.assertEqual(pack.evidence[0].snapshot_id, self.bundle.ref.snapshot_id)
        self.assertEqual(pack.evidence[0].excerpt, pack.snippets[0]["text"])
        self.assertEqual((pack.evidence[0].location.start, pack.evidence[0].location.end), (3, 4))
        self.assertGreater(pack.bytes_read, 0)

    def test_truncation_and_invalid_citations_are_visible(self):
        pack = self.reader.read(self.bundle.ref, ContextRequest([Location("c.py", 3, 4)], max_bytes=5))
        self.assertTrue(pack.truncations)
        self.assertLessEqual(len(pack.snippets[0]["text"].encode("utf-8")), 5)
        for location in (Location(".env", 1, 1), Location("c.py", 3, 99), Location("c.py", 3, 4, "wrong-id")):
            with self.subTest(location=location), self.assertRaises(ContractError):
                self.reader.read(self.bundle.ref, ContextRequest([location]))
