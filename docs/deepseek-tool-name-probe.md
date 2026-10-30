# Two developer tool-call checks

This entry diagnoses the current service's tool-call behavior on two fixed,
author-known development flows. It is not another benchmark, a repair of frozen
results, or a continuation of the stopped U stage. Both flows prescribe search,
then read_symbol, then one terminal call: finish for a code property, ask_user
for missing current invocation evidence. They do not measure autonomous
retrieval or semantic quality.

The [public development inputs](../examples/deepseek-tool-name-dev/cases.json)
reuse self-authored CC0-1.0 arithmetic fixtures: two Python files total 91 bytes
and fixed cases.json is 1170 bytes. Exact hashes are checked before preparation
and again against the captured data/manifests. No gold, custody declaration,
four-arm grid, target execution or additional source is needed or admitted.

## Prepare and review without credentials

Use a new, private output root outside this checkout and input directory:

```sh
python3.12 -I -S -B scripts/run_deepseek_tool_name_probe.py prepare \
  --inputs examples/deepseek-tool-name-dev --out /EXTERNAL/NEW_PROBE
python3.12 -I -S -B scripts/run_deepseek_tool_name_probe.py proposal \
  --control /EXTERNAL/NEW_PROBE --project-env /PROJECT/.env
```

Preparation binds the implementation, two cases, source/manifests, control
path, model, ceilings and prices. The proposal binds the exact credential input
method/path, but does not read it. Omitting project-env selects no-echo terminal
input. The generated record is unauthorized. Operator confirmation records are
attestations, not cryptographic signatures; only create an authorized record
after the user has approved this exact new batch and exposure scope.

```sh
python3.12 -I -S -B scripts/run_deepseek_tool_name_probe.py preflight \
  --control /EXTERNAL/NEW_PROBE --approval /EXTERNAL/NEW_PROBE/approval-authorized.json
python3.12 -I -S -B scripts/run_deepseek_tool_name_probe.py execute \
  --control /EXTERNAL/NEW_PROBE --approval /EXTERNAL/NEW_PROBE/approval-authorized.json \
  --enable-live
```

Execute has no default live access or scripts/credential-source override.
The exact approval and exclusive claim precede credential acquisition and
dispatch. Repeated execution, copied control roots, changed code/data, altered
budgets and approvals are refused. Real logs cannot be inside the checkout.
The project-env option reads one explicitly bound regular .env file only,
checks its official DeepSeek service metadata, and keeps the Key in memory.
Terminal input fails if echo cannot be disabled. Never put a Key in argv,
messages, logs or a newly persisted credential file.

## Fixed limits and observations

- Official `https://api.deepseek.com/chat/completions`, `deepseek-flash`,
  thinking enabled/high, existing `study-evidence-v1` contract.
- Two cells, at most three requests each: six requests and a fresh $0.50 local
  budget, single concurrency, no automatic retry or resume. No old allowance or
  reservation funds this run. Each request reserves $0.33 through the existing
  persistent admission/settlement gate.
- Output limit 8192 total reasoning-plus-answer tokens/request; existing cell
  limits remain 24576 output, 24000 planned input, four reads and 180 seconds.
  Request JSON is at most 131072 bytes. Input token thresholds are checked from
  returned usage, not a pre-send tokenizer guarantee. History is not trimmed.
- Stop at the first failed contract, unknown outcome, invalid usage, identity
  absence/drift, insufficient reservation or source/approval mismatch. A valid
  but different prescribed flow also stops the check without rewriting its
  native result. Early termination is recorded as early; it does not verify
  decision 3. Every planned cell stays in the denominator.

The shared provider retains the broad historical error code and adds only
bounded diagnostic labels. Name type, invalid name format and undeclared names
remain distinct. A failed name matching the local fixed static-reader enum
records only that family; with decision 3 and terminal-only declarations it is
reported as terminal_round_read_attempt. Arbitrary returned names are never
persisted in diagnostics. Valid finish/ask_user, actual terminal choice and
whether the prescribed flow was followed are separate observations. Billing
and identity evidence survive content-contract failures. The historical three
names remain unknown; no new observation fills their missing subtype.

The model receives the fixed questions, captured source, normal system/tool
schemas and bounded tool conversation. Search means the local static reader,
not the provider's separately billed Search/Fetch API. Reasoning and tool-call
IDs are replayed as required by the [thinking guide](https://api-docs.deepseek.com/guides/thinking_mode/).
The [primary API](https://api-docs.deepseek.com/api/create-chat-completion/)
supports auto tool choice; required/named forced choice is unavailable in
thinking mode. Restricting declarations and instructions cannot guarantee that
a live model follows them.

Peak [prices](https://api-docs.deepseek.com/quick_start/pricing/), checked
2026-10-30, are input hit $0.006/M, miss $0.30/M and output $1.20/M. The
8000-input/8192-output planning scenario rounds to $0.012231/request or
$0.073386 for six. The loose input envelope derives a $0.324404 minimum reserve,
rounded to $0.33. Local accrual plus holds plus the next reserve must fit $0.50;
the run may stop early. These are local controls, not a supplier billing hard
limit. Recheck official terms before activation.

## Offline evidence and private records

`dry-run --control /NEW/SEPARATE_FIXTURE --scripts /LITERAL_REPLIES.json` accepts
only exact two-cell, one-to-three-response literal fixtures. It never reads a
credential or constructs real HTTP, labels results as simulated and consumes
its own claim. Scripted approval tests use approved_live_fake provenance; no
fake receipt qualifies a real run or unlocks R/U. Do not rehearse on the root
reserved for a later approved real execution.

Source, request/output/reasoning, usage and receipts stay in the private owned
log root. Fourteen days is retention guidance; upload and automatic deletion
are disabled, and deletion requires separate permission. Safe CLI summaries
exclude raw responses and credentials. Matching model identity, a valid
terminal and a passed fixture are not semantic success. If both real checks
comply, report that the earlier failure was not reproduced; do not claim a
historical explanation or policy gain.
