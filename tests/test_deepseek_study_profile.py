"""Public development contract fixtures; these are not unseen questions."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.evals import deepseek_study_profile as study


def development_pack(root, count=9):
    tasks = []
    for number in range(count):
        snapshot = f'snapshots/dev{number}'
        folder = root / snapshot
        folder.mkdir(parents=True)
        (folder / 'entry.py').write_text('from helper import double\ndef run(x):\n    return double(x)\n')
        (folder / 'helper.py').write_text('def double(x):\n    return x * 2\n')
        tasks.append({'id': f'dev{number}', 'project_id': f'project{number % 3}',
            'snapshot_root': snapshot, 'question': 'Describe the result from the authorized source.',
            'scenario': 'A development fixture, not a quality benchmark.',
            'assumptions': ['x is an integer.'], 'authorized_files': ['entry.py', 'helper.py'],
            'initial_evidence_files': ['entry.py'], 'entrypoint': {'file': 'entry.py', 'symbol': 'run'},
            'license': {'spdx': 'CC0-1.0', 'provenance': 'Self-authored public development fixture.'}})
    pack = {'schema': study.PACK_SCHEMA, 'pack_id': 'known-development-fixture', 'tasks': tasks}
    (root / 'pack.json').write_text(json.dumps(pack))
    return pack


COMMITMENT = {'schema': 'macr.deepseek.study.custodian.v1', 'version': 'fixture-only',
    'gold_sha256': 'a' * 64, 'rubric_sha256': 'b' * 64,
    'qualification_statement': 'Synthetic commitment only; no unseen qualification claimed.'}


class StudyProfileTests(unittest.TestCase):
    def setUp(self):
        self.root = fixture_root()
        self.pack = development_pack(self.root)

    def test_freeze_gold_free_pack_and_balanced_four_strategy_grid(self):
        result = study.prepare_study(self.root, custodian=COMMITMENT,
            out=self.root.parent / (self.root.name + '-control'), project_root=Path(study.__file__).parents[3])
        self.assertEqual(len(result['tasks']), 9)
        self.assertEqual(len(result['cells']), 36)
        self.assertEqual(result['planned_request_ceiling'], 108)
        self.assertFalse(result['remote_enabled'])
        self.assertEqual(result['budget']['max_usd'], '2.000000')
        for row in result['tasks']:
            self.assertEqual(row['fixed_plan']['source_sha256'], row['manifest']['snapshot']['sha256'])
        self.assertNotIn('gold.json', json.dumps(result))
        self.assertEqual(result['custodian']['gold_sha256'], 'a' * 64)
        for strategy in study.STRATEGIES:
            positions = [i % 4 for i, cell in enumerate(result['cells']) if cell['strategy'] == strategy]
            self.assertLessEqual(max(positions.count(i) for i in range(4)) - min(positions.count(i) for i in range(4)), 1)

    def test_gold_filename_is_rejected_without_opening_it(self):
        (self.root / 'gold.json').write_text('DO-NOT-READ-SENTINEL')
        with patch.object(Path, 'read_text', side_effect=AssertionError('unscoped read')):
            with self.assertRaisesRegex(study.StudyError, 'pack_inventory_invalid'):
                study.load_neutral_pack(self.root)

    def test_labels_extra_fields_and_unlisted_source_are_rejected(self):
        for mutation in ('category', 'gold_sha256'):
            payload = copy.deepcopy(self.pack)
            payload['tasks'][0][mutation] = 'forbidden'
            (self.root / 'pack.json').write_text(json.dumps(payload))
            with self.assertRaises(study.StudyError):
                study.load_neutral_pack(self.root)
        (self.root / 'pack.json').write_text(json.dumps(self.pack))
        (self.root / 'snapshots/dev0/extra.py').write_text('x=1\n')
        with self.assertRaisesRegex(study.StudyError, 'pack_inventory_invalid'):
            study.load_neutral_pack(self.root)

    def test_source_initial_byte_limits_and_license_are_enforced(self):
        (self.root / 'snapshots/dev0/entry.py').write_text('#' * 4097)
        with self.assertRaisesRegex(study.StudyError, 'initial_source_limit'):
            study.load_neutral_pack(self.root)

    def test_symlink_and_answer_named_authorized_file_rejected(self):
        self.pack['tasks'][0]['authorized_files'][1] = 'gold.py'
        (self.root / 'pack.json').write_text(json.dumps(self.pack))
        with self.assertRaises(study.StudyError):
            study.load_neutral_pack(self.root)

    def test_new_profile_cannot_enable_live_or_recycle_old_budget(self):
        with self.assertRaisesRegex(study.StudyError, 'remote_disabled'):
            study.validate_budget({'max_requests':108,'max_usd':'2.000000','remote_enabled':True})
        with self.assertRaises(study.StudyError):
            study.validate_budget({'max_requests':108,'max_usd':'2.000000','predecessor_control_root':'old'})
        self.assertEqual(study.validate_budget({'max_requests':2,'max_usd':'0.330000'})['max_requests'], 2)

    def test_empty_budget_and_different_implementation_root_cannot_reset_or_misbind(self):
        with self.assertRaises(study.StudyError):
            study.validate_budget({})
        other=fixture_root()
        with self.assertRaisesRegex(study.StudyError,'implementation_root_mismatch'):
            study.prepare_study(self.root,custodian=COMMITMENT,
                out=self.root.parent/(self.root.name+'-control'),project_root=other)

    def test_source_or_implementation_drift_fails_before_execution(self):
        out = self.root.parent / (self.root.name + '-control')
        frozen = study.prepare_study(self.root,custodian=COMMITMENT,out=out,project_root=Path(study.__file__).parents[3])
        self.assertEqual(study.load_study(out)['profile_sha256'], frozen['profile_sha256'])
        (self.root / 'snapshots/dev0/helper.py').write_text('def double(x):\n    return x * 3\n')
        with self.assertRaises(study.StudyError):
            study.load_study(out)

    def test_actual_read_policy_dependency_and_package_initializers_are_frozen(self):
        out=self.root.parent/(self.root.name+'-control')
        frozen=study.prepare_study(self.root,custodian=COMMITMENT,out=out,
            project_root=Path(study.__file__).parents[3])
        dependency='src/macr/investigation/evidence_profiles.py'
        self.assertIn('src/macr/evals/deepseek_study_live.py',frozen['implementation'])
        self.assertIn('src/macr/evals/deepseek_study_continuation.py',frozen['implementation'])
        self.assertIn('scripts/run_deepseek_study_live.py',frozen['implementation'])
        self.assertIn(dependency,frozen['implementation'])
        self.assertIn('src/macr/__init__.py',frozen['implementation'])
        real_read=study._read
        def changed(root,name,limit):
            raw=real_read(root,name,limit)
            return raw+b'\n# changed read policy\n' if name==dependency else raw
        with patch.object(study,'_read',side_effect=changed):
            with self.assertRaisesRegex(study.StudyError,'study_implementation_drift'):
                study.load_study(out)

    def test_malformed_commitment_is_not_read_as_a_file(self):
        with self.assertRaises(study.StudyError):
            study.prepare_study(self.root,custodian={'gold_path':'outside/gold.json'},
                out=self.root.parent / (self.root.name + '-control'),project_root=Path(study.__file__).parents[3])
