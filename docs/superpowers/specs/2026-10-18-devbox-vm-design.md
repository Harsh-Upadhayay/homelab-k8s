# Devbox VM — replacing the in-cluster workbench

**Date:** 2026-10-18
**Status:** Design — awaiting approval
**Supersedes (once landed):** most of ADR-0054 … ADR-0066 (v4.0 Developer Workspace); closes #103 and #76 as superseded

## 1. Intent

The v4.0 in-cluster devbox has been the daily driver for 3–4 months and keeps failing for the
same structural reasons:

- **One shared RWO volume** is mounted by the devbox *and* every project's app pods. Every pod
  with `fsGroup` re-chowns the whole 50Gi volume on start (live on 2026-10-18: a `kiroku-api` pod
  past 110k files, probe timeouts across the namespace, one workspace replica failing mid-storm).
  RWO pins every consumer to one node with nothing enforcing it (Multi-Attach deadlocks), and one
  failure takes every project down at once (INC-008, INC-010: 47h + 20h outages).
- **NAT machinery for a direct tailnet path**: node SNAT rule, `hostPort`, conntrack reaper,
  `Recreate` strategy — four coupled fixes for one pod.
- **An in-cluster build chain** that can fill a node (INC-006) and that the devbox itself depends
  on to restart (`:dev` + `imagePullPolicy: Always`).

The replacement removes those causes rather than patching them: **one ordinary Proxmox VM** that
holds the code, editor backend, agents and every project's dev stack (Docker Compose). Success is
"open a terminal and work", reliably, with far fewer moving parts.

### Target daily workflow (the operator's own words, structured)

1. Write code on the VM (ssh/mosh + tmux, or VS Code Remote-SSH).
2. Iterate against the project's Compose stack on the VM, **port-forwarded to the Mac** (`ssh -L`
   or VS Code's forwarding) — `localhost` on the Mac keeps OAuth callbacks, secure cookies and
   service workers working without TLS.
3. **Dev** deployments to the cluster happen via **CI**; **prod** deployments via **CD** (Argo).
4. The VM holds **cluster-admin**.

## 2. Goals, non-goals, anti-goals

**Goals**
- A devbox VM provisioned and configured the same way as the rest of the platform
  (Terraform + Ansible, versions pinned), reachable over the tailnet with a direct path.
- Code and `$HOME` moved from the workspace PVC with no loss; dev database contents restored
  **as-is** (except pwm — §6.4).
- Full removal of the in-cluster workbench and everything that exists only to support it.
- Cluster-admin `kubectl` from the VM that does not depend on the Tailscale operator.
- A designed (not yet built) ghcr.io image-pull path for the CI/CD work that follows.
- Every project running under Docker Compose on the VM against its restored data — the
  operator writes each `compose.yaml` (learning exercise, in scope); the assistant supplies the
  exported manifests, digests, data paths and port table.

**Non-goals (explicitly the operator's own learning exercise, later)**
- CI to dev, CD to prod, dev/prod namespaces, and building the ghcr pull path.
- DR for the devbox (discussed; deferred — §10).

**Anti-goals — the design has failed if:**
- Any data the operator chose to keep is lost (§6), or anything is deleted before the soak passes.
- `pve-dell` RAM is overcommitted (ADR-0020).
- Anything touches the laptop's internal NVMe (ADR-0022).
- The VM becomes a k3s node, or the dev environment again depends on in-cluster pieces to start.

## 3. Constraints

- **ADR-0020 — RAM never overcommitted.** `pve-dell` has 30GiB; `k3s-worker-1` currently has
  28672MB (Terraform `1561e7c`; CLAUDE.md's "22GiB" is stale). CPU may be mildly overcommitted. (CLAUDE.md's "12c" for worker-1 is stale too: it has 14.)
- **ADR-0022** — only Dell's external SSD (`local-lvm`) is usable storage.
- **Pinned versions everywhere**; bumps are deliberate.
- **Learning mode:** the operator writes the Terraform VM resource and the `devbox` Ansible role;
  the assistant guides, reviews, fixes small issues and explains. Mechanical work (file copies,
  Mac config, deletions, docs) may be done directly.
- **k8s/ changes ship via branch + PR**; Argo auto-syncs `main`, so merge = deploy. (The
  `workbench` Application itself is manual-sync.)
- **Off-LAN operator.** The Mac is usually not on 192.168.1.0/24; everything must work over the
  tailnet. Ansible inventory already addresses hosts by MagicDNS.

## 4. Decisions (made by the operator)

| # | Decision |
|---|---|
| D1 | Devbox is a **Proxmox VM** on `pve-dell`, not pods; dev stacks run in **Docker Compose** on it. |
| D2 | **10GiB RAM, 8 vCPU, 150GB** disk. The 8 vCPU are **overcommitted** — `k3s-worker-1` keeps its **14** (`terraform.tfvars`), so **22 vCPU on 14 threads**, accepted knowingly. |
| D3 | Cluster-admin via a **dedicated ServiceAccount + `cluster-admin` ClusterRoleBinding**, token on the VM, API reached directly on the LAN at `192.168.1.21:6443` (not via the Tailscale operator proxy). |
| D4 | CI images go to **ghcr.io**. Pull credentials for private images flow **SSM → ESO** like every other secret. Most images are public (no creds); some are private. The pull path is designed here and **built as step 1 of the CI/CD exercise**. |
| D5 | Dev database contents **matter and are restored as-is**. **pwm's data loss is acceptable.** |
| D6 | During migration, **all dev workloads may be down**; the old workbench is retired after the cutover + soak. |
| D7 | **No cordon/drain** for the `k3s-worker-1` memory change; the brief outage is accepted. |
| D8 | The direct-path NAT machinery is **removed**, not ported. Issue #103 (worker subnet routers) is superseded by the VM, which gets a direct path natively via `tailscale_host`. |
| D9 | **DR is deferred** (§10). |

## 5. Architecture

```
Mac ──ssh/mosh (tailnet, direct)──► devbox VM  (pve-dell, local-lvm, LAN + tailnet)
 │                                   ├── /workspace/<project>         code (same paths as today)
 │◄── ssh -L / VS Code forwarding ───┤── /srv/devdata/<project>/<svc> restored DB data
 │                                   ├── docker compose per project   (operator's exercise)
 │                                   └── kubectl (cluster-admin) ──LAN──► k3s API 192.168.1.21:6443
 └── GitHub ── CI → ghcr.io ── dev ns ;  CD (Argo) → prod ns        (operator's exercise)
```

### 5.1 Resources (pve-dell)

| VM | Before | After |
|---|---|---|
| `k3s-worker-1` | 14 vCPU / 28672MB | 14 vCPU / **18432MB** |
| `devbox` (new) | — | **8 vCPU / 10240MB / 150GB** |
| Host reserve | ~2GiB | ~2GiB (unchanged) |
| vCPU on 14 threads | 14 | **22** (overcommit accepted, D2) |

Measured 2026-10-18: worker-1 runs ~6.0GiB of non-workbench pods plus 7.4GiB of workbench pods.
After the cutover it needs ~6GiB + headroom, so 18GiB is comfortable. During the migration
window (devbox pod still up, ~5.3GiB) it is ~11.3GiB — still fits.

Dell thin pool: 816GiB, 30% used — a 150GB thin disk is fine.

**Known risk:** the CPU overcommit means the two VMs contend at the hypervisor when worker-1 is
busy (celery fleet, Longhorn rebuild). Proxmox `cpuunits` is the dial if it bites; not set up-front.

### 5.2 Provisioning — Terraform (`terraform/proxmox/`)

- A **new, separate** `proxmox_virtual_environment_vm` resource for the devbox — *not* an entry in
  the `k3s_worker` `for_each` (it is not a worker; its lifecycle, tags and disks differ).
- Clone template `9000` (Ubuntu 26.04 cloud-init) on `pve-dell`; `local-lvm`; one `scsi0` disk of
  150GB with `discard = "on"` (see the worker resource's comment for why that is load-bearing);
  QEMU guest agent enabled; `x86-64-v2-AES` CPU type like the workers.
- cloud-init: `vm_user` (`harsh`) + `ssh_public_key` (`~/.ssh/id_ed25519.pub`) — the k3s-node key
  pair; static LAN IP on `192.168.1.0/24` (builder picks a free address and verifies it is unused).
- `k3s-worker-1` memory `28672 → 18432` in `terraform.tfvars`. Applying it reboots worker-1 via
  the guest agent (D7).
- Tag: something like `devbox` alongside `local.common_tags` (builder's call).

### 5.3 Configuration — Ansible (`ansible/`)

- New inventory group **`devbox`**; `site.yml` gets a play for it running **`tailscale_host`**
  (existing role, `ubuntu` distro vars like the k3s nodes) and a **new `devbox` role**.
- **Tailnet identity — same as the k3s nodes** (assistant's call): join with the existing
  `tailscale_auth_key_reusable` via the unmodified `tailscale_host` role, as a user-owned
  (untagged) machine. MagicDNS name = the OS hostname set by cloud-init, i.e. `devbox`. No
  `--ssh`, no `--advertise-tags`, so the shared role needs no change. Access is **plain OpenSSH**
  (+ mosh over UDP 60000–61000), exactly how `ssh harsh@k3s-worker-1` already works over the
  tailnet today. Phase 2 verifies the tailnet policy allows tcp/22 and udp/60000–61000 from the
  Mac to the new machine, and that key expiry is disabled for it like the k3s nodes. The old
  devbox's `tag:devbox` grant is unrelated and goes away at decommission.
- **Bootstrap gap:** inventory addresses hosts by MagicDNS, but the VM has no tailnet identity
  until `tailscale_host` runs. The first run needs LAN reachability — e.g. `ProxyJump` through a
  tailnet host on the LAN (`pve-dell`), or a one-off `ansible_host=<LAN IP>` with a jump host.
  Builder's call; document whichever is used.
- **`devbox` role** contents (all versions pinned, readable tasks in the style of existing roles):
  - Base: `tmux mosh ripgrep git less tree htop iproute2 dnsutils netcat-openbsd build-essential
    unzip jq`; `LANG=C.UTF-8` (mosh requires a UTF-8 locale).
  - **Docker Engine + Compose plugin** from Docker's apt repo, pinned; `harsh` in the `docker`
    group (root-equivalent — acceptable given D3 already grants cluster-admin).
  - **mise** (runtimes stay user state under `$HOME`), **kubectl `v1.36.2`** (cluster minor),
    **helm** (pinned), **neovim** upstream tarball (Ubuntu's may be old; check).
  - `/workspace` and `/srv/devdata` created, owned by `harsh`.
  - **Not** in the role: Claude Code, Codex and other AI CLIs — user-installed into `$HOME` as
    today (native auto-updater), per the existing Dockerfile reasoning.
  - mosh's UDP range (60000–61000) must be reachable on the tailnet interface if a host firewall
    exists (Ubuntu cloud images ship with ufw inactive — verify).

### 5.4 Cluster-admin access (D3)

- In `homelab-k8s`: a ServiceAccount (e.g. `devbox-admin` in `kube-system`), a
  `ClusterRoleBinding` to `cluster-admin`, and a **`kubernetes.io/service-account-token` Secret**
  for a long-lived token. Manifest location: a new small platform directory under `k8s/` managed
  by Argo (builder's call on the exact path); the token Secret is created by the token controller,
  not stored in git.
- The VM's `~/.kube/config`: server `https://192.168.1.21:6443`, the k3s CA, and the SA token.
  Getting the token onto the VM is a one-time manual step (`kubectl get secret … | ssh devbox`),
  like the other bootstrap credentials. Revocation = delete the token Secret.
- **Accepted risk, recorded in the new ADR:** a machine that runs arbitrary third-party code
  (`npm install`, agents) holds cluster-admin. This deliberately exceeds ADR-0065's namespace-admin.
- Why not the Tailscale operator proxy: it adds a dependency on the operator pod (which also runs
  on Dell) for no gain on a LAN-resident VM.

### 5.5 Access from the Mac

- `~/.ssh/config`: `Host devbox` → MagicDNS name, `User harsh`, `IdentityFile ~/.ssh/id_ed25519`,
  `ForwardAgent yes`. The old `workbench` entry is removed at decommission.
- **`bin/devx` rewritten thin** (it loses all Kubernetes scaling logic):
  - `devx <project>` — mosh to `devbox`, attach/create tmux session `<project>` in
    `/workspace/<project>` (falls back to `/workspace` if missing). Keeps today's session-group
    behaviour only if it still earns its keep (builder's call).
  - `devx -s <project>` — same over `ssh -A` (git push needs the agent; mosh cannot forward it).
  - `devx -v <project>` — `code --remote ssh-remote+devbox /workspace/<project>`.
  - `devx fwd <port>…` (or a per-project port list) — `ssh -N -L` forwards. The runbook's
    per-project port table carries over so stacks never collide.
  - `ls` / `down` / `pull` / `status` reduce to whatever is still meaningful (e.g. `status` =
    tailnet + ssh reachability). Stays bash 3.2-compatible.

### 5.6 Workspace layout on the VM

- **Code stays at `/workspace/<dir>`** (same paths as the pod). Claude Code keys history/memory by
  path (`~/.claude/projects/-workspace-<repo>`), as do VS Code recents and tmux layouts.
- `$HOME` moves from `/home/vscode` to `/home/harsh`: references to `/home/vscode` in copied
  config files are rewritten. Both users are uid 1000, so ownership carries over.
- Current `/workspace` contents: `_devbox-build ai_scraping home homelab-k8s init kiroku
  neovara-homepage office-aws password-manager pwm-tutorials ratelimiter scratch sideby`.
  All of them come across; `home` is the old `$HOME` subPath (handled separately), and
  `_devbox-build` is dead once the Dockerfile is gone (copy it anyway; operator deletes later).
- `homelab-k8s` on the VM is fine (ADR-0062 explicitly allowed a VM outside the cluster). Whether
  the platform's Terraform/Ansible are also *run* from the VM (needs `.env`, the Proxmox key) is
  not part of this work.

## 6. Data migration

### 6.1 Code and home (pod → VM directly)

Never via the Mac (the 25GB would cross the world twice). `ssh -A` into the current devbox, then
stream `tar` to `harsh@devbox` over LAN/tailnet.

- `/workspace/<each dir>` including untracked and ignored files that matter (`.env*`, local data
  such as `kiroku/data`), **excluding** rebuildable trees: `node_modules`, `.venv`, build/dist
  output, `.pytest_cache`, language caches. Builder defines the exclude list and verifies per repo
  with `git status --ignored` that nothing excluded is irreplaceable.
- From `/home/vscode`: `.claude* .codex .agents .copilot .config .gitconfig* .git-credentials-work
  .ssh .aws .terraform.d .npmrc .tmux .tmux.conf .bashrc .bash_profile .kube` plus loose notes
  (`cheatsheet.md`, `RESTART-RECOVERY.md`, `argocd-documentor-ai-deploy*`). **Skip:** `.cache
  .npm .vscode-server .zed_server .local/share/mise` (reinstalled), `go` caches, `.docker`, stray
  `*.tmp.*`/`*.bak*`. `.kube` is copied but superseded by §5.4's kubeconfig.
- Per repo before copying: confirm everything is committed **and pushed**, or explicitly list what
  is not (commit before you stop — ADR-0063 — still applies until the VM is live).

### 6.2 PVC-backed databases — cold physical copy

For each: scale the workload to 0 → a throwaway helper pod mounts the PVC → `tar --numeric-owner`
streamed to the VM → `/srv/devdata/<project>/<service>/`. Nothing is modified on the source.

| Data | Source | Restore notes |
|---|---|---|
| `ais-mysql` | PVC `data-ais-mysql-0` | Image **`mysql:8.4.5@sha256:679e7e924f38a3cbb62a3d7df32924b83f7321a602d3f9f967c01b3df18495d6`** |
| `ais-qdrant` | PVC `storage-ais-qdrant-0` | Running `qdrant/qdrant:latest` → **pin `@sha256:12364fe851b9f17356fc88189fc06d1b521262e04659ec7345975b00c9246a10`** |
| `ais-rabbitmq` | PVC `ais-rabbitmq-data` | Running `rabbitmq:3-management` → **pin `@sha256:e582c0bc7766f3342496d8485efb5a1df782b5ce3886ad017e2eaae442311f69`**; node-name issue §6.3 |
| `sideby-data` | PVC `sideby-data` (mounted at `/data` in `sideby-app`) | Copy as-is |
| kiroku data | `/workspace/kiroku/data` | Comes with §6.1 |
| `cost-bot` | none (moto in memory) | Nothing to restore |

The digests above were captured live on 2026-10-18 and must be re-checked (and recorded in a
notes file next to the data) immediately before the copy — floating tags may have moved on any
restart. The operator's Compose files must use these exact digests to read the data back as-is.

### 6.3 RabbitMQ node name

RabbitMQ names its data directory after its node name, which defaults to the hostname — in a
Deployment, the pod name. The volume holds 11 `rabbit@ais-rabbitmq-<pod>` directories; only the
current one (`rabbit@ais-rabbitmq-7d9f94b469-6g4gm` as of 2026-10-18) is live, and earlier queues
are orphaned. Copy the whole directory; the Compose service must set
`RABBITMQ_NODENAME`/`hostname` to the live node name to restore as-is (a stable name also stops
the reset-on-restart behaviour). Re-check which directory is live at copy time.

### 6.4 pwm — loss accepted

`pwm-mysql` uses an **`emptyDir`** (data dies with the pod) and its encrypted values depend on KMS
keys held only in the `pwm-kms-emulator` (moto) process memory. The operator has accepted losing
pwm's dev data; it is **not** migrated. (Optional: a best-effort `mysqldump` before teardown, with
the understanding that encrypted columns are undecryptable.)

## 7. ghcr.io pull path (designed now, built in the CI/CD exercise — D4)

- **Push:** GitHub Actions with the built-in `GITHUB_TOKEN` (`permissions: packages: write`). No
  stored credential.
- **Public images:** no pull credentials at all.
- **Private images:**
  - Token: a GitHub PAT with only `read:packages` (ghcr.io has historically required a *classic*
    PAT — verify at creation time).
  - Stored in SSM under `/neovara/homeinfra/ghcr/*`, managed by `terraform/aws/` (`value_wo`).
  - An ESO `ExternalSecret` **template** renders `type: kubernetes.io/dockerconfigjson`.
  - Fan-out via a **`ClusterExternalSecret`** with a namespace selector (opt-in label, e.g.
    `neovara-ghcr-pull=true`); target namespaces also need `neovara-external-secrets=true` for the
    `aws-parameter-store` store's `conditions`.
  - Pods reference it via `imagePullSecrets` (or per-namespace ServiceAccount).
  - Rotation: `refreshInterval: "0"` (manual) per house style, or `1h` for this one secret — decide
    when built.
- **Policy:** consistent with ADR-0059's amendment (no *work-account* credentials in the cluster);
  the new ADR states it explicitly.
- Consequence: the in-cluster registry, BuildKit and `registries.yaml` mirror are retired (§8).

## 8. Migration phases (order matters)

**Phase 0 — capture and freeze** *(nothing is scaled down before this completes)*
1. Re-capture and record image digests for every workbench datastore (§6.2).
2. Optional best-effort pwm `mysqldump` (§6.4).
3. Per repo: commit + push, or record exactly what is unpushed/untracked.
4. One-off Longhorn snapshot of the `workspace` volume (safety net until decommission — not a
   backup policy).
5. Scale all dev workloads in `workbench` to 0 (D6). Devbox pod stays up as the copy source.

**Phase 1 — free RAM:** Terraform `k3s-worker-1` → 18432MB; apply (reboot, no drain — D7).
Verify the node is Ready and Longhorn volumes re-attach healthy.

**Phase 2 — build the VM:** Terraform devbox VM → Ansible (`tailscale_host` + `devbox`) →
cluster-admin SA + kubeconfig (§5.4). Verify: direct tailnet path from the Mac
(`tailscale ping devbox` reports direct), `docker run hello-world`, `kubectl auth can-i '*' '*'
--all-namespaces` → yes.

**Phase 3 — move data:** §6.1 code + home; §6.2/6.3 datastores (each scaled to 0 first —
already true after Phase 0). Verify file counts/sizes per directory against the source;
rewrite `/home/vscode` references; install AI CLIs and runtimes; re-login where needed.

**Phase 3b — Compose stacks:** operator writes `compose.yaml` per project (smallest first,
`ais` last) from exported manifests; datastores at pinned digests over `/srv/devdata`. Verify each
stack is up, reachable via port forward, hot-reloads, and shows its restored data.

**Phase 4 — Mac side:** `~/.ssh/config`, new `bin/devx`. Verify `devx <project>`, `devx -s`,
`devx -v`, and a port forward to a test server.

**Phase 5 — soak:** daily use for a period the operator chooses. Old workbench stays scaled down
but intact. **Nothing is deleted before the soak passes.**

**Phase 6 — decommission** (branch + PR for repo changes):
- Delete: the `workbench` Argo Application; `k8s/workbench/` (devbox, registry, BuildKit,
  namespace, RBAC, ExternalSecret, Dockerfile); `k8s/longhorn/manifests/storageclass-workbench.yaml`;
  the hand-applied dev workloads and their PVCs; the workspace volume and its snapshot.
  Order and reclaim behaviour matter — `longhorn-workbench` is `reclaimPolicy: Delete`, so deleting
  the PVC deletes the data. Builder sequences this; the operator confirms before each destructive step.
- Ansible, remove-then-delete: stop/disable `devbox-tailscale-snat.service` (its `ExecStop` removes
  the iptables rule) and `devbox-tailscale-reap.timer` on both workers, remove their files, *then*
  delete the `devbox_tailscale_snat` role and its `site.yml` entry. Same for the `registries.yaml`
  mirror task in `k3s_node` (remove the file; containerd picks it up on the next k3s restart).
- `terraform/aws/`: remove the `/neovara/homeinfra/workbench/tailscale-authkey` parameter.
- Tailscale admin: remove the old `workbench` machine.
- GitHub: close #103 (superseded by this design) and #76 (no PVC config left).
- Incidents INC-006/008/010: mark their workbench-specific preventive actions obsolete.

**Phase 7 — docs:** new ADR set (VM devbox, cluster-admin risk, ghcr+ESO pull path, dev data
location) with "Superseded by" links on ADR-0054 … 0066 as applicable; runbook rewritten for the
VM; CLAUDE.md architecture block (devbox VM on pve-dell, worker-1 18GiB, correct the stale 22GiB,
ADR-0062 status).

## 9. Calls made by the assistant (please check)

- Code stays at `/workspace/<dir>`; restored data at `/srv/devdata/<project>/<service>`.
- Devbox is a separate Terraform resource, not a `k3s_worker` entry.
- Cluster-admin SA lives in `kube-system` (name `devbox-admin`), token via a
  `service-account-token` Secret, manifests Argo-managed.
- Docker group membership for `harsh` (root-equivalent) accepted alongside cluster-admin.
- AI CLIs stay user-installed in `$HOME`, not in the Ansible role.
- The VM joins the tailnet like a k3s node (reusable key, untagged, plain OpenSSH) — §5.3.
- Phase 0 snapshot of the workspace volume kept until decommission.
- The old `devx` tmux session-group design is kept only if still useful.

## 10. Deferred — DR (recorded so it is not lost)

Discussed 2026-10-18, deferred by the operator. Findings:
- `pve-asrock` cannot host the devbox in a Dell outage without breaking ADR-0020 (~3GiB free; it
  also hosts the control plane). The planned third Proxmox node is the natural on-prem target.
- Proxmox HA/replication is unavailable (LVM-thin, no shared storage).
- Preferred shape when picked up: treat the VM as cattle — the same Terraform/Ansible can target an
  EC2 instance (`terraform/aws/`, off by default) — and back up *files* (restic → S3, credentials in
  SSM) rather than disk images, so restores land on any target. Dev databases need a pre-snapshot
  dump hook for consistency. Acceptance would include a real restore drill.
- This un-defers part of CLAUDE.md's "full backup strategy" for the devbox only; needs its own ADR.

## 11. Risks

- CPU contention between worker-1 and the devbox under load (§5.1).
- Floating-tag datastore images restarting onto newer versions before the copy (§6.2 — capture
  digests first).
- Worker-1 reboot without drain briefly takes down everything with single replicas on it (D7).
- `pve-dell` outage takes the devbox down (as today) until DR exists (§10).
