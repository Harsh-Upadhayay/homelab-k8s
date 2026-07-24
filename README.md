# Homelab Kubernetes Platform

A production-shaped Kubernetes platform built for hands-on operational learning — etcd internals, CNI networking, ingress, secure remote access — not just to get something running. Correctness and understanding are prioritized over the fastest path. Proxmox cluster `neovara` now contains `pve-dell` and the freshly rebuilt, empty `pve-asrock`; the existing Kubernetes VMs remain on Dell while the workstation awaits its Immich recovery worker. A third physical Proxmox node is planned one to two months later. The new node and worker are documented but not implemented in Terraform or Ansible yet.

The full phase-by-phase build is in **[GUIDE.md](./GUIDE.md)**. This README is the map; the guide is the territory.

## Architecture

```
Proxmox cluster neovara
├── pve-dell
│   ├── k3s-server-1   control plane, tainted, embedded etcd
│   ├── k3s-worker-1   application workloads + Longhorn data disk
│   └── k3s-worker-2   application workloads + Longhorn data disk
└── pve-asrock         empty; recovery worker not provisioned yet
    └── preserved physical HDD remains outside Proxmox storage
```

| Layer | Choice | Why (short version — see GUIDE.md / wiki for the full reasoning) |
|---|---|---|
| Provisioning | Terraform (`bpg/proxmox`) | Declarative infra state — VM existence, sizing, IPs |
| Hypervisor topology | One Proxmox cluster (ADR-0049) | One API/token and normal node lifecycle; temporary two-node read-only-on-quorum-loss behavior is accepted until node 3 |
| Configuration | Ansible | Idempotent, SSH-based OS + k3s setup — different problem than provisioning, kept as a different tool |
| Cluster datastore | Embedded etcd (`cluster-init: true`) | Real etcd snapshot/restore/inspection, clean path to HA later |
| CNI | Flannel + kube-proxy (defaults) | Simplest CNI first; Cilium deliberately deferred as its own rebuild-required project |
| Ingress | Traefik (`ClusterIP` only) | No LoadBalancer/MetalLB needed — Cloudflare Tunnel reaches Traefik entirely inside the cluster |
| Public entry | Cloudflare Tunnel (`cloudflared`) | Zero inbound ports on the router or exposed home IP; current per-host tunnel entries all target Traefik, which performs the actual app routing |
| Admin/private entry | Tailscale (two mechanisms: host-level `tailscaled` + in-cluster Operator) | Private access to the Proxmox UI, node SSH, `kubectl`, and dashboards — never public |
| TLS | cert-manager + Cloudflare DNS-01 | Real certs for internal/Tailscale-only services (public path is already terminated at Cloudflare's edge) |

## Repo layout

```
homelab-k8s/
├── GUIDE.md              full phase-by-phase build guide (start here)
├── CHANGELOG.md
├── ROADMAP.md
├── claude.md              context/decisions for AI-assisted work in this repo
├── terraform/             provisions the current three VMs on pve-dell
├── ansible/               configures the OS, installs k3s, and manages the Proxmox host itself
│   └── roles/
│       ├── proxmox_host/  host-level housekeeping (apt/repos) + joining the tailnet
│       ├── common/        shared k3s prerequisites (all cluster nodes)
│       ├── k3s_server/    control-plane bootstrap
│       └── k3s_agent/     worker join
└── k8s/                   GitOps-managed platform and application manifests
    ├── argocd/             app-of-apps definitions and AppProjects
    ├── traefik/
    ├── cert-manager/
    ├── cloudflared/
    ├── tailscale/
    ├── longhorn/
    ├── monitoring/
    ├── argo-rollouts/
    ├── apps/
    └── example-app/
```

## Getting started

1. Read [GUIDE.md](./GUIDE.md) start to finish once before running anything — it explains *why*, not just *what*.
2. Prerequisites, version pins, and the non-negotiable storage constraint (the internal NVMe is off-limits; this deployment uses the external USB SSD) are in GUIDE.md's Phase 0.
3. Follow the phases in order — each one is additive and rebuild-safe except the CNI choice (flagged explicitly where it matters).

## Status

See [ROADMAP.md](./ROADMAP.md) for what's built, what's next, and what's intentionally deferred.

## Documentation

- **[GUIDE.md](./GUIDE.md)** — the canonical build guide, one phase at a time
- **[Wiki](../../wiki)** — architecture decision log and troubleshooting reference
- **[docs/Homelab Learning Map.md](./docs/Homelab%20Learning%20Map.md)** — revision notes: formal ADRs (`docs/adr/`, one log per milestone) for *why*, and concept docs (`docs/concepts/`) for the Ansible/Terraform/Kubernetes/platform mechanics, written up as each phase lands
- **[Proxmox VE 9.2-1 I219-V recovery installer](./docs/troubleshooting/proxmox-ve-9.2-1-i219v-recovery/)** — unofficial modified installer, checksum, exact source patch, build manifest, risks, verification, and rollback
- **[CHANGELOG.md](./CHANGELOG.md)** — what changed, when
- **[ROADMAP.md](./ROADMAP.md)** — what's next and what's deliberately deferred
