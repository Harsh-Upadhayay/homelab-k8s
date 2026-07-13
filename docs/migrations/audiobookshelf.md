# Migration plan — Audiobookshelf (M3)

**Date:** 2026-07-13 · **Status:** in progress; pre-flight passed · **Exposure:** internal-first, then
public at `audiobookshelf.neovara.uk`.

This is the next Compose → Kubernetes migration. Immich is explicitly outside this run: its
321 GB library, database/vector-version transition, and workstation-storage lifecycle remain a
separate blocked project. Nothing in this plan changes, copies, or deletes Immich data.

## Source assessment

Source: `../homelab/audiobookshelf/compose.yml`, running on this workstation.

| Boundary | Source path | Container path | Current state |
| --- | --- | --- | --- |
| SQLite/config | `/storage/audiobookshelf/config` | `/config` | 25.8 MiB; `absdatabase.sqlite` is 15.8 MB |
| Metadata/backups | `/storage/audiobookshelf/metadata` | `/metadata` | 206.3 MiB; two built-in backups present |
| Audiobooks | `/storage/audiobookshelf/audiobooks` | `/audiobooks` | 49.7 GiB; 1,105 files |
| Podcasts | `/storage/audiobookshelf/podcasts` | `/podcasts` | empty |

- Image: `ghcr.io/advplyr/audiobookshelf:2.10.1` (pinned; released 2024-05-27).
- Runtime: one root-run process, port 80, no restarts/OOM kills in the inspected run; current
  idle memory is about 163 MiB.
- Filesystem: `/storage` is mergerfs over the workstation's ext4 `/dev/sdb1` data disk.
- Public path: `https://audiobookshelf.neovara.uk` currently returns HTTP 200 through the old
  Traefik/Cloudflare path.
- Database baseline: SQLite `quick_check=ok`; 3 users, 1 library, 1 library folder,
  60 library items/books, 12 media-progress rows, and 338 playback sessions after the pre-flight
  login. The stored library
  path is `/audiobooks`, so that container path must not change.
- Known source issue: every periodic scan currently reports invalid data for one existing `.m4b`.
  This predates Kubernetes. Preserve the file byte-for-byte and track the warning as a source
  defect, not a migration regression.
- Known source issue: source restarts can try to re-sync an already-recorded local playback session
  and log `UNIQUE constraint failed: playbackSessions.id`. This occurred before the migration and
  did not make `quick_check` fail; compare behavior rather than treating the first occurrence on
  Kubernetes as a new data-corruption signal.

## Target design

### Packaging and GitOps

Audiobookshelf publishes an official container and recommends Docker, but does not publish an
official Helm chart. The available Audiobookshelf charts are third-party wrappers around generic
common charts. For one container, four mounts, and one Service, that abstraction adds another
release/dependency chain without removing meaningful work.

Use the repo's readable off-the-shelf-app scaffold instead:

```text
k8s/apps/homelab/audiobookshelf/
├── namespace.yaml
├── deployment.yaml
├── service.yaml
├── pvcs.yaml
└── routing.yaml
k8s/argocd/apps/audiobookshelf.yaml
```

The ArgoCD Application belongs to the existing `homelab` AppProject, which already permits the
`audiobookshelf` namespace. It uses this Git repo as its only source, so no Helm repository needs
to be added to the project allow-list.

### Workload

- One `Deployment`, one replica, pinned initially to **2.10.1**.
- `strategy: Recreate`, not RollingUpdate. All four claims are RWO, and `/config` contains a
  single-writer SQLite database; overlapping old/new pods on the same node would be unsafe even
  though Kubernetes might allow both to mount an RWO volume there.
- Preserve source behavior for the migration: run as root initially, while still applying
  `allowPrivilegeEscalation: false`, `RuntimeDefault` seccomp, and a minimal capability set
  (`drop: [ALL]`, `add: [NET_BIND_SERVICE]` for port 80). A non-root conversion is a separate
  post-migration change because every current file is root-owned and the old repo explicitly warns
  against casually changing this runtime user.
- `TZ=Asia/Tokyo`, matching Compose.
- Initial resources: request `100m` CPU / `256Mi` memory, limit memory at `1Gi`, and no CPU limit
  (trusted homelab-app policy). Re-size from real Prometheus data after scans and playback have run.
- Use HTTP startup/readiness probes against `/` on port 80. Do not add liveness initially:
  upstream discourages restart-oriented health checks, and readiness is enough to keep an
  initializing or migrating server out of Service endpoints without creating a restart loop.

### Storage

Keep four separate RWO claims so the upstream mount boundaries stay explicit:

| PVC | Class | Request | Reason |
| --- | --- | --- | --- |
| `audiobookshelf-config` | `longhorn-replicated` | 1 Gi | Authoritative SQLite/users/progress |
| `audiobookshelf-metadata` | `longhorn-replicated` | 2 Gi | Covers, derived metadata, logs, built-in backups |
| `audiobookshelf-audiobooks` | `longhorn-replicated` | 70 Gi | 49.7 GiB now, about 40% growth headroom |
| `audiobookshelf-podcasts` | `longhorn-replicated` | 5 Gi | Empty now, ready for future downloads |

Each application mount uses the claim's `data` subdirectory. This keeps Longhorn's ext4
`lost+found` directory outside Audiobookshelf's library/config paths; the mover creates `data`
before the zero-replica Deployment is enabled.

Longhorn currently reports about 259 GiB and 268 GiB available on the two workers. Scheduling
78 GiB on each worker for these 2-copy claims fits with substantial headroom. Both replicas still
live on one physical external SSD, so this protects against a worker-VM failure, not physical-disk
failure. The untouched source copy remains the rollback copy until a real off-box backup exists.

The upstream warning against putting `/config` on SMB/NFS is satisfied: a Longhorn RWO claim is
presented to the pod as a locally mounted ext4 block device through CSI/iSCSI. It is not a shared
network filesystem. Do not change `/config` to RWX, NFS, or SMB.

All current Longhorn classes use reclaim policy `Delete`. Consequently, deleting the namespace or
any of these PVCs deletes the migrated data. No PVC/namespace deletion is part of this run.

### Networking and authentication

- `ClusterIP` Service on port 80.
- First route: `audiobookshelf.in.neovara.uk` on `websecure` with `tls: {}` for validation over
  Tailscale.
- Public cutover route: `audiobookshelf.neovara.uk` on `web`, reached through the one cloudflared
  route to Traefik. Traefik handles WebSockets without an app-specific setting.
- Carry the secure response headers but add no CORS middleware; upstream explicitly warns that
  Traefik CORS headers can break login.
- Authelia/LLDAP do not move. The three existing Audiobookshelf users and password hashes move with
  SQLite, and the app's own login becomes the public authentication boundary.

## Execution plan

### 0. Pre-flight and rollback baseline

Completed 2026-07-13: root/admin login passed; SQLite `quick_check=ok`; the recorded row/file
baseline was refreshed; built-in backup `2026-07-13T2154.audiobookshelf` completed successfully;
both Longhorn workers were Ready and schedulable. Monitoring's only degraded resources were the
three intentionally excluded control-plane Endpoints, so that known state was waived for this run.

1. Confirm the real root/admin account can log in to the source before touching it. Do not discover
   a forgotten password after the source has been stopped.
2. Record the baseline above again immediately before migration: source version, SQLite
   `quick_check`, database row counts, file counts/bytes, and a sample of listening progress.
3. Trigger and verify a fresh built-in Audiobookshelf backup. This protects the database and cover
   images but **does not** replace a media backup.
4. Confirm both workers and Longhorn are healthy and can schedule all four replicated claims.
5. Resolve or explicitly waive the currently `OutOfSync/Degraded` kube-prometheus-stack
   Application before using monitoring as acceptance evidence. It is unrelated to Audiobookshelf,
   but it should not muddy migration triage.

### 1. Land an inert GitOps deployment

1. Add the namespace, four PVCs, Recreate Deployment, Service, internal IngressRoute, and ArgoCD
   Application.
2. Commit the Deployment with `replicas: 0`. ArgoCD may create/bind storage and routing, but no
   blank Audiobookshelf instance may initialize `/config` before the real state is copied.
3. Verify ArgoCD `Synced/Healthy`, all claims `Bound`, and Longhorn volumes `Healthy` before moving
   data.

### 2. Pre-seed the bulk media

1. Run a throwaway mover pod mounting the audiobook and podcast PVCs.
2. Copy `/storage/audiobookshelf/audiobooks/` over the LAN using resumable `rsync` over a temporary,
   migration-only SSH path. Use archive mode and preserve names/timestamps; do not alter the source.
3. Copy the empty podcast tree so its boundary and permissions are still verified.
4. Compare file count, apparent bytes, and a deterministic checksum sample. Do not copy `/config`
   live—SQLite remains owned by the running source.
5. Ask users not to upload, merge, embed metadata, or reorganize books between the bulk copy and
   cutover. Playback is safe; library mutations are not.

In progress 2026-07-13: the mover is pulling over a temporary restricted SSH authorization with
resumable rsync. Full-speed writes to a 2-replica Longhorn volume caused brief API/etcd latency and
unrelated probe failures, so the copy was resumed with `--bwlimit=6000`. The API returned Ready
after throttling. At the operator's direction the limit was later removed because the cluster had
no active users; the transfer then sustained about 12.8 MiB/s at the workstation link's practical
maximum. At 13:44 UTC both replicas faulted, Longhorn auto-salvaged/rebuilt the volume, and the
existing mover mount remained read-only; rsync stopped with code 11. A full mover detach/remount
restored a clean read-write ext4 mount, retained 23.4 GiB/354 files of partial data, and passed a
synced write/delete check. The resumable copy then restarted with `--bwlimit=5000`, sustaining
about 4.9 MiB/s while both volumes and the API remained Healthy/Ready. Do not remove the temporary SSH
authorization until rsync completes and its final verification pass succeeds.

While the bulk copy ran, an isolated `emptyDir` smoke pod validated the exact 2.10.1 image and
proposed security/probe configuration without mounting any migration PVC. A 145 MiB single-file
audiobook initialized and scanned as exactly one library item/one audio file; authenticated range
playback returned HTTP 206 with the requested 1,024 bytes. The temporary Traefik route returned
HTTPS 200 with the secure headers, and its Socket.IO WebSocket upgraded with HTTP 101. All smoke
resources were deleted afterward.

### 3. Quiesce and take the final consistent copy

1. Start the maintenance window and stop the old `audiobookshelf` Compose container cleanly.
2. Re-run the media `rsync --delete` to catch the delta.
3. Copy `/config` and `/metadata` only now, with the writer stopped. Preserve root ownership and
   modes. Copying a live SQLite file is not an accepted shortcut.
4. Run SQLite `quick_check` against the PVC copy and compare the baseline row counts.
5. Remove the mover pod so no second process holds the RWO claims.

### 4. Validate the migrated source version internally

1. Commit `replicas: 1` while keeping image `2.10.1` and only the internal route enabled.
2. Wait for the startup/readiness probes, then verify clean database initialization/migration logs.
3. Validate through `https://audiobookshelf.in.neovara.uk`:
   - root/admin login and both non-root users;
   - 1 library, 60 books, covers/metadata, collections/playlists, and playback history;
   - resume position for at least one known in-progress book;
   - browser playback, seek, pause/resume, and a mobile-client connection;
   - WebSocket connection remains established;
   - a library scan returns the same item count;
   - the known invalid `.m4b` warning is unchanged, with no new missing files.
4. Restart/reschedule the pod once and repeat login/playback to prove the four PVCs reattach with
   intact state.

### 5. Upgrade through the real compatibility boundaries

Audiobookshelf runs database migrations automatically in version order. We still separate the old
host migration from the application upgrade and checkpoint the two historically important
boundaries. Before each image change, create a built-in backup plus Longhorn snapshots of all four
claims; keep image and matching snapshots as one rollback unit.

1. **2.10.1 → 2.17.2** — v2.17.2 specifically fixed upgrades from v2.10.1-and-older to v2.17+.
2. **2.17.2 → 2.25.1** — last release before the authentication-system change.
3. **2.25.1 → 2.26.3** — JWT/refresh-token migration. All users must re-login to the web/mobile
   clients; verify every account and discard assumptions based on old sessions/tokens.
4. **2.26.3 → 2.35.1** — latest stable release verified on 2026-07-13.

At every checkpoint require: pod Ready, SQLite `quick_check=ok`, expected row/item counts, admin
login, known progress, one playback, clean startup logs, and ArgoCD `Synced/Healthy`. Stop on the
first failed checkpoint; do not stack another version change onto an unexplained failure.

### 6. Public cutover

1. In Cloudflare Tunnel, create the specific public hostname `audiobookshelf.neovara.uk` pointing
   to `http://traefik.traefik.svc.cluster.local:80`. A specific record overrides the old wildcard
   route without moving any other service.
2. Add the public `web` IngressRoute and confirm the live cloudflared config actually refreshed;
   tunnel `Healthy` alone is not proof.
3. Validate HTTP 200, login, WebSocket, playback, mobile-client reconnect, and upload-size behavior
   over the real public path.
4. After a stable observation window, remove the internal validation route so the final shape is
   public-only, matching kiroku/Nextcloud's cutover convention.

### 7. Close-out

1. Keep the old Compose container stopped, not deleted. Keep all four source directories untouched.
2. Record final versions, counts, PVCs, incidents, and rollback evidence in this file; change status
   to complete only after public validation.
3. Mark Audiobookshelf migrated in the checklist. Immich remains deferred and its source data stays
   intact.

## Rollback

- Before public cutover: scale the Kubernetes Deployment to zero and restart the old Compose
  container. DNS/public routing never moved, so rollback is immediate.
- After public cutover: point/delete the specific Cloudflare tunnel hostname so the legacy wildcard
  serves the old host again, scale Kubernetes to zero, then start Compose.
- After an application upgrade: restore the matching four-volume Longhorn snapshot set and pin the
  corresponding image version. Never run an older image against a partially upgraded live database
  and hope it repairs itself.
- Never delete the Kubernetes namespace/PVCs during rollback; all relevant StorageClasses have
  reclaim policy `Delete`.

## Upstream references

- [Official Docker install and storage contract](https://audiobookshelf.org/docs/documentation/install/docker/)
- [Official database-migration behavior](https://audiobookshelf.org/docs/contributing/database-migrations/)
- [v2.17.2 release — fix for upgrades from v2.10.1 and older](https://github.com/advplyr/audiobookshelf/releases/tag/v2.17.2)
- [v2.26.0 release — new authentication system and mandatory re-login](https://github.com/advplyr/audiobookshelf/releases/tag/v2.26.0)
- [v2.35.1 release](https://github.com/advplyr/audiobookshelf/releases/tag/v2.35.1)
