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
