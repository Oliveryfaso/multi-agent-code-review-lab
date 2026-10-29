# Proposed paired retrieval study (offline development only)

This version implements contract fixtures for a proposed study of **9 unseen
questions, 36 cells and at most 108 requests**. No unseen questions or answers
are shipped. The nine generated development fixtures deliberately repeat a
known mechanism; they prove execution/accounting contracts, not quality or
generalization. Remote dispatch is refused even when a provider is configured
for live access. A separate [activation candidate](deepseek-study-live-candidate.md)
requires a new exact-scope approval and explicit enablement; no live
authorization is included.

A separately approved nine-task execution and frozen custodian review are now
summarized in the [descriptive results](deepseek-neutral-study-results-20261029.md).
Those aggregate observations retain all failures and the two-segment identity
limitation; they do not turn the fixtures below into measured quality evidence.

A separately selected [offline output profile v2](deepseek-study-output-profile-v2.md)
adds an 8192-token total generation ceiling and terminal/evidence fixtures.
The default protocol below and frozen results retain their original limits.

| Strategy | Evidence and process | Request ceiling |
|---|---|---:|
| `direct_initial_3` (A-I) | Initial evidence, allowed-domain self-check, final diagnosis | 3 |
| `fixed_read_3` (F) | Same initial evidence; two frozen supplement groups before checks | 3 |
| `adaptive_read_3` (D) | Same initial evidence; at most two reads per first two decisions; third must terminate | 3 |
| `direct_full_3` (A-FULL) | Initial evidence plus remaining authorized source; same self-check process | 3 |

The first three share identical E0 bytes and order, including static facts from
initial source and the authorized filename list. Tool declarations and control
instructions differ. F follows a deterministic two-layer breadth-first AST
call/import traversal from the neutral entrypoint, sorted by distance, path
and symbol, with at most four symbols. Dynamic or ambiguous relations remain
unresolved. F's host evidence uses the same static tool results and truncation
as D; it does not fabricate model tool calls. Index preparation, scanned bytes,
read actions and timings are recorded. This is scoped static reading, not an
OS sandbox or runtime execution.

A-I/F/A-FULL always require three valid responses; an invalid first answer
fails the cell immediately. A-I/A-FULL preserve their first answer as an
auxiliary observation of that same cell. It is neither an extra sample nor an
answer selected retrospectively. D may terminate early. Every request allows
2,000 output tokens, every cell 6,000 including reasoning, four tool reads
(zero for either direct strategy), and a 180-second dispatch deadline. Input
plans are 8,000/request and 24,000/cell, checked against received usage; they
are **not enforced pre-dispatch token caps**. Equal ceilings do not imply
equal cost, computation or actual evidence. A-FULL is an information reference,
not a guaranteed accuracy upper bound. D–F is the primary comparison, D–A-I
secondary; neither alone identifies a pure tool-function causal effect.

## Reproduce without credentials or network

From a checkout, choose a new output directory outside the repository:

```sh
python3 -I -S -B scripts/run_deepseek_study_dryrun.py --out /tmp/debugger-study-fixture
python3 -I -S -B scripts/run_offline_tests.py test_deepseek_study_profile test_deepseek_study_plan test_deepseek_study_runner test_deepseek_study_report test_deepseek_study_batch
```

The normal scripted matrix makes 90 fake requests (three per direct/fixed
cell, one per adaptive cell), leaves semantic review pending, and writes
exclusive per-request artifacts, accounted cell results, a paired report and
a summary. Reusing an output or copied control directory is rejected. No
automatic retry or resume exists. The guard remains 120 seconds/64 MiB for
offline tests. Lower simulated limits, for example `--max-requests 1`, exercise
stopping and retain every unattempted planned slot.

The default **$2/108-request configuration is a proposal and simulated local
budget**, not a live authorization or provider hard cap. Every fake request
first persists a $0.33 reservation; trusted usage settles at the frozen
planning rates, unknown usage retains the reservation and stops the batch.
Known output-contract failures are counted and advance without retries;
identity, budget, scope, infrastructure and unknown failures stop the batch.
Accounting separates known accrual, held reservations, cache usage and standard
full-miss sensitivity. Rates must be verified and frozen again before any
future live approval. This profile never opens, extends, resets or releases
the historical authorization/ledger or its unresolved reservations.

## Neutral package for an independent author

The [neutral package and custodian handoff](deepseek-study-neutral-handoff.md)
documents the exact validator fields and limits, delivery responsibilities and
remaining access risks. The execution side receives no gold or concrete rubric.

An execution package contains **only** `pack.json` and the exact authorized
Python files under `snapshots/`. Extra files (including gold, labels, tests,
repairs, credentials or symlinks) are rejected by name before source reads.
No answer-file path is accepted. Each task declares 2–4 short files, at most
12 KiB total and 4 KiB initially visible. The pack allows up to nine tasks for
development; eligibility for the proposed nine-question study is verified by
the independent custodian, never inferred by this runner.

```json
{
  "schema": "macr.deepseek.study.pack.v1",
  "pack_id": "neutral-package-version",
  "tasks": [{
    "id": "case01", "project_id": "project01",
    "snapshot_root": "snapshots/case01",
    "question": "Neutral diagnostic question",
    "scenario": "Observable behavior and allowed input domain",
    "assumptions": ["Explicit domain assumption"],
    "authorized_files": ["entry.py", "helper.py"],
    "initial_evidence_files": ["entry.py"],
    "entrypoint": {"file": "entry.py", "symbol": "run"},
    "license": {"spdx": "CC0-1.0", "provenance": "Author and source declaration"}
  }]
}
```

The control-plane preparation function additionally accepts an external
custodian record with schema `macr.deepseek.study.custodian.v1`, version,
`gold_sha256`, `rubric_sha256` and a qualification statement. These are opaque
commitments, not proof the answers have been independently checked. The
execution developer must not request or read their preimages. Preparation
freezes source, implementation, model, prompt, fixed plans and seeded balanced
strategy order, interleaved across projects. Gold is never loaded to compute
its digest. Sources and commitments must be finalized before seeing results.

The custodian keeps gold, labels, reference tests, repairs, mechanism-family
records and counterexample proofs **outside this package and the execution
agent's assigned read scope**. Shared-host paths alone cannot guarantee blind
access isolation. Future authors must qualify three genuinely new projects,
one defect/healthy/external-evidence task per project, six retrieval and three
full-information cases, excluding the already-seen mechanism families. A true
external gap requires two compatible environments with different answers; an
unread authorized helper is not such a gap. No future gold is generated here.

## Review and reporting boundary

`export_review_packets` creates anonymous answer/evidence packets plus a
separate keeper-only ID mapping. Keep the mapping from reviewers. Packets
omit strategy, model, cost and labels; evidence can still reveal the strategy,
so this is hidden-label review rather than guaranteed unguessability. Reviewers
lock mechanism/domain/citation/gap judgments before the custodian merges gold.
Result and review hashes bind the later decision; contract acceptance alone
never earns a semantic pass.

The report starts from all planned cells, including failures, unknowns and
unattempted work. Without custodian categories and final decisions, semantic
scores remain pending. After independent merge, report full solutions/6
solvable tasks, full solutions/9, reasonable abstentions/3 external-gap tasks,
and the separate composite disposition/9; expose false positives/releases,
unnecessary/arm-evidence abstentions, partial answers, conditional reviewable
quality and pairwise D–F/D–A-I outcomes with costs and latency. Invalid tasks
retain the original denominator and reason; do not replace them after results.
Three projects cannot support general significance or broad generalization.

Planned-denominator zero completion is distinct from an observed paired tie.
Unattempted, unknown and infrastructure-failed pairs are `not_comparable`;
unreviewed answers remain `pending`. Comparisons require two reviewed outcomes
or known contract failures. `pair_counts` keeps all planned pairs and splits
complete/incomplete pairs, independently reviewed quality pairs and known
contract-failure disposition pairs. The latter are operational comparisons.
Final false-positive flags must match a healthy task answered
`supported_defect`; false-release flags must match a defect task answered
`no_defect_supported`. Contradictory or omitted flags are rejected, including
definite error flags on abstentions. Matching a verdict never grants a complete
semantic solution without independent mechanism, domain and citation review.

Before a future live run, a separate reviewed interface and user approval must
name the exact neutral/source file hashes, A-FULL's complete-source scope,
prompt/schema/tool and conversation history, official model/endpoint/thinking,
new request and money authorization, historical unknown responsibility, and
private log location/access/retention. Exclude gold, private projects/config,
credentials, old logs and third-party review/search services. This document,
the neutral package and dummy commitments do not authorize that run.
