# A bounded decision about tool value

Status: offline implementation and independent-review preparation, **not live consent**.
The user asked us to keep moving toward reliable diagnosis and a fair same-model
comparison, then simplify if the tool does not justify its cost. Existing results
and unused authorizations remain frozen. No new Key read or request is authorized.

## One decision, two gates

1. **S0, developer protocol gate:** run the two existing, author-known developer
   flows once with the versioned concise-gap candidate. At most 6 new requests,
   separate local budget $0.50. Both prescribed search/read/terminal flows must
   validate, with settled usage and one verified provider fingerprint. Otherwise
   stop the stage; these two examples do not prove general reliability.
2. **S1, independent comparison:** only after a qualifying S0, run 9 new synthetic
   tasks, each once with a direct API strategy and once with the tool strategy.
   At most 54 requests and a separate $1.50 budget. No repeats or replacement
   tasks. Every planned cell remains in the 18-cell denominator.

The complete proposed stage is **60 new requests / $2.00**, with single concurrency
and no automatic retry. Each stage uses the existing persistent reservation gate;
their separate ceilings sum to the whole-stage ceiling. Historical budgets and
the $0.66 unknown hold cannot be supplied or transferred. Unknown outcomes,
untrusted usage, identity drift/missing identity, scope/code drift and exhausted
reserves halt. Settled content-contract failures remain failures and continue to
the next S1 cell, without retry or prompt repair. An S0 failure never enters S1.

## Fair input and fixed policy

Use official `deepseek-flash`, thinking enabled, reasoning effort high, at
`https://api.deepseek.com/chat/completions`. Every request has 8192 total output
tokens (reasoning plus answer), at most three requests per cell, an 8000-input
token plan per request / 24000 per cell, and 180 seconds per cell. Input usage is
checked after the provider returns; it is not a provider-side hard input cap.
Successful and failed replies with trusted usage must both stop on input overrun
while preserving billed usage. Full reasoning history for tools must be retained;
the provider ignores old reasoning in requests without tools, so effective
contexts are not claimed identical.

Both S1 arms receive the same complete authorized Python source, task, assumptions
and filenames in the same initial message. **Neither receives host-generated AST
facts initially.** Every task's complete source fits within the existing 4096-byte
initial-source boundary. Direct API uses the existing initial-answer, self-check,
final-answer process with no tools; its first answer is an auxiliary observation,
not another sample. The tool arm uses the existing four-read / three-decision
policy, with the third decision limited to finish or ask_user. Both retain the
five-field diagnosis validator, 200-character gap limit and common evidence
decision criteria. Arm-specific protocol messages necessarily differ. This
measures the whole tool strategy under equal ceilings, not a pure tool-function
causal effect or equal realized compute.

Freeze the order seed and all nine adjacent task pairs before dispatch. Alternate
which arm runs first, with five pairs one way and four the other; the seed selects
the first direction. Do not reorder after observing latency, cache hits or answers.

Add one explicit `study-value-v1` profile, preserving every prior default/profile.
The existing offline concise-gap probe stays unable to authorize itself. A new
thin whole-stage owner may use its private execution kernel only after verifying
the stage's exact approval, fresh claims, S0/S1 data, code and budgets. Reuse the
existing provider, static tools, study core, reservation gate and review formats.
No service, queue, database, agent role or general experiment framework is added.

## Independent custody and scoring

The parent coordinates the existing independent custodian. Request a **new** neutral
pack using `macr.deepseek.study.pack.v1`: 9 tasks in 3 projects, 3 tasks per project;
2–4 `.py` files per task, every authorized file included initially, total source
per task <=4096 bytes. Use original synthetic material with licensing/provenance.
Freeze three defect tasks, three healthy tasks and three genuinely decisive
external-gap tasks, with mechanism-family separation from prior
sets, and no task/category mapping in the neutral pack. Do not send the executor
gold, categories or hidden tests. Supply only the existing five-field custodian
commitment, neutral inventory/hash, rubric version and aggregate qualification.
The final exact file/byte inventory and plan hash require that neutral handoff;
until then only public development fixtures can run offline.

After all cells reach terminal or explicitly not-attempted states, export anonymous
answers with actually supplied evidence for independent locked semantic review.
Reuse exact-result hashes and strict flag/category/verdict consistency. Contract
failures earn no task credit; absent/pending/unknown cells are not observed ties.
Report six-task full solutions, three-task necessary abstentions, unsupported
determinate claims, unnecessary abstentions, full failure denominator, paired
wins/losses/noncomparability, actual requests/tokens/cost and total/median latency.
No diagnosis labels alone earn semantic credit. No claim of full double blinding.

An additional, independently locked `determinate_reviews` map binds each valid
answer's `result_sha256` and `review_sha256`, `status: locked`, boolean
`determinate_error`, and a rationale. It covers an unsupported determinate
conclusion, including a wrong mechanism, trigger or cited support even when the
verdict label matches. It cannot contradict a known false positive/release or
mark an unknown/ask_user answer as determinate. Missing entries leave adoption
inconclusive. This is a narrow new-stage review field; historical review contracts
and frozen scores remain unchanged.

## A natural end, including no gain

Default operational choice is the simpler direct API route. Retain the tool route
as an experimental option only if all 18 cells were attempted, metering/identity
are complete, locked scores exist for every valid answer, the tool has at least
two more beneficial paired outcomes than direct, has no additional unsupported
determinate conclusions, and its total accrued cost and total elapsed time are
each <=1.25 times direct. Correct necessary abstention is a benefit on an external
gap, not a full solution. These are engineering selection thresholds on nine
tasks, not a general statistical proof. If complete results do not meet the rule,
prefer direct and stop adding tool complexity. Incomplete/contaminated execution
is inconclusive, not evidence of no tool effect; direct remains the default.

Beneficial pairing means tool wins minus direct wins >=2: the outcome is full
solution on each of the six solvable tasks and reasonable abstention on each of
the three genuine external gaps. Partial answers and content-contract failures
earn zero complete-task credit. The error guard is also paired: any task with a
tool determinate error and no direct determinate error fails it; equal aggregate
error counts cannot hide a newly harmed task.

No live repair/retest is included. Evidence-based development fixes can continue
offline, but changing prompts/code after a live failure, restarting a failed cell,
using replacements, recovering a new unknown, increasing budget or sending new
data requires a new bounded execution approval. Do not iterate on S1 outcomes.

## Price, credentials and logs

Official pricing checked 2026-10-30: peak cache-hit/miss input $0.006/$0.30 per
million; output $1.20 per million. The loose engineering envelope 1,048,576 input
and 8192 output costs $0.324404 rounded up; reserve $0.33 before every request.
The 60-request 8000-input/all-miss planning sensitivity is $0.733860 after
per-request rounding, not a promise.
Reserves can stop the run early. $2 is a local protection, not a supplier bill cap.
The provider's advertised 1M context is not an independently verified exact token
bound. Recheck contract/prices before any separately approved live execution.

Only a future exact-scope approval may allow the existing project Key to be read
in memory for official authentication. No Key in argv, chat, traces or new files.
Logs stay in a private local directory outside the checkout, include source,
requests/reasoning/answers, usage and receipts; retain for review with 14-day
guidance, no upload or automatic deletion. A leaked Key can incur unauthorized
charges. No gold, private project, configuration or historical logs are sent.

Sources: [pricing](https://api-docs.deepseek.com/quick_start/pricing/),
[request contract](https://api-docs.deepseek.com/api/create-chat-completion/),
[thinking history](https://api-docs.deepseek.com/guides/thinking_mode/).
