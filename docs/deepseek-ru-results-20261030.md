# R/U regression and incomplete paired study

The approved two-stage execution made **33 real DeepSeek requests**, with
**$0.051547 in local usage-based accrual**. R completed and met its machine
promotion gate. U then stopped at its frozen third-contract-failure threshold;
14 of its 18 planned cells were not attempted. The incomplete U stage provides
no paired semantic comparison or evidence of an increment from the newer policy.

This is a separate execution from the [earlier nine-task study](deepseek-neutral-study-results-20261029.md).
See the [two-stage protocol](deepseek-comparison-pilot.md) for preparation,
promotion, budgets and stop rules. No failed cell was retried, and the historical
$0.66 unknown reservation remained in its original ledger.

## Full denominators and locked semantic review

| Phase | Complete solution | Necessary abstention | Partial | Incorrect / insufficient among reviewed outputs | Contract failed | Not attempted | Planned cells |
|---|---:|---:|---:|---:|---:|---:|---:|
| R | 4 | 1 | 1 | 0 | 1 | 0 | 7 |
| U | 0 | 0 | 1 | 0 | 3 | 14 | 18 |

R consists of seven selected historical truncation cells from five known tasks.
Its six reviewable outputs received independent semantic review: four complete
solutions, one necessary abstention and one partial solution. A necessary
abstention is a complete disposition of an evidence gap, not a solved defect.
The remaining cell failed its output contract. R is a known-failure regression;
neither its machine gate nor these selected cases establish unseen quality,
tool benefit, or a causal effect of the larger output budget.

U planned nine new synthetic tasks with two adaptive-retrieval profiles:
`study-8192-v2` and `study-evidence-v1`. Its sole reviewable output was partial
and received no complete-solution or necessary-abstention credit. The other
three processed cells failed contracts. Of nine planned task pairs, none has
two semantically reviewable outputs; two involve contract failures and seven
were not attempted. No profile win, tie, accuracy estimate or expansion-gate
result is inferred from the surviving output.

The seven reviewed outputs had no recorded false positive, false release,
unnecessary abstention or arm-only evidence gap. These are reviewed-output
observations, not guarantees about the four failed or fourteen unattempted
cells. First answers were auxiliary observations and added no samples.

Anonymous scores were locked before keeper/profile alignment, then matched to
the original mappings and exact complete-result digests using the strict native
review validator. Failed and missing cells received no fabricated reviews or
task classifications. One completeness disagreement retained the partial grade
after bounded arbitration; no scoring dispute remains open. Independent source
review and custody review were separated, but the coordinator had seen aggregate
terminal counts and content can reveal a strategy. This was not complete double
blinding or verified OS isolation.

## Actual requests, cost and machine outcomes

| Phase / profile | Planned cells | Reviewable | Contract failed | Not attempted | Requests / ceiling | Local accrual / new budget |
|---|---:|---:|---:|---:|---:|---:|
| R: `study-8192-v2` | 7 | 6 | 1 | 0 | 21 / 21 | $0.045670 / $1.00 |
| U: `study-8192-v2` | 9 | 1 | 1 | 7 | 6 | $0.003195 |
| U: `study-evidence-v1` | 9 | 0 | 2 | 7 | 6 | $0.002682 |
| U total | 18 | 1 | 3 | 14 | 12 / 54 | $0.005877 / $1.50 |

The official `deepseek-flash` Chat Completions endpoint used thinking/high and
8192 total reasoning-plus-answer output tokens per request, with at most three
requests per cell. Both U profiles had the same configured ceilings; actual
consumption and the incomplete denominator remain separate from those ceilings.
No independent answer-token reservation was available.

| Phase | Input tokens | Output tokens, including reasoning | Cache-hit input | Cache-miss input | Claim-to-summary elapsed |
|---|---:|---:|---:|---:|---:|
| R | 25,123 | 36,033 | 17,408 | 7,715 | 238.289 s |
| U | 25,377 | 2,437 | 15,872 | 9,505 | 23.860 s |

Elapsed time includes phase preparation and tools, not only provider latency.
Charges use the frozen peak rates: input hit $0.006/M, miss $0.30/M and output
$1.20/M ([official price source](https://api-docs.deepseek.com/quick_start/pricing/),
checked 2026-10-30). They are local calculations from provider usage, not a
supplier invoice or supplier-enforced account limit. The full-cache-miss
sensitivity is $0.061329, also a local calculation. No new unknown cost hold
remains; no old reservation funded this execution.

Independent machine review verified all 33 unique request bindings, per-request
settlements, one consistent validated model fingerprint, both approvals and the
genuine R receipt used for U. R had seven terminal cells, six contract-valid
reviewable results, zero truncations and no operational anomaly, satisfying the
predefined machine gate. That gate did not score semantics.

## Four contract failures

| Phase / count | Verified category | Safe failure evidence |
|---|---|---|
| R / 1 | `eval_contract_invalid` | On the final self-check, one `unknowns` item had 216 characters, exceeding the 200-character limit. Native replay accepted rounds 1–2 and rejected round 3. The actual serialized requests already included the matching length constraint. |
| U / 3 | `model_response_invalid`: `assistant_message / tool_function_name` | All occurred on decision 3, when only `finish` and `ask_user` were declared. HTTP was 200 and observed finish reason was `tool_calls`; model identity and billable usage remained verifiable. |

The historical U diagnostic cannot distinguish an invalid name syntax from a name outside
that request's declared set. Exact returned names were not retained, so no
particular read tool or other field is blamed. These failures are neither
unknown-cost events nor established semantic wrong answers. The third U failure
stopped the stage as registered. Its processed sequence was failure, reviewable,
failure, failure: the threshold counts cumulative failures, not consecutive
failures. Those three failed requests used 227, 71 and 120 output tokens, well
below 8192; they do not demonstrate token exhaustion. Eighteen pure normalization
regressions, including supported integer-index extensions, passed offline; no
erroneous rejection of a supported response has been established. These test
checks are not additional model samples. No output was trimmed, contract relaxed, frozen
result replaced, or stop lifted to improve the score.

A subsequent offline patch adds only fixed reason labels for future name-type,
name-format and undeclared-name failures, plus bounded decision/tool-set context.
It preserves the broad error code, action validation, field limits, billing and
identity evidence, and cumulative stop threshold. Synthetic three-turn and field
boundary tests cover these paths. It does not reconstruct the missing historical
names, change the frozen requests or claim improved model behavior.

## Frozen provenance and limits

- Execution code: `31519587847c32c1515173a68ce4b0b46f87d133`.
- R plan: `fa8db4ab60ad8c313fc9b69abd08a47554289a77556a028943511283acc4b3bc`.
- U sealed scope: `241e43da01df986e58f0fa4ba51094334c4cee61558de0343781fa6a292fb35d`.
- U final plan, derived after eligible real R: `042471e338b25067df40c1f8ae47db1f4d9c2617bb93d681db287234a5564a37`.
- Locked anonymous review files: R `ee76176670c9d91731d308ca26ffd74db7a7577b1c0290a834320b9c3fcd6d61`; U `397ce114de0fd7c8c7c3f380b618f21d887c9cd7205dd59caaf25d8e04ea43fa`.

Only aggregates and opaque provenance hashes are published here. New case
answers, gold, rubrics, keeper mappings, source/request/output logs, credentials
and personal configuration remain private. The small, selected R sample and
stopped U sample support the operational and descriptive observations above,
not generalization or a policy-gain claim. U remains stopped; this report grants
no additional calls or recovery.
