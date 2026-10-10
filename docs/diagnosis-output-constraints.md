# Optional Diagnosis output constraints

`macr.evals.diagnosis_constraints` provides `diagnosis_schema()` (fresh schema), `build_optional_response_format()` (server wrapper) and `diagnosis_gbnf()` (grammar candidate). No dependencies, I/O or automatic activation are added. Existing schema, parser and default callers remain unchanged.

Every branch requires exactly `verdict`, `cause`, `when`, `refs` and `unknowns`:

| Verdict | Cause / when | Refs | Unknowns |
| --- | --- | --- | --- |
| `unknown` | Both `null` | 0–3 | 1–6 |
| `supported_defect`, `no_defect_supported` | Nonempty strings, at most 600 / 300 characters | 1–3 | 0–6 |

Refs require exactly `file`, `start`, `end`, with positive integer lines. Unknown details are nonempty strings of at most 200 characters. The legal `unknown` exit preserves uncertainty; constraints cannot invent reasons or evidence.

Application validation remains authoritative for stripped text, relative paths, duplicate refs, `start <= end`, source bounds and semantics. Schema limits apply before stripping; GBNF fixes field order and counts JSON character productions, including escaped Unicode units. Neither claims equivalence to every parser-accepted string.

Sources are pinned to llama.cpp **b11521**, revision `b42b7e6d3059503dd4bed6ca5dcf342bb6379f67`. Its [schema implementation](https://github.com/ggml-org/llama.cpp/blob/b42b7e6d3059503dd4bed6ca5dcf342bb6379f67/common/json-schema.cpp) maps `oneOf` to ANY_OF; distinct verdict constants keep these branches mutually exclusive. The candidate avoids `if`/`then`, patterns and `uniqueItems`. The [server wrapper](https://github.com/ggml-org/llama.cpp/blob/b42b7e6d3059503dd4bed6ca5dcf342bb6379f67/tools/server/server-common.cpp) accepts this response-format shape. Select one constraint route per request.

A fixed-library parse-only check used [`llama_sampler_init_grammar`](https://github.com/ggml-org/llama.cpp/blob/b42b7e6d3059503dd4bed6ca5dcf342bb6379f67/include/llama.h) with null vocabulary and freed successful samplers. A short grammar and this candidate initialized; invalid syntax, missing root and undefined rule returned null. This verifies syntax/root/rules only, with zero model loads, services or requests. Text acceptance, schema conversion, sampling, HTTP generation and semantics remain unverified. See the fixed [grammar implementation](https://github.com/ggml-org/llama.cpp/blob/b42b7e6d3059503dd4bed6ca5dcf342bb6379f67/src/llama-grammar.cpp).

`macr.evals.diagnostic_review` optionally guards review persistence:

```python
from macr.evals.diagnostic_review import begin_finish_review, save_finish_review

phase = begin_finish_review(captured_out)
# Review the already captured responses in this same process.
saved = save_finish_review(captured_out, reviews=reviews, phase=phase)
```

Begin at finish-phase start. The API uses the process's `time.monotonic`; it accepts no caller clock. An exclusive start marker prevents origin reset, and a process-local registry checks the issued token's object identity. Reconstructed, copied or reset tokens cannot restore timing. Missing timing, clock failure/rollback or elapsed **>= 300 seconds** saves without semantic reviews and returns `finish_status="pending"`.

Before scoring, an exclusive consume claim records default pending authority. Acceptance requires a matching `review-finish-authority.json` naming that claim nonce and the saved comparison, published through the existing atomic writer after comparison/sidecar persistence and the final checkpoint. Follow the returned `authority`; a raw comparison score or sidecar alone does not accept reviews. Later calls cannot replace the first authority or accept new reviews.

Late reports remain evidence, with `superseded_not_accepted` sidecars when persistence succeeds. If replacement, sidecar or authority persistence fails, the pending claim still prevents acceptance; a failed authority fsync leaves only an unpublished temporary file. Checkpoints do not interrupt work. `whole_run_compliance` stays `not_assessed`; authority and late evidence writes are not certified within the deadline.

A future measurement must freeze inputs/scoring and change only the selected constraint. Model execution requires new approval; these APIs provide none.
