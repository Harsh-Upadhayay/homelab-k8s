# INC-2026-002: Stale Longhorn snapshot tickets blocked worker evacuation

## Incident metadata

| Field | Value |
| --- | --- |
| Date | 2026-07-24 |
| Severity | SEV-4 |
| Status | Resolved; preventive follow-up open |
| Systems | Longhorn v1.12.0, Immich PostgreSQL, Nextcloud, `k3s-worker-2` |
| Start | 2026-07-24 maintenance window (exact time not retained) |
| End | 2026-07-24 maintenance window (exact time not retained) |
| Duration | Limited to the planned worker-evacuation window |
| Detection | The worker-deletion gate still found attached Longhorn volumes after application workloads were stopped |
| Data impact | No loss observed; replica counts and health were checked before and after remediation, and the worker was deleted only after zero replicas, engines, attachments, and Orphans referenced it |

## Executive summary

During the planned evacuation of `k3s-worker-2`, Longhorn rebuilt all ten replicas away from the
worker, but an Immich PostgreSQL volume and a Nextcloud volume remained attached. Their Kubernetes
workloads were already stopped. The remaining attachment requests came from Longhorn's internal
snapshot controller: the corresponding snapshots were marked for removal and reported purge
completion, but their Snapshot custom resources were stuck terminating on the `longhorn.io`
finalizer. After confirming that the purge had completed, only the two stale snapshot-controller
attachment tickets were removed and only the finalizers on those already-purged Snapshot resources
were cleared. Both volumes then detached normally. The node-deletion safety gate passed and the
worker was retired without data loss.

## Impact

- Planned `k3s-worker-2` retirement paused because the storage safety gate correctly failed.
- Two volumes retained engines on the worker despite their application pods being stopped.
- No application outage beyond the already-authorized maintenance window was introduced.
- No PVC, PV, Longhorn Volume, Replica, replica directory, or healthy snapshot data was deleted.
- The ten replicas scheduled on `k3s-worker-2` were rebuilt successfully before the worker was
  removed.

## Detection

The issue was detected by the explicit pre-deletion inventory, not by an alert. Replica evacuation
had reached zero replicas on `k3s-worker-2`, but Longhorn still reported engines/attachment tickets
for two volumes. Inspecting each VolumeAttachment showed that the remaining requester was the
snapshot controller rather than CSI or a workload pod.

An earlier, clearer signal would have been a maintenance check that groups every Longhorn
attachment ticket by requester and flags tickets whose owning snapshot is both `markRemoved=true`
and fully purged.

## Timeline

Times are ordered reconstruction from the 2026-07-24 JST maintenance window; exact command
timestamps were not retained.

| Time | Event |
| --- | --- |
| Maintenance start | Argo CD reconciliation and application workloads were paused for the storage move. |
| + replica rebuilds | Scheduling was disabled on the `k3s-worker-2` Longhorn disk and all ten replicas rebuilt onto eligible disks. |
| + safety gate | The replica count reached zero, but two volumes still had engines and Longhorn attachment tickets on `k3s-worker-2`; node deletion stopped. |
| + diagnosis | The residual tickets were traced to Longhorn's snapshot controller. Their Snapshot resources were `markRemoved=true`, purge status was 100%, and deletion was stuck on `longhorn.io` finalizers. |
| + remediation | The two exact stale snapshot-controller tickets were removed. Finalizers were cleared only on the matching, already-purged Snapshot resources. |
| + verification | Both volumes detached. The gate reported zero worker replicas, engines, Longhorn attachment tickets, Kubernetes VolumeAttachments, and Orphans. |
| Maintenance continuation | `k3s-worker-2` was drained and deleted, then VM 102 and only its owned Proxmox disks were removed. |

## Technical root cause

Longhorn represents more than one reason to keep a volume attached as tickets on its
VolumeAttachment custom resource. Stopping an application removes the workload/CSI reason, but it
does not override an independent snapshot-controller ticket.

In this incident, two internal Snapshot resources had completed their data purge but did not finish
deletion because their `longhorn.io` finalizers remained. Their snapshot-controller attachment
tickets therefore outlived the snapshot work and kept the corresponding volume engines attached to
`k3s-worker-2`. Replica evacuation alone could not satisfy the node-deletion gate because replicas
and live engine attachments are independent safety conditions.

## Contributing factors

- The maintenance procedure initially treated stopped workloads and zero replicas as the likely
  detachment boundary; Longhorn has independent attachment requesters.
- Snapshot deletion state is split across Snapshot spec, purge status, finalizers, and
  VolumeAttachment tickets, so no single object made the stale state obvious.
- Longhorn manager logs also contain recurring attempts to materialize historical snapshot names
  containing uppercase `T`/`Z`, which Kubernetes rejects as invalid RFC 1123 resource names. This
  was observed controller noise during the incident, but the available evidence does not establish
  it as the cause of the two stale tickets.
- No alert or maintenance report currently identifies completed-but-finalized snapshots with
  residual attachment tickets.

## Resolution and recovery

The response preserved the storage gate and narrowed every mutation to an already-verified stale
object:

1. confirmed all ten worker replicas had healthy replacements elsewhere;
2. identified the two residual engines and mapped their attachment tickets to the snapshot
   controller;
3. confirmed each associated snapshot was marked removed and its purge status was 100%;
4. removed only those two stale tickets;
5. cleared `longhorn.io` finalizers only on the matching already-purged Snapshot resources;
6. confirmed both volumes detached; and
7. reran the full gate for replicas, engines, Longhorn and Kubernetes attachments, and Orphans
   before deleting the node or VM.

## What went well

- The delete gate treated any remaining attachment as a hard stop.
- Applications and GitOps reconciliation were intentionally quiesced, reducing concurrent state
  changes during diagnosis.
- Replica evacuation and health verification completed before any ticket or finalizer mutation.
- Remediation targeted two exact resources rather than deleting controllers, volumes, or broad
  object sets.
- VM 102 was removed only after Kubernetes and Longhorn independently showed no references.

## What did not go well

- The snapshot-controller ticket state was not anticipated in the evacuation runbook.
- Exact incident start/end timestamps were not captured while stabilizing the maintenance.
- Repeated invalid historical snapshot-name errors add noise that can obscure actionable controller
  failures.
- The manual finalizer operation depended on operator judgement and lacks an automated precondition
  check.

## Where we got lucky

- Both affected snapshots had already completed purge; a finalizer removal before purge completion
  would have been unsafe.
- The applications were already stopped, so extending the maintenance window did not interrupt
  active writes.
- Other eligible Longhorn disks had enough capacity to rebuild every worker replica before the
  stale attachments were investigated.

## Corrective and preventive actions

| Priority | Action | Owner | Status | Completion evidence |
| --- | --- | --- | --- | --- |
| P0 | Require zero replicas, engines, Longhorn tickets, Kubernetes VolumeAttachments, and Orphans before deleting a storage node or VM | Repository owner | Done | Gate used for the `k3s-worker-2` retirement and recorded in the Immich runbook |
| P1 | Add a reusable evacuation diagnostic that reports attachment tickets with requester, target node, and related snapshot purge/finalizer state | Repository owner | Open | Script or runbook command produces one auditable report |
| P1 | Document finalizer removal as an exceptional action allowed only after exact-object matching and confirmed purge completion | Repository owner | Done | This incident review and the Immich convergence runbook |
| P2 | Investigate the historical uppercase snapshot-name/RFC 1123 errors separately and determine whether snapshot metadata needs supported cleanup or an upstream fix | Repository owner | Open | Root cause documented and manager logs no longer repeat the errors, or upstream issue linked |
| P2 | Capture UTC/JST timestamps automatically in future maintenance evidence bundles | Repository owner | Open | Next incident or migration bundle contains machine-generated start/end timestamps |

## Lessons and review questions

- A Longhorn volume can remain attached without an application pod because attachment tickets are
  multi-owner intent, not merely a reflection of CSI state.
- Replica count, engine placement, attachment intent, Kubernetes attachment, and orphan state are
  separate deletion gates.
- A Kubernetes finalizer is a promise that a controller will finish cleanup. Removing one is not a
  generic unstick operation; it is safe only after independently proving the promised cleanup is
  complete.
- Should the preflight become a read-only script that refuses to emit remediation commands until
  every purge and replica-health invariant passes?
- Can Prometheus expose stuck Snapshot finalizers or long-lived internal attachment tickets without
  creating alert noise during ordinary snapshots?

## Evidence

- Affected Immich PostgreSQL volume:
  `pvc-78a421f5-cc42-4e1c-b9c0-c9cd94b7c7c9`.
- The affected Nextcloud volume was identified live through its attachment ticket; its exact ID was
  not retained in the evidence bundle and is intentionally not reconstructed from memory.
- Longhorn Snapshot resources showed `markRemoved=true`, purge progress 100%, and terminating
  `longhorn.io` finalizers before remediation.
- Final pre-delete result: zero replicas, engines, Longhorn attachment tickets, Kubernetes
  VolumeAttachments, and Orphans on `k3s-worker-2`.
- Longhorn manager logs also showed historical snapshot CR creation failures for names such as
  `abs-2-17-2-20260713T170155Z`, rejected because uppercase characters violate Kubernetes RFC 1123
  metadata naming rules.
- Related migration runbook: [Immich migration and workstation rebuild](../migrations/immich.md).

