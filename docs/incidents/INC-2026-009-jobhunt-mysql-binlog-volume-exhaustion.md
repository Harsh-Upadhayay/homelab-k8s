# INC-2026-009: MySQL binary logs filled the job-hunt data volume and wedged every write

## Incident metadata

| Field | Value |
| --- | --- |
| Date | 2026-09-08 |
| Severity | SEV-3 |
| Status | Resolved; alert delivery (#92) and replica locality remain open. Detection findings corrected 2026-09-09 |
| Systems | job-hunt (Django, MySQL 8, Celery), Longhorn v1.12.0, `k3s-worker-1`, `k3s-worker-3` |
| Start | 2026-09-07 20:56 JST (reconstructed from the oldest wedged transaction's 82,069s age) |
| End | 2026-09-08 20:03 JST (Django served `/api/jobs/stats/` 200) |
| Duration | Approximately 23 hours of total application unavailability |
| Detection | User reported "can't login to jobhunt". Prometheus fired the correct alerts throughout, but Alertmanager routed them to the `"null"` receiver, so none were delivered |
| Data impact | No loss. Freeing space let the 151 in-flight transactions commit rather than roll back; `jobhunt_db.jobs_job` verified at 54,086 rows afterwards. Eleven rotated binary logs were deliberately deleted, discarding point-in-time-recovery material that had no configured consumer |

## Executive summary

The `mysql-data` Longhorn volume reached 100% capacity (7.8G of 8Gi). With no free space InnoDB
could not complete a commit, so every write transaction parked in `waiting for handler commit`
indefinitely and never released its connection. The pile-up exhausted `max_connections = 151`, and
the Django pod — which opens a database connection during its startup system check — began failing
with `OperationalError: (1040, 'Too many connections')` and entered CrashLoopBackOff. No API pod
meant no login. What filled the volume was binary logs, not application data: `jobhunt_db` occupied
1.4G while eleven rotated binlogs totalled roughly 6.1G, retained by MySQL's 30-day default
`binlog_expire_logs_seconds` against an app writing about 1 GB of binlog per day. Deleting the
rotated binlogs freed 5.7G, the wedged transactions committed, connections drained from 151 to 3,
and Django recovered on its own. The volume was then expanded 8Gi to 58Gi, which required
working around a stale-PID defect in the Longhorn engine described below.

## Impact

- job-hunt was completely unavailable to users for roughly 23 hours; login returned no response
  because the Django Service had no ready endpoint.
- The Django Deployment sat at `0/1 AVAILABLE` with 275 restarts accumulated.
- All MySQL write traffic was stalled for the duration, including Celery Beat's periodic-task
  bookkeeping and Celery Worker's job ingestion.
- `celery-beat` and `celery-worker` remained Running throughout and were not themselves crash-looping,
  which is part of why the failure was quiet.
- Committed data was unaffected, and the 151 stalled transactions ultimately committed successfully.
- No other namespace was affected. The 13 other Longhorn volumes attached to `k3s-worker-1`
  remained available throughout, which constrained the remediation options described below.

Classified SEV-3 rather than SEV-2 because job-hunt is a personal-tier application rather than a
critical service, control plane, or storage system, and because no data was lost or put at
irreversible risk. The duration alone would otherwise argue for SEV-2.

## Detection

The first signal *received by a human* was a user report that login did not work. This was initially
recorded as "nothing alerted", which is wrong and was corrected on 2026-09-09.

Prometheus detected this incident correctly and promptly. Querying
`max_over_time(ALERTS{namespace="job-hunt",alertstate="firing"}[36h])` after recovery shows the
following fired during the outage:

| Alert | Severity | Object |
| --- | --- | --- |
| `KubePersistentVolumeFillingUp` | critical | `job-hunt/mysql-data` |
| `KubePodCrashLooping` | warning | `django-57c4bcd54f-dqf2n` |
| `KubePodNotReady` | warning | `django-57c4bcd54f-dqf2n` |
| `KubeDeploymentReplicasMismatch` | warning | `job-hunt/django` |

The stock kube-prometheus-stack rules named the root cause — a filling PersistentVolume — at critical
severity, not merely the downstream symptom. The detection layer worked exactly as intended.

**The delivery layer does not exist.** Alertmanager's only receiver is the chart's placeholder
`"null"`, and the root route sends every alert to it:

```yaml
receivers:
- name: "null"
route:
  receiver: "null"
```

Every alert this cluster has produced in 65 days has been discarded. At the time of writing, 22
alerts are firing and undelivered, including a critical `KubePersistentVolumeFillingUp` on
`immich/immich-library`, which sits at 2.7% free of 350Gi — the same failure shape as this incident,
on a volume 40 times larger.

The failure was silently visible in the cluster for roughly a day: a Deployment stuck at zero
available replicas, a pod with hundreds of restarts, and a PersistentVolumeClaim at 100% usage. Any
one of those is a straightforward alert. The most valuable of the three is volume-fullness, because
it fires *before* user impact — the volume filled at approximately 20:56 JST on 2026-09-07 and the
symptom the user noticed was a consequence that only mattered once someone tried to log in.

No new rule would have helped here. The rule already existed, already fired, and already carried the
right severity. What was missing was any path from Alertmanager to a human. This is the fourth
capacity-exhaustion incident in this cluster's log (INC-2026-005, -006, -008), and the previous three
each produced a narrow "add an alert rule" issue (#51, #53, #54) that never shipped — all three were
scoped to the layer that was not broken. Tracked correctly now as #92.

## Timeline

All times JST. Times before 19:35 are reconstructed from log ages and file mtimes.

| Time | Event |
| --- | --- |
| 2026-09-07 ~20:56 | Volume reaches 100%; oldest transaction enters `waiting for handler commit` and never leaves (reconstructed from its 82,069s age) |
| 2026-09-07 ~21:00 onward | Connections accumulate as each new write wedges; Django restarts begin failing on error 1040 |
| 2026-09-08 19:35 | User reports "can't login to jobhunt"; investigation begins |
| 2026-09-08 ~19:38 | Django logs show `OperationalError: (1040, 'Too many connections')`; MySQL `SHOW PROCESSLIST` shows 151 sessions in `waiting for handler commit` |
| 2026-09-08 ~19:40 | `df` inside the MySQL pod shows `/var/lib/mysql` at 7.8G/7.8G, 0 available — root cause identified |
| 2026-09-08 ~19:41 | `du` attributes ~6.1G to eleven rotated binlogs against a 1.4G database |
| 2026-09-08 19:44 | PVC expansion to 58Gi rejected by `job-hunt-quota` (`requests.storage` capped at 10Gi) |
| 2026-09-08 ~19:45 | PR #90 raises the namespace storage quota to 64Gi; merged and synced by Argo CD |
| 2026-09-08 19:46 | PVC patched to 58Gi; Longhorn expands the replica successfully |
| 2026-09-08 19:48 | Frontend expansion fails: `nsenter: cannot open /host/proc/1215/ns/mnt`; resize enters a retry loop |
| 2026-09-08 ~19:52 | Stale-PID hypothesis confirmed — `iscsid` on `k3s-worker-1` now runs as PID 1985648, not the cached 1215 |
| 2026-09-08 ~19:56 | Decision: free space in place rather than hard-kill a wedged MySQL |
| 2026-09-08 19:57 | Eleven rotated binlogs deleted; free space goes from 0 to 5.7G; `binlog.index` rewritten to list only the active log |
| 2026-09-08 ~19:59 | Connection count falls 151 to 3 as the wedged transactions commit and release |
| 2026-09-08 20:03 | Django starts cleanly and serves `/api/jobs/stats/` 200 — **user impact ends** |
| 2026-09-08 20:07 | MySQL scaled to 0 to attempt offline expansion |
| 2026-09-08 ~20:10 | Longhorn reuses the same long-lived engine process; expansion still fails on the same stale PID |
| 2026-09-08 ~20:14 | MySQL scaled back to 1 but schedules onto `k3s-worker-3`; attach fails because the expansion ticket pins the volume to `k3s-worker-1` — brief second outage |
| 2026-09-08 ~20:18 | Volume force-detached (`spec.nodeID: ""`), terminating the stale engine process |
| 2026-09-08 ~20:20 | Fresh engine starts, resolves `iscsid` correctly, and completes the expansion; `expansionRequired` goes false |
| 2026-09-08 20:22 | MySQL Running on `k3s-worker-3`; filesystem reports 57G with 55G available |
| 2026-09-08 20:26 | Django ready 1/1; login endpoint returns 401 for bad credentials and 200 on `/api/jobs/stats/` |

## Technical root cause

The causal chain has four links, and only the first is the actual defect.

**1. Binary log retention was unbounded relative to the volume.** MySQL 8 defaults to
`binlog_expire_logs_seconds = 2592000` (30 days) with `max_binlog_size = 1G`. The job-hunt workload —
Celery workers continuously scraping and upserting job rows — generates roughly 1 GB of binlog per
day. Thirty days of retention therefore implies a ~30 GB steady-state binlog footprint on an 8Gi
volume holding a 1.4G database. The volume was guaranteed to fill; the only question was when.
Nothing purges early, because auto-purge is time-based and has no awareness of free space.

**2. A full volume converts InnoDB commits into permanent hangs, not errors.** This is the
non-obvious link. A commit must durably write the redo log; with zero bytes available that write
cannot complete and does not fail fast — the session blocks in `waiting for handler commit`
indefinitely. The oldest such session had been blocked for 82,069 seconds. Critically, a blocked
session still holds its connection slot.

**3. Wedged sessions exhaust the connection pool.** Each new write from Celery wedged the same way,
so the connection count climbed monotonically to `max_connections = 151` and stopped there. At that
point MySQL was still Running and its liveness probe still passed — it simply could not accept new
connections. The single `SUPER`-reserved slot masked the severity briefly, since `root` could still
connect for diagnosis until that slot was also consumed by a hung `SHOW BINARY LOGS`.

**4. Django opens a connection at startup, so it could not start.** Django's system check runs
`_check_sql_mode`, which requires a live connection to read `sql_mode`. With the pool exhausted this
raised `OperationalError: (1040, 'Too many connections')` before the server ever bound port 8000, the
startup probe got `connection refused`, and the pod crash-looped. The Service therefore had no ready
endpoint and login produced no response.

### Secondary defect: Longhorn engine cached a stale `iscsid` PID

Independent of the outage, expanding the volume exposed a distinct bug. Longhorn performs iSCSI
operations by `nsenter`-ing into the host's `iscsid` namespaces using a PID captured when the volume's
engine process started. The engine for this volume had cached PID 1215. `iscsid` on `k3s-worker-1`
had since restarted and was running as PID 1985648, so every frontend expansion attempt failed with:

```
fail to refresh iSCSI initiator: failed to execute: /usr/bin/nsenter
[nsenter --mount=/host/proc/1215/ns/mnt --net=/host/proc/1215/ns/net iscsiadm --version],
stderr nsenter: cannot open /host/proc/1215/ns/mnt: No such file or directory
```

The replica and backend expanded correctly every time; only the frontend refresh failed, so the
volume sat with `expansionRequired: true` retrying every five seconds. Scaling the workload to zero
did not help, because Longhorn auto-attached the volume for expansion and **reused the same
long-lived engine process**, stale PID included. Only fully detaching the volume terminated that
process; the replacement engine resolved `iscsid` correctly and the expansion completed immediately.

This is the same family as INC-2026-001, where an instance-manager retained a stale bind-mounted
view of `/var/lib/longhorn`. Both are Longhorn components caching a host reference that later became
invalid, and both were cleared by forcing the caching process to restart.

## Contributing factors

- The 8Gi volume was sized for the database, not for the database plus a month of binary logs. Nothing
  in the manifest expressed that binlogs are a first-class consumer of the same filesystem.
- Binary logging is enabled with no consumer: there is no replica, no configured point-in-time-recovery
  procedure, and no backup shipping the logs anywhere. The volume was being filled by material that
  nothing could use.
- Alertmanager has no configured receiver, so all alerting is silently discarded. The monitoring stack
  has been installed and correctly detecting problems for 65 days while delivering nothing, which is
  arguably worse than having no monitoring at all: it produced false confidence that the cluster was
  observed.
- Django's liveness and readiness probes both target `/api/jobs/stats/`, so a database-dependent
  failure presents identically to an application failure and offers no signal about which layer broke.
- MySQL's own liveness probe kept passing while the server was functionally unable to serve any new
  client, so Kubernetes' view of MySQL was "healthy" throughout.
- `job-hunt-quota` capped `requests.storage` at 10Gi, so the obvious remediation was blocked until a
  GitOps change was authored, merged, and synced. The quota is a deliberate and correct guardrail, but
  it sits on the recovery path during a storage incident.
- The `mysql` Deployment declares no node affinity, so an incidental reschedule moved it away from the
  node holding its only replica.

## Resolution and recovery

Service was restored by freeing space in place rather than by restarting a wedged MySQL. This choice
mattered: a hard restart would have forced InnoDB crash recovery and rolled back all 151 uncommitted
transactions, discarding roughly a day of Celery writes. Because `mysqld` held no open file
descriptors on the rotated binlogs (verified via `/proc/1/fd`), deleting them freed space immediately
and let those transactions commit normally.

1. Confirmed no replicas existed (`SHOW REPLICAS` empty), so rotated binlogs had no consumer.
2. Deleted `binlog.000001` through `binlog.000011`, keeping the active `binlog.000012`. Free space
   went from 0 to 5.7G.
3. Rewrote `binlog.index` to list only the active log, preserving the original as
   `binlog.index.bak-20260908`.
4. Watched the connection count fall from 151 to 3 as the stalled commits completed.
5. Django recovered without intervention and served `/api/jobs/stats/` 200.
6. Raised the namespace storage quota to 64Gi (PR #90) and expanded the PVC to 58Gi.
7. Force-detached the volume to terminate the engine holding the stale `iscsid` PID; the replacement
   engine completed the expansion.

Verification: all seven job-hunt pods Running and ready with Django at zero restarts; filesystem
reports 57G with 55G available (4% used); `POST /api/auth/login/` returns 401 for bad credentials,
confirming the authentication path reaches the database and rejects correctly; `/api/jobs/stats/` and
the frontend both return 200; `jobhunt_db.jobs_job` contains 54,086 rows.

## What went well

- The error message named the root cause layer precisely once `df` was consulted, and the
  `waiting for handler commit` state made the disk-full to connection-exhaustion link legible.
- Checking `/proc/1/fd` before deleting binlogs established that the space would actually be reclaimed
  and that `mysqld` was not writing to those files.
- Choosing to free space in place instead of restarting preserved a day of in-flight writes that a
  hard kill would have rolled back.
- The namespace quota did exactly what a quota is for — it refused an unreviewed 50Gi expansion and
  forced the change through Git.
- Verification tested the reported symptom (the login endpoint) rather than stopping at pod readiness.

## What did not go well

- No notification reached anyone for roughly 23 hours on a completely unavailable application, despite
  Prometheus correctly firing a critical alert naming the exact root cause.
- The initial version of this report concluded "nothing alerted" and proposed writing a new PVC alert
  rule. That diagnosis was wrong and would have wasted the corrective effort on the one layer that was
  working. It was only caught by checking the live Alertmanager config while filing the follow-up
  issue.
- The volume expansion turned a resolved incident into a second, self-inflicted outage: scaling MySQL
  down and back up while the expansion ticket pinned the volume to `k3s-worker-1` left the pod unable
  to attach for several minutes.
- The offline-expansion attempt was based on an incorrect assumption that detaching the workload would
  restart the engine process. It did not, and the wasted cycle extended the second outage.
- A diagnostic `SHOW BINARY LOGS` consumed MySQL's last reserved `SUPER` connection and hung,
  eliminating the remaining path for SQL-level diagnosis.

## Where we got lucky

- `mysqld` happened to hold no open descriptors on the rotated binlogs. Had it held them, deletion
  would have freed no space and the only remaining option would have been the destructive restart.
- The 151 stalled transactions were still committable. Had the volume stayed full much longer, or had
  MySQL been restarted at any point during the 23 hours, that work would have rolled back.
- The single replica happened to be healthy. Expanding, force-detaching, and reattaching a volume with
  `numberOfReplicas: 1` offers no redundancy if anything goes wrong mid-operation.
- The stale-PID defect affected only this volume's engine. Restarting the shared instance-manager to
  clear it would have disrupted the 13 other volumes attached to `k3s-worker-1`.

## Corrective and preventive actions

| Priority | Action | Owner | Status | Completion evidence |
| --- | --- | --- | --- | --- |
| P0 | Bound MySQL binary log growth in `deep-astaad/job-hunt` — either `binlog_expire_logs_seconds` cut to ~3 days or `skip-log-bin`, since there is no replica and no PITR consumer | Harsh | Open | MySQL config in the app repo plus `SHOW VARIABLES LIKE 'binlog_expire%'` output |
| P0 | Give Alertmanager a real receiver and route alerts to it instead of `"null"`, with severity-based routing and the noise backlog cleared first (#92) | Harsh | Open | A real notification received on a real device from a deliberately triggered alert |
| P0 | Add an earlier PVC warning tier at <25% free; the stock critical rule fires at <3%, which gave no usable warning window for a database | Harsh | Open | Rule in `values-kube-prometheus-stack.yaml` and a fired test alert |
| P1 | Triage `immich/immich-library` at 2.7% free of 350Gi — currently firing critical and undelivered | Harsh | Open | Volume below the critical threshold |
| P1 | Return the `mysql` pod to `k3s-worker-1` where its only replica lives, or raise the volume to two replicas, so MySQL I/O stops crossing the network | Harsh | Open | `kubectl get pod -o wide` and replica `nodeID` on the same node, or `numberOfReplicas: 2` Healthy |
| P1 | Alert on Deployments with zero available replicas for more than 15 minutes, so a crash-looping app is never invisible for a day | Harsh | Open | Firing test alert |
| P2 | Give Django a startup/readiness probe that distinguishes "app not up" from "database unreachable", so the failing layer is visible from pod status | Harsh | Open | Probe definitions in the app repo |
| P2 | File the Longhorn stale-`iscsid`-PID behaviour upstream and record the force-detach workaround in the troubleshooting docs, cross-referencing INC-2026-001 | Harsh | Open | Upstream issue link and a runbook entry |
| P2 | Review whether the remaining single-replica Longhorn volumes in personal-tier namespaces should be raised to two replicas | Harsh | Open | Replica counts per volume |

## Lessons and review questions

The reusable lesson is that **a full disk does not produce a clean error at the database layer — it
produces an infinite hang, and hangs are what exhaust connection pools.** The user-visible symptom
(a login failure) was four causal links away from the actual defect (unbounded binlog retention), and
every intermediate link presented as something else: Django looked like a connection-limit problem,
MySQL looked healthy to Kubernetes, and Celery looked fine. Reading `df` early is what collapsed the
whole chain.

The second lesson is that **remediation carries its own risk budget.** The outage was over at 20:03;
the expansion that followed caused a second, avoidable outage. Once service is restored, the correct
posture is to slow down, not to keep operating at incident tempo.

The third lesson came after the fact and is the most uncomfortable: **a monitoring stack that detects
perfectly and delivers nowhere is worse than no monitoring**, because it produces confidence without
coverage. Prometheus named the root cause of this incident at critical severity while the application
was down for a day, and that alert went to a receiver literally named `"null"`. The first version of
this report compounded the error by concluding that the alert did not exist and proposing to write
it. Always verify which layer of a pipeline is broken before scoping work against it.

Review questions:

- Why does InnoDB block indefinitely on a full filesystem instead of returning an error? What would
  `innodb_flush_method` or a reserved-space mechanism change about that behaviour?
- What is a binary log actually for, and what does it mean that this cluster was paying its full
  storage cost with no replica, no PITR procedure, and no log shipping?
- Why did MySQL's liveness probe pass while the server could not accept a single new connection? What
  would a probe that fails in that state look like, and what would it cost during normal operation?
- Longhorn's engine caches a host PID for `iscsid` at start. What other host references do storage
  components cache, and what invalidates them? How does this relate to INC-2026-001's stale mount
  namespace?
- Why did detaching the workload not restart the engine process, and what is the difference between an
  attachment ticket held by a workload and one held by the expansion controller?
- This is the fourth capacity-exhaustion incident, and every one of them fired an alert nobody
  received. What is the difference between a monitoring stack that detects and one that is
  *operationally useful*, and which of the two did this cluster have for 65 days?
- Why did the initial investigation conclude "nothing alerted" without checking the Alertmanager
  config? What is the general lesson about verifying which layer of a pipeline is actually broken
  before proposing work on it?

## Evidence

- Django: `django.db.utils.OperationalError: (1040, 'Too many connections')` raised from
  `mysql/validation.py:_check_sql_mode`; 275 restarts on `django-57c4bcd54f-dqf2n`.
- MySQL: 151 sessions in `waiting for handler commit`, oldest at 82,069s; `max_connections = 151`.
- Filesystem before: `/dev/longhorn/pvc-e5650372-... 7.8G 7.8G 0 100% /var/lib/mysql`.
- Composition: `jobhunt_db` 1.4G; `binlog.000002` through `binlog.000012` totalling ~6.1G;
  `binlog_expire_logs_seconds = 2592000`; `max_binlog_size = 1073741824`.
- Longhorn: `nsenter: cannot open /host/proc/1215/ns/mnt: No such file or directory`, against a live
  `iscsid` at PID 1985648 on `k3s-worker-1`.
- Quota rejection: `exceeded quota: job-hunt-quota, requested: requests.storage=50Gi, used: 9Gi,
  limited: 10Gi`.
- Filesystem after: `57G 2.1G 55G 4% /var/lib/mysql`; PVC `58Gi`; engine `currentSize 62277025792`.
- Recovery verification: `POST /api/auth/login/` 401, `GET /api/jobs/stats/` 200, frontend 200,
  `jobhunt_db.jobs_job` 54,086 rows.
- Alert delivery (added 2026-09-09): `max_over_time(ALERTS{namespace="job-hunt",alertstate="firing"}[36h])`
  returned `KubePersistentVolumeFillingUp` (critical, `mysql-data`), `KubePodCrashLooping`,
  `KubePodNotReady`, and `KubeDeploymentReplicasMismatch`. Alertmanager's config declares exactly one
  receiver, `"null"`, as the root route target. 22 alerts firing and undelivered at time of writing.
