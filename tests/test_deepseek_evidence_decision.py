"""Authored wire/host boundaries; scripted replies never establish model quality."""
import copy
from hashlib import sha256
import json
from pathlib import Path
import unittest

from debugger_fixtures import fixture_root
from test_deepseek_policy_runner import FakeTransport, MODEL, UNKNOWN, call, reply
from test_deepseek_study_profile import COMMITMENT, development_pack
from macr.evals.deepseek_study_profile import prepare_study, load_study, digest
from macr.evals.deepseek_study_runner import run_study_cell, STRATEGIES
from macr.evals.deepseek_policy_runner import _write_exclusive, OUTPUT_SCHEMA, TOOL_SCHEMAS
from macr.providers.deepseek import DeepSeekProvider

PROFILE='study-evidence-v1'
PROMPT='deepseek-study-evidence-v1'
PROJECT=Path(__file__).resolve().parents[1]
# Frozen b20d119 system bytes, before this decision-criteria implementation.
OLD_SYSTEMS={
    'legacy:direct':'b1830f038837737682f1c13ffdab372ad6396e6bf202cef07f8df57db88bd54a',
    'legacy:adaptive':'654c7620c6a7d09e764159c1a1498987e5ac12524d5578823b22ab8e1c83d52c',
    'v2:direct':'4fc2257f76888b53158d6b10a7024570f1d868da50158cca93617780e194c10a',
    'v2:adaptive':'ee12bad1a09d3068a76d83d47a19a8191603eff6b0d62357087101994cf1adc3'}


class EvidenceDecisionTests(unittest.TestCase):
    def prepare(self, profile=PROFILE):
        root=fixture_root();development_pack(root,1)
        out=root.parent/(root.name+'-control')
        try:
            study=prepare_study(root,custodian=COMMITMENT,out=out,
                project_root=PROJECT,execution_profile=profile)
        except ValueError as error:
            self.fail(f'explicit decision profile must prepare: {error}')
        return root,out,study

    def run_cell(self, root, out, study, strategy, responses, name='cell'):
        transport=FakeTransport(*responses)
        result=run_study_cell(out/'manifests/dev0',dataset_root=root,strategy=strategy,
            provider=DeepSeekProvider(model=MODEL['model_id'],transport=transport),
            out=out/name,fixed_plan=study['tasks'][0]['fixed_plan'])
        return result,transport

    def test_new_version_freezes_same_budget_and_distinct_cell_identity(self):
        _,out,study=self.prepare()
        self.assertEqual(study['prompt_version'],PROMPT)
        self.assertEqual(study['execution_profile'],PROFILE)
        self.assertEqual(study['cell_limits']['max_output_per_request'],8192)
        self.assertEqual(study['cell_limits']['max_total_output_tokens'],24576)
        self.assertEqual(study['cell_limits']['planned_input_per_request'],8000)
        self.assertEqual(study['price_snapshot']['reservation_micro_usd'],330000)
        self.assertFalse(study['remote_enabled'])
        self.assertEqual(load_study(out),study)
        _,_,v2=self.prepare('study-8192-v2')
        self.assertEqual(study['cell_limits'],v2['cell_limits'])
        self.assertEqual(study['price_snapshot'],v2['price_snapshot'])
        self.assertTrue(set(c['cell_id'] for c in study['cells']).isdisjoint(
            c['cell_id'] for c in v2['cells']))

    def test_actual_four_arm_wires_carry_decision_rules_and_unchanged_schema(self):
        root,out,study=self.prepare()
        for strategy in STRATEGIES:
            responses=([reply(None,calls=[call('end','finish',{'diagnosis':UNKNOWN})])]
                if strategy=='adaptive_read_3' else [reply(json.dumps(UNKNOWN)) for _ in range(3)])
            result,transport=self.run_cell(root,out,study,strategy,responses,name=strategy)
            self.assertEqual(result['status'],'semantic_pending')
            self.assertFalse(result['semantic_proof'])
            for raw in transport.raw_bodies:
                wire=json.loads(raw);system=wire['messages'][0]['content']
                for fragment in ('code property or a current external outcome',
                    'two states compatible with the supplied facts',
                    'do not withhold a code-proven conclusion',
                    'Accept explicitly supplied facts as task assumptions',
                    'smallest sufficient set', 'authorized source within the remaining tool budget'):
                    self.assertIn(fragment,system)
                self.assertEqual(wire['max_tokens'],8192)
                self.assertEqual(wire['reasoning_effort'],'high')
                schema,_=json.JSONDecoder().raw_decode(system.split('Diagnosis JSON Schema:\n',1)[1])
                self.assertEqual(schema,OUTPUT_SCHEMA)

    def test_legacy_and_v2_actual_system_bytes_are_unchanged_in_all_arms(self):
        for profile,label in ((None,'legacy'),('study-8192-v2','v2')):
            root,out,study=self.prepare(profile)
            for strategy in STRATEGIES:
                responses=([reply(None,calls=[call('end','finish',{'diagnosis':UNKNOWN})])]
                    if strategy=='adaptive_read_3' else [reply(json.dumps(UNKNOWN)) for _ in range(3)])
                _,transport=self.run_cell(root,out,study,strategy,responses,name=strategy)
                for raw in transport.raw_bodies:
                    wire=json.loads(raw);system=wire['messages'][0]['content']
                    kind='adaptive' if strategy=='adaptive_read_3' else 'direct'
                    self.assertEqual(sha256(system.encode()).hexdigest(),OLD_SYSTEMS[label+':'+kind])
                    self.assertEqual(wire['max_tokens'],2000 if profile is None else 8192)

    def test_rehashed_profile_or_prompt_substitution_is_rejected(self):
        for substitution in ('drop_tag','old_tag','old_prompt'):
            _,out,study=self.prepare()
            if substitution=='drop_tag':del study['execution_profile']
            elif substitution=='old_tag':study['execution_profile']='study-8192-v2'
            else:study['prompt_version']='deepseek-study-8192-v2'
            study['profile_sha256']=digest({k:v for k,v in study.items() if k!='profile_sha256'})
            (out/'study.json').unlink();_write_exclusive(out/'study.json',study,262144)
            with self.assertRaises(ValueError):load_study(out)

    def test_new_profile_remains_live_disabled_before_any_claim(self):
        _,out,_=self.prepare()
        from macr.evals.deepseek_study_live import propose_study_live_approval
        with self.assertRaisesRegex(ValueError,'study_profile_live_disabled'):
            propose_study_live_approval(out)
        self.assertFalse((out/'execution.claim.json').exists())

    def test_third_request_keeps_terminal_only_tools_and_never_fourth_dispatch(self):
        root,out,study=self.prepare();canonical=copy.deepcopy(TOOL_SCHEMAS)
        responses=[reply(None,calls=[call('r1','search',{'query':'double'})]),
            reply(None,calls=[call('r2','read_symbol',{'file':'helper.py','symbol':'double'})]),
            reply(None,calls=[call('r3','search',{'query':'entry'})]),
            reply(None,calls=[call('unused','finish',{'diagnosis':UNKNOWN})])]
        result,transport=self.run_cell(root,out,study,'adaptive_read_3',responses)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error_code'],'model_response_invalid')
        self.assertEqual(len(transport.posts),3)
        self.assertEqual(result['tool_reads'],2)
        self.assertEqual({t['function']['name'] for t in transport.posts[2]['tools']},
            {'finish','ask_user'})
        self.assertEqual(len(transport.answers),1)
        self.assertEqual(TOOL_SCHEMAS,canonical)

    def test_nondecisive_unknown_does_not_force_a_definite_answer_to_abstain(self):
        root,out,study=self.prepare()
        candidate={'verdict':'no_defect_supported','cause':'For the given input 3, double returns 6.',
            'when':'Input 3 under the stated arithmetic contract.',
            'refs':[{'file':'entry.py','start':1,'end':3}],
            'unknowns':['The deployment hostname is unknown and irrelevant to this arithmetic property.']}
        responses=[reply(json.dumps(candidate)) for _ in range(3)]
        result,_=self.run_cell(root,out,study,'direct_initial_3',responses)
        self.assertEqual(result['status'],'semantic_pending')
        self.assertEqual(result['answer'],candidate)
        self.assertFalse(result['semantic_proof'])

    def test_legally_cited_but_semantically_wrong_answer_is_not_host_scored_or_corrected(self):
        root,out,study=self.prepare()
        candidate={'verdict':'no_defect_supported','cause':'The deployment switch is certainly true because this line is present.',
            'when':'The current deployment, although its switch was never supplied.',
            'refs':[{'file':'entry.py','start':1,'end':3}], 'unknowns':[]}
        result,_=self.run_cell(root,out,study,'direct_initial_3',
            [reply(json.dumps(candidate)) for _ in range(3)])
        self.assertEqual(result['answer'],candidate)
        self.assertEqual(result['status'],'semantic_pending')
        self.assertEqual(result['review_status'],'pending')
        self.assertFalse(result['semantic_proof'])

    def test_same_decisive_gap_ask_and_unknown_both_remain_unresolved_for_review(self):
        root,out,study=self.prepare()
        gap='Supply the current compression switch for the target environment: true enables compression, false disables it.'
        unknown={'verdict':'unknown','cause':None,'when':None,'refs':[], 'unknowns':[gap]}
        for action in ('ask_user','finish'):
            args={'gap':gap} if action=='ask_user' else {'diagnosis':unknown}
            result,transport=self.run_cell(root,out,study,'adaptive_read_3',
                [reply(None,calls=[call('end',action,args)])],name=action)
            self.assertEqual(result['review_status'],'pending')
            self.assertFalse(result['semantic_proof'])
            self.assertEqual(result['requests_attempted'],1)
            self.assertEqual(len(transport.posts),1)
            if action=='ask_user':
                self.assertEqual(result['task_resolution'],'unresolved')
                self.assertEqual(result['gap'],gap)
            else:self.assertEqual(result['answer'],unknown)
