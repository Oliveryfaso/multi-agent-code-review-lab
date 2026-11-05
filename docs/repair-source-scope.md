# Declared source files from a real checkout

The normal `repair` command accepts `macr.repair.task.v3` to read an explicit
source view from a larger repository. A README, unrelated Python file or vendor
tree no longer forces a caller to copy the entire checkout into the model view
or create a separate curated source directory first.

When the relevant files are not known yet, [repair-locate](repair-localization.md)
can propose a hash-bound view from a separately authorized discovery scope.
The caller still confirms editable and public-test roles before repair.

```json
{
  "schema": "macr.repair.task.v3",
  "task_id": "development",
  "issue": "A public, independently supplied issue description.",
  "source_scope": "declared_files",
  "source_files": {
    "src/logic.py": "<SHA-256 of the complete file bytes>",
    "tests/public.py": "<SHA-256 of the complete public test bytes>"
  },
  "editable_paths": ["src/logic.py"],
  "test_paths": ["tests/public.py"],
  "test_plan": null
}
```

V3 requires both `source_scope: "declared_files"` and `test_plan`. Explicit null
selects the existing unittest declaration path. A non-null plan must satisfy the
existing strict pytest [test-plan contract](repair-pytest-input.md). Invalid
plans never fall back to unittest. V1/V2 keep their closed source-inventory
behavior; V2 still rejects null plans. There is no automatic schema upgrade.

Use the existing entry; no new execution command is introduced:

```sh
python3 -I -B cli/agent_review.py repair \
  --repo /path/to/checkout --task task.json --model-config model.json \
  --out /separate/private-parent/new-record --proposal-contract v6
```

## Reading and snapshot behavior

The loader validates the complete manifest before reading any source: nonempty
SHA-256 commitments, canonical relative Python paths, existing secret/private
exclusions, Unicode/case/file-directory collisions and the internal 64-entry
snapshot budget. It does not inventory the raw checkout. Anchored, no-follow
reads open only the declared files and check their complete bytes and hashes.
The same commitments are checked again immediately before loop capture. A
mismatch at either read stops before provider dispatch or record creation.

The existing source bounds remain: 32 files, 16 KiB/file, 64 KiB total, 4,096
lines, per-file AST size/depth and compilation validity. No file is truncated or
split. Editable/protected-file authority remains unchanged. Output must remain
outside the checkout, including case/Unicode aliases.

The loop writes those selected bytes into its owned `sources/N` trees and uses
the existing snapshot/index/check path there. Internal records still have closed
inventories; this feature does not make extra files in a saved record acceptable.
It never snapshots a partially filtered raw checkout or imports its code.

## An explicit partial view

Every scoped request, report, CLI summary and pending scoring packet carries:

```json
{
  "source_view": {
    "mode": "declared_files",
    "coverage": "partial_repository",
    "unlisted_files": "not_read",
    "dependency_completeness": "not_verified"
  }
}
```

The model is told not to infer that unlisted files are absent. Scope is also
bound into common context and task/spend commitments, so an unlabelled complete
snapshot context cannot be substituted. All three strategies receive the same
authorized source view and normal limits. In comparison specifications, full
source means all bytes of the declared task snapshot, not the entire checkout;
the partial-view marker remains explicit.

The caller supplies the issue and source selection; this command does not infer
them from a reference patch, expand dependency imports, read unlisted metadata,
prove dependency completeness or measure full-repository repair compatibility.
Separate scoring overlays remain evaluator-only and do not enter this view.

Verification defaults remain blocked. Synthetic replay is still synthetic;
without a qualified verifier, feedback strategies stop before any remote cell
or credential is opened. This input improvement does not authorize model
network access, target execution, a VM/container or additional data transfer,
and does not demonstrate an actual repair or model-quality gain.
