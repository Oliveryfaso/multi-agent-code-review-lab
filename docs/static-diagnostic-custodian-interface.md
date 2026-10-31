# Static diagnostic: independent custodian interface

Version 1, 2026-10-31. This is an authoring and scoring interface, not live-run
authorization. The current offline profile is `static-diagnostic-v1`. Its R/E
comparison uses one request per arm, the same complete authorized source, task,
`diagnostic-compact-v3` schema and budget. E alone receives the computed static
packet. No forced read call or additional source is part of E.

## Neutral package: exact structure

The neutral root contains `pack.json` and only its declared source files. JSON
must have unique string keys, finite values and valid UTF-8. `pack.json` is at
most 65536 bytes. Its exact top-level keys are:

|Key|Type / requirement|
|---|---|
|`schema`|Literal `macr.static-diagnostic.pack.v1`|
|`pack_id`|String matching `[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`|
|`tasks`|Exactly 9 task objects; 3 projects with exactly 3 tasks each|

Each task has **exactly** the following ten legacy neutral fields plus
`entries`; no category, answer, expected verdict, witness or rubric field:

|Key|Type / requirement|
|---|---|
|`id`|Unique string matching the `pack_id` identifier pattern|
|`project_id`|String matching the same identifier pattern|
|`snapshot_root`|Unique safe relative directory under `snapshots/`|
|`question`|Nonblank string, no NUL; at most 4000 stripped characters and 4096 raw UTF-8 bytes|
|`scenario`|Same type and limits as `question`; explicit task facts are assumptions, not observed production data|
|`assumptions`|List of at most 12 nonblank strings, each no NUL, at most 1000 stripped characters and 4096 raw UTF-8 bytes|
|`authorized_files`|Unique list of 2–4 safe relative `.py` paths, relative to `snapshot_root`|
|`initial_evidence_files`|Unique list with exactly the same set as `authorized_files`; no dependency is withheld from R|
|`entrypoint`|Exact object `{"file": <authorized path>, "symbol": <qualified name>}`|
|`license`|Exact object `{"spdx": <string>, "provenance": <string>}`; both nonblank and at most 1000 stripped characters|
|`entries`|Unique nonempty list of at most 6 exact `file.py:qualified_name` strings; each at most 256 UTF-8 bytes|

`entrypoint` is retained for compatibility and must equal the file/symbol split
of `entries[0]`. Every entry file must be authorized. Qualified names are at most
128 characters and match `[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*` with ASCII semantics.
Entry declarations must resolve uniquely by exact file and qualified name in the
captured index. Unsupported computed facts are permitted; accidentally missing
or ambiguous seed declarations are not qualification substitutes.

The existing neutral loader has **no top-level `source` or `provenance` field**.
Source bytes live in declared files; authorship is `license.provenance`. Do not
add invented top-level fields. This version changes the pack schema and adds
`entries`; the new loader validates it explicitly, without changing the old
`macr.deepseek.study.pack.v1` loader or frozen studies. Use
[`static_diagnostic_pack.load_pack(root)`](../src/macr/evals/static_diagnostic_pack.py)
for quality input. Its separate `development=True` mode is restricted to the
three known public development cases and permits only their root README as
extra metadata; it is not the blind quality-package mode. Loading parses source
as data. Exact entry declaration resolution is checked later by preparation;
structural loading alone does not establish task quality or semantic correctness.

All source files must be nonempty, decodable, and completely citable using the
existing physical-line coordinate rules. Per task, their raw bytes sum to at
most 4096; all nine tasks together sum to at most 36864. These are raw-source
limits, not token estimates or whole-request byte counts. The preparation owner
also enforces the current profile's capsule, prepared-request and actual-wire
limits; it must reject oversize input rather than truncate R's source.

Paths follow the existing `_relative` contract in
[`deepseek_policy_manifest.py`](../src/macr/evals/deepseek_policy_manifest.py):
no absolute paths, links, traversal, backslashes, controls, secrets, answer/gold/
rubric/test/commit material or protected-directory components. No undeclared
file or directory is allowed in the neutral root. Handoff metadata, inventories,
validation records and opaque commitments go in a **separate neutral handoff**.

## Independent qualification and coverage

Privately qualify 3 defects, 3 healthy tasks and 3 genuine decisive external gaps.
R/E must each retain the fixed six-solvable/three-gap denominators. The distinction
is whether the supplied full source and explicit facts settle the requested
conclusion, not whether the collector emits a binding or an unknown marker.

Use entirely new mechanism families: exclude all earlier blind packs, author-known
comparison cases, paired variants and the three public development samples under
`examples/static-diagnostic-development/` (`keyword-defect`, `keyword-healthy`,
`dynamic`). Check overlap and bug-fix/variant families privately. Export only a
qualification declaration, not a case-family or category map.

Include local/import bindings and guard/dynamic/rebinding coverage, with at least:

- one healthy task whose computed binding is limited/dynamic but whose requested
  code property is nevertheless established by the complete source;
- one genuine external-gap task with a resolved static binding but a decisive
  external fact still absent;
- one defect whose computed observations are unsupported but whose complete
  source supplies a concrete counterexample.

These are cross-cutting requirements, not public task tags or predictable category
buckets. Do not force tool use, make capsule support a label oracle, or invent a
runtime observation. Static bindings remain assumptions about captured syntax,
not proved return values, runtime types or interprocedural data flow. A possible
incident is not evidence of the current external outcome. A necessary gap must
name the smallest facts that can change the asked conclusion and explain how.

## Separate opaque commitment and freeze handoff

Reuse the existing five-field custodian commitment, validated by `_custodian`
in [`deepseek_study_profile.py`](../src/macr/evals/deepseek_study_profile.py):

- `schema`: literal `macr.deepseek.study.custodian.v1`;
- `version`: nonblank string, at most 2000 stripped characters;
- `gold_sha256`: 64 lowercase hexadecimal characters;
- `rubric_sha256`: 64 lowercase hexadecimal characters;
- `qualification_statement`: nonblank string, at most 2000 stripped characters.

No other keys, private paths, gold bytes or category map are supplied. Original
synthetic source with an explicit license is preferred; do not imply verified
absence of training contamination. The qualification statement can attest the
balance, novelty and cross-cutting checks without associating cases with them.

The neutral handoff should freeze the package/commitment exact-file digests,
canonical pack digest, source inventory (relative paths, bytes and SHA256), total
bytes and validation outcome. Canonical digests use compact sorted UTF-8 JSON,
`ensure_ascii=False`, `allow_nan=False`, separators `,` and `:`, no trailing newline.
Label exact-byte hashes separately. Inventory must not include private file paths.
Keep gold, detailed rubric, witnesses and qualification/category maps outside both
neutral roots. Role/directory separation under one user is not OS isolation.

## Closed execution and anonymous scoring

No score returns to execution until the whole planned stage is closed: all 18 S1
cells are terminal or explicitly not attempted. A transport/budget/identity stop
retains unattempted rows; it does not shrink the denominator. Fresh raters receive
only anonymous output packets and the complete common source/task actually
available to both arms. They do not receive arm names, order, costs, computed
capsule, keeper mapping or executor interpretations before source-only judgments
are frozen. Packet content can still reveal strategy; do not claim full blinding.

Assess exact mechanism, trigger/domain, unchanged task requirements and cited
support. Matching verdict labels and contract-valid JSON are insufficient. A
partial answer never earns full-solution credit. Fact support or unknown markers
must not replace semantic review. The custodian then adjudicates against the
privately frozen rubric before locking exportable judgments. Detailed arguments,
answers, category associations and witnesses remain private. Exportable rationales
must be answer-free from the start; do not redact/rewrite entries after hashing.

### Exact anonymous score-import bundle

The new import contract is `macr.static-diagnostic.review-import.v1`, with exact
keys `schema`, `run_sha256`, `reviews`, `determinate_reviews`. `run_sha256` binds
the complete unchanged closed-stage result object, using canonical JSON above.
Both maps are keyed by anonymous `review_id`; their sets must equal the exported
reviewable packets. Nonreviewable failures receive no invented semantic record.

Each `reviews[review_id]` has exactly:

- `result_sha256`: canonical digest of the complete original cell result,
  including request/response/usage receipts, not just answer text;
- `status`: literal `locked`;
- `rationale`: nonblank answer-free string, at most 8000 characters;
- `flags`: exact seven **strict boolean** fields `full_solution`,
  `reasonable_abstention`, `partial`, `unnecessary_abstention`, `false_positive`,
  `false_release`, `arm_evidence_gap`.

`full_solution` cannot coexist with any other flag. `reasonable_abstention`
cannot coexist with `partial`, `unnecessary_abstention`, `false_positive` or
`false_release`. Other flag consistency must match the actual answer and the
private qualification: false positive is a definite defect verdict on a healthy
task; false release is a definite healthy verdict on a defect task. Absence of an
error flag is not full-task success. See the reusable flag checks in
[`deepseek_study_report.py`](../src/macr/evals/deepseek_study_report.py).

Each `determinate_reviews[review_id]` has exactly:

- `result_sha256`: same complete original result digest;
- `review_sha256`: canonical digest of that complete original locked review
  entry, including its unchanged rationale and flags;
- `status`: literal `locked`;
- `determinate_error`: strict boolean;
- `rationale`: nonblank answer-free string, at most 8000 characters.

The bit covers unsupported definite mechanisms, triggers, cited support or current
external outcomes, even when the verdict label matches. Unknown/abstention requires
false. Full solution cannot have true; false-positive/false-release or a definite
conclusion on a qualified genuine external gap requires true. Hashes bind original
entries, never a whole review file or later rewritten adjudication.

Freeze source-only judgments, custody adjudication, and all semantic/determinate
records **before** keeper joining. Only the keeper may replace outer anonymous
keys with cell IDs; original nested records and digest inputs stay unchanged.
The task qualification map stays in custody: entries keep exactly `category`
(`defect`, `healthy`, `external_gap`), `stratum` (`full_context`), `valid` (strict
boolean), `reason` (nonblank, <=8000 characters). If a final cell adjudication is
needed, retain the existing exact shape `result_sha256`, `review_sha256`, `flags`,
`rationale`. Neither private map is sent to execution or the parent for convenience.

## Aggregation boundary and interpretation

This new R/E experiment is an actual **18-cell, nine-pair** grid. Do not fabricate
a legacy 36-cell four-strategy study or call its adapter with a fake grid. The new
pure aggregation implementation must consume the new plan, original results,
locked score bundle and keeper's private qualifications with explicit version
checks. Its entry and validation commands must be confirmed against the actual
implementation before scoring import; this document does not claim that a new
live owner or aggregator is already implemented.

Report full-solution and necessary-abstention counts on all fixed task denominators,
contract/transport/identity/unknown failures, not-attempted/pending rows, conditional
reviewed quality, cost/tokens/preparation/model time, and per-pair comparability.
**Semantic wins/losses/ties require both outputs to be reviewable, independently
locked and valid for the same task.** A failed or unattempted arm makes that pair
semantically noncomparable; two failures are never an observed semantic tie.
A separate full-denominator delivery/disposition metric may assign failures zero,
but it must not be called a semantic or reasoning gain. At most nine pairs are
independent task observations; two arms/repeats are not new samples.

A future small study can support a bounded engineering decision, not a claim of
more than 10% general quality gain or statistical significance. Freeze any adoption
criteria before dispatch. No gain may be claimed from scripted responses alone.
R and E receive identical complete source; any observed effect would concern the
computation/presentation of that information, not extra private/runtime evidence.

Planning caps, **not approval**: a separate three-development-pair S0 is proposed
at <=6 requests/$0.50; only if every S0 delivery passes could a separately approved
nine-pair S1 run at <=18 requests/$1.00. Proposed total <=24 requests/$1.50, with
fresh separate ledgers and all prior unknown holds preserved. S0 failure blocks
S1. The proposed S1 delivery guard stops after the second known contract failure,
retaining all remaining not-attempted cells in the full 18-cell denominator.
Unknown outcome/usage, identity or budget/scope anomalies retain stricter immediate
stop rules. No new Key read, paid call, retry, replacement task or spending of old
unused authorization is granted by this interface or by authoring the package.
