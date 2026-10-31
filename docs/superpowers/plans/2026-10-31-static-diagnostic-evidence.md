# Deterministic diagnostic evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Follow RED→GREEN and a final independent review; user has already requested direct execution without repeated small approvals.

**Goal:** Produce a real, bounded static evidence capsule and an offline, same-full-source API comparison using existing v3 output contracts.
**Architecture:** Reuse snapshots/index/static_facts and compact-v3. One model call/arm, modern converse, literal fixture transport only; new thin preparation/CLI, no new service/role/framework.
**Tech Stack:** Existing stdlib Python3.12, dataclasses/unittest; no dependencies.
**Spec:** docs/superpowers/specs/2026-10-31-static-diagnostic-evidence-design.md

## Global Constraints

No Key/API/target execution/VM or private gold. Preserve aa46123 results/oldschemas/defaults. Full captured source identical R/E; <=12files/16384bytes, no silent source clipping. Facts1200contextbytes/6functions/32facts/16bindings/50candidates, packet32768bytes, request65536bytes, compact-v3 output8192bytes. All accepted results semantic_pending. Only literal offline responses and <=one request/arm; livecalls0.

## Review Focus

- Ephemeral UUIDs must not masquerade as evidence novelty: pin repeated-content digest.
- Dynamic/rebound/out-of-hop bindings must stay unknown: existing collector and negative fixtures.
- A full-source baseline must not lose selected dependencies: byte/source equality checks.
- Snapshot/packet/request mutation cannot grant citation authority: reverify captured bytes and original scope.
- Provider/CLI errors must retain failure evidence without retries, Key access or successful grades.

## Task1: Capsule and two-arm offline core

Files: create src/macr/investigation/diagnostic_packet.py and tests/test_diagnostic_packet.py. Interfaces prepare_pair/run_offline_pair are fixed in spec.
- [ ] Write tests for actualcross-file binding, stable digest, sourceparity, unknown/truncation/drift and v3 envelope; run RED before implementation.
- [ ] Implement normalization/preparation using real index/collector reads, then one-call fixture converse and v3 validation. No captured content omission or fake facts.
- [ ] Run focused tests GREEN; record real operations, preparation timing, model input bindings and fixture/live distinction.

## Task2: Real offline entry, development examples and docs

Files: create scripts/run_static_diagnostic.py, tests/test_static_diagnostic_cli.py, examples/static-diagnostic-development/ source-onlycases and docs/static-diagnostic-evidence.md. Modify offline test allowlist/workflow plus concise README link; leave existing runtime CLI unchanged.
- [ ] Write actual CLI prepare/offline tests RED, then implement fresh owned directories, safe capture/index, bounded outputs and fixture-only input.
- [ ] Add paired healthy/defect and unresolved examples; run actual CLI with prepared scope-bound literal replies. Verify real facts and request changes; no modelquality claim.
- [ ] Document R/E hypothesis, full-source baseline, costs/safety/caps, closed old result and separately approved nextstage requirements.

## Task3: Review and delivery

- [ ] Independent review core/CLI/negative cases and documentation evidence claims.
- [ ] Related bounded regressions (newmodules plus staticfacts/compact/provider/currentfrozenvalue tests), no unrelated reruns.
- [ ] Inspect exact diff/secret+gold exclusions, normally commit/push reviewed code/tests/docs under existing authorization, verify exact main SHA and twoCI terminals.
- [ ] Report useful mechanism evidence, actual offline entry artifacts and limits; zero new paid requests. No new liveapproval assumed.
