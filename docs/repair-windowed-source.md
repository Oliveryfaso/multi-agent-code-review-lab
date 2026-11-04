Windowed repair keeps complete committed originals locally while showing the
model an explicit partial view. It is opt-in through task v4; existing task
v1–v3 limits and requests remain unchanged. It grants no execution permission.

Start from a task-v3 declaration, change `schema` to `macr.repair.task.v4`, and
add a `source_windows` selection such as this illustrative field:

```json
{
  "schema": "macr.repair.source-windows.v1",
  "files": {
    "package/module.py": [{"start_line": 40, "end_line": 65}],
    "checks.py": [{"start_line": 1, "end_line": 12}]
  }
}
```

Keep `source_scope: declared_files`, complete `source_files` hashes, explicit
editable/public-test paths and the existing `test_plan` field. The normal
`repair` command accepts this task without another provider or runner. Remote
access stays disabled unless the existing explicit spend/transport gates pass;
an unavailable verifier still stops feedback strategies honestly.

Ranges are inclusive **current snapshot** physical line numbers, not permanent
original-file anchors. Each selected path has 1–8 sorted, nonoverlapping,
nonadjacent ranges. There are at most 32 windows, 256 shown lines and 16 KiB of
raw shown UTF-8 text in total. LF, CRLF and CR are retained exactly; line numbers
are separate metadata, never prepended to the source text. Every editable file
needs a selected window. Protected public test files must be fully shown and
still satisfy the legacy 16 KiB/AST public-test contract. Read-only helpers may
be omitted from model text, with that omission declared.

Task v4 admits at most 32 complete originals, 128 KiB/file, 512 KiB total and
16,384 physical lines. Full files are parsed and compiled without execution,
with 32,768 AST nodes/file and depth 64. The legacy defaults remain 16 KiB/file,
64 KiB total, 4,096 lines, 2,048 AST nodes and depth 32. View/request/history and
scoring record limits still apply independently; these source bounds do not
guarantee every input fits every later budget.

In a windowed model request `files` is empty. `source_windows` contains a
projection for every declared original: its complete current hash/byte/line
counts, displayed ranges with exact text and line numbers, and omitted ranges.
Omitted text is not sent or represented as a shortened complete file. The
complete source inventory still determines `base_content_id`; snapshots,
accepted candidates, saved-candidate selection and scoring use complete bytes.

The existing proposal contract and `{file, old, new}` edits remain in force.
With `source-windows.v1`, each `old` must fit entirely within one shown window
and be unique in the complete current file. The 16 KiB old/new text limit does **not** expand with
the original-file limit. Replacements apply to complete files; all other bytes
and protected tests remain intact. The complete changed source must compile,
and the next current-line projection must remain valid and within its budget.
No range is silently moved, clamped or expanded after an insertion/deletion.
This checks local integrity and syntax, not semantic repair correctness.

## Opt-in matching of a unique displayed occurrence

Task v4 can explicitly select `macr.repair.source-windows.v2` in the nested
`source_windows.schema`, keeping the same strict `schema`/`files` fields and
range budgets. This selects a different edit-matching policy; ordinary v1
selections, advisory preparation defaults and existing requests stay unchanged.

V2 exposes `macr.repair.source-window-projection.v2` with the fixed
`edit_policy: unique_shown_span`. An `old` is accepted only if it has exactly
one wholly contained occurrence across that file's separate displayed windows.
Overlapping or multiple displayed matches are rejected. A span crossing an
omitted boundary is not an eligible match, and windows are never concatenated.
Copies in omitted source do not make an otherwise unique displayed match
ambiguous. The editor derives the exact byte span from the current raw physical
lines and replaces only that span in the complete committed file, preserving
hidden copies and all other bytes. All normal edit authority, syntax, public
test protection and next-projection checks still apply.

This helps when the entire usable displayed context is duplicated elsewhere;
many shorter duplicate snippets can already be disambiguated in v1 by including
more unique context within the shown window. V2 does not infer which occurrence
is a bug or reveal missing context. Its policy and schema are bound into the
projection/context, task/spend scope, serialized request and replay/scoring.
An old scope cannot be relabelled as v2. All comparison arms use the same
explicitly selected matching policy and model budgets. No new model call,
source access or execution authority is granted, and no semantic quality gain
is established by deterministic patch application.

All three comparison arms receive the same initial issue, window projection,
complete-source identity and model/call/token/money limits. The context/spend
commitments bind the view. The existing feedback and stopping-policy differences
remain. Scoring replays the same visible-edit restrictions and exports/stages
complete originals/candidates with unchanged independent test overlays; it does
not award credit for unrun or synthetic checks.

For advisory preparation from an authorized Python discovery root:

```sh
python3 -I -B cli/agent_review.py repair-locate \
  --repo /ABS/authorized-source --issue-file /ABS/issue.txt \
  --windowed-input --out /ABS/new-private-output
```

This adds `windowed_source` with full commitments, a bounded selection and its
projection. Source positions are lexical/static hints; file-only hits may use
explicitly labelled header context. Budget omissions remain gaps. The existing
strict `source_files` field/status is preserved, so it can still be null while
a windowed proposal exists. Public tests and edit authority must be declared
separately; `task_ready` stays false. No model, target code, test collection or
native execution runs during preparation.
