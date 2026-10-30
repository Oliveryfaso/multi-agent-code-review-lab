"""Synthetic three-turn contracts and stop gates; no model-quality evidence."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from debugger_fixtures import fixture_root
from test_deepseek_comparison_pilot import (
    approved, prepare_regression, prepare_unseen, scripted)
from test_deepseek_policy_runner import FakeTransport, MODEL, UNKNOWN, call, reply
from test_deepseek_study_profile import COMMITMENT, development_pack
from macr.evals import deepseek_comparison_pilot as pilot
from macr.evals.deepseek_study_live import _ScriptedWire
from macr.evals.deepseek_study_profile import prepare_study
from macr.evals.deepseek_study_runner import run_study_cell
from macr.providers.base import validate_billing_evidence, validate_identity_evidence
from macr.providers.deepseek import DeepSeekProvider

PROJECT = Path(__file__).resolve().parents[1]


def indexed_call(identifier, name, arguments):
    return call(identifier, name, arguments) | {'index': 0}


def three_turns(last_calls):
    return (
        reply(None, calls=[indexed_call('read-1', 'search', {'query': 'double'})]),
        reply(None, calls=[indexed_call('read-2', 'read_symbol',
            {'file': 'helper.py', 'symbol': 'double'})]),
        reply(None, calls=last_calls))


def terminal(action='finish'):
    arguments = ({'diagnosis': UNKNOWN} if action == 'finish' else
        {'gap': 'Supply the current deployment switch before deciding that external outcome.'})
    return indexed_call('end', action, arguments)


class TerminalContractTests(unittest.TestCase):
    def prepare(self, profile):
        root = fixture_root()
        development_pack(root, 1)
        out = root.parent / (root.name + '-control')
        study = prepare_study(root, custodian=COMMITMENT, out=out,
            project_root=PROJECT, execution_profile=profile)
        return root, out, study

    def run_three(self, profile, last_calls):
        root, out, study = self.prepare(profile)
        # A fourth scripted response must remain unused, even on contract failure.
        transport = FakeTransport(*three_turns(last_calls),
            reply(None, calls=[terminal()]))
        settlements = []
        with patch('macr.providers.deepseek._ApprovedTransport.post',
                side_effect=AssertionError('synthetic test reached remote transport')):
            result = run_study_cell(out / 'manifests/dev0', dataset_root=root,
                strategy='adaptive_read_3', provider=DeepSeekProvider(
                    model=MODEL['model_id'], transport=transport),
                out=out / 'cell', fixed_plan=study['tasks'][0]['fixed_plan'],
                settle=lambda request_hash, **value:
                    settlements.append((request_hash, value)))
        self.assertEqual(len(transport.posts), 3)
        self.assertEqual(len(transport.answers), 1)
        self.assertEqual(len(settlements), 3)
        self.assertEqual(result['tool_reads'], 2)
        reads = [event for event in result['tool_events']
                 if event['name'] not in {'finish', 'ask_user'}]
        self.assertEqual(len(reads), 2)
        self.assertEqual({tool['function']['name'] for tool in transport.posts[2]['tools']},
            {'finish', 'ask_user'})
        self.assertEqual(transport.posts[2]['max_tokens'], 8192)
        self.assertEqual([event['decision'] for event in reads], [1, 2])
        return result, transport, settlements

    def test_indexed_finish_and_ask_user_after_two_reads_remain_review_pending(self):
        for profile in ('study-8192-v2', 'study-evidence-v1'):
            for action, status in (('finish', 'semantic_pending'),
                                   ('ask_user', 'ask_user_terminated')):
                with self.subTest(profile=profile, action=action):
                    result, _, settlements = self.run_three(profile, [terminal(action)])
                    self.assertEqual(result['status'], status)
                    self.assertEqual(result['review_status'], 'pending')
                    self.assertFalse(result['semantic_proof'])
                    normalized = result['requests'][-1]['response']['assistant_message']
                    self.assertNotIn('index', normalized['tool_calls'][0])
                    self.assertTrue(all(value['error'] is None for _, value in settlements))
                    if action == 'ask_user':
                        self.assertEqual(result['task_resolution'], 'unresolved')
                        self.assertEqual(result['abstention_review_status'], 'pending')

    def test_third_turn_names_fail_with_safe_reason_and_keep_billing_identity(self):
        cases = (
            ('read_symbol', 'not_declared'),
            ('UNDECLARED_PRIVATE_SENTINEL', 'not_declared'),
            ('functions.PRIVATE_SENTINEL', 'format'),
            (None, 'type'))
        for name, reason in cases:
            with self.subTest(reason=reason, name_type=type(name).__name__):
                result, _, settlements = self.run_three('study-evidence-v1',
                    [indexed_call('bad-end', name, {})])
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['error_code'], 'model_response_invalid')
                self.assertIsNone(result['answer'])
                last = result['requests'][-1]
                evidence = last['response']
                diagnostic = evidence['failure_diagnostic']
                self.assertEqual(diagnostic['stage'], 'assistant_message')
                self.assertEqual(diagnostic['validation_error'], 'tool_function_name')
                self.assertEqual(diagnostic['tool_name_reason'], reason)
                self.assertEqual(diagnostic['declared_tool_set'], 'terminal_only')
                self.assertEqual(diagnostic['policy_decision'], 3)
                self.assertEqual(diagnostic['first_tool_index_kind'], 'integer')
                self.assertEqual(diagnostic['finish_reason'], 'tool_calls')
                self.assertNotIn('PRIVATE_SENTINEL', json.dumps(diagnostic))
                billing = validate_billing_evidence(evidence['billing_evidence'])
                identity = validate_identity_evidence(evidence['identity_evidence'])
                self.assertEqual(billing['request_sha256'], last['request_sha256'])
                self.assertEqual(identity['request_sha256'], last['request_sha256'])
                self.assertEqual(identity['model_state'], 'validated')
                self.assertEqual(identity['fingerprint_state'], 'validated')
                self.assertEqual(result['reservations'][-1]['state'], 'failed_known_usage')
                self.assertIsNotNone(result['reservations'][-1]['usage'])
                self.assertIsNotNone(settlements[-1][1]['error'])

    def test_third_turn_multiple_or_mixed_terminals_remain_rejected(self):
        for calls in ([terminal(), terminal('ask_user') | {'id': 'ask-end'}],
                      [terminal(), indexed_call('other-end', 'finish', {'diagnosis': UNKNOWN})]):
            with self.subTest(names=[row['function']['name'] for row in calls]):
                result, _, settlements = self.run_three('study-8192-v2', calls)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['error_code'], 'third_decision_must_terminate')
                self.assertIsNone(result['answer'])
                self.assertIsNone(settlements[-1][1]['error'])
                self.assertTrue(all(row['usage'] is not None for row in result['reservations']))

    def test_cumulative_failure_success_failure_failure_stops_with_fourteen_unattempted(self):
        _, out, plan, regression = prepare_unseen(pilot)
        predecessor = (regression / 'summary.json').read_bytes()
        scripts = scripted(plan)
        invalid = three_turns([indexed_call('not-terminal', 'read_symbol',
            {'file': 'helper.py', 'symbol': 'double'})])
        for index, cell in enumerate(plan['cells'][:4]):
            scripts[cell['cell_id']] = (three_turns([terminal()]) if index == 1
                                      else copy.deepcopy(invalid))
        posts = []
        original_post = _ScriptedWire.post

        def observe(wire, endpoint, body, timeout_seconds, max_response_bytes):
            posts.append(json.loads(body))
            return original_post(wire, endpoint, body, timeout_seconds, max_response_bytes)

        key_reader = Mock(side_effect=AssertionError('scripted path read a credential'))
        with patch.object(_ScriptedWire, 'post', observe), patch(
                'macr.providers.deepseek._ApprovedTransport.post',
                side_effect=AssertionError('scripted pilot reached remote transport')):
            result = pilot.execute_approved_pilot(out, approved(pilot, out),
                enabled=True, key_reader=key_reader, scripted_by_cell=scripts)
        self.assertEqual([row['status'] for row in result['rows'][:4]],
            ['failed', 'semantic_pending', 'failed', 'failed'])
        self.assertEqual([row['status'] for row in result['rows'][4:]], ['not_attempted'] * 14)
        self.assertEqual(result['known_contract_failures'], 3)
        self.assertTrue(result['batch_halted'])
        self.assertEqual(result['processed_cells'], 4)
        self.assertEqual(result['planned_cells'], 18)
        self.assertEqual(result['accounting']['request_count'], 12)
        self.assertEqual(result['accounting']['unknown_held_usd'], '0.000000')
        self.assertEqual(result['model_calls_live'], 0)
        self.assertEqual(len(posts), 12)
        self.assertEqual(len(list((out / 'ledger').glob('*.reservation.json'))), 12)
        self.assertEqual(len(list((out / 'ledger').glob('*.outcome.json'))), 12)
        self.assertEqual(len(list((out / 'cells').glob('*.accounted.json'))), 4)
        key_reader.assert_not_called()
        self.assertEqual((regression / 'summary.json').read_bytes(), predecessor)

    def test_six_of_seven_reviewable_with_no_truncation_still_meets_machine_gate(self):
        _, out, plan = prepare_regression(pilot)
        scripts = scripted(plan)
        first = plan['cells'][0]['cell_id']
        scripts[first] = tuple(reply(json.dumps(UNKNOWN if number < 2 else
            UNKNOWN | {'unknowns': ['x' * 216]})) for number in range(3))
        result = pilot.run_pilot_dryrun(out, scripted_by_cell=scripts)
        self.assertFalse(result['batch_halted'])
        self.assertEqual(result['known_contract_failures'], 1)
        self.assertEqual(result['rows'][0]['error_code'], 'eval_contract_invalid')
        self.assertEqual(result['accounting']['request_count'], 21)
        self.assertEqual(result['promotion_gate']['terminal_cells'], 7)
        self.assertEqual(result['promotion_gate']['reviewable_cells'], 6)
        self.assertEqual(result['promotion_gate']['truncated_cells'], 0)
        self.assertTrue(result['promotion_gate']['eligible'])
        self.assertEqual(result['semantic_scoring'], 'pending_independent_review')

    def test_unknown_or_identity_drift_stops_immediately_before_three_known_failures(self):
        for failure in ('unknown', 'fingerprint_drift'):
            with self.subTest(failure=failure):
                _, out, plan, _ = prepare_unseen(pilot)
                scripts = scripted(plan)
                if failure == 'unknown':
                    scripts[plan['cells'][0]['cell_id']] = ('timeout',)
                    count, hold, error = 1, '0.330000', 'study_halted_unknown'
                else:
                    replies = list(three_turns([terminal()]))
                    replies[1]['system_fingerprint'] = 'different-synthetic-fingerprint'
                    scripts[plan['cells'][0]['cell_id']] = tuple(replies)
                    count, hold, error = 2, '0.000000', 'system_fingerprint_drift'
                result = pilot.run_pilot_dryrun(out, scripted_by_cell=scripts)
                self.assertTrue(result['batch_halted'])
                self.assertEqual(result['known_contract_failures'], 0)
                self.assertEqual(result['rows'][0]['error_code'], error)
                self.assertEqual(result['accounting']['request_count'], count)
                self.assertEqual(result['accounting']['unknown_held_usd'], hold)
                self.assertEqual(result['processed_cells'], 1)
                self.assertEqual(sum(row['status'] == 'not_attempted' for row in result['rows']), 17)
                self.assertEqual(len(list((out / 'ledger').glob('*.reservation.json'))), count)


if __name__ == '__main__':
    unittest.main()
