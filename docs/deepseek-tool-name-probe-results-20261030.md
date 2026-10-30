# Two developer checks: observed protocol results

The separately approved two-flow check made **six real requests**. One flow
completed its prescribed terminal contract; the other returned a legal tool
name but failed its argument-length contract and stopped. The run did not
reproduce the earlier tool-name failures. Two prescribed, author-known flows
do not establish reliability, autonomous retrieval quality or policy gain.

| Developer flow | Requests | Verified tool names | Terminal outcome | Peak local accrual |
|---|---:|---|---|---:|
| Code property | 3 | search → read_symbol → finish | Valid prescribed three-turn contract; no semantic grade | $0.000874 |
| Current invocation evidence | 3 | search → read_symbol → ask_user | Legal name; gap argument rejected | $0.001806 |
| Total | 6 / 6 | Six legal, declared names | One accepted flow and one failed flow | $0.002680 |

All requests have settlement outcomes; new unknown reservation is **$0**. The
old $0.66 hold and stopped U stage stayed separate and untouched. Input/output
totals were 13,875 / 1,365 tokens. Costs use the frozen peak provider rates and
are local usage-based accrual, not a supplier invoice; full-cache-miss
sensitivity is $0.005804. No retry or new batch followed the failure.

## What failed

The last ask_user call supplied exactly one nonblank string field, gap. It
contained **205 Unicode characters after trimming outer whitespace**, above
the **200-character** maximum already present in the actual request's schema.
Its completion used 151 tokens with finish_reason=tool_calls. The recorded
error is ask_user_contract_invalid. This was an enforced field-length limit,
not an observed tool-name or total-output-token failure.

The summary's accepted terminal is null because argument validation prevented
acceptance; the returned function name was still legal ask_user. The original
205-character output remains failed and unchanged. It was not shortened or
credited as a valid abstention. The exact gap text and model reasoning are
not published.

Independent machine review verified the fixed inputs, code/plan/approval,
six unique reservation/outcome and request bindings, consistent verified
model identities, usage projections and per-request rounded cost. This is
protocol/accounting evidence, not an independent semantic score.

## Bounded follow-up

The original schema already communicated the 200-character limit. A new
**offline-only** `concise-gap-v1` prompt candidate makes that same limit explicit
in natural language and asks for a concise sufficient evidence request without
repeating code analysis or tool history. It derives the numeric bounds from
the existing schema, retains jointly necessary facts, and changes no validator,
budget, tool, old prompt or frozen result. See the
[entry and candidate instructions](deepseek-tool-name-probe.md).

This is a prompt hypothesis. Its real effect on the model has **not** been
tested. Fixture acceptance or serialized instructions do not show that future
outputs will comply. The older three missing names remain unknown, and no
historical tool-name fault is declared repaired. No additional paid request
or reuse of unused funds is authorized by this follow-up.

Execution provenance:

- Code: `546ee224d399a30b651acb5ddb7ebbea650cedcc`.
- Plan: `6bf4fa04ea96074b644703dd66f2f5cd6ee75e90c49b8cd76e27c6fb623839d6`.
- Runtime summary exact-file SHA256: `6a7f57e8a0c9c12afb77dcd0764b6364fad6cdb2afa541dba61abf549835cc21`.

Only aggregate facts and opaque provenance are public. Original logs, outputs,
credentials, configuration and custody material remain private.
