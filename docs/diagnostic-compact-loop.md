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
