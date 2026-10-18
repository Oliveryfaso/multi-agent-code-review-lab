# External real-project validation

This dated record distinguishes source-reading acceptance from target execution, diagnosis or repository correctness. Raw source captures, observer records and resource/process evidence remain private.

## Fixed plan — 2026-10-15

The second project is the existing public MIT-licensed [wechat-history-export-skill](https://github.com/Oliveryfaso/wechat-history-export-skill/tree/50c67993eda1b0195f0fad10608812d9a3e6b22c), clean `main` at `50c67993eda1b0195f0fad10608812d9a3e6b22c`. Its 19 regular files total 54,463 bytes, including eight Python files. Script-style SQLite/FFI/export tooling differs from this tool's package architecture; its small size limits performance conclusions.

Only repository source is read as data. No target import, execution, test, build, installation, database access, debugger attach or decryption is permitted. Adjacent private data is outside the selected root. Artificial traceback and missing/path clues are labeled test inputs rather than claims of a target runtime failure.

The trusted product's 94 source files match the reviewed `44b1890` runtime. The fixed eight slots use four CLI cases and four authenticated loopback HTTP cases, sequentially, with zero automatic analysis retries. The standard profile is retained; two cases only lower excerpt/RSS limits. Overall observation is bounded to 54 minutes with one shared cleanup window of at most five seconds.

| Slot | Interface | Fixed coverage |
| --- | --- | --- |
| 1 | CLI | Complete `parse_capture`, physical lines 21–41. |
| 2 | HTTP API | `find_contact`, lines 68–83; two reported frames counted separately from one selected declaration. |
| 3 | CLI | `shard_messages`, lines 109–158; `max_body_bytes=1024`, explicit truncated partial source. |
| 4 | HTTP API | Preserve genuine source while reporting a required missing-entry gap. |
| 5 | CLI | Two genuine `verify_page_one` candidates; explicit ambiguity. |
| 6 | HTTP API | Reject a parent-path entry before registry admission or worker creation. |
| 7 | CLI | `max_worker_rss_bytes=1`: retain the genuine resource failure and actual closure; not a performance pass. |
| 8 | HTTP API | One cancellation after actual live-worker identification; preserve a committed result if completion wins. |

Independent expected source bytes, physical spans, profile values and states are fixed before product output. Unexpected mismatches stop later slots. Missing live OS identity remains an evidence gap; saved receipts cannot fabricate a past observation. A cancellation race does not justify rerunning or padding the target.

## Results and frozen continuations

The following records remain separate. A continuation reads prior evidence without repeating its analyses and executes only the remaining fixed slots. All original failed receipts and freezes remain intact. **All eight fixed slots have now been attempted once: slots 1–6 and slot 8 passed their fixed checks; slot 7 has incomplete live-identity evidence. Overall eight-slot evidence qualification remains INCOMPLETE.**

| Attempt | Executed scope | Actual outcome |
| --- | --- | --- |
| E1, fixed r2 | Preflight only; no CLI, worker or listener created. | **Environment FAIL**: available Darwin pressure was `warning`, raw `2`, errno `0`; tool exit `4`, `actual_host_memory_pressure`, elapsed 0.128 s. All eight slots unrun in this attempt. |
| E2 | Slot 1 CLI completed once; slot 2 was never dispatched. | **Collector FAIL**: a helper function shadowed the imported HTTP module. Slot 1 independently passed its original source/resource/closure checks; its valid result is retained. |
| E3 | Slot 2 HTTP API completed and committed once; no later slot dispatched. | **Collector FAIL**: the observer incorrectly compared the HTTP `final` projection to the entire supervisor envelope. Independent verification passed all 32 original slot-2 checks; its valid result is retained without rewriting the failed receipt. |
| E4 | Slots 3–6 passed; slot 7 triggered the intended RSS failure; slot 8 never dispatched. | **Collector FAIL**: gap logging supplied `kind` twice, raising `TypeError`. Slot 7 had already recorded its missing live individual OS identity and remains **EVIDENCE INCOMPLETE**; fixing the logger cannot repair that missing observation. |
| E5 | Slot 8 only: actual in-flight cancellation passed; no earlier analysis repeated. | Expected tool exit `4`, `evidence_incomplete`: semantic checks passed, collector `errors=[]`, no new evidence gap. The only inherited gap is slot 7's missing live individual OS identity. |

E1's observer sampled RSS 50,020,352 B, high-water RSS 50,069,504 B and owned allocated peak 12,288 B. These are observer measurements, not worker performance. Its positive OS identity and actual tool exit were recorded; shared cleanup took about 0.062 s before receipt persistence. No analysis process group or listener was created. The prior observer freeze had already failed static review because exception cleanup could miss a late worker and a path-rejection record overstated negative process observations. It remains intact and unlaunched; additive r2 corrected those defects and passed independent review plus 140 file-binding checks before the real pressure stop.

Host pressure returned to `normal` / raw `1` in a fresh read-only probe before E2. Each executed successor was separately frozen, independently reviewed and submitted once through normal tool approval. E2–E5 preserve the original eight inputs/oracles and standard limits. Their helpers changed only collector defects and continuation bookkeeping; product and target source did not change. Consumed observation/cleanup time is charged conservatively against the original 54-minute / five-second total. There has been no analysis retry or replay of an executed slot.

| Retained per-slot evidence | Slot 1, E2 | Slot 2, E3 |
| --- | --- | --- |
| Reading | Complete `parse_capture`, 747 B, physical lines 21–41. | Complete `find_contact`, 641 B, physical lines 68–83; two input frames have `duplicate` / `valid` statuses and resolve to one unique main declaration. |
| Native worker resources | Seven normal/raw-1 pressure samples; five RSS samples, sampled peak 30,310,400 B, high-water RSS 30,359,552 B. | Eight normal/raw-1 pressure samples; six RSS samples, sampled peak 31,244,288 B, high-water RSS 32,096,256 B. |
| Persistence and closure | Original documents/source associations verified; `COMMITTED`, actual worker wait `0`, positive live OS identity and owned PID/group/port closure. | Original documents/source associations verified; `COMMITTED`, actual worker wait `0`, positive live OS identity and owned PID/group/port closure. |

For a retained final envelope, the existing HTTP contract returns the original staged document in `report`, and only the envelope's `run` and `usage` fields in `final`. E3's collector assertion contradicted that contract; its failure does not establish a product API failure. Independent verification binds the retained original report, final projection, captured source and checkpoint evidence. It does not manufacture an original raw HTTP response file where none was saved.

| E4 fixed slot | Independent actual result |
| --- | --- |
| 3 | **PASS**: partial/committed, CLI exit `1`; exact 1,024-byte prefix of the original 2,039-byte declaration, actual lines 109–132, `body_truncated`, incomplete range/symbol retained. |
| 4 | **PASS**: partial/committed, API ready/verified/persisted; original 747-byte source retained with the required synthetic `entry_unavailable` gap. |
| 5 | **PASS**: partial/committed, CLI exit `1`; original source retained and `entry_ambiguous` identifies exactly the two frozen `verify_page_one` declarations. |
| 6 | **PASS for fixed predispatch checks**: HTTP `409`, `forbidden_path` / `path`, unchanged registry; bounded probes before/after observed no live worker or new positive worker identity. Discrete probes cannot prove that no arbitrary transient process existed. |
| 7 | **EVIDENCE INCOMPLETE**: genuine `scope_limit` / `max_worker_rss_bytes` at `worker_memory`, observed RSS 229,376 B above 1 B, CLI exit `4`, uncommitted. The core records one start, no restart, TERM and actual wait `-15`; no qualifying external positive live individual OS identity was captured. This is neither a full acceptance pass nor a performance pass. |

Slots 3–5 have positive live OS identities, normal/raw-1 pressure and actual worker wait `0`. Their sampled peak RSS values were 30,638,080 / 30,736,384 / 28,213,248 B; corresponding independent high-water measurements were 31,162,368 / 30,720,000 / 28,835,840 B. These are bounded samples, not an instantaneous peak guarantee. E4 retained 22 normal/raw-1 collector pressure samples, elapsed 1.426 s and cleanup about 0.109 s before receipt persistence. All four owned parents were actually waited; owned groups, exact PID probes and the owned port closed. None of these observations fills slot 7's missing live identity.

## Final cancellation case and remaining gap

E5 attempted only slot 8. After observing the current worker's positive individual OS identity within its actual lifetime, it sent one cancel request. The outcome was **CANCELLED**, uncommitted, with no `COMMITTED` receipt; active cancellation was genuinely exercised. The worker had two normal/raw-1 pressure samples and one RSS sample of 196,608 B, then TERM and confirmed actual wait `-15`.

Normal service shutdown returned HTTP `202` / `stopping`, and the service's actual wait returned `0`; the owned group, exact PID probe and owned port were closed. E5 retained six normal/raw-1 collector pressure samples, elapsed 0.527 s and cleanup about 0.037 s before receipt persistence. Its semantic checks passed with no new collector error or evidence gap. Its expected overall exit `4` / `evidence_incomplete` carries forward slot 7's original missing identity; E5's successful observations do not repair that past gap.

All 19 frozen target files and its clean Git revision were unchanged after each executed attempt. Model calls and target execution remained zero. Raw checkpoints, source associations, failed receipts, exact tool exits and independent sidecars stay in local private evidence; no failed attempt is reclassified as passing.

No original slot remains unrun. There were seven genuine analyses plus slot 6's predispatch rejection, sequentially, with zero automatic analysis or launch retries. Slot 7's RSS guard outcome is preserved, but its missing live individual OS identity keeps full eight-case evidence qualification incomplete. Sustained/larger-load performance, additional source languages and native Linux monitoring remain unrun; this small repository is not a performance benchmark or proof of target correctness.

## Acceptance conclusion v1.0 — 2026-10-16

The accepted boundary covers MACR's CLI, HTTP service and worker. The existing inner collector must retain source/invocation bindings, live worker identity, resource observations, actual waits and thread/queue/owned-group cleanup evidence. The proof chain ends at the execution platform. Its outermost CPU and independent deadline-bound exit upper bound remain **UNMEASURED**; tool exit status and later PID absence do not supply those measurements. Original product resource, timing, failure and cleanup rules remain in force.

This version maps retained evidence; it is not a new eight-case run or a rescore of any frozen attempt.

| Original slot | Retained evidence | Scoped conclusion |
| --- | --- | --- |
| 1 | E2 source, committed documents, live identity, resources and actual waits. | Fixed complete-source checks passed. E2 collector failure remains. |
| 2 | E3 saved report/envelope, checkpoints and independent 32-check verification. | Fixed API checks passed. Separate raw wire body was not saved; E3 collector failure remains. |
| 3 | E4 exact 1,024-byte source prefix, partial status and committed documents. | Truncation checks passed; incomplete source is explicit. |
| 4 | E4 retained genuine source and required missing-entry gap. | Fixed partial-result checks passed. |
| 5 | E4 two genuine declarations and required ambiguity gap. | Fixed ambiguity checks passed. |
| 6 | E4 HTTP409/path rejection, unchanged registry and bounded probes. | Predispatch checks passed; no continuous negative process proof is claimed. |
| 7 | E4 genuine RSS failure and TERM/wait; original live identity missing. | Historical evidence remains **INCOMPLETE**. A later separately frozen targeted run observed the expected guard, live identity and cleanup; it does not replace E4. |
| 8 | E5 live identity before one cancel, cancelled/uncommitted, actual waits and service closure. | Active cancellation checks passed. E5's inherited incomplete batch status remains. |

The later targeted run used the new timing metadata and the implementation represented by `762b1fa`; smoke and CLI each ran once, with no retries. CLI exit4, worker TERM/wait(-15), no publication and the independent worker identity matched the fixed RSS1 case. Under the accepted boundary, this invocation's targeted product guard and required inner-collector checks passed; its frozen overall incomplete verdict remains unchanged. The inner entry's wait4 CPU and a deadline-qualified monotonic exit upper bound were recorded. That upper bound is not the exact exit instant; its CPU scope does not establish product-only CPU, outer-platform CPU or observer overhead.

No unresolved product behavior defect is established by these reviewed cases. Recorded unexpected failures belong to environment or collector execution and remain preserved. Still untested are the complete native CLI/API matrix on the newer implementation, sustained/larger-load behavior and Linux monitoring. These limits prevent a claim of complete new-revision or performance acceptance; they do not justify recursive observers, replaying consumed cases or changing historical verdicts. This update runs no analysis and grants no new native execution permission.
