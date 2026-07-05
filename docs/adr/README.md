# Architecture Decision Records

> Back to [[Homelab Learning Map]]

Architecture decisions for this platform, in [Nygard ADR format](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions) (Status / Context / Decision / Consequences). ADRs are grouped into one log per release milestone (matching the [GitHub Milestones](../../../milestones) — v0.1 … v2.0), each log opening with a short narrative. ADR numbers are stable and append-only — a decision is never edited away, only superseded by a later ADR.

The Ansible/Terraform/Kubernetes/platform *mechanics* these decisions rely on live separately in [[Ansible Concepts]], [[Terraform Concepts]], [[Kubernetes Concepts]], and [[Platform Concepts]].

## Logs

- [[v0.1 - Foundation]] — ADR-0001 … ADR-0007, ADR-0019 … ADR-0024
- [[v0.2 - Cluster Bootstrap]] — ADR-0008 … ADR-0012
- [[v0.3 - Ingress and TLS]] — ADR-0013 … ADR-0015, ADR-0027
- [[v0.4 - Public and Private Access]] — ADR-0016 … ADR-0018, ADR-0025 … ADR-0026, ADR-0028 … ADR-0029
- [[v2.0 - Operability]] — ADR-0030 … ADR-0033

(0019–0021 live in the v0.1 log despite the number gap: 0019 was burned by a tried-and-reverted refactor and is reinstated as Rejected rather than reused, and the decisions themselves belong to Foundation's provisioning scope.)

v1.0 has no log — it produced no architecture decisions of its own, only validation of decisions already captured above. The v2.0 log opened with the Longhorn phase.

## Index

| ADR | Title | Status | Log |
|-----|-------|--------|-----|
| 0001 | Split provisioning (Terraform) from configuration (Ansible) | Accepted | [[v0.1 - Foundation]] |
| 0002 | Pin versions deliberately across the stack | Accepted | [[v0.1 - Foundation]] |
| 0003 | Defer secrets management for v1 | Accepted | [[v0.1 - Foundation]] |
| 0004 | Automate Proxmox host housekeeping and the Tailscale host join via Ansible | Accepted | [[v0.1 - Foundation]] |
| 0005 | Split Proxmox host automation into two separate roles/playbooks | Superseded by 0006 | [[v0.1 - Foundation]] |
| 0006 | Merge Proxmox host automation into one role with tagged task files | Accepted | [[v0.1 - Foundation]] |
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
| 0019 | Workers as a for_each map | Rejected | [[v0.1 - Foundation]] |
| 0020 | Near-full host allocation: overcommit CPU, never RAM | Accepted | [[v0.1 - Foundation]] |
| 0021 | Distributed storage direction: data disks now, Longhorn later | Accepted | [[v0.1 - Foundation]] |
| 0022 | Internal NVMe strictly off-limits; everything on the external SSD | Accepted | [[v0.1 - Foundation]] |
| 0023 | terraform@pve needs a storage-scoped ACL, not just PVEVMAdmin | Accepted | [[v0.1 - Foundation]] |
| 0024 | terraform@pve also needs an SDN-scoped ACL for network attach | Accepted | [[v0.1 - Foundation]] |
| 0030 | Data-replica policy as StorageClass tiers (longhorn = 1 dev, longhorn-replicated = 2 prod) | Accepted | [[v2.0 - Operability]] |
| 0031 | Longhorn's StorageClasses are not the cluster default | Accepted | [[v2.0 - Operability]] |
| 0032 | Keep the upstream-conventional longhorn-system namespace | Accepted | [[v2.0 - Operability]] |
| 0033 | Longhorn UI exposed internal-only via the Tailscale front door | Accepted | [[v2.0 - Operability]] |
