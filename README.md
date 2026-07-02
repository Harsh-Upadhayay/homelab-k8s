# Homelab Kubernetes Platform

A production-shaped Kubernetes platform on a single Proxmox host, built for hands-on operational learning — etcd internals, CNI networking, ingress, secure remote access — not just to get something running. Correctness and understanding are prioritized over the fastest path.

The full phase-by-phase build is in **[GUIDE.md](./GUIDE.md)**. This README is the map; the guide is the territory.

## Architecture

```
Proxmox host (pve-dell)
├── k3s-server-1   control plane, tainted (no app workloads), embedded etcd
└── k3s-worker-1   application workloads
```

| Layer | Choice | Why (short version — see GUIDE.md / wiki for the full reasoning) |
|---|---|---|
| Provisioning | Terraform (`bpg/proxmox`) | Declarative infra state — VM existence, sizing, IPs |
| Configuration | Ansible | Idempotent, SSH-based OS + k3s setup — different problem than provisioning, kept as a different tool |
| Cluster datastore | Embedded etcd (`cluster-init: true`) | Real etcd snapshot/restore/inspection, clean path to HA later |
| CNI | Flannel + kube-proxy (defaults) | Simplest CNI first; Cilium deliberately deferred as its own rebuild-required project |
| Ingress | Traefik (`ClusterIP` only) | No LoadBalancer/MetalLB needed — Cloudflare Tunnel reaches Traefik entirely inside the cluster |
| Public entry | Cloudflare Tunnel (`cloudflared`) | Zero inbound ports on the router, no exposed home IP; one route → Traefik, Traefik does all per-app routing |
| Admin/private entry | Tailscale (two mechanisms: host-level `tailscaled` + in-cluster Operator) | Private access to the Proxmox UI, node SSH, `kubectl`, and dashboards — never public |
| TLS | cert-manager + Cloudflare DNS-01 | Real certs for internal/Tailscale-only services (public path is already terminated at Cloudflare's edge) |

## Repo layout

```
homelab-k8s/
├── GUIDE.md              full phase-by-phase build guide (start here)
├── CHANGELOG.md
├── ROADMAP.md
├── claude.md              context/decisions for AI-assisted work in this repo
├── terraform/             provisions the two VMs on Proxmox
├── ansible/               configures the OS, installs k3s, and manages the Proxmox host itself
│   └── roles/
│       ├── proxmox_host/  host-level housekeeping (apt/repos) + joining the tailnet
│       ├── common/        shared k3s prerequisites (both nodes)
│       ├── k3s_server/    control-plane bootstrap
│       └── k3s_agent/     worker join
└── k8s/                   manifests applied after the cluster is up
    ├── traefik/
    ├── cert-manager/
    ├── cloudflared/
    ├── tailscale/
    └── example-app/
```

## Getting started

1. Read [GUIDE.md](./GUIDE.md) start to finish once before running anything — it explains *why*, not just *what*.
2. Prerequisites, versions pinned, and the one non-negotiable hardware constraint (NVMe for the control-plane VM) are all in GUIDE.md's Phase 0.
3. Follow the phases in order — each one is additive and rebuild-safe except the CNI choice (flagged explicitly where it matters).

## Status

See [ROADMAP.md](./ROADMAP.md) for what's built, what's next, and what's intentionally deferred.

## Documentation

- **[GUIDE.md](./GUIDE.md)** — the canonical build guide, one phase at a time
- **[Wiki](../../wiki)** — architecture decision log and troubleshooting reference
- **[docs/Homelab Learning Map.md](./docs/Homelab%20Learning%20Map.md)** — revision notes: formal ADRs (`docs/adr/`, one log per milestone) for *why*, and concept docs (`docs/concepts/`) for the Ansible/Terraform/Kubernetes/platform mechanics, written up as each phase lands
- **[CHANGELOG.md](./CHANGELOG.md)** — what changed, when
- **[ROADMAP.md](./ROADMAP.md)** — what's next and what's deliberately deferred
