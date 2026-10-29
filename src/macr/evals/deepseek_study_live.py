"""Disabled-by-default candidate for one separately approved, fresh study.

Approval records document local operator consent; they are not signatures.
Import, proposal and preflight never acquire credentials or send requests.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
import stat

from macr.evals.deepseek_policy_runner import PolicyRunError, _write_exclusive
from macr.evals.deepseek_study_batch import _FixtureGate, _run_study_core
from macr.evals.deepseek_study_profile import STRATEGIES, StudyError, digest, load_study, read_json, require
from macr.evals.deepseek_study_runner import _run_study_cell_core
from macr.investigation.scope import open_directory
from macr.providers.base import ProviderError
from macr.providers.deepseek import DeepSeekProvider, _ApprovedTransport

ENDPOINT = 'https://api.deepseek.com/chat/completions'
MODEL = {'model_id': 'deepseek-flash', 'provider': 'deepseek',
         'thinking': True, 'reasoning_effort': 'high'}
PRICE = {'checked_date': '2026-10-29', 'source': 'https://api-docs.deepseek.com/quick_start/pricing/',
    'input_miss_per_million': '0.30', 'input_hit_per_million': '0.006',
    'output_per_million': '1.20', 'context_tokens': 1000000,
    'max_output_per_request': 2000, 'reservation_micro_usd': 330000}
LOG_POLICY = {'retention_days': 14, 'upload': False, 'auto_delete': False,
              'purge_requires_user_approval': True}


def _proposal(study):
    require('execution_profile' not in study, 'study_profile_live_disabled')
    tasks, cells = study['tasks'], study['cells']
    projects = Counter(task['project_id'] for task in tasks)
    expected = {(task['id'], strategy) for task in tasks for strategy in STRATEGIES}
    require(len(tasks) == 9 and len(projects) == 3 and set(projects.values()) == {3}
        and len(cells) == 36 and len({cell['cell_id'] for cell in cells}) == 36
        and {(cell['task_id'], cell['strategy']) for cell in cells} == expected
        and study['planned_request_ceiling'] == 108, 'study_live_grid_invalid')
    require(study['budget'] == {'max_requests': 108, 'max_usd': '2.000000'},
        'study_live_budget_invalid')
    require(all(task['manifest']['model'] == MODEL for task in tasks), 'study_live_model_invalid')
    root = Path(study['control_root'])
    with open_directory(root) as descriptor:
        require(stat.S_IMODE(os.fstat(descriptor).st_mode) == 0o700, 'study_live_log_root_not_private')
    sources = [{'path': task['snapshot_root'] + '/' + name, 'sha256': row['sha256']}
        for task in tasks for name, row in sorted(task['manifest']['snapshot']['files'].items())]
    return {'schema': 'macr.deepseek.study.live-approval.v1', 'authorized': False,
        'confirmation_ref': '', 'authorization_scope': 'fresh-study-no-prior-requests-or-funds',
        'control_root': study['control_root'], 'profile_sha256': study['profile_sha256'],
        'pack_root': study['pack_root'], 'pack_sha256': study['pack_sha256'], 'sources': sources,
        'cell_ids': [cell['cell_id'] for cell in cells], 'cells_sha256': digest(cells),
        'model': dict(MODEL), 'endpoint': ENDPOINT, 'max_requests': 108, 'max_usd': '2.000000',
        'price_snapshot': dict(PRICE), 'log_root': study['control_root'],
        'log_policy': dict(LOG_POLICY), 'concurrent_requests': 1, 'automatic_retries': 0,
        'resume': False, 'ledger_reset': False}


def propose_study_live_approval(control_root):
    """Return a concrete proposal with authorization explicitly absent."""
    return _proposal(load_study(control_root))


def preflight_approved_study(control_root, approval):
    study = load_study(control_root)
    expected = _proposal(study)
    require(type(approval) is dict and approval.get('authorized') is True
        and type(approval.get('confirmation_ref')) is str
        and 0 < len(approval['confirmation_ref'].strip()) <= 128,
        'study_live_approval_missing')
    require(approval | {'authorized': False, 'confirmation_ref': ''} == expected,
        'study_live_approval_scope_mismatch')
    return study


class _ScriptedWire:
    """Bounded literal test replies only; no arbitrary transport callback."""
    def __init__(self, replies):
        require(type(replies) is tuple and 1 <= len(replies) <= 3, 'study_live_script_invalid')
        self.replies = []
        for reply in replies:
            if type(reply) is str and reply == 'timeout':
                self.replies.append(reply)
                continue
            require(type(reply) is dict, 'study_live_script_invalid')
            try:
                raw = json.dumps(reply, allow_nan=False).encode('utf-8')
                require(len(raw) <= 1048576, 'study_live_script_invalid')
                self.replies.append(json.loads(raw))
            except (TypeError, ValueError, UnicodeError, RecursionError):
                raise StudyError('study_live_script_invalid') from None

    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        if not self.replies:
            raise ProviderError('model_transport')
        reply = self.replies.pop(0)
        if reply == 'timeout':
            raise TimeoutError() from None
        return reply


class _BoundWire:
    def __init__(self, inner, *, simulated):
        self.inner, self.simulated = inner, simulated
        self.expected_hash, self.live_attempts = None, 0

    @property
    def response_metadata(self):
        return getattr(self.inner, 'response_metadata', None)

    def post(self, endpoint, body, timeout_seconds, max_response_bytes):
        if (endpoint != ENDPOINT or type(body) is not bytes or self.expected_hash is None
                or sha256(body).hexdigest() != self.expected_hash):
            raise ProviderError('model_request_invalid')
        self.expected_hash = None  # Consume once, including uncertain transport failures.
        if not self.simulated:
            self.live_attempts += 1
        return self.inner.post(endpoint, body, timeout_seconds, max_response_bytes)


class _StudyGate(_FixtureGate):
    def __init__(self, root, study, provenance):
        super().__init__(root, study, provenance=provenance)
        self.wire = None

    def admit(self, request_sha256):
        if self.wire is None:
            raise PolicyRunError('study_live_wire_missing')
        super().admit(request_sha256)
        self.wire.expected_hash = request_sha256

    def settle(self, request_sha256, **outcome):
        try:
            return super().settle(request_sha256, **outcome)
        finally:
            self.wire.expected_hash = None


def execute_approved_study(control_root, approval, *, enabled=False, key_reader=None,
                           scripted_by_cell=None):
    """Activate only an explicit exact scope; claims survive every failure."""
    require(enabled is True, 'study_live_disabled')
    study = preflight_approved_study(control_root, approval)
    require(callable(key_reader), 'study_live_key_reader_invalid')
    simulated = scripted_by_cell is not None
    scripts = {}
    if simulated:
        require(type(scripted_by_cell) is dict and set(scripted_by_cell) ==
            {cell['cell_id'] for cell in study['cells']}, 'study_live_script_invalid')
        scripts = {cell: _ScriptedWire(replies) for cell, replies in scripted_by_cell.items()}
    provenance = 'approved_live_fake' if simulated else 'approved_live'
    root = Path(study['control_root'])
    retention = {'schema': 'macr.deepseek.study.log.v1',
        'profile_sha256': study['profile_sha256'], 'confirmation_ref': approval['confirmation_ref'],
        'contents': ['source', 'model_output', 'reasoning_content', 'usage', 'receipts'], **LOG_POLICY}
    try:
        _write_exclusive(root / 'retention.json', retention, 4096)
    except FileExistsError:
        require(read_json(root, 'retention.json', 4096) == retention, 'study_live_log_policy_mismatch')
    key, transports, gate = None, [], None

    def gate_factory(root, frozen, *, provenance):
        nonlocal gate
        require(frozen['profile_sha256'] == study['profile_sha256'], 'study_live_profile_changed')
        gate = _StudyGate(root, frozen, provenance)
        return gate

    def provider_factory(cell):
        nonlocal key
        if key is None:
            try:
                key = key_reader()
            except Exception:
                raise StudyError('study_live_key_input_failed') from None
            require(type(key) is str and 1 <= len(key) <= 4096 and key == key.strip()
                and all(32 < ord(char) < 127 for char in key), 'study_live_key_invalid')
        inner = scripts[cell['cell_id']] if simulated else _ApprovedTransport(key)
        transports.append(inner)
        wire = _BoundWire(inner, simulated=simulated)
        gate.wire = wire
        return DeepSeekProvider(model=MODEL['model_id'], transport=wire, allow_remote=False)

    def cell_runner(*args, **kwargs):
        return _run_study_cell_core(*args, **kwargs, provenance=provenance)

    try:
        return _run_study_core(root, provider_factory=provider_factory, cell_runner=cell_runner,
            provenance=provenance, gate_factory=gate_factory)
    finally:
        key = None
        for transport in transports:
            if type(transport) is _ApprovedTransport:
                transport._api_key = ''
