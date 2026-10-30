# Bounded Debugger Value Stage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare one finite, gold-free, same-input comparison with a developer gate and an explicit simplify decision.

**Architecture:** Reuse the two-flow probe and selected-cell study kernels, provider, gates and strict review helpers. One thin stage owner binds exact scope and a two-arm report; default remote access stays off.

**Tech Stack:** Python standard library and existing offline fixture runner.

**Spec:** `docs/superpowers/specs/2026-10-30-debugger-value-stage.md`.

## Global Constraints

- No real API, Key or custody gold reads in this implementation; no changes to frozen data/results.
- New stage only: 6/$0.50 S0 plus 54/$1.50 S1; single concurrency, no retry/recovery.
- Same complete <=4096-byte source per task initially, no initial AST facts in either arm.
- Preserve old profiles, 200-character gap, all validators and three-request limits.
- No new service, dependency, data set or general scheduling framework.

## Review Focus

- S0 failure must not create S1 request reservations or read another credential.
- Missing scores/unknowns/unattempted cells cannot become ties or proof of no benefit.
- Raw requests cannot contain tools or host AST facts; paired E0 bytes must match.
- Failed responses with trusted usage must stop on input-plan overrun after settlement.
- Scope/path/code/price drift and reused/copied claims must reject before dispatch.

### Task 1: Freeze and exercise the two-arm profile

**Files:** generation/manifest/study profile/runner; `tests/test_deepseek_value_stage.py`.

- [ ] Write wire-level failing fixtures for new explicit profile: identical no-AST E0, raw without tools, D terminal restrictions, unchanged legacy defaults.
- [ ] Run RED; add only the new profile and source/input contract.
- [ ] Run GREEN and existing profile/field-boundary regressions.

### Task 2: Whole-stage gate, report and entry

**Files:** `src/macr/evals/deepseek_value_stage.py`, `scripts/run_deepseek_value_stage.py`, value-stage tests and offline/CI allowlists.

**Interfaces:** `prepare_value_stage(pack_root, *, custodian, out, project_root, seed=20261030)`, `load_value_stage(control)`, `propose_value_approval(control, *, credential_source=None)`, `preflight_value_stage(control, approval)`, `run_value_dryrun(control, *, scripted_by_cell)`, `execute_value_stage(control, approval, *, enabled=False, key_reader=None, scripted_by_cell=None)`.

- [ ] Write RED for exact 60/$2 scopes, full-source neutral pack requirement, S0-gated S1, fake end-to-end execution, unknown/identity/usage stops, no replay and copied path refusal.
- [ ] Reuse existing kernels and gates; no alternate transport or recovery system.
- [ ] Add pure, strict two-arm aggregation/selection using existing semantic-review helpers; pending scores cannot decide adoption.
- [ ] CLI reuses existing safe Key callbacks only after preflight; no real reads in tests.
- [ ] Run full related offline regressions with original guard limits, independent code/design review, explicit scoped commit and normal push; verify precise-commit CI.

### Task 3: Actual freeze dependency and approval handoff

- [ ] Give parent exact neutral-pack and five-field custodian requirements; do not create blind tasks.
- [ ] Validate public fixture dry-run and CLI; document that no neutral blind pack or live consent exists yet.
- [ ] After independent neutral handoff, freeze actual file/byte inventory and scope hash without Key/network.
- [ ] Return one complete 60/$2 proposed approval card with official destination, source inventory, credential risk, log policy and natural stopping rule. Actual execution remains a separate user-approved step.
