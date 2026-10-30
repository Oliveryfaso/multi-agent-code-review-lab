"""Versioned concise-gap candidate: synthetic wires only, never live admission."""
import copy
from hashlib import sha256
import importlib
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from test_deepseek_policy_runner import call
from test_deepseek_tool_name_probe import three_turns, terminal
from macr.evals.deepseek_policy_runner import ASK_USER_GAP_SCHEMA
from macr.evals.deepseek_study_live import _ScriptedWire
from macr.evals.deepseek_study_profile import digest

PROJECT = Path(__file__).resolve().parents[1]
INPUTS = PROJECT / 'examples/deepseek-tool-name-dev'
VARIANT = 'concise-gap-v1'
BASELINE_SYSTEM_SHA256 = 'afaef3a3aa3bf7652bdbba67ff8cd2c2f7d6d405698826960023d96163b62f56'


class GapPromptTests(unittest.TestCase):
    def prepare(self, variant=None):
        api = importlib.import_module('macr.evals.deepseek_tool_name_probe')
        root = fixture_root()
        inputs = root / 'inputs'
        shutil.copytree(INPUTS, inputs)
        out = root / 'control'
        kwargs = {} if variant is None else {'prompt_variant': variant}
        try:
            plan = api.prepare_probe(inputs, out=out, project_root=PROJECT, **kwargs)
        except TypeError as error:
            if 'prompt_variant' in str(error):
                self.fail('explicit offline prompt variant is not implemented')
            raise
        return api, inputs, out, plan

    def scripts(self, gap=None):
        ask = terminal('ask_user') if gap is None else call('terminal', 'ask_user', {'gap': gap})
        return {'dev-finish': three_turns(terminal('finish')),
                'dev-ask': three_turns(ask)}

    def run_offline(self, api, out, *, gap=None):
        observed = []
        original = _ScriptedWire.post

        def capture(wire, endpoint, body, timeout_seconds, max_response_bytes):
            observed.append(json.loads(body))
            return original(wire, endpoint, body, timeout_seconds, max_response_bytes)

        with patch.object(_ScriptedWire, 'post', capture), patch(
                'macr.providers.deepseek._ApprovedTransport.post',
                side_effect=AssertionError('gap fixture reached real transport')):
            result = api.run_probe_dryrun(out, scripted_by_cell=self.scripts(gap))
        return result, observed

    def test_baseline_actual_three_turn_system_remains_byte_identical(self):
        api, _, out, plan = self.prepare()
        result, requests = self.run_offline(api, out)
        self.assertNotIn('prompt_variant', plan)
        self.assertEqual(result['accounting']['request_count'], 6)
        self.assertFalse(result['batch_halted'])
        for request in requests:
            system = request['messages'][0]['content']
            self.assertEqual(sha256(system.encode()).hexdigest(), BASELINE_SYSTEM_SHA256)
        for case_id in ('dev-finish', 'dev-ask'):
            manifest = json.loads((out / 'manifests' / case_id / 'manifest.json').read_text())
            self.assertEqual(manifest['prompt_version'], 'deepseek-study-evidence-v1')

    def test_candidate_has_new_manifest_identity_and_only_adds_bounded_gap_suffix_to_wire(self):
        api, _, baseline_out, baseline = self.prepare()
        _, _, candidate_out, candidate = self.prepare(VARIANT)
        _, baseline_requests = self.run_offline(api, baseline_out)
        result, candidate_requests = self.run_offline(api, candidate_out)
        self.assertEqual(candidate['prompt_variant'], VARIANT)
        self.assertEqual(set(candidate), set(baseline) | {'prompt_variant'})
        self.assertNotEqual(candidate['plan_sha256'], baseline['plan_sha256'])
        self.assertEqual(candidate['cases_sha256'], baseline['cases_sha256'])
        self.assertEqual(candidate['budget'], {'max_requests': 6, 'max_usd': '0.500000'})
        self.assertEqual(candidate['source_bytes'], 91)
        self.assertEqual(candidate['cases_bytes'], 1170)
        self.assertEqual(result['accounting']['request_count'], 6)
        self.assertFalse(result['batch_halted'])
        for case_id in ('dev-finish', 'dev-ask'):
            manifest = json.loads((candidate_out / 'manifests' / case_id / 'manifest.json').read_text())
            self.assertEqual(manifest['prompt_version'], 'deepseek-study-gap-budget-v1')
        self.assertEqual(len(baseline_requests), len(candidate_requests))
        for original, candidate_request in zip(baseline_requests, candidate_requests):
            base_system = original['messages'][0]['content']
            candidate_system = candidate_request['messages'][0]['content']
            self.assertTrue(candidate_system.startswith(base_system))
            suffix = candidate_system[len(base_system):].strip()
            self.assertTrue(suffix)
            self.assertIn(str(ASK_USER_GAP_SCHEMA['maxLength']), suffix)
            self.assertIn('ask_user', suffix)
            self.assertIn('gap', suffix)
            self.assertTrue('concise' in suffix.casefold() or 'short' in suffix.casefold())
            self.assertTrue(any(word in suffix.casefold()
                                for word in ('character', 'codepoint', 'code point')))
            normalized = copy.deepcopy(candidate_request)
            normalized['messages'][0]['content'] = base_system
            self.assertEqual(normalized, original)
            ask = next(tool['function'] for tool in candidate_request['tools']
                       if tool['function']['name'] == 'ask_user')
            self.assertEqual(ask['parameters']['properties']['gap'], ASK_USER_GAP_SCHEMA)
            self.assertEqual(candidate_request['max_tokens'], 8192)

    def test_candidate_gap_cap_counts_characters_and_never_truncates_invalid_output(self):
        for gap, accepted in (('界' * 200, True), (' ' + 'x' * 200 + ' ', True),
                              ('x' * 201, False), ('界' * 205, False)):
            with self.subTest(characters=len(gap), accepted=accepted):
                api, _, out, _ = self.prepare(VARIANT)
                result, _ = self.run_offline(api, out, gap=gap)
                row = result['rows'][1]
                self.assertEqual(result['accounting']['request_count'], 6)
                self.assertEqual(result['model_calls_live'], 0)
                record = json.loads((out / 'cells/dev-ask.accounted.json').read_text())
                arguments = record['requests'][-1]['response']['assistant_message']['tool_calls'][0]['function']['arguments']
                self.assertEqual(json.loads(arguments)['gap'], gap)
                if accepted:
                    self.assertEqual(row['status'], 'ask_user_terminated')
                    self.assertFalse(result['batch_halted'])
                    self.assertEqual(record['gap'], gap)
                else:
                    self.assertEqual(row['status'], 'failed')
                    self.assertEqual(row['error_code'], 'ask_user_contract_invalid')
                    self.assertTrue(result['batch_halted'])
                    self.assertIsNone(record['answer'])

    def test_candidate_cannot_propose_preflight_or_execute_even_with_forged_approval(self):
        api, _, baseline_out, _ = self.prepare()
        _, _, out, candidate = self.prepare(VARIANT)
        approval = api.propose_probe_approval(baseline_out) | {
            'authorized': True, 'confirmation_ref': 'synthetic-no-live-candidate',
            'plan_sha256': candidate['plan_sha256'], 'control_root': str(out), 'log_root': str(out)}
        reads = []

        def key_reader():
            reads.append(True)
            return 'DUMMY-NEVER-DISPATCH'

        actions = [lambda: api.propose_probe_approval(out),
            lambda: api.preflight_probe(out, approval),
            lambda: api.execute_probe(out, approval, enabled=True, key_reader=key_reader),
            lambda: api.execute_probe(out, approval, enabled=True, key_reader=key_reader,
                                      scripted_by_cell=self.scripts())]
        with patch.object(_ScriptedWire, 'post', side_effect=AssertionError('candidate executed')), \
                patch('macr.providers.deepseek._ApprovedTransport.post',
                      side_effect=AssertionError('candidate reached network')):
            for action in actions:
                with self.assertRaisesRegex(ValueError, '^probe_prompt_variant_live_disabled$'):
                    action()
        self.assertEqual(reads, [])
        self.assertFalse((out / 'execution.claim.json').exists())
        self.assertFalse((out / 'ledger').exists())

    def test_unknown_variant_is_rejected_before_creating_control_output(self):
        api = importlib.import_module('macr.evals.deepseek_tool_name_probe')
        for variant in ('', 'unknown', True, {'name': VARIANT}):
            with self.subTest(kind=type(variant).__name__):
                root = fixture_root()
                out = root / 'control'
                with self.assertRaises(ValueError):
                    api.prepare_probe(INPUTS, out=out, project_root=PROJECT, prompt_variant=variant)
                self.assertFalse(out.exists())

    def test_rehashed_prompt_variant_flag_cannot_reinterpret_existing_manifest(self):
        for existing_variant, replacement in ((None, VARIANT), (VARIANT, None)):
            with self.subTest(existing_variant=existing_variant):
                api, _, out, plan = self.prepare(existing_variant)
                changed = dict(plan)
                if replacement is None:
                    changed.pop('prompt_variant')
                else:
                    changed['prompt_variant'] = replacement
                changed['plan_sha256'] = digest({key: value for key, value in changed.items()
                                                 if key != 'plan_sha256'})
                (out / 'probe.json').write_text(json.dumps(changed))
                with self.assertRaises(ValueError):
                    api.load_probe(out)
                self.assertFalse((out / 'execution.claim.json').exists())


if __name__ == '__main__':
    unittest.main()
