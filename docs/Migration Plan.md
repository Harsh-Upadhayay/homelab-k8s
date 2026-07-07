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

## Repo boundaries — hybrid by ownership ("Option E")

This repo's scope widens from "infra provisioning" to **cluster repo**: everything the
platform operator owns. That's three layers — **infra** (`terraform/`, `ansible/`),
**platform** (Traefik, cert-manager, Longhorn, monitoring, ArgoCD itself), and
**off-the-shelf workloads** (homepage, nextcloud, audiobookshelf, immich). Deploying
third-party software is platform-adjacent config — the same kind of artifact as Grafana
values, so it belongs here.

**Dev-owned projects live in their own repos.** kiroku, jobhunt, and any future shared
(friend-owned) project keep their k8s manifests next to their source code (a `deploy/`
dir); this repo holds only a *pointer* — an ArgoCD `Application` whose `repoURL` targets
the project repo. Rationale: **push access to a GitOps-watched repo is deploy access to
the cluster**, and this repo self-manages ArgoCD, so write access here ≈ cluster-admin.
Collaborators get write on their own project repo, never on this one.

## Namespace & grouping convention

Kubernetes namespaces are **flat** — no nesting. Grouping is expressed at three composed layers:

- **Filesystem:** `k8s/apps/homelab/<app>/` for off-the-shelf apps in this repo;
  `deploy/` in the project's own repo for dev-owned apps.
- **Cluster:** one short namespace per app (`nextcloud`, `kiroku`) carrying a `group: homelab|personal` label.
- **GitOps:** one ArgoCD `AppProject` per group (`homelab`, `personal`, later `shared`) —
  each `Application` is assigned to its project. For dev-owned groups the AppProject is the
  **security boundary**, not just UI grouping: `sourceRepos` allow-lists only that project's
  repo(s), `destinations` only its namespace(s), and cluster-scoped resources are denied.
  Namespace creation stays on this repo's side (the `CreateNamespace=true` sync option runs
  with ArgoCD's own permissions, not the external repo's).

Per-app manifest set — **homelab (off-the-shelf) app, in this repo** (the reusable
scaffold, established in M0):

```
k8s/apps/homelab/<app>/
├── namespace.yaml        # ns + group label
├── deployment.yaml       # or statefulset.yaml
├── service.yaml
├── ingressroute.yaml     # public (neovara.uk) or internal (in.neovara.uk)
├── pvc.yaml              # Longhorn-backed, per durable data boundary
└── (configmap.yaml / cronjob.yaml as needed)
k8s/argocd/apps/<app>.yaml        # the Application registering the above
k8s/argocd/projects/homelab.yaml  # AppProject (created once per group)
```

**Dev-owned app** — same manifest set, but it lives in the project's repo under `deploy/`;
this repo carries only:

```
k8s/argocd/apps/<app>.yaml         # pointer Application: repoURL = project repo, path = deploy/
k8s/argocd/projects/personal.yaml  # locked-down AppProject (sourceRepos/destinations restricted)
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
| **M0** | — (groundwork) | App scaffold, AppProjects **as security boundaries**, namespace-label grouping, RBAC + ResourceQuota/LimitRange baseline for dev-owned namespaces, the host→PVC mover pattern, secret convention | — |
| **M1** | homepage | Deployment + Service + IngressRoute + ConfigMap; first real app end-to-end on the public path | homelab / public |
| **M2** | kiroku (+ kiroku-api) | **First pointer Application** (manifests in kiroku's own repo under `deploy/`), custom GHCR image, two-container app w/ internal Service DNS, first small PVC | personal / public |
| **M3** | audiobookshelf | First **real data migration** from old `/storage`; app with a media library; own login | homelab / public |
| **M4** | jobhunt | Pointer Application again; multi-tier app: StatefulSet (MySQL) + Redis + Deployments (django/celery×2/frontend) + a migration **Job** + nginx front | personal / public |
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

## CKA learning track (woven into the phases)

This migration doubles as CKA prep. Each phase is mapped to the exam domains —
**Troubleshooting 30% · Cluster Architecture, Installation & Configuration 25% ·
Services & Networking 20% · Workloads & Scheduling 15% · Storage 10%** — and each
concept is taught at the phase where it naturally appears, with exam-style practice
(imperative `kubectl`, time-boxed, `--dry-run=client -o yaml` as the YAML source, not
copy-paste).

| Phase | CKA domain(s) | Concepts to learn & practice |
| --- | --- | --- |
| **M0** | Cluster Architecture (RBAC) · Workloads | **RBAC end-to-end**: Role vs ClusterRole, RoleBinding vs ClusterRoleBinding, ServiceAccounts; build a namespace-scoped "deployer" Role for the future shared namespace and verify with `kubectl auth can-i --as=system:serviceaccount:...`. **ResourceQuota + LimitRange** on `personal` namespaces (dev-owned code gets bounded blast radius). Labels + selectors as the grouping primitive. |
| **M1** | Workloads & Scheduling | Deployment anatomy; **liveness vs readiness probes**; **requests vs limits and the QoS classes** they produce; rolling update, `kubectl rollout status/history/undo`; the imperative-create workflow (`kubectl create deploy --image=... --dry-run=client -o yaml`). |
| **M2** | Services & Networking | **CoreDNS + Service DNS**: `<svc>.<ns>.svc.cluster.local` forms, cross-namespace resolution, debugging with a throwaway `busybox` pod (`nslookup`, `wget`). First **NetworkPolicy**: default-deny in the kiroku namespace, then allow frontend→api only — the exam's classic netpol task shape. |
| **M3** | Storage | The whole 10% domain in one phase: **PV/PVC binding, access modes, reclaim policies, StorageClasses, dynamic vs static provisioning** — `longhorn-static` pre-provisioned binding is literally the exam's "create a PV, bind a PVC to it" task. Plus **securityContext** (`runAsUser`/`fsGroup`) to satisfy the old UID/GID contracts. |
| **M4** | Workloads · Troubleshooting | **StatefulSet vs Deployment** (stable identity, one-PVC-per-replica via `volumeClaimTemplates`), **headless Services**, **Job** semantics (`backoffLimit`, `restartPolicy`), init containers. A 6-workload app is a triage playground — deliberate break-and-fix drills. |
| **M5** | Workloads & Scheduling | **CronJob** (schedule syntax, `concurrencyPolicy`, history limits); **Secrets**: types, `envFrom` vs volume mounts, and why a ConfigMap/Secret change doesn't restart pods by itself. |
| **M6** | Workloads (autoscaling) · Troubleshooting | **HPA** on the CPU-bound ML service (workload autoscaling is in the current CKA curriculum); **OOMKilled, node-pressure eviction, QoS-ordered eviction** — tuning real memory-hungry workloads on RAM-bounded nodes. |
| **M7** | Cluster Architecture · Troubleshooting | **etcd snapshot & restore drill** (embedded etcd — the exam's `etcdctl`/restore task, k3s-flavored); **node lifecycle**: `cordon`/`drain`/`uncordon`; joining a new node (workstation → k3s agent ≈ the kubeadm-join concept); node `NotReady` triage. |
| **every phase** | Troubleshooting (30%) | The acceptance checks below are run as triage practice, not checklist theatre: `kubectl describe`/`logs --previous`/`get events --sort-by`, `kubectl debug`, DNS + Service-endpoint checks. At least one deliberate break-then-fix per phase. |

Known gaps this migration **won't** cover (study separately before the exam): **kubeadm**
cluster install/upgrade (k3s hides it), **Gateway API / vanilla Ingress** (this platform
deliberately uses Traefik's IngressRoute CRD instead), and multi-node scheduling controls
(taints/tolerations/affinity beyond the existing control-plane taint).

## Acceptance checks (per phase, before moving on)

- `kubectl get pods -n <ns>` healthy/ready; the ArgoCD Application `Synced`/`Healthy`.
- Ingress hostname serves a valid cert and routes to the right backend (public *or* tailnet-only, as intended).
- Persistent data survives pod restart and node reschedule.
- Logs + metrics visible in the existing Grafana/Loki.
- For stateful apps: old data backed up and verified restored **before** the old Compose workload is stopped.
