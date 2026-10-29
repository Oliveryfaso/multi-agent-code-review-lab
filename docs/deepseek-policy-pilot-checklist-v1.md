# DeepSeek live-run checklist

This is a review checklist, not authorization to run. The historical pilots remain stopped: the unknown request must not be retried, its $0.33 reservation must not be released, and frozen results must not be rewritten. Continuing requires a separately confirmed recovery plan for unattempted cells; a new name or directory alone cannot enable it.

## Scope to approve and freeze

| Item | Required record |
| --- | --- |
| Model and destination | Exact model and endpoint, thinking settings, response-model allowlist and fingerprint policy; recheck the official contract and prices |
| Inputs | Neutral task/scenario/assumptions and exact authorized synthetic source files; exclude gold, rubrics, private projects, credentials and operating configuration |
| Plan | Task/arm/repeat grid, prompt and implementation versions, interleaving seed, unique execution identities and owned outputs |
| Budget | One persistent cumulative request and USD allowance including prior requests, settled local estimates and unresolved reservations; no automatic retry or extra connectivity call |
| Consent and predecessor | Explicit approved scope and verified predecessor evidence; a consent record is not itself proof that the operator obtained permission |
| Credentials | A specifically authorized credential and secure in-memory ingress; never chat, command arguments, traces, or newly persisted credential files |
| Local logs | An approved directory outside the source repository, directories 0700 and files 0600; named access, content and retention rules |
| Scoring | Independent semantic review, planned denominator and conditional quality kept separate, with failures and unattempted cells visible |

The tested profile used `deepseek-flash` at `https://api.deepseek.com/chat/completions`, thinking enabled and reasoning effort high. The supplied launcher uses no-echo terminal input and a frozen approval scope; it does not obtain a Key from arguments, environment variables or files. A different credential-ingress arrangement needs its own explicit scope and review.

The launcher rejects `GetPassWarning` before ordinary-input fallback can consume input. Terminal input failures return a fixed error and do not dispatch; a TTY check alone is not treated as proof that echo was disabled.

Ordinary preparation requires explicit `--approval-ref`, `--predecessor-control` and `--predecessor-sha256` inputs; it has no personal approval or prior-run defaults. The expected predecessor digest becomes part of the approved scope and must match verified, settled carry evidence before live input is accepted. This ordinary path still rejects an unknown predecessor. The separate retained-unknown recovery path below requires its own explicit confirmation and does not make an old request eligible for replay.

Only these eighteen original synthetic source paths are generation candidates, relative to `evals/deepseek-policy-dev-v1/`:

```text
snapshots/p01/logic.py
snapshots/p02/entry.py
snapshots/p02/metrics.py
snapshots/p03/entry.py
snapshots/p03/ring.py
snapshots/p04/logic.py
snapshots/p05/logic.py
snapshots/p06/entry.py
snapshots/p06/fields.py
snapshots/p07/logic.py
snapshots/p08/entry.py
snapshots/p08/layout.py
snapshots/p09/logic.py
snapshots/p10/encoding.py
snapshots/p10/entry.py
snapshots/p11/db_ops.py
snapshots/p11/entry.py
snapshots/p12/logic.py
```

Their combined source length is 1,867 UTF-8 bytes. Generation messages also contain versioned instructions, neutral task metadata, static evidence and any authorized tool transcript. Six retrieval cases give A more initial source than B/C/D; results must preserve that distinction. The gold and rubric may be public development files, but remain outside generation and tool scope.

## Budget and stop conditions

The historical authorization was **84 requests and $3 in total**. Those same cumulative limits apply across the historical pilots and any approved recovery; a new batch does not receive a new allowance. The durable ledger carries prior verified receipts forward and binds one successor. Each request reserves locally before dispatch. It settles only from a validated provider response with trusted usage, never from diagnostic counters or model text.

The historical $0.33 reservation assumed the frozen official context/output contract and peak prices. It and the $3 local threshold are conservative local controls, not a supplier-account hard cap or a guarantee that every planned cell can run. Missing or inconsistent usage, timeout, interruption or unknown outcome retains the reservation and halts the batch. Consult the [current official pricing](https://api-docs.deepseek.com/quick_start/pricing/); do not turn a character/token estimate into a claimed hard limit.

The historical unresolved state is **5 total requests, $0.004719 locally accrued and $0.33 still reserved**. The provider's actual charges have not been reconciled. An approved recovery carries all three values forward: the unknown reservation remains separately visible and occupied for this authorization, rather than becoming a claimed bill or a settled charge. Before any new reservation, $0.334719 is therefore already occupied and at most $2.665281 remains available.

## Explicit retained-unknown recovery

Recovery defaults to denied. After the implementation passes offline tests and independent review, `--prepare-recovery` requires `--retain-unknown`, a new `--approval-ref`, and the exact `--predecessor-control` and `--predecessor-sha256`. The confirmation must explicitly allow retaining the unresolved reservation and continuing only unattempted tests. Recording an approval reference is an audit aid, not proof that permission was obtained.

The frozen recovery plan and one-time confirmation bind the predecessor evidence, cumulative request count and occupied funds, the new batch's manifests, prompt and implementation digests, model and source scope, and its unique output directory. A separate durable recovery lock permits one target batch. A copied directory, reused confirmation or changed plan cannot reset the allowance; a partially completed preparation remains locked rather than silently starting over. The original successor lock, registries, unknown receipts and stopped states remain unchanged.

Eligibility is derived from logical `(task, repeat, arm)` identities across both historical pilots, not prompt-dependent cell IDs. All previously claimed cells are excluded, including failed or unknown cells. The current eligible subset has **45 cells**, with at most **78 new requests**: 11 A, 12 B, 11 C at two requests each and 11 D at three each. Each receives a new execution identity. Together with the previous five requests, this plan can use at most 83 requests; the unused request allowance does not authorize replay. A normal full-batch launch with a cell limit is not a substitute for this subset plan.

Every dispatch rechecks the bound evidence and cumulative budget before reserving funds. The $0.33 historical hold is never settled or released by a new response. New missing usage, timeout or unknown outcome still stops the recovery batch; this confirmation is not permission to ignore future uncertainty. The local reservation requirement can stop execution before all 45 cells finish, even when the request ceiling has not been reached.

Supplier reconciliation may later clarify an attributable charge or non-acceptance, but is not required for the explicit conservative-retention choice. Until such evidence exists, the historical disposition remains unknown. A total balance cannot attribute one call, and absent request IDs or response data must not be invented. Adding a billing endpoint, another service, new source files or log upload requires separate scope approval.

Recovery results belong to their own versioned batch. Preserve failures and unattempted cells in the planned denominator, and review semantics independently. Different prompt or implementation versions must not be combined into an unconditional four-arm score. A recovery plan or successful offline test is not evidence of a completed live comparison or a measured tool benefit.

## Logs and review

Requests and outputs can contain authorized source, model answers, reasoning content, usage and receipts. Keep them in the approved private local root. The tested policy records fourteen days for review and reconciliation; it does not automatically upload or delete anything. Any cleanup rule must be explicitly authorized, and retained aggregate results must remain distinct from raw logs.

Neither the source fixtures nor a live call should start a VM, service, container, target-code test runner or local baseline model. VM dynamic acceptance is a separate blocked line of work. The synthetic development gold is already known; a later formal blind or held-out evaluation needs a separate design. See [aggregate results](deepseek-policy-results.md) for the incomplete evidence available so far.
