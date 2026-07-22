# Immich migration and workstation rebuild runbook

## Status

Immich's Compose state was migrated, converted from pgvecto.rs to VectorChord, upgraded from
v2.7.3 to v3.0.3, and accepted at `https://immich.in.neovara.uk`. Database counts, stored paths,
and files matched the source. The old Compose instance was stopped after a final manual comparison
on 2026-07-22 JST.

The application components are now intentionally disabled in Git while the workstation is rebuilt
as a Proxmox host. PostgreSQL remains online on its independent, two-replica Longhorn PVC. Do not
re-enable the Immich server until the library is recovered and attached.

## Completed pre-rebuild checkpoint

The controlled maintenance transition completed on 2026-07-22 JST:

- Commit `dc5a5ce` was pushed and Argo CD reconciled `Synced/Healthy`.
- At 11:40 JST the chart-managed server, machine-learning, and Valkey deployments were absent;
  only the independently managed PostgreSQL deployment remained.
- The library volume reported `detached`; its replica reported `stopped` and `started=false`.
- `lsof` found no process with the replica directory open. `fuser` reported only the expected
  kernel reference for the still-mounted outer filesystem.
- `k3s-agent` was stopped and disabled. The Kubernetes node becoming
  `NotReady,SchedulingDisabled` is expected and must not be deleted.
- `/mnt/longhorn-immich` unmounted successfully. A subsequent `findmnt` found no mount, and
  read-only `e2fsck -fn /dev/sdb2` completed with exit code 0.
- `/dev/sdb1` remains mounted and unchanged. Its cleanup is deferred.

The workstation is at the rebuild boundary, but it has not been powered off. Immediately before
starting the Proxmox installer, stop the remaining Docker workloads, shut down cleanly, and
disconnect the HDD as described below.

## Preserved disk and Longhorn identity

The entire physical HDD `/dev/sdb` must survive unchanged:

| Item | Identity | Purpose |
| --- | --- | --- |
| `/dev/sdb1` | ext4 UUID `d807ca3e-804b-4f99-865f-12ec3397932f`, 900 GiB | Old `/storage` tree, rollback data, recovery artifacts |
| `/dev/sdb2` | ext4 UUID `e613c520-2cd4-4b0f-b8dd-1be2ea055b49`, 497.3 GiB | Preserved Longhorn replica disk |
| Longhorn disk | UUID `72fbdae2-c51d-41f7-a679-33c6d617ab62`, name `immich-hdd` | Previously mounted at `/mnt/longhorn-immich` |
| Library volume/PV | `pvc-ff54c47e-3e29-4ce3-9192-b6e644351b97`, 350 GiB | Authoritative migrated library volume |
| Replica CR | `pvc-ff54c47e-3e29-4ce3-9192-b6e644351b97-r-3ef55838` | Only library replica |
| Replica directory | `pvc-ff54c47e-3e29-4ce3-9192-b6e644351b97-b9dc1bdb` | Raw Longhorn v1 replica data |
| Old node | `k3s-worker-migration` | Temporary bare-metal worker; do not require this name after rebuild |

The PostgreSQL volume is `pvc-78a421f5-cc42-4e1c-b9c0-c9cd94b7c7c9`, 10 GiB, retained, with two
replicas on the permanent workers. The library and source rollback are two partitions on the same
physical HDD, so they protect against migration mistakes but not physical disk failure.

## Recovery artifacts

The post-authentication database dump is:

`/mnt/storage1/immich/migration/immich-post-maintenance-20260722-113158-JST.dump`

- Size: 96,922,770 bytes (93 MiB)
- SHA-256: `ebb7754c7c4f5dffde1cb319634758d06ac3795f8190ffc05fdf4f1e42389802`
- Custom-format restore catalog validated with the PostgreSQL `pg_restore --list` binary from the
  running compatibility image.

The secret-free recovery evidence bundle is:

`/mnt/storage1/immich/migration/workstation-rebuild-20260722-113158-JST/`

It contains the partition table, filesystem UUIDs, SMART report, `fstab`, Longhorn disk and replica
metadata, Kubernetes PV/PVC and Longhorn CR exports, Argo CD state, database counts, API version,
and a depth-limited source-data inventory. It contains no Kubernetes Secret values.

## Deferred `/dev/sdb1` cleanup audit

No cleanup was performed. The partition contains about 365 GiB and must remain intact through the
rebuild. Largest top-level trees at the checkpoint were:

| Tree | Approximate size | Treatment |
| --- | ---: | --- |
| `immich` | 287 GiB | Required rollback and recovery artifacts; preserve |
| `audiobookshelf` | 51 GiB | Migrated rollback; review later |
| `jobhunt` | 23 GiB | MySQL state; preserve for its future migration |
| `nextcloud` | 2.4 GiB | Migrated rollback; review later |
| `jobhunt-dev` | 1.6 GiB | Review later |
| `monitoring` | 922 MiB | Superseded state; review later |
| `kiroku` | 515 MiB | Migrated rollback; review later |
| `jenkins` | 372 MiB | Review before dropping |
| `openclaw` | 325 MiB | Deferred state; preserve |
| `mediaserver` | 17 MiB | Deferred configuration; preserve |
| `ollama` | 48 KiB | Review later |

Cleanup is deliberately deferred. Do not format, resize, delete, or repurpose `/dev/sdb1` until
Immich recovery has passed and each remaining tree has received a separate keep/delete decision.

## Pre-Proxmox shutdown procedure

1. Stop the old Compose Immich instance and confirm the old public endpoint no longer serves it.
2. Confirm the new database dump, restore catalog, SHA-256, and evidence bundle above.
3. Commit and push the GitOps maintenance state: `server.enabled`, `machine-learning.enabled`, and
   `valkey.enabled` are all `false`; PostgreSQL stays enabled.
4. Wait for Argo CD to prune the application deployments and for library volume
   `pvc-ff54c47e-3e29-4ce3-9192-b6e644351b97` to report `detached`.
5. Confirm no process has the replica directory open.
6. Stop and disable `k3s-agent` on the workstation, then unmount `/mnt/longhorn-immich`.
7. Capture final post-detach state in the evidence bundle and shut down cleanly.
8. For the Proxmox installer, select only the 119 GiB `/dev/sda`. The safest procedure is to
   physically disconnect the 1.4 TB HDD during installation and reconnect it afterward.

Do not request Longhorn node/disk eviction: no other eligible disk has capacity for this 350 GiB
replica. Do not delete the PVC, PV, Longhorn Volume, Replica, Kubernetes Node, or Longhorn Node CR.

## Post-Proxmox recovery without the old node name or mount path

1. Add the new Proxmox host and k3s worker to Terraform and Ansible. The current code only models
   `pve-dell`, `k3s-worker-1`, and `k3s-worker-2`; an unmanaged VM would violate the repo's
   provisioning standard.
2. Pass the preserved `/dev/sdb2` partition to the new worker and mount it read-only at any chosen
   recovery path. Do not format it and do not add it as a clean Longhorn disk.
3. Verify the preserved `longhorn-disk.cfg`, `volume.meta`, replica directory, filesystem UUID, and
   recorded checksums against the evidence bundle.
4. Keep Longhorn's `orphan-resource-auto-deletion` setting empty. Never delete an Orphan resource
   for this directory: deleting that CR deletes the associated replica data.
5. Verify `lsof` and `fuser` report no writers to the replica directory.
6. Use the Longhorn v1.12.0 `launch-simple-longhorn` recovery procedure with:
   - volume name `pvc-ff54c47e-3e29-4ce3-9192-b6e644351b97`
   - volume size `375809638400` bytes
   - the preserved replica directory listed above
7. Mount `/dev/longhorn/pvc-ff54c47e-3e29-4ce3-9192-b6e644351b97` read-only and copy its filesystem
   into a newly managed Longhorn volume with sufficient capacity. Longhorn detects an untracked
   replica as orphaned data; it does not automatically bind it to the existing PVC under an
   unrelated node identity/path.
8. Bind or reference the recovered claim from the Immich values, preserving both `/data` and
   `/usr/src/app/upload` mounts until stored paths are separately normalized.
9. Restore the three application component `enabled` flags to `true`, commit, push, and wait for
   Argo CD `Synced/Healthy`.
10. Repeat the database counts, zero-missing-path check, file count, sample image/video playback,
    login, pod-restart, and node-reschedule checks.
11. Only after acceptance may the old `/dev/sdb1` Immich rollback be considered for deletion and
    its free space be introduced as a separate clean Longhorn disk.

Upstream recovery references:

- <https://longhorn.io/docs/1.12.0/advanced-resources/data-recovery/export-from-replica/>
- <https://longhorn.io/kb/restoring-data-from-an-orphaned-replica-directory/>
- <https://longhorn.io/docs/1.12.0/advanced-resources/data-cleanup/orphaned-data-cleanup/>
