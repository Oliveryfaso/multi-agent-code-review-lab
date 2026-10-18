# Current Mac external-project acceptance — 2026-10-18

This records the completed 2026-10-16 runs. The [historical validation record](external-project-validation.md), including every original FAIL and INCOMPLETE verdict, remains unchanged. Raw source captures, process records and runtime data stay private.

The product snapshot is [`b22fa45d2c641655fca789995a017ed9cd4c8b4e`](https://github.com/Oliveryfaso/multi-agent-code-review-lab/commit/b22fa45d2c641655fca789995a017ed9cd4c8b4e), with 95 source files. The second project remains [wechat-history-export-skill at `50c6799`](https://github.com/Oliveryfaso/wechat-history-export-skill/tree/50c67993eda1b0195f0fad10608812d9a3e6b22c), with 19 frozen files read only as data. No target import, execution, database access or model call occurred.

| Separately frozen batch | Actual outcome | Retained qualification |
| --- | --- | --- |
| Current-version eight-case plan; six admitted analyses, zero automatic retries | Slots 1–6 passed their fixed checks. Slot 7 stopped on a collector RSS/exit race; slot 8 was NOT_RUN. Actual tool exit `4`. | Batch **FAIL**, permanently retained. Slot 7's raw guard and live identity do not replace its failed qualification. |
| Repaired collector; only slots 7/8, two admitted analyses, zero automatic retries | Both fixed cases passed; actual tool exit `0`, post-record checks true, no collector errors or evidence gaps. | **PASS for this two-case batch**, not a single-batch eight-case PASS. |

Slot 7 observed the worker's live independent OS identity and the expected 1-byte RSS guard: 229,376 bytes observed, `scope_limit` / `worker_memory`, one start, zero restarts, TERM and actual wait `-15`. Product `failed` and CLI exit `4` are the expected guard outcome. Publication remained uncommitted, with no `COMMITTED` receipt or publishable report. This is not a performance pass.

Slot 8 rechecked the current worker identity before exactly one cancel request. It actually finished cancelled/uncommitted, with a positive RSS sample, TERM and actual wait `-15`; active cancellation was covered. Service shutdown returned HTTP `202` / `stopping`, followed by actual wait `0`. Owned CLI/service waits, observer-thread exit, queue closure, owned groups, exact PID/port checks and before/after 95-product/19-target bindings passed.

The repaired batch changed only collector RSS/owned-group exit handling and its narrower selection, receipts and budget. Product source, original case inputs, oracles and effective profiles stayed unchanged; slots 1–6 were not repeated. The new total was 330 seconds plus one shared five-second cleanup window. Existing product, RSS, storage, free-space and normal-pressure guards stayed in force. Nine focused offline checks passed before launch; no tests or analyses were rerun to publish this document.

The original RSS/zombie race branches were not encountered in the successful native invocation. Those branches and strict rejection cases are covered by the offline regressions; this run does not claim native reproduction of the races. It adds no observer layer, induced load or retry.

These identifiers bind retained private evidence; they are not public downloads:

| Evidence | SHA-256 | Bound files |
| --- | --- | --- |
| Earlier failed current-version batch results | `44c95c7caa5e6473f0fff97d059061c461297e18327b8a40bda3619f44bc2bad` | 170 |
| Repaired two-case preparation | `4c6be8c36f95dc07cc26abfd23f62468c8ec303da036f68c612b70498412ed2b` | 113 |
| Repaired two-case actual results | `a34eac61e064e5b9bd8d67e611903086fd1d8060dd4124adde0aa4443243dbb5` | 32 |

Independent read-only review found no remaining Critical, Important or Minor issue in this limited conclusion. The same product version now has fixed-scenario evidence across the two batches. No failed batch is rescored, no historical missing identity is backfilled, and no complete single-batch 8/8 result is claimed.

The proof chain ends at the execution platform. Outermost CPU and an independent deadline-qualified exit upper bound remain UNMEASURED. Exact OS lifetime, worker business entry, continuous liveness, target correctness, sustained/larger-load behavior, other source languages and native Linux monitoring remain unverified. These limits do not call for recursive monitoring or additional runs.
