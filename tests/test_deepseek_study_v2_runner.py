"""V2 scripted wire/budget contracts, not model accuracy or blind-task tests."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from macr.evals import deepseek_study_profile as profile
from macr.evals import deepseek_study_runner as runner
from macr.evals import deepseek_study_batch as batch
from macr.evals.deepseek_policy_runner import OUTPUT_SCHEMA, TOOL_SCHEMAS
from macr.evals.deepseek_policy_tools import StaticEvidenceTools
from macr.providers.deepseek import DeepSeekProvider
from test_deepseek_policy_runner import FakeTransport, MODEL, UNKNOWN, call, reply
from test_deepseek_study_profile import COMMITMENT, development_pack


V2 = 'study-8192-v2'
PROMPT = 'deepseek-study-8192-v2'
ALL_TOOLS = {'search', 'read_symbol', 'read_caller', 'finish', 'ask_user'}
TERMINAL = {'finish', 'ask_user'}


class StudyV2RunnerTests(unittest.TestCase):
    def setUp(self):
        self.root = fixture_root()
        development_pack(self.root, 1)
        self.control = self.root.parent / (self.root.name + '-v2-study')

    def prepare(self, *, legacy=False, seed=20261029):
        options = {} if legacy else {'execution_profile': V2}
        self.study = profile.prepare_study(self.root, custodian=COMMITMENT,
            out=self.control, project_root=Path(profile.__file__).parents[3], seed=seed, **options)
        self.task = self.study['tasks'][0]
        return self.study

    def run_cell(self, strategy, transport, *, name='cell'):
        return runner.run_study_cell(self.control / 'manifests' / self.task['id'],
            dataset_root=self.root, strategy=strategy,
            provider=DeepSeekProvider(model=MODEL['model_id'], transport=transport),
            out=self.control / name, fixed_plan=self.task['fixed_plan'])

    def three_tools(self, *, third=None):
        return FakeTransport(
            reply(None, calls=[call('read-1', 'search', {'query': 'double'})]),
            reply(None, calls=[call('read-2', 'read_symbol', {'file': 'helper.py', 'symbol': 'double'})]),
            third or reply(None, calls=[call('end', 'finish', {'diagnosis': UNKNOWN})]))

    def test_four_arms_serialize_shared_evidence_criteria_8192_and_original_schema(self):
        study = self.prepare()
        self.assertEqual(study['prompt_version'], PROMPT)
        manifest = self.task['manifest']
        self.assertEqual(manifest['prompt_version'], PROMPT)
        self.assertEqual(manifest['budget']['max_total_output_tokens'], 24576)
        self.assertEqual(manifest['budget']['max_total_input_tokens'], 24000)
        criteria = getattr(runner, 'EXTERNAL_EVIDENCE_CRITERIA', None)
        self.assertIsInstance(criteria, str)
        for phrase in ('supplied code establishes', 'current external situation', 'smallest evidence'):
            self.assertIn(phrase, criteria)
        initial = []
        for strategy in ('direct_initial_3', 'fixed_read_3', 'adaptive_read_3', 'direct_full_3'):
            responses = ([reply(None, calls=[call('end', 'finish', {'diagnosis': UNKNOWN})])]
                if strategy == 'adaptive_read_3' else [reply(json.dumps(UNKNOWN)) for _ in range(3)])
            transport = FakeTransport(*responses)
            result = self.run_cell(strategy, transport, name=strategy)
            self.assertEqual(result['status'], 'semantic_pending')
            self.assertFalse(result['semantic_proof'])
            self.assertEqual(result['model_calls_live'], 0)
            wires = [json.loads(body) for body in transport.raw_bodies]
            initial.append(wires[0]['messages'][1]['content'])
            for wire in wires:
                self.assertEqual(wire['max_tokens'], 8192)
                self.assertEqual(wire['thinking'], {'type': 'enabled'})
                self.assertEqual(wire['reasoning_effort'], 'high')
                system = wire['messages'][0]['content']
                self.assertEqual(system.count(criteria), 1)
                schema, _ = json.JSONDecoder().raw_decode(
                    system.split('Diagnosis JSON Schema:\n', 1)[1])
                self.assertEqual(schema, OUTPUT_SCHEMA)
                self.assertEqual(set(schema['required']), {'verdict', 'cause', 'when', 'refs', 'unknowns'})
                self.assertEqual(schema['properties']['cause']['maxLength'], 600)
                self.assertEqual(schema['properties']['when']['maxLength'], 300)
                self.assertEqual(schema['properties']['unknowns']['maxItems'], 6)
                self.assertEqual(schema['properties']['unknowns']['items']['maxLength'], 200)
            if strategy == 'adaptive_read_3':
                ask = next(tool for tool in wires[0]['tools'] if tool['function']['name'] == 'ask_user')
                self.assertEqual(ask['function']['parameters']['properties']['gap'],
                    {'type': 'string', 'minLength': 1, 'maxLength': 200})
        self.assertEqual(len(set(initial)), 1)

    def test_third_wire_only_advertises_canonical_terminal_tools_without_global_mutation(self):
        self.prepare()
        canonical = copy.deepcopy(TOOL_SCHEMAS)
        transport = self.three_tools()
        result = self.run_cell('adaptive_read_3', transport)
        self.assertEqual(result['status'], 'semantic_pending')
        self.assertEqual((result['requests_attempted'], result['tool_reads']), (3, 2))
        for wire in transport.posts[:2]:
            self.assertEqual({row['function']['name'] for row in wire['tools']}, ALL_TOOLS)
        self.assertEqual(transport.posts[2]['tools'],
            [row for row in canonical if row['function']['name'] in TERMINAL])
        self.assertEqual({row['function']['name'] for row in transport.posts[2]['tools']}, TERMINAL)
        self.assertEqual(TOOL_SCHEMAS, canonical)
        fresh = FakeTransport(reply(None, calls=[call('fresh', 'finish', {'diagnosis': UNKNOWN})]))
        self.run_cell('adaptive_read_3', fresh, name='fresh')
        self.assertEqual({row['function']['name'] for row in fresh.posts[0]['tools']}, ALL_TOOLS)
        self.assertEqual(TOOL_SCHEMAS, canonical)

    def test_hallucinated_third_read_is_rejected_without_read_or_fourth_dispatch(self):
        self.prepare()
        transport = self.three_tools(third=reply(None, calls=[call('bad-third', 'search', {'query': 'run'})]))
        transport.answers.append(reply(None, calls=[call('must-not-run', 'finish', {'diagnosis': UNKNOWN})]))
        original = StaticEvidenceTools.execute
        with patch.object(StaticEvidenceTools, 'execute', autospec=True, side_effect=original) as execute:
            result = self.run_cell('adaptive_read_3', transport)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_code'], 'model_response_invalid')
        self.assertEqual(result['tool_reads'], 2)
        self.assertEqual(execute.call_count, 2)
        self.assertEqual([row.args[1] for row in execute.call_args_list], ['search', 'read_symbol'])
        self.assertEqual(len(transport.posts), 3)
        self.assertEqual(len(transport.answers), 1)
        self.assertEqual([row['decision'] for row in result['tool_events']], [1, 2])

    def test_two_declared_finish_calls_reach_and_fail_local_third_decision_guard(self):
        self.prepare()
        transport = self.three_tools(third=reply(None, calls=[
            call('finish-one', 'finish', {'diagnosis': UNKNOWN}),
            call('finish-two', 'finish', {'diagnosis': UNKNOWN})]))
        transport.answers.append(reply(None, calls=[call('unused', 'finish', {'diagnosis': UNKNOWN})]))
        result = self.run_cell('adaptive_read_3', transport)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_code'], 'third_decision_must_terminate')
        response = result['requests'][2]['response']
        self.assertEqual(response['state'], 'received')
        self.assertEqual(len(response['assistant_message']['tool_calls']), 2)
        self.assertEqual(result['tool_reads'], 2)
        self.assertIsNone(result['answer'])
        self.assertEqual([row['decision'] for row in result['tool_events']], [1, 2])
        self.assertEqual(len(transport.posts), 3)
        self.assertEqual(len(transport.answers), 1)

    def test_ask_user_keeps_existing_character_boundary_and_unresolved_review(self):
        self.prepare()
        for number, (gap, status) in enumerate((('界' * 200, 'ask_user_terminated'),
                                               ('界' * 201, 'failed'))):
            transport = FakeTransport(reply(None, calls=[call('ask', 'ask_user', {'gap': gap})]),
                reply(None, calls=[call('unused', 'finish', {'diagnosis': UNKNOWN})]))
            result = self.run_cell('adaptive_read_3', transport, name='ask-' + str(number))
            self.assertEqual(result['status'], status)
            self.assertEqual(len(transport.posts), 1)
            self.assertEqual(result['tool_reads'], 0)
            if status == 'ask_user_terminated':
                self.assertEqual(result['gap'], gap)
                self.assertEqual(result['task_resolution'], 'unresolved')
                self.assertEqual(result['abstention_review_status'], 'pending')
                self.assertEqual(result['review_status'], 'pending')
                self.assertFalse(result['semantic_proof'])
            else:
                self.assertEqual(result['error_code'], 'ask_user_contract_invalid')

    def test_no_tool_raw_reasoning_history_is_preserved_without_effective_history_claim(self):
        self.prepare()
        thought = 'Known fixture reasoning retained in client history. ' * 300
        first = reply(json.dumps(UNKNOWN))
        first['choices'][0]['message']['reasoning_content'] = thought
        transport = FakeTransport(first, reply(json.dumps(UNKNOWN)), reply(json.dumps(UNKNOWN)))
        result = self.run_cell('direct_initial_3', transport)
        self.assertEqual(result['status'], 'semantic_pending')
        self.assertEqual(len(transport.posts), 3)
        self.assertTrue(all('tools' not in wire for wire in transport.posts))
        for wire in transport.posts[1:]:
            assistants = [row for row in wire['messages'] if row['role'] == 'assistant']
            self.assertEqual(assistants[0]['reasoning_content'], thought)
        durable = json.loads((self.control/'cell'/'decision-01.response.json').read_bytes())
        self.assertEqual(durable['assistant_message']['reasoning_content'], thought)
        # This inspects raw client/wire history, not provider-effective reasoning context.

    def test_tool_history_8001_measured_input_halts_and_settles_without_cropping(self):
        study = self.prepare(seed=0)
        self.assertEqual(study['cells'][0]['strategy'], 'adaptive_read_3')
        thought = 'Self-authored long retained tool-turn reasoning. ' * 300
        first = reply(None, calls=[call('read', 'search', {'query': 'double'})])
        first['choices'][0]['message']['reasoning_content'] = thought
        first['usage'].update(completion_tokens=6000,
            completion_tokens_details={'reasoning_tokens': 5900})
        second = reply(None, calls=[call('finish', 'finish', {'diagnosis': UNKNOWN})])
        second['usage'].update(prompt_tokens=8001, completion_tokens=20,
            prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=8001,
            completion_tokens_details={'reasoning_tokens': 2})
        transport = FakeTransport(first, second,
            reply(None, calls=[call('unused', 'finish', {'diagnosis': UNKNOWN})]))
        factories = []
        def factory(cell):
            factories.append(cell['cell_id'])
            self.assertEqual(cell['strategy'], 'adaptive_read_3')
            return DeepSeekProvider(model=MODEL['model_id'], transport=transport)
        result = batch.run_study(self.control, provider_factory=factory)
        self.assertTrue(result['batch_halted'])
        self.assertEqual(len(factories), 1)
        self.assertEqual(len(transport.posts), 2)
        self.assertEqual(len(transport.answers), 1)
        self.assertEqual(result['rows'][0]['error_code'], 'measured_budget_overrun')
        self.assertEqual(sum(row['status'] == 'not_attempted' for row in result['rows']), 3)
        self.assertEqual(result['accounting']['request_count'], 2)
        self.assertEqual(result['accounting']['known_accrued_usd'], '0.009629')
        self.assertEqual(result['accounting']['unknown_held_usd'], '0.000000')
        self.assertEqual(result['accounting']['state'], 'open')
        assistant = next(row for row in transport.posts[1]['messages'] if row['role'] == 'assistant')
        self.assertEqual(assistant['reasoning_content'], thought)
        self.assertTrue(all(wire['max_tokens'] == 8192 for wire in transport.posts))
        self.assertEqual(study['tasks'][0]['manifest']['budget']['max_total_input_tokens'], 24000)
        cell = self.control/'cells'/study['cells'][0]['cell_id']
        durable = json.loads((cell/'result.json').read_bytes())
        self.assertIsNone(durable['answer'])
        self.assertEqual(durable['reservations'][1]['reserved_input_tokens'], 8000)
        self.assertEqual(durable['reservations'][1]['reserved_output_tokens'], 8192)
        self.assertEqual(durable['reservations'][1]['state'], 'measured_budget_overrun')
        self.assertEqual(durable['requests'][1]['response']['raw_usage']['prompt_tokens'], 8001)
        self.assertEqual(durable['requests'][0]['response']['assistant_message']['reasoning_content'], thought)
        for number in (1, 2):
            outcome = json.loads((self.control/'ledger'/f'{number:03d}.outcome.json').read_bytes())
            self.assertEqual(outcome['state'], 'settled')
        self.assertFalse((cell/'decision-03.request.json').exists())

    def test_all_reasoning_8192_truncation_settles_known_fee_and_continues_other_cells(self):
        study = self.prepare(seed=0)
        self.assertEqual(study['cells'][0]['strategy'], 'adaptive_read_3')
        thought = 'Scripted reasoning token. ' * 8192
        truncated = reply('', finish='length')
        truncated['choices'][0]['message']['reasoning_content'] = thought
        truncated['usage'].update(prompt_tokens=1000, completion_tokens=8192,
            prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=1000,
            completion_tokens_details={'reasoning_tokens': 8192})
        adaptive = FakeTransport(truncated,
            reply(None, calls=[call('unused', 'finish', {'diagnosis': UNKNOWN})]))
        factories = []
        def factory(cell):
            factories.append(cell['cell_id'])
            transport = adaptive if cell['strategy'] == 'adaptive_read_3' else FakeTransport(
                *[reply(json.dumps(UNKNOWN)) for _ in range(3)])
            return DeepSeekProvider(model=MODEL['model_id'], transport=transport)
        result = batch.run_study(self.control, provider_factory=factory)
        self.assertFalse(result['batch_halted'])
        self.assertEqual(len(factories), 4)
        self.assertEqual(len(result['rows']), 4)
        self.assertEqual(sum(row['status'] == 'failed' for row in result['rows']), 1)
        self.assertEqual(sum(row['status'] == 'semantic_pending' for row in result['rows']), 3)
        self.assertEqual(result['rows'][0]['error_code'], 'model_output_incomplete')
        self.assertEqual(len(adaptive.posts), 1)
        self.assertEqual(adaptive.posts[0]['max_tokens'], 8192)
        self.assertEqual(len(adaptive.answers), 1)
        cell_id = study['cells'][0]['cell_id']
        accounted = json.loads((self.control/'cells'/f'{cell_id}.accounted.json').read_bytes())
        self.assertEqual(accounted['accounting']['known_accrued_usd'], '0.010131')
        self.assertEqual(accounted['accounting']['unknown_held_usd'], '0.000000')
        self.assertIsNone(accounted['answer'])
        response = accounted['requests'][0]['response']
        self.assertEqual(response['finish_reason'], 'length')
        self.assertEqual(response['raw_usage']['completion_tokens'], 8192)
        self.assertEqual(response['raw_usage']['completion_tokens_details']['reasoning_tokens'], 8192)
        self.assertEqual(response['assistant_message']['content'], '')
        self.assertEqual(response['assistant_message']['reasoning_content'], thought)
        outcome = json.loads((self.control/'ledger'/'001.outcome.json').read_bytes())
        self.assertEqual(outcome['state'], 'settled')
        self.assertEqual(outcome['charged_micro_usd'], 10131)
        self.assertEqual(result['accounting']['request_count'], 10)
        self.assertEqual(result['accounting']['known_accrued_usd'], '0.010257')
        self.assertEqual(result['accounting']['unknown_held_usd'], '0.000000')
        self.assertFalse((self.control/'cells'/cell_id/'decision-02.request.json').exists())

    def test_default_legacy_runner_stays_at_2000_without_v2_criteria(self):
        self.prepare(legacy=True)
        transport = FakeTransport(reply(None, calls=[call('legacy', 'finish', {'diagnosis': UNKNOWN})]))
        result = self.run_cell('adaptive_read_3', transport)
        self.assertEqual(result['status'], 'semantic_pending')
        self.assertEqual(transport.posts[0]['max_tokens'], 2000)
        criteria = getattr(runner, 'EXTERNAL_EVIDENCE_CRITERIA', None)
        self.assertIsInstance(criteria, str)
        self.assertNotIn(criteria, transport.posts[0]['messages'][0]['content'])
        self.assertNotEqual(self.task['manifest']['prompt_version'], PROMPT)
