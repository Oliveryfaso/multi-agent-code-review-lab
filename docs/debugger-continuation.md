# Debugger continuation: citation and workflow validation

This batch starts from public `bc81db8` and retains the unrelated external-drive
working tree at `b4a2378`. Original holdout captures and original scores are unchanged.
The implementation uses the existing standard-library components and adds no dependency.

The comparison parser validates every raw citation before stable deduplication of exact
`file/start/end` positions. Raw strict contract and diagnostic metrics remain separate
from normalized metrics, including when reading legacy records. Foreign, out-of-range,
overlapping-but-distinct and raw over-limit references are not repaired or silently dropped.

Candidate diagnostics and final reports are distinct. The optional check records requirement,
trigger, behavior, existing protection and counterevidence. Explicit state conflicts reject
with no final; provider failures remain technical failures. Acceptance means structural and
explicit-state consistency, not causal proof. Caller observations are bound to the visible
scope and remain outside the model prompt. The pipeline makes at most one injected check call,
never flips a verdict and retains both original texts.

The Solver now requests explicit five-field citations instead of lending retrieval citations
to free text. It validates all references against the evidence the model actually saw, then
counts distinct source positions. The Orchestrator records failed attempts, classifies
transient, invalid-output and missing-context paths, and allows one recovery. Exhausted
failures remain failures and cannot authorize patch generation. Raw invalid Unicode output
is preserved with reversible JSON escaping.

These choices draw on [PydanticAI's output validation budget](https://pydantic.dev/docs/ai/core-concepts/output/#output-validators),
[LangGraph's error categories](https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph)
and [Anthropic's per-candidate checks](https://github.com/anthropics/claude-code/blob/main/plugins/code-review/commands/code-review.md).
No framework migration or copied confidence threshold is introduced.

## Frozen measurement

Development v2 contains six cases, two per verdict category, isolated from the old holdout.
For each case baseline, neutral three-state wording, and explanation-first field order change
one factor. The explanation-first GBNF uses the same productions with only member order changed.
The cross-field path reuses the exact baseline candidate and makes at most one check request.
All variants are reported; acceptance results do not select or tune a winner.

Independent acceptance v2 has six new cases created after product code was finalized, two per
category. Each gets a baseline candidate and optional check. Three additional Orchestrator
fixtures exercise retrieval, Solver, recovery, FinalReview and persistence through the real
entry. Planning is frozen for those fixtures to exclude unavailable legacy Git history and
execution tools; full planner behavior is outside that measurement.

The frozen ledger allows at most 42 planned generation keys within the authorized 48-request,
two-service ceiling. No new VM, model download, external model API or code upload is allowed.
4B uses the existing b11521 CPU profile: context 2048, output 384, two threads, seed 0 and
no prompt cache. Native tokenization checks admission before dispatch. Technical failures
and unused keys are not healthy answers or correct abstentions. A check's auxiliary attempt
counter is not the authoritative native dispatch ledger.

Resource guards retain 4B RSS 5.5 GiB, startup available memory at least 30%, and running at
least 20%. Batch and owned auxiliary artifact allocations together must stay below 512 MiB.
300 seconds is a reminder; resource violations still stop the owned service. The historical
7B hash was recovered, but its file inode no longer matches the old identity evidence, so
this batch does not start 7B.

## Offline replay

`python -I -S -B scripts/verify_diagnostic.py --input <project-local-bundle.json> --out <new-artifact-directory>`
replays a captured candidate and verifier response without contacting a provider. The bundle
has `request`, `candidate_raw`, `verification_response`, `error_code`, and `observations`;
exactly one of response or error is present. It writes the full capture and status, with
`new_model_calls=0` and `semantic_proof=false`.

## Evidence and limits

Six local regression groups passed 255 tests without skips; independent review found and
fixed the invalid-Unicode persistence boundary and verified 113 related tests. A complete
scripted callback smoke covered 39 callback calls and three completed Orchestrator fixtures,
with zero real services or generations. These overlapping test counts are not additive.

Real outcomes have received independent review; [the separate result record](debugger-continuation-results.json) preserves the first frozen capture.
Contract, healthy false alarms, defect recall, correct abstention, accepted-final coverage,
end-to-end completion and native request/token cost must be reported separately. A small
balanced set cannot establish general model accuracy, prompt superiority or whole-repository
health. Model self-verification is correlated with its candidate and can miss the same error.


## First frozen results

| Dataset / variant | Raw / normalized contract | Strict diagnosis | Healthy false alarms | Defect label / causal recall | Correct abstention |
| --- | --- | --- | --- | --- | --- |
| Development baseline | 6/6 / 6/6 | 3/6 | 0/2 | 2/2 / 1/2 | 0/2 |
| Development neutral | 6/6 / 6/6 | 4/6 | 0/2 | 2/2 / 2/2 | 0/2 |
| Development explanation first | 6/6 / 6/6 | 1/6 | 2/2 | 2/2 / 1/2 | 0/2 |
| Independent acceptance baseline | 6/6 / 6/6 | 3/6 | 0/2 | 2/2 / 1/2 | 0/2 |

Strict diagnosis requires the correct verdict, supported mechanism, actual failure trigger
or full healthy input domain, and source support. A condition that includes a healthy input
is partial. For example dv01 baseline falsely says both false permits access; av01's broad
short-sequence trigger includes the healthy empty tuple. Labels alone do not pass those cases.
Missing external dependency guarantees were repeatedly guessed. No variant becomes the default.

**Check coverage 0/12** means zero accepted final reports from six development baseline
candidates plus six independent acceptance candidates, each with one check. Nine checks ended
with `length`; the three complete outputs failed the required verification shape. Their actual
HTTP bodies requested only `json_object`, without a schema or verification grammar. Some checks
also repeated the candidate's false trigger or invented dependency behavior. The original raw
responses remain unchanged; zero reports cannot demonstrate fewer false alarms or correct abstention.

**Native agent completion 0/3** means zero runs with `run_outcome.end_to_end_completed=true`
across defect, healthy and missing-context fixtures. Each made two Solver requests and exhausted
one recovery. All six ended with `length`. The wire inputs contained eight source-location facts
with empty snippets even though retrieval had the actual code. The `rank -> [:12] -> compact8`
selection dropped source text, and repeated tool citations consumed output space. These are distinct
input and completion failures, rather than six accepted diagnoses. [The fixture inputs](../evals/diagnostic-agent-v2/cases.json)
are retained separately from independent acceptance.

Two deterministic follow-ups fix exact five-field identity (`null` differs from empty string)
and select nonblank source snippets from the full retrieved set before the provider's cap.
Parsing uses exactly that visible set; pure deterministic fallback ordering remains unchanged.
Real-entry offline tests prove `return staff or paid` enters the request and foreign references
remain rejected. Six original native responses replay as failures; replay is not a new model run.
The first capture predates these two fixes, so it does not prove their native completion or cost.
Final CI-equivalent regression passed 249 core tests plus five bounded kernel-loopback tests,
with no skips; 76 modules imported and CLI help passed. The related 41 and independent 12 checks
overlap those counts. Default sandbox bind was denied; the same five loopback fixtures passed
through the formal approved permission route, with zero model requests and external connections.

The first capture cost one service, 42 requests, 21,844 input and 11,177 output tokens, and
1,633.52 seconds. Peak RSS was 4.844 GiB and the minimum sampled available memory was 68%.
All native idle proofs qualified, and both the owned PID and listener were independently absent
after cleanup. No VM, download or external model API was added. Sampling does not prove an exact
continuous minimum; 7B remains unused because the recovered file identity no longer matches.

## Architecture review entry

Review the fixed implementation commit together with this first result record; the continuation
artifact index binds that commit, original frozen plan, 42-key request ledger, actual failed
examples, measured/follow-up source differences, and independent reviews. Useful failure examples
are dv01 (wrong actual trigger), dv03 (invented dependency guarantee), dv02 explanation-first
(healthy explanation with defect verdict), av01 check (complete but wrong verification shape),
and the defect agent wire request (retrieved source dropped before the model). Offline assertions
cover identity, visible context, bounded recovery and failure persistence; they do not establish
native semantic correctness, planner coverage, or a solution to the 384-token completion bottleneck.
No separate architecture review session is created here.
