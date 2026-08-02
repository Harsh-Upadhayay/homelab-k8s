# Homelab Kubernetes Platform — Context for Claude Code

This file is read automatically at the start of every session. It captures decisions already made and argued through — don't re-derive or second-guess them without the user explicitly raising it.

## What this is

A production-shaped homelab Kubernetes platform built for hands-on operational learning (etcd internals, networking, GitOps, storage, and observability) — not just to get something running. Correctness and understanding are prioritized over the fastest path. See `GUIDE.md` for the foundation build and `ROADMAP.md` for the layers that landed afterward. ADR-0049's two-node Proxmox topology is live: cluster `neovara` contains `pve-dell` and `pve-asrock`. One parameterized Terraform resource provisions both workers; the same Ansible roles configure both. Each worker receives a Proxmox-managed data disk that the shared `longhorn_node` role mounts at `/var/lib/longhorn`. A third Proxmox node is planned one to two months later.

## Architecture

```
Proxmox cluster neovara
├── pve-dell: laptop, 14 threads / 30GiB / 816GiB thin pool on an EXTERNAL 1TB USB SSD
│   ├── k3s-server-1   4c/6GiB,  60GB          control plane, tainted, embedded etcd
│   └── k3s-worker-1   12c/18GiB, 60GB + 650GB application workloads + data disk
└── pve-asrock: patched I219-V NIC; motherboard SSD hosts Proxmox + worker OS
    └── k3s-worker-3   6c/12GiB, 40GB + 1300GB application workloads + HDD data disk
```

**HARD CONSTRAINT (ADR-0022): the laptop's internal NVMe (`nvme0n1`, Samsung 1TB) holds Windows and the user's personal data. It is STRICTLY off-limits — never add it as a storage pool, LVM PV, mount, or passthrough target, never suggest using it "for etcd performance" or "free space." The external USB SSD (`sda`) is the only working storage. Capacity grows by adding physical nodes later, never by touching that disk.**

**HARD CONSTRAINT (`pve-asrock` NIC): do not upgrade or reinstall ASRock's Proxmox kernel.** Its Intel I219-V works through an ABI-specific unsigned e1000e patch for `7.0.2-6-pve`. The host deliberately holds `proxmox-default-kernel`, `proxmox-kernel-7.0`, and `proxmox-kernel-7.0.2-6-pve-signed`; `apt-get -s dist-upgrade` must show no kernel install/removal before an upgrade is approved. Do not remove those holds until a matching replacement module has been rebuilt, tested on the physical NIC, and local console recovery is available. See `docs/troubleshooting/proxmox-ve-9.2-1-i219v-recovery/README.md`.

Provisioning is split deliberately: **Terraform** (`terraform/`) provisions the VMs, **Ansible** (`ansible/`) configures the OS and installs k3s. Different problems (infra existence vs. configuration state), different tools — don't collapse them into one.

**CURRENT HYPERVISOR TOPOLOGY (ADR-0049): one Proxmox cluster, not independent hosts.** Cluster `neovara` was created on `pve-dell`; `pve-asrock` joined on 2026-07-23 and now hosts `k3s-worker-3`. Keep one Terraform provider and cluster-wide token; select placement with each VM's `node_name`. The temporary two-node stage intentionally accepts that losing either member makes `pmxcfs` read-only and delays cold-start `onboot` guests until quorum returns. Running guests continue. A third member is planned soon. Do not add a QDevice unless availability requirements change.

Sizing rule (ADR-0020): CPU is mildly overcommitted (16 vCPU on 14 threads — vCPUs are schedulable threads); **RAM is never overcommitted** (24 of 30GiB allocated, ~5GiB host reserve) because a host OOM kill against the server VM kills etcd and the cluster with it.

## Decisions already made — do not silently change

The formal, numbered record of these (Status/Context/Decision/Consequences, with reversals tracked via "Superseded by") lives in `docs/adr/` — this section is the fast-reading summary for AI context loading, that's the durable version.

- **Embedded etcd via `cluster-init: true`**, not SQLite — even at a single server node. This is what enables real etcd snapshot/restore/inspection and a clean path to a 3-node HA quorum later.
- **Preferred control-plane placement after Immich recovery: move the existing `k3s-server-1` VM to the workstation**, subject to the 119GiB SSD capacity check and a zero-replacement migration plan. Do not leave exactly two embedded-etcd servers as fake HA; K3s HA requires an odd member count, normally three. Moving the API does not provide Dell-outage app continuity until critical Longhorn volumes also have healthy workstation replicas.
- **`secrets-encryption: true`** — Kubernetes Secrets encrypted at rest in etcd, not just base64.
- **Control-plane taint** (`node-role.kubernetes.io/control-plane:NoSchedule`) on k3s-server-1 — app pods must never schedule there.
- **Flannel + kube-proxy on defaults — Cilium is deliberately deferred.** This was an explicit, reasoned tradeoff: start on the simplest CNI, learn the platform layers first, adopt Cilium later as its own project. **This is the one non-additive item in the whole design** — Flannel → Cilium is not a live migration, it requires a full cluster rebuild. Don't suggest switching CNIs casually; if it comes up, flag that it means a rebuild, not a config change.
- **Traefik is `type: ClusterIP`, never LoadBalancer/NodePort.** No MetalLB, no Cilium LB-IPAM. cloudflared reaches Traefik entirely inside the cluster, so no LAN LoadBalancer IP is needed anywhere in this design.
- **Cloudflare Tunnel (cloudflared), not port-forwarding or a direct LoadBalancer path.** During the legacy migration window, each public first-level hostname is a separate Cloudflare tunnel entry, but every entry targets the same `http://traefik.traefik.svc.cluster.local:80` Service (not container port 8000); Traefik `IngressRoute` objects perform the actual per-app routing. After the old `*.neovara.uk` route is retired, ADR-0028's end state is one wildcard tunnel hostname pointing at that same Service.
- **cloudflared's egress is locked down by NetworkPolicy** to Traefik + DNS + Cloudflare's edge only. Don't remove this "to simplify" — it's the fix for cloudflared otherwise being able to reach every Service in every namespace by default.
- **Tailscale is two separate mechanisms, not one**: `tailscaled` installed directly on hosts by the shared `ansible/roles/tailscale_host` role (Proxmox hypervisors + each k3s node, for UI + SSH access) + the Tailscale Kubernetes Operator in-cluster (API server proxy for `kubectl`, `loadBalancerClass: tailscale` for exposing dashboards). Don't conflate them.
- **Two SSH key pairs, by design — don't cross them.** Proxmox host = `root` + `~/.ssh/proxmox_ed25519` (installed by a manual `ssh-copy-id` bootstrap); k3s nodes = `harsh` + `~/.ssh/id_ed25519` (installed by Terraform cloud-init's `ssh_public_key` at clone time). Using one against the other gives `Permission denied (publickey)` — that's expected. Both private keys live only where generated; migrating machines means copying them across first.
- **IngressRoute (Traefik's native CRD), not Gateway API**, for routing — chosen to avoid installing a second CRD set while the rest of the stack is still being learned. Migrating later is a config change, not a rebuild, so this can move if asked.
- **Versions are pinned deliberately everywhere** (k3s, Terraform provider, Helm charts, cert-manager, cloudflared). Reproducibility means "the version that was tested," not "whatever's latest today." If bumping a version, do it as a conscious, explicit action — check the project's releases page first, don't silently float to `latest`.
- **Longhorn is live on the ADR-0021/0052 disk layout.** Every worker receives a dedicated Proxmox-managed `scsi1`; the shared `longhorn_node` role formats it as ext4 label `k3s-data`, mounts it at `/var/lib/longhorn`, and orders k3s after the mount. Worker 1's disk is on Dell `local-lvm`; worker 3's is on ASRock's independent HDD-backed `longhorn-hdd` datastore. One `for_each` Terraform resource owns both workers, with placement and capacity expressed only as parameters (ADR-0053). Immich recovery history and acceptance evidence remain in `docs/migrations/immich.md`.
- **GitOps (Argo CD) is live across the platform and migrated apps.** It was bootstrapped once with Helm, then made self-managing through app-of-apps (ADR-0042). Platform charts/companions, Tailscale Operator, and the migrated Audiobookshelf/Nextcloud/Immich/Kiroku resources are represented by child Applications; runtime Secrets remain imperative and deliberately untracked. Always check `kubectl get applications -n argocd` for current health instead of copying a historical Application count. Argo Rollouts' canary and blue/green mechanics were exercised live; the committed `whoami` example is blue/green (ADR-0047).
- **Secrets management is live via External Secrets Operator (ESO) + AWS SSM Parameter Store** — a deliberate deviation from issue #17's SOPS-first plan (went straight to ESO+SSM, the dominant industry pattern for the cloud/K8s secrets problem; no ADR by the user's explicit choice). Flow: an `ExternalSecret` in a consumer namespace references the `ClusterSecretStore` named `aws-parameter-store` (namespace `external-secrets`), which authenticates as IAM user `neovara-k8s-eso` assuming role `neovara-k8s-eso-ssm` — the role holds `ssm:GetParameter*` on `/neovara/<tier>/<app>/<key>` (tier ∈ {dev, prod, homeinfra}) + `kms:Decrypt` on AWS-managed `alias/aws/ssm`. The user's static access key is the ONE imperative bootstrap Secret, `external-secrets/aws-creds`; every app secret then flows SSM→ESO→K8s. Terraform owns the AWS side (`terraform/aws/`); secret values use `value_wo` (write-only, never persisted to state, paired with ephemeral TF vars) and non-secrets use `insecure_value`. The store is gated by `conditions` to namespaces labeled `neovara-external-secrets=true` (opt-in per app). `refreshInterval: "0"` means manual refresh only — annotate the ExternalSecret to force a re-sync. First migrated: `nextcloud-db`; the remaining `.env` secrets (K3S_TOKEN, tailscale keys, IMMICH_LOGIN) follow the same pattern.

## Explicitly deferred — do not add unless asked

Full backup strategy (off-box etcd shipping, Velero, Proxmox Backup Server), Cilium, HA control plane (`k3s-server-2`/`-3`), and additional unplanned workers. Each is staged as a clean follow-on and none should be silently bootstrapped while working on something else. The existing control-plane VM move is issue #49. (Longhorn, monitoring/logging, GitOps/Argo CD, secrets management, Tailscale's GitOps adoption, the ASRock worker, and post-Immich storage convergence were all once deferred and have since landed.)

## Working style for this repo

- The user is learning Kubernetes hands-on and wants to understand every component, not just have it work — prefer explaining *why* a change is correct over just making it, especially for anything touching the decisions above.
- Code-review workflow when the user writes Terraform/K8s code and asks for a check: fix spelling/syntax and minor issues directly, flag anything big separately, then follow up with a concise brief of the changes that teaches the underlying concept or root cause (e.g. HCL block-vs-attribute, or *why* an S3 bucket name can't have underscores). The user writes the code for the learning value; opencode guides, fixes, and explains — avoid handing over copy-paste solutions when the user is in learning mode. Trivial scaffolding/mechanical moves (gitignore, file relocations) may be done directly to keep velocity.
- Prefer custom, minimal, readable config (as already done for the Ansible roles) over pulling in third-party roles/charts that hide what's actually happening, unless the third-party option is clearly the standard and inspectable.
- Keep manifests commented the way they already are in this repo — the comments carry the reasoning, not just the "what."
- Treat qualifying outages and near misses as learning artifacts. After recovery, create a blameless
  report from `docs/incidents/TEMPLATE.md`, add it to `docs/incidents/README.md`, distinguish
  observed facts from hypotheses, and track preventive work as Open until completion is verified.
