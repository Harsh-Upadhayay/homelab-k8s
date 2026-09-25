# INC-2026-011: An unattended NVIDIA driver upgrade left Immich's GPU pods unable to start for eleven days

## Incident metadata

| Field | Value |
| --- | --- |
| Date | 2026-09-25 (detected); fault introduced 2026-09-12 |
| Severity | SEV-2 |
| Status | Resolved; preventive actions open |
| Systems | `k3s-worker-3`, NVIDIA driver 580-server / `nvidia-persistenced`, `unattended-upgrades`, Immich (`immich-server`, `immich-machine-learning`), kubelet eviction, Alertmanager |
| Start | 2026-09-14 ~12:00 UTC (17:30 IST) — last `immich-server` pod stopped being Ready (reconstructed from Prometheus `kube_pod_status_ready`). Latent fault since 2026-09-12 06:42 UTC; `immich-machine-learning` unavailable since 2026-09-12 ~12:27 UTC |
| End | 2026-09-25 14:41:12 UTC (20:11 IST) — replacement `immich-server` pod Ready; `https://immich.in.neovara.uk` returned 200 |
| Duration | ~11 days 2.7 hours with no Ready `immich-server` pod; ~13 days 2.2 hours without machine learning. The user-visible 503 across the whole window is inferred from readiness, not from HTTP-level evidence (no Traefik request metrics exist) |
| Detection | User asked "is immich down?" at ~19:50 IST on 2026-09-25. `KubePodCrashLooping` for `immich` had been firing since 2026-09-14 12:24 UTC and was discarded by Alertmanager's `"null"` route (#92) |
| Data impact | No server-side loss indicated. `immich-postgres` (on `k3s-worker-1`) and `immich-valkey` stayed Running and were never touched by this fault; the `immich-library` volume remounted normally on recovery. Not verified: whether mobile clients held an upload backlog during the outage and whether it has since drained |

All times below are UTC unless marked IST (UTC+5:30). `k3s-worker-3`'s journal and apt logs are
UTC; the operator is in IST.

## Executive summary

On 2026-09-12 at 06:41 UTC, Ubuntu's `unattended-upgrades` upgraded the NVIDIA 580-server driver
packages on `k3s-worker-3` from `580.173.02` to `580.178.04`. The upgrade stopped
`nvidia-persistenced`, which never came back, and replaced the user-space driver libraries while the
old `580.173.02` kernel module stayed loaded — a state that only a reboot resolves. Containers that
were already running kept working, but every *new* GPU container on the node failed at creation with
`open /run/nvidia-persistenced/socket: no such file or directory`. That latent fault became an
outage through `k3s-worker-3`'s chronically full 40 GiB OS disk: repeated kubelet `DiskPressure`
evictions removed the ML pod on 2026-09-12 and the last serving `immich-server` pod on 2026-09-14,
and every replacement crash-looped. Immich returned 503 from then until the operator noticed on
2026-09-25, eleven days later. Prometheus had fired `KubePodCrashLooping` the whole time; Alertmanager
discarded it. Rebooting the worker loaded the matching kernel module and restarted
`nvidia-persistenced`, and all four Immich pods came back Running.

## Impact

- **Immich web and mobile access was unavailable for approximately eleven days** (2026-09-14 ~12:00
  to 2026-09-25 14:41 UTC). No `immich-server` pod was Ready at any point in that window, so the
  Service had no endpoints and Traefik returned 503. This is a readiness-based reconstruction; the
  503 was directly observed only on 2026-09-25.
- **Immich machine learning (smart search, face recognition) was unavailable for approximately 13
  days**, from the 2026-09-12 eviction of `immich-machine-learning-554d8c85b6-gdbcd` onward.
- For two days of that window (2026-09-12 12:32 to 2026-09-14 11:17 UTC) `immich-postgres` was also
  down, but that was INC-2026-010's `pve-dell` outage, not this fault.
- A final eviction burst on 2026-09-25 at 06:17 UTC created and evicted 37 `immich-server` pods in
  about eight seconds; these were tombstones, not lost service — the Deployment had already had no
  Ready pod for eleven days.
- Recovery required rebooting `k3s-worker-3`. Because six Longhorn volumes had their only replica on
  that node (`immich-library`, `nextcloud-data`, Prometheus, Loki, Alertmanager,
  `audiobookshelf-audiobooks-single`), those workloads were briefly unavailable during the reboot.
- Not affected: the control plane, `k3s-worker-1`'s workloads, `immich-postgres`'s data, and any
  non-GPU pod on `k3s-worker-3` that was not evicted.

Classified SEV-2 rather than SEV-3: Immich is the photo library, a critical service under this
platform's own definitions, and the outage was extended (eleven days) — the definition of SEV-2 —
with no confirmed irreversible loss.

## Detection

The first human signal was the operator trying to use Immich on 2026-09-25 and finding it down. That
is a notification failure, not a detection failure: the platform detected the condition within
minutes and kept detecting it for eleven days.

Querying Prometheus after recovery (first `firing` sample per alert, read-only):

| Alert | First firing (UTC) | Object |
| --- | --- | --- |
| `KubeNodeEviction` | 2026-09-12 12:29 | `k3s-worker-3` |
| `KubePodNotReady` | 2026-09-12 12:39 | `immich` |
| `KubeDeploymentReplicasMismatch` | 2026-09-12 12:39 | `immich` |
| `KubeContainerWaiting` | 2026-09-12 13:34 | `immich` |
| `KubePodCrashLooping` | 2026-09-14 12:24 | `immich-server` and `immich-machine-learning` pods |

`KubePodCrashLooping` exists (`PrometheusRule/kube-prometheus-stack-kubernetes-apps`,
`for: 15m`, `severity: warning`) and fired correctly. None of these alerts was delivered: the
Alertmanager configuration in `secret/alertmanager-kube-prometheus-stack-alertmanager` still has a
single receiver, `"null"`, as the default route, and no `AlertmanagerConfig` objects exist. This is
issue #92, still open — the third consecutive incident (after INC-2026-009 and INC-2026-010) whose
duration was set by undelivered alerts.

Two further signals existed and were not actionable:

- `FreeDiskSpaceFailed` on `k3s-worker-3` has recurred 896 times since 2026-09-06 08:50 UTC (the day
  of INC-2026-008) — the kubelet has been unable to reclaim image space for nineteen days. It is a
  Kubernetes Event only; nothing alerts on it.
- GPU telemetry did **not** reveal the fault. `DCGM_FI_DEV_GPU_TEMP` kept reporting throughout and
  `nvidia.com/gpu` allocatable stayed at 4, because the DCGM exporter and device plugin were started
  before the upgrade and kept their existing handles. A healthy-looking GPU dashboard coexisted with
  a node that could not start a single new GPU container.

The unknown is how many of the kubelet eviction events would have been visible to the operator had
alerts been routed; there is no evidence the operator was looking at the cluster during the window.

## Timeline

All times UTC. IST = UTC+5:30. Times marked ~ are reconstructed from Prometheus samples (5-minute
resolution) or object creation times.

| Time | Event |
| --- | --- |
| 2026-09-06 08:50 | First `FreeDiskSpaceFailed` on `k3s-worker-3` (INC-2026-008). The event recurs 896 times up to this incident |
| 2026-09-06 11:46 | `immich-server-d7764bb8d-nlmp9` and `immich-machine-learning-554d8c85b6-gdbcd` created (after the GPU rollout); both run normally |
| 2026-09-12 06:41:32 | `unattended-upgrade` begins upgrading `nvidia-driver-580-server` and ~15 related packages `580.173.02-0ubuntu0.26.04.1` → `580.178.04-0ubuntu0.26.04.1` |
| 2026-09-12 06:41:45 | `nvidia-cdi-refresh.service` skipped by its `ExecCondition` |
| 2026-09-12 06:42:04 | `nvidia-persistenced` receives signal 15 and stops ("The daemon no longer has permission to remove its runtime data directory /var/run/nvidia-persistenced"); systemd reports "Deactivated successfully". It never restarts |
| 2026-09-12 06:45:17 | Package upgrades complete; `nvidia-firmware-580-server-580.178.04` installed |
| 2026-09-12 06:45:24 | `nvidia-firmware-580-server-580.173.02` removed. Node now runs kernel module 580.173.02 against 580.178 user-space libraries. Running GPU containers are unaffected |
| 2026-09-12 12:18:58 | INC-2026-010: `pve-dell` goes down; pods from `k3s-worker-1` begin failing over to `k3s-worker-3` |
| ~2026-09-12 12:27 | `k3s-worker-3` `DiskPressure=True`. `immich-machine-learning-...-gdbcd` evicted; replacement `...-v6wz6` created 12:26:38 and cannot start. ML Deployment reaches 0 available |
| 2026-09-12 12:29 | `KubeNodeEviction` fires for `k3s-worker-3` — discarded |
| 2026-09-14 ~11:06 | INC-2026-010: `pve-dell` restarted |
| 2026-09-14 11:19–11:59 | `DiskPressure` flaps on `k3s-worker-3` (samples 11:22, 11:47, 12:02). Six more ML replacement pods created and lost in turn |
| ~2026-09-14 12:00 | `immich-server-...-nlmp9` evicted (`Evicted` / `TerminationByKubelet`); replacement `...-gq8fv` created 12:00:26 and enters CrashLoopBackOff. **No Ready `immich-server` pod from here on — Immich outage begins (~17:30 IST)** |
| 2026-09-14 12:24 | `KubePodCrashLooping` fires for `immich` — discarded, and keeps firing for eleven days |
| ~2026-09-15 10:52–11:07 | Another `DiskPressure` episode; `immich-server-...-nlqnj` and two ML pods created, all crash-looping (these reach ~2,500 restarts) |
| ~2026-09-25 06:17 | Another `DiskPressure` episode. 37 `immich-server` pods created and evicted within ~8 s; `...-8mqkz` and ML `...-8275g` survive admission and crash-loop |
| 2026-09-25 06:22:28 | `DiskPressure` returns to False (11:52 IST) |
| ~2026-09-25 14:20 | Operator asks "is immich down?" (~19:50 IST); `https://immich.in.neovara.uk` returns 503. Incident detected |
| 2026-09-25 ~14:20–14:35 | Diagnosis via `kubectl debug node/k3s-worker-3 --profile=sysadmin` (SSH unavailable): `nvidia-smi` reports "Driver/library version mismatch"; `/run/nvidia-persistenced` absent; apt history identifies the 2026-09-12 upgrade |
| 2026-09-25 ~14:35 | Operator cordons and drains `k3s-worker-3`; drain times out on Longhorn's `block-if-contains-last-replica` policy |
| 2026-09-25 14:38:10 | `NodeNotReady` — operator reboots VM `k3s-worker-3` from Proxmox on `pve-asrock` |
| 2026-09-25 14:39:27 | Node `Rebooted` and `NodeReady`. Old GPU pods fail admission with `UnexpectedAdmissionError` (device plugin not yet re-registered); replacements created 14:39:30 and 14:40:21 |
| 2026-09-25 14:40:19 | `immich-machine-learning-554d8c85b6-sk9rq` Ready |
| 2026-09-25 14:41:12 | `immich-server-d7764bb8d-5b9mv` Ready (20:11 IST). URL returns 200. Outage ends |

## Technical root cause

**1. An unattended upgrade replaced the driver underneath a loaded kernel module (the trigger).**
The `nvidia_gpu_node` role installs `nvidia-driver-580-server` by package *name*, and the group_vars
comment calls it "Pinned deliberately, like `k3s_version`". It was not pinned in any sense apt
honours: no `apt-mark hold`, no `Pin-Priority` preference, and no entry in
`Unattended-Upgrade::Package-Blacklist`. Ubuntu published `580.178.04` from an archive origin the
node's `unattended-upgrades` configuration allows (which pocket was not recorded), and
`unattended-upgrades` installed it on its normal daily run.

A driver upgrade has two halves. The user-space half (`libnvidia-*`, `nvidia-smi`, the persistence
daemon) is just files, and apt replaced them immediately. The kernel half (`nvidia.ko`, rebuilt by
DKMS) cannot be swapped while the GPU is in use, so the running kernel kept `580.173.02` until the
next boot. NVIDIA refuses to run mismatched halves, which is what `nvidia-smi` reported on
2026-09-25: `Failed to initialize NVML: Driver/library version mismatch`.

**2. `nvidia-persistenced` stopped and nothing restarted it (the proximate failure).** The package
upgrade stopped the daemon at 06:42:04. Its unit is `static` — not enabled, started only as a
dependency or by a package script — and after the upgrade nothing started it again, so
`/run/nvidia-persistenced/` (and its `socket`) ceased to exist. Why the post-install step did not
restart it is **not established**; the kernel/user-space mismatch would plausibly have made it fail
even if it had been started.

**3. Every new GPU container asks for that socket at creation time (why only new pods failed).**
Both Immich pods use `runtimeClassName: nvidia` and request `nvidia.com/gpu: 1`. The NVIDIA container
runtime injects the driver into the container as a set of bind mounts — libraries, device nodes, and
`/run/nvidia-persistenced/socket` — while runc is building the container. If any mount source is
missing, runc aborts before the application starts:

```
failed to create containerd task: ... runc create failed: unable to start container process:
error during container init: failed to fulfil mount request:
open /run/nvidia-persistenced/socket: no such file or directory
```

That is why the fault was latent: a container created before 06:42 on 2026-09-12 had its mounts
already in place and kept running; only container *creation* failed. The exact source of the mount
list — a CDI spec generated while the daemon was running, left stale because
`nvidia-cdi-refresh.service` was skipped at 06:41:45, versus the runtime's legacy discovery — is a
**hypothesis** and was not inspected. Either way, supplying the socket alone would not have been a
fix: the version mismatch in (1) would still have broken CUDA inside the container. Only a reboot
reconciles both.

**4. Disk-pressure evictions turned the latent fault into an outage (the amplifier).**
`k3s-worker-3`'s 40 GiB OS disk has been above the kubelet's image-GC threshold since INC-2026-008:
on 2026-09-25 `/dev/sda1` was 35 GiB used of 38, with 28 GiB in
`/var/lib/rancher/k3s/agent/containerd`, and image GC reported `freed 0 bytes` because every image
on the node is in use. With so little headroom, every burst of scheduling onto the node crossed the
eviction threshold. Prometheus shows `DiskPressure=True` episodes on 2026-09-12 12:27,
2026-09-14 11:22–12:07, 2026-09-15 10:52–11:07 and 2026-09-25 06:17. The first three coincide with
INC-2026-010's failover to and from `pve-dell`; that the image pulls from those reschedules caused
the pressure is a **hypothesis** consistent with the timing. Each eviction removed a GPU pod that was
running only because it predated the upgrade, and each replacement hit (3). The second eviction took
the last serving `immich-server`.

In a node without the driver fault, those evictions would have caused a restart of seconds. Without
the evictions, the driver fault would have stayed latent until the next unrelated restart. Neither
alone produced an eleven-day outage; undelivered alerts (#92) supplied the duration.

## Contributing factors

- NVIDIA packages were eligible for the node's `unattended-upgrades` run, contradicting the
  "Pinned deliberately" intent recorded next to `nvidia_driver_package` and ADR-0067's version-pinning
  principle. The pin existed in a comment, not in apt.
- A driver upgrade needs a reboot to take effect, and nothing on the node schedules, requests, or
  reports one, so the node sat in a half-upgraded state for thirteen days. Whether
  `unattended-upgrades` automatic reboot is configured was not checked; it evidently did not fire.
- `k3s-worker-3`'s OS disk has had no image-store headroom since INC-2026-008, whose P1 action to grow
  it or relocate containerd's root is still open. Kubelet evictions on this node are routine rather
  than exceptional.
- Alertmanager routes every alert to `"null"` (#92). `KubePodCrashLooping`, `KubePodNotReady`,
  `KubeDeploymentReplicasMismatch` and `KubeNodeEviction` all fired and none was delivered.
- GPU health signals (DCGM metrics, `nvidia.com/gpu` allocatable) stayed green because the long-lived
  GPU components were started before the upgrade. There is no probe that tries to *start* a GPU
  container.
- Immich's `immich-server` depends on the GPU. Losing the GPU therefore takes the whole web/API tier
  down, not just ML acceleration.
- SSH to `k3s-worker-3` looked unavailable at diagnosis: `192.168.1.24` timed out and the short
  MagicDNS name `k3s-worker-3` failed on `REMOTE HOST IDENTIFICATION HAS CHANGED`
  (`~/.ssh/known_hosts` line 121). Diagnosis went through `kubectl debug node/...`, which depends on
  the node being healthy enough to run a pod. Later the same day the inventory's FQDN
  (`k3s-worker-3.egret-pence.ts.net`) worked with its verified key, and that key's fingerprint
  (`SHA256:I2Ep…`) is exactly the one the short name presented — so line 121 is a stale entry for
  the short name, not a changed host.
- Six Longhorn volumes held their only replica on `k3s-worker-3`, so the drain could not complete
  under `block-if-contains-last-replica` and the reboot briefly took those workloads down.

## Resolution and recovery

1. Confirmed the mechanism before acting, from a privileged debug pod (`kubectl debug
   node/k3s-worker-3 --profile=sysadmin`, then `chroot /host`): `/proc/driver/nvidia/version`
   reported kernel module `580.173.02`; `nvidia-smi` reported the library mismatch;
   `/run/nvidia-persistenced` did not exist; `/var/log/apt/history.log` and the journal placed the
   upgrade and the daemon stop at 2026-09-12 06:41–06:45.
2. Cordoned and drained `k3s-worker-3`. The drain timed out on the six last-replica Longhorn volumes;
   that was accepted as a brief planned outage for those workloads rather than forcing it.
3. Rebooted VM `k3s-worker-3` gracefully from Proxmox on `pve-asrock` (14:38 UTC). The new boot loaded
   the kernel module rebuilt by DKMS during the upgrade, and `nvidia-persistenced` came back.
4. Verified `nvidia-smi` and the persistence daemon, uncordoned the node, and deleted the Failed and
   Evicted `immich` pod tombstones.

Recovery was verified by all four Immich pods reporting Running with `immich-server` passing its
readiness probe at 14:41:12 UTC, `immich-machine-learning` Ready at 14:40:19, and
`https://immich.in.neovara.uk` returning 200 — not by inference from the node condition.

Recovery is not the same as prevention: at the time of writing nothing yet stops the next driver
upgrade from repeating this, and `k3s-worker-3` was still reporting `ImageGCFailed` at 92% of its
image filesystem after the reboot.

## What went well

- The error message was precise. `open /run/nvidia-persistenced/socket` pointed straight at the
  host-side driver stack rather than at Immich.
- `kubectl debug node/... --profile=sysadmin` gave full host access when SSH did not, and the host's
  apt history and journal were intact enough to date the cause to the second.
- The database, Valkey and the photo library volume were never involved. The fault was confined to
  container creation on one node.
- The reboot was done gracefully from Proxmox after an attempted drain, not as a hard reset, and the
  Longhorn last-replica block was understood rather than overridden.
- Prometheus kept a complete readiness and alert history, which is what established that the outage
  began on 2026-09-14 and not on the day it was noticed.

## What did not go well

- An eleven-day outage of the photo library was found by the operator trying to use it.
- The "pinned" driver was not pinned. The config claimed an invariant that nothing enforced, which is
  worse than not claiming it, because it stopped anyone from checking.
- The initial framing on 2026-09-25 attributed the outage to that morning's eviction burst. Prometheus
  showed that burst only evicted pods that were already failing; service had been down since
  2026-09-14.
- The node's full OS disk, flagged in INC-2026-006 and INC-2026-008, converted a latent fault into
  an outage again.
- SSH to the worker was tried by the wrong names first (a stale short-name `known_hosts` entry and a
  LAN address that times out) rather than the inventory's FQDN, so the most basic recovery tool
  looked unavailable when it was not.
- A cleanup step during diagnosis also deleted a stale Completed pod,
  `node-debugger-k3s-worker-1-8xgpb` in `default`, that this session had not created. It was
  harmless, but it was an unintended change made during an incident.

## Where we got lucky

- The running `immich-server` container survived the upgrade for two days. Had the upgrade coincided
  with any restart, the outage would have started on 2026-09-12.
- The fault affected only new GPU containers. The GPU Operator's own device plugin and DCGM exporter
  were already running; had they restarted, `nvidia.com/gpu` would likely have gone to zero and
  broken scheduling as well.
- A reboot was sufficient. The upgraded DKMS module built and loaded against the running kernel; had
  the build failed, recovery would have needed a driver rollback on a node reachable only through a
  debug pod.
- `immich-postgres` lives on `k3s-worker-1` and was untouched. Had it been colocated on the GPU node,
  the reboot would have been a database restart too.

## Corrective and preventive actions

| Priority | Action | Owner | Status | Completion evidence |
| --- | --- | --- | --- | --- |
| P0 | Hold every installed `*nvidia*` package (`apt-mark hold`) and add an `unattended-upgrades` `Package-Blacklist` drop-in for them in `ansible/roles/nvidia_gpu_node`, so a driver change only ever happens as a deliberate, attended role run with a planned reboot. Applied 2026-09-25 via `ansible-playbook site.yml --limit k3s-worker-3 --tags nvidia_gpu` (re-run: `changed=0`) | Harsh | Done | Verified 2026-09-25: `apt-mark showhold` lists 21 NVIDIA packages; `apt-get -s install --only-upgrade nvidia-driver-580-server` upgrades 0; `unattended-upgrade --dry-run --debug` applies `PkgPin('/^nvidia-/', -32768)` (and `libnvidia-`, `xserver-xorg-video-nvidia-`); `nvidia-smi` reports 580.178.04 |
| P0 | Close #92: route Alertmanager's default receiver somewhere a human reads. Third consecutive incident whose duration was set by discarded alerts | Harsh | Open | A test alert, and a real `KubePodCrashLooping`, observed arriving at the destination |
| P1 | Grow `k3s-worker-3`'s OS disk (`os_disk_size` for `k3s-worker-3` in `terraform/proxmox/terraform.tfvars`) or relocate containerd's root, closing INC-2026-008's still-open action. Eviction pressure is what turned this latent fault into an outage | Harsh | Open | `df` on the node showing sustained headroom; `FreeDiskSpaceFailed`/`ImageGCFailed` events stop recurring |
| P1 | Remove the stale short-name `k3s-worker-3` entry (`known_hosts` line 121; the host key did not change — it matches the FQDN's verified key) and resolve or document the LAN SSH timeout to `192.168.1.24` | Harsh | Open | `ssh harsh@k3s-worker-3` and `ssh harsh@192.168.1.24` both succeed against the same verified key, or the LAN path is documented as intentionally closed |
| P2 | Detect a pending driver reboot: alert when `/var/run/reboot-required` exists on a GPU node, or when the loaded NVIDIA kernel module version differs from the installed package (for example via a node-exporter textfile metric) | Harsh | Open | Alert rule committed and shown firing after a test package change on the node |

## Lessons and review questions

The first lesson is that **a version pin is only real where the package manager can see it.** A
package name in group_vars says which driver to install; it says nothing about whether apt may
replace it tomorrow. `unattended-upgrades` treated the driver exactly as it
treats `openssl`. Anything the platform treats as "pinned deliberately" should be checked by
asking apt, not by reading the comment.

The second is that **kernel-module drivers upgrade in two halves, and the node is broken between
them.** The files change at install time; the kernel changes at the next boot. A GPU node that has
had its driver packages upgraded and not been rebooted is a node that cannot start new GPU
containers, however healthy its existing ones look.

The third is that latent faults need a second event to become outages, and on this node the second
event is always available: a disk with no headroom evicts pods whenever anything is scheduled onto it.
Recurring disk pressure is not background noise; it is what repeatedly converts other problems into
outages.

The fourth, again, is that detection without delivery is not detection. Every alert this incident
needed already existed and fired within minutes.

Review questions:

- What does `nvidia-persistenced` actually do, why does the container runtime mount its socket into
  every GPU container, and what happens to a container if the socket is missing but the rest of the
  driver is intact?
- How does the NVIDIA container runtime decide what to inject — CDI spec versus legacy hook — and
  which one is this node using? What does `nvidia-cdi-refresh.service` regenerate, and why was it
  skipped during the upgrade?
- Why can user-space driver files be replaced while the GPU is in use, but not the kernel module? What
  exactly is NVML comparing when it reports "Driver/library version mismatch"?
- What is the difference between `apt-mark hold`, an APT `Pin-Priority` preference, and
  `Unattended-Upgrade::Package-Blacklist`? Which of them stops a manual `apt upgrade`, which stops
  only the unattended run, and which does the role need?
- Why did image GC report `freed 0 bytes` while the disk sat above its high threshold, and how does
  the kubelet choose between image GC and pod eviction when `nodefs` runs low?
- Why did one eviction produce 37 replacement pods in eight seconds on 2026-09-25, and why is that
  harmless to the ReplicaSet but noisy for everything that lists pods?
- Why did Longhorn's `block-if-contains-last-replica` drain policy stop the drain, and what would a
  safe drain of `k3s-worker-3` look like for the six single-replica volumes?

## Evidence

- `immich` pod events, 2026-09-25: `Failed ... failed to fulfil mount request: open
  /run/nvidia-persistenced/socket: no such file or directory` on `immich-server-d7764bb8d-8mqkz`
  (first 06:25:28) and `immich-machine-learning-554d8c85b6-8275g` (first 06:25:33); exit code 128.
- `k3s-worker-3` `/var/log/apt/history.log`: `unattended-upgrade` run 2026-09-12 06:41:32–06:45:17
  upgrading `nvidia-driver-580-server`, `nvidia-dkms-580-server`, `libnvidia-compute-580-server`,
  `nvidia-utils-580-server`, `libnvidia-{common,cfg1,decode,fbc1,extra,encode,gl}-580-server`,
  `nvidia-kernel-{source,common}-580-server`, `nvidia-compute-utils-580-server`,
  `xserver-xorg-video-nvidia-580-server` from `580.173.02-0ubuntu0.26.04.1` to
  `580.178.04-0ubuntu0.26.04.1`; `nvidia-firmware-580-server-580.173.02` removed 06:45:24.
- `k3s-worker-3` journal: `nvidia-persistenced` signal 15 at 2026-09-12 06:42:04, "Deactivated
  successfully", no later start; `nvidia-cdi-refresh.service` skipped by `ExecCondition` at 06:41:45;
  `nvidia-persistenced.service` is `static`.
- On 2026-09-25 before reboot: `/proc/driver/nvidia/version` = `580.173.02`; `nvidia-smi` =
  `Failed to initialize NVML: Driver/library version mismatch` / `NVML library version: 580.178`;
  `/run/nvidia-persistenced` absent; node uptime 19 days.
- Prometheus `kube_pod_status_ready{namespace="immich",pod=~"immich-server.*",condition="true"}`:
  sum 1 until 2026-09-14 12:02, 0 until 2026-09-25 14:42 (5-minute samples).
  `kube_deployment_status_replicas_available` agrees for both `immich-server` and
  `immich-machine-learning`.
- Prometheus `kube_pod_status_reason`: `immich-machine-learning-...-gdbcd` `Evicted` from 2026-09-12
  12:27; `immich-server-...-nlmp9` `Evicted` from 2026-09-14 12:02.
- Prometheus `kube_node_status_condition{node="k3s-worker-3",condition="DiskPressure",status="true"}`
  episodes: 2026-09-12 12:27; 2026-09-14 11:22–12:07 (flapping); 2026-09-15 10:52–11:07;
  2026-09-25 06:17–06:22. Retention floor 2026-09-08 01:47 UTC, so earlier episodes are not visible.
- `kube_pod_created`: 37 `immich-server` pods created 2026-09-25 06:17:15–06:17:22.
- Prometheus `ALERTS` first-firing times as tabulated under Detection.
- `PrometheusRule/kube-prometheus-stack-kubernetes-apps` — `KubePodCrashLooping`, `for: 15m`.
- `secret/alertmanager-kube-prometheus-stack-alertmanager` — only receiver `"null"`, default route
  `"null"`; no `AlertmanagerConfig` objects (issue #92, open).
- Node events on `k3s-worker-3`: `FreeDiskSpaceFailed` count 896 from 2026-09-06 08:50:30 to
  2026-09-25 14:29:28 ("93% of 37.6 GiB used ... freed 0 bytes"); `ImageGCFailed` at 92% after the
  reboot; `NodeNotReady` 14:38:10, `Rebooted` / `NodeReady` 14:39:27.
- Host disk on 2026-09-25: `/dev/sda1` 38G, 35G used, 2.8G free;
  `/var/lib/rancher/k3s/agent/containerd` 28G; `/var/log` 1.6G.
- Recovery: `immich-machine-learning-554d8c85b6-sk9rq` Ready 14:40:19; `immich-server-d7764bb8d-5b9mv`
  Ready 14:41:12; transient `UnexpectedAdmissionError ... no healthy devices present` on the old pods
  at 14:39:28 while the device plugin re-registered.
- `DCGM_FI_DEV_GPU_TEMP` continuously present and `nvidia.com/gpu` allocatable constant at 4
  throughout 2026-09-08 → 2026-09-25.
- `ansible/group_vars/k3s_agent.yml` — `nvidia_driver_package: "nvidia-driver-580-server"` under a
  "Pinned deliberately" comment, with no hold or blacklist anywhere in
  `ansible/roles/nvidia_gpu_node/` at the time of the incident.
- `terraform/proxmox/terraform.tfvars` — `k3s-worker-3` `os_disk_size = 40`.
- Related: INC-2026-008 (same node, same full OS disk, open action to grow it), INC-2026-006 (same node,
  ephemeral-storage eviction), INC-2026-009 and INC-2026-010 (issue #92), ADR-0067 (GPU worker and the
  driver pin).
