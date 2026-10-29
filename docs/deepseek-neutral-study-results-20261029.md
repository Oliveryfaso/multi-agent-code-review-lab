# Nine-task retrieval study: descriptive results

On this small synthetic study, adaptive retrieval (D) fully resolved **5/6
solvable tasks**, versus **1/6** for fixed retrieval (F), initial-evidence
self-check (A-I), and the full-source reference (A-FULL). This is a result for
the frozen policies and budgets below, not an isolated tool-function effect or
an established general advantage. F achieved one necessary abstention; D
achieved none. Output-contract failures and an identity-evidence gap matter to
the comparison.

The study used nine independently held synthetic tasks from three project units:
six solvable tasks and three genuine external-evidence gaps, four strategies,
one repetition, and **36 planned cells**. All cells were processed. The final
custodian review covers **25 outputs**, alongside **10 contract failures and
one identity failure**. No failed output was retried, trimmed, repaired or
rescored into success. Machine-readable aggregate data is in the
[companion JSON](deepseek-neutral-study-results-20261029.json); no task source,
answer, per-case score or private category mapping is included.

## Quality with the complete planned denominator

| Strategy | Full solution / 6 solvable | Necessary abstention / 3 gaps | Correct disposition / 9 | Reviewed | Contract failures | Identity failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A-I: initial evidence + self-check | 1/6 | 0/3 | 1/9 | 9 | 0 | 0 |
| F: fixed evidence | 1/6 | 1/3 | 2/9 | 5 | 4 | 0 |
| D: adaptive evidence | 5/6 | 0/3 | 5/9 | 7 | 1 | 1 |
| A-FULL: authorized full source | 1/6 | 0/3 | 1/9 | 4 | 5 | 0 |

Full credit requires a supported mechanism, trigger/domain and citations;
matching the expected verdict alone is insufficient. A necessary abstention
must identify a genuine gap across the complete authorized snapshot. Missing
information only from an arm's initial evidence can justify that arm's
abstention while leaving a solvable task unresolved.

There were **8/24 full solutions** across solvable-task slots and **1/12
necessary abstentions** across gap-task slots. Correct disposition combines
those two forms of credit: **9/36 planned cells**, or **9/25 conditionally among
reviewed outputs**. The latter excludes eleven failures and is not a full-plan
accuracy rate. Sixteen of 24 solvable slots were not fully resolved; eleven of
12 gap slots did not earn necessary-abstention credit.

The 25 reviewed outputs have mutually exclusive final categories: eight complete
solutions, one necessary abstention, eight partial and eight incorrect. The
native `partial` flag appears eleven times and can overlap other zero-credit
flags; it must not be added to those mutually exclusive counts. Three substantive
review disagreements were adjudicated: one full-credit downgrade, no upgrades
and no unresolved dispute. One identical prior output was counted once.

Each arm recorded **0/3 counted false defect verdicts on healthy tasks** and
**0/3 counted false healthy verdicts on defect tasks**. These narrow sign-error
counters exclude abstentions and contract/identity failures and do not detect
every wrong mechanism or overbroad trigger. Their denominators retain the three
planned task slots of each type. Zero counted sign errors does not mean all
answers were correct, all cases passed or the system is reliably safe.

## D versus F: paired outcomes

| Planned task set | D wins | Ties | D losses | Not comparable | Both independently reviewed | Includes known contract failure |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Six solvable tasks | 4 | 2 | 0 | 0 | 4 | 2 |
| Three external-evidence gaps | 0 | 1 | 1 | 1 | 0 | 2 |
| All nine tasks | 4 | 3 | 1 | 1 | 4 | 4 |

Solvable-task pairs compare full solution; gap-task pairs compare necessary
abstention. A known contract failure receives zero disposition credit, so the
six-task 4/2/0/0 includes two operational comparisons, not six independent
semantic-quality comparisons. The identity failure makes its affected pair
incomparable, never a tie. The all-task result includes one D loss and one
incomparable pair. D versus A-I has the same six-solvable 4/2/0/0; across all nine
it is 4/4/0/1.

## Actual consumption, including failures

All strategies used `deepseek-flash`, thinking enabled, effort `high`. A-I, F and
D shared the same initial evidence; A-FULL additionally received authorized
source. Strategies differ in control/tool declarations and effective history.
Each cell allowed at most three requests, 2,000 output tokens per request
(6,000 per cell), and four static reads; direct arms use no reads. D may finish
early. The study ceiling was 108 requests and $2 of local allowance. Equal
ceilings do not mean equal evidence, computation, calls or actual spend.

| Strategy | Requests | Input tokens | Cache hit / miss | Output tokens | Reasoning included in output | Read tools | Local accrual USD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A-I | 27 | 27,351 | 20,352 / 6,999 | 19,899 | 16,813 | 0 | 0.026114 |
| F | 25 | 26,960 | 18,688 / 8,272 | 27,308 | 24,463 | 9 | 0.035377 |
| D | 18 | 37,324 | 28,544 / 8,780 | 9,779 | 7,581 | 10 | 0.014549 |
| A-FULL | 20 | 21,377 | 16,384 / 4,993 | 22,349 | 20,043 | 0 | 0.028423 |
| Total | 90 | 113,012 | 83,968 / 29,044 | 79,335 | 68,900 | 19 | 0.104463 |

| Strategy | Sum of cell elapsed seconds | Sum of local provider-call seconds |
| --- | ---: | ---: |
| A-I | 135.274 | 135.062 |
| F | 182.537 | 182.291 |
| D | 73.681 | 73.458 |
| A-FULL | 151.232 | 151.051 |
| Total | 542.724 | 541.863 |

These are summed local measurements, not supplier server latency or total
experiment wall time. Raw precision and preparation/tool timing components are
in the JSON. Reasoning tokens are part of output, not an additional charge.
All 90 requests had usable settled usage. Costs are conservative frozen-peak-rate
local accruals, not verified supplier debits; full-miss sensitivity is $0.129147.
There was no new unknown hold. The separate older experiment's $0.66 unknown
reservation remains untouched. No unused allowance was spent on retries.

## Failures and interpretation limits

The eleven failures comprise seven output truncations (`model_output_incomplete`),
two result-schema failures (`eval_contract_invalid`), one failure to terminate
at the third decision, and the historical fingerprint-evidence failure. A-FULL
has four truncations and F has three. Their low full-solution counts therefore
do not establish that additional information or fixed retrieval worsens model
reasoning. Failure records remain in every planned denominator.

Offline inspection of the frozen receipts established:

- All seven truncations have `finish_reason="length"` and exactly 2,000 output
  tokens against the 2,000-token request ceiling. Six report 2,000 reasoning
  tokens and empty assistant content. The remaining F response reports 1,922
  reasoning tokens and 298 characters of assistant content. Reasoning/message
  envelopes and usage were retained; no unrecorded continuation is inferred.
- The two result-contract failures reproduce under the frozen validator: one
  A-FULL `unknowns` item is 233 characters against 200, and one F `when` value is
  321 characters against 300. Both occur at the third decision. The strings are
  neither shortened nor scored as valid outputs.
- D's third-decision failure requested one `search` action instead of `finish`
  or `ask_user`. That request carried the explicit terminal-only instruction,
  but still advertised the three read tools alongside the terminal actions.
  The client rejected it and performed no forbidden third read. This is a
  policy-contract failure; it does not establish that its unavailable final
  diagnosis would have been right or wrong.

All these content failures have settled, independently checked usage. The
historical identity failure has known cost but its missing fingerprint value
cannot be reconstructed from the presence-only diagnostic. It remains a
separate failure and makes the affected pair incomparable.

Execution used two frozen implementation/identity segments: three cells/seven
requests under `074dea2`, followed by only the untouched 33 cells/83 requests
under `3632ceb`. The historical seventh response has no recoverable verified
fingerprint. The 83 new request/response identities match the earlier verified
pin, but whole-study uninterrupted identity is not established. Arm and pair
numbers pool the two segments; no same-segment-only quality result is inferred.

Review scores were locked before per-output strategy mapping was opened.
Evidence quantity and answer shape can still reveal strategy; complete double
blindness is not claimed. The custodian knew frozen gold, delivery-level arm
totals and the prior identical-output review. Fifteen auxiliary first answers
were observed, thirteen were supplied for assessment; none is an extra sample.
Only three project units and nine synthetic tasks support descriptive results,
without significance, external-project or non-contamination claims.

The executor subsequently replayed the native strict import for all 25 locked
reviews against unchanged raw-result digests in both logical/runtime key spaces,
and reconciled the complete consumption totals. It did not read private gold,
task categories or adjudication records. Executor-only native reports retain
pending semantic metrics without that private merge; the quality tables here
are the custodian's frozen final aggregates, not reconstructed labels.

The records support a promising policy-level observation on these tasks. They
do not isolate pure reasoning or tool-function gains, establish that more source
is harmful, or replace a fresh, fixed-version evaluation with adequate power.
No additional API requests or historical result changes were used for this
aggregation.

## Two bounded follow-ups

For a separately frozen and approved future experiment:

1. Calibrate a common output budget across all arms on known development
   fixtures. Six of seven truncated responses used their entire 2,000-token
   allowance for reasoning. Recalculate fee reservations for any changed cap;
   the present results and costs remain unchanged.
2. Consider advertising only `finish` and `ask_user` at the third decision,
   while retaining local action validation. The observed request advertised
   reads that its instruction prohibited. Check the actual serialized request
   with fake transport before freezing a new profile; a narrower tool list is
   not a guarantee of model compliance.

Neither change was implemented or tested in another paid run here.
