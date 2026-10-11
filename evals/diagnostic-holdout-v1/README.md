# Diagnostic holdout v1

This is an author-created synthetic evaluation set for source-grounded diagnosis.
The authors know the answers. It is independent of the three original diagnosis
examples, but it is **not an external benchmark, author-blind test, or proof that a
model has never seen these programs**. Before its first future local-model use,
freeze the input, prompt, reference, and model/runtime parameters together through
the existing comparison CLI. This batch makes no model call.

`cases.json` contains twelve retained cases with neutral IDs `c01` through `c12`.
Only `reference.json` carries their categories: four supported defects, four
healthy implementations within explicit scopes, and four evidence gaps. They cover
interval/empty boundaries, mutable aliases, defaults, argument arithmetic, missing
dependencies, callback selection, configuration units, and host bindings. The
mechanisms do not repeat the original pop-before-read, mapping-default, or resolver
argument-forwarding examples. Do not merge these twelve cases into any original
three-case score. The recorded 3B `0/3`, 4B `1/3`, and GBNF structural `3/3` remain
historical results with their original denominators.

`development/cases.json` and `development/reference.json` contain three separate
examples with IDs `d01` through `d03`. Their IDs and source bytes do not overlap the
retained set. Use them to check formatting and review workflow. They never enter
the retained denominator; do not tune a prompt or grammar on retained responses
and then describe that same set as an untouched holdout. A changed input or rubric
requires a new version and a new comparison manifest.

The case-file version is `diagnostic-holdout-v1`; the development version is
`diagnostic-development-v1`. Both use prompt version `diagnostic-holdout-v1` and
the existing five-field response contract (`verdict`, `cause`, `when`, `refs`,
`unknowns`). Model input consists solely of a case's `source`, `question`,
`scenario`, and `assumptions`. Source snippets start at line 1 and contain the full
consecutive, unnumbered program text. Split names, categories, reference answers,
mechanisms, triggers, probe returns, evidence roles, and rejection examples belong
only to the reference side and must never be included in model messages. The
comparison layer may retain a neutral case ID separately for recording.

Each reference supplies a mechanism and concrete trigger, or the exact missing
evidence that prevents a verdict. `evidence_roles` identifies the source ranges a
reviewer should check. `accepted_shape_witness` is one valid output example, not
an exact-wording answer key. `reject_examples` illustrates specific unsupported
claims and vague causal explanations. JSON validity and the expected verdict do
not establish semantic success: review the causal mechanism, trigger, citation
support, and specific abstention reason. Equivalent wording is allowed. Unclear
paraphrases must remain pending for human review, rather than being failed or
passed by a small model or a broad keyword match. A healthy verdict must retain
the stated ordinary-built-in-type and stable-binding scope; an unavailable external
implementation must not be silently treated as healthy.

`tests/test_diagnostic_holdout_cases.py` checks counts, input/reference separation,
line ranges and exact cited excerpts, witness contracts, and file/byte bounds. It
executes only the eight fixed, repository-owned defect/healthy programs after
matching their complete source text against a test-local allowlist. The sole
probe is each program's zero-argument `demo`. Those programs have no imports,
loops, recursion, private attributes, network, filesystem access, process calls,
or external effects. Restricted built-ins are only `dict` and `list`; each probe
gets a fresh namespace so the mutable-default example has a defined call history.
Probe returns describe what the shown implementation actually does, including its
wrong results; references separately explain the required behavior.

The four unknown programs and all development programs are parsed as AST only and
are never executed. Their missing dependency, callback, configuration, or host
bindings are deliberate. The test is not an arbitrary-code runner and has no
command-line option or input path for executing user programs. Runtime discovery,
dynamic imports, external code, and user-provided snippets are outside its scope.

The dataset has exactly five payload files, at most 96 KiB together. A retained
model-input case is at most 4 KiB of UTF-8 JSON before the versioned system prompt;
these are byte bounds, not measured token counts. The single test file is bounded
at 24 KiB. OS metadata files beginning `._` are not dataset payload and must not be
published. No real project code, credentials, model weights, run logs, or databases
are included.

Run the offline fixture checks with:

```sh
python3.11 -I -S -B scripts/run_offline_tests.py test_diagnostic_holdout_cases
```
