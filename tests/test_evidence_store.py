import unittest

from macr.memory.evidence_store import EvidenceStore
from macr.schemas import Evidence


class EvidenceStoreTests(unittest.TestCase):
    def test_deduplicates_same_location_and_keeps_strongest_confidence(self):
        store = EvidenceStore()
        store.add(Evidence(file="a.py", line_start=1, line_end=1, reason="first", source_tool="search", confidence=0.4))
        store.add(Evidence(file="a.py", line_start=1, line_end=1, reason="second", source_tool="search", confidence=0.8))

        items = store.items()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].confidence, 0.8)
        self.assertIn("first", items[0].reason)
        self.assertIn("second", items[0].reason)


if __name__ == "__main__":
    unittest.main()
