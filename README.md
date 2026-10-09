# Multi-Agent Code Review Lab

A local Python code review and Debugger research lab. The public core includes source inventory, sanitized snapshots, AST/symbol/call indexing, bound context and citations, provider contracts, budget accounting, and a single-writer run journal.

Python 3.10+ on macOS or Linux; the core uses the standard library and has no package dependencies.

```bash
python3 -B cli/agent_review.py --help
```

`static-baseline` retrieves source context from an explicit input JSON and writes an offline report to a new project artifact directory. It does not infer a proven cause, call a model or execute repository code. Input fields are `symptom`, `symbol_names` and `reported_traceback`; analyzed files are in a sibling `source/` directory. Output directories must not already exist.

```bash
python3 -B cli/agent_review.py static-baseline --input examples/my-case/input.json --out artifacts/my-baseline --json
```

Supply your own small case at the example path. The source reader excludes secret/configuration paths and metadata; source is parsed as data and is never imported.

Offline regression uses explicit test names and a separate UUID directory per run:

```bash
python3 -B scripts/run_offline_tests.py test_offline_tests
python3 -B scripts/run_offline_tests.py test_code_graph test_repository_scope
python3 -B scripts/run_offline_tests.py test_context_reader test_static_readers
```

Each run has a 120-second deadline, a 64 MiB allocated-output monitoring threshold and at most 2 MiB of summary/diagnostic records. Successful runs remove only their newly created temporary fixtures; failures retain their directory and diagnostic. File allocation monitoring is not a hard quota. The allowlisted test guard blocks native process/network dispatch and redirects default snapshot, index and patch-check output. It is not an untrusted-code sandbox. Direct tests that require fixture storage reject an unmanaged fixture root; use the bounded entry instead of full discovery.

`rules` and scripted `mock` providers are available. Explicit loopback HTTP profiles support externally managed local services; no model, runtime or service launcher is bundled. Token/usage fixtures do not prove deployed model readiness. Remote fallback is disabled by default.

Dynamic checks require an approved execution backend and sanitized inputs. The public package contains backend contracts and result normalization, with host execution closed. Machine-specific VM/service orchestration and its private authorization/evidence are kept outside this public core. A citation hit or passing scripted fixture is not a repaired repository.

The existing CLI review/workbench paths remain for compatibility; this release verifies selected offline regressions and CLI parsing, not live services or arbitrary repair. See [SECURITY.md](SECURITY.md) for project boundaries.
