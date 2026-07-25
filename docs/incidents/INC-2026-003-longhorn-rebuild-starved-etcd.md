# INC-2026-003: Longhorn rebuild I/O starved single-node etcd

## Incident metadata

| Field | Value |
| --- | --- |
| Date | 2026-07-24 to 2026-07-25 JST |
| Severity | SEV-3 |
| Status | Resolved; recurrence mitigated; preventive actions open |
| Systems | k3s control plane, embedded etcd, Longhorn, `k3s-worker-1`, `pve-dell` |
| Start | 2026-07-24 23:52 JST (first observed API failure) |
| End | 2026-07-25 10:19 JST (Prometheus stopped after recurrence; API stable) |
| Duration | Two intermittent failure windows followed by explicitly accepted maintenance outages |
| Detection | The Immich rebuild monitor received API discovery failures; direct `/readyz` then reported etcd failure |
| Data impact | No loss observed; the original Immich replica remained RW on `k3s-worker-3`, and the incomplete worker-1 replica was rebuilt again |

## Executive summary

During the Immich library's approximately 292 GiB rebuild from `k3s-worker-3` to
`k3s-worker-1`, the target wrote roughly 36 MiB/s to worker 1's virtual Longhorn disk. That disk
and the `k3s-server-1` OS/embedded-etcd disk share the same physical USB SSD on `pve-dell`.
Storage latency rose high enough that etcd linearizable reads and lease updates took 5–13 seconds.
The controller manager lost leader election, k3s exited, and systemd restarted the single control
plane seven times. Fully stopping VM 101 immediately restored etcd and API readiness. A 15 MB/s
Proxmox write limit initially allowed the rebuild to resume, but it did not provide a durable
latency bound: k3s restarted an eighth time about 48 minutes later. Even 10 and 5 MB/s ceilings
produced multi-second `fdatasync` latency. Because Kubernetes downtime was explicitly acceptable,
the final mitigation cleanly stopped k3s/etcd while leaving both worker agents and the already
running Longhorn rebuild alive. The engine was monitored directly from its network namespace and
the worker-1 data disk was capped at 30 MB/s for the offline copy. During application recovery,
Prometheus WAL replay and compaction reproduced the same physical-disk contention and restarted
k3s once more. Prometheus was then stopped declaratively until the control-plane VM is moved to
ASRock under issue #49.

## Impact

- The only Kubernetes API server was intermittently unavailable and returned 503 responses.
- The Tailscale Kubernetes API proxy also became unavailable because its upstream API was not
  ready; it was a symptom, not the cause.
- Controllers could not reconcile reliably while etcd was stalled.
- Application workloads were already intentionally stopped for storage maintenance, so there was
  no additional user-facing application outage.
- The Immich volume remained available through its original RW ASRock replica. The partial
  worker-1 replica was disposable and restarted from a new replica process.

## Detection

The first signal was a monitor failure reading Longhorn CRDs through the Tailscale API endpoint.
Switching to the control-plane VM proved the failure was server-side: `/readyz` specifically
reported etcd failure, followed by connection resets while k3s restarted.

The platform had no alert that directly connected etcd request latency with hypervisor datastore
contention. A rebuild bandwidth check and etcd latency dashboard should have been reviewed before
starting a hundreds-of-gigabytes copy onto a datastore shared with the control plane.

## Timeline

Times are JST on 2026-07-24/25; journal evidence is stored in UTC and is one hour/date conversion
behind these entries.

| Time | Event |
| --- | --- |
| ~23:44 | Immich replica rebuild to worker 1 was active at roughly 36 MiB/s. |
| 23:52 | Longhorn monitor received Kubernetes API discovery failures. |
| 23:53 | Direct `/readyz` reported etcd failure; journal showed ReadIndex and range requests taking 5–13 seconds. |
| 23:54 | Controller-manager lease renewal exceeded its five-second deadline; leader election was lost and k3s exited. |
| 23:54–23:57 | systemd repeatedly restarted k3s; the restart counter eventually reached seven. |
| 23:56 | Stopping `k3s-agent` in worker 1 did not end the load because the VM and leftover container processes remained active. |
| 23:58 | Graceful VM 101 shutdown timed out. With workloads quiesced and the authoritative replica safe, VM 101 was hard-stopped. |
| 23:59 | etcd `/health` returned true and Kubernetes `/readyz` returned success repeatedly; restart count stopped increasing. |
| 00:01 | A 15 MB/s write ceiling was configured on VM 101 `scsi1`; VM 101 restarted with its final CPU/RAM allocation. |
| 00:02 | Worker 1 returned Ready and the Immich replica rebuild resumed under the write ceiling. |
| 00:49 | The 15 MB/s ceiling proved insufficient; etcd latency again approached five seconds and k3s restarted an eighth time. |
| 00:52–00:54 | The ceiling was reduced first to 10 and then 5 MB/s. Readiness recovered, but etcd still logged multi-second `fdatasync` and transaction latency. |
| 00:56 | k3s was cleanly stopped for an intentional API maintenance outage; worker agents and the in-flight Longhorn engine remained active. |
| 00:58 | Direct engine-namespace monitoring proved rebuild progress continued without the API. The offline-copy ceiling was raised to 30 MB/s. |
| 10:17 | After storage convergence, Prometheus WAL recovery and compaction drove etcd `fdatasync` latency as high as 5.8 seconds and made the API unready. |
| 10:17–10:18 | Cloud-controller-manager lost leader election; k3s exited and systemd restarted it once. |
| 10:19 | Prometheus was scaled to zero. Six direct readiness checks passed and the k3s restart count remained stable. |

## Technical root cause

`k3s-server-1` and `k3s-worker-1` are separate VMs but their disks are logical volumes in the same
`pve-dell` `local-lvm` thin pool on one physical USB SSD. The Longhorn replica rebuild generated a
sustained write workload against worker 1's 650 GiB `scsi1`. The physical device could not provide
the low tail latency etcd requires while servicing that bulk write stream.

etcd's raft thread consequently waited too long for linearizable ReadIndex agreement and backend
operations. Kubernetes lease reads and writes exceeded controller-manager deadlines. Losing the
controller-manager leader-election lease is fatal to the embedded k3s control-plane process, so
k3s exited and systemd restarted it. Each restart re-opened and defragmented the small etcd
database, but the continuing shared-device contention caused the cycle to repeat.

The causal relationship was validated operationally:

1. stopping only `k3s-agent` did not restore etcd while VM 101 remained active;
2. fully stopping VM 101 ended the target-disk I/O;
3. etcd health and API readiness recovered immediately and remained stable for repeated checks;
4. restarting VM 101 with a 15 MB/s data-disk write limit allowed the rebuild and etcd to coexist.

No kernel I/O error, filesystem-full condition, inode exhaustion, memory pressure, etcd corruption,
or missing raft leader was observed. The etcd initial corruption check passed after restart.

## Contributing factors

- The control plane and a bulk Longhorn target shared one physical datastore despite being
  different VMs.
- The rebuild started without a hypervisor I/O ceiling.
- A single embedded-etcd member makes any server-process restart a complete API outage.
- The initial monitor used the Tailscale proxy, adding one indirection before the direct
  control-plane failure was confirmed.
- `systemctl stop k3s-agent` did not terminate every process producing guest I/O, so the first
  mitigation did not remove the physical-device load.
- The graceful VM shutdown did not complete during the guest's degraded state.

## Resolution and recovery

1. Preserved the original worker-3 Immich replica and made no ASRock disk mutation.
2. Stopped worker 1's k3s agent, then fully stopped VM 101 when residual VM I/O continued.
3. Repeatedly verified embedded-etcd health, API readiness, and a stable systemd restart count.
4. Added a temporary `mbps_wr=15` QEMU limit to VM 101's Longhorn data disk and restarted the VM
   with its configured 12 vCPU and 18 GiB RAM.
5. Detected the later k3s restart, reduced the ceiling to 10 and then 5 MB/s, and confirmed that
   bandwidth limiting alone still did not guarantee acceptable etcd tail latency.
6. Cleanly stopped k3s/etcd for an intentional maintenance outage while leaving both worker agents
   and the in-flight Longhorn engine alive.
7. Verified the rebuild directly with the engine CLI inside its network namespace, then used a
   30 MB/s offline-copy ceiling without exposing etcd to the bulk-write workload.
8. After Prometheus later reproduced the same contention, stopped it and made zero replicas the
   temporary Git-managed state until the control-plane move in #49.

## What went well

- The rebuild monitor exposed the control-plane failure quickly.
- Direct host access distinguished the upstream API failure from a Tailscale proxy problem.
- The original Immich replica remained authoritative and untouched throughout.
- The maintenance window had already quiesced applications, reducing concurrent writes and user
  impact.
- A simple stop/no-stop causal test confirmed the shared-disk contention rather than relying only
  on correlation.
- etcd's startup corruption check passed after every recovery.

## What did not go well

- The migration plan modeled Longhorn capacity but not physical-datastore latency contention.
- There was no preconfigured bulk-rebuild throttle.
- Seven k3s restarts occurred before the VM-level source of I/O was fully stopped.
- The system lacks an HA control plane, so a single process failure removed all API availability.
- The first mitigation assumed stopping the agent would end all workload I/O.

## Where we got lucky

- The authoritative Immich replica was on the other physical host.
- The etcd database remained uncorrupted despite repeated unclean process exits.
- Applications were already stopped, so API unavailability did not interrupt active users.
- The target replica was disposable and could restart without rollback complexity.

## Corrective and preventive actions

| Priority | Action | Owner | Status | Completion evidence |
| --- | --- | --- | --- | --- |
| P0 | Do not run a bulk rebuild concurrently with etcd on this physical SSD; use an intentional control-plane maintenance outage or move etcd first | Repository owner | Done for this migration | k3s stopped cleanly while the engine continued under direct monitoring |
| P0 | Keep the authoritative source replica until the throttled target is RW and the volume Healthy | Repository owner | Done | Storage convergence gate passed and all retained volumes are Healthy |
| P0 | Keep Prometheus stopped while etcd shares the Dell SSD | Repository owner | Done until #49 | `prometheusSpec.replicas: 0` and stable direct API readiness |
| P1 | Add etcd/API health and k3s restart-count checks to every long-running rebuild monitor | Repository owner | Done | Migration monitor checks `/readyz` and `NRestarts` alongside rebuild progress |
| P1 | Record physical datastore co-tenancy in migration capacity reviews, not only VM and Longhorn topology | Repository owner | Done | Immich convergence runbook and this incident review |
| P1 | Rate-limit both reads and writes when rebuilding back out of the Dell datastore | Repository owner | Open | Temporary `mbps_rd`/`mbps_wr` limits verified during reverse rebuild |
| P2 | Move the existing control-plane VM to motherboard-connected ASRock SSD storage after capacity is resolved | Repository owner | Open | GitHub issue #49 |
| P2 | Alert on etcd backend/raft latency and unexpected k3s service restarts | Repository owner | Open ([#51](https://github.com/Harsh-Upadhayay/homelab-k8s/issues/51)) | Prometheus rules and tested notification |

## Lessons and review questions

- VM boundaries do not create storage failure or performance domains when their disks share one
  physical device.
- Distributed-storage rebuild traffic must be treated as production bulk I/O and rate-limited
  around latency-sensitive databases.
- An API proxy cannot be evaluated independently when its upstream control plane is unhealthy.
- Stopping an orchestrator service does not prove all child processes or VM I/O have stopped.
- What write/read limit keeps etcd tail latency healthy on this exact Dell USB SSD?
- Should storage maintenance automation fail closed whenever the target Longhorn disk and etcd disk
  resolve to the same Proxmox physical device?

## Evidence

- etcd logged `waiting for ReadIndex response took too long`.
- etcd range and transaction requests took approximately 5–13 seconds.
- Kubernetes controller-manager logged `Failed to renew lease` and `leaderelection lost`.
- systemd reported k3s exit status 1 and a final restart count of seven.
- etcd database size was approximately 19 MB; filesystem use was 18%, inode use 3%, and memory
  remained available.
- Kernel logs contained no OOM or block-device/filesystem error for the incident window.
- etcd initial corruption checking passed after restart.
- VM 101 stop immediately restored `{"health":"true"}` and successful `/readyz`.
- A later eighth k3s restart proved the initial 15 MB/s ceiling was not a reliable tail-latency
  control; 5 MB/s still produced a 2.4-second `fdatasync`.
- With k3s intentionally stopped, direct `longhorn rebuild-status` inside the engine network
  namespace continued advancing with no error under a 30 MB/s worker-data-disk ceiling.
- Prometheus recovery later produced a 5.8-second etcd `fdatasync`, leader-election loss, and one
  k3s restart; stopping Prometheus stabilized direct `/readyz` checks.
- Related migration runbook: [Immich migration and workstation rebuild](../migrations/immich.md).
