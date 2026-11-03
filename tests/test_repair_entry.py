"""Normal CLI and real provider encoder/parser, with all I/O intercepted."""
from copy import deepcopy
import io
import json
import re
import runpy
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
    def __init__(self, replies): self.replies, self.requests = list(replies), []
    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        wire = json.loads(body); self.requests.append(wire)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception): raise reply
        payload = json.loads(wire['messages'][1]['content'])
        output = {key:payload[key] for key in ('base_content_id','hypothesis_id','evidence_ids')}
        output.update(reply)
        return {'id':'fake-request','model':wire['model'],'system_fingerprint':'fixture-fingerprint',
            'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':json.dumps(output),
                                                       'reasoning_content':'synthetic'}}],
            'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150}}


class RepairEntryTests(TestCase):
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

    def invoke(self, strategy='direct_api', transport=None, verifier=None):
        output = io.StringIO(); provider = RepairProviderBinding(self.config, transport=transport)
        self.provider = provider
        args = ['agent-review','repair','--repo',str(self.repo),'--task',str(self.task_file),
                '--model-config',str(self.config_file),'--out',str(self.out),'--strategy',strategy]
        with patch('sys.argv', args), patch.object(entry, '_make_provider', return_value=provider), \
             patch.object(entry, '_verification_backend', return_value=verifier), redirect_stdout(output):
            with self.assertRaises(SystemExit) as exited: cli.main()
        return exited.exception.code, json.loads(output.getvalue())

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
