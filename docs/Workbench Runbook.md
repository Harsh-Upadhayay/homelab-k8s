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

## In-cluster builds

Three components in `workbench`, one file each under `k8s/workbench/manifests/`:

| Component | What it is | Storage |
|---|---|---|
| `devbox` | shell + editor backend | `workspace` PVC, `longhorn-workbench` |
| `registry` | `registry:2`, where built images land | `registry` PVC, plain `longhorn` |
| `buildkit` | rootless `buildkitd`, does the building | `emptyDir` — cache is rebuildable by definition |

**Why a registry is mandatory, not a convenience:** a Deployment cannot reference an image on a PVC. `image:` is not a filesystem path — containerd resolves images only from its own per-node content store, populated solely by pulling from a registry (ADR-0059).

**Scope is dev only** (ADR-0060). This must never serve images Argo CD deploys: an in-cluster build path under GitOps produces artifacts not reproducible from git. Real images come from CI (issue #27).

Both Services listen on **port 80**, so references need no port suffix:

- `registry.workbench.svc.cluster.local/<repo>:dev`
- `tcp://buildkit.workbench.svc:80`

Verified: `curl http://registry.workbench.svc.cluster.local/v2/` returns `HTTP 200`.

### Rootless BuildKit needs Unconfined seccomp + AppArmor

Without these the container dies instantly with `[rootlesskit:child] error: failed to share mount point: /: permission denied`:

```yaml
securityContext:
  seccompProfile: { type: Unconfined }
  appArmorProfile: { type: Unconfined }
  runAsUser: 1000
  runAsGroup: 1000
```

`Unconfined` is **not** the same as privileged — the container still runs as a non-root user and cannot escape to the node. It only stops the kernel filtering the user/mount-namespace syscalls rootless mode is built on. `--oci-worker-no-process-sandbox` alone is not sufficient.

Healthy startup logs `found 1 workers` and `running server on [::]:1234`. The CDI, fsverity, and "skipping containerd worker" warnings are all benign in rootless mode.

## Node cannot pull images — check DNS first

**Symptom:** pods stick in `ContainerCreating` indefinitely on one node, with **no `Pulling` event at all** and often `Events: <none>`. Existing pods on that node keep running fine, and the node still reports `Ready` — the kubelet heartbeat reaches the API server without needing DNS.

The absence of a `Pulling` event is the tell: containerd never got far enough to start one.

Diagnose from the Proxmox host if SSH to the node is also down (`qm guest exec` needs no working sshd):

```
ssh -i ~/.ssh/proxmox_ed25519 root@pve-dell.egret-pence.ts.net \
  "qm guest exec 101 -- /bin/bash -c 'k3s crictl pull busybox:latest'"
```

`dial tcp: lookup registry-1.docker.io: Try again` is a **resolver failure**, not a network one — confirm with `ping 1.1.1.1`, which will succeed.

Then check the real culprit:

```
systemctl is-active systemd-resolved     # inactive => all DNS fails
systemctl list-units --state=failed
systemctl is-system-running              # "degraded" is the giveaway
```

Seen on 2026-07-29: `-.mount` (Root Mount) in a failed state despite the filesystem being genuinely healthy (`rw`, writable, no ext4 errors in dmesg). Both `systemd-resolved` and `ssh` depend on it, so both were blocked, and `journald` was down too — which is why the logs explaining the trigger were lost. With resolved dead but `/etc/resolv.conf` still pointing at its stub on `127.0.0.53`, every lookup failed.

**`systemctl reset-failed` does not fix it** — `-.mount` is created during early boot and cannot be re-evaluated on a live system. A reboot is the fix, and it is safe when the filesystem checks out:

```
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data   # preferred
ssh -i ~/.ssh/proxmox_ed25519 root@pve-dell... 'qm reboot 101'
kubectl uncordon <node>
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
