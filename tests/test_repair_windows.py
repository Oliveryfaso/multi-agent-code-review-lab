"""Authored source bytes only; compilation and projection never execute targets."""
from copy import deepcopy
from hashlib import sha256
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.investigation import repair_edits, repair_io, repair_sources
from macr.investigation.validation import ContractError

try:
    from macr.investigation import repair_windows as windows
except ImportError:
    windows = None


def selection(*ranges, path='app.py'):
    return {'schema': 'macr.repair.source-windows.v1', 'files': {
        path: [{'start_line': start, 'end_line': end} for start, end in ranges]}}


class RepairWindowsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(windows, 'window primitives are missing')

    def test_projection_binds_full_bytes_with_exact_physical_line_windows(self):
        files = {'app.py': '# café\r\nx = 1\ry = 2\nz = 3'.encode(), 'helper.py': b'pass\n',
                 'empty.py': b''}
        before = deepcopy(files)
        view = windows.project_source_windows(files, selection((2, 3)))
        self.assertEqual(view['schema'], 'macr.repair.source-window-projection.v1')
        row = view['files']['app.py']
        self.assertEqual(row['sha256'], sha256(files['app.py']).hexdigest())
        self.assertEqual((row['bytes'], row['line_count']), (len(files['app.py']), 4))
        self.assertEqual(row['windows'], [{'start_line': 2, 'end_line': 3,
            'line_numbers': [2, 3], 'text': 'x = 1\ry = 2\n',
            'sha256': sha256(b'x = 1\ry = 2\n').hexdigest()}])
        self.assertEqual(row['omitted_ranges'], [{'start_line': 1, 'end_line': 1},
                                                {'start_line': 4, 'end_line': 4}])
        self.assertEqual(view['files']['helper.py']['windows'], [])
        self.assertEqual(view['files']['helper.py']['omitted_ranges'],
                         [{'start_line': 1, 'end_line': 1}])
        self.assertEqual(view['files']['empty.py']['line_count'], 0)
        self.assertEqual(view['files']['empty.py']['omitted_ranges'], [])
        self.assertEqual(files, before)

    def test_selection_shape_scope_and_canonical_nonadjacent_ranges(self):
        good = selection((1, 2), (4, 4))
        self.assertEqual(windows.validate_selection_shape(good, ['app.py', 'helper.py']), good)
        bad = [None, {}, good | {'extra': 1}, good | {'schema': 'wrong'},
               good | {'files': {}}, selection((True, 2)), selection((0, 1)),
               selection((2, 1)), selection((1, 2), (2, 3)),
               selection((1, 2), (3, 3)), selection((4, 4), (1, 1)),
               selection((1, 1), path='../app.py'), selection((1, 1), path='app.txt'),
               selection(*[(i * 2 + 1, i * 2 + 1) for i in range(9)])]
        for item in bad:
            with self.subTest(item=item), self.assertRaises(ContractError):
                windows.validate_selection_shape(item)
        with self.assertRaises(ContractError):
            windows.validate_selection_shape(good, ['other.py'])

    def test_empty_per_file_ranges_rejected_but_omitted_helpers_remain_explicit(self):
        files = {'app.py': b'value = 1\n', 'helper.py': b'pass\n'}
        chosen = selection((1, 1)); chosen['files']['helper.py'] = []
        with self.assertRaises(ContractError):
            windows.validate_selection_shape(chosen, files)
        with self.assertRaises(ContractError): windows.project_source_windows(files, chosen)
        del chosen['files']['helper.py']
        view = windows.project_source_windows(files, chosen)
        self.assertEqual(view['files']['helper.py']['windows'], [])
        self.assertEqual(view['files']['helper.py']['omitted_ranges'],
                         [{'start_line': 1, 'end_line': 1}])

    def test_invalid_current_ranges_are_rejected_never_clamped(self):
        for files, chosen in [({'app.py': b''}, selection((1, 1))),
                              ({'app.py': b'x=1\n'}, selection((1, 2))),
                              ({'app.py': b'x=1\n'}, selection((1, 1), path='missing.py'))]:
            with self.subTest(chosen=chosen), self.assertRaises(ContractError):
                windows.project_source_windows(files, chosen)

    def test_public_protected_files_must_be_fully_visible(self):
        files = {'app.py': b'x=1\ny=2\n', 'checks.py': b'import unittest\n# public\n'}
        for chosen in (selection((1, 1)), {'schema': 'macr.repair.source-windows.v1',
                'files': {'app.py': [{'start_line': 1, 'end_line': 1}],
                          'checks.py': [{'start_line': 1, 'end_line': 1}]}}):
            with self.assertRaises(ContractError):
                windows.project_source_windows(files, chosen, protected_paths=['checks.py'])
        chosen = selection((1, 1)); chosen['files']['checks.py'] = [{'start_line': 1, 'end_line': 2}]
        windows.project_source_windows(files, chosen, protected_paths=['checks.py'])
        with self.assertRaises(ContractError):
            windows.project_source_windows(files, chosen, protected_paths=['unknown.py'])

    def test_fully_shown_public_tests_keep_legacy_ast_limit(self):
        files = {'checks.py': b'x=1;' * 600 + b'\n'}
        with self.assertRaises(ContractError):
            windows.project_source_windows(files, selection((1, 1), path='checks.py'),
                                           protected_paths=['checks.py'])
        windows.project_source_windows(files, selection((1, 1), path='checks.py'))

    def test_window_line_byte_and_count_budgets(self):
        files = {'app.py': b'#\n' * 257}
        windows.project_source_windows(files, selection((1, 256)))
        with self.assertRaises(ContractError):
            windows.project_source_windows(files, selection((1, 257)))
        for content in ('#' + 'é' * 8192, '#' + 'x' * 16384):
            with self.subTest(content_size=len(content)), self.assertRaises(ContractError):
                windows.project_source_windows({'app.py': content.encode()}, selection((1, 1)))
        files = {f'f{i}.py': b'#\n' * 3 for i in range(32)}
        chosen = {'schema': 'macr.repair.source-windows.v1', 'files': {
            path: [{'start_line': 1, 'end_line': 1}] for path in files}}
        windows.project_source_windows(files, chosen)
        chosen['files']['f0.py'].append({'start_line': 3, 'end_line': 3})
        with self.assertRaises(ContractError): windows.project_source_windows(files, chosen)

    def test_full_original_profile_is_explicit_and_keeps_legacy_defaults(self):
        files = {'app.py': b'# hidden bytes\n' * 1800 + b'value = 1\n'}
        with self.assertRaises(ContractError): repair_edits.validate_files(files)
        repair_edits.validate_files(files, full_originals=True)
        with self.assertRaises(ContractError): repair_io._syntax(files['app.py'], 'app.py')
        repair_io._syntax(files['app.py'], 'app.py', full_originals=True)
        for flag in (1, None, 'yes'):
            with self.subTest(flag=flag), self.assertRaises(ContractError):
                repair_edits.validate_files({'app.py': b'pass\n'}, full_originals=flag)

    def test_full_original_byte_total_line_and_ast_limits(self):
        full = b'#' + b'x' * (128 * 1024 - 2) + b'\n'
        repair_edits.validate_files({f'f{i}.py': full for i in range(4)}, full_originals=True)
        repair_edits.validate_files({'app.py': b'#\r' * 16384}, full_originals=True)
        repair_edits.validate_files({'app.py': b'x=1\n' * 8191}, full_originals=True)
        repair_edits.validate_files({'app.py': ('x=' + '+' * 40 + '1\n').encode()}, full_originals=True)
        for files in ({'app.py': full + b'\n'}, {**{f'f{i}.py': full for i in range(4)}, 'x.py': b'pass'},
                      {'app.py': b'#\n' * 16385}, {'app.py': b'x=1\n' * 8192},
                      {'app.py': ('x=' + '+' * 64 + '1\n').encode()}):
            with self.subTest(files_count=len(files)), self.assertRaises(ContractError):
                repair_edits.validate_files(files, full_originals=True)

    def test_full_profile_shape_budget_is_checked_before_compile(self):
        with patch.object(repair_edits, '_syntax') as syntax:
            with self.assertRaises(ContractError):
                repair_edits.validate_files({'app.py': b'#' * (128 * 1024 + 1)}, full_originals=True)
            syntax.assert_not_called()

    def test_visible_edits_require_single_window_and_complete_file_uniqueness(self):
        files = {'app.py': b'x = 1\ny = 2\nz = 3\n'}
        edit = {'file': 'app.py', 'old': 'x = 1', 'new': 'x = 4'}
        chosen = selection((1, 1), (3, 3))
        windows.validate_visible_edits(files, [edit], chosen)
        for old in ('y = 2', 'x = 1\ny = 2', '1\ny = 2\nz = 3'):
            with self.subTest(old=old), self.assertRaises(ContractError):
                windows.validate_visible_edits(files, [edit | {'old': old}], chosen)
        ambiguous = {'app.py': b'x = 1\n# x = 1\n'}
        with self.assertRaises(ContractError):
            windows.validate_visible_edits(ambiguous, [edit], selection((1, 1)))
        with self.assertRaises(ContractError):
            windows.validate_visible_edits({'app.py': b'# aaa\n'},
                [edit | {'old': 'aa', 'new': 'bb'}], selection((1, 1)))

    def test_apply_edits_preserves_full_hidden_original_and_requires_selection(self):
        hidden = b'# HIDDEN_OWNED_SENTINEL\n' * 1000
        files = {'app.py': b'value = 1\r\n' + hidden, 'checks.py': b'assert False\n'}
        before = deepcopy(files); edit = {'file': 'app.py', 'old': 'value = 1', 'new': 'value = 2'}
        result = repair_edits.apply_edits(files, [edit], editable_paths=['app.py'],
                                          full_originals=True, source_windows=selection((1, 1)))
        self.assertEqual(result['app.py'], b'value = 2\r\n' + hidden)
        self.assertEqual(result['checks.py'], files['checks.py']); self.assertEqual(files, before)
        with self.assertRaises(ContractError):
            repair_edits.apply_edits(files, [edit], editable_paths=['app.py'], full_originals=True)

    def test_edit_text_bound_stays_16k_with_full_originals_and_hidden_growth(self):
        files = {'app.py': b'value = 1\n'}; chosen = selection((1, 1))
        prefix = 'value = 2\n#'
        exact = prefix + 'x' * (16384 - len(prefix.encode('utf-8')))
        edit = {'file': 'app.py', 'old': 'value = 1', 'new': exact}
        windows.validate_visible_edits(files, [edit], chosen)
        result = repair_edits.apply_edits(files, [edit], editable_paths=['app.py'],
                                          full_originals=True, source_windows=chosen)
        self.assertEqual(result['app.py'], exact.encode('utf-8') + b'\n')
        self.assertEqual(windows.project_source_windows(result, chosen)['files']['app.py']['windows'][0]['text'],
                         'value = 2\n')
        for replacement in (exact + 'x', prefix + 'é' * 8192):
            too_large = edit | {'new': replacement}
            # Its displayed first line would fit; the unchanged edit contract
            # must reject the larger replacement, including raw UTF8 bytes.
            windows.project_source_windows({'app.py': replacement.encode() + b'\n'}, chosen)
            with self.subTest(bytes=len(replacement.encode())):
                with self.assertRaises(ContractError):
                    windows.validate_visible_edits(files, [too_large], chosen)
                with self.assertRaises(ContractError):
                    repair_edits.apply_edits(files, [too_large], editable_paths=['app.py'],
                                              full_originals=True, source_windows=chosen)

    def test_next_projection_validates_current_lines_and_growth_before_acceptance(self):
        files = {'app.py': b'x = 1\ny = 2\nz = 3\n'}; before = deepcopy(files)
        changed = repair_edits.apply_edits(files,
            [{'file': 'app.py', 'old': 'x = 1', 'new': 'x = 1\na = 4'}],
            editable_paths=['app.py'], full_originals=True, source_windows=selection((1, 2)))
        shown = windows.project_source_windows(changed, selection((1, 2)))['files']['app.py']['windows'][0]
        self.assertEqual(shown['text'], 'x = 1\na = 4\n')
        for edits, chosen in [([{'file': 'app.py', 'old': 'y = 2\nz = 3', 'new': 'z = 3'}], selection((2, 3))),
                              ([{'file': 'app.py', 'old': 'x = 1', 'new': '#' + 'a' * 16384}], selection((1, 1))),
                              ([{'file': 'app.py', 'old': 'x = 1', 'new': 'break'}], selection((1, 1)))]:
            with self.subTest(chosen=chosen), self.assertRaises(ContractError):
                repair_edits.apply_edits(files, edits, editable_paths=['app.py'],
                                          full_originals=True, source_windows=chosen)
        self.assertEqual(files, before)

    def test_context_identity_binds_hidden_bytes_issue_selection_scope_and_plan(self):
        files = {'app.py': b'x=1\n# hidden\n'}; chosen = selection((1, 1)); issue = 'a' * 64
        plan = {'kind': 'owned-public-plan'}
        actual = windows.windowed_context_id(files, issue, chosen, test_plan=plan)
        value = {'schema': 'macr.repair.windowed-initial-context.v1',
                 'source_files': {'app.py': sha256(files['app.py']).hexdigest()},
                 'issue_sha256': issue, 'source_windows': windows.project_source_windows(files, chosen),
                 'source_scope': 'declared_files', 'test_plan': plan}
        self.assertEqual(actual, sha256(repair_io.encode_bounded(value, 1024 * 1024)).hexdigest())
        for changed_files, changed_issue, changed_selection, changed_plan in [
                ({'app.py': b'x=1\n# changed\n'}, issue, chosen, plan),
                (files, 'b' * 64, chosen, plan), (files, issue, selection((2, 2)), plan),
                (files, issue, chosen, {'kind': 'other'})]:
            self.assertNotEqual(actual, windows.windowed_context_id(changed_files, changed_issue,
                                changed_selection, test_plan=changed_plan))
        for kwargs in ({'source_scope': 'all'}, {'issue_sha256': 'bad'}):
            args = {'issue_sha256': issue, 'selection': chosen} | kwargs
            with self.assertRaises(ContractError): windows.windowed_context_id(files, **args)

    def test_malformed_helper_inputs_fail_with_contract_errors(self):
        for files in (None, [], {'app.py': 'pass'}, {'app.py': 1}, {1: b'pass'}):
            with self.subTest(files=files), self.assertRaises(ContractError):
                windows.windowed_context_id(files, 'a' * 64, selection((1, 1)))
        body = b'pass\n'; row = self.candidate('app.py', body, [])
        with self.assertRaises(ContractError):
            windows.propose_source_windows({'app.py': body},
                [row | {'evidence': [{'kind': [], 'line': 1}]}])

    def test_committed_reader_full_profile_still_reads_exact_bytes_and_hashes(self):
        body = b'# full committed bytes\r\n' * 1000 + b'raise RuntimeError("never run")\n'
        root = fixture_root(); (root / 'app.py').write_bytes(body)
        expected = {'app.py': sha256(body).hexdigest()}
        with self.assertRaises(ContractError): repair_sources.read_committed_sources(root, expected)
        self.assertEqual(repair_sources.read_committed_sources(root, expected, full_originals=True), {'app.py': body})
        (root / 'app.py').write_bytes(body + b'# drift\n')
        with self.assertRaises(ContractError): repair_sources.read_committed_sources(root, expected, full_originals=True)

    def candidate(self, path, body, evidence):
        return {'path': path, 'sha256': sha256(body).hexdigest(), 'bytes': len(body), 'evidence': [
            {'file': path, 'kind': kind, 'line': line, 'position_basis': 'source',
             'source_sha256': sha256(body).hexdigest(), 'source_bytes': len(body)} for kind, line in evidence]}

    def test_proposal_prefers_source_positions_merges_and_preserves_full_inventory(self):
        body = b'# owned source\n' * 80; files = {'app.py': body, 'helper.py': b'pass\n'}
        row = self.candidate('app.py', body, [('body', 70), ('signature', 22), ('symbol', 20), ('exact_phrase', 45)])
        result = windows.propose_source_windows(files, [row])
        self.assertEqual(result['source_files'], {p: sha256(b).hexdigest() for p, b in files.items()})
        self.assertEqual(result['source_windows'], selection((14, 28), (39, 51)))
        self.assertEqual(result['projection'], windows.project_source_windows(files, result['source_windows']))
        self.assertTrue(any(gap['code'] == 'source_file_unshown' and gap['file'] == 'helper.py'
                            for gap in result['gaps']))

    def test_proposal_file_only_fallback_and_unshown_budget_are_explicit(self):
        files = {'app.py': b'# header\n' * 20}
        row = self.candidate('app.py', files['app.py'], [])
        result = windows.propose_source_windows(files, [row])
        self.assertEqual(result['source_windows'], selection((1, 7)))
        self.assertTrue(any(g['code'] == 'header_context_fallback' for g in result['gaps']))
        files = {'a.py': b'#' + b'x' * 16384, 'b.py': b'pass\n'}
        rows = [self.candidate(path, body, []) for path, body in files.items()]
        result = windows.propose_source_windows(files, rows)
        self.assertNotIn('a.py', result['source_windows']['files'])
        self.assertEqual(set(result['source_files']), set(files))
        self.assertTrue(any(g['code'] == 'source_file_unshown' and g['file'] == 'a.py' for g in result['gaps']))

    def test_proposal_rejects_stale_evidence_and_never_slices_oversized_original(self):
        body = b'# owned\n' * 20; files = {'app.py': body}
        row = self.candidate('app.py', body, [('symbol', 10)])
        for bad in (row | {'sha256': '0' * 64}, row | {'bytes': 1},
                    row | {'evidence': [row['evidence'][0] | {'source_sha256': '0' * 64}]},
                    row | {'evidence': [row['evidence'][0] | {'line': 21}]}):
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                windows.propose_source_windows(files, [bad])
        huge = b'#' * (128 * 1024 + 1)
        with self.assertRaises(ContractError): windows.propose_source_windows({'app.py': huge}, [])


if __name__ == '__main__': unittest.main()
