# Architecture Decision Records

> Back to [[Homelab Learning Map]]

Architecture decisions for this platform, in [Nygard ADR format](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions) (Status / Context / Decision / Consequences). ADRs are grouped into one log per release milestone (matching the [GitHub Milestones](../../../milestones) — v0.1 … v2.0), each log opening with a short narrative. ADR numbers are stable and append-only — a decision is never edited away, only superseded by a later ADR.

The Ansible/Terraform/Kubernetes/platform *mechanics* these decisions rely on live separately in [[Ansible Concepts]], [[Terraform Concepts]], [[Kubernetes Concepts]], and [[Platform Concepts]].

## Logs

- [[v0.1 - Foundation]] — ADR-0001 … ADR-0007, ADR-0019 … ADR-0024, ADR-0049 … ADR-0050
- [[v0.2 - Cluster Bootstrap]] — ADR-0008 … ADR-0012
- [[v0.3 - Ingress and TLS]] — ADR-0013 … ADR-0015, ADR-0027
- [[v0.4 - Public and Private Access]] — ADR-0016 … ADR-0018, ADR-0025 … ADR-0026, ADR-0028 … ADR-0029
- [[v2.0 - Operability]] — ADR-0030 … ADR-0048, ADR-0051 … ADR-0053
- [[v4.0 - Developer Workspace]] — ADR-0054 … ADR-0064

(0019–0021 live in the v0.1 log despite the number gap: 0019 records the
original tried-and-reverted refactor and is now superseded by 0053 rather than
reused; the decisions themselves belong to Foundation's provisioning scope.)

v1.0 has no log — it produced no architecture decisions of its own, only validation of decisions already captured above. The v2.0 log opened with the Longhorn phase.

## Index

| ADR | Title | Status | Log |
|-----|-------|--------|-----|
| 0001 | Split provisioning (Terraform) from configuration (Ansible) | Accepted | [[v0.1 - Foundation]] |
| 0002 | Pin versions deliberately across the stack | Accepted | [[v0.1 - Foundation]] |
| 0003 | Defer secrets management for v1 | Accepted | [[v0.1 - Foundation]] |
| 0004 | Automate Proxmox host housekeeping and the Tailscale host join via Ansible | Accepted | [[v0.1 - Foundation]] |
| 0005 | Split Proxmox host automation into two separate roles/playbooks | Superseded by 0006 | [[v0.1 - Foundation]] |
| 0006 | Merge Proxmox host automation into one role with tagged task files | Superseded by 0050 | [[v0.1 - Foundation]] |
| 0007 | Inventory must use portable, resolvable connection targets | Accepted | [[v0.1 - Foundation]] |
| 0008 | Use embedded etcd instead of SQLite | Accepted | [[v0.2 - Cluster Bootstrap]] |
| 0009 | Enable secrets-encryption at rest | Accepted | [[v0.2 - Cluster Bootstrap]] |
| 0010 | Taint the control-plane node against application workloads | Accepted | [[v0.2 - Cluster Bootstrap]] |
| 0011 | Flannel + kube-proxy on defaults; defer Cilium | Accepted | [[v0.2 - Cluster Bootstrap]] |
| 0012 | Disable k3s's bundled Traefik and ServiceLB | Accepted | [[v0.2 - Cluster Bootstrap]] |
| 0013 | Traefik Service is ClusterIP only | Accepted | [[v0.3 - Ingress and TLS]] |
| 0014 | Use Traefik's native IngressRoute CRD instead of Gateway API | Accepted | [[v0.3 - Ingress and TLS]] |
| 0015 | cert-manager serves internal/Tailscale-only TLS, not the public path | Accepted | [[v0.3 - Ingress and TLS]] |
| 0016 | Cloudflare Tunnel with exactly one route to Traefik | Accepted | [[v0.4 - Public and Private Access]] |
| 0017 | Lock down cloudflared's egress via NetworkPolicy | Accepted | [[v0.4 - Public and Private Access]] |
| 0018 | Tailscale as two separate mechanisms | Accepted | [[v0.4 - Public and Private Access]] |
| 0025 | Tailscale Kubernetes Operator's OAuth client needs three scopes, not one | Accepted | [[v0.4 - Public and Private Access]] |
| 0026 | Grant Tailscale-authenticated users cluster-admin via RBAC | Accepted | [[v0.4 - Public and Private Access]] |
| 0027 | Pin the Traefik Helm chart version | Accepted | [[v0.3 - Ingress and TLS]] |
| 0028 | Public hostnames use specific first-level names during migration (Universal SSL limit) | Accepted | [[v0.4 - Public and Private Access]] |
| 0029 | Internal DNS via wildcard CNAME to the proxy's pinned MagicDNS name | Accepted | [[v0.4 - Public and Private Access]] |
| 0019 | Workers as a for_each map | Superseded by 0053 | [[v0.1 - Foundation]] |
| 0020 | Near-full host allocation: overcommit CPU, never RAM | Accepted | [[v0.1 - Foundation]] |
| 0021 | Distributed storage direction: data disks now, Longhorn later | Accepted | [[v0.1 - Foundation]] |
| 0022 | Internal NVMe strictly off-limits; everything on the external SSD | Accepted | [[v0.1 - Foundation]] |
| 0023 | terraform@pve needs a storage-scoped ACL, not just PVEVMAdmin | Accepted | [[v0.1 - Foundation]] |
| 0024 | terraform@pve also needs an SDN-scoped ACL for network attach | Accepted | [[v0.1 - Foundation]] |
| 0030 | Data-replica policy as StorageClass tiers (longhorn = 1 dev, longhorn-replicated = 2 prod) | Superseded by 0051 | [[v2.0 - Operability]] |
| 0031 | Longhorn's StorageClasses are not the cluster default | Accepted | [[v2.0 - Operability]] |
| 0032 | Keep the upstream-conventional longhorn-system namespace | Accepted | [[v2.0 - Operability]] |
| 0033 | Longhorn UI exposed internal-only via the Tailscale front door | Accepted | [[v2.0 - Operability]] |
| 0034 | Storage tier for the monitoring stack: longhorn (1-copy, dev tier) | Accepted | [[v2.0 - Operability]] |
| 0035 | Deterministic Helm naming instead of post-install discovery | Accepted | [[v2.0 - Operability]] |
| 0036 | Grafana and Prometheus exposed internal-only; Grafana's login removed entirely | Accepted | [[v2.0 - Operability]] |
| 0037 | One Grafana only; Loki wired in as a provisioned datasource | Accepted | [[v2.0 - Operability]] |
| 0038 | Alloy runs as a single Deployment (API-based log collection), not a DaemonSet | Accepted | [[v2.0 - Operability]] |
| 0039 | Prometheus discovers ServiceMonitors/PodMonitors cluster-wide, corrected from a scoped default | Accepted | [[v2.0 - Operability]] |
| 0040 | k3s control-plane component metrics exposed via k3s server args, scraped through hand-authored Endpoints | Accepted | [[v2.0 - Operability]] |
| 0041 | Third-party Grafana dashboards imported via labeled ConfigMap, not manual UI import | Accepted | [[v2.0 - Operability]] |
| 0042 | ArgoCD bootstrapped via Helm, then self-managed via the "app of apps" pattern | Accepted | [[v2.0 - Operability]] |
| 0043 | ArgoCD exposed internal-only; TLS terminated at Traefik, not argocd-server itself | Accepted | [[v2.0 - Operability]] |
| 0044 | Existing apps migrate to ArgoCD one at a time, via multi-source Applications, manual sync before automated | Accepted | [[v2.0 - Operability]] |
| 0045 | Argo Rollouts added as a GitOps-native app; no bootstrap paradox, nothing to adopt | Accepted | [[v2.0 - Operability]] |
| 0046 | Complete the GitOps migration: all remaining apps + companions, one at a time; tailscale-operator deferred on secrets policy | Superseded in part by 0048 | [[v2.0 - Operability]] |
| 0047 | whoami repurposed as the Rollout exercise app; Traefik API group pinned; canary swapped for blueGreen | Accepted | [[v2.0 - Operability]] |
| 0048 | Adopt Tailscale Operator into GitOps while keeping OAuth credentials imperative | Accepted | [[v2.0 - Operability]] |
| 0049 | Grow one Proxmox cluster across physical hosts; accept the temporary two-node quorum limit | Accepted | [[v0.1 - Foundation]] |
| 0050 | Organize Ansible roles by lifecycle ownership | Accepted | [[v0.1 - Foundation]] |
| 0051 | One Longhorn data copy by default; redundancy is an explicit per-volume promotion | Accepted | [[v2.0 - Operability]] |
| 0052 | ASRock HDD becomes a separate Proxmox-managed datastore | Accepted | [[v2.0 - Operability]] |
| 0053 | One parameterized lifecycle for every worker | Accepted | [[v2.0 - Operability]] |
| 0054 | One flat `workbench` namespace holding the devbox and every project's app containers | Accepted (amended: placement, port blocks) | [[v4.0 - Developer Workspace]] |
| 0055 | RWO Longhorn volume for the workspace; RWX rejected on small-file performance | Accepted (amended: `dataLocality`) | [[v4.0 - Developer Workspace]] |
| 0056 | Credential-free git transport over `ext::kubectl exec` with `updateInstead` | Not implemented — superseded in practice by 0064 agent forwarding (#61) | [[v4.0 - Developer Workspace]] |
| 0057 | No build abstraction; commands run directly in the shell | Accepted | [[v4.0 - Developer Workspace]] |
| 0058 | Mounted-source hot reload instead of image rebuilds in the inner loop | Accepted (planned — #64) | [[v4.0 - Developer Workspace]] |
| 0059 | In-cluster `registry:2` instead of ghcr.io; containerd cannot read images from a PVC | Accepted (amended: NodePort, public pulls) | [[v4.0 - Developer Workspace]] |
| 0060 | The in-cluster builder is dev-only and never produces images Argo CD deploys | Accepted | [[v4.0 - Developer Workspace]] |
| 0061 | `workbench` under Argo CD with manual sync and `/spec/replicas` ignored | Accepted (amended: infra only, no prune) | [[v4.0 - Developer Workspace]] |
| 0062 | `homelab-k8s` and `homelab` excluded from in-cluster development | Accepted | [[v4.0 - Developer Workspace]] |
| 0063 | No backups for the workspace volume; GitHub is the backup | Accepted | [[v4.0 - Developer Workspace]] |
| 0064 | Tailscale SSH in the devbox container, not a Service on port 22 | Accepted (amended: in-container, not sidecar) | [[v4.0 - Developer Workspace]] |
| 0065 | The devbox holds namespace-admin on `workbench`, secrets included | Accepted | [[v4.0 - Developer Workspace]] |
| 0066 | `devx` discovers workloads by label; lifecycle lives in the manifest | Accepted | [[v4.0 - Developer Workspace]] |
