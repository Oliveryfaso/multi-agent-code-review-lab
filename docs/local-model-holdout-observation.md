# Completed local holdout comparison — 2026-10-11

Status: **24 real outputs captured; captured-response semantic reviews accepted; recovery service guard failed**.

Both models passed **4 / 12 (33.3%)** on the unchanged [holdout](../evals/diagnostic-holdout-v1/cases.json) and [scoring reference](../evals/diagnostic-holdout-v1/reference.json). The 7B model improved output-contract compliance but provided no overall diagnostic pass gain. The paired comparison is complete at the output level; it combines the preserved 4B twelve and 7B first five responses with exactly seven new 7B responses. The 7B result spans two services. [Sanitized machine-readable results](local-model-holdout-results.json) retain every case score, independent review dimension and reason.

## Scores and failure types

| Model | Strict JSON | Complete field / citation contract | Semantic diagnosis |
| --- | ---: | ---: | ---: |
| Unsloth Qwen3-4B-Instruct-2507 Q4_K_M | 12 / 12 (100%) | 4 / 12 (33.3%) | 4 / 12 (33.3%) |
| Qwen2.5-Coder-7B-Instruct Q4_K_M | 12 / 12 (100%) | 11 / 12 (91.7%) | 4 / 12 (33.3%) |

All format failures remain in the twelve-case quality denominator. The fifteen contract-valid rows received independent semantic review; nine rejected contract rows received no semantic score. There are no pending, unattempted or response-row infrastructure failures. A service-monitor failure is recorded separately below. GBNF compatibility and strict JSON did not establish citation validity, verdict correctness or supported reasoning.

| Model | Expected class (4 each) | Pass | Contract failures | Semantic failures |
| --- | --- | ---: | ---: | ---: |
| 4B | Defect | 3 / 4 | 1 | 0 |
| 4B | Healthy | 0 / 4 | 4 | 0 |
| 4B | Unknown | 1 / 4 | 3 | 0 |
| 7B | Defect | 1 / 4 | 1 | 2 |
| 7B | Healthy | 0 / 4 | 0 | 4 |
| 7B | Unknown | 3 / 4 | 0 | 1 |

| Case / topic | Expected | 4B | 7B |
| --- | --- | --- | --- |
| c01 / closed interval | Defect | Pass | Missed defect |
| c02 / last element | Healthy | Contract failure | False alarm |
| c03 / codec ordering | Unknown | Contract failure | Pass |
| c04 / snapshot alias | Defect | Pass | Contract failure |
| c05 / dictionary copies | Healthy | Contract failure | False alarm |
| c06 / predicate contract | Unknown | Contract failure | Unsupported defect |
| c07 / shared default | Defect | Contract failure | Pass |
| c08 / copied list | Healthy | Contract failure | False alarm |
| c09 / TTL unit | Unknown | Contract failure | Pass |
| c10 / percentage | Defect | Pass | Missed defect |
| c11 / empty separator | Healthy | Contract failure | False alarm |
| c12 / host index | Unknown | Pass | Pass |

The 4B model's eight contract failures included duplicate references, inconsistent verdict/unknown fields and an invalid citation. Its healthy cases were rejected by the contract and were not semantically reviewed. All four contract-valid 7B healthy answers alleged unsupported defects: a list-return obligation, dictionary aliasing, modification of a copied input list, and empty-separator fallback. The 7B model also missed the closed-interval defect, invented order loss for an unspecified filter callback, and gave a healthy verdict for the percentage defect despite a correct cause description. The latter case, c10, failed; c09's specific missing TTL unit passed.

Review corrections preserved the original outputs. The 4B c04 explanation still has inaccurate return/line wording; c12 still has incomplete prose and omits valid negative indices. Their core causal/missing-evidence claims meet the frozen rubric, so their earlier numerical scores did not change. The 7B c09 incomplete ending was not completed, and c10's contradictory verdict and partial trigger were not repaired.

## Actual execution and cost

Official llama.cpp b11521 ran on the same Mac, CPU only, two inference threads, parallel one, context 2,048, input cap 1,536 and output cap 384. Frozen messages, ordering, model/runtime revisions, explicit ChatML template, the same 1,538-byte GBNF, temperature 0, top-p 1 and seed 0 were retained. No case code was executed. Every captured response returned HTTP 200, matched native token usage and had independently checked verbosity-5 slot release/idle evidence.

| Service / cases | Requests | Wrapper success | Sampled peak RSS | Available memory start / minimum | Service elapsed |
| --- | ---: | --- | ---: | ---: | ---: |
| 4B / c01–c12 | 12 | Yes | 4.328 GiB | 62% / 53% | 296.604 s |
| 7B / c01–c05 | 5 | No | 6.304 GiB | 59% / 34% | 182.939 s |
| 7B recovery / c06–c12 | 7 | No | 7.082 GiB | 79% / 71% | 148.768 s |

Across each model's twelve requests, 4B HTTP latency averaged **23.797 s** (median 23.029 s), and 7B averaged **25.607 s** (median 22.391 s). Input totals were 3,307 tokens for each; output totals were 3,013 / 1,607. The largest prompt/output were 317/307 for 4B and 317/221 for 7B. These are end-to-end HTTP timings with different output lengths, not equal-length decoding throughput. The 7B services totalled **331.707 s**, including two starts and their health/cleanup overhead; 4B totalled 296.604 s.

The latest continuation consumed exactly **one service start and seven generation requests**, exclusively c06–c12. The comparison consumed three starts and 24 requests in total. Cumulative actual model services are **20**; the retained VM count remains **15**. There was no retry, completed-case redispatch, new download, new VM or external model API in the continuation.

All three owned services received identity-bound SIGTERM and exited with code 0. Their process/listener observations were absent, reader/monitor threads completed, and owned empty temporary directories were removed. Cleanup took 0.146 / 0.195 / 0.179 seconds. Subsequent read-only ownership checks again found the owned services absent and sent no signals.

## Service guard and scoring provenance

The first 7B service stopped after c05 on `record_write_reserve`. The recovered service captured all seven remaining outputs, but its wrapper recorded **success=false** because allocation monitoring encountered `ENOENT` while checking a temporary comparison-state file. Enumeration/stat racing an atomic state-file replacement is consistent with the source and error; the exact stack, scan sequence, exception monotonic time and temporary-file lifetime were not retained. This is an inference, not a proven unique cause. Individual output capture/usage/GBNF/release/idle integrity passed independent audit. The service failure remains preserved; full runtime allocation/free-disk compliance is **not certified**.

The original finish origin and pending comparison remain unchanged. Under new authorization, a derived snapshot of the first seventeen captures with unchanged verified record content and hashes received a fresh review origin and formal acceptance before the additional service started. A separate final snapshot then combined those unchanged seventeen records with the seven new records and accepted all applicable reviews. No old process-local token was restored and no initial authority was overwritten. Offline scoring generated no model calls and retains its generic `real_model_accuracy_measured=false` marker; the real-execution statements here are supported separately by native captures and independent owner audit. Accepted captured reviews do not certify whole-service guard compliance.

A recovery preparation attempt stopped when a non-content header-read duration differed; it was narrowed to reproducible content metadata while preserving identity/content qualification. That engineering correction is separate from the historical download stops.

## Storage and acquisition history

The historical 32 MiB record cap was too small for the observed allocation pattern. Its post-stop inventory contained **2,664,058 logical file bytes but 33,947,648 allocated bytes (32.375 MiB)**, excluding the two exact native logs. On the observed ExFAT volume, many small payload and AppleDouble entries each allocated 128 KiB. The 133 AppleDouble files alone allocated 16.625 MiB, and directories another 1 MiB. This explains storage amplification; it is not evidence of memory pressure. Historical memory minima were 53% / 34%, above the 20% running floor. The 32.375 MiB snapshot was measured after stopping, not at the exact failing reserve check.

The newly authorized record and nonweight caps were both **512 MiB**. RSS limits remained 5.5 / 8 GiB; memory start/run floors remained 30% / 20%; total allocation remained 5 GiB and free-disk start/run floors 7 / 2 GiB. The final scoring snapshot observed 77.75 MiB records and 83.25 MiB nonweight allocation before this public export. Those snapshots do not cure the monitoring failure or establish whole-run compliance.

The earlier complete HTTP 200 GET stopped with `acquisition_io_deadline_reserve` after **4,240,863,473 / 4,683,073,536 bytes**. The shared 1,800-second preparation allowance already counted about 936.799 seconds before the recorded attempt, leaving about 848.201 seconds before its I/O reserve. The saved failure names that deliberate time gate. Later one HTTP 206 Range response received all **442,210,063** missing bytes, but the downloader rejected a post-transfer FD/path identity difference with `partial_changed_during_full_read`. The failed before/after values were not retained, so the changed field and cause remain unknown. Neither historical failure is reclassified as successful.

Subsequent independent stable full reads verified the complete length, expected SHA-256 and both historical prefixes, followed by a separately qualified same-directory rename. This established derived complete-content/source/identity qualification before execution; it did not fabricate a native streamed checksum or erase the failure receipts.

- 4B revision: `a06e946bb6b655725eafa393f4a9745d460374c9`.
- 7B revision: `13fb94bfda8c8cf22497dc57b78f391a9acb426a`.
- 7B complete SHA-256: `509287f78cb4d4cf6b3843734733b914b2c158e43e22a7f4bf5e963800894d3c`.
- Candidate GBNF SHA-256: `cc5ceae80ed6ffe95c7f3d75c32fb9a1f4820789ecc1962ac96fab6477dc2a40`.

## Selection for the current task

This holdout does **not justify choosing 7B for better source diagnosis**. It improved contract compliance and missing-evidence abstention, but overall semantic success stayed at 4/12, supported-defect success fell from 3/4 to 1/4, and all healthy cases failed. Its largest sampled RSS was about **1.637 times** 4B's; mean HTTP latency was about **1.076 times** as long despite shorter outputs. Both 33.3% outcomes are inadequate evidence for automatic diagnosis. Twelve fixed cases, differing service/system states and the 7B restart limit generalization; this observation is a small task-specific result, not a general model ranking.

Independent final verification confirmed all 24 retained rows, fifteen accepted reviews, nine contract rejections, category totals, unchanged original captures and the preserved initial authority. This export adds no model calls.
