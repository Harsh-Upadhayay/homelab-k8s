# INC-2026-010: A `pve-dell` outage took every single-replica volume on `k3s-worker-1` down for two days

## Incident metadata

| Field | Value |
| --- | --- |
| Date | 2026-09-12 |
| Severity | SEV-3 |
| Status | Resolved; alert delivery (#92) remains open; cause of the `pve-dell` outage undetermined |
| Systems | `pve-dell`, `k3s-worker-1`, Longhorn v1.12.0, workbench (devbox, registry, `ais-*`, `kiroku*`, `neovara-homepage`, `pwm`, `ratelimiter-docs`) |
| Start | 2026-09-12 12:18:58 UTC (`k3s-worker-1` Ready condition → Unknown) |
| End | 2026-09-14 11:14:41 UTC (devbox pod `1/1 Running`) |
| Duration | Approximately 46 hours 56 minutes |
| Detection | User noticed devbox was unreachable while working, roughly 46 hours after onset. Prometheus fired `KubeNodeNotReady` and `KubeNodeUnreachable` correctly throughout; Alertmanager's default route discarded them |
| Data impact | No loss. The single devbox replica was auto-salvaged intact and the volume returned `attached/healthy` before any pod wrote to it |

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
| 2026-09-14 11:14:41 | devbox `1/1 Running`. Service restored |
| 2026-09-15 10:42:33 | devbox volume promoted to `numberOfReplicas: 2`; second replica created on `k3s-worker-3` |

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

## What went well

- Longhorn's `auto-salvage` recovered a faulted single-replica volume with no operator action and
  no data loss.
- The control plane survived the outage entirely, because ADR-0049's move of `k3s-server-1` to
  `pve-asrock` had already removed the dependency on `pve-dell`. This is that decision paying off.
- Prometheus's detection was correct, immediate, and required no new rules.
- The data disk remounted cleanly from `sdb` on boot, confirming the `longhorn_node` role's
  mount-before-k3s ordering held across an unclean shutdown.
- No data was lost despite every affected volume having exactly one copy.

## What did not go well

- A two-day outage was found by a human stumbling into it, not by a notification.
- `longhorn-manager` turned a transient write conflict into a 41-restart crash loop with an
  unbounded, luck-dependent escape.
- `kubectl get nodes` reporting `Ready` while the node's storage layer was fully down sent recovery
  in the wrong direction initially.
- The first assumption during triage was that a scale-up command had caused the problem; it had not,
  and the pod had already been failing for 46 hours.

## Where we got lucky

- The `pve-dell` host came back on the first restart attempt, with its filesystem and Longhorn data
  disk intact. Nothing about this incident established that the underlying hardware fault is
  understood or will not recur.
- The single surviving devbox replica was healthy enough to salvage. A single-copy volume whose one
  copy is damaged has no recovery path in this design, and ADR-0063 explicitly provides the
  workspace no backups.
- `longhorn-manager` eventually won its startup race. Nothing bounds how long that could have taken.

## Corrective and preventive actions

| Priority | Action | Owner | Status | Completion evidence |
| --- | --- | --- | --- | --- |
| P0 | Close #92: route Alertmanager's default receiver somewhere a human actually reads. Second consecutive incident extended solely by undelivered alerts | Harsh | Open | A deliberately-failed test alert observed arriving at its destination |
| P1 | Promote the devbox workspace volume to `numberOfReplicas: 2` so a `pve-dell` outage degrades rather than faults it | Harsh | In progress | Volume reports `robustness: healthy` with replicas on both `k3s-worker-1` and `k3s-worker-3` |
| P1 | Review which other single-copy volumes justify promotion, per ADR-0051's per-volume model — particularly the in-cluster registry, which blocks image pulls cluster-wide when unavailable | Harsh | Open | A recorded decision per volume, with resulting replica counts |
| P2 | Determine why `pve-dell` stopped. Until this is known the trigger is unaddressed and recurrence is unbounded | Harsh | Open | Root cause identified, or monitoring added that would capture the next occurrence |
| P2 | Add an alert on Longhorn node readiness (`longhorn_node_status{condition="ready"}`) distinct from Kubernetes node readiness, so the divergence seen at 11:08 is visible | Harsh | Open | Alert rule committed and verified firing against a drained Longhorn node |
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
- Related: INC-2026-009 (same `"null"` route, issue #92), INC-2026-003 (Longhorn rebuild I/O),
  ADR-0051 (one data copy by default), ADR-0049 (control-plane move off `pve-dell`), ADR-0063
  (no backups for the workspace)
