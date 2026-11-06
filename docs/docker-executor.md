# Opt-in Docker public-test executor

`DockerBackend` implements the existing `ExecutionBackend` contract with a real
Docker CLI lifecycle, bounded subprocess I/O and a self-contained Python test
collector. It is **disabled by default**. No live engine qualification, container
run or original/reference/masking calibration is established by its offline
tests. Previously refused native operations remain stopped;
this adapter is not an automatic alternative route around a refusal.

After separately authorizing the exact engine, image, data and lifecycle,
the normal workbench accepts **both** `--enable-repair-verification` and
`--repair-backend-config PATH`. The configuration cannot come from the browser.
Startup qualifies fixed metadata before model Key input or HTTP binding. Without
a configured backend, the existing default factory still returns `None`.
Model calls retain their separate frozen spend-plan/cell and credential gates.
These options request a code path; they are not an execution grant or isolation
attestation.

## Configuration

The strict `macr.docker.backend.v1` object has exactly these 14 fields. Values
must describe separately approved resources; there are no discovered defaults.

| Field | Required value |
| --- | --- |
| `schema` | `macr.docker.backend.v1` |
| `backend_id` | Bounded application-owned identifier |
| `docker_path`, `docker_sha256` | Absolute Docker executable path and exact SHA256 |
| `endpoint`, `daemon_id` | Explicit local `unix://` socket and expected daemon ID |
| `engine_version` | Exactly `29.7.2`; client must also report `29.7.2` |
| `image_ref`, `image_id` | Approved repository digest (`name@sha256:…`) and matching local image ID (`sha256:…`) |
| `architecture` | `arm64` or `amd64`, matching engine and image |
| `control_root` | Private owned control directory, separate from snapshots |
| `snapshot_root` | Allowed captured-snapshot root; never an original-repository mount |
| `expires_at` | Integer admission expiry |
| `max_jobs` | 1–64 retained job claims under this control root |

Qualification checks only the selected binary/socket and bounded version,
daemon and image metadata. It requires Linux, cgroup v2, a non-Swarm-manager
daemon, resource-limit support, seccomp, matching identity/digests and an image
without inherited volumes or unknown environment keys. It creates private
control records and an empty private Docker client configuration. Its 20-second
target and binary-read bounds are local protections. Metadata qualification is
recorded as `local_unattested`, with `isolation_observed: false`; it does not
establish a trusted image's contents, effective isolation or permission to run.
An approved image must already contain CPython and, for pytest tasks, pytest and
the needed dependencies. Nothing is pulled, built, installed or logged in.

## One check and its cleanup

1. Reverify the captured snapshot and exact unittest modules or frozen pytest
   nodeids. At most 32 regular files/16 MiB are staged in a host-generated bounded
   tar; links, arbitrary artifacts and reserved collector paths are rejected.
2. Create an owned local Docker volume and a **never-started** staging container.
   Copy the archive into that container's writable managed volume, then remove
   the stager and confirm its absence. There is no host bind mount.
3. Create the target with read-only input volume/root, non-root UID, no network,
   no capabilities, no-new-privileges and builtin seccomp. Project scratch storage
   uses bounded `/work` (64 MiB) and `/tmp` (32 MiB) tmpfs. Entrypoint, environment,
   mounts and limits are checked before start; image environment values are
   cleared or replaced. The CLI gets a small environment whitelist and no
   inherited model credentials or personal Docker configuration.
4. The fixed collector verifies input bytes and effective read-only, namespace,
   privilege and cgroup conditions before importing target tests. It reports
   exact test identities and outcomes. Missing/duplicate tests, skips, xfails,
   collection errors or output overflow cannot become a pass. Raw framework
   output is discarded; the host accepts only the bounded bound result.
5. Recheck daemon and full container-ID/ownership bindings; force-stop only owned
   running container IDs if needed, remove both containers and the volume, and verify strict
   absence. Only then can a completed result be returned. A cached public-result
   reader binds the same snapshot, spec, source and tests; it never reruns them.

Supported checks use `docker-public-python-v1`, up to one CPU quota, 16–512 MiB
memory with equal swap limit, 1–32 PIDs, 1 KiB–1 MiB output and **15–60 seconds
including preparation and a 10-second cleanup reserve**. CPU is a rate quota,
not a CPU-seconds measurement. The helper I/O deadline is not a preemptive spawn
guarantee; owned-helper TERM/KILL waits can add 0.2 + 0.5 seconds. Killing a helper
does not prove that a daemon-side container stopped.

Mutations have durable intents and are not automatically retried. Reused claims,
unknown outcomes and incomplete cleanup block later work rather than resetting
admission. Expiry blocks new work; already-owned finishing/cleanup remains allowed
only within the original deadline. No old PID/name is blindly killed. Uncertain
ownership or deletion may leave resources behind, with no completion credit.
Control metadata and snapshots remain private locally; they are not automatically
deleted or uploaded. The control-root lock coordinates cooperating owners of
that root, not every actor or workload on a Docker daemon.

## Before real use

First obtain an applicable platform decision for this exact dedicated engine
and lifecycle, respecting any previous refusal. Then verify the approved pins
and run self-authored isolation/timeout/cleanup probes plus original-fails,
reference-passes and masking-fails cases through the same backend. Check public
test identities and retain failures before approving a model repair pilot.
No qualified live engine or successful calibration is currently claimed.

Docker daemon access is privileged and containers share an engine kernel; these
flags do not prove safety against engine/kernel defects. Target Python can also
interfere with its own test framework or emitted result. Results therefore retain
`backend_reported_unattested`, `target_code_executed: null` and `repaired: false`.
They are not authenticated scores, hidden-test success or evidence of model gain.

Protocol sources: [Docker run isolation/resource options](https://docs.docker.com/reference/cli/docker/container/run/),
[managed volume semantics](https://docs.docker.com/engine/storage/volumes/),
[Docker CLI v29.7.2 inspect handling](https://github.com/docker/cli/blob/v29.7.2/cli/command/inspect/inspector.go)
and [Moby docker-v29.7.2 container errors](https://github.com/moby/moby/blob/docker-v29.7.2/daemon/errors.go).
The exact inspect-error handling is version-specific; another version requires
qualification, not a generic “not found” fallback. Versioned source supports the
protocol design, not the state of an unobserved local installation.
