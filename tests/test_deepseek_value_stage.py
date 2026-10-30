"""One finite stage, with literal transports and no credential or model access."""
import copy
import importlib
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import Mock, patch

from debugger_fixtures import fixture_root
from test_deepseek_policy_runner import UNKNOWN, reply, call
from test_deepseek_study_profile import COMMITMENT, development_pack
from test_deepseek_terminal_contract import three_turns, terminal

try:
    stage = importlib.import_module('macr.evals.deepseek_value_stage')
except ModuleNotFoundError:
    stage = None

PROJECT = Path(__file__).resolve().parents[1]


class ValueStageTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(stage, 'the bounded value-stage owner is not implemented')

    def prepare(self, mutate=None):
        root = fixture_root()
        pack = development_pack(root)
        for task in pack['tasks']:
            task['initial_evidence_files'] = list(task['authorized_files'])
        if mutate:
            mutate(pack)
        (root / 'pack.json').write_text(json.dumps(pack))
        out = root.parent / (root.name + '-value')
        plan = stage.prepare_value_stage(root, custodian=COMMITMENT,
            out=out, project_root=PROJECT)
        return root, out, plan

    def approved(self, out):
        return stage.propose_value_approval(out) | {
            'authorized': True, 'confirmation_ref': 'synthetic-stage-consent'}

    def scripts(self, plan):
        return {'S0': {'dev-finish': three_turns([terminal('finish')]),
                       'dev-ask': three_turns([terminal('ask_user')])},
            'S1': {cell['cell_id']: (
                tuple(reply(json.dumps(UNKNOWN)) for _ in range(3))
                if cell['strategy'] == 'direct_initial_3' else
                (reply(None, calls=[call('finish', 'finish', {'diagnosis': UNKNOWN})]),))
                for cell in plan['S1']['cells']}}

    def test_prepare_binds_exact_stage_and_only_eighteen_actual_cells(self):
        _, out, plan = self.prepare()
        self.assertEqual(stage.load_value_stage(out), plan)
        self.assertFalse(plan['remote_enabled'])
        self.assertEqual(plan['budgets'], {
            'S0': {'max_requests': 6, 'max_usd': '0.500000'},
            'S1': {'max_requests': 54, 'max_usd': '1.500000'}})
        self.assertEqual((plan['max_requests'], plan['max_usd']), (60, '2.000000'))
        self.assertEqual(len(plan['S1']['cells']), 18)
        self.assertEqual({cell['strategy'] for cell in plan['S1']['cells']},
                         {'direct_initial_3', 'adaptive_read_3'})
        self.assertFalse((out / 'execution.claim.json').exists())

    def test_partial_initial_source_and_wrong_grid_are_rejected(self):
        for mutation in (lambda p: p['tasks'][0].update(initial_evidence_files=['entry.py']),
                         lambda p: p['tasks'].pop(),
                         lambda p: p['tasks'][0].update(project_id='another-project')):
            with self.subTest(mutation=mutation.__code__.co_firstlineno):
                with self.assertRaises(ValueError):
                    self.prepare(mutation)

    def test_default_off_and_exact_approval_before_claim_key_or_dispatch(self):
        _, out, _ = self.prepare()
        key = Mock(side_effect=AssertionError('unexpected credential input'))
        approval = self.approved(out)
        with self.assertRaisesRegex(ValueError, 'value_live_disabled'):
            stage.execute_value_stage(out, approval, key_reader=key)
        for changed in (approval | {'authorized': False}, approval | {'max_requests': 61},
                        approval | {'endpoint': 'https://example.invalid/chat/completions'}):
            with self.subTest(scope=changed.get('endpoint')):
                with self.assertRaises(ValueError):
                    stage.execute_value_stage(out, changed, enabled=True, key_reader=key)
                self.assertFalse((out / 'execution.claim.json').exists())
        key.assert_not_called()

    def test_fake_whole_stage_uses_shared_limits_without_key_or_network(self):
        _, out, plan = self.prepare()
        key = Mock(side_effect=AssertionError('fake path read a credential'))
        with patch('urllib.request.build_opener', side_effect=AssertionError('network')):
            result = stage.execute_value_stage(out, self.approved(out), enabled=True,
                key_reader=key, scripted_by_cell=self.scripts(plan))
        self.assertEqual(result['provenance'], 'approved_live_fake')
        self.assertTrue(result['S0_gate']['eligible'])
        self.assertFalse(result['stage_halted'])
        self.assertEqual(result['S1']['planned_cells'], 18)
        self.assertEqual(len(result['S1']['rows']), 18)
        self.assertEqual(result['S0']['accounting']['request_count'], 6)
        self.assertEqual(result['S1']['accounting']['request_count'], 36)
        self.assertEqual(result['model_calls_live'], 0)
        self.assertTrue((out / 'S1' / 'blind-review-packets.json').exists())
        key.assert_not_called()

    def test_s0_protocol_failure_never_claims_reserves_or_executes_s1(self):
        _, out, plan = self.prepare()
        scripts = self.scripts(plan)
        scripts['S0']['dev-ask'] = three_turns([
            call('end', 'ask_user', {'gap': 'x' * 201})])
        scripts['S1'] = {}
        result = stage.run_value_dryrun(out, scripted_by_cell=scripts)
        self.assertFalse(result['S0_gate']['eligible'])
        self.assertTrue(result['stage_halted'])
        self.assertIsNone(result['S1'])
        self.assertEqual(result['S1_not_attempted_cells'], 18)
        self.assertFalse((out / 'S1' / 'execution.claim.json').exists())
        self.assertFalse((out / 'S1' / 'ledger').exists())

    def test_settled_s1_content_failure_stays_in_denominator_and_continues(self):
        _, out, plan = self.prepare()
        scripts = self.scripts(plan)
        first = plan['S1']['cells'][0]
        scripts['S1'][first['cell_id']] = (reply('{}'),)
        result = stage.run_value_dryrun(out, scripted_by_cell=scripts)
        self.assertFalse(result['stage_halted'])
        self.assertEqual(len(result['S1']['rows']), 18)
        self.assertEqual(sum(row['status'] == 'failed' for row in result['S1']['rows']), 1)
        self.assertEqual(result['S1']['accounting']['unknown_held_usd'], '0.000000')

    def test_s1_unknown_preserves_reservation_and_stops_remaining_cells(self):
        _, out, plan = self.prepare()
        scripts = self.scripts(plan)
        scripts['S1'][plan['S1']['cells'][0]['cell_id']] = ('timeout',)
        result = stage.run_value_dryrun(out, scripted_by_cell=scripts)
        self.assertTrue(result['stage_halted'])
        self.assertEqual(result['S1']['accounting']['unknown_held_usd'], '0.330000')
        self.assertEqual(result['S1']['accounting']['request_count'], 1)
        self.assertEqual(sum(row['status'] == 'not_attempted' for row in
                             result['S1']['rows']), 17)

    def test_s1_identity_is_pinned_to_the_verified_s0_fingerprint(self):
        _, out, plan = self.prepare()
        scripts = self.scripts(plan)
        first = plan['S1']['cells'][0]
        bad = copy.deepcopy(scripts['S1'][first['cell_id']][0])
        bad['system_fingerprint'] = 'different-valid-fingerprint'
        scripts['S1'][first['cell_id']] = (bad,)
        result = stage.run_value_dryrun(out, scripted_by_cell=scripts)
        self.assertTrue(result['stage_halted'])
        self.assertEqual(result['S1']['rows'][0]['error_code'], 'system_fingerprint_drift')
        self.assertEqual(result['S1']['accounting']['unknown_held_usd'], '0.000000')
        self.assertEqual(sum(row['status'] == 'not_attempted' for row in
                             result['S1']['rows']), 17)

    def test_failed_response_with_trusted_8001_input_stops_after_settlement(self):
        _, out, plan = self.prepare()
        scripts = self.scripts(plan)
        first = plan['S1']['cells'][0]
        bad = reply('{}')
        bad['choices'][0]['message']['role'] = 'invalid'
        bad['usage'].update(prompt_tokens=8001, prompt_cache_hit_tokens=0,
                            prompt_cache_miss_tokens=8001)
        scripts['S1'][first['cell_id']] = (bad,)
        result = stage.run_value_dryrun(out, scripted_by_cell=scripts)
        self.assertEqual(result['S1']['rows'][0]['error_code'], 'measured_budget_overrun')
        self.assertTrue(result['stage_halted'])
        self.assertEqual(result['S1']['accounting']['unknown_held_usd'], '0.000000')
        self.assertGreater(float(result['S1']['accounting']['known_accrued_usd']), 0)
        accounted = json.loads((out / 'S1' / 'cells' /
            (first['cell_id'] + '.accounted.json')).read_text())
        evidence = accounted['requests'][0]['response']
        self.assertEqual(evidence['identity_evidence']['model_state'], 'validated')
        self.assertEqual(evidence['billing_evidence']['raw_usage']['prompt_tokens'], 8001)
        self.assertEqual(sum(row['status'] == 'not_attempted' for row in
                             result['S1']['rows']), 17)

    def test_claim_replay_and_copied_path_are_refused_without_credentials(self):
        _, out, plan = self.prepare()
        approval = self.approved(out)
        stage.run_value_dryrun(out, scripted_by_cell=self.scripts(plan))
        key = Mock(side_effect=AssertionError('replay read a credential'))
        with self.assertRaises(ValueError):
            stage.execute_value_stage(out, approval, enabled=True, key_reader=key)
        clone = out.parent / (out.name + '-copy')
        shutil.copytree(out, clone)
        with self.assertRaises(ValueError):
            stage.load_value_stage(clone)
        key.assert_not_called()


if __name__ == '__main__':
    unittest.main()
