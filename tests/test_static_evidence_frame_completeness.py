"""Independent exact full-evidence assertions, supplementing frozen r2 tests."""
import copy
import unittest
import test_static_evidence_frames as fixture_module

FULL_DECLARATION = 'def process():\n    value = 7\n    return value\n'
KNOWN_SPAN = {'start': 1, 'end': 3}


class CompleteFrameEvidenceTests(unittest.TestCase):
    def setUp(self):
        # Module alias keeps the original17-test class out of unittest discovery.
        self.fixture = fixture_module.FrameContractV2Tests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def assert_complete(self, report, frames, entry, origin):
        self.assertEqual(report.status, 'completed')
        self.assertFalse(any(gap['required'] for gap in report.gaps))
        self.assertEqual(report.errors, [])
        self.assertEqual(report.run['selection_version'], 'exact-qualified-physical-span-v2')
        self.assertIsNotNone(report.source_version)
        self.assertEqual(report.coverage['selected_entry_count'], 1)
        self.assertEqual(report.coverage['full_entry_count'], 1)
        self.assertEqual(report.coverage['partial_entry_count'], 0)
        self.assertEqual(len(report.selection['resolved']), 1)
        self.assertEqual(report.selection['unresolved'], [])
        self.assertEqual(report.selection['ambiguous'], [])
        self.assertEqual(len(report.evidence), 1)
        row, evidence = report.selection['resolved'][0], report.evidence[0]
        self.assertEqual(row['entry'], entry)
        self.assertEqual(row['origin'], origin)
        self.assertEqual(row['file'], 'actions.py')
        self.assertEqual(row['requested_span'], KNOWN_SPAN)
        self.assertIsInstance(row['evidence_id'], str)
        self.assertTrue(row['evidence_id'])
        self.assertEqual(row['evidence_id'], evidence['evidence_id'])
        self.assertEqual(row['symbol_id'], evidence['symbol_id'])
        self.assertTrue(row['symbol_id'].endswith(':actions.py:process:1'))
        self.assertEqual(evidence['file'], 'actions.py')
        self.assertEqual(evidence['text'], FULL_DECLARATION)
        self.assertEqual(evidence['requested_span'], KNOWN_SPAN)
        self.assertEqual(evidence['actual_span'], KNOWN_SPAN)
        self.assertIs(evidence['complete_range'], True)
        self.assertIs(evidence['complete_symbol'], True)
        version = report.source_version
        self.assertEqual(evidence['content_id'], version['content_id'])
        self.assertEqual(evidence['content_id'], version['snapshot_ref']['content_id'])
        self.assertEqual(evidence['snapshot_id'], version['snapshot_ref']['snapshot_id'])
        actual_frames = [(frame['file'], frame['line'], frame['name'], frame['origin'], frame['status'])
                         for frame in report.selection['reported_frames']]
        self.assertEqual(actual_frames,
                         [('actions.py', line, name, 'reported_traceback', status)
                          for line, name, status in frames])

    def test_explicit_overlap_preserves_exact_full_evidence(self):
        f = self.fixture
        report = f.prepare(f.frame(), ['actions.py:process'])
        self.assert_complete(report, [(2, 'process', 'valid')], 'actions.py:process', 'explicit_entry')

    def test_exact_repeats_preserve_one_exact_full_evidence(self):
        f = self.fixture
        report = f.prepare(f.frame() * 3)
        self.assert_complete(report, [(2, 'process', 'duplicate'), (2, 'process', 'duplicate'),
                                     (2, 'process', 'valid')], 'actions.py:2', 'reported_traceback')

    def test_distinct_lines_preserve_one_exact_full_evidence(self):
        f = self.fixture
        report = f.prepare(f.frame(2) + f.frame(3))
        self.assert_complete(report, [(2, 'process', 'valid'), (3, 'process', 'valid')],
                             'actions.py:3', 'reported_traceback')

    def test_explicit_and_repeats_preserve_exact_full_evidence(self):
        f = self.fixture
        report = f.prepare(f.frame() * 2, ['actions.py:process'])
        self.assert_complete(report, [(2, 'process', 'duplicate'), (2, 'process', 'valid')],
                             'actions.py:process', 'explicit_entry')

    def test_normalized_paths_and_blank_name_preserve_exact_full_evidence(self):
        f = self.fixture
        report = f.prepare(f.frame(file=str(f.repo / 'actions.py')) + f.frame(name=None) +
                           f.frame(name='process   '))
        self.assert_complete(report, [(2, 'process', 'duplicate'), (2, '', 'valid'), (2, 'process', 'valid')],
                             'actions.py:2', 'reported_traceback')

    def test_data_negative_controls_reject_missing_extra_and_pseudo_full(self):
        f = self.fixture
        original = f.prepare(f.frame() * 2)
        expected = [(2, 'process', 'duplicate'), (2, 'process', 'valid')]
        self.assert_complete(original, expected, 'actions.py:2', 'reported_traceback')
        bad = copy.deepcopy(original)
        bad.evidence.clear()
        with self.assertRaises(AssertionError):
            self.assert_complete(bad, expected, 'actions.py:2', 'reported_traceback')
        bad = copy.deepcopy(original)
        bad.selection['resolved'][0]['evidence_id'] = None
        with self.assertRaises(AssertionError):
            self.assert_complete(bad, expected, 'actions.py:2', 'reported_traceback')
        bad = copy.deepcopy(original)
        extra = copy.deepcopy(bad.evidence[0])
        extra['evidence_id'] = 'data-only-extra-unique-id'
        bad.evidence.append(extra)
        with self.assertRaises(AssertionError):
            self.assert_complete(bad, expected, 'actions.py:2', 'reported_traceback')
        bad = copy.deepcopy(original)
        bad.evidence[0]['text'] = 'def proc'
        bad.evidence[0]['complete_range'] = bad.evidence[0]['complete_symbol'] = True
        with self.assertRaises(AssertionError):
            self.assert_complete(bad, expected, 'actions.py:2', 'reported_traceback')
