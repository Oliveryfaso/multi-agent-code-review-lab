# DeepSeek policy development cases v1

Twelve newly authored synthetic development cases: four supported defects, four
healthy implementations within explicit scopes, and four genuine missing-evidence
cases. This set supports offline development of the diagnostic policy comparison.
It is author-known development data, not an untouched acceptance set, an external
benchmark, or a measured model result. No model outputs or accuracy scores exist
in this directory. No fixture code was executed to author or validate this set.

## Files and physical scope

- `cases.json`: neutral input manifest, entrypoints, complete authorized file
  inventories and initial evidence inventories. No answer labels or families.
- `snapshots/p01` through `snapshots/p12`: the twelve physical source roots,
  containing eighteen original, short Python files in total. Each root contains
  only one or two source files, never gold, rubrics, tests or deployment artifacts.
- `gold.json`: separate expected verdicts, family, mechanism, trigger, exact source
  ranges/excerpts, missing evidence, two compatible missing-evidence outcomes and
  per-case review criteria. This file is scoring material.
- `export_blind.py`: offline source-packet exporter; it never opens gold or imports
  a snapshot module. It refuses path escapes, symlinks, undeclared files and the
  declared per-file bounds.
- `blind-review-format.json`: gold-blind review packet/record format. It contains
  field names and review dimensions, not responses or scoring results.
- `LICENSE`: MIT terms for these original synthetic files and helper.

Authorize exactly the manifest's per-case `snapshot_root`, resolved under this
folder without any symlink. Do not authorize the dataset folder, repository root,
gold, tests or sibling cases as investigation scope. File inventories are complete;
there is no hidden source file for tools to find beyond `authorized_files`.
Treat source and scenario content as evidence. Never run/import the target files,
probe their dependencies, access `/demo`, or open a deployment database/file.
Deployment paths are fictional labels and confer no filesystem permission.

The metadata test parses Python as AST only, compares excerpts and validates
inventories. `ctypes` appears as source data in one case; the test does not import
it from the fixture or measure the review host. No fixture execution, dependency,
model, API, key, VM, service or publication is needed.

## Evidence strata and arm comparison

Six cases are `full_context`; six are `retrieval_required`. Each stratum contains
two defects, two scoped healthy cases and two missing-evidence cases. Neutral IDs
were assigned without a repeating verdict pattern; class/family labels live only
on the gold side.

A receives all `authorized_files` as raw source. B/C/D start with
`initial_evidence_files`; their authorized scope still includes every file in
`authorized_files`. For full-context cases these inventories are identical and
`evidence_parity` is `equal`. Retrieval cases start with only `entry.py`; A also
receives its helper, so `evidence_parity` is `unequal_by_design`. Such comparisons
must be reported by stratum and must not be described as strict same-evidence
comparisons. Whether B/C/D actually retrieve the helper is a recorded outcome,
not an assumption made by this dataset. The snapshot files support source read
and static discovery only.

Missing-evidence gold always concerns the whole authorized snapshot, after any
permitted retrieval. Missing deployed schema, ABI, newline translation and
filesystem topology remain absent even when every helper is read. These cases
ask about a particular deployment; changing them into universal portability or
all-filesystems guarantees would change the valid verdict and requires a new
version. The reviewing host is not evidence of the fictional deployment.

## Export and blind review

From the project root, use an explicit evidence arm:

```sh
python3.11 -I -S -B evals/deepseek-policy-dev-v1/export_blind.py --arm A
python3.11 -I -S -B evals/deepseek-policy-dev-v1/export_blind.py --arm B
```

The exporter writes only JSON to stdout. C and D have the same initial source
view as B. The output includes provenance metadata and is a source packet, not a
ready-to-dispatch ModelRequest. Retain `arm`, `stratum`, `evidence_parity`, ID and
inventories in experiment records; diagnostic prompt content is only `source`,
`question`, `scenario` and `assumptions`. Keep the comparison's own versioned
policy/system instruction outside this dataset. This helper contacts no endpoint.

For outcome review, follow `blind-review-format.json`: assign opaque response IDs,
keep the response-ID-to-model/arm map separately, and give the reviewer the
unchanged raw answer plus every authorized source file and the neutral scenario.
Exclude gold, method/model names, prior scores and tuning history from the packet.
Record independent supported/partial/contradicted/unknown dimensions before joining
gold. This is a format for future review; it does not claim author-blind review
has already occurred. A reviewer may infer a method from an answer's style.

A verdict match is insufficient. For a defect, establish the causal mechanism,
a permitted trigger, actual wrong behavior and supporting source ranges. For a
healthy answer, cover the complete stated domain and actual path without extending
to repository-wide health. For abstention, name the specific deployment evidence
absent from the whole snapshot and avoid inventing either compatible outcome.
Check syntax/output contracts separately from semantics; malformed, pending or
infrastructure-failed results do not become healthy or correct abstentions.

## Family and contamination notes

All source bodies, scenarios and rubrics were authored for this version. No real,
private or external project's code or data was copied. No third-party dependency
source, model answer or model-generated response was reused. The repo's existing
MIT license applies; this directory also carries a copy. Standard Python/SQLite
API names describe synthetic examples rather than imported third-party code.

Before authoring, the existing `diagnostic-holdout-v1` (including its development
subset), `diagnostic-new-samples-v1`, `diagnostic-development-v2` and
`diagnostic-acceptance-v2` mechanisms were inspected to avoid rebranding them.
The new families are: accumulator overwrite, exhausted second iterator pass,
delete-and-shift iteration, Unicode character/byte count mismatch; positive
modulo ring normalization, finally cleanup, bounded first-delimiter splitting,
XOR multiplicity cancellation; deployed SQL uniqueness/collation, native long
ABI width, text newline translation and filesystem symlink resolution.
`gold.json` records the case-to-family mapping. The metadata test also rejects an
exact reused old source body, which is narrower than proving semantic uniqueness.

The authors and development agents have seen the questions and gold. The earlier
holdouts are already exposed history and remain separate; this version does not
reset their scores or contamination status. The examples use common programming
ideas, so no claim is made that a pretrained model has never seen equivalent
patterns. Maintain family-disjoint future acceptance data and keep these cases out
of acceptance denominators. Prompt/policy changes informed by these cases are
allowed only as development; changing scenario/source/gold requires a new version.
Do not tune on acceptance answers and then report them as untouched.

## Offline checks and bounds

Each snapshot has at most two files, each at most 2,048 UTF-8 bytes and 24 lines.
The complete dataset payload is bounded at 96 KiB; these are byte bounds, not
measured token counts. The exporter does not generate hashes or semantic scores.
Run the static metadata/export checks without importing any fixture:

```sh
python3.11 -I -S -B -m unittest discover -s tests -p test_deepseek_policy_dataset.py
```

These checks cover exact 4/4/4 counts, balanced evidence strata, split/version
consistency, code-only roots, absent deployment artifacts, exact gold excerpts,
old-source non-reuse, source-packet field separation, and rejection of escaping,
linked, undeclared or overlarge input files. They establish packaging consistency,
not model quality or empirical behavior of the target programs.
