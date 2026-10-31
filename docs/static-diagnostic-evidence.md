# Deterministic Debugger evidence + API

The long-term goal remains a stronger API+Debugger combination. The
[completed value-stage result](deepseek-value-results-20261031.md) remains a
failure to earn its preset tool-policy threshold; it is not a decision to stop
combination research. That run supplied full source initially, so rereading
source would usually be redundant. This version tests a different, smaller
mechanism: **recomputable static bindings and coverage, then one model call**.

## What is new

`static-diagnostic-v1` reuses the actual snapshot store, repository index,
`collect_static_facts` and source reader. Explicit file/qualified-symbol entries
select seeds without a label or model answer. It computes local/import relations
and a one-hop, limited argument-binding capsule with source locations,
assumptions, unresolved paths and truncation. Repeated unchanged inputs produce
stable capsule hashes; random evidence UUIDs do not count as novelty.

This is deterministic computation from code, not new runtime knowledge. Literal
arguments and straight-line assignments can support syntactic parameter binding;
call returns, branches, dynamic dispatch and rebound names are not promoted to
execution facts. No repository code is imported, run, built or tested. The
execution/test backend remains outside this feature.

## Fair inputs and output contract

- R receives the complete captured authorized source, task and assumptions.
- E receives exactly that source/task plus the static capsule, once. Both use the
  same model settings, source table, output schema and one-request ceiling.
- Common source capture and E-only index/collector overhead are recorded
  separately. The capsule is not free when comparing end-to-end time or cost.
- This first profile requires 1–12 nonempty, completely citable captured text files / 16384 bytes. Oversize preparation fails;
  it does not silently remove a dependency from R. This is not a large-repository
  benchmark or a claim of complete repository coverage.

The existing **diagnostic-compact-v3** is reused. It already treats 600/300/200
as legacy field caps, not current product limits. Long explanations remain
available within the 8192-byte total output object; brevity targets produce
warnings. Exact keys/types/verdict, unknown/null branches, source-ID citations,
closed scope and total output limits remain hard. No clipping, rewriting or
retry repairs a submitted answer. Valid final `stop` plus diagnosis JSON enters
the same parser for R/E. Tool calls or incomplete output fail in this one-call
profile. A valid candidate is `semantic_pending`, not verified causal truth.

The collector keeps its 1200-byte context, six-function, 32-fact, 16-binding and
50-candidate ceilings. Unsupported/truncated computed observations stay explicit;
R/E still receive the same full captured source. A missing static observation
alone is not a necessary external evidence gap. The capsule is <=32768 bytes and
the prepared UTF-8 request and actual encoded wire are each <=65536 bytes. A wire-limit failure stays in the two-arm denominator without dispatching that arm; these are byte limits, not token estimates.
Explicit model limits are 8000 input / 8192 total output tokens / 60 seconds for
the offline entry. Actual provider usage remains separate from input planning.

## Real offline entry

This new entry prepares data and replays literal provider envelopes only. It has
no Key reader, live switch, arbitrary transport callback or service. It exercises
DeepSeek's hardened `converse` wire contract with explicit thinking high; it does
not route through the legacy `complete` adapter.

Use the existing CPython 3.12 interpreter; no dependency installation is needed.
Choose a fresh private output outside the analyzed source directory:

```sh
mkdir -p ../private-diagnostic
python3.12 -I -S -B scripts/run_static_diagnostic.py prepare \
  --repo examples/static-diagnostic-development/keyword-defect \
  --entry entry.py:start \
  --question 'Does start satisfy the supplied requirement?' \
  --scenario 'start must return True; integer literals and unchanged module bindings.' \
  --out ../private-diagnostic/prepare-001
```

`prepared.json` contains two actual requests, source/scope hashes, the computed
capsule and operation/timing evidence. Both requests' final task payloads and
response schemas are identical. E has one supplemental user message, not another
Agent. A changed task or source changes the scope binding.

For the same task, `offline` replaces the `prepare` command and adds
`--replies /path/to/literal-replies.json`, with a new output directory. The reply
file is an exact `direct`/`evidence` map, each value a single literal SDK response
envelope (or the failure fixture `"timeout"`). Its JSON diagnosis must use the
scope and source IDs from preparation. `result.json` retains both rows, request
and response envelopes, usage/identity evidence and failure status. Exit0 means
both outputs are contract-valid and pending semantic review, not correctness;
exit1 preserves model/contract failure, and exit2 is rejected preparation/input.
Existing output directories are preserved.

The [public development sources](../examples/static-diagnostic-development/README.md)
cover imported keyword-binding defect/healthy variants and a genuinely unknown
callback. They are development families, not independent holdout observations.
Offline end-to-end checks execute real capture, index, source reads, binding
computation, serialization and citation validation; only model responses are
literal fixtures. They prove wiring and mechanical behavior, not a model
improvement. Giving R a deliberately wrong fixture answer would prove nothing.

## Bounded validation requires explicit approval

The separate [stage entry](static-diagnostic-stage.md) prepares a three-pair
development qualification followed conditionally by nine independently authored
new task pairs. Its fixed proposed ceiling is 24 requests/$1.50, in separate
phase budgets, with persistent reservation, usage, identity and unique-run gates.
It remains default off until the exact frozen stage receives explicit approval.
This offline single-pair entry cannot launch it. Old balances and approvals do
not apply. A fixture response's token counters are not a real model bill.

The first real check should assess delivery and whether the model actually uses
supported bindings, including healthy and unresolved counterexamples. Subsequent
unseen quality evaluation needs independent authorship, locked rubrics and full
failure denominators. Do not tune on completed holdouts. R/E estimates the value
of static computation/presentation; autonomous feedback and post-hoc same-evidence
replay are separate later questions, not causal claims of this slice.
