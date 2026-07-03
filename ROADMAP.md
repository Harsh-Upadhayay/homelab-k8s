# Roadmap

Tracked as GitHub Milestones + Issues in this repo (see the [Milestones](../../milestones) and [Issues](../../issues) tabs) — this file is the human-readable summary. Phase numbers refer to [GUIDE.md](./GUIDE.md).

## v0.1 — Foundation
Proxmox host prepared, VM template built, VMs provisioned.
- [x] Phase 1 — Proxmox host: enterprise repo disabled, no-subscription repo enabled, storage verified on NVMe, SSH key access established
- [x] Automate Phase 1 remainder + Tailscale host join via Ansible (`ansible/roles/proxmox_host/`)
- [ ] Phase 2 — Build the Ubuntu cloud-init template
- [x] Bootstrap the scoped `terraform@pve` API token via Ansible (`--tags terraform-token`)
- [ ] Phase 3 — Terraform: provision `k3s-server-1`, `k3s-worker-1`, and `k3s-worker-2` (each worker with a dedicated data disk for the future storage pool — ADR-0021)

## v0.2 — Cluster Bootstrap
The actual k3s cluster, up and verified.
- [ ] Phase 4–6 — Ansible: OS config + install k3s (embedded etcd, secrets-encryption, control-plane taint)
- [ ] Phase 7 — Verify the cluster (nodes Ready, taint landed, etcd snapshot confirmed)
- [ ] Phase 8 — Install Helm

## v0.3 — Ingress & TLS
- [ ] Phase 9 — Traefik (ClusterIP, IngressRoute CRDs)
- [ ] Phase 10 — cert-manager + Cloudflare DNS-01 ClusterIssuers

## v0.4 — Public & Private Access
- [ ] Phase 11 — Cloudflare Tunnel (`cloudflared` + NetworkPolicy)
- [ ] Phase 12 Part A — Tailscale on the Proxmox host (automated, needs an auth key to run)
- [ ] Phase 12 Part B — Tailscale Kubernetes Operator (API server proxy, `loadBalancerClass: tailscale`)

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
