import copy
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from evidence_ui_fixtures import committed_fixture, replace_report
from macr.api.evidence_reader import read_committed
from macr.investigation.validation import ContractError


class CommittedReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='macr-ui-reader-', dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_saved_source_is_readable_after_original_project_deleted_without_ast(self):
        out, binding, _ = committed_fixture(self.base)
        shutil.rmtree(self.base / 'source')
        with patch('ast.parse', side_effect=AssertionError('history must not rebuild AST')):
            result = read_committed(out, binding)
        self.assertEqual(result['report']['evidence'][0]['text'], 'def read():\r\n    return "hello"\r\n')
        self.assertEqual(result['report']['coverage']['unread']['items'][0]['reason'], 'not_selected_for_display')
        self.assertEqual(result['json_bytes'], (out / 'pending/report.json').read_bytes())

    def test_changed_unshown_snapshot_is_rejected(self):
        out, binding, report = committed_fixture(self.base)
        ref = report.source_version['snapshot_ref']
        (Path(ref['root_ref']) / 'other.txt').write_bytes(b'changed\n')
        with self.assertRaises(ContractError) as caught:
            read_committed(out, binding)
        self.assertEqual(caught.exception.code, 'history_corrupt')

    def test_enlarged_saved_capture_is_corruption_not_read_budget(self):
        out, binding, report = committed_fixture(self.base)
        path = Path(report.source_version['snapshot_ref']['root_ref']) / 'other.txt'
        path.write_bytes(path.read_bytes() + b'x')
        with self.assertRaises(ContractError) as caught:
            read_committed(out, binding)
        self.assertEqual(caught.exception.code, 'history_corrupt')

    def test_partial_excerpt_keeps_exact_physical_lines_and_completeness(self):
        out, binding, report = committed_fixture(self.base, partial=True)
        result = read_committed(out, binding)
        self.assertEqual(result['report']['status'], 'partial')
        self.assertFalse(result['report']['evidence'][0]['complete_symbol'])
        self.assertEqual(result['report']['evidence'][0]['text'], report.evidence[0]['text'])

    def test_rebound_report_cannot_invent_excerpt_text(self):
        out, binding, _ = committed_fixture(self.base)
        replace_report(out, binding, lambda raw: raw['evidence'][0].update(text='def fake():\r\n    return "hello"\r\n'))
        with self.assertRaises(ContractError) as caught:
            read_committed(out, binding)
        self.assertEqual(caught.exception.code, 'history_corrupt')

    def test_request_and_receipt_binding_are_both_required(self):
        out, binding, _ = committed_fixture(self.base)
        wrong = copy.deepcopy(binding)
        wrong['request']['question'] = 'another reading'
        with self.assertRaises(ContractError):
            read_committed(out, wrong)
        wrong = copy.deepcopy(binding)
        wrong['receipt']['sha256'] = '0' * 64
        with self.assertRaises(ContractError):
            read_committed(out, wrong)

    def test_missing_and_symlink_history_never_creates_a_directory(self):
        out, binding, _ = committed_fixture(self.base)
        renamed = self.base / 'moved'
        out.rename(renamed)
        with self.assertRaises(ContractError):
            read_committed(out, binding)
        self.assertFalse(out.exists())
        out.symlink_to(renamed, target_is_directory=True)
        with self.assertRaises(ContractError):
            read_committed(out, binding)

    def test_unsupported_selection_version_is_distinct_from_corruption(self):
        out, binding, _ = committed_fixture(self.base)
        replace_report(out, binding, lambda raw: raw['run'].update(selection_version='future-v9'))
        with self.assertRaises(ContractError) as caught:
            read_committed(out, binding)
        self.assertEqual(caught.exception.code, 'history_version_unsupported')
