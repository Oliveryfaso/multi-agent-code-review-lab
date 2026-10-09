# Offline core CI

`Offline Core` runs on pushes to `main` and pull requests targeting `main`. It uses Ubuntu 24.04, Python 3.11, SHA-pinned official checkout/setup-python actions and only `contents: read`. A newer run for the same branch or PR cancels the older one.

The job imports public modules, checks CLI parsing and runs 69 selected offline tests in six sequential batches, including scripted diagnostic comparison. Every batch uses the existing 120-second deadline, 64 MiB allocated-output monitoring threshold and 2 MiB record limit; the whole job stops after 10 minutes. These are monitored output limits, not filesystem quotas.

No model/VM asset, package installation, dependency cache or artifact upload is used. A failed job prints at most 64 KiB of diagnostic body in its logs. The tests use fresh synthetic fixtures and need only committed core files; they do not validate live services, model quality or arbitrary-code isolation.

Run the explicit test commands from [.github/workflows/offline-core.yml](../.github/workflows/offline-core.yml) locally. Direct full discovery is outside this CI scope. The separate legacy PR-review workflow retains its previous behavior.
