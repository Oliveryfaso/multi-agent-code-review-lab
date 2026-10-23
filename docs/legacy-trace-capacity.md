# Legacy trace safe summary contract

New `TraceStore.save` writes use `schema: macr.trace.summary`, `version: 1` by
default. The encoder projects an explicit allowlist before copying or JSON
encoding. It never walks query, repository paths, board, source, requests, tool
raw data, arbitrary metrics, exception messages, model output, answer prose or
patch bodies. Unknown fields are discarded, rather than filtered by secret
keywords. No raw sidecar or automatic raw fallback is written.

## Preserved metadata

The summary contains task ID and canonical timestamp; call/state/evidence counts;
up to 64 tool, model and state rows; fixed status/role/tool enums; finite latency;
known token counters and `usage_kind`; answer/patch presence; diagnosis stage,
parsed/present flags and canonical attempt UUID; final-review flags and issue
counts; fixed omission flags and explicit row-truncation counts. Call/state IDs
are deterministic ordinals within this trace. Arbitrary model names, unknown
provider names, error text, review questions, source paths and raw citations are
omitted. Unknown enums become `unknown`, invalid timestamps/attempt UUIDs become
null, and unknown boolean values become null. Unknown token usage stays unknown;
it is not presented as measured zero or a zero cost.

Task IDs are the existing validated ASCII identifiers (1–128 characters).
Canonical IDs and timestamps remain metadata, not an arbitrary-secret detector.
Synthetic canary absence establishes the tested omission paths; it cannot prove
that every possible identifier, number or allowed enum contains no secret.

A summary example (excerpt; fields are operational metadata):

```json
{
  "schema": "macr.trace.summary",
  "version": 1,
  "task_id": "example-task",
  "counts": {"tool_calls": 1, "llm_calls": 1, "states": 1, "evidence": 1},
  "llm_calls": [{"id": "llm-1", "role": "diagnostic_candidate",
    "provider": "local_http", "model_present": true, "status": "completed",
    "usage_kind": "measured", "usage": {"input_tokens": 100, "output_tokens": 40},
    "latency_ms": 3, "finish_reason": "stop"}],
  "diagnosis": {"present": true, "status": "accepted", "candidate_present": true,
    "final_present": true, "candidate_parsed": true, "verification_parsed": true,
    "semantic_proof": false},
  "metrics": {"final_review": {"present": true, "ok": false,
    "human_review_required": true, "issue_count": 1, "question_count": 0}},
  "omission": {"free_text": true, "paths": true, "tool_data": true,
    "requests": true, "source": true, "board": true, "raw_model_output": true},
  "truncation": {"tool_calls_omitted": 0, "llm_calls_omitted": 0,
    "state_timeline_omitted": 0, "applied": false}
}
```

## Admission bounds and atomic writes

Projection reads only fixed fields and at most 64 rows per collection, without
unbounded `asdict`, `deepcopy`, raw traversal or JSON allocation first. Exact
built-in containers avoid arbitrary object hooks. Known numbers must be finite,
nonnegative and at most 2^63−1; usage counters must be integers. Invalid selected
numbers fail with a value-free `ValueError` before any write. Omitted raw values,
including cycles, nonfinite values and arbitrary objects, are never traversed.
Only retained rows are validated; discarded tails are counted, not inspected.

A second encoder boundary bounds total UTF-8 text and final escaped/formatted
JSON to 1 MiB, visited values/keys to 32,768, nesting to 64 and integer bit length
to 4,096. It accounts shared occurrences separately, rejects active cycles and
unsupported types, and checks actual encoded bytes. Projection's fixed layout,
row caps, 64-character enums, 128-character task ID and 40-character input time
limit bound allocation before this defensive copy/encoding boundary.

Safe bytes are generated once and used for both `<task_id>.json` and `latest.json`.
The existing 0600, flush/fsync/replace helper is unchanged. Retained failed-write
tmp files therefore contain only the same safe summary. Files are individually
atomic; they are not a pair transaction. A successful task write followed by
latest replacement failure leaves the new complete task beside the previous
complete latest, and the error propagates. No previous file is rewritten on an
encoding failure. Concurrent writes still have the existing per-file semantics.

## Compatibility and retention boundaries

`Trace.to_dict`, in-memory diagnostic continuation, provider/wire requests,
explicit CLI JSON/patch/report outputs and EvalRunner results are unchanged.
They can contain sensitive content and have their own output/ownership policy.
A summary intentionally cannot reconstruct diagnosis, source binding or raw
model output from disk; it is not a resume/recovery authority or semantic proof.
Tests now check business/source binding in memory and captured requests, while
independently checking actual persisted summary bytes.

Viewer detects summaries before reading evaluation reports or patch files. It shows
“Content omitted” and review metadata, preserving the review upload form and
current-job status. It never interprets missing prose as no issues/no patch and
does not seek a raw substitute. Unknown summary versions show an unsupported
message without legacy fallback. Historical full traces retain their old read
path. Typed RunStore, SQLite and their recovery contracts are unchanged.

The TraceStore legacy-read adapter initializes the existing reader's usage
counter before delegation, fixing its previously uninitialized-counter failure.
Both entry points return historical full records and new summaries only as
`legacy_unverified`, without granting verification or recovery authority.

Existing raw traces, failed tmp files and explicit business artifacts are not
scanned, migrated or deleted. This is a per-record boundary, not a process/RSS,
whole-directory or concurrency quota. TTL and aggregate-budget work is design
only: a future dry-run must inventory authorized roots, logical/allocated bytes,
owner/pinned/recovery references and failed tmp separately; propose candidates
and retained reasons; then obtain an explicit cleanup policy before deleting.
This change installs no scanner, timer, automatic cleanup or service.

## Offline verification

Use `scripts/run_offline_tests.py` with `test_trace_store`, `test_atomic_file`,
`test_diagnostic_orchestrator`, `test_diagnostic_pipeline_cli`,
`test_agent_evidence_integration`, `test_run_store`, `test_sqlite_run_store` and
`test_run_recovery`. Fixtures are synthetic and native dispatch is blocked.
Checks cover arbitrary non-keyword canaries in task/latest/failed tmp; raw
omission without copying; row caps; controlled nonfinite failure; encoded
escaping/exact 1 MiB/depth/width/cycle bounds; previous-file preservation including
second-replace and second-fsync failures; Viewer
summary and old-read behavior; and in-memory diagnostic/wire contracts. No model,
VM, listener or analysed target source is executed. Browser pixels are not part
of these rendering assertions.
