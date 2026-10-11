# Bounded native holdout observation — 2026-10-11

Status: **partial measurement; formal semantic acceptance pending**.

The approved batch used the unchanged [12-case holdout](../evals/diagnostic-holdout-v1/cases.json) and [scoring reference](../evals/diagnostic-holdout-v1/reference.json). Four cases each cover supported defects, healthy behavior and missing evidence. The existing 1,538-byte candidate GBNF, frozen messages, reference and scoring dimensions remained unchanged. No case code was executed.

## Actual execution

Both services used official llama.cpp b11521 on the same Mac, CPU only, two inference threads, parallel one, context 2,048, input cap 1,536 and output cap 384. Sampling was temperature 0, top-p 1, seed 0. Native verbosity 5, usage, slot release and idle observations were retained for every captured response.

| Model | Executed / planned | Strict JSON | Contract | Peak RSS | Service elapsed |
| --- | ---: | ---: | ---: | ---: | ---: |
| Unsloth Qwen3-4B-Instruct-2507 Q4_K_M | 12 / 12 | 12 / 12 | 4 / 12 | 4.328 GiB | 296.604 s |
| Qwen2.5-Coder-7B-Instruct Q4_K_M | 5 / 12 | 5 / 5 | 4 / 5 | 6.304 GiB | 182.939 s |

All 17 requests returned HTTP 200 and matched the retained native token usage and idle evidence. The largest measured prompt/output were 317/307 tokens for 4B and 312/221 for 7B. System available memory was 62%/59% at startup and at least 53%/34% during the respective services. The RSS limits were 5.5/8 GiB. Each service's auxiliary observations stayed below its 60-second total; logs were 3,268,257 and 1,144,792 bytes.

The 7B service stopped after c05 with `record_write_reserve`; c06–c12 were never dispatched. There were exactly two additional service starts and 17 generation requests, with no retry, VM start or external model API. Cumulative actual model services are 19; the retained VM count remains 15.

Both owned services received identity-bound SIGTERM and exited with code 0. Their PID and listener observations were absent after shutdown, reader/monitor threads completed, and owned empty temporary directories were removed. Cleanup took 0.146/0.195 seconds. A subsequent read-only ps/lsof check again found both owned PIDs and ports absent and sent no signals.

## Contract and semantic observations

GBNF compatibility with this runtime was observed for both models. Strict JSON success did not establish the complete output contract: retained failures included duplicate references and an out-of-range citation. These failures remained in the quality denominator; recorded infrastructure response rows were zero.

Independent semantic drafts found four supported 4B results (c01, c04, c10, c12) and one supported 7B result (c03). The other three contract-valid 7B answers contradicted the supplied source or behavior contract. These drafts are **unaccepted evidence**, not formal scores. The durable pending comparison has zero formally reviewed rows and a null diagnostic rate for both profiles. The intended complete paired 12-case comparison was not obtained.

## Storage and acceptance limits

The fixed record allocation limit remained 32 MiB. The stopped batch's subsequent finish writes brought actual record allocation, excluding the two exact native service logs, to **33,947,648 bytes (32.375 MiB)**, exceeding the limit by 393,216 bytes. Nonweight allocation was 37.75 MiB against 64 MiB; total allocation was 4.398 GiB against 5 GiB. This batch must not be reported as fully budget compliant.

Only the finish origin and a pending comparison were saved. No finish consume/accepted authority, finish-waiting record, execution summary or final-outcome file exists. The native owner's printed final-outcome path did not confirm persistence. The process-local finish origin will not be reconstructed or reused. Further model requests require a new approved batch; this observation adds none.

## Acquisition and reproducibility

One bounded Range attempt returned HTTP 206 and added the missing 442,210,063 bytes to the already retained 7B prefix. No complete GET or redownload occurred in this recovery batch. The native downloader stopped on a local identity-stability check; its failure receipt remains preserved. Subsequent independent full reads verified the final 4,683,073,536-byte file, both historical prefixes, current stable FD/path identity and the expected full SHA-256 before any service start. This is derived complete-content qualification, not a fabricated native download success or streamed checksum.

- 7B revision: `13fb94bfda8c8cf22497dc57b78f391a9acb426a`.
- 7B complete SHA-256: `509287f78cb4d4cf6b3843734733b914b2c158e43e22a7f4bf5e963800894d3c`.
- Candidate GBNF SHA-256: `cc5ceae80ed6ffe95c7f3d75c32fb9a1f4820789ecc1962ac96fab6477dc2a40`.
- Frozen plan canonical SHA-256: `318e551e0317aa80d0fd7876215dcaa78154085f24dfb11a132e6db7de92a85f`.
- Actual qualification bound 61 modules and 88 source files; the cache qualification retained all 60 runtime/archive files and 118 separate stdout/stderr captures for 59 Mach-O checks.

Public changes admit explicitly configured requests up to 300 seconds in both dataset loading and comparison preparation, while retaining each profile's tighter bound. `begin_finish_review(..., soft_reminder_seconds=1800)` records elapsed reminders without rejecting late reviews. The default hard deadline, process-local origin checks, one-time consume rule and immutable first authority remain intact. The native batch retained preparation 7,200 seconds, service 5,400 seconds, request 300 seconds and whole observation 21,600 seconds; its finish reminder was 1,800 seconds and did not cause this stop.

Validation: 101 related offline tests passed with no skips; a real offline initialization of all 24 frozen cells at 300 seconds passed, while 301 seconds and a request above its profile limit were rejected. Independent source/qualification review and a subsequent read-only review of all 17 real captures passed. Model weights, private configuration, databases and native runtime logs are excluded from this public change.
