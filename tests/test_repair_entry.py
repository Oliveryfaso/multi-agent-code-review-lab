"""Normal CLI and real provider encoder/parser, with all I/O intercepted."""
from copy import deepcopy
import io
import json
import re
import runpy
from pathlib import Path
from contextlib import redirect_stdout
from unittest import TestCase
from unittest.mock import patch

from debugger_fixtures import fixture_root, write_files
from repair_fixtures import SOURCES, OLD, NEW
from macr import cli
from macr.investigation import repair_entry as entry
from macr.investigation import repair_loop as loop
from macr.providers.base import ProviderError
from macr.providers.repair_binding import RepairProviderBinding, development_config


class InterceptedTransport:
    def __init__(self, replies, *, ensure_ascii=True, finish_reason='stop'):
        self.replies, self.requests, self.ensure_ascii = list(replies), [], ensure_ascii
        self.finish_reason = finish_reason
    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        wire = json.loads(body); self.requests.append(wire)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception): raise reply
        payload = json.loads(wire['messages'][1]['content'])
        output = {key:payload[key] for key in ('base_content_id','hypothesis_id','evidence_ids')}
        output.update(reply)
        return {'id':'fake-request','model':wire['model'],'system_fingerprint':'fixture-fingerprint',
            'choices':[{'finish_reason':self.finish_reason,'message':{'role':'assistant','content':json.dumps(output,ensure_ascii=self.ensure_ascii),
                                                       'reasoning_content':'synthetic'}}],
            'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150}}


class InterceptedOfficialOpener:
    """Exercise the real HTTP adapter with an opener that cannot use sockets."""
    def __init__(self,replies,*,fingerprint='fixture-fingerprint',escape_content_hyphens=False):
        self.transport=InterceptedTransport(replies);self.fingerprint=fingerprint;self.requests=[]
        self.escape_content_hyphens=escape_content_hyphens
    def open(self,request,timeout):
        self.requests.append(request)
        payload=self.transport.post(request.full_url,request.data,timeout,1048576)
        payload['system_fingerprint']=self.fingerprint
        if self.escape_content_hyphens:
            message=payload['choices'][0]['message']
            message['content']=message['content'].replace('-',r'\u002d')
        data=json.dumps(payload,ensure_ascii=False).encode()
        class Response:
            status=200;headers={'x-request-id':'DUMMY-HTTP-RECEIPT'}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,limit):return data[:limit]
        return Response()


class RepairEntryTests(TestCase):
    def test_official_response_credential_echo_stops_without_persisting_raw_response(self):
        key='DUMMY-ACTIVE-KEY-FIXTURE'
        for escaped in (False,True):
            with self.subTest(escaped=escaped):
                plan,path=self.spend_plan()
                plan['ledger_root']=str(self.root/('echo-ledger-'+str(escaped)))
                path.write_text(json.dumps(plan));self.out=self.root/('echo-out-'+str(escaped))
                opener=InterceptedOfficialOpener([{'status':'no_candidate','summary':key,'edits':[]}],
                    escape_content_hyphens=escaped)
                with patch.object(entry,'_read_runtime_credential',return_value=key), \
                     patch('urllib.request.build_opener',return_value=opener):
                    code,result=self.invoke(enable_remote=True,spend_plan=path,spend_cell='one',proposal_contract='v2')
                self.assertEqual((code,result['status']),(2,'provider_outcome_unknown'))
                self.assertEqual(len(opener.requests),1)
                self.assertEqual(opener.requests[0].get_header('Authorization'),'Bearer '+key)
                self.assertFalse((self.out/'rounds/1/response.json').exists())
                held=result['global_spend']['unknown_reserved_usd']
                self.assertNotEqual(held,'0.000000');self.assertTrue(result['global_spend']['stopped'])
                needles=(key.encode(),key.replace('-',r'\u002d').encode(),
                    json.dumps(key.replace('-',r'\u002d'))[1:-1].encode())
                for directory in (self.out,Path(plan['ledger_root'])):
                    for retained in directory.rglob('*'):
                        if retained.is_file():
                            data=retained.read_bytes()
                            for needle in needles:self.assertNotIn(needle,data)
                self.out=self.root/('echo-next-'+str(escaped))
                with patch.object(entry,'_read_runtime_credential',side_effect=AssertionError('no Key retry')), \
                     patch('urllib.request.build_opener',side_effect=AssertionError('no HTTP retry')):
                    code,result=self.invoke(enable_remote=True,spend_plan=path,spend_cell='two',proposal_contract='v2')
                self.assertEqual((code,result['status']),(2,'rejected'))
                self.assertEqual(result['global_spend']['unknown_reserved_usd'],held)
                self.assertFalse(self.out.exists())

    def large_utf8_envelope_fixture(self):
        fixed = dict(self.files); edits = []
        for name, old_number, new_number in (('a.py','1','3'), ('b.py','2','4')):
            old = '#' + 'é'*7900 + '\nvalue = ' + old_number + '\n'
            new = old.replace('value = '+old_number, 'value = '+new_number)
            self.files[name] = old.encode('utf-8'); fixed[name] = new.encode('utf-8')
            (self.repo/name).write_bytes(self.files[name])
            self.task['editable_paths'].append(name)
            edits.append({'file':name, 'old':old, 'new':new})
        self.task['source_files'] = loop._hashes(self.files)
        self.task_file.write_text(json.dumps(self.task))
        self.config['limits']['max_input_per_request'] = 32768
        self.config['limits']['max_total_input_tokens'] = 98304
        self.config_file.write_text(json.dumps(self.config))
        names = loop.test_manifest(self.files, ['checks.py'])
        verifier = loop.ReplayVerifier([self.observation(self.files,names[:1]), self.observation(fixed)])
        return {'status':'candidate', 'summary':'Synthetic encoding-boundary probe.', 'edits':edits}, verifier, fixed

    def test_large_utf8_response_envelope_reloads_and_reaches_score_handoff(self):
        from macr.evals.repair_comparison import compile_comparison
        from pathlib import Path
        reply, verifier, fixed = self.large_utf8_envelope_fixture()
        transport = InterceptedTransport([reply], ensure_ascii=False)
        code, result = self.invoke('api_debugger', transport, verifier)
        self.assertEqual((code,result['status']), (0,'simulation_passed'))
        report = loop.verify_loop(self.out)
        envelope_bytes = (self.out/'rounds/1/response.json').read_bytes()
        envelope = loop.read_proposal_response(self.out,1)
        self.assertLessEqual(len(envelope['content'].encode()),65536)
        self.assertLessEqual(len(envelope_bytes),loop.CAP)
        self.assertGreater(len(json.dumps(envelope,ensure_ascii=True).encode()),loop.CAP)
        spec = json.loads((Path(__file__).resolve().parents[1]/'examples/repair_comparison_development.json').read_text())
        spec['tasks'] = [{'task_id':self.task['task_id'], 'snapshot_sha256':loop.source_id(self.files),
            'issue_sha256':loop.digest(self.task['issue']),
            'visible_tests_sha256':loop.source_id({'checks.py':self.files['checks.py']}),
            'initial_context_sha256':entry.initial_context_id(self.files,self.task['issue'])}]
        spec['model']['configuration_sha256'] = loop.digest(self.config)
        spec['model']['output_contract_sha256'] = loop.digest(loop.OUTPUT_CONTRACT)
        limits = self.config['limits']
        spec['limits'] = {'max_requests':limits['max_requests'], 'input_tokens':limits['max_total_input_tokens'],
            'output_tokens':limits['max_total_output_tokens'], 'max_usd':limits['max_usd']}
        plan = compile_comparison(spec)
        cell = next(c for c in plan['cells'] if c['arm']=='api_debugger')
        packet = entry.score_handoff(self.out,plan,cell['cell_id'])
        self.assertIsNone(packet['result']['review'])
        for name in ('a.py','b.py'):
            self.assertEqual(packet['candidate_files'][name].encode(),fixed[name])
            self.assertEqual((self.repo/name).read_bytes(),self.files[name])
        self.assertFalse(report['repaired']); self.assertFalse(report['target_code_executed'])

    def test_large_utf8_failed_response_envelope_keeps_failure_and_outer_cap(self):
        from macr.investigation.validation import ContractError
        reply, verifier, _ = self.large_utf8_envelope_fixture()
        transport = InterceptedTransport([reply], ensure_ascii=False, finish_reason='length')
        code, result = self.invoke('api_debugger', transport, verifier)
        self.assertEqual((code,result['status']), (2,'provider_response_rejected'))
        report = loop.verify_loop(self.out)
        self.assertEqual(verifier.calls,1)
        self.assertEqual(loop.read_proposal_response(self.out,1)['finish_reason'],'length')
        self.assertEqual(report['provider_requests'],1)
        self.assertFalse(report['unknown_request_reservation'])
        self.assertFalse(report['repaired']); self.assertFalse(report['target_code_executed'])
        other = self.root/'outer-boundary'; (other/'rounds/1').mkdir(parents=True)
        path = other/'rounds/1/response.json'
        path.write_bytes(b'{}' + b' '*(loop.CAP-2))
        self.assertEqual(loop.read_proposal_response(other,1),{})
        path.write_bytes(b'{}' + b' '*(loop.CAP-1))
        with self.assertRaises(ContractError): loop.read_proposal_response(other,1)

    def test_utf8_wire_proposal_reloads_and_exports_without_ascii_rejection(self):
        from macr.evals.repair_comparison import compile_comparison, score_packet
        from macr.investigation.validation import ContractError
        from pathlib import Path
        original = SOURCES['builder.py'] + '#' + '文'*5300 + '\n'
        changed = original.replace(OLD, NEW)
        self.files['builder.py'] = original.encode('utf-8')
        (self.repo/'builder.py').write_bytes(self.files['builder.py'])
        self.task['source_files'] = loop._hashes(self.files)
        self.task_file.write_text(json.dumps(self.task))
        self.config['limits']['max_input_per_request'] = 32768
        self.config['limits']['max_total_input_tokens'] = 98304
        self.config_file.write_text(json.dumps(self.config))
        fixed = dict(self.files, **{'builder.py':changed.encode('utf-8')})
        names = loop.test_manifest(self.files, ['checks.py'])
        verifier = loop.ReplayVerifier([self.observation(self.files, names[:1]), self.observation(fixed)])
        reply = self.proposal(old=original, new=changed)
        reply['summary'] = '说明'*340
        transport = InterceptedTransport([reply], ensure_ascii=False)
        code, result = self.invoke('api_debugger', transport, verifier)
        self.assertEqual((code, result['status']), (0, 'simulation_passed'))
        report = loop.verify_loop(self.out)
        wire = transport.requests[0]
        payload = json.loads(wire['messages'][1]['content'])
        self.assertEqual(payload['files']['builder.py'], original)
        self.assertIn('65536 UTF-8 bytes', wire['messages'][0]['content'])
        content = loop._read(self.out, 'rounds/1/response.json')['content']
        self.assertLessEqual(len(content.encode('utf-8')), 65536)
        self.assertGreater(len(json.dumps(json.loads(content),ensure_ascii=True).encode()),65536)
        spec = json.loads((Path(__file__).resolve().parents[1]/'examples/repair_comparison_development.json').read_text())
        spec['tasks'] = [{'task_id':self.task['task_id'], 'snapshot_sha256':loop.source_id(self.files),
            'issue_sha256':loop.digest(self.task['issue']),
            'visible_tests_sha256':loop.source_id({'checks.py':self.files['checks.py']}),
            'initial_context_sha256':entry.initial_context_id(self.files,self.task['issue'])}]
        spec['model']['configuration_sha256'] = loop.digest(self.config)
        spec['model']['output_contract_sha256'] = loop.digest(loop.OUTPUT_CONTRACT)
        limits = self.config['limits']
        spec['limits'] = {'max_requests':limits['max_requests'], 'input_tokens':limits['max_total_input_tokens'],
            'output_tokens':limits['max_total_output_tokens'], 'max_usd':limits['max_usd']}
        plan = compile_comparison(spec)
        cell = next(c for c in plan['cells'] if c['arm']=='api_debugger')
        packet = entry.score_handoff(self.out, plan, cell['cell_id'])
        self.assertEqual(packet['candidate_files']['builder.py'].encode(), fixed['builder.py'])
        self.assertIsNone(packet['result']['review'])
        self.assertFalse(packet['semantic_truth_validated'])
        self.assertFalse(report['repaired']); self.assertFalse(report['target_code_executed'])
        oversized = packet['proposal_chain']*2
        self.assertGreater(len(json.dumps(oversized,ensure_ascii=False,separators=(',',':')).encode()),65536)
        row = dict(packet['result'], requests=2)
        with self.assertRaises(ContractError) as raised:
            score_packet(plan,cell['cell_id'],row,original_files=self.files,
                editable_paths=self.task['editable_paths'],protected_paths=self.task['test_paths'],
                final_output=packet['final_output'],verification_receipt=packet['verification_receipt'],
                proposal_chain=oversized)
        self.assertEqual(raised.exception.field, 'json_bytes')

    def setUp(self):
        self.root = fixture_root(); self.repo = write_files(self.root/'generation', SOURCES)
        self.files = {n:s.encode() for n,s in SOURCES.items()}
        self.out = self.root/'run'
        self.config = development_config(); self.config_file = self.root/'model.json'
        self.config_file.write_text(json.dumps(self.config))
        self.task = {'schema':'macr.repair.task.v1','task_id':'public-invoice',
            'issue':'Discount applies to items, not delivery.', 'source_files':loop._hashes(self.files),
            'editable_paths':['app.py','builder.py','records.py'], 'test_paths':['checks.py']}
        self.task_file = self.root/'task.json'; self.task_file.write_text(json.dumps(self.task))
        self.guard = patch('socket.socket.connect', side_effect=AssertionError('no network'))
        self.guard.start(); self.addCleanup(self.guard.stop)
        self.process_guard = patch('subprocess.Popen', side_effect=AssertionError('no target or VM'))
        self.process_guard.start(); self.addCleanup(self.process_guard.stop)

    def invoke(self, strategy='direct_api', transport=None, verifier=None, *, proposal_contract='v1',
               enable_remote=False,spend_plan=None,spend_cell=None):
        output = io.StringIO(); provider = RepairProviderBinding(self.config, transport=transport)
        self.provider = provider
        args = ['agent-review','repair','--repo',str(self.repo),'--task',str(self.task_file),
                '--model-config',str(self.config_file),'--out',str(self.out),'--strategy',strategy]
        if proposal_contract != 'v1': args.extend(['--proposal-contract',proposal_contract])
        if enable_remote:args.append('--enable-remote')
        if spend_plan is not None:args.extend(['--spend-plan',str(spend_plan)])
        if spend_cell is not None:args.extend(['--spend-cell',spend_cell])
        with patch('sys.argv', args), patch.object(entry, '_make_provider', return_value=provider), \
             patch.object(entry, '_verification_backend', return_value=verifier), redirect_stdout(output):
            with self.assertRaises(SystemExit) as exited: cli.main()
        self.assertTrue(output.getvalue(),'Repair CLI must return a structured boundary result.')
        return exited.exception.code, json.loads(output.getvalue())

    def spend_plan(self,*,strategy='direct_api',cells=('one','two')):
        import time
        plan={'schema':'macr.repair.spend-plan.v1','run_id':'public-runtime-fixture',
            'ledger_root':str(self.root/'durable-spend'),'configuration_sha256':loop.digest(self.config),
            'output_contract_sha256':loop.digest(loop.output_contract('v2')),
            'max_requests':6,'max_usd':'2.000000','max_total_input_tokens':49152,
            'max_total_output_tokens':6144,'expires_at':int(time.time())+600,
            'cells':[{'cell_id':cell,'binding_sha256':entry.spend_binding_sha256(self.task,strategy,self.config)}
                     for cell in cells]}
        path=self.root/'spend-plan.json';path.write_text(json.dumps(plan));return plan,path

    def test_remote_scope_is_explicit_and_incomplete_flags_never_read_key(self):
        from contextlib import redirect_stderr
        for enabled,plan,cell in ((True,None,None),(False,self.root/'absent.json','one')):
            with self.subTest(enabled=enabled),patch.object(entry,'_read_runtime_credential',
                    side_effect=AssertionError('no Key'),create=True),redirect_stderr(io.StringIO()):
                code,result=self.invoke(enable_remote=enabled,spend_plan=plan,spend_cell=cell,proposal_contract='v2')
                self.assertEqual((code,result['status']),(2,'rejected'));self.assertFalse(self.out.exists())

    def test_official_cli_uses_real_ledger_and_one_dummy_key_per_cell(self):
        plan,path=self.spend_plan()
        opener=InterceptedOfficialOpener([self.proposal(),{'status':'finalize_current','summary':'Retain.','edits':[]}])
        with patch.object(entry,'_read_runtime_credential',return_value='DUMMY-NOT-A-REAL-KEY') as reader, \
             patch('urllib.request.build_opener',return_value=opener):
            code,result=self.invoke(enable_remote=True,spend_plan=path,spend_cell='one',proposal_contract='v2')
        self.assertEqual((code,result['status']),(0,'candidate_finalized'))
        self.assertEqual((reader.call_count,len(opener.requests)),(1,2))
        self.assertFalse(result['simulation']);self.assertTrue(result['remote_enabled'])
        self.assertEqual(result['candidate_origin'],'official_https_output_unattested')
        self.assertEqual(result['global_spend']['requests'],2)
        self.assertEqual(result['global_spend']['unknown_reserved_usd'],'0.000000')
        self.assertEqual(result['global_spend']['finished_cells'],1)
        report=loop.verify_loop(self.out)
        self.assertIsNone(report['real_model_calls']);self.assertIsNone(report['paid_requests'])
        self.assertFalse(report['target_code_executed']);self.assertFalse(report['repaired'])
        self.assertEqual(report['provider_accounting']['schema'],'macr.repair.provider-meter.v2')
        self.assertEqual(loop._read(self.out,'claim.json')['config']['provider_runtime']['spend_commitment']['cell_id'],'one')
        for data in (json.dumps(result),(self.out/'report.json').read_text(),(self.out/'claim.json').read_text()):
            self.assertNotIn('DUMMY-NOT-A-REAL-KEY',data)
        self.assertTrue((Path(plan['ledger_root'])/'cell-001.finish.json').is_file())
        from macr.investigation.validation import ContractError
        forged=deepcopy(report);forged['simulation']=True
        loop._write(self.out,'report.json',forged)
        with self.assertRaises(ContractError):loop.verify_loop(self.out)
        loop._write(self.out,'report.json',report)
        from macr.evals.repair_comparison import compile_comparison,build_report
        spec=json.loads((Path(__file__).resolve().parents[1]/'examples/repair_comparison_development.json').read_text())
        spec['tasks']=[{'task_id':self.task['task_id'],'snapshot_sha256':loop.source_id(self.files),
            'issue_sha256':loop.digest(self.task['issue']),
            'visible_tests_sha256':loop.source_id({'checks.py':self.files['checks.py']}),
            'initial_context_sha256':entry.initial_context_id(self.files,self.task['issue'])}]
        spec['model']['configuration_sha256']=loop.digest(self.config)
        spec['model']['output_contract_sha256']=loop.digest(loop.output_contract('v2'))
        limits=self.config['limits']
        spec['limits']={'max_requests':limits['max_requests'],'input_tokens':limits['max_total_input_tokens'],
            'output_tokens':limits['max_total_output_tokens'],'max_usd':limits['max_usd']}
        comparison=compile_comparison(spec)
        cell=next(c for c in comparison['cells'] if c['arm']=='direct_api')
        packet=entry.score_handoff(self.out,comparison,cell['cell_id'])
        self.assertEqual(packet['result']['candidate_origin'],'provider_unattested')
        self.assertIsNone(packet['result']['review'])
        self.assertEqual(build_report(comparison,{cell['cell_id']:packet['result']})['arms']['direct_api']['scored'],0)

    def test_remote_debugger_without_real_verifier_never_consumes_ledger_or_key(self):
        plan,path=self.spend_plan(strategy='api_debugger',cells=('debugger',))
        fake=loop.ReplayVerifier([self.observation(self.files,loop.test_manifest(self.files,['checks.py']))])
        with patch.object(entry,'_read_runtime_credential',side_effect=AssertionError('no Key')), \
             patch('urllib.request.build_opener',side_effect=AssertionError('no HTTP')):
            code,result=self.invoke('api_debugger',enable_remote=True,spend_plan=path,
                spend_cell='debugger',proposal_contract='v2',verifier=fake)
        self.assertEqual((code,result['status']),(2,'verification_unavailable'))
        self.assertEqual(result['provider_requests'],0);self.assertFalse(result['remote_enabled'])
        self.assertIsNone(result['global_spend']);self.assertEqual(result['spend_status'],'not_opened_no_verifier')
        self.assertFalse(Path(plan['ledger_root']).exists())

    def test_invalid_runtime_plan_rejects_before_claim_or_credential(self):
        plan,path=self.spend_plan()
        for key,value in (('configuration_sha256','0'*64),('output_contract_sha256','0'*64),
                          ('expires_at',1),('ledger_root',str(self.out/'spend')),
                          ('ledger_root',str(self.repo/'spend')),
                          ('cells',[{'cell_id':'one','binding_sha256':'0'*64}])):
            bad=deepcopy(plan);bad[key]=value;path.write_text(json.dumps(bad))
            with self.subTest(key=key),patch.object(entry,'_read_runtime_credential',side_effect=AssertionError('no Key')):
                code,result=self.invoke(enable_remote=True,spend_plan=path,spend_cell='one',proposal_contract='v2')
                self.assertEqual((code,result['status']),(2,'rejected'))
                self.assertFalse(Path(plan['ledger_root']).exists());self.assertFalse(self.out.exists())

    def test_runtime_credential_failure_finishes_claim_without_request_reserve(self):
        plan,path=self.spend_plan()
        with patch.object(entry,'_read_runtime_credential',side_effect=ProviderError('repair_credential_input_unavailable')), \
             patch('urllib.request.build_opener',side_effect=AssertionError('no HTTP')):
            code,result=self.invoke(enable_remote=True,spend_plan=path,spend_cell='one',proposal_contract='v2')
        self.assertEqual((code,result['status']),(2,'rejected'))
        self.assertEqual(result['global_spend']['requests'],0)
        self.assertEqual(result['global_spend']['unknown_reserved_usd'],'0.000000')
        self.assertEqual(result['global_spend']['finished_cells'],1)
        self.assertFalse(self.out.exists())

    def test_unreconciled_global_ledger_never_looks_like_zero_spend(self):
        plan,path=self.spend_plan()
        opener=InterceptedOfficialOpener([{'status':'no_candidate','summary':'No edit.','edits':[]}])
        with patch.object(entry,'_read_runtime_credential',return_value='DUMMY-KEY'), \
             patch('urllib.request.build_opener',return_value=opener):
            code,result=self.invoke(enable_remote=True,spend_plan=path,spend_cell='one',proposal_contract='v2')
        self.assertEqual(code,0)
        (Path(plan['ledger_root'])/'plan.json').write_text('{}')
        self.out=self.root/'unreconciled'
        with patch.object(entry,'_read_runtime_credential',side_effect=AssertionError('no Key')):
            code,result=self.invoke(enable_remote=True,spend_plan=path,spend_cell='two',proposal_contract='v2')
        self.assertEqual((code,result['status']),(2,'rejected'))
        self.assertIsNone(result['global_spend'])
        self.assertEqual(result['spend_status'],'unreconciled')
        self.assertFalse(self.out.exists())

    def test_runtime_timeout_preserves_global_reserve_and_stops_next_cell(self):
        plan,path=self.spend_plan();opener=InterceptedOfficialOpener([TimeoutError()])
        with patch.object(entry,'_read_runtime_credential',return_value='DUMMY-KEY'), \
             patch('urllib.request.build_opener',return_value=opener):
            code,result=self.invoke(enable_remote=True,spend_plan=path,spend_cell='one',proposal_contract='v2')
        self.assertEqual((code,result['status']),(2,'provider_outcome_unknown'))
        held=result['global_spend']['unknown_reserved_usd'];self.assertNotEqual(held,'0.000000')
        self.out=self.root/'second'
        with patch.object(entry,'_read_runtime_credential',side_effect=AssertionError('no retry Key')), \
             patch('urllib.request.build_opener',side_effect=AssertionError('no retry HTTP')):
            code,result=self.invoke(enable_remote=True,spend_plan=path,spend_cell='two',proposal_contract='v2')
        self.assertEqual((code,result['status']),(2,'rejected'))
        self.assertEqual(result['provider_requests'],0)
        self.assertEqual(result['global_spend']['unknown_reserved_usd'],held)
        self.assertTrue(result['global_spend']['stopped']);self.assertFalse(self.out.exists())

    def test_failed_durable_settlement_keeps_global_reserve_even_with_local_usage(self):
        plan,path=self.spend_plan()
        opener=InterceptedOfficialOpener([{'status':'no_candidate','summary':'No edit.','edits':[]}])
        with patch.object(entry,'_read_runtime_credential',return_value='DUMMY-KEY'), \
             patch('urllib.request.build_opener',return_value=opener), \
             patch('macr.providers.repair_spend.RepairSpendLedger.settle',side_effect=OSError('DUMMY write failure')):
            code,result=self.invoke(enable_remote=True,spend_plan=path,spend_cell='one',proposal_contract='v2')
        self.assertEqual(code,2)
        self.assertNotEqual(result['known_usd'],'0.000000')
        self.assertEqual(result['global_spend']['known_usd'],'0.000000')
        self.assertNotEqual(result['global_spend']['unknown_reserved_usd'],'0.000000')
        self.assertTrue(result['global_spend']['stopped'])

    def test_duplicate_cell_and_global_identity_cannot_reset_in_new_output(self):
        plan,path=self.spend_plan();reply={'status':'no_candidate','summary':'No edit.','edits':[]}
        opener=InterceptedOfficialOpener([reply],fingerprint='first-fingerprint')
        with patch.object(entry,'_read_runtime_credential',return_value='DUMMY-KEY'), \
             patch('urllib.request.build_opener',return_value=opener):
            code,result=self.invoke(enable_remote=True,spend_plan=path,spend_cell='one',proposal_contract='v2')
        self.assertEqual(code,0);known=result['global_spend']['known_usd'];self.out=self.root/'duplicate'
        with patch.object(entry,'_read_runtime_credential',side_effect=AssertionError('no repeat Key')):
            code,result=self.invoke(enable_remote=True,spend_plan=path,spend_cell='one',proposal_contract='v2')
        self.assertEqual(code,2);self.assertEqual(result['global_spend']['known_usd'],known)
        self.assertFalse(self.out.exists());self.out=self.root/'identity-drift'
        opener=InterceptedOfficialOpener([reply],fingerprint='second-fingerprint')
        with patch.object(entry,'_read_runtime_credential',return_value='DUMMY-KEY'), \
             patch('urllib.request.build_opener',return_value=opener):
            code,result=self.invoke(enable_remote=True,spend_plan=path,spend_cell='two',proposal_contract='v2')
        self.assertEqual(code,2);self.assertTrue(result['global_spend']['stopped'])
        self.assertEqual(result['global_spend']['requests'],2)
        self.assertNotEqual(result['global_spend']['known_usd'],known)

    def test_credential_reader_fails_before_echo_fallback_or_non_tty_read(self):
        import getpass,warnings
        consumed=[]
        def warning_reader(*args,**kwargs):
            warnings.warn('DUMMY echo warning',getpass.GetPassWarning);consumed.append(True);return 'DUMMY-KEY'
        with patch('sys.stdin.isatty',return_value=False),patch('getpass.getpass',side_effect=AssertionError('no read')):
            with self.assertRaises(ProviderError):entry._read_runtime_credential()
        with patch('sys.stdin.isatty',return_value=True),patch('getpass.getpass',side_effect=warning_reader):
            with self.assertRaises(ProviderError):entry._read_runtime_credential()
        self.assertEqual(consumed,[])

    def proposal(self, old=OLD, new=NEW):
        return {'status':'candidate','summary':'Intercepted response, not a real model.',
                'edits':[{'file':'builder.py','old':old,'new':new}]}

    def observation(self, files, failed=()):
        names = loop.test_manifest(files,['checks.py'])
        return {'source_id':loop.source_id(files),'execution_status':'completed',
                'outcomes':{n:'failed' if n in failed else 'passed' for n in names}}

    def test_normal_cli_default_never_enters_network(self):
        code, result = self.invoke()
        self.assertEqual(code, 2); self.assertEqual(result['status'], 'provider_disabled')
        self.assertEqual(result['provider_requests'], 0)
        self.assertEqual(self.provider.snapshot()['requests'], 0)

    def test_repository_checkout_entrypoint_routes_repair_without_pythonpath(self):
        from pathlib import Path
        project=Path(__file__).resolve().parents[1]
        self.assertEqual(Path(cli.__file__).resolve(),project/'src/macr/cli.py')
        # Checkout type (.git file versus directory) is not product behavior.
        # Keep this declaration check usable on the project's Python 3.10 floor.
        metadata=(project/'pyproject.toml').read_text()
        scripts=re.search(r'(?ms)^\[project\.scripts\]\s*\n(.*?)(?=^\[|\Z)',metadata)
        self.assertIsNotNone(scripts)
        self.assertRegex(scripts.group(1),r'(?m)^agent-review\s*=\s*"macr\.cli:main"\s*$')
        transport=InterceptedTransport([{'status':'no_candidate','summary':'Intercepted checkout entry.','edits':[]}])
        provider=RepairProviderBinding(self.config,transport=transport)
        args=[str(project/'cli/agent_review.py'),'repair','--repo',str(self.repo),'--task',str(self.task_file),
              '--model-config',str(self.config_file),'--out',str(self.out),'--strategy','direct_api']
        output=io.StringIO()
        with patch('sys.argv',args),patch.object(entry,'_make_provider',return_value=provider), \
             patch.object(entry,'_verification_backend',return_value=None),redirect_stdout(output):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path(str(project/'cli/agent_review.py'),run_name='__main__')
        self.assertEqual(stopped.exception.code,0)
        self.assertEqual(json.loads(output.getvalue())['status'],'no_candidate')
        self.assertEqual(len(transport.requests),1)
        self.assertEqual(loop.verify_loop(self.out)['provider_requests'],1)

    def test_debugger_without_verifier_stops_before_any_provider(self):
        transport = InterceptedTransport([self.proposal()])
        code, result = self.invoke('api_debugger', transport)
        self.assertEqual(result['status'], 'verification_unavailable'); self.assertEqual(code,2)
        self.assertEqual(transport.requests, [])

    def additional_test_file(self):
        body = ('import unittest\nclass MoreChecks(unittest.TestCase):\n'
                '    def test_secondary(self):\n        self.assertTrue(True)\n').encode()
        self.files['more_checks.py'] = body
        (self.repo/'more_checks.py').write_bytes(body)
        self.task['source_files'] = loop._hashes(self.files)
        self.task['test_paths'] = ['more_checks.py', 'checks.py']
        self.task_file.write_text(json.dumps(self.task))
        return loop.test_manifest(self.files, self.task['test_paths'])

    def test_unsorted_test_paths_preserve_default_stops_and_reload(self):
        names = self.additional_test_file()
        for strategy, status in (('direct_api', 'provider_disabled'),
                                 ('api_debugger', 'verification_unavailable')):
            with self.subTest(strategy=strategy):
                self.out = self.root/strategy
                code, result = self.invoke(strategy)
                self.assertEqual((code, result['status']), (2, status))
                report = loop.verify_loop(self.out)
                self.assertEqual(report['checks'][0]['spec']['selectors'], ['checks', 'more_checks'])
                self.assertEqual(loop._read(self.out, 'claim.json')['config']['test_names'], names)
                self.assertEqual(report['provider_requests'], 0)
                self.assertFalse(report['target_code_executed'])

    def test_unsorted_test_paths_stay_bound_through_candidate_feedback(self):
        names = self.additional_test_file()
        fixed = deepcopy(self.files)
        fixed['builder.py'] = fixed['builder.py'].replace(OLD.encode(), NEW.encode())
        def observed(files, failed=()):
            return {'source_id':loop.source_id(files), 'execution_status':'completed',
                    'outcomes':{name:'failed' if name in failed else 'passed' for name in names}}
        verifier = loop.ReplayVerifier([observed(self.files, names[:1]), observed(fixed)])
        transport = InterceptedTransport([self.proposal()])
        code, result = self.invoke('api_debugger', transport, verifier)
        self.assertEqual((code, result['status']), (0, 'simulation_passed'))
        report = loop.verify_loop(self.out)
        self.assertEqual(len(report['checks']), 2)
        for check in report['checks']:
            self.assertEqual(check['spec']['selectors'], ['checks', 'more_checks'])
            self.assertEqual(set(check['outcomes']), set(names))
        payload = json.loads(transport.requests[0]['messages'][1]['content'])
        self.assertEqual(payload['protected_paths'], ['checks.py', 'more_checks.py'])
        for path in self.task['test_paths']:
            self.assertEqual(payload['files'][path], self.files[path].decode())
            self.assertEqual((self.repo/path).read_bytes(), self.files[path])
        self.assertEqual(report['provider_requests'], 1)
        self.assertFalse(report['target_code_executed'])
        self.assertFalse(report['repaired'])

    def test_direct_cli_uses_existing_provider_serialization_and_same_meter(self):
        transport = InterceptedTransport([self.proposal(),
            {'status':'insufficient_evidence','summary':'Unverified; need a supported test backend.','edits':[]}])
        code, result = self.invoke(transport=transport)
        self.assertEqual(code, 0); self.assertEqual(result['status'], 'insufficient_evidence')
        self.assertEqual(result['provider_requests'], 2)
        report = loop.verify_loop(self.out)
        self.assertFalse(report['repaired']); self.assertFalse(report['target_code_executed'])
        for wire in transport.requests:
            self.assertEqual(wire['model'], self.config['model'])
            self.assertEqual(wire['max_tokens'], self.config['limits']['max_output_per_request'])
            self.assertEqual(wire['reasoning_effort'], 'high')
            payload=json.loads(wire['messages'][1]['content'])
            self.assertEqual(payload['feedback']['verification'], 'not_performed')
            self.assertNotIn('outcomes',payload['feedback'])
        self.assertEqual(report['input_tokens'], 200); self.assertEqual(report['output_tokens'],100)
        self.assertEqual(report['provider_accounting']['known_usd'], '0.000180')
        self.assertEqual((self.repo/'builder.py').read_bytes(),self.files['builder.py'])

    def test_debugger_uses_same_flow_and_failed_feedback_with_intercepted_adapter(self):
        names=loop.test_manifest(self.files,['checks.py'])
        wrong=deepcopy(self.files); wrong['builder.py']=wrong['builder.py'].replace(OLD.encode(),b'return 2000')
        fixed=deepcopy(wrong); fixed['builder.py']=fixed['builder.py'].replace(b'return 2000',NEW.encode())
        verifier=loop.ReplayVerifier([self.observation(self.files,names[:1]),self.observation(wrong,names[1:]),
                                     self.observation(fixed)])
        transport=InterceptedTransport([self.proposal(new='return 2000'),self.proposal(old='return 2000')])
        code,result=self.invoke('api_debugger',transport,verifier)
        self.assertEqual(code,0,result); self.assertEqual(result['status'],'simulation_passed')
        report=loop.verify_loop(self.out)
        self.assertEqual(report['provider_requests'],2)
        self.assertEqual(report['provider_accounting']['known_usd'],'0.000180')
        payload=json.loads(transport.requests[1]['messages'][1]['content'])
        self.assertEqual(payload['feedback']['outcomes'][names[1]],'failed')
        self.assertEqual(payload['files']['checks.py'],SOURCES['checks.py'])
        self.assertFalse(report['repaired']); self.assertTrue(report['simulation'])

    def test_hidden_score_content_is_never_loaded_or_sent(self):
        hidden=write_files(self.root/'custodian',{'secret.txt':'SYNTHETIC_HIDDEN_SCORE_SENTINEL'})
        transport=InterceptedTransport([{'status':'no_candidate','summary':'No grounded edit.','edits':[]}])
        self.invoke(transport=transport)
        self.assertNotIn('SYNTHETIC_HIDDEN_SCORE_SENTINEL',json.dumps(transport.requests))
        self.assertNotIn(str(hidden),json.dumps(transport.requests))
        self.out=self.root/'bad-run'; self.task['hidden_tests']=str(hidden)
        self.task_file.write_text(json.dumps(self.task))
        transport=InterceptedTransport([self.proposal()])
        code,result=self.invoke(transport=transport)
        self.assertEqual(code,2); self.assertEqual(transport.requests,[])
        self.assertFalse(self.out.exists())

    def test_unlisted_test_or_source_rejected_before_body_or_provider(self):
        (self.repo/'hidden_checks.py').write_text('SYNTHETIC_NOT_AUTHORIZED_TO_READ = True\n')
        transport=InterceptedTransport([self.proposal()])
        code,result=self.invoke(transport=transport)
        self.assertEqual(code,2); self.assertEqual(transport.requests,[])
        self.assertFalse(self.out.exists())

    def test_timeout_retains_reserve_stops_and_cannot_repeat_output(self):
        transport=InterceptedTransport([TimeoutError(),self.proposal()])
        code,result=self.invoke(transport=transport)
        self.assertEqual(code,2); self.assertEqual(result['status'],'provider_outcome_unknown')
        self.assertEqual(len(transport.requests),1)
        self.assertNotEqual(result['unknown_reserved_usd'],'0.000000')
        code,result=self.invoke(transport=transport)
        self.assertEqual(code,2); self.assertEqual(len(transport.requests),1)

    def test_test_edit_never_reaches_candidate_validation(self):
        p=self.proposal();p['edits'][0]['file']='checks.py'
        code,result=self.invoke(transport=InterceptedTransport([p]))
        self.assertEqual(code,2); self.assertEqual(result['status'],'proposal_invalid')
        self.assertEqual((self.repo/'checks.py').read_text(),SOURCES['checks.py'])

    def test_source_race_after_manifest_validation_stops_before_dispatch(self):
        transport=InterceptedTransport([self.proposal()]); output=io.StringIO()
        def build(config):
            (self.repo/'builder.py').write_text('raise RuntimeError("never executed")\n')
            return RepairProviderBinding(config,transport=transport)
        args=['agent-review','repair','--repo',str(self.repo),'--task',str(self.task_file),
              '--model-config',str(self.config_file),'--out',str(self.out),'--strategy','direct_api']
        with patch('sys.argv',args),patch.object(entry,'_make_provider',side_effect=build),redirect_stdout(output):
            with self.assertRaises(SystemExit) as result:cli.main()
        self.assertEqual(result.exception.code,2)
        self.assertEqual(transport.requests,[]);self.assertFalse(self.out.exists())

    def test_added_unlisted_source_is_not_read_during_second_inventory(self):
        transport=InterceptedTransport([self.proposal()]);output=io.StringIO()
        original_read=loop.read_safe
        def read(root,name,*args,**kwargs):
            if name=='hidden_checks.py':raise AssertionError('unlisted body must not be read')
            return original_read(root,name,*args,**kwargs)
        def build(config):
            (self.repo/'hidden_checks.py').write_text('SYNTHETIC_HIDDEN = True\n')
            return RepairProviderBinding(config,transport=transport)
        args=['agent-review','repair','--repo',str(self.repo),'--task',str(self.task_file),
              '--model-config',str(self.config_file),'--out',str(self.out),'--strategy','direct_api']
        with patch('sys.argv',args),patch.object(entry,'_make_provider',side_effect=build), \
             patch.object(loop,'read_safe',side_effect=read),redirect_stdout(output):
            with self.assertRaises(SystemExit) as result:cli.main()
        self.assertEqual(result.exception.code,2);self.assertEqual(transport.requests,[])

    def test_meter_must_match_normalized_provider_response_and_actual_wire(self):
        self.invoke(transport=InterceptedTransport([{'status':'no_candidate','summary':'No edit.','edits':[]}]))
        report_path=self.out/'report.json';report=json.loads(report_path.read_text())
        meter=report['provider_accounting'];meter['calls'][0]['wire_sha256']='0'*64
        (self.out/'provider-accounting.json').write_text(json.dumps(meter))
        report['artifact_hashes']=loop._file_hashes(self.out,skip=('report.json',))
        report_path.write_text(json.dumps(report))
        with self.assertRaises(ValueError):loop.verify_loop(self.out)

    def test_internally_consistent_meter_still_cannot_replace_response_usage(self):
        self.invoke(transport=InterceptedTransport([{'status':'no_candidate','summary':'No edit.','edits':[]}]))
        path=self.out/'report.json';report=json.loads(path.read_text());meter=report['provider_accounting']
        call=meter['calls'][0];call['usage']['input_tokens']=0
        call['known_micro_usd']=self.provider._charge(0,call['usage']['output_tokens'])
        meter['input_tokens']=0;meter['known_usd']=f"0.{call['known_micro_usd']:06d}"
        report['input_tokens']=0
        (self.out/'provider-accounting.json').write_text(json.dumps(meter))
        report['artifact_hashes']=loop._file_hashes(self.out,skip=('report.json',))
        path.write_text(json.dumps(report))
        with self.assertRaises(ValueError):loop.verify_loop(self.out)

    def test_application_record_exports_to_independent_scoring_without_success_credit(self):
        from macr.evals.repair_comparison import compile_comparison,build_report
        from pathlib import Path
        transport=InterceptedTransport([self.proposal(),
            {'status':'no_candidate','summary':'Stop without real verification.','edits':[]}])
        self.invoke(transport=transport)
        spec=json.loads((Path(__file__).resolve().parents[1]/'examples/repair_comparison_development.json').read_text())
        spec['tasks']=[{'task_id':self.task['task_id'],'snapshot_sha256':loop.source_id(self.files),
            'issue_sha256':loop.digest(self.task['issue']),
            'visible_tests_sha256':loop.source_id({'checks.py':self.files['checks.py']}),
            'initial_context_sha256':entry.initial_context_id(self.files,self.task['issue'])}]
        spec['model']['configuration_sha256']=loop.digest(self.config)
        spec['model']['output_contract_sha256']=loop.digest(loop.OUTPUT_CONTRACT)
        limits=self.config['limits']
        spec['limits']={'max_requests':limits['max_requests'],'input_tokens':limits['max_total_input_tokens'],
            'output_tokens':limits['max_total_output_tokens'],'max_usd':limits['max_usd']}
        plan=compile_comparison(spec);cell=next(c for c in plan['cells'] if c['arm']=='direct_api')
        packet=entry.score_handoff(self.out,plan,cell['cell_id'])
        self.assertEqual(len(packet['proposal_chain']),2)
        self.assertIsNone(packet['candidate_files'])
        self.assertIsNone(packet['result']['review'])
        report=build_report(plan,{cell['cell_id']:packet['result']})
        self.assertEqual(report['arms']['direct_api']['scored'],0)
        self.assertEqual(report['arms']['api_debugger']['statuses']['not_attempted'],1)
        self.assertEqual(report['pairs']['not_comparable'],1)
        self.out=self.root/'timeout-before-model'
        verifier=loop.ReplayVerifier([{'source_id':loop.source_id(self.files),
            'execution_status':'timeout','outcomes':{}}])
        code,result=self.invoke('api_debugger',InterceptedTransport([]),verifier)
        self.assertEqual(result['status'],'verification_timeout');self.assertEqual(code,2)
        cell=next(c for c in plan['cells'] if c['arm']=='api_debugger')
        packet=entry.score_handoff(self.out,plan,cell['cell_id'])
        self.assertEqual(packet['result']['requests'],0)
        self.assertEqual(packet['result']['status'],'verification_timeout')
        self.assertGreater(packet['result']['timing_ms']['total'],0)
        report=build_report(plan,{cell['cell_id']:packet['result']})
        self.assertEqual(report['arms']['api_debugger']['statuses']['verification_timeout'],1)
        self.assertEqual(report['arms']['api_debugger']['scored'],0)

    def test_settled_provider_overrun_exports_actual_failure_instead_of_losing_denominator(self):
        from macr.evals.repair_comparison import compile_comparison, build_report
        from pathlib import Path

        class OverrunTransport(InterceptedTransport):
            def post(inner, *args):
                reply = super().post(*args)
                reply['usage'] = {'prompt_tokens':inputs, 'completion_tokens':outputs,
                                  'total_tokens':inputs+outputs}
                return reply

        for inputs, outputs, cost, finish in (
                (25000,50,'0.007560','stop'), (100,4000,'0.004830','stop'),
                (9000,50,'0.002760','stop'), (100,1100,'0.001350','stop'),
                (3500000,50,'1.050060','stop'), (25000,50,'0.007560','length')):
            with self.subTest(inputs=inputs,outputs=outputs,finish=finish):
                self.out = self.root/f'overrun-{inputs}-{outputs}-{finish}'
                transport = OverrunTransport([self.proposal()], finish_reason=finish)
                code, result = self.invoke(transport=transport)
                self.assertEqual((code,result['status']), (2,'provider_response_rejected'))
                persisted = loop.verify_loop(self.out)
                self.assertEqual(persisted['provider_accounting']['known_usd'],cost)
                self.assertEqual(persisted['provider_accounting']['requests'],1)
                self.assertTrue(persisted['provider_accounting']['stopped'])
                spec = json.loads((Path(__file__).resolve().parents[1]/
                                   'examples/repair_comparison_development.json').read_text())
                spec['tasks'] = [{'task_id':self.task['task_id'],
                    'snapshot_sha256':loop.source_id(self.files), 'issue_sha256':loop.digest(self.task['issue']),
                    'visible_tests_sha256':loop.source_id({'checks.py':self.files['checks.py']}),
                    'initial_context_sha256':entry.initial_context_id(self.files,self.task['issue'])}]
                spec['model']['configuration_sha256'] = loop.digest(self.config)
                spec['model']['output_contract_sha256'] = loop.digest(loop.OUTPUT_CONTRACT)
                limits = self.config['limits']
                spec['limits'] = {'max_requests':limits['max_requests'],
                    'input_tokens':limits['max_total_input_tokens'],
                    'output_tokens':limits['max_total_output_tokens'], 'max_usd':limits['max_usd']}
                plan = compile_comparison(spec)
                cell = next(c for c in plan['cells'] if c['arm']=='direct_api')
                packet = entry.score_handoff(self.out,plan,cell['cell_id'])
                row = packet['result']
                self.assertEqual((row['status'],row['input_tokens'],row['output_tokens']),
                                 ('budget_exceeded',inputs,outputs))
                self.assertEqual((row['known_usd'],row['unknown_reserved_usd']), (cost,'0.000000'))
                self.assertIsNone(row['review']); self.assertIsNone(packet['final_output'])
                self.assertIsNone(packet['candidate_files'])
                report = build_report(plan,{cell['cell_id']:row})
                self.assertTrue(report['must_stop'])
                self.assertEqual(report['actual_known_usd'],cost)
                self.assertEqual(report['arms']['direct_api']['scored'],0)
                self.assertEqual(report['arms']['api_debugger']['statuses']['not_attempted'],1)
                self.assertEqual(report['pairs']['not_comparable'],1)

    def test_explicit_v2_finalizes_same_candidate_for_both_strategies_without_extra_check(self):
        from macr.evals.repair_comparison import compile_comparison, build_report
        from pathlib import Path
        fixed = dict(self.files);fixed['builder.py']=fixed['builder.py'].replace(OLD.encode(),NEW.encode())
        names = loop.test_manifest(self.files,['checks.py'])
        for strategy in ('direct_api','api_debugger'):
            with self.subTest(strategy=strategy):
                self.out=self.root/('finalize-'+strategy)
                verifier=loop.ReplayVerifier([self.observation(self.files,names[:1]),
                                              self.observation(fixed,names[:1])])
                transport=InterceptedTransport([self.proposal(),
                    {'status':'finalize_current','summary':'Keep this candidate for independent evaluation.','edits':[]}])
                code,result=self.invoke(strategy,transport,verifier,proposal_contract='v2')
                self.assertEqual((code,result['status']),(0,'candidate_finalized'))
                report=loop.verify_loop(self.out)
                self.assertEqual(report['provider_requests'],2)
                self.assertEqual(len(report['checks']),2)
                self.assertEqual(report['final_source_id'],loop.source_id(fixed))
                self.assertEqual((report['repaired'],report['target_code_executed']),(False,False))
                self.assertIn('finalize_current',json.dumps(transport.requests[1]['messages']))
                spec=json.loads((Path(__file__).resolve().parents[1]/'examples/repair_comparison_development.json').read_text())
                spec['tasks']=[{'task_id':self.task['task_id'],'snapshot_sha256':loop.source_id(self.files),
                    'issue_sha256':loop.digest(self.task['issue']),
                    'visible_tests_sha256':loop.source_id({'checks.py':self.files['checks.py']}),
                    'initial_context_sha256':entry.initial_context_id(self.files,self.task['issue'])}]
                spec['model']['configuration_sha256']=loop.digest(self.config)
                spec['model']['output_contract_sha256']=loop.digest(loop.output_contract('v2'))
                limits=self.config['limits']
                spec['limits']={'max_requests':limits['max_requests'],
                    'input_tokens':limits['max_total_input_tokens'],
                    'output_tokens':limits['max_total_output_tokens'],'max_usd':limits['max_usd']}
                plan=compile_comparison(spec);cell=next(c for c in plan['cells'] if c['arm']==strategy)
                packet=entry.score_handoff(self.out,plan,cell['cell_id'])
                self.assertEqual(packet['final_output']['status'],'finalize_current')
                self.assertEqual(len(packet['proposal_chain']),2)
                self.assertEqual(packet['candidate_files']['builder.py'].encode(),fixed['builder.py'])
                self.assertEqual(packet['candidate_files']['checks.py'].encode(),self.files['checks.py'])
                self.assertIsNone(packet['result']['review'])
                self.assertEqual(packet['result']['verdict'],'defect')
                aggregate=build_report(plan,{cell['cell_id']:packet['result']})
                self.assertEqual(aggregate['arms'][strategy]['scored'],0)
                old_spec=deepcopy(spec);old_spec['model']['output_contract_sha256']=loop.digest(loop.OUTPUT_CONTRACT)
                old_plan=compile_comparison(old_spec)
                with self.assertRaises(ValueError):
                    entry.score_handoff(self.out,old_plan,cell['cell_id'])
