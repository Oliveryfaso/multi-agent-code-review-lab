"""Isolated, synthetic LangGraph recovery experiment; never dispatches real work."""

from __future__ import annotations

import argparse
import fcntl
import importlib.metadata
import json
import os
import re
import sqlite3
import stat
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
from unittest.mock import patch

from macr.investigation.validation import ContractError, validate_identifier
from macr.memory.run_store import encoded
from macr.providers.base import ModelRequest, ModelResponse, invoke
from macr.providers.mock import MockLLMProvider

WORKFLOW_VERSION = "mock-v2"
PACKAGES = {"langgraph": "1.2.14", "langgraph-checkpoint-sqlite": "3.1.1",
            "langgraph-checkpoint": "4.2.0"}
MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT_BYTES = 256
RUNS_ROOT = (Path(__file__).resolve().parents[3] / "artifacts" / "implementation" /
             "langgraph-mock-recovery-01" / "runs")
THREAD_ID = "mock-recovery"
OWNER = b"macr-langgraph-mock-recovery-01\n"
REPORT_TEXT = "Synthetic evidence reviewed."
UNKNOWN_EXPLANATION = ("The simulated external outcome is unknown. Automatic replay is disabled; "
                       "no external action was performed.")
FILES = {"writer.lock", "checkpoints.sqlite3", "checkpoints.sqlite3-wal",
         "checkpoints.sqlite3-shm"}
SCHEMA = {
    "checkpoints": ("thread_id", "checkpoint_ns", "checkpoint_id",
                    "parent_checkpoint_id", "type", "checkpoint", "metadata"),
    "writes": ("thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx",
               "channel", "type", "value"),
}
SCHEMA_KEYS = {"checkpoints": (1, 2, 3, 0, 0, 0, 0),
               "writes": (1, 2, 3, 4, 5, 0, 0, 0)}


class RecoveryError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def build_input(text: str = "synthetic evidence", scenario: str = "normal") -> dict:
    return {"schema_version": 1, "workflow_version": WORKFLOW_VERSION,
            "scenario": scenario, "text": text}


def _input(value: dict) -> dict:
    if (type(value) is not dict or set(value) != set(build_input()) or
            type(value["schema_version"]) is not int or value["schema_version"] != 1 or
            value["workflow_version"] != WORKFLOW_VERSION or
            value["scenario"] not in ("normal", "pause", "unknown") or
            type(value["text"]) is not str or not value["text"]):
        raise RecoveryError("input_incompatible")
    try:
        length = len(value["text"].encode("utf-8"))
    except UnicodeError:
        raise RecoveryError("input_incompatible") from None
    if length > MAX_TEXT_BYTES:
        raise RecoveryError("input_incompatible")
    return dict(value)


def _json(value) -> str:
    return encoded(value).decode("utf-8")


def _binding(payload: dict) -> dict:
    return {"schema_version": 1, "workflow_version": WORKFLOW_VERSION,
            "packages": PACKAGES, "input": payload}


def _evidence(payload: dict) -> dict:
    return {"input": payload, "synthetic": True,
            "text_bytes": len(payload["text"].encode("utf-8"))}


def _result(payload: dict) -> dict:
    return {"schema_version": 1, "workflow_version": WORKFLOW_VERSION,
            "input": payload, "evidence": _evidence(payload),
            "summary": REPORT_TEXT, "synthetic": True,
            "completed_mock_steps": {"evidence": 1, "report": 1},
            "model_calls": 0, "tool_calls": 0}


def _deny(*args, **kwargs):
    raise RecoveryError("external_action_forbidden")


@contextmanager
def _offline():
    """Guard known mock entrypoints, not a sandbox for untrusted Python/native code."""
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {
            "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING": "false",
            "LANGSMITH_TRACING_V2": "false", "LANGSMITH_OTEL_ENABLED": "false",
            "LANGSMITH_OTEL_ONLY": "false",
            "LANGCHAIN_TRACING_V2": "false", "LANGGRAPH_STRICT_MSGPACK": "true",
            "LANGSMITH_API_KEY": "", "LANGCHAIN_API_KEY": "",
            "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "",
        }))
        for target in ("socket.socket.connect", "socket.socket.connect_ex",
                       "socket.create_connection", "urllib.request.urlopen",
                       "http.client.HTTPConnection.connect", "subprocess.Popen",
                       "os.system", "os.posix_spawn", "os.posix_spawnp", "os.fork"):
            stack.enter_context(patch(target, side_effect=_deny))
        yield


def _dependencies():
    try:
        if any(importlib.metadata.version(name) != version for name, version in PACKAGES.items()):
            raise RecoveryError("dependency_version_incompatible")
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        from langgraph.checkpoint.sqlite import SqliteSaver
        from langgraph.func import entrypoint, task
        from langgraph.types import Command, interrupt
        from langsmith import tracing_context
    except (importlib.metadata.PackageNotFoundError, ImportError):
        raise RecoveryError("dependency_unavailable") from None
    return JsonPlusSerializer, SqliteSaver, entrypoint, task, Command, interrupt, tracing_context


def _graph(connection, counters, provider_calls, *, restoring=False):
    serializer, saver_type, entrypoint, task, command, interrupt, _ = _dependencies()
    saver = saver_type(connection, serde=serializer(pickle_fallback=False,
                                                    allowed_msgpack_modules=None))

    @task
    def evidence_task(payload: dict) -> dict:
        if restoring:
            raise RecoveryError("checkpoint_incomplete")
        counters["evidence"] = counters.get("evidence", 0) + 1
        return _evidence(payload)

    @task
    def report_task(evidence: dict) -> dict:
        provider = MockLLMProvider([ModelResponse(content=REPORT_TEXT, model="scripted-mock")])
        response = invoke(provider, ModelRequest(
            messages=[{"role": "user", "content": "Review the fixed synthetic evidence."}],
            max_input_tokens=256, max_output_tokens=64, timeout_seconds=1))
        provider_calls[0] += 1
        if response.content != REPORT_TEXT or response.tool_calls:
            raise RecoveryError("mock_provider_invalid")
        counters["report"] = counters.get("report", 0) + 1
        return _result(evidence["input"])

    @entrypoint(checkpointer=saver)
    def mock_workflow(payload: dict):
        payload = _input(payload)
        evidence = evidence_task(payload).result()
        if payload["scenario"] in ("pause", "unknown"):
            answer = interrupt({"kind": ("after_evidence" if payload["scenario"] == "pause"
                                         else "outcome_unknown"),
                                "binding": _binding(payload), "evidence": evidence})
            if payload["scenario"] != "pause" or answer != {"continue_mock": True}:
                raise RecoveryError("resume_incompatible")
        result = report_task(evidence).result()
        return entrypoint.final(value=result, save=result)

    # This CLI uses invoke/get_state; it has no eager streaming subscriber.
    mock_workflow.stream_eager = False
    return mock_workflow, command, saver


class MockRecovery:
    """One native checkpointer per new owned directory; no scheduler or journal."""

    def __init__(self, root: Path, *, counters: dict | None = None):
        self.root = Path(root).absolute()
        try:
            validate_identifier(self.root.name)
        except ContractError:
            raise RecoveryError("root_not_owned") from None
        if (self.root.parent != RUNS_ROOT or self.root != self.root.resolve() or
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", self.root.name)):
            raise RecoveryError("root_not_owned")
        self.database = self.root / "checkpoints.sqlite3"
        self.counters = {} if counters is None else counters
        self.provider_calls = [0]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def _files(self):
        if not self.root.is_dir() or self.root.is_symlink():
            raise RecoveryError("root_missing")
        total = 0
        for child in self.root.iterdir():
            info = child.lstat()
            name = child.name[2:] if child.name.startswith("._") else child.name
            if name not in FILES or not stat.S_ISREG(info.st_mode):
                raise RecoveryError("root_not_owned")
            total += info.st_size
        if total > MAX_BYTES:
            raise RecoveryError("storage_limit")

    @contextmanager
    def _owned(self, *, new=False):
        if new:
            if RUNS_ROOT != RUNS_ROOT.resolve():
                raise RecoveryError("root_not_owned")
            RUNS_ROOT.mkdir(parents=True, exist_ok=True)
            try:
                self.root.mkdir()
            except FileExistsError:
                raise RecoveryError("root_exists") from None
            with (self.root / "writer.lock").open("xb") as owner:
                owner.write(OWNER)
        self._files()
        lock_path = self.root / "writer.lock"
        if not lock_path.is_file() or lock_path.is_symlink():
            raise RecoveryError("ownership_missing")
        with lock_path.open("rb") as lock:
            if lock.read(len(OWNER) + 1) != OWNER:
                raise RecoveryError("ownership_incompatible")
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RecoveryError("run_busy") from None
            try:
                yield
                self._files()
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _preflight(self, connection):
        connection.execute("PRAGMA trusted_schema=OFF")
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise RecoveryError("checkpoint_invalid")
        if (connection.execute("PRAGMA user_version").fetchone() != (0,) or
                connection.execute("PRAGMA journal_mode").fetchone() != ("wal",)):
            raise RecoveryError("schema_incompatible")
        objects = connection.execute(
            "SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        if set(objects) != {(name, "table") for name in SCHEMA}:
            raise RecoveryError("schema_incompatible")
        for table, columns in SCHEMA.items():
            rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
            expected = tuple((index, name, "INTEGER" if name == "idx" else
                              "BLOB" if name in ("checkpoint", "metadata", "value") else
                              "TEXT", 1 if name in ("thread_id", "checkpoint_ns",
                              "checkpoint_id", "task_id", "idx", "channel") else 0,
                              "''" if name == "checkpoint_ns" else None, key)
                             for index, (name, key) in enumerate(zip(columns, SCHEMA_KEYS[table])))
            if tuple(rows) != expected:
                raise RecoveryError("schema_incompatible")
            identities = connection.execute(
                f"SELECT DISTINCT thread_id,checkpoint_ns FROM {table}").fetchall()
            if any(identity != (THREAD_ID, "") for identity in identities):
                raise RecoveryError("checkpoint_identity_incompatible")
        raw = connection.execute(
            "SELECT metadata FROM checkpoints ORDER BY checkpoint_id DESC LIMIT 1").fetchone()
        if raw is None or len(raw[0]) > 4096:
            raise RecoveryError("checkpoint_missing")
        metadata = json.loads(raw[0])
        binding = json.loads(metadata["mock_recovery"])
        if binding["workflow_version"] != WORKFLOW_VERSION:
            raise RecoveryError("checkpoint_version_incompatible")
        payload = _input(binding["input"])
        if _json(binding) != _json(_binding(payload)):
            raise RecoveryError("checkpoint_version_incompatible")
        return payload

    @contextmanager
    def _connection(self, *, write=False, new=False):
        if not new:
            info = self.database.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size < 100:
                raise RecoveryError("checkpoint_invalid")
            with self.database.open("rb") as header:
                if header.read(16) != b"SQLite format 3\x00":
                    raise RecoveryError("checkpoint_invalid")
        mode = "rwc" if new else ("rw" if write else "ro")
        with closing(sqlite3.connect(self.database.as_uri() + "?mode=" + mode,
                                    uri=True, timeout=1, check_same_thread=False)) as connection:
            connection.execute("PRAGMA trusted_schema=OFF")
            if write:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("PRAGMA max_page_count=128")
                connection.execute("PRAGMA wal_autocheckpoint=8")
            yield connection

    def _view(self, graph, saver, payload, *, cached):
        snapshot = graph.get_state(self._config(payload))
        if any(item.error is not None for item in snapshot.tasks):
            raise RecoveryError("checkpoint_incomplete")
        if not snapshot.next and not snapshot.interrupts:
            if _json(snapshot.values) != _json(_result(payload)):
                raise RecoveryError("state_incompatible")
            return {"status": "completed", "dispatch_permitted": False,
                    "cached_result": cached,
                    "completed_mock_steps": {"evidence": 1, "report": 1},
                    "result": snapshot.values}
        if snapshot.next != ("mock_workflow",) or len(snapshot.interrupts) != 1:
            raise RecoveryError("checkpoint_incomplete")
        kind = "after_evidence" if payload["scenario"] == "pause" else "outcome_unknown"
        expected = {"kind": kind, "binding": _binding(payload), "evidence": _evidence(payload)}
        if (payload["scenario"] == "normal" or
                _json(snapshot.interrupts[0].value) != _json(expected)):
            raise RecoveryError("state_incompatible")
        saved = saver.get_tuple(self._config(payload))
        returns = [value for _, channel, value in saved.pending_writes or []
                   if channel == "__return__"]
        if len(returns) != 1 or _json(returns[0]) != _json(_evidence(payload)):
            raise RecoveryError("checkpoint_incomplete")
        return {"status": "paused" if kind == "after_evidence" else "blocked",
                "dispatch_permitted": False, "cached_result": cached,
                "completed_mock_steps": {"evidence": 1, "report": 0}, "result": None,
                "interrupt": snapshot.interrupts[0].value,
                "explanation": UNKNOWN_EXPLANATION if kind == "outcome_unknown" else
                "Synthetic checkpoint paused after evidence; explicit mock resume is available."}

    @staticmethod
    def _config(payload):
        # The procedural entrypoint waits on its one nested task in the same pool.
        return {"configurable": {"thread_id": THREAD_ID}, "max_concurrency": 2,
                "recursion_limit": 8, "callbacks": [],
                "metadata": {"mock_recovery": _json(_binding(payload))}}

    def _run(self, operation, payload=None):
        try:
            if operation == "start":
                payload = _input(payload)
            with _offline():
                tracing_context = _dependencies()[-1]
                with tracing_context(enabled=False, parent=False), self._owned(new=operation == "start"):
                    if operation == "start":
                        with self._connection(write=True, new=True) as connection:
                            graph, _, saver = _graph(connection, self.counters, self.provider_calls)
                            graph.invoke(payload, self._config(payload), durability="sync")
                            return self._view(graph, saver, payload, cached=False)
                    with self._connection() as connection:
                        payload = self._preflight(connection)
                        graph, command, saver = _graph(connection, self.counters, self.provider_calls, restoring=True)
                        report = self._view(graph, saver, payload, cached=True)
                    if operation == "status" or report["status"] == "completed":
                        return report
                    if report["status"] == "blocked":
                        raise RecoveryError("outcome_unknown")
                    with self._connection(write=True) as connection:
                        self._preflight(connection)
                        graph, command, saver = _graph(connection, self.counters, self.provider_calls, restoring=True)
                        graph.invoke(command(resume={"continue_mock": True}),
                                     self._config(payload), durability="sync")
                        return self._view(graph, saver, payload, cached=False)
        except RecoveryError:
            raise
        except Exception:
            raise RecoveryError("checkpoint_invalid") from None

    def _operate(self, operation, payload=None):
        before = {key: self.counters.get(key, 0) for key in ("evidence", "report")}
        before_provider = self.provider_calls[0]
        report = self._run(operation, payload)
        report["executed_mock_steps_this_call"] = {
            key: self.counters.get(key, 0) - count for key, count in before.items()}
        report["mock_provider_calls_this_call"] = self.provider_calls[0] - before_provider
        return report

    def start(self, payload: dict) -> dict:
        return self._operate("start", payload)

    def status(self) -> dict:
        return self._operate("status")

    def resume(self) -> dict:
        return self._operate("resume")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "status", "resume"):
        command = commands.add_parser(name)
        command.add_argument("--root", type=Path, required=True)
        if name == "start":
            command.add_argument("--text", default="synthetic evidence")
            command.add_argument("--scenario", choices=("normal", "pause", "unknown"),
                                 default="normal")
    args = parser.parse_args(argv)
    run = None
    try:
        with MockRecovery(args.root) as run:
            report = (run.start(build_input(args.text, args.scenario)) if args.command == "start"
                      else getattr(run, args.command)())
    except RecoveryError as error:
        report = {"status": "rejected", "dispatch_permitted": False, "error": error.code,
                  "mock_provider_calls_this_call": run.provider_calls[0] if run is not None else 0}
        explanation = {
            "outcome_unknown": UNKNOWN_EXPLANATION,
            "dependency_unavailable": "Optional mock recovery dependencies are unavailable; no run was created or resumed.",
            "dependency_version_incompatible": "Pinned optional dependencies do not match; no run was created or resumed.",
            "checkpoint_version_incompatible": "Stored mock workflow version is incompatible; no migration or replay was attempted.",
        }.get(error.code)
        if explanation:
            report["explanation"] = explanation
    report.update(mock_only=True, production_resume=False, state_authority="langgraph_sqlite",
                  model_calls=0, tool_calls=0)
    encoded = _json(report)
    if len(encoded.encode("utf-8")) > 8192:
        report = {"status": "rejected", "dispatch_permitted": False,
                  "error": "output_limit", "mock_only": True,
                  "production_resume": False, "state_authority": "langgraph_sqlite",
                  "model_calls": 0, "tool_calls": 0,
                  "mock_provider_calls_this_call": report["mock_provider_calls_this_call"]}
        encoded = _json(report)
    print(encoded)
    return 0 if report["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
