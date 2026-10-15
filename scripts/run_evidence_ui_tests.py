"""Bounded own-product fixtures; native analysis/model/target execution blocked."""
from contextlib import ExitStack
import io
import errno
import json
import os
from pathlib import Path
import resource
import signal
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

PROJECT = Path(__file__).resolve().parents[1]
NAMES = ('test_evidence_reader', 'test_evidence_workbench', 'test_evidence_http', 'test_evidence_ui_cli', 'test_evidence_ui_guard')
EXPECTED = 41
ALLOCATION_LIMIT = 64 * 1024 * 1024
RSS_LIMIT = 256 * 1024 * 1024


def physical_usage(roots, deadline):
    from macr.investigation.scope import open_directory
    allocated, entries, metadata = 0, 0, 0

    def charge(info, name):
        nonlocal allocated, entries, metadata
        if time.monotonic() >= deadline:
            raise RuntimeError('fixture_deadline')
        allocated += info.st_blocks * 512
        entries += 1
        metadata += len(os.fsencode(name)) + 128
        if entries > 100000 or metadata > 16 * 1024 * 1024:
            raise RuntimeError('fixture_metadata_limit')
        if allocated > ALLOCATION_LIMIT:
            raise RuntimeError('fixture_allocation_limit')

    def walk(fd, depth):
        if depth > 64:
            raise RuntimeError('fixture_depth_limit')
        with os.scandir(fd) as children:
            for child in children:
                if time.monotonic() >= deadline:
                    raise RuntimeError('fixture_deadline')
                try:
                    info = os.stat(child.name, dir_fd=fd, follow_symlinks=False)
                    charge(info, child.name)
                    if stat.S_ISDIR(info.st_mode):
                        nested = os.open(child.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                        try:
                            opened = os.fstat(nested)
                            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                                raise RuntimeError('fixture_directory_changed')
                            walk(nested, depth + 1)
                        finally:
                            os.close(nested)
                except OSError as error:
                    if error.errno not in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
                        raise
                    # A concurrent fixture deletion or link replacement is skipped.
    for root in roots:
        with open_directory(root) as fd:
            charge(os.fstat(fd), root.name)
            walk(fd, 0)
    return allocated


def main():
    if not sys.flags.isolated or not sys.dont_write_bytecode or not sys.flags.no_site:
        raise SystemExit('Run with python -I -S -B scripts/run_evidence_ui_tests.py')
    sys.path[:0] = [str(PROJECT / 'src'), str(PROJECT / 'tests'), str(PROJECT / 'scripts')]
    from run_static_evidence_tests import ProductOrigins
    finder = ProductOrigins()
    sys.meta_path.insert(0, finder)
    started = time.monotonic()
    deadline = started + 120
    previous = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, lambda *args: (_ for _ in ()).throw(RuntimeError('fixture_deadline')))
    signal.alarm(120)
    attempts, ports = [0], set()
    original_bind, original_connect, original_connect_ex = socket.socket.bind, socket.socket.connect, socket.socket.connect_ex
    original_temp = tempfile.TemporaryDirectory
    def blocked(*args, **kwargs):
        attempts[0] += 1
        raise RuntimeError('analysis_or_external_dispatch_forbidden')
    def bind(stream, address):
        if not isinstance(address, tuple) or address != ('127.0.0.1', 0):
            return blocked()
        original_bind(stream, address)
        ports.add(stream.getsockname()[1])
    def allowed_address(address):
        return isinstance(address, tuple) and address[0] == '127.0.0.1' and address[1] in ports
    def connect(stream, address):
        return original_connect(stream, address) if allowed_address(address) else blocked()
    def connect_ex(stream, address):
        return original_connect_ex(stream, address) if allowed_address(address) else blocked()
    run_dir = PROJECT / 'artifacts/test-runs' / ('evidence-ui-' + str(uuid4()))
    run_dir.mkdir(parents=True)
    output = io.StringIO()
    try:
        with original_temp(prefix='macr-ui-fixtures-', dir=Path(tempfile.gettempdir()).resolve()) as private, ExitStack() as stack:
            def owned_temp(*args, **kwargs):
                kwargs['dir'] = private
                return original_temp(*args, **kwargs)
            for target in ('subprocess.Popen', 'os.system', 'urllib.request.urlopen'):
                stack.enter_context(patch(target, side_effect=blocked))
            for name in ('fork', 'forkpty', 'posix_spawn', 'posix_spawnp', 'execv', 'execve', 'execvp', 'execvpe', 'spawnv', 'spawnve', 'spawnvp', 'spawnvpe'):
                if hasattr(os, name):
                    stack.enter_context(patch.object(os, name, side_effect=blocked))
            stack.enter_context(patch('macr.investigation.evidence_supervisor.collect_evidence', side_effect=blocked))
            stack.enter_context(patch('macr.investigation.evidence_supervisor._host_pressure', side_effect=blocked))
            stack.enter_context(patch('macr.investigation.evidence_supervisor._worker_rss', side_effect=blocked))
            stack.enter_context(patch.object(socket.socket, 'bind', bind))
            stack.enter_context(patch.object(socket.socket, 'connect', connect))
            stack.enter_context(patch.object(socket.socket, 'connect_ex', connect_ex))
            stack.enter_context(patch.object(tempfile, 'TemporaryDirectory', owned_temp))
            guard_errors, peak = [], {'allocated_bytes': 0, 'rss_bytes': 0}
            stop = threading.Event()
            def sample():
                allocated = physical_usage((Path(private), run_dir), deadline)
                rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                rss = int(rss if sys.platform == 'darwin' else rss * 1024)
                peak['allocated_bytes'] = max(peak['allocated_bytes'], allocated)
                peak['rss_bytes'] = max(peak['rss_bytes'], rss)
                if rss > RSS_LIMIT:
                    raise RuntimeError('fixture_rss_limit')
            def monitor():
                try:
                    while not stop.is_set():
                        sample()
                        stop.wait(.02)
                except BaseException as error:
                    guard_errors.append(type(error).__name__ + ':' + str(error)[:128])
            thread = threading.Thread(target=monitor, name='owned-fixture-monitor', daemon=False)
            thread.start()
            try:
                suite = unittest.defaultTestLoader.loadTestsFromNames(NAMES)
                if suite.countTestCases() != EXPECTED:
                    raise RuntimeError('fixed_fixture_selection')
                result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
            finally:
                stop.set()
                thread.join(2)
            sample()
            remaining = [thread.name for thread in threading.enumerate() if thread is not threading.main_thread()]
            accepted = result.wasSuccessful() and result.testsRun == EXPECTED and not attempts[0] and not remaining and not guard_errors
            diagnostic = output.getvalue().encode()
            if len(diagnostic) > 256 * 1024:
                raise RuntimeError('fixture_diagnostic_limit')
            (run_dir / 'diagnostic.txt').write_bytes(diagnostic)
            receipt = {'status': 'passed' if accepted else 'failed', 'tests_run': result.testsRun,
                       'expected': EXPECTED, 'forbidden_attempts': attempts[0], 'remaining_threads': remaining,
                       'loopback_listeners_created': len(ports), 'native_analysis': False,
                       'fixture_physical_allocation_included': True, 'allocated_peak_bytes': peak['allocated_bytes'],
                       'runner_peak_rss_bytes': peak['rss_bytes'], 'rss_units': 'bytes', 'guard_errors': guard_errors,
                       'limits': {'wall_seconds': 120, 'allocated_bytes': ALLOCATION_LIMIT, 'runner_rss_bytes': RSS_LIMIT},
                       'elapsed_seconds': time.monotonic() - started,
                       'execution_status': 'requires_post_record_resource_check'}
            payload = (json.dumps(receipt, indent=2) + '\n').encode()
            if len(payload) > 512 * 1024:
                raise RuntimeError('fixture_receipt_limit')
            (run_dir / 'receipt.json').write_bytes(payload)
            sample()
            print(json.dumps(receipt | {'post_record_resource_check': 'passed', 'final_allocated_bytes': physical_usage((Path(private), run_dir), deadline), 'run_dir': str(run_dir)}))
        return 0 if accepted else 1
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
        sys.meta_path.remove(finder)


if __name__ == '__main__':
    raise SystemExit(main())
