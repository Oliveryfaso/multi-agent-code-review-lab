"""Authenticated bounded loopback transport; never serves user directories."""
from collections import deque
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
import json
from pathlib import Path
import re
import secrets
import signal
import socket
import threading
import time
from urllib.parse import parse_qs, urlsplit

from .evidence_workbench import Workbench
from ..investigation.evidence_profiles import load_json
from ..investigation.scope import read_safe
from ..investigation.validation import ContractError

BODY_LIMIT = 128 * 1024
HEADER_LIMIT = 16 * 1024
RESOURCE_LIMIT = 512 * 1024
_RESOURCES = {'/': ('index.html', 'text/html; charset=utf-8'), '/workbench.js': ('workbench.js', 'text/javascript; charset=utf-8'),
              '/workbench.css': ('workbench.css', 'text/css; charset=utf-8')}


class _HeaderReader:
    def __init__(self, stream, remaining):
        self.stream, self.remaining = stream, remaining

    def readline(self, limit=-1):
        data = self.stream.readline(min(self.remaining + 1, limit) if limit >= 0 else self.remaining + 1)
        self.remaining -= len(data)
        if self.remaining < 0:
            raise http.client.HTTPException('header_limit')
        return data


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass  # HTTP input, bootstrap secrets and user data never enter logs.

    def handle_one_request(self):
        try:
            self.raw_requestline = self.rfile.readline(1025)
            if not self.raw_requestline:
                self.close_connection = True
                return
            if len(self.raw_requestline) > 1024:
                self.request_version = 'HTTP/1.1'
                self._error(400, 'input_invalid', 'request_line')
                return
            if not self.parse_request():
                return
            self.close_connection = True
            self._handle()
            self.wfile.flush()
        except (TimeoutError, ConnectionError, OSError):
            self.close_connection = True

    def parse_request(self):
        stream = self.rfile
        self.rfile = _HeaderReader(stream, HEADER_LIMIT - len(self.raw_requestline))
        try:
            return super().parse_request()
        finally:
            self.rfile = stream

    def handle_expect_100(self):
        self._error(417, 'input_invalid', 'expect')
        return False

    def send_error(self, code, message=None, explain=None):
        self._error(code, 'input_invalid', 'http_message')

    def _reply(self, code, data, content_type='application/json; charset=utf-8', *, attachment=None):
        self.close_connection = True
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Connection', 'close')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if attachment:
            self.send_header('Content-Disposition', 'attachment; filename="' + attachment + '"')
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, value):
        data = json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode()
        if len(data) > 5 * 1024 * 1024:
            self._error(413, 'output_limit_unrepresentable', 'http_output')
            return
        self._reply(code, data)

    def _error(self, code, error, field):
        clean = lambda value: value if type(value) is str and re.fullmatch('[a-z][a-z0-9_]{0,63}', value) else 'internal_error'
        self._reply(code, json.dumps({'error': {'code': clean(error), 'field': clean(field)}}, separators=(',', ':')).encode())

    def _handle(self):
        try:
            for name in ('Host', 'Origin', 'Authorization', 'Content-Length', 'Content-Type', 'Transfer-Encoding', 'Expect'):
                if len(self.headers.get_all(name, [])) > 1:
                    self._error(400, 'input_invalid', 'duplicate_header')
                    return
            if self.headers.get('Transfer-Encoding') is not None:
                self._error(400, 'input_invalid', 'transfer_encoding')
                return
            length_text = self.headers.get('Content-Length', '0')
            if not re.fullmatch('[0-9]{1,10}', length_text):
                self._error(400, 'input_invalid', 'content_length')
                return
            length = int(length_text)
            if length > BODY_LIMIT:
                self._error(413, 'input_invalid', 'request_bytes')
                return
            if self.headers.get('Host') != self.server.authority:
                self._error(403, 'transport_forbidden', 'host')
                return
            if not self.server.allow_request():
                self._error(429, 'transport_busy', 'request_rate')
                return
            split = urlsplit(self.path)
            if not self.path.startswith('/') or split.scheme or split.netloc or split.fragment:
                self._error(404, 'route_unavailable', 'route')
                return
            if self.command == 'GET' and not length and split.path in _RESOURCES and not split.query:
                name, content_type = _RESOURCES[split.path]
                trusted = Path(resources.files('macr.api').joinpath('evidence_ui'))
                self._reply(200, read_safe(trusted, name, RESOURCE_LIMIT), content_type)
                return
            if not split.path.startswith('/api/'):
                self._error(404, 'route_unavailable', 'route')
                return
            wanted = 'Bearer ' + self.server.access_token
            supplied = self.headers.get('Authorization', '')
            if not hmac.compare_digest(supplied.encode('utf-8'), wanted.encode('ascii')):
                self._error(401, 'session_required', 'authorization')
                return
            origin = self.headers.get('Origin')
            if origin is not None and origin != self.server.origin or self.command == 'POST' and origin != self.server.origin:
                self._error(403, 'transport_forbidden', 'origin')
                return
            query = parse_qs(split.query, keep_blank_values=True, strict_parsing=True) if split.query else {}
            if any(len(values) != 1 for values in query.values()):
                raise ContractError('input_invalid', 'query')
            workbench = self.server.workbench
            if self.command == 'GET':
                if length:
                    raise ContractError('input_invalid', 'get_body')
                if split.path == '/api/history' and not set(query) - {'cursor'}:
                    text = query.get('cursor', ['0'])[0]
                    if not re.fullmatch('[0-9]{1,9}', text):
                        raise ContractError('input_invalid', 'cursor')
                    self._json(200, workbench.history(int(text)))
                    return
                if split.path == '/api/runs' and set(query) == {'intent_id'}:
                    self._json(200, workbench.by_intent(query['intent_id'][0]))
                    return
                match = re.fullmatch('/api/runs/([0-9a-f-]{36})(?:/(report|export))?', split.path)
                if match:
                    identifier, action = match.groups()
                    if action is None and not query:
                        self._json(200, workbench.state(identifier))
                        return
                    if action == 'report' and not query:
                        result = workbench.report(identifier)
                        self._json(200, {k: result[k] for k in ('report', 'receipt', 'read_usage', 'final', 'state', 'state_persistence')})
                        return
                    if action == 'export' and set(query) == {'format'} and query['format'][0] in ('json', 'markdown'):
                        result = workbench.report(identifier)
                        is_json = query['format'][0] == 'json'
                        self._reply(200, result['json_bytes' if is_json else 'markdown_bytes'],
                                    'application/json' if is_json else 'text/markdown; charset=utf-8',
                                    attachment='evidence-' + identifier + ('.json' if is_json else '.md'))
                        return
            elif self.command == 'POST' and not query:
                if self.headers.get('Content-Type') != 'application/json':
                    raise ContractError('input_invalid', 'content_type')
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ContractError('input_invalid', 'body_incomplete')
                body = load_json(raw.decode('utf-8'), max_bytes=BODY_LIMIT, max_depth=32)
                if split.path == '/api/runs':
                    self._json(202, workbench.start(body))
                    return
                match = re.fullmatch('/api/runs/([0-9a-f-]{36})/cancel', split.path)
                if match and body == {}:
                    self._json(202, workbench.cancel(match[1]))
                    return
                if split.path == '/api/shutdown' and body == {}:
                    workbench.begin_shutdown()
                    self._json(202, {'state': 'stopping'})
                    self.server.request_stop()
                    return
            self._error(404, 'route_unavailable', 'route')
        except ContractError as error:
            status = 400 if error.code in ('input_invalid', 'contract_invalid') else 404 if error.code == 'run_not_found' else 409
            self._error(status, error.code, error.field)
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            self._error(503, 'workbench_unavailable', 'transport')


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = False
    allow_reuse_address = False
    request_queue_size = 4

    def __init__(self, workbench, port):
        self.workbench = workbench
        self.slots = threading.BoundedSemaphore(4)
        self.rate_lock = threading.Lock()
        self.requests = deque()
        self.access_token = secrets.token_urlsafe(32)
        self.stop_requested = False
        super().__init__(('127.0.0.1', port), Handler)
        self.authority = '127.0.0.1:' + str(self.server_port)
        self.origin = 'http://' + self.authority

    def process_request(self, request, client_address):
        request.settimeout(10)
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        pass

    def allow_request(self):
        with self.rate_lock:
            now = time.monotonic()
            while self.requests and now - self.requests[0] >= 1:
                self.requests.popleft()
            if len(self.requests) >= 40:
                return False
            self.requests.append(now)
            return True

    def request_stop(self):
        if not self.stop_requested:
            self.stop_requested = True
            threading.Thread(target=self.shutdown, name='workbench-http-stop', daemon=False).start()


def make_server(workbench, port=0):
    return BoundedServer(workbench, port)


def run_cli(args):
    workbench = None
    server = None
    previous_term = None
    try:
        root = Path(args.data_root) if args.data_root else Path.home() / 'Library/Application Support/MACR Evidence'
        workbench = Workbench(root)
        server = make_server(workbench)
        def interrupt(signum, frame):
            raise KeyboardInterrupt()
        previous_term = signal.signal(signal.SIGTERM, interrupt)
        print(server.origin + '/#' + server.access_token, flush=True)
        try:
            server.serve_forever(poll_interval=.1)
        except KeyboardInterrupt:
            pass
        return 0
    except (ContractError, OSError):
        print('workbench unavailable; inspect the owned data root and runtime boundary', file=__import__('sys').stderr)
        return 4
    finally:
        if workbench:
            workbench.begin_shutdown()
        if server:
            server.server_close()
        if workbench:
            workbench.shutdown()
        if previous_term is not None:
            signal.signal(signal.SIGTERM, previous_term)
