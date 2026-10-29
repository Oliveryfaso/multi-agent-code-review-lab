"""Offline dollar/request gates and complete planned denominators."""
import json
from pathlib import Path
import unittest

from debugger_fixtures import fixture_root
from test_deepseek_study_profile import development_pack, COMMITMENT
from test_deepseek_policy_runner import FakeTransport, reply, call, UNKNOWN
from macr.providers.deepseek import DeepSeekProvider
from macr.evals.deepseek_study_profile import prepare_study, StudyError
from macr.evals.deepseek_policy_runner import PolicyRunError
from macr.evals import deepseek_study_batch as batch


class StudyBatchTests(unittest.TestCase):
    def prepare(self, count=1, budget=None):
        root = fixture_root()
        development_pack(root,count)
        out = root.parent / (root.name + '-study')
        study = prepare_study(root,custodian=COMMITMENT,out=out,
            project_root=Path(batch.__file__).parents[3],budget=budget)
        return out, study

    def factory(self, cell):
        if cell['strategy'] == 'adaptive_read_3':
            answers = [reply(None,calls=[call('finish','finish',{'diagnosis':UNKNOWN})])]
        else:
            answers = [reply(json.dumps(UNKNOWN)) for _ in range(3)]
        return DeepSeekProvider(model='deepseek-flash',transport=FakeTransport(*answers))

    def test_all_36_cells_scripted_with_real_artifacts_and_no_quality_claim(self):
        out, study = self.prepare(9)
        result = batch.run_study(out,provider_factory=self.factory)
        self.assertEqual(len(result['rows']),36)
        self.assertEqual(result['model_calls_live'],0)
        self.assertEqual(result['accounting']['request_count'],90)
        self.assertEqual(result['planned_cells'],36)
        self.assertEqual(result['semantic_scoring'],'pending_independent_review')
        self.assertTrue(all((out/'cells'/cell['cell_id']/'result.json').is_file() for cell in study['cells']))
        with self.assertRaisesRegex(StudyError,'execution_already_claimed'):
            batch.run_study(out,provider_factory=self.factory)

    def test_unknown_holds_reservation_and_stops_remaining_planned_cells(self):
        out, study = self.prepare()
        def timeout(cell):
            return DeepSeekProvider(model='deepseek-flash',transport=FakeTransport(TimeoutError()))
        result = batch.run_study(out,provider_factory=timeout)
        self.assertEqual(result['accounting']['request_count'],1)
        self.assertEqual(result['accounting']['unknown_held_usd'],'0.330000')
        self.assertEqual(result['accounting']['state'],'halted_unknown')
        self.assertEqual(sum(row['status']=='not_attempted' for row in result['rows']),3)

    def test_known_contract_failure_kept_and_next_cell_runs_without_retry(self):
        out, study = self.prepare()
        first = study['cells'][0]['cell_id']
        def factory(cell):
            if cell['cell_id'] == first:
                return DeepSeekProvider(model='deepseek-flash',transport=FakeTransport(reply('{}')))
            return self.factory(cell)
        result=batch.run_study(out,provider_factory=factory)
        self.assertEqual(result['rows'][0]['status'],'failed')
        self.assertEqual(sum(row['status']=='not_attempted' for row in result['rows']),0)
        self.assertEqual(result['accounting']['unknown_held_usd'],'0.000000')

    def test_known_bad_read_arguments_and_read_count_fail_cell_and_continue(self):
        variants=([call('bad','read_symbol',{'file':123,'symbol':'run'})],
            [call('bad','read_symbol',{'file':'entry.py','symbol':123})],
            [call(str(i),'search',{'query':str(i)}) for i in range(3)])
        for calls in variants:
            out, study=self.prepare()
            def factory(cell):
                if cell['strategy']=='adaptive_read_3':
                    return DeepSeekProvider(model='deepseek-flash',transport=FakeTransport(reply(None,calls=calls)))
                return self.factory(cell)
            result=batch.run_study(out,provider_factory=factory)
            self.assertEqual(sum(row['status']=='not_attempted' for row in result['rows']),0)
            self.assertEqual(sum(row['status']=='failed' for row in result['rows']),1)
            report=json.loads((out/'paired-report.json').read_text())
            failed=next(row for row in report['cells'] if row['strategy']=='adaptive_read_3')
            self.assertEqual(failed['state'],'contract_failed')

    def test_billed_contract_error_still_enforces_batch_fingerprint(self):
        out,study=self.prepare()
        target=study['cells'][1]['cell_id']
        def factory(cell):
            if cell['cell_id']==target:
                response=reply(json.dumps(UNKNOWN),finish='length')
                response['system_fingerprint']='different-fingerprint'
                return DeepSeekProvider(model='deepseek-flash',transport=FakeTransport(response))
            return self.factory(cell)
        result=batch.run_study(out,provider_factory=factory)
        self.assertEqual(result['rows'][1]['error_code'],'system_fingerprint_drift')
        self.assertEqual(sum(row['status']=='not_attempted' for row in result['rows']),2)
        self.assertEqual(result['accounting']['unknown_held_usd'],'0.000000')

    def test_request_and_dollar_budget_stop_before_extra_dispatch(self):
        for budget in ({'max_requests':1,'max_usd':'2.000000'},
                       {'max_requests':108,'max_usd':'0.330000'}):
            out, study = self.prepare(budget=budget)
            calls=[]
            def factory(cell):
                provider=self.factory(cell)
                original=provider.transport.post
                def post(*args):
                    calls.append(1)
                    return original(*args)
                provider.transport.post=post
                return provider
            result=batch.run_study(out,provider_factory=factory)
            self.assertEqual(len(calls),1)
            self.assertEqual(result['accounting']['request_count'],1)

    def test_remote_provider_refused_before_request_or_key_usage(self):
        out, _ = self.prepare()
        result=batch.run_study(out,provider_factory=lambda _:DeepSeekProvider(allow_remote=True))
        self.assertEqual(result['accounting']['request_count'],0)
        self.assertTrue(any(row['status']=='failed' for row in result['rows']))

    def test_invalid_source_and_copied_control_rejected(self):
        import shutil
        out, _ = self.prepare()
        clone=out.parent/(out.name+'-copy')
        shutil.copytree(out,clone)
        with self.assertRaisesRegex(StudyError,'control_path_mismatch'):
            batch.run_study(clone,provider_factory=self.factory)

    def test_cli_remote_flag_and_project_output_rejected_before_mutation(self):
        import run_deepseek_study_dryrun as cli
        root=fixture_root()
        with self.assertRaises(SystemExit):
            cli.main(['--out',str(root/'unused'),'--live'])
        self.assertFalse((root/'unused').exists())
        with self.assertRaises(StudyError):
            cli.main(['--out',str(Path(cli.__file__).parents[1]/'forbidden-study-output')])
