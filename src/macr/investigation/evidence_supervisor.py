"""One owned worker, fail-closed monitors and parent-only verified publication.

The 120+5 default is a normal monitor/cleanup target. Kernel-uninterruptible IO
and parent read/fsync cannot be made a hard wall-clock guarantee by Python.
"""
from __future__ import annotations

import ctypes
import math
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time
from typing import Callable

from macr import __version__
from .evidence_budget import BudgetExceeded, EvidenceBudget
from .evidence_profiles import load_json
from .evidence_records import EvidenceRequest, StaticEvidenceReport
from .evidence_workspace import EvidenceWorkspace, encode_bounded
from .validation import ContractError

_SAMPLE_SECONDS = .05


def _host_pressure():
    if sys.platform != 'darwin':
        return {'available': False, 'state': 'unknown', 'raw': None, 'errno': None,
                'provenance': 'unsupported_platform'}
    try:
        library = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        query = library.sysctlbyname
        query.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
                          ctypes.c_void_p, ctypes.c_size_t]
        query.restype = ctypes.c_int
        value, size = ctypes.c_int32(), ctypes.c_size_t(4)
        ctypes.set_errno(0)
        result = query(b'kern.memorystatus_vm_pressure_level', ctypes.byref(value),
                       ctypes.byref(size), None, 0)
        error = ctypes.get_errno()
        valid = result == 0 and size.value == 4 and value.value in (1, 2, 4)
        return {'available': valid, 'state': {1: 'normal', 2: 'warning', 4: 'critical'}.get(value.value, 'unknown') if valid else 'unknown',
                'raw': value.value if result == 0 and size.value == 4 else None,
                'errno': error, 'provenance': 'darwin_readonly_sysctl_scalar'}
    except (OSError, AttributeError):
        return {'available': False, 'state': 'unknown', 'raw': None, 'errno': None,
                'provenance': 'darwin_readonly_sysctl_scalar'}


def _pressure_check(observation):
    if (type(observation) is not dict or observation.get('available') is not True
            or type(observation.get('raw')) is not int
            or {1: 'normal', 2: 'warning', 4: 'critical'}.get(observation['raw']) != observation.get('state')):
        raise ContractError('unsupported_environment', 'monitor_unavailable')
    if observation['state'] != 'normal':
        raise ContractError('resource_pressure', 'host_memory_pressure')


def _worker_rss(pid):
    """Read only the explicit owned live PID; Darwin proc_taskinfo ABI is 96 bytes."""
    if sys.platform != 'darwin':
        raise ContractError('unsupported_environment', 'rss_monitor_unavailable')
    try:
        library = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        query = library.proc_pidinfo
        query.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        query.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(96)
        if query(pid, 4, 0, buffer, 96) != 96:
            raise ContractError('unsupported_environment', 'rss_monitor_unavailable')
        return ctypes.c_uint64.from_buffer(buffer, 8).value
    except (OSError, AttributeError):
        raise ContractError('unsupported_environment', 'rss_monitor_unavailable') from None


def _worker_command(profile):
    entry = Path(__file__).with_name('evidence_worker.py').resolve()
    return [str(Path(sys.executable).absolute()), '-I', '-S', '-B', str(entry), str(profile.max_input_bytes)]


def _observe_pressure(monitor):
    started = time.monotonic()
    observation = _host_pressure()
    finished = time.monotonic()
    previous = monitor['pressure_last_sample_monotonic']
    if previous is not None:
        monitor['pressure_interval_max_seconds'] = max(monitor['pressure_interval_max_seconds'] or 0., started - previous)
    monitor['pressure_last_sample_monotonic'] = started
    monitor['pressure_sample_count'] += 1
    monitor['pressure_call_max_seconds'] = max(monitor['pressure_call_max_seconds'], finished - started)
    monitor['pressure'] = observation
    _pressure_check(observation)


def _sample(process, budget, monitor):
    started = time.monotonic()
    _observe_pressure(monitor)
    if process.poll() is not None:
        return
    try:
        rss = _worker_rss(process.pid)
    except ContractError:
        if process.poll() is not None:
            monitor['rss_exit_race_count'] += 1
            return
        raise
    finished = time.monotonic()
    previous = monitor['rss_last_sample_monotonic']
    if previous is not None:
        interval = started - previous
        monitor['rss_interval_min_seconds'] = min(monitor['rss_interval_min_seconds'] or interval, interval)
        monitor['rss_interval_max_seconds'] = max(monitor['rss_interval_max_seconds'] or interval, interval)
    else:
        monitor['rss_first_sample_monotonic'] = started
    monitor['rss_last_sample_monotonic'] = started
    monitor['rss_sample_count'] += 1
    monitor['rss_peak_bytes'] = max(monitor['rss_peak_bytes'] or 0, rss)
    monitor['rss_call_max_seconds'] = max(monitor['rss_call_max_seconds'], finished - started)
    budget.observe('max_worker_rss_bytes', monitor['rss_peak_bytes'], 'worker_memory')


def _close(process, cleanup_seconds, closure):
    """TERM -> KILL -> wait; never infer closure from a sent signal."""
    closure['cleanup_attempted'] = True
    end = time.monotonic() + cleanup_seconds
    try:
        if process.poll() is None:
            closure['steps'].append('term')
            process.terminate()
            try:
                process.wait(timeout=min(.5, cleanup_seconds / 2))
            except subprocess.TimeoutExpired:
                closure['steps'].append('kill')
                process.kill()
        process.wait(timeout=max(0, end - time.monotonic()))
        closure['steps'].append('wait')
        closure['exit_confirmed'] = True
    except (OSError, subprocess.TimeoutExpired):
        closure['steps'].append('unknown')
        closure['exit_confirmed'] = False
    closure['returncode'] = process.returncode
    closure['worker_closed_monotonic'] = time.monotonic() if closure['exit_confirmed'] else None


def _communicate(process, data, budget, monitor, workspace):
    stdout = bytearray()
    counts = {'stdout': 0, 'stderr': 0, 'stdin': 0}
    next_sample = 0.
    next_storage = 0.
    with selectors.DefaultSelector() as selector:
        for stream, mode, name in ((process.stdin, selectors.EVENT_WRITE, 'stdin'),
                                  (process.stdout, selectors.EVENT_READ, 'stdout'),
                                  (process.stderr, selectors.EVENT_READ, 'stderr')):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, mode, name)
        while selector.get_map() or process.poll() is None:
            budget.check('worker_communication')
            now = time.monotonic()
            if now >= next_sample:
                _sample(process, budget, monitor)
                next_sample = time.monotonic() + _SAMPLE_SECONDS
            if now >= next_storage and process.poll() is None:
                from .scope import open_directory
                with open_directory(workspace.root, identities=budget.anchors) as fd:
                    free = os.fstatvfs(fd)
                    budget.minimum('min_free_bytes', free.f_bavail * free.f_frsize, 'supervisor_storage')
                workspace.check_storage('supervisor_storage', live=True)
                next_storage = time.monotonic() + .1
            for key, _ in selector.select(min(_SAMPLE_SECONDS, max(0., budget.deadline - time.monotonic()))):
                stream, name = key.fileobj, key.data
                try:
                    if name == 'stdin':
                        written = os.write(stream.fileno(), data[counts['stdin']:counts['stdin'] + 4096])
                        counts[name] += written
                        if counts[name] == len(data):
                            selector.unregister(stream)
                            stream.close()
                        continue
                    field = 'max_control_bytes' if name == 'stdout' else 'max_stderr_bytes'
                    cap = getattr(budget.profile, field)
                    chunk = os.read(stream.fileno(), min(4096, cap - counts[name] + 1))
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    counts[name] += len(chunk)
                    budget.charge('max_io_bytes', len(chunk), 'worker_' + name)
                    budget.observe(field, counts[name], 'worker_' + name)
                    if name == 'stdout':
                        stdout.extend(chunk)
                except BlockingIOError:
                    continue
                except BrokenPipeError:
                    selector.unregister(stream)
                    stream.close()
        budget.check('worker_exit')
        process.wait(timeout=max(0., budget.deadline - time.monotonic()))
    budget.usage['control_input_written_bytes'] = counts['stdin']
    budget.usage['control_output_read_bytes'] = counts['stdout']
    budget.usage['stderr_read_bytes'] = counts['stderr']
    return bytes(stdout)


def _merge_usage(budget, worker_usage, initial, *, enforce=True):
    allowed = set(budget.profile.to_dict()) | {'output_bytes', 'guard_metadata_entries', 'guard_metadata_bytes'}
    if (type(worker_usage) is not dict or set(worker_usage) - allowed or 'max_io_bytes' not in worker_usage
            or any(type(value) not in (int, float) or value < 0
                   or type(value) is float and not math.isfinite(value) for value in worker_usage.values())):
        raise ContractError('citation_invalid', 'worker_usage')
    parent = dict(budget.usage)
    for field, value in worker_usage.items():
        if field in ('max_io_bytes', 'max_metadata_entries', 'max_metadata_bytes', 'output_bytes'):
            value += parent.get(field, 0) - initial.get(field, 0)
        budget.usage[field] = value
    budget.check('parent_accounting')
    if enforce:
        for field in ('max_io_bytes', 'max_metadata_entries', 'max_metadata_bytes'):
            budget.observe(field, budget.usage.get(field, 0), 'parent_accounting')


def _worker_failure(envelope, budget):
    """Decode only a source-less failure after the owned process confirmed exit."""
    invalid = ContractError('citation_invalid', 'worker_failure_control')
    if (type(envelope) is not dict or set(envelope) not in ({'schema_version', 'kind', 'error', 'usage'},
            {'schema_version', 'kind', 'error', 'usage', 'preparation'})
            or type(envelope['schema_version']) is not int or envelope['schema_version'] != 1
            or envelope['kind'] != 'worker_failure'):
        raise invalid
    detail = envelope['error']
    if (type(detail) is not dict or set(detail) != {'code', 'field', 'stage', 'exceeded'}
            or any(type(detail[key]) is not str or re.fullmatch(r'[a-z][a-z0-9_]{0,63}', detail[key]) is None
                   for key in ('code', 'field', 'stage'))):
        raise invalid
    exceeded = detail['exceeded']
    if exceeded is not None:
        if (type(exceeded) is not dict or set(exceeded) != {'field', 'stage', 'effective', 'observed'}
                or detail['code'] != 'scope_limit' or exceeded['field'] != detail['field']
                or exceeded['stage'] != detail['stage']
                or any(type(exceeded[key]) not in (int, float) or exceeded[key] < 0
                       or type(exceeded[key]) is float and not math.isfinite(exceeded[key]) for key in ('effective', 'observed'))
                or exceeded['effective'] != getattr(budget.profile, detail['field'], None)):
            raise invalid
        error = BudgetExceeded(detail['field'], detail['stage'], exceeded['effective'], exceeded['observed'])
    else:
        error = ContractError(detail['code'], detail['field'])
        error.stage = detail['stage']
    if 'preparation' in envelope:
        prepared = envelope['preparation']
        if (type(prepared) is not dict or set(prepared) != {'status', 'errors', 'exceeded'}
                or prepared['status'] not in ('rejected', 'failed', 'cancelled')
                or type(prepared['errors']) is not list or type(prepared['exceeded']) is not list):
            raise invalid
        for item in prepared['errors']:
            budget.check('parent_failure_control')
            if (type(item) is not dict or set(item) != {'code', 'field', 'stage', 'retryable'}
                    or type(item['retryable']) is not bool
                    or any(type(item[key]) is not str or re.fullmatch(r'[a-z][a-z0-9_]{0,63}', item[key]) is None
                           for key in ('code', 'field', 'stage'))):
                raise invalid
        for item in prepared['exceeded']:
            budget.check('parent_failure_control')
            if (type(item) is not dict or set(item) != {'field', 'stage', 'effective', 'observed'}
                    or any(type(item[key]) is not str or re.fullmatch(r'[a-z][a-z0-9_]{0,63}', item[key]) is None for key in ('field', 'stage'))
                    or any(type(item[key]) not in (int, float) or item[key] < 0
                           or type(item[key]) is float and not math.isfinite(item[key]) for key in ('effective', 'observed'))
                    or item['effective'] != getattr(budget.profile, item['field'], None)):
                raise invalid
        error.preparation_status = prepared['status']
        error.preparation_errors = prepared['errors']
        error.preparation_exceeded = prepared['exceeded']
    return error


def _verify_result(envelope, workspace, request, closure):
    from .evidence_worker import document, source_association
    profile, budget = request.profile, workspace.budget
    budget.check('parent_verify')
    if (type(envelope) is not dict or set(envelope) != {'schema_version', 'status', 'documents', 'source',
            'request_sha256', 'workspace_identity', 'usage', 'worker_peak_rss_bytes', 'worker_rss_units'}
            or type(envelope['schema_version']) is not int or envelope['schema_version'] != 1
            or envelope['workspace_identity'] != list(workspace.identity)
            or envelope['request_sha256'] != document(encode_bounded(request.to_dict(), profile.max_input_bytes))['sha256']
            or closure['exit_confirmed'] is not True or closure['returncode'] != 0
            or envelope['worker_rss_units'] != 'bytes' or type(envelope['worker_peak_rss_bytes']) is not int
            or envelope['worker_peak_rss_bytes'] <= 0):
        raise ContractError('citation_invalid', 'worker_envelope')
    documents = envelope['documents']
    if type(documents) is not dict or set(documents) != {'report.json', 'report.md', 'worker.log'}:
        raise ContractError('citation_invalid', 'worker_documents')
    report = None
    for name, cap in (('report.json', profile.max_report_bytes), ('report.md', profile.max_markdown_bytes), ('worker.log', profile.max_log_bytes)):
        budget.check('parent_document')
        raw = workspace.read('pending/' + name, limit=cap)
        if documents[name] != document(raw):
            raise ContractError('citation_invalid', 'document_identity')
        if name == 'report.json':
            report = StaticEvidenceReport.from_json(raw.decode('utf-8'), max_bytes=cap)
        budget.check('parent_document')
    if report.request is None or report.request.to_dict() != request.to_dict() or report.status != envelope['status']:
        raise ContractError('citation_invalid', 'report_association')
    if report.status not in ('completed', 'partial'):
        raise ContractError('citation_invalid', 'unpublishable_status')
    source = source_association(report, workspace)
    if source != envelope['source']:
        raise ContractError('citation_invalid', 'source_association')
    budget.observe('max_worker_rss_bytes', envelope['worker_peak_rss_bytes'], 'worker_high_water')
    closure['resource_monitor']['worker_high_water_rss_bytes'] = envelope['worker_peak_rss_bytes']
    workspace.check_storage('parent_final_storage')
    budget.check('parent_verified')
    receipt = {'schema_version': 1, 'tool_version': __version__, 'status': report.status,
               'profile': profile.to_dict(), 'documents': documents, 'source': source,
               'worker': {'pid': closure['pid'], 'returncode': closure['returncode'], 'exit_confirmed': True},
               'usage_at_verification': dict(budget.usage)}
    return report, receipt


def _failure(request, error, budget, closure):
    code = error.code if isinstance(error, ContractError) else 'storage_unavailable' if isinstance(error, OSError) else 'worker_failed'
    status = 'cancelled' if code == 'cancelled' else 'rejected' if code in ('input_invalid', 'forbidden_path') else 'failed'
    exceeded = [{'field': error.field, 'stage': error.stage, 'effective': error.limit, 'observed': error.observed}] if isinstance(error, BudgetExceeded) else []
    status = getattr(error, 'preparation_status', status)
    exceeded = getattr(error, 'preparation_exceeded', exceeded)
    report = StaticEvidenceReport(request, status, run={'status': status, 'phase': 'stopped', 'publication': 'uncommitted',
        'worker_started': closure['starts'] == 1, 'worker_pid': closure['pid'],
        'worker_returncode': closure['returncode'], 'worker_exit_confirmed': closure['exit_confirmed'],
        'closure': closure, 'committed_before_cancel': False},
        errors=[{'code': code, 'field': getattr(error, 'field', 'worker'), 'stage': getattr(error, 'stage', budget.stage),
                 'retryable': False}], usage={'model_calls': 0, 'tokens': None, **budget.usage},
        limits={'effective': budget.profile.to_dict(), 'exceeded': exceeded})
    if getattr(error, 'preparation_errors', None):
        report.errors = error.preparation_errors
    try:
        return report.validate()
    except ContractError:
        # The configured document cap cannot represent this failure. This typed
        # outcome omits the request and writes no artifact under an enlarged cap.
        report.request = None
        report.run['failure_detail_status'] = 'report_cap_unrepresentable'
        report.errors.append({'code': 'output_limit_unrepresentable', 'field': 'max_report_bytes',
                              'stage': 'failure_report', 'retryable': False})
        try:
            return report.validate()
        except ContractError:
            report.limits['effective'] = {'name': budget.profile.name, 'version': budget.profile.version,
                                         'max_report_bytes': budget.profile.max_report_bytes}
            report.run['effective_profile_detail_status'] = 'unavailable'
            return report.validate()


def collect_evidence(request: EvidenceRequest, *, cancelled: Callable[[], bool] | None = None) -> StaticEvidenceReport:
    """Public supervisor only. Event cancellation is supplied as event.is_set.

    A successful COMMITTED receipt is authoritative for staged pending documents.
    The returned report adds final lifecycle and cumulative parent/worker usage.
    """
    from .evidence_profiles import EvidenceProfile
    profile = request.profile if isinstance(request, EvidenceRequest) else None
    invalid_profile = False
    try:
        if not isinstance(profile, EvidenceProfile):
            raise ContractError('input_invalid', 'profile')
        profile.to_dict()
    except ContractError:
        profile = EvidenceProfile()
        invalid_profile = True
    budget = EvidenceBudget(profile, cancelled=cancelled)
    monitor = {'pressure': None, 'pressure_sample_count': 0, 'pressure_last_sample_monotonic': None,
               'pressure_interval_max_seconds': None, 'pressure_call_max_seconds': 0., 'rss_units': 'bytes', 'rss_method': 'Darwin proc_pidinfo PROC_PIDTASKINFO',
               'rss_sample_target_seconds': _SAMPLE_SECONDS, 'rss_sample_count': 0, 'rss_peak_bytes': None,
               'rss_first_sample_monotonic': None, 'rss_last_sample_monotonic': None,
               'rss_interval_min_seconds': None, 'rss_interval_max_seconds': None,
               'rss_call_max_seconds': 0., 'rss_exit_race_count': 0,
               'sampling_limit': 'sampled RSS is not an instantaneous hard bound; high water ends at worker envelope'}
    closure = {'starts': 0, 'restarts': 0, 'pid': None, 'returncode': None,
               'exit_confirmed': None, 'steps': [], 'cleanup_attempted': False,
               'worker_started_monotonic': None, 'worker_closed_monotonic': None,
               'resource_monitor': monitor}
    process = None
    initial = {}
    try:
        budget.check('input_validation')
        if invalid_profile or not isinstance(request, EvidenceRequest) or cancelled is not None and not callable(cancelled):
            raise ContractError('input_invalid', 'request')
        try:
            request.validate()
            from dataclasses import replace
            # Bind caller-relative roots before the fixed worker cwd can change their meaning.
            request = replace(request, repo=str(Path(request.repo).absolute()),
                              out=str(Path(request.out).absolute()))
        except ContractError:
            raise ContractError('input_invalid', 'request') from None
        package_parent = Path(__file__).resolve().parents[2]
        target = Path(request.repo).absolute()
        if package_parent.is_relative_to(target) or target.is_relative_to(package_parent):
            raise ContractError('forbidden_path', 'trusted_product_target_overlap')
        _observe_pressure(monitor)
        budget.check('workspace_create')
        workspace = EvidenceWorkspace(request.repo, request.out, profile, budget=budget)
        initial = dict(budget.usage)
        data = encode_bounded({'request': request.to_dict(), 'token': workspace.token, 'deadline': budget.deadline,
                               'started': budget.started, 'initial_usage': initial}, profile.max_input_bytes,
                               budget=budget, stage='parent_control', limit_field='max_input_bytes')
        budget.check('worker_start')
        process = subprocess.Popen(_worker_command(profile), cwd=str(package_parent), env={},
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   close_fds=True, shell=False)
        closure.update(starts=1, pid=process.pid, worker_started_monotonic=time.monotonic())
        raw = _communicate(process, data, budget, monitor, workspace)
        closure.update(returncode=process.returncode, exit_confirmed=True, worker_closed_monotonic=time.monotonic())
        closure['steps'].append('wait')
        if process.returncode != 0:
            if not raw:
                closure['failure_detail_status'] = 'unavailable'
                raise ContractError('worker_failed', 'worker_control_unavailable')
            budget.check('parent_failure_control')
            failure = load_json(raw.decode('utf-8'), max_bytes=profile.max_control_bytes, max_depth=64)
            error = _worker_failure(failure, budget)
            _merge_usage(budget, failure['usage'], initial, enforce=False)
            closure['failure_detail_status'] = 'available'
            raise error
        budget.check('parent_envelope')
        envelope = load_json(raw.decode('utf-8'), max_bytes=profile.max_control_bytes, max_depth=64)
        if type(envelope) is not dict:
            raise ContractError('citation_invalid', 'worker_envelope')
        _merge_usage(budget, envelope.get('usage'), initial)
        report, receipt = _verify_result(envelope, workspace, request, closure)
        _observe_pressure(monitor)
        budget.check('precommit')
        report.run.update(publication='committed', phase='published', worker_started=True,
                          worker_pid=process.pid, worker_returncode=process.returncode,
                          worker_exit_confirmed=True, closure=closure, committed_before_cancel=False,
                          postcommit_cancel_check='observed')
        report.usage.update(budget.usage)
        report.validate()
        callback = budget.cancelled
        workspace.commit(encode_bounded(receipt, profile.max_control_bytes, budget=budget, stage='receipt_encode', limit_field='max_control_bytes'))
        report.run.update(publication='committed', phase='published', worker_started=True,
                          worker_pid=process.pid, worker_returncode=process.returncode,
                          worker_exit_confirmed=True, closure=closure,
                          committed_before_cancel=False, postcommit_cancel_check='observed')
        try:
            report.run['committed_before_cancel'] = bool(callback and callback())
        except (Exception, KeyboardInterrupt):
            report.run['postcommit_cancel_check'] = 'unavailable'
        report.usage.update(budget.usage)
        return report
    except BaseException as error:
        if isinstance(error, KeyboardInterrupt):
            error = ContractError('cancelled', 'keyboard_interrupt')
        if process is not None and closure['exit_confirmed'] is not True:
            _close(process, budget.profile.cleanup_seconds, closure)
        valid_request = request if isinstance(request, EvidenceRequest) else None
        try:
            if valid_request is not None:
                valid_request.validate()
        except ContractError:
            valid_request = None
        return _failure(valid_request, error, budget, closure)
    finally:
        if process is not None:
            if closure['exit_confirmed'] is not True and not closure['cleanup_attempted']:
                _close(process, budget.profile.cleanup_seconds, closure)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
