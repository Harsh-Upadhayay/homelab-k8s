# Immich

Immich is fully managed by the `immich` Argo CD Application with automated
prune and self-heal. It uses the official community chart `0.13.1`, with the
server and CPU-only machine-learning images pinned to `v3.0.3`. PostgreSQL,
storage, namespace, and the internal Traefik route remain explicit companion
manifests in this directory.

## Authoritative state

- `immich-library`: 350 GiB, `longhorn-immich-hdd-retain`, retained PV and
  Longhorn volume `pvc-ff54c47e-3e29-4ce3-9192-b6e644351b97`.
- `immich-postgres`: 10 GiB, `longhorn-immich-db-retain`, retained PV and
  Longhorn volume `pvc-78a421f5-cc42-4e1c-b9c0-c9cd94b7c7c9`.
- `immich-db`: runtime Secret documented in `SECRETS.md`; its values are
  intentionally excluded from this public repository.

The restored database still stores absolute asset paths below
`/usr/src/app/upload`. The chart-standard `/data` mount and the historical path
therefore point at the same `immich-library` claim. Do not remove the legacy
mount until a separately planned path-normalization change has rewritten and
revalidated every stored path.

## Adoption and upgrade record

- Signed commit `05f2f45` introduced the GitOps baseline on v2.7.3.
- The official compatibility PostgreSQL image rebuilt `clip_index` and
  `face_index` as `vchordrq`, then dropped pgvecto.rs. Active extensions after
  migration were `vchord 0.4.3` and `vector 0.8.1`.
- Signed commit `2903b33` upgraded the server and machine learning to v3.0.3.
- Final v3 checks on 2026-07-21 JST found database counts
  `19004|1|870|203|37655|12634`, 56,659 unique database-referenced paths with
  zero missing files, and 58,248 files visible through both library mounts.
- `https://immich.in.neovara.uk` returned HTTP 200 and API version v3.0.3
  through the tailnet-only Traefik route.

The pre-adoption logical database dump is
`/mnt/storage1/immich/migration/immich-pre-gitops-20260721-214501-JST.dump`
(92 MiB, SHA-256
`913d31e4cc161809c611c921bb27acacaac30d131e8a7007f9214a3cfe5b992d`).

## 2026-07-21 rollout incident

The first chart sync pulled several new images onto `k3s-worker-migration` and
crossed its SSD ephemeral-storage watermark. Kubelet evicted the Immich pods and
the node's Longhorn manager/CSI pods; the library's only replica was temporarily
marked failed while its engine detached. No media files or Longhorn data were
deleted.

Disposable workstation data was cleared to recover above the kubelet watermark:
archived journals were vacuumed to 100 MiB, the APT cache was cleaned, disabled
Firefox and Mesa Snap revisions were removed, stopped Docker JSON logs were
truncated, and `/home/neovara/.cache` was cleared. Docker's 20 GiB volume store
and 40 GiB image-layer store were not removed. DiskPressure cleared at 22:04
JST; Longhorn's enabled auto-salvage then cleared the replica failure marker and
reattached the healthy volume at 22:06 JST. The zero-missing-file validation was
run after recovery and again after the v3 upgrade.

The node was cordoned before the v3 rollout so application pods moved to the
permanent workers without another image-pull eviction. A separate transient
Tailscale Kubernetes API proxy 503 occurred from roughly 22:23 to 22:25 JST;
the direct API server remained Ready, and the proxy recovered without a cluster
restart or rollout change.

## Workstation rebuild boundary

The library currently has one Longhorn replica on `k3s-worker-migration`; the
node is cordoned after its SSD hit the kubelet ephemeral-storage watermark.
Cordoning does not stop its Longhorn replica or prevent Immich from running on a
permanent worker. Do not wipe or repartition the preserved HDD before recording
the live Longhorn replica/disk state and completing the separate Proxmox
recovery procedure. GitOps recreates Kubernetes objects, but it does not import
an orphaned Longhorn replica from an arbitrary new node or disk path.
