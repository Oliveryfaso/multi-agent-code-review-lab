# Whole-file scoring overlays

`repair-comparison stage-scoring` prepares evaluator-only source trees. It does
not collect tests, execute code, connect a verification backend, award credit or
authorize a run. Use an independently prepared scoring overlay; never give its
directory or the complete scoring plan to a generation provider.

Generation tasks v1–v3 retain their existing 16 KiB/file, 64 KiB/total and syntax
limits. Opt-in [task-v4 line windows](repair-windowed-source.md) retain complete
bounded larger originals while explicitly limiting the model-visible context.
Public-test feedback still uses the generation task's unchanged tests.
Large fixed-revision or hidden tests belong to the separate scoring layer. No
file is truncated, extracted into selected functions, rewritten or imported.

## Freeze before generation

The evaluator prepares a strict manifest, sorted by task ID and file path:

```json
{
  "schema": "macr.repair.scoring-overlay.v1",
  "tasks": [
    {
      "task_id": "development",
      "files": [
        {
          "path": "tests/test_regression.py",
          "bytes": 42000,
          "sha256": "<SHA-256 of the complete original file bytes>"
        }
      ]
    }
  ]
}
```

It must cover exactly the frozen comparison tasks (at most 32). Each task has
1–8 files, at most 1 MiB each and 8 MiB per task. Paths must be canonical,
nonsecret relative Python paths with no symlinks, case aliases or file/directory
collisions. The overlay may replace a protected public test or add a scoring
test; it cannot replace editable implementation or another original source file.

Before compiling the comparison, set `spec.scoring.hidden_tests_sha256` to
`macr.evals.repair_comparison.result_digest(manifest)`: SHA-256 of sorted-key,
compact UTF-8 canonical JSON, not the digest of pretty-printed manifest bytes.
The plan commits the same overlay for every arm. `generation_view` continues to
exclude scoring commitments, file references and contents. A local commitment
does not authenticate a custodian or prove that a caller never exposed a test.

Store the exact bytes at `<overlay-root>/<task_id>/<path>`. Only the selected
task's listed files are read. Other private scoring files are neither scanned
nor read. A changed size, digest, path or manifest is rejected, without silently
updating the frozen plan. Historical plans without this specific commitment
cannot be retrofitted by the staging command.

### Explicit path mappings

`macr.repair.scoring-overlay.v2` adds a required `declared_path` to every file
entry. `path` remains the canonical destination and sole read path. The original
declaration can be identical or differ only by repeated interior `/`, for example
`declared_path: "tests//test_regression.py"`, `path: "tests/test_regression.py"`.
Both strings must be explicitly supplied and are included in the frozen digest.
The stage records the original declaration; it never silently rewrites it.

Leading/trailing slashes, `.`/`..`, backslashes, case changes, Unicode changes and
other mappings are rejected. Two declarations cannot map to one destination.
The limit applies to both path strings. Reading, permissions, sorting, overrides
and collision checks still use only canonical `path`; file bytes never change.
This handles repository-relative duplicate separators, not arbitrary path aliases.
The declaration is caller metadata, not authenticated proof of upstream origin.

The v1 schema/fields remain unchanged. V2 requires a new manifest commitment and
therefore a newly frozen comparison plan; it cannot be applied to an old plan
without rejection. This mapping does not normalize public test selectors or
establish collection or runtime compatibility.

## Prepare a pending evaluator bundle

From the normal checkout entry (an installed `agent-review` exposes the same
subcommand; packaging that console script is not validated by these tests):

```sh
python3 -I -B cli/agent_review.py repair-comparison stage-scoring \
  --plan comparison/plan.json --record runs/cell-record --cell CELL_ID \
  --overlay-manifest scoring/overlay.json --overlay-root scoring/files \
  --out scoring/staged-cell
```

The output must be a fresh directory, separate from all input records, files and
overlay roots. The command first revalidates the saved repair record and its
candidate against the comparison. It then reads and verifies the whole overlay
before claiming the output. It creates:

- `original/`: the original generation snapshot with the scoring overlay.
- `candidate/`: the exact retained candidate with the same overlay, only when a
  candidate actually exists. A v6 historical selection uses the selected bytes,
  not the last attempted edit.
- `packet.json` and `results.json`: the existing pending handoff and result,
  unchanged, including failures, missing verification, costs and provenance.
- `overlay-manifest.json` and `staging.json`: overlay commitments, explicit test
  replacements, complete raw-file inventories and separate staged identities.

`original/` means the buggy/original snapshot, not a direct-API answer. A
missing candidate stays missing. Simulated proposals remain labelled simulated.
Old public-test passes, selected-candidate receipts and their generation source
IDs are not verification of either overlaid tree. Staging retains zero scoring
credit and `execution_status: not_performed`. Collection, scoring selectors,
dependencies, isolation and runtime results remain unassessed. The generation
test plan is not automatically reused as a scoring selector.

Directories and files are private local artifacts (0700/0600). A partial write
remains failed and cannot be resumed or overwritten. `staging.json` is written
last after byte readback; it is a local integrity record, not an execution or
pass receipt. There is no saved-bundle execution/import command in this change;
any future consumer must revalidate all commitments and file bytes.

## Capacity is a separate metric

The whole-file storage limit can cover a large test without making the full
repair task supported. Implementation source limits, test selection, fixtures,
plugins, target Python, project reconstruction and a permitted isolated backend
are separate requirements. Unlisted helper files and dependencies must still be
qualified; preserving listed file bytes does not establish environment fidelity.
Static capacity counts must keep all tasks, missing files and rejected paths in
their denominator. They are neither a repair rate nor a model-quality gain.
