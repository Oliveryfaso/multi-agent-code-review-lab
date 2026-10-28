# DeepSeek live-run checklist

This is a review checklist, not authorization to run. The historical pilots are stopped; the unknown request must not be retried, its $0.33 reservation must not be released, and frozen results must not be rewritten. A new name or directory is not a continuation mechanism.

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

Public preparation requires explicit `--approval-ref`, `--predecessor-control` and `--predecessor-sha256` inputs; it has no personal approval or prior-run defaults. The expected predecessor digest becomes part of the approved scope and must match verified, settled carry evidence before live input is accepted. These parameters do not provide reconciliation or resume, and they cannot make the historical unknown request eligible for carry.

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

The historical authorization was **84 requests and $3 across both pilots together**, not that amount for each batch. The durable ledger carries prior verified receipts forward and binds one successor. Each request reserves locally before dispatch. It settles only from a validated provider response with trusted usage, never from diagnostic counters or model text.

The historical $0.33 reservation assumed the frozen official context/output contract and peak prices. It and the $3 local threshold are conservative local controls, not a supplier-account hard cap or a guarantee that every planned cell can run. Missing or inconsistent usage, timeout, interruption or unknown outcome retains the reservation and halts the batch. Consult the [current official pricing](https://api-docs.deepseek.com/quick_start/pricing/); do not turn a character/token estimate into a claimed hard limit.

The unresolved state is **5 total requests, $0.004719 locally accrued and $0.33 still reserved**. The provider's actual charges have not been reconciled. There is no supported resume or 45-cell subset-execution interface. A full new batch with a cell limit would start from the beginning and is not a safe substitute. Do not mark earlier cells complete, edit old registries or release the unknown amount to make it run.

## Minimum reconciliation before considering continuation

The operator must use official billing/usage records or supplier support to establish an attributable disposition: verified non-acceptance/no charge, or the charge and available usage, amount and currency. Keep credentials private. The retained local window and model/destination can help a private investigation, but no request ID or raw response survived; do not invent them. A total balance alone cannot attribute one call, particularly when other activity or currencies are involved.

External reconciliation does not itself unlock the implementation. A reviewed transition record and an explicit new execution plan would still be needed to preserve the original unknown evidence, account for its disposition, select only eligible unexecuted logical cells and give them new execution identities. Those mechanisms are not implemented by this patch. New billable work requires a separate execution decision within the authorized scope; adding a billing endpoint, another service, new source files or log upload requires explicit scope approval.

## Logs and review

Requests and outputs can contain authorized source, model answers, reasoning content, usage and receipts. Keep them in the approved private local root. The tested policy records fourteen days for review and reconciliation; it does not automatically upload or delete anything. Any cleanup rule must be explicitly authorized, and retained aggregate results must remain distinct from raw logs.

Neither the source fixtures nor a live call should start a VM, service, container, target-code test runner or local baseline model. VM dynamic acceptance is a separate blocked line of work. The synthetic development gold is already known; a later formal blind or held-out evaluation needs a separate design. See [aggregate results](deepseek-policy-results.md) for the incomplete evidence available so far.
