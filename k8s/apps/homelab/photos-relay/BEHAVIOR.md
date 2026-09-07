# photos-relay — runtime behaviour, recovery & failure modes

The operational reference for the relay: how it behaves in steady state, how it
recovers from disruptions on its own, what it *cannot* recover from, and the
signals that tell you which is happening. For one-time **setup**, the repo layout,
and the list of tunables, see `README.md` in this directory; for the *why* behind
the design, `docs/superpowers/specs/2026-09-02-immich-google-photos-relay-design.md`.

---

## 1. What it does, in one paragraph

A dedicated Android phone (Moto G13, on a Google One 2 TB plan) is USB-cabled to
`pve-asrock` and passed through to `k3s-worker-3`. A single always-on pod there
reads the Immich library over Immich's HTTP API, `adb`-pushes each original into a
folder Google Photos backs up (`/sdcard/DCIM/Camera`, files prefixed `immich_`), waits until Google
Photos confirms the upload, then deletes the local
copy to reclaim phone storage. It repeats forever, picking up new assets as they
land in Immich. **Google Photos is treated as the primary backup** for the
iOS→Immich→relay→Google-Photos path.

**Safety invariant (never violated):** Immich is the source of truth and the phone
is disposable. A file is deleted from the phone *only after* its batch reports
"Backup complete", and the relay only ever deletes files it pushed (the `immich_`
prefix) — never the handset's own camera roll. Even a wrongly-deleted file is
re-derivable from Immich, so no original can be lost.

---

## 2. Physical + passthrough chain

```
Android phone (USB) → pve-asrock host port (bus-port 1-4)
    → QEMU USB passthrough into VM 103 (k3s-worker-3)  [terraform/proxmox usb_devices]
    → /dev/bus/usb (hostPath, Directory) in the privileged relay pod
    → Google's static platform-tools adb  → the phone's Google Photos app
```

- Passthrough is pinned by **physical bus-port** (`host=1-4`), *not* vendor:product —
  Android rewrites its USB product id on every mode change, so a vendor:product map
  would break; the bus-port is stable as long as the phone stays in the same port.
- The pod is **privileged** (not just root): Kubernetes' devices cgroup denies
  `open()` on the USB char node (major 189) even to root; there is no per-device
  allow without a device plugin, so privileged is the supported path.
- adb uses **glibc + Google's static platform-tools adb**, not Alpine's musl adb
  (which assert-crashes opening this handset over QEMU passthrough).
- adb authorization is **RSA-key-based and preserved** (the keypair is an ESO
  secret). The phone's one-time "Always allow" persists for that key across phone
  and pod reboots, so the relay reconnects with **no on-device prompt**.

---

## 3. Steady-state loop

Each pass (`run_pass`):

1. **Scan** Immich (`POST /search/metadata`, paginated, `isTrashed:false`) — list
   every asset id.
2. **Diff** against the persisted `done` set; anything not done is a to-do.
3. **Batch** the to-do (bounded by `BATCH_MAX_FILES=300` or `BATCH_MAX_BYTES=15 GiB`,
   whichever hits first).
4. For each item: **download** the original from Immich → **push** to the phone via
   adb (preserving mtime).
5. **Wait for backup** — poll Google Photos' UI until it shows "Backup complete".
6. **Mark done** (persist the ids) and **reclaim** — delete the pushed files, rescan
   MediaStore.
7. When a pass finds nothing new, **idle** for `IDLE_INTERVAL_S` (currently **300 s**)
   and scan again.

### Latency: how long a new photo takes to reach Google Photos

Two hops, only the second of which the relay controls:

| Hop | Who | Typical |
|---|---|---|
| iOS → Immich | the **Immich mobile app's** background upload | minutes, app-dependent (not the relay) |
| Immich → Google Photos | **the relay** | detection ≤ `IDLE_INTERVAL_S` (5 min) + push (seconds) + Google Photos upload confirm (~1–20 min, scales with size) |

Measured end-to-end for a real 136 MiB video: **~8.5 min** (≈5 min detect + ~20 s
push + ~2.5 min cloud confirm). Expect **~5–10 min** for photos, longer for large
videos. Worst-case detection is bounded by the poll interval (was up to 1 h before
it was lowered to 5 min).

---

## 4. The backup "oracle"

The phone is unrooted and runs a ReVanced Photos build, so there is **no database
or API to query** — backup state is read from the app UI via `uiautomator`:

- `backup_complete()` wakes the screen, foregrounds Google Photos, dumps the UI, and
  looks for **"backup complete"**.
- **Promo dialogs are dismissed first** (`_dismiss_dialog` taps known buttons — "Got
  it", "Not now", …) because they otherwise cover the status text and would stall
  detection forever.
- **Attention markers** — "backup is off", "sign in", "storage full", etc. — mean the
  pipeline is stuck until a human acts. The relay surfaces these as a red banner on
  the status page (`attention` field) rather than waiting silently. It **cannot**
  fix a logged-out app or a full account itself.

A batch that never confirms within `BACKUP_TIMEOUT_S` (6 h) is left un-marked and
retried next pass — nothing is lost, it just doesn't advance.

---

## 5. State & deduplication

- The `done` set (asset ids already in Google Photos) lives in `relay-state.json` on
  a Longhorn PVC. **It is the dedup source of truth.**
- **Durability:** the volume runs **2 Longhorn replicas on different nodes**
  (`k3s-worker-3` + `k3s-worker-1`), so a single disk/node loss doesn't lose it.
  Every save also writes a `.bak`, and startup restores from `.bak` if the primary
  is missing or corrupt.
- **Backstop:** Google Photos deduplicates byte-identical uploads by content hash,
  and the relay always pushes identical originals — so even *total* state loss means
  wasted re-push time, **never duplicate photos**.

---

## 6. Self-healing behaviours (automatic)

| Trigger | Mechanism | Effect |
|---|---|---|
| Main loop wedges (deadlock, stuck adb) | **liveness probe** on `/healthz` (503 if no heartbeat for `LIVENESS_MAX_AGE=900 s`) + startup probe | k8s restarts the pod |
| Pod termination | **preStop `adb kill-server`** + 45 s grace | releases the USB claim, exits clean — no Terminating-pileup, no blind-on-restart race |
| adb comes up "blind" (stale USB claim) | `wait_for_device` **restarts the adb server** after 2 missed polls | device reappears in ~2 min, no human nudge |
| Phone drops mid-pass | `run_pass` raises **`DeviceLost`** (checks `adb get-state`) and ends the pass | back to `wait_for_device` in seconds — a device blip is a ~2-min pause, not an hours-long churn of failed pushes |
| Immich unreachable | download errors are caught per-asset; `immich_ok` flips on the status page | pass retries; nothing marked done |
| Big download looks idle | `heartbeat()` in the download loop + chunked idle sleep | liveness never false-trips on a legitimately long transfer |

---

## 7. Recovery scenarios

### worker-3 restarts → **auto-recovers, hands-off** (proven live)
VM boots → USB re-passes (same bus-port) → node rejoins → the pod reschedules
(it's `nodeSelector`-pinned to worker-3, so it waits for the node to be Ready) →
adb reconnects (self-heals if blind) → phone re-authorizes silently via the
preserved key → state re-attaches (the worker-1 replica keeps it safe while
worker-3's rebuilds) → resumes from the persisted `done` set. Recovery time ≈
node-boot + pod-start + up to one ~2-min adb self-heal round.
**Note:** `immich-server` and `immich-machine-learning` also run on worker-3 (the
GPU node), so Immich's API is briefly down too during the restart; the relay
tolerates it and they recover together.

### pod restarts (OOM, eviction, redeploy) → **auto** (Deployment)
The Deployment recreates it (Recreate strategy). Same reconnect chain as above.

### full `pve-asrock` **host** reboot → auto, but **quorum-gated**
The worker-3 VM's `onboot` autostart waits for Proxmox quorum to return — in the
two-node stage that needs `pve-dell` up too (ADR-0049). A plain worker-3 *VM/guest*
restart has no such dependency.

### the **phone** reboots → usually auto, sometimes not
"Always allow" and Google Photos' sign-in persist across a phone reboot, so it
normally reconnects on its own. **But** if the phone drops USB debugging, or Photos
logs out / turns backup off, the relay cannot fix it — the status page's attention
banner is your signal to intervene.

---

## 8. Signals — how to tell what's happening

- **Status page:** `https://photos-relay.in.neovara.uk` (tailnet-only, via the
  Traefik `*.in` internal front door — *not* a per-service tailscale LB). Shows
  progress, current phase, rate/ETA, Immich reachability, a **heartbeat**, and a red
  **attention banner** when the phone needs a human. `/status.json` is the raw
  snapshot; `/healthz` is the liveness endpoint.
- **Heartbeat goes stale** on the page → the main loop stopped (pod down / wedged).
- **Attention banner** → phone-side problem (backup off, signed out, storage full).
- **Logs:** `kubectl logs -n photos-relay deploy/photos-relay -f` — each pass logs
  `library has N assets; M still to mirror`, pushes, and `mirrored and reclaimed`.

---

## 9. Dependencies & what is NOT automatic

The relay keeps itself alive; these are outside its control:

- **worker-3 must be up** (the phone is physically there). If it's down, the relay
  pauses until it returns, then self-heals.
- **The phone** must stay: charging, on Wi-Fi, USB-debugging enabled, and Google
  Photos **signed in with Backup ON** on the **2 TB** account. A phone-side failure
  surfaces as the attention banner but needs a manual fix.
- **Google Photos account storage** — 2 TB; ample headroom, but not infinite.

---

## 10. Operating notes

- **Editing the relay logic:** the running code is an *embedded copy* inside
  `manifests/configmap.yaml`, not read from `relay/relay.py` directly. After editing
  `relay/relay.py`, **regenerate the ConfigMap** (procedure in `README.md`) and commit
  both — otherwise the pod keeps running the old code.
- **Tunables** (idle interval, batch caps, timeouts) are Deployment env vars — see the
  table in `README.md`.
- **Known follow-ups:** bake the relay image to drop the start-time adb download; move
  the SSM secrets into `terraform/aws` for full IaC coverage.
