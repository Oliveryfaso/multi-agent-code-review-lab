"""Repair records under the workbench's existing owner and admission lock.

The HTTP request selects neutral inputs only. Transport/verifier injection is an
application-owned fixture seam; this service cannot enable an execution runtime.
"""
from copy import deepcopy
import math
import os
from pathlib import Path
import re
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

from .evidence_reader import document
from .evidence_workbench import META_LIMIT, ROOT_LIMIT, RESERVE_EXTRA, _uuid
from ..investigation.evidence_profiles import load_json
from ..investigation.evidence_workspace import encode_bounded, safe_write
from ..investigation.scope import open_directory, read_safe, safe_parent, validate_workspace_path
from ..investigation.validation import ContractError

# The loop caps artifact inventory at 1024 and every artifact at 256 KiB.
# Reserve that conservative ceiling under the shared retained-root allowance.
REPAIR_RESERVE = 1024 * 262144 + RESERVE_EXTRA
_FIELDS = {'intent_id', 'repo', 'task', 'model_config', 'strategy', 'proposal_contract'}
_STATES = {'running', 'verifying', 'blocked', 'completed', 'failed', 'unknown'}
_BLOCKED = {'verification_unavailable', 'provider_disabled', 'provider_unavailable'}


def _outcome_state(summary, exit_code):
    if summary['status'] in _BLOCKED:
        return 'blocked'
    if (summary['status'] in ('provider_outcome_unknown', 'verification_outcome_unknown')
            or summary.get('unknown_reserved_usd', '0.000000') != '0.000000'):
        return 'unknown'
    return 'completed' if exit_code == 0 else 'failed'


def _input(payload):
    if type(payload) is not dict or set(payload) != _FIELDS:
        raise ContractError('input_invalid', 'repair_fields')
    _uuid(payload['intent_id'])
    for key in ('repo', 'task', 'model_config'):
        value = payload[key]
        if type(value) is not str or not 0 < len(value.encode('utf-8')) <= 4096 or not Path(value).is_absolute():
            raise ContractError('input_invalid', key)
        validate_workspace_path(Path(value), output=key != 'repo')
    if payload['strategy'] not in ('direct_api', 'api_debugger', 'api_test_feedback'):
        raise ContractError('input_invalid', 'strategy')
    if payload['proposal_contract'] not in ('v1', 'v2', 'v3', 'v4', 'v5', 'v6'):
        raise ContractError('input_invalid', 'proposal_contract')
    if payload['strategy'] == 'api_test_feedback' and payload['proposal_contract'] in ('v1', 'v2'):
        raise ContractError('input_invalid', 'proposal_contract')
    return deepcopy(payload)


class RepairWorkbench:
    def __init__(self, owner, *, provider_factory=None, verifier_factory=None):
        self.owner = owner
        self.provider_factory, self.verifier_factory = provider_factory, verifier_factory
        try:
            raw = read_safe(owner.root, 'repair-registry.json', META_LIMIT)
        except FileNotFoundError:
            with safe_parent(owner.root, 'repairs', identities=owner.anchors) as (fd, leaf):
                try:
                    os.mkdir(leaf, 0o700, dir_fd=fd)
                except FileExistsError:
                    raise ContractError('registry_invalid', 'unowned_repair_root') from None
            self.registry = {'schema_version': 1, 'root_identity': owner.root_identity, 'runs': {}}
            self._save()
        else:
            self.registry = load_json(raw.decode('utf-8'), max_bytes=META_LIMIT, max_depth=32)
            self._validate()
            with open_directory(owner.root / 'repairs', identities=owner.anchors):
                pass
            for run in self.registry['runs'].values():
                if run['state'] in ('running', 'verifying') or run['state'] == 'unknown' and run['binding'] is None:
                    # Never rerun or adopt an unbound output after interruption.
                    run.update(state='unknown', error='closure_unknown')
                    owner.blocked = owner.blocked or 'closure_unknown'
                elif run['state'] == 'unknown':
                    owner.blocked = owner.blocked or 'repair_outcome_unknown'
            self._save()

    def _save(self):
        owner = self.owner
        owner._anchor()
        data = encode_bounded(self.registry, META_LIMIT)
        if len(data) + owner._metadata_bytes(include_repair=False) + len(encode_bounded(owner.registry, META_LIMIT)) > META_LIMIT:
            raise ContractError('workbench_storage_limit', 'metadata')
        safe_write(owner.root, 'repair-registry.json', data, limit=META_LIMIT,
                   budget=owner._budget(), stage='workbench_repair_registry')

    def _validate(self):
        try:
            value = self.registry
            if (type(value) is not dict or set(value) != {'schema_version', 'root_identity', 'runs'}
                    or type(value['schema_version']) is not int or value['schema_version'] != 1
                    or value['root_identity'] != self.owner.root_identity or type(value['runs']) is not dict):
                raise ValueError()
            intents = {run['intent_id'] for run in self.owner.registry['runs'].values()}
            for identifier, run in value['runs'].items():
                _uuid(identifier)
                if type(run) is not dict or set(run) != {'id', 'intent_id', 'payload_digest', 'request', 'started_at',
                        'state', 'repair_status', 'exit_code', 'summary', 'binding', 'error', 'reservation'}:
                    raise ValueError()
                request = _input(run['request'])
                if (run['id'] != identifier or run['intent_id'] != request['intent_id'] or run['intent_id'] in intents
                        or run['payload_digest'] != document(encode_bounded(request, 32768))['sha256']
                        or type(run['started_at']) not in (int, float) or not math.isfinite(run['started_at']) or run['started_at'] <= 0
                        or run['state'] not in _STATES or type(run['reservation']) is not int
                        or not 0 <= run['reservation'] <= REPAIR_RESERVE
                        or run['exit_code'] is not None and (type(run['exit_code']) is not int or run['exit_code'] not in (0, 2))):
                    raise ValueError()
                intents.add(run['intent_id'])
                repo = Path(request['repo'])
                if self.owner.root.is_relative_to(repo) or repo.is_relative_to(self.owner.root):
                    raise ValueError()
                for name in ('repair_status', 'error'):
                    if run[name] is not None and (type(run[name]) is not str or re.fullmatch('[a-z][a-z0-9_]{0,63}', run[name]) is None):
                        raise ValueError()
                if run['summary'] is not None:
                    summary = run['summary']
                    if (type(summary) is not dict or summary.get('status') != run['repair_status']
                            or summary.get('remote_enabled') is not False
                            or summary.get('target_code_executed') is not False):
                        raise ValueError()
                    encode_bounded(summary, 16384)
                binding = run['binding']
                if binding is not None:
                    if (type(binding) is not dict or set(binding) != {'out_identity', 'report'}
                            or type(binding['out_identity']) is not list or len(binding['out_identity']) != 2
                            or any(type(n) is not int or n < 0 for n in binding['out_identity'])
                            or type(binding['report']) is not dict or set(binding['report']) != {'sha256', 'size_bytes'}
                            or type(binding['report']['size_bytes']) is not int or not 0 < binding['report']['size_bytes'] <= 262144
                            or type(binding['report']['sha256']) is not str
                            or re.fullmatch('[0-9a-f]{64}', binding['report']['sha256']) is None):
                        raise ValueError()
                    if run['summary'] is None or run['exit_code'] is None:
                        raise ValueError()
                if run['state'] in ('completed', 'blocked') and (binding is None or run['summary'] is None or run['exit_code'] is None):
                    raise ValueError()
        except (ContractError, ValueError, TypeError, KeyError, UnicodeError):
            raise ContractError('registry_invalid', 'repair_records') from None

    def has_intent(self, intent):
        return any(run['intent_id'] == intent for run in self.registry['runs'].values())

    def _public(self, run):
        return {key: deepcopy(run[key]) for key in ('id', 'intent_id', 'started_at', 'state', 'repair_status',
                'exit_code', 'summary', 'error')} | {'kind': 'repair', 'project': run['request']['repo'],
                'task': run['request']['task'], 'strategy': run['request']['strategy'],
                'proposal_contract': run['request']['proposal_contract'],
                'result_available': run['binding'] is not None and run['error'] is None
                    and run['state'] not in ('running', 'verifying')}

    def _get(self, identifier):
        _uuid(identifier)
        try:
            return self.registry['runs'][identifier]
        except KeyError:
            raise ContractError('run_not_found', 'id') from None

    def state(self, identifier):
        with self.owner.mutex:
            return self._public(self._get(identifier))

    def by_intent(self, intent):
        _uuid(intent)
        with self.owner.mutex:
            for run in self.registry['runs'].values():
                if run['intent_id'] == intent:
                    return self._public(run)
        raise ContractError('run_not_found', 'intent_id')

    def history(self, cursor=0):
        if type(cursor) is not int or cursor < 0:
            raise ContractError('input_invalid', 'cursor')
        with self.owner.mutex:
            records = sorted(self.registry['runs'].values(), key=lambda run: run['started_at'], reverse=True)
            end = cursor + 20
            return {'items': [self._public(run) for run in records[cursor:end]],
                    'next_cursor': end if end < len(records) else None}

    def start(self, payload):
        owner = self.owner
        with owner.mutex:
            owner._anchor()
            if owner.closed or owner.stopping:
                raise ContractError('workbench_stopping', 'start')
            request = _input(payload)
            intent = request['intent_id']
            digest = document(encode_bounded(request, 32768))['sha256']
            for run in self.registry['runs'].values():
                if run['intent_id'] == intent:
                    if run['payload_digest'] != digest:
                        raise ContractError('intent_conflict', 'intent_id')
                    return self._public(run)
            if any(run['intent_id'] == intent for run in owner.registry['runs'].values()):
                raise ContractError('intent_conflict', 'intent_id')
            if owner.blocked:
                raise ContractError(owner.blocked, 'start')
            if owner.active:
                raise ContractError('workbench_busy', 'active_run')
            repo = Path(request['repo'])
            if owner.root.is_relative_to(repo) or repo.is_relative_to(owner.root):
                raise ContractError('forbidden_path', 'output_in_target')
            with open_directory(repo):
                pass
            if owner._allocation() + REPAIR_RESERVE > ROOT_LIMIT:
                raise ContractError('workbench_storage_limit', 'retained_allocation')
            identifier = str(uuid4())
            run = {'id': identifier, 'intent_id': intent, 'payload_digest': digest, 'request': request,
                   'started_at': time.time(), 'state': 'running', 'repair_status': None, 'exit_code': None,
                   'summary': None, 'binding': None, 'error': None, 'reservation': REPAIR_RESERVE}
            self.registry['runs'][identifier] = run
            try:
                self._save()
            except BaseException:
                del self.registry['runs'][identifier]
                raise
            owner.active = identifier
            owner.event = threading.Event()
            owner.thread = threading.Thread(target=self._execute, args=(run,), name='repair-coordinator', daemon=False)
            try:
                owner.thread.start()
            except BaseException:
                owner.active = None
                run.update(state='failed', error='thread_unavailable', reservation=0)
                self._save()
                raise ContractError('workbench_unavailable', 'thread') from None
            return self._public(run)

    def _provider(self, config):
        from ..investigation import repair_entry
        from ..providers.repair_binding import RepairProviderBinding
        provider = (self.provider_factory or repair_entry._make_provider)(config)
        if (type(provider) is not RepairProviderBinding or provider.config != config
                or provider.snapshot()['schema'] != 'macr.repair.provider-meter.v1'
                or provider.snapshot()['remote_enabled'] is not False):
            raise ContractError('repair_runtime_forbidden', 'provider')
        return provider

    def _verifier(self):
        from ..investigation.repair_loop import ReplayVerifier
        verifier = self.verifier_factory() if self.verifier_factory else None
        if verifier is not None and type(verifier) is not ReplayVerifier:
            raise ContractError('repair_runtime_forbidden', 'verifier')
        return verifier

    def _identity(self, out):
        with open_directory(out, identities=self.owner.anchors) as fd:
            info = os.fstat(fd)
        return [info.st_dev, info.st_ino]

    def _read_record(self, run, binding):
        from ..investigation import repair_loop
        out = self.owner.root / 'repairs' / run['id']
        if self._identity(out) != binding['out_identity'] or document(read_safe(out, 'report.json', repair_loop.CAP)) != binding['report']:
            raise ContractError('history_corrupt', 'repair_binding')
        report = repair_loop.verify_loop(out)
        config = repair_loop._read(out, 'claim.json')['config']
        summary = run['summary']
        origins = {'candidate_origin': report['candidate_origin'], 'verification_origin': report['verification_origin']}
        selection_fields = {'selected_content_id', 'final_verification_status', 'historical_verification_origin'}
        selection = report.get('final_selection')
        if selection is not None:
            origins.update(selected_content_id=selection['selected_source_id'], final_verification_status='not_rerun',
                           verification_origin='unavailable', historical_verification_origin=report['verification_origin'])
        if (report['provider_accounting']['remote_enabled'] is not False or report['simulation'] is not True
                or report['target_code_executed'] is not False or report['repaired'] is not False
                or set(summary) & selection_fields != set(origins) & selection_fields
                or any(summary.get(key) != value for key, value in origins.items())
                or config['strategy'] != run['request']['strategy']
                or config.get('proposal_contract', 'v1') != run['request']['proposal_contract']
                or summary.get('task_id') != config['task_id'] or summary.get('strategy') != config['strategy']
                or summary.get('model_configuration_sha256') != report['provider_accounting']['configuration_sha256']
                or any(summary.get(key) != report['provider_accounting'][key] for key in ('known_usd', 'unknown_reserved_usd'))
                or any(summary.get(key) != report[key] for key in ('status', 'provider_requests', 'proposal_requests',
                    'simulation', 'repaired', 'target_code_executed', 'input_tokens', 'output_tokens', 'total_seconds'))):
            raise ContractError('history_corrupt', 'repair_summary')
        proposals = []
        for row in report['rounds']:
            if 'candidate_source_id' in row or row['status'] in ('candidate_finalized', 'candidate_selected_unverified',
                                                               'no_candidate', 'insufficient_evidence'):
                raw = repair_loop.read_proposal_response(out, row['number'])
                proposal = repair_loop.parse_proposal_content(raw['content'], proposal_contract=run['request']['proposal_contract'])
                projected = {'round': row['number']} | proposal
                if 'candidate_source_id' in row:
                    projected['candidate_source_id'] = row['candidate_source_id']
                proposals.append(projected)
        if self._identity(out) != binding['out_identity'] or document(read_safe(out, 'report.json', repair_loop.CAP)) != binding['report']:
            raise ContractError('history_corrupt', 'repair_binding')
        return report, proposals

    def _execute(self, run):
        from ..investigation import repair_entry, repair_loop
        owner = self.owner
        out = owner.root / 'repairs' / run['id']
        summaries = []
        try:
            args = SimpleNamespace(**run['request'], out=out, enable_remote=False, spend_plan=None, spend_cell=None)
            code = repair_entry.run(args, provider_factory=self._provider, verifier_factory=self._verifier, emit=summaries.append)
            if len(summaries) != 1:
                raise ContractError('repair_result_invalid', 'summary')
            summary = deepcopy(summaries[0])
            if summary.get('remote_enabled') is not False or summary.get('target_code_executed') is not False:
                raise ContractError('repair_runtime_forbidden', 'summary')
            with owner.mutex:
                run.update(summary=summary, exit_code=code, repair_status=summary['status'])
            if summary['status'] == 'rejected':
                with owner.mutex:
                    run.update(state=_outcome_state(summary, code), error=summary['error'])
            else:
                with owner.mutex:
                    run['state'] = 'verifying'
                    self._save()
                binding = {'out_identity': self._identity(out),
                           'report': document(read_safe(out, 'report.json', repair_loop.CAP))}
                with owner.consumer:
                    self._read_record(run, binding)
                with owner.mutex:
                    run.update(binding=binding, state=_outcome_state(summary, code))
            with owner.mutex:
                if run['state'] == 'unknown':
                    owner.blocked = owner.blocked or 'repair_outcome_unknown'
        except BaseException as error:
            with owner.mutex:
                uncertain = not summaries or not isinstance(error, Exception)
                run.update(state='unknown' if uncertain else 'failed',
                           error=error.code if isinstance(error, ContractError) else 'repair_escaped')
                if uncertain:
                    owner.blocked = owner.blocked or 'closure_unknown'
        finally:
            with owner.mutex:
                if run['state'] != 'unknown':
                    run['reservation'] = 0
                try:
                    owner._allocation()
                    self._save()
                except BaseException:
                    owner.blocked = owner.blocked or 'storage_unavailable'
                    run['error'] = run['error'] or 'storage_unavailable'
                owner.active = None

    def result(self, identifier):
        owner = self.owner
        with owner.mutex:
            owner._anchor()
            if owner.active:
                raise ContractError('workbench_busy', 'history_read')
            run = self._get(identifier)
            if run['binding'] is None:
                raise ContractError('report_unavailable', 'repair_record')
            try:
                with owner.consumer:
                    report, proposals = self._read_record(run, run['binding'])
            except (ContractError, OSError, ValueError, TypeError, KeyError) as error:
                run.update(state='failed', error=error.code if isinstance(error, ContractError) else 'history_unavailable')
                try:
                    self._save()
                except (ContractError, OSError):
                    owner.blocked = owner.blocked or 'storage_unavailable'
                raise ContractError('history_corrupt', 'repair_record') from None
            run.update(state=_outcome_state(run['summary'], run['exit_code']), error=None)
            try:
                self._save()
            except (ContractError, OSError):
                owner.blocked = owner.blocked or 'storage_unavailable'
            return {'state': self._public(run), 'summary': deepcopy(run['summary']), 'report': report, 'proposals': proposals}
