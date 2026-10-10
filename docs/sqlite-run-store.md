# Optional SQLite investigation storage

`SQLiteRunStore` uses Python's standard-library `sqlite3` module to commit an investigation event, its resulting state, and its sequence together. It is an explicit choice for a new storage root. The existing `RunStore` JSON backend remains the default.

This adapter reuses the current investigation contracts and the existing `CodeReviewWorkflow.read_investigation(store, run_id)` boundary. It does not turn the main review workflow into an automatically resumable graph runner. `WorkflowCheckpoint` describes workflow progress; it does not implement node replay or tool reconciliation.

## Why this reuse point

The JSON backend manages journal appends, flushes, checkpoint replacement, tail parsing, and alignment between the journal and checkpoint. SQLite supplies transaction and recovery machinery for this boundary while the project retains its domain validation.

| Option | Reuse | Integration cost and tradeoff |
| --- | --- | --- |
| Existing JSON `RunStore` | Current readable journal and checkpoint format | Remains the default; preserves existing storage and inspection tools. |
| Standard-library `SQLiteRunStore` | Database transactions for event, state, and sequence | No added dependency or service; opt in through the existing store interface for a new root. Database inspection requires the adapter or SQLite tooling. |
| LangGraph SQLite checkpointer | Checkpoint persistence integrated with LangGraph | Adds packages and requires graph state, channel, and execution integration. It does not directly replace this project's event, action, and budget contracts. Deferred until graph execution itself needs that integration. |

Python documents explicit transaction control through `isolation_level=None`, which works on the project's Python 3.10 minimum. The adapter uses explicit `BEGIN`, `COMMIT`, and `ROLLBACK`; it does not depend on the newer Python `autocommit` argument. See [Python 3.10 transaction control](https://docs.python.org/3.10/library/sqlite3.html#transaction-control), [SQLite transactions](https://www.sqlite.org/lang_transaction.html), and [LangGraph checkpoint libraries](https://docs.langchain.com/oss/python/langgraph/checkpointers).

## Usage

The host must supply a valid `InvestigationState` and its matching `SnapshotStore`, as it does for `RunStore`. The snapshot and all referenced profiles still undergo the existing validation. Choose a fresh root under this checkout's `artifacts/` directory:

```python
from pathlib import Path

from macr.memory.sqlite_run_store import SQLiteRunStore
from macr.workflow import CodeReviewWorkflow


def store_and_read(state, snapshots):
    store = SQLiteRunStore(
        Path("artifacts/sqlite-runs-example"), snapshots=snapshots
    )
    try:
        checkpoint = store.checkpoint(state)
        recovered = CodeReviewWorkflow().read_investigation(
            store, state.task.run_id
        )
        return checkpoint, recovered
    finally:
        store.close()
```

The adapter also supports `append(event)`, `load(run_id)`, and `read_events(run_id)`. Event sequences start at one and must advance by exactly one. `CheckpointRef.path` points to `runs.sqlite3`; consumers must treat it as an opaque storage reference, rather than opening it as a JSON checkpoint.

There is no CLI backend flag or automatic migration. JSON and SQLite roots reject one another. Existing JSON histories remain in their original format, and this change does not read or rewrite them during a SQLite run.

## Contracts and failure behavior

- The original snapshot, scope, profile, evidence, queue, message, action, and budget validation remains in `RunStore`. Both backends use the same event application and checkpoint forward checks.
- The existing root lock admits one store writer. The instance mutex serializes caller threads. SQLite uses `BEGIN IMMEDIATE` for writes and a single read transaction for a coherent state and event snapshot. Database contention fails immediately; there is no automatic retry.
- Each event insert and state/sequence update commit in one transaction. The event primary key and sequence checks reject duplicate or conflicting events. If a commit succeeds but its acknowledgement is lost, reopening reveals the consumed sequence; blindly appending it again fails.
- Completed action accounting is retained once. In-flight or unknown actions remain visible in `ResumeResult.uncertain_actions`, with their reservations retained. Loading storage never invokes tools or models, redispatches actions, or resets the budget.
- Storage failures and interruption block further writes on that instance. Reopening performs validation before use. Diagnostic reads on a failed but open instance are allowed and still validated; closing releases the connection and root lock.
- The database file must remain a regular file at its bound device/inode identity. Missing, replaced, or symbolically linked database paths are rejected. Schema version one must match the expected two tables exactly; incompatible schemas are preserved and rejected without automatic upgrades.

The adapter provides atomic storage updates, not exactly-once external tool effects. A future execution runner still needs an explicit policy for unknown outcomes and remaining wall-time budgets.

## Local resource limits

SQLite runs in the caller process, without a database server or resident agent service. The configured main database limit is 64 MiB, with an approximately 1 MiB page-cache target, rollback journal mode `DELETE`, and synchronization mode `FULL`. Schema/index space counts toward the main database limit. A database-full failure rolls back the transaction and preserves the previous committed state.

The main database limit is not a hard combined memory or disk cap: the rollback journal and SQLite bookkeeping can add transient space, and the cache setting is a target. Journal files are checked at operation boundaries; this is not a peak-space monitor. `FULL` also depends on the filesystem and device honoring synchronization. The tests exercise rollback and lost acknowledgements, not power-loss durability. See [SQLite pragmas](https://www.sqlite.org/pragma.html), [atomic commit](https://www.sqlite.org/atomiccommit.html), and [isolation](https://www.sqlite.org/isolation.html).

## Validation

Run the new storage tests separately from the legacy recovery tests so each fixture batch retains its existing 120-second, 64-MiB allocated-file, and 2-MiB record limits:

```sh
python3 scripts/run_offline_tests.py test_sqlite_run_store
python3 scripts/run_offline_tests.py test_trace_store test_run_store test_run_recovery
```

Use a supported Python 3.10 or newer interpreter. The 17 new tests cover rollback, lost commit acknowledgement, interruption, duplicate accounting, unknown actions, contention, thread serialization, coherent reads, database-full rollback, corruption, schema and path identity, profile/snapshot drift, and backend separation.

Local validation passed all 17 on Python 3.10 and 3.12. A clean export of the public core, overlaid with only the four implementation/test files, passed the original 88 offline tests plus these 17 on Python 3.11; 70 modules imported and CLI help exited successfully. These checks use synthetic fixtures and start no model or VM. They verify the storage adapter, not arbitrary-point continuation of a live review workflow.
