# Two-stage comparison pilot candidate

This is a separately gated execution candidate, disabled by default. It does
not grant a model request, credentials or spending. The old four-arm study entry
and its frozen profiles are unchanged. Preparing an approval proposal, preflight
or literal-response dry-run never acquires a Key or calls an API.

| Phase | Selected cells | Request ceiling | Fresh local budget |
| --- | --- | --- | --- |
| R | All7 previously truncated case/strategy pairs, once, using study-8192-v2 |21|$1.00|
| U |9 independently held unseen tasks, adaptive strategy under each8192 profile, once|54|$1.50|

These are proposed scopes, not permissions. The selected-cell controller does
not fabricate the missing arms of a full four-arm grid. R/U have different
roots, approvals and ledgers. Historical allowances and the separate$0.66
unknown reservation remain outside both. No stage resumes or automatically
retries a used cell. No extra connection probe is outside the request count.

## Inputs and promotion

R requires a custodian-supplied mechanical selection record: exactly7 unique
task/strategy pairs with old response hashes, length finish reason and a frozen
selection-source hash/declaration. That declaration needs independent verification;
its structure alone does not prove all historical failures were selected. The
executor does not read old answers or gold to select cases. A neutral source
package supplies only the original authorized code, task facts and manifests.

U requires a new neutral9-task/3-project package and hash-only custody/rubric
commitments. Newness and category qualification belong to the independent
custodian; changing a pack name/hash does not establish unseen mechanisms.
Known development fixtures and the scored nine tasks are not U samples.

U preparation binds an eligible R receipt. Actual U preflight additionally
requires that receipt to have real approved-live provenance and its own exact
U approval. A fake R rehearsal cannot activate real U. R qualifies only when
all7 cells have terminal outcomes, no operational/identity/unknown accounting
anomaly occurred, at least5 have contract-valid reviewable termination, and at
most2 cells truncated. This is an operational gate, not a semantic score.
Promotion is recomputed from all7 frozen accounted results and the request-bound
reservation/outcome ledger, including usage-derived charges and original-result
hashes. Duplicate rows, identity anomalies or summary/ledger amount mismatches
are refused; an edited summary's eligible flag is not sufficient.

Seed20261030 freezes task blocks and balanced5/4 version-first order for U.
Both versions use the same reviewed implementation root and provider settings;
the v2 prompt remains the compatible baseline, including its initial evidence
guidance. This tests the newer standard's increment within D. It does not
re-establish D versus direct API/fixed retrieval, or isolate output-budget effects.

## Limits and stop contract

Each request has8192 total reasoning-plus-answer output tokens; each cell has
at most3 requests/24576 output,8000 planned input/request and24000/cell,
4 reads,180s and provider timeout at most60s. Input is checked against returned
usage after dispatch; it is not a pre-send tokenizer hard limit. Request bytes
are131072 and cell record bytes1048576. Third adaptive turn is terminal-only;
there is no fourth call, answer-token reservation or guarantee of an answer.

One phase-wide gate reserves before dispatch, writes request-bound evidence,
allows one in-flight request and pins the first verified model fingerprint
across every selected cell and both U versions. Identity gaps/drift, untrusted
usage, unknown remote outcome, measured budget overrun, source/approval drift
or insufficient next reservation stop the phase. Unknown retains the hold;
independently trustworthy billing can settle even if policy content is invalid.
Neither an old fingerprint nor a previous phase fills a new identity gap.

A known, settled output/tool contract failure ends that cell and stays in the
denominator; it can advance without retry. The third such failed cell stops the
stage. Ordinary access/infrastructure errors retain their existing fatal
classification. Stopping early leaves the remaining planned cells unattempted.

## Fees, authentication and local records

The proposed contract uses DeepSeek official Chat Completions, deepseek-flash,
thinking enabled/high, with no provider search/fetch service. Rates checked
2026-10-30: peak input hit$0.006/M, miss$0.30/M, output$1.20/M. Freeze a fresh
check before actual approval. [Official price source](https://api-docs.deepseek.com/quick_start/pricing/).

At the assumed8000 input/8192 output, per-request local accounting rounds up
to$0.012231, giving R$0.256851 and U$0.660474 if every request reaches that
ceiling. This is a planning scenario, not an expected or guaranteed bill.
Engineering input envelope1048576 derives minimum$0.324404, rounded to$0.33
reserved before each request. Admission requires known accrual plus all holds
plus that next reserve to fit the stage budget. Settlement releases unused
reservation; the low budgets can still stop early. Existing trustworthy-usage
input limit1000000 is not widened. No local accounting rule is a supplier hard cap.

Only the separately approved execute command can obtain a Key via a supplied
in-memory reader or the no-echo terminal callback. Never put it in argv, model
messages, logs or a newly persisted credential file. Failure to disable terminal
echo is fatal. Log roots are private and bound to the proposal; local source,
output/reasoning, usage and receipts are retained for14days as policy guidance,
with no upload or automatic deletion. Deletion requires separate approval.

## Review and reporting

Reports keep the exact7/18 planned cells, failures, unknown holds and unattempted
slots. U version pairs remain pending or incomparable before locked independent
semantic review; missing results are never counted as ties. Actual request/read
counts, token categories, known accrual, unknown holds and median/range latency
are separate from maximum allowances. Packets bind original result hashes,
show only actually visible source and hide version/strategy/accounting labels;
the keeper map is separate. Answer text can reveal strategy, so blinding is
imperfect. The old full-grid report validator is not loosened for this subset.

R valid outputs remain semantic pending unless reviewed. U requires independent
hash-bound scoring and custody adjudication: complete solutions/6, necessary
abstention/3, unnecessary abstention/6, sign errors and unsupported certainty,
plus the full failure denominator. Only after those reviews can a small-sample
comparison be interpreted. No success rate follows from a scripted rehearsal.
Historical R selection and its time/model/config changes prevent a single-factor
causal conclusion; U's one-repeat, three-project sample cannot support broad
generalization or automated expansion.

Before a real approval card is ready, bind exact source paths/bytes/hashes,
license/qualification, R selection or U commitments, code/config/order hashes,
new budget/request ceiling, stop rules and the repository-external log path.
R needs its own permission; U needs a qualified R and a later separate permission.

Preparation and inspection commands (placeholder paths are not a real pack):

```sh
python3 -I -S -B scripts/run_deepseek_comparison_pilot.py prepare --phase R --pack /NEUTRAL/PACK --custodian /HASH_ONLY.json --selection /R_SELECTION.json --out /EXTERNAL/NEW_R
python3 -I -S -B scripts/run_deepseek_comparison_pilot.py proposal --control /EXTERNAL/NEW_R
python3 -I -S -B scripts/run_deepseek_comparison_pilot.py dry-run --control /EXTERNAL/NEW_R --scripts /LITERAL_REPLIES.json
```

The R selection object has schema`macr.deepseek.pilot.regression.v1`,
`source_sha256`, a nonblank`selection_statement`, and exactly7`rows`, each with
`task_id`, original`strategy`, old`response_sha256` and`finish_reason:"length"`.
The custody file has only schema/version/gold_sha256/rubric_sha256/
qualification_statement; it contains no answers or private paths. U preparation
instead supplies`--phase U --regression-control /COMPLETED/R` and its new neutral
pack. The seed is fixed. Dry-run and live execution both consume a one-use claim;
never try live against a rehearsed control root.
