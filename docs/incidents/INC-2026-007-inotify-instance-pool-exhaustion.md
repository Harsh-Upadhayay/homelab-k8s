# INC-2026-006: fs.inotify.max_user_instances pool exhaustion risk across all k3s nodes

## Incident metadata

| Field | Value |
| --- | --- |
| Date | 2026-08-12 |
| Severity | SEV-4 |
| Status | Resolved |
| Systems | All three k3s nodes (k3s-server-1, k3s-worker-1, k3s-worker-3); kubelet; containerd; every pod on the cluster |
| Start | Unknown — latent since node provisioning. k3s-server-1/k3s-worker-1 were 39 days old and k3s-worker-3 18 days old at time of diagnosis; the kernel default this incident is about was never touched at any point in that window. |
| End | 2026-08-12, same session — sysctl fix applied and verified live |
| Duration | Not a bounded outage; a standing latent condition, detected and mitigated within one session (see Detection/Resolution) |
| Detection | Manual review of pod logs across the cluster via Grafana/Loki surfaced the same exception in many unrelated pods |
| Data impact | None identified. No confirmed pod crash, restart, or CrashLoopBackOff was traced to this condition; a post-fix scan of all pod logs across all namespaces found zero occurrences of the symptom. |

## Executive summary

A generic, misleading log line — `failed to create fsnotify watcher: too many open files` — was observed across
multiple unrelated pods while reviewing logs in Grafana/Loki. Investigation traced this to the kernel sysctl
`fs.inotify.max_user_instances`, which had been left at its stock default of 128 on all three nodes since they
were first provisioned. That limit is a single pool shared by every process running under one real UID on a
node; since k3s/containerd run without user-namespace remapping, every pod's root process and kubelet's own
volume-watching activity all draw from the same 128-slot pool per node. Live inspection showed kubelet alone
was already holding 25-42 of those 128 slots on each node just from routine ConfigMap/Secret volume watching,
before any application pod asked for a watcher — leaving very little headroom for the rest of the cluster's
workloads. The sysctl was raised to 12400 on all three nodes via a new Ansible task, applied live, verified,
and committed to the platform's Infrastructure-as-Code.

## Impact

- No confirmed service outage, pod crash, or CrashLoopBackOff has been traced to this condition.
- The only confirmed effect was a confusing, hard-to-search log line appearing in multiple unrelated pods'
  logs, discovered incidentally during routine log review.
- This is recorded as a near miss: the cluster was closer to a shared-resource exhaustion edge than intended
  (kubelet alone consuming 20-33% of the old 128-slot ceiling on each node in steady state), even though no
  workload had yet been confirmed to fail because of it at the time of detection.

## Detection

First signal was a human noticing the same exception text recurring across several unrelated pods while
manually reading logs in Grafana/Loki — not an automated alert. This was not more directly actionable sooner
for two compounding reasons: no Prometheus alert exists on per-node inotify instance headroom, and the error
text itself never mentions "inotify" (Go's `fsnotify` library, like most userspace libraries, simply forwards
the OS's generic `EMFILE`/`ENFILE` string). A literal text search for "inotify" across the cluster's Loki logs
found nothing except Loki logging back its own query text — the actual symptom string gives no lexical hint of
its root cause. An alert on per-node inotify instance usage-vs-limit (if such a metric can be exported — see
Corrective actions) or a Loki alert rule matching the generic "too many open files"/"fsnotify" pattern across
namespaces would have surfaced this without depending on a human happening to notice a repeated string.

## Timeline

Times are UTC on 2026-08-12 where confirmed; most of this incident's internal sequencing was not logged with
wall-clock precision and is reconstructed from session order only — marked approximate below.

| Time | Event |
| --- | --- |
| (approximate, earlier in session) | Routine cluster CPU/RAM/disk review; user separately reports noticing a common inotify-related exception across all pods' logs while browsing Grafana/Loki. |
| (approximate) | Investigation: literal Loki text search for "inotify" returns no real matches (only Loki's own self-logged query text); user supplies the exact line, `failed to create fsnotify watcher: too many open files`. |
| (approximate) | Root cause traced via live `/proc/[pid]/fd` inspection over SSH on all three nodes: `fs.inotify.max_user_instances` confirmed at the stock default of 128 on every node; kubelet alone found holding 25-42 instances per node. |
| (approximate) | Fix designed: new Ansible task in `ansible/roles/k3s_node/tasks/main.yml` (`ansible.posix.sysctl`, target `fs.inotify.max_user_instances=12400`, file `/etc/sysctl.d/90-k3s.conf`, `reload: true`). Syntax-checked against `site.yml`. |
| (approximate) | Fix applied live via a targeted ad-hoc run (`ansible k3s_cluster -m ansible.posix.sysctl ...`), deliberately scoped narrower than a full `site.yml` run to avoid re-triggering the `k3s_server`/`longhorn_node` plays' potential restart handlers on the control plane. |
| (approximate) | Verified live on all three nodes via direct `sysctl fs.inotify.max_user_instances` read: `12400` confirmed on k3s-server-1, k3s-worker-1, k3s-worker-3. |
| (approximate) | Full-cluster log scan (all namespaces, all pods, all containers, last 15 minutes) for `inotify`, `fsnotify`, `ENOSPC`, `too many open files`, `watch limit`: zero matches. |
| 11:43 UTC | (Separate, related sub-thread: an unrelated upstream Linux kernel mailing-list RFC pitch about this same default was sent and partially bounced during the same investigation — not part of this cluster's incident, but the one hard timestamp available from this session; included for calibration of the approximate markers above.) |
| (approximate, after the above) | Change committed (`47bd5ff`) and pushed to `origin/main`. |

## Technical root cause

`fs.inotify.max_user_instances` is a Linux kernel sysctl capping how many separate `inotify_init()` "watch
sessions" (instances) a single real UID may hold open on a host, system-wide. It has defaulted to 128 since
inotify's introduction in 2005 and, unlike its sibling limit `fs.inotify.max_user_watches` (which auto-scales
with available RAM as of kernel 5.11), it has never been made to scale — it is a flat constant regardless of
machine size or workload count.

Because k3s/containerd here run without user-namespace UID remapping (the default for essentially every
mainstream container runtime), every container's root process is literally UID 0 on the host — the same UID
as every other unremapped container's root, and the same UID as the host's own `kubelet`/`containerd`
processes. All of them draw from one shared, node-wide 128-slot pool. Live inspection confirmed `kubelet`
alone was holding 25-42 of those 128 slots per node in steady state, purely from its normal duty of watching
mounted ConfigMap/Secret volumes for live updates — before any application pod's own file-watching needs are
counted at all. Once the pool is exhausted, the next `inotify_init()` call by *any* process on that node fails
with `EMFILE`, which most userspace libraries (including Go's `fsnotify`, used here) surface as a generic
"too many open files" with no mention of inotify — explaining why the same string appeared across several
unrelated pods rather than pointing at one misbehaving application.

## Contributing factors

- The kernel default (128) was never explicitly set or reviewed by this platform's Ansible roles at any point
  since the nodes were first provisioned — it simply inherited whatever Ubuntu's stock kernel shipped.
- `kubelet`'s own steady-state inotify usage already consumed a substantial fraction of the tiny default pool
  before any workload was considered, leaving little real headroom on any node.
- The sibling limit, `fs.inotify.max_user_watches`, auto-scaling with RAM since kernel 5.11 could create a
  false impression that "inotify limits are handled" on a modern kernel, obscuring that the *instances* limit
  received no equivalent treatment.
- The failure's error text is generic by construction (a bare OS `EMFILE`/`ENFILE` string) and never mentions
  inotify, making it resistant to keyword-based log search or alerting without already knowing the cause.
- No Prometheus alert or dashboard panel exists for per-node inotify instance headroom.

## Resolution and recovery

1. Confirmed the literal symptom string via the user directly, since keyword search for "inotify" in Loki
   found nothing (the string itself never contains that word).
2. Traced the failure to the kernel level by reading `/proc/[pid]/fd` inotify-fd counts per process, over SSH
   with root, on all three nodes — establishing the 128 default and kubelet's 25-42-instance baseline as
   observed fact, not assumption.
3. Added a new Ansible task to `ansible/roles/k3s_node/tasks/main.yml` (the role already applied to all three
   nodes via the `k3s_cluster` host group in `ansible/site.yml`), using the same `ansible.posix.sysctl` module
   and `/etc/sysctl.d/90-k3s.conf` file already used for this role's existing networking sysctls, setting
   `fs.inotify.max_user_instances=12400` with `reload: true`.
4. Syntax-checked the full playbook (`ansible-playbook site.yml --syntax-check`) before applying anything.
5. Applied the change with a deliberately narrow ad-hoc Ansible command targeting only this module and value,
   rather than a full `site.yml` run, to avoid unnecessarily re-executing the `k3s_server` and `longhorn_node`
   plays and risking an unintended control-plane restart.
6. Verified the change was live (not just written to disk) via a direct `sysctl fs.inotify.max_user_instances`
   read over SSH on all three nodes: `12400` confirmed on each.
7. Verified the fix's effect by scanning every pod, every container, every namespace's logs (last 15 minutes)
   for the failure string and related terms: zero matches, cluster-wide.
8. Committed the change (`47bd5ff`) and pushed to `origin/main`, so the live fix is also the durable,
   version-controlled source of truth — a future full `site.yml` run will find it already applied.

## What went well

- Caught via routine log review before any confirmed application-level failure was traced to it.
- Root cause was established from direct kernel/process evidence on the live nodes, not inferred or guessed.
- The fix was applied with a deliberately narrow blast radius (a targeted ad-hoc command) rather than a full
  playbook run, avoiding unnecessary risk to the control plane.
- The fix was captured in the platform's Ansible role immediately, so it is not a live-only hotfix that could
  silently drift from what's committed.

## What did not go well

- This kernel default had been silently unmanaged since every node's initial provisioning — nothing in this
  platform's Ansible previously reviewed or pinned it.
- No monitoring exists for this class of resource exhaustion; detection depended entirely on a human noticing
  a repeated string while reading logs for an unrelated reason.
- The error text's genericness means even deliberate keyword-based searching (as was tried first) does not
  reliably surface this class of failure.

## Where we got lucky

- Kubelet's own steady-state usage (25-42 instances) left enough headroom below the old 128-slot cap that, as
  far as could be confirmed, the cluster had not yet tipped into an actual application-level failure at the
  point this was caught — but the margin was thin, and this should not be read as evidence the old default was
  safe, only that failure had not yet been observed.

## Corrective and preventive actions

| Priority | Action | Owner | Status | Completion evidence |
| --- | --- | --- | --- | --- |
| P0 | Raise `fs.inotify.max_user_instances` to 12400 on all k3s nodes via Ansible | Repository owner | Done | `sysctl fs.inotify.max_user_instances` reads 12400 on all three nodes; task committed in `ansible/roles/k3s_node/tasks/main.yml` (commit `47bd5ff`) |
| P0 | Verify the failure string is gone cluster-wide after the fix | Repository owner | Done | Full-namespace, full-pod log scan for the failure string and related terms returned zero matches |
| P1 | Investigate whether per-node inotify instance usage-vs-limit can be exported (via node_exporter or a textfile collector) and alerted on in Prometheus | Repository owner | Open | Alert configured and confirmed to fire against a synthetic near-limit condition |
| P2 | Audit other kernel sysctl defaults on these nodes that have never been explicitly reviewed or pinned by Ansible, beyond the ones already set for pod networking | Repository owner | Open | Audit result recorded, with any further gaps either pinned or explicitly accepted as fine at stock default |

## Lessons and review questions

- A shared, per-UID kernel resource limit is invisible to per-pod thinking — the unit of exhaustion here was
  the *node*, not any individual container, and only cluster-wide log correlation revealed the pattern.
- Generic OS error strings (`EMFILE`/`ENFILE` surfacing as "too many open files") can hide a specific,
  diagnosable root cause behind text that gives no lexical clue what subsystem actually failed — worth
  remembering the next time a vague resource-exhaustion message shows up in multiple unrelated places at once.
- Should other never-explicitly-set kernel defaults on these nodes be audited proactively, rather than found
  reactively one incident at a time? (Tracked as the P2 action above.)
- Full technical background — inotify instances vs. watches, per-UID kernel accounting, and why containers
  share it — is written up in [[Platform Concepts]] rather than repeated here.

## Evidence

- Failure string: `failed to create fsnotify watcher: too many open files`.
- Pre-fix `sysctl fs.inotify.max_user_instances` on all three nodes: `128`.
- Live per-process inotify fd counts (via `/proc/[pid]/fd`, root, over SSH): kubelet (`k3s-server`/`k3s-agent`)
  observed holding 25 (k3s-server-1), 31 (k3s-worker-1), and 42 (k3s-worker-3) instances respectively.
- Post-fix `sysctl fs.inotify.max_user_instances` on all three nodes: `12400`.
- Post-fix full-cluster log scan (all namespaces, all pods, all containers, last 15 minutes) for `inotify`,
  `fsnotify`, `ENOSPC`, `too many open files`, `watch limit`: zero matches.
- Commit: `47bd5ff` — `fix(k3s): raise fs.inotify.max_user_instances to 12400 on all nodes` — modifies only
  `ansible/roles/k3s_node/tasks/main.yml`, pushed to `origin/main`.
