# INC-2026-006: BuildKit cache filled a worker's ephemeral storage and left the registry volume read-only

## Incident metadata

| Field | Value |
| --- | --- |
| Date | 2026-08-08 to 2026-08-09 JST |
| Severity | SEV-3 |
| Status | Resolved; preventive actions open |
| Systems | `workbench` namespace, BuildKit, in-cluster registry, Longhorn, `k3s-worker-3`, k3s control plane |
| Start | 2026-08-08 ~21:20 JST (BuildKit evicted mid-build; reconstructed from the eviction event) |
| End | 2026-08-09 12:58 JST (registry pod restarted onto a read-write volume; push succeeded) |
| Duration | In-cluster image builds and pushes were unavailable for roughly 15.5 hours, most of it unattended overnight |
| Detection | A `buildctl` build failed with a gRPC `graceful_stop`; the operator investigated rather than retrying |
| Data impact | No loss. The registry volume was 22% used at recovery, its ext4 filesystem remounted `rw` cleanly, and `/v2/_catalog` returned all seven pre-existing repositories. No Longhorn replica was rebuilt or discarded. |

## Executive summary

Three consecutive builds of a large development image (~2.2 GB, containing three browser engines)
were run against the shared in-cluster BuildKit. BuildKit's layer cache is an `emptyDir`, which
counts against the node's ephemeral storage, and the accumulated cache drove `k3s-worker-3` below
the kubelet's eviction threshold. The kubelet evicted the BuildKit pod mid-build, which the client
observed only as an opaque gRPC `graceful_stop`. BuildKit rescheduled to `k3s-worker-1` and rebuilt
successfully, but the same node disk pressure had already produced an I/O error on the Longhorn
volume backing the in-cluster registry; ext4 remounted that filesystem read-only, so every
subsequent blob upload returned `500 Internal Server Error`. Restarting the registry pod remounted
the volume read-write and restored pushes. Two unrelated-looking symptoms — a killed build and a
failing registry — had the same root cause, and neither error message named disk pressure.

## Impact

- In-cluster image builds and pushes were unavailable: no workload in `workbench` could be
  rebuilt or redeployed from a new image for the duration.
- The `ais-backend` image could not be published, blocking the `ai_scraping` development
  environment that depended on it.
- Four `ais-celery-*` Deployments crash-looped for roughly 15 hours on a stale image, accumulating
  180–193 restarts each and writing container logs to node disk — churn that consumed the same
  resource that caused the incident.
- The Kubernetes API server was intermittently unavailable; sampling later measured 1 failure in 8
  probes over one minute. One pod (`ais-backend`) failed to start with
  `CreateContainerConfigError: failed to sync secret cache: timed out waiting for the condition`,
  which is the kubelet failing to *read* an existing Secret, not a manifest defect.
- The `ais-rabbitmq` liveness probe (`exec rabbitmq-diagnostics -q ping`) timed out at 10 s under
  load and restarted the pod once.

Not affected: no Longhorn volume lost data, no PVC was deleted, the registry's contents survived
intact, and the platform namespaces outside `workbench` showed no eviction or restart activity.

## Detection

The first signal was a `buildctl` client error:

```
error: failed to receive status: rpc error: code = Unavailable desc = closing transport due to:
connection error: desc = "error reading from server: EOF", received prior goaway:
code: NO_ERROR, debug data: "graceful_stop"
```

This is actionable only in the weak sense that it proves the *server* went away; it names neither
the node, the eviction, nor disk. `code: NO_ERROR` actively misleads, because it describes an
orderly gRPC shutdown rather than the kubelet killing the process.

Two earlier signals would each have detected this sooner and more clearly:

- A node-level `NodeHasDiskPressure` condition or an alert on kubelet's ephemeral-storage eviction
  threshold. None exists for the `workbench` namespace's consumers.
- BuildKit cache size. `buildctl du` reports it directly, and nothing watches it.

An additional detection gap made diagnosis slower than necessary: the build command was piped to
`tail`, so the pipeline's exit status was `tail`'s success rather than `buildctl`'s failure, and the
harness reported a failed build as exit code 0. `set -o pipefail` was adopted mid-incident.

## Timeline

All times JST. Times marked (~) are reconstructed from Kubernetes event ages rather than logged
directly, because the namespace's events had partially aged out before the report was written.

| Time | Event |
| --- | --- |
| 2026-08-08 ~19:15 | First large image build starts against BuildKit on `k3s-worker-3`. |
| 2026-08-08 ~19:34 | Second build completes and pushes successfully (~1.4 GB image). |
| 2026-08-08 ~20:45 | Third build starts, adding two more browser engines to the image. |
| 2026-08-08 ~21:20 | kubelet evicts BuildKit: `The node was low on resource: ephemeral-storage. Threshold quantity: 2021241476, available: 847424Ki`. Build dies with `graceful_stop`. |
| 2026-08-08 ~21:21 | BuildKit rescheduled to `k3s-worker-1` and becomes Ready with an empty cache. |
| 2026-08-08 21:00–12:00 | Unattended. Four `ais-celery-*` Deployments crash-loop on a stale image, reaching 180–193 restarts. |
| 2026-08-09 ~12:45 | Build retried on the new BuildKit pod. Image builds successfully; the push fails with repeated `500 Internal Server Error`. |
| 2026-08-09 12:57 | Registry logs identify the cause: `filesystem: mkdir /var/lib/registry/docker/registry/v2/repositories/ais-backend/_uploads/...: read-only file system`. |
| 2026-08-09 ~12:57 | Registry pod is replaced; the new pod briefly reports `Multi-Attach error for volume "pvc-090fe099-..."` while the old pod still holds the RWO volume. |
| 2026-08-09 12:58 | New registry pod starts on `k3s-worker-3` with the volume mounted `rw`. Writability and catalog verified. |
| 2026-08-09 ~13:00 | Crash-looping workers scaled to 0 to stop log churn. |
| 2026-08-09 ~13:05 | Build retried; push succeeds (`sha256:aec81004…`). |
| 2026-08-09 ~13:10 | Workers scaled back to 1 and start cleanly; API server observed intermittently unavailable during this window. |

## Technical root cause

BuildKit's cache directory is an `emptyDir` volume (chosen deliberately in the workbench design on
the grounds that build cache is rebuildable). An `emptyDir` is backed by the node's filesystem and
is charged against **ephemeral storage**, the same budget as container images, writable layers, and
logs. The BuildKit Deployment declares no `resources.requests.ephemeral-storage` and no
`sizeLimit` on the volume, so nothing bounded its growth and nothing informed the scheduler of its
footprint.

Three builds of a ~2.2 GB image, each with a large uncached browser-installation layer, grew that
cache until `k3s-worker-3` fell below the kubelet's eviction threshold (~2.0 GB) with 847 MiB
available. The kubelet then evicted the pod consuming the resource. Because BuildKit serves its
client over gRPC, the client saw only a transport-level `graceful_stop`.

The second, more damaging effect was on storage. Longhorn replicas are stored on the node's disk.
With `k3s-worker-3` at its capacity limit, I/O against the replica backing the registry's
`pvc-090fe099-...` volume failed. ext4 responds to a write error by remounting the filesystem
read-only to prevent corruption. The registry process itself stayed healthy and kept serving reads,
so it continued to answer `HEAD` requests with `404 blob unknown` and `POST` uploads with `500`,
producing an error surface that looked like a registry application fault rather than a storage
fault. The registry volume was only 22% full, so capacity on the volume was never the issue — the
distinction between `ENOSPC` and a read-only remount is what pointed at I/O error rather than a
full disk.

The intermittent API-server unavailability and the `failed to sync secret cache` pod failure are
consistent with control-plane pressure during the same window, matching the pattern already
documented in [INC-2026-003](./INC-2026-003-longhorn-rebuild-starved-etcd.md). This was not
independently confirmed during this incident and is recorded as a hypothesis, not a finding.

## Contributing factors

- **BuildKit's cache is unbounded.** No `sizeLimit` on the `emptyDir`, no
  `resources.requests/limits.ephemeral-storage`, and no periodic `buildctl prune`.
- **The image is unusually large.** ~2.2 GB, carrying three browser engines (Playwright's Firefox
  and Chromium, Patchright's Chromium, Camoufox's Firefox) for a development workload.
- **`k3s-worker-3` is the busiest node.** It hosts `devbox`, the `workspace` PVC, BuildKit,
  `neovara-homepage`, and `ratelimiter-docs`, so it had the least headroom to absorb the cache.
- **No ephemeral-storage observability.** Node disk pressure is not alerted on, and the
  `workbench` namespace has neither a `ResourceQuota` nor a `LimitRange`, so nothing bounds or
  accounts for ephemeral storage there.
- **Crash-looping workloads were left running unattended**, adding log writes to a disk-pressured
  node for 15 hours.
- **A shell pipeline masked the build's exit status** (`buildctl ... | tail`), briefly making a
  failed build look successful.

## Resolution and recovery

Recovery required only restarting the registry pod, which forces a fresh volume attach and
remount:

```
kubectl delete pod -n workbench -l app.kubernetes.io/name=registry
```

Recovery was verified against the failure mode directly, not inferred from the pod becoming Ready:

```
# mount is rw, not ro
grep " /var/lib/registry " /proc/mounts
# -> /dev/longhorn/pvc-090fe099-... /var/lib/registry ext4 rw,relatime 0 0

# the filesystem actually accepts a write
touch /var/lib/registry/.wt && rm -f /var/lib/registry/.wt

# contents intact
curl -s http://registry.workbench.svc.cluster.local/v2/_catalog
# -> all seven pre-existing repositories present
```

A subsequent `buildctl` build then pushed successfully, and the dependent workloads started on the
new image. BuildKit was left on `k3s-worker-1`, where it rescheduled itself, with an empty cache.

## What went well

- Reading the registry's own logs, rather than retrying the push, identified the read-only
  filesystem immediately and distinguished it from a capacity problem.
- The distinction between `ENOSPC` and a read-only remount correctly redirected the investigation
  from "the registry volume is full" to "the volume had an I/O error"; the volume was 22% used.
- No data was lost. Registry contents, Longhorn volumes, and the MySQL/Qdrant/RabbitMQ PVCs in the
  same namespace all survived.
- Removing an RWO PVC mount from the application tier shortly before the incident (for unrelated
  scheduling reasons) allowed those pods to evacuate `k3s-worker-3` automatically. Had they still
  mounted the `workspace` volume, they would have been unable to reschedule.
- Kubernetes self-healed the workload placement: BuildKit and the registry both rescheduled without
  operator action.

## What did not go well

- The primary error message (`graceful_stop`, `code: NO_ERROR`) named neither disk nor eviction and
  actively suggested an orderly shutdown.
- One root cause produced two symptoms that appeared unrelated for some time.
- BuildKit's low CPU usage and silent logs were initially misread as a stalled build; BuildKit only
  logs warnings and errors, so a healthy build is invisible there. Process state inside the pod was
  the signal that resolved it and should have been checked first.
- A 15-hour unattended window let crash-looping pods accumulate ~190 restarts each.
- `buildctl ... | tail` reported a failed build as exit code 0.

## Where we got lucky

- The eviction hit BuildKit, whose cache is explicitly disposable, rather than `devbox` (whose
  `$HOME` and all uncommitted work sit on a single-replica PVC on the same node).
- The registry volume was only 22% full, so the read-only remount was recoverable by a remount
  rather than requiring capacity reclamation or an fsck.
- `k3s-worker-1` had enough free ephemeral storage to accept BuildKit and a fresh cache, so builds
  could resume at all.
- The application tier had been unpinned from `k3s-worker-3` hours earlier for unrelated reasons.
  This was fortunate timing, not a control.

## Corrective and preventive actions

| Priority | Action | Owner | Status | Completion evidence |
| --- | --- | --- | --- | --- |
| P1 | Bound BuildKit's cache: set `sizeLimit` on its `emptyDir` and `resources.requests.ephemeral-storage`, so the scheduler accounts for it and the kubelet evicts BuildKit before the node | operator | Open | `k8s/workbench/manifests/buildkit.yaml` shows both fields; a large build no longer drives the node below threshold |
| P1 | Add an alert on node ephemeral-storage headroom and `NodeHasDiskPressure` | operator | Open | Prometheus rule fires in a controlled disk-fill test |
| P2 | Add periodic `buildctl prune --keep-duration` (CronJob or documented ritual) | operator | Open | Cache size bounded across a week of builds |
| P2 | Document the read-only-remount signature and the registry-restart recovery in the Workbench Runbook, including the `ENOSPC` vs `remount-ro` distinction | operator | Open | Runbook section exists and is linked from this report |
| P2 | Document `set -o pipefail` for build invocations in the Workbench Runbook's build section | operator | Open | Runbook build snippet includes it |
| P3 | Evaluate whether the `ai_scraping` dev image needs all three browser engines; drop stock Playwright browsers if Patchright and Camoufox suffice | project owner | Open | Image size measured before and after |
| P3 | Consider giving BuildKit a small dedicated PVC instead of `emptyDir`, moving its cache off node ephemeral storage entirely | operator | Deferred | ADR or runbook note recording the decision either way |

## Lessons and review questions

**`emptyDir` is charged to node ephemeral storage.** "The cache is rebuildable, so `emptyDir` is
fine" is a correct statement about *durability* and says nothing about *capacity*. An unbounded
`emptyDir` on a shared node is a disk-exhaustion vector, and the pod that fills it is the one the
kubelet evicts — which reads as a random failure rather than a resource decision.

**A read-only filesystem is not a full filesystem.** `ENOSPC` means out of space; a read-only
remount means ext4 detected an I/O error and protected itself. Confusing the two sends the
investigation to the wrong layer. Check `/proc/mounts` for `ro` before checking `df`.

**One root cause can present as several unrelated failures.** A disk-pressure event surfaced as a
killed gRPC stream, a 500-returning registry, an intermittently unavailable API server, and a pod
stuck in `CreateContainerConfigError`. The shared-dependency test in
[Kubernetes Concepts](../concepts/Kubernetes%20Concepts.md) applies in reverse here: when several
things fail at once, look for the one resource they share.

**Silence is not evidence of a hang.** BuildKit logs only warnings and errors, so a working build
produces no log lines; low CPU during an I/O-bound `dpkg` phase looks identical to being stuck.
`ps` inside the pod answered in one command what log-reading could not.

Review questions:

- What is the kubelet's ephemeral-storage eviction threshold on these nodes, and how is it
  configured in the k3s agent?
- Which pods in `workbench` declare `resources.requests.ephemeral-storage`? (Currently: none.)
- Would a `LimitRange` in `workbench` with an ephemeral-storage default have prevented this, and
  what would it have broken?
- How does Longhorn surface a replica I/O error, and is there a metric that would have shown it
  before the ext4 remount?
- The application tier escaped only because it had been unpinned from the node hours earlier. What
  is the general rule for when a workload may safely mount the RWO `workspace` volume?

## Evidence

- Eviction event: `Warning Evicted pod/buildkit-7874f49464-kgf7t` — `The node was low on resource:
  ephemeral-storage. Threshold quantity: 2021241476, available: 847424Ki`
- Client-side build failure: `received prior goaway: code: NO_ERROR, debug data: "graceful_stop"`
- Registry error (repeated, `http.response.status=500`):
  `err.detail="filesystem: mkdir /var/lib/registry/docker/registry/v2/repositories/ais-backend/_uploads/<uuid>: read-only file system"`
- Push failure: `unexpected status from POST request to
  http://registry.workbench.svc.cluster.local/v2/ais-backend/blobs/uploads/: 500 Internal Server Error`
- RWO contention during registry replacement: `Multi-Attach error for volume
  "pvc-090fe099-138b-44fa-96fd-e0eb30902d64" Volume is already used by pod(s) registry-f9bdbcb8b-99wrc`
- Pod-start failure during API instability: `CreateContainerConfigError` /
  `failed to sync secret cache: timed out waiting for the condition`
- Recovery state: `/dev/longhorn/pvc-090fe099-... /var/lib/registry ext4 rw,relatime 0 0`,
  `19.5G total / 5.5G used / 14.0G available (28%)`
- Successful push after recovery: `registry.workbench.svc.cluster.local/ais-backend:dev@sha256:aec81004b573c437e6872c3934d052fa15328026aefc2f5e5d04c452dbb82414`
- Objects: `buildkit-7874f49464-kgf7t` (evicted), `buildkit-7874f49464-pfgz9` (replacement,
  `k3s-worker-1`), `registry-f9bdbcb8b-99wrc` (read-only), `registry-f9bdbcb8b-5jkcg`
  (replacement, `k3s-worker-3`), PVC `pvc-090fe099-138b-44fa-96fd-e0eb30902d64`
- Related: [INC-2026-003](./INC-2026-003-longhorn-rebuild-starved-etcd.md) (control-plane pressure
  from storage I/O on shared physical disks)
