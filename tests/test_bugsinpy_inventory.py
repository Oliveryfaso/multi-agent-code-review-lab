"""Trusted tree/task strings only; never execute or import benchmark source."""
from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root, write_files
from macr.investigation.validation import ContractError

try:
    from macr.evals import bugsinpy_inventory as inventory
except ImportError:
    inventory = None


REVISION = 'a' * 40
TREE_SHA = 'b' * 40


def tree(*bugs):
    entries = []
    for project, number in bugs:
        prefix = f'projects/{project}/bugs/{number}'
        entries.extend({'path': prefix + '/' + name, 'type': 'blob'}
                       for name in ('bug.info', 'run_test.sh', 'bug_patch.txt'))
    return {'sha': TREE_SHA, 'truncated': False, 'tree': entries}


class BugsInPyInventoryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(inventory, 'BugsInPy metadata inventory is missing')

    def build(self, index=None, **kwargs):
        return inventory.build_inventory(tree(('alpha', 1)) if index is None else index,
                                         revision=REVISION, **kwargs)

    def task(self, *, pytest=False, oversized=False):
        root = fixture_root()
        files = {f'lib{i}.py': b'value = 1\n' for i in range(4)}
        files['checks.py'] = (b'raise RuntimeError("source must never run")\n'
            + (b'def test_value(): pass\n' if pytest else
               b'import unittest\nclass Checks(unittest.TestCase):\n    def test_value(self): pass\n'))
        if oversized:
            files['lib0.py'] = b'#' + b'x' * 16384
        repo = write_files(root / 'source', {name: body.decode() for name, body in files.items()})
        task = {'schema': 'macr.repair.task.v1', 'task_id': 'owned-inventory-test',
                'issue': 'Fixed public test string.',
                'source_files': {name: sha256(body).hexdigest() for name, body in files.items()},
                'editable_paths': [f'lib{i}.py' for i in range(4)], 'test_paths': ['checks.py']}
        task_path = root / 'task.json'
        task_path.write_text(json.dumps(task))
        return {'repo': str(repo), 'task': str(task_path)}

    def invoke(self, index, *, task_map=None, out=None, revision=REVISION):
        root = fixture_root()
        path = root / 'index.json'
        path.write_text(json.dumps(index))
        mapping = None
        if task_map is not None:
            mapping = root / 'task-map.json'
            mapping.write_text(json.dumps(task_map))
        out = root / 'report.json' if out is None else out
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = inventory.run(SimpleNamespace(index=path, revision=revision, task_map=mapping, out=out))
        return code, json.loads(stream.getvalue()), out

    def test_complete_denominator_sorted_without_opening_benchmark_assets(self):
        index = tree(('zeta', 1), ('alpha', 10), ('alpha', 2))
        index['tree'].append({'path': 'projects/alpha/bugs/3', 'type': 'tree'})
        with patch.object(inventory, 'load_task', side_effect=AssertionError('unmapped task read')):
            report = self.build(index)
        self.assertEqual([row['id'] for row in report['rows']], ['alpha/2', 'alpha/3', 'alpha/10', 'zeta/1'])
        self.assertEqual(report['bug_count'], 4)
        self.assertEqual(report['input_states'], {'input_compatible': 0, 'input_unsupported': 0, 'not_assessed': 4})
        incomplete = report['rows'][1]
        self.assertIn('missing_bug_info', incomplete['reasons'])
        self.assertIn('missing_run_test_sh', incomplete['reasons'])
        self.assertTrue(all(row['runtime_state'] == 'uncalibrated' for row in report['rows']))
        self.assertFalse(report['target_code_executed'])

    def test_provenance_separates_caller_revision_from_tree_sha(self):
        index = tree(('alpha', 1))
        report = self.build(index)
        source = report['source']
        self.assertEqual(source['revision'], REVISION)
        self.assertEqual(source['tree_sha'], TREE_SHA)
        self.assertFalse(source['remote_verified'])
        self.assertEqual(source['kind'], 'caller_provided_github_recursive_tree')
        canonical = json.dumps(index, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        self.assertEqual(source['index_sha256'], sha256(canonical).hexdigest())
        reordered = {key: index[key] for key in ('tree', 'sha', 'truncated')}
        self.assertEqual(self.build(reordered)['source']['index_sha256'], source['index_sha256'])

    def test_projection_is_explicit_and_cannot_claim_raw_response_or_tree_sha(self):
        projection = {'schema': 'macr.bugsinpy.path-projection.v1', 'kind': 'derived_path_projection',
                      'revision': REVISION, 'raw_response_retained': False,
                      'truncated': False, 'tree': tree(('alpha', 1))['tree']}
        report = self.build(projection)
        self.assertEqual(report['bug_count'], 1)
        self.assertEqual(report['source']['kind'], 'caller_provided_derived_path_projection')
        self.assertIsNone(report['source']['tree_sha'])
        self.assertFalse(report['source']['remote_verified'])
        self.assertFalse(report['source']['raw_response_retained'])
        self.assertEqual(report['input_states']['not_assessed'], 1)
        entry_sha = deepcopy(projection); entry_sha['tree'][0]['sha'] = TREE_SHA
        for invalid in (projection | {'sha': TREE_SHA}, projection | {'raw_response_retained': True},
                        projection | {'revision': 'c' * 40}, entry_sha):
            with self.subTest(invalid=invalid), self.assertRaises(ContractError): self.build(invalid)

    def test_truncation_duplicates_and_invalid_paths_or_revisions_fail_closed(self):
        good = tree(('alpha', 1))
        invalid = []
        duplicate = deepcopy(good); duplicate['tree'].append(deepcopy(duplicate['tree'][0])); invalid.append(duplicate)
        truncated = deepcopy(good); truncated['truncated'] = True; invalid.append(truncated)
        wrong_sha = deepcopy(good); wrong_sha['sha'] = 'bad'; invalid.append(wrong_sha)
        bad_path = deepcopy(good); bad_path['tree'][0]['path'] = '../private'; invalid.append(bad_path)
        for index in invalid:
            with self.subTest(index=index), self.assertRaises(ContractError): self.build(index)
        with self.assertRaises(ContractError): inventory.build_inventory(good, revision='main')

    def test_unknown_or_malformed_mapping_rejected_before_any_task_read(self):
        for task_map in ({'other/1': {'repo': 'not-opened', 'task': 'not-opened'}},
                         {'alpha/1': {'repo': 'not-opened', 'task': 'not-opened', 'hidden': 'never'}},
                         {'alpha/1': {'repo': 1, 'task': 'not-opened'}}):
            with self.subTest(task_map=task_map), patch.object(inventory, 'load_task') as loader:
                with self.assertRaises(ContractError): self.build(task_map=task_map)
                loader.assert_not_called()

    def test_explicit_neutral_task_uses_normal_loader_with_four_editable_files(self):
        mapping = self.task()
        report = self.build(tree(('alpha', 1), ('alpha', 2)), task_map={'alpha/1': mapping})
        self.assertEqual([row['input_state'] for row in report['rows']], ['input_compatible', 'not_assessed'])
        self.assertEqual(report['input_states'], {'input_compatible': 1, 'input_unsupported': 0, 'not_assessed': 1})
        self.assertEqual(report['runtime_state'], 'uncalibrated')
        self.assertFalse(report['target_code_executed'])
        self.assertNotIn(mapping['repo'], json.dumps(report))

    def test_scoped_checkout_acceptance_is_explicitly_partial_in_inventory(self):
        mapping=self.task()
        task=json.loads(Path(mapping['task']).read_text())
        task.update(schema='macr.repair.task.v3',source_scope='declared_files',test_plan=None)
        Path(mapping['task']).write_text(json.dumps(task))
        (Path(mapping['repo'])/'README.md').write_text('ordinary unrelated repository file')
        report=self.build(task_map={'alpha/1':mapping})
        row=report['rows'][0]
        self.assertEqual(row['input_state'],'input_compatible')
        self.assertEqual(row['source_view']['coverage'],'partial_repository')
        self.assertEqual(row['source_view']['dependency_completeness'],'not_verified')
        self.assertEqual(report['runtime_state'],'uncalibrated')
        self.assertNotIn(mapping['repo'],json.dumps(report))

    def test_real_loader_rejection_stays_in_denominator_and_does_not_leak_source(self):
        for mapping in (self.task(pytest=True), self.task(oversized=True)):
            with self.subTest(mapping=mapping):
                report = self.build(task_map={'alpha/1': mapping})
                row = report['rows'][0]
                self.assertEqual((report['bug_count'], row['input_state']), (1, 'input_unsupported'))
                self.assertIn('neutral_task_rejected', row['reasons'])
                self.assertNotIn('source must never run', json.dumps(report))
                self.assertNotIn(mapping['task'], json.dumps(report))

    def test_loader_exception_messages_are_not_report_contents(self):
        mapping = {'alpha/1': {'repo': 'explicit-source', 'task': 'explicit-task'}}
        for error in (ContractError('repair_loop_invalid', 'source_inventory_mismatch'),
                      ContractError('DO-NOT-PRINT-PRIVATE-PATH', 'DO-NOT-PRINT-PRIVATE-PATH'),
                      OSError('DO-NOT-PRINT-PRIVATE-PATH')):
            with self.subTest(error=type(error).__name__), patch.object(inventory, 'load_task', side_effect=error):
                report = self.build(task_map=mapping)
                self.assertEqual(report['rows'][0]['input_state'], 'input_unsupported')
                self.assertNotIn('DO-NOT-PRINT', json.dumps(report))

    def test_report_cannot_add_a_file_to_mapped_source(self):
        mapping = self.task()
        out = Path(mapping['repo']) / 'inventory.json'
        code, summary, _ = self.invoke(tree(('alpha', 1)), task_map={'alpha/1': mapping}, out=out)
        self.assertEqual((code, summary['status']), (2, 'rejected'))
        self.assertEqual(summary['field'], 'output_source_overlap')
        self.assertFalse(out.exists())

    def test_run_writes_exclusive_private_report_and_stdout_summary(self):
        code, summary, out = self.invoke(tree(('alpha', 1), ('alpha', 2)))
        self.assertEqual((code, summary['status'], summary['bug_count']), (0, 'recorded', 2))
        self.assertNotIn('rows', summary)
        report = json.loads(out.read_text())
        self.assertEqual(len(report['rows']), 2)
        self.assertEqual(out.stat().st_mode & 0o777, 0o600)
        before = out.read_bytes()
        code, summary, _ = self.invoke(tree(('alpha', 3)), out=out)
        self.assertEqual((code, summary['status']), (2, 'rejected'))
        self.assertEqual(out.read_bytes(), before)

    def test_run_reads_only_explicit_tree_and_task_map_without_asset_reads(self):
        with patch.object(inventory, 'read_safe', wraps=inventory.read_safe) as reader, \
             patch.object(inventory, 'load_task', return_value={}) as loader:
            code, _, _ = self.invoke(tree(('alpha', 1)),
                task_map={'alpha/1': {'repo': 'explicit-source', 'task': 'explicit-task'}})
        self.assertEqual(code, 0)
        self.assertEqual([call.args[1] for call in reader.call_args_list], ['index.json', 'task-map.json'])
        loader.assert_called_once_with('explicit-source', 'explicit-task')

    def test_json_input_bounds_duplicate_keys_and_output_alias_are_rejected(self):
        root = fixture_root()
        index = root / 'index.json'
        out = root / 'report.json'
        for raw in ('{"sha":1,"sha":2}', 'x' * (4 * 1024 * 1024 + 1)):
            index.write_text(raw)
            stream = io.StringIO()
            with redirect_stdout(stream):
                code = inventory.run(SimpleNamespace(index=index, revision=REVISION, task_map=None, out=out))
            self.assertEqual(code, 2)
            self.assertFalse(out.exists())
            self.assertNotIn(raw, stream.getvalue())
        target = root / 'existing.json'; target.write_text('untouched')
        out.symlink_to(target)
        try:
            code, _, _ = self.invoke(tree(('alpha', 1)), out=out)
            self.assertEqual(code, 2)
            self.assertEqual(target.read_text(), 'untouched')
        finally:
            out.unlink()

    def test_report_never_enters_mapped_source_even_when_map_is_rejected(self):
        mapping = self.task()
        source = Path(mapping['repo'])
        alias = source.parent / 'source-alias'
        alias.symlink_to(source, target_is_directory=True)
        try:
            for repo in (str(source / 'child' / '..'), str(alias)):
                with self.subTest(repo=repo):
                    out = source / 'inventory.json'
                    code, summary, _ = self.invoke(tree(('alpha', 1)),
                        task_map={'alpha/1': mapping | {'repo': repo}}, out=out)
                    self.assertEqual((code, summary['status']), (2, 'rejected'))
                    self.assertFalse(out.exists())
        finally:
            alias.unlink()

    def test_checkout_cli_registers_inventory_without_subprocess_or_target_execution(self):
        root = fixture_root()
        index = root / 'index.json'; index.write_text(json.dumps(tree(('alpha', 1))))
        out = root / 'report.json'
        project = Path(__file__).resolve().parents[1]
        argv = [str(project / 'cli/agent_review.py'), 'repair-benchmark-inventory',
                '--index', str(index), '--revision', REVISION, '--out', str(out)]
        stream = io.StringIO()
        with patch('sys.argv', argv), redirect_stdout(stream):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path(str(project / 'cli/agent_review.py'), run_name='__main__')
        self.assertEqual(stopped.exception.code, 0)
        self.assertEqual(json.loads(stream.getvalue())['bug_count'], 1)
        self.assertFalse(json.loads(out.read_text())['target_code_executed'])


class TestSourceProbeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(inventory)
        self.probe = getattr(inventory, 'probe_test_sources', None)
        self.assertTrue(callable(self.probe), 'pure test-source probe is missing')

    def body(self, count=1):
        return ('raise RuntimeError("source-must-never-run")\n'
                'from unittest import TestCase as Case\nclass Checks(Case):\n'
                + ''.join(f'    def test_{i}(self): pass\n' for i in range(count))).encode()

    def check(self, paths, sources, origin='fixed_revision_overlay'):
        return self.probe(paths, sources, origin=origin)

    def test_probe_records_static_file_facts_without_filesystem_or_source_execution(self):
        body = self.body(2)
        sources = {'tests/checks.py': body}
        before = dict(sources)
        with patch.object(inventory, 'load_task', side_effect=AssertionError('no task load')), \
             patch.object(inventory, 'read_safe', side_effect=AssertionError('no filesystem read')):
            report = self.check(['tests/checks.py'], sources)
        self.assertEqual(sources, before)
        self.assertEqual(report['assessment_scope'], 'declared_test_files_only')
        self.assertEqual(report['origin'], 'fixed_revision_overlay')
        self.assertEqual(report['state'], 'accepted_by_static_profile')
        self.assertTrue(report['coverage_complete'])
        self.assertEqual(report['rows'][0]['bytes'], len(body))
        self.assertEqual(report['rows'][0]['lines'], len(body.splitlines()))
        self.assertEqual(report['rows'][0]['content_sha256'], sha256(body).hexdigest())
        self.assertEqual(report['rows'][0]['declared_test_count'], 2)
        self.assertEqual(report['known_declared_test_count'], 2)
        self.assertNotIn('source-must-never-run', json.dumps(report))
        for key, expected in (('runtime_state', 'uncalibrated'),
                              ('whole_source_snapshot', 'not_assessed'),
                              ('selector_equivalence', 'not_assessed')):
            self.assertEqual(report[key], expected)
        self.assertFalse(report['target_code_executed'])
        self.assertNotIn('input_state', report)

    def test_buggy_probe_origin_and_missing_sources_cannot_claim_complete_acceptance(self):
        report = self.check(['checks.py', 'missing.py'], {'checks.py': self.body()},
                            origin='buggy_revision_probe_only')
        self.assertEqual(report['origin'], 'buggy_revision_probe_only')
        self.assertEqual(report['state'], 'incomplete')
        self.assertFalse(report['coverage_complete'])
        missing = report['rows'][1]
        self.assertEqual((missing['path'], missing['state']), ('missing.py', 'missing'))
        for key in ('bytes', 'lines', 'content_sha256', 'declared_test_count'):
            self.assertIsNone(missing[key])

    def test_directory_and_nonpython_paths_remain_rows_with_known_blockers(self):
        report = self.check(['tests/', 'README.md', 'missing.py'], {'README.md': b'public text'})
        self.assertEqual([row['path'] for row in report['rows']], ['tests/', 'README.md', 'missing.py'])
        self.assertEqual(report['state'], 'unsupported')
        self.assertFalse(report['coverage_complete'])
        self.assertEqual([row['state'] for row in report['rows']], ['unsupported', 'unsupported', 'missing'])

    def test_probe_rejects_wrong_types_extra_sources_and_unknown_origin(self):
        for paths, sources, origin in (
                ('checks.py', {}, 'fixed_revision_overlay'),
                ([1], {}, 'fixed_revision_overlay'),
                (['checks.py'], {'checks.py': 'text'}, 'fixed_revision_overlay'),
                (['checks.py'], {'extra.py': self.body()}, 'fixed_revision_overlay'),
                (['checks.py'], [], 'fixed_revision_overlay'),
                (['checks.py', 'checks.py'], {}, 'fixed_revision_overlay'),
                (['checks.py'], {}, 'hidden_scoring_overlay')):
            with self.subTest(paths=paths, origin=origin), self.assertRaises(ContractError):
                self.check(paths, sources, origin)

    def test_shared_validator_rejections_keep_metrics_and_safe_error_fields(self):
        for body, field in ((b'x' * 16385, 'source_size'),
                            (b'x=1\n' * 600, 'source_ast_budget'),
                            (b'def invalid(', 'source_compile'),
                            (b'\xff', 'source_compile'),
                            (b'def test_pytest(): pass\n', 'declared_tests_missing_or_ambiguous')):
            with self.subTest(field=field):
                report = self.check(['checks.py'], {'checks.py': body})
                row = report['rows'][0]
                self.assertEqual(report['state'], 'unsupported')
                self.assertTrue(report['coverage_complete'])
                self.assertEqual(row['error'], {'code': 'repair_io_invalid', 'field': field})
                self.assertEqual(row['bytes'], len(body))
                self.assertEqual(row['content_sha256'], sha256(body).hexdigest())
                self.assertIsNone(row['declared_test_count'])

    def test_subset_file_and_declaration_caps_fail_without_dropping_rows(self):
        self.assertEqual(self.check([], {})['state'], 'unsupported')
        sources = {f'checks{i}.py': self.body() for i in range(8)}
        self.assertEqual(self.check(list(sources), sources)['state'], 'accepted_by_static_profile')
        sources['checks8.py'] = self.body()
        report = self.check(list(sources), sources)
        self.assertEqual((report['state'], len(report['rows'])), ('unsupported', 9))
        self.assertIn('test_file_count_exceeded', report['reasons'])
        for count, expected in ((16, 'accepted_by_static_profile'), (17, 'unsupported')):
            sources = {'a.py': self.body(16), 'b.py': self.body(count)}
            report = self.check(list(sources), sources)
            self.assertEqual(report['state'], expected)
            self.assertEqual(report['known_declared_test_count'], 16 + count)
        self.assertIn('declared_test_count_exceeded', report['reasons'])

    def test_subset_byte_and_line_caps_are_lower_bounds_only(self):
        base = self.body()
        full = base + b'#' + b'x' * (16384 - len(base) - 2) + b'\n'
        sources = {f'checks{i}.py': full for i in range(4)}
        report = self.check(list(sources), sources)
        self.assertEqual((report['state'], report['observed_bytes']), ('accepted_by_static_profile', 65536))
        sources['checks4.py'] = base
        report = self.check(list(sources), sources)
        self.assertEqual(report['state'], 'unsupported')
        self.assertIn('test_subset_total_bytes_exceeded', report['reasons'])
        report = self.check([*sources, 'missing.py'], sources)
        self.assertEqual(report['state'], 'unsupported')
        self.assertFalse(report['coverage_complete'])
        for lines, expected in ((4096, 'accepted_by_static_profile'), (4097, 'unsupported')):
            body = base + b'#\n' * (lines - len(base.splitlines()))
            report = self.check(['checks.py'], {'checks.py': body})
            self.assertEqual((report['state'], report['observed_lines']), (expected, lines))
        self.assertIn('test_subset_total_lines_exceeded', report['reasons'])
        self.assertEqual(report['whole_source_snapshot'], 'not_assessed')


if __name__ == '__main__': unittest.main()
