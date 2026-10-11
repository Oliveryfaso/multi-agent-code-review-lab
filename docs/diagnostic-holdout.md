# Separate diagnostic evaluation and offline comparison

The synthetic `evals/diagnostic-holdout-v1` set has twelve retained cases: four
supported defects, four healthy implementations within stated scopes, and four
cases with specific missing evidence. Three development examples live in a
separate directory. Authors know the answers; this is neither an external nor a
blind benchmark. Original three-case measurements retain their own denominators.
Read the [dataset notes](../evals/diagnostic-holdout-v1/README.md) for mechanisms,
scope, safe fixed probes, and limits.

`load_dataset` reads only explicit project-local regular JSON files, each at most
64 KiB, with at most twelve cases. It checks exact field sets, IDs, full source
line ranges, witness contracts, and reference consistency. Model messages contain
only source, question, scenario, assumptions, and a versioned instruction. Neutral
case IDs stay in records. Categories, topics, mechanisms, witnesses, rejected
examples, and probe returns stay on the reference side. The loader executes no
source and does not decide whether an explanation is semantically correct.

## Prepare without dispatch

The existing comparison CLI accepts explicit dataset/reference files and a JSON
list of one to four frozen `ModelProfile` objects. Their sampling, output/context
limits, timeout, runtime and template settings must match. Model and tokenizer
identities may differ. The endpoint is a numeric loopback declaration; preparing
a comparison does not contact it, discover a service, or attest deployment.

```sh
python3.11 -I -S -B scripts/compare_diagnostics.py \
  --dataset evals/diagnostic-holdout-v1/cases.json \
  --reference evals/diagnostic-holdout-v1/reference.json \
  --profiles artifacts/proposed-profiles.json \
  --out artifacts/proposed-holdout
```

`proposed-profiles.json` must already be explicitly supplied; there is no automatic
profile discovery or generated live configuration. The output directory must be
new. Prepared requests use the common profile timeout, at most 180 seconds,
input 1536 and output 384 tokens, context 2048 in the proposed comparison. The
180-second preparation limit grants no execution budget. Real generation still
requires separately approved service/download limits, the owner lifecycle,
identity checks, exact native template/tokenizer admission and GBNF adapter.
No character/byte heuristic in the dataset is a measured token count.

The CLI freezes requests, profile declarations, the scoring category/topic map,
and the complete rich human rubric under the existing reference integrity
binding. It retains mechanisms, triggers, missing evidence, witnesses, rejected
examples and ignored probe descriptions on the scoring side. Changing any of
these frozen fields is refused as reference drift. Changing a retained prompt
or reference after seeing outputs requires
a new version; development cases never enter retained scores. The existing
`--input` captured-response replay interface remains available.

## Review and report

Reuse `LocalHTTPProvider`, the existing evaluation contracts and comparison
RunStore records. An inert transport can test the real adapter without a service.
Scripted usage/release proofs are test fixtures; they provide no native execution
evidence. Every real response needs verified request/profile/response bindings,
measured usage and owner-native release/idle/remove-waiting evidence. An
infrastructure failure stops capture; unattempted and uncertain cells stay
separate from quality denominators.

Deterministic checks establish strict JSON syntax, the five-field contract, and
valid source ranges. They award no semantic pass. A human reviewer reads the
source, bounded requirement and raw answer, then records mechanism, trigger,
citation support and specific abstention reason as supported, partial,
contradicted or unknown. The review binds to the exact record digest and cites
the answer's evidence. Correct verdict alone, matching keywords, or matching
witness prose is insufficient. Equivalent wording is allowed. Healthy answers
need the stated scope and actual call; unknown answers need a concrete missing
dependency or contract rather than generic requests for more context.

Reports group results by expected verdict and neutral topic. Each group includes
expected, recorded, captured, unattempted and uncertain counts; quality cases;
strict-JSON and contract passes/rates; reviewed semantic cases and diagnostic
passes/rate; and format, semantic, pending and infrastructure failures. JSON
scalars/arrays are syntax-valid but fail the object contract. Infrastructure
records have no structure score and are excluded from quality denominators;
format failures remain in them. Any outstanding semantic review makes that
group's diagnostic rate null. A completed subset may have a rate while separately
showing unattempted cells; it is not a completed twelve-case measurement.

The report marks offline captured-response review and scripted provenance;
`real_model_accuracy_measured` remains false. Retain native owner evidence and
independent bounded review before making any live claim. The existing 300-second
monotonic finish guard still saves pending when review does not finish in time.
The current service summary adapter retains its old three-request cap; the
proposed twelve-request live owner must be explicitly frozen and validated after
new approval, before any launch.

## Offline verification

```sh
python3.11 -I -S -B scripts/run_offline_tests.py \
  test_diagnostic_compare test_diagnostic_review test_diagnostic_dataset \
  test_diagnostic_holdout_cases test_diagnostic_holdout_replay
```

The runner's existing 120-second wall, 64-MiB owned output and 2-MiB test-log
limits apply. Fixture probes execute only eight complete, fixed synthetic
programs with restricted builtins; unknown and development programs are
AST-only. Replay checks use the actual twelve cases, CLI, inert provider and
record/report APIs. Hand-written review fixtures reject unsupported examples
including correct-verdict/vague-cause answers, and accept an equivalent
paraphrase. These are software tests, never model accuracy results.

The [next-model proposal](local-model-next-comparison.md) pins the official
Coder7B candidate, explains estimated memory, and gives one exact future batch
approval. This preparation adds no weight download, model service, generation,
VM, paid API or external inference.
