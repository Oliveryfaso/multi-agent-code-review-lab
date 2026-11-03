"""Pure development-contract checks: no provider, host or target execution."""
from copy import deepcopy
import importlib
import json
from pathlib import Path
import unittest

try:
    comparison = importlib.import_module('macr.evals.repair_comparison')
except ModuleNotFoundError:
    comparison = None

EXAMPLE = Path(__file__).resolve().parents[1] / 'examples/repair_comparison_development.json'


class ThreeArmComparisonTests(unittest.TestCase):
    """Synthetic receipt arithmetic only; none of these rows attest execution."""
    arms = ('direct_api', 'api_test_feedback', 'api_debugger')
    contrast = 'feedback_and_public_pass_stop_policy'

    def spec(self, tasks=8, repeats=1, proposal_contract='v3'):
        from macr.investigation.repair_loop import output_contract, digest
        spec = json.loads(EXAMPLE.read_text())
        spec.update(schema='macr.repair-comparison.spec.v2', contrast=self.contrast,
                    repeats=repeats)
        contract = deepcopy(output_contract('v2'))
        contract['version'] = 'macr.repair.bound-proposal.'+proposal_contract
        if proposal_contract == 'v6':
            contract['fields'].append('selected_content_id')
            contract['statuses'].append('select_candidate')
        spec['model']['output_contract_sha256'] = digest(contract)
        spec['tasks'] = [dict(task_id='case-'+str(n), **{key:comparison.result_digest([key,n])
            for key in ('snapshot_sha256','issue_sha256','visible_tests_sha256','initial_context_sha256')})
            for n in range(tasks)]
        return spec

    def row(self, spec, cell, outcome='full_repair', *, status='answer_recorded', reviewed=True):
        task = next(t for t in spec['tasks'] if t['task_id']==cell['task_id'])
        row = comparison._empty(task, spec)
        row.update(status=status, candidate_origin='model', verification_origin='isolated_execution',
            requests=1,input_tokens=40,output_tokens=20,known_usd='0.001000',
            model_identity_sha256='a'*64)
        if status=='answer_recorded':
            row.update(verdict='defect' if outcome!='necessary_abstention' else 'unknown',
                final_output_sha256='b'*64,candidate_sha256='c'*64 if outcome!='necessary_abstention' else None,
                verification_receipt_sha256='d'*64)
            if reviewed:
                row['review']={'status':'locked','source':'independent_custodian',
                    'rubric_sha256':spec['scoring']['rubric_sha256'],
                    'hidden_tests_sha256':spec['scoring']['hidden_tests_sha256'],
                    'result_sha256':comparison.result_digest(row),'outcome':outcome}
        return row

    def test_v1_canonical_plan_report_and_generation_remain_unchanged(self):
        spec=json.loads(EXAMPLE.read_text());plan=comparison.compile_comparison(spec)
        self.assertEqual(comparison.result_digest(plan),'25f3ca6df927bbd25334c9e1480b64fdf1c5241d4c73f3e3f18a39148c2d000e')
        self.assertEqual(comparison.result_digest(comparison.build_report(plan,{})),
            '904d5ef541b831807633ab93bfb107d13d51972a065d21e3ff80dc1b054053eb')
        self.assertEqual(comparison.result_digest(comparison.generation_view(plan,spec['tasks'][0]['task_id'])),
            'f422d969be82338448a16e89d93643b27e0b5f72bc96317ac5b12fe30f56045b')

    def test_v3_canonical_plan_report_and_generation_remain_unchanged(self):
        spec=self.spec();plan=comparison.compile_comparison(spec)
        self.assertEqual(comparison.result_digest(plan),
            '42f49093326f9d2a6929ca47248f9d42d4c9b24d4437464c528dda39a7273732')
        self.assertEqual(comparison.result_digest(comparison.build_report(plan,{})),
            '9b24c5a9ab9748eff8c7074c1d1e853b25085a5bebb92a427d1f9d639708f5d6')
        self.assertEqual(comparison.result_digest(comparison.generation_view(plan,spec['tasks'][0]['task_id'])),
            '6b9f0bf654a423497af80fa302680885b9c417956330192f8fa1f177dcc2efdb')

    def test_v4_canonical_plan_report_and_generation_remain_unchanged(self):
        spec=self.spec(proposal_contract='v4');plan=comparison.compile_comparison(spec)
        self.assertEqual(comparison.result_digest(plan),
            '18303ac7ec6a0c5b991511821f0a7d8e6db402be4591cdde198cd60225407aca')
        self.assertEqual(comparison.result_digest(comparison.build_report(plan,{})),
            '41c7806accb1f3f1b04423b4efe7cf8f2d67fc8c3c99b46a5afef6d8b9ddaf68')
        self.assertEqual(comparison.result_digest(comparison.generation_view(plan,spec['tasks'][0]['task_id'])),
            '709078ed4dbf5824b7c48a22d03b64448d387b14ce03263e99055103072b702b')

    def test_v5_canonical_plan_report_and_generation_remain_unchanged(self):
        spec=self.spec(proposal_contract='v5');plan=comparison.compile_comparison(spec)
        self.assertEqual(comparison.result_digest(plan),
            '359c54c7aac25b2fe58c1a2ae743638261eeda152ddcae607270660aae05308d')
        self.assertEqual(comparison.result_digest(comparison.build_report(plan,{})),
            '807bfa707e56145178cdf1c2b5a2579f7fa9e6741c10f8e00dc67192267d5697')
        self.assertEqual(comparison.result_digest(comparison.generation_view(plan,spec['tasks'][0]['task_id'])),
            '4b27cd90b5adc59ca2892917d7b273909be1cfbff655a39fa93f410d5449f056')

    def test_v2_three_arm_order_is_frozen_and_balanced_without_extra_authority(self):
        for tasks in (1,2,3,5,6,8):
            spec=self.spec(tasks=tasks,repeats=2)
            try: plan=comparison.compile_comparison(spec)
            except ValueError as error: self.fail('explicit three-arm v2 should compile: '+str(error))
            self.assertEqual(plan['schema'],'macr.repair-comparison.plan.v2')
            self.assertEqual(plan['planned_cells'],tasks*2*3)
            self.assertEqual(plan['max_requests'],tasks*2*9)
            self.assertEqual(set(plan['arms']),set(self.arms))
            orders=[tuple(c['arm'] for c in plan['cells'][n:n+3]) for n in range(0,len(plan['cells']),3)]
            for pos in range(3):
                counts=[sum(order[pos]==arm for order in orders) for arm in self.arms]
                self.assertLessEqual(max(counts)-min(counts),1)
            if len(orders)>=6:self.assertEqual(len(set(orders[:6])),6)
            self.assertFalse(plan['remote_enabled']);self.assertFalse(plan['execution_authorized'])
            self.assertEqual(comparison.validate_comparison(plan),plan)
            self.assertEqual(comparison.compile_comparison(spec),plan)
            plan['cells'][0],plan['cells'][1]=plan['cells'][1],plan['cells'][0]
            with self.assertRaises(ValueError):comparison.validate_comparison(plan)

    def test_v2_rejects_task_content_aliases_old_contracts_and_unfair_request_ceiling(self):
        for mutate in (lambda s:s['tasks'].append(dict(s['tasks'][0],task_id='alias')),
                lambda s:s['model'].update(output_contract_sha256='1'*64),
                lambda s:s['limits'].update(max_requests=4)):
            spec=self.spec();mutate(spec)
            with self.subTest(mutate=mutate),self.assertRaises(ValueError):comparison.compile_comparison(spec)

    def test_large_proposal_does_not_claim_current_spend_ledger_can_execute_it(self):
        for tasks,fits in ((21,True),(24,False)):
            spec=self.spec(tasks=tasks,repeats=2);plan=comparison.compile_comparison(spec)
            self.assertEqual(plan['current_spend_capacity']['fits'],fits)
            self.assertEqual(plan['current_spend_capacity']['max_cells'],128)
            self.assertEqual(plan['current_spend_capacity']['max_requests'],384)
            self.assertFalse(plan['execution_authorized'])
            if not fits:
                self.assertEqual((plan['planned_cells'],plan['max_requests']),(144,432))
                self.assertEqual(comparison.build_report(plan,{})['planned_cells'],144)

    def test_v2_generation_capsule_is_detached_and_excludes_scoring(self):
        spec=self.spec();plan=comparison.compile_comparison(spec)
        view=comparison.generation_view(plan,spec['tasks'][0]['task_id'])
        self.assertEqual(view['schema'],'macr.repair-comparison.generation.v2')
        self.assertEqual(set(view),{'schema','task','model','limits'})
        self.assertNotIn('scoring',json.dumps(view));self.assertNotIn('hidden_tests',json.dumps(view))
        view['limits']['max_requests']=1
        self.assertEqual(plan['spec']['limits']['max_requests'],3)

    def test_v4_diagnostic_contract_is_frozen_per_plan_and_cannot_mix_result_versions(self):
        spec=self.spec(tasks=1,proposal_contract='v4')
        try:plan=comparison.compile_comparison(spec)
        except ValueError as error:self.fail('explicit v4 three-arm plan should compile: '+str(error))
        self.assertEqual(comparison.validate_comparison(plan),plan)
        view=comparison.generation_view(plan,spec['tasks'][0]['task_id'])
        self.assertEqual(view['model']['output_contract_sha256'],spec['model']['output_contract_sha256'])
        self.assertFalse(plan['remote_enabled']);self.assertFalse(plan['execution_authorized'])
        self.assertEqual(comparison.build_report(plan,{})['planned_cells'],3)
        legacy=self.spec(tasks=1);legacy_plan=comparison.compile_comparison(legacy)
        self.assertNotEqual(plan['comparison_sha256'],legacy_plan['comparison_sha256'])
        for current,wrong in ((plan,legacy),(legacy_plan,spec)):
            cell=current['cells'][0]
            row=self.row(current['spec'],cell,reviewed=False)
            row['output_contract_sha256']=wrong['model']['output_contract_sha256']
            with self.subTest(contract=current['spec']['model']['output_contract_sha256']),\
                    self.assertRaisesRegex(ValueError,'comparison_model_or_contract_mismatch'):
                comparison.build_report(current,{cell['cell_id']:row})

    def test_v4_finalize_chain_keeps_candidate_in_unscored_handoff(self):
        self.finalize_chain('v4')

    def test_v5_trajectory_contract_rejects_mixed_legacy_results(self):
        spec=self.spec(tasks=1,proposal_contract='v5')
        try:plan=comparison.compile_comparison(spec)
        except ValueError as error:self.fail('explicit v5 three-arm plan should compile: '+str(error))
        self.assertFalse(plan['remote_enabled']);self.assertFalse(plan['execution_authorized'])
        self.assertEqual(comparison.build_report(plan,{})['planned_cells'],3)
        for version in ('v3','v4'):
            legacy=self.spec(tasks=1,proposal_contract=version)
            legacy_plan=comparison.compile_comparison(legacy)
            self.assertNotEqual(plan['comparison_sha256'],legacy_plan['comparison_sha256'])
            for current,wrong in ((plan,legacy),(legacy_plan,spec)):
                cell=current['cells'][0];row=self.row(current['spec'],cell,reviewed=False)
                row['output_contract_sha256']=wrong['model']['output_contract_sha256']
                with self.subTest(legacy=version,plan=current['comparison_sha256']),\
                        self.assertRaisesRegex(ValueError,'comparison_model_or_contract_mismatch'):
                    comparison.build_report(current,{cell['cell_id']:row})

    def test_v5_finalize_chain_keeps_current_candidate_in_unscored_handoff(self):
        self.finalize_chain('v5')

    def selection_inputs(self, version='v6'):
        from macr.investigation.repair_loop import source_id
        files={'app.py':b'def value():\n    return 1\n',
            'checks.py':b'import unittest\nclass Checks(unittest.TestCase):\n'
                b'    def test_value(self):\n        self.assertTrue(True)\n'}
        chosen=dict(files);chosen['app.py']=b'def value():\n    return 2\n'
        last=dict(files);last['app.py']=b'def value():\n    return 3\n'
        spec=self.spec(tasks=1,proposal_contract=version)
        spec['tasks'][0].update(snapshot_sha256=source_id(files),
            visible_tests_sha256=source_id({'checks.py':files['checks.py']}))
        try:plan=comparison.compile_comparison(spec)
        except ValueError as error:self.fail('explicit '+version+' selection plan should compile: '+str(error))
        cell=plan['cells'][0]
        def proposal(status, base, edits, selected=None):
            return {'status':status,'summary':'Authored selection fixture, not a model repair.',
                'base_content_id':base,'hypothesis_id':'1'*64,'evidence_ids':['issue-report'],
                'edits':edits,'selected_content_id':selected}
        first=proposal('candidate',source_id(files),[{'file':'app.py','old':'return 1','new':'return 2'}])
        second=proposal('candidate',source_id(chosen),[{'file':'app.py','old':'return 2','new':'return 3'}])
        selected=proposal('select_candidate',source_id(last),[],source_id(chosen))
        receipt={'source_id':source_id(chosen),'origin':'unavailable','execution_status':'not_performed'}
        row=self.row(spec,cell,reviewed=False)
        row.update(candidate_origin='simulation',verification_origin='unavailable',requests=3,
            candidate_sha256=source_id(chosen),final_output_sha256=comparison.result_digest(selected),
            verification_receipt_sha256=comparison.result_digest(receipt))
        return {'plan':plan,'cell_id':cell['cell_id'],'row':row,'original_files':files,
            'editable_paths':['app.py'],'protected_paths':['checks.py'],
            'proposal_chain':[first,second,selected],'final_output':selected,'verification_receipt':receipt}

    def test_v6_selects_earlier_candidate_without_reapplying_or_granting_score(self):
        from macr.investigation.repair_loop import source_id
        from macr.investigation.repair_edits import apply_edits
        from unittest.mock import patch
        args=self.selection_inputs()
        with patch('macr.investigation.repair_edits.apply_edits',wraps=apply_edits) as applied:
            packet=comparison.score_packet(**args)
        self.assertEqual(applied.call_count,2)
        self.assertEqual(packet['candidate_files']['app.py'],'def value():\n    return 2\n')
        self.assertEqual(packet['candidate_files']['checks.py'].encode(),args['original_files']['checks.py'])
        last=dict(args['original_files']);last['app.py']=b'def value():\n    return 3\n'
        self.assertEqual(packet['last_applied_source_id'],source_id(last))
        self.assertNotEqual(packet['result']['candidate_sha256'],packet['last_applied_source_id'])
        self.assertFalse(packet['semantic_truth_validated']);self.assertFalse(packet['provenance_authenticated'])
        report=comparison.build_report(args['plan'],{args['cell_id']:packet['result']})
        self.assertEqual(report['arms'][args['plan']['cells'][0]['arm']]['scored'],0)
        self.assertEqual(report['planned_cells'],3)
        legacy=self.selection_inputs('v5')
        for current,wrong in ((args,legacy),(legacy,args)):
            row=deepcopy(current['row'])
            row['output_contract_sha256']=wrong['row']['output_contract_sha256']
            with self.assertRaisesRegex(ValueError,'comparison_model_or_contract_mismatch'):
                comparison.build_report(current['plan'],{current['cell_id']:row})

    def test_v6_selection_rejects_unaccepted_missing_wrong_and_nonterminal_ids(self):
        from macr.investigation.repair_loop import source_id
        original_id=source_id(self.selection_inputs()['original_files'])
        for selected in (None,'','f'*64,'not-a-digest',original_id):
            args=self.selection_inputs();args['final_output']['selected_content_id']=selected
            args['row']['final_output_sha256']=comparison.result_digest(args['final_output'])
            with self.subTest(selected=selected),self.assertRaisesRegex(ValueError,'comparison_score_selection_contract'):
                comparison.score_packet(**args)
        args=self.selection_inputs();args['proposal_chain']=[args['final_output']]
        args['final_output']['base_content_id']=source_id(args['original_files'])
        args['row']['final_output_sha256']=comparison.result_digest(args['final_output'])
        with self.assertRaisesRegex(ValueError,'comparison_score_selection_contract'):comparison.score_packet(**args)
        args=self.selection_inputs();args['final_output']['edits']=[{'file':'app.py','old':'return 3','new':'return 2'}]
        args['row']['final_output_sha256']=comparison.result_digest(args['final_output'])
        with self.assertRaisesRegex(ValueError,'comparison_score_selection_contract'):comparison.score_packet(**args)
        args=self.selection_inputs();args['proposal_chain'].insert(1,args['proposal_chain'].pop())
        args['proposal_chain'][1]['base_content_id']=args['proposal_chain'][2]['base_content_id']
        args['final_output']=args['proposal_chain'][-1]
        args['row']['final_output_sha256']=comparison.result_digest(args['final_output'])
        with self.assertRaisesRegex(ValueError,'comparison_score_selection_contract'):comparison.score_packet(**args)

    def test_v6_selection_field_is_required_and_null_on_nonselection(self):
        for status in ('candidate','no_candidate','insufficient_evidence','finalize_current'):
            for missing in (False,True):
                args=self.selection_inputs();item=args['proposal_chain'][0];item['status']=status
                if missing:item.pop('selected_content_id')
                else:item['selected_content_id']='f'*64
                with self.subTest(status=status,missing=missing),self.assertRaisesRegex(
                        ValueError,'comparison_score_selection_field'):comparison.score_packet(**args)
        args=self.selection_inputs();args['row']['candidate_sha256']='f'*64
        with self.assertRaisesRegex(ValueError,'comparison_score_candidate_binding'):comparison.score_packet(**args)
        args=self.selection_inputs();args.pop('proposal_chain');args['edits']=[{'file':'app.py','old':'return 1','new':'return 2'}]
        with self.assertRaisesRegex(ValueError,'comparison_score_selection_chain_required'):comparison.score_packet(**args)
        args=self.selection_inputs('v5')
        with self.assertRaisesRegex(ValueError,'comparison_score_selection_chain_required'):comparison.score_packet(**args)

    def test_v6_can_select_current_or_a_later_accepted_baseline_snapshot(self):
        from macr.investigation.repair_loop import source_id
        for baseline in (False,True):
            args=self.selection_inputs();final=args['final_output']
            chosen=dict(args['original_files'])
            if baseline:
                args['proposal_chain'][1]['edits'][0]['new']='return 1'
            else:chosen['app.py']=b'def value():\n    return 3\n'
            final['base_content_id']=final['selected_content_id']=source_id(chosen)
            args['row']['candidate_sha256']=source_id(chosen)
            args['row']['final_output_sha256']=comparison.result_digest(final)
            args['verification_receipt']['source_id']=source_id(chosen)
            args['row']['verification_receipt_sha256']=comparison.result_digest(args['verification_receipt'])
            packet=comparison.score_packet(**args)
            self.assertEqual(packet['candidate_files']['app.py'].encode(),chosen['app.py'])

    def test_v6_finalize_current_keeps_null_selection_and_current_candidate(self):
        self.finalize_chain('v6')

    def finalize_chain(self, version):
        from macr.investigation.repair_edits import apply_edits
        from macr.investigation.repair_loop import source_id
        files={'app.py':b'def value():\n    return 1\n',
            'checks.py':b'import unittest\nclass Checks(unittest.TestCase):\n'
                b'    def test_value(self):\n        self.assertTrue(True)\n'}
        edits=[{'file':'app.py','old':'return 1','new':'return 2'}]
        changed=apply_edits(files,edits,editable_paths=['app.py'])
        spec=self.spec(tasks=1,proposal_contract=version)
        spec['tasks'][0].update(snapshot_sha256=source_id(files),
            visible_tests_sha256=source_id({'checks.py':files['checks.py']}))
        try:plan=comparison.compile_comparison(spec)
        except ValueError as error:self.fail('explicit '+version+' score plan should compile: '+str(error))
        cell=plan['cells'][0]
        first={'status':'candidate','base_content_id':source_id(files),'edits':edits}
        final={'status':'finalize_current','base_content_id':source_id(changed),'edits':[]}
        if version == 'v6':first['selected_content_id']=final['selected_content_id']=None
        receipt={'origin':'simulation','target_code_executed':False}
        row=self.row(spec,cell,reviewed=False)
        row.update(candidate_origin='simulation',verification_origin='simulation',requests=2,
            candidate_sha256=source_id(changed),final_output_sha256=comparison.result_digest(final),
            verification_receipt_sha256=comparison.result_digest(receipt))
        packet=comparison.score_packet(plan,cell['cell_id'],row,original_files=files,
            editable_paths=['app.py'],protected_paths=['checks.py'],proposal_chain=[first,final],
            final_output=final,verification_receipt=receipt)
        self.assertEqual(packet['last_applied_source_id'],source_id(changed))
        self.assertEqual(packet['candidate_files']['checks.py'].encode(),files['checks.py'])
        self.assertEqual(packet['result']['output_contract_sha256'],spec['model']['output_contract_sha256'])
        self.assertFalse(packet['semantic_truth_validated']);self.assertFalse(packet['provenance_authenticated'])
        report=comparison.build_report(plan,{cell['cell_id']:packet['result']})
        self.assertEqual(report['arms'][cell['arm']]['scored'],0)
        self.assertEqual(report['planned_cells'],3)

    def test_missing_and_pending_results_are_unresolved_not_zero_effect_ties(self):
        spec=self.spec();plan=comparison.compile_comparison(spec)
        for results in ({},{c['cell_id']:self.row(spec,c,reviewed=False) for c in plan['cells']}):
            report=comparison.build_report(plan,results)
            self.assertEqual(report['schema'],'macr.repair-comparison.report.v2')
            self.assertEqual(report['planned_cells'],24)
            for arm in self.arms:
                completion=report['arms'][arm]['completion_bounds']
                self.assertEqual(completion['observed_successes'],0)
                self.assertEqual(completion['unresolved'],8)
                self.assertEqual(completion['rate_bounds'],[0.0,1.0])
            for contrast in report['contrasts'].values():
                self.assertEqual(contrast['conditional_quality']['tie'],0)
                self.assertEqual(contrast['conditional_quality']['not_comparable'],8)
                self.assertIsNone(contrast['task_cluster']['point_difference'])
                self.assertEqual(contrast['task_cluster']['difference_bounds'],[-1.0,1.0])
                self.assertEqual(contrast['uncertainty']['interval'],[-1.0,1.0])

    def test_definite_failures_keep_all_denominators_without_correct_ties(self):
        spec=self.spec();plan=comparison.compile_comparison(spec)
        results={c['cell_id']:self.row(spec,c,status='contract_failed') for c in plan['cells']}
        report=comparison.build_report(plan,results)
        for stats in report['arms'].values():
            self.assertEqual(stats['planned'],8)
            self.assertEqual(stats['statuses']['contract_failed'],8)
            self.assertEqual(stats['completion_bounds']['definite_noncompletions'],8)
            self.assertEqual(stats['completion_bounds']['observed_successes'],0)
            self.assertEqual(stats['outcomes']['full_repair'],0)
        for contrast in report['contrasts'].values():
            self.assertEqual(contrast['conditional_quality']['tie'],0)
            self.assertEqual(contrast['e2e_pair_states']['both_noncompletion'],8)
            self.assertEqual(contrast['task_cluster']['point_difference'],0.0)
            self.assertGreater(contrast['uncertainty']['interval'][1],0)
        self.assertFalse(report['semantic_truth_validated'])

    def test_repeat_aggregation_preserves_task_count_and_uncertainty_width(self):
        reports=[]
        for repeats in (1,4):
            spec=self.spec(repeats=repeats);plan=comparison.compile_comparison(spec)
            results={c['cell_id']:self.row(spec,c,outcome='partial' if c['arm']=='api_debugger' else 'full_repair')
                     for c in plan['cells']}
            reports.append(comparison.build_report(plan,results))
        for report in reports:
            contrast=report['contrasts']['api_debugger_vs_api_test_feedback']
            self.assertEqual(contrast['task_cluster']['independent_tasks_assumed'],8)
            self.assertEqual(contrast['task_cluster']['point_difference'],-1.0)
            self.assertFalse(report['full_repair_rate_established'])
        self.assertEqual(reports[0]['contrasts']['api_debugger_vs_api_test_feedback']['uncertainty']['interval'],
                         reports[1]['contrasts']['api_debugger_vs_api_test_feedback']['uncertainty']['interval'])
        self.assertEqual(reports[1]['arms']['api_test_feedback']['outcomes']['full_repair'],32)

    def test_unknown_outcome_retains_reserve_and_prohibits_later_dispatch(self):
        spec=self.spec();plan=comparison.compile_comparison(spec);first=plan['cells'][0]
        unknown=self.row(spec,first,status='unknown');unknown['unknown_reserved_usd']='0.030000'
        report=comparison.build_report(plan,{first['cell_id']:unknown})
        self.assertTrue(report['must_stop']);self.assertEqual(report['unknown_reserved_usd'],'0.030000')
        later=plan['cells'][1]
        with self.assertRaisesRegex(ValueError,'comparison_dispatch_after_stop'):
            comparison.build_report(plan,{first['cell_id']:unknown,later['cell_id']:self.row(spec,later)})

    def test_simulated_or_unattested_rows_cannot_earn_completion_credit(self):
        spec=self.spec();plan=comparison.compile_comparison(spec);cell=plan['cells'][0]
        row=self.row(spec,cell);row['candidate_origin']='simulation'
        with self.assertRaises(ValueError):comparison.build_report(plan,{cell['cell_id']:row})
        row['review']=None
        report=comparison.build_report(plan,{cell['cell_id']:row})
        stats=report['arms'][cell['arm']]
        self.assertEqual(stats['completion_bounds']['observed_successes'],0)
        self.assertEqual(stats['completion_bounds']['unresolved'],8)


class RepairComparisonTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(comparison, 'fair repair comparison contract missing')
        self.spec = json.loads(EXAMPLE.read_text())

    def plan(self):
        return comparison.compile_comparison(self.spec)

    def row(self, cell, *, outcome='full_repair', verdict='defect'):
        row = {'status': 'answer_recorded', 'candidate_origin': 'model',
            'verification_origin': 'isolated_execution', 'verdict': verdict,
            'initial_context_sha256': self.spec['tasks'][0]['initial_context_sha256'],
            'model_configuration_sha256': self.spec['model']['configuration_sha256'],
            'output_contract_sha256': self.spec['model']['output_contract_sha256'],
            'model_identity_sha256': 'a' * 64, 'requests': 1,
            'feedback_validation_attempts': 0,
            'final_output_sha256': 'b' * 64,
            'candidate_sha256': 'c' * 64 if verdict == 'defect' else None,
            'verification_receipt_sha256': 'd' * 64,
            'input_tokens': 40, 'output_tokens': 20,
            'known_usd': '0.001000', 'unknown_reserved_usd': '0.000000',
            'timing_ms': {'preparation': 2, 'provider': 3, 'verification': 4, 'total': 9},
            'review': None}
        review = {'status': 'locked', 'source': 'independent_custodian',
            'rubric_sha256': self.spec['scoring']['rubric_sha256'],
            'hidden_tests_sha256': self.spec['scoring']['hidden_tests_sha256'],
            'result_sha256': comparison.result_digest(row), 'outcome': outcome}
        row['review'] = review
        return row

    def test_compile_freezes_equal_opportunities_and_keeps_live_disabled(self):
        plan = self.plan()
        self.assertEqual(plan['planned_cells'], 2)
        self.assertEqual(plan['max_requests'], 6)
        self.assertEqual(plan['max_usd'], '0.200000')
        self.assertEqual({cell['arm'] for cell in plan['cells']}, {'direct_api', 'api_debugger'})
        self.assertFalse(plan['remote_enabled'])
        self.assertFalse(plan['execution_authorized'])
        self.assertEqual(comparison.validate_comparison(plan), plan)
        self.spec['limits']['max_requests'] = 2
        self.assertEqual(plan['spec']['limits']['max_requests'], 3)

    def test_rejects_unequal_arm_budgets_hidden_inputs_and_changed_frozen_plan(self):
        for key, value in [('arms', {'direct_api': {'max_requests': 1}}),
                           ('hidden_tests_path', '/private/gold.py')]:
            with self.subTest(key=key):
                bad = deepcopy(self.spec); bad[key] = value
                with self.assertRaises(ValueError): comparison.compile_comparison(bad)
        bad = deepcopy(self.spec); bad['scoring']['generation_access'] = 'allowed'
        with self.assertRaises(ValueError): comparison.compile_comparison(bad)
        plan = self.plan(); plan['cells'].reverse()
        with self.assertRaises(ValueError): comparison.validate_comparison(plan)

    def test_order_balances_positions_without_counting_repeats_as_new_tasks(self):
        self.spec['repeats'] = 2
        plan = self.plan()
        self.assertEqual([c['arm'] for c in plan['cells'][::2]], ['direct_api', 'api_debugger'])
        report = comparison.build_report(plan, {})
        self.assertEqual(report['independent_tasks'], 1)
        self.assertEqual(report['planned_pairs'], 2)
        self.assertEqual(report['pairs']['not_comparable'], 2)
        self.assertEqual(report['pairs']['tie'], 0)

    def test_full_denominator_missing_and_manual_simulation_unavailable_never_gain(self):
        plan = self.plan(); left, right = plan['cells']
        manual = self.row(left); manual.update(candidate_origin='human', review=None)
        simulated = self.row(right); simulated.update(verification_origin='simulation', review=None)
        report = comparison.build_report(plan, {left['cell_id']: manual, right['cell_id']: simulated})
        self.assertEqual(report['arms']['direct_api']['planned'], 1)
        self.assertEqual(report['arms']['direct_api']['scored'], 0)
        self.assertEqual(report['pairs']['not_comparable'], 1)
        simulated['verification_origin'] = 'unavailable'
        report = comparison.build_report(plan, {right['cell_id']: simulated})
        self.assertEqual(report['arms']['direct_api']['statuses']['not_attempted'], 1)
        self.assertEqual(report['arms']['api_debugger']['unscored'], 1)
        forged = self.row(right); forged['verification_origin'] = 'simulation'
        with self.assertRaises(ValueError): comparison.build_report(plan, {right['cell_id']: forged})

    def test_configured_http_is_unattested_and_cannot_earn_locked_success_credit(self):
        plan=self.plan();cell=plan['cells'][0]
        row=self.row(cell);row.update(candidate_origin='provider_unattested',review=None)
        report=comparison.build_report(plan,{cell['cell_id']:row})
        self.assertEqual(report['arms'][cell['arm']]['scored'],0)
        self.assertEqual(report['arms'][cell['arm']]['unscored'],1)
        row['review']=self.row(cell)['review']
        with self.assertRaises(ValueError):comparison.build_report(plan,{cell['cell_id']:row})

    def test_cost_tokens_and_times_report_actual_use_rather_than_equal_ceiling(self):
        plan = self.plan(); left, right = plan['cells']
        one, three = self.row(left), self.row(right)
        three.update(requests=3, input_tokens=120, output_tokens=60, known_usd='0.003000',
                     feedback_validation_attempts=2, review=None)
        report = comparison.build_report(plan, {left['cell_id']: one, right['cell_id']: three})
        self.assertEqual(report['arms']['direct_api']['requests'], 1)
        self.assertEqual(report['arms']['api_debugger']['requests'], 3)
        self.assertEqual(report['actual_known_usd'], '0.004000')
        self.assertEqual(report['arms']['api_debugger']['input_tokens'], 120)
        self.assertEqual(report['arms']['api_debugger']['feedback_validation_attempts'], 2)
        self.assertEqual(report['arms']['api_debugger']['timing_ms']['total'], 9)
        self.assertFalse(report['equal_ceilings_prove_equal_consumption'])
        self.assertFalse(report['orchestration_only_gain_established'])

    def test_actual_model_contract_initial_context_and_baseline_feedback_cannot_drift(self):
        plan = self.plan(); cell = plan['cells'][0]
        for key, value in [('model_configuration_sha256', 'f' * 64),
                           ('output_contract_sha256', 'f' * 64),
                           ('initial_context_sha256', 'f' * 64),
                           ('feedback_validation_attempts', 1)]:
            row = self.row(cell); row.update({key: value, 'review': None})
            with self.subTest(key=key), self.assertRaises(ValueError):
                comparison.build_report(plan, {cell['cell_id']: row})

    def test_missing_identity_cannot_continue_even_when_no_candidate_was_produced(self):
        plan = self.plan(); left, right = plan['cells']
        failed = self.row(left)
        failed.update(status='transport_failed', candidate_origin='none',
                      verification_origin='none', model_identity_sha256=None,
                      verdict=None, review=None)
        report = comparison.build_report(plan, {left['cell_id']: failed})
        self.assertTrue(report['must_stop'])
        with self.assertRaises(ValueError):
            comparison.build_report(plan, {left['cell_id']: failed, right['cell_id']: self.row(right)})

    def test_unknown_preserves_reserve_and_prohibits_later_dispatch(self):
        plan = self.plan(); left, right = plan['cells']
        unknown = self.row(left)
        unknown.update(status='unknown', review=None, verdict=None,
                       unknown_reserved_usd='0.030000')
        report = comparison.build_report(plan, {left['cell_id']: unknown})
        self.assertTrue(report['must_stop'])
        self.assertFalse(report['automatic_retry_allowed'])
        self.assertEqual(report['unknown_reserved_usd'], '0.030000')
        with self.assertRaises(ValueError):
            comparison.build_report(plan, {left['cell_id']: unknown, right['cell_id']: self.row(right)})

    def test_refuses_budget_overrun_unknown_cost_disguised_as_known_and_bad_timing(self):
        plan = self.plan(); cell = plan['cells'][0]
        for field, value in [('requests', 4), ('input_tokens', 10001),
                ('output_tokens', 2001), ('known_usd', '0.100001'),
                ('unknown_reserved_usd', '0.010000')]:
            row = self.row(cell); row.update({field: value, 'review': None})
            with self.subTest(field=field), self.assertRaises(ValueError):
                comparison.build_report(plan, {cell['cell_id']: row})
        row = self.row(cell); row['review'] = None; row['timing_ms']['total'] = 8
        with self.assertRaises(ValueError): comparison.build_report(plan, {cell['cell_id']: row})

    def test_settled_budget_failures_keep_actual_consumption_and_stop_without_credit(self):
        plan = self.plan(); cell, later = plan['cells']
        for field, value in [('requests', 4), ('input_tokens', 10001),
                ('output_tokens', 2001), ('known_usd', '0.100001')]:
            row = self.row(cell)
            row.update(status='budget_exceeded', review=None, verdict=None,
                       final_output_sha256=None, candidate_sha256=None)
            row[field] = value
            with self.subTest(field=field):
                report = comparison.build_report(plan, {cell['cell_id']: row})
                stats = report['arms'][cell['arm']]
                self.assertEqual(stats[field], value)
                self.assertEqual(stats['statuses']['budget_exceeded'], 1)
                self.assertEqual((stats['scored'], stats['unscored']), (0, 1))
                self.assertEqual(report['planned_cells'], 2)
                self.assertEqual(report['pairs']['not_comparable'], 1)
                self.assertTrue(report['must_stop'])
                with self.assertRaisesRegex(ValueError, 'comparison_dispatch_after_stop'):
                    comparison.build_report(plan, {cell['cell_id']: row,
                        later['cell_id']: self.row(later)})

    def test_budget_failure_cannot_carry_success_review_or_unbounded_statistics(self):
        plan = self.plan(); cell = plan['cells'][0]
        row = self.row(cell)
        row.update(status='budget_exceeded', review=None, verdict=None,
                   final_output_sha256=None, candidate_sha256=None)
        for field, value in [('review', self.row(cell)['review']), ('verdict', 'defect'),
                ('candidate_sha256', 'c'*64), ('final_output_sha256', 'b'*64),
                ('unknown_reserved_usd', '0.030000'), ('requests', 0),
                ('input_tokens', True), ('output_tokens', -1), ('requests', 10**12+1)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                comparison.build_report(plan, {cell['cell_id']: dict(row, **{field:value})})

    def test_locked_review_binds_raw_result_rubric_actual_verdict_and_identity(self):
        plan = self.plan(); left, right = plan['cells']
        rows = {left['cell_id']: self.row(left), right['cell_id']: self.row(right)}
        report = comparison.build_report(plan, rows)
        self.assertEqual(report['pairs']['tie'], 1)
        self.assertEqual(report['arms']['direct_api']['outcomes']['full_repair'], 1)
        for mutate in [lambda r: r.update(known_usd='0.002000'),
                       lambda r: r['review'].update(rubric_sha256='f' * 64),
                       lambda r: r['review'].update(outcome='necessary_abstention')]:
            bad = deepcopy(rows); mutate(bad[left['cell_id']])
            with self.assertRaises(ValueError): comparison.build_report(plan, bad)
        rows[right['cell_id']]['model_identity_sha256'] = 'b' * 64
        rows[right['cell_id']]['review']['result_sha256'] = comparison.result_digest(
            dict(rows[right['cell_id']], review=None))
        report = comparison.build_report(plan, rows)
        self.assertTrue(report['must_stop'])
        self.assertEqual(report['pairs']['not_comparable'], 1)
        self.assertEqual(report['arms']['api_debugger']['scored'], 0)
        self.assertEqual(report['arms']['api_debugger']['outcomes']['full_repair'], 0)
        self.assertEqual(report['arms']['api_debugger']['identity_invalid'], 1)
        self.assertEqual(report['arms']['api_debugger']['excluded_locked_reviews'], 1)

    def test_score_cannot_survive_replaced_output_candidate_or_final_verification_receipt(self):
        plan = self.plan(); cell = plan['cells'][0]
        for field in ('final_output_sha256', 'candidate_sha256', 'verification_receipt_sha256'):
            row = self.row(cell); row[field] = 'f' * 64
            with self.subTest(field=field), self.assertRaises(ValueError):
                comparison.build_report(plan, {cell['cell_id']: row})
        row = self.row(cell); row['candidate_sha256'] = None
        row['review']['result_sha256'] = comparison.result_digest(dict(row, review=None))
        with self.assertRaises(ValueError):
            comparison.build_report(plan, {cell['cell_id']: row})

    def test_abstention_false_positive_and_failure_retained_without_semantic_inference(self):
        plan = self.plan(); left, right = plan['cells']
        one = self.row(left, outcome='false_positive', verdict='defect')
        other = self.row(right, outcome='necessary_abstention', verdict='unknown')
        report = comparison.build_report(plan, {left['cell_id']: one, right['cell_id']: other})
        self.assertEqual(report['pairs']['api_debugger_win'], 1)
        self.assertEqual(report['arms']['direct_api']['outcomes']['false_positive'], 1)
        self.assertEqual(report['arms']['api_debugger']['outcomes']['necessary_abstention'], 1)
        other.update(status='contract_failed', review=None, verdict=None)
        report = comparison.build_report(plan, {right['cell_id']: other})
        self.assertEqual(report['arms']['api_debugger']['statuses']['contract_failed'], 1)
        self.assertEqual(report['pairs']['not_comparable'], 1)
        self.assertFalse(report['semantic_truth_validated'])


if __name__ == '__main__':
    unittest.main()
