# Homelab Kubernetes Platform — Context for Claude Code

This file is read automatically at the start of every session. It captures decisions already made and argued through — don't re-derive or second-guess them without the user explicitly raising it.

## What this is

A production-shaped homelab Kubernetes platform on a single Proxmox host, built for hands-on operational learning (etcd internals, networking, GitOps eventually) — not just to get something running. Correctness and understanding are prioritized over the fastest path. See `GUIDE.md` for the full phase-by-phase build.

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
- **Cloudflare Tunnel (cloudflared), not port-forwarding or a direct LoadBalancer path.** The tunnel has exactly one route: wildcard hostname → `http://traefik.traefik.svc.cluster.local:8000`. Per-app routing happens entirely via Traefik IngressRoute objects after that — never by adding more tunnel routes.
- **cloudflared's egress is locked down by NetworkPolicy** to Traefik + DNS + Cloudflare's edge only. Don't remove this "to simplify" — it's the fix for cloudflared otherwise being able to reach every Service in every namespace by default.
- **Tailscale is two separate mechanisms, not one**: `tailscaled` directly on the Proxmox host (hypervisor UI, node-level access) + the Tailscale Kubernetes Operator in-cluster (API server proxy for `kubectl`, `loadBalancerClass: tailscale` for exposing dashboards). Don't conflate them.
- **IngressRoute (Traefik's native CRD), not Gateway API**, for routing — chosen to avoid installing a second CRD set while the rest of the stack is still being learned. Migrating later is a config change, not a rebuild, so this can move if asked.
- **Versions are pinned deliberately everywhere** (k3s, Terraform provider, Helm charts, cert-manager, cloudflared). Reproducibility means "the version that was tested," not "whatever's latest today." If bumping a version, do it as a conscious, explicit action — check the project's releases page first, don't silently float to `latest`.
- **Distributed storage direction is Longhorn, shaped now, installed later (ADR-0021).** Every worker carries a dedicated data disk (`scsi1`, ext4, label `k3s-data`, mounted at `/var/lib/longhorn` by the `k3s_agent` role) kept separate from the OS disk. Workers are explicit named Terraform blocks, not a `for_each` map — that was tried and reverted (ADR-0019); revisit at 3+ workers. Honest caveat: both workers share one physical NVMe, so replication between them simulates the topology without providing durability until real nodes arrive.

## Explicitly deferred — do not add unless asked

**Secrets management (SOPS + age, and later ESO/Vault)** — deferred. Kubernetes Secrets are created imperatively (`kubectl create secret ...`) and are **not committed to git in any form**, encrypted or otherwise, for now. Don't introduce `.sops.yaml`, age keys, or "encrypt this secret" workflows unless the user asks — if a manifest needs a Secret value, ask for it or have the user apply it directly rather than writing it into a tracked file.

GitOps (Argo CD/Flux), monitoring (kube-prometheus-stack), logging (Loki + Alloy), full backup strategy (off-box etcd shipping, Velero, Proxmox Backup Server), Cilium, HA control plane (`k3s-server-2`/`-3`), workers beyond the two already provisioned, and the **Longhorn install itself** (the disks and mounts are already shaped for it per ADR-0021 — but don't helm-install Longhorn until asked; it's a v2.0 phase). Each is staged as a clean, additive follow-on — none of them are missing by accident, and none should be silently bootstrapped while working on something else.

## Working style for this repo

- The user is learning Kubernetes hands-on and wants to understand every component, not just have it work — prefer explaining *why* a change is correct over just making it, especially for anything touching the decisions above.
- Prefer custom, minimal, readable config (as already done for the Ansible roles) over pulling in third-party roles/charts that hide what's actually happening, unless the third-party option is clearly the standard and inspectable.
- Keep manifests commented the way they already are in this repo — the comments carry the reasoning, not just the "what."