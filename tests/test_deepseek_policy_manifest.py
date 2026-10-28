import copy
import json
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.evals.deepseek_policy_manifest import (
    DEFAULT_BUDGET, MAX_FILE_BYTES, MAX_MANIFEST_BYTES, PolicyManifestError,
    load_policy_manifest, prepare_policy_manifest, read_snapshot_file,
)


class DeepSeekPolicyManifestTests(unittest.TestCase):
    def setUp(self):
        self.root = fixture_root()
        self.dataset = self.root / 'dataset'
        self.snapshot = self.dataset / 'snapshots' / 'p01'
        self.snapshot.mkdir(parents=True)
        self.raw = b'\r\ndef compute():\r\n    return 2\r'
        (self.snapshot / 'logic.py').write_bytes(self.raw)
        (self.dataset / 'gold.json').write_text('GOLD_ONLY_SENTINEL', encoding='utf-8')
        self.case = {
            'id': 'p01', 'snapshot_root': 'snapshots/p01',
            'question': 'Does compute satisfy the requirement?',
            'scenario': 'compute() must return 2.', 'assumptions': ['Ordinary Python 3 semantics.'],
            'authorized_files': ['logic.py'], 'initial_evidence_files': ['logic.py'],
            'stratum': 'full_context', 'evidence_parity': 'equal',
            'entrypoint': {'file': 'logic.py', 'symbol': 'compute'},
        }
        self.out = self.root / 'run'

    def prepare(self, **kwargs):
        values = {'dataset_root': self.dataset, 'out': self.out,
                  'model': {'model_id': 'deepseek-chat'}}
        values.update(kwargs)
        return prepare_policy_manifest(self.case, **values)

    def test_round_trip_binds_neutral_task_and_preserves_physical_lines(self):
        from macr.investigation.source_text import physical_lines
        manifest = self.prepare(repeat=3)
        loaded = load_policy_manifest(self.out, dataset_root=self.dataset)
        self.assertEqual(loaded, manifest)
        self.assertEqual(manifest['task'], self.case)
        self.assertEqual(manifest['repeat'], 3)
        self.assertEqual(manifest['arms'], ['A', 'B', 'C', 'D'])
        self.assertEqual(manifest['budget'], DEFAULT_BUDGET)
        self.assertNotIn('GOLD_ONLY_SENTINEL', json.dumps(manifest))
        text = read_snapshot_file(loaded, self.dataset, 'logic.py')
        self.assertEqual(text.encode('utf-8'), self.raw)
        self.assertEqual(physical_lines(text), ('\r\n', 'def compute():\r\n', '    return 2\r'))
        self.case['question'] = 'caller mutation'
        self.assertNotEqual(manifest['task']['question'], self.case['question'])

    def test_only_authorized_files_are_opened_and_sibling_gold_cannot_be_read(self):
        from macr.evals import deepseek_policy_manifest as module
        real = module.read_safe
        opened = []

        def record(root, relative, limit):
            opened.append(relative)
            return real(root, relative, limit)

        with patch.object(module, 'read_safe', side_effect=record):
            manifest = self.prepare()
            load_policy_manifest(self.out, dataset_root=self.dataset)
            for relative in ('../gold.json', '../../gold.json', '/gold.json', 'gold.json',
                             'rubric.py', 'answers.py', 'test_answer.py', 'unlisted.py',
                             './logic.py', 'sub/../logic.py', 'sub\\logic.py'):
                with self.subTest(relative=relative), self.assertRaises(PolicyManifestError):
                    read_snapshot_file(manifest, self.dataset, relative)
        self.assertEqual(opened, ['snapshots/p01/logic.py', 'manifest.json', 'snapshots/p01/logic.py'])

    def test_answer_paths_and_extra_answer_metadata_fail_before_output(self):
        for name in ('gold.py', 'rubric.json', 'reference.py', 'answers.py', 'test_logic.py',
                     'tests/logic.py', '../gold.json'):
            case = copy.deepcopy(self.case)
            case['authorized_files'] = case['initial_evidence_files'] = [name]
            case['entrypoint']['file'] = name
            with self.subTest(name=name), self.assertRaises(PolicyManifestError):
                prepare_policy_manifest(case, dataset_root=self.dataset, out=self.out,
                                        model={'model_id': 'deepseek-chat'})
            self.assertFalse(self.out.exists())
        for field in ('gold', 'expected_verdict', 'rubric', 'accepted_shape_witness'):
            self.case[field] = 'answer'
            with self.subTest(field=field), self.assertRaises(PolicyManifestError):
                self.prepare()
            self.case.pop(field)
        self.assertFalse(self.out.exists())

    def test_source_drift_is_rejected_on_load_and_individual_read(self):
        manifest = self.prepare()
        (self.snapshot / 'logic.py').write_bytes(self.raw.replace(b'2', b'3'))
        with self.assertRaisesRegex(PolicyManifestError, 'policy_snapshot_drift'):
            load_policy_manifest(self.out, dataset_root=self.dataset)
        with self.assertRaisesRegex(PolicyManifestError, 'policy_snapshot_drift'):
            read_snapshot_file(manifest, self.dataset, 'logic.py')

    def test_every_run_binding_rejects_tampering(self):
        manifest = self.prepare()
        changes = [
            lambda m: m['task'].update(question='Changed question'),
            lambda m: m['snapshot']['files']['logic.py'].update(sha256='0' * 64),
            lambda m: m['snapshot'].update(root='snapshots/p02'),
            lambda m: m['model'].update(model_id='another-model'),
            lambda m: m.update(prompt_version='other-v1'),
            lambda m: m.update(arms=['A', 'B']),
            lambda m: m['budget'].update(max_total_input_tokens=24003),
            lambda m: m.update(repeat=2),
            lambda m: m.update(schema='macr.deepseek.policy.manifest.v2'),
        ]
        for change in changes:
            changed = copy.deepcopy(manifest)
            change(changed)
            (self.out / 'manifest.json').write_text(json.dumps(changed), encoding='utf-8')
            with self.subTest(change=change), self.assertRaises(PolicyManifestError):
                load_policy_manifest(self.out, dataset_root=self.dataset)
            with self.assertRaises(PolicyManifestError):
                read_snapshot_file(changed, self.dataset, 'logic.py')

    def test_source_and_manifest_reads_are_bounded(self):
        (self.snapshot / 'logic.py').write_bytes(b'x' * (MAX_FILE_BYTES + 1))
        with self.assertRaisesRegex(PolicyManifestError, 'size_limit'):
            self.prepare()
        self.assertFalse(self.out.exists())
        (self.snapshot / 'logic.py').write_bytes(self.raw)
        self.prepare()
        (self.out / 'manifest.json').write_bytes(b' ' * (MAX_MANIFEST_BYTES + 1))
        with self.assertRaisesRegex(PolicyManifestError, 'size_limit'):
            load_policy_manifest(self.out, dataset_root=self.dataset)

    def test_total_source_limit_and_file_count_are_enforced(self):
        for number in range(5):
            name = f'part{number}.py'
            (self.snapshot / name).write_bytes(b'#' * MAX_FILE_BYTES)
        self.case['authorized_files'] = self.case['initial_evidence_files'] = [f'part{i}.py' for i in range(5)]
        self.case['entrypoint']['file'] = 'part0.py'
        with self.assertRaisesRegex(PolicyManifestError, 'policy_source_size_limit'):
            self.prepare()
        self.case['authorized_files'] = self.case['initial_evidence_files'] = [f'part{i}.py' for i in range(25)]
        with self.assertRaisesRegex(PolicyManifestError, 'policy_files_invalid'):
            self.prepare()
        self.assertFalse(self.out.exists())

    def test_repeat_model_and_budget_validation_precede_output(self):
        invalid = [
            {'repeat': True}, {'repeat': 0}, {'repeat': 1001}, {'repeat': 1.0},
            {'model': {'model_id': 'deepseek-chat', 'api_key': 'forbidden'}},
            {'model': {'model_id': 'deepseek-chat', 'temperature': float('nan')}},
            {'model': {'model_id': 'deepseek-chat', 'endpoint': 'https://user:pass@example.test/v1'}},
            {'model': {'model_id': 'deepseek-chat', 'thinking': False, 'reasoning_effort': 'high'}},
            {'model': {'model_id': 'deepseek-chat', 'thinking': True, 'reasoning_effort': 'none'}},
            {'model': {'model_id': 'deepseek-chat', 'reasoning_effort': 'high'}},
            {'prompt_version': 'unknown-prompt-v2'}, {'budget': {}},
        ]
        for key, value in [('max_total_input_tokens', True), ('max_wall_seconds', float('inf')),
                           ('max_wall_seconds', 301), ('max_tool_reads', 5), ('max_record_bytes', -1)]:
            invalid.append({'budget': dict(DEFAULT_BUDGET, **{key: value})})
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(PolicyManifestError):
                self.prepare(**values)
            self.assertFalse(self.out.exists())

    def test_enabled_reasoning_model_settings_are_frozen(self):
        model = {'model_id': 'deepseek-flash', 'provider': 'deepseek',
                 'thinking': True, 'reasoning_effort': 'high'}
        manifest = self.prepare(model=model)
        self.assertEqual(manifest['model'], model)
        self.assertEqual(load_policy_manifest(self.out, dataset_root=self.dataset)['model'], model)

    def test_duplicate_keys_nonfinite_and_malformed_manifest_are_rejected(self):
        self.prepare()
        for raw in ('{"schema":"v1","schema":"v1"}', '{"repeat":NaN}', '[]', '{'):
            (self.out / 'manifest.json').write_text(raw, encoding='utf-8')
            with self.subTest(raw=raw), self.assertRaises(PolicyManifestError):
                load_policy_manifest(self.out, dataset_root=self.dataset)

    def test_existing_output_is_exclusive(self):
        manifest = self.prepare()
        before = (self.out / 'manifest.json').read_bytes()
        with self.assertRaisesRegex(PolicyManifestError, 'policy_output_exists'):
            self.prepare(repeat=2)
        self.assertEqual((self.out / 'manifest.json').read_bytes(), before)
        self.assertEqual(load_policy_manifest(self.out, dataset_root=self.dataset), manifest)

    def test_symlinks_in_source_root_or_output_paths_are_rejected(self):
        original = self.snapshot / 'logic.py'
        original.unlink()
        original.symlink_to(self.dataset / 'gold.json')
        with self.assertRaises(PolicyManifestError):
            self.prepare()
        original.unlink()
        original.write_bytes(self.raw)
        linked_dataset = self.root / 'linked-dataset'
        linked_dataset.symlink_to(self.dataset, target_is_directory=True)
        self.addCleanup(linked_dataset.unlink)
        with self.assertRaises(PolicyManifestError):
            self.prepare(dataset_root=linked_dataset)
        linked_out = self.root / 'linked-parent'
        linked_out.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(linked_out.unlink)
        with self.assertRaises(PolicyManifestError):
            self.prepare(out=linked_out / 'new-run')
        directory = self.snapshot / 'nested'
        directory.symlink_to(self.snapshot, target_is_directory=True)
        self.addCleanup(directory.unlink)
        self.case['authorized_files'] = self.case['initial_evidence_files'] = ['nested/logic.py']
        self.case['entrypoint']['file'] = 'nested/logic.py'
        with self.assertRaises(PolicyManifestError):
            self.prepare()
        self.assertFalse(self.out.exists())

    def test_retrieval_stratum_requires_an_explicit_unequal_initial_subset(self):
        (self.snapshot / 'helper.py').write_text('def helper():\n    return 2\n', encoding='utf-8')
        self.case['authorized_files'].append('helper.py')
        with self.assertRaisesRegex(PolicyManifestError, 'policy_evidence_parity_invalid'):
            self.prepare()
        self.case.update(stratum='retrieval_required', evidence_parity='unequal_by_design')
        manifest = self.prepare()
        self.assertEqual(manifest['task']['initial_evidence_files'], ['logic.py'])
        self.assertEqual(read_snapshot_file(manifest, self.dataset, 'helper.py'), 'def helper():\n    return 2\n')


if __name__ == '__main__':
    unittest.main()
