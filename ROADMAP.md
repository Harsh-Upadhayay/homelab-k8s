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

## v1.0 — Validated Base Platform
- [ ] Phase 13 — End-to-end validation (public path via Cloudflare, private path via Tailscale, both proven against the `whoami` example app)

This is the point where "a production-shaped homelab Kubernetes platform" (per `claude.md`) is actually true end to end.

---

## Deferred — deliberate, not forgotten

Each of these is staged as a clean, additive follow-on once v1.0 is solid. None are missing by accident; see `claude.md` for the full reasoning behind deferring each one.

- **GitOps (Argo CD)** — turns manual `helm install`/`kubectl apply` into commit-and-reconcile; will also take over managing Traefik/cert-manager/cloudflared themselves.
- **Monitoring** (kube-prometheus-stack) and **logging** (Loki + Grafana Alloy) — both slot in additively.
- **Secrets management** (SOPS + age, later External Secrets Operator + Vault) — Kubernetes Secrets stay imperative and uncommitted until this lands.
- **Full backup strategy** — off-box etcd snapshot shipping (`--etcd-s3-*`), Velero, Proxmox Backup Server. Local etcd snapshots are already running from Phase 5.
- **Cilium** — the one *non-additive* item on this list. Flannel → Cilium is a full cluster rebuild, not a live migration; treated as its own dedicated project and a real rebuild-from-Git/DR drill.
- **Distributed storage (Longhorn)** — the disks, mounts, and second worker are already provisioned for it (ADR-0021); the install itself (Helm chart + CSI, `open-iscsi` prerequisite on workers) is its own additive phase. Until real physical nodes join, replication across the two workers simulates the topology without providing physical durability.
- **HA control plane** (`k3s-server-2`, `k3s-server-3`) and **additional workers** — both purely additive given the embedded-etcd choice already made.
