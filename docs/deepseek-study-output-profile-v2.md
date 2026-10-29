# Explicit offline output profile v2

`study-8192-v2` is a new, explicitly selected **offline** contract. It changes
neither the default 2000-token study nor frozen runs, reviews or scores. The
legacy live proposal/entry rejects this profile before credential input; this
patch provides no new paid-run authorization or automatic activation.

Choose a new real output path outside the checkout (symlinked ancestors are
rejected), then run the known development fixture:

```sh
python3 -I -S -B scripts/run_deepseek_study_dryrun.py --profile study-8192-v2 --out /ABSOLUTE/REAL/OUTSIDE_REPO
```

The Python preparation equivalent is `prepare_study(...,
execution_profile='study-8192-v2')`. Omitting that argument/flag retains the
original contract. The command writes repeated known development examples and
scripted responses: they are not blind questions or model-quality evidence.

## Generation, history and remaining limits

The new per-request `max_tokens=8192` is the total **reasoning plus answer**
ceiling, 4.096 times the old 2000 limit. At most three requests give 24576
generated tokens per cell. All four arms retain thinking enabled and canonical
`reasoning_effort=high`. The provider's documented controls do not reserve
answer tokens independently; a response can still consume the whole ceiling
in reasoning and end with empty content or `finish_reason=length`.

Input plans remain 8000/request and 24000/cell, checked using measured usage
after the response. They are not a trusted pre-dispatch tokenizer cap. A larger
thinking history may make D's next request exceed the plan: retain the exact
history and known charges, record `measured_budget_overrun`, and stop. No
history clipping, relaxed input threshold, extra request, retry, truncated
answer repair or automatic success is allowed.

Tool-bearing history preserves required prior reasoning. No-tool requests also
retain the client transcript, but the official contract says prior reasoning
is ignored in that mode; equal effective reasoning context is not claimed.
Request/record byte limits, wall limit, source scope, four-read limit, identity
and unknown-outcome stops remain unchanged. Sources:
[Chat API](https://api-docs.deepseek.com/api/create-chat-completion/) and
[thinking history](https://api-docs.deepseek.com/guides/thinking_mode/).

## Fees are derived, not a stale constant

The frozen profile carries a cost-only engineering input envelope C=1048576,
covering decimal/binary interpretations of the documented "1M" context. It is
not a verified exact provider limit and **does not widen** the existing 1e6
usage/billing trust boundary or 8000-token input plan. The metadata explicitly
marks the exact context limit unverified. Before any future paid run, this
assumption, model, prices and approval must be reviewed and frozen again.

At the frozen peak rates, use no cache discount for the reservation:

`minimum_reserve_micro_usd = ceil((3*C + 12*max_tokens) / 10)`.

V2 rounds that minimum upward to cents and checks it using integer arithmetic:

| Total request output ceiling | Minimum reserve | Rounded reserve |
| ---: | ---: | ---: |
| 8192 | $0.324404 | $0.33 |
| 16384 (arithmetic boundary only) | $0.334234 | $0.34 |

The 16384 row is not another executable profile in this patch. A 330000-micro-USD
reservation for that cap is rejected. Legacy default reservation remains
330000 exactly. The new batch gate takes the actual cap and reservation from
the frozen named profile and keeps known usage independent of content parsing.

Admission still requires `known accrual + pending/unknown holds + next reserve
<= approved local budget`, alongside the durable request ceiling and single
flight. Unknown evidence keeps the hold and stops; copying a control or
rerunning a claimed cell cannot reset funds. For example, 108 × $0.33 is $35.64
of worst-case reservations; a $2 local allowance does not guarantee completion.
These are local accounting bounds under explicit assumptions, not a supplier
account hard cap or a verified invoice. Old allowances and the separate $0.66
hold are not new funding. [Price source](https://api-docs.deepseek.com/quick_start/pricing/).

## Evidence guidance and terminal actions

The same new criteria are serialized for all four arms: distinguish supplied
code facts, decisive missing facts about the current external situation, and
the smallest evidence needed to distinguish compatible outcomes with its
causal role. An initial authorized-code access gap is not automatically a
genuine external gap. A possible failing situation is not proof of the current
one. Use existing citations and `unknowns`/`gap` fields; no new keys, required
headings, human-answer loop or model call is introduced.

D decisions one/two advertise the existing five tools. Decision three advertises
only the unchanged canonical `finish` and `ask_user` schemas, using a new list
without mutating the shared schemas. Thinking mode does not support forced
named tools or `tool_choice=required`; neither is used. The client retains
provider and local role/ID/argument/action checks. An invented read, multiple
terminal actions or absent terminal action fails; no third read or fourth call
is made. `ask_user` still terminates unresolved with independent review pending.

Existing diagnosis and gap character/object limits remain: in particular a gap
is at most 200 characters, `unknowns` at most six 200-character items, `cause`
600 and `when` 300. Scripted threshold/helper fixtures exercise code facts,
external facts, sufficient supplied evidence and an overbroad request. Shape
acceptance never grants semantic correctness or necessary-abstention credit;
no historical request-specificity rate is inferred or rescored.

## Offline evidence boundary

The fixture tests inspect actual encoded request bodies, response/ledger
records and dispatch counts. They include all-reasoning capacity, canonical
third-turn tool declarations, unadvertised-tool/provider rejection, multiple
terminal/local rejection, exact fee boundaries and a second D request with
8001 measured input tokens: known fees are preserved and the remaining batch
stops without altering its first reasoning message. Old defaults and legacy
live rejection are exercised too.

This combines a generation-budget change with prompt/interface changes. Any
future paid result measures the combined policy unless separately ablated.
No guarantee of useful answers, semantic quality or pure tool gain follows.
No neutral nine-task source, hidden gold or private scoring map was used for
these development fixtures, and no real provider request or Key access occurred.
