# INC-2026-008: The GPU driver and operator images exhausted a worker's ephemeral storage and evicted every pod on it

## Incident metadata

| Field | Value |
| --- | --- |
| Date | 2026-09-06 JST |
| Severity | SEV-3 |
| Status | Resolved; preventive actions open |
| Systems | `k3s-worker-3`, Immich, photos-relay, `workbench` (devbox and project apps), GPU Operator, `kube-prometheus-stack` operator, Longhorn |
| Start | 2026-09-06 ~17:50 JST (kubelet set `DiskPressure=True`; reconstructed from the first eviction) |
| End | 2026-09-06 ~18:30 JST (last rescheduled workload mounted its volume and became Ready) |
| Duration | Roughly 40 minutes of degraded service, overlapping a planned maintenance window whose *reboots* were expected but whose *evictions* were not |
| Detection | `nvidia-cuda-validator` showed `Evicted` while checking GPU Operator rollout; `DiskPressure=True` on the node confirmed it |
| Data impact | No loss. Longhorn reported every volume `attached`/`healthy` with replicas intact throughout; `immich-postgres` was never evicted; the 50 GiB `workbench` workspace volume remounted cleanly with both replicas healthy. |

## Executive summary

Planned work to pass a GPU through to `k3s-worker-3` installed the NVIDIA driver in the guest and
then pulled the GPU Operator's image set (device plugin, DCGM exporter, NFD, driver and CUDA
validators) onto that node. `k3s-worker-3` has a 40 GiB OS disk of which the containerd image store
already occupied 28 GiB, so the new images drove the node below the kubelet's eviction threshold.
The kubelet set `DiskPressure=True` and evicted 18 pods — including `immich-server`, `photos-relay`,
the devbox, and every `workbench` project app — and then evicted the GPU Operator's own CUDA
validator, stalling the rollout it was meant to complete. Pruning unused images, the APT cache and
journals took the disk from 96% to 29%; the evicted workloads rescheduled onto `k3s-worker-1`, whose
I/O was then saturated by a recursive `fsGroup` ownership change across the 50 GiB workspace volume,
which in turn caused liveness-probe timeouts that repeatedly SIGTERM-killed the Prometheus operator.
No data was lost and the GPU work itself completed successfully.

## Impact

`immich-server` and `photos-relay` were evicted and restarted, briefly interrupting the photo
library and the Google Photos relay. The `workbench` devbox and its project applications
(`neovara-homepage`, `ratelimiter-docs`) were evicted from `k3s-worker-3` and spent roughly 25
minutes unavailable while rescheduling onto `k3s-worker-1` and waiting for the shared RWO workspace
volume. `kube-prometheus-stack-operator` entered a restart loop (10 restarts) on `k3s-worker-1`,
degrading monitoring reconciliation but not Prometheus itself. The GPU Operator rollout stalled at
`Init:2/4` until the node recovered.

Not affected: the k3s control plane and etcd (on `pve-asrock` but in a separate VM with its own
disk), `k3s-worker-1`'s own resident workloads, every Longhorn volume's data, and the public
ingress path.

## Detection

The first signal was `nvidia-cuda-validator ... Evicted` in a routine `kubectl get pods -n
gpu-operator` while waiting for the operator to finish rolling out. That was actionable but
indirect — it surfaced as "the thing I am installing is broken" rather than "the node is out of
disk." Checking the node's conditions immediately showed `DiskPressure=True`, and `df` on the guest
showed 97% used.

A node-level alert on `nodefs.available` or on the `DiskPressure` condition would have fired before
the first eviction, while the only consequence was still a slow image pull. There is no such alert
today, which is the same gap INC-2026-006 recorded for this exact node.

## Timeline

All times JST (UTC+9). Times marked ~ are reconstructed from object ages and log timestamps.

| Time | Event |
| --- | --- |
| 17:13 | `pve-asrock` completes its planned reboot; patched NIC returns at 1000 Mb/s, GPU bound to `vfio-pci` |
| ~17:35 | VM 103 cold-stopped for the Terraform `hostpci` attachment (planned) |
| ~17:45 | `nvidia_gpu_node` role installs the driver and reboots `k3s-worker-3` (planned); `nvidia-smi` reports the card |
| 17:49 | GPU Operator Helm install begins; operator, NFD, device plugin, DCGM and validator images start pulling onto `k3s-worker-3` |
| ~17:50 | Node crosses the kubelet eviction threshold; `DiskPressure=True`; evictions begin |
| ~17:51 | 18 pods evicted for `ephemeral-storage`, including `immich-server`, `photos-relay`, devbox and `workbench` apps |
| ~17:52 | `nvidia-cuda-validator` evicted; operator validator stalls at `Init:2/4`. Incident detected |
| ~17:52 | `k3s crictl rmi --prune`, `apt-get clean`, `journalctl --vacuum-size=200M`: 37 GiB → 25 GiB used (96% → 65%), continuing to 11 GiB (29%) as the prune completed |
| ~17:56 | `DiskPressure` clears — roughly four minutes after the disk was actually freed |
| ~17:57 | Evicted validator pods deleted; `nvidia-operator-validator` reaches Running and `nvidia-cuda-validator` Completes |
| 17:59 | Helm release reconciled to `deployed`; GPU rollout healthy |
| 18:06 | End-to-end smoke test passes: a pod requesting `nvidia.com/gpu: 1` runs `nvidia-smi` against the card |
| ~18:20 | 18 evicted pod tombstones deleted; they had been holding the RWO workspace volume and blocking rescheduled pods with `Multi-Attach error` |
| ~18:22 | Rescheduled `workbench` pods land on `k3s-worker-1`; kubelet begins a recursive `fsGroup` ownership change across the 50 GiB workspace volume |
| ~18:22 | `kube-prometheus-stack-operator` liveness probes begin timing out on the saturated node; kubelet SIGTERM-kills it repeatedly |
| ~18:30 | Ownership change completes; workspace pods mount and become Ready |

## Technical root cause

`k3s-worker-3`'s 40 GiB OS disk carried a 28 GiB containerd image store before this change — roughly
90% full at rest. Installing `nvidia-driver-580-server` and pulling the GPU Operator's image set
added several gigabytes of ephemeral storage, crossing the kubelet's `nodefs.available` eviction
threshold (observed in the eviction messages as a 2.02 GB threshold against 1.58 GiB available).

The kubelet then evicted pods ranked by ephemeral-storage usage relative to request. **No workload
on this platform declares an `ephemeral-storage` request**, so every pod on the node had a request of
zero and ranking fell back to raw consumption — which is why the eviction set was broad and
indiscriminate rather than targeting the actual consumer, and why unrelated user-facing services
went down alongside the GPU components.

The recovery then produced a second-order effect that lasted far longer than the original fault.
Evicting workloads off `k3s-worker-3` moved them to `k3s-worker-1` — which ended up holding 68 pods
against worker-3's 12, because Kubernetes does not move pods back once rescheduled — and the RWO
workspace volume followed them. Kubelet applies `fsGroup` ownership recursively at mount time under
the default `fsGroupChangePolicy: Always`, and that volume holds **444,457 files** (a development
tree of `node_modules` and `.git` across every project). The resulting chown, competing with 16
Longhorn replicas on a node that normally hosts 8, progressed at roughly 1,000 files per minute and
saturated the node's I/O long enough for liveness probes on co-resident pods to time out.

Two components were killed repeatedly by those probes while being entirely healthy: the Prometheus
operator and `argocd-server` both show a clean startup and cache sync, then `received SIGTERM` /
`API Server received signal: terminated` with exit code 0. Neither was crashing; both were being
killed by a probe that could not get a response from a saturated node. Moving those two plus Alloy
onto the now-idle `k3s-worker-3` stopped the restart loops immediately and roughly doubled the chown
rate, which is the clearest evidence that the "crash" was contention, not fault.

## Contributing factors

- The containerd image store lives on the 40 GiB OS disk rather than on the node's 1300 GiB data
  disk, so image growth competes directly with the eviction threshold.
- `k3s-worker-3`'s OS disk sits on `pve-asrock`'s 74.68 GiB thin pool, which has little headroom to
  grow into, so "make the disk bigger" is not a free answer.
- No pod declares `ephemeral-storage` requests, which makes kubelet eviction ranking effectively
  arbitrary and prevents protecting stateful or user-facing workloads.
- No alerting on node disk usage or on the `DiskPressure` condition — the same gap INC-2026-006
  identified on this node in August and left open.
- The change design accounted for the GPU, the NIC, the console and the IOMMU group, but not for
  image-store growth on the target node.
- Evicted pod objects continued to hold the RWO volume attachment, so cleaning up tombstones was a
  prerequisite for recovery rather than cosmetic tidying.

## Resolution and recovery

Disk was reclaimed on the node with unused-image pruning plus cache and journal cleanup, which took
it from 96% to 29% used:

```bash
sudo k3s crictl rmi --prune
sudo apt-get clean
sudo journalctl --vacuum-size=200M
```

`DiskPressure` cleared about four minutes later. Evicted GPU Operator pods were deleted so the
DaemonSet could recreate them onto the now-healthy node; the validator reached Running and the CUDA
validator Completed. The 18 evicted tombstones were then removed cluster-wide, which released the
RWO workspace volume so the rescheduled `workbench` pods could attach:

```bash
kubectl delete pods -A --field-selector=status.phase=Failed
```

The pile-up on `k3s-worker-1` was then relieved by cordoning it, deleting the three heaviest
stateless pods with no PVCs (`alloy`, `kube-prometheus-stack-operator`, `argocd-server`) so the
scheduler placed them on the idle `k3s-worker-3`, and uncordoning:

```bash
kubectl cordon k3s-worker-1
kubectl -n monitoring delete pod -l app.kubernetes.io/name=alloy
kubectl -n monitoring delete pod <prometheus-operator-pod>
kubectl -n argocd delete pod <argocd-server-pod>
kubectl uncordon k3s-worker-1
```

That ended both probe-kill loops and cut Longhorn's CPU on the node from roughly 226% to 27%.

Recovery was verified rather than assumed: Longhorn reported all 22 volumes `healthy` with no
rebuild in progress, and the workspace volume `attached` on `k3s-worker-1` with both replicas; `immich`, `photos-relay` and the `workbench`
applications returned to Running; and the GPU chain was re-proven end to end with a pod requesting
`nvidia.com/gpu: 1` running `nvidia-smi`.

## What went well

- No data was lost, and no Longhorn replica was rebuilt or discarded.
- `immich-postgres` was never evicted, so no database was interrupted mid-write.
- The detection-to-mitigation gap was short: the eviction was noticed on the next status check and
  the disk was freed within a couple of minutes.
- Before killing processes that appeared to be orphans holding the workspace mount, their pod UIDs
  were checked against live pods — they turned out to belong to the *new* pods, and killing them
  would have taken down a workload that had just recovered.
- The GPU work itself was unaffected in substance: every artefact of it re-verified clean afterwards
  (`terraform plan` no changes, both Ansible roles `changed=0`).

## What did not go well

- The incident was self-inflicted by a change whose plan did not model disk consumption on the
  target node, despite an August incident (INC-2026-006) on the same node for the same class of
  cause.
- The blast radius was far wider than the change: user-facing services with nothing to do with the
  GPU were evicted because no workload declares ephemeral-storage requests.
- `DiskPressure` persisted for about four minutes after the disk was actually freed, and pods
  created during that window were evicted on arrival, which looked like the fix had failed.
- Evicted tombstones silently blocked recovery via `Multi-Attach` on the RWO volume; nothing
  surfaced that as the reason the devbox would not start.
- The recovery itself caused a second-order I/O saturation on `k3s-worker-1` that produced a
  misleading `CrashLoopBackOff` on a component that was actually healthy.

## Where we got lucky

- The eviction happened during an attended maintenance window, so it was noticed in minutes rather
  than overnight — INC-2026-006's equivalent ran unattended for roughly 15.5 hours.
- `immich-postgres` happened to survive the eviction ranking; nothing protected it.
- `k3s-worker-1` had enough free capacity to absorb the entire evicted set. If it had not, the
  workloads would have had nowhere to go.
- The control plane runs in a separate VM with its own disk on the same physical host, so a worker
  disk filling could not reach etcd.

## Corrective and preventive actions

| Priority | Action | Owner | Status | Completion evidence |
| --- | --- | --- | --- | --- |
| P1 | Alert on node `nodefs.available` and on the `DiskPressure` condition for every node; this is the second incident of this class on this node | Operator | Open | Alert rule in `k8s/monitoring/`, fired in a test |
| P1 | Give `k3s-worker-3` more room for its image store — either relocate containerd's root to the 1300 GiB data disk or grow the OS disk, noting the 74.68 GiB thin pool constrains the latter | Operator | Open | `df` on the node showing sustained headroom after a GPU-image pull |
| P2 | Declare `ephemeral-storage` requests on user-facing and stateful workloads so kubelet eviction ranking protects them instead of being arbitrary | Operator | Open | Requests present in manifests; eviction ranking exercised |
| P2 | Add image-store growth to the pre-flight checklist for any change that installs drivers or pulls a new operator's image set onto a node | Operator | Open | Checklist step in the relevant plan/runbook |
| P1 | Set `fsGroupChangePolicy: OnRootMismatch` on the devbox and every other pod mounting the `workbench` workspace volume — measured at **444,457 files**, the default `Always` policy re-chowns all of them on every mount and dominated recovery time | Operator | Open | Devbox restart time measured before and after |
| P2 | Investigate why kubelet image GC (default `imageGCHighThresholdPercent` 85) did not reclaim before the eviction threshold was crossed | Operator | Open | Finding recorded, settings adjusted if warranted |
| P2 | Consider a rebalancing story for post-eviction pile-up — Kubernetes does not move pods back, so one node held 68 pods against another's 12 until pods were manually relocated | Operator | Open | Documented procedure, or a descheduler decision recorded as an ADR |

## Lessons and review questions

The reusable lesson is that **a node's image store is ephemeral storage, and ephemeral storage is an
evictable resource shared by every pod on the node** — so "install a driver and an operator" is a
capacity change, not just a configuration change. The second lesson is that eviction ranking is only
as meaningful as the requests workloads declare; with no `ephemeral-storage` requests anywhere, the
kubelet cannot distinguish a database from a build cache.

Review questions:

- How does the kubelet rank pods for eviction under `DiskPressure`, and what would change if
  critical pods declared `ephemeral-storage` requests?
- Why does `DiskPressure` persist after the underlying disk is freed, and what governs that
  hysteresis?
- Where does containerd store images on a k3s node, and what are the tradeoffs of relocating that
  root onto a separate disk?
- Why does an RWO volume's attachment survive pod deletion long enough to block a replacement, and
  what is the correct order of operations to recover it?
- `fsGroup` ownership changes are applied recursively at mount time — what options exist
  (`fsGroupChangePolicy: OnRootMismatch`) and when are they safe?

## Evidence

- Eviction messages: `The node was low on resource: ephemeral-storage. Threshold quantity:
  2021241476, available: 1576384Ki`.
- Node condition `DiskPressure=True` on `k3s-worker-3`; 18 pods in `Evicted`/`Failed` phase, all on
  that node.
- Guest disk before and after reclamation: `/dev/sda1 38G 37G 1.6G 96% /` → `38G 11G 27G 29% /`;
  `du` attributed 28 GiB to `/var/lib/rancher/k3s/agent/containerd`.
- `kube-prometheus-stack-operator` logs showing `received SIGTERM, exiting gracefully...` with
  `lastState.terminated: {reason: Completed, exitCode: 0}` and restart count 10, alongside
  `Liveness probe failed: ... context deadline exceeded`.
- `Multi-Attach error for volume "pvc-a17050e8-..."` on the devbox pod, naming the evicted
  `ratelimiter-docs` and `neovara-homepage` pods as holders.
- `VolumePermissionChangeInProgress ... processed 1151 files` on the workspace volume mount.
- Related: INC-2026-006 (same node, same resource, different consumer), ADR-0067 (records the disk
  constraint as a consequence of the GPU work).
