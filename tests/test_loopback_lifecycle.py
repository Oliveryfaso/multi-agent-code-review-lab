"""Tiny real TCP fixtures: only this process, kernel-assigned IPv4 loopback ports.

No model, child service, HTTP request, external host or filesystem fixture.
Direct execution has a 30-second guard; every socket operation has one second.
"""
import errno
import os
from pathlib import Path
import signal
import socket
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from macr.providers.b11521_evidence import listener_port

OBSERVATIONS=[]
CLOSED=[]

class LoopbackLifecycleTests(unittest.TestCase):
    def setUp(self):self.owned=[]

    def tearDown(self):
        for sock in self.owned:sock.close()
        closed=all(sock.fileno()==-1 for sock in self.owned);CLOSED.append(closed)
        self.assertTrue(closed)

    def socket(self,reuse=False):
        sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM);sock.settimeout(1);self.owned.append(sock)
        if reuse:sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        return sock

    def listener(self):
        sock=self.socket(reuse=True);sock.bind(('127.0.0.1',0));sock.listen(1);return sock

    def exchange_and_close(self,server):
        address=server.getsockname();client=self.socket();client.connect(address)
        peer,_=server.accept();peer.settimeout(1);self.owned.append(peer)
        peer.sendall(b'x');self.assertEqual(client.recv(1),b'x')
        peer.shutdown(socket.SHUT_WR);self.assertEqual(client.recv(1),b'')
        client.close();peer.close();server.close();return address

    def test_two_consecutive_kernel_services_use_owned_socket_without_handoff(self):
        ports=[]
        for _ in range(2):
            server=self.listener();port=server.getsockname()[1];ports.append(port)
            conflict=self.socket()
            with self.assertRaises(OSError) as error:conflict.bind(('127.0.0.1',port))
            self.assertEqual(error.exception.errno,errno.EADDRINUSE)
            log=f'srv  llama_server: listening on http://127.0.0.1:{port}\n'.encode()
            output=f'p{os.getpid()}\nn127.0.0.1:{port}\n'
            self.assertEqual(listener_port(log,pid=os.getpid(),returncode=0,output=output),port)
            self.exchange_and_close(server)
        OBSERVATIONS.append({'fixture':'two_consecutive_kernel_services','services':2,'ports':ports,'one_byte_per_service':True})

    def test_closed_service_can_fail_old_bind_check_without_listener(self):
        address=self.exchange_and_close(self.listener())
        with self.assertRaises(ConnectionRefusedError):self.socket().connect(address)
        old=self.socket()
        with self.assertRaises(OSError) as error:old.bind(address)
        self.assertEqual(error.exception.errno,errno.EADDRINUSE)
        matching=self.socket(reuse=True);matching.bind(address);matching.close()
        OBSERVATIONS.append({'fixture':'closed_active_connection','listener_absent':True,'ordinary_bind_errno':error.exception.errno,'reuseaddr_bind_succeeded':True,'historical_state_proven':False})

    def test_real_live_listener_conflict_is_not_shared_or_stopped(self):
        server=self.listener();candidate=self.socket(reuse=True)
        with self.assertRaises(OSError) as error:candidate.bind(server.getsockname())
        self.assertEqual(error.exception.errno,errno.EADDRINUSE)
        self.exchange_and_close(server)
        OBSERVATIONS.append({'fixture':'live_listener_conflict','conflict_rejected':True,'existing_fixture_remained_usable':True})

    def test_check_socket_itself_holds_port_until_context_closes(self):
        probe=self.socket();probe.bind(('127.0.0.1',0));address=probe.getsockname()
        candidate=self.socket(reuse=True)
        with self.assertRaises(OSError) as error:candidate.bind(address)
        self.assertEqual(error.exception.errno,errno.EADDRINUSE)
        probe.close();candidate.bind(address);candidate.close()
        OBSERVATIONS.append({'fixture':'own_check_socket','conflict_while_open':True,'released_after_close':True})

    def test_endpoint_mismatch_stops_before_fixture_payload(self):
        server=self.listener();port=server.getsockname()[1];other=port-1 if port>1 else port+1
        log=f'srv  llama_server: listening on http://127.0.0.1:{other}\n'.encode()
        with self.assertRaises(ValueError):listener_port(log,pid=os.getpid(),returncode=0,output=f'p{os.getpid()}\nn127.0.0.1:{port}\n')
        OBSERVATIONS.append({'fixture':'endpoint_race_or_drift','payload_sent':False,'mismatch_rejected':True})

if __name__=='__main__':
    def expired(*unused):raise TimeoutError('loopback_fixture_30_second_limit')
    signal.signal(signal.SIGALRM,expired);signal.alarm(30)
    try:
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(LoopbackLifecycleTests))
        import json
        print(json.dumps({'status':'passed' if result.wasSuccessful() else 'failed','tests_run':result.testsRun,'observations':OBSERVATIONS,'all_owned_sockets_closed':len(CLOSED)==result.testsRun and all(CLOSED),'model_starts':0,'model_requests':0,'external_connections':0}))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    finally:signal.alarm(0)
