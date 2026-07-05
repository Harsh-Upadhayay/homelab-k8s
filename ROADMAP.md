# Roadmap

Tracked as GitHub Milestones + Issues in this repo (see the [Milestones](../../milestones) and [Issues](../../issues) tabs) — this file is the human-readable summary. Phase numbers refer to [GUIDE.md](./GUIDE.md).

## v0.1 — Foundation
Proxmox host prepared, VM template built, VMs provisioned.
- [x] Phase 1 — Proxmox host: enterprise repo disabled, no-subscription repo enabled, storage verified on NVMe, SSH key access established
- [x] Automate Phase 1 remainder + Tailscale host join via Ansible (`ansible/roles/proxmox_host/`)
- [x] Phase 2 — Build the Ubuntu cloud-init template
- [x] Bootstrap the scoped `terraform@pve` API token via Ansible (`--tags terraform-token`)
- [x] Phase 3 — Terraform: provision `k3s-server-1`, `k3s-worker-1`, and `k3s-worker-2` (each worker with a dedicated data disk for the future storage pool — ADR-0021). Needed two additional scoped ACL grants beyond the guide's original single grant (ADR-0023 storage, ADR-0024 SDN) before the first apply succeeded.

## v0.2 — Cluster Bootstrap
The actual k3s cluster, up and verified.
- [x] Phase 4–6 — Ansible: OS config + install k3s (embedded etcd, secrets-encryption, control-plane taint). Clean run, 0 failed across all three nodes.
- [x] Phase 7 — Verify the cluster: all 3 nodes Ready, control-plane taint confirmed, embedded etcd confirmed (`control-plane,etcd` role + working manual snapshot save/list), worker data disks confirmed mounted (ADR-0021).
- [x] Phase 8 — Install Helm

## v0.3 — Ingress & TLS
- [x] Phase 9 — Traefik (ClusterIP, IngressRoute CRDs). Chart had drifted its values schema since the guide was written — caught two silent/loud breaks (`ports.websecure.tls` relocation, `service.type` defaulting to `LoadBalancer` unpinned) and pinned the chart version to fix it for good (ADR-0027).
- [x] Phase 10 — cert-manager + Cloudflare DNS-01 ClusterIssuers. Installed via the OCI chart (`crds.enabled=true`), staging+prod `ClusterIssuer`s Ready. Issued a real browser-trusted wildcard cert for `*.in.neovara.uk` (staging-tested first, then prod) into the `internal-wildcard-tls` Secret in `traefik`. Validated the ClusterIssuer secret-namespace rule against live docs (input creds in `cert-manager`, output cert in `traefik`).

## v0.4 — Public & Private Access
- [x] Phase 11 — Cloudflare Tunnel (`cloudflared` + NetworkPolicy). Remotely-managed tunnel live and **Healthy**, egress locked to Traefik/DNS/edge. Public path verified end-to-end: `https://whoami.neovara.uk` → edge → tunnel → Traefik returns the same 404 as an in-cluster hit. Hit and documented Cloudflare's Universal SSL one-level-wildcard limit → public apps use specific first-level names during migration (ADR-0028); also fixed the tunnel target `:8000`→`:80`, added TCP 7844 to the netpol, and fixed a `namespaceSelector` schema bug.
- [x] Phase 12 Part A — Tailscale on the Proxmox host
- [x] Phase 12 Part B — Tailscale Kubernetes Operator: API server proxy live, confirmed working `kubectl` access from a second physical device (MacBook) over the tailnet, zero exposed ports. Needed a 3-scope OAuth client (ADR-0025) and an explicit RBAC `ClusterRoleBinding` (ADR-0026) beyond the base install.
- [x] Phase 12 Part C — Internal service exposure live: `https://whoami.in.neovara.uk` works over Tailscale via a `loadBalancerClass: tailscale` Service for Traefik (`k8s/traefik/tailscale-service.yaml`) + a default `TLSStore` serving the Phase 10 wildcard cert (`k8s/traefik/tlsstore.yaml`) + one grey-cloud wildcard CNAME to the pinned MagicDNS name (ADR-0029). Verified from a tailnet device (real Let's Encrypt cert, no `-k`, HTTP/2 200) including the negative test: off-tailnet the name doesn't even resolve.

## v1.0 — Validated Base Platform
- [x] Phase 13 — End-to-end validation (public path via Cloudflare, private path via Tailscale, both proven against the `whoami` example app). Public: `https://whoami.neovara.uk` → edge → tunnel → Traefik → Service → pod (200 with load-balancing across replicas). Private: `kubectl` over Tailscale confirmed working from a second device. Hit a tunnel-config-staleness gotcha (dashboard hostname wasn't syncing to cloudflared — cloudflared debug logs showed the old `k8s.neovara.uk` entry; fixed by deleting and re-creating the hostname rather than editing). To clean up and keep the cluster lean, `kubectl delete -f k8s/example-app/`.

This is the point where "a production-shaped homelab Kubernetes platform" (per `claude.md`) is actually true end to end.

## v2.0 — Operability (in progress)
The platform grows the things that make it *operable*: storage, GitOps, observability, backups.
- [x] Distributed storage — Longhorn 1.12.0 live on the ADR-0021 disk layout (zero disk changes needed). Two StorageClass tiers as data-replica policy: `longhorn` (1 copy, dev) and `longhorn-replicated` (2 copies, prod) — ADR-0030/0031; upstream `longhorn-system` namespace kept after verifying the alternative (ADR-0032); auth-less UI internal-only at `longhorn.in.neovara.uk` (ADR-0033). Smoke-tested: volume survived pod rescheduling, replicas confirmed one-per-worker, PVC delete confirmed as the real data delete. Standing caveat: one physical NVMe under both workers — topology is real, durability is simulated until physical nodes arrive.
- [x] Monitoring + logging — kube-prometheus-stack 87.10.1 (Prometheus, Alertmanager, Grafana, kube-state-metrics, node-exporter) + Loki 7.0.0 + Grafana Alloy 1.10.0, all in `monitoring`, all on the `longhorn` dev storage tier (ADR-0034). Grafana and Prometheus exposed internal-only via IngressRoute, same Tailscale-only pattern as Longhorn's UI; Grafana's own login removed entirely rather than left as an unused extra (ADR-0036). Loki wired into the same Grafana as a provisioned datasource — no second Grafana, no manual UI step (ADR-0037). Alloy runs as a single Deployment reading logs via the Kubernetes API, not the chart's default per-node DaemonSet, after catching that the default would have tripled log ingestion on this 3-node cluster (ADR-0038); Service names pinned via `fullnameOverride` so the whole stack rebuilds from Git with zero manual `kubectl get svc` steps (ADR-0035). Verified live: all Prometheus targets scraping (including node/pod/container metrics via cAdvisor and kubelet), logs flowing from all 8 active namespaces with no duplication, 27 dashboards + 3 datasources auto-provisioned. Caught two real chart-default bugs along the way — Loki's bundled chunks-cache defaulting to an unschedulable 8Gi, and a River-syntax comment (`#` instead of `//`) that crash-looped Alloy — neither of which `helm template` could catch on its own.
- [x] Metrics scraping for existing infra + real dashboards (issue #25, closed) — Longhorn (`metrics.serviceMonitor.enabled: true`) and Traefik (`metrics.prometheus.service/serviceMonitor.enabled: true`) both wired up, plus k3s's control-plane components (kube-scheduler/kube-controller-manager/etcd), which needed a real k3s server config change — `bind-address=0.0.0.0` / `etcd-expose-metrics: true` via Ansible — and hand-authored `Endpoints` objects, since k3s runs these as threads inside its own binary rather than as pods a Service selector could ever match (ADR-0040). Along the way, found and fixed a bigger bug underneath all three: Prometheus's ServiceMonitor discovery was never actually cluster-wide by default, contrary to an assumption baked into the original monitoring install — `serviceMonitorSelectorNilUsesHelmValues` defaults to `true`, scoping discovery to the chart's own release label (ADR-0039). Official Grafana dashboards imported declaratively for both (Traefik ID 17347, Longhorn ID 17626) via labeled `ConfigMap`, the same sidecar-watch mechanism Grafana already uses, no manual import (ADR-0041). Verified live: all three control-plane jobs plus `traefik-metrics`/`longhorn-backend` showing `up` in Prometheus, both dashboards present and populated with real data.

---

## Deferred — deliberate, not forgotten

Each of these is staged as a clean, additive follow-on once v1.0 is solid. None are missing by accident; see `claude.md` for the full reasoning behind deferring each one.

- **GitOps (Argo CD)** — turns manual `helm install`/`kubectl apply` into commit-and-reconcile; will also take over managing Traefik/cert-manager/cloudflared themselves.
- **Monitoring** (kube-prometheus-stack) and **logging** (Loki + Grafana Alloy) — both slot in additively.
- **Secrets management** (SOPS + age, later External Secrets Operator + Vault) — Kubernetes Secrets stay imperative and uncommitted until this lands.
- **Full backup strategy** — off-box etcd snapshot shipping (`--etcd-s3-*`), Velero, Proxmox Backup Server. Local etcd snapshots are already running from Phase 5.
- **Cilium** — the one *non-additive* item on this list. Flannel → Cilium is a full cluster rebuild, not a live migration; treated as its own dedicated project and a real rebuild-from-Git/DR drill.
- **HA control plane** (`k3s-server-2`, `k3s-server-3`) and **additional workers** — both purely additive given the embedded-etcd choice already made.
