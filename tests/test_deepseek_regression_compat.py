"""Synthetic five-task regression scope and non-gold commitment boundaries."""
import json
from pathlib import Path
import unittest

from debugger_fixtures import fixture_root
from test_deepseek_study_profile import COMMITMENT, development_pack
from test_deepseek_comparison_pilot import (
    make_regression_selection, regression_commitment, prepare_regression, scripted)
from macr.evals import deepseek_comparison_pilot as pilot
from macr.evals.deepseek_study_profile import prepare_study, load_study, digest, StudyError

PROJECT = Path(__file__).resolve().parents[1]
V2 = 'study-8192-v2'


class RegressionCompatibilityTests(unittest.TestCase):
    def fixture(self, count=5):
        root = fixture_root()
        pack = development_pack(root, count)
        selection = make_regression_selection()
        return root, pack, selection, regression_commitment(root, pack, selection)

    def test_five_task_non_gold_regression_roundtrips_seven_unique_cells(self):
        root, pack, selection, custodian = self.fixture()
        out = root.parent / (root.name + '-control')
        try:
            plan = pilot.prepare_pilot(root, custodian=custodian, out=out, phase='R',
                project_root=PROJECT, regression_selection=selection)
        except StudyError as error:
            self.fail(f'five-task non-gold regression is not supported: {error}')
        self.assertEqual(len(plan['cells']), 7)
        self.assertEqual({c['task_id'] for c in plan['cells']}, {'dev0', 'dev1', 'dev2', 'dev3', 'dev4'})
        self.assertEqual(len({(c['task_id'], c['strategy']) for c in plan['cells']}), 7)
        self.assertEqual(plan['custodian'], custodian)
        self.assertEqual(len(plan['custodian']), 7)
        self.assertNotIn('gold_sha256', plan['custodian'])
        self.assertEqual(pilot.load_pilot(out), plan)
        study = load_study(Path(plan['profiles'][V2]['control_root']))
        self.assertIs(study['regression_custody'], True)
        self.assertEqual(study['custodian'], custodian)
        self.assertEqual(len(study['tasks']), 5)
        self.assertEqual(len(pilot.propose_pilot_approval(out)['sources']), 10)

    def test_four_commitment_bindings_and_extra_fields_cannot_be_forged(self):
        for field in ('neutral_manifest_sha256', 'selection_sha256', 'pack_sha256',
                'source_inventory_sha256', 'gold_sha256'):
            with self.subTest(field=field):
                root, _, selection, custodian = self.fixture()
                custodian[field] = '0' * 64
                out = root.parent / (root.name + '-bad-hash')
                with self.assertRaises(StudyError):
                    pilot.prepare_pilot(root, custodian=custodian, out=out, phase='R',
                        project_root=PROJECT, regression_selection=selection)
                self.assertFalse(out.exists())

    def test_regression_selection_must_cover_exactly_the_neutral_pack_tasks(self):
        root, _, selection, custodian = self.fixture(count=6)
        out = root.parent / (root.name + '-unselected-task')
        with self.assertRaises(StudyError):
            pilot.prepare_pilot(root, custodian=custodian, out=out, phase='R',
                project_root=PROJECT, regression_selection=selection)
        self.assertFalse(out.exists())

    def test_gold_and_regression_commitments_cannot_cross_modes(self):
        root, _, selection, custodian = self.fixture()
        out = root.parent / (root.name + '-gold-as-regression')
        with self.assertRaises(StudyError):
            pilot.prepare_pilot(root, custodian=COMMITMENT, out=out, phase='R',
                project_root=PROJECT, regression_selection=selection)
        self.assertFalse(out.exists())
        for mode, profile, commitment in ((False, V2, custodian),
                (True, 'study-evidence-v1', custodian), (True, V2, COMMITMENT)):
            with self.subTest(mode=mode, profile=profile):
                destination = root.parent / (root.name + '-mode-' + str(mode) + '-' + profile)
                with self.assertRaises(StudyError):
                    prepare_study(root, custodian=commitment, out=destination,
                        project_root=PROJECT, execution_profile=profile, regression_custody=mode)
                self.assertFalse(destination.exists())
        _, regression, r_plan = prepare_regression(pilot)
        pilot.run_pilot_dryrun(regression, scripted_by_cell=scripted(r_plan))
        u_root, u_pack, u_selection, u_custodian = self.fixture(count=9)
        u_pack['pack_id'] = 'different-synthetic-unseen-mode-boundary'
        (u_root / 'pack.json').write_text(json.dumps(u_pack))
        u_custodian = regression_commitment(u_root, u_pack, u_selection)
        destination = u_root.parent / (u_root.name + '-regression-as-unseen')
        with self.assertRaises(StudyError):
            pilot.prepare_pilot(u_root, custodian=u_custodian, out=destination, phase='U',
                project_root=PROJECT, regression_control=regression)
        self.assertFalse(destination.exists())

    def test_rehashed_mode_removal_cannot_reinterpret_a_regression_study(self):
        for mode in ('remove', 'false'):
            with self.subTest(mode=mode):
                _, out, plan = prepare_regression(pilot)
                control = Path(plan['profiles'][V2]['control_root'])
                value = json.loads((control / 'study.json').read_bytes())
                if mode == 'remove':
                    del value['regression_custody']
                else:
                    value['regression_custody'] = False
                value['profile_sha256'] = digest({k: v for k, v in value.items() if k != 'profile_sha256'})
                (control / 'study.json').write_text(json.dumps(value))
                with self.assertRaises(StudyError):
                    load_study(control)

    def test_unseen_mode_still_requires_nine_tasks_in_three_equal_projects(self):
        _, regression, plan = prepare_regression(pilot)
        pilot.run_pilot_dryrun(regression, scripted_by_cell=scripted(plan))
        for shape in ('five_tasks', 'one_project'):
            with self.subTest(shape=shape):
                root = fixture_root()
                pack = development_pack(root, 5 if shape == 'five_tasks' else 9)
                pack['pack_id'] = 'different-synthetic-unseen-grid-' + shape
                if shape == 'one_project':
                    for task in pack['tasks']:
                        task['project_id'] = 'single-project'
                (root / 'pack.json').write_text(json.dumps(pack))
                out = root.parent / (root.name + '-invalid-unseen-grid')
                with self.assertRaises(StudyError):
                    pilot.prepare_pilot(root, custodian=COMMITMENT, out=out, phase='U',
                        project_root=PROJECT, regression_control=regression)
                self.assertFalse(out.exists())
