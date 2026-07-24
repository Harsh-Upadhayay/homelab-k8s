# INC-2026-001: Longhorn instance-manager retained a stale storage mount

## Incident metadata

| Field | Value |
| --- | --- |
| Date | 2026-07-24 |
| Severity | SEV-3 |
| Status | Resolved; preventive automation remains open |
| Systems | Immich, Longhorn v1.12.0, k3s worker `k3s-worker-3`, Argo CD |
| Start | 2026-07-24 18:50 JST (first failed attach recorded at 18:50:28) |
| End | 2026-07-24 18:59 JST (Immich listening at 18:59:38) |
| Duration | Approximately 9 minutes during planned migration recovery |
| Detection | User observed `immich-server` cycling through Creating, Terminating, and Unknown |
| Data impact | No loss observed; the preserved replica retained its disk UUID and data directory, became Healthy, and the user verified the migrated Immich library and media |

## Executive summary

During the final activation step of the Immich migration, the server could not mount its 350 GiB
Longhorn library volume. The physical Longhorn filesystem had been mounted at
`/var/lib/longhorn` after k3s and Longhorn's worker components were already running. Although the
engine binary was present on the newly mounted filesystem, the existing Longhorn instance-manager
pod retained a bind-mounted view of the directory that existed before the filesystem was mounted.
Longhorn therefore failed to start the volume engine with `ENOENT`, the volume entered a
Faulted/attach-retry cycle, and Kubernetes repeatedly failed to start the Immich pod. Recreating
the worker's instance-manager refreshed its mount namespace; the engine started, CSI attached the
volume, a fresh Immich pod mounted it, and Argo CD returned to Healthy.

## Impact

- Immich remained unavailable while its server pod could not start.
- The `immich-library` volume temporarily reported `faulted` while attach attempts failed.
- Argo CD remained `Synced` but `Progressing`, waiting for the server Deployment.
- Immich PostgreSQL stayed Running on its separate Longhorn volume.
- Immich machine learning and Valkey started successfully.
- Other Kubernetes workloads and Longhorn volumes remained available.
- No PVC, PV, Volume, Replica, Orphan, or filesystem data was deleted or recreated.

This occurred during a planned migration activation rather than an unplanned outage of a previously
healthy production endpoint, so it is classified SEV-3 rather than SEV-2.

## Detection

The first human-visible signal was rapid `immich-server` pod churn. Kubernetes events showed
`FailedAttachVolume`, while the decisive signal was the Longhorn engine failure:

```text
exec: "/engine-binaries/docker.io-longhornio-longhorn-engine-v1.12.0/longhorn":
stat /engine-binaries/docker.io-longhornio-longhorn-engine-v1.12.0/longhorn:
no such file or directory
```

The host copy of that file existed and was executable. Running `stat` from inside the
worker-3 instance-manager reproduced the `No such file or directory`, proving that this was a mount
visibility problem rather than a missing download, damaged replica, or incorrect engine image.

Detection was reactive. There was no preflight check that the persistent disk was mounted before
k3s started, nor a check that both the engine-image pod and instance-manager could see the same
engine binary before an application was allowed to attach the volume.

## Timeline

Times are JST (UTC+09:00). Times before the first Kubernetes event are reconstructed from the
operator session and are intentionally marked approximate.

| Time | Event |
| --- | --- |
| ~18:27 | `k3s-worker-3` joined the cluster and Longhorn components started before the preserved filesystem was mounted at its durable path. |
| ~18:39 | The preserved ext4 filesystem was mounted read-write at `/var/lib/longhorn`; its disk UUID, Longhorn disk UUID, and replica directory were verified. |
| ~18:44 | A read-only verification pod exposed a missing Longhorn engine binary on the host path. The worker-3 engine-image pod was recreated, which restored the host binary and allowed the verification pod to read the library. The already-running instance-manager was not recreated. |
| ~18:50 | GitOps enabled Immich server, machine learning, and Valkey. |
| 18:50:28 | CSI began attaching `pvc-ff54c47e-3e29-4ce3-9192-b6e644351b97` to `k3s-worker-3`. |
| 18:51–18:56 | Attach attempts alternated between `DeadlineExceeded` and `volume ... is not ready for workloads`; server pods were repeatedly replaced while the volume engine failed. |
| ~18:56 | The engine error identified the missing `/engine-binaries/.../longhorn` path. A `stat` inside the worker-3 instance-manager reproduced the failure. |
| ~18:56 | Only the worker-3 instance-manager pod was deleted. Longhorn recreated it without deleting the Volume or Replica. |
| ~18:56 | The recreated instance-manager successfully saw the executable at `/engine-binaries/.../longhorn` (47,295,960 bytes, executable). |
| 18:57:08 | The CSI attacher reported the VolumeAttachment successfully attached. Longhorn reported the volume `attached/healthy` and the replica Running on `k3s-worker-3`. |
| ~18:58 | The pod created during the failed-attach window was replaced once to force a clean kubelet mount/start attempt against the healthy attachment. |
| 18:59:38 | Immich v3.0.3 reported that the server was listening and its machine-learning dependency was healthy. |
| ~19:00 | All Immich pods were Ready; Argo CD reported `Synced/Healthy/Succeeded`. The user subsequently verified accounts, albums, photos, videos, search, and timeline data. |

## Technical root cause

The causal chain was:

1. `k3s-agent` started Longhorn's DaemonSet-managed components on `k3s-worker-3`.
2. The instance-manager pod received a hostPath/bind-mounted view of Longhorn's engine-binary
   directory while `/var/lib/longhorn` still referred to the worker's root filesystem.
3. The preserved physical filesystem was later mounted over `/var/lib/longhorn`.
4. The engine-image pod was recreated and populated the engine binary on the now-mounted
   filesystem. The existing instance-manager's `/engine-binaries` bind mount still referred to the
   pre-mount directory, so it could not see the new file.
5. When the Immich PVC was requested, the replica process could start from the correct
   `/host/var/lib/longhorn` view, but the volume engine process could not start because its
   executable was absent from the instance-manager's view.
6. Without a running engine, the volume was not ready for workloads. CSI attach failed, leaving the
   Immich server in `ContainerCreating` and producing the observed pod churn.

The root cause was therefore **incorrect mount-before-service ordering**, expressed at runtime as a
stale container mount namespace. It was not replica corruption, a failed disk reassociation, a CSI
credential problem, or missing Immich data.

## Contributing factors

- The preserved-disk path intentionally bypassed normal clean-disk formatting, so the standard
  Longhorn disk automation was skipped and the mount was completed after cluster join.
- The recovery runbook correctly required mounting before Longhorn use, but did not make
  mount-before-`k3s-agent` an explicit, enforced dependency.
- Recreating the engine-image pod restored the host file but did not refresh the separate
  instance-manager mount namespace.
- No preflight compared the host engine-binary path with the path visible inside the
  instance-manager.
- The library had one deliberately preserved replica. There was no alternate replica from which
  Longhorn could attach while worker-3 was being repaired.
- Rapid pod replacement was a secondary symptom and initially made the incident look like an
  Immich or Kubernetes lifecycle problem rather than a Longhorn process-start failure.

## Resolution and recovery

The response preserved the storage object chain throughout:

`PVC -> PV -> Longhorn Volume -> Replica CR -> preserved disk UUID -> replica directory`.

The decisive remediation was scoped to the disposable Longhorn process pod:

```sh
kubectl -n longhorn-system delete pod \
  instance-manager-945b7dd9dd7a46b36cb7f80805cc290b --wait=true
```

Longhorn recreated the pod. Before allowing recovery to continue, the engine binary was verified
from inside the new instance-manager:

```sh
kubectl -n longhorn-system exec INSTANCE_MANAGER_POD -- \
  stat /engine-binaries/docker.io-longhornio-longhorn-engine-v1.12.0/longhorn
```

The response then:

1. waited for the engine to become Running and the volume to become `attached/healthy`;
2. allowed CSI to retry naturally until the VolumeAttachment became `attached: true`;
3. replaced one pending Immich server pod created during the failed-attach window;
4. waited for the server readiness condition;
5. verified the replica remained on disk UUID
   `72fbdae2-c51d-41f7-a679-33c6d617ab62` with the original data directory;
6. verified all Immich pods and Argo CD health; and
7. completed user-level checks of the migrated library.

No destructive storage command was used. In particular, the Replica, Volume, PVC, PV, and any
Orphan objects were never deleted.

## What went well

- Stable filesystem, Longhorn disk, volume, and replica identities had been recorded before the
  migration.
- A read-only verification pod proved the replica contents were readable before Immich was
  enabled.
- Diagnosis compared the host filesystem view with the container view instead of assuming a
  missing file meant failed installation.
- Recovery was narrowed to controller-managed process pods; persistent storage objects were left
  intact.
- The CSI attacher was allowed to retry after the engine recovered instead of deleting a healthy
  volume or forcing a rebuild.
- Application-level verification confirmed more than pod health: the user checked albums, photos,
  videos, search, and timeline data.

## What did not go well

- Storage was mounted after the service that consumes it had started.
- Automation did not encode the ordering dependency or fail fast on the wrong mount source.
- The initial engine-image restart treated only one of two container filesystem views.
- Pod lifecycle symptoms were noisy, and the useful Longhorn engine error was not surfaced
  immediately.
- The runbook described the desired order but lacked a branch for recovering from violating it.

## Where we got lucky

- The preserved filesystem and replica were healthy.
- The Longhorn CRs and exact disk UUID survived, so automatic replica reassociation worked.
- PostgreSQL used an independent healthy volume and required no restore.
- The migration retained a current database backup and prior evidence bundle even though neither
  was needed.
- No automated orphan cleanup was enabled that could have converted a metadata mismatch into data
  deletion.

## Corrective and preventive actions

| Priority | Action | Owner | Status | Completion evidence |
| --- | --- | --- | --- | --- |
| P0 | Document mount-before-k3s as a hard recovery invariant and add the stale-instance-manager recovery branch | Repository owner | Done | This report and the linked Immich recovery runbook |
| P0 | Before future preserved-disk recovery, assert the expected filesystem UUID is mounted at the configured Longhorn path before starting or enabling `k3s-agent` | Repository owner | Open | Ansible check fails safely when the source UUID or mountpoint is wrong |
| P1 | Encode a systemd ordering dependency so the required mount is available before `k3s-agent` on hosts with preserved/dedicated Longhorn disks | Repository owner | Open | `systemd-analyze critical-chain k3s-agent` shows the mount prerequisite; reboot test passes |
| P1 | Add a recovery preflight that verifies `longhorn-disk.cfg`, replica identity, the host engine binary, and the same binary from inside the instance-manager | Repository owner | Open | Runbook/automation produces four successful checks before application enablement |
| P1 | Add monitoring for Longhorn volume Faulted state, repeated CSI attach failure, and Argo CD applications stuck Progressing | Repository owner | Open | Test alert fires during a controlled failure exercise |
| P2 | Run a controlled reboot/reschedule exercise after automation is complete to prove the physical disk mounts and Longhorn recovers without manual pod recreation | Repository owner | Open | Exercise record linked from this report |
| P2 | Use the incident-review template for future qualifying incidents and near misses | Repository owner | Done | `docs/incidents/README.md` and `TEMPLATE.md` |

## Lessons and review questions

The central lesson is that a path is not an identity. `/var/lib/longhorn` named one directory when
the pod started and a different mounted filesystem later. A container can keep a reference to the
first object even while the host shell sees the second. Restarting the component that writes a
file does not refresh the mount namespace of a different, already-running component.

Questions for the learning review:

1. How do Linux mounts, bind mounts, and container mount namespaces allow the host and a pod to
   resolve the same-looking path to different underlying filesystems?
2. Why did the replica process work while the engine process failed?
3. Why was deleting an instance-manager pod safe, while deleting the Longhorn Replica CR would
   have crossed a data-loss boundary?
4. Which Kubernetes object represented the desired attachment, and why was waiting for the CSI
   retry safer than immediately deleting it?
5. What should Ansible assert, and what should systemd order, so a reboot cannot recreate this
   failure?
6. Which signals would reduce time to diagnosis: pod phase, Kubernetes events, Longhorn Volume
   state, engine logs, or Argo CD health?

## Evidence

- PVC/PV/Volume: `pvc-ff54c47e-3e29-4ce3-9192-b6e644351b97`
- Replica: `pvc-ff54c47e-3e29-4ce3-9192-b6e644351b97-r-3ef55838`
- Preserved Longhorn disk UUID: `72fbdae2-c51d-41f7-a679-33c6d617ab62`
- Preserved filesystem UUID: `e613c520-2cd4-4b0f-b8dd-1be2ea055b49`
- Original replica data directory:
  `pvc-ff54c47e-3e29-4ce3-9192-b6e644351b97-b9dc1bdb`
- CSI VolumeAttachment:
  `csi-da82f028eae25b3215893159aff6410bc88bc1894d9ffc12e4570aa2e2db5a08`
- Final state: Immich pods Ready, Longhorn volume `attached/healthy`, replica Running,
  Argo CD `Synced/Healthy/Succeeded`
- Recovery runbook: [Immich migration and workstation rebuild](../migrations/immich.md)
