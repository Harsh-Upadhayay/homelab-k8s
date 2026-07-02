# Changelog

Loosely follows [Keep a Changelog](https://keepachangelog.com/). Dates are when a change actually landed in this repo, not when the underlying infrastructure step happened by hand (those are tracked in [GUIDE.md](./GUIDE.md)'s phase list and [ROADMAP.md](./ROADMAP.md)).

## [Unreleased]

### Added
- Terraform (`terraform/`) provisioning `k3s-server-1` (control plane) and `k3s-worker-1` (worker) via the `bpg/proxmox` provider, cloned from a cloud-init template.
- Ansible roles `common`, `k3s_server`, `k3s_agent` — OS prerequisites and k3s bootstrap/join, driven by `ansible/site.yml`.
- Ansible role `proxmox_host` (`ansible/proxmox-host.yml`) — codifies Phase 1's remaining housekeeping (disable enterprise repo, enable no-subscription repo, `apt full-upgrade`) under the `repos` tag, and joining the Proxmox host to the tailnet under the `tailscale` tag, non-interactively via a runtime-supplied auth key.
- Kubernetes manifests for Traefik (ClusterIP-only ingress), cert-manager + Cloudflare DNS-01 ClusterIssuers, `cloudflared` (Cloudflare Tunnel) with a locked-down NetworkPolicy, the Tailscale Kubernetes Operator, and a `whoami` example app for end-to-end validation.
- `GUIDE.md` — full phase-by-phase build guide with the reasoning behind every architectural decision.
- `claude.md` — durable context and decisions for AI-assisted work in this repo.

### Changed
- Terraform layout split by concern: `main.tf` → `server.tf` (control-plane singleton) + `workers.tf` (one resource expanded over a `workers` map with `for_each`). Adding a worker is now a map entry in `terraform.tfvars` plus an Ansible inventory entry, not a copy-pasted resource block (ADR-0019).

### Removed
- SOPS + age secrets scaffolding (`.sops.yaml`, `secrets/`, the worked-example encrypted Secret manifest). Deferred by design for v1 — Kubernetes Secrets are created imperatively via `kubectl create secret` and are not committed to Git in any form for now. See `claude.md` and `ROADMAP.md`.
