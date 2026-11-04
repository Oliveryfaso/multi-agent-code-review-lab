"""Owned byte fixtures only: scoring overlays are read, never compiled/executed."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.evals.repair_comparison import compile_comparison, generation_view, result_digest
from macr.investigation.validation import ContractError

try:
    from macr.evals import repair_scoring_overlay as overlay
except ImportError:
    overlay = None


class RepairScoringOverlayTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(overlay, 'scoring-only immutable test overlay is missing')
        self.original = {'impl.py': b'value = 1\n', 'tests/public.py': b'assert False\n'}
        self.protected = ['tests/public.py']
        self.editable = ['impl.py']

    def manifest(self, tasks):
        return {'schema': 'macr.repair.scoring-overlay.v1', 'tasks': [
            {'task_id': task_id, 'files': [
                {'path': path, 'bytes': len(body), 'sha256': sha256(body).hexdigest()}
                for path, body in sorted(files.items())]}
            for task_id, files in sorted(tasks.items())]}

    def mapped_manifest(self, tasks):
        manifest = self.manifest(tasks)
        manifest['schema'] = 'macr.repair.scoring-overlay.v2'
        for task in manifest['tasks']:
            for entry in task['files']: entry['declared_path'] = entry['path']
        return manifest

    def plan(self, manifest):
        example = Path(__file__).resolve().parents[1] / 'examples/repair_comparison_development.json'
        spec = json.loads(example.read_text())
        spec['tasks'] = [dict(task_id=task['task_id'], **{key: result_digest([key, task['task_id']])
            for key in ('snapshot_sha256', 'issue_sha256', 'visible_tests_sha256', 'initial_context_sha256')})
            for task in manifest['tasks']]
        spec['scoring']['hidden_tests_sha256'] = result_digest(manifest)
        return compile_comparison(spec)

    def write(self, tasks):
        root = fixture_root()
        for task_id, files in tasks.items():
            for relative, body in files.items():
                path = root / task_id / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
        return root

    def read(self, root, manifest, plan, task_id='case-a', **kwargs):
        return overlay.read_overlay(root, manifest, plan, task_id,
            original_files=kwargs.get('original_files', self.original),
            protected_paths=kwargs.get('protected_paths', self.protected),
            editable_paths=kwargs.get('editable_paths', self.editable))

    def test_complete_manifest_commitment_is_scoring_only_and_canonical(self):
        manifest = self.manifest({'case-b': {'tests/new.py': b'new'}, 'case-a': {'tests/public.py': b'replacement'}})
        plan = self.plan(manifest)
        before = deepcopy(manifest)
        with patch.object(overlay, 'read_safe', side_effect=AssertionError('validation must not read files')):
            self.assertIs(overlay.validate_overlay_manifest(manifest, plan), manifest)
        self.assertEqual(manifest, before)
        view = generation_view(plan, 'case-a')
        self.assertNotIn('scoring', view)
        self.assertNotIn('replacement', json.dumps(manifest))
        self.assertNotIn(result_digest(manifest), json.dumps(view))

    def test_large_original_bytes_crlf_and_final_byte_are_returned_without_parsing(self):
        body = b'not valid Python\r\n' + b'# raw\r\n' * 4000 + b'\x00\xffFINAL'
        tasks = {'case-a': {'tests/public.py': body, 'tests/new.py': b'raise RuntimeError("never run")\r\n'}}
        manifest = self.manifest(tasks); plan = self.plan(manifest); root = self.write(tasks)
        original_before = dict(self.original)
        with patch('ast.parse', side_effect=AssertionError('overlay bytes must not be parsed')), \
             patch('builtins.compile', side_effect=AssertionError('overlay bytes must not be compiled')):
            result = self.read(root, manifest, plan)
        self.assertEqual(result, tasks['case-a'])
        self.assertGreater(len(result['tests/public.py']), 16384)
        self.assertTrue(result['tests/public.py'].endswith(b'\x00\xffFINAL'))
        self.assertEqual(self.original, original_before)

    def test_only_requested_task_and_listed_paths_are_read(self):
        tasks = {'case-a': {'tests/public.py': b'a'}, 'case-b': {'tests/other.py': b'b'}}
        manifest = self.manifest(tasks); plan = self.plan(manifest)
        root = self.write({'case-a': tasks['case-a']})
        with patch.object(overlay, 'read_safe', wraps=overlay.read_safe) as reader:
            result = self.read(root, manifest, plan)
        self.assertEqual(result, tasks['case-a'])
        self.assertEqual([call.args[1] for call in reader.call_args_list], ['case-a/tests/public.py'])

    def test_all_plan_tasks_and_unchanged_manifest_digest_are_required_before_read(self):
        manifest = self.manifest({'case-a': {'tests/public.py': b'a'}, 'case-b': {'tests/public.py': b'b'}})
        plan = self.plan(manifest)
        removed = deepcopy(manifest); removed['tasks'].pop()
        tampered = deepcopy(manifest); tampered['tasks'][0]['files'][0]['sha256'] = 'a' * 64
        for invalid in (removed, tampered):
            with self.subTest(invalid=invalid), patch.object(overlay, 'read_safe') as reader:
                with self.assertRaises(ContractError): self.read(fixture_root(), invalid, plan)
                reader.assert_not_called()
        with self.assertRaises(ContractError): self.read(fixture_root(), manifest, plan, 'not-in-plan')

    def test_unknown_metadata_fields_noncanonical_order_and_size_shapes_are_rejected(self):
        good = self.manifest({'case-a': {'tests/a.py': b'a', 'tests/b.py': b'b'}})
        invalids = [good | {'url': 'https://invalid.example/private'}, good | {'schema': 'wrong'}]
        for change in ({'bytes': True}, {'bytes': 0}, {'bytes': 1048577}, {'sha256': 'ABC'},
                       {'source': 'private-path'}, {'content': 'never include source'}):
            bad = deepcopy(good); bad['tasks'][0]['files'][0].update(change); invalids.append(bad)
        bad = deepcopy(good); bad['tasks'][0]['files'].reverse(); invalids.append(bad)
        bad = deepcopy(good); bad['tasks'][0]['files'].append(deepcopy(bad['tasks'][0]['files'][0])); invalids.append(bad)
        bad = deepcopy(good); bad['tasks'][0]['files'] = []; invalids.append(bad)
        bad = deepcopy(good); bad['tasks'][0]['files'] *= 5; invalids.append(bad)
        for invalid in invalids:
            with self.subTest(invalid=invalid), self.assertRaises(ContractError):
                overlay.validate_overlay_manifest(invalid, self.plan(invalid))
        two = self.manifest({'case-a': {'tests/a.py': b'a'}, 'case-b': {'tests/b.py': b'b'}})
        plan = self.plan(two); two['tasks'].reverse()
        with self.assertRaises(ContractError): overlay.validate_overlay_manifest(two, plan)

    def test_safe_paths_and_casefold_or_file_directory_collisions_are_enforced(self):
        for path in ('../bad.py', '/bad.py', './bad.py', 'tests//bad.py', 'tests/bad.txt',
                     'tests/bad.py/', '.git/test.py', '.aws/test.py', 'oracles/test.py',
                     '.env-data.py', 'credentials/test.py', 'https://bad.py', 'a' * 4097 + '.py'):
            manifest = self.manifest({'case-a': {path: b'x'}})
            with self.subTest(path=path), self.assertRaises(ContractError):
                overlay.validate_overlay_manifest(manifest, self.plan(manifest))

    def test_directory_case_aliases_and_non_nfc_names_are_rejected_in_both_namespaces(self):
        for paths in (('Tests/a.py', 'tests/b.py'), ('tests/caf\u00e9.py', 'tests/cafe\u0301.py'),
                      ('tests/cafe\u0301.py',)):
            manifest = self.manifest({'case-a': {path: b'x' for path in paths}})
            with self.subTest(paths=paths), self.assertRaises(ContractError):
                overlay.validate_overlay_manifest(manifest, self.plan(manifest))
        for original, path in (({**self.original, 'Tests/helper.py': b'x'}, 'tests/new.py'),
                               ({**self.original, 'tests/cafe\u0301.py': b'x'}, 'tests/new.py')):
            manifest = self.manifest({'case-a': {path: b'x'}}); plan = self.plan(manifest)
            with self.subTest(original_paths=list(original)), patch.object(overlay, 'read_safe') as reader:
                with self.assertRaises(ContractError):
                    self.read(fixture_root(), manifest, plan, original_files=original)
                reader.assert_not_called()
        manifest = self.manifest({'case-a': {'tests/caf\u00e9.py': b'x'}})
        overlay.validate_overlay_manifest(manifest, self.plan(manifest))
        for paths in (('tests/A.py', 'tests/a.py'), ('tests/a.py', 'tests/a.py/child.py')):
            manifest = self.manifest({'case-a': {path: b'x' for path in paths}})
            with self.subTest(paths=paths), self.assertRaises(ContractError):
                overlay.validate_overlay_manifest(manifest, self.plan(manifest))

    def test_implementation_unprotected_and_original_namespace_collisions_block_before_read(self):
        for path in ('impl.py', 'support.py', 'IMPL.py', 'impl.py/child.py', 'parent.py'):
            original = {**self.original, 'support.py': b'value=2', 'parent.py/child.py': b'value=3'}
            manifest = self.manifest({'case-a': {path: b'overlay'}}); plan = self.plan(manifest)
            with self.subTest(path=path), patch.object(overlay, 'read_safe') as reader:
                with self.assertRaises(ContractError):
                    self.read(fixture_root(), manifest, plan, original_files=original)
                reader.assert_not_called()
        manifest = self.manifest({'case-a': {'tests/PUBLIC.py': b'overlay'}})
        with self.assertRaises(ContractError): self.read(fixture_root(), manifest, self.plan(manifest))

    def test_invalid_authority_cannot_grant_editable_or_unknown_protected_paths(self):
        manifest = self.manifest({'case-a': {'tests/public.py': b'overlay'}}); plan = self.plan(manifest)
        for protected, editable in ((['tests/public.py', 'impl.py'], ['impl.py']),
                                    (['missing.py'], ['impl.py']),
                                    (['tests/public.py'] * 2, ['impl.py']),
                                    (['tests/public.py'], ['missing.py'])):
            with self.subTest(protected=protected, editable=editable), self.assertRaises(ContractError):
                self.read(fixture_root(), manifest, plan, protected_paths=protected, editable_paths=editable)

    def test_file_tampering_size_and_digest_mismatches_fail_closed(self):
        tasks = {'case-a': {'tests/public.py': b'original\r\nFINAL'}}
        manifest = self.manifest(tasks); plan = self.plan(manifest); root = self.write(tasks)
        path = root / 'case-a/tests/public.py'
        for body in (b'ORIGINAL\r\nFINAL', b'original\r\nFINA', b'original\r\nFINAL!'):
            with self.subTest(body=body), self.assertRaises(ContractError):
                path.write_bytes(body)
                self.read(root, manifest, plan)

    def test_one_mebibyte_file_boundary_is_read_in_full(self):
        body = b'\xff' * 1048575 + b'Z'
        tasks = {'case-a': {'tests/public.py': body}}
        manifest = self.manifest(tasks); plan = self.plan(manifest); root = self.write(tasks)
        self.assertEqual(self.read(root, manifest, plan)['tests/public.py'], body)

    def test_v2_explicit_internal_slash_mapping_reads_only_canonical_path_and_full_bytes(self):
        body = b'not valid Python\r\n' + b'# raw\r\n' * 4000 + b'\x00\xffFINAL'
        tasks = {'case-a': {'foo/bar.py': body}}
        manifest = self.mapped_manifest(tasks)
        manifest['tasks'][0]['files'][0]['declared_path'] = 'foo//bar.py'
        before = deepcopy(manifest); plan = self.plan(manifest); root = self.write(tasks)
        with patch.object(overlay, 'read_safe', wraps=overlay.read_safe) as reader:
            self.assertEqual(self.read(root, manifest, plan), tasks['case-a'])
        self.assertEqual([call.args[1] for call in reader.call_args_list], ['case-a/foo/bar.py'])
        self.assertEqual(manifest, before)
        canonical_declaration = deepcopy(manifest)
        canonical_declaration['tasks'][0]['files'][0]['declared_path'] = 'foo/bar.py'
        self.assertNotEqual(result_digest(manifest), result_digest(canonical_declaration))
        with patch.object(overlay, 'read_safe') as reader:
            with self.assertRaises(ContractError): self.read(root, canonical_declaration, plan)
            reader.assert_not_called()
        overlay.validate_overlay_manifest(canonical_declaration, self.plan(canonical_declaration))

    def test_v2_mapping_rejects_other_normalizations_absolute_paths_and_schema_field_mixups(self):
        good = self.mapped_manifest({'case-a': {'foo/bar.py': b'x'}})
        for declared in ('foo//other.py', '/foo//bar.py', '//foo/bar.py', 'foo//bar.py/',
                         './foo//bar.py', 'foo/./bar.py', 'foo/../foo/bar.py', 'foo\\bar.py',
                         'Foo//bar.py', 'foo//bar.py\n', 'foo//bar.py\x00',
                         'x' * 4097 + '//bar.py', None, 1):
            bad = deepcopy(good); bad['tasks'][0]['files'][0]['declared_path'] = declared
            with self.subTest(declared=repr(declared)), self.assertRaises(ContractError):
                overlay.validate_overlay_manifest(bad, self.plan(bad))
        nfd = self.mapped_manifest({'case-a': {'foo/caf\u00e9.py': b'x'}})
        nfd['tasks'][0]['files'][0]['declared_path'] = 'foo//cafe\u0301.py'
        with self.assertRaises(ContractError): overlay.validate_overlay_manifest(nfd, self.plan(nfd))
        for bad in (good | {'schema': 'macr.repair.scoring-overlay.v1'},
                    self.manifest({'case-a': {'foo/bar.py': b'x'}}) | {'schema': 'macr.repair.scoring-overlay.v2'}):
            with self.subTest(schema=bad['schema']), self.assertRaises(ContractError):
                overlay.validate_overlay_manifest(bad, self.plan(bad))

    def test_v2_cannot_alias_destinations_or_relax_original_namespace_permissions(self):
        good = self.mapped_manifest({'case-a': {'foo/bar.py': b'x'}})
        duplicate = deepcopy(good)
        duplicate['tasks'][0]['files'][0]['declared_path'] = 'foo//bar.py'
        duplicate['tasks'][0]['files'].append({**duplicate['tasks'][0]['files'][0], 'declared_path': 'foo///bar.py'})
        with self.assertRaises(ContractError): overlay.validate_overlay_manifest(duplicate, self.plan(duplicate))
        for paths in (('Foo/a.py', 'foo/b.py'), ('foo/A.py', 'foo/a.py'), ('foo.py', 'foo.py/a.py')):
            bad = self.mapped_manifest({'case-a': {path: b'x' for path in paths}})
            with self.subTest(paths=paths), self.assertRaises(ContractError):
                overlay.validate_overlay_manifest(bad, self.plan(bad))
        for path in ('impl.py', 'IMPL.py', 'impl.py/child.py'):
            bad = self.mapped_manifest({'case-a': {path: b'x'}})
            with self.subTest(path=path), patch.object(overlay, 'read_safe') as reader:
                with self.assertRaises(ContractError): self.read(fixture_root(), bad, self.plan(bad))
                reader.assert_not_called()

    def test_v1_manifest_shape_and_digest_remain_unchanged(self):
        manifest = self.manifest({'case-a': {'tests/public.py': b'unchanged'}})
        before = deepcopy(manifest); digest = result_digest(manifest)
        overlay.validate_overlay_manifest(manifest, self.plan(manifest))
        self.assertEqual((manifest, result_digest(manifest)), (before, digest))
        self.assertEqual(set(manifest['tasks'][0]['files'][0]), {'path', 'bytes', 'sha256'})

    def test_symlink_and_nonregular_leaves_and_ancestors_are_rejected(self):
        tasks = {'case-a': {'tests/public.py': b'x'}}
        manifest = self.manifest(tasks); plan = self.plan(manifest)
        for linked_part in ('root', 'task', 'leaf', 'directory'):
            with self.subTest(linked_part=linked_part):
                root = fixture_root(); outside = self.write(tasks)
                if linked_part == 'root':
                    alias = root / 'alias'; alias.symlink_to(outside, target_is_directory=True)
                    root = alias
                elif linked_part == 'task':
                    (root / 'case-a').symlink_to(outside / 'case-a', target_is_directory=True)
                else:
                    parent = root / 'case-a/tests'; parent.mkdir(parents=True)
                    if linked_part == 'leaf':
                        (parent / 'public.py').symlink_to(outside / 'case-a/tests/public.py')
                    else:
                        (parent / 'public.py').mkdir()
                try:
                    with self.assertRaises((ContractError, OSError)): self.read(root, manifest, plan)
                finally:
                    if linked_part == 'root': root.unlink()
                    elif linked_part == 'task': (root / 'case-a').unlink()
                    elif linked_part == 'leaf': (root / 'case-a/tests/public.py').unlink()


if __name__ == '__main__': unittest.main()
