"""One-time continuation uses public synthetic receipts, never study questions."""
import importlib
import importlib.util
from hashlib import sha256
import json
from pathlib import Path
import shutil
import threading
import unittest
from unittest.mock import patch

from debugger_fixtures import fixture_root
from test_deepseek_policy_runner import UNKNOWN, call, reply
from test_deepseek_study_profile import development_pack, COMMITMENT
from macr.evals.deepseek_policy_runner import _json_bytes, PolicyRunError
from macr.evals.deepseek_policy_spend import _normalized_charge, _usd
from macr.evals.deepseek_study_profile import prepare_study, digest, StudyError, read_json

PROJECT = Path(__file__).resolve().parents[1]


def source_fixture():
    """Self-authored halted 36-cell study with seven settled synthetic receipts."""
    pack = fixture_root()
    development_pack(pack)
    source = pack.parent / (pack.name + '-legacy-control')
    study = prepare_study(pack, custodian=COMMITMENT, out=source, project_root=PROJECT)
    study['implementation'] = {'historical-code-version': 'c' * 64}
    for cell in study['cells']:
        cell['cell_id'] = digest({'old': cell['cell_id']})[:32]
    study['profile_sha256'] = digest({k:v for k,v in study.items() if k != 'profile_sha256'})
    files = ['study.json'] + [f"manifests/{task['id']}/manifest.json" for task in study['tasks']]

    def write(name, value):
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_json_bytes(value) + b'\n')
        if name not in files:
            files.append(name)

    write('study.json', study)
    states = [('semantic_pending',None), ('failed','model_output_incomplete'),
              ('failed','system_fingerprint_unknown')]
    rows, number = [], 0
    for index, cell in enumerate(study['cells']):
        if index >= 3:
            rows.append(cell | {'status':'not_attempted'})
            continue
        requests, costs = [], []
        for decision in range(1, (1 if index == 1 else 3) + 1):
            number += 1
            usage = {'prompt_tokens':1000, 'completion_tokens':800 if number < 7 else 283,
                'prompt_cache_hit_tokens':0, 'prompt_cache_miss_tokens':1000,
                'cache_basis':'reported', 'reasoning_tokens':2}
            cost = _normalized_charge(usage, 2000)
            costs.append(cost)
            request_hash = digest({'fixture-request': number})
            reservation = {'number':number, 'cell_id':cell['cell_id'],
                'request_sha256':request_hash, 'reserved_micro_usd':330000,
                'state':'reserved_unknown', 'usage':None}
            write(f'ledger/{number:03d}.reservation.json', reservation)
            write(f'ledger/{number:03d}.outcome.json', reservation |
                {'state':'settled','usage':usage,'charged_micro_usd':cost})
            response = {'state':'received','model':'deepseek-flash',
                'system_fingerprint':'fixture-fingerprint','raw_usage':None,'usage':None,
                'assistant_message':None,'finish_reason':'stop','latency_ms':0}
            if number == 7:
                response.update(state='unknown',model=None,system_fingerprint=None,
                    finish_reason=None,latency_ms=None)
            requests.append({'decision':decision,'request_sha256':request_hash,
                'request':{},'response':response,'error_code':None})
            folder = f"cells/{cell['cell_id']}"
            for suffix, value in (('response',response), ('request',{}),
                    ('reservation', {'request_sha256':request_hash}), ('outcome',{})):
                write(f'{folder}/decision-{decision:02d}.{suffix}.json', value)
        status, error = states[index]
        result = {'schema':'macr.deepseek.study.cell.v1','cell_id':cell['cell_id'],
            'task_id':cell['task_id'],'strategy':cell['strategy'],'status':status,
            'error_code':error,'answer':UNKNOWN if status=='semantic_pending' else None,
            'review_status':'pending' if status=='semantic_pending' else 'not_ready',
            'requests':requests,'requests_attempted':len(requests),'tool_reads':0,
            'elapsed_seconds':0.,'timing_seconds':{'preparation':0.,'provider':0.,'tool':0.},
            'model_calls_live':len(requests),'accounting':{'request_count':len(requests),
                'known_accrued_usd':_usd(sum(costs)), 'unknown_held_usd':'0.000000'}}
        write(f"cells/{cell['cell_id']}.accounted.json", result)
        write(f"cells/{cell['cell_id']}/result.json", result)
        write(f"cells/{cell['cell_id']}/claim.json", {'fixture':True})
        rows.append(cell | {'status':status,'error_code':error,'result_sha256':digest(result)})
    summary = {'schema':'macr.deepseek.study.execution.v1',
        'profile_sha256':study['profile_sha256'],'provenance':'approved_live',
        'planned_cells':36,'planned_request_ceiling':108,'rows':rows,
        'accounting':{'state':'open','request_count':7,'known_accrued_usd':'0.008200',
            'unknown_held_usd':'0.000000','full_miss_sensitivity_usd':'0.010307',
            'basis':'provider_usage_peak_local_accrual_not_supplier_bill','model_calls_live':7},
        'model_calls_live':7,'semantic_scoring':'pending_independent_review','batch_halted':True}
    write('summary.json', summary)
    for name in ('retention.json','execution.claim.json','paired-report.json'):
        write(name, {'fixture':True})
    while len(files) < 72:
        write(f'approval-{len(files):02d}.json', {'fixture':True})
    freeze_rows = [{'path':name,'bytes':len((source/name).read_bytes()),
        'sha256':sha256((source/name).read_bytes()).hexdigest()} for name in sorted(files)]
    freeze = {'schema':'macr.deepseek.study.terminal-freeze.v1',
        'profile_sha256':study['profile_sha256'],'files':freeze_rows,
        'files_sha256':digest(freeze_rows),'state':'halted_identity_evidence_unavailable_no_retry'}
    (source/'terminal-artifact-freeze.json').write_bytes(_json_bytes(freeze)+b'\n')
    return source, study, freeze


class StudyContinuationTests(unittest.TestCase):
    def continuation(self):
        self.assertIsNotNone(importlib.util.find_spec('macr.evals.deepseek_study_continuation'),
            'separate one-time continuation entry is missing')
        module = importlib.import_module('macr.evals.deepseek_study_continuation')
        for name in ('prepare_continuation', 'propose_continuation_approval', 'execute_continuation'):
            self.assertTrue(callable(getattr(module, name, None)), name)
        return module

    def test_separate_continuation_entry_exists(self):
        self.continuation()

    def test_pinned_terminal_shape_is_three_then_one_then_three_requests(self):
        module, source, old, freeze, out, plan = self.prepare()
        verified=module._source(source)
        observed=[(result['status'],result['error_code'],result['requests_attempted'])
                  for result in verified['results'].values()]
        self.assertEqual(observed,[('semantic_pending',None,3),
            ('failed','model_output_incomplete',1),('failed','system_fingerprint_unknown',3)])
        self.assertEqual(sum(result['requests_attempted'] for result in verified['results'].values()),7)
        self.assertEqual(sum(_normalized_charge(read_json(source,
            f'ledger/{number:03d}.outcome.json')['usage'],2000) for number in range(1,8)),8200)

    def prepare(self):
        module = self.continuation()
        source, old, freeze = source_fixture()
        for name, value in (('SOURCE_PROFILE_SHA',old['profile_sha256']),
                            ('SOURCE_FILES_SHA',freeze['files_sha256'])):
            patched = patch.object(module, name, value, create=True)
            patched.start()
            self.addCleanup(patched.stop)
        out = source.parent / (source.name + '-continuation')
        plan = module.prepare_continuation(source, out=out, project_root=PROJECT)
        return module, source, old, freeze, out, plan

    def scripts(self, plan, *, first=None):
        result = {cell['cell_id']: ((reply(None,calls=[call('end','finish',
            {'diagnosis':UNKNOWN})]),) if cell['strategy']=='adaptive_read_3' else
            tuple(reply(json.dumps(UNKNOWN)) for _ in range(3))) for cell in plan['remaining_cells']}
        if first is not None:
            result[plan['remaining_cells'][0]['cell_id']] = (first,)
        return result

    def approve(self, module, out):
        return module.propose_continuation_approval(out) | {
            'authorized':True,'confirmation_ref':'fixture-explicit-continuation-consent'}

    def test_prepare_freezes_exact_remaining_33_and_preserves_source(self):
        module, source, old, freeze, out, plan = self.prepare()
        self.assertEqual(len(plan['cell_mapping']),36)
        self.assertEqual([row['logical_cell_id'] for row in plan['cell_mapping']],
            [row['cell_id'] for row in old['cells']])
        self.assertEqual([row['logical_cell_id'] for row in plan['cell_mapping'][3:]],
            [row['cell_id'] for row in old['cells'][3:]])
        self.assertEqual([row['cell_id'] for row in plan['remaining_cells']],
            [row['code_cell_id'] for row in plan['cell_mapping'][3:]])
        self.assertEqual(len(plan['remaining_cells']),33)
        self.assertEqual(plan['source_profile_sha256'],old['profile_sha256'])
        self.assertEqual(plan['source_files_sha256'],freeze['files_sha256'])
        new = read_json(out,'study.json')
        self.assertEqual(len(new['tasks']),9)
        self.assertEqual(len(new['cells']),36)
        self.assertEqual(new['budget'],old['budget'])
        self.assertEqual(new['order_seed'],old['order_seed'])
        self.assertEqual(new['custodian'],COMMITMENT)
        self.assertEqual(plan['opening'],{'request_count':7,'known_accrued_usd':'0.008200',
            'unknown_held_usd':'0.000000'})
        self.assertEqual(plan['max_new_requests'],99)
        self.assertEqual(plan['remaining_usd'],'1.991800')
        self.assertEqual(plan['historical_pin']['system_fingerprint'],'fixture-fingerprint')
        self.assertEqual(plan['historical_pin']['validated_requests'],6)
        self.assertEqual(plan['historical_pin']['unverifiable_request'],7)
        for row in freeze['files']:
            self.assertEqual(sha256((source/row['path']).read_bytes()).hexdigest(),row['sha256'])
        self.assertFalse((out/'execution.claim.json').exists())

    def test_proposal_binds_cumulative_budget_and_accepted_gap_without_authorizing(self):
        module, source, old, freeze, out, plan = self.prepare()
        proposal = module.propose_continuation_approval(out)
        self.assertFalse(proposal['authorized'])
        self.assertEqual(proposal['confirmation_ref'],'')
        self.assertEqual(proposal['max_requests'],108)
        self.assertEqual(proposal['max_usd'],'2.000000')
        self.assertEqual(proposal['max_new_requests'],99)
        self.assertEqual(proposal['remaining_usd'],'1.991800')
        self.assertEqual(proposal['opening'],plan['opening'])
        self.assertEqual(proposal['remaining_cells'],plan['remaining_cells'])
        self.assertEqual(proposal['source_files_sha256'],freeze['files_sha256'])
        self.assertTrue(proposal['historical_fingerprint_gap_accepted'])
        self.assertEqual(proposal['historical_pin'],plan['historical_pin'])
        self.assertEqual(proposal['automatic_retries'],0)
        self.assertEqual(proposal['log_policy']['retention_days'],14)
        self.assertFalse(proposal['resume'])
        self.assertFalse(proposal['ledger_reset'])

    def test_disabled_unapproved_and_changed_scope_do_not_claim_or_read_key(self):
        module, source, old, freeze, out, plan = self.prepare()
        approval = self.approve(module,out)
        key = lambda: self.fail('preflight requested a credential')
        with self.assertRaisesRegex(StudyError,'continuation_disabled'):
            module.execute_continuation(out,approval,key_reader=key)
        for proposed in (module.propose_continuation_approval(out),
                approval|{'max_requests':109}, approval|{'max_new_requests':100},
                approval|{'historical_fingerprint_gap_accepted':False},
                approval|{'remaining_cells':list(reversed(approval['remaining_cells']))},
                approval|{'historical_pin':{}}, approval|{'confirmation_ref':' '}):
            with self.assertRaises(StudyError):
                module.execute_continuation(out,proposed,enabled=True,key_reader=key)
        self.assertFalse((out/'execution.claim.json').exists())
        self.assertFalse((source.parent/'continuation-claims'/old['profile_sha256']).exists())

    def test_source_drift_missing_file_and_copied_source_are_rejected(self):
        module, source, old, freeze, out, plan = self.prepare()
        copied = source.parent/(source.name+'-copy')
        shutil.copytree(source,copied)
        with self.assertRaises(StudyError):
            module.prepare_continuation(copied,out=out.parent/'copied-preparation',project_root=PROJECT)
        target = source/'ledger/001.outcome.json'
        raw = target.read_bytes()
        target.write_bytes(raw+b' ')
        with self.assertRaises(StudyError):
            module.propose_continuation_approval(out)
        target.unlink()
        with self.assertRaises(ValueError):
            module.propose_continuation_approval(out)

    def test_freeze_must_match_pinned_digest_and_never_scan_source_siblings(self):
        module, source, old, freeze, out, plan = self.prepare()
        sentinel = source/'custodian-scores.json'
        sentinel.write_text('DO NOT OPEN THIS')
        from macr.evals import deepseek_study_continuation as product
        original = product._read
        opened = []
        def read(root,name,limit):
            if Path(root)==source:
                self.assertIn(name,{'terminal-artifact-freeze.json'}|{r['path'] for r in freeze['files']})
                opened.append(name)
            return original(root,name,limit)
        with patch.object(product,'_read',side_effect=read), \
                patch('os.scandir',side_effect=AssertionError('source scan')):
            # A source verification is independent of loading the neutral pack.
            product._source(source)
        self.assertNotIn(sentinel.name,opened)
        altered = dict(freeze)
        altered['files'] = list(reversed(freeze['files']))
        altered['files_sha256'] = digest(altered['files'])
        (source/'terminal-artifact-freeze.json').write_bytes(_json_bytes(altered))
        with self.assertRaises(StudyError):
            module.propose_continuation_approval(out)

    def test_fake_execution_claims_source_once_before_key_and_reports_original_36(self):
        module, source, old, freeze, out, plan = self.prepare()
        reads = []
        def key():
            self.assertTrue((out/'execution.claim.json').is_file())
            claim = source.parent/'continuation-claims'/f"{old['profile_sha256']}.json"
            self.assertTrue(claim.is_file())
            self.assertEqual(json.loads(claim.read_text())['control_root'],str(out))
            reads.append(1)
            return 'fixture-only-key'
        result = module.execute_continuation(out,self.approve(module,out),enabled=True,
            key_reader=key,scripted_by_cell=self.scripts(plan))
        self.assertEqual(reads,[1])
        self.assertEqual(result['planned_cells'],33)
        self.assertEqual(result['original_planned_cells'],36)
        self.assertEqual(result['planned_request_ceiling'],99)
        self.assertFalse(result['batch_halted'])
        self.assertEqual(result['accounting']['opening']['request_count'],7)
        self.assertEqual(result['accounting']['cumulative']['request_count'],
            result['accounting']['segment']['request_count']+7)
        report = read_json(out,'paired-report.json')
        self.assertEqual(len(report['cells']),36)
        self.assertEqual([r['cell_id'] for r in report['cells']],[c['cell_id'] for c in old['cells']])
        self.assertEqual(report['continuation']['historical_fingerprint_gap_accepted'],True)
        self.assertFalse(report['continuation']['all_request_fingerprint_continuity_verified'])
        self.assertEqual(report['continuation']['segments'][0]['processed_cells'],3)
        self.assertNotIn('fixture-only-key',(out/'summary.json').read_text())
        for row in freeze['files']:
            self.assertEqual(sha256((source/row['path']).read_bytes()).hexdigest(),row['sha256'])
        with self.assertRaisesRegex(StudyError,'continuation_already_claimed|execution_already_claimed'):
            module.execute_continuation(out,self.approve(module,out),enabled=True,
                key_reader=lambda: self.fail('second key read'),scripted_by_cell=self.scripts(plan))

    def test_second_prepared_directory_cannot_reset_cumulative_budget(self):
        module, source, old, freeze, out, plan = self.prepare()
        second = out.parent/(out.name+'-second')
        other = module.prepare_continuation(source,out=second,project_root=PROJECT)
        module.execute_continuation(out,self.approve(module,out),enabled=True,
            key_reader=lambda:'fixture-only-key',scripted_by_cell=self.scripts(plan,first='timeout'))
        with self.assertRaisesRegex(StudyError,'continuation_already_claimed'):
            module.execute_continuation(second,self.approve(module,second),enabled=True,
                key_reader=lambda:self.fail('second directory read key'),scripted_by_cell=self.scripts(other))
        self.assertFalse((second/'execution.claim.json').exists())

    def test_first_future_fingerprint_must_match_old_pin_and_missing_stops(self):
        for fingerprint in ('different-from-old',None):
            with self.subTest(fingerprint=fingerprint):
                module, source, old, freeze, out, plan = self.prepare()
                bad = reply(json.dumps(UNKNOWN))
                bad['system_fingerprint']=fingerprint
                result = module.execute_continuation(out,self.approve(module,out),enabled=True,
                    key_reader=lambda:'fixture-only-key',scripted_by_cell=self.scripts(plan,first=bad))
                self.assertTrue(result['batch_halted'])
                self.assertEqual(result['accounting']['segment']['request_count'],1)
                self.assertEqual(result['accounting']['cumulative']['request_count'],8)
                self.assertEqual(result['accounting']['cumulative']['unknown_held_usd'],'0.000000')
                self.assertEqual(result['rows'][0]['error_code'],
                    'system_fingerprint_unknown' if fingerprint is None else 'system_fingerprint_drift')
                self.assertEqual(sum(r['status']=='not_attempted' for r in result['rows']),32)

    def test_unknown_usage_keeps_full_hold_and_halts_remaining_cells(self):
        module, source, old, freeze, out, plan = self.prepare()
        bad = reply(json.dumps(UNKNOWN))
        bad.pop('usage')
        result = module.execute_continuation(out,self.approve(module,out),enabled=True,
            key_reader=lambda:'fixture-only-key',scripted_by_cell=self.scripts(plan,first=bad))
        self.assertTrue(result['batch_halted'])
        self.assertEqual(result['accounting']['cumulative']['known_accrued_usd'],'0.008200')
        self.assertEqual(result['accounting']['cumulative']['unknown_held_usd'],'0.330000')
        self.assertEqual(sum(r['status']=='not_attempted' for r in result['rows']),32)

    def test_failed_key_claims_persist_and_script_injection_fails_before_claim(self):
        module, source, old, freeze, out, plan = self.prepare()
        invalid = self.scripts(plan)
        invalid[plan['remaining_cells'][0]['cell_id']] = (lambda:None,)
        with self.assertRaises(StudyError):
            module.execute_continuation(out,self.approve(module,out),enabled=True,
                key_reader=lambda:self.fail('script validation read key'),scripted_by_cell=invalid)
        self.assertFalse((out/'execution.claim.json').exists())
        def key():
            raise RuntimeError('private fixture key error')
        result = module.execute_continuation(out,self.approve(module,out),enabled=True,
            key_reader=key,scripted_by_cell=self.scripts(plan))
        self.assertTrue(result['batch_halted'])
        self.assertEqual(result['accounting']['segment']['request_count'],0)
        self.assertEqual(result['rows'][0]['error_code'],'study_live_key_input_failed')
        self.assertNotIn('private fixture key error',(out/'summary.json').read_text())

    def test_gate_counts_opening_spend_and_reserves_against_cumulative_limit(self):
        module, source, old, freeze, out, plan = self.prepare()
        root=fixture_root()
        (root/'ledger').mkdir()
        study=read_json(out,'study.json')
        gate=module._ContinuationGate(root,study,'approved_live_fake','fixture-fingerprint')
        gate.wire=module._BoundWire(module._ScriptedWire(('timeout',)),simulated=True)
        gate.cell_id=plan['remaining_cells'][0]['cell_id']
        gate.rows=[{'cell_id':gate.cell_id,'state':'settled','charged_micro_usd':1661801,
                    'usage':{'prompt_tokens':1,'completion_tokens':1}}]
        with self.assertRaisesRegex(PolicyRunError,'study_usd_budget_exhausted'):
            gate.admit('1'*64)
        gate.rows=[{'cell_id':gate.cell_id,'state':'settled','charged_micro_usd':1,
                    'usage':{'prompt_tokens':1,'completion_tokens':1}} for _ in range(99)]
        with self.assertRaisesRegex(PolicyRunError,'study_request_budget_exhausted'):
            gate.admit('2'*64)

    def test_preparation_cannot_write_inside_original_control(self):
        module, source, old, freeze, out, plan = self.prepare()
        forbidden=source/'nested-continuation'
        with self.assertRaisesRegex(StudyError,'continuation_output_scope_invalid'):
            module.prepare_continuation(source,out=forbidden,project_root=PROJECT)
        self.assertFalse(forbidden.exists())

    def test_parallel_activation_has_one_source_claim_and_one_key_ingress(self):
        module, source, old, freeze, out, plan = self.prepare()
        approval=self.approve(module,out)
        entered,release=threading.Event(),threading.Event()
        outcomes,failures=[],[]
        def key():
            entered.set()
            if not release.wait(5):
                raise AssertionError('fixture release missing')
            return 'fixture-only-key'
        def execute():
            try:
                outcomes.append(module.execute_continuation(out,approval,enabled=True,
                    key_reader=key,scripted_by_cell=self.scripts(plan,first='timeout')))
            except BaseException as error:
                failures.append(error)
        worker=threading.Thread(target=execute)
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            with self.assertRaisesRegex(StudyError,'continuation_already_claimed'):
                module.execute_continuation(out,approval,enabled=True,
                    key_reader=lambda:self.fail('parallel key ingress'),scripted_by_cell=self.scripts(plan))
        finally:
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures,[])
        self.assertEqual(len(outcomes),1)

    def test_changed_plan_after_preflight_stops_before_key_and_preserves_claims(self):
        module, source, old, freeze, out, plan = self.prepare()
        original=module._run_study_core
        def change(*args,**kwargs):
            changed=plan|{'remaining_usd':'2.000000'}
            (out/'continuation.json').write_bytes(_json_bytes(changed))
            return original(*args,**kwargs)
        with patch.object(module,'_run_study_core',side_effect=change):
            with self.assertRaisesRegex(StudyError,'continuation_plan_drift'):
                module.execute_continuation(out,self.approve(module,out),enabled=True,
                    key_reader=lambda:self.fail('changed plan read key'),scripted_by_cell=self.scripts(plan))
        self.assertTrue((out/'execution.claim.json').is_file())
        self.assertTrue((source.parent/'continuation-claims'/f"{old['profile_sha256']}.json").is_file())

    def test_transport_secret_is_cleared_after_fake_approved_transport_run(self):
        module, source, old, freeze, out, plan = self.prepare()
        transports=[]
        class FakeApprovedTransport:
            def __init__(self,key):
                self._api_key=key
                transports.append(self)
            def post(self,endpoint,body,timeout_seconds,max_response_bytes):
                request=json.loads(body)
                return reply(None,calls=[call('end','finish',{'diagnosis':UNKNOWN})]) \
                    if request.get('tools') else reply(json.dumps(UNKNOWN))
        with patch.object(module,'_ApprovedTransport',FakeApprovedTransport):
            result=module.execute_continuation(out,self.approve(module,out),enabled=True,
                key_reader=lambda:'fixture-only-key')
        self.assertFalse(result['batch_halted'])
        self.assertTrue(transports)
        self.assertTrue(all(transport._api_key=='' for transport in transports))

    def test_logical_report_mapping_preserves_actual_result_receipt_digest(self):
        module, source, old, freeze, out, plan = self.prepare()
        prior=module._source(source)
        cell=plan['remaining_cells'][0]
        result=next(iter(prior['results'].values()))|{'cell_id':cell['cell_id'],
            'task_id':cell['task_id'],'strategy':cell['strategy']}
        artifact_digest=digest(result)
        with patch.object(module,'build_study_report',wraps=module.build_study_report) as builder:
            module._report(read_json(out,'study.json'),{cell['cell_id']:result},source=prior,plan=plan)
        logical=plan['cell_mapping'][3]['logical_cell_id']
        mapped=builder.call_args.args[1][logical]
        self.assertEqual(digest(mapped),artifact_digest)
        self.assertIs(mapped,result)
        self.assertEqual(mapped['cell_id'],cell['cell_id'])

    def test_same_size_authorized_source_drift_cannot_prepare_continuation(self):
        module, source, old, freeze, out, plan = self.prepare()
        helper=Path(old['pack_root'])/'snapshots/dev0/helper.py'
        before=helper.read_bytes()
        changed=before.replace(b'x * 2',b'x * 3')
        self.assertNotEqual(before,changed)
        self.assertEqual(len(before),len(changed))
        helper.write_bytes(changed)
        second=out.parent/(out.name+'-drifted-source')
        with self.assertRaisesRegex(StudyError,'continuation_source_task_drift'):
            module.prepare_continuation(source,out=second,project_root=PROJECT)
        self.assertFalse((second/'continuation.json').exists())
        self.assertFalse((second/'execution.claim.json').exists())
        self.assertFalse((source.parent/'continuation-claims'/f"{old['profile_sha256']}.json").exists())
        with self.assertRaises(StudyError):
            module.propose_continuation_approval(out)
