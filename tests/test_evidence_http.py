import http.client
import json
from unittest.mock import patch
from uuid import uuid4
from evidence_ui_fixtures import publish_fixture_request, failed_budget_fixture
from macr.investigation.validation import ContractError
from pathlib import Path
import socket
import tempfile
import threading
import unittest

from macr.api.evidence_http import make_server
from macr.api.evidence_workbench import Workbench


class LoopbackProtocolTests(unittest.TestCase):
    def test_repair_capabilities_are_authenticated_nonsecret_launch_metadata(self):
        self.assertEqual(self.request('GET','/api/repair-capabilities')[0],401)
        code,raw,_=self.request('GET','/api/repair-capabilities',headers=self.auth())
        self.assertEqual(code,200);value=json.loads(raw)
        self.assertEqual(value['schema'],'macr.workbench.repair-capabilities.v1')
        self.assertFalse(value['model']['remote_enabled']);self.assertFalse(value['verification']['execution_enabled'])
        self.assertEqual(self.request('GET','/api/repair-capabilities?path=/etc/passwd',headers=self.auth())[0],404)
        from macr.api.repair_runtime import RepairRuntime
        from macr.investigation.repair_loop import digest,output_contract
        from macr.providers.repair_binding import development_config
        import time
        path=Path(self.temp.name)/'approved-plan.json'
        plan={'schema':'macr.repair.spend-plan.v1','run_id':'http-authored','ledger_root':str(Path(self.temp.name)/'ledger'),
            'configuration_sha256':digest(development_config()),'output_contract_sha256':digest(output_contract('v6')),
            'max_requests':1,'max_usd':'0.500000','max_total_input_tokens':24576,'max_total_output_tokens':3072,
            'expires_at':int(time.time())+600,'cells':[{'cell_id':'one','binding_sha256':'a'*64}]}
        path.write_text(json.dumps(plan));runtime=RepairRuntime(path,'one',credential_reader=lambda:'DUMMY-NOT-A-REAL-KEY')
        self.addCleanup(runtime.close);runtime.prepare();self.workbench.repairs.runtime=runtime
        code,raw,_=self.request('GET','/api/repair-capabilities',headers=self.auth());value=json.loads(raw)
        self.assertTrue(value['model']['credential_ready']);self.assertEqual(value['model']['cell_id'],'one')
        self.assertNotIn(b'DUMMY-NOT-A-REAL-KEY',raw);self.assertNotIn(str(path).encode(),raw)
        self.assertNotIn(plan['ledger_root'].encode(),raw)

    def repair_payload(self):
        from macr.investigation import repair_loop as loop
        from macr.providers.repair_binding import development_config
        from repair_fixtures import SOURCES
        repo=Path(self.temp.name)/'repair-source';repo.mkdir()
        files={name:text.encode() for name,text in SOURCES.items()}
        for name,data in files.items():(repo/name).write_bytes(data)
        task=Path(self.temp.name)/'repair-task.json';config=Path(self.temp.name)/'repair-model.json'
        task.write_text(json.dumps({'schema':'macr.repair.task.v1','task_id':'http-invoice',
            'issue':'Delivery must not be discounted.','source_files':loop._hashes(files),
            'editable_paths':['builder.py'],'test_paths':['checks.py']}))
        config.write_text(json.dumps(development_config()))
        return {'intent_id':str(uuid4()),'repo':str(repo),'task':str(task),'model_config':str(config),
            'strategy':'api_debugger','proposal_contract':'v6'},files

    def repair_headers(self):
        return self.auth()|{'Origin':self.origin,'Content-Type':'application/json'}

    def test_repair_http_reaches_normal_entry_and_retains_blocked_result(self):
        payload,_=self.repair_payload()
        code,raw,_=self.request('POST','/api/repairs',headers=self.repair_headers(),body=json.dumps(payload))
        self.assertEqual(code,202);run=json.loads(raw);self.workbench.thread.join(3)
        code,raw,_=self.request('GET','/api/repairs/'+run['id']+'/result',headers=self.auth())
        self.assertEqual(code,200);result=json.loads(raw)
        self.assertEqual(result['state']['state'],'blocked')
        self.assertEqual(result['state']['repair_status'],'verification_unavailable')
        self.assertEqual(result['summary']['provider_requests'],0)
        self.assertFalse(result['summary']['repaired']);self.assertFalse(result['summary']['remote_enabled'])
        self.assertEqual(result['proposals'],[])
        code,raw,_=self.request('GET','/api/repair-history?cursor=0',headers=self.auth())
        self.assertEqual(code,200);self.assertEqual(json.loads(raw)['items'][0]['id'],run['id'])

    def test_repair_http_uses_real_loop_with_authored_transport_and_no_repeat(self):
        from macr.investigation import repair_entry as entry,repair_loop as loop
        from macr.providers.repair_binding import RepairProviderBinding
        from repair_fixtures import OLD,NEW
        from test_workbench_repair import AuthoredTransport
        payload,files=self.repair_payload();fixed=dict(files);fixed['builder.py']=fixed['builder.py'].replace(OLD.encode(),NEW.encode())
        names=loop.test_manifest(files,['checks.py'])
        observe=lambda source,failed:{'source_id':loop.source_id(source),'execution_status':'completed',
            'outcomes':{n:'failed' if n in failed else 'passed' for n in names}}
        replay=loop.ReplayVerifier([observe(files,names[:1]),observe(fixed,())])
        transport=AuthoredTransport([{'status':'candidate','summary':'Exclude delivery.',
            'edits':[{'file':'builder.py','old':OLD,'new':NEW}]}])
        with patch.object(self.workbench.repairs,'provider_factory',lambda config:RepairProviderBinding(config,transport=transport)), \
             patch.object(self.workbench.repairs,'verifier_factory',lambda:replay):
            code,raw,_=self.request('POST','/api/repairs',headers=self.repair_headers(),body=json.dumps(payload))
            self.assertEqual(code,202);run=json.loads(raw);self.workbench.thread.join(3)
            code,raw,_=self.request('GET','/api/repairs/'+run['id']+'/result',headers=self.auth())
            self.assertEqual(code,200);result=json.loads(raw)
            self.assertEqual(result['report']['status'],'simulation_passed');self.assertFalse(result['summary']['repaired'])
            self.assertEqual(result['proposals'][0]['edits'][0]['file'],'builder.py')
            code,raw,_=self.request('POST','/api/repairs',headers=self.repair_headers(),body=json.dumps(payload))
            self.assertEqual(code,202);self.assertEqual(json.loads(raw)['id'],run['id'])
        self.assertEqual(len(transport.requests),1)
        self.assertEqual((Path(payload['repo'])/'builder.py').read_bytes(),files['builder.py'])

    def test_repair_routes_keep_auth_and_reject_runtime_and_arbitrary_output(self):
        payload,_=self.repair_payload()
        self.assertEqual(self.request('POST','/api/repairs',headers={'Origin':self.origin,'Content-Type':'application/json'},body=json.dumps(payload))[0],401)
        self.assertEqual(self.request('POST','/api/repairs',headers=self.auth()|{'Content-Type':'application/json'},body=json.dumps(payload))[0],403)
        for extra in ({'enable_remote':True},{'out':'/tmp/arbitrary-repair'},{'key':'DUMMY-NOT-A-KEY'},
                      {'provider':'official'},{'spend_plan':'/tmp/plan'}):
            with self.subTest(extra=extra):
                self.assertEqual(self.request('POST','/api/repairs',headers=self.repair_headers(),body=json.dumps(payload|extra))[0],400)
        self.assertEqual(self.request('GET','/api/repairs/'+str(uuid4())+'/result?path=/etc/passwd',headers=self.auth())[0],404)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='macr-ui-http-', dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        self.workbench = Workbench(Path(self.temp.name) / 'owned')
        self.addCleanup(self.workbench.shutdown)
        self.server = make_server(self.workbench)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close)
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.workbench.shutdown()
        self.assertFalse(self.thread.is_alive())

    def request(self, method, path, *, headers=None, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def auth(self):
        return {'Authorization': 'Bearer ' + self.server.access_token}

    def raw(self, request):
        with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=2) as stream:
            stream.sendall(request)
            result = b''
            while True:
                part = stream.recv(65536)
                if not part:
                    break
                result += part
            return result

    def test_authenticated_get_without_origin_works_but_unauthenticated_read_does_not(self):
        self.assertEqual(self.request('GET', '/api/history', headers=self.auth())[0], 200)
        self.assertEqual(self.request('GET', '/api/history')[0], 401)
        self.assertEqual(self.request('GET', '/api/history', headers=self.auth() | {'Origin': 'null'})[0], 403)

    def test_post_requires_same_origin_and_strict_json_before_start(self):
        self.assertEqual(self.request('POST', '/api/runs', headers=self.auth(), body=b'{}')[0], 403)
        headers = self.auth() | {'Origin': self.origin, 'Content-Type': 'application/json'}
        self.assertEqual(self.request('POST', '/api/runs', headers=headers, body=b'{"a":1,"a":2}')[0], 400)
        self.assertEqual(self.workbench.history()['items'], [])

    def test_host_and_directory_escape_are_rejected(self):
        self.assertEqual(self.request('GET', '/api/history', headers=self.auth() | {'Host': 'attacker.invalid'})[0], 403)
        self.assertEqual(self.request('GET', '/../../pyproject.toml')[0], 404)
        self.assertEqual(self.request('GET', '/files?path=/etc/passwd', headers=self.auth())[0], 404)

    def test_duplicate_length_chunked_oversized_body_and_headers_fail_closed(self):
        host = ('Host: 127.0.0.1:' + str(self.server.server_port) + '\r\n').encode()
        prefix = b'POST /api/runs HTTP/1.1\r\n' + host
        cases = [b'Content-Length: 0\r\nContent-Length: 0\r\n\r\n',
                 b'Transfer-Encoding: chunked\r\n\r\n', b'Content-Length: 131073\r\n\r\n',
                 b'X-Long: ' + b'x' * 16384 + b'\r\n\r\n']
        for suffix in cases:
            result = self.raw(prefix + suffix)
            self.assertIn(result.split(b'\r\n')[0].split()[1], (b'400', b'413', b'431'))
        self.assertEqual(self.workbench.history()['items'], [])

    def test_report_http_response_includes_current_verified_state(self):
        repo = Path(self.temp.name) / 'source'
        repo.mkdir()
        (repo / 'entry.py').write_text('def read():\n    return 1\n')
        payload = {'intent_id': str(uuid4()), 'repo': str(repo), 'question': 'Read fixture',
                   'entries': ['entry.py:read'], 'symbols': [], 'traceback': '', 'profile_name': 'standard'}
        with patch('macr.investigation.evidence_supervisor.collect_evidence', publish_fixture_request), \
                patch('macr.api.evidence_workbench.read_committed', side_effect=ContractError('history_budget_exceeded', 'max_io_bytes')):
            run = self.workbench.start(payload)
            self.workbench.thread.join(2)
        self.assertEqual(self.workbench.state(run['id'])['verification_status'], 'budget_exceeded')
        status, data, _ = self.request('GET', '/api/runs/' + run['id'] + '/report', headers=self.auth())
        self.assertEqual(status, 200)
        result = json.loads(data)
        self.assertIn('state', result)
        self.assertEqual(result['state']['id'], run['id'])
        self.assertEqual(result['state']['state'], 'ready')
        self.assertEqual(result['state']['verification_status'], 'passed')

    def test_packaged_page_and_assets_are_available_without_directory_serving(self):
        status, data, headers = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn('想查看什么'.encode(), data)
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertEqual(self.request('GET', '/workbench.js')[0], 200)
        self.assertEqual(self.request('GET', '/workbench.css')[0], 200)
        self.assertEqual(self.request('GET', '/evidence_ui/')[0], 404)

    def test_failure_stage_and_budget_are_exposed_without_source_or_export(self):
        repo = Path(self.temp.name) / 'failure-source'; repo.mkdir()
        (repo / 'entry.py').write_text('def read(): return 1\n')
        payload = {'intent_id': str(uuid4()), 'repo': str(repo), 'question': 'Read fixture',
                   'entries': ['entry.py:read'], 'symbols': [], 'traceback': '', 'profile_name': 'standard'}
        with patch('macr.investigation.evidence_supervisor.collect_evidence', failed_budget_fixture):
            run = self.workbench.start(payload); self.workbench.thread.join(2)
        code, raw, _ = self.request('GET', '/api/runs/' + run['id'], headers=self.auth())
        self.assertEqual(code, 200)
        state = json.loads(raw)
        self.assertEqual(state['analysis_failure'], {'code': 'scope_limit', 'field': 'max_metadata_entries',
            'stage': 'supervisor_storage', 'effective': 20000, 'observed': 20001})
        self.assertEqual(state['publication'], 'uncommitted')
        for route in ('/report', '/export?format=json'):
            self.assertNotEqual(self.request('GET', '/api/runs/' + run['id'] + route, headers=self.auth())[0], 200)

    def test_manual_capacity_choice_is_exact_in_http_state_and_report(self):
        from macr.investigation.evidence_profiles import EvidenceProfile
        repo = Path(self.temp.name) / 'capacity-source'; repo.mkdir()
        (repo / 'entry.py').write_text('def read(): return 1\n')
        payload = {'intent_id': str(uuid4()), 'repo': str(repo), 'question': 'Read fixture',
                   'entries': ['entry.py:read'], 'symbols': [], 'traceback': '', 'profile_name': 'standard-capacity'}
        headers = self.auth() | {'Origin': self.origin, 'Content-Type': 'application/json'}
        with patch('macr.investigation.evidence_supervisor.collect_evidence', publish_fixture_request):
            code, raw, _ = self.request('POST', '/api/runs', headers=headers, body=json.dumps(payload))
            self.assertEqual(code, 202)
            run = json.loads(raw); self.workbench.thread.join(2)
        code, raw, _ = self.request('GET', '/api/runs/' + run['id'], headers=self.auth())
        self.assertEqual(code, 200)
        state = json.loads(raw)
        expected = EvidenceProfile.select('standard').to_dict() | {'max_metadata_entries': 100000, 'max_metadata_bytes': 16 * 1024 * 1024}
        self.assertEqual(state['profile_name'], 'standard-capacity')
        self.assertEqual(state['effective_profile'], expected)
        code, raw, _ = self.request('GET', '/api/runs/' + run['id'] + '/report', headers=self.auth())
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)['report']['request']['profile'], expected)
