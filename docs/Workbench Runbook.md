# Workbench Runbook

> Back to [[Homelab Learning Map]] · Decisions [[v4.0 - Developer Workspace]] · Milestone [v4.0](../ROADMAP.md)

Operating notes for the `workbench` namespace — the in-cluster developer workspace introduced in v4.0.

**Status: stub.** Started in M0 (issue #59) to hold the dev-port table, which is needed before `devx` (#62) and dev frontend exposure (#63) can be built. The daily loop, the offline flip, and PVC-node recovery are written in M8 (issue #67).

---

## Dev ports

**This is a lookup table, not an allocation scheme.** It exists so `devx` knows which URL to open and so M4's Tailscale `Ingress` objects have a known `targetPort` — *not* to prevent collisions.

Ports do not collide across projects here. A port lives in a network namespace, and in Kubernetes the **pod** is the network namespace: every pod has its own IP and its own full port space. Two Deployments in `workbench` can both bind 5173 with no conflict, and so can two Tailscale `Ingress` frontends. This was originally misunderstood as a reason for per-project port blocks; see the ADR-0054 amendment.

Two cases *do* collide, and neither needs pre-allocation:

| Case | Collides? | Why, and what to do |
|---|---|---|
| Two app-container pods, same port | No | Separate pods, separate network namespaces. |
| Two Tailscale `Ingress` frontends, same port | No | Separate tailnet nodes, each with its own MagicDNS name. |
| Two dev servers in tmux panes **inside the devbox pod** | **Yes** | One pod, one network namespace. Announces itself instantly as `EADDRINUSE` — change the port on the spot. |
| Two `kubectl port-forward` sessions **on the Mac** | **Yes** | The Mac's own port space. Shrinking by design: M4 replaces port-forward in the normal loop. |

### Conventions

- **Prefer the framework default** (Vite 5173, Django 8000, Next 3000). Move a project off its default only if it genuinely conflicts with something in its *own* pod.
- **Add a row when a project is onboarded**, not before. No pre-allocation across repos that may never be onboarded.
- **Record the port where the code lives**, not only here — this table is the index, the repo is the source of truth.

### Table

| Project | Service | Port | Notes |
|---|---|---|---|
| `kiroku` | `kiroku-dev` | 5173 | Vite default. Dev hostname is `kiroku-dev` because `kiroku` already runs in-cluster as a live workload. First project through the inner loop (issue #64). |

---

## Excluded from this workflow

`homelab-k8s` and `homelab` are **not** developed in-cluster. A `terraform apply` from a pod running on the cluster it provisions can destroy its own execution environment mid-apply and leave state locked. Those two stay on the Mac or a Proxmox VM (ADR-0062).

---

## To be written in M8 (issue #67)

- The daily loop — `devx <project> up`, connect, work, `devx <project> down`
- The offline flip — working when the tailnet or the cluster is unavailable
- Recovery when the PVC's node is down — the workspace is single-replica with no backups by design (ADR-0063); GitHub is the backup and the rule is **commit before you stop**
