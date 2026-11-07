# Final evaluator observations

The evaluator-only `prepare-final` and `import-final` commands complete the
**data handoff**, not the execution or independent-authentication requirement.
They do not start a backend, import a test module, execute a target, call a
model, read credentials, award repair credit or change a generation record.
There is no fallback to Docker, a VM or a host process.

The normal generation path still receives only public tests and its public
feedback. Do not pass a final policy, request, receipt, overlay, custodian
record or final output directory to a generation provider or workbench session.
The final commands are separate from `BackendVerifier` and public receipts.

## Freeze before generation

Prepare a strict `macr.repair.final-policy.v1` JSON object containing:

- `tasks`, sorted by task ID and covering exactly the comparison tasks. Each
  entry has `task_id`, `kind` (`defective`, `healthy` or
  `insufficient_evidence`), `criteria_sha256`, and `hidden_test_plan`.
- `hidden_test_plan` uses the existing pytest test-plan v1/v2 shape, with exact
  node IDs. Every selected file must be in that task's committed scoring
  overlay. Selectors do not prove collection or fixture/dependency fidelity.
- `runner_sha256`, `environment_sha256`, `dependency_lock_sha256` and
  `calibration_sha256`, identifying the independently prepared runner,
  environment, dependencies and original/reference/masking qualification.
- `limits`: positive integer `cpu_seconds`, `memory_bytes`, `pids`,
  `wall_seconds` and `output_bytes`. Format ceilings are 3600 CPU/wall seconds,
  64 GiB memory, 4096 PIDs and 16 MiB output per check. These are request limits,
  not evidence that a backend enforces them.

Set `spec.scoring.rubric_sha256` to `result_digest(policy)` **before** compiling
and using the comparison. `criteria_sha256` commits the separate semantic
criteria; the policy does not contain or authenticate their contents.

Separately prepare a strict custodian object with `schema` set to
`macr.repair.final-custodian.v1`, a bounded `custodian_id`, and
`protocol_sha256`. Set `spec.scoring.custodian_commitment_sha256` to
`result_digest(custodian)`. The existing `hidden_tests_sha256` remains the
complete overlay-manifest commitment. Historical plans cannot be retrofitted
without rejection. A descriptor, protocol hash or calibration hash is an
integrity commitment, not proof of identity, qualification or independence.

## Prepare a final-only request

After `stage-scoring`, run:

```sh
python3 -I -S -B cli/agent_review.py repair-comparison prepare-final \
  --plan comparison/plan.json --record runs/cell-record --cell CELL_ID \
  --stage scoring/staged-cell \
  --overlay-manifest scoring/overlay.json --overlay-root scoring/files \
  --policy scoring/final-policy.json --custodian scoring/custodian.json \
  --out scoring/final-request
```

This recomputes the source handoff from the complete saved generation record
and committed overlay. It compares every saved stage metadata/source byte,
rejecting missing/extra entries, symlinks, hard links, aliases and nonprivate
stage permissions. It never ignores an extra config or helper file. The
recomputed v6 candidate is the selected candidate, not the last attempted edit.
Local byte verification is not a claim of race-free later execution. A future
backend must capture and reverify its actual inputs at dispatch.

`request.json` binds the comparison, complete cell (arm and repeat included),
packet, stage, overlay, policy, custodian, unchanged generation result and
separate check identities. Each check binds its exact selectors, framework and
whole raw-file inventory digest. There are four checks when a candidate exists:

- Public original and public candidate, with unchanged generation tests.
- Hidden original and hidden candidate, with the identical scoring overlay.

A missing candidate produces only the two original checks. It is never replaced
with the original. Public tests must be rechecked even if generation previously
reported a pass: an overlay may replace a protected public test, and an old
receipt does not validate either new tree. A separately authorized producer
must reconstruct/capture the corresponding complete source trees, not execute
arbitrary paths from the request. No execution/import plugin is discovered.

## Import a producer's final observation

Use the same inputs, operation `import-final`, and additionally
`--receipt scoring/final-receipt.json`, with a fresh `--out` directory. The
command recomputes the request again; it does not trust an edited saved request.
All inputs are validated before output claim. Outputs cannot overlap inputs;
existing/partial output directories cannot be resumed or overwritten.

A strict receipt has schema `macr.repair.final-receipt.v1`, `phase: final`,
`request_sha256`, `origin: independent_custodian_reported`, `execution`,
`checks`, `preparation_ms` and `total_ms`. `execution` must repeat the four
frozen runner/environment/dependency/calibration commitments. The origin is
explicitly a producer claim, not authentication.

Each check, in request order, contains exactly:

- `check_id`, `execution_status`, `exit_code`, `test_counts`, `outcomes`.
- `failure_kinds`: exactly the failed test IDs, each `assertion` or `exception`.
- `limits_observed`: `cpu_ms`, `memory_bytes`, `pids`, `output_bytes`, or null
  when measurements are unavailable. Wall usage is `elapsed_ms`.
- `output_truncated`, `cleanup_status` (`closed` or `outcome_unknown`),
  and `elapsed_ms`.

Completed outcomes can be `passed`, `failed`, `error`, `skipped`, `xfailed` or
`xpassed`. Counts must agree with reported identities and categories. Incomplete
checks have no outcomes, counts or exit code. Raw output, diagnostic text,
external file references, arbitrary review/outcome fields and public receipt
schemas are rejected. Unlisted IDs and reordered/duplicate checks are rejected.

A test pass requires every frozen ID, all `passed`, exit 0, consistent counts,
no truncation, closed cleanup and reported measurements within every frozen
limit. Zero tests, skips, xfail/xpass, errors, collection failures, missing
measurements and overruns do not establish a pass. An original hidden assertion
failure is recorded separately from an exception or a baseline that passes.
Neither fact establishes semantic repair, healthy correctness or necessary
abstention. Those require the independent rubric and provenance review.

## What the output means

- `request.json` and `final.json` retain final observations and their source
  bindings, with final timing separate from generation timing.
- `results.json` contains the **unchanged generation row**, including original
  model source, historical public receipt, costs, final candidate and
  `review: null`. No `simulation` or `provider_unattested` origin is upgraded.
- `report.json` keeps all planned cells and zero new success credit. Its
  `final_scoring` sidecar records the imported status, final latency and stop
  flag. An unknown execution or cleanup outcome sets `must_stop`; no automatic
  retry is allowed. Timeout/error/missing tests remain explicitly unresolved,
  not passes or removed denominator cells.

Even a structurally perfect final receipt remains
`pending_independent_review`, with `scoring_credit_awarded: false` and
`provenance_authenticated: false`. This path cannot create a locked review.
The existing low-level `build_report` accepts already reviewed result maps;
its structural locked-review checks are not independent authentication and
must not be used to manufacture a review from these observations.

Remaining before a real repair comparison: qualify an authorized execution
backend with original/reference/masking controls, establish a real independent
custodian and model-provenance validation boundary, produce truthful final
observations in the frozen environment, and apply the independent semantic
rubric. No current code test or commitment proves those steps occurred.

Offline regression (authored fixtures only):

```sh
python3 -I -S -B scripts/run_offline_tests.py test_repair_final_scoring
```
