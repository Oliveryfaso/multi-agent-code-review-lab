"""One owned local run; the existing supervisor owns every analysis worker."""
from dataclasses import replace
import ctypes
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import re
import sys
import threading
import time
from uuid import UUID, uuid4

from .evidence_reader import document, read_committed
from ..investigation.evidence_budget import EvidenceBudget
from ..investigation.evidence_profiles import EvidenceProfile, load_json
from ..investigation.evidence_records import EvidenceRequest
from ..investigation.evidence_workspace import check_storage, encode_bounded, safe_write
from ..investigation.scope import open_directory, read_safe, safe_parent, validate_workspace_path
from ..investigation.validation import ContractError

ROOT_LIMIT = 2 * 1024 * 1024 * 1024
META_LIMIT = 5 * 1024 * 1024
RESERVE_EXTRA = 8 * 1024 * 1024


def _uuid(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ContractError('input_invalid', 'id') from None
    return value




PROFILE_CHOICES = ('standard', 'larger', 'standard-capacity')


def _selected_profile(choice):
    if type(choice) is not str or choice not in PROFILE_CHOICES:
        raise ContractError('input_invalid', 'profile_name')
    if choice == 'standard-capacity':
        return EvidenceProfile.select('standard', {'max_metadata_entries': 100000,
                                                  'max_metadata_bytes': 16 * 1024 * 1024})
    return EvidenceProfile.select(choice)


def _profile_choice(profile):
    effective = profile.to_dict()
    for choice in PROFILE_CHOICES:
        if effective == _selected_profile(choice).to_dict():
            return choice
    raise ContractError('input_invalid', 'effective_profile')


def _validate_failure(value, profile):
    if value is None:
        return
    if type(value) is not dict or set(value) != {'code', 'field', 'stage', 'effective', 'observed'}:
        raise ValueError('failure_summary')
    if any(type(value[key]) is not str or re.fullmatch('[a-z][a-z0-9_]{0,63}', value[key]) is None
           for key in ('code', 'field', 'stage')):
        raise ValueError('failure_summary')
    numbers = (value['effective'], value['observed'])
    if numbers != (None, None):
        if any(type(number) not in (int, float) or not 0 <= number <= 2**63 - 1
               or not math.isfinite(number) for number in numbers):
            raise ValueError('failure_summary')
        if value['code'] != 'scope_limit' or value['effective'] != getattr(profile, value['field'], None):
            raise ValueError('failure_summary')


def _failure_summary(outcome, profile):
    if outcome.status not in ('failed', 'rejected', 'cancelled') or not outcome.errors:
        return None
    error = outcome.errors[0]
    result = {key: error.get(key) for key in ('code', 'field', 'stage')} | {'effective': None, 'observed': None}
    for limit in outcome.limits.get('exceeded', [])[:1]:
        if limit.get('field') == result['field'] and limit.get('stage') == result['stage']:
            result.update(effective=limit.get('effective'), observed=limit.get('observed'))
    try:
        _validate_failure(result, profile)
    except (ValueError, TypeError):
        return None
    return result


def _local_filesystem(fd):
    """Descriptor-bound local filesystem allowlist; unknown mounts fail closed."""
    libc = ctypes.CDLL(None, use_errno=True)
    buffer = ctypes.create_string_buffer(4096)
    if sys.platform == 'darwin':
        # Darwin statfs64 ABI: fixed prefix72 followed by f_fstypename[16].
        query = libc.fstatfs64
        query.argtypes, query.restype = [ctypes.c_int, ctypes.c_void_p], ctypes.c_int
        if query(fd, buffer) != 0 or buffer.raw[72:88].split(b'\0')[0] != b'apfs':
            raise ContractError('unsupported_environment', 'local_data_filesystem')
    elif sys.platform.startswith('linux') and ctypes.sizeof(ctypes.c_long) == 8:
        query = libc.fstatfs
        query.argtypes, query.restype = [ctypes.c_int, ctypes.c_void_p], ctypes.c_int
        if query(fd, buffer) != 0:
            raise ContractError('unsupported_environment', 'local_data_filesystem')
        kind = ctypes.c_long.from_buffer(buffer).value
        if kind not in (0xEF53, 0x01021994, 0x794C7630, 0x58465342, 0x9123683E):
            raise ContractError('unsupported_environment', 'local_data_filesystem')
    else:
        raise ContractError('unsupported_environment', 'local_data_filesystem')


class Workbench:
    def __init__(self, root: Path):
        self.root = Path(root).absolute()
        self.mutex = threading.RLock()
        self.consumer = threading.Lock()
        self.event = threading.Event()
        self.thread = None
        self.active = None
        self.stopping = False
        self.closed = False
        self.blocked = None
        self.anchors = {}
        self.lock_fd = None
        validate_workspace_path(self.root, output=True)
        try:
            with open_directory(self.root, create=True, observed=self.anchors) as fd:
                _local_filesystem(fd)
                root_stat = os.fstat(fd)
                if root_stat.st_uid != os.getuid():
                    raise ContractError('forbidden_path', 'data_owner')
                self.root_identity = [root_stat.st_dev, root_stat.st_ino]
            with safe_parent(self.root, 'owner.lock', identities=self.anchors) as (fd, leaf):
                self.lock_fd = os.open(leaf, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
                lock_info = os.fstat(self.lock_fd)
                if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.getuid():
                    raise ContractError('forbidden_path', 'lock_type')
                self.lock_identity = (lock_info.st_dev, lock_info.st_ino)
                try:
                    fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ContractError('workbench_busy', 'data_root') from None
            self._anchor()
            try:
                raw = read_safe(self.root, 'registry.json', META_LIMIT)
            except FileNotFoundError:
                with open_directory(self.root, identities=self.anchors) as fd:
                    if set(os.listdir(fd)) != {'owner.lock'}:
                        raise ContractError('registry_invalid', 'unowned_data_root')
                self.registry = {'schema_version': 1, 'root_identity': self.root_identity, 'runs': {}}
                for directory in ('runs', 'envelopes'):
                    with safe_parent(self.root, directory, identities=self.anchors) as (fd, leaf):
                        os.mkdir(leaf, 0o700, dir_fd=fd)
                self._save()
            else:
                self.registry = load_json(raw.decode('utf-8'), max_bytes=META_LIMIT, max_depth=64)
                self._validate_registry()
                self._recover()
        except BaseException as error:
            if self.lock_fd is not None:
                os.close(self.lock_fd)
                self.lock_fd = None
            if isinstance(error, OSError):
                raise ContractError('forbidden_path', 'data_directory') from None
            raise

    def _anchor(self):
        with open_directory(self.root, identities=self.anchors) as fd:
            current = os.stat('owner.lock', dir_fd=fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != self.lock_identity or not stat.S_ISREG(current.st_mode):
                self.blocked = 'closure_unknown'
                raise ContractError('closure_unknown', 'owner_identity')

    def _budget(self):
        budget = EvidenceBudget(replace(EvidenceProfile(), max_allocated_bytes=ROOT_LIMIT, active_seconds=10., max_metadata_entries=100000, max_metadata_bytes=16 * 1024 * 1024))
        budget.anchors.update(self.anchors)
        return budget

    def _allocation(self):
        self._anchor()
        budget = self._budget()
        check_storage(self.root, budget, 'workbench_storage')
        return budget.usage.get('max_allocated_bytes', 0)

    def _metadata_bytes(self):
        total = 0
        budget = self._budget()
        budget.check('workbench_metadata')
        names = {identifier + '.json' for identifier in self.registry['runs']}
        with open_directory(self.root / 'envelopes', identities=budget.anchors) as fd, os.scandir(fd) as entries:
            for entry in entries:
                budget.charge('max_metadata_entries', 1, 'workbench_metadata')
                budget.charge('max_metadata_bytes', len(entry.name.encode('utf-8')) + 128, 'workbench_metadata')
                if entry.name not in names:
                    raise ContractError('registry_invalid', 'unregistered_envelope')
                info = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise ContractError('registry_invalid', 'envelope_type')
                total += info.st_size
                if total > META_LIMIT:
                    raise ContractError('workbench_storage_limit', 'metadata')
        budget.check('workbench_metadata_complete')
        return total

    def _save(self):
        self._anchor()
        data = encode_bounded(self.registry, META_LIMIT)
        if len(data) + self._metadata_bytes() > META_LIMIT:
            raise ContractError('workbench_storage_limit', 'metadata')
        safe_write(self.root, 'registry.json', data, limit=META_LIMIT, budget=self._budget(), stage='workbench_registry')

    def _validate_registry(self):
        try:
            raw = self.registry
            if (type(raw) is not dict or set(raw) != {'schema_version', 'root_identity', 'runs'}
                    or type(raw['schema_version']) is not int or raw['schema_version'] != 1
                    or raw['root_identity'] != self.root_identity or type(raw['runs']) is not dict):
                raise ValueError()
            intents = set()
            for identifier, run in raw['runs'].items():
                _uuid(identifier)
                if set(run) - {'analysis_failure'} != {'id', 'intent_id', 'payload_digest', 'request', 'started_at', 'state', 'analysis_status', 'publication', 'verification_status', 'cancel_requested', 'worker_started', 'worker_exit_confirmed', 'worker_pid', 'binding', 'envelope', 'error', 'reservation'}:
                    raise ValueError()
                if run['id'] != identifier or _uuid(run['intent_id']) in intents:
                    raise ValueError()
                intents.add(run['intent_id'])
                request = EvidenceRequest.from_dict(run['request'])
                _validate_failure(run.get('analysis_failure'), request.profile)
                if run.get('analysis_failure') is not None and (run['analysis_status'] not in ('failed', 'rejected', 'cancelled') or run['publication'] == 'committed'):
                    raise ValueError('failure_summary_state')
                if request.out != str(self.root / 'runs' / identifier) or type(run['reservation']) is not int or run['reservation'] < 0:
                    raise ValueError()
                _profile_choice(request.profile)
                if (run['reservation'] > request.profile.max_allocated_bytes + RESERVE_EXTRA
                        or type(run['payload_digest']) is not str or not re.fullmatch('[0-9a-f]{64}', run['payload_digest'])
                        or type(run['started_at']) not in (int, float) or not math.isfinite(run['started_at']) or run['started_at'] <= 0
                        or run['state'] not in ('running', 'cancelling', 'verifying', 'ready', 'verification_failed', 'rejected', 'failed', 'cancelled', 'closure_unknown')
                        or run['analysis_status'] not in (None, 'completed', 'partial', 'rejected', 'failed', 'cancelled')
                        or run['publication'] not in ('committed', 'uncommitted', 'unknown')
                        or run['verification_status'] not in ('not_started', 'pending', 'passed', 'budget_exceeded', 'corrupt', 'unsupported', 'unavailable')
                        or type(run['cancel_requested']) is not bool
                        or run['worker_started'] is not None and type(run['worker_started']) is not bool
                        or run['worker_exit_confirmed'] is not None and type(run['worker_exit_confirmed']) is not bool
                        or run['worker_pid'] is not None and (type(run['worker_pid']) is not int or run['worker_pid'] <= 0)
                        or run['error'] is not None and (type(run['error']) is not str or not re.fullmatch('[a-z][a-z0-9_]{0,63}', run['error']))):
                    raise ValueError()
                if run['binding'] is not None:
                    if (type(run['binding']) is not dict or set(run['binding']) != {'out_identity', 'request', 'receipt'}
                            or run['binding']['request'] != run['request']):
                        raise ValueError()
                if run['state'] == 'ready' and (run['binding'] is None or run['worker_exit_confirmed'] is not True):
                    raise ValueError()
                if run['envelope'] is not None:
                    if set(run['envelope']) != {'relative', 'identity'} or run['envelope']['relative'] != 'envelopes/' + identifier + '.json':
                        raise ValueError()
        except (ContractError, ValueError, TypeError, KeyError):
            raise ContractError('registry_invalid', 'records') from None

    def _binding(self, run):
        out = Path(run['request']['out'])
        with open_directory(out) as fd:
            info = os.fstat(fd)
        marker = load_json(read_safe(out, '.macr-owner.json', 16384).decode(), max_bytes=16384)
        if (marker.get('out') != str(out) or marker.get('repo') != run['request']['repo']
                or [marker.get('device'), marker.get('inode')] != [info.st_dev, info.st_ino]):
            raise ContractError('history_corrupt', 'ownership')
        receipt = read_safe(out, 'COMMITTED', 16384)
        return {'out_identity': [info.st_dev, info.st_ino], 'request': run['request'], 'receipt': document(receipt)}

    def _recover(self):
        for run in self.registry['runs'].values():
            if (run['state'] in ('running', 'cancelling', 'verifying')
                    or run['state'] == 'verification_failed' and run['binding'] is None
                    and run['worker_exit_confirmed'] is True and run['publication'] == 'committed'):
                try:
                    binding = run['binding'] or self._binding(run)
                    saved = read_committed(Path(run['request']['out']), binding)
                except (OSError, ContractError):
                    if run['worker_exit_confirmed'] is True:
                        run.update(state='verification_failed', verification_status='unavailable', error='history_unavailable', reservation=0)
                    else:
                        run.update(state='closure_unknown', error='closure_unknown', publication='unknown')
                        self.blocked = 'closure_unknown'
                else:
                    run.update(state='ready', analysis_status=saved['report']['status'], binding=binding,
                               publication='committed', verification_status='passed', worker_started=True, worker_exit_confirmed=True, worker_pid=saved['receipt']['worker']['pid'], reservation=0)
            elif run['state'] == 'closure_unknown' or run['worker_started'] is True and run['worker_exit_confirmed'] is not True:
                run['state'] = 'closure_unknown'
                self.blocked = 'closure_unknown'
        self._save()

    def start(self, payload):
        with self.mutex:
            self._anchor()
            if self.closed or self.stopping:
                raise ContractError('workbench_stopping', 'start')
            if type(payload) is not dict or set(payload) != {'intent_id', 'repo', 'question', 'entries', 'symbols', 'traceback', 'profile_name'}:
                raise ContractError('input_invalid', 'request_fields')
            intent = _uuid(payload['intent_id'])
            profile = _selected_profile(payload['profile_name'])
            if type(payload['repo']) is not str or not Path(payload['repo']).is_absolute():
                raise ContractError('input_invalid', 'repo')
            validate_workspace_path(Path(payload['repo']))
            with open_directory(Path(payload['repo'])):
                pass
            digest = document(encode_bounded(payload, profile.max_input_bytes))['sha256']
            for run in self.registry['runs'].values():
                if run['intent_id'] == intent:
                    if run['payload_digest'] != digest:
                        raise ContractError('intent_conflict', 'intent_id')
                    return self._public(run)
            if self.blocked:
                raise ContractError(self.blocked, 'start')
            if self.active:
                raise ContractError('workbench_busy', 'active_run')
            identifier = str(uuid4())
            request = EvidenceRequest(payload['repo'], str(self.root / 'runs' / identifier), payload['question'], payload['entries'], payload['symbols'], payload['traceback'], profile)
            repo = Path(request.repo)
            if self.root.is_relative_to(repo) or repo.is_relative_to(self.root):
                raise ContractError('forbidden_path', 'output_in_target')
            reserve = profile.max_allocated_bytes + RESERVE_EXTRA
            if self._allocation() + reserve > ROOT_LIMIT:
                raise ContractError('workbench_storage_limit', 'retained_allocation')
            run = {'id': identifier, 'intent_id': intent, 'payload_digest': digest, 'request': request.to_dict(),
                   'started_at': time.time(), 'state': 'running', 'analysis_status': None, 'cancel_requested': False,
                   'publication': 'uncommitted', 'verification_status': 'not_started',
                   'worker_started': None, 'worker_exit_confirmed': None, 'worker_pid': None, 'binding': None,
                   'envelope': None, 'error': None, 'analysis_failure': None, 'reservation': reserve}
            self.registry['runs'][identifier] = run
            try:
                self._save()
            except BaseException:
                del self.registry['runs'][identifier]
                raise
            self.event = threading.Event()
            self.active = identifier
            self.thread = threading.Thread(target=self._execute, args=(identifier, request), name='evidence-supervisor', daemon=False)
            try:
                self.thread.start()
            except BaseException:
                self.active = None
                run.update(state='failed', analysis_status='failed', worker_started=False, reservation=0, error='thread_unavailable')
                self._save()
                raise ContractError('workbench_unavailable', 'thread') from None
            return self._public(run)

    def _execute(self, identifier, request):
        from ..investigation.evidence_supervisor import collect_evidence
        run = self.registry['runs'][identifier]
        verified = False
        try:
            outcome = collect_evidence(request, cancelled=self.event.is_set)
            with self.mutex:
                run.update(analysis_status=outcome.status, worker_started=outcome.run.get('worker_started'),
                           worker_exit_confirmed=outcome.run.get('worker_exit_confirmed'), worker_pid=outcome.run.get('worker_pid'),
                           publication=outcome.run.get('publication', 'unknown'))
                run['analysis_failure'] = _failure_summary(outcome, request.profile) if run['publication'] != 'committed' else None
                if outcome.run.get('worker_started') and outcome.run.get('worker_exit_confirmed') is not True:
                    run.update(state='closure_unknown', error='closure_unknown')
                    self.blocked = 'closure_unknown'
                elif outcome.run.get('publication') == 'committed':
                    run['state'] = 'verifying'
                    run['verification_status'] = 'pending'
                    run['binding'] = self._binding(run)
                    self._save_read_state()
                else:
                    run['state'] = outcome.status
                    run['error'] = outcome.errors[0]['code'] if outcome.errors else None
            raw = None
            try:
                raw = outcome.to_dict()
            except ContractError:
                run['error'] = 'envelope_unavailable'
            if run['publication'] == 'committed' and run['state'] != 'closure_unknown':
                with self.consumer:
                    saved = read_committed(Path(request.out), run['binding'])
                if raw is not None:
                    for field in ('request', 'source_version', 'selection', 'evidence', 'observations', 'coverage', 'gaps', 'errors', 'limits', 'capabilities', 'status'):
                        if raw[field] != saved['report'][field]:
                            raise ContractError('history_corrupt', 'returned_association')
                verified = True
            with self.mutex:
                try:
                    if raw is None:
                        raise ContractError('output_limit_unrepresentable', 'final_envelope')
                    data = encode_bounded(raw, request.profile.max_report_bytes)
                    relative = 'envelopes/' + identifier + '.json'
                    if len(data) + self._metadata_bytes() + len(encode_bounded(self.registry, META_LIMIT)) > META_LIMIT:
                        raise ContractError('workbench_storage_limit', 'metadata')
                    safe_write(self.root, relative, data, limit=request.profile.max_report_bytes, budget=self._budget(), stage='workbench_result')
                    run['envelope'] = {'relative': relative, 'identity': document(data)}
                except (ContractError, OSError):
                    run['error'] = 'envelope_unavailable'
                if run['state'] != 'closure_unknown':
                    run['reservation'] = 0
        except BaseException as error:
            with self.mutex:
                known = run['worker_started'] is False or run['worker_exit_confirmed'] is True
                code = error.code if isinstance(error, ContractError) else 'supervisor_escaped'
                verification = run['publication'] == 'committed' and known
                run.update(state='verification_failed' if verification else 'failed' if known else 'closure_unknown', error=code)
                if verification:
                    run['verification_status'] = {'history_budget_exceeded': 'budget_exceeded', 'history_corrupt': 'corrupt', 'history_version_unsupported': 'unsupported'}.get(code, 'unavailable')
                if known:
                    run['reservation'] = 0
                else:
                    run['publication'] = 'unknown'
                    self.blocked = 'closure_unknown'
        finally:
            with self.mutex:
                if verified and run['state'] == 'verifying':
                    run.update(state='ready', verification_status='passed')
                try:
                    self._allocation()
                    self._save()
                except BaseException:
                    self.blocked = self.blocked or 'storage_unavailable'
                    run['error'] = run['error'] or 'storage_unavailable'
                self.active = None

    def _get(self, identifier):
        _uuid(identifier)
        try:
            return self.registry['runs'][identifier]
        except KeyError:
            raise ContractError('run_not_found', 'id') from None

    def _public(self, run):
        return {k: run[k] for k in ('id', 'intent_id', 'started_at', 'state', 'analysis_status', 'publication', 'verification_status', 'cancel_requested', 'worker_started', 'worker_exit_confirmed', 'worker_pid', 'error')} | {
            'project': run['request']['repo'], 'question': run['request']['question'][:120],
            'profile_name': _profile_choice(EvidenceProfile.from_dict(run['request']['profile'])),
            'effective_profile': EvidenceProfile.from_dict(run['request']['profile']).to_dict(),
            'analysis_failure': dict(run['analysis_failure']) if run.get('analysis_failure') is not None else None}

    def state(self, identifier):
        with self.mutex:
            return self._public(self._get(identifier))

    def by_intent(self, intent):
        _uuid(intent)
        with self.mutex:
            for run in self.registry['runs'].values():
                if run['intent_id'] == intent:
                    return self._public(run)
        raise ContractError('run_not_found', 'intent_id')

    def cancel(self, identifier):
        with self.mutex:
            self._anchor()
            run = self._get(identifier)
            if self.active == identifier and run['state'] in ('running', 'cancelling') and run['publication'] != 'committed':
                run.update(cancel_requested=True, state='cancelling')
                self.event.set()
                self._save()
            return self._public(run)

    def history(self, cursor=0):
        if type(cursor) is not int or cursor < 0:
            raise ContractError('input_invalid', 'cursor')
        with self.mutex:
            records = sorted(self.registry['runs'].values(), key=lambda r: r['started_at'], reverse=True)
            end = cursor + 20
            return {'items': [self._public(r) for r in records[cursor:end]], 'next_cursor': end if end < len(records) else None}

    def _save_read_state(self):
        try:
            self._save()
            return 'persisted'
        except (ContractError, OSError):
            self.blocked = self.blocked or 'storage_unavailable'
            return 'unavailable'

    def report(self, identifier):
        with self.mutex:
            self._anchor()
            if self.active:
                raise ContractError('workbench_busy', 'history_read')
            run = self._get(identifier)
            if run['binding'] is None:
                raise ContractError('report_unavailable', 'uncommitted')
            with self.consumer:
                envelope = ({'root': self.root, 'root_identity': self.root_identity} | run['envelope']) if run['envelope'] else None
                try:
                    saved = read_committed(Path(run['request']['out']), run['binding'], envelope=envelope)
                except ContractError as error:
                    if run['worker_exit_confirmed'] is True and run['publication'] == 'committed':
                        run.update(state='verification_failed', verification_status={
                            'history_budget_exceeded': 'budget_exceeded', 'history_corrupt': 'corrupt',
                            'history_version_unsupported': 'unsupported'}.get(error.code, 'unavailable'), error=error.code)
                        self._save_read_state()
                    raise
                persistence = 'unchanged'
                if run['worker_exit_confirmed'] is True and run['publication'] == 'committed':
                    run.update(state='ready', verification_status='passed', error=None)
                    persistence = self._save_read_state()
                return saved | {'state': self._public(run), 'state_persistence': persistence}

    def shutdown(self):
        with self.mutex:
            if self.closed:
                return
            self.stopping = True
            self.event.set()
            thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        with self.mutex:
            self.closed = True
            if self.lock_fd is not None:
                os.close(self.lock_fd)
                self.lock_fd = None

    def begin_shutdown(self):
        with self.mutex:
            self.stopping = True
            self.event.set()
