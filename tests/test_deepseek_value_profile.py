"""Shared full-source wire inputs, never blind tasks or real provider traffic."""
import json
from pathlib import Path
import unittest

from debugger_fixtures import fixture_root
from test_deepseek_study_profile import development_pack, COMMITMENT
from test_deepseek_policy_runner import FakeTransport, UNKNOWN, call, reply
from macr.evals.deepseek_study_profile import prepare_study, load_study
from macr.evals.deepseek_study_runner import run_study_cell
from macr.providers.deepseek import DeepSeekProvider

PROJECT = Path(__file__).resolve().parents[1]


class ValueProfileTests(unittest.TestCase):
    def test_value_pair_order_is_adjacent_and_counterbalanced_for_frozen_seeds(self):
        for seed in (0, 1, 20261030):
            pack_root = fixture_root()
            pack = development_pack(pack_root, 9)
            for task in pack['tasks']:
                task['initial_evidence_files'] = list(task['authorized_files'])
            (pack_root / 'pack.json').write_text(json.dumps(pack))
            study = prepare_study(pack_root, custodian=COMMITMENT,
                out=pack_root.parent / (pack_root.name + '-value'), project_root=PROJECT,
                execution_profile='study-value-v1', seed=seed)
            selected = [cell for cell in study['cells'] if cell['strategy'] in
                {'direct_initial_3', 'adaptive_read_3'}]
            self.assertEqual(len(selected), 18)
            firsts = []
            for offset in range(0, 18, 2):
                left, right = selected[offset:offset+2]
                self.assertEqual(left['task_id'], right['task_id'])
                self.assertNotEqual(left['strategy'], right['strategy'])
                firsts.append(left['strategy'])
            self.assertEqual(sorted(firsts.count(strategy) for strategy in
                ('direct_initial_3', 'adaptive_read_3')), [4, 5])

    def prepare(self, *, full=True):
        pack_root = fixture_root()
        pack = development_pack(pack_root, 1)
        if full:
            pack['tasks'][0]['initial_evidence_files'] = ['entry.py', 'helper.py']
        (pack_root / 'pack.json').write_text(json.dumps(pack))
        out = pack_root.parent / (pack_root.name + '-value')
        try:
            study = prepare_study(pack_root, custodian=COMMITMENT, out=out,
                project_root=PROJECT, execution_profile='study-value-v1')
        except ValueError as error:
            self.fail('explicit same-input value profile not implemented: ' + str(error))
        return pack_root, out, study

    def test_actual_raw_and_tool_wires_share_all_source_without_host_ast_facts(self):
        pack, out, study = self.prepare()
        requests = {}
        for strategy in ('direct_initial_3', 'adaptive_read_3'):
            answers = ([reply(json.dumps(UNKNOWN))] * 3 if strategy == 'direct_initial_3'
                else [reply(None, calls=[call('end', 'finish', {'diagnosis': UNKNOWN})])])
            transport = FakeTransport(*answers)
            result = run_study_cell(out / 'manifests/dev0', dataset_root=pack,
                strategy=strategy, provider=DeepSeekProvider(model='deepseek-flash', transport=transport),
                out=out / strategy, fixed_plan={})
            self.assertEqual(result['status'], 'semantic_pending')
            self.assertEqual(result['requests_attempted'], 3 if strategy == 'direct_initial_3' else 1)
            requests[strategy] = transport.posts
            for wire in transport.posts:
                self.assertEqual(wire['max_tokens'], 8192)
                self.assertEqual(wire['thinking'], {'type': 'enabled'})
                self.assertEqual(wire['reasoning_effort'], 'high')
        raw = requests['direct_initial_3'][0]
        tool = requests['adaptive_read_3'][0]
        self.assertEqual(raw['messages'][1], tool['messages'][1])
        e0 = json.loads(raw['messages'][1]['content'])
        self.assertNotIn('static_evidence', e0)
        self.assertEqual([row['file'] for row in e0['source']], ['entry.py', 'helper.py'])
        self.assertFalse(raw.get('tools'))
        self.assertEqual(raw['response_format'], {'type': 'json_object'})
        self.assertEqual({item['function']['name'] for item in tool['tools']},
            {'search', 'read_symbol', 'read_caller', 'finish', 'ask_user'})
        self.assertIn('200 Unicode code points', tool['messages'][0]['content'])
        self.assertEqual(load_study(out)['profile_sha256'], study['profile_sha256'])

    def test_value_profile_third_decision_keeps_only_existing_terminal_tools(self):
        pack, out, _ = self.prepare()
        transport = FakeTransport(
            reply(None, calls=[call('read1', 'search', {'query': 'double'})]),
            reply(None, calls=[call('read2', 'read_symbol', {'file': 'helper.py', 'symbol': 'double'})]),
            reply(None, calls=[call('end', 'ask_user', {'gap': 'Provide the current threshold.'})]))
        result = run_study_cell(out / 'manifests/dev0', dataset_root=pack,
            strategy='adaptive_read_3', provider=DeepSeekProvider(model='deepseek-flash', transport=transport),
            out=out / 'tools', fixed_plan={})
        self.assertEqual(result['status'], 'ask_user_terminated')
        self.assertEqual(len(transport.posts), 3)
        self.assertEqual({item['function']['name'] for item in transport.posts[2]['tools']},
            {'finish', 'ask_user'})
        self.assertEqual(transport.posts[2]['tool_choice'], 'auto')

    def test_value_profile_rejects_unequal_initial_source_before_creating_control(self):
        pack_root = fixture_root()
        development_pack(pack_root, 1)
        out = pack_root.parent / (pack_root.name + '-value')
        with self.assertRaisesRegex(ValueError, 'value_full_initial_source_required'):
            prepare_study(pack_root, custodian=COMMITMENT, out=out,
                project_root=PROJECT, execution_profile='study-value-v1')
        self.assertFalse(out.exists())
