# INC-2026-005: Prometheus TSDB exhausted its persistent volume

## Incident metadata

| Field | Value |
| --- | --- |
| Date | 2026-07-25 JST |
| Severity | SEV-3 |
| Status | Mitigated; WAL recovery in progress |
| Systems | Prometheus, kube-prometheus-stack, Longhorn |
| Start | 2026-07-25 10:00 JST |
| End | In progress |
| Duration | In progress |
| Detection | Prometheus remained unready after monitoring was restored |
| Data impact | Healthy compacted blocks retained; Prometheus discarded inconsistent mmap head chunks created around the out-of-space crash |

## Executive summary

Prometheus failed to restart after planned storage maintenance because its original 10 GiB
Longhorn volume was full. WAL replay attempted to preallocate a chunk and panicked with
`no space left on device`. The PVC was expanded online to 20 GiB and the pod restarted.
Prometheus retained its healthy compacted blocks, discarded inconsistent memory-mapped head chunks,
and replayed the WAL using its built-in recovery path.

## Impact

- Prometheus metrics queries and alert evaluation were unavailable during recovery.
- Grafana remained available, but Prometheus-backed panels had no live datasource.
- Application workloads, their data, and Longhorn storage health were unaffected.
- Some recent, not-yet-compacted metric samples may be absent because corrupted mmap head chunks
  were discarded during recovery.

## Detection

The StatefulSet pod remained `1/2 Ready` and restarted its Prometheus container. Container logs
showed a panic from TSDB chunk preallocation with `no space left on device`. The PVC capacity and
request were both 10 GiB.

## Timeline

Times are JST on 2026-07-25.

| Time | Event |
| --- | --- |
| 10:00 | Monitoring workloads were restored after planned storage maintenance. |
| 10:05 | Prometheus continued crash-looping while the config reloader remained healthy. |
| 10:06 | Logs identified TSDB preallocation failure caused by a full filesystem. |
| 10:07 | The Git-managed request and live PVC were increased from 10 GiB to 20 GiB. |
| 10:07 | Longhorn completed block and filesystem expansion; the pod was recreated. |
| 10:08 | Prometheus retained healthy blocks, discarded inconsistent mmap head chunks, and began WAL replay. |

## Technical root cause

The Prometheus retention period was 15 days, but the persistent volume was sized at only 10 GiB.
Ingested time-series data and WAL files consumed all available filesystem space. On startup,
Prometheus needed to preallocate another chunk while replaying the WAL; the filesystem returned
`ENOSPC`, causing a panic and restart. Repeated interrupted starts also left memory-mapped head
chunks out of sequence. Once space was available, Prometheus's recovery path removed those
inconsistent transient chunk files and replayed the WAL.

## Contributing factors

- Storage sizing was not validated against actual 15-day ingestion volume.
- No effective PVC free-space alert was available before the monitoring stack was stopped.
- Monitoring itself was unavailable during the maintenance window.
- Full-volume startup requires temporary write headroom beyond steady-state retained blocks.

## Resolution and recovery

1. Confirmed the failure in Prometheus logs rather than treating the unready sidecar as the cause.
2. Increased the declared PVC request from 10 GiB to 20 GiB.
3. Expanded the existing Longhorn PVC online, preserving compacted metrics blocks.
4. Recreated the pod so kubelet completed filesystem expansion.
5. Monitored mmap cleanup, WAL replay, readiness, and filesystem headroom.

## What went well

- The Longhorn StorageClass supported online expansion.
- The failure was isolated to observability data.
- Prometheus identified healthy blocks separately and used its built-in head recovery.

## What did not go well

- The volume reached 100% without a preventive capacity action.
- The original sizing comment assumed 10 GiB was sufficient without measured ingestion data.
- Monitoring restoration surfaced two independent control-plane/tooling failures at once.

## Where we got lucky

- The volume was expandable and worker-1 had sufficient storage headroom.
- Compacted blocks were healthy.
- No application depends on Prometheus for its primary request path.

## Corrective and preventive actions

| Priority | Action | Owner | Status | Completion evidence |
| --- | --- | --- | --- | --- |
| P0 | Expand the Prometheus PVC from 10 GiB to 20 GiB | Repository owner | Done | PVC reports 20 GiB and Prometheus filesystem has free space |
| P0 | Complete WAL recovery and verify readiness | Repository owner | In progress | Pod `2/2 Ready` and `/-/ready` succeeds |
| P1 | Alert on persistent-volume free space before exhaustion | Repository owner | Open ([#54](https://github.com/Harsh-Upadhayay/homelab-k8s/issues/54)) | Tested alert fires with actionable volume and namespace labels |
| P2 | Revisit 15-day retention against measured ingestion and available capacity | Repository owner | Open ([#54](https://github.com/Harsh-Upadhayay/homelab-k8s/issues/54)) | Capacity calculation recorded in the monitoring runbook |

## Lessons and review questions

- Retention is a time objective, not a storage bound; size must follow observed ingestion and WAL
  overhead.
- Stateful services need restart/recovery headroom in addition to their steady-state footprint.
- Should Prometheus use size-based retention as a second guardrail alongside time retention?

## Evidence

- Prometheus panic: `preallocate: no space left on device`.
- PVC before mitigation: requested/capacity 10 GiB.
- PVC after mitigation: requested/capacity 20 GiB with resize conditions cleared.
- Recovery logs identified healthy blocks and then removed inconsistent mmap head chunks before
  replaying WAL segments.
