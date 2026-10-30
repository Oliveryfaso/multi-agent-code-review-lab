"""Two-stage admission fixtures, never historical data or a model-quality test."""
import copy
import importlib
import importlib.util
from hashlib import sha256
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from test_deepseek_policy_runner import UNKNOWN, call, reply
from test_deepseek_study_profile import COMMITMENT, development_pack
from macr.evals.deepseek_study_profile import digest, StudyError

PROJECT = Path(__file__).resolve().parents[1]
V2 = 'study-8192-v2'
EVIDENCE = 'study-evidence-v1'


def make_regression_selection():
    return {'schema': 'macr.deepseek.pilot.regression.v1', 'source_sha256': 'c' * 64,
        'selection_statement': 'Self-authored fixture for all seven selected length failures; not historical evidence.',
        'rows': [{'task_id': task_id, 'strategy': strategy,
            'response_sha256': f'{i + 1:064x}', 'finish_reason': 'length'}
            for i, (task_id, strategy) in enumerate((
                ('dev0', 'direct_full_3'), ('dev0', 'fixed_read_3'),
                ('dev1', 'fixed_read_3'), ('dev2', 'direct_full_3'),
                ('dev3', 'direct_full_3'), ('dev4', 'direct_full_3'),
                ('dev4', 'fixed_read_3')))]}


def regression_commitment(root, pack, selection):
    """Synthetic-only hash commitment; this does not qualify an actual dataset."""
    sources = []
    for task in pack['tasks']:
        for name in task['authorized_files']:
            path = task['snapshot_root'] + '/' + name
            raw = (root / path).read_bytes()
            sources.append({'path': path, 'size_bytes': len(raw), 'sha256': sha256(raw).hexdigest()})
    return {'schema': 'macr.deepseek.regression.custodian.v1', 'version': 'synthetic-only',
        'neutral_manifest_sha256': selection['source_sha256'],
        'selection_sha256': digest(selection), 'pack_sha256': digest(pack),
        'source_inventory_sha256': digest(sorted(sources, key=lambda row: row['path'])),
        'qualification_statement': 'Self-authored compatibility fixture only; not actual regression qualification.'}


def prepare_regression(pilot):
    root = fixture_root()
    pack = development_pack(root, 5)
    selection = make_regression_selection()
    out = root.parent / (root.name + '-regression')
    plan = pilot.prepare_pilot(root, custodian=regression_commitment(root, pack, selection),
        out=out, phase='R', project_root=PROJECT, regression_selection=selection)
    return root, out, plan


def scripted(plan):
    return {cell['cell_id']: ((reply(None, calls=[call('end', 'finish', {'diagnosis': UNKNOWN})]),)
        if cell['strategy'] == 'adaptive_read_3' else tuple(reply(json.dumps(UNKNOWN)) for _ in range(3)))
        for cell in plan['cells']}


def approved(pilot, out):
    return pilot.propose_pilot_approval(out) | {
        'authorized': True, 'confirmation_ref': 'synthetic-operator-consent-not-live-permission'}


def prepare_unseen(pilot):
    _, regression, regression_plan = prepare_regression(pilot)
    result = pilot.run_pilot_dryrun(regression, scripted_by_cell=scripted(regression_plan))
    if not result['promotion_gate']['eligible']:
        raise AssertionError('synthetic regression did not reach its machine-only gate')
    root = fixture_root()
    pack = development_pack(root, 9)
    pack['pack_id'] = 'second-independent-development-contract-fixture'
    for task in pack['tasks']:
        (root / task['snapshot_root'] / 'helper.py').write_text('def double(x):\n    return x * 4\n')
    (root / 'pack.json').write_text(json.dumps(pack))
    out = root.parent / (root.name + '-unseen-contract')
    plan = pilot.prepare_pilot(root, custodian=COMMITMENT, out=out, phase='U',
        project_root=PROJECT, regression_control=regression)
    return root, out, plan, regression


class ComparisonPilotTests(unittest.TestCase):
    def pilot(self):
        self.assertIsNotNone(importlib.util.find_spec('macr.evals.deepseek_comparison_pilot'),
            'the separately approved two-stage pilot entry is not implemented')
        return importlib.import_module('macr.evals.deepseek_comparison_pilot')

    def test_approval_binds_exact_regression_scope_without_enabling_it(self):
        pilot = self.pilot()
        _, out, plan = prepare_regression(pilot)
        self.assertEqual(plan['phase'], 'R')
        self.assertEqual(plan['budget'], {'max_requests': 21, 'max_usd': '1.000000'})
        self.assertEqual(len(plan['cells']), 7)
        self.assertEqual(set(plan['profiles']), {V2})
        self.assertEqual({(c['task_id'], c['strategy']) for c in plan['cells']},
            {('dev0', 'direct_full_3'), ('dev0', 'fixed_read_3'), ('dev1', 'fixed_read_3'),
             ('dev2', 'direct_full_3'), ('dev3', 'direct_full_3'), ('dev4', 'direct_full_3'),
             ('dev4', 'fixed_read_3')})
        proposal = pilot.propose_pilot_approval(out)
        self.assertFalse(proposal['authorized'])
        self.assertEqual(proposal['confirmation_ref'], '')
        self.assertEqual(proposal['plan_sha256'], plan['plan_sha256'])
        self.assertEqual(proposal['max_requests'], 21)
        self.assertEqual(proposal['max_usd'], '1.000000')
        self.assertEqual(proposal['endpoint'], 'https://api.deepseek.com/chat/completions')
        self.assertEqual(len(proposal['sources']), 10)
        self.assertEqual({row['path'].split('/')[1] for row in proposal['sources']},
            {f'dev{i}' for i in range(5)})
        self.assertFalse((out / 'execution.claim.json').exists())

    def test_disabled_unapproved_and_changed_scope_cannot_read_key(self):
        pilot = self.pilot()
        _, out, _ = prepare_regression(pilot)
        approval = approved(pilot, out)
        never = lambda: self.fail('preflight reached credential ingress')
        with self.assertRaisesRegex(StudyError, 'pilot_live_disabled'):
            pilot.execute_approved_pilot(out, approval, key_reader=never)
        with self.assertRaisesRegex(StudyError, 'pilot_approval_missing'):
            pilot.execute_approved_pilot(out, pilot.propose_pilot_approval(out),
                enabled=True, key_reader=never)
        for change in ({'max_requests': 54}, {'max_usd': '2.500000'},
                {'plan_sha256': '0' * 64}, {'phase': 'U'},
                {'predecessor_unknown_usd': '0.660000'}):
            with self.subTest(change=change), self.assertRaises(StudyError):
                pilot.execute_approved_pilot(out, approval | change, enabled=True, key_reader=never)
        self.assertFalse((out / 'execution.claim.json').exists())

    def test_invalid_regression_selection_or_phase_is_rejected_before_output(self):
        pilot = self.pilot()
        variants = []
        selected = make_regression_selection()
        wrong = copy.deepcopy(selected); wrong['rows'].pop(); variants.append(wrong)
        wrong = copy.deepcopy(selected); wrong['rows'][0]['finish_reason'] = 'stop'; variants.append(wrong)
        wrong = copy.deepcopy(selected); wrong['rows'][0]['task_id'] = 'outside'; variants.append(wrong)
        wrong = copy.deepcopy(selected); wrong['rows'][0] = wrong['rows'][1]; variants.append(wrong)
        wrong = copy.deepcopy(selected); wrong['source_sha256'] = 'not-a-hash'; variants.append(wrong)
        for selection in variants:
            root = fixture_root(); pack = development_pack(root, 5)
            out = root.parent / (root.name + '-invalid')
            with self.assertRaises(StudyError):
                pilot.prepare_pilot(root, custodian=regression_commitment(root, pack, selection), out=out, phase='R',
                    project_root=PROJECT, regression_selection=selection)
            self.assertFalse(out.exists())
        root = fixture_root(); pack = development_pack(root, 5)
        out = root.parent / (root.name + '-invalid-phase')
        with self.assertRaises(StudyError):
            pilot.prepare_pilot(root, custodian=regression_commitment(root, pack, selected), out=out, phase='all',
                project_root=PROJECT, regression_selection=selected)
        self.assertFalse(out.exists())

    def test_rehashed_phase_budget_change_does_not_relax_the_frozen_contract(self):
        pilot = self.pilot()
        _, out, plan = prepare_regression(pilot)
        plan['budget']['max_usd'] = '2.000000'
        plan['plan_sha256'] = digest({k: v for k, v in plan.items() if k != 'plan_sha256'})
        (out / 'pilot.json').write_text(json.dumps(plan))
        with self.assertRaises(StudyError):
            pilot.load_pilot(out)

    def test_copied_control_directory_cannot_create_a_second_execution_scope(self):
        pilot = self.pilot()
        _, out, _ = prepare_regression(pilot)
        duplicate = out.parent / (out.name + '-copy')
        shutil.copytree(out, duplicate)
        with self.assertRaises(StudyError):
            pilot.propose_pilot_approval(duplicate)
        self.assertFalse((duplicate / 'execution.claim.json').exists())

    def test_source_drift_is_refused_before_credential_ingress(self):
        pilot = self.pilot()
        root, out, _ = prepare_regression(pilot)
        approval = approved(pilot, out)
        (root / 'snapshots/dev0/helper.py').write_text('def double(x):\n    return x * 99\n')
        with self.assertRaises(StudyError):
            pilot.execute_approved_pilot(out, approval, enabled=True,
                key_reader=lambda: self.fail('source drift read a credential'))
        self.assertFalse((out / 'execution.claim.json').exists())

    def test_real_execution_refuses_checkout_logs_before_key_or_claim(self):
        pilot = self.pilot()
        _, out, _ = prepare_regression(pilot)
        with self.assertRaisesRegex(StudyError, 'pilot_live_log_inside_checkout'):
            pilot.execute_approved_pilot(out, approved(pilot, out), enabled=True,
                key_reader=lambda: self.fail('checkout log scope reached credential ingress'))
        self.assertFalse((out / 'execution.claim.json').exists())

    def test_regression_promotion_rejects_summary_tampering_before_new_stage_output(self):
        pilot = self.pilot()
        for mutation in ('identity_anomaly', 'duplicate_rows', 'erased_known_accrual'):
            with self.subTest(mutation=mutation):
                _, regression, plan = prepare_regression(pilot)
                summary = pilot.run_pilot_dryrun(regression, scripted_by_cell=scripted(plan))
                self.assertTrue(summary['promotion_gate']['eligible'])
                self.assertEqual(summary['accounting']['known_accrued_usd'], '0.000294')
                if mutation == 'identity_anomaly':
                    summary['rows'][0]['identity_verified'] = False
                    summary['rows'][0]['operational_anomaly'] = True
                elif mutation == 'duplicate_rows':
                    summary['rows'] = [copy.deepcopy(summary['rows'][0]) for _ in range(7)]
                else:
                    summary['accounting']['known_accrued_usd'] = '0.000000'
                # Only this test-owned summary changes; accounted results and ledger stay intact.
                (regression / 'summary.json').write_text(json.dumps(summary))
                root = fixture_root()
                pack = development_pack(root, 9)
                pack['pack_id'] = 'second-independent-development-contract-fixture'
                (root / 'pack.json').write_text(json.dumps(pack))
                out = root.parent / (root.name + '-tampered-predecessor')
                with self.assertRaises(StudyError):
                    pilot.prepare_pilot(root, custodian=COMMITMENT, out=out, phase='U',
                        project_root=PROJECT, regression_control=regression)
                self.assertFalse(out.exists())

    def test_regression_dryrun_reports_only_seven_cells_and_machine_gate(self):
        pilot = self.pilot()
        _, out, plan = prepare_regression(pilot)
        with patch('macr.providers.deepseek._ApprovedTransport.post',
                side_effect=AssertionError('dryrun reached a live transport')):
            result = pilot.run_pilot_dryrun(out, scripted_by_cell=scripted(plan))
        self.assertEqual(len(result['rows']), 7)
        self.assertEqual(result['accounting']['request_count'], 21)
        self.assertEqual(result['accounting']['known_accrued_usd'], '0.000294')
        self.assertEqual(result['model_calls_live'], 0)
        self.assertFalse(result['batch_halted'])
        self.assertTrue(result['promotion_gate']['eligible'])
        self.assertNotEqual(result['provenance'], 'approved_live')
        self.assertTrue(all(row['status'] == 'semantic_pending' for row in result['rows']))

    def test_completed_or_unknown_claim_is_never_reexecuted(self):
        pilot = self.pilot()
        for unknown in (False, True):
            _, out, plan = prepare_regression(pilot)
            scripts = scripted(plan)
            if unknown: scripts[plan['cells'][0]['cell_id']] = ('timeout',)
            pilot.run_pilot_dryrun(out, scripted_by_cell=scripts)
            with self.assertRaises(StudyError):
                pilot.run_pilot_dryrun(out, scripted_by_cell=scripted(plan))

    def test_unknown_and_measured_overrun_stop_with_truthful_held_funds(self):
        pilot = self.pilot()
        for outcome in ('timeout', 'input_overrun'):
            _, out, plan = prepare_regression(pilot)
            scripts = scripted(plan)
            if outcome == 'timeout': scripts[plan['cells'][0]['cell_id']] = ('timeout',)
            else:
                overrun = reply(json.dumps(UNKNOWN))
                overrun['usage']['prompt_tokens'] = 8001
                overrun['usage']['prompt_cache_miss_tokens'] = 8001
                scripts[plan['cells'][0]['cell_id']] = (overrun,)
            result = pilot.run_pilot_dryrun(out, scripted_by_cell=scripts)
            self.assertTrue(result['batch_halted'])
            self.assertEqual(result['accounting']['request_count'], 1)
            self.assertEqual(result['accounting']['unknown_held_usd'],
                '0.330000' if outcome == 'timeout' else '0.000000')
            self.assertEqual(result['accounting']['known_accrued_usd'],
                '0.000000' if outcome == 'timeout' else '0.002410')
            self.assertEqual(sum(row['status'] == 'not_attempted' for row in result['rows']), 6)
            self.assertFalse(result['promotion_gate']['eligible'])

    def test_third_known_output_failure_stops_without_retrying_or_adding_cells(self):
        pilot = self.pilot()
        _, out, plan = prepare_regression(pilot)
        scripts = scripted(plan)
        for cell in plan['cells'][:3]:
            scripts[cell['cell_id']] = (reply('', finish='length'),)
        result = pilot.run_pilot_dryrun(out, scripted_by_cell=scripts)
        self.assertTrue(result['batch_halted'])
        self.assertEqual(result['accounting']['request_count'], 3)
        self.assertEqual(sum(row['status'] == 'failed' for row in result['rows']), 3)
        self.assertEqual(sum(row['status'] == 'not_attempted' for row in result['rows']), 4)
        self.assertFalse(result['promotion_gate']['eligible'])

    def test_unseen_pair_order_is_balanced_and_has_a_fresh_stage_budget(self):
        pilot = self.pilot()
        _, out, plan, regression = prepare_unseen(pilot)
        prior = (regression / 'summary.json').read_bytes()
        self.assertEqual(plan['phase'], 'U')
        self.assertEqual(plan['budget'], {'max_requests': 54, 'max_usd': '1.500000'})
        self.assertEqual(len(plan['cells']), 18)
        self.assertEqual(set(plan['profiles']), {V2, EVIDENCE})
        first = []
        for index in range(0, 18, 2):
            pair = plan['cells'][index:index + 2]
            self.assertEqual(pair[0]['task_id'], pair[1]['task_id'])
            self.assertEqual({cell['profile'] for cell in pair}, {V2, EVIDENCE})
            self.assertEqual({cell['strategy'] for cell in pair}, {'adaptive_read_3'})
            first.append(pair[0]['profile'])
        self.assertEqual(sorted((first.count(V2), first.count(EVIDENCE))), [4, 5])
        result = pilot.run_pilot_dryrun(out, scripted_by_cell=scripted(plan))
        self.assertEqual(result['accounting']['request_count'], 18)
        self.assertEqual(result['accounting']['known_accrued_usd'], '0.000252')
        self.assertEqual(result['accounting']['unknown_held_usd'], '0.000000')
        self.assertEqual((regression / 'summary.json').read_bytes(), prior)
        self.assertEqual(result['model_calls_live'], 0)

    def test_unseen_two_profiles_share_one_identity_pin(self):
        pilot = self.pilot()
        _, out, plan, _ = prepare_unseen(pilot)
        scripts = scripted(plan)
        drift = reply(None, calls=[call('end', 'finish', {'diagnosis': UNKNOWN})])
        drift['system_fingerprint'] = 'different-verified-fixture-fingerprint'
        scripts[plan['cells'][1]['cell_id']] = (drift,)
        self.assertNotEqual(plan['cells'][0]['profile'], plan['cells'][1]['profile'])
        result = pilot.run_pilot_dryrun(out, scripted_by_cell=scripts)
        self.assertTrue(result['batch_halted'])
        self.assertEqual(result['accounting']['request_count'], 2)
        self.assertEqual(result['accounting']['known_accrued_usd'], '0.000028')
        self.assertEqual(result['rows'][1]['error_code'], 'system_fingerprint_drift')
        self.assertEqual(sum(row['status'] == 'not_attempted' for row in result['rows']), 16)
        self.assertEqual(result['accounting']['unknown_held_usd'], '0.000000')

    def test_maximum_scripted_path_uses_21_plus_54_requests_without_live_or_semantic_credit(self):
        pilot = self.pilot()
        _, out, plan, regression = prepare_unseen(pilot)
        prior = (regression / 'summary.json').read_bytes()
        r_result = json.loads(prior)
        scripts = {cell['cell_id']: (
            reply(None, calls=[call('read-1', 'search', {'query': 'double'})]),
            reply(None, calls=[call('read-2', 'read_symbol', {'file': 'helper.py', 'symbol': 'double'})]),
            reply(None, calls=[call('end', 'finish', {'diagnosis': UNKNOWN})]))
            for cell in plan['cells']}
        with patch('macr.providers.deepseek._ApprovedTransport.post',
                side_effect=AssertionError('maximum scripted path reached a live transport')):
            u_result = pilot.run_pilot_dryrun(out, scripted_by_cell=scripts)
        self.assertEqual(r_result['accounting']['request_count'], 21)
        self.assertEqual(u_result['accounting']['request_count'], 54)
        self.assertEqual(r_result['accounting']['request_count'] + u_result['accounting']['request_count'], 75)
        self.assertEqual(r_result['accounting']['known_accrued_usd'], '0.000294')
        self.assertEqual(u_result['accounting']['known_accrued_usd'], '0.000756')
        self.assertEqual((regression / 'summary.json').read_bytes(), prior)
        self.assertEqual(len(list((regression / 'ledger').glob('*.reservation.json'))), 21)
        self.assertEqual(len(list((out / 'ledger').glob('*.reservation.json'))), 54)
        for summary in (r_result, u_result):
            self.assertEqual(summary['model_calls_live'], 0)
            self.assertFalse(summary['batch_halted'])
            self.assertEqual(summary['accounting']['unknown_held_usd'], '0.000000')
            self.assertEqual(summary['semantic_scoring'], 'pending_independent_review')
        self.assertTrue(r_result['promotion_gate']['eligible'])
        self.assertEqual(len(u_result['pairs']), 9)
        self.assertEqual({pair['outcome'] for pair in u_result['pairs']}, {'pending'})
        for cell in plan['cells']:
            durable = json.loads((out / 'cells' / (cell['cell_id'] + '.accounted.json')).read_bytes())
            self.assertEqual(durable['requests_attempted'], 3)
            self.assertEqual(durable['tool_reads'], 2)
            self.assertEqual(durable['review_status'], 'pending')
            self.assertFalse(durable['semantic_proof'])
            self.assertEqual(len(durable['requests']), 3)
            for request in durable['requests']:
                self.assertEqual(request['request']['max_tokens'], 8192)
            third = durable['requests'][2]['request']
            self.assertEqual({tool['function']['name'] for tool in third['tools']}, {'finish', 'ask_user'})
            self.assertFalse((out / 'cells' / cell['cell_id'] / 'decision-04.request.json').exists())

    def test_fake_regression_receipt_cannot_unlock_real_unseen_ingress(self):
        pilot = self.pilot()
        _, out, _, _ = prepare_unseen(pilot)
        with self.assertRaisesRegex(StudyError, 'pilot_regression_live_required'):
            pilot.execute_approved_pilot(out, approved(pilot, out), enabled=True,
                key_reader=lambda: self.fail('fake predecessor reached a real Key'))
        self.assertFalse((out / 'execution.claim.json').exists())

    def test_literal_scripts_cannot_inject_a_transport_callback(self):
        pilot = self.pilot()
        _, out, plan = prepare_regression(pilot)
        scripts = scripted(plan)
        scripts[plan['cells'][0]['cell_id']] = (lambda: None,)
        with self.assertRaises(StudyError):
            pilot.run_pilot_dryrun(out, scripted_by_cell=scripts)
        self.assertFalse((out / 'execution.claim.json').exists())
