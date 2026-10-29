# DeepSeek policy results and limits

This page contains aggregate research outcomes only. Raw requests, responses, reasoning, credentials, machine configuration and operating records are private local artifacts and are not part of this patch.

## What actually ran

Two pilots used twelve author-known synthetic development cases and four planned arms. The first stopped on a citation-structure failure; the prompt-revised pilot stopped with `model_response_invalid` and no validated response. They did not complete the comparison.

| Pilot | Planned cells | Structurally valid, pending review | Failed cells | Unattempted cells | Actual model requests |
| --- | ---: | ---: | ---: | ---: | ---: |
| Initial prompt | 48 | 0 | 1 | 47 | 1 |
| Revised citation prompt | 48 | 2 | 1 | 45 | 4 |

The two plans are separate denominators over the same development tasks, not independent new examples. The revised pilot's planned arms each had twelve cells: A produced one reviewable cell, C produced one after two requests, B had no execution, and D failed on its first request. Its unattempted counts were A 11, B 12, C 11 and D 11.

The first failure used a human-readable citation representation that did not satisfy the strict machine contract. The revised prompt communicates the canonical citation schema and gives D's terminal tool the same schema. Old failures were not repaired or rescored as successes.

The D failure has no validated response or usage. The retained placeholder cannot distinguish transport JSON decoding from provider-envelope validation, so it cannot establish a model reasoning error or the supplier's actual raw response. Its tools were not executed. The failure-observation upgrade adds safe stage/shape metadata for future diagnosis; it cannot reconstruct this missing evidence.

## Independent post-hoc semantic review

The two structurally valid cells in the revised pilot were reviewed against the authorized source and development rubric:

| Cell | Mechanism | Trigger/domain | Citation support | Post-hoc conclusion |
| --- | --- | --- | --- | --- |
| p02/A | Supported | Partial: the claimed failure domain was too broad | Supported | Partial support; not a complete semantic pass |
| p12/C | Supported | Supported | Supported | Candidate and model verification supported |
| p09/D | No validated content | Not reviewable | Not reviewable | Remains a response-contract failure |

Thus the two reviewable cells yielded one supported and one partial judgment. The full revised plan remains 48 cells: two structurally valid, one failed and forty-five unattempted. The immutable records remain pending/failed; these post-hoc judgments did not rewrite them. A matching verdict or model-verifier acceptance was not counted as sufficient proof.

These were independent post-hoc audits with task/arm and gold available, not formal blind review. No complete accuracy estimate, false-positive/abstention profile, unseen generalization or causal tool benefit can be inferred. The initial and revised prompts must not be pooled into an unconditional four-arm comparison.

## Local cost evidence

The two pilots shared an authorization of at most 84 requests and a $3 local budget. Five requests were attempted in total. Local conservative accrual is **$0.004719**, with a separate **$0.33 unresolved reservation**. The revised pilot contributes $0.002672 of the accrued amount; the first contributes $0.002047. The unknown call is not treated as free and its reservation is not released.

These figures describe the two stopped historical pilots. They are derived from trusted response usage at the frozen peak-price method, rounded conservatively per request, plus a retained reservation. They are not a supplier bill or a verified actual debit. The supplier charge for the unknown response has not been established. Total nominal request headroom is 79; the historical batches remain halted and cannot dispatch again.

The revised pilot has 45 unattempted logical cells. Their worst-case request count is `11 + 12 + 2×11 + 3×11 = 78`, which fits the nominal request headroom. A separately confirmed retained-unknown recovery can freeze those cells as a new subset, carrying all prior requests, known accrual and the unresolved reservation. Arithmetic or a new directory alone cannot enable it. This optional path does not settle the unknown bill, establish supplier balance or prove a completed comparison. See the [recovery checklist](deepseek-policy-pilot-checklist-v1.md#explicit-retained-unknown-recovery).

## Offline validation and publication boundary

The preceding failure-observation candidate passed **190/190 related offline tests**, with no skips, and received independent narrow review. The initial integration-review patch passed **197/197 related offline tests**. Review then identified and fixed a possible echoed-input fallback: the launcher now rejects `GetPassWarning` before reading fallback input. The revised patch passed **201/201 related offline tests**, with no skips, including echo-control failure, terminal I/O failure and the normal no-echo path. A complete scripted dry run produced 48 cells and 72 fixture requests with zero live model calls. These checks exercised protocol fixtures, cumulative reservations, failure-stage metadata and the preservation of unknown costs; they were not measured diagnostic accuracy, remote CI, or evidence that this patch has been merged.

No new runtime dependency was required. The ordinary static evidence product still runs without calling a model or executing target source. This patch makes no claim of VM dynamic acceptance, production sandboxing, a completed live comparison or a measured improvement over direct API use.
