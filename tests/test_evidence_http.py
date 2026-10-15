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
