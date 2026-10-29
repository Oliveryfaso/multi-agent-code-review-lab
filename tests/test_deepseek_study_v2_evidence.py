"""Authored development gaps demonstrate contract boundaries, not model quality."""
import json
from pathlib import Path
import unittest

from debugger_fixtures import fixture_root
from test_deepseek_study_profile import COMMITMENT, development_pack
from test_deepseek_policy_runner import FakeTransport, MODEL, call, reply
from macr.evals.deepseek_study_profile import prepare_study
from macr.evals.deepseek_study_runner import run_study_cell
from macr.providers.deepseek import DeepSeekProvider


class V2EvidenceFixtureTests(unittest.TestCase):
    def run_fixture(self, *, threshold=None, diagnosis=None, gap=None):
        root=fixture_root();pack=development_pack(root,1);task=pack['tasks'][0]
        task['initial_evidence_files']=['entry.py','helper.py']
        task['question']='Does the current score meet the configured acceptance threshold?'
        task['scenario']='Input is 3. Accept exactly when the computed score meets the current external threshold.'
        task['assumptions']=['The current threshold is between 1 and 10.']
        if threshold is not None:task['scenario']+=' The supplied current threshold is 5.'
        (root/'pack.json').write_text(json.dumps(pack))
        out=root.parent/(root.name+'-control')
        study=prepare_study(root,custodian=COMMITMENT,out=out,
            project_root=Path(__file__).resolve().parents[1],execution_profile='study-8192-v2')
        action=call('terminal','ask_user',{'gap':gap}) if gap is not None else call(
            'terminal','finish',{'diagnosis':diagnosis})
        transport=FakeTransport(reply(None,calls=[action]))
        result=run_study_cell(out/'manifests/dev0',dataset_root=root,strategy='adaptive_read_3',
            provider=DeepSeekProvider(model=MODEL['model_id'],transport=transport),
            out=out/'fixture-cell',fixed_plan=study['tasks'][0]['fixed_plan'])
        return result,transport.posts[0]

    def test_unsupplied_decisive_threshold_ends_unresolved_without_granting_credit(self):
        # The same code score 6 is compatible with a passing threshold 4 and failing 8.
        gap='Code computes score 6; the current external acceptance threshold is missing. Supply its current value to decide whether 6 meets it.'
        result,wire=self.run_fixture(gap=gap)
        self.assertEqual(result['gap'],gap)
        self.assertEqual(result['status'],'ask_user_terminated')
        self.assertEqual(result['task_resolution'],'unresolved')
        self.assertEqual(result['abstention_review_status'],'pending')
        self.assertFalse(result['semantic_proof'])
        self.assertEqual(result['requests_attempted'],1)
        initial=json.loads(wire['messages'][1]['content'])
        self.assertEqual({row['file'] for row in initial['source']},{'entry.py','helper.py'})
        self.assertNotIn('supplied current threshold',initial['scenario'])

    def test_supplied_fact_and_authored_diagnosis_remain_review_pending(self):
        diagnosis={'verdict':'no_defect_supported','cause':'The code computes 6, meeting the supplied threshold of 5.',
            'when':'Input is 3 and the supplied current threshold is 5.',
            'refs':[{'file':'entry.py','start':1,'end':3},{'file':'helper.py','start':1,'end':2}],
            'unknowns':[]}
        result,wire=self.run_fixture(threshold=5,diagnosis=diagnosis)
        self.assertEqual(result['status'],'semantic_pending')
        self.assertEqual(result['answer'],diagnosis)
        self.assertEqual(result['review_status'],'pending')
        self.assertFalse(result['semantic_proof'])
        self.assertIn('supplied current threshold is 5',json.loads(wire['messages'][1]['content'])['scenario'])

    def test_overbroad_gap_is_never_rewritten_or_credited_by_the_host(self):
        result,_=self.run_fixture(gap='Please supply all logs and all configuration.')
        self.assertEqual(result['gap'],'Please supply all logs and all configuration.')
        self.assertEqual(result['status'],'ask_user_terminated')
        self.assertEqual(result['abstention_review_status'],'pending')
        self.assertFalse(result['semantic_proof'])
        self.assertEqual(result['requests_attempted'],1)
