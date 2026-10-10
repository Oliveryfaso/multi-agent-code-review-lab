"""Opt-in single-writer transaction storage for new investigation roots.

Domain checks come from RunStore; unknown actions never dispatch on recovery.
JSON roots remain unchanged. A database transaction cannot make tool effects
exactly once or guarantee durability on storage that ignores synchronization.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
import stat

from macr.investigation import records as r
from macr.investigation.validation import ContractError, decode_record
from macr.memory.run_store import RunStore, StorageError, encoded


class SQLiteRunStore(RunStore):
    BACKEND = 'sqlite'
    _SCHEMA = {
        'runs': 'CREATE TABLE runs (run_id TEXT PRIMARY KEY NOT NULL, sequence INTEGER NOT NULL CHECK(sequence>=0), state BLOB NOT NULL)',
        'events': 'CREATE TABLE events (run_id TEXT NOT NULL REFERENCES runs(run_id), sequence INTEGER NOT NULL CHECK(sequence>0), payload BLOB NOT NULL, PRIMARY KEY(run_id,sequence))',
    }

    def __init__(self, root: Path | None = None, *, snapshots=None, profiles=None):
        self._connection, self._db_identity = None, None
        default = Path(__file__).resolve().parents[3] / 'artifacts' / 'sqlite-runs'
        super().__init__(root or default, snapshots=snapshots, profiles=profiles)
        self._path = self.root / 'runs.sqlite3'
        try:
            self._check_files()
            if not self._path.exists():
                fd = os.open(self._path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                os.close(fd)
            info = self._path.lstat()
            self._db_identity = (info.st_dev, info.st_ino)
            self._connection = sqlite3.connect(self._path, timeout=0, isolation_level=None, check_same_thread=False)
            self._check_files()
            self._connection.execute('PRAGMA trusted_schema=OFF')
            version = self._connection.execute('PRAGMA user_version').fetchone()[0]
            tables = dict(self._connection.execute("SELECT name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_autoindex_%'"))
            if not (version == 0 and not tables or version == 1 and tables == self._SCHEMA):
                raise StorageError('database_schema_invalid')
            self._connection.execute('PRAGMA foreign_keys=ON')
            mode = self._connection.execute('PRAGMA journal_mode').fetchone()[0]
            if mode != 'delete':
                raise StorageError('database_configuration_invalid')
            self._connection.execute('PRAGMA synchronous=FULL')
            self._connection.execute('PRAGMA cache_size=-1024')
            page_size = self._connection.execute('PRAGMA page_size').fetchone()[0]
            limit = self._connection.execute(f'PRAGMA max_page_count={self.MAX_BYTES // page_size}').fetchone()[0]
            if limit * page_size > self.MAX_BYTES:
                raise StorageError('database_size_limit')
            if version == 0:
                with self._transaction():
                    for statement in self._SCHEMA.values():
                        self._connection.execute(statement)
                    self._connection.execute('PRAGMA user_version=1')
        except StorageError:
            self.close()
            raise
        except (OSError, sqlite3.Error, ContractError):
            self.close()
            raise StorageError('database_unavailable') from None

    def _check_files(self):
        if self.root.resolve() != self.root or not self.root.is_dir():
            raise StorageError('forbidden_path')
        if self._db_identity is not None and not self._path.exists():
            raise StorageError('database_identity_changed')
        allowed = {'writer.lock', 'runs.sqlite3', 'runs.sqlite3-journal'}
        for path in self.root.iterdir():
            name = path.name[2:] if path.name.startswith('._') else path.name
            if name not in allowed:
                raise StorageError('backend_mismatch')
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise StorageError('forbidden_path')
            if info.st_size > self.MAX_BYTES + 1024 * 1024:
                raise StorageError('database_size_limit')
            if path == self._path and self._db_identity is not None and (info.st_dev, info.st_ino) != self._db_identity:
                raise StorageError('database_identity_changed')

    def close(self):
        # The inherited mutex serializes a shared connection across caller threads.
        if getattr(self, '_mutex', None) is not None:
            with self._mutex:
                if self._connection is not None:
                    self._connection.close()
                    self._connection = None
                super().close()

    def _commit(self):
        self._connection.execute('COMMIT')

    @contextmanager
    def _transaction(self, *, write=True):
        if write:
            self._guard()
        elif self._closed:
            raise StorageError()
        owned = not self._connection.in_transaction
        try:
            self._check_files()
            if owned:
                self._connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield
            if owned:
                self._commit()
        except BaseException as error:
            if isinstance(error, StorageError) or not isinstance(error, ContractError):
                self._failed = True
            if owned and self._connection is not None and self._connection.in_transaction:
                try:
                    self._connection.execute('ROLLBACK')
                except sqlite3.Error:
                    self._failed = True
            if isinstance(error, (OSError, sqlite3.Error)):
                raise StorageError() from None
            raise

    def _read(self, run_id):
        with self._transaction(write=False):
            return self._read_snapshot(run_id)

    def _read_snapshot(self, run_id):
        if self._closed:
            raise StorageError()
        self._folder(run_id)
        self._check_files()
        row = self._connection.execute('SELECT sequence,state FROM runs WHERE run_id=?', (run_id,)).fetchone()
        if row is None:
            raise StorageError('state_unreadable')
        sequence, raw = row
        if type(sequence) is not int or sequence < 0 or type(raw) is not bytes or len(raw) > self.MAX_BYTES:
            raise StorageError('checkpoint_schema_invalid')
        self.last_usage.bytes_read += len(raw)
        try:
            state = self._validate_state(decode_record(r.InvestigationState, json.loads(raw)))
        except (ValueError, UnicodeError):
            raise StorageError('state_unreadable') from None
        if state.task.run_id != run_id:
            raise ContractError('run_identity_invalid')
        events, size = [], 0
        for number, data in self._connection.execute('SELECT sequence,payload FROM events WHERE run_id=? ORDER BY sequence', (run_id,)):
            if type(data) is not bytes or type(number) is not int or number != len(events) + 1:
                raise StorageError('event_sequence_invalid')
            size += len(data)
            if size > self.MAX_BYTES:
                raise StorageError('journal_size_limit')
            self.last_usage.bytes_read += len(data)
            try:
                event = decode_record(r.RunEvent, json.loads(data))
            except (ContractError, ValueError, UnicodeError):
                raise StorageError('event_corrupt') from None
            if event.run_id != run_id or event.sequence != number:
                raise StorageError('event_sequence_invalid')
            events.append(event)
        if len(events) != sequence:
            raise StorageError('checkpoint_ahead_of_journal')
        recovered = r.ResumeResult(state, sorted(state.inflight), [], deepcopy(self.last_usage))
        return recovered, sequence, events

    def _load(self, run_id):
        try:
            recovered, sequence, _ = self._read(run_id)
            return recovered, sequence
        except StorageError:
            self._failed = True
            raise
        except (OSError, sqlite3.Error):
            self._failed = True
            raise StorageError('state_unreadable') from None

    def read_events(self, run_id: str) -> list[r.RunEvent]:
        """Read the bounded, typed journal without executing recorded actions."""
        with self._mutex:
            self.last_usage = r.ActionUsage(tool_calls=1)
            try:
                return self._read(run_id)[2]
            except StorageError:
                self._failed = True
                raise
            except (OSError, sqlite3.Error):
                self._failed = True
                raise StorageError('event_corrupt') from None

    def checkpoint(self, state: r.InvestigationState) -> r.CheckpointRef:
        with self._mutex:
            self.last_usage = r.ActionUsage(tool_calls=1)
            self._guard()
            state = self._validate_state(state)
            self._folder(state.task.run_id)
            with self._transaction():
                row = self._connection.execute('SELECT sequence FROM runs WHERE run_id=?', (state.task.run_id,)).fetchone()
                sequence = 0
                if row is not None:
                    recovered, sequence = self._load(state.task.run_id)
                    self._check_forward(recovered.state, state)
                data = encoded(asdict(state))
                if len(data) > self.MAX_BYTES:
                    raise StorageError('state_size_limit')
                if row is None:
                    self._connection.execute('INSERT INTO runs VALUES(?,?,?)', (state.task.run_id, sequence, data))
                else:
                    changed = self._connection.execute('UPDATE runs SET state=? WHERE run_id=? AND sequence=?', (data, state.task.run_id, sequence))
                    if changed.rowcount != 1:
                        raise ContractError('event_sequence_invalid')
            return r.CheckpointRef(state.task.run_id, sequence, str(self._path))

    def append(self, event: r.RunEvent) -> int:
        with self._mutex:
            self.last_usage = r.ActionUsage(tool_calls=1)
            self._guard()
            event = decode_record(r.RunEvent, asdict(event))
            with self._transaction():
                recovered, previous = self._load(event.run_id)
                if event.sequence != previous + 1:
                    raise ContractError('event_sequence_invalid')
                state = self._apply(recovered.state, event)
                data, state_data = encoded(asdict(event)), encoded(asdict(state))
                if max(len(data), len(state_data)) > self.MAX_BYTES:
                    raise StorageError('state_size_limit')
                self._connection.execute('INSERT INTO events VALUES(?,?,?)', (event.run_id, event.sequence, data))
                changed = self._connection.execute('UPDATE runs SET sequence=?,state=? WHERE run_id=? AND sequence=?', (event.sequence, state_data, event.run_id, previous))
                if changed.rowcount != 1:
                    raise ContractError('event_sequence_invalid')
            return event.sequence
