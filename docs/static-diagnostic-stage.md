# Explicit static evidence comparison stage

This entry tests whether one deterministic Debugger capsule improves a single
API answer. R and E receive the same complete authorized source, task, citation
table, compact-v3 response contract and model settings. Only E receives the
computed capsule. It estimates the value of computation and presentation; it
does not introduce runtime observations or demonstrate an agent-policy benefit.

The [static evidence mechanism](static-diagnostic-evidence.md) remains usable
offline. The separate stage entry is **default off**. Preparing a plan, a
proposal or a fixture run never authorizes real requests or reads credentials.
Historical results, budgets and unknown reservations are not reused.

## Fixed proposed scope

| Phase | Data | Maximum requests | Local budget |
| --- | --- | ---: | ---: |
| S0 | Three public development pairs | 6 | $0.50 |
| Conditional S1 | Nine independently authored new tasks, R/E once | 18 | $1.00 |
| Whole stage | One call per cell; no retries | 24 | $1.50 |

S1 starts only after all six S0 responses satisfy the mechanical output,
identity and accounting gates. S0 qualification is not a semantic quality
claim. The first S0 failure stops the stage. S1 stops at its second known
contract failure; every failed or unattempted cell remains in the denominator.
The phase budgets cannot be borrowed from each other.

Each request uses official `deepseek-flash`, thinking enabled/high, an 8,000
input-token plan and an 8,192 combined thinking/answer output ceiling. The API
does not provide a separate answer-token reservation. Actual measured input
over 8,000 stops the stage without rewriting history. No output is silently
trimmed into compliance.

A persistent $0.33 reservation precedes each dispatch. Trustworthy usage is
settled before evaluating the assistant's output, so a format failure can
still have a known charge. Missing usage or an uncertain outcome retains the
reservation and stops. Missing or changed model identity, data/code drift,
insufficient reserve and infrastructure errors also stop. There is no retry,
resume, copied-directory reset or extra connectivity call.

These are local engineering controls, not a supplier invoice hard limit.
The reservation uses a deliberately loose input envelope and peak pricing;
neither it nor the 8,000 input plan is a verified supplier-side input cap.
See [the provider contract](deepseek-provider-compatibility.md).

## Preparation and approval

The [custodian interface](static-diagnostic-custodian-interface.md) specifies the
neutral nine-task pack, hash-only commitment and locked review import. A
separate custodian keeps gold, category maps and hidden checks private. Role
and directory separation under the same OS account is not OS isolation.

From the trusted checkout, with input and output roots separate:

```sh
python3.12 -I -S -B scripts/run_static_diagnostic_stage.py prepare \
  --development-pack examples/static-diagnostic-development \
  --qualification-pack /path/to/new-neutral-pack \
  --custodian /path/to/hash-only-commitment.json \
  --stage-id static-value-new-001 --out /path/to/private-stage
python3.12 -I -S -B scripts/run_static_diagnostic_stage.py proposal \
  --control /path/to/private-stage > /path/to/approval-proposal.json
```

The proposal has `authorized: false`. An actual one-time user confirmation
must cover the frozen plan, destination, authentication, external data,
budgets and stops before an approval record can enable execution. A local
confirmation record is auditable metadata, not a cryptographic signature.

```sh
python3.12 -I -S -B scripts/run_static_diagnostic_stage.py preflight \
  --control /path/to/private-stage --approval /path/to/approved-record.json
```

Preflight rechecks source, implementation, full requests and control-root
bindings with zero requests. `dry-run --control ... --replies ...` consumes a
fresh fixture stage using one literal SDK response (or `"timeout"`) per cell;
it cannot subsequently be used for live execution. It proves dispatch and
accounting behavior, not model accuracy. A live command additionally requires
`execute --enable-live`, the exact approved record and an exclusive claim
before a credential callback can run. A crash retains the claim and spend
records; the entry does not reconstruct a fresh ledger to resume.

Key input uses the established no-echo terminal reader or an explicitly bound
project `.env` source. The Key is read only after authorization and the unique
claim, remains in memory for official authentication and is not put into
arguments, reports or a newly persisted credential file. A compromised Key
can be misused and incur charges. Preparation does not inspect it.

## Evidence and decisions

Freeze both serialized requests before approval. Record source and capsule
hashes, actual source coverage, unresolved relations, truncation, packet and
wire bytes. A possible static relation is never an executed call. Allocate
common source capture time to each standalone strategy; charge index and
capsule work only to E. Report real provider usage, cost and latency separately
from preprocessing and fixture counters.

After the whole stage closes, export source-only anonymous review packets.
Raters receive full source and answers, without strategy, capsule, cost,
order or the separate keeper mapping before review lock. This reduces cues;
it does not justify claiming complete blinding. Score import binds exact
run/result/review hashes. Failures and missing reviews cannot become semantic
ties. A paired win, loss or tie requires two usable, independently locked
reviews; complete delivery dispositions remain separate.

Advancing this evidence route requires complete credible observations, at
least two net semantic paired wins, no added unsupported determinate errors
and E's allocated end-to-end cost and time within 1.25 times R. An incomplete
stage is inconclusive. Insufficient gain retains the direct baseline for this
mechanism and guides simplification; it does not end combination research.

Logs contain source, requests, model outputs, usage and identity evidence.
Keep them in the owned local directory outside the checkout, directories
mode 0700 and files 0600. The retention guidance is 14 days for review and
reconciliation, with no automatic deletion or upload. Publish only reviewed
code and safe aggregate results, never gold, raw logs or private mappings.
