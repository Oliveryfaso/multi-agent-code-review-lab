# Disabled study activation candidate

This separate entry prepares a reviewable proposal for the nine-task paired
study. It authorizes no API request. Public offline cell/batch functions keep
their fixture-only checks. Private policy cores reuse their unchanged
strategies; the activation entry owns approval, credential ingress and wire
admission. This is a thin entry, not a new agent framework or OS sandbox.

`propose_study_live_approval(control_root)` loads and verifies the frozen neutral
profile, then returns `authorized: false` and an empty confirmation reference.
The scope binds the exact control path, profile and neutral/source hashes,
ordered 36 cells, model/endpoint, new 108-request/$2 limits, price snapshot,
single concurrency, zero automatic retries, and log policy. Activation requires
that exact record, an explicit authorization reference and `enabled=True`.
These local records express operator consent; they are not signed permissions.
The calling agent must obtain actual user authorization before activating or
marking a record approved. Do not reuse the old experiment's remaining requests
or funds, release its holds, or route new cases through its old live wrapper.

There is one permanent execution claim before any Key reader is called.
Copied control roots fail validation; repeated/concurrent execution cannot
claim twice. Source, implementation, plan and profile drift fail closed.
Profile identity is checked again after the execution reload and before Key
ingress. Interrupted or uncertain execution is not resumable. No retry/reset
or recovery path is included in this candidate.

Each request first persists a $0.33 reservation. The wire may consume only that
request's exact admitted body hash at the official endpoint, once. Trusted
usage settles independently of answer parsing; untrusted/missing usage retains
the reservation and stops the whole batch. Identity, scope, infrastructure and
budget failures also stop. Known answer-contract failures remain failed cells
and advance without resending. Actual wire attempts and reserved-request
counts are separate. A local deadline cannot guarantee remote cancellation or
halt provider billing.

## Frozen planning contract and budget

Checked on **2026-10-29** against the official [pricing table](https://api-docs.deepseek.com/quick_start/pricing/)
and [request example](https://api-docs.deepseek.com/): `deepseek-flash` at
`https://api.deepseek.com/chat/completions`, thinking enabled, high effort,
nonstreaming. The advertised version is DeepSeek-V4.1-Flash-1017. Responses
must retain the configured model identity and a pinned fingerprint; unknown
or changed identity stops the batch. HTTP authentication and the actual
returned identity have not been validated by this offline candidate.

Use peak rates per million tokens: input miss $0.30, input hit $0.006, output
$1.20. Reasoning is part of output, not an additional charge. Usage without
cache split is conservatively charged all miss. One request's conservative
context-based bound is `(1,000,000 × .30 + 2,000 × 1.20)/1,000,000 = $0.3024`,
below the $0.33 reservation, conditional on the same official price/context
contract. The 8k/request and 24k/cell input figures are planning limits checked
after receipt, not trusted pre-dispatch token caps.

The new proposal is 9 × 4 × 3 = **108 maximum requests**, with a **new $2 local
budget**. It may stop early; it does not guarantee all 36 cells. If all cells
fit the 24k input/6k output plan, full-miss planning cost is
`36 × (24,000 × .30 + 6,000 × 1.20)/1,000,000 = $0.5184`, not a bill guarantee.
Recheck pricing before actual approval/activation. The original 67 requests,
known accrual and unknown holds remain historical obligations; neither a new
directory nor a new authorization turns unknown billing into zero.

The provider's [thinking contract](https://api-docs.deepseek.com/guides/thinking_mode/)
distinguishes effective history: tool-bearing continuations must return prior
reasoning; no-tool requests ignore earlier reasoning even when it is sent.
The client preserves its transcript, but does not claim equal effective
reasoning context or computation across strategies. Fixed evidence is a real
host message; it does not fabricate Chat Completion tool calls. JSON mode and
client diagnosis/tool validators remain separate constraints.

## Credential, log and command boundary

The CLI accepts no Key argument, file or environment option. Only explicit
execution with an approved scope and interactive terminal can request hidden
input. Echo-control warnings and input failures stop without fallback input.
The Key remains in process memory for official DeepSeek authentication only;
it is excluded from requests' model data, approval records and logs. No Key is
read during import, proposal, preflight or documentation preparation; tests use
dummy values and scripted responses.

The approved log root is exactly the frozen control directory outside the
neutral package. The root must be owner-only `0700`; output records use `0600`.
Logs contain source, answers/reasoning, exact request/response evidence and
usage. Default retention is 14 days, no upload, no automatic deletion; purge
requires later user approval. These modes do not isolate same-user agents.
Neutral/custodian role separation and its residual risks remain as described
in the [handoff contract](deepseek-study-neutral-handoff.md).

Offline proposal (no Key or API):

```sh
python3 -I -S -B scripts/run_deepseek_study_live.py --control FROZEN_CONTROL --proposal
```

After a separate explicit user approval, a matching approval file can be checked
without authentication using `--approval APPROVAL_JSON --preflight`. Future
activation uses `--approval APPROVAL_JSON --execute --enable-live` in an
interactive terminal. These are interface templates, not permission to run
them now. Parameter parsing, disabled/default rejection, no-echo rejection,
claim ordering, exact wire binding and the simulated path are offline-tested;
real HTTP/authentication/provider billing are not.

The approval must list exact external data: neutral question/scenario/allowed
domain, authorized files and initial/full-source scope, initial summaries,
fixed/adaptive static excerpts, prompts/schemas/tool declarations, and model
conversation histories repeated in follow-up requests. Raw source/package bytes
are an inventory measure, not total network traffic. Each encoded request is
bounded to 131,072 bytes, so 108 bodies are at most 14,155,776 bytes, excluding
HTTP authentication/headers. Gold, concrete rubric/labels/proofs, reference
tests, repairs, old private logs, private projects/configuration and new
Search/Fetch or third-party services remain excluded.

An explicitly approved [one-time continuation](deepseek-study-continuation.md)
can select only the frozen study's untouched cells after a historical identity
gap. It retains the original results and spending, pins future identity to the
verified historical value, and reports both implementation segments separately.
