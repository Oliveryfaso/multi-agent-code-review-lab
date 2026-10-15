# Release Checklist

Use this checklist for the public local-first tool. Choose checks for the actual change and keep runtime acceptance separate from offline fixtures. This document does not authorize models, VMs, target-code execution or new large-load runs.

## Check the change

Review the exact diff and preserve existing user changes. When commands, installation, module boundaries, supported platforms or behavior change, update the related README, architecture and usage documents in the same batch. See [documentation maintenance](documentation-maintenance.md).

For text and diagram changes, check accuracy, links, heading anchors and rendering. Product behavior changes need the relevant existing bounded checks; documentation changes alone do not require rerunning the native matrix.

## Select bounded checks

Run from the trusted tool checkout. [Offline Core](../.github/workflows/offline-core.yml) contains the current named test selections for contracts, retrieval, persistence and orchestration; use those explicit selections with `scripts/run_offline_tests.py`. Avoid unrestricted test discovery and the historical demo script as release defaults.

For changes to the static-evidence path or local workbench, select the applicable entries below:

| Change | Existing check |
| --- | --- |
| Static-evidence contracts and CLI without native dispatch | `python3 -I -S -B scripts/run_static_evidence_tests.py` |
| Workbench ownership, HTTP and reader behavior | `python3 -I -S -B scripts/run_evidence_ui_tests.py` |
| Workbench JavaScript | `node --check src/macr/api/evidence_ui/workbench.js` and `node scripts/run_evidence_ui_js_tests.cjs` |
| Real loopback lifecycle fixtures | `python3 -I -S -B tests/test_loopback_lifecycle.py` |

The HTTP and loopback checks create the tool's own bounded listeners. Pure or mocked fixtures do not establish native memory-pressure, RSS, worker closure, packaging or browser acceptance. Record the environment, exact revision, pass/fail and unrun scope. Native and external-project checks need their own fixed plan and process/resource evidence; target code and dependencies are not executed for static reading. See [workbench boundaries](evidence-workbench.md).

## Do Not Commit

- Secrets, API keys, `.env`, personal configuration or credentials in URLs.
- Runtime databases, raw traces/logs, bootstrap URLs, private screenshots or exports containing source/input.
- Model weights, VM images, local packages, large generated artifacts or experiment working copies.
- External repository checkouts, uploads, private target source, caches and locally generated patch outputs.
- Private planning notes and other ignored local assets.

Select reviewed source, dependency declarations, tests and documentation by explicit path. Inspect the staged diff; `.gitignore` alone does not establish that a tracked or explicitly added file is safe to publish. Public evidence summaries must be scoped and sanitized. See [SECURITY.md](../SECURITY.md).

## Verify publication

Before committing or pushing, confirm the intended remote repository is `Oliveryfaso/multi-agent-code-review-lab` and check the target branch and staged paths. Keep normal history; resolve remote changes without force-pushing or discarding user work.

After a normal push, compare the exact remote branch commit with the intended local commit and inspect that commit's CI results. Report pending, failed or unavailable CI explicitly; an earlier green run does not verify a new commit.

Summarize user-visible changes, compatibility, evidence and known limits. Distinguish current static evidence reading from historical Agent/Debugger experiments, and distinguish completed reading from root-cause proof, successful repair or a whole-repository verdict. Retain original failures and identify tests or platforms that were not run.
