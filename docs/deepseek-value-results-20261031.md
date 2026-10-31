# Value-stage pilot: prefer direct API

Locked independent scoring selects **prefer direct API** for the diagnostic
research route. The tool policy earned one net paired win, below the preset
minimum of two. Both strategies fully solved 3/6 solvable tasks; contract failures
were 6/9 direct and 5/9 tool. Neither strategy has established production
reliability. Tools remain opt-in research, without a demonstrated retrieval
benefit. The usable static-evidence product still calls no model; this result
does not enable a production DeepSeek default.

## Frozen comparison

The nine new synthetic tasks used identical complete authorized source as initial
evidence, without a host AST summary. Both policies used official DeepSeek
`deepseek-flash`, thinking high, at most three requests per cell and 8192 total
reasoning-plus-answer output tokens per request. Direct API used initial answer,
self-check and final answer; the tool policy could end earlier. First answers
were auxiliary observations, not extra samples or substitutes for final results.
The [protocol and selection rule](deepseek-value-stage.md) were fixed before the
run; no prompt edits, retries, clipping, repairs or replacement tasks were used.

S0 passed its two prescribed developer flows with six requests. This small
protocol gate did not establish general output reliability. S1 attempted all 18
planned cells. Seven reviewable outputs received independent semantic and
separately locked determinate-error judgments before the keeper joined labels.
Eleven failures retained their original statuses and no invented semantic grade.
Private task qualifications and answers remained with the custodian. No gold or
category map was supplied to generation or the executor.

## Locked results, complete denominator

|Metric|Direct API|Tool policy|
|---|---:|---:|
|Complete solutions on solvable tasks|3/6|3/6|
|Necessary abstentions on genuine external gaps|0/3|1/3|
|Correct final disposition across all tasks|3/9|4/9|
|Contract failures|6/9|5/9|
|Reviewable outputs scored|3|4|
|Unattempted cells|0|0|

The native pair result was one tool win, zero losses and eight ties. Three ties
came from independently reviewed outputs. The other five were known contract
failure disposition ties, **not correctly solved pairs**. The one win also used
the known-failure disposition comparison. Keeping failures in the denominator
does not turn them into semantic judgments or prove a retrieval effect.

All execution, identity, usage and review readiness checks passed. The checks for
no added unsupported determinate conclusion and cost/time within 1.25 times
direct API also passed. The decisive net-win check failed: **1 < 2**, so the
unchanged native selection was `prefer_direct`. There were no unresolved scoring
disputes. This is a finite engineering choice, not a significance or
non-inferiority claim.

## Actual resource use

|S1 policy|Cells attempted|Requests|Local accrual USD|Sum of cell elapsed seconds|Input/output tokens|Read-tool calls|
|---|---:|---:|---:|---:|---:|---:|
|Direct API|9|17|0.043697|194.906240|23440 / 34343|0|
|Tool policy|9|9|0.031590|140.149312|21462 / 24624|0|

The complete stage made **32 real requests**, including S0, within its separate
60-request/$2 local authorization. All 32 reservations settled from trusted
provider usage; unmatched in-flight requests and new unknown holds were zero.
Peak-price local accrual was **$0.077874**: S0 $0.002587 plus S1 $0.075287. This is
conservative local accounting, not a supplier invoice. Historical $0.66 unknown
reservations remain in their original ledgers.

Elapsed totals include recorded preparation, tool and provider phases. Both
policies had the same ceilings, not the same actual consumption. All nine tool
cells ended or failed after their first request: six returned `finish`, one
`ask_user`, one stopped without a call, and one exhausted the output allowance.
No `search`, `read_symbol` or `read_caller` action occurred. Lower observed cost or
time therefore cannot be attributed to evidence acquisition. This whole-policy
comparison does not isolate the causal contribution of tools or verification.

## Failure evidence

|Recorded failure|Direct API|Tool policy|Total|
|---|---:|---:|---:|
|Diagnosis field length violation|5|3|8|
|Incomplete output at the token limit|1|1|2|
|Missing terminal tool action|0|1|1|

The eight field failures reproduced with the original validator: four unknowns
items exceeded 200 characters, two causes exceeded 600, and two triggers exceeded
300. All ten requests in these cells carried the actual limits; tool finish
schemas used the same constraints. No omitted-limit or false-rejection
implementation defect was established. The original failures were preserved.

Both incomplete responses reported `finish_reason=length`, 8192 completion
tokens and 8192 reasoning tokens. More total tokens would not guarantee a valid
answer. The remaining failure returned `stop` on its first decision without a
tool call, despite declaring the allowed tools with `tool_choice=auto`; the
frozen policy required `finish` or `ask_user`. It was not a tool-name rejection.
No tool-name, transport, identity or accounting failure was recorded in this
batch. Contract failure is not by itself evidence of a wrong causal mechanism;
it is a failure of the submitted policy to deliver a usable result.

## Decision and limits

Prefer the simpler direct API route for further diagnostic product work. Keep
the tool-capable harness as an explicitly approved research option, without
making it the default or advertising an established benefit. High contract
failure rates remain a reliability limitation for **both** policies. The static
no-model product path and remote-off research boundaries remain intact.

This stage is closed: do not tune on these tasks, repair the frozen scores or
spend unused authorization on another run. A new paid study needs a separate
bounded purpose and approval. Nine synthetic tasks cannot establish broad
accuracy, universal net benefit or absence of training contamination. Custodian
role separation is not OS isolation, and anonymous packet contents can reveal
strategy. Raw requests, outputs, held-out answers, task-category maps and
credentials are excluded from this publication.

Execution: commit `ed406b7045c1a86064a74666ded0dfe95bb5ab01`, plan
`3428f7a1e1b9f60181d7c2a6e3d76b331422eb07866fde49bcca1ff0a39e0bb1`.
The custodian's final lock SHA256 is
`eb1b33956f039d6c8eecea6a9b55906e98fba8c86b9e739e1bfdfc9e96b1fa13`;
its sanitized aggregate SHA256 is
`101902e53ef8ad5d74e9c927bbe17f8a68e11cb47b8e586f04731989c398ad20`.
The executor verified these bindings and all seven complete result/review hashes
without importing private task categories or reconstructing the private report.
