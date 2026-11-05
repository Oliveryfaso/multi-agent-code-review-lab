# Scripted repair feedback reference

The normal `repair` command shares this loop and adds a default-disabled
provider adapter and independent scoring handoff. See [repair-entry.md](repair-entry.md).
The scripted description below applies to the explicit demo wrapper; it does not
enable a real transport or runtime. Saved experiment results are not reclassified.

This explicit development profile connects source-bound patch proposals, constrained edits,
verification feedback and a subsequent proposal. It reuses `PatchAgent.propose_bound`,
`SnapshotStore`, `TestRunnerTool` and the existing check classifications. Legacy
orchestration, the direct API baseline, native admission and frozen experiments
are unchanged.

The only implemented dispatch adapters are `ScriptedPatchProvider` and
`ReplayVerifier`. They neither call a model nor run repository code. Other
adapters are rejected before output creation. An unavailable verifier stops
before any proposal; the baseline must first have an explicitly synthetic
failure. At most three scripted requests can follow. A failed candidate becomes
the next request's full source, paired with its exact source-bound feedback.

`apply_edits` accepts at most three unique-file replacements, each matching old
text exactly once. The caller supplies explicit editable Python paths; test
paths cannot overlap. No files are added/deleted, the original tree is not
modified, and all protected files remain byte-identical. Source is bounded to
32 files, 16 KiB each, 64 KiB total and statically compiled but never executed.
Syntax validity does not establish semantic safety or correctness.

Run the self-authored development demonstration from the repository root:

```sh
python3 -B scripts/run_repair_loop_demo.py simulate --out /absolute/new/demo-directory
python3 -B scripts/run_repair_loop_demo.py verify --out /absolute/new/demo-directory
```

The script deliberately proposes an authored constant-return change, supplies
synthetic failure outcomes, then supplies an authored correction and synthetic
pass outcomes. These outcomes are prescribed, not observed by executing the
fixture. It verifies that failure feedback reaches the second actual serialized
request and that tests cannot be edited. `simulation_passed` always retains
`repaired=false`, `target_code_executed=false`, zero real model/API calls and
explicit scripted/synthetic provenance. It demonstrates control flow, not model
autonomy, failure reproduction, masking detection, repair quality or isolation.

The exclusive output directory is a one-shot claim. Request intent is saved
before dispatch; unknown provider/verification outcomes stop. There is no
automatic retry, restart, resume or live execution entry. Partial artifacts
remain for inspection. Cumulative input/output token reservations use request
ceilings before dispatch; scripted usage is retained separately from billing.
Invalid provider envelopes can stop as unknown without retaining a raw invalid
envelope. A future live adapter must use the established bounded failure-evidence
interface and independent cost/identity admission rather than reuse this fake.

Each source snapshot, request, raw valid response, edit and normalized check is
retained. `verify_loop` rederives edits, immutable tests, exact snapshot files,
fixed test specs, feedback, request bytes, terminal status and token accounting.
Local hashes detect changes against a retained freeze; they do not authenticate
the history, execution, model, timing or observation producer. No saved result
can authorize a native operation or become a real repair claim.

## Future comparison contract

`macr.evals.repair_comparison` compiles a proposal-only JSON specification and
validates reports without dispatching anything. Both arms receive the same full
original source, issue and visible tests, model configuration and output contract.
Both can use the same cumulative request/token/dollar ceilings and revision
opportunities. Direct API can iteratively self-review; Debugger adds computed
evidence and available validation feedback. That measures the whole strategy.
An orchestration-only claim needs a separately frozen same-evidence auxiliary.

The contract records a balanced paired order, the full planned denominator,
actual calls/tokens/cost/reserves and phase times. Hidden tests/rubric appear only
as opaque commitments and are excluded from generation by role/process design;
the JSON validator cannot enforce that exclusion. Final independent verification
is common to both arms, and in-generation validation attempts are separate.
Human or simulated proposals, unavailable verification, pending reviews and
identity drift cannot count as model repair gains. Review bindings include the
final output, candidate and verification receipt. Unknown cost/identity requires
stopping; no retry allowance is implied.

The example JSON is a placeholder development contract, not a frozen new task
pack or permission to run an experiment. Real evaluation still requires an
approved isolation backend, reviewed real provider integration and accounting,
independent unseen tasks/hidden scoring, exact input freezes and a new bounded
paid-run approval. No native execution admission is changed.
