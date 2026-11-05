# Real repair benchmark readiness

`repair-benchmark-inventory` inventories a locally supplied BugsInPy GitHub
recursive-tree index. It can check explicitly prepared, source-only repair tasks
through the normal `repair` loader. It does not download a checkout, run shell
scripts, import target modules, call a model, or enable a verification backend.

```sh
python3 -I -B cli/agent_review.py repair-benchmark-inventory \
  --index /path/to/pinned-tree.json \
  --revision 11c5f1eea954a42132cfd06bf257766a7963e0fd \
  --out /existing/private-directory/new-inventory.json
```

The index must declare `truncated=false`; duplicate paths and malformed indices
are rejected. The upstream commit, index's declared object SHA and canonical
index digest are distinct records. They bind supplied data, not independent
authentication of GitHub or proof that a caller supplied a complete index.
No bug is removed because a task is absent or unsupported. Missing metadata or
test-script paths are reported on that row.

A path-only projection must identify itself explicitly with
`schema="macr.bugsinpy.path-projection.v1"`, `kind="derived_path_projection"`,
the same `revision`, `raw_response_retained=false`, `truncated=false` and a
`tree` of `{path,type}` entries. It must not supply a `sha` field. Such a report
is labelled `caller_provided_derived_path_projection` with `tree_sha=null`;
it is not an archived API response or independently authenticated full index.

Optionally supply `--task-map /path/to/neutral-task-map.json`:

```json
{
  "black/1": {
    "repo": "/path/to/curated-source-only-snapshot",
    "task": "/path/to/neutral-task.json"
  }
}
```

Only explicitly mapped source/task paths are opened. The map must not contain
unknown benchmark IDs. Each mapped task is loaded by the same hash-bound
`load_task` used by `repair`; it is not a separate, looser compatibility check.
The report separates `input_compatible`, `input_unsupported` and `not_assessed`.
All runtime results remain uncalibrated. Input acceptance is neither successful
test collection nor a repair result. The output is a new private JSON file;
existing reports are never overwritten. The command prints only a summary.

## Fixed BugsInPy scope and input separation

The [official index at commit
11c5f1e](https://api.github.com/repos/soarsmu/BugsInPy/git/trees/11c5f1eea954a42132cfd06bf257766a7963e0fd?recursive=1)
was inspected on 2026-11-04: 501 bug metadata paths and 501 test-script paths
across 17 projects. This differs from the historical 493-bug figure. Use the
actual pinned index denominator; do not hardcode either number in the importer.
Without prepared source-only snapshots, all 501 inputs remain unassessed.
There is no measured full-benchmark repair compatibility or success rate yet.

An additional read-only source audit retrieved the declared fixed-revision test
files for all 501 bugs. Against the legacy unittest **test-subset** profile, 30
passed and 471 were blocked. This is not whole-task compatibility: implementation
snapshots, neutral issues, dependencies, collection and execution remain
unassessed. Per-bug blocker counts overlap: 270 had a file exceeding 16 KiB,
175 failed unittest declaration checks, 40 exceeded the AST budget and two were
rejected by the analysis interpreter's syntax checks. Aggregate test bytes/lines
can also exceed limits; do not add these counts to obtain a denominator.

The explicit [task-v2 pytest/nodeid profile](repair-pytest-input.md) was then
checked against the same complete fixed-test bytes. Literal official selectors
yielded 75 static plan accepts, 197 plan rejections and 229 cases whose selectors
were not assessed. Of the 75, 74 had been blocked by the legacy rule. Retaining
the old 30 yields a combined static lower bound of 104/501, not a strict
same-selector improvement experiment or whole-task execution rate. All 270
oversized-file cases remain blocked; selecting a method never truncates its
file. The 32 parameterized selections lacking a frozen case ID remain unsupported
by this narrow plan, not evidence of broken upstream tests.

Both test origins were read separately. Buggy test-file coverage was complete
for 429 bugs and incomplete for 72, including 31 abbreviated commit references
not resolved to full SHAs. Its probe states were 30 accepted, 411 unsupported
and 60 incomplete: 12 of the unsupported cases also lacked files, because known
blockers take precedence. Missing buggy-version tests do not invalidate the official fixed
test overlay. Lexical script inspection, including the old `py.test` executable
name, identified 375 pytest, 122 unittest and four other commands. This neither
executes nor validates those shell commands or proves selector equivalence.
All raw sources and per-case control metadata remain outside the repository.

Evaluator code can use `probe_test_sources(paths, sources, origin=...)` from
`macr.evals.bugsinpy_inventory`, with byte-valued sources keyed only by the listed
paths. Origin must be `fixed_revision_overlay` or
`buggy_revision_probe_only`. Missing and unsupported paths stay in the report;
even a static pass leaves the whole snapshot, selector equivalence and runtime
unassessed. This helper creates no task, imports no target module and performs
no network call.

BugsInPy metadata has no uniform issue/problem-statement field. A missing task
description must be supplied and frozen independently; do not derive a solver
hint from the reference patch. Never hand an unfiltered official checkout to a
model: [its checkout implementation](https://github.com/soarsmu/BugsInPy/blob/11c5f1eea954a42132cfd06bf257766a7963e0fd/framework/bin/bugsinpy-checkout)
defaults to the fixed revision, overlays tests from that revision even for a
buggy checkout, and copies metadata containing the fixed commit into the tree.

Keep the solver view to authorized buggy source, neutral task text and explicitly
public tests. Reference patches, fixed commit identifiers, scoring tests and
their preparation scripts belong to the evaluator. The inventory never opens
the contents of those upstream metadata, script or patch files. The older
`prepare-real-eval` retrieval dataset contains scoring oracles; its output is
not a neutral repair task and must not be substituted for this data boundary.

The benchmark repository has no explicit license file in the inspected revision;
the [open license question](https://github.com/soarsmu/BugsInPy/issues/94) must be
resolved separately from each target project's license. No benchmark sources,
patches or task data are bundled here. Public historical bugs are development
material with possible training contamination, not fresh blind holdouts.
License text was also checked at one fixed snapshot per project (bug 1), not
every benchmark revision. For example, the historical Keras sample is MIT while
current repository metadata reports Apache-2.0. Current metadata must not be
substituted for a historical snapshot's license; component terms may differ too.

## Current repair compatibility

The existing limits remain: 32 Python source files, 16 KiB per file, 64 KiB
total, 4,096 Python lines, 64 filesystem entries including directories, and
bounded parsed syntax. Legacy tasks require 1–8 public test files and 1–32
declared synchronous unittest methods. Explicit top-level `import unittest as ut`,
`from unittest import TestCase`, and `from unittest import TestCase as Case`
are now recognized. Conditional imports, inferred inheritance and rebound aliases
are not inferred; declared names do not attest runtime collection.
The 4,096-line limit is now checked by the common source validator before
parsing or provider/record creation, rather than being discovered only during
snapshot capture. Candidate edits use the same unchanged limit.

Each candidate may edit 1–3 **distinct files per proposal**. This is not a limit
of three editable files per task or three files across the complete repair loop.
Tests remain immutable. The inventory does not inspect a gold patch to guess
how many edits a solution needs.

Explicit v2 tasks can bind exact pytest nodeids; they use a distinct pytest
CheckSpec, not the old unittest selector. Arbitrary shell commands are still
unsupported and no real backend is enabled. Large snapshots, Python-version dependencies, native
extensions and missing runtime qualification must remain explicit limitations.
All three comparison arms retain the same permissions and budgets. This input
improvement establishes no model-quality gain and does not change prior results.
