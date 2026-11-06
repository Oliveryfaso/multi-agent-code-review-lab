# Public verification backend interface

Repair can consume structured **public-test** results from an application-owned
`BackendVerifier`. The default CLI factory still returns `None`: no VM, container,
host subprocess, plugin, saved-result import or new execution permission is
enabled. The workbench's explicit Docker configuration path is described in the
[executor guide](docker-executor.md); its existence does not establish a qualified
live engine. Existing stopped native operations are not retried by this interface.

An embedding application may call `repair_entry.run(args,
verifier_factory=approved_factory)`. The side-effect-free factory must return
`BackendVerifier(backend, public_result_reader, backend_id=...,
execution_profile=...)` or `None`.
The backend implements the existing `ExecutionBackend` contract. Its owner must
separately establish execution authority, environment identity, isolation,
resource limits, unique dispatch and cleanup. An object, capability boolean,
opaque backend label or local hash is not evidence of those facts. There is
no backend selected by default, so unconfigured feedback CLI runs remain
`verification_unavailable`. The workbench may explicitly construct the concrete
Docker backend after separately approved metadata qualification. That backend
owns staging, resource policy, collection and verified container/volume cleanup;
it does not supply a general host subprocess or an automatic fallback around a refusal.

The explicit execution profile is a bounded opaque label for an already
configured backend policy; empty labels and `none` are rejected. It is bound
into each check and receipt. The backend may reject unsupported profiles. A
label does not create a permission, shell command or isolation configuration.

## One public check

The loop captures the complete candidate, including unchanged public tests,
and durably records an intent before dispatch. The public request binds the
backend descriptor, full `SnapshotRef`, normalized `CheckSpec`, content-derived
source ID and exact public test names. Numeric fields use decoded record types
before hashing, so persisted/reloaded requests have stable digests.

The bridge uses `TestRunnerTool.run_check` and its existing network-denial,
read-only-input, isolated-write, secret-exclusion and resource-limit capability
requirements. The normal remote path checks these side-effect-free capabilities
before Key input or spend-cell claim; each actual check rechecks the same gate.
This does not perform an extra baseline test. Direct self-review does not even
construct a verifier. Neither this bridge nor a factory grants execution rights.

After a completed backend result, `public_result_reader(request)` returns only:

```json
{
  "schema": "macr.repair.public-check-receipt.v1",
  "phase": "public",
  "request_sha256": "<digest of the exact request>",
  "observation": {
    "source_id": "<current complete source ID>",
    "execution_status": "completed",
    "outcomes": {"checks.py::Case.test_public": "passed"},
    "diagnostics": []
  }
}
```

Outcomes must cover exactly the frozen public names, each `passed` or `failed`.
Counts and exit status must agree with the normalized backend `CheckResult`;
missing/zero/skipped tests, truncated results and contradictory counts cannot
become a pass. Optional diagnostics use the existing bounded public format:
test ID, assertion/exception category, message and source-bound frames. They
cannot name an undisclosed test or out-of-view source file. The caller owns
redaction and the real public/hidden producer boundary; a structural allowlist
cannot prove that a producer never read hidden content or that text is truthful.

The bridge does not open stdout/stderr references, arbitrary receipt files or
scorer paths, and does not retain raw log references or exception text. The
reader cannot overwrite backend blocked/error/timeout/unknown results. Those
states do not invoke the reader. A dispatch exception or malformed result is
conservatively unknown; malformed/missing completed receipts are invalid.
There is no retry. Backend cleanup and stopping remain backend-owned: this
interface cannot infer that an unknown job stopped or safely kill it.

An unknown verification result is exported as `verification_unknown`, not
`not_attempted`, even with zero model requests. Its public receipt and attempt
count remain auditable; final hidden scoring stays unavailable. In the durable
application path the cell finishes as stopped and the same ledger remains
halted across later output directories/cells. Settled model usage remains
intact; no fictitious unknown model-cost reservation is added. This local halt
does not attest that an external job was stopped or cleaned up.

## Persistence and conclusions

The loop verifies snapshot integrity again before exposing feedback. It stores
the bounded request, categorical backend result, validated public receipt or
failure category. All proposal versions receive the public projection, not the
backend audit, local snapshot paths or hidden scoring records. Reopening through
`verify_loop` recomputes source, request, test and receipt links without invoking
the backend or reader. A partial/repeated output directory is not resumed.

Origin is `backend_reported_unattested`, never `synthetic_replay`. A public pass
may stop the Debugger arm as `public_tests_reported_passed`; the same-budget
public-feedback baseline can continue or retain its candidate as before.
`repaired` remains false. Actual target execution and execution authority are
`null` (not independently established), not assertions of either execution or
non-execution. The existing `simulation` field describes provider configuration,
not backend authenticity. Offline fixture backends returning these records do
not prove any target ran.

`score_handoff` keeps final verification `unavailable` and semantic review
pending even after a public pass. Hidden tests/rubrics remain with the independent
scorer. A baseline timeout stays in the planned denominator with zero model
requests; a historical public pass or retained candidate is not a fresh hidden
score. The no-feedback direct API baseline and model/spend limits are unchanged.

Focused offline checks (authored observations only):

```sh
python -I -S -B scripts/run_offline_tests.py test_repair_backend_verifier
```

These checks validate the interface and real CLI control flow with intercepted
transport and fixture results, not reproduction, isolation enforcement, model
quality or repair gain. A qualified authorized backend and original/reference/
masking calibration are still required before a real repair evaluation.
