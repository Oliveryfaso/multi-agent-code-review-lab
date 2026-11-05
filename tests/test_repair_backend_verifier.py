"""Authored backend/transport observations only; no target or host execution."""
from copy import deepcopy
import json
from unittest import TestCase
from unittest.mock import patch

import test_repair_entry as fixtures
from macr.investigation import repair_entry as entry, repair_loop as loop
from macr.investigation.records import BackendCapabilities
from macr.execution.results import classify_check


class FixtureBackend:
    """Returns declared records, never executes the snapshot."""
    def __init__(self, names, states=('failed','passed'), *, capable=True, count=None):
        self.names=list(names);self.states=list(states);self.capable=capable;self.count=count
        self.calls=[];self.reads=[];self.modify=None

    def capabilities(self):
        return BackendCapabilities(**{k:self.capable for k in (
            'network_disabled','readonly_input','isolated_writes','secrets_absent','cpu_limit',
            'memory_limit','pid_limit','wall_limit','output_limit','process_tree_stop')})

    def run(self, check, snapshot):
        self.calls.append((deepcopy(check),deepcopy(snapshot)))
        self.state=self.states.pop(0)
        if self.state=='raise':raise RuntimeError('DUMMY-PRIVATE-ERROR-MUST-NOT-PERSIST')
        if self.state in ('passed','failed'):
            n=len(self.names) if self.count is None else self.count
            failed=int(self.state=='failed')
            result=classify_check({'execution_status':'completed','exit_code':failed,
                'test_counts':{'passed':n-failed,'failed':failed,'executed':n,'collected':n}},check)
        else: result=classify_check({'execution_status':self.state},check)
        result.stdout_ref='DUMMY-PRIVATE-RAW-LOG-REFERENCE'
        return result

    def public_result(self, request):
        self.reads.append(deepcopy(request))
        outcomes={n:('failed' if i==0 and self.state=='failed' else 'passed') for i,n in enumerate(self.names)}
        receipt={'schema':'macr.repair.public-check-receipt.v1','phase':'public',
            'request_sha256':loop.digest(request),'observation':{
                'source_id':request['source_id'],'execution_status':'completed',
                'outcomes':outcomes,'diagnostics':[]}}
        if self.modify:self.modify(receipt)
        return receipt


class RepairBackendVerifierTests(TestCase):
    def setUp(self):
        self.h=fixtures.RepairEntryTests('runTest');self.h.setUp();self.addCleanup(self.h.doCleanups)
        self.names=loop.test_manifest(self.h.files,['checks.py'])

    def verifier(self, backend):
        self.assertTrue(hasattr(loop,'BackendVerifier'),'Normal repair cannot yet consume source-bound public backend receipts')
        return loop.BackendVerifier(backend,backend.public_result,backend_id='authored-backend',execution_profile='fixture-public-v1')

    def invoke(self, backend, strategy='api_debugger'):
        transport=fixtures.InterceptedTransport([self.h.proposal()])
        code,result=self.h.invoke(strategy,transport,self.verifier(backend),proposal_contract='v6')
        return code,result,transport

    def score_plan(self, strategy='api_debugger'):
        import test_repair_comparison as comparison_fixtures
        from macr.evals.repair_comparison import compile_comparison
        spec=comparison_fixtures.ThreeArmComparisonTests('runTest').spec(tasks=1,proposal_contract='v6')
        spec['tasks']=[{'task_id':self.h.task['task_id'],'snapshot_sha256':loop.source_id(self.h.files),
            'issue_sha256':loop.digest(self.h.task['issue']),
            'visible_tests_sha256':loop.source_id({'checks.py':self.h.files['checks.py']}),
            'initial_context_sha256':entry.initial_context_id(self.h.files,self.h.task['issue'])}]
        spec['model']['configuration_sha256']=loop.digest(self.h.config)
        limits=self.h.config['limits']
        spec['limits']={'max_requests':limits['max_requests'],'input_tokens':limits['max_total_input_tokens'],
            'output_tokens':limits['max_total_output_tokens'],'max_usd':limits['max_usd']}
        plan=compile_comparison(spec)
        return plan,next(c for c in plan['cells'] if c['arm']==strategy)

    def test_public_pass_and_backend_timeout_remain_unscored_in_full_denominator(self):
        for state in ('passed','timeout'):
            with self.subTest(state=state):
                self.h.out=self.h.root/('score-'+state)
                backend=FixtureBackend(self.names,('failed','passed') if state=='passed' else ('timeout',))
                self.invoke(backend)
                plan,cell=self.score_plan()
                packet=entry.score_handoff(self.h.out,plan,cell['cell_id'])
                self.assertEqual(packet['result']['verification_origin'],'unavailable')
                self.assertIsNone(packet['result']['review'])
                self.assertFalse(packet['semantic_truth_validated'])
                self.assertEqual(packet['result']['requests'],1 if state=='passed' else 0)
                self.assertEqual(packet['verification_receipt']['origin'],'backend_reported_unattested')

    def test_remote_unavailable_backend_does_not_read_key_or_claim_spend(self):
        from macr.providers.base import ProviderError
        backend=FixtureBackend(self.names,capable=False)
        plan,path=self.h.spend_plan(strategy='api_debugger',proposal_contract='v6')
        with patch.object(entry,'_read_runtime_credential',side_effect=ProviderError('unexpected_credential_input')) as key:
            code,result=self.h.invoke('api_debugger',None,self.verifier(backend),proposal_contract='v6',
                enable_remote=True,spend_plan=path,spend_cell='one')
        self.assertEqual((code,result['status']),(2,'verification_unavailable'))
        key.assert_not_called();self.assertFalse(__import__('pathlib').Path(plan['ledger_root']).exists())
        self.assertEqual(backend.calls,[])

    def test_simulation_api_cannot_dispatch_a_backend_interface(self):
        from macr.investigation.validation import ContractError
        backend=FixtureBackend(self.names)
        with self.assertRaises(ContractError):
            loop.run_simulated_loop(self.h.repo,self.h.task['issue'],editable_paths=self.h.task['editable_paths'],
                test_paths=self.h.task['test_paths'],provider=None,verifier=self.verifier(backend),out=self.h.out)
        self.assertEqual(backend.calls,[])

    def test_backend_mutating_snapshot_is_detected_before_model_feedback(self):
        backend=FixtureBackend(self.names)
        original=backend.run
        def mutate(check,snapshot):
            result=original(check,snapshot)
            from pathlib import Path
            p=Path(snapshot.root_ref)/'builder.py';p.write_bytes(p.read_bytes()+b'# altered\n')
            return result
        backend.run=mutate
        code,result,transport=self.invoke(backend)
        self.assertEqual(code,2)
        self.assertEqual(len(transport.requests),0)

    def test_normal_cli_consumes_bound_public_results_and_replays_without_dispatch(self):
        backend=FixtureBackend(self.names)
        code,result,transport=self.invoke(backend)
        self.assertEqual(code,0,result)
        self.assertEqual((code,result['status'],len(transport.requests)),(0,'public_tests_reported_passed',1))
        self.assertEqual(result['verification_origin'],'backend_reported_unattested')
        self.assertIsNone(result['target_code_executed']);self.assertFalse(result['repaired'])
        report=loop.verify_loop(self.h.out)
        self.assertEqual((len(backend.calls),len(backend.reads)),(2,2))
        self.assertEqual(report['checks'][0]['backend_audit']['request']['test_names'],self.names)
        self.assertEqual(report['checks'][0]['backend_audit']['request']['phase'],'public')
        message=json.loads(transport.requests[0]['messages'][1]['content'])
        self.assertIn('backend_reported_unattested',json.dumps(message))
        self.assertNotIn('respect its explicit synthetic origin',transport.requests[0]['messages'][0]['content'])
        self.assertNotIn('DUMMY-PRIVATE-RAW-LOG-REFERENCE',json.dumps(report))
        self.assertFalse(report['repaired']);self.assertIsNone(report['execution_authorized'])

    def test_direct_api_does_not_even_construct_the_verifier(self):
        transport=fixtures.InterceptedTransport([{'status':'no_candidate','summary':'Authored direct output.','edits':[]}])
        with patch.object(entry,'_verification_backend',side_effect=AssertionError('must not construct backend')):
            # invoke patches the factory itself, so call its provider seam directly.
            from macr.providers.repair_binding import RepairProviderBinding
            import io
            from contextlib import redirect_stdout
            from macr import cli
            provider=RepairProviderBinding(self.h.config,transport=transport)
            argv=['agent-review','repair','--repo',str(self.h.repo),'--task',str(self.h.task_file),
                '--model-config',str(self.h.config_file),'--out',str(self.h.out),'--strategy','direct_api']
            with patch('sys.argv',argv),patch.object(entry,'_make_provider',return_value=provider),redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:cli.main()
        self.assertEqual(stopped.exception.code,0)

    def test_bad_receipt_binding_hidden_phase_and_extra_fields_stop_before_model(self):
        changes=[lambda r:r.update(request_sha256='0'*64),lambda r:r.update(phase='hidden'),
            lambda r:r.update(hidden_score={'passed':True}),
            lambda r:r['observation']['outcomes'].pop(self.names[-1])]
        for i,change in enumerate(changes):
            with self.subTest(change=i):
                self.h.out=self.h.root/('bad-'+str(i));backend=FixtureBackend(self.names);backend.modify=change
                code,result,transport=self.invoke(backend)
                self.assertEqual((code,result['status'],len(transport.requests)),(2,'verification_invalid',0))
                report=loop.verify_loop(self.h.out)
                self.assertTrue(report['checks'][0]['invalid'])
                self.assertIsNone(report['checks'][0]['backend_audit']['receipt'])
                self.assertNotIn('hidden_score',json.dumps(report))

    def test_missing_capabilities_never_dispatch_or_read_receipt(self):
        backend=FixtureBackend(self.names,capable=False)
        code,result,_=self.invoke(backend)
        self.assertEqual((code,result['status']),(2,'verification_unavailable'))
        self.assertEqual((backend.calls,backend.reads),([],[]))
        loop.verify_loop(self.h.out)

    def test_unknown_timeout_and_exception_do_not_call_reader_or_retry(self):
        for state,status in [('outcome_unknown','verification_outcome_unknown'),('timeout','verification_timeout'),
                             ('raise','verification_outcome_unknown')]:
            with self.subTest(state=state):
                self.h.out=self.h.root/state;backend=FixtureBackend(self.names,(state,))
                code,result,transport=self.invoke(backend)
                self.assertEqual((code,result['status'],len(transport.requests)),(2,status,0))
                self.assertEqual((len(backend.calls),len(backend.reads)),(1,0))
                self.assertNotIn('DUMMY-PRIVATE-ERROR',json.dumps(loop.verify_loop(self.h.out)))

    def test_backend_counts_cannot_be_overridden_by_complete_reader_outcomes(self):
        backend=FixtureBackend(self.names,count=len(self.names)-1)
        code,result,transport=self.invoke(backend)
        self.assertEqual((code,result['status'],len(transport.requests)),(2,'verification_invalid',0))
        loop.verify_loop(self.h.out)

    def test_reader_exception_is_recorded_without_exception_text_or_reexecution(self):
        backend=FixtureBackend(self.names)
        def fail(_):raise OSError('DUMMY-PRIVATE-READER-ERROR')
        verifier=self.verifier(backend);verifier.public_result_reader=fail
        code,result=self.h.invoke('api_debugger',fixtures.InterceptedTransport([]),verifier,proposal_contract='v6')
        self.assertEqual((code,result['status']),(2,'verification_invalid'))
        report=loop.verify_loop(self.h.out)
        self.assertEqual(report['checks'][0]['backend_audit']['failure'],'reader_unavailable')
        self.assertNotIn('DUMMY-PRIVATE-READER-ERROR',json.dumps(report))
        self.assertEqual(len(backend.calls),1)

    def test_all_proposal_contracts_project_public_feedback_without_backend_audit(self):
        for contract in ('v1','v2','v3','v4','v5','v6'):
            with self.subTest(contract=contract):
                self.h.out=self.h.root/contract;backend=FixtureBackend(self.names)
                code,result=self.h.invoke('api_debugger',fixtures.InterceptedTransport([self.h.proposal()]),
                    self.verifier(backend),proposal_contract=contract)
                self.assertEqual((code,result['status']),(0,'public_tests_reported_passed'))
                request=loop._read(self.h.out,'rounds/1/intent.json')['request']
                content=request['messages'][1]['content']
                self.assertNotIn('backend_audit',content)
                self.assertNotIn('root_ref',content)
                self.assertNotIn(str(self.h.out),content)
                self.assertEqual(loop.verify_loop(self.h.out)['provider_requests'],1)

    def test_persisted_receipt_cannot_be_reused_for_another_snapshot_even_with_rehashed_files(self):
        from macr.investigation.validation import ContractError
        backend=FixtureBackend(self.names);self.invoke(backend)
        report=loop._read(self.h.out,'report.json')
        report['checks'][1]['backend_audit']['receipt']=deepcopy(report['checks'][0]['backend_audit']['receipt'])
        loop._write(self.h.out,'checks/1/receipt.json',report['checks'][1])
        report['artifact_hashes']=loop._file_hashes(self.h.out,skip=('report.json',))
        loop._write(self.h.out,'report.json',report)
        with self.assertRaises(ContractError):loop.verify_loop(self.h.out)
        self.assertEqual((len(backend.calls),len(backend.reads)),(2,2))

    def test_completed_zero_tests_and_truncated_backend_results_cannot_report_pass(self):
        for kind in ('zero','truncated'):
            with self.subTest(kind=kind):
                self.h.out=self.h.root/kind;backend=FixtureBackend(self.names,('passed',),count=0 if kind=='zero' else None)
                if kind=='truncated':
                    original=backend.run
                    def truncated(check,snapshot):
                        result=original(check,snapshot);result.output_truncated=True;return result
                    backend.run=truncated
                code,result,transport=self.invoke(backend)
                self.assertEqual((code,result['status'],len(transport.requests)),(2,'verification_invalid',0))
                self.assertFalse(loop.verify_loop(self.h.out)['repaired'])

    def test_public_diagnostics_are_source_bound_and_hidden_test_ids_are_rejected(self):
        for valid in (False,True):
            with self.subTest(valid=valid):
                self.h.out=self.h.root/('diagnostic-'+str(valid));backend=FixtureBackend(self.names)
                def diagnostics(receipt):
                    if backend.state=='failed':
                        receipt['observation']['diagnostics']=[{'test_id':self.names[0] if valid else 'hidden.py::secret',
                            'kind':'assertion','message':'Authored public diagnostic.',
                            'frames':[{'file':'builder.py','line':3}]}]
                backend.modify=diagnostics
                code,result,transport=self.invoke(backend)
                self.assertEqual(result['status'],'public_tests_reported_passed' if valid else 'verification_invalid')
                self.assertEqual(len(transport.requests),int(valid))
                report=loop.verify_loop(self.h.out)
                self.assertNotIn('hidden.py',json.dumps(report))

    def test_repeated_launch_does_not_dispatch_again(self):
        backend=FixtureBackend(self.names);self.invoke(backend)
        calls=len(backend.calls);code,result,_=self.invoke(backend)
        self.assertEqual((code,result['status']),(2,'rejected'))
        self.assertEqual(len(backend.calls),calls)

    def test_execution_profile_is_explicit_and_bound_to_each_dispatch(self):
        backend=FixtureBackend(self.names);verifier=self.verifier(backend)
        self.assertIn('execution_profile',verifier.identity)
        self.h.invoke('api_debugger',fixtures.InterceptedTransport([self.h.proposal()]),verifier,proposal_contract='v6')
        self.assertTrue(all(check.execution_profile==verifier.identity['execution_profile']
                            for check,_ in backend.calls))
        self.assertTrue(all(r['spec']['execution_profile']==verifier.identity['execution_profile'] for r in backend.reads))

    def test_malformed_backend_result_is_unknown_not_a_known_execution_error(self):
        for field,value in [('test_counts',{'passed':'invalid'}),('schema_version',2)]:
            with self.subTest(field=field):
                self.h.out=self.h.root/field;backend=FixtureBackend(self.names)
                original=backend.run
                def malformed(check,snapshot):
                    result=original(check,snapshot);setattr(result,field,value);return result
                backend.run=malformed
                code,result,transport=self.invoke(backend)
                self.assertEqual((code,result['status'],len(transport.requests)),(2,'verification_outcome_unknown',0))
                report=loop.verify_loop(self.h.out)
                self.assertEqual(report['checks'][0]['backend_audit']['failure'],'backend_outcome_unknown')
                self.assertEqual((len(backend.calls),len(backend.reads)),(1,0))
