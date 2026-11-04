"""Bounded source transformations only; fixture source is never executed."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from macr.investigation.validation import ContractError

try:
    from macr.investigation import repair_edits
except ImportError:
    repair_edits = None


class RepairEditsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(repair_edits, 'bounded reusable repair edits are missing')
        self.files = {'pkg/math.py': b'def total(a, b):\n    return a - b\n',
                      'checks.py': b'assert False, "never executed"\n'}
        self.edit = {'file': 'pkg/math.py', 'old': 'return a - b', 'new': 'return a + b'}

    def apply(self, files=None, edits=None, editable=None):
        return repair_edits.apply_edits(self.files if files is None else files,
            [self.edit] if edits is None else edits,
            editable_paths=['pkg/math.py'] if editable is None else editable)

    def test_edits_copy_source_and_preserve_uneditable_tests(self):
        before = deepcopy(self.files)
        result = self.apply()
        self.assertEqual(result, {'pkg/math.py': b'def total(a, b):\n    return a + b\n',
                                  'checks.py': b'assert False, "never executed"\n'})
        self.assertEqual(self.files, before)
        self.assertIsNot(result, self.files)

    def test_explicit_editable_subset_never_grants_test_or_added_file_access(self):
        for name in ('checks.py', 'new.py'):
            edit = {'file': name, 'old': 'assert False', 'new': 'assert True'}
            with self.subTest(name=name), self.assertRaises(ContractError):
                self.apply(edits=[edit])
        for editable in ([], ['new.py'], ['pkg/math.py', 'pkg/math.py'], 'pkg/math.py'):
            with self.subTest(editable=editable), self.assertRaises(ContractError):
                self.apply(editable=editable)

    def test_noncanonical_paths_are_rejected_without_normalization(self):
        for name in ('/a.py', '../a.py', './a.py', 'pkg//a.py', 'pkg/a.py/',
                     'pkg\\a.py', 'pkg/a.py\x00', 'pkg/a.py\n', 'pkg/a.txt', 'pkg/a.py:1',
                     'a' * 4096 + '.py', '\ud800.py'):
            with self.subTest(name=name), self.assertRaises(ContractError):
                repair_edits.validate_files({name: b'value = 1\n'})

    def test_malformed_shapes_and_byte_types_fail_closed(self):
        for files in (None, [], {}, {'a.py': 'value = 1'}, {'a.py': bytearray(b'pass')},
                      {1: b'pass'}):
            with self.subTest(files=files), self.assertRaises(ContractError):
                repair_edits.validate_files(files)
        for edits in (None, {}, [], [None], [self.edit | {'extra': True}],
                      [self.edit] * 4):
            with self.subTest(edits=edits), self.assertRaises(ContractError):
                self.apply(edits=edits) if edits is not None else repair_edits.apply_edits(
                    self.files, None, editable_paths=['pkg/math.py'])

    def test_missing_ambiguous_empty_noop_or_duplicate_file_edits_are_rejected(self):
        values = [{'old': 'missing'}, {'old': ''}, {'old': ' '}, {'new': ''},
                  {'new': ' '}, {'new': self.edit['old']}, {'old': 'a'},
                  {'old': None}, {'new': 7}, {'new': '\ud800'}, {'old': '\ud800'}]
        before = deepcopy(self.files)
        for changes in values:
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                self.apply(edits=[self.edit | changes])
        with self.assertRaises(ContractError): self.apply(edits=[self.edit, self.edit])
        self.assertEqual(self.files, before)

    def test_all_input_sources_are_validated_even_if_not_editable(self):
        for body in (b'def bad(__debug__):\n    pass\n', b'break\n', b'\xff', b'x = 1\x00'):
            with self.subTest(body=body), self.assertRaises(ContractError):
                self.apply(files=self.files | {'checks.py': body})

    def test_invalid_result_fails_without_mutating_original(self):
        before = deepcopy(self.files)
        for replacement in ('return (', 'return None\n\ndef bad(__debug__):\n    pass'):
            with self.subTest(replacement=replacement), self.assertRaises(ContractError):
                self.apply(edits=[self.edit | {'new': replacement}])
        self.assertEqual(self.files, before)

    def test_input_byte_and_file_limits_precede_parsing(self):
        huge = b'#' + b'x' * 16384
        too_many = {f'a{i}.py': b'pass\n' for i in range(33)}
        total = {f'a{i}.py': b'#' + b'x' * 16382 + b'\n' for i in range(5)}
        for files in ({'a.py': huge}, too_many, total):
            with self.subTest(count=len(files)), patch.object(repair_edits, '_syntax') as syntax:
                with self.assertRaises(ContractError): repair_edits.validate_files(files)
                syntax.assert_not_called()

    def test_exact_source_limits_are_accepted(self):
        full = b'#' + b'x' * 16382 + b'\n'
        repair_edits.validate_files({f'a{i}.py': full for i in range(4)})
        repair_edits.validate_files({f'a{i}.py': b'pass\n' for i in range(32)})

    def test_snapshot_line_budget_is_checked_before_parsing_and_after_edits(self):
        files={'a.py': b'\n'*4094+b'pass\n', 'checks.py': b'pass\n'}
        repair_edits.validate_files(files)
        too_many=dict(files);too_many['a.py']=b'\n'+files['a.py']
        with patch.object(repair_edits,'_syntax') as syntax:
            with self.assertRaises(ContractError): repair_edits.validate_files(too_many)
            syntax.assert_not_called()
        before=deepcopy(files)
        with self.assertRaises(ContractError):
            repair_edits.apply_edits(files,[{'file':'a.py','old':'pass','new':'pass\n'}],
                                     editable_paths=['a.py'])
        self.assertEqual(files,before)

    def test_edit_growth_is_checked_for_each_file_and_total_budget(self):
        for files, change in (
            ({'a.py': b'pass\n'}, {'file': 'a.py', 'old': 'pass', 'new': '#' + 'x' * 16384}),
            ({'a.py': b'#' + b'x' * 13105 + b'\n',
              **{f'b{i}.py': b'#' + b'x' * 13105 + b'\n' for i in range(4)}},
             {'file': 'a.py', 'old': '#', 'new': '#  '}),
        ):
            before = deepcopy(files)
            with self.subTest(change=change), self.assertRaises(ContractError):
                repair_edits.apply_edits(files, [change], editable_paths=['a.py'])
            self.assertEqual(files, before)

    def test_three_distinct_edits_succeed_and_later_failure_is_atomic(self):
        files = {f'a{i}.py': b'value = 1\n' for i in range(3)}
        edits = [{'file': name, 'old': 'value = 1', 'new': 'value = 2'} for name in files]
        self.assertEqual(repair_edits.apply_edits(files, edits, editable_paths=list(files)),
                         {f'a{i}.py': b'value = 2\n' for i in range(3)})
        broken = deepcopy(edits); broken[-1]['old'] = 'absent'
        with self.assertRaises(ContractError):
            repair_edits.apply_edits(files, broken, editable_paths=list(files))
        self.assertEqual(files, {f'a{i}.py': b'value = 1\n' for i in range(3)})

    def test_source_is_compiled_but_never_executed(self):
        files = {'a.py': b'raise AssertionError("must never execute")\nvalue = 1\n'}
        result = repair_edits.apply_edits(files,
            [{'file': 'a.py', 'old': 'value = 1', 'new': 'value = 2'}], editable_paths=['a.py'])
        self.assertEqual(result['a.py'], b'raise AssertionError("must never execute")\nvalue = 2\n')
