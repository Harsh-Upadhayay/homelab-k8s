# Service Migration Plan — Compose homelab → Kubernetes

Migrating the original Docker Compose homelab ([Harsh-Upadhayay/homelab](https://github.com/Harsh-Upadhayay/homelab))
onto this k8s platform, one subsystem at a time. The old lab is **decommissioned at the
end** of this migration, so any service left behind must have its data preserved first.

This is the durable mapping doc the platform conventions call for: every Compose stack →
its k8s replacement and current status. Phase tracking lives in the **v3.0 milestone** +
one issue per phase (M0–M7), the same Milestone/Issue pattern as the rest of the ROADMAP.

## Context: this is NOT the old repo's `.todo` plan

The original repo carries a migration plan in its `.todo` that assumed a target of "k3s +
Helm + kubectl, local-path storage, no ArgoCD, no Longhorn." **That plan is stale.** This
platform is already much further along, and the migration is planned against what actually
exists here:

- **New app = an ArgoCD `Application`** (app-of-apps), not `helm install`. See `k8s/argocd/`.
- **State = Longhorn PVCs** (`longhorn` dev / `longhorn-replicated` prod), not host bind-mounts.
- **Routing = Traefik `IngressRoute`** CRDs, not Ingress/Gateway API.
- **Public path** = cloudflared → `<app>.neovara.uk`. **Private path** = Tailscale → `<app>.in.neovara.uk`.
- **Secrets = imperative `kubectl create secret`, never committed** (SOPS still deferred — see `claude.md`).
- **Monitoring already exists** — kube-prometheus-stack + Loki + Alloy. Don't re-migrate Prometheus/Grafana.

## Locked decisions

- **GPU workloads deferred.** The old lab was bare metal with an NVIDIA GPU; this cluster is
  Proxmox VMs with no GPU passthrough (yet). Anything that *requires* the GPU stays off-cluster
  for now. **Immich is the exception** — it is migrated **CPU-only** (Immich supports a fully
  CPU-native ML + transcoding path; we simply don't apply the CUDA/NVENC config).
- **Authelia + LLDAP removed entirely.** They gave finicky, never-quite-one-click integration and
  are not worth porting. New auth model:
  - **Internal apps → Tailscale reachability *is* the auth.** Not on the tailnet = the hostname
    doesn't even resolve. Strip each app's own login where the app allows it.
  - **Public apps → their own built-in login** (Nextcloud, Immich, Audiobookshelf each have one).
  - No ForwardAuth middleware, no in-cluster OIDC provider.
- **Watchtower dropped** — GitOps/ArgoCD is the update mechanism now.

## Scope

| Verdict | Services | Group |
| --- | --- | --- |
| ✅ **Migrate** | homepage, nextcloud, audiobookshelf, immich (CPU-only) | `homelab` |
| ✅ **Migrate** | kiroku (+ kiroku-api), jobhunt | `personal` |
| 🧊 **Defer + preserve data** | ollama, openclaw, mediaserver (gluetun, qbittorrent, flaresolverr, prowlarr, sonarr, radarr, jellyseerr, jellyfin) | (migrated later, by hand) |
| 🗑️ **Drop** | watchtower, authelia, lldap, portfolio (stays on GitHub Pages), openvscode-server, jenkins (→ GitHub Actions) | — |
| ✔️ **Already replaced** | traefik, cloudflared, prometheus/grafana/node-exporter/cadvisor | — |

## Namespace & grouping convention

Kubernetes namespaces are **flat** — no nesting. Grouping is expressed at three composed layers:

- **Filesystem:** `k8s/apps/<group>/<app>/` — e.g. `k8s/apps/homelab/nextcloud/`, `k8s/apps/personal/kiroku/`.
- **Cluster:** one short namespace per app (`nextcloud`, `kiroku`) carrying a `group: homelab|personal` label.
- **GitOps:** one ArgoCD `AppProject` per group (`homelab`, `personal`) — Argo's own grouping layer,
  with per-group source/destination allow-lists and its own UI grouping. Each `Application` is assigned to its project.

Per-app manifest set (the reusable scaffold, established in M0):

```
k8s/apps/<group>/<app>/
├── namespace.yaml        # ns + group label
├── deployment.yaml       # or statefulset.yaml
├── service.yaml
├── ingressroute.yaml     # public (neovara.uk) or internal (in.neovara.uk)
├── pvc.yaml              # Longhorn-backed, per durable data boundary
└── (configmap.yaml / cronjob.yaml as needed)
k8s/argocd/apps/<app>.yaml  # the Application registering the above
k8s/argocd/projects/<group>.yaml  # AppProject (created once per group)
```

Secrets are created imperatively and documented (name + keys, no values) in each app's dir,
never committed.

## The host → PVC data-migration pattern (established once in M0, reused everywhere)

Old state lives on the old host under `/storage/...`. For each stateful app:

1. Create the Longhorn PVC(s) for the app.
2. Run a throwaway **mover pod** that mounts the PVC.
3. `rsync` / `kubectl cp` the old `/storage/<app>` data into the PVC (or `pg_dump` → restore for databases).
4. Fix ownership to match the container's runtime UID/GID (see the old repo's
   `ops/runtime-users-and-permissions.md` for the numeric UID/GID contracts).
5. Start the real workload, smoke-test, then cut DNS/ingress over.
6. Only then stop the old Compose stack.

## Data migration is its own track, decoupled from service migration

The cluster and the source data are on the **same LAN** (workstation/old host ↔ k3s nodes on
`192.168.1.0/24`). So user data (Nextcloud files, Immich library, the media tree) is migrated
as a **standalone workstream, ahead of the services that consume it** — staged into pre-created
PVCs so a service pod, whenever it lands, just mounts an already-populated volume.

Mechanism (same-LAN, efficient):
- Pre-create the target PVC(s), then run a **mover Job** that mounts the PVC and pulls data over
  the LAN via `rsync` over SSH (or an NFS mount of the source). No public path, no cloudflared —
  node-to-source directly over GbE.
- Bulk data (the Immich library especially) is copied **once, up front**; a final incremental
  `rsync --delete` at cutover catches the delta, keeping the service's downtime window tiny.
- The `longhorn-static` StorageClass is available for binding pre-provisioned volumes to a
  known PVC name, which suits this "populate the volume before the app exists" flow.

### ⚠️ Capacity: everything-but-media fits Longhorn now; media waits for a future node

Longhorn reports **~541 GiB schedulable** across the two worker disks (both on the one external
USB SSD — ADR-0022: the internal NVMe stays off-limits). The "one physical SSD" caveat affects
*durability* (two replicas on one disk isn't real redundancy — already noted in the ROADMAP),
**not** schedulable capacity.

- **Everything in scope except the media stack fits in ~541 GiB** — Nextcloud data, the Immich
  library, kiroku/jobhunt DBs, audiobookshelf config all stage into Longhorn PVCs directly.
  Budget `longhorn-replicated` (2 copies) only for what truly needs it; single-replica `longhorn`
  for the rest to stretch the pool.
- **Media tree (mediaserver, ~TB-scale) does NOT go into this pool** — and doesn't need an NFS/NAS
  workaround. Plan: once the in-scope services are migrated, **this workstation (currently hosting
  the old lab + the 1.4 TB `/storage` disk) is converted into a k3s node**, its 1.4 TB disk added
  as a Longhorn disk, and the **media stack data is migrated by hand** onto it. Clean additive
  capacity growth (add a node/disk — never touch the internal NVMe), owned by the user, off this
  milestone's critical path.

So there is **no open storage-tier decision** and no NFS dependency: in-scope data → Longhorn now;
media → future workstation-as-node, user-migrated.

## Phased sequence (one new concept per phase)

| Phase | App(s) | New concept | Group / exposure |
| --- | --- | --- | --- |
| **M0** | — (groundwork) | App scaffold, AppProjects, namespace-label grouping, the host→PVC mover pattern, secret convention | — |
| **M1** | homepage | Deployment + Service + IngressRoute + ConfigMap; first real app end-to-end on the public path | homelab / public |
| **M2** | kiroku (+ kiroku-api) | Custom GHCR image, two-container app w/ internal Service DNS, first small PVC | personal / public |
| **M3** | audiobookshelf | First **real data migration** from old `/storage`; app with a media library; own login | homelab / public |
| **M4** | jobhunt | Multi-tier app: StatefulSet (MySQL) + Redis + Deployments (django/celery×2/frontend) + a migration **Job** + nginx front | personal / public |
| **M5** | nextcloud | The heavy one: Postgres + Redis + app, large PVCs, `pg_dump` restore, trusted-proxy, upload-buffering middleware, cron → **CronJob** | homelab / public |
| **M6** | immich (CPU-only) | Heaviest: server + ML(CPU) + pgvecto-rs vector DB + redis, large library PVC, DB restore, **explicitly no GPU** | homelab / public |
| **M7** | old-lab decommission | **Backup/restore drill** + preserve deferred-app data (ollama models, mediaserver media tree + *arr configs, openclaw config/workspace), then power down the old lab | — |

Ordering climbs the difficulty curve deliberately: stateless → small stateful → media library →
multi-tier → heavy relational DB → vector DB + ML.

## Deferred-app data preservation (M7)

`ollama`, `openclaw`, and the whole `mediaserver` tree stay on Compose and get migrated later by
hand. The old host is deprecated, but its 1.4 TB `/storage` disk is **not** wiped — it's the
storage that gets folded back in when the workstation becomes a k3s node (see the capacity
section). So preservation is mostly "don't destroy the disk," plus a safety backup for the
small stuff:

- **Media tree** → stays on the 1.4 TB disk in place; migrated by the user after the
  workstation-as-node conversion adds that disk to Longhorn. No copy needed.
- **`ollama/data` (models), `openclaw/{config,workspace}`, `mediaserver` per-service `state/*/config`**
  → small enough to also take a **verified backup copy** (restic/tar) before the host is repurposed,
  as insurance against the reformat that the node conversion implies.

Note: converting the workstation to a k3s node reformats/repartitions its OS disk — so the
`/storage` (`sdb`) disk must be preserved as-is through that step, and the small config datasets
above backed up off-disk first.

## Acceptance checks (per phase, before moving on)

- `kubectl get pods -n <ns>` healthy/ready; the ArgoCD Application `Synced`/`Healthy`.
- Ingress hostname serves a valid cert and routes to the right backend (public *or* tailnet-only, as intended).
- Persistent data survives pod restart and node reschedule.
- Logs + metrics visible in the existing Grafana/Loki.
- For stateful apps: old data backed up and verified restored **before** the old Compose workload is stopped.
