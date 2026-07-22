# Homelab Kubernetes Platform — Context for Claude Code

This file is read automatically at the start of every session. It captures decisions already made and argued through — don't re-derive or second-guess them without the user explicitly raising it.

## What this is

A production-shaped homelab Kubernetes platform whose current baseline runs on one Proxmox host, built for hands-on operational learning (etcd internals, networking, GitOps, storage, and observability) — not just to get something running. Correctness and understanding are prioritized over the fastest path. See `GUIDE.md` for the foundation build and `ROADMAP.md` for the layers that landed afterward. The preserved Immich-disk recovery adds a second Proxmox host and worker VM, but that host/VM is not represented in Terraform or Ansible yet; follow `docs/migrations/immich.md` rather than inventing a bare-metal worker conversion.

## Architecture

```
Proxmox host (pve-dell: laptop, 14 threads / 30GiB / 816GiB thin pool on an EXTERNAL 1TB USB SSD)
├── k3s-server-1   4c/6GiB,  60GB          control plane, tainted (no app workloads), embedded etcd
├── k3s-worker-1   6c/9GiB,  60GB + 280GB  application workloads + data disk
└── k3s-worker-2   6c/9GiB,  60GB + 280GB  identical twin — makes rescheduling/storage node-agnosticism observable
```

**HARD CONSTRAINT (ADR-0022): the laptop's internal NVMe (`nvme0n1`, Samsung 1TB) holds Windows and the user's personal data. It is STRICTLY off-limits — never add it as a storage pool, LVM PV, mount, or passthrough target, never suggest using it "for etcd performance" or "free space." The external USB SSD (`sda`) is the only working storage. Capacity grows by adding physical nodes later, never by touching that disk.**

Provisioning is split deliberately: **Terraform** (`terraform/`) provisions the VMs, **Ansible** (`ansible/`) configures the OS and installs k3s. Different problems (infra existence vs. configuration state), different tools — don't collapse them into one.

Sizing rule (ADR-0020): CPU is mildly overcommitted (16 vCPU on 14 threads — vCPUs are schedulable threads); **RAM is never overcommitted** (24 of 30GiB allocated, ~5GiB host reserve) because a host OOM kill against the server VM kills etcd and the cluster with it.

## Decisions already made — do not silently change

The formal, numbered record of these (Status/Context/Decision/Consequences, with reversals tracked via "Superseded by") lives in `docs/adr/` — this section is the fast-reading summary for AI context loading, that's the durable version.

- **Embedded etcd via `cluster-init: true`**, not SQLite — even at a single server node. This is what enables real etcd snapshot/restore/inspection and a clean path to a 3-node HA quorum later.
- **`secrets-encryption: true`** — Kubernetes Secrets encrypted at rest in etcd, not just base64.
- **Control-plane taint** (`node-role.kubernetes.io/control-plane:NoSchedule`) on k3s-server-1 — app pods must never schedule there.
- **Flannel + kube-proxy on defaults — Cilium is deliberately deferred.** This was an explicit, reasoned tradeoff: start on the simplest CNI, learn the platform layers first, adopt Cilium later as its own project. **This is the one non-additive item in the whole design** — Flannel → Cilium is not a live migration, it requires a full cluster rebuild. Don't suggest switching CNIs casually; if it comes up, flag that it means a rebuild, not a config change.
- **Traefik is `type: ClusterIP`, never LoadBalancer/NodePort.** No MetalLB, no Cilium LB-IPAM. cloudflared reaches Traefik entirely inside the cluster, so no LAN LoadBalancer IP is needed anywhere in this design.
- **Cloudflare Tunnel (cloudflared), not port-forwarding or a direct LoadBalancer path.** During the legacy migration window, each public first-level hostname is a separate Cloudflare tunnel entry, but every entry targets the same `http://traefik.traefik.svc.cluster.local:80` Service (not container port 8000); Traefik `IngressRoute` objects perform the actual per-app routing. After the old `*.neovara.uk` route is retired, ADR-0028's end state is one wildcard tunnel hostname pointing at that same Service.
- **cloudflared's egress is locked down by NetworkPolicy** to Traefik + DNS + Cloudflare's edge only. Don't remove this "to simplify" — it's the fix for cloudflared otherwise being able to reach every Service in every namespace by default.
- **Tailscale is two separate mechanisms, not one**: `tailscaled` installed directly on hosts (Proxmox hypervisor + each k3s node — shared `ansible/tasks/tailscale.yml`, imported by the `proxmox_host` and `common` roles, for UI + SSH access) + the Tailscale Kubernetes Operator in-cluster (API server proxy for `kubectl`, `loadBalancerClass: tailscale` for exposing dashboards). Don't conflate them.
- **Two SSH key pairs, by design — don't cross them.** Proxmox host = `root` + `~/.ssh/proxmox_ed25519` (installed by a manual `ssh-copy-id` bootstrap); k3s nodes = `harsh` + `~/.ssh/id_ed25519` (installed by Terraform cloud-init's `ssh_public_key` at clone time). Using one against the other gives `Permission denied (publickey)` — that's expected. Both private keys live only where generated; migrating machines means copying them across first.
- **IngressRoute (Traefik's native CRD), not Gateway API**, for routing — chosen to avoid installing a second CRD set while the rest of the stack is still being learned. Migrating later is a config change, not a rebuild, so this can move if asked.
- **Versions are pinned deliberately everywhere** (k3s, Terraform provider, Helm charts, cert-manager, cloudflared). Reproducibility means "the version that was tested," not "whatever's latest today." If bumping a version, do it as a conscious, explicit action — check the project's releases page first, don't silently float to `latest`.
- **Longhorn is live on the ADR-0021 disk layout.** Each permanent worker carries a dedicated `scsi1` ext4 data disk mounted at `/var/lib/longhorn`, separate from its OS disk. Workers are explicit named Terraform blocks, not a `for_each` map — that was tried and reverted (ADR-0019); revisit when the permanent fleet grows. Honest caveat: both permanent workers' virtual disks share the same external physical USB SSD, so two Longhorn replicas protect against VM/node failure but not failure of that physical device. Immich's retained single replica is a separate preservation case; its disk UUID, selectors, and recovery order are in `docs/migrations/immich.md`.
- **GitOps (Argo CD) is live across the platform and migrated apps.** It was bootstrapped once with Helm, then made self-managing through app-of-apps (ADR-0042). Platform charts/companions, Tailscale Operator, and the migrated Audiobookshelf/Nextcloud/Immich/Kiroku resources are represented by child Applications; runtime Secrets remain imperative and deliberately untracked. Always check `kubectl get applications -n argocd` for current health instead of copying a historical Application count. Argo Rollouts' canary and blue/green mechanics were exercised live; the committed `whoami` example is blue/green (ADR-0047).

## Explicitly deferred — do not add unless asked

**Secrets management (SOPS + age, and later ESO/Vault)** — deferred. Kubernetes Secrets are created imperatively (`kubectl create secret ...`) and are **not committed to git in any form**, encrypted or otherwise, for now. Don't introduce `.sops.yaml`, age keys, or "encrypt this secret" workflows unless the user asks — if a manifest needs a Secret value, ask for it or have the user apply it directly rather than writing it into a tracked file.

Full backup strategy (off-box etcd shipping, Velero, Proxmox Backup Server), Cilium, HA control plane (`k3s-server-2`/`-3`), and unplanned workers beyond the two already provisioned. Each is staged as a clean follow-on and none should be silently bootstrapped while working on something else. The explicitly planned exception is the second-host worker required to recover Immich; even that must first be added to Terraform and Ansible using the existing standards. (Longhorn, monitoring/logging, GitOps/Argo CD, and Tailscale's GitOps adoption were all once deferred and have since landed.)

## Working style for this repo

- The user is learning Kubernetes hands-on and wants to understand every component, not just have it work — prefer explaining *why* a change is correct over just making it, especially for anything touching the decisions above.
- Prefer custom, minimal, readable config (as already done for the Ansible roles) over pulling in third-party roles/charts that hide what's actually happening, unless the third-party option is clearly the standard and inspectable.
- Keep manifests commented the way they already are in this repo — the comments carry the reasoning, not just the "what."
