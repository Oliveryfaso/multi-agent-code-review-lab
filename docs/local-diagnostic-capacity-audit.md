# Local diagnostic capacity audit

The previous CPU evaluation reserved 1536 input tokens and 512 output tokens in a 2048-token context, with a 300-second call timeout. These were experiment settings, not the cached models’ training-context limits. All three 4B candidates reached the 512-token output ceiling with `finish_reason=length`. They are technical incomplete outputs, not completed semantic errors. Their original evidence and scores remain unchanged.

The new bounded configuration reserves 3072 input tokens and 1024 output tokens in a 4096-token context, with a 600-second call timeout. Candidate and verifier use the same explicit ModelBudget. The input allowance accommodates the actual candidate included in the verifier request. ModelProfile, server argv, runtime properties, token accounting and native response checks must all agree; changing a request default alone does not change the evaluated runtime. The historical builder defaults and legacy v2 wire format remain available for reproduction.

| Layer | Audit result | Action |
| --- | --- | --- |
| Output/context | 512/2048 were artificial capacity settings | New run uses 1024/4096 with measured usage |
| Input | Original candidates used 979–1030 tokens; verifier includes generated candidate | Reserve 3072 without adding source |
| Timeout | CPU speed differs by model; socket timeout does not cancel remote generation | 600 seconds plus owned monitor, idle proof and process closure |
| v3 field lengths | Preferred lengths are soft targets | Preserve complete explanation; retain legacy v2 limits |
| Parser/HTTP | Whole-output 8192 bytes; request/response 1 MiB | Retain resource bounds and record any rejection separately |
| Test quota | Closed batches cannot donate unused calls | New exclusive batch, at most two starts and 24 generations |
| Resource protection | M3 with 16 GiB RAM; one CPU service at a time | Preserve 4B 5.5 GiB / 7B 8 GiB RSS guards, free-memory and disk floors |

Doubling context adds an estimated 288 MiB of KV cache for 4B and 112 MiB for 7B, based on the old runtime logs. These estimates are not measurements of the new configuration. A 6144-token context could push 4B above its existing RSS guard, so it is not selected. No new model, VM, download or external model API is required.

The development comparison holds capacity constant while changing prompt examples. Both current arms use the enlarged configuration and unchanged source, scenario, schema, system text, sampling and scoring. Only candidate `shape_examples` differs: the historical filled examples versus an empty list. For the same candidate, the verifier request is byte-identical; its own `shape_example` remains. The actual verifier receives its arm's generated candidate. Comparison with the earlier 512-token run changes context, reservations, timeout and the source-scope binding together. It does not isolate the causal effect of output length alone. Each model runs filled cases before empty cases, once each; order and time effects are not eliminated.

All cases are previously seen development cases. There are no fresh acceptance calls, live prompt repairs or automatic retries. Report finish status, JSON/contract validity, conditional semantic quality, healthy false positives, defect recall, specific necessary abstention, verifier correctness, final completion, actual token usage, time and memory separately. Empty semantic denominators remain null. A valid JSON fragment with `finish_reason=length` remains incomplete, and verifier refusal does not prove correction or a correct abstention.

The closed batch used two sequential CPU services and 21 of its 24 generation keys: 12 candidates and nine verifiers. Three 4B candidates reached the enlarged 1024-token output ceiling, and their verifier keys were closed unused. Eighteen generations completed without truncation. Two completed 4B candidates used 680 and 555 output tokens, and three verifier inputs used 1547, 1686 and 1602 tokens. The old limits would have constrained these requests. This demonstrates capacity use, not improved reasoning.

| Model / candidate examples | Candidate JSON and contract | Correct labels on completed candidates | Correct complete candidates | Accepted final / product E2E |
| --- | --- | --- | --- | --- |
| 4B / filled | 2/3 | 0/2 | 0/2 | 0/3 |
| 4B / empty | 1/3 | 0/1 | 0/1 | 0/3 |
| 7B / filled | 3/3 | 2/3 | 0/3 | 0/3 |
| 7B / empty | 3/3 | 2/3 | 0/3 | 0/3 |

The 4B filled arm falsely reports a defect in its one completed healthy case. The empty healthy case truncates, so its conditional false-positive rate is null. Both 7B arms incorrectly abstain on the healthy case: zero false positives does not imply correct healthy recognition. Both report the defect label but lack a correct complete defect witness. Neither model supplies the specific missing information needed for correct abstention on the underspecified case. Final label, false-positive and recall conditionals are null because there are no accepted finals. The machine-readable [results](local-diagnostic-capacity-results.json) keep these separate denominators.

The 4B verifier contains one valid predicate refutation, but also has incomplete or inconsistent coverage; it does not deliver a corrected final. The 7B filled arm exactly matches two candidate example objects; removing them changes wording without fixing caller, requirement or counterfactual coverage. All eligible candidates fail complete semantic review. All four strata have zero accepted final deliveries. Independent grading used the frozen calibrated criteria; it did not soften scoring or treat rejection as correction.

Actual usage was 22,680 input and 8,411 output tokens, with zero cache tokens. The batch took 1951.6 seconds (32.5 minutes); generation HTTP time was 1923.3 seconds. Sampled RSS peaked at 4,828,954,624 bytes for 4B (4.50 GiB) and 7,302,316,032 bytes for 7B (6.80 GiB). Minimum observed free memory was 46% and 33%. Neither service triggered its resource guard. Both exited with code zero, their owned listeners and PIDs were absent, and monitor threads completed. Swap was not measured. These are sampled resource observations, not continuous maxima.

The empty-example behavior is not promoted. The original filled-example default and overall `fact_checks=False` default are preserved, with both experimental arms retained locally. The final code change lets b11521 evidence validation accept an explicit enlarged capacity while preserving its historical defaults and checks. Offline tests cover the actual candidate and verifier budgets and preserve `finish_reason=length` as technical incomplete even when the fragment is valid JSON.

Verification passed 130 distinct product tests and five distinct controller tests on the measured source. After restoring the default, six relevant offline tests passed and all 21 captured requests were reconstructed exactly from the final builder using the frozen arm control. This establishes input equivalence, not native qualification of the final revision; no native replay was made. Initial failed test receipts remain retained locally. There were no fresh acceptance calls, new models, VMs, downloads, external model API calls or automatic repairs. This task remains local and does not trigger a push or new CI run. The models remain unqualified for reliable complete diagnosis.
