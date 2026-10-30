"""Synthetic per-field limits and serialized contracts, never model-quality tests."""
import copy
import json
import unittest

from debugger_fixtures import fixture_root
from macr.evals.deepseek_policy_manifest import DEFAULT_BUDGET, prepare_policy_manifest
from macr.evals.deepseek_policy_runner import OUTPUT_SCHEMA, PolicyRunError, _diagnosis
from macr.evals.deepseek_study_generation import EVIDENCE_PROFILE, V2_PROFILE, get_generation
from macr.evals.deepseek_study_plan import build_fixed_plan
from macr.evals.deepseek_study_runner import STRATEGIES, run_study_cell
from macr.providers.deepseek import DeepSeekProvider
from test_deepseek_policy_runner import FakeTransport, MODEL, UNKNOWN, call, reply


class DeepSeekFieldBoundaryTests(unittest.TestCase):
    def diagnosis(self, *gaps):
        return UNKNOWN | {'unknowns': list(gaps)}

    def run_fixture(self, profile, strategy, final):
        root = fixture_root()
        source = root / 'snapshots' / 'fixture'
        source.mkdir(parents=True)
        (source / 'entry.py').write_text(
            'from helper import twice\ndef run(value):\n    return twice(value)\n')
        (source / 'helper.py').write_text('def twice(value):\n    return value * 2\n')
        case = {'id': 'field-boundary-fixture', 'snapshot_root': 'snapshots/fixture',
            'question': 'Describe the authorized static source.',
            'scenario': 'A self-authored output-contract fixture.',
            'assumptions': ['Only inspect static Python source.'],
            'authorized_files': ['entry.py', 'helper.py'],
            'initial_evidence_files': ['entry.py'],
            'stratum': 'retrieval_required', 'evidence_parity': 'unequal_by_design',
            'entrypoint': {'file': 'entry.py', 'symbol': 'run'}}
        generation = get_generation(profile)
        manifest_dir = root / 'manifest'
        manifest = prepare_policy_manifest(case, dataset_root=root, out=manifest_dir,
            model=MODEL, prompt_version=generation['prompt_version'],
            budget=DEFAULT_BUDGET | {
                'max_total_output_tokens': generation['max_total_output_tokens']})
        first = self.diagnosis('x' * 200)
        if strategy == 'adaptive_read_3':
            responses = [
                reply(None, calls=[call('read-1', 'search', {'query': 'twice'})]),
                reply(None, calls=[call('read-2', 'read_symbol',
                    {'file': 'helper.py', 'symbol': 'twice'})]),
                reply(None, calls=[call('finish-1', 'finish', {'diagnosis': final})])]
        else:
            responses = [reply(json.dumps(answer)) for answer in (first, first, final)]
        # An available fourth response must never become a repair request.
        responses.append(reply(json.dumps(first)))
        transport = FakeTransport(*responses)
        result = run_study_cell(manifest_dir, dataset_root=root, strategy=strategy,
            provider=DeepSeekProvider(model=MODEL['model_id'], transport=transport),
            out=root / 'cell', fixed_plan=build_fixed_plan(manifest, root))
        return result, transport

    def assert_wire_contracts(self, transport):
        self.assertEqual(len(transport.raw_bodies), 3)
        for raw in transport.raw_bodies:
            wire = json.loads(raw)
            self.assertEqual(wire['max_tokens'], 8192)
            system = wire['messages'][0]['content']
            marker = 'Diagnosis JSON Schema:\n'
            self.assertIn(marker, system)
            schema, _ = json.JSONDecoder().raw_decode(system.split(marker, 1)[1])
            self.assertEqual(schema, OUTPUT_SCHEMA)
            self.assertEqual(schema['properties']['unknowns']['items'],
                {'type': 'string', 'maxLength': 200})
            for tool in wire.get('tools', []):
                function = tool['function']
                if function['name'] == 'finish':
                    self.assertEqual(function['parameters']['properties']['diagnosis'], schema)

    def test_native_unknowns_item_accepts_199_and_200_but_rejects_201_and_216(self):
        for size in (199, 200, 201, 216):
            with self.subTest(size=size):
                answer = self.diagnosis('x' * size)
                original = copy.deepcopy(answer)
                if size <= 200:
                    self.assertEqual(_diagnosis(answer, []), original)
                else:
                    with self.assertRaises(PolicyRunError) as caught:
                        _diagnosis(answer, [])
                    self.assertEqual(caught.exception.code, 'eval_contract_invalid')
                self.assertEqual(answer, original)

    def test_native_unknowns_count_unicode_characters_not_utf8_bytes(self):
        for character in ('界', '\U0001f642'):
            for size in (200, 201, 216):
                with self.subTest(character=character, size=size):
                    text = character * size
                    self.assertEqual(len(text), size)
                    self.assertGreater(len(text.encode('utf-8')), 200)
                    answer = self.diagnosis(text)
                    original = copy.deepcopy(answer)
                    if size == 200:
                        self.assertEqual(_diagnosis(answer, [])['unknowns'], [text])
                    else:
                        with self.assertRaisesRegex(PolicyRunError,
                                '^eval_contract_invalid$'):
                            _diagnosis(answer, [])
                    self.assertEqual(answer, original)

    def test_each_unknown_has_its_own_cap_and_overlimit_items_are_not_split(self):
        gaps = [character * 200 for character in 'abcdef']
        self.assertGreater(sum(map(len, gaps)), 200)
        answer = self.diagnosis(*gaps)
        self.assertEqual(_diagnosis(answer, []), answer)
        for size in (201, 216):
            with self.subTest(size=size):
                bad = self.diagnosis('a' * 200, 'b' * size, 'c' * 200)
                original = copy.deepcopy(bad)
                with self.assertRaisesRegex(PolicyRunError, '^eval_contract_invalid$'):
                    _diagnosis(bad, [])
                self.assertEqual(bad, original)
                self.assertEqual(list(map(len, bad['unknowns'])), [200, size, 200])

    def test_both_named_profiles_serialize_cap_in_every_round_of_all_four_strategies(self):
        final = self.diagnosis('界' * 200)
        for profile in (V2_PROFILE, EVIDENCE_PROFILE):
            for strategy in STRATEGIES:
                with self.subTest(profile=profile, strategy=strategy):
                    result, transport = self.run_fixture(profile, strategy, final)
                    self.assert_wire_contracts(transport)
                    self.assertEqual(result['status'], 'semantic_pending')
                    self.assertEqual(result['answer'], final)
                    self.assertEqual(result['requests_attempted'], 3)
                    self.assertEqual(len(transport.answers), 1)
                    self.assertEqual(result['model_calls_live'], 0)

    def test_third_round_per_item_overrun_fails_without_fourth_call_or_rewriting(self):
        for profile in (V2_PROFILE, EVIDENCE_PROFILE):
            for strategy in STRATEGIES:
                for size in (201, 216):
                    with self.subTest(profile=profile, strategy=strategy, size=size):
                        final = self.diagnosis('a' * 200, 'b' * size, '界' * 200)
                        original = copy.deepcopy(final)
                        result, transport = self.run_fixture(profile, strategy, final)
                        self.assert_wire_contracts(transport)
                        self.assertEqual(result['status'], 'failed')
                        self.assertEqual(result['error_code'], 'eval_contract_invalid')
                        self.assertEqual(result['requests_attempted'], 3)
                        self.assertIsNone(result['answer'])
                        self.assertEqual(len(transport.answers), 1)
                        self.assertEqual(result['model_calls_live'], 0)
                        message = result['requests'][2]['response']['assistant_message']
                        if strategy == 'adaptive_read_3':
                            function = message['tool_calls'][0]['function']
                            recorded = json.loads(function['arguments'])['diagnosis']
                            self.assertEqual(result['tool_reads'], 2)
                        else:
                            recorded = json.loads(message['content'])
                        self.assertEqual(recorded, original)
                        self.assertEqual(final, original)


if __name__ == '__main__':
    unittest.main()
