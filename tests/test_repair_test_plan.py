"""Fixed source strings are compiled for declarations, never imported/executed."""
from copy import deepcopy
import unittest

from macr.investigation.validation import ContractError

try:
    from macr.investigation import repair_test_plan as plans
except ImportError:
    plans = None


class RepairTestPlanTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(plans, 'pytest test-plan contract is missing')
        self.path = 'tests/test_checks.py'
        self.files = {self.path: b'raise RuntimeError("must never execute")\n'
            b'import pytest\n@pytest.fixture\ndef value(): return 1\n'
            b'def test_value(value): assert value == 1\n'
            b'class TestChecks:\n    def test_method(self, value): assert value == 1\n'
            b'async def test_async(value): pass\n',
            'tests/helper.py': b'raise RuntimeError("helper must never execute")\nvalue = 1\n'}

    def plan(self, *nodeids):
        return {'schema': 'macr.repair.test-plan.v1', 'framework': 'pytest', 'nodeids': list(nodeids)}

    def validate(self, plan, files=None, test_paths=None):
        return plans.validate_test_plan(self.files if files is None else files,
            [self.path] if test_paths is None else test_paths, plan)

    def test_function_class_fixture_and_helper_preserve_exact_plan_and_source(self):
        plan = self.plan(self.path + '::test_value', self.path + '::TestChecks::test_method')
        before = deepcopy(plan); files_before = dict(self.files)
        protected = ['tests/helper.py', self.path]
        result = self.validate(plan, test_paths=protected)
        self.assertIs(result, plan)
        self.assertEqual(result, before)
        self.assertEqual(list(result), ['schema', 'framework', 'nodeids'])
        self.assertEqual(plans.test_names(self.files, protected, plan), sorted(plan['nodeids']))
        self.assertEqual(self.files, files_before)
        self.validate(self.plan(self.path + '::test_async'))

    def test_frozen_parameter_ids_validate_declarations_without_evaluating_decorators(self):
        body = (b'raise RuntimeError("never run")\n'
            b'@pytest.mark.parametrize("value", explode())\ndef test_value(value): pass\n'
            b'@mark.parametrize("value", explode())\nclass TestChecks:\n'
            b'    def test_method(self, value): pass\n')
        files = {self.path: body}
        plan = self.plan(self.path + '::test_value[caller-frozen::id]',
                         self.path + '::TestChecks::test_method[not-evaluated]')
        self.assertEqual(self.validate(plan, files=files), plan)
        for nodeid in (self.path + '::test_value', self.path + '::TestChecks::test_method'):
            with self.subTest(nodeid=nodeid), self.assertRaises(ContractError):
                self.validate(self.plan(nodeid), files=files)
        self.validate(self.plan(self.path + '::test_value[]'), files=files)

    def test_method_parametrize_and_direct_import_spelling_require_a_frozen_id(self):
        for body, nodeid in (
                (b'@parametrize("x", [1])\ndef test_value(x): pass\n', self.path + '::test_value'),
                (b'class TestChecks:\n    @mark.parametrize("x", [1])\n'
                 b'    def test_method(self, x): pass\n', self.path + '::TestChecks::test_method')):
            with self.subTest(nodeid=nodeid):
                with self.assertRaises(ContractError): self.validate(self.plan(nodeid), files={self.path: body})
                self.validate(self.plan(nodeid + '[frozen]'), files={self.path: body})

    def test_strict_schema_unique_count_and_shape_limits(self):
        good = self.plan(self.path + '::test_value')
        for plan in (None, good | {'extra': 1}, good | {'schema': 'wrong'},
                     good | {'framework': 'unittest'}, good | {'nodeids': []},
                     good | {'nodeids': 'not-a-list'}, good | {'nodeids': [1]},
                     good | {'nodeids': good['nodeids'] * 2},
                     self.plan(*(self.path + f'::test_value[{i}]' for i in range(33)))):
            with self.subTest(plan=plan), self.assertRaises(ContractError): self.validate(plan)
        self.validate(self.plan(*(self.path + f'::test_value[{i}]' for i in range(32))))

    def test_shape_validator_needs_no_source_and_preserves_exact_fields_and_order(self):
        plan = self.plan('missing.py::test_value[caller-frozen]')
        self.assertIs(plans.validate_plan_shape(plan), plan)
        self.assertEqual(plan['nodeids'], ['missing.py::test_value[caller-frozen]'])
        for invalid in (None, {}, plan | {'framework': 'unittest'},
                        self.plan('--unsafe'), self.plan('missing.py')):
            with self.subTest(invalid=invalid), self.assertRaises(ContractError):
                plans.validate_plan_shape(invalid)

    def test_file_scope_is_a_nonempty_subset_of_protected_test_files(self):
        plan = self.plan(self.path + '::test_value')
        self.validate(plan, test_paths=['tests/helper.py', self.path])
        for paths in ([], [self.path] * 2, ['other.py'], [self.path, 'missing.py'],
                      [self.path, 'tests/'], 'tests/test_checks.py', [1]):
            with self.subTest(paths=paths), self.assertRaises(ContractError):
                self.validate(plan, test_paths=paths)
        files = {**self.files, **{f'helper{i}.py': b'pass\n' for i in range(8)}}
        with self.assertRaises(ContractError): self.validate(plan, files=files, test_paths=list(files))
        with self.assertRaises(ContractError):
            self.validate(self.plan('tests/helper.py::test_value'))

    def test_missing_unknown_and_ambiguous_or_dynamic_declarations_are_rejected(self):
        for nodeid in (self.path + '::test_missing', self.path + '::TestMissing::test_value',
                       self.path + '::TestChecks::test_missing'):
            with self.subTest(nodeid=nodeid), self.assertRaises(ContractError): self.validate(self.plan(nodeid))
        for body in (b'def test_value(): pass\ndef test_value(): pass\n',
                     b'def test_value(): pass\ntest_value = factory()\n',
                     b'if flag:\n    def test_value(): pass\n',
                     b'test_value = factory()\n',
                     b'from somewhere import test_value\n',
                     b'def test_value(): pass\nif flag:\n    test_value = factory()\n'):
            with self.subTest(body=body), self.assertRaises(ContractError):
                self.validate(self.plan(self.path + '::test_value'), files={self.path: body})
        for body in (b'class TestChecks:\n    def test_method(self): pass\n'
                     b'class TestChecks:\n    def test_method(self): pass\n',
                     b'class TestChecks:\n    def test_method(self): pass\n'
                     b'    def test_method(self): pass\n',
                     b'class TestChecks:\n    def test_method(self): pass\n'
                     b'TestChecks.test_method = factory()\n',
                     b'class Base:\n    def test_method(self): pass\nclass TestChecks(Base): pass\n'):
            with self.subTest(body=body), self.assertRaises(ContractError):
                self.validate(self.plan(self.path + '::TestChecks::test_method'), files={self.path: body})

    def test_other_function_local_names_do_not_ambiguate_selected_declaration(self):
        body = b'def other():\n    test_value = 1\n    return test_value\ndef test_value(): pass\n'
        self.validate(self.plan(self.path + '::test_value'), files={self.path: body})

    def test_unsafe_broad_and_noncanonical_selectors_are_rejected(self):
        for nodeid in ('--pyargs', self.path, self.path + '::TestChecks',
                       self.path + '::test_*', self.path + '::test_value\x00',
                       self.path + '::test_value\n', self.path + '::test_value\r',
                       self.path + '::test_value[x;command]', './' + self.path + '::test_value',
                       '../' + self.path + '::test_value', '/' + self.path + '::test_value',
                       'tests//test_checks.py::test_value', 'tests/::test_value',
                       'tests/*.py::test_value', self.path + '::TestChecks::test_method::extra',
                       self.path + '::value', self.path + '::test_value[x][y]',
                       self.path + '::test_value[x', self.path + '::test_value]'):
            with self.subTest(nodeid=repr(nodeid)), self.assertRaises(ContractError):
                self.validate(self.plan(nodeid))

    def test_nodeid_limit_counts_original_utf8_bytes_and_does_not_truncate(self):
        prefix = self.path + '::test_value['
        remaining = 1024 - len((prefix + ']').encode())
        count, remainder = divmod(remaining, len('文'.encode()))
        nodeid = prefix + '文' * count + 'x' * remainder + ']'
        self.assertEqual(len(nodeid.encode()), 1024)
        self.assertEqual(self.validate(self.plan(nodeid))['nodeids'], [nodeid])
        for invalid in (nodeid[:-1] + 'x]', prefix + '\ud800]'):
            with self.subTest(invalid=repr(invalid)), self.assertRaises(ContractError):
                self.validate(self.plan(invalid))

    def test_full_original_files_keep_shared_size_ast_and_compile_limits(self):
        base = b'def test_value(): pass\n'
        full = base + b'#' + b'x' * (16384 - len(base) - 2) + b'\n'
        plan = self.plan(self.path + '::test_value')
        self.validate(plan, files={self.path: full})
        for body in (full + b'\n', base + b'x=1\n' * 600,
                     base + b'def invalid(', base + b'break\n', b'\xff'):
            with self.subTest(size=len(body)), self.assertRaises(ContractError):
                self.validate(plan, files={self.path: body})

    def test_v2_exact_static_names_do_not_assume_default_collection_prefixes(self):
        files={self.path:b'raise RuntimeError("do not collect")\n'
            b'class InvoiceCase:\n    def testTotal(self): pass\n'
            b'def configured_check(): pass\n'}
        plan=self.plan(self.path+'::InvoiceCase::testTotal',self.path+'::configured_check')
        with self.assertRaises(ContractError): self.validate(plan,files=files)
        plan['schema']='macr.repair.test-plan.v2'
        self.assertEqual(self.validate(plan,files=files),plan)
        self.assertEqual(plans.test_names(files,[self.path],plan),sorted(plan['nodeids']))
        for name in ('MissingCase::testTotal','InvoiceCase::missing','missing_check',
                     'InvoiceCase','InvoiceCase::test*'):
            bad=deepcopy(plan);bad['nodeids']=[self.path+'::'+name]
            with self.subTest(name=name), self.assertRaises(ContractError):
                self.validate(bad,files=files)

    def test_v2_static_binding_keeps_ambiguity_parametrization_and_size_boundaries(self):
        plan=self.plan(self.path+'::InvoiceCase::check')
        plan['schema']='macr.repair.test-plan.v2'
        for body in (b'class InvoiceCase:\n    def check(self): pass\nInvoiceCase=factory()\n',
                     b'class InvoiceCase(Base): pass\n',
                     b'class InvoiceCase:\n    @pytest.mark.parametrize("x",[1])\n'
                     b'    def check(self,x): pass\n',
                     b'class InvoiceCase:\n    def check(self): pass\n'+b'#'*16384):
            with self.subTest(body=body[:40]), self.assertRaises(ContractError):
                self.validate(plan,files={self.path:body})
        with self.assertRaises(ContractError):
            self.validate(plan, files={**self.files, 'tests/helper.py': b'break\n'},
                          test_paths=[self.path, 'tests/helper.py'])


if __name__ == '__main__': unittest.main()
