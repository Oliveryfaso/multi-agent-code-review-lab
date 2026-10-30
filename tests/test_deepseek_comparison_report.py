"""Synthetic selected-cell reporting; no gold or model-quality oracle."""
from copy import deepcopy
import importlib
import unittest

from macr.evals.deepseek_study_report import result_digest

try:
    report=importlib.import_module('macr.evals.deepseek_comparison_report')
except ModuleNotFoundError:
    report=None

V2='study-8192-v2'
NEW='study-evidence-v1'


def fixture(phase='U'):
    tasks=[{'id':f't{i}','question':'Does this supplied code meet its contract?',
        'scenario':'Only the stated inputs are allowed.','assumptions':['Integer inputs.'],
        'source':[{'file':'unseen.py','start':1,'end':1,'body':'PRIVATE_FULL_SNAPSHOT'}]}
        for i in range(7 if phase=='R' else 9)]
    profiles=[V2] if phase=='R' else [V2,NEW]
    cells=[{'cell_id':f'{task["id"]}-{index}','task_id':task['id'],
        'strategy':'adaptive_read_3','profile':profile,'study_root':'/fixture/'+profile}
        for task in tasks for index,profile in enumerate(profiles)]
    plan={'phase':phase,'plan_sha256':'a'*64,'tasks':tasks,'cells':cells}
    results={cell['cell_id']:answer(cell,elapsed=(int(cell['task_id'][1:])+1)) for cell in cells}
    return plan,results,accounting(results)


def answer(cell,elapsed=1):
    return {'task_id':cell['task_id'],'strategy':cell['strategy'],
        'status':'semantic_pending','error_code':None,'review_status':'pending',
        'answer':{'verdict':'unknown','cause':None,'when':None,'refs':[],
            'unknowns':['A specific external fact is not supplied.']},
        'first_answer':None,'gap':None,'tool_reads':1,'elapsed_seconds':elapsed,
        'timing_seconds':{'preparation':.1,'provider':elapsed-.1,'tool':0.},
        'source':[{'file':'seen.py','start':1,'end':1,'body':'return x\n','extra':'drop'}],
        'requests':[{'response':{'state':'received','model':'deepseek-flash',
            'system_fingerprint':'known-fixture-identity','finish_reason':'stop',
            'raw_usage':{'prompt_tokens':10,'completion_tokens':2,
                'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':10,
                'completion_tokens_details':{'reasoning_tokens':1}}}}],
        'accounting':{'request_count':1,'known_accrued_usd':'0.000006','unknown_held_usd':'0.000000'}}


def accounting(results):
    return {'state':'open','request_count':sum(len(r['requests']) for r in results.values()),
        'known_accrued_usd':f'0.{len(results)*6:06d}','unknown_held_usd':'0.000000'}


class PilotReportTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(report,'selected-cell comparison reporting is not implemented')

    def test_r_machine_gate_uses_seven_terminal_cells_not_scripted_semantic_credit(self):
        plan,results,_=fixture('R')
        for cell in plan['cells'][-2:]:
            result=results[cell['cell_id']]
            result.update(status='failed',error_code='model_output_incomplete',answer=None)
            result['requests'][0]['response']['finish_reason']='length'
        actual=report.build_pilot_report(plan,results,accounting(results))
        self.assertEqual(actual['planned_cells'],7)
        self.assertEqual(len(actual['rows']),7)
        self.assertTrue(actual['r_gate']['eligible'])
        self.assertEqual(actual['r_gate']['reviewable_cells'],5)
        self.assertEqual(actual['r_gate']['truncated_cells'],2)
        self.assertEqual(actual['semantic_scoring'],'pending_independent_review')
        self.assertNotIn('accuracy',actual)

    def test_r_missing_terminal_or_halt_or_unknown_never_promotes(self):
        plan,results,ledger=fixture('R')
        for variant in ('missing','halted','unknown'):
            data=deepcopy(results);state=deepcopy(ledger)
            if variant=='missing':data.pop(plan['cells'][0]['cell_id'])
            if variant=='unknown':state['unknown_held_usd']='0.330000'
            actual=report.build_pilot_report(plan,data,state,halted=variant=='halted')
            self.assertFalse(actual['r_gate']['eligible'])
            self.assertEqual(actual['planned_cells'],7)

    def test_r_identity_and_measured_overrun_block_even_with_five_reviewable(self):
        for code in ('system_fingerprint_unknown','system_fingerprint_drift','measured_budget_overrun'):
            plan,results,ledger=fixture('R')
            result=results[plan['cells'][0]['cell_id']]
            result.update(status='failed',error_code=code)
            actual=report.build_pilot_report(plan,results,ledger)
            self.assertFalse(actual['r_gate']['eligible'])
            self.assertTrue(actual['r_gate']['operational_anomaly'])

    def test_u_preserves_eighteen_cells_and_nine_pending_pairs_never_ties(self):
        plan,results,ledger=fixture()
        actual=report.build_pilot_report(plan,results,ledger)
        self.assertEqual(actual['planned_cells'],18)
        self.assertEqual(len(actual['pairs']),9)
        self.assertTrue(all(row['status']=='semantic_pending' for row in actual['rows']))
        self.assertTrue(all(p['outcome']=='pending' for p in actual['pairs']))
        for profile in (V2,NEW):
            group=actual['profiles'][profile]
            self.assertEqual(group['planned_cells'],9)
            self.assertEqual(group['actual']['requests'],9)
            self.assertEqual(group['actual']['input_tokens'],90)
            self.assertEqual(group['actual']['output_tokens'],18)
            self.assertEqual(group['actual']['reasoning_tokens'],9)
            self.assertEqual(group['actual']['known_accrued_usd'],'0.000054')
            self.assertEqual(group['latency_seconds'],{'count':9,'median':5,'min':1,'max':9})

    def test_u_missing_identity_and_operational_failure_are_incomparable(self):
        plan,results,ledger=fixture()
        results.pop('t0-0')
        results['t1-0'].update(status='failed',error_code='system_fingerprint_unknown')
        results['t2-0'].update(status='failed',error_code='measured_budget_overrun')
        results['t3-0'].update(status='failed',error_code='model_output_invalid')
        actual=report.build_pilot_report(plan,results,ledger,halted=True)
        by_task={p['task_id']:p for p in actual['pairs']}
        missing=next(row for row in actual['rows'] if row['cell_id']=='t0-0')
        self.assertEqual(missing['status'],'not_attempted')
        self.assertTrue(all(by_task[t]['outcome']=='not_comparable' for t in ('t0','t1','t2')))
        self.assertEqual(by_task['t3']['outcome'],'pending')
        self.assertTrue(by_task['t3']['contains_known_contract_failure'])

    def test_request_length_denominator_is_separate_from_truncated_cells(self):
        plan,results,_=fixture()
        result=results['t0-0']
        result['requests']=[deepcopy(result['requests'][0]) for _ in range(3)]
        for req in result['requests'][:2]:req['response']['finish_reason']='length'
        result['accounting'].update(request_count=3)
        actual=report.build_pilot_report(plan,results,accounting(results))
        self.assertEqual(actual['length_requests'],{'numerator':2,'denominator':20})
        self.assertEqual(actual['truncated_cells'],{'numerator':1,'denominator':18})

    def test_u_different_verified_fingerprints_make_the_affected_pair_incomparable(self):
        plan,results,ledger=fixture()
        results['t0-1']['requests'][0]['response']['system_fingerprint']='different-fixture-identity'
        actual=report.build_pilot_report(plan,results,ledger,halted=True)
        pairs={p['task_id']:p for p in actual['pairs']}
        self.assertEqual(pairs['t0']['outcome'],'not_comparable')
        self.assertEqual(pairs['t1']['outcome'],'pending')

    def test_r_missing_cell_cost_receipt_is_not_known_metering(self):
        plan,results,ledger=fixture('R')
        results['t0-0']['accounting'].pop('known_accrued_usd')
        actual=report.build_pilot_report(plan,results,ledger)
        self.assertFalse(actual['r_gate']['eligible'])
        self.assertTrue(actual['r_gate']['operational_anomaly'])

    def test_r_global_cost_and_hold_must_equal_individual_cell_receipts(self):
        for variant in ('lower_cost','higher_cost','hidden_hold','missing_hold'):
            with self.subTest(variant=variant):
                plan,results,ledger=fixture('R')
                if variant=='lower_cost':ledger['known_accrued_usd']='0.000000'
                elif variant=='higher_cost':ledger['known_accrued_usd']='0.000048'
                elif variant=='hidden_hold':
                    results['t0-0']['accounting']['unknown_held_usd']='0.330000'
                else:results['t0-0']['accounting'].pop('unknown_held_usd')
                actual=report.build_pilot_report(plan,results,ledger)
                self.assertFalse(actual['r_gate']['eligible'])
                self.assertTrue(actual['r_gate']['operational_anomaly'])

    def test_rows_bind_the_exact_accounted_result_and_missing_result_has_no_digest(self):
        plan,results,ledger=fixture()
        results.pop('t0-1')
        actual=report.build_pilot_report(plan,results,ledger)
        rows={r['cell_id']:r for r in actual['rows']}
        self.assertEqual(rows['t0-0']['result_sha256'],result_digest(results['t0-0']))
        self.assertIsNone(rows['t0-1']['result_sha256'])
        changed=deepcopy(results);changed['t0-0']['answer']['unknowns']=['Different unchanged-output receipt.']
        second=report.build_pilot_report(plan,changed,ledger)
        self.assertNotEqual(rows['t0-0']['result_sha256'],second['rows'][0]['result_sha256'])

    def test_review_export_is_bound_and_hides_labels_costs_and_unseen_task_source(self):
        plan,results,_=fixture()
        selected={'t0-0':results['t0-0']}
        exported=report.export_pilot_review_packets(plan,selected)
        self.assertEqual(len(exported['packets']),1)
        packet=exported['packets'][0]
        self.assertEqual(packet['result_sha256'],result_digest(selected['t0-0']))
        self.assertEqual(set(packet),{'review_id','result_sha256','question','scenario',
            'assumptions','source','answer','first_answer','gap'})
        self.assertEqual(packet['source'],[{'file':'seen.py','start':1,'end':1,'body':'return x\n'}])
        self.assertNotIn('PRIVATE_FULL_SNAPSHOT',str(packet))
        self.assertEqual(exported['keeper_mapping'],{packet['review_id']:'t0-0'})

    def test_export_uses_only_supplied_visible_sources_without_task_fallback(self):
        plan,results,_=fixture()
        first=results['t0-0'];first.pop('source')
        first['review_evidence']={'source':[{'file':'visible.py','start':2,'end':2,'body':'x=1'}]}
        second=results['t0-1'];second.pop('source')
        exported=report.export_pilot_review_packets(plan,{'t0-0':first,'t0-1':second})
        packets=exported['packets']
        self.assertEqual(packets[0]['source'][0]['file'],'visible.py')
        self.assertEqual(packets[1]['source'],[])

    def test_unselected_result_duplicate_cell_or_wrong_identity_is_rejected(self):
        plan,results,ledger=fixture()
        for variant in ('extra','duplicate','identity'):
            frozen=deepcopy(plan);data=deepcopy(results)
            if variant=='extra':data['not-selected']=data['t0-0']
            elif variant=='duplicate':frozen['cells'][1]=deepcopy(frozen['cells'][0])
            else:data['t0-0']['task_id']='different-task'
            with self.assertRaises(ValueError):report.build_pilot_report(frozen,data,ledger)

    def test_report_and_export_do_not_mutate_inputs(self):
        plan,results,ledger=fixture();saved=deepcopy((plan,results,ledger))
        report.build_pilot_report(plan,results,ledger)
        report.export_pilot_review_packets(plan,results)
        self.assertEqual((plan,results,ledger),saved)
