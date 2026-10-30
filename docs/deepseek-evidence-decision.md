# Evidence-decision contract, version 1

The original study entry stays offline. The separate
[two-stage pilot candidate](deepseek-comparison-pilot.md) requires exact phase
approval and, for real U, a qualified real R receipt; no live access follows
from selecting this profile.

`study-evidence-v1` / `deepseek-study-evidence-v1` is an explicitly selected,
**offline-only** decision-criteria version. It extends the existing v2 criteria
entry; it does not replace the default or `study-8192-v2`. Compared with
[`b20d119`](https://github.com/Oliveryfaso/multi-agent-code-review-lab/commit/b20d11984ec00520c6a887a9732d13e68876f47d),
only the shared decision standard changes. Five output fields, tools, read/call
limits, high thinking and generation/billing controls are retained. No agent,
automatic semantic judge or extra model request is added.

```sh
python3 -I -S -B scripts/run_offline_tests.py test_deepseek_evidence_decision
python3 -I -S -B scripts/run_deepseek_study_dryrun.py --profile study-evidence-v1 --out /ABSOLUTE/REAL/OUTSIDE_REPO
```

The second command generates known development fixtures and scripted responses,
not unseen questions or model calls. Its response count is not a success rate.
The existing live proposal rejects this profile before credentials or a claim.

## What changes in the serialized request

All four arms receive the same additional standard: identify whether the asked
proposition is a code property or a current external outcome; separate code
proof from supplied task facts; accept given facts without demanding production
proof. A gap is decisive only when two compatible states change that proposition.
Explain the distinguishing condition and outcomes concisely, without requiring
a reasoning transcript. Otherwise give the code-supported conclusion even when
irrelevant external details remain unknown. A static counterexample proves a
code defect, not that an incident occurred in the current deployment.

When the arm can read, retrieve decisive authorized source within the remaining
budget. A failed/unavailable read is an access gap, not proof the complete
snapshot lacks the answer. For a true external gap, request the smallest
sufficient fact or jointly necessary set, its applicable environment and how it
changes the conclusion. Equivalent sufficient observations are acceptable;
complete logs or raw records are not automatically necessary.

Use the existing `unknowns` or `ask_user({gap})` space. `finish(unknown)` retains
null `cause`/`when`. Short and detailed terminal actions have the same semantic
standard; neither resolves a missing fact. The runner retains the original
candidate and review-pending status, including schema-valid wrong mechanisms.
Nondecisive `unknowns` can coexist with a definite diagnosis. No keyword gate
forces all uncertainty into abstention, and no host correction invents evidence.

Old default and v2 serialized system bytes are pinned in regression tests. The
new prompt version participates in manifests, cell identity and existing source
fingerprints. Rehashed profile/prompt substitution is refused. Bounds remain
8192 combined reasoning/answer output per request,24576 across at most three
requests,8k/24k planned input and the derived$0.33 request reserve. There is no
independent answer reserve, guaranteed answer, new allowance for long reasoning
history or supplier bill hard cap. The third adaptive request still advertises
only `finish`/`ask_user`; illegal reads fail without a fourth request.

## Independent semantic review handoff

Use the existing hash-bound locked review (`result_sha256`, `status`, `rationale`,
seven `flags`) and independent custodian merge. The reviewer checks the task's
scope, provenance of every decisive fact, compatible alternatives, minimum
evidence sufficiency, correct conclusion dependency, actual visible source and
arm permissions. Cite the basis concisely in `rationale`. Legal references and
verdict agreement alone do not prove a correct mechanism.

| Candidate behavior | Existing scoring treatment |
| --- | --- |
| Correct definite mechanism, trigger/domain and support | `full_solution` |
| True decisive external gap, sufficient minimal evidence and correct dependency | `reasonable_abstention` |
| Relevant gap with incomplete/wrong dependency or insufficient request | `partial` or zero complete credit; explain in rationale |
| Missing authorized code for this arm | `arm_evidence_gap`; no full-snapshot necessary-abstention credit |
| Visible facts suffice but candidate asks for unrelated information | `unnecessary_abstention` |
| Wrong definite direction on healthy/defect tasks | `false_positive` / `false_release`, exactly matching category and verdict |
| External-gap task receives an unsupported definite conclusion | No full credit; explicitly report erroneous certainty in rationale/summary |
| Truncation, malformed output/tool action or identity failure | Original failure class; no invented semantic score |

Keep existing flag combinations; do not mix complete necessary-abstention credit
with `partial`. `ask_user` has no `refs` parameter: review its supplied source and
trajectory rather than requiring an illegal key. It ends unresolved with no
human answer. Do not treat an arm-only source gap as a whole-snapshot external
gap, or a narrow zero false-positive count as proof of complete correctness.
The existing runner stops non-ok reads without a follow-up request
(`read_tool_invalid` for ordinary access failures). These criteria add no
recovery. Development group4b is a hypothetical manual access-gap scoring
scenario, not a result emitted after such a failure.

The [eight development groups](deepseek-evidence-decision-development.md) contain
public, authored positive/negative candidates for independent manual semantic
review. Their oracle is separate from executable fake-transport tests; neither
is a model-quality result. No scored nine-task answer or private gold was used.

## Preregistered comparison rules; no live batch authorized

The future comparison is baseline `b20d119` / `study-8192-v2` versus this decision
version, within each of the same four arms. Primary paired evidence is adaptive
baseline versus adaptive candidate; other arms are reported separately. Hold
model settings, source/evidence permissions,8192/24576 output caps and budget
rules fixed; show actual requests, input/output/reasoning tokens, fees and
latency. Old2000-token results are historical and cannot isolate this change.

Use one repeat per version/arm/case, grouped by case, with seed20261030 shuffling
the eight version/arm cells within each group. No retry or selection of a best
answer. With N qualified unseen cases the full plan is8N cells and at most24N
requests. N, actual neutral package/custodian/rubric hashes, source allowlist,
implementation commits, dollar budget, model/endpoint and logging scope must
be frozen and separately approved before execution; they are not supplied or
approved by this document. Do not reuse prior spend authorization.

Before that approval, the independent custodian must freeze unseen material
partitioned by project/mechanism from these public fixtures, and task strata.
The executor receives only the neutral package; private gold remains with the
custodian. Anonymous semantic reviews lock before unblinding; text may reveal
strategy, so do not promise perfect blinding or OS-level isolation.

Pin the first independently verified provider identity for the comparison;
missing/drifting identity or untrusted usage stops the batch under existing
rules. Unknown charges keep reservations. Do not bridge identity gaps, replay a
cell or silently pool segmented versions. Known contract failures stay in the
full denominator without automatic retry.

Report complete solutions on solvable cases and complete necessary abstention
on true external-gap cases separately, plus unnecessary abstention, sign errors
and erroneous certainty. Include every planned cell and failure, alongside the
conditional reviewable-output denominator. Show paired counts for both semantic
outputs, pairs containing known contract failures and identity-incomparable pairs.
Directionally promising requires strictly better paired necessary-abstention
credit, no lower paired complete-solution credit, and no higher unnecessary
abstention count; otherwise report mixed/inconclusive evidence. Small samples
support no general or isolated tool-benefit claim. Freeze these acceptance rules
before results, and do not change questions or rules after seeing outputs.
