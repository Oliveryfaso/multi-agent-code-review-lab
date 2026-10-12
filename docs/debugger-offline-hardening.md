# Debugger offline hardening

The original 24 outputs, holdout, prompts and accepted reviews remain unchanged.
See [results](local-model-holdout-results.json) and
[execution limits](local-model-holdout-observation.md). No new framework is added.

## Output evidence

Both models passed 4/12 diagnoses and 12/12 strict JSON. Contracts passed 4/12
for 4B and 11/12 for 7B. Only fifteen valid answers received semantic review.

4B had seven duplicate-reference rejections: c02/c03/c05/c06/c08/c09/c11;
c07 cited 5-10 outside source 1-9. These are application rejection reasons,
not JSON or string-length failures. Healthy 0/4 means four contract failures:
c02/c11 had raw healthy verdicts; c05/c08 reported defects while describing
correct behavior. Formal healthy-false-alarm fields are null. Do not convert
raw observations into semantic scores or deduplicate to rescue output.

7B healthy 0/4 means four valid false alarms: c02 invents a list-return obligation;
c05 invents dictionary-copy aliasing; c08 invents copied-input mutation;
c11 invents fallback from an empty separator. Elsewhere, c01 misses the upper
endpoint; c04 cites unavailable lines; c06 invents order loss and assumes an
unavailable predicate contract; c07 correctly identifies shared-default state;
c10 explains the percentage defect but declares healthy. c03/c09/c12 correctly
abstain for missing codec ordering, TTL unit and host index value/range.

The frozen generic GBNF restricts fields, verdict branches, types, counts and character productions.
It does not enforce unique references, legal paths, ordered/source-bounded ranges,
complete sentences, causal truth or verdict consistency. The host validator
remains authoritative and rejects invalid output without repair.

## Small implementation scope

Extend the existing `static_locate` response API with optional
`status="insufficient_evidence"`: `claim=null`, `trigger_conditions=[]`,
`locations=[]`, and nonempty `gaps` naming concrete missing evidence. Preserve
legacy candidates without status. Candidate locations contain exactly
`file/start/end`: legal relative file, true integer bounds, ordered source-contained
ranges, no duplicates. Insufficiency must not fabricate hypotheses or citations.
Pending and candidate are not semantic passes. The public baseline uses
`prepare_only`; the local cycle has a response caller. This adds no public
CLI response path.

Share a same-process lock between atomic writes and strict `tree_usage` scans,
preventing covered writes from replacing a temporary file between enumeration
and stat. Keep allocation accounting and invalid/missing-entry failures strict.
The comparison writer's JSON and Markdown capacity checks now call that shared
scanner. Complete local derived owner/binding sources also wrap their actual
`stage_usage` and `record_allocation` bodies; their existing callers, exclusions
and budgets remain connected. Historical frozen sources and grants are unchanged.
The lock excludes other processes and uncoordinated filesystem activity; it is
not a transaction or power-loss guarantee. The old recovery result remains
native success=false. A replacement race fits its ENOENT, but the historical
stack and exact scan/write sequence are missing. Its unique cause and whole-run
guard compliance remain unproven.

## Validation and next measurement

Synthetic fixtures cover legacy candidates, insufficiency, duplicate/out-of-range
locations, whitespace-only gaps and synchronized scans/writes through real comparison
entry points. Three isolated new development sources check duplicate rejection and
missing-evidence exits without promoting legal output to a semantic pass. They test
application behavior, not model quality or native guard compatibility.

The [next measurement plan](debugger-next-validation.md) freezes twelve future unseen
examples per model, separates format and semantic outcomes, and reports category
coverage and actual denominators. Neither the old holdout nor these development
fixtures may be used as the new independent evaluation set.

No retry, repair, verdict flip or case-specific rule is proposed. No model,
download or VM call is needed for these changes. Model gains, cross-process protection
and real native-service compatibility remain unvalidated.

The later [compact diagnostic loop](diagnostic-compact-loop.md) records its own
production-entry changes and frozen native measurements. Its first compact
candidate failed the explicit character contract; no verifier or independent
acceptance case was dispatched. The historical results above remain unchanged.

## Visible facts and the bounded 4B/7B comparison (2026-10-12)

The opt-in `fact_checks=True` path now sends the same source-bound syntax
observations to the production candidate and verifier. It records exact return
expressions, call arguments/direct scalars and simple early-return guards from
complete visible bodies. Guard observations describe necessary lexical conditions;
they do not prove runtime reachability, name values or dependency return guarantees.
Task/source/packet mismatches fail before dispatch. Default-off requests and the
v3 output schema remain unchanged; fact-enabled v2 is refused.

A frozen comparison used the same three seen development cases, candidate
messages/scope/schema, sampling and 1536-input/512-output/2048-context budgets,
with each verifier receiving its own model's original candidate. The two cached
CPU models ran sequentially with independent quality gates. Actual consumption
was **2 starts and 9 generations**, 9,325 input + 2,684 output = 12,009 tokens,
zero retries, downloads, VMs or external model API calls. Both processes, listeners
and monitor threads closed normally. Execution-assistant capacity resumptions
retained the same claim and counters; they were separate from local model quality.

| Development outcome | 4B | 7B |
| --- | --- | --- |
| Candidate contract parsing | 0/3 | 3/3 |
| Verifier contract parsing | 0/0 (unavailable) | 3/3 |
| Candidate label correctness | 0/0 (unavailable) | 2/3 |
| Complete correct candidates | 0/0 (unavailable) | 0/3 |
| Actual final delivery / end-to-end completion | 0/3 | 0/3 |
| Specific essential-gap abstention among parsed candidates | 0/0 | 0/1 |
| Candidate healthy false positives | 0/0 | 0/1 |
| Candidate defect label recall / complete witness | 0/0 | 1/1 / 0/1 |
| Fresh generations after own gate | 0 | 0 |
| Input / output tokens | 2,989 / 1,536 | 6,336 / 1,148 |
| Generation wire seconds | 189.963 | 453.615 |
| Service seconds including preparation and grading wait | 682.523 | 1,152.830 |
| Peak RSS, GiB | 5.02 | 7.42 |
| Start / minimum observed memory-free percentage | 82 / 68 | 76 / 42 |

All three 4B candidates reached the output limit and remained technical failures;
no verifier was called and no fragment received semantic credit. The 7B verifier
returned incomplete coverage on all three pairs, so the host preserved the original
candidates and withheld every final. Its healthy candidate incorrectly abstained;
its defect candidate blamed a correct protection branch rather than explaining the
swapped-argument violation. Verifier reasons also contained false branch claims.
Rejection therefore prevented publication but did not establish reliable correction.
A healthy false-positive count of zero does not demonstrate health recognition, and
a correct defect label does not establish a correct causal witness.

All rates conditioned on actual finals are `null` with denominator 0. Each planned
development cohort delivered 0/3 finals; each fresh cohort remained unattempted
after its own gate failure. Offline checks passed 359 distinct core tests and
77 production-module imports; the owner's 91 tests overlap that selection.
Historical scripted replay still accepted the same old outputs and retains its
old semantic grade. It is not native quality evidence.

The [sanitized metrics](local-model-fact-check-contrast-results.json) retain the
separate denominators and costs. These three seen cases do not establish general
model superiority or a measured fact-packet gain. Compared with the preceding
batch, packet and condensed instructions changed together. Neither model's
failed gate supports promoting this opt-in path or switching the default model.
The historical reader failure's unique cause remains unproved; the reviewed
derived reader completed this run without that error. No additional batch started.

The implementation borrows bounded output validation from
[Pydantic AI](https://pydantic.dev/docs/ai/core-concepts/output/#output-validators),
explicit failure classes from
[LangGraph](https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph),
and separate candidate validation from
[Anthropic's review command](https://github.com/anthropics/claude-code/blob/main/plugins/code-review/commands/code-review.md).
This batch keeps its own zero-retry limit and concrete-trigger criteria; it copies
no confidence threshold and adds no framework dependency.
