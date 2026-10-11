# Offline core CI

`Offline Core` runs on pushes to `main` and pull requests targeting `main`. It uses Ubuntu 24.04, Python 3.11, SHA-pinned official checkout/setup-python actions and only `contents: read`. A newer run for the same branch or PR cancels the older one.

The job imports public modules, checks CLI parsing and runs selected offline tests in sequential batches, including scripted diagnostic comparison, b11521 observation checks, atomic-write races and explicit static-diagnosis abstention. Every batch uses the existing 120-second deadline, 64 MiB allocated-output monitoring threshold and 2 MiB record limit; the whole job stops after 10 minutes. These are monitored output limits, not filesystem quotas.

Five separate real TCP fixtures then check kernel-assigned loopback listeners, consecutive close/start, live conflicts, a held checking socket and address drift. They run in one Python process, send one byte per simulated service and close every owned socket. Each operation has a one-second timeout and the entire fixture entry has a 30-second guard. This fixed test file is run separately; the offline runner continues to forbid all native socket/HTTP/process dispatch. The fixtures are trusted test code, not an arbitrary-code sandbox or a model-service proof.

No model/VM asset, package installation, dependency cache or artifact upload is used. A failed job prints at most 64 KiB of diagnostic body in its logs. The tests use fresh synthetic fixtures and need only committed core files; they do not validate live services, model quality or arbitrary-code isolation.

Run the explicit test commands from [.github/workflows/offline-core.yml](../.github/workflows/offline-core.yml) locally. Direct full discovery is outside this CI scope. The separate legacy PR-review workflow retains its previous behavior.

The offline runner counts allocation through `macr.memory.atomic_file.tree_usage`. Its snapshot and `atomic_write` share a process-local lock around temporary-file creation and replacement, so a scanner cannot enumerate an atomic temporary file and then lose it to a concurrent replacement. The helper still counts directories, AppleDouble files and temporary files retained after write failure; missing entries and symlinks still fail. The deterministic regression tests use events to force the former enumeration/replacement race and verify both coordination and rejection of genuinely missing files. The lock is not held during file writes or `fsync`.

This coordination only covers writers and scanners using the same imported module in one process. `check_mock_recovery.fixture_bytes` keeps its separate semantics for directly owned child fixtures, including their cleanup and link accounting; it does not use this strict helper. Historical frozen model owners and bindings remain unchanged. A future authorized derived binding must explicitly import the shared helper through its verified module loader before its live allocation guard benefits from this fix; updating the library alone does not repair an old frozen entry. No cross-process coordination or historical whole-run compliance is claimed.

Existing model guards can retain their exact log/weight exclusions, entry checks and budgets by wrapping their existing `stage_usage` and `record_allocation` scan bodies with the public context manager, after the verified loader is installed:

```python
from macr.memory.atomic_file import namespace_snapshot
with namespace_snapshot():
    usage = binding.stage_usage()  # Existing counter and exclusions, unchanged.
```

Both the guard and recorder must use that same `macr.memory.atomic_file` module instance; loading a second copy under another name creates a different lock. The helper does not authenticate an unqualified import or grant another model run.
