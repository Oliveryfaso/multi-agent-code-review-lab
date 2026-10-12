# Compact diagnostic loop

The diagnostic API retrieves source once, generates one candidate, verifies that
candidate once, and lets the host assemble an accepted final report. Preparation
does not call a model. Generic question and patch workflows retain their existing
behavior.

```python
from macr.agents.orchestrator import Orchestrator
from macr.providers.base import ModelBudget

trace = Orchestrator(provider=local_provider).run_diagnostic(
    repository, question,
    scenario="The concrete caller and allowed input domain being investigated.",
    assumptions=[], search_terms=["function_name"],
    budget=ModelBudget(1536, 512, 300),
)
```

`prepare_diagnostic`, `diagnostic_candidate` and `verify_diagnostic` expose the
same stages separately. A persisted trace contains the source table, task scope,
budget, requests, attempts, candidate, verification and optional final report.
No automatic model retry occurs in this diagnostic path. A technical failure,
rejected candidate, supported defect, supported healthy scenario and essential
evidence gap remain separate states.

The current `diagnostic-compact-v2` candidate uses at most three short reference
IDs; each verifier check still uses at most two. The host restores the
exact file, start/end lines, symbol and source tool from a table bound to the
case, attempt and source bytes. Source readers capture complete lines, recalculate
clipped ranges, and keep empty or oversized first lines as noncitable locators.
Old attempts, foreign IDs and conflicting sources are rejected. Neither multiple
tools at one location nor confidence supplied by a model increases authority.

The verifier receives the five keyed checks, their permitted enum values,
necessary citations and text/array limits. It checks requirement, trigger,
behavior, protection and counterevidence. Host validation detects contradictory
states and missing references; it does not establish whether a causal explanation
is true. The mandatory `coverage: complete|incomplete` checks the explicit task,
including caller path, argument propagation, actual and required results, the
stated input domain, or an essential missing dependency. Incomplete coverage
rejects publication as `verification_task_incomplete`; the verifier cannot add
missing facts to the original candidate. A complete coverage rating remains a
model judgment, not semantic proof. Rejecting a defect does not produce a healthy
report.

Cause/when lengths of 160/80 characters and each unknown of 80 are preferred
targets. Hard limits are 600/300/200, matching the existing diagnostic parser.
Exceeding a preferred target records a trace quality warning and preserves the
original text for verification. Two unknowns, the verifier's 96-character reason,
8192-byte parsing bound, source binding and explicit token/context budgets remain
hard limits. No truncation, verdict repair or additional generation occurs.

For the confirmed llama.cpp `b11521` backend, a dedicated diagnostic provider can
opt in with `LocalHTTPProvider(profile, diagnostic_schema_decoding=True, ...)`.
The default remains disabled. The option requires the declared runtime/version
and JSON capability; only compact candidate and verifier routes receive their
original nonempty object schema as
`response_format={"type": "json_schema", "json_schema": {"schema": request.response_schema}}`.
Other routes retain their generic JSON behavior. Unsupported declarations or
invalid diagnostic routing fail before dispatch, without a generic-mode retry.

The fixed parser reads that nested `schema` value. Its converter implements the
current protocol's object, nullable type, enum/const, required/property, closed
object, string-length and array-length constraints. These are runtime decoding
constraints, not a replacement for source binding, per-state citation requirements,
cross-field validation or semantic review. Neither prompts nor diagnostic limits
change with this option. The fixed-version sources are
[the request parser](https://github.com/ggml-org/llama.cpp/blob/b11521/tools/server/server-common.cpp)
and [the schema converter](https://github.com/ggml-org/llama.cpp/blob/b11521/common/json-schema-to-grammar.cpp).

`ModelBudget` applies explicit caller limits without changing generic request
defaults. `LocalHTTPProvider` retains a validated response envelope when a model
returns incomplete output, invalid JSON or the wrong top-level shape. Known raw
output, usage and finish reason remain available through `ProviderError.response`;
a timeout without a response leaves usage unknown. Nested business contracts
remain caller owned.

The implementation borrows bounded validation and complete-output handling from
[Pydantic AI output validators](https://pydantic.dev/docs/ai/core-concepts/output/#output-validators),
typed failure/state handling from
[LangGraph's error guidance](https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph#handle-errors-appropriately),
and candidate-by-candidate verification from
[Anthropic's code review command](https://github.com/anthropics/claude-code/blob/main/plugins/code-review/commands/code-review.md).
It reuses the existing provider, evidence store and trace persistence; no framework
migration or dependency was added.

## Validation scope

Offline production-provider replay and integration checks passed. They cover
admission before dispatch, trusted failed response recording, exact source
binding, candidate/verifier stage limits, rejection without verdict changes,
and existing generic recovery behavior. Scripted responses establish application
behavior, not model diagnostic quality.

## Historical v1 comparison

The first native comparison used one cached 4B CPU service and a maximum of 15 dispatches:
three old/compact development pairs, three reused-candidate verifiers, and three
independent candidate/verifier pairs. Both interfaces share input 1536, output
512, context 2048 and timeout 300 seconds. The same model, template, sampling and
source bodies are used. This compares interface schemes; changed instructions,
schema and reference representation prevent a compression-only causal claim.

Before generation, the owned server template/tokenizer checks every prepared
request and representative complete outputs. Development contract completion
and independent semantic review must both pass before acceptance dispatches.
The sealed rubric never enters the model pipeline. Any failed stage stops further
dispatches; unattempted cases retain their fixed denominators.

## Historical v1 native result: 2026-10-11

The frozen batch stopped at the first compact candidate contract failure. It used
one service start and two generation dispatches. The owned process and listener
were closed; an independent OS process check found no remaining model or runner.
The native supervisor's `success=true` records successful guarded execution and
closure. The diagnostic trace records failure and no final report.

| Measurement | Old Solver | Compact candidate |
| --- | --- | --- |
| Input / output tokens | 716 / 324 | 658 / 128 |
| Finish reason | `stop` | `stop` |
| JSON object | valid | valid |
| Complete business contract | valid | rejected |
| Final diagnostic report | not this API | none |

The compact response identified the OR/AND discrepancy and supplied `(True,
False)` as its trigger, but its cause contained 198 characters against the frozen
160 limit and its trigger explanation contained 127 against 80. The host rejected
it as `compact_contract_invalid`. Its scope and reference ID matched the source.
The response was not truncated and did not exhaust the 512-token budget. The
server's JSON-object mode does not enforce the full nested business contract or
these character limits. The contract and raw output were not relaxed or repaired
after observing this failure.

Before dispatch, all 15 prepared request shapes tokenized to 611–802 tokens
against the 1536-input limit. Complete illustrative candidates used 49–73 tokens,
and verifier examples 81–82, against the 512-output allowance. This establishes
capacity for those examples, not compliance by generated answers.

The three-case development plan produced one attempted compact candidate and two
unattempted candidates. Accepted candidate contracts were 0/1 attempted; final
diagnostic reports were 0/3 planned. No verifier or independent acceptance case
was dispatched. Healthy false alarms, final defect recall, correct abstention,
and independent end-to-end quality remain unmeasured. Raw causal correctness is
reported separately from a published, verified finding.

The two measured calls used 1374 input and 452 output tokens in total, with no
cache reuse. Guarded service time was 65.84 seconds, including startup, capacity
checks and cleanup; generation wire times were 34.96 and 22.08 seconds. Peak
process RSS was 5,399,166,976 bytes, below 5.5 GiB. System availability was 77%
at startup and at least 75% during sampling. There were no new VMs, downloads,
paid/external model calls or retries. Thirteen planned dispatches remained unused;
the one-start allowance was consumed, so this closed batch was not restarted.

Offline validation passed 302 core tests before the final source-deduplication
change, followed by 46 affected integration tests including the new regression.
Overlapping tests are not summed. Five real loopback lifecycle fixtures and six
scripted callback scenarios passed. These prove application handling, not native
diagnostic accuracy. Existing historical results remain unchanged.

After selective local integration, an 81-test selection on the external ExFAT
volume hit the 64 MiB fixture allocation guard after entering 21 tests; no test
assertion was reported failed. That bounded failure is retained separately from
the passed publication-checkout suite. A narrower local selection of 54 provider
and critical entry/source-regression checks passed without increasing the guard.

The raw candidate also omitted the explicitly requested two caller layers, even
though all three function bodies were visible. The v1 five-state verifier did not
directly check task coverage. This omission remains a task failure; relaxing the
character threshold does not make the historical output pass. The v1 captures,
limits and failed result remain unchanged.

## V2 follow-up protocol

A separately frozen batch permits one cached service and at most twelve
generations: three isolated development cases and three sealed acceptance cases,
each with one candidate and, if parseable, one verifier. It repeats no old Solver
arm and performs no repair or retry. Ordinary output-quality failures end that
case and continue the remaining scheduled cases. Resource, permission, ownership,
source/runtime drift and uncertain request closure stop the whole batch.

Acceptance requires frozen code/data, safe operation and at least one actual
development verifier response parsed by the production validator; a valid
rejection qualifies for this measurement gate. If none of the three development
cases reaches that stage, acceptance remains unattempted. Per-case correctness,
task coverage, verifier decision and final delivery are scored separately from
transport completion, with unattempted items retaining their fixed denominators.

## V2 native result: 2026-10-11

The separately frozen follow-up completed all three development cases using one
cached 4B CPU service and four generations. It continued after ordinary output
failures as planned. All four responses were JSON objects with finish reason
`stop`; none exhausted the 512-output-token limit. Only one candidate passed its
business contract, and the one dispatched verifier failed its contract. Final
reports were 0/3 planned. The production-parsed-verifier gate remained 0, so all
three independent acceptance cases were unattempted. Guarded execution and
cleanup succeeded; diagnostic delivery failed.

| Development case | Candidate contract | Raw semantic result | Verifier / final |
| --- | --- | --- | --- |
| OR/AND defect | valid; cause 203, when 196 characters | Correct mechanism, explicit `(True, False)` propagation through both callers, actual/required result, three source references | Invalid verifier; no final |
| Healthy nonnegative domain | invalid; cause 797/600, when 333/300 | Correctly computes zero for `done > total`, then invents `done ≤ total` and raises a false alarm | Skipped; no final |
| Essential dependency gap | invalid; when 380/300 | Claims a supported defect with empty unknowns despite the missing `registry.resolve` contract; also contradicts the stated present-string key domain | Skipped; no final |

The first candidate exceeded the preferred brevity targets without losing its
original explanation or preventing verifier dispatch. This demonstrates the
revised host behavior on one development response; it does not establish an
accuracy improvement or isolate a prompt effect. The verifier omitted `scope`,
returned an array where keyed check references were required, added a forbidden
`verdict` field, and used a 661-character reason against the hard 96 limit. The
production validator first reported `compact_scope_mismatch`. Its
`counterevidence: does_not_refute` enum was legal. Its self-rated complete coverage
could not repair the invalid contract or authorize publication.

Raw development candidate defect recall was 1/1, healthy false alarms 1/1, and
correct essential-gap abstention 0/1. These are single development examples,
including invalid contracts, and are not final-report or independent accuracy
metrics. Final defect delivery was 0/1; healthy and gap cases ended in technical
failure. Absence of a final alarm is not a correctly supported healthy answer or
abstention. Independent healthy false alarms, defect recall, correct abstention
and end-to-end quality remain unmeasured. There was no retry, automatic repair,
additional service or extension of this closed batch.

The twelve representative prepared request shapes tokenized to 686–900 input
tokens. Complete development capacity examples used 76–102 candidate and 93–107
verifier tokens. Actual requests used 724–919 input tokens and outputs 148–332.
Capacity checks established that the examples fit; the server still enforced
JSON-object shape rather than the complete typed output contract. Full typed
server enforcement and bounded model correction were not tested.

Measured usage was 3110 input plus 938 output tokens (4048 total), with no cache
reuse. Guarded service time was 138.69 seconds, including preparation and cleanup;
generation wire times were 23.13, 37.61, 39.22 and 30.42 seconds. The 515 process
samples peaked at 5,458,444,288 RSS bytes, below 5.5 GiB. System availability was
81% at startup and at least 80% across 28 samples. The owned PID and listener were
absent after cleanup, and an independent OS process check found no remaining
model or runner. There were no new VMs, downloads or external model calls. Eight
generation slots remained unused and the one-start allowance was consumed.

The exact Offline Core selection passed 315/315 tests on the frozen source.
Twenty compact protocol tests, 52 integration/legacy tests, 41 independent
product checks, five actual loopback lifecycle fixtures, 27 inert native-adapter
checks and eight scripted callback tests also passed; overlapping selections are
not summed. Scripted rejection, continuation, parsed-verifier gating and cleanup
prove application behavior, not native diagnostic quality. The retained captures,
sealed rubric and historical v1 results were not rewritten.

After selective integration on the external ExFAT volume, a 28-test selection
reached the unchanged 64 MiB allocation guard after entering 26 tests, with no
assertion failure. The retained run is a bounded allocation failure, not a passed
suite. Partitioning that same selection into 23, 3 and 2 tests passed all 28
without increasing the guard; each successful partition removed its owned
temporary fixtures. No additional native request was used. The five owned files
were integrated while preserving the bytes of all 153 unrelated initial local
modifications and leaving the outer checkout's HEAD unchanged.

## Schema decoding follow-up: 2026-10-12

This separately frozen experiment changed the production decoder mode only.
The compact-v2 prompts, business schema, sampling, cached 4B model, ChatML,
1536-input/512-output/2048-context budget and timeout stayed unchanged. One
service and at most twelve generations were allowed: the complete defect case
first, then healthy/essential-gap cases only after an independently correct
final, and three sealed cases only after both latter tasks completed correctly.
No old Solver arm, retry, repair generation or semantic-prompt change was used.

The first pair completed with legal JSON and `finish_reason=stop`. Both responses
met the wire JSON schema: the verifier included the exact scope, keyed reference
object, allowed fields and enums, and a reason within the hard length bound.
The candidate passed application validation. The verifier did not: it chose
`protection=ineffective` but supplied only `refs.behavior=["e1"]`, omitting the
required `refs.protection`. The unchanged host rejected it as
`verification_citation_missing`; verification and final remained absent. These
state-dependent citation rules are application rules, not represented by the
wire schema's optional reference keys. Decoding does not fill or guess them.

The original candidate explained OR versus AND and the complete
`mixed_access()` → `access_status(True, False)` → `allowed(True, False)` path,
with actual access incorrectly granted under a policy requiring both flags.
Its cause was 203 characters, trigger 287, and it cited `e1`. Independent review
found the causal facts and caller explanation met the frozen rubric's minimum
semantic criteria; the single citation supports the faulty function but leaves
caller citation coverage partial. No new three-reference threshold was imposed.
The verifier's 96-character reason ended with “violating the stated”.
This is a linguistically incomplete reason at the character bound; neither
response exhausted the token budget or returned `length`. No host text cropping
or conclusion repair occurred, and no length limit was relaxed after the result.

A-stage final delivery was 0/1 attempted. The planned three-case development
denominator remains 0/3 final reports, with two cases unattempted; all three
independent acceptance cases were also unattempted. The stage ended after its
two requests as frozen. Healthy false alarms, essential-gap abstention and
independent diagnostic quality have no new measurement. Structural schema
compliance is established for this pair; a correct diagnostic closed loop is not.

Two auxiliary typed `/apply-template` probes, one per role, were accepted by the
cached server and produced prompts byte-identical to the messages-only render.
They generated no model tokens. Twelve representative requests fit at 696–904
input tokens; candidate capacity examples used 76–102 output tokens and verifier
examples 93–107. Actual generation used 733/165 and 936/132 input/output tokens,
1669 input plus 297 output (1966 total), with no cache reuse. Guarded service
time was 60.90 seconds; generation wire times were 24.26 and 27.30 seconds.

The 224 RSS samples peaked at 5,508,579,328 bytes, below 5.5 GiB. Available memory
was 81% at startup and at least 78% across twelve samples. The owned PID and
listener were absent and monitor threads completed after cleanup; a fresh OS
process check found no remaining model or runner. There were no downloads, new
VMs or external model calls. Ten generation slots remained unused, with no
remaining service start or automatic extension.

Before execution, the exact Offline Core selection passed 320/320 tests and all
77 public modules imported. The affected 74-test selection, independent provider
26-test selection, 37 inert adapter tests and twelve actual Orchestrator callback
tests also passed; the callback tests used sixteen scripted fixtures. Overlapping
selections are not summed. Four retained v2 captures replayed through unchanged
shared validators with their original outcomes. Scripted tests and schema
compliance establish application behavior, not semantic accuracy improvement.

The A manifest records the suite before its terminal status change. The callback
then writes `stage_A_failed`, changing that suite file's hash; the four raw/request
files and trace still match. Independent review reproduced the earlier hash by
changing only the status in memory and separately bound the final suite hash.
No semantic gate was consumed or later stage released.

## Protection citation audit: 2026-10-12

Zero-generation replay identifies the precise failure as a missing
`refs.protection` key, rather than an empty list, foreign ID or invalid location.
The returned `refs.behavior=["e1"]` is valid but does not satisfy a different
check's citation obligation. The supplied `staff or paid` predicate is an existing
access mechanism; for the stated both-true policy it is ineffective on the mixed
input. Its existing source can support both behavior and protection checks.
Repeating that ID under two check keys does not create a second source or raise
confidence. No correct guard or new source needs to be invented.

| Protection state | Meaning in the supplied task | Citation obligation |
| --- | --- | --- |
| `effective` | A visible mechanism prevents the proposed failure | Nonempty `refs.protection` identifying that mechanism |
| `ineffective` | A visible mechanism exists but does not prevent the failure | Nonempty `refs.protection` identifying that mechanism |
| `absent` | No mechanism appears in the fully visible relevant path | No invented protection reference; assess only that visible scope |
| `unknown` | Necessary protection source or contract is unavailable | No invented reference; a definite defect cannot pass on this state, and an unknown assessment needs an essential gap |

The decoder schema accepts optional keyed reference arrays; the visible
`contract.refs.required_when` and verifier instructions add state-dependent
obligations that the host enforces. They can all be satisfied together. This is
a structural/business-rule boundary, not a requirement to cite nonexistent
protection. The generic verifier enforces the same states. No production rule,
schema, verdict, source binding or native capture was changed for this audit.

In a clearly labelled synthetic witness, adding the existing source ID only to
`refs.protection` makes the unchanged candidate pass the existing host validator.
Missing and empty arrays still fail; foreign IDs remain invalid. Changing the
state to `absent` demonstrates its existing interface path without that key; it
does not relabel this native answer. Changing it to `unknown` cannot turn a
supported-defect candidate into an accepted unknown or healthy result. These
replays establish deterministic behavior, not a successful model correction.

An in-scope ID can pass membership/range validation while failing to support the
specific check. For example, the caller row by itself does not show the access
predicate. Host acceptance of such a scripted reference is not semantic proof;
an unrelated visible ID must not be added merely to satisfy a required key.

The proposed single variable is to append the following paragraph only to the
verifier system prompt. It is **not enabled or natively tested**:

> When checks.protection is effective or ineffective, include refs.protection
> with 1–2 allowed IDs identifying the visible protective mechanism. Even if an
> ID already appears in refs.behavior, repeat it under refs.protection when it
> supports both checks. Do not invent a guard or an ID. Keep absent/unknown when
> the evidence supports those states; this instruction does not require a
> protection reference for them.

Keep the candidate prompt, schema, model, source, field limits and budgets fixed
for that future comparison. One actual verifier generation on the retained,
source-bound candidate is the minimum to test the prompt change; it measures
verification only. A fresh candidate-to-verifier-to-final attempt requires two
generations. This audit starts no service and sends no model request. It does
not establish that the model will now meet the obligation or restore the native
closed loop.

## Enabled citation clarification: bounded native test, 2026-10-12

The proposal above was subsequently enabled for compact verifier requests by
appending that exact paragraph. It explains the existing conditional reference
contract; the candidate prompt, schema template, host rules, cached 4B CPU
profile, sampling and budgets remain unchanged. It contains no case answer or
source ID. The new batch permits one service and two fresh generation keys,
with no retry, later stage, model validator or independent acceptance case.

The same visible development fixture was recovered from its previous executed
request, source and trace, without opening sealed inputs. Both native outputs
finished with `stop`. The candidate described the OR predicate, the
`mixed_access -> access_status(True, False) -> allowed(True, False)` path, the
incorrect grant and its conflict with the both-true access policy. The verifier
returned `protection=ineffective` and explicitly cited existing `e1` under both
`refs.behavior` and `refs.protection`. The host accepted it and retained the
fresh candidate as final. The repeated source did not become a second location
or supply confidence. These are actual outputs, with no inserted reference or
state repair.

Manual source comparison confirms the boolean-domain mechanism and caller
explanation, including the implied expected denial. The final still cites
only `e1`: the correct caller facts are visible in `e2`/`e3` but are not explicitly
referenced. Citation coverage remains partial under the existing rubric; no
new three-reference threshold is imposed.

| Observed application measure | Previous schema-only batch | Citation clarification batch |
| --- | --- | --- |
| Structured native outputs | 2/2 | 2/2 |
| Candidate business contract | 1/1 | 1/1 |
| Verifier business contract | 0/1, missing protection key | 1/1, explicit protection key |
| Host final | 0/1 | 1/1 |
| Healthy, necessary-gap and independent acceptance runs | Not attempted | Not attempted |

Fresh task/attempt IDs change scope bindings and schema `const` values, and the
fresh candidate changes verifier input. The retained wire comparison explicitly
normalizes those fields: candidate system text is identical; verifier system
text only adds the paragraph; visible source, task, schema template and other
wire parameters match. This is a single observed development result, not a
strict byte-identical A/B or evidence of broader accuracy, healthy false-positive
rate, defect recall or correct abstention improvement.

The verifier's reason is still 96 characters and ends at “violating”, mid-clause.
It has no `length` stop or native token-truncation evidence. The citation fix
does not establish complete explanatory prose or independent semantic proof.

Actual cost was one service (cumulative start 28), two generations, 1,736 input
and 307 output tokens, 2,043 total, and 62.79 seconds including cleanup. All
17 HTTP events were recorded: two generations and fifteen health, identity,
template or token-count requests. The owned process, listener and monitor
threads closed. No VM, download, external model API or additional round was
used. Full Offline Core passed 323/323; overlapping related and scripted
boundary selections are not summed or counted as native semantic evidence.

## Publication boundary and six independent cases: 2026-10-12

**Independent acceptance failed: 2/6 final diagnoses met the frozen minimum
semantic requirement, although the interface completed 6/6.** This batch does
not establish improved diagnostic accuracy or successful three-state handling.

The previous development result exposed a deterministic publication error:
diagnostic host acceptance set `final_review.ok=true` and suppressed human
review, even though the diagnostic branch does not invoke the final reviewer.
The source-bound validator checks shape, allowed references and model-state
coherence; `coverage=complete` is a model judgment and `semantic_proof=false`
remains the boundary. It cannot establish complete caller coverage, per-claim
citation support or complete explanatory prose.

The diagnostic publisher now always records `final_review.ok=false` and
`human_review_required=true`. An accepted result explains that its contract and
source checks passed but semantic/task coverage needs review. The CLI prints
that diagnostic issue, and the existing viewer shows its review warning.
The original candidate, verifier, verdict, host acceptance and interface
completion remain intact. Generic clarification behavior is unchanged. No
minimum-reference threshold, keyword verdict repair, prompt, schema, field
limit, sampling parameter or model profile was changed for this batch.

Before new inputs were used, implementation and the exact Offline Core
selection were frozen. A separate author prepared six unused cases, balanced
as two defects, two healthy implementations and two essential dependency gaps.
All eighteen local functions were complete; the gap cases lack a necessary
external behavioral guarantee rather than a function snippet. Public inputs
were separated from a private expected/scoring file, neither supplied to the
model nor used as capacity-preflight answers. Capacity used the production
syntax examples. A different ordinary reviewer scored the retained raw
candidate and final against the precommitted rubric. No model validator,
post-output tuning, repair, retry or additional native round was used.

| Measure | Candidate | Retained final |
| --- | --- | --- |
| Business contract and source binding | 6/6 | 6/6 two-stage interface completion |
| Correct three-state label | 3/6 | 3/6 |
| Minimum whole-task semantics | 2/6 | 2/6 |
| Defect causal/caller examples satisfied | 2/2 | 2/2 |
| Healthy false-defect reports | 1/2 | 1/2 |
| Correct healthy label | 1/2 | 1/2 |
| Healthy whole-task minimum, including requested caller | 0/2 | 0/2 |
| Correct essential-gap unknown | 0/2 | 0/2 |
| Complete candidate explanatory prose | 4/6 | 4/6, unchanged candidate |
| Correct complete caller explanation | 2/6 | 2/6 |
| Explicit references to all caller blocks | 5/6 | 5/6 |
| Complete verifier reason | Not applicable | 0/6 |

The two defect examples describe concrete actual-versus-required caller
outcomes. One still misstates an argument position in `cause`, while its
`when` already contains the correct concrete counterexample; its trailing
clause is unfinished. Minimum causal success does not erase these quality
faults or constitute a population recall estimate.

One healthy result correctly covers the complete boolean domain but omits the
requested entry-caller connection. It is therefore a correct label with
partial task coverage. Its partial reference coverage is recorded separately;
no new three-reference gate causes that failure. The other healthy example is
misread as a defect: the model says a false condition enters its branch and
reports the wrong result. The verifier repeats and accepts that source error.

Both essential-gap results are labelled healthy. They reason conditionally
about unknown callback outputs, then claim the stated requirement is satisfied
without the missing dependency guarantee. The verifier also accepts those
claims. A fixed callable returning a bool does not establish its necessary
input/output relation. This is unsupported certainty, not a technical failure
or a correct unknown assessment.

Every native response was complete structured JSON with `finish_reason=stop`:
12/12, with no `length` finish. All six verifier reasons are 96 characters and
end mid-clause; two candidate explanations are also unfinished. Character
caps and a native stop do not establish linguistically complete prose, and
these captures do not prove a native token-length cutoff. The accepted final
is exactly the original candidate in all six cases; verifier prose does not
repair a missing or incorrect candidate explanation. Both raw captures and
independent scoring remain unchanged, and all six cases are marked seen for
future development rather than reusable independent acceptance.

Actual cost was one cached 4B CPU service (cumulative start 29), twelve unique
generations and 13,737 measured tokens: 11,609 input plus 2,128 output. The
whole run including cleanup took 422.49 seconds. All 89 HTTP events were
recorded: twelve generations and seventy-seven health, identity, template or
token-count calls. Per-call native idle-release evidence, cache-disabled
usage and raw-to-typed accounting were audited. The owned process, listener
and monitor threads closed. RSS peaked at 5.23 GiB under the 5.5 GiB limit;
available memory was 82% at startup and at least 76% in recorded runtime
samples. No VM, download or external model API was used. The legacy trace
cost rollup still shows zero prompt tokens in this path; these measured
native totals are the cost evidence, and that rollup remains a known gap.

Full Offline Core passed 323/323 before acceptance, and a real CLI/viewer
integration check passed after applying the patch in the main workspace.
Overlapping scripted selections are not added to the native denominators.
These checks verify the deterministic publication fix; they do not reverse
the independent acceptance failure. Local-only receipts under
`artifacts/implementation/diagnostic-three-state-01` bind the input/rubric
hashes, frozen implementation, twelve request/response pairs, scoring,
resource audit and preservation checks. Runtime logs, private scoring data,
model assets and personal configuration are excluded from publication.

## Requirement-preserving prompt contrast: 2026-10-12

**Independent acceptance failed: none of the three new final diagnoses met
the frozen minimum whole-task semantics.** The three seen development pairs
improved from 0/3 to 1/3 on that minimum. Only one concrete defect control
improved; general diagnostic accuracy and successful unknown handling remain
unverified.

Previous traces exposed unsupported certainty: the candidate merged an extra
implementation condition into the stated requirement and treated a fixed,
deterministic bool-returning dependency as a sufficient behavioral guarantee.
The verifier accepted that error. A source/prompt/schema audit also found the
hard 96-character reason limit and unfinished explanations, without a native
`length` finish or a counterfactual proving that the limit caused the errors.

An explicit `prompt_variant='requirement_preserving'` passes through the real
request builder, solver, staged candidate and complete diagnostic entry. It
appends a single candidate system paragraph about preserving requirements,
compatible dependency behaviors, concrete reachable violations, and positive
support for the full healthy requirement. The default stays `baseline`.
Invalid variants fail before retrieval or candidate dispatch and preserve a
prepared trace. The variant is recorded in local request metadata. The
baseline wire and metadata, verifier prompt, schema, host rules, reason limit,
source limits, model profile, sampling and budgets are unchanged.

Implementation, tests, protocol, 18 generation keys and one-service budget
were frozen before new inputs were released to implementation/runtime. Three
old seen development cases cover a necessary dependency gap, a healthy
control and a concrete defect. Each pair has identical task/source/scope/
schema; candidate wire differs only by the frozen system suffix. The changed
candidate subsequently changes verifier input, a recorded downstream effect
rather than a separate fixed-candidate verifier prompt contrast. Three new
cases, one per state, were independently authored with complete functions
and a private expected/scoring file. The private file was not used for
implementation, tests, capacity checks or model input. A separate ordinary
reviewer scored unchanged raw, parsed and final outputs after shutdown.

| Measure | Seen baseline | Seen variant | New variant acceptance |
| --- | --- | --- | --- |
| Minimum whole-task semantics, original candidate | 0/3 | 1/3 | 0/3 |
| Minimum whole-task semantics, retained final | 0/3 | 1/3 | 0/3 |
| Correct raw label | 1/3 | 2/3 | 2/3 |
| Correct parsed label, all planned units | 1/3 | 2/3 | 1/3 |
| Correct final label, all planned units | 0/3 | 2/3 | 1/3 |
| Candidate business contract | 3/3 | 3/3 | 2/3 |
| Verifier parsed, including rejected, attempted calls | 2/3 | 2/3 | 1/2 |
| Host interface completed with final | 1/3 | 2/3 | 1/3 |
| Healthy false-defect reports, raw candidate | 1/1 | 0/1 | 0/1 |
| Healthy whole-task minimum | 0/1 | 0/1 | 0/1 |
| Correct completed essential-gap unknown | 0/1 | 0/1 | 0/1 |
| Concrete defect causal/caller minimum | 0/1 | 1/1 | 0/1 |
| Correct complete original caller explanation | 0/3 | 1/3 | 1/3 |
| Raw references to all caller blocks | 3/3 | 3/3 | 3/3 |
| Complete candidate prose | 2/3 | 2/3 | 2/3 |
| Complete verifier reason | 0/3 | 0/3 | 0/2 |

The seen dependency-gap pair remains incorrectly healthy in both arms. The
seen healthy variant corrects its label and concrete result but still says
a false condition enters the `if` branch; that control-flow contradiction
fails the explanation requirement. The defect variant correctly explains
swapped arguments, a reachable caller and actual-versus-required results;
its baseline lacks the correct cause and is rejected. This is a single
paired minimum gain, without a defect-control regression in this tiny set.
The healthy final false-defect count is 0/1 in both arms, but baseline has no
final and variant has the explanation fault; that count alone is no success.

New case 1 writes `unknown` and recognizes a necessary dependency guarantee,
but non-null `cause`/`when` violate the existing unknown business contract,
and its caller/prose explanation is incomplete. Its parser fails and verifier
is skipped, so it is partial recognition rather than correct completed
unknown. New case 2 has a correct concrete three-function caller, but only
restates the requirement for the remaining domain without source support for
the actual guard and other outputs. Its accepted final remains incomplete.
New case 3 misses the wrapper label inversion, reports healthy and fails a
required verifier reference; no final is produced.

All 17 responses meet raw JSON/wire schema and finish with `stop`. Candidate
business contract passes 8/9; verifier parsing, including rejection, passes
5/8; interface completion is 4/9. One candidate cross-field failure, three
missing conditional verifier references and one valid rejection remain
unaltered. Technical failures are not healthy or correct unknown outcomes.
Eight verifier reasons are 96-character residual clauses; candidate prose is
complete in 6/9. There is no `length` finish or causal proof of native token
truncation. All nine candidates cite the caller blocks, which does not prove
correct caller reasoning or per-check citation support. Those measures are
separate in the local independent report. No reference, verdict, candidate
explanation or state is repaired, and final publication requires human review.

| Measured native cost | Seen baseline | Seen variant | New variant |
| --- | --- | --- | --- |
| Generations | 6 | 6 | 5 |
| Input tokens | 5,837 | 6,163 | 5,019 |
| Output tokens | 1,124 | 1,094 | 1,046 |
| Generation wire seconds | 226.07 | 236.65 | 225.48 |

Actual cost is one cached 4B CPU service (cumulative start 30), 17 unique
generations, 17,019 input plus 3,264 output tokens, 20,283 total, and 703.51
seconds including cleanup. Only the invalid candidate's verifier was skipped;
six unkeyed reserve slots were unused. All 128 HTTP events are accounted for:
17 generations and 111 health/identity/template/token-count calls. Saved
schemas, raw-to-typed usage, zero-cache usage and native idle-release evidence
match. RSS peaks at 4.88 GiB under 5.5 GiB; available memory is 80% at startup
and at least 73% in recorded runtime samples. Process, listener and monitor
threads closed. There was no retry, repair, extra service, VM, download or
external model API. Historical total-record peak and runtime disk minimum
were not continuously recorded and remain unknown.

Full Offline Core passes 326/326, with three new tests covering the sole
suffix, actual wire/persistence and invalid-variant no-dispatch behavior.
Overlapping 73-test and one-test workspace integration selections pass but
are not added to native denominators. Source/data pins, old 514 evidence
files and all 154 initial local modifications were checked, with exact inverse
proof for owned overlaps. Old six-case raw outputs and scores are unchanged.
The new three cases are now seen, not reusable independent acceptance.

The variant stays opt-in. The smallest next direction is coherent unknown
cross-fields during generation, followed by concrete source-based control-flow
verification. Neither direction is implemented or validated here, and no
extra native round was started. Local-only receipts under
`artifacts/implementation/diagnostic-semantic-contrast-01` bind protocol,
captures, grading, resources and preservation. Private expected data, case
payloads, runtime logs, models and personal configuration are not published.
