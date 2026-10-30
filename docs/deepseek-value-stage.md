# A finite check of the tool strategy

**Preparation only. No new paid execution is authorized.** This stage has a
natural end: pass a small developer protocol gate, compare against direct API on
independent tasks, then keep the simpler route unless the tool earns its cost.
It does not reopen any stopped experiment or reuse historical funds.

| Phase | Fixed work | Request / local budget ceiling |
|---|---|---|
| S0 | Two existing developer flows with the concise-gap candidate, once each | 6 / $0.50 |
| S1 | Nine fresh synthetic tasks, direct API and tool strategy once per task | 54 / $1.50 |
| Whole stage | S1 runs only if both S0 flows validate | 60 / $2.00 |

S0 uses the existing two public synthetic source files (91 bytes) and fixed
developer questions (1170 bytes). S1's separate source/question inventory must
come from the new neutral handoff; it is not included in those S0 byte counts.

The direct strategy gets the initial answer, a self-check and a final answer;
the tool strategy may end earlier within the same three-request ceiling. Both
receive identical full-source initial evidence with no host AST summary. Every
request allows 8192 total reasoning-plus-answer tokens. Actual requests, tokens,
cost and elapsed time are reported separately. This tests the whole strategy,
not a same-call-count ablation or a pure causal tool-function effect.

Known, settled S1 content-contract failures stay in the denominator and continue
without retry. Unknown usage/outcomes, identity problems, scope drift, input-plan
overrun or inadequate reserves stop. No prompt fixes or replacement tasks are
allowed during the comparison. Paid repair/retests require another bounded
approval; ordinary offline development can continue.

## Independent neutral handoff

Use the existing `load_neutral_pack()` in
[`deepseek_study_profile.py`](../src/macr/evals/deepseek_study_profile.py).
The separate custodian supplies a new `macr.deepseek.study.pack.v1` package:

- 9 tasks in 3 projects, 3 per project; privately balanced 3 defects, 3 healthy
  tasks and 3 genuinely decisive external gaps. No category map is sent to the executor.
- 2–4 original synthetic Python files per task; all authorized files also appear
  in `initial_evidence_files`. Complete source <=4096 bytes/task, <=36,864 bytes overall.
- `pack.json` <=65,536 bytes, containing only `schema`, `pack_id`, `tasks` and the
  existing neutral task fields. Source, license and provenance are mandatory.
- No gold, answers, hidden tests or extra files in the neutral root. Inventory,
  freeze metadata and the five-field custodian commitment stay in a separate
  neutral handoff directory. Keep private answers elsewhere under custodian control.

The executor validates only neutral data and opaque gold/rubric commitments. It
does not create the blind tasks or inspect private grading material. Separate
directories under one user account provide role separation, not OS isolation.
Exact source inventory, bytes and plan hash can be frozen only after that handoff.

## Reproduce preparation without credentials

The fixture runner exercises both phases with literal synthetic replies, never
the real endpoint:

```sh
python3 -I -S -B scripts/run_offline_tests.py test_deepseek_value_profile test_deepseek_value_stage test_deepseek_value_cli test_deepseek_value_report
python3 -I -S -B scripts/run_deepseek_value_stage.py --help
```

After receiving only the approved neutral handoff, prepare an unused control
directory outside the checkout and inspect its default-unauthorized proposal:

```sh
python3 -I -S -B scripts/run_deepseek_value_stage.py prepare --pack-root /path/to/neutral-pack --custodian /path/to/neutral-handoff/commitment.json --out /path/to/private-new-stage
python3 -I -S -B scripts/run_deepseek_value_stage.py proposal --control /path/to/private-new-stage
```

Preparation and proposal do not read credentials or dispatch requests. Live
execution also needs exact whole-stage consent, a matching approval record and
explicit enablement. A failed or consumed stage cannot be resumed or reset.

## Selection after independent scoring

Keep all 18 planned cells. Report full solutions on six solvable tasks and
necessary abstentions on three external-gap tasks; partial/contract-invalid
answers do not earn complete-task credit. Pending, unknown and unattempted pairs
cannot become observed ties. Independent reviews bind exact result hashes and
check mechanism, trigger and cited support, not just verdict labels. The extra
locked `determinate_error` review bit captures a definite but unsupported answer.

Direct API remains the default. Keep the tool as an experimental option only
with complete execution/identity/metering/review, at least two net paired wins,
no newly harmed task, and total local cost and elapsed time each <=1.25 times
direct. Otherwise prefer direct, or explicitly mark incomplete evidence
inconclusive. Nine tasks support an engineering choice, not a general performance
claim. Do not keep tuning on the independent comparison to cross this threshold.

The exact protocol and review contract are in the
[stage specification](superpowers/specs/2026-10-30-debugger-value-stage.md).

## Future execution boundary

Target only the official DeepSeek chat endpoint, `deepseek-flash`, thinking high.
Use a fresh stage-bound local log directory outside the checkout; no automatic
retries, ledger reset, upload or deletion. Logs include source, requests, outputs,
reasoning, usage and receipts, with 14-day retention guidance. A future approval
may allow an existing project Key in memory for official authentication only;
it must never enter argv, chat, logs or new credential files. A leaked Key can
incur unauthorized charges.

Reserve $0.33 before each request and retain uncertain reservations. The proposed
$2 is a local control, not a supplier hard limit or a guarantee of completion.
At the 8000-input plan and 8192-output ceiling, 60 all-cache-miss peak-rate requests
have a $0.733860 planning sensitivity after per-request rounding. Recheck the
[official prices](https://api-docs.deepseek.com/quick_start/pricing/) before any
approved live run. Prior $0.66 unknown reservations remain in their original ledgers.
