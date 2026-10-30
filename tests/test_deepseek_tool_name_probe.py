"""Exact two-flow activation and accounting; only literal synthetic responses."""
import copy
import importlib
import importlib.util
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from test_deepseek_policy_runner import FakeTransport, call, reply
from macr.evals.deepseek_policy_runner import PolicyRunError
from macr.evals.deepseek_study_live import _BoundWire, _ScriptedWire, _StudyGate
from macr.evals.deepseek_study_profile import StudyError
from macr.providers.base import (
    ConversationRequest, validate_billing_evidence, validate_identity_evidence)
from macr.providers.deepseek import DeepSeekProvider

PROJECT = Path(__file__).resolve().parents[1]
INPUTS = PROJECT / 'examples/deepseek-tool-name-dev'
DIAGNOSIS = {'verdict': 'no_defect_supported',
    'cause': 'run delegates to double, which multiplies its integer input by two.',
    'when': 'For every integer input under the supplied arithmetic assumptions.',
    'refs': [{'file': 'entry.py', 'start': 1, 'end': 3},
             {'file': 'helper.py', 'start': 1, 'end': 2}], 'unknowns': []}


def terminal(name):
    args = ({'diagnosis': copy.deepcopy(DIAGNOSIS)} if name == 'finish' else
        {'gap': 'Provide a current invocation record showing whether run(7) was executed today.'})
    return call('terminal', name, args) | {'index': 0}


def three_turns(last):
    return (reply(None, calls=[call('read-1', 'search', {'query': 'double'}) | {'index': 0}]),
        reply(None, calls=[call('read-2', 'read_symbol',
            {'file': 'helper.py', 'symbol': 'double'}) | {'index': 0}]),
        reply(None, calls=[last]))


class ToolNameProbeTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('macr.evals.deepseek_tool_name_probe'),
            'exact two-flow probe owner is not implemented')
        return importlib.import_module('macr.evals.deepseek_tool_name_probe')

    def prepare(self):
        api = self.api()
        root = fixture_root()
        inputs = root / 'inputs'
        shutil.copytree(INPUTS, inputs)
        out = root / 'control'
        plan = api.prepare_probe(inputs, out=out, project_root=PROJECT)
        return api, inputs, out, plan

    def approval(self, api, out):
        return api.propose_probe_approval(out) | {
            'authorized': True, 'confirmation_ref': 'synthetic-scope-confirmation'}

    def scripts(self):
        return {'dev-finish': three_turns(terminal('finish')),
                'dev-ask': three_turns(terminal('ask_user'))}

    def execute_fake(self, api, out, scripts=None):
        with patch('macr.providers.deepseek._ApprovedTransport.post',
                side_effect=AssertionError('literal probe attempted network')):
            return api.execute_probe(out, self.approval(api, out), enabled=True,
                key_reader=lambda: self.fail('literal probe read a credential'),
                scripted_by_cell=self.scripts() if scripts is None else scripts)

    def test_proposal_binds_only_two_exact_development_flows_and_new_funds(self):
        api, _, out, plan = self.prepare()
        proposal = api.propose_probe_approval(out)
        self.assertEqual([row['case_id'] for row in plan['cells']], ['dev-finish', 'dev-ask'])
        self.assertEqual(plan['budget'], {'max_requests': 6, 'max_usd': '0.500000'})
        self.assertEqual(proposal['plan_sha256'], plan['plan_sha256'])
        self.assertEqual((proposal['max_requests'], proposal['max_usd']), (6, '0.500000'))
        self.assertFalse(proposal['authorized'])
        self.assertEqual(proposal['confirmation_ref'], '')
        self.assertEqual(proposal['credential_source'], {'kind': 'terminal'})
        self.assertFalse((out / 'execution.claim.json').exists())
        self.assertEqual(api.load_probe(out), plan)
        self.assertNotIn('custodian', plan)
        self.assertNotIn('predecessor_control_root', plan)

    def test_disabled_unapproved_or_scope_changed_never_claim_or_read_key(self):
        api, _, out, _ = self.prepare()
        approved = self.approval(api, out)
        reader = lambda: self.fail('refused scope reached credentials')
        with self.assertRaises(ValueError):
            api.execute_probe(out, approved, key_reader=reader)
        proposals = [api.propose_probe_approval(out), approved | {'max_requests': 7},
            approved | {'max_usd': '0.600000'}, approved | {'plan_sha256': '0' * 64},
            approved | {'confirmation_ref': ' '},
            approved | {'endpoint': 'https://unapproved.invalid/chat/completions'},
            approved | {'old_unknown_held_usd': '0.660000'}]
        for approval in proposals:
            with self.subTest(fields=sorted(approval)):
                with self.assertRaises(ValueError):
                    api.execute_probe(out, approval, enabled=True, key_reader=reader,
                                      scripted_by_cell=self.scripts())
        self.assertFalse((out / 'execution.claim.json').exists())
        self.assertFalse((out / 'ledger').exists())

    def test_valid_literal_flows_accumulate_six_request_ledger_and_claim_before_dispatch(self):
        api, _, out, _ = self.prepare()
        original = _ScriptedWire.post
        dispatch_claims = []

        def observe(wire, *args):
            dispatch_claims.append((out / 'execution.claim.json').is_file())
            return original(wire, *args)

        with patch.object(_ScriptedWire, 'post', observe):
            summary = self.execute_fake(api, out)
        self.assertEqual(dispatch_claims, [True] * 6)
        self.assertEqual(summary['planned_cells'], 2)
        self.assertEqual(summary['provenance'], 'approved_live_fake')
        self.assertEqual([row['status'] for row in summary['rows']],
                         ['semantic_pending', 'ask_user_terminated'])
        self.assertFalse(summary['batch_halted'])
        self.assertEqual(summary['accounting']['request_count'], 6)
        self.assertEqual(summary['accounting']['known_accrued_usd'], '0.000084')
        self.assertEqual(summary['accounting']['unknown_held_usd'], '0.000000')
        self.assertEqual(summary['model_calls_live'], 0)
        for row, label in zip(summary['rows'], ('valid_finish', 'valid_ask_user')):
            self.assertEqual(row['observation']['classification'], label)
            self.assertTrue(row['observation']['third_decision_reached'])
            self.assertTrue(row['observation']['flow_followed'])
        reservations = [json.loads(path.read_text())
                        for path in sorted((out / 'ledger').glob('*.reservation.json'))]
        self.assertEqual([r['number'] for r in reservations], [1, 2, 3, 4, 5, 6])
        self.assertTrue(all(r['reserved_micro_usd'] == 330000 for r in reservations))
        self.assertEqual(len(list((out / 'ledger').glob('*.outcome.json'))), 6)

    def test_first_tool_name_failure_retains_fee_identity_and_stops_second_case(self):
        cases = [('functions.PRIVATE_SENTINEL', 'tool_name_format'),
                 ('UNDECLARED_PRIVATE_SENTINEL', 'undeclared_tool_name'),
                 ('read_symbol', 'terminal_round_read_attempt')]
        for name, classification in cases:
            with self.subTest(classification=classification):
                api, _, out, _ = self.prepare()
                scripts = self.scripts()
                scripts['dev-finish'] = three_turns(call('bad', name, {}))
                summary = self.execute_fake(api, out, scripts)
                self.assertEqual(summary['accounting']['request_count'], 3)
                self.assertEqual(summary['accounting']['known_accrued_usd'], '0.000042')
                self.assertEqual(summary['accounting']['unknown_held_usd'], '0.000000')
                self.assertTrue(summary['batch_halted'])
                self.assertEqual([r['status'] for r in summary['rows']], ['failed', 'not_attempted'])
                self.assertEqual(summary['rows'][0]['error_code'], 'model_response_invalid')
                self.assertEqual(summary['rows'][0]['observation']['classification'], classification)
                result = json.loads((out / 'cells/dev-finish.accounted.json').read_text())
                last = result['requests'][-1]
                evidence = last['response']
                billing = validate_billing_evidence(evidence['billing_evidence'])
                identity = validate_identity_evidence(evidence['identity_evidence'])
                self.assertEqual(billing['request_sha256'], last['request_sha256'])
                self.assertEqual(identity['request_sha256'], last['request_sha256'])
                self.assertEqual(identity['fingerprint_state'], 'validated')
                self.assertNotIn('PRIVATE_SENTINEL', json.dumps(evidence['failure_diagnostic']))

    def test_unknown_request_retains_next_reservation_and_never_replays(self):
        api, _, out, _ = self.prepare()
        scripts = self.scripts() | {'dev-finish': ('timeout',)}
        summary = self.execute_fake(api, out, scripts)
        self.assertTrue(summary['batch_halted'])
        self.assertEqual(summary['accounting']['request_count'], 1)
        self.assertEqual(summary['accounting']['known_accrued_usd'], '0.000000')
        self.assertEqual(summary['accounting']['unknown_held_usd'], '0.330000')
        self.assertEqual(summary['rows'][1]['status'], 'not_attempted')
        before = [p.name for p in (out / 'ledger').iterdir()]
        with self.assertRaises(ValueError):
            self.execute_fake(api, out, scripts)
        self.assertEqual([p.name for p in (out / 'ledger').iterdir()], before)

    def test_fingerprint_drift_is_settled_but_stops_before_third_request(self):
        api, _, out, _ = self.prepare()
        scripts = self.scripts()
        replies = list(scripts['dev-finish'])
        replies[1]['system_fingerprint'] = 'different-synthetic-fingerprint'
        scripts['dev-finish'] = tuple(replies)
        summary = self.execute_fake(api, out, scripts)
        self.assertTrue(summary['batch_halted'])
        self.assertEqual(summary['accounting']['request_count'], 2)
        self.assertEqual(summary['accounting']['known_accrued_usd'], '0.000028')
        self.assertEqual(summary['accounting']['unknown_held_usd'], '0.000000')
        self.assertEqual(summary['rows'][0]['error_code'], 'system_fingerprint_drift')
        self.assertEqual(summary['rows'][1]['status'], 'not_attempted')

    def test_copied_scope_source_drift_and_reuse_cannot_reset_a_budget(self):
        api, _, out, plan = self.prepare()
        clone = out.parent / 'copied-control'
        shutil.copytree(out, clone)
        with self.assertRaises(ValueError):
            api.propose_probe_approval(clone)
        self.assertFalse((clone / 'execution.claim.json').exists())
        changed_code = dict(plan['implementation'])
        changed_code[next(iter(changed_code))] = '0' * 64
        with patch.object(api, '_code', return_value=changed_code):
            with self.assertRaises(ValueError):
                self.execute_fake(api, out)
        self.assertFalse((out / 'execution.claim.json').exists())
        self.execute_fake(api, out)
        with self.assertRaises(ValueError):
            self.execute_fake(api, out)
        api, _, out, plan = self.prepare()
        source = next(Path(plan['data_root']).rglob('helper.py'))
        source.write_text('def double(x):\n    return x * 3\n')
        with self.assertRaises(ValueError):
            self.execute_fake(api, out)
        self.assertFalse((out / 'execution.claim.json').exists())

    def test_changed_input_and_scope_inside_input_are_rejected_before_output(self):
        api = self.api()
        for relative in ('cases.json', 'sources/helper.py'):
            root = fixture_root()
            inputs = root / 'inputs'
            shutil.copytree(INPUTS, inputs)
            path = inputs / relative
            path.write_bytes(path.read_bytes() + b' ')
            out = root / 'control'
            with self.assertRaises(ValueError):
                api.prepare_probe(inputs, out=out, project_root=PROJECT)
            self.assertFalse(out.exists())
        with self.assertRaises(ValueError):
            api.prepare_probe(INPUTS, out=INPUTS / 'forbidden-control', project_root=PROJECT)
        self.assertFalse((INPUTS / 'forbidden-control').exists())

    def test_real_checkout_scope_is_refused_before_key_and_unit_claim_order_is_separate(self):
        api, _, out, plan = self.prepare()
        approval = self.approval(api, out)
        with self.assertRaises(ValueError):
            api.preflight_probe(out, approval)
        with self.assertRaises(ValueError):
            api.execute_probe(out, approval, enabled=True,
                key_reader=lambda: self.fail('checkout scope read a credential'))
        self.assertFalse((out / 'execution.claim.json').exists())
        # This only isolates ingress ordering; it is NOT a real-scope acceptance test.
        claims = []

        def abort_key():
            claims.append((out / 'execution.claim.json').is_file())
            raise RuntimeError('synthetic abort before authentication')

        with patch.object(api, '_preflight', return_value=plan), patch(
                'macr.providers.deepseek._ApprovedTransport.post',
                side_effect=AssertionError('ingress-order test attempted network')):
            try:
                api.execute_probe(out, approval, enabled=True, key_reader=abort_key)
            except StudyError:
                pass
        self.assertEqual(claims, [True])
        self.assertFalse(list((out / 'ledger').glob('*.reservation.json')))

    def test_project_credential_metadata_does_not_open_a_credential_or_allow_four_replies(self):
        api, _, out, _ = self.prepare()
        parent = out.parent / 'credential-project'
        parent.mkdir()
        absent = parent / '.env'
        proposal = api.propose_probe_approval(out,
            credential_source={'kind': 'project_env', 'path': str(absent)})
        self.assertEqual(proposal['credential_source'], {'kind': 'project_env', 'path': str(absent)})
        self.assertFalse(absent.exists())
        too_long = self.scripts()
        too_long['dev-finish'] += (reply(None, calls=[terminal('finish')]),)
        invalid = [too_long, self.scripts() | {'old-U': three_turns(terminal('finish'))},
            {'dev-finish': self.scripts()['dev-finish']},
            self.scripts() | {'dev-finish': lambda: self.fail('arbitrary callback was executed')}]
        for scripts in invalid:
            with self.subTest(keys=sorted(scripts)):
                with self.assertRaises(ValueError):
                    api.execute_probe(out, self.approval(api, out), enabled=True,
                        key_reader=lambda: self.fail('invalid script read a Key'), scripted_by_cell=scripts)
        self.assertFalse((out / 'execution.claim.json').exists())

    def test_early_terminal_is_not_reported_as_third_decision_validation(self):
        api, _, out, _ = self.prepare()
        scripts = self.scripts() | {'dev-finish': (reply(None, calls=[terminal('finish')]),)}
        summary = self.execute_fake(api, out, scripts)
        observation = summary['rows'][0]['observation']
        self.assertFalse(observation['third_decision_reached'])
        self.assertFalse(observation['flow_followed'])
        self.assertEqual(summary['accounting']['request_count'], 1)
        self.assertTrue(summary['batch_halted'])
        self.assertEqual(summary['rows'][1]['status'], 'not_attempted')

    def test_observation_preserves_record_limit_when_response_or_diagnostic_is_absent(self):
        api = self.api()
        for response in (None, {'failure_diagnostic': None}):
            with self.subTest(response_type=type(response).__name__):
                result = {'status': 'failed', 'error_code': 'run_record_limit',
                    'requests': [{'decision': 1, 'response': {}},
                                 {'decision': 2, 'response': {}},
                                 {'decision': 3, 'response': response}],
                    'tool_events': [
                        {'decision': 1, 'name': 'search', 'arguments': {'query': 'double'}},
                        {'decision': 2, 'name': 'read_symbol',
                         'arguments': {'file': 'helper.py', 'symbol': 'double'}}]}
                original = copy.deepcopy(result)
                observation = api._observation(result, 'finish')
                self.assertEqual(observation['classification'], 'run_record_limit')
                self.assertTrue(observation['third_decision_reached'])
                self.assertFalse(observation['flow_followed'])
                self.assertIsNone(observation['observed_terminal'])
                self.assertEqual(result, original)

    def test_half_dollar_gate_refuses_one_microdollar_beyond_next_reservation(self):
        root = fixture_root()
        (root / 'ledger').mkdir()
        gate = _StudyGate(root, {'execution_profile': 'study-evidence-v1',
            'budget': {'max_requests': 6, 'max_usd': '0.500000'}}, 'scripted_offline_fixture')
        gate.cell_id = 'dev-finish'
        gate.wire = _BoundWire(_ScriptedWire((reply('fixture'),)), simulated=True)
        gate.admit('a' * 64)
        envelope = reply('fixture')
        envelope['usage'] = {'prompt_tokens': 566663, 'completion_tokens': 1,
            'prompt_cache_hit_tokens': 0, 'prompt_cache_miss_tokens': 566663,
            'completion_tokens_details': {'reasoning_tokens': 0}}
        response = DeepSeekProvider(model='deepseek-flash', transport=FakeTransport(envelope)).converse(
            ConversationRequest(messages=[{'role': 'user', 'content': 'fixture'}], max_output_tokens=8192))
        gate.settle('a' * 64, response=response)
        self.assertEqual(gate.state()['known_accrued_usd'], '0.170001')
        with self.assertRaisesRegex(PolicyRunError, '^study_usd_budget_exhausted$'):
            gate.admit('b' * 64)
        self.assertEqual(gate.state()['request_count'], 1)
        self.assertEqual(len(list((root / 'ledger').glob('*.reservation.json'))), 1)


if __name__ == '__main__':
    unittest.main()
