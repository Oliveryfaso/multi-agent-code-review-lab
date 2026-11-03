# Explicit mock-only recovery CLI

`agent-review mock-recovery` is an isolated synthetic demo. It is not production
investigation resume. The existing ask, patch and repair commands retain their
behavior. The CLI imports the optional recovery module only after
this explicit subcommand is selected; ordinary installation needs no LangGraph.

## Run from this checkout

Use a Python with the optional dependencies installed. The repository's CLI
wrapper exposes the same command without installing the project itself:

```sh
python -I -B cli/agent_review.py mock-recovery start --root "$PWD/artifacts/implementation/langgraph-mock-recovery-01/runs/demo-a" --scenario pause
python -I -B cli/agent_review.py mock-recovery status --root "$PWD/artifacts/implementation/langgraph-mock-recovery-01/runs/demo-a"
python -I -B cli/agent_review.py mock-recovery resume --root "$PWD/artifacts/implementation/langgraph-mock-recovery-01/runs/demo-a"
python -I -B cli/agent_review.py mock-recovery resume --root "$PWD/artifacts/implementation/langgraph-mock-recovery-01/runs/demo-a"
```

Run each line in a new process. `--root` must be a fresh direct child of that
fixed runs directory for start; reuse the same existing root for status/resume.
Use a different name for a second start. Canonical checkout paths are required.
Input text is at most 256 UTF-8 bytes. There is no provider, repository, backend,
model profile, tool, cloud destination, replacement resume input or retry option.
The original `scripts/langgraph_mock_recovery.py` is a thin compatibility entry
calling this same package implementation. Both checkout entries are supported;
arbitrary wheel installation directory inference has not been qualified.

| Scenario/call | Status | This call: evidence/report | This call: Mock Provider |
| --- | --- | --- | --- |
| normal start | completed | 1/1 | 1 |
| pause start | paused | 1/0 | 0 |
| paused status | paused | 0/0 | 0 |
| paused resume | completed | 0/1 | 1 |
| completed resume | completed, cached | 0/0 | 0 |
| unknown start/status | blocked | 1/0 or 0/0 | 0 |
| unknown resume | rejected, explanation supplied | no replay | 0 |

Every JSON report states `mock_only:true`, `production_resume:false`,
`state_authority:langgraph_sqlite`, `dispatch_permitted:false`, and zero real
`model_calls`/`tool_calls`. `mock_provider_calls_this_call` separately measures
actual calls to the internal scripted mock; a call followed by rejection is
still counted. `completed_mock_steps` is logical progress;
`executed_mock_steps_this_call` measures mock computation, excluding a cache-miss
guard refusing before computation. Exit 0 means completed; paused, blocked and
rejected outcomes use 2. Output is capped at 8 KiB.

## One execution state authority

LangGraph Functional API and its native SqliteSaver are the only authority for
next steps, saved task results, interrupts and final output. There is no second
scheduler, next-step file or RunStore journal. The report task always constructs
an internal `MockLLMProvider` with one fixed scripted response and uses the
existing Provider `invoke` validation contract. It never selects a real Provider
or dispatches returned tool calls. RunStore's canonical `encoded` helper and the
existing `validate_identifier` contract are reused; no RunStore/SQLiteRunStore
instance, production InvestigationState, reservation or budget write is created.

`normal` completes two sequential synthetic steps. `pause` persists an interrupt
after evidence; native task-result caching supplies that evidence during the
next process's entrypoint replay. Completed resume reads and verifies final
output without invoke. `unknown` only simulates an unknown outcome marker: no
external action occurs. It explains that automatic replay is disabled and refuses
resume before writable recovery/invoke. Real unknown effects need a separately
reviewed reconciliation integration; this command offers no force/retry switch.

Workflow `mock-v2` includes the Provider and shared codec integration. Existing
`mock-v1`, incompatible metadata/schema, truncated data or missing task returns
are refused without migration or repair. An incorrect native dynamic task ID
can pass the saved-value precheck; if replay then misses the cache, the evidence
guard refuses before computation. That native failure may itself be recorded.
No replacement native ID algorithm is implemented and no old data is migrated.

Each operation checks a fixed ownership label, a regular-file nonblocking writer
lock, SQLite header, integrity, exact pinned schema and metadata. Read inspection
uses an existing read-only connection. Owned files are checked against a 2 MiB
aggregate logical-size limit; database page growth is limited. The synchronous
native pool is bounded to two workers with unused `stream_eager` disabled;
evidence and report remain sequential. This is a single-machine, single-operation
mock demo, not a production worker watchdog, backup system or concurrency service.

## Optional dependencies and telemetry

Default project dependencies remain empty. Extra `mock-recovery` fixes
`langgraph==1.2.14`, `langgraph-checkpoint-sqlite==3.1.1` and
`langgraph-checkpoint==4.2.0`. For Python 3.12 on macOS arm64,
[the complete 41-package hash lock](../requirements/mock-recovery-py312-macos-arm64.lock)
fixes all transitive wheels. In a fresh chosen virtual environment, its install
command is:

```sh
python -m pip install --require-hashes --only-binary=:all: -r requirements/mock-recovery-py312-macos-arm64.lock
```

Create a separate virtual environment for these optional dependencies; do not
install into a global or existing user environment. Use official PyPI without
additional indexes or local find-links, and retain the lock unchanged. Do not
substitute versions if a pinned wheel or digest cannot be obtained. No dependency
installation occurs when this CLI is invoked. Missing optional packages produce
`dependency_unavailable`; boundary version drift produces
`dependency_version_incompatible`. Both are checked before root/lock creation.
The three top-level boundary pins alone do not qualify other transitive/platform
resolutions. The lock also includes SDK, HTTP and native-wheel dependencies; it
is not a minimal three-wheel installation. Review upstream license notices
before redistributing dependencies.

During optional import, invoke and inspection the entry forces tracing and OTEL
switches off, blanks relevant API keys, enters `tracing_context(enabled=False,
parent=False)`, disables callbacks, and rejects known stdlib socket/HTTP/urllib,
subprocess and fork/spawn paths. It creates no telemetry Client or account. These
are guards for known mock functions, not a sandbox for malicious Python/native
code. LangSmith has a pytest auto-plugin; validation uses unittest rather than
loading it. Environment changes are scoped to the operation and then restored.

## Validation and remaining boundary

The 37 explicitly selected recovery checks passed with zero skips on CPython
3.12 macOS arm64 using the complete 41-package hash lock. These validate the
trusted synthetic workflow and native checkpointer integration, not real
Provider/tool effects or production recovery.

The optional core and CLI tests are selected explicitly under the existing
`run_offline_tests.run_suite` fixture guard; they are not added to the ordinary
allowlist. Limits remain 120 seconds, 64 MiB allocated fixtures, 2 MiB records.
Successful owned fixture tmp is cleaned; failures are retained. Run the 37
optional tests from this checkout using the chosen environment:

```sh
python -I -B - <<'PYTEST'
from pathlib import Path
import json
import sys
import unittest
root = Path.cwd()
sys.path[:0] = [str(root / 'scripts'), str(root / 'tests'), str(root / 'src')]
import run_offline_tests as runner
loader = unittest.TestLoader()
suite = loader.loadTestsFromNames([
    'test_langgraph_mock_recovery', 'test_mock_recovery_cli'])
if loader.errors:
    raise SystemExit('optional test loading failed')
summary = runner.run_suite(suite)
print(json.dumps(summary))
raise SystemExit(0 if summary['status'] == 'passed' else 1)
PYTEST
```

This command rejects skipped, incomplete or failed selections; the test count
is a software regression count, not model tasks or demonstrated repair gain.
A main-thread fixture alarm alone does not prove native executor workers exited.
The tests guard known trusted synthetic operations; do not use them to run
untrusted target code.

Production resume, real Provider/tool effects, model/VM execution and old-data
migration remain unsupported. Existing production budget, identity, snapshot,
evidence and grading contracts remain intact. Framework caching does not
guarantee exactly-once external effects. Passing the synthetic suite would not
establish production recovery, runtime isolation or model superiority.
