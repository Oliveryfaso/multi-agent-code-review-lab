# Local static source evidence

`agent-review evidence-locate` and `collect_evidence` read captured source bytes and return a versioned report. They do not import or execute target code, call models, evaluate guards, propagate values or diagnose a root cause. Every report declares `diagnosis_status=not_performed`, `cause=null`, `model_calls=0`, `tokens=null` and `data_flow_proven=false`. A completed reading task is not a healthy-repository or defect verdict.

The installed entry point remains `agent-review`. Existing command parsers, defaults and handlers are preserved. Product imports needed by a legacy handler occur when that command runs. Accidental module-level provider/agent re-exports in `macr.cli` are no longer loaded on import; `_provider`, `_print_trace` and `main` remain available. Legacy commands may still require their existing separately authorized runtime or history assets; these are not included in the wheel.

## Install and inspect

Use the delivered wheel in a new local environment, from any directory:

```sh
python3.12 -m venv /local/tools/macr-env
/local/tools/macr-env/bin/python -m pip --isolated install --no-index --no-deps --no-cache-dir --disable-pip-version-check /local/releases/multi_agent_code_review_lab-0.1.0-py3-none-any.whl
/local/tools/macr-env/bin/agent-review --version
/local/tools/macr-env/bin/agent-review evidence-locate --help
```

The `/local/...` paths are placeholders; substitute your own paths. Runtime dependencies are empty. Version is fixed at 0.1.0; build backend is pinned to setuptools 72.1.0 and must already be available offline. No automatic download or global install is needed. The packaged local schema is `macr/investigation/static_evidence.schema.json`, and the license is MIT.

A fixed installed build of the shared implementation passed 24 sequential CLI/API acceptance slots on macOS ARM64 with CPython 3.12.13, with real pressure/RSS observations and process closure records. The curated public CLI is checked separately using bounded pure fixtures; those checks do not establish another native acceptance of this exact public tree. Linux native validation remains pending; Windows is unsupported by the required POSIX reader/monitor primitives. A pure-Python wheel tag is not an operating-system safety claim. Parsing follows the actual reported interpreter grammar; the package metadata's Python>=3.10 compatibility floor is not a verified runtime support matrix.

## Read a task

An example using this project's allowed source (choose a genuinely new output directory):

```sh
agent-review evidence-locate \
  --repo ./my-project \
  --question 'Show the source and syntactic parameter mapping for the CLI helper.' \
  --entry src/macr/cli.py:_provider \
  --out ./artifacts/my-new-reading --json
```

This example is a usage instruction, not a claimed successful real-worker run. The separately authorized second real-project acceptance has a [fixed plan and dated result](external-project-validation.md). There is no automatic batch or fallback service.

A generic example with local UTF8 question and already-reported traceback files:

```sh
agent-review evidence-locate --repo /local/project \
  --question-file /local/inputs/question.txt \
  --entry src/api.py:Service.handle --symbol helper \
  --traceback-file /local/inputs/trace.txt \
  --profile larger --budget-file /local/inputs/budget.json \
  --limit max_entries=12 --limit max_body_bytes=524288 \
  --limit active_seconds=300.5 --out /local/results/new-run --json
```

| Parameter | Meaning |
| --- | --- |
| `--repo` | Required explicit source directory. Relative paths use the caller's current directory. |
| `--out` | Required new output directory, outside the target and not its ancestor; safe existing parent required. |
| `--question` / `--question-file` | Exactly one required. A nonblank UTF8 reading task, bounded by `max_question_bytes`. |
| `--entry` | Repeatable relative `file`, `file:qualified_symbol`, `file:line` or `file:start-end`. Lines are 1-based and positive; range end must be >= start. |
| `--symbol` | Repeatable qualified symbol. Cross-file homonyms and duplicate declarations remain ambiguous. |
| `--traceback-file` | Optional explicit UTF8 already-reported trace, bounded by `max_traceback_bytes` and `max_traceback_frames`. At least an entry, symbol or nonblank trace is required. |
| `--profile` | `standard` (default) or `larger`; profile version 1. |
| `--budget-file` | Strict local UTF8 JSON object of profile overrides; unknown fields, name/version changes, duplicate keys, booleans used as numbers and nonfinite/deep values rejected. |
| `--limit FIELD=VALUE` | Any numeric profile field; positive finite integer, except seconds may be fractional. Repeatable; last explicit value wins. No permanent upper cap. |
| `--json` | Exactly one report JSON envelope when representable within the effective cap. |
| `--help`, `--version` | Show static help/version without target read, worker, optional providers or services. |

Auxiliary input paths must be explicitly named, regular, non-symlink files with safe non-symlink ancestors and no protected path components. No pipe, special file or protected target subtree is opened. Inputs are validated before source reads, output creation or worker dispatch. Auxiliary files use strict UTF8, unlike Python source's detected encoding. They are bounded individually but their validation IO precedes the supervisor's monitored analysis budget; this is not a hard kernel IO timeout. Missing/invalid auxiliary input is exit 2; a symlink/special/protected boundary is exit 3.

Precedence is **named defaults → budget file → explicit limits**. Explicit `max_input_bytes` / `max_input_depth` bootstrap budget-file decoding: a file cannot enlarge the cap for its own already-performed read. A file may increase later question/trace/source budgets. Increasing a budget never grants a protected path or target execution. `exclude_patterns` is a budget-file list of nonempty strings, not a numeric `--limit`. Unknown numeric fields are input errors. All effective values and exceeded field/stage/effective/observed values appear in the report.

Application exclusions are not Gitignore. Matching uses case-sensitive `fnmatchcase` on relative paths or their components; `*` can cross `/`. No Git negation, nested Gitignore or executable configuration is read. Built-in secret/private/metadata exclusions remain enforced regardless of profile.

## API and report

```python
from macr.investigation.evidence_profiles import EvidenceProfile
from macr.investigation.evidence_records import EvidenceRequest
from macr.investigation.evidence_supervisor import collect_evidence

request = EvidenceRequest(
    repo='/local/project', out='/local/results/new-run',
    question='Show this declaration and its direct parameter expressions.',
    entries=['src/api.py:handle'],
    profile=EvidenceProfile.select('larger', {'max_entries': 12}),
)
report = collect_evidence(request)  # trusted single worker, no target execution
# For cooperative cancellation: collect_evidence(request, cancelled=event.is_set).
```

The public API is supervisor-only; there is no public synchronous prepare bypass or monitor override. At intake it validates and clones the request, binding repo/out to lexical absolute caller-rooted paths before dispatch to the fixed worker cwd. It revalidates actual input bytes after expansion, does not mutate the caller's request and does not resolve symlinks to bypass policy. Returned request roots are canonical absolute paths. The worker uses absolute installed product/interpreter paths, `-I -S -B`, non-target cwd and a controlled package path. This isolates imports and parser failure; it is not an OS security sandbox.

JSON has `kind=static_evidence_locate`, `schema_version=1`, status, request, run, source_version, selection, evidence, observations, coverage, gaps, errors, usage, limits and capabilities. The local schema checks structural fields; typed validation additionally checks byte associations, spans, completeness, budgets, depth and lifecycle. It uses only local `$ref` definitions, never a target schema or network resolver. Read the schema with `importlib.resources.files('macr.investigation').joinpath('static_evidence.schema.json').read_text()`.

### Worker timing metadata v2

`run.closure.timing.version=2` makes the parent records explicit:

| Closure field | Meaning |
| --- | --- |
| `worker_spawn_call_before_monotonic` | Parent record immediately before the worker Popen call. |
| `worker_started_monotonic` | Existing parent record after Popen returns; it is not the worker's exact OS creation or business-entry time. |
| `worker_closed_monotonic` | Existing parent record after confirmed worker wait; unknown wait leaves it unavailable. |

`timing` also records the unchanged analysis-budget origin, clock API/implementation/unit/runtime/platform metadata, and the supervisor PID/canonical output invocation. Worker business start remains `not_observed`; OS creation is described only as bounded by the Popen call. Clock metadata can be null when unavailable without bypassing the existing resource guard. A pre-spawn failure has no spawn-call lower bound; a failed Popen can retain that attempt bound without claiming a worker started.

A separately captured live identity may precede the parent's after-return record. A consumer combining those observations must independently establish the same supported clock/runtime and invocation, then use the exact pre-Popen through confirmed-wait envelope. The metadata alone proves no live identity, continuous OS lifetime, signal authority or performance. Legacy reports retain their original interpretation; absent v2 fields cannot be backfilled from the after-return record.

Budgets, launch flags, resource guards and cleanup remain unchanged. The extra clock metadata and pre-call read consume the existing budgets and report cap. Ten synthetic supervisor regressions run in the bounded static-evidence test entry with fake processes, storage, clocks and native metrics; they do not launch a worker or execute a target. A separate short candidate guard run captured current OS identity and confirmed TERM/wait, but its overall acceptance remains **evidence incomplete** because the independent actual entry-exit time bound and CPU measurement were not captured. That result does not upgrade historical outcomes or qualify the complete public tree, native overhead or the platform matrix.

Explicit entries precede deepest reported trace frames. A line selects the innermost containing symbol; missing/ambiguous matches remain explicit with bounded candidates and omitted counts. Tracebacks do not prove execution or current-version agreement; external paths are not read. Parsed Python, text-only, unparsed, excluded and unreadable coverage are distinct; indexed text is not AST coverage. Functions, async functions, classes, methods and nested symbols can supply source. Existing facts/bindings support limited ordinary-function one-hop syntax only: no guard truth, default evaluation, star expansion, decorator behavior, rebinding proof, recursion or return-value propagation. Unsupported syntax is a tool limitation and does not itself imply a defect.

With `run.selection_version=exact-qualified-physical-span-v2`, reported frames retain input order while selection processes them deepest first. A frame is `valid` after its file, physical line and available reported-name checks pass and its source selection is admitted or reused. Explicit entries and different physical lines in the same declaration share one source row/excerpt without making the first matching frame a duplicate. Only a separately validated repeat of the normalized `(file, physical line, reported name)` tuple is `duplicate`; the deepest admitted occurrence is its valid representative. A blank reported name is distinct from a supplied name. Neither status proves execution, recursion or source completeness.

An entry-limited target is not admitted: repeated denied frames stay `limited`. Missing/out-of-range/ambiguous resolution produces terminal `invalid` frames with its detailed unresolved/ambiguity gaps; name disagreement remains `mismatch` even when the source was already selected. Body or reference shortages can leave admitted frames valid/duplicate while evidence stays truncated or absent and required gaps keep the report partial. Inspect requested/actual spans, complete flags, evidence IDs and limits. Source coverage counts unique resolved evidence units rather than frame occurrences. Historical v1 reports retain their original version and interpretation; `stable_result` preserves the selection version and frame semantics, so consumers must include that version in selection-result cache keys.

Captured references bind a single content_id to saved raw bytes, exact file/span, encoding, actual/requested spans and `complete_range` / `complete_symbol`. Long declarations or truncated final lines are explicitly partial; incomplete collector input does not manufacture facts. Physical lines recognize LF/CRLF/CR, retain terminators and do not turn Unicode separators into source lines. UTF8 AST columns describe decoded-source UTF8 byte positions, not original nonUTF8 file byte offsets. BOM/cookie conflicts, unknown codecs, strict decode errors and unsupported coordinate encodings are reported. Bad or unsupported grammar is `parse_unavailable`, not a syntax-defect diagnosis.

The snapshot is a captured byte set, not an atomic Git transaction, author proof or runtime proof. Inventory drift and observed identity changes reject; change-and-restore and later external tampering cannot be ruled out. `stable_result(report)` removes named volatile run/path/time measurements while preserving semantic errors, limitations, profile and source results; entire JSON byte identity is not promised.

## Output, exits and retention

Default terminal output is a short control-escaped status/diagnosis/publication summary, plus the receipt path when committed. JSON preserves raw captured text through JSON escaping; Markdown escapes controls/HTML and uses safe code fences. Stderr contains only sanitized stage/code/action diagnostics, without raw question, source, traceback, path or exception text. Worker logs contain sanitized stage/status only. Reports and snapshots may contain selected source and user input; treat the entire output directory as sensitive. No data is transmitted.

| Exit | Meaning | Output/publication |
| --- | --- | --- |
| 0 | Requested main evidence reading completed | Valid source result; limitations still visible. |
| 1 | Partial main evidence (missing/ambiguous/truncated/budget-stopped) | Verified subset or explicit gaps; never forged refs. |
| 2 | Input/contract error | Rejected JSON if representable; no worker/source/output mutation on CLI validation failures. |
| 3 | Scope/source trust/environment boundary | Rejected/failed report, no new valid source commit. Includes protected paths, source binding/drift or unavailable monitoring. |
| 4 | Internal/storage/worker failure or unknown lifecycle, output unavailable/unrepresentable | No claimed successful lifecycle; inspect report/run/receipt. |
| 130 | Cancellation | Precommit cancellation has no COMMITTED; postcommit output interruption retains authoritative committed artifacts. |

Invalid argparse syntax prints usage and a fixed diagnostic on stderr with stdout empty. Parsed input returns one typed report JSON when it fits. JSON uses the standard renderer; if only pretty formatting exceeds the cap, the same typed report may be compacted within the same requested cap. If no report JSON fits (including a known `max_report_bytes=1` request), stdout is empty and stderr reports `output_limit_unrepresentable`, exit 4. The CLI does not invent one-byte JSON, enlarge a budget, drop guards or claim an oversized fallback artifact. API request=null failure outcomes can retain a known effective cap; that is an in-memory outcome, not permission to persist larger JSON. Unavailable profile detail fails closed. Terminal summaries likewise honor `max_markdown_bytes`.

One worker starts, with zero automatic retries. Closure unknown takes exit 4 even if cancellation was requested. After confirmed worker exit, the parent verifies bounded report/log/manifest identities and writes `COMMITTED` last. Its receipt binds final document hashes/sizes, source manifest/content_id and confirmed worker exit. Read `pending/report.json`, `pending/report.md` and `pending/worker.log` only with a valid receipt; a pending directory alone is not a finished result. A returned report adds final lifecycle and cumulative usage after the saved worker report. Successful partial results may be committed. If cancellation occurs after receipt replacement or while CLI output is interrupted, the valid receipt/source outcome remains authoritative and is not rewritten as source-less/uncommitted. Output failure does not undo an existing receipt.

Consumers should check COMMITTED's document SHA256/size and source manifest association against the actual saved bytes before using references; these hashes are association checks, not signatures or proof against later tampering. Allocation/free-space and actual repeated IO are counted; saved report usage is at worker render, envelope usage at final worker verification, receipt usage before receipt IO, returned usage includes commit IO. ExFAT directory sync/privacy guarantees may differ from POSIX and must not be assumed. A monotonic active budget covers monitored supervisor work; cleanup has a separate target, not a hard guarantee against kernel-uninterruptible IO or device failure. Sampled RSS/pressure is not instantaneous peak enforcement.

Keep failed owned output for diagnosis. Retention is manual: after inspection, delete only the directory you explicitly created for this run. The tool never removes old or other people's outputs. Existing output is rejected; unknown worker closure cannot authorize another automatic run.

## Profile reference

All fields below are configurable positive finite budgets. Raising counts/AST/source/IO/RSS/storage caps can increase CPU, memory, disk, repeat verification and latency. Raising duration or cleanup targets increases time spent waiting; reducing free-space reserve increases storage risk. AST accounting follows parsing and cannot prevent parser OOM; one trusted worker bounds the crash domain. Semantic one-hop limitations do not disappear when budgets rise.

| Field | Standard | Larger |
| --- | ---: | ---: |
| `name` | `standard` | `larger` |
| `version` | `1` | `1` |
| `exclude_patterns` | `[]` | `[]` |
| `max_python_files` | `2000` | `4000` |
| `max_text_files` | `2000` | `4000` |
| `max_python_lines` | `100000` | `200000` |
| `max_text_bytes` | `67108864` | `134217728` |
| `max_file_bytes` | `524288` | `524288` |
| `max_metadata_entries` | `20000` | `20000` |
| `max_metadata_bytes` | `4194304` | `4194304` |
| `max_entries` | `6` | `6` |
| `max_functions` | `6` | `6` |
| `max_body_bytes` | `65536` | `262144` |
| `max_collector_bytes` | `32768` | `32768` |
| `max_facts` | `32` | `32` |
| `max_bindings` | `256` | `256` |
| `max_refs` | `256` | `256` |
| `max_candidates` | `50` | `50` |
| `max_ast_nodes_per_file` | `100000` | `100000` |
| `max_ast_depth` | `512` | `512` |
| `max_ast_nodes` | `1000000` | `1000000` |
| `max_symbols` | `100000` | `100000` |
| `max_relations` | `200000` | `200000` |
| `max_io_bytes` | `1073741824` | `2147483648` |
| `active_seconds` | `120.0` | `240.0` |
| `cleanup_seconds` | `5.0` | `5.0` |
| `max_report_bytes` | `4194304` | `4194304` |
| `max_markdown_bytes` | `4194304` | `4194304` |
| `max_log_bytes` | `1048576` | `1048576` |
| `max_scope_manifest_bytes` | `4194304` | `4194304` |
| `max_index_cache_bytes` | `33554432` | `33554432` |
| `max_allocated_bytes` | `536870912` | `1073741824` |
| `min_free_bytes` | `1073741824` | `1073741824` |
| `max_worker_rss_bytes` | `1073741824` | `2147483648` |
| `max_control_bytes` | `16384` | `16384` |
| `max_stderr_bytes` | `65536` | `65536` |
| `max_question_bytes` | `8192` | `8192` |
| `max_traceback_bytes` | `65536` | `65536` |
| `max_traceback_frames` | `64` | `64` |
| `max_input_bytes` | `131072` | `131072` |
| `max_input_depth` | `32` | `32` |

`*_bytes` are bytes; `*_seconds` are monotonic seconds; remaining numeric fields are counts/depth/lines. Metadata bytes are an estimate (UTF8 name/path bytes + 128 per entry), not measured kernel heap.

The future Debugger roadmap includes authorized dynamic reproduction, candidate verification and stronger evaluation. A frontend is a future item; no UI, model-backed diagnosis or repair is delivered by this static command.

## Independent entry completion evidence

`macr.investigation.entry_completion.observe_owned_child` can observe an already
owned direct child with PID-specific `wait4(WNOHANG)`. The caller must be its sole
waiter: do not also call `Popen.poll`, `Popen.wait` or `communicate` on that PID.
The helper never launches or signals a process. On an unreaped incomplete return,
the caller still owns cleanup under its original deadline and resource guards.

An optional `on_poll(pid)` callback runs only after a zero nonblocking wait result,
so an external recorder can enforce its existing live resource guards. It never
queries through that callback after a successful reaping wait. Callback errors,
latency, cancellation and deadline tightening count against the same budget;
the callback grants no launch or cleanup authority.

The first clock read after wait returns is a monotonic **upper bound** on exit,
not an actual exit instant. Kernel user/system CPU and raw platform-unit maximum
RSS are recorded with explicit wait4 accounting scope; these are not per-worker
CPU measurements or a measured simultaneous process-tree RSS peak. Missing,
denied, invalid or late observations remain `evidence_incomplete`; missing CPU
is never replaced with zero. A tightening deadline callback cannot extend the
original bound. Wait errors stop collection without an alternate query or retry.

`qualify_completion` checks expected PID, invocation, clock metadata, successful
exit and the supplied original finalization deadline without a timing tolerance.
This structural check does not authenticate a record. Future native use still
needs a frozen source/interpreter/launch binding, approved outer recorder and the
original pressure, resource, closure and persistence checks. The observer has
only fake wait/clock test coverage so far; historical incomplete batches remain
incomplete. It is not connected to product execution by this change.

## Failure publication and live storage scans

Rejected, failed and cancelled preparation outcomes remain uncommitted: the worker sends bounded sanitized failure control with original machine errors and exceeded limits, and the parent independently allows only completed/partial statuses to enter publication. Matching envelope status alone does not authorize COMMITTED. A valid source-less partial remains eligible only after all other verification/resource/lifecycle checks pass. `stale_snapshot` and `case_collision` are source-trust exit 3; unknown started-worker closure retains exit 4 precedence, and genuine storage faults remain exit 4.

Storage scans are strict by default, including worker final storage, parent post-exit verification, publication and legacy callers. Only the parent scan while its owned worker is running opts into the live mode: disappearance of a canonical owned UUID4 atomic-temp name may trigger a regular no-follow replacement proof and one complete strict rescan. Every attempted entry and proof lookup is charged to the same remaining metadata/deadline/cancellation budgets; physical allocation is observed per attempt, not added twice across passes. Non-temp disappearance, absent/nonregular replacements, links, other storage errors or repeated disappearance still fail closed. One rescan does not promise success under persistent namespace churn. These sampled scans are neither an atomic process-wide filesystem snapshot nor an instantaneous allocation hard bound.

## Canonical symbol associations for partial excerpts

Every emitted excerpt that carries a symbol_id includes its canonical captured-AST declaration metadata in observations.symbols. This association does not prove the complete body was displayed: partial excerpts retain complete_symbol=false. Required main-entry body/ref gaps keep partial status; optional one-hop gaps remain limitations. Derived facts, bindings and relations still require complete source evidence; metadata alone does not authorize them.
