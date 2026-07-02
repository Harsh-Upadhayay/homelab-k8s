# Homelab Kubernetes Platform — Context for Claude Code

This file is read automatically at the start of every session. It captures decisions already made and argued through — don't re-derive or second-guess them without the user explicitly raising it.

## What this is

A production-shaped homelab Kubernetes platform on a single Proxmox host, built for hands-on operational learning (etcd internals, networking, GitOps eventually) — not just to get something running. Correctness and understanding are prioritized over the fastest path. See `GUIDE.md` for the full phase-by-phase build.

## Architecture

```
Proxmox host
├── k3s-server-1   control plane, tainted (no app workloads), embedded etcd
└── k3s-worker-1   application workloads
```

Provisioning is split deliberately: **Terraform** (`terraform/`) provisions the VMs, **Ansible** (`ansible/`) configures the OS and installs k3s. Different problems (infra existence vs. configuration state), different tools — don't collapse them into one.

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

## Explicitly deferred — do not add unless asked

**Secrets management (SOPS + age, and later ESO/Vault)** — deferred. Kubernetes Secrets are created imperatively (`kubectl create secret ...`) and are **not committed to git in any form**, encrypted or otherwise, for now. Don't introduce `.sops.yaml`, age keys, or "encrypt this secret" workflows unless the user asks — if a manifest needs a Secret value, ask for it or have the user apply it directly rather than writing it into a tracked file.

GitOps (Argo CD/Flux), monitoring (kube-prometheus-stack), logging (Loki + Alloy), full backup strategy (off-box etcd shipping, Velero, Proxmox Backup Server), Cilium, HA control plane (`k3s-server-2`/`-3`), additional workers. Each is staged as a clean, additive follow-on — none of them are missing by accident, and none should be silently bootstrapped while working on something else.

## Working style for this repo

- The user is learning Kubernetes hands-on and wants to understand every component, not just have it work — prefer explaining *why* a change is correct over just making it, especially for anything touching the decisions above.
- Prefer custom, minimal, readable config (as already done for the Ansible roles) over pulling in third-party roles/charts that hide what's actually happening, unless the third-party option is clearly the standard and inspectable.
- Keep manifests commented the way they already are in this repo — the comments carry the reasoning, not just the "what."