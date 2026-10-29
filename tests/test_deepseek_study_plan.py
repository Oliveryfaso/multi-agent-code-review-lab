"""Deterministic syntax-only fixed retrieval using self-authored source fixtures."""
import copy
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.evals.deepseek_policy_manifest import prepare_policy_manifest
from macr.evals.deepseek_policy_tools import StaticEvidenceTools


class StudyPlanTests(unittest.TestCase):
    def prepare(self, files, *, entry_file='entry.py', entry_symbol='run'):
        root = fixture_root()
        snapshot = root / 'snapshots' / 'fixture'
        snapshot.mkdir(parents=True)
        for name, text in files.items():
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding='utf-8')
        case = {'id': 'contract-fixture', 'snapshot_root': 'snapshots/fixture',
                'question': 'Explain the function behavior.', 'scenario': 'All shown inputs.',
                'assumptions': [], 'authorized_files': list(files),
                'initial_evidence_files': [entry_file],
                'stratum': 'full_context' if len(files) == 1 else 'retrieval_required',
                'evidence_parity': 'equal' if len(files) == 1 else 'unequal_by_design',
                'entrypoint': {'file': entry_file, 'symbol': entry_symbol}}
        manifest = prepare_policy_manifest(case, dataset_root=root, out=root / 'manifest',
                                          model={'model_id': 'deepseek-flash'})
        return root, manifest

    def build(self, files, **kwargs):
        from macr.evals.deepseek_study_plan import build_fixed_plan
        root, manifest = self.prepare(files, **kwargs)
        return build_fixed_plan(manifest, root), root, manifest

    def test_breadth_first_two_depths_sorted_deduplicated_and_executable(self):
        files = {
            'entry.py': 'from beta import second\nfrom alpha import first\ndef run(x):\n    return second(x) + first(x) + first(x)\n',
            'alpha.py': 'from gamma import third\ndef first(x):\n    return third(x)\n',
            'beta.py': 'def second(x):\n    return x\n',
            'gamma.py': 'def third(x):\n    return fourth(x)\ndef fourth(x):\n    return x\n'}
        plan, root, manifest = self.build(files)
        self.assertEqual(plan['symbols'], [
            {'distance': 1, 'file': 'alpha.py', 'symbol': 'first'},
            {'distance': 1, 'file': 'beta.py', 'symbol': 'second'},
            {'distance': 2, 'file': 'gamma.py', 'symbol': 'third'}])
        self.assertEqual([len(group) for group in plan['groups']], [2, 1])
        self.assertEqual(plan['scanned_bytes'], sum(len(text.encode()) for text in files.values()))
        tools = StaticEvidenceTools(manifest, root)
        for group in plan['groups']:
            for action in group:
                result = tools.execute(action['name'], action['arguments'])
                self.assertEqual(result['status'], 'ok')
                self.assertTrue(result['snippets'])
                self.assertEqual(set(result), {'tool', 'status', 'snippets', 'truncated'})

    def test_more_than_four_candidates_stop_at_four_without_depth_first_bias(self):
        plan, _, _ = self.build({'entry.py':
            'def run(x):\n    return z(x)+c(x)+a(x)+e(x)+b(x)\n'
            'def z(x):\n    return x\ndef c(x):\n    return x\ndef a(x):\n    return x\n'
            'def e(x):\n    return x\ndef b(x):\n    return x\n'})
        self.assertEqual([row['symbol'] for row in plan['symbols']], ['a', 'b', 'c', 'e'])
        self.assertEqual([len(group) for group in plan['groups']], [2, 2])

    def test_empty_plan_and_recursive_entry_are_not_filled_by_arbitrary_helpers(self):
        plan, _, _ = self.build({'entry.py':
            'def run(x):\n    return run(x)\ndef unrelated(x):\n    return x\n'})
        self.assertEqual(plan['groups'], [[], []])
        self.assertEqual(plan['symbols'], [])

    def test_module_alias_relative_import_and_lexical_nested_function(self):
        plan, _, _ = self.build({
            'pkg/entry.py': 'from . import helper as h\ndef run(x):\n    def inner(x):\n        return h.normalize(x)\n    return inner(x)\n',
            'pkg/helper.py': 'def normalize(x):\n    return x\n'}, entry_file='pkg/entry.py')
        self.assertEqual(plan['symbols'], [
            {'distance': 1, 'file': 'pkg/entry.py', 'symbol': 'run.inner'},
            {'distance': 2, 'file': 'pkg/helper.py', 'symbol': 'normalize'}])

    def test_dynamic_external_reassigned_duplicate_targets_are_unresolved(self):
        plan, _, _ = self.build({
            'entry.py': 'from remote import send\nfrom local import helper\nhelper = factory()\ndef run(obj):\n    return send(helper(obj.method()))\n',
            'local.py': 'def helper(x):\n    return x\ndef helper(x):\n    return x\n'})
        self.assertEqual(plan['symbols'], [])
        self.assertEqual({row['callee'] for row in plan['unresolved']}, {'send', 'helper', 'obj.method'})
        self.assertTrue(all(row['reason'] for row in plan['unresolved']))

    def test_parameter_shadowing_and_instance_attributes_do_not_resolve_as_local_defs(self):
        plan, _, _ = self.build({'entry.py':
            'def helper(x):\n    return x\ndef run(helper):\n    return helper(1)\n'})
        self.assertEqual(plan['symbols'], [])
        self.assertEqual(plan['unresolved'][0]['callee'], 'helper')

    def test_method_bare_call_does_not_resolve_class_namespace(self):
        plan, _, _ = self.build({'entry.py':
            'class Box:\n    def helper(self, x):\n        return x\n'
            '    def run(self, x):\n        return helper(x)\n'}, entry_symbol='Box.run')
        self.assertEqual(plan['groups'], [[], []])
        self.assertEqual(plan['symbols'], [])
        self.assertIn({'file': 'entry.py', 'symbol': 'Box.run', 'callee': 'helper',
                       'reason': 'unresolved_static_relation'}, plan['unresolved'])

    def test_method_bare_call_resolves_module_binding_while_self_and_cls_stay_unresolved(self):
        plan, _, _ = self.build({
            'entry.py': 'from helper_module import helper\nclass Box:\n'
            '    def helper(self, x):\n        return x\n'
            '    def run(self, cls, x):\n        return helper(x)+self.helper(x)+cls.helper(x)\n',
            'helper_module.py': 'def helper(x):\n    return x\n'}, entry_symbol='Box.run')
        self.assertEqual(plan['symbols'], [
            {'distance': 1, 'file': 'helper_module.py', 'symbol': 'helper'}])
        self.assertEqual({row['callee'] for row in plan['unresolved']},
                         {'self.helper', 'cls.helper'})

    def test_ambiguous_read_symbol_is_not_a_single_symbol_plan_action(self):
        plan, _, _ = self.build({'entry.py':
            'def run(x):\n    return helper(x)\ndef helper(x):\n    return x\n'
            'class Box:\n    def helper(self, x):\n        return x\n'})
        self.assertEqual(plan['groups'], [[], []])
        self.assertIn('read_symbol_ambiguous', {row['reason'] for row in plan['unresolved']})

    def test_digest_ignores_question_model_answers_timing_and_binds_source(self):
        from macr.evals.deepseek_study_plan import build_fixed_plan
        root, manifest = self.prepare({'entry.py': 'def run(x):\n    return x\n'})
        with patch('macr.evals.deepseek_study_plan.monotonic', side_effect=[0.0, 0.2]):
            first = build_fixed_plan(manifest, root)
        changed = copy.deepcopy(manifest)
        changed['task']['question'] = 'Different neutral question.'
        # Rebind only the legacy manifest integrity, not source or plan inputs.
        from macr.evals.deepseek_policy_manifest import _digest
        changed['manifest_sha256'] = _digest({key: value for key, value in changed.items()
                                             if key != 'manifest_sha256'})
        with patch('macr.evals.deepseek_study_plan.monotonic', side_effect=[0.0, 0.8]):
            second = build_fixed_plan(changed, root)
        self.assertEqual(first['sha256'], second['sha256'])
        self.assertNotEqual(first['elapsed_seconds'], second['elapsed_seconds'])
        self.assertEqual(len(first['sha256']), 64)
        other, _, _ = self.build({'entry.py': 'def run(x):\n    return x + 1\n'})
        self.assertNotEqual(first['sha256'], other['sha256'])

    def test_invalid_python_is_recorded_without_importing_target_source(self):
        plan, _, _ = self.build({'entry.py': 'def run(x):\n    return x\n',
                                 'helper.py': 'raise RuntimeError("DO NOT EXECUTE")\ndef bad(:\n'})
        self.assertEqual(plan['symbols'], [])
        self.assertIn({'file': 'helper.py', 'symbol': '<module>', 'callee': None,
                       'reason': 'python_source_invalid'}, plan['unresolved'])


if __name__ == '__main__':
    unittest.main()
