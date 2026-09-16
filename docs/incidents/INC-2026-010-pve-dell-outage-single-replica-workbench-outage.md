# INC-2026-010: A `pve-dell` outage took every single-replica volume on `k3s-worker-1` down for two days

## Incident metadata

| Field | Value |
| --- | --- |
| Date | 2026-09-12 |
| Severity | SEV-3 |
| Status | Resolved; alert delivery (#92), RWO co-scheduling, and the cause of the `pve-dell` outage remain open |
| Systems | `pve-dell`, `k3s-worker-1`, Longhorn v1.12.0, workbench (devbox, registry, `ais-*`, `kiroku*`, `neovara-homepage`, `pwm`, `ratelimiter-docs`) |
| Start | 2026-09-12 12:18:58 UTC (`k3s-worker-1` Ready condition → Unknown) |
| End | 2026-09-16 06:52 UTC (all five workspace consumers `1/1 Running`, read and write verified) |
| Duration | Phase 1 approximately 46 hours 56 minutes; phase 2 a further 19 hours 50 minutes |
| Detection | Phase 1: user noticed devbox was unreachable while working, roughly 46 hours after onset. Prometheus fired `KubeNodeNotReady` and `KubeNodeUnreachable` correctly throughout; Alertmanager's default route discarded them. Phase 2: user reported devbox still unhealthy |
| Data impact | No loss, verified. The sole devbox replica was auto-salvaged intact in phase 1. In phase 2 the ext4 filesystem aborted its journal and shut down, but detach and reattach replayed it cleanly: `Filesystem state: clean`, no `lost+found`, all eleven repositories and `/home/vscode` present, read and write confirmed from inside the running pod |

## Executive summary

The `pve-dell` hypervisor stopped running, taking its only guest — `k3s-worker-1` — offline at
12:18 UTC on 2026-09-12. Because ADR-0051 sets every Longhorn volume to a single data copy, every
volume whose only replica lived on that worker became `faulted`, not merely degraded. Kubernetes
correctly evicted and rescheduled the affected pods onto `k3s-worker-3`, but they could not start:
there was no second copy of their data anywhere in the cluster, so each new pod sat in
`ContainerCreating` indefinitely. The condition persisted for roughly two days because no alert
reached a human — Prometheus detected it immediately and Alertmanager discarded the notification via
the same `"null"` default route recorded in INC-2026-009. When the host was restarted on 2026-09-14,
recovery stalled a second time: `longhorn-manager` on `k3s-worker-1` crash-looped 41 times on a
fatal optimistic-concurrency conflict while writing its default `Setting` objects, leaving the
Longhorn node `Ready=False` and the volumes still unattachable. It escaped only when one restart
happened to win the race, after which auto-salvage cleared the failed replica and service restored
itself without operator intervention.

A second, longer outage then followed from the remediation. The devbox volume was promoted to two
replicas *in place*, while attached and actively written; the resulting snapshot purge coincided
with the block device dropping out from under the mounted filesystem, and ext4 aborted its journal
and shut down. Longhorn's own rebuild completed and reported the volume `healthy`, but every kubelet
mount above it stayed dead, so five pods failed for a further twenty hours against storage that
could not be read. Scaling the consumers to zero released the stale mounts, the filesystem remounted
clean with no data loss, and all five recovered — the last two only after being deleted onto the
single node their `ReadWriteOnce` volume was attached to.

## Impact

- devbox was unavailable for approximately 47 hours; the workspace volume could not attach anywhere.
- The in-cluster registry, `ais-backend`, `ais-mysql`, `ais-qdrant`, `ais-rabbitmq`, `kiroku`,
  `kiroku-api`, `neovara-homepage`, `pwm` and `ratelimiter-docs` were down or degraded for the same
  reason and the same duration.
- `KubePodNotReady` fired in 16 namespaces, so impact extended well beyond workbench — including
  `argocd`, `cert-manager`, `immich`, `nextcloud`, `traefik`, `tailscale` and `monitoring`.
- The cluster kept a control plane throughout: `k3s-server-1` and `k3s-worker-3` both live on
  `pve-asrock` and were unaffected, so `kubectl` and the API server stayed available.
- Anything whose replica already lived on `k3s-worker-3`, or which held no state at all, continued
  to serve normally.
- No data was lost and nothing required restore from backup.

Phase 2 impact, on top of the above:

- devbox, `kiroku`, `kiroku-api`, `ratelimiter-docs` and `neovara-homepage` — every consumer of the
  shared `workspace` volume — were unavailable for a further ~20 hours, after service had already
  been restored.
- `kiroku-api` and `ratelimiter-docs` each accumulated 234 restarts; devbox never started, holding
  `CreateContainerConfigError`.
- The outage was confined to that one volume. No other Longhorn volume on `k3s-worker-1` entered a
  read-only or shut-down state, and no other namespace was affected.
- The ext4 journal was aborted mid-write, but replayed cleanly on remount with no data loss.

Classified SEV-3 rather than SEV-2 because the control plane and the storage system itself both
stayed healthy, the affected workloads are development-tier, and no data was lost or put at
irreversible risk. The duration alone would otherwise argue for SEV-2.

## Detection

The first signal received by a human was the operator noticing devbox was unreachable, about 46
hours after onset, while doing unrelated work. That is a detection failure, not a monitoring
failure.

Prometheus detected the outage correctly and immediately. Querying after recovery
(`max_over_time(ALERTS{alertstate="firing"}[72h])`) confirms the following fired during the window:

| Alert | Severity | Object |
| --- | --- | --- |
| `KubeNodeNotReady` | warning | `k3s-worker-1` |
| `KubeNodeUnreachable` | warning | `k3s-worker-1` |
| `KubePodNotReady` | warning | 16 namespaces |
| `TargetDown` | warning | `kube-system` |

None were delivered. Alertmanager's default route still terminates at `receiver: "null"` — the
identical defect recorded in INC-2026-009 and tracked as issue #92, which was open at the time of
this incident and remains so. This is now the second consecutive incident whose entire duration is
attributable to undelivered alerts rather than to undetected conditions.

Phase 2 was likewise found by the operator rather than announced by the platform, and produced no
new class of signal: the affected pods sat in `CreateContainerConfigError` and `CrashLoopBackOff`
(234 restarts each) for nearly twenty hours, which `KubePodNotReady` would have reported had #92
not been discarding it.

The signal that would have detected this sooner already exists and already fired. Nothing new needs
to be written; #92 needs to be closed.

## Timeline

All times UTC.

| Time | Event |
| --- | --- |
| 2026-09-12 12:18:58 | `k3s-worker-1` Ready condition transitions to Unknown. `pve-dell` and `k3s-worker-1` both stop reporting to the tailnet |
| 2026-09-12 12:23:58 | 300s `node.kubernetes.io/unreachable:NoExecute` toleration expires; pods on the node begin eviction |
| 2026-09-12 12:24:05 | Longhorn marks the devbox replica `failedAt`; the volume becomes `detached/faulted` |
| 2026-09-12 ~12:24 | Replacement pods schedule onto `k3s-worker-3` and enter `ContainerCreating`, where they remain for the next two days. Prometheus alerts fire and are discarded |
| 2026-09-14 ~11:06 | Operator restarts `pve-dell` |
| 2026-09-14 11:07:54 | `k3s-worker-1` reachable over SSH, uptime 1 minute. `k3s-agent` active; `/var/lib/longhorn` correctly mounted from `sdb` (650G, label `k3s-data`) |
| 2026-09-14 11:08:32 | `longhorn-manager` on `k3s-worker-1` exits fatal: conflict on `settings.longhorn.io "default-replica-count"`. Longhorn node stays `Ready=False / ManagerPodDown` |
| 2026-09-14 11:08:02 | `k3s-worker-1` Kubernetes node reports `Ready=True` while Longhorn on it is still down |
| 2026-09-14 ~11:08:46 | A `longhorn-manager` restart finally starts cleanly (41st restart); manager reaches 2/2, CSI plugin 3/3, instance-manager Running |
| 2026-09-14 11:09:23 | Longhorn node `Ready=True`; devbox volume transitions `detached/faulted` → `detached/unknown` |
| 2026-09-14 11:09:49 | Auto-salvage completes; volume `attached/healthy` |
| 2026-09-14 ~11:09:12 | New pod `devbox-6f4c8f448f-spj8k` scheduled to `k3s-worker-1` |
| 2026-09-14 11:09:5x–11:11:58 | 8 × `FailedAttachVolume` — "the volume is currently attached to different node k3s-worker-3" — while the stale RWO attachment from the failover drains, then `SuccessfulAttachVolume` |
| 2026-09-14 11:13:53 | Image pulled from the in-cluster registry in 130ms; container created and started |
| 2026-09-14 11:14:41 | devbox `1/1 Running`. Phase 1 service restored |
| **Phase 2 — recovery-induced outage** | |
| 2026-09-15 10:42:33 | devbox volume patched to `numberOfReplicas: 2` **while attached, mounted, and being written**. Longhorn begins a pre-rebuild purge of two ~25 GiB removed snapshots |
| 2026-09-15 10:44:05 | Engine logs the first `failed to start rebuild ... context deadline exceeded`; purge continues underneath and advances normally |
| 2026-09-15 10:49:46 | `device offline error, dev sdw` — the Longhorn block device drops out from under the mounted filesystem. `Aborting journal on device sdw-8` |
| 2026-09-15 10:49:50 | `EXT4-fs error (device sdw): Detected aborted journal`; `I/O error while writing superblock`; `Remounting filesystem read-only` |
| 2026-09-15 10:49:52 | `EXT4-fs (sdw): shut down requested (2)`. All I/O to the volume now returns `EIO`; seven kubelet mounts are left flagged `emergency_ro,shutdown` |
| 2026-09-15 10:50:27 | Purge counter resets from 100% to 2%; the new replica is abandoned and the volume returns to a single replica |
| 2026-09-15 ~10:50 onward | devbox `CreateContainerConfigError` ("failed to prepare subPath"); `kiroku-api` and `ratelimiter-docs` accumulate 234 restarts each |
| 2026-09-16 ~06:20 | Longhorn independently completes a second rebuild; volume reports `healthy` with 2 replicas while the client-side filesystem remains shut down |
| 2026-09-16 06:39 | All four workspace consumers scaled to 0 to release the stale mounts |
| 2026-09-16 06:40:38 | Old device unmounted; filesystem remounts clean at 06:40:39, `r/w with ordered data mode`, no journal-recovery or orphan-inode errors |
| 2026-09-16 06:41 | Inspection: `Filesystem state: clean`, no `lost+found`, all six required subPaths present and populated. Full `fsck` judged unnecessary |
| 2026-09-16 ~06:45 | Consumers scaled back up. `kiroku` and `ratelimiter-docs` schedule onto `k3s-worker-3` and fail with `Multi-Attach error` against the RWO volume held on `k3s-worker-1` |
| 2026-09-16 06:52 | Both pods deleted and rescheduled onto `k3s-worker-1`. All five consumers `1/1 Running`; read and write verified from inside devbox. Volume `attached/healthy`, 2 replicas |

## Technical root cause

Three independent failures compose here, and only the first is about the hardware.

**1. The hypervisor outage (trigger, cause undetermined).** `pve-dell` stopped running at 12:18 UTC
on 2026-09-12. Both the hypervisor and its guest dropped off the tailnet within the same window,
which is consistent with the physical host losing power, suspending, or losing its network
uplink — the available evidence does not distinguish between these, and the machine was restarted
before any state could be captured from it. **This is recorded as undetermined, not diagnosed.**
Notably, the host came back with `/var/lib/longhorn` correctly mounted from `sdb` and `k3s-agent`
healthy, so there is no evidence of storage or filesystem damage.

**2. Single-replica policy converted a node outage into a data outage (the amplifier).** This is the
substantive cause of the impact duration and breadth. Under ADR-0051 every Longhorn volume holds
one data copy. A volume with two copies would have gone `degraded` and continued serving from the
surviving node; a volume with one copy goes `faulted` and serves nothing. Kubernetes did everything
correctly — eviction, rescheduling, retrying attachment — and none of it could help, because the
bytes existed in exactly one place and that place was switched off. The pods were not failing; they
were waiting for storage that could not exist until the hardware returned.

**3. Longhorn could not restart cleanly (the recovery blocker).** After the host returned, the
Kubernetes node reported `Ready=True` at 11:08:02 while Longhorn on it was still entirely down —
a genuinely misleading pair of signals. `longhorn-manager` was in CrashLoopBackOff, and the
previous container's final log line gives the reason:

```
level=fatal msg="Error starting manager: Operation cannot be fulfilled on
settings.longhorn.io \"default-replica-count\": the object has been modified;
please apply your changes to the latest version and try again"
```

Longhorn treats an optimistic-concurrency conflict while initialising its default `Setting` objects
as **fatal** rather than retrying the write. The observed fact is the fatal exit and the 41
restarts. The mechanism — both `longhorn-manager` pods racing to initialise the same `Setting`
objects on startup, with the loser dying — is the most plausible reading of the error and matches
its timing, but was not directly instrumented and is recorded here as a **hypothesis**. Either way
the escape path was luck: the pod recovered only when a restart happened to win the race, which
is why 41 restarts were needed and why the delay was arbitrary rather than bounded.

The `driver.longhorn.io not found in the list of registered CSI drivers` errors visible in the
kubelet log during this window were a red herring — normal boot-ordering noise that cleared once
the CSI plugin registered.

**4. The remediation itself caused a second, longer outage (phase 2).** Service had already been
restored when the devbox volume was patched to `numberOfReplicas: 2` *in place* — attached, mounted,
and with five pods actively writing to it. That patch obliged Longhorn to purge two ~25 GiB removed
snapshots before it could rebuild, and seven minutes into that work the block device went offline
beneath the live filesystem:

```
10:49:46  device offline error, dev sdw, sector 83886096 op 0x1:(WRITE)
10:49:46  Aborting journal on device sdw-8.
10:49:50  EXT4-fs error (device sdw): Detected aborted journal
10:49:50  EXT4-fs (sdw): Remounting filesystem read-only
10:49:52  EXT4-fs (sdw): shut down requested (2)
```

`device offline error` is not a media fault; it means the device vanished from under a mounted
filesystem. ext4 responded correctly — abort the journal, go read-only, then refuse all I/O — and
that `shutdown` state is terminal until the filesystem is unmounted. That is why the condition
survived Longhorn's own recovery: by 06:20 the next morning the volume reported `healthy` with two
replicas at the block layer while every kubelet mount above it was still dead, so the pods kept
failing with `failed to prepare subPath` against a filesystem that could not be read.

The exact sector-level mechanism linking the purge to the device drop was not instrumented and is
recorded as a **hypothesis**; the observed facts are the timing (patch at 10:42:33, purge running,
device offline at 10:49:46), the purge counter resetting from 100% to 2% immediately afterwards, and
the abandoned replica. The operational lesson does not depend on resolving that gap: a replica
promotion is a **data-movement operation**, not a metadata edit, and running one against a hot
volume put a working service back into outage for twenty hours. Scaling the consumers to zero first
is what eventually resolved it, and is what should have preceded the patch.

**5. Five pods share one RWO volume with no co-scheduling constraint (latent, exposed by phase 2).**
`devbox`, `kiroku`, `kiroku-api`, `ratelimiter-docs` and `neovara-homepage` all mount the single
`workspace` PVC through different subPaths. `ReadWriteOnce` permits exactly one node, but nothing in
the manifests pins these pods together — there is no `nodeSelector` or `nodeAffinity` anywhere in
`k8s/workbench/manifests/`. On the scale-up, the scheduler placed two of them on `k3s-worker-3`
while the volume was held by `k3s-worker-1`, and they failed hard:

```
Multi-Attach error for volume "pvc-a17050e8-..." Volume is already used by
pod(s) devbox-..., neovara-homepage-...
```

They were recovered by deleting them until they happened to land on the right node. That is not a
control; it is a coin flip that came up heads. Any future reschedule — eviction, node drain, another
host outage — can split this set again.

## Contributing factors

- Alertmanager's default `"null"` route (#92) meant a correctly-detected two-day outage produced no
  notification. This is the dominant contributing factor to the duration.
- `pve-dell` is a laptop, and `k3s-worker-1` is its only guest, so any loss of that single physical
  machine removes an entire worker's worth of storage at once.
- ADR-0021's constraint means the external USB SSD is the only usable storage on that host, so a
  physical disconnection is a plausible failure mode with no redundancy behind it.
- A Kubernetes node reporting `Ready=True` while Longhorn on it is `Ready=False` makes "is the node
  back?" ambiguous during recovery; checking only `kubectl get nodes` would have suggested success.
- The workbench StorageClass's replica count cannot be raised in place — `parameters` is an
  immutable field on `StorageClass` — so the fix necessarily operates per-volume rather than by
  editing a class.
- Raising `numberOfReplicas` presents as a one-field patch but commits Longhorn to purging and
  copying tens of gigabytes, with no warning, confirmation, or dry-run distinguishing it from a
  metadata edit.
- The `workspace` volume carried two ~25 GiB snapshots marked for removal, so the promotion's
  pre-rebuild purge was far larger than the volume's nominal 50 GiB size would suggest.
- Five Deployments share one `ReadWriteOnce` volume with no co-scheduling constraint, so correct
  placement depends entirely on the scheduler happening to keep them together.
- Nothing monitors filesystem-level health, so a shut-down ext4 mount beneath a `healthy` Longhorn
  volume produced no signal at all.

## Resolution and recovery

Service was restored by restarting `pve-dell`, after which Longhorn's own automation did the rest
once its manager escaped the crash loop:

1. `longhorn-manager` reached 2/2 → Longhorn node `Ready=True`.
2. `auto-salvage: true` (verified enabled) cleared the `failedAt` flag on the sole replica and
   brought the volume to `attached/healthy` without operator action.
3. The stale RWO attachment to `k3s-worker-3` drained after 8 retries and the volume attached to
   `k3s-worker-1`.
4. The pod pulled its image and started; readiness passed at 11:14:41 UTC.

Recovery was verified by the volume reporting `attached/healthy` and the devbox pod reporting
`1/1 Running`, not by inference from the node condition alone.

The devbox workspace volume was subsequently promoted to two data copies — the mechanism ADR-0051
explicitly prescribes for this situation ("extra copies are enabled by promoting the selected
existing Longhorn Volume, not inferred from its old StorageClass name"):

```
kubectl -n longhorn-system patch volumes.longhorn.io <volume> \
  --type=merge -p '{"spec":{"numberOfReplicas":2}}'
```

The rebuild is preceded by a snapshot purge of two ~25 GiB removed snapshots. The engine logs
`failed to start rebuild ... context deadline exceeded` repeatedly during this phase; that is the
rebuild-start call timing out while the purge runs underneath, not a stall — `purgeStatus` shows
`state=in_progress` with advancing progress throughout.

**This patch was applied to the volume while it was attached, mounted and being written, and that
is what caused phase 2.** It should have been preceded by scaling the consumers to zero.

Phase 2 was resolved by doing exactly that, then inspecting before repairing:

1. All four workspace Deployments scaled to `0`, releasing the seven `emergency_ro,shutdown` mounts.
   ext4's `shutdown` state cannot be cleared in place; unmounting is the only exit.
2. The volume detached and reattached, and the filesystem remounted clean —
   `r/w with ordered data mode`, with no journal-recovery or orphan-inode messages.
3. Integrity was inspected **before** any repair was attempted: `tune2fs -l` reported
   `Filesystem state: clean`, there was no `lost+found`, and all six required subPaths (`home`,
   `kiroku`, `kiroku/backend`, `kiroku/data`, `ratelimiter`, `neovara-homepage`) were present and
   populated. A full `fsck` was therefore judged unnecessary rather than run reflexively — the
   deliberate choice being that an unnecessary `fsck` on a backup-less volume is itself a risk.
4. Consumers scaled back up. `kiroku` and `ratelimiter-docs` were scheduled onto `k3s-worker-3` and
   failed with `Multi-Attach error`; deleting them returned both to `k3s-worker-1`.

Recovery was verified from inside the running devbox container — all eleven repositories listed
under `/workspace`, `/home/vscode` populated, and a write test succeeded — not merely from the
volume reporting `healthy`, which had already proved misleading for twenty hours.

## What went well

- Longhorn's `auto-salvage` recovered a faulted single-replica volume with no operator action and
  no data loss.
- The control plane survived the outage entirely, because ADR-0049's move of `k3s-server-1` to
  `pve-asrock` had already removed the dependency on `pve-dell`. This is that decision paying off.
- Prometheus's detection was correct, immediate, and required no new rules.
- The data disk remounted cleanly from `sdb` on boot, confirming the `longhorn_node` role's
  mount-before-k3s ordering held across an unclean shutdown.
- No data was lost despite every affected volume having exactly one copy.
- In phase 2, the filesystem was inspected before being repaired. `Filesystem state: clean` and the
  intact subPath tree made a `fsck` demonstrably unnecessary, so a backup-less volume was never
  exposed to one.
- Phase 2's damage was contained to a single volume; no other Longhorn device on the node was
  affected.

## What did not go well

- A two-day outage was found by a human stumbling into it, not by a notification.
- `longhorn-manager` turned a transient write conflict into a 41-restart crash loop with an
  unbounded, luck-dependent escape.
- `kubectl get nodes` reporting `Ready` while the node's storage layer was fully down sent recovery
  in the wrong direction initially.
- The first assumption during triage was that a scale-up command had caused the problem; it had not,
  and the pod had already been failing for 46 hours.
- The remediation was applied to a hot volume and caused a second outage roughly ten hours longer
  than a safe maintenance window would have cost. The preventive action became the incident.
- For nearly twenty hours Longhorn reported the volume `healthy` while it was entirely unusable.
  Volume-level health and filesystem-level health are different properties, and only one of them was
  being watched.
- Recovery of the two stranded pods was achieved by deleting them until the scheduler chose the
  right node, which is not a repeatable procedure.

## Where we got lucky

- The `pve-dell` host came back on the first restart attempt, with its filesystem and Longhorn data
  disk intact. Nothing about this incident established that the underlying hardware fault is
  understood or will not recur.
- The single surviving devbox replica was healthy enough to salvage. A single-copy volume whose one
  copy is damaged has no recovery path in this design, and ADR-0063 explicitly provides the
  workspace no backups.
- `longhorn-manager` eventually won its startup race. Nothing bounds how long that could have taken.
- The ext4 journal abort in phase 2 cost no data. A journal aborted mid-write can require `fsck` and
  can surface files in `lost+found`; this one replayed clean on remount. ADR-0063 gives this volume
  no backups, so a worse outcome had no recovery path beyond whatever was already pushed to GitHub.
- The two stranded pods landed on the correct node on the first delete. Nothing forced that.

## Corrective and preventive actions

| Priority | Action | Owner | Status | Completion evidence |
| --- | --- | --- | --- | --- |
| P0 | Close #92: route Alertmanager's default receiver somewhere a human actually reads. Second consecutive incident extended solely by undelivered alerts | Harsh | Open | A deliberately-failed test alert observed arriving at its destination |
| P1 | Promote the devbox workspace volume to `numberOfReplicas: 2` so a `pve-dell` outage degrades rather than faults it | Harsh | Done | Volume reports `attached/healthy`, `spec.numberOfReplicas: 2`, replicas running on both `k3s-worker-1` and `k3s-worker-3`; read and write verified from inside devbox 2026-09-16 06:52 UTC |
| P1 | Co-schedule the five `workspace` consumers, or stop sharing one RWO volume between five Deployments. Without this, any reschedule can strand pods with `Multi-Attach error` | Harsh | Open | All five pods provably confined to one node by configuration, verified by draining the other worker |
| P1 | Never promote a replica count on a hot volume again: scale consumers to zero, promote, verify `healthy`, scale up. Record this as a runbook | Harsh | Open | Runbook committed under `docs/runbooks/`, referenced from ADR-0051 |
| P1 | Review which other single-copy volumes justify promotion, per ADR-0051's per-volume model — particularly the in-cluster registry, which blocks image pulls cluster-wide when unavailable | Harsh | Open | A recorded decision per volume, with resulting replica counts |
| P2 | Determine why `pve-dell` stopped. Until this is known the trigger is unaddressed and recurrence is unbounded | Harsh | Open | Root cause identified, or monitoring added that would capture the next occurrence |
| P2 | Add an alert on Longhorn node readiness (`longhorn_node_status{condition="ready"}`) distinct from Kubernetes node readiness, so the divergence seen at 11:08 is visible | Harsh | Open | Alert rule committed and verified firing against a drained Longhorn node |
| P2 | Alert on filesystem-level failure (`node_filesystem_readonly`, or kernel `EXT4-fs error` via Loki) so a shut-down filesystem under a `healthy` Longhorn volume is not invisible for twenty hours | Harsh | Open | Alert fires against a deliberately remounted-read-only test volume |
| P2 | Check whether the `longhorn-manager` startup-conflict fatal is a known upstream defect in v1.12.0 and whether a later release retries instead | Harsh | Open | Upstream issue linked, or reproduction documented |
| P2 | Address the pre-existing `Multipathd=False` and `KernelModulesLoaded=False (dm_crypt)` conditions on the Longhorn nodes; multipathd is a documented Longhorn data-corruption risk | Harsh | Open | Both conditions report healthy on all nodes |

## Lessons and review questions

The reusable lesson is that **replica count is not a performance knob, it is the difference between
a degraded service and an unavailable one.** ADR-0051 traded automatic node-loss continuity for
capacity and deliberate per-volume decisions, and that trade is defensible — but it only works if
the per-volume promotion step actually happens for the volumes that matter. This incident is what
the deferred half of that decision costs when it is never revisited: a routine host restart became
a two-day outage across sixteen namespaces.

The second lesson is that detection and notification are different systems, and only one of them
was working. Two consecutive incidents have now had their entire duration determined by #92.

The third lesson is the sharpest, because it was self-inflicted: **the fix caused a longer outage
than the fault.** Raising a replica count reads like a configuration edit, but it commits Longhorn
to moving tens of gigabytes, and running that against an attached, mounted, actively-written volume
took a recovered service back down for twenty hours. The safe version of the same change — scale to
zero, promote, verify, scale up — was available throughout and costs minutes. Preventive work
deserves the same maintenance-window discipline as the incident it is meant to prevent.

The fourth is that a healthy storage layer does not imply a usable one. Longhorn reported
`robustness: healthy` for most of phase 2 while every consumer was failing, because the damage lived
in the ext4 mount above the block device, which nothing was watching.

Review questions:

- Why does a `faulted` Longhorn volume block pod startup entirely rather than surfacing an error to
  the pod? What is the difference between `faulted`, `degraded`, and `detached` in Longhorn's state
  machine, and which are recoverable without operator action?
- Why are `StorageClass.parameters` immutable in Kubernetes, when most object fields are not? What
  would break if they were mutable, given PVs record their parameters at provision time?
- What is the RWO attachment lifecycle that produced 8 × `FailedAttachVolume` before succeeding, and
  what releases the old `VolumeAttachment` when the holding node was unreachable?
- Why does a snapshot purge have to complete before a rebuild starts, and why are removed snapshots
  ~25 GiB each on a 50 GiB volume?
- What does ext4's `shutdown` state mean, why is it terminal until unmount, and how does it differ
  from `remount-ro`? Why did the journal replay clean on remount rather than requiring `fsck`?
- Why can a Longhorn volume report `robustness: healthy` while every filesystem mounted on it is
  returning `EIO`? What is the boundary between the two layers' health models?
- What does `ReadWriteOnce` actually constrain — node, pod, or mount — and why can five pods share
  one RWO volume successfully on one node but not across two?
- Under what circumstances is a fatal exit the correct response to a Kubernetes write conflict, and
  what should Longhorn have done instead?

## Evidence

- `kubectl get node k3s-worker-1 -o jsonpath='{.status.conditions[?(@.type=="Ready")]}'` — status
  `Unknown`, `lastTransitionTime: 2026-09-12T12:18:58Z`
- `tailscale status` — `k3s-worker-1` and `pve-dell` both `offline, last seen 1d ago`; `pve-asrock`,
  `k3s-server-1`, `k3s-worker-3` online
- Longhorn `Volume/pvc-a17050e8-b356-46d0-9957-400a42e94279` — `detached/faulted`, sole replica
  `pvc-a17050e8-...-r-61bc0ebc` on `k3s-worker-1`, `failedAt: 2026-09-12T12:24:05Z`
- `StorageClass/longhorn-workbench` — `numberOfReplicas: "1"`,
  `k8s/longhorn/manifests/storageclass-workbench.yaml`
- Server dry-run confirming immutability: `The StorageClass "longhorn-workbench" is invalid:
  parameters: Invalid value: {...}: field is immutable`
- `longhorn-manager-fd65x` container `longhorn-manager`, previous log, final line — fatal on
  `settings.longhorn.io "default-replica-count"` conflict; 41 restarts recorded
- `Node/k3s-worker-1` (longhorn.io) conditions — `Ready=False / ManagerPodDown`, alongside
  pre-existing `Multipathd=False` and `KernelModulesLoaded=False`
- Pod events for `devbox-6f4c8f448f-spj8k` — 8 × `FailedAttachVolume` ("currently attached to
  different node k3s-worker-3"), then `SuccessfulAttachVolume`
- Prometheus `max_over_time(ALERTS{alertstate="firing"}[72h])` — `KubeNodeNotReady`,
  `KubeNodeUnreachable` for `k3s-worker-1`; `KubePodNotReady` across 16 namespaces
- `alertmanager-kube-prometheus-stack-alertmanager` secret — default `receiver: "null"`
- Phase 2 kernel log on `k3s-worker-1` — `device offline error, dev sdw`; `Aborting journal on
  device sdw-8`; `EXT4-fs error (device sdw): Detected aborted journal`; `Remounting filesystem
  read-only`; `shut down requested (2)`, all within 2026-09-15 10:49:46–10:49:52
- Seven kubelet mounts of `pvc-a17050e8-...` flagged `emergency_ro,shutdown`; confined to this one
  volume (no other Longhorn device on the node affected)
- Clean remount 2026-09-16 06:40:39 — `EXT4-fs (sdt): mounted filesystem ... r/w with ordered data
  mode`, no journal-recovery or orphan-inode messages
- `tune2fs -l` after recovery — `Filesystem state: clean`; no `lost+found` directory present
- Post-recovery verification from inside `devbox` — eleven repositories under `/workspace`,
  `/home/vscode` populated, write test succeeded
- Pod events for `kiroku` and `ratelimiter-docs` on scale-up — `Multi-Attach error for volume
  "pvc-a17050e8-..." Volume is already used by pod(s) devbox-..., neovara-homepage-...`
- Absence of any `nodeSelector`/`nodeAffinity` in `k8s/workbench/manifests/`
- Related: INC-2026-009 (same `"null"` route, issue #92), INC-2026-003 (Longhorn rebuild I/O — the
  closest precedent for phase 2), ADR-0051 (one data copy by default; per-volume promotion),
  ADR-0049 (control-plane move off `pve-dell`), ADR-0063 (no backups for the workspace)
