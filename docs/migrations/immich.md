# Immich migration and workstation rebuild runbook

## Status

Immich's Compose state was migrated, converted from pgvecto.rs to VectorChord, upgraded from
v2.7.3 to v3.0.3, and accepted at `https://immich.in.neovara.uk`. Database counts, stored paths,
and files matched the source. The old Compose instance was stopped after a final manual comparison
on 2026-07-22 JST.

Post-Proxmox recovery completed on 2026-07-24 JST. `k3s-worker-3` runs on `pve-asrock`, mounts the
preserved `/dev/sdb2` filesystem at `/var/lib/longhorn`, and owns the reassociated library replica
under its original Longhorn disk UUID. Immich, machine learning, Valkey, and PostgreSQL are Running;
Argo CD is `Synced/Healthy`, and the library volume is `attached/healthy`.

The post-rebuild database baseline matched: 19,004 total assets, 19,003 active assets, one user,
870 albums, 203 persons, and the same newest pre-rebuild asset timestamp. A live snapshot then
checked all 56,799 database-referenced paths after client activity resumed, with zero missing.
The user verified accounts, albums, photos, videos, search, and timeline. The obsolete Kubernetes
and Longhorn `k3s-worker-migration` objects and their six stale pod records were removed only after
zero replicas, engines, attachments, and Orphans referenced that node.

The storage-convergence follow-on in issue #48 then retired `k3s-worker-2`, consolidated its Dell
resources into `k3s-worker-1`, and converted the ASRock HDD from its temporary recovery partitions
to the separate Proxmox-managed `longhorn-hdd` datastore. Current volumes use one data copy
(ADR-0051); the large Immich, Audiobookshelf, and Nextcloud-data volumes live on worker 3's managed
HDD disk. Moving the existing control-plane VM remains issue #49.

## Post-recovery worker and HDD convergence

Issue #48 used a deliberately gated, two-stage copy so the only healthy Immich replica was never
destroyed:

1. The old `/dev/sdb1` rollback tree was copied off-host and explicitly released for reuse. It was
   formatted as ext4 label `longhorn-asrock`, mounted at `/var/lib/longhorn-asrock`, and added as a
   second Longhorn disk on `k3s-worker-3`.
2. All ten replicas on `k3s-worker-2` rebuilt elsewhere. Loki's old `local-path` log history was
   intentionally discarded because it was operationally immaterial and the corrected chart values
   now create Loki persistence on the `longhorn` StorageClass.
3. The node-deletion gate required zero Longhorn replicas, engines, attachment tickets, Kubernetes
   VolumeAttachments, and Orphans. Two already-purged snapshots retained stale snapshot-controller
   tickets and blocked the gate; the narrow remediation and prevention are recorded in
   [INC-2026-002](../incidents/INC-2026-002-longhorn-stale-snapshot-attachment-tickets.md).
4. Only after the gate passed was `k3s-worker-2` drained and deleted. Proxmox VM 102 and only its
   owned `scsi0`, `scsi1`, and cloud-init volumes were removed. Its Terraform resource/state and
   Ansible inventory entry were removed in the same change.
5. `k3s-worker-1` inherited the retired VM's allocation: 12 vCPU, 18 GiB RAM, and a 650 GiB
   Longhorn data disk. The guest ext4 filesystem was expanded online.
6. The Immich library rebuilt onto `k3s-worker-1` before `/dev/sdb2` became eligible for removal.
   The first unthrottled attempt saturated the Dell physical datastore shared with etcd, and a
   15 MB/s ceiling later proved insufficient. Because API downtime was acceptable, k3s/etcd was
   stopped cleanly while the already-running Longhorn engine continued under direct
   engine-namespace monitoring and a 30 MB/s offline-copy ceiling. The complete evidence is in
   [INC-2026-003](../incidents/INC-2026-003-longhorn-rebuild-starved-etcd.md).
7. Every current Longhorn Volume was reduced to one data copy. Disk eviction moved the three
   previously ASRock-only volumes to worker 1 and removed redundant ASRock replicas.
8. Only after zero replicas, engines, attachment tickets, Kubernetes VolumeAttachments, and
   Orphans referenced either ASRock Longhorn disk was VM 103 stopped and the raw HDD detached.
9. The entire HDD was then released: both temporary partitions were erased, the independent
   `longhorn_hdd` VG and `data` thin pool were created, and Proxmox registered the node-local
   `longhorn-hdd` datastore. The SSD-backed `pve/local-lvm` VG was not extended or changed.
10. Terraform allocated VM 103 a 1,300 GiB managed `scsi1` from `longhorn-hdd`. Ansible formatted
    it as ext4 label `k3s-data`, mounted it at `/var/lib/longhorn`, and retained the
    mount-before-k3s systemd ordering shared with worker 1.
11. Longhorn registered the new clean disk. The one-copy Immich library (350 GiB), Audiobookshelf
    library (70 GiB), and Nextcloud data (20 GiB) volumes moved from Dell back to ASRock; smaller
    volumes remained on Dell.
12. Node readiness, volume health and placement, Terraform convergence, Ansible idempotence,
    GitOps reconciliation, and application health were rechecked before closing the migration.

The physical HDD is identified only by
`/dev/disk/by-id/wwn-0x50024e920627da0f` during host initialization. Proxmox then owns its LVM
layout and exposes only the managed VM volume to worker 3. The final guest storage lifecycle is the
same as worker 1: `/dev/sdb`, ext4 label `k3s-data`, mounted at `/var/lib/longhorn`. Historical
filesystem and Longhorn disk UUIDs from the recovery partitions are not final-state identities.

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
| Old node | `k3s-worker-migration` | Removed from Kubernetes and Longhorn after acceptance |

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

## Resume from the Mac after the workstation is rebuilt

This runbook and the declarative Kubernetes, Terraform, and Ansible source are tracked in Git. A
fresh clone on the Mac must use `main` from `origin` and start with this file. The actual library,
database dump, evidence bundle, credentials, kubeconfig, and Terraform state are deliberately not
stored in Git.

The pre-rebuild Mac verification copied the local-only handoff into `~/homelab-handoff/`, including:

- SSH keys for the Proxmox hosts and k3s nodes
- `terraform.tfstate`, `terraform.tfstate.backup`, and `.terraform.lock.hcl`
- the direct cluster-admin kubeconfig, in addition to the Tailscale Operator kubeconfig
- the workstation-rebuild evidence directory
- `immich-post-maintenance-20260722-113158-JST.dump`

Before running Terraform from the clone, restore the copied state and lock file into `terraform/`
and keep them untracked. Never initialize an empty state and apply the existing `pve-dell` resources
as if they were new. `PROXMOX_VE_API_TOKEN` remains a runtime secret; retrieve the existing
`terraform@pve!tf` token from the password manager. After the workstation joins the Proxmox cluster,
that identity and its ACLs are cluster-wide; do not create a second provider/token merely because the
VM is placed on another physical node. Obtain the existing k3s join token live from `k3s-server-1`
when adding the worker; do not generate a new cluster token.

Verify the copied database dump on macOS with:

```bash
shasum -a 256 ~/homelab-handoff/recovery/immich-post-maintenance-20260722-113158-JST.dump
```

Expected SHA-256:
`ebb7754c7c4f5dffde1cb319634758d06ac3795f8190ffc05fdf4f1e42389802`.

The first safe post-install handoff is the new Proxmox hostname/IP plus read-only output from
`pveversion`, `ip -br address`, `pvesm status`, `lsblk`, and `blkid`. Do not initialize the HDD in
the Proxmox UI. Resume at **Proxmox cluster expansion before any guest is created** below.

## Proxmox cluster expansion before any guest is created

ADR-0049 fixes the hypervisor topology: `pve-dell`, the rebuilt workstation, and the planned third
physical node belong to one Proxmox cluster. The workstation is **not** a separate Proxmox
installation with a second Terraform provider. The initial two-node period deliberately accepts
loss of Proxmox configuration writes when either member is absent; provisioning is infrequent and a
third member is expected in one to two months.

This must happen while the workstation is still empty. Proxmox overwrites a joining node's
`/etc/pve` configuration and requires it to hold no guests. Do not build its Ubuntu template, create
the k3s worker, attach the HDD to a VM, or run Terraform first.

1. Keep the 1.4 TB HDD disconnected for the Proxmox installation. Reconnect it only after the new
   host boots, then verify both preserved partition UUIDs with `lsblk -f` and `blkid`. Do not add the
   HDD as Proxmox storage, an LVM PV, or a ZFS member.
2. Give the workstation a unique permanent Proxmox hostname and static LAN IP. Verify forward and
   reverse name resolution, time synchronization, identical supported Proxmox versions, and stable
   low-latency LAN connectivity between it and `pve-dell`. The installed host is
   `pve-asrock.home.arpa` at `192.168.1.51`; `pve-dell.home.arpa` is `192.168.1.50`.
   Because ASRock's Intel I219-V requires the ABI-specific patched
   `7.0.2-6-pve` e1000e module, keep the `proxmox-default-kernel`,
   `proxmox-kernel-7.0`, and `proxmox-kernel-7.0.2-6-pve-signed` packages held.
   Do not run a kernel upgrade until the replacement ABI has a rebuilt and
   physically tested module; follow the recovery installer's **Kernel upgrades**
   section.
3. Capture `pvecm status`, `pvecm nodes`, `/etc/pve/storage.cfg`, and the VM inventory from
   `pve-dell`. If it is still standalone, create the cluster there. The live
   cluster is named `neovara`; it was created on `pve-dell` and the **empty
   workstation** joined over the stable LAN on 2026-07-23.
4. Verify both nodes appear in `pvecm nodes`, both report `Online`, `pvecm status` reports `Quorate:
   Yes` with both online, and the existing three VMs on `pve-dell` are unchanged and running.
5. Recheck `pvesm status` on both nodes. Proxmox storage configuration is cluster-wide but the
   underlying `local-lvm` media remains node-local. Apply node restrictions if storage IDs are not
   genuinely present on both nodes; clustering does not make local disks shared.
6. Run the Proxmox Ansible role against the new host, including its host-specific repository,
   Tailscale, hardware and Terraform ACL settings. Do not apply `pve-dell`-specific laptop/NIC
   workarounds to different hardware without detection/host variables.
7. Extend the single Terraform root and Ansible inventory for the new physical node and k3s worker.
   Keep one Proxmox provider and token; use the VM resource's `node_name` for placement. Restore the
   copied Terraform state first, then require a reviewed plan with the existing three VM resources
   unchanged and only the intended new infrastructure added.

Two-node Proxmox behavior is accepted but must remain understood:

- With both nodes online, either node's API can manage the cluster and Terraform can place VMs on
  either physical node.
- If one node is absent, already-running VMs on the survivor continue, but `pmxcfs` becomes
  read-only and Terraform/configuration changes fail.
- If the survivor cold-starts while the other member is absent, `onboot` guests wait for quorum.
- This is not Proxmox HA. The planned third node supplies the normal third vote. If `pve-dell` is
  later removed and only two members remain, the same limitation returns unless another node or a
  QDevice supplies a third vote.

References:

- <https://pve.proxmox.com/pve-docs/pve-admin-guide.html#chapter_pvecm>
- <https://pve.proxmox.com/pve-docs/chapter-pmxcfs.html>

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

### Why the existing volume can be reused directly

This is **not** an orphan-only recovery while the Kubernetes and Longhorn CRs listed above still
exist. The retained object chain is:

`immich-library` PVC -> retained PV `pvc-ff54c47e-3e29-4ce3-9192-b6e644351b97` -> Longhorn Volume
CR -> Replica CR `pvc-ff54c47e-3e29-4ce3-9192-b6e644351b97-r-3ef55838` -> disk UUID
`72fbdae2-c51d-41f7-a679-33c6d617ab62`.

The final live check before the rebuild confirmed that the PVC and PV were still `Bound`, the
Longhorn Volume was detached, and the Replica CR still had all of the following:

- label `longhorndiskuuid=72fbdae2-c51d-41f7-a679-33c6d617ab62`
- `spec.diskID: 72fbdae2-c51d-41f7-a679-33c6d617ab62`
- `spec.nodeID: k3s-worker-migration`
- `spec.diskPath: /mnt/longhorn-immich`
- `spec.dataDirectoryName: pvc-ff54c47e-3e29-4ce3-9192-b6e644351b97-b9dc1bdb`
- `spec.active: true`

Longhorn v1.12.0's node controller lists Replica CRs by the disk UUID read from the preserved
`longhorn-disk.cfg`. When that disk becomes Ready on another Longhorn node, the controller updates
each matching Replica CR's `spec.nodeID` and `spec.diskPath` to the new node and its configured
path. Therefore neither the old Kubernetes node name nor the old mount path is required, and the
350 GiB library is not expected to be copied into a second Longhorn volume.

The volume retains `nodeSelector: [immich-migration]` and `diskSelector: [immich-hdd]`. Apply those
tags to the new Longhorn node and preserved disk. These are replica-placement selectors; they do
not force the Immich pod to run on the storage node. The Longhorn engine follows the workload pod
and connects to the replica over the cluster network.

Implementation evidence:

- Longhorn v1.12.0 node controller, disk-UUID replica reassociation:
  <https://github.com/longhorn/longhorn-manager/blob/fcba150e04d609c944b53cb92f7cd8adf32d7585/controller/node_controller.go#L952-L970>
- Longhorn node-maintenance guidance on reusing existing replicas after storage returns:
  <https://longhorn.io/docs/1.12.0/maintenance/maintenance/>

### Primary path: reassociate the retained disk and volume

1. Complete **Proxmox cluster expansion before any guest is created** above, then add the new
   Proxmox node and k3s worker to Terraform and Ansible. The current code only models `pve-dell`,
   `k3s-worker-1`, and `k3s-worker-2`; an unmanaged VM would violate the repo's provisioning
   standard. Use the one clustered Proxmox provider, not a provider alias for an independent host.
2. Pass the preserved HDD or `/dev/sdb2` through to the new worker using stable hardware identity,
   not an assumed `/dev/sdX` name. Confirm the partition by ext4 UUID
   `e613c520-2cd4-4b0f-b8dd-1be2ea055b49`; never format it.
3. Mount the partition read-only at any chosen recovery path. Verify the filesystem UUID,
   `longhorn-disk.cfg`, `volume.meta`, replica directory, and recorded checksums against the
   evidence bundle. Confirm `lsof` and `fuser` report no writers.
4. Unmount it and mount the same filesystem read-write at its durable path, persisted by filesystem
   UUID. Longhorn needs write access when it starts the replica, but the path does not have to be
   `/mnt/longhorn-immich`. **The durable mount must exist before `k3s-agent` starts.** Starting
   Longhorn first and mounting over `/var/lib/longhorn` later can leave engine-image and
   instance-manager pods with different filesystem views.
5. Join the worker to the existing k3s cluster and verify its Longhorn manager/CSI prerequisites.
   Add Longhorn node tag `immich-migration`. Add the mounted path as disk `immich-hdd`, preserving
   the existing `longhorn-disk.cfg`, with disk tag `immich-hdd`. Do not initialize it as a clean
   disk or replace its disk UUID.
6. Watch the existing Replica CR. Longhorn should retain disk ID
   `72fbdae2-c51d-41f7-a679-33c6d617ab62` and data directory name
   `pvc-ff54c47e-3e29-4ce3-9192-b6e644351b97-b9dc1bdb`, while changing only `spec.nodeID` and
   `spec.diskPath` to the new worker/path.
7. Keep `orphan-resource-auto-deletion` empty. The preserved directory should match the existing
   Replica CR rather than become orphaned. If an Orphan CR appears for it, stop and diagnose the
   disk-UUID/CR association; never delete that Orphan CR, because deletion removes its data.
8. Confirm the replica can start and the existing volume becomes attachable. `auto-salvage` was
   `true` at the final live check. If the volume remains Faulted, inspect controller/replica logs
   and perform a controlled salvage using this preserved replica; do not delete/rebuild it.
9. Attach the existing `immich-library` claim to a read-only verification pod and confirm expected
   files before starting Immich. No new library PVC, PV, or 350 GiB copy is part of the primary
   path.
10. Preserve both `/data` and `/usr/src/app/upload` mounts in the Immich values. Restore the three
    application component `enabled` flags to `true`, commit, push, and wait for Argo CD
    `Synced/Healthy`.
11. Repeat the database counts, zero-missing-path check, file count, sample image/video playback,
    login, pod-restart, and node-reschedule checks.
12. Only after acceptance may the obsolete `k3s-worker-migration` Kubernetes/Longhorn objects be
    reviewed for removal. The old `/dev/sdb1` Immich rollback may then be considered for deletion,
    and its free space may be introduced as a separate clean Longhorn disk.

Steps 1–10 completed on 2026-07-24. Step 11 passed database, path, sample-file, playback, login, and
server-pod restart checks. A read-only verification pod also attached and read the library from
`k3s-worker-1`, proving remote Longhorn presentation, but a full Immich-server cross-node
reschedule remains issue #48 acceptance scope before worker-2 retirement. Step 12 removed the
obsolete node objects; `/dev/sdb1` cleanup remains deliberately deferred to that same issue.

Before node deletion, the cluster reported zero old-node replicas, engines, VolumeAttachments,
Orphans, and non-system workloads. Scheduling was disabled on the old Longhorn node and both disks;
deleting the Kubernetes Node triggered automatic Longhorn Node removal. Six unreachable stale pod
records required explicit force deletion because the destroyed kubelet could not acknowledge
termination.

The 2026-07-24 recovery violated the mount-before-k3s ordering and produced a real attach outage.
If this happens again, do not delete the PVC, PV, Volume, or Replica. Read
[INC-2026-001](../incidents/INC-2026-001-longhorn-stale-mount-namespace.md) before acting: after
verifying the host engine binary, both the engine-image and instance-manager filesystem views may
need to be refreshed.

### Recovery branch: the Longhorn disk was mounted after k3s started

Use this branch only when the durable Longhorn filesystem is now mounted correctly, but the
engine error says `/engine-binaries/.../longhorn` does not exist.

1. Confirm the mounted source is the intended filesystem UUID and that `longhorn-disk.cfg` and the
   preserved replica directory are present. Stop if the identity differs.
2. Confirm the engine binary exists and is executable under the host's
   `/var/lib/longhorn/engine-binaries/` tree. If it does not, recreate only the worker's
   engine-image pod and wait for the file to appear.
3. Find the instance-manager pod scheduled to the affected worker and run `stat` against the same
   binary through its `/engine-binaries/` path.
4. If the host sees the file but the instance-manager does not, delete only that instance-manager
   pod. Longhorn recreates this process pod with a fresh view of the mounted filesystem. Never
   substitute deletion of the Replica, Volume, PV, PVC, disk directory, or an Orphan.
5. Verify the recreated instance-manager sees the executable before retrying the application.
6. Wait for the Longhorn engine to become Running, the volume to become `attached/healthy`, and
   the CSI VolumeAttachment to report `attached: true`. CSI retries normally; do not delete the
   attachment merely because it retains an earlier error while a retry is active.
7. If an application pod created during the failed-attach window remains in `ContainerCreating`
   after the attachment is healthy, replace that pod once and let its controller recreate it.
8. Verify Longhorn identity and health, application readiness, Argo CD health, and application
   data before considering any old node or rollback-data cleanup.

## Control-plane placement and eventual `pve-dell` retirement

> **COMPLETED 2026-08-17 — the control-plane move described below was carried out.** `k3s-server-1`
> now runs on `pve-asrock`'s internal `local-lvm`. The planning text that follows is kept verbatim
> because it records the reasoning and the acceptance bar; read it as history, not as pending work.
> Execution plan and evidence:
> `docs/superpowers/plans/2026-08-15-k3s-server-1-control-plane-migration.md`.
>
> How the four prerequisites below were satisfied:
>
> 1. **Capacity inspected for real.** ASRock's `pve` VG had only 14.75GiB genuinely free — the thin
>    pool was 53.93GiB at 72.5% used, and `k3s-worker-3`'s own disk sat at 97.7% of its allocation.
>    Rather than squeeze into that, the pool was extended to **74.68GiB**: 14.75GiB of unallocated VG
>    space plus ~6GiB reclaimed by shrinking a verified-unused 8G swap LV to 2G. Separately, the
>    source disk's apparent 63.47% (~38GiB) allocation proved to be an artifact of `discard=ignore`
>    never releasing freed blocks; enabling discard and running `fstrim` reclaimed 47.9GiB and took
>    it to 18.50%. A RAM check that the original prerequisites did not call for turned out to matter
>    more than the disk one: ASRock had only ~2.6GiB free against 32,027MB physical, so landing the
>    4096MB control plane would have overcommitted the host. `k3s-worker-3` was cut 28,672MB →
>    24,576MB to create ~3.3GiB of reserve; `k3s-server-1` was left at 4096MB deliberately.
> 2. **Verified etcd snapshot and a stated rollback path.** An on-demand snapshot
>    (`pre-asrock-migration-k3s-server-1-1786942650`) was taken and verified immediately before the
>    move. The primary rollback was structural rather than restorative: an offline `qm migrate` does
>    not delete the source disk until the transfer completes, so a failed transfer is recovered with
>    `qm start 100` back on `pve-dell` — which is exactly what happened on the first attempt (see
>    below) and restored service in about 30 seconds.
> 3. **A migration that preserved the VM, proven not merely intended.** Mechanism was offline
>    `qm migrate 100 pve-asrock --with-local-disks --targetstorage local-lvm` (9m36s, ~60GiB at
>    ~111-113 MB/s). VMID 100, IP `192.168.1.21`, disk contents, embedded etcd and the VM's Tailscale
>    identity all survived. On the Terraform side the `bpg/proxmox` provider detected the new
>    `node_name` on refresh and proposed **no changes at all**, so the "must show no control-plane
>    replacement" bar was met outright; `apply -refresh-only` then persisted it as
>    `0 added, 0 changed, 0 destroyed`. Note that `terraform plan` alone reported `No changes` while
>    the on-disk state still read `pve-dell` — the reconciling refresh happens in memory, so the plan
>    verdict can mask stale state.
> 4. **Full post-move verification.** 3/3 nodes `Ready` with `k3s-server-1` still on
>    `192.168.1.21`; 19/19 Argo CD Applications `Synced`/`Healthy`; every Longhorn volume
>    `attached`/`healthy`; CoreDNS resolving; `nextcloud.neovara.uk` returning 302 across repeated
>    attempts and `immich.in.neovara.uk` returning 200; cloudflared clean.
>
> **The first attempt failed and is worth recording.** A 10-minute timeout on the *calling* side
> severed the SSH client at ~53GiB transferred; the Proxmox task log recorded `broken pipe`. Nothing
> was wrong with the hosts, network or storage — throughput was steady throughout. It was rolled
> back cleanly and retried with the transfer launched detached (`nohup`) on the source host so it no
> longer depends on the controlling session. Two estimates were corrected in the process:
> `qm migrate --with-local-disks` streams the **entire** logical volume, not just allocated extents
> (so `fstrim` reduces destination allocation but not wire transfer), and a failed attempt leaves an
> orphaned partial LV on the destination that must be removed before any retry or `lvcreate`
> collides.
>
> **What this move does not achieve**, restated because it is easy to over-read: it does not create
> etcd HA (`k3s-server-1` remains the sole server), and it does not yet deliver Dell-outage
> continuity. Critical Longhorn volumes still need healthy ASRock replicas, and — newly identified
> during this work — `kubectl` from the Mac reaches the API through the Tailscale operator pod,
> which still runs on `k3s-worker-1` on `pve-dell`. Both remain open.

Today `k3s-server-1`, `k3s-worker-1`, and `k3s-worker-2` all live on `pve-dell`; a Dell outage is
therefore a practical Kubernetes outage. After Immich is recovered, the preferred next placement
change is to move the **existing** `k3s-server-1` VM to the workstation, retaining its Kubernetes
identity, embedded-etcd data, IP and API endpoint. Do not leave exactly two embedded-etcd server
members as an attempted HA design: K3s embedded-etcd HA requires an odd number, normally three or
more. A real three-server control plane is a separate future project.

Before moving the existing control-plane VM:

1. Inspect real workstation `local-lvm` capacity and actual guest disk use. The 119 GiB SSD must
   safely hold Proxmox plus the worker OS and control-plane OS; never rely only on thin-provisioned
   virtual sizes.
2. Take and verify an etcd snapshot and have a tested rollback path.
3. Use a reviewed Proxmox/Terraform migration plan that preserves the VM rather than destroys and
   recreates it. A valid plan must show no control-plane replacement.
4. After the move, verify etcd health, Kubernetes API access from the Mac, all nodes, Argo CD,
   Longhorn, DNS, Traefik and both public/private application paths.

Moving the API alone does not make applications survive a Dell outage. At the 2026-07-23 live
checkpoint, ordinary Longhorn replicas—including both replicas of Immich PostgreSQL volume
`pvc-78a421f5-cc42-4e1c-b9c0-c9cd94b7c7c9`—were on `k3s-worker-1` and
`k3s-worker-2`, which share `pve-dell`. Before claiming Dell-outage continuity, place healthy
workstation replicas for every critical PVC and prove the workstation worker has enough compute for
the workloads that must reschedule there. The preserved Immich library replica alone is not enough;
Immich also needs its PostgreSQL volume and routing components.

When the planned third Proxmox node arrives, join it empty to this same Proxmox cluster. To retire
`pve-dell` later:

1. Move or replace every Dell-owned VM and evacuate every required Longhorn replica while Dell is
   healthy. Local Proxmox storage is not made recoverable merely by cluster membership.
2. Confirm no VM, template, required local disk, Kubernetes role, Longhorn replica, route or secret
   recovery dependency remains unique to Dell.
3. Change `proxmox_endpoint` to a surviving cluster member and prove Terraform refresh/plan works
   with zero unintended destroy or replace actions.
4. Power Dell off and remove it from a surviving member with the supported `pvecm delnode` flow.
   Do not improvise by deleting `/etc/pve` files or letting a removed node rejoin with stale cluster
   state.
5. Update Terraform, Ansible, README/architecture docs and recovery material in the same change.

References:

- Proxmox node join: <https://pve.proxmox.com/pve-docs/pve-admin-guide.html#pvecm_join_node_to_cluster>
- Proxmox node removal: <https://pve.proxmox.com/pve-docs/pve-admin-guide.html#pvecm_remove_node>
- K3s embedded-etcd HA: <https://docs.k3s.io/datastore/ha-embedded>

### Fallback only: export an orphaned replica

Use `launch-simple-longhorn` and copy the filesystem to another managed volume only if the Replica
CR/control-plane metadata is missing, or if disk-UUID reassociation fails after the preserved
metadata and Longhorn controller logs have been checked. This fallback is not the expected
post-Proxmox path and must not be started merely because the old node name or mount path changed.

Fallback references:

- <https://longhorn.io/docs/1.12.0/advanced-resources/data-recovery/export-from-replica/>
- <https://longhorn.io/kb/restoring-data-from-an-orphaned-replica-directory/>
- <https://longhorn.io/docs/1.12.0/advanced-resources/data-cleanup/orphaned-data-cleanup/>
