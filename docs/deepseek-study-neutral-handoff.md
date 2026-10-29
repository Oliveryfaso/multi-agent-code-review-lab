# Neutral package and custodian handoff

This is the delivery contract for an independent author/custodian of the
proposed nine-question [paired study](deepseek-paired-study.md). It creates no
questions, gold or authorization to call a model. The executable validators are
the authority; this repository does not provide a separate `.schema.json` file.

| Contract | Authority |
|---|---|
| Neutral package and exact inventory | [`load_neutral_pack`, `PACK_SCHEMA`, `TASK_FIELDS`](../src/macr/evals/deepseek_study_profile.py) |
| Shared text, relative paths and entrypoint | [`_task`, `_relative`](../src/macr/evals/deepseek_policy_manifest.py) |
| Custodian commitment and preparation | [`_custodian`, `prepare_study`](../src/macr/evals/deepseek_study_profile.py) |
| Later anonymous review and scoring merge | [`export_review_packets`, `_review_inputs`](../src/macr/evals/deepseek_study_report.py) |

## What the custodian delivers

Keep three separate sets of material:

| Material | Recipient and contents |
|---|---|
| Neutral package | Executor: `pack.json` and exactly its authorized Python source files under `snapshots/` |
| Handoff record | Executor: the five-field commitment below and a neutral file inventory with relative paths, byte sizes and SHA-256 digests |
| Private custody material | Custodian only: gold, specific rubric answers, task categories, counterexample proofs, reference tests, repairs and source/mechanism qualification records |

The executor must not receive or open private custody material, including to
recompute its hashes. Do not put it in the neutral package, trusted code
checkout, shared execution workspace, conversation attachments or execution
logs. The handoff must not contain private gold paths or a task-to-category,
answer or counterexample mapping. Keep detailed provenance and license records
with the custodian; the neutral task carries only its required declaration.

## `pack.json` contract

The top-level object has **exactly** these three fields. Duplicate JSON keys and
non-finite numbers are rejected. The file is at most 65,536 bytes.

| Field | Required value |
|---|---|
| `schema` | `macr.deepseek.study.pack.v1` |
| `pack_id` | String matching `[A-Za-z0-9][A-Za-z0-9_.-]{0,63}` |
| `tasks` | List of 1–9 task objects for development; the proposed study requires exactly nine independently qualified tasks |

Each task has **exactly** these ten fields; extra label or answer fields are
rejected:

| Field | Contract |
|---|---|
| `id` | Same identifier rule as `pack_id`; unique in the package |
| `project_id` | Same identifier rule; associates tasks with their project unit |
| `snapshot_root` | Unique safe relative path beginning `snapshots/`; use the simple convention `snapshots/<id>` |
| `question` | Nonblank text, trimmed length at most 4,000 characters; no NUL |
| `scenario` | Same text rule as `question`; state the observable scenario and allowed input domain |
| `assumptions` | List of at most 12 nonblank strings, each trimmed length at most 1,000 characters; no NUL |
| `authorized_files` | Unique list of 2–4 safe relative paths ending `.py`, relative to this task's `snapshot_root` |
| `initial_evidence_files` | Unique, nonempty subset of `authorized_files`; list order is part of the frozen input |
| `entrypoint` | Object with exactly `file` and `symbol`; `file` is initially visible; `symbol` is an ASCII Python identifier or dot-separated identifiers, at most 128 characters |
| `license` | Object with exactly `spdx` and `provenance`; both strings are nonblank with trimmed length at most 1,000 characters |

Use neutral IDs, filenames and wording. Do not encode categories, mechanisms,
expected verdicts or hints for a particular strategy in these fields. The
validator checks structure; it cannot establish that permitted prose or source
is free of answer leakage, or that a license/provenance declaration is true.
The custodian must check both before freezing the package.

Relative paths have no absolute prefix, backslashes, empty/dot/parent segments,
NUL or control characters. `_relative` bounds trimmed path length to 512
characters and excludes answer/test/private names and patterns, including
`gold`, `rubric`, `answer`, `expected`, `solution`, `repair`, `test`, credentials,
`.git`, `.env*`, `._*` and key files. Use short ASCII paths: runtime evidence
tools additionally cap a source-relative filename at 512 UTF-8 bytes. Consult
the validator for the complete forbidden set rather than treating these
examples as an exhaustive allowlist.

Each task's authorized source totals at most **12,288 bytes**, and its initial
source at most **4,096 bytes**. Deliver source as UTF-8 Python files accepted by
the existing snapshot reader. The package contains only `pack.json`, those
exact files and their necessary parent directories. No symlinks, extra files
or unrelated directories are accepted; inventory is bounded to 128 entries.
Do not add a package README, standalone LICENSE, tests, gold or commitment file
to the neutral root. Extra inventory is rejected before opening source files;
this name check does not prove source content is neutral.

## External commitment

Pass this record separately to `prepare_study` as the `custodian` object. There
is no gold-file path parameter. It has **exactly** five fields:

| Field | Required value |
|---|---|
| `schema` | `macr.deepseek.study.custodian.v1` |
| `version` | Nonblank string, trimmed length at most 2,000 characters |
| `gold_sha256` | Exactly 64 lowercase hexadecimal characters |
| `rubric_sha256` | Exactly 64 lowercase hexadecimal characters |
| `qualification_statement` | Nonblank string, trimmed length at most 2,000 characters; declare overall eligibility and review completion without per-task labels or answers |

The custodian freezes the exact private gold/rubric bytes and computes these
digests independently. Preserve those bytes and verify them again on the
custodian side before final scoring. The executor treats the digests as opaque
commitments. A correctly shaped digest or statement is not proof of correct
answers, independent authorship or completed qualification.

Before seeing any model result, the custodian must qualify three new project
units, each with one defect, one healthy and one genuinely external-evidence
task. Each category has one full-context and two retrieval tasks, for three
full-context and six retrieval tasks overall. Exclude already-seen project and
mechanism families, including renamed or constant-changed versions. An external
gap needs two environments compatible with the complete authorized snapshot
and task yet producing different conclusions; an unread authorized helper does
not establish that gap. Keep the category map and proofs private. Passing
`load_neutral_pack` does not certify any of these research qualifications.

## Execution and review access boundary

The current arrangement is **role separation plus a program-level reading
boundary**. Assign the executor only the neutral root, named neutral handoff
record, trusted implementation and its new control/output directory. The
trusted implementation is read to freeze its version; control/output is kept
outside the neutral package. Do not give the executor a browsing or discovery
task over the custodian's private directory.

At runtime, [`StaticEvidenceTools`](../src/macr/evals/deepseek_policy_tools.py)
reads task evidence only from the manifest-authorized files in that neutral
snapshot, verifies their frozen bytes, and exposes bounded `search`,
`read_symbol` and `read_caller` results. It does not execute target code or offer
a filesystem-wide search capability. The new study runner still rejects live
providers; neutral handoff and offline validation do not authorize API use.

Separate paths and owner-only modes such as directories `0700` and files `0600`
do **not** isolate agents running as the same OS user. Broader host tools may
still read other same-user paths. The runtime whitelist does not restrict those
tools, prove a private file is technically inaccessible, or attest that the
executor never saw it. OS-enforced read isolation would require a separately
verified restricted account/workspace/environment; none is claimed here.
Residual risks are accidental sharing, answer leakage in allowed content,
overbroad host access and unenforced human review order. Preserve and disclose
any exposure; do not call exposed material unseen.

For later review, send only anonymous `packets`; keep `keeper_mapping` with the
custodian. Reviewers lock their evidence-based judgments before the custodian
reveals gold and merges the mapped, hash-bound result/review records. The code
checks bindings, not the actual order or independence of human decisions.
Evidence can also reveal a strategy despite omitted arm/model/cost labels.
Specific categories and scoring must not flow back into generation.
