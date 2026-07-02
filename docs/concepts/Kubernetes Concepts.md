# Kubernetes Concepts

> Back to [[Homelab Learning Map]]

Not yet populated — the cluster doesn't exist yet (Phases 4–8), and none of the manifests in `k8s/` have been applied. Per this doc's convention, entries land here once actually run, not just read.

Will cover, roughly in build order: k3s vs. upstream Kubernetes, embedded etcd vs. SQLite, node taints, Helm (`repo add`/`install`/`values.yaml`), CRDs vs. built-in resources, Traefik's `IngressRoute`, `ClusterIP` vs. `LoadBalancer` Services, cert-manager's `ClusterIssuer`/`Certificate`/DNS-01 challenge flow, `NetworkPolicy` enforcement on plain Flannel, and the Tailscale Operator's `loadBalancerClass`.
