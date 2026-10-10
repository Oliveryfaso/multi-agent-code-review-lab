"""Single-writer journal and checkpoints under the approved artifact root.

Flush/replace recovery is tested; this is not an exactly-once or power-loss guarantee.
Unknown jobs remain recorded and are never dispatched by the storage layer.
"""
from __future__ import annotations

import fcntl
import json
import os
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from threading import RLock

from macr.investigation import records as r
from macr.investigation.budget import BudgetLedger, FIELDS
from macr.investigation.queue import InvestigationQueue
from macr.investigation.scope import read_safe
from macr.investigation.snapshots import SnapshotStore
from macr.investigation.validation import ContractError, decode_record, validate_action, validate_identifier, validate_transition, TRANSITIONS, TERMINALS
from macr.memory.board import AgentBoard
from macr.memory.evidence_store import EvidenceStore
from macr.memory.atomic_file import atomic_write


class StorageError(ContractError):
    def __init__(self, code='storage_unavailable', field='run_store'):
        super().__init__(code, field)


def encoded(value) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError):
        raise ContractError('state_not_serializable') from None


class RunStore:
    MAX_BYTES = 64 * 1024 * 1024
    BACKEND = "json"

    def __init__(self, root: Path | None = None, *, snapshots: SnapshotStore | None = None, profiles: dict[str, set[str]] | None = None):
        self.allowed = (Path(__file__).resolve().parents[3] / 'artifacts').resolve()
        self.root = (root or self.allowed / 'runs').resolve()
        if not self.root.is_relative_to(self.allowed):
            raise StorageError('forbidden_path')
        self.snapshots = snapshots or SnapshotStore()
        self.profiles = profiles or {'scope': {'default'}, 'execution': {'none'}, 'model': {'rules', 'mock'}, 'budget': {'locate-default-v1', 'scan-default-v1'}}
        self._failed, self._closed, self._fd = False, False, None
        self._mutex = RLock()
        self.last_usage = r.ActionUsage()
        database = self.root / "runs.sqlite3"
        if self.BACKEND == "json" and (database.exists() or database.is_symlink()):
            raise StorageError("backend_mismatch")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(self.root / 'writer.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.close()
            raise StorageError() from None

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self._closed = True

    def _guard(self):
        if self._failed or self._closed:
            raise StorageError()

    def _folder(self, run_id: str) -> Path:
        validate_identifier(run_id)
        folder = self.root / run_id
        if folder.is_symlink() or folder.resolve().parent != self.root:
            raise StorageError('forbidden_path')
        return folder

    def _json(self, folder: Path, relative: str):
        try:
            raw = read_safe(folder, relative, self.MAX_BYTES)
            self.last_usage.bytes_read += len(raw)
            return json.loads(raw)
        except (OSError, UnicodeError, ValueError):
            raise StorageError('state_unreadable') from None

    def _validate_state(self, state: r.InvestigationState) -> r.InvestigationState:
        state = decode_record(r.InvestigationState, asdict(state))
        task, ref = state.task, state.task.snapshot_ref
        if ref is None or state.scope is None or state.phase not in set(TRANSITIONS) | TERMINALS:
            raise ContractError('state_identity_invalid')
        for kind, value in [('scope', task.scope_profile), ('execution', task.execution_profile), ('model', task.model_profile), ('budget', state.budget.profile.profile_id)]:
            if value not in self.profiles.get(kind, set()):
                raise ContractError('unknown_profile', kind)
        if task.budget_profile not in ('default', state.budget.profile.profile_id) or task.scope_profile != state.scope.profile.profile_id or ref.scope_version != state.scope.scope_version:
            raise ContractError('profile_identity_invalid')
        if state.scope.errors or state.index is not None and state.index.content_id != ref.content_id:
            raise ContractError('state_identity_invalid')
        self.snapshots.verify(ref)
        self.last_usage.bytes_read += self.snapshots.last_usage.bytes_read
        self.last_usage.tool_calls += 1
        previous_bytes = self.snapshots.last_usage.bytes_read
        scope_files = {f.path: f for f in state.scope.eligible}
        manifest = self.snapshots._manifest(ref)
        self.last_usage.bytes_read += self.snapshots.last_usage.bytes_read - previous_bytes
        manifest_files = manifest.files
        if manifest.scope_profile != asdict(state.scope.profile):
            raise ContractError('scope_identity_invalid')
        if set(scope_files) != {entry['path'] for entry in manifest_files}:
            raise ContractError('scope_identity_invalid')
        for entry in manifest_files:
            scoped = scope_files[entry['path']]
            if scoped.size_bytes != entry['size_bytes'] or scoped.language != entry['language']:
                raise ContractError('scope_identity_invalid')
        self._ledger(state.budget)
        evidence = EvidenceStore(ref)
        for key, record in state.evidence.items():
            validate_identifier(key)
            if key != record.evidence_id:
                raise ContractError('evidence_identity_invalid')
            if record.location:
                entry = scope_files.get(record.location.file)
                if entry is None or record.location.end > (entry.lines or 0):
                    raise ContractError('citation_invalid')
            evidence.add_record(record)
        for key, record in state.hypotheses.items():
            validate_identifier(key)
            if key != record.hypothesis_id:
                raise ContractError('hypothesis_identity_invalid')
            for evidence_id in record.support_ids + record.counter_ids:
                evidence.get_record(evidence_id)
        if set(state.inflight) & set(state.completed):
            raise ContractError('action_identity_conflict')
        reservations = state.budget.reservations
        for key, action in state.inflight.items():
            if key != action.action_id or key not in reservations or reservations[key].state not in ('reserved', 'unknown'):
                raise ContractError('inflight_identity_invalid')
            validate_action(asdict(action), state)
        for key, result in state.completed.items():
            if key != result.action_id or key not in reservations or reservations[key].state != 'settled' or result.status == 'outcome_unknown':
                raise ContractError('completed_identity_invalid')
            for evidence_id in result.evidence_ids:
                evidence.get_record(evidence_id)
        for key, reservation in reservations.items():
            if reservation.state in ('reserved', 'unknown') and key not in state.inflight or reservation.state == 'settled' and key not in state.completed:
                raise ContractError('orphan_reservation')
        for key, check in state.checks.items():
            if key != check.check_id:
                raise ContractError('check_identity_invalid')
        queue = InvestigationQueue.restore(state.queue)
        for entry in queue._pending:
            action = entry['action']
            if action.action_id in state.inflight or action.action_id in state.completed:
                raise ContractError('queued_action_conflict')
            validate_action(asdict(action), state)
        AgentBoard.from_messages(state.messages, evidence=evidence, hypothesis_ids=set(state.hypotheses), request_ids=set(state.inflight) | set(state.completed))
        for role, cursor in state.cursors.items():
            if role not in ('coordinator', 'investigator', 'falsifier', 'retrieval_critic', 'verifier', 'report') or cursor < 0 or cursor > len(state.messages):
                raise ContractError('message_cursor_invalid')
        return state

    @staticmethod
    def _ledger(view):
        # Replay accounting at the persisted timestamp; runtime resume measures downtime separately.
        return BudgetLedger.restore(view, clock=lambda: 0.0, wall_clock=lambda: view.captured_at or 0.0)

    def _budget_transition(self, before, proposed, expected):
        proposed = decode_record(r.BudgetView, proposed)
        self._ledger(proposed)
        if proposed.profile != before.profile or proposed.reservations != expected.reservations or proposed.reserved != expected.reserved or proposed.bounds_exceeded != expected.bounds_exceeded:
            raise ContractError('budget_event_invalid')
        if any(getattr(proposed.used, name) != getattr(expected.used, name) for name in FIELDS) or proposed.used.active_seconds < before.used.active_seconds:
            raise ContractError('budget_event_invalid')
        return proposed

    def _apply(self, state, event):
        state = deepcopy(state)
        if event.kind == 'phase':
            if set(event.payload) != {'phase'}:
                raise ContractError('event_payload_invalid')
            validate_transition(state.phase, event.payload['phase'])
            state.phase = event.payload['phase']
        elif event.kind == 'reservation':
            if set(event.payload) != {'action', 'reservation', 'budget'}:
                raise ContractError('event_payload_invalid')
            action = validate_action(event.payload['action'], state)
            reservation = decode_record(r.Reservation, event.payload['reservation'])
            if event.action_id != action.action_id or action.action_id != reservation.action_id or action.action_id in state.completed or action.action_id in state.inflight:
                raise ContractError('action_identity_conflict')
            ledger = self._ledger(state.budget)
            expected_reservation = ledger.reserve(action, reservation.upper_bounds)
            if expected_reservation != reservation:
                raise ContractError('reservation_event_invalid')
            state.budget = self._budget_transition(state.budget, event.payload['budget'], ledger.view())
            state.inflight[action.action_id] = action
        elif event.kind == 'result':
            if set(event.payload) != {'result', 'budget'}:
                raise ContractError('event_payload_invalid')
            result = decode_record(r.ActionResult, event.payload['result'])
            if event.action_id != result.action_id or result.action_id not in state.inflight or result.action_id in state.completed:
                raise ContractError('action_identity_conflict')
            ledger = self._ledger(state.budget)
            expected = ledger.settle(result.action_id, result.usage)
            state.budget = self._budget_transition(state.budget, event.payload['budget'], expected)
            if result.status != 'outcome_unknown' and state.budget.reservations[result.action_id].state == 'settled':
                state.inflight.pop(result.action_id)
                state.completed[result.action_id] = result
        else:
            raise ContractError('unknown_event')
        return self._validate_state(state)

    def _events(self, folder):
        path = folder / 'events.jsonl'
        if not path.exists():
            return [], False
        raw = read_safe(folder, 'events.jsonl', self.MAX_BYTES)
        self.last_usage.bytes_read += len(raw)
        complete, partial = [], bool(raw and not raw.endswith(b'\n'))
        lines = raw.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if not line.endswith(b'\n') and i == len(lines)-1:
                break
            try:
                complete.append(decode_record(r.RunEvent, json.loads(line)))
            except (ContractError, ValueError, UnicodeError):
                raise StorageError('event_corrupt') from None
        return complete, partial

    def _load(self, run_id):
        folder = self._folder(run_id)
        envelope = self._json(folder, 'checkpoint.json')
        if not isinstance(envelope, dict) or set(envelope) != {'schema_version', 'sequence', 'state'} or envelope['schema_version'] != 1 or type(envelope['sequence']) is not int or envelope['sequence'] < 0:
            raise ContractError('checkpoint_schema_invalid')
        sequence = envelope['sequence']
        state = self._validate_state(decode_record(r.InvestigationState, envelope['state']))
        if state.task.run_id != run_id:
            raise ContractError('run_identity_invalid')
        events, partial = self._events(folder)
        previous = 0
        for event in events:
            if event.run_id != run_id or event.sequence != previous+1:
                raise StorageError('event_sequence_invalid')
            previous = event.sequence
            if event.sequence > sequence:
                state = self._apply(state, event)
                sequence = event.sequence
        if envelope['sequence'] > previous and envelope['sequence'] != 0:
            raise StorageError('checkpoint_ahead_of_journal')
        errors = []
        if partial:
            self._failed = True
            errors.append(r.RunError('event_tail_incomplete', 'Original journal retained; new dispatch must remain blocked.'))
        uncertain = sorted(state.inflight)
        return r.ResumeResult(state, uncertain, errors, deepcopy(self.last_usage)), sequence

    def load(self, run_id: str) -> r.ResumeResult:
        with self._mutex:
            self.last_usage = r.ActionUsage(tool_calls=1)
            try:
                return self._load(run_id)[0]
            except OSError:
                self._failed = True
                raise StorageError() from None

    def _write(self, path: Path, data: bytes, flags: int):
        fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    def _check_forward(self, prior, state):
        if prior.task != state.task or prior.scope != state.scope or any(key not in state.inflight or state.inflight[key] != action for key, action in prior.inflight.items()) or any(state.completed.get(key) != result for key, result in prior.completed.items()):
            raise ContractError('checkpoint_rewind')
        if any(state.budget.reservations.get(key) != reservation for key, reservation in prior.budget.reservations.items()) or any(getattr(state.budget.used, field) < getattr(prior.budget.used, field) for field in FIELDS + ('active_seconds',)) or prior.budget.bounds_exceeded and not state.budget.bounds_exceeded:
            raise ContractError('checkpoint_rewind')
        if state.phase != prior.phase:
            validate_transition(prior.phase, state.phase)
        if any(state.evidence.get(key) != record for key, record in prior.evidence.items()) or not set(prior.hypotheses) <= set(state.hypotheses):
            raise ContractError('checkpoint_rewind')
        if len(state.messages) < len(prior.messages) or any(state.cursors.get(role, -1) < cursor for role, cursor in prior.cursors.items()):
            raise ContractError('checkpoint_rewind')
        for previous, current in zip(prior.messages, state.messages):
            if asdict(previous) | {'consumption_status': current.consumption_status} != asdict(current) or previous.consumption_status == 'consumed' and current.consumption_status != 'consumed':
                raise ContractError('checkpoint_rewind')

    def checkpoint(self, state: r.InvestigationState) -> r.CheckpointRef:
        with self._mutex:
            self.last_usage = r.ActionUsage(tool_calls=1)
            self._guard()
            state = self._validate_state(state)
            folder = self._folder(state.task.run_id)
            try:
                folder.mkdir(parents=True, exist_ok=True)
                sequence = 0
                if (folder / 'checkpoint.json').exists():
                    recovered, sequence = self._load(state.task.run_id)
                    self._check_forward(recovered.state, state)
                self._guard()
                data = encoded({'schema_version': 1, 'sequence': sequence, 'state': asdict(state)})
                if len(data) > self.MAX_BYTES:
                    self._failed = True
                    raise StorageError('state_size_limit')
                atomic_write(folder / 'checkpoint.json', data)
                return r.CheckpointRef(state.task.run_id, sequence, str(folder / 'checkpoint.json'))
            except OSError:
                self._failed = True
                raise StorageError() from None

    def append(self, event: r.RunEvent) -> int:
        with self._mutex:
            self.last_usage = r.ActionUsage(tool_calls=1)
            self._guard()
            event = decode_record(r.RunEvent, asdict(event))
            recovered, previous = self._load(event.run_id)
            self._guard()
            if event.sequence != previous+1:
                raise ContractError('event_sequence_invalid')
            self._apply(recovered.state, event)
            folder = self._folder(event.run_id)
            data = encoded(asdict(event)) + b'\n'
            path = folder / 'events.jsonl'
            try:
                if len(data) + (path.stat().st_size if path.exists() else 0) > self.MAX_BYTES:
                    self._failed = True
                    raise StorageError('journal_size_limit')
                self._write(path, data, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            except OSError:
                self._failed = True
                raise StorageError() from None
            return event.sequence

    def read_legacy(self, path: Path) -> dict:
        path = Path(path)
        project = self.allowed.parent
        if not path.resolve().is_relative_to(project) or path.is_symlink() or path.suffix != '.json':
            raise ContractError('forbidden_path')
        from macr.investigation.scope import exclusion
        relative = path.resolve().relative_to(project).as_posix()
        # Legacy traces are permitted explicitly; secret-like names remain excluded.
        if any(exclusion(part, r.ScopeProfile()) in ('secret_path', 'appledouble') for part in relative.split('/')):
            raise ContractError('forbidden_path')
        return {'payload': self._json(path.parent, path.name), 'validation_status': 'legacy_unverified'}
