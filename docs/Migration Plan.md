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
  for now. Immich runs CPU-only machine learning; its completed workstation rebuild and
  preserved-disk UUID reassociation are recorded in `docs/migrations/immich.md`.
- **Authelia + LLDAP removed entirely.** They gave finicky, never-quite-one-click integration and
  are not worth porting. New auth model:
  - **Internal apps → Tailscale reachability *is* the auth.** Not on the tailnet = the hostname
    doesn't even resolve. Strip each app's own login where the app allows it.
  - **Public apps → their own built-in login** (Nextcloud and Audiobookshelf each have one).
  - No ForwardAuth middleware, no in-cluster OIDC provider.
- **Watchtower dropped** — GitOps/ArgoCD is the update mechanism now.

## Scope

| Verdict | Services | Group |
| --- | --- | --- |
| ✅ **Migrate** | nextcloud, audiobookshelf, immich | `homelab` |
| ✅ **Migrate** | kiroku (+ kiroku-api), jobhunt | `personal` |
| 🧊 **Defer + preserve data** | ollama, openclaw, mediaserver (gluetun, qbittorrent, flaresolverr, prowlarr, sonarr, radarr, jellyseerr, jellyfin) | (migrated later, by hand) |
| 🗑️ **Drop** | homepage, watchtower, authelia, lldap, portfolio (stays on GitHub Pages), openvscode-server, jenkins (→ GitHub Actions) | — |
| ✔️ **Already replaced** | traefik, cloudflared, prometheus/grafana/node-exporter/cadvisor | — |

## Repo boundaries — hybrid by ownership ("Option E")

This repo's scope widens from "infra provisioning" to **cluster repo**: everything the
platform operator owns. That's three layers — **infra** (`terraform/`, `ansible/`),
**platform** (Traefik, cert-manager, Longhorn, monitoring, ArgoCD itself), and
**off-the-shelf workloads** (nextcloud, audiobookshelf, Immich). Deploying
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

**Dev-owned app** — the *workloads* live in the project's repo under `deploy/`, but the
namespace's **guardrails are platform-owned and stay in this repo** (a dev must not be able
to raise their own quota or grant themselves RBAC). So this repo carries:

```
k8s/apps/personal/<app>/           # PLATFORM-OWNED guardrails (this repo)
├── namespace.yaml                 # ns + group: personal label
├── resourcequota.yaml             # aggregate "how much" ceiling
├── limitrange.yaml                # per-container defaults/bounds (pairs with the quota)
└── rbac.yaml                      # least-privilege deployer SA+Role+RoleBinding (no secrets)
k8s/argocd/apps/<app>-governance.yaml # Application applying the guardrail bundle above (default project)
k8s/argocd/apps/<app>.yaml            # pointer Application: repoURL = project repo, path = deploy/ (personal project)
k8s/argocd/projects/personal.yaml     # locked-down AppProject (shared by all personal apps)
```

The reusable scaffold (`k8s/apps/personal/kiroku/`, the four guardrail files) is established
in M0; each new dev app copies that dir. The two Applications are added when the app lands
(M2 for kiroku). The guardrail Application sits in the trusted `default` project (it creates a
Namespace + RBAC — cluster/namespaced admin the `personal` project deliberately forbids); the
pointer Application sits in `personal` and, with the namespace already made, uses
`CreateNamespace=false`.

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
`192.168.1.0/24`). So in-scope user data (Nextcloud and Audiobookshelf) is migrated
as a **standalone workstream, ahead of the services that consume it** — staged into pre-created
PVCs so a service pod, whenever it lands, just mounts an already-populated volume.

Mechanism (same-LAN, efficient):
- Pre-create the target PVC(s), then run a **mover Job** that mounts the PVC and pulls data over
  the LAN via `rsync` over SSH (or an NFS mount of the source). No public path, no cloudflared —
  node-to-source directly over GbE.
- Bulk data (the Audiobookshelf library) is copied **once, up front**; a final incremental
  `rsync --delete` at cutover catches the delta, keeping the service's downtime window tiny.
- The `longhorn-static` StorageClass is available for binding pre-provisioned volumes to a
  known PVC name, which suits this "populate the volume before the app exists" flow.

### Capacity: Audiobookshelf and Immich are placed; the media stack remains deferred

On 2026-07-13 Longhorn reports about **259 GiB / 268 GiB available per worker** (both workers
still sit on the one external USB SSD — ADR-0022: the internal NVMe stays off-limits). A
2-replica volume must fit on **each** worker; summing both disks is misleading for replicated
capacity. Audiobookshelf's authoritative config and metadata use replicated claims, while its
49.7 GiB reproducible audiobook library uses a 70 GiB single-replica `longhorn` claim.

- **Audiobookshelf fits now** — all four source mount boundaries stage into explicit Longhorn
  PVCs; config/metadata/podcasts are replicated and bulk audiobook media is single-replica. See
  `docs/migrations/audiobookshelf.md`.
- **Immich uses a dedicated HDD tier** — the library is a retained 350 GiB,
  single-replica Longhorn volume on the workstation's preserved `/dev/sdb2`; PostgreSQL is on a
  separate retained, two-replica claim. The app migration, v3 upgrade, Proxmox rebuild, and
  disk-UUID recovery are complete. Longhorn updated the existing Replica CR's node and path on
  `k3s-worker-3`, reusing the PVC/volume without copying the 350 GiB library. Orphan export remains
  documented only as a fallback in `docs/migrations/immich.md`.
- **Media tree (mediaserver, ~TB-scale) does NOT go into the current pool.** The workstation is
  is now a second Proxmox host; `k3s-worker-3` receives the preserved partition by passthrough.
  Immich recovery reused the existing Longhorn partition and disk UUID first. The
  remaining HDD space can become another Longhorn disk only after the preserved `/dev/sdb1`
  rollback/deferred-data partition is explicitly audited and approved for cleanup. Terraform and
  Ansible now model the worker; the remaining storage convergence is tracked in issue #48.

The second host is not standalone. ADR-0049 requires the freshly installed, guest-free workstation
to join a Proxmox cluster created on `pve-dell` before any template, VM, Terraform apply, or HDD
passthrough. One provider/token then manages both nodes and VM placement is expressed with
`node_name`. The temporary two-node stage accepts read-only Proxmox configuration after loss of one
member because provisioning is infrequent; a third physical node is planned one to two months later.
After Immich recovery, the preferred control-plane change is migration of the existing
`k3s-server-1` VM to the workstation—not creation of a two-member etcd cluster. Dell retirement
waits until the third node exists, all guests and Longhorn dependencies have moved, Terraform points
at a survivor, and a zero-destroy plan has passed. See `docs/migrations/immich.md` for the executable
order and ADR-0049 for the decision.

The media tree and rollback copies remain intact on `/dev/sdb1` pending the workstation rebuild
and a later, separately approved cleanup.

## Phased sequence (one new concept per phase)

| Phase | App(s) | New concept | Group / exposure |
| --- | --- | --- | --- |
| **M0 (complete)** | — (groundwork) | App scaffold, AppProjects **as security boundaries**, namespace-label grouping, RBAC + ResourceQuota/LimitRange baseline for dev-owned namespaces, the host→PVC mover pattern, secret convention | — |
| **M1 (cancelled)** | homepage | Dropped from the target; the source dashboard depends on old-host Prometheus and is not required after workstation retirement | — |
| **M2 (complete)** | kiroku (+ kiroku-api) | **First pointer Application** (manifests in kiroku's own repo under `deploy/`), custom GHCR image, two-container app w/ internal Service DNS, first small PVC | personal / public |
| **M3 (complete)** | audiobookshelf | First **real data migration** from old `/storage`; app with a media library; own login | homelab / public |
| **M4** | jobhunt | Pointer Application again; multi-tier app: StatefulSet (MySQL) + Redis + Deployments (django/celery×2/frontend) + a migration **Job** + nginx front | personal / public |
| **M5 (complete)** | nextcloud | The heavy one: Postgres + Redis + app, large PVCs, `pg_dump` restore, trusted-proxy, upload-buffering middleware, cron → **CronJob** | homelab / internal |
| **M6 (complete)** | immich | GitOps chart adoption, retained PVCs, pgvecto.rs → VectorChord, v3 upgrade, then disk-UUID reassociation of the preserved replica across the workstation rebuild | homelab / internal |
| **M7 (in progress)** | old-lab decommission | **Backup/restore drill** + preserve deferred-app data (ollama models, mediaserver media tree + *arr configs, openclaw config/workspace), then power down the old lab | — |

Audiobookshelf M3 is complete: Kubernetes runs 2.35.1 at `audiobookshelf.neovara.uk` with exact
migrated state and the 49.7 GiB library on a one-replica Longhorn claim. Public login, API state,
byte-range playback, secure headers, and WebSockets passed. The old Compose deployment remains
running but is no longer on the public route; its data remains the rollback copy. The detached old
PVC was released after acceptance. Immich M6 is also migrated and accepted; its only library
replica was preserved and reassociated on `k3s-worker-3` through the workstation rebuild.

## Deferred-app data preservation (M7)

`ollama`, `openclaw`, and the whole `mediaserver` tree stay on Compose or disk and get migrated later
by hand. Immich is migrated and post-Proxmox recovery is accepted, but its rollback/deferred-data
partition remains protected until issue #48 explicitly clears it. The old host has been repurposed,
and `/dev/sdb2` is passed through to `k3s-worker-3`; `/dev/sdb1` is **not** wiped. Preservation is
now about not destroying that remaining partition plus taking a safety backup for the small stuff:

- **Media tree** → stays on the 1.4 TB disk in place; migrated by the user after the
  Proxmox/worker-VM conversion and only after its source partition is explicitly released for reuse.
- **Immich rollback** → recovery and acceptance are complete; keep `/storage/immich` untouched
  until issue #48's separate cleanup decision and off-HDD safety boundary are satisfied.
- **`ollama/data` (models), `openclaw/{config,workspace}`, `mediaserver` per-service `state/*/config`**
  → small enough to also take a **verified backup copy** (restic/tar) before the host is repurposed,
  as insurance before the source partition is eventually cleaned or repurposed.

Note: installing Proxmox reformats the workstation's 119 GiB SSD. The 1.4 TB HDD must be excluded
from the installer and preserved with both partitions intact; Linux device names such as `/dev/sdb`
may change after the rebuild, so use the recorded filesystem and Longhorn disk UUIDs. Back up the
small config datasets off-disk before later deleting or repurposing their source partition.

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
| **M6** | Storage · Troubleshooting | Immich exercised retained PVs, application quiescing, database migration, node-loss planning, and preserved Longhorn disk-UUID reassociation; HPA remains a separate study topic. |
| **M7** | Cluster Architecture · Troubleshooting | **etcd snapshot & restore drill** (embedded etcd — the exam's `etcdctl`/restore task, k3s-flavored); **node lifecycle**: `cordon`/`drain`/`uncordon`; joining a new Proxmox-hosted worker VM (≈ the kubeadm-join concept); node `NotReady` triage. |
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
