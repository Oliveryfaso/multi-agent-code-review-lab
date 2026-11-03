# Normal repair entry — default disabled

The repository entry `python3 -I -B cli/agent_review.py repair` uses the shared
source-bound repair loop. The declared installed console entry is `agent-review`. It is an explicit command; existing
`ask`, `patch`, static evidence and native admission defaults are unchanged.
Repair parser registration is lazy and does not load optional recovery frameworks.
The checkout entry is tested here; building/installing a console-script artifact
is outside this offline integration.

From this Git checkout (no temporary PYTHONPATH or project installation needed):

```sh
python3 -I -B cli/agent_review.py repair \
  --repo examples/repair-preview-development/baseline \
  --task examples/repair-entry-development/task.json \
  --model-config examples/repair-entry-development/model.json \
  --strategy api_debugger --out /absolute/new/private/record
```

The supplied development task reuses the public invoice fixture. It is not a new
held-out pack. The default Debugger result is `verification_unavailable`, exit 2,
zero provider requests. `--strategy direct_api` instead reports
`provider_disabled`, also zero requests. Neither status is a successful repair.

## Actual connection and its boundaries

The CLI calls `run_repair_loop`, which shares the scripted path's request construction, strict
proposal parser, exact-once edits, snapshots, feedback, intents and local reload
checks. `RepairProviderBinding` applies the same model configuration and
`ModelBudget` for either strategy, then reuses `DeepSeekProvider.converse` to
encode/parse the actual provider conversation contract. Tests inject fake
responses or intercept the official transport's HTTP opener and inspect the
generated wire payload; they do not
replace the repair coordinator or proposal parser.

The default construction has no transport or credential access. Official HTTP
is a separate explicit mode described below; enabling its configuration does
not prove that an HTTP request occurred or authenticate the responding model.
Injected transports remain a trusted test seam. `real_model_calls`/`paid_requests`
remain unknown for unattested dispatched responses. All outcomes still report
`repaired=false` and no target execution or execution authority.

The shared configuration includes model/official endpoint, thinking settings,
fingerprint policy, cumulative request/input/output limits and a caller-frozen
price schedule. Before each call the binding reserves the configured complete
input envelope plus output ceiling, then settles only normalized usage or
independent wire-bound failure evidence. Unknown cost retains its reserve and
stops; identity mismatch, budget exhaustion and interrupted dispatch stop without
retry. Caller estimates are engineering controls, not a current-price assertion,
supplier invoice, hard spending guarantee or trusted tokenizer bound. No source
is truncated to fit a budget. The default meter is process-local. Explicit
official HTTP additionally requires the shared durable spend ledger below.

## Explicit official HTTP mode

`--enable-remote --proposal-contract v2 --spend-plan PATH --spend-cell ID` must
be supplied together. The plan is nonsecret JSON with schema
`macr.repair.spend-plan.v1`, `run_id`, canonical `ledger_root`,
`configuration_sha256`, `output_contract_sha256`, `max_requests`, `max_usd`,
`max_total_input_tokens`, `max_total_output_tokens`, `expires_at` (Unix seconds)
and ordered `cells`, each containing exactly `cell_id` and `binding_sha256`.
`spend_binding_sha256(task, strategy, config, proposal_contract='v2')` binds the
complete neutral task, strategy, common configuration and output contract; it
excludes the output directory. Plan/task/configuration/version mismatches,
expired plans and ledger overlap with source/output fail before claim or Key
input. Explicit v3 is also supported; its output-contract digest and strategy
must match the frozen cell. A plan is a local limit and integrity record, not user authorization or
permission to bypass the platform's network controls.

Only `https://api.deepseek.com/chat/completions` is available, through the existing
bounded stdlib transport, normal TLS validation, no environment proxies,
redirects or retries. No real validator is supported by the CLI yet:
`api_debugger` and `api_test_feedback` report `verification_unavailable` without opening/claiming the
ledger, reading a Key or requesting HTTP. A synthetic replay cannot unlock it.
`direct_api` can record a candidate, not verify or execute it.

After valid scope and a successful unique cell claim, the CLI asks once for a
temporary Key using terminal no-echo input. Non-TTY, failed echo suppression,
empty input and cancellation reject before request reservation. No argv, shell
environment, credential-file or historical-Key discovery exists. A small memory
box supplies that same value to the transport for this cell and is cleared on
exit; immutable Python strings cannot be securely zeroed. Keys never belong in
the plan, summary, record or repository. Each future real use still needs its
applicable data, spending and credential authorization.
The adapter rejects literal active-Key echoes and its one supported JSON-content
decode without returning or recording that response. Invalid, duplicate-key or
uninspectable JSON content also fails closed before retention. With no trusted
billing receipt returned here, the unknown-cost reserve remains held and stops
further cells; this does not prove that the supplier charged nothing. It is not
a general scanner for other secrets or arbitrary encodings, or an unconditional
privacy guarantee.

The private ledger is separate from the loop output, holds a cross-process
single-writer lock and persists a reservation before each dispatch. Different
output directories cannot replay a cell or reset total budget/fingerprint.
Unknown outcomes retain their full reservation and stop later cells. Completed
known cells can be followed only in frozen order; damaged, copied, incomplete or
busy state fails closed. `finished_cells` counts terminal cells, including
failures, and is not a success count.

Remote CLI output includes `global_spend` and `spend_status`; the existing
top-level tokens/costs are only this cell's local meter. The durable summary
controls further admission. A local response can have known usage even if its
durable settlement write failed: the global reservation remains held. If global
reconciliation is impossible, `global_spend=null`/`spend_status=unreconciled`
means unknown, never zero. An unavailable verifier leaves that summary unopened.
No ledger files are appended inside the loop's hash-bound output directory.

Configured HTTP uses meter v2 and a saved `provider_runtime` commitment. Its
report is `simulation=false` with `official_https_output_unattested`; this
describes configuration, not authenticated real HTTP. Reload binds the runtime
reference, meter and exact requests. Its pending scoring origin is
`provider_unattested`, which cannot earn model-success credit. Socket timeouts
and local budgets are engineering protections, not whole-request cancellation
or supplier-enforced spending guarantees. Source/output logs and ledger stay
local; there is no automatic upload or deletion.

Both strategies start with full source, issue and visible tests and have the same
configured revision opportunities. Direct API can self-review its current
candidate, but receives no validation outcomes. Debugger can use source-bound
verification feedback. At present no approved runtime backend is configured, so
normal Debugger stops before a model request. The existing replay verifier can
only be injected by trusted tests and remains synthetic; there is no saved-replay
CLI argument or backend fallback. No host subprocess substitutes for isolation.

For evaluation, a one-call API is only a reference. The explicit v3 strategy
`api_test_feedback` is a competitive iterative API baseline receiving the same
public-test feedback, edit permissions and budgets as `api_debugger`. It lets
the model continue, finalize or withdraw after a public test pass; Debugger
stops automatically on that pass. Comparing these policies measures this
specific early-stop quality/resource tradeoff, with the same final independent
tests. It does not establish a general orchestration benefit. The normal
`repair-comparison` CLI supports a versioned three-arm frozen plan, pending
handoff and full-denominator report; see [repair-comparison.md](repair-comparison.md).
Neither feedback strategy currently has a permitted real verification backend.

Use `--proposal-contract v2` for an explicit retain-and-finish action in either original
strategy. After an accepted candidate, `finalize_current` requires empty edits
and the current source/evidence bindings. It consumes its normal model request,
preserves the candidate without another check and reports `candidate_finalized`,
never a verified repair. The complete chain and unchanged tests reach the same
pending scoring handoff. A first-round finalize, stale binding or dispatch after
finalize is rejected. `no_candidate` and `insufficient_evidence` still withdraw
the candidate. Legacy v1 remains the default with unchanged request bytes; it
lacks this action and must not be used as a strong iterative baseline. New v2
comparisons must bind the v2 output-contract digest for both strategies. V3
retains those actions and bounds while providing the common allowlisted public
feedback view for three-arm comparisons. The third strategy requires v3; all
three arms in a new comparison must share its digest. Old V1/V2 request bytes
and saved comparisons are not rewritten.

### Public failure diagnostics (explicit v4)

`--proposal-contract v4` retains v3's proposal actions and budgets, and adds
structured public-test diagnostics to subsequent model requests. A verifier
observation may include `diagnostics`: up to eight objects with `test_id`,
`kind` (`assertion` or `exception`), `message` (at most 1,024 UTF-8 bytes), and
`frames` (up to three `{file, line}` locations). Each entry must belong to a
failed public test in that exact source snapshot. Locations must reference a
supplied source file and an existing line; empty frames or missing diagnostics
remain explicit gaps. The loop does not open raw stdout references or hidden
test records. Structured frame paths are limited to the supplied source;
freeform messages are not redacted or authenticated by this validator. A future
live producer must sanitize its messages before constructing observations; no
such collector is enabled here. Invalid diagnostic structures stop verification
before the next provider call.

Both `api_test_feedback` and `api_debugger` receive the same public diagnostic
view; `direct_api` continues to self-review without verification feedback.
Saved receipts and serialized requests are rebound during local replay. A new
three-arm comparison can bind v4's output-contract digest, uniformly across
all cells; it cannot mix v3 and v4 records. V1–V3 defaults, requests and saved
experiments remain unchanged.

Messages are untrusted data. A valid source location proves neither the
diagnosis nor the repair. The production verifier remains unavailable, and the
default provider transport remains disabled. Deterministic intercepted-CLI
tests demonstrate that an authored response can select its next edit location
from this feedback; they do not establish model autonomy or quality gains.

## Source and scoring boundary

The neutral task has exactly `schema`, `task_id`, `issue`, `source_files` (SHA256
map), `editable_paths`, `test_paths`. It accepts no hidden test, scoring, secret
or private-file path fields. The entire generation inventory is checked against
that map before source bodies are read, including the second read immediately
before snapshot capture. Hash drift rejects the task. Protected tests cannot
appear in editable paths or any accepted patch. Source is parsed/compiled but
never imported/executed. This boundary cannot certify that a human custodian did
not mistakenly include an answer in an authorized source file; role separation
is not OS isolation or proof of no contamination.

Multiple `test_paths` may be supplied in any order. New runs use the same sorted
path order for the saved contract and every check, so a valid task can be
reloaded regardless of the input list order; existing records are not rewritten.

The 64 KiB proposal limit counts the supplied JSON's UTF-8 bytes, including
whitespace. Proposal parsing and the pending scoring handoff accept non-ASCII
text under that limit without ASCII expansion or truncation. Per-edit limits,
duplicate-key rejection, nesting limits and generic record bounds still apply.
The scoring proposal chain retains its separate 64 KiB aggregate limit; stored
response envelopes retain their 256 KiB limit measured as written UTF-8 bytes.

`repair_comparison.generation_view` strips the independent scoring contract.
`repair_entry.score_handoff(record, plan, cell_id)` revalidates the application
record, common input/model/output/budget commitments, complete multi-round
proposal chain and final source/test binding before creating a pending packet.
It never reads hidden scoring contents or invokes a scorer. The packet contains
opaque scoring commitments for the independent recipient, not reference answers.
The normalized verification handoff omits local snapshot paths. Current injected
provider and synthetic/unavailable validation records cannot earn model-success
credit. Only a later separately locked independent review can supply a score;
local hashes cannot authenticate the producer or semantic truth.

Missing/unexecuted, verification-unavailable (including zero model calls),
timeouts, contract failures and unknown outcomes remain in the planned
denominator. Zero-request preparations still retain measured latency. Model
requests, tokens, known local cost, unknown reserve and preparation/provider/
validation/total time use the same accounting route for both arms. Equal ceilings
are not equal actual consumption or an orchestration-only causal claim.

After a revalidated provider record reports settled usage beyond a per-request
or cumulative limit, the pending handoff uses `budget_exceeded`. It retains the
actual tokens and known local charge, earns no success credit and requires the
comparison to stop; later dispatched cells are rejected. This failure-only
reporting path does not increase dispatch limits, release unknown reserves or
authenticate metering. Original response records remain available for audit.

## Persistence and verification

Each output directory is exclusive; a repeated or partial run cannot be resumed
or overwritten. The record keeps intents, exact requests and valid responses,
source/test snapshots, every failed candidate, feedback and provider metering.
Reload recomputes request/wire hashes, binds successful usage/identity to the
actual normalized response, replays reservation arithmetic and binds final
outcomes to the same sources. Hashes provide local consistency only. The current
scripted profile is `simulation.v2`; older profile records are not migrated in place or granted new authority.

Real repair, useful model autonomy and API + Debugger gain are unproven. They
still require a permitted isolated validator, action-authorized live use of the
reviewed transport/accounting, independently held-out tasks/hidden scoring,
an exact fair freeze and a new bounded paid-run approval. No native backend or existing execution gate is enabled by this command.

## Offline verification scope

This integration passed 407 explicitly selected deterministic checks with no
skips on Python 3.12. These exercise source/storage boundaries, existing APIs,
the normal checkout entry, intercepted provider responses, budgeting and pending
scoring records. They are software regression checks, not 407 model-evaluation
tasks, and establish no real repair or combination-quality gain.

A separate set of 37 optional recovery checks also passed with zero skips in
the exact 41-package hash-locked Python 3.12 macOS arm64 environment. These use
native LangGraph/SQLite checkpoints and a fixed mock provider; they do not prove
production recovery or exactly-once external effects. That optional command is
not a dependency of this default-disabled repair entry. The installed global
console script, live services and target execution were not tested; the
repository's documented checkout wrapper was.
