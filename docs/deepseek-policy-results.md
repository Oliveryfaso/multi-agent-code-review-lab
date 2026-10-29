# DeepSeek policy results and limits

This page contains aggregate research outcomes only. Raw requests, responses, reasoning, credentials, machine configuration and operating records are private local artifacts and are not part of this patch.

## What actually ran

Two initial pilots used twelve author-known synthetic development cases and four planned arms. The first stopped on a citation-structure failure; the prompt-revised pilot stopped with `model_response_invalid` and no validated response. A separately approved recovery selected only unattempted logical cells and stopped on its first request. After the compatibility repair, a second continuation attempted 15 of its 44 cells before an evaluator-contract failure stopped it. The final version records eligible, fully settled contract errors as cell failures and processed all 29 remaining cells. This completes operational coverage of the logical grid, not a fresh, single-version four-arm experiment.

| Pilot | Planned cells | Structurally valid, pending review | Failed cells | Unattempted cells | Actual model requests |
| --- | ---: | ---: | ---: | ---: | ---: |
| Initial prompt | 48 | 0 | 1 | 47 | 1 |
| Revised citation prompt | 48 | 2 | 1 | 45 | 4 |
| Retained-unknown recovery | 45 | 0 | 1 | 44 | 1 |
| Compatibility-repaired continuation | 44 | 14 | 1 | 29 | 18 |
| Known-failure remainder | 29 | 21 | 8 | 0 | 43 |

These overlapping plans are separate denominators over the same development tasks, not independent new examples. The revised pilot's planned arms each had twelve cells: A produced one reviewable cell, C produced one after two requests, B had no execution, and D failed on its first request. Its unattempted counts were A 11, B 12, C 11 and D 11.

The first failure used a human-readable citation representation that did not satisfy the strict machine contract. The revised prompt communicates the canonical citation schema and gives D's terminal tool the same schema. Old failures were not repaired or rescored as successes.

The D failure has no validated response or usage. The retained placeholder cannot distinguish transport JSON decoding from provider-envelope validation, so it cannot establish a model reasoning error or the supplier's actual raw response. Its tools were not executed. The failure-observation upgrade adds safe stage/shape metadata for future diagnosis; it cannot reconstruct this missing evidence.

The recovery attempted only p01/D. It received HTTP 200 and a JSON response, then failed at assistant-message validation before any tool executed. The bounded diagnostic records one extra tool-call field, without its name or value. No answer or candidate was accepted: this is not reviewable as a diagnosis, and conditional answer quality is N/A. The newer adapter normalization does not retroactively repair that frozen result. See the [compatibility contract](deepseek-provider-compatibility.md).

The 44-cell continuation used the repaired adapter and a separately confirmed second recovery contract, excluding every previously claimed logical cell across the lineage:

| Arm | Planned | Accepted, pending official review | Failed | Unattempted | Real requests |
| --- | ---: | ---: | ---: | ---: | ---: |
| A: direct | 11 | 5 | 0 | 6 | 5 |
| B: initial evidence | 12 | 4 | 0 | 8 | 4 |
| C: candidate plus verifier | 11 | 3 | 0 | 8 | 6 |
| D: bounded tools | 10 | 2 | 1 | 7 | 3 |

All 18 provider responses had usable measured usage. p12/D returned a parsed `finish` action, then failed because its `when` string had 324 characters against the advertised 300-character limit. The error is `eval_contract_invalid`, after provider parsing; no answer was accepted. Its cost is known locally, and the registry is `batch_halted_contract` even though the money ledger has no new unknown hold. The failed diagnosis was not shortened, retried or rescored. The remaining 29 cells were not attempted in that version.

### Final known-failure remainder

The new frozen cell-failure policy processed exactly those 29 cells, without retrying any earlier claim. Contract failures remain failed results. Only allowlisted output/action errors with trusted settled usage and received, stable identity can advance; unknown, authentication, scope, budget, accounting and identity failures remain fatal.

| Arm | Planned / processed | Accepted, pending official review | Contract failed | Real requests | Read tools | Local accrual USD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 6 | 5 | 1 | 6 | 0 | 0.006446 |
| B | 8 | 8 | 0 | 8 | 0 | 0.007158 |
| C | 8 | 3 | 5 | 15 | 0 | 0.018018 |
| D | 7 | 5 | 2 | 14 | 14 | 0.010258 |
| Total | 29 | 21 | 8 | 43 | 14 | 0.041880 |

There were zero fatal halts, unattempted cells or open claims. Four failures were verifier-contract errors, three diagnosis-contract errors and one output truncation. None was repaired, shortened or retried. All 43 requests settled from trusted usage; no new unknown hold arose. `batch_complete` means all cells were processed, not that all diagnoses passed.

The four verifier errors also expose a harness interface defect: frozen requests asked for a short reason but did not communicate the validator's 256-character cap. Their reason lengths were 307, 583, 262 and 279. `response_format: json_object` did not deliver the locally held schema. These failures are confounded by missing contract communication and must not be interpreted as reasoning degradation or evidence that verification harms diagnosis. Their failed records remain unchanged. The offline-only `contracts-v3` repair serializes the verifier schema and aligns other output guidance; it has not been tested in another paid run.

Across all versions, 49 cell executions cover all 48 unique task/repeat/arm identities; the extra execution is an earlier prompt-version repeat. The versions, stop rules and incomplete historical responses remain separate. This coverage count is not a pooled accuracy numerator or a new independent dataset.

## Independent post-hoc semantic review

The two structurally valid cells in the revised pilot were reviewed against the authorized source and development rubric:

| Cell | Mechanism | Trigger/domain | Citation support | Post-hoc conclusion |
| --- | --- | --- | --- | --- |
| p02/A | Supported | Partial: the claimed failure domain was too broad | Supported | Partial support; not a complete semantic pass |
| p12/C | Supported | Supported | Supported | Candidate and model verification supported |
| p09/D | No validated content | Not reviewable | Not reviewable | Remains a response-contract failure |

Thus the two reviewable cells yielded one supported and one partial judgment. The full revised plan remains 48 cells: two structurally valid, one failed and forty-five unattempted. The immutable records remain pending/failed; these post-hoc judgments did not rewrite them. A matching verdict or model-verifier acceptance was not counted as sufficient proof.

These were independent post-hoc audits with task/arm and gold available, not formal blind review. No complete accuracy estimate, false-positive/abstention profile, unseen generalization or causal tool benefit can be inferred. The initial and revised prompts must not be pooled into an unconditional four-arm comparison.

### Compatibility-repaired continuation: separate post-hoc audit

Independent reviewers checked mechanisms, trigger domains, actual request evidence and citations for all 14 accepted outputs. Official records remain pending; the audit did not alter them.

| Assessment | Cells |
| --- | --- |
| Supported definite diagnosis | p05/A, p05/D, p06/A, p09/A, p10/A |
| Partial diagnosis: correct mechanism but trigger overclaim | p07/B, p07/C |
| Correct full-snapshot evidence gap | p01/A, p04/D, p11/B |
| Correct gap, partial explanatory completeness | p04/C |
| Defensible gap under the arm's initial evidence; task unresolved | p02/B, p03/B |
| Defensible initial-evidence gap, incomplete full-snapshot gap | p08/C |
| Contract failure; no accepted answer to score | p12/D |

For p07/B and C, an allowed counterexample supports the defect, but the claim that every multi-element tuple fails is too broad. C's model-verifier acceptance does not repair that error. p02/B and p03/B lack helper bodies in their initial evidence, while those helpers exist in the authorized snapshot; their specific abstentions are defensible for that arm but do not solve the task. p08/C does not identify the full-snapshot missing deployment topology. p04/C identifies the missing ABI without the complete conversion explanation.

No false definitive defect or healthy verdict, or clearly unnecessary abstention, was observed among these 14 accepted outputs. That observation is conditional on this small, interrupted development subset, not a full-plan error rate. All 15 attempted cells executed zero read tools; D terminated on its first decision in each case. Consequently, this run does not measure the benefit of active evidence retrieval. A receives full source while B/C/D begin with selected evidence, so this is also not a strict same-evidence comparison.

### Final remainder: separate post-hoc audit

Independent gold-aware reviewers assessed all 29 cells. Of the 21 accepted outputs, ten definite diagnoses have supported mechanisms, trigger domains and citations. One definite diagnosis, p02/D, is partial: it identifies iterator exhaustion and a valid example, but incorrectly extends the trigger to every nonempty tuple. Nonempty all-zero tuples can still meet the requirement.

Six accepted abstentions identify genuine full-snapshot deployment gaps; p04/A and B have partial explanatory completeness. Four more abstentions identify helper bodies missing from their arm's initial evidence, although those bodies exist in the authorized snapshot. They are defensible under that evidence but do not resolve the full task. All eight contract-failed cells remain unscored, including candidates whose verifier failed and the truncated D response. No false definitive defect/healthy verdict or clearly unnecessary abstention was observed among the accepted outputs; this is a conditional, unblinded observation, not a full-plan accuracy estimate.

D executed 14 read tools: nine searches and five symbol reads. p03/D retrieved `ring.py` and produced a supported healthy diagnosis; p03/C abstained with its initial evidence. p10/D used search and a symbol read to obtain the encoding helper and reached a supported diagnosis, using two model requests against A's one; this is not a matched-call comparison. p02/D retrieved `metrics.py`, yet its final diagnosis still overclaimed the trigger. p08/D retrieved its helper but exhausted its response output cap. These examples demonstrate working evidence retrieval and its limits, not an isolated causal tool benefit. Tasks are author-known and small, A has full source, D uses extra requests, and the final subset has different task counts by arm.

## Local cost evidence

All attempts shared an authorization of at most 84 requests and a $3 local budget. **67 requests were attempted in total**. Local conservative known accrual is **USD 0.062963**: USD 0.021083 carried into the final remainder plus USD 0.041880 for its 43 requests. **USD 0.66 remains in unresolved reservations** from the two older unknown responses. All new failures have trusted usage and add no unknown hold. Neither old reservation is released.

These figures are derived from trusted response usage at the frozen peak-price method, rounded conservatively per request, plus retained reservations. They are not a supplier bill or a verified actual debit. Supplier charges for the two unknown responses have not been established. Current occupied local allowance is USD 0.722963. Nominal request headroom is 17, but there are no unattempted logical cells in this scope and no further run was started.

The first recovery selected 45 cells and stopped after one; its second continuation selected 44 and stopped after 15. The [known-contract cell-failure policy](deepseek-policy-cell-failures-v1.md) then selected the exact 29-cell remainder and processed it completely. All old stops, failures, locks and unknown holds remain intact. See the [recovery checklist](deepseek-policy-pilot-checklist-v1.md#explicit-retained-unknown-recovery).

Final-remainder usage was 38,724 input and 31,501 output tokens, including 26,971 reasoning tokens; input included 25,728 cache hits and 12,996 misses. Reasoning is already part of output, not a second charge. Summed local provider-call latency was 208,928 ms, excluding separate preparation/control overhead; it is not supplier server time or total experiment wall time. Model and fingerprint were stable within the batch.

## Offline validation and publication boundary

The preceding failure-observation candidate passed **190/190 related offline tests**, with no skips, and received independent narrow review. The initial integration-review patch passed **197/197 related offline tests**. Review then identified and fixed a possible echoed-input fallback: the launcher now rejects `GetPassWarning` before reading fallback input. The revised patch passed **201/201 related offline tests**, with no skips, including echo-control failure, terminal I/O failure and the normal no-echo path. A complete scripted dry run produced 48 cells and 72 fixture requests with zero live model calls. These checks exercised protocol fixtures, cumulative reservations, failure-stage metadata and the preservation of unknown costs; they were not measured diagnostic accuracy, remote CI, or evidence that this patch has been merged.

No new runtime dependency was required. The ordinary static evidence product still runs without calling a model or executing target source. This patch makes no claim of VM dynamic acceptance, production sandboxing, a completed live comparison or a measured improvement over direct API use.

The retained-unknown recovery implementation subsequently passed 223 related offline checks. The response-compatibility repair uses synthetic/fake transports only, adds no paid result, and keeps all historical fees and outcomes unchanged. Validated future billing evidence is separate from usable assistant output; historical diagnostic counters are never promoted into settlement evidence.

The compatibility code passed 311 related offline checks. The bounded second-recovery extension passed 320 checks in two disjoint guarded groups, plus independent review; both published CI workflows passed for its exact implementation commit. Those fixtures are separate from the 18 real requests reported above. All 103 pre-existing frozen JSON files and the new batch's 21 implementation files matched their saved hashes after execution. No VM, target-code execution, private project source or gold was sent to DeepSeek.

The known-failure policy passed 335 related checks in two guarded groups, plus independent review. An initial 43-cell fake fixture hit the unchanged 120-second guard; final tests instead covered three actual sequential fixture cells and a separate completion/pre-credential boundary check. They do not claim a full 43-cell offline completion. The 29 real cells above are measured execution, separate from those fixtures. All 271 earlier frozen JSON files and all 21 final implementation files matched their saved hashes after the run. Independent accounting reconciled every new request and settlement. Raw requests, answers, reasoning, credentials and operating records remain private.

The offline-only `contracts-v3` correction passed 341 related checks, including serialized request inspection, character/UTF-8 boundaries and legacy-read/new-execution separation. Independent review passed. No new paid result or retroactive score was created; future model compliance and performance remain unmeasured.
