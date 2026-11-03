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
encode/parse the actual provider conversation contract. Tests inject a fake
transport into that adapter and inspect the generated wire payload; they do not
replace the repair coordinator or proposal parser.

There is **no built-in transport, credential reader or remote enable option** in
this command. Normal application construction is disabled. Injected transports
are a trusted test/application seam, not proof of approved network access or an
authentic model. `real_model_calls`/`paid_requests` are unknown for such
unattested transport results; they are not quietly certified from a model label.
All current outcomes stay `repaired=false`, no target execution, no execution
authority and explicit simulation/unattested provenance.

The shared configuration includes model/official endpoint, thinking settings,
fingerprint policy, cumulative request/input/output limits and a caller-frozen
price schedule. Before each call the binding reserves the configured complete
input envelope plus output ceiling, then settles only normalized usage or
independent wire-bound failure evidence. Unknown cost retains its reserve and
stops; identity mismatch, budget exhaustion and interrupted dispatch stop without
retry. Caller estimates are engineering controls, not a current-price assertion,
supplier invoice, hard spending guarantee or trusted tokenizer bound. No source
is truncated to fit a budget. Real cross-process durable ledger/admission is not
enabled by this in-memory meter.

Both strategies start with full source, issue and visible tests and have the same
configured revision opportunities. Direct API can self-review its current
candidate, but receives no validation outcomes. Debugger can use source-bound
verification feedback. At present no approved runtime backend is configured, so
normal Debugger stops before a model request. The existing replay verifier can
only be injected by trusted tests and remains synthetic; there is no saved-replay
CLI argument or backend fallback. No host subprocess substitutes for isolation.

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

## Persistence and verification

Each output directory is exclusive; a repeated or partial run cannot be resumed
or overwritten. The record keeps intents, exact requests and valid responses,
source/test snapshots, every failed candidate, feedback and provider metering.
Reload recomputes request/wire hashes, binds successful usage/identity to the
actual normalized response, replays reservation arithmetic and binds final
outcomes to the same sources. Hashes provide local consistency only. The current
scripted profile is `simulation.v2`; older profile records are not migrated in place or granted new authority.

Real repair, useful model autonomy and API + Debugger gain are unproven. They
still require a permitted isolated validator, reviewed live transport with
durable authorization/accounting, independently held-out tasks/hidden scoring,
an exact fair freeze and a new bounded paid-run approval. No native backend or existing execution gate is enabled by this command.

## Offline verification scope

This integration passed 335 explicitly selected deterministic checks with no
skips on Python 3.12. These exercise source/storage boundaries, existing APIs,
the normal checkout entry, intercepted provider responses, budgeting and pending
scoring records. They are software regression checks, not 335 model-evaluation
tasks, and establish no real repair or combination-quality gain.

A separate set of 37 optional recovery checks also passed with zero skips in
the exact 41-package hash-locked Python 3.12 macOS arm64 environment. These use
native LangGraph/SQLite checkpoints and a fixed mock provider; they do not prove
production recovery or exactly-once external effects. That optional command is
not a dependency of this default-disabled repair entry. The installed global
console script, live services and target execution were not tested; the
repository's documented checkout wrapper was.
