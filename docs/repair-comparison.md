# Three repair policies, one comparison contract

The explicit `macr.repair-comparison.spec.v2` contract compares three policies:

| CLI strategy | Feedback during revision | Who ends a successful public test run? |
| --- | --- | --- |
| `direct_api` | Source, issue and visible tests; model self-review without candidate test results | Model finalizes, withdraws, or reaches the request ceiling |
| `api_test_feedback` | Same inputs plus public test feedback | Model may revise, finalize or withdraw after a pass |
| `api_debugger` | Same inputs and same public test feedback | Coordinator stops on a public test pass |

All three use the same explicit `--proposal-contract v3` (or v4/v5/v6), model configuration,
initial source/issue/tests, edit permissions and per-cell request/token/dollar
ceilings. A ceiling is an equal opportunity, not equal consumption. Reports retain
actual calls, tokens, known local cost, unknown reserves and phase/total times.
No hidden test, reference patch or scorer content is supplied to generation.

The feedback-versus-self-review comparison measures the effect of receiving
public validation information. The automatic-stop-versus-model-stop comparison
measures this particular stopping policy, including its quality/cost tradeoff.
Neither proves a general Debugger or orchestration advantage. A public test pass
is not final independent correctness. A later revision may lose a correct
candidate; the handoff retains the last eligible candidate, not a selected best
historical answer.

V3 preserves the V2 finalization actions and hard output bounds, and introduces
a common allowlisted feedback view. It omits host snapshot paths, raw verifier
records, arbitrary details and free-form output. Only public manifest test names
and normalized outcomes enter the model view. Missing verification is explicit,
never a synthetic success. The original records remain available locally for
auditing. Legacy V1/V2 requests and the two-arm V1 comparison stay unchanged.

## Local CLI workflow

From the checkout, without a temporary `PYTHONPATH` or an installation:

```sh
python3 -I -B cli/agent_review.py repair-comparison compile \
  --spec examples/repair_comparison_three_arm_development.json \
  --out /absolute/new/comparison-plan
```

This produces `plan.json` and a detached `generation/<task_id>.json` capsule for
each task. Capsules exclude scoring commitments and contain only neutral task
commitments, model policy and limits. The example contains placeholder task and
scoring commitments; it demonstrates the contract and is not a frozen pilot.
The compiler does not call a provider, execute tests, claim a spend cell or grant
execution authority. The plan, including order, is rederived on every import.

The ordinary `repair` command accepts the three strategies. V3 is explicit:

```sh
python3 -I -B cli/agent_review.py repair \
  --repo examples/repair-preview-development/baseline \
  --task examples/repair-entry-development/task.json \
  --model-config examples/repair-entry-development/model.json \
  --strategy api_test_feedback --proposal-contract v3 \
  --out /absolute/new/repair-record
```

Network remains off by default. Both feedback policies currently stop with
`verification_unavailable` before Key input, ledger claim or model dispatch:
there is no approved real verifier configured in this entry. A replay verifier
is a trusted deterministic test seam, not runtime permission or real execution.
The independent spend ledger binds strategy, neutral task, output contract and
model configuration; changing output directories cannot replay a claimed cell.
No previous experiment's allowance authorizes this comparison.

For a record bound to an actual comparison cell, export its pending packet:

```sh
python3 -I -B cli/agent_review.py repair-comparison handoff \
  --plan /absolute/comparison-plan/plan.json \
  --record /absolute/repair-record --cell CELL_ID \
  --out /absolute/new/score-handoff
```

`packet.json` contains source-bound candidate material for an independent
custodian; `results.json` contains that cell's pending result row. Exporting
does not award credit. Current simulated/unattested outputs cannot become
locked model-success reviews merely by importing them. Packets can contain
source and outputs: keep them local, with no automatic upload or deletion.

After independent reviews are locked, supply a sanitized map keyed by planned
cell IDs (missing cells may be omitted):

```sh
python3 -I -B cli/agent_review.py repair-comparison report \
  --plan /absolute/comparison-plan/plan.json \
  --results /absolute/sanitized-results.json \
  --out /absolute/new/comparison-report
```

All three commands require new exclusive output directories. A handoff output
must be disjoint from its immutable repair record. They reject
duplicate JSON keys, edited plans, unknown result cells and conflicting review
bindings. No command imports a private scoring file or searches for credentials.
Local hashes establish consistency, not provider authenticity, independent
review, OS isolation or proof that a custodian excluded every answer leak.

## Reading the report

Keep every planned cell in the denominator, including contract/transport
failures, timeouts, missing/unknown responses, unavailable verification and
pending reviews. Outcome counts separate full repair, necessary abstention,
partial answers and wrong conclusions. Effective completion is a composite;
it must not be described as a defect-repair rate without an independently
frozen repair-task denominator. Equal unsuccessful outcomes are not two correct
answers. Missing or unreviewed outcomes are not observed ties.

Three paired contrasts are reported. Repeats are averaged within each task
before uncertainty is calculated. Known terminal failures contribute observed
non-completion; unknown or unreviewed results retain an unresolved range. The
conservative bounds assume independent tasks and account for the three
comparisons; they cannot remove project/family dependence, contamination or
scoring bias. Eight pilot tasks remain exploratory. Exact content aliases
cannot be relabeled into additional independent tasks.

Unknown cost, missing/drifting identity or a settled budget overrun stops
further dispatch in the frozen order. Actual overruns remain failed observations
and are never hidden by clipping values to ceilings. Costs are local accounting,
not supplier invoices. Timing is measured/reported, not proof of hard whole-run
cancellation or a guaranteed supplier spending cap.

## What a real pilot still needs

An actually permitted isolation backend with verified public/hidden-test
separation and cleanup must be qualified first. The stopped native attempt is
not reopened by this code. An independent custodian must freeze fresh tasks,
unchanged public tests, hidden scoring and mappings outside generation access.
Official HTTP output is currently marked `provider_unattested`; a reviewed
independent provenance/scoring import must qualify its origin before it can
receive model-success credit. Changing that label alone is insufficient.
Then freeze exact model/configuration, data bytes, three-arm order, common
budgets, verification limits and paired endpoints before requesting real API,
credential, data-transfer and execution permission.

The plan also reports whether its cell/request counts fit the current durable
ledger's 128-cell/384-request capacity. A capacity fit is not readiness or
permission. Larger proposals remain descriptive and cannot be split into new
ledgers to reset budget, identity or unknown-stop state.

One proposed exploratory design is two development tasks followed by eight new
tasks, three arms once each: at most 30 cells and 90 model requests at three per
cell. This is a proposal, not an approved run or dollar allowance. It requires
current official prices and a conservative reserve calculation against exact
requests. Old funds and unknown reservations are not reused. Offline control
flow tests demonstrate software behavior, not that API + Debugger repairs more
real defects.
