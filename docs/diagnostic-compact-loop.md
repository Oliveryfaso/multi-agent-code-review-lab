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

The wire protocol uses at most two short reference IDs. The host restores the
exact file, start/end lines, symbol and source tool from a table bound to the
case, attempt and source bytes. Source readers capture complete lines, recalculate
clipped ranges, and keep empty or oversized first lines as noncitable locators.
Old attempts, foreign IDs and conflicting sources are rejected. Neither multiple
tools at one location nor confidence supplied by a model increases authority.

The verifier receives the five keyed checks, their permitted enum values,
necessary citations and text/array limits. It checks requirement, trigger,
behavior, protection and counterevidence. Host validation detects contradictory
states and missing references; it does not establish whether a causal explanation
is true. Rejecting a defect does not produce a healthy report.

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

The native comparison uses one cached 4B CPU service and a maximum of 15 dispatches:
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

## Native result: 2026-10-11

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

The remaining limitation is model compliance with the compact length contract.
A future, separately frozen experiment can compare schema-constrained generation
or an explicitly budgeted validation correction. Neither was tested here, and
no accuracy or false-alarm improvement is claimed.
