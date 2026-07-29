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

## Connecting

`ssh workbench` from any tailnet device. No password, no key, no `authorized_keys` — authentication is tailnet identity, authorization is the `tag:devbox` ACL rule (ADR-0064). VS Code Remote-SSH attaches to the same host.

The Mac's `~/.ssh/config` entry:

```
Host workbench
  HostName workbench.egret-pence.ts.net
  User vscode
  ForwardAgent yes
```

`ForwardAgent yes` delegates the *use* of the Mac's SSH keys without copying them — `git push` works from the devbox with **no credential on the PVC** (ADR-0056). Verify with `ssh-add -l` inside the devbox: keys listed, but `~/.ssh/` holds only `known_hosts`.

### Gotchas, each hit for real

- **`--statedir=<dir>`, never `--state=<file>`.** Tailscale SSH generates host keys and needs a state *directory*. With only `--state`, `tailscaled` starts, exits 0, and advertises the SSH capability but never serves port 22 — the log line is `SSH will appear as disabled for this node`. The symptom is an SSH connection that hangs at **banner exchange**, which looks exactly like a network fault and is not.
- **Never delete the devbox node from the Tailscale console while the PVC state survives.** The daemon comes back believing it is registered, reports `Online: True`, and has no working path — pings time out. Recovery is `rm -rf $HOME/.local/share/tailscale`, then re-run `tailscale up --authkey=... --hostname=workbench --ssh` once.
- **Duplicate nodes / name drift.** A non-ephemeral key plus a wiped state directory registers a *new* node each time, and Tailscale will not reuse a name still held, so the host becomes `workbench-1`, `-2`, … Keeping `--statedir` on the PVC is what prevents this: verified that after a full pod delete, `tailscaled` resumed as the same node with no auth key at all.
- **Starting `tailscaled` by hand needs `setsid ... </dev/null`.** Backgrounding it from a `kubectl exec` session kills it when that session ends.
- **macOS DNS caching** can hold a stale MagicDNS answer after a node's IP changes: `sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder`.

## Kubernetes access from the devbox

The devbox holds **namespace-admin on `workbench` only** — a `devbox` ServiceAccount bound to the built-in `admin` ClusterRole through a namespaced `RoleBinding` (ADR-0065). Secrets included, deliberately.

Verified scope: `200` on `workbench` secrets and deployments; `403` on other namespaces' secrets, cluster-wide secrets, and nodes.

Cluster-wide operations stay on the Mac, where the real kubeconfig lives. To check what the devbox identity can actually do, **test with its token, not `kubectl auth can-i --as`** — the impersonation form reports the caller's own permissions when the caller holds cluster-admin, which it does here via ADR-0026:

```
kubectl exec -n workbench deploy/devbox -- bash -c '
T=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
curl -s -o /dev/null -w "%{http_code}\n" \
  --cacert /var/run/secrets/kubernetes.io/serviceaccount/ca.crt \
  -H "Authorization: Bearer $T" \
  https://kubernetes.default.svc/api/v1/namespaces/workbench/secrets'
```

## Runtime-installed tooling

`tailscaled`, `tailscale`, and anything else in `~/.local/bin` are installed **at runtime into the PVC-backed `$HOME`**, not baked into the image. They survive pod restarts because `$HOME` is on the volume.

**Known consequence:** if the PVC is ever recreated, the Deployment starts a `tailscaled` that does not exist, the liveness probe fails, and the pod crash-loops. Recovery is to reinstall the binaries and re-run `tailscale up` once. This is deliberate — baking them in needs BuildKit (issue #65) — but it is a known state, not a surprise.

---

## Excluded from this workflow

`homelab-k8s` and `homelab` are **not** developed in-cluster. A `terraform apply` from a pod running on the cluster it provisions can destroy its own execution environment mid-apply and leave state locked. Those two stay on the Mac or a Proxmox VM (ADR-0062).

---

## To be written in M8 (issue #67)

- The daily loop — `devx <project> up`, connect, work, `devx <project> down`
- The offline flip — working when the tailnet or the cluster is unavailable
- Recovery when the PVC's node is down — the workspace is single-replica with no backups by design (ADR-0063); GitHub is the backup and the rule is **commit before you stop**
