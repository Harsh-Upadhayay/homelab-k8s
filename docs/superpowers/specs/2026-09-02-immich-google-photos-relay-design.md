# Immich → Google Photos relay — design

**Status:** Implemented on branch `feat/immich-photos-relay`, pending review/merge.
**Date:** 2026-09-02

## Goal

Use a dedicated Android handset (Moto G13, serial `ZD222B3GL8`) with a Google One
2 TB plan as an upload relay: pull every original from the cluster's Immich
library, feed it to the phone's Google Photos backup, confirm the batch reached
the cloud, delete the local copies to reclaim phone storage, and repeat until the
whole library is mirrored — then keep mirroring new assets forever.

This is, in effect, the off-box backup Immich currently lacks: the Longhorn
backup target is a stale migration leftover (`AVAILABLE: false`, zero backups),
and every Immich volume is single-replica. Google Photos becomes a real second
copy of the photo library.

## Why the phone reaches the cluster over USB, not the network

The handset is cabled to `pve-asrock` and mapped into `k3s-worker-3` as a USB
device (Terraform `usb_devices`, see `terraform/proxmox`). USB was chosen over
ADB-over-TCP deliberately:

- USB keeps the phone **charging**, which permanently satisfies Google Photos'
  "only back up while charging" condition.
- Wired ADB survives Android **doze**; `adb tcpip` dies to Wi-Fi power-save.

The device is bound by **physical bus-port (`1-4`), never vendor:product**.
Android rewrites its USB product ID on every mode change — observed live on this
handset, `0x2e82 → 0x2e76 → 0x2e81` across a debugging toggle and two
re-enumerations — so an ID binding would break constantly. The port number is
stable while the cable stays in one socket.

## The read path: Immich HTTP API, not the filesystem

The relay needs 58k+ originals out of Immich. Three options were weighed:

| Option | Credentials | Autonomy | Cost | Verdict |
|---|---|---|---|---|
| **Immich HTTP API** over ClusterIP | one API key (user-provided secret) | full once key exists | none | **chosen** |
| Filesystem via Longhorn clone | none | full | ~290 GB copy per refresh cycle | rejected: heavy, no incrementality |
| Filesystem via co-mounted PVC | none | full | must relocate production Immich to worker-3 | rejected: disturbs a running app |

Co-locating file access with the phone is physically impossible without one of
the filesystem compromises: the phone forces the relay onto `k3s-worker-3`, while
`immich-library` is `ReadWriteOnce` and attaches to whichever node runs
`immich-server` (currently `k3s-worker-1`). The API reads over the network from
any node and touches nothing in Immich except requiring a key — so it decouples
cleanly from Immich's storage placement.

The API key is a **user-provided runtime secret**, consistent with this repo's
established philosophy ("runtime Secrets remain imperative"). It flows through the
same ESO + SSM path as every other app secret:
`/neovara/homeinfra/photos-relay/immich-api-key` → `ClusterSecretStore` →
`ExternalSecret` → the `photos-relay-immich` Secret. Create it in Immich with
**read + download** permissions (Account → API Keys).

## Safety model: Immich is the source of truth, the phone is disposable

The single most important property: **deleting a file from the phone can never
lose data**, because every file is re-derivable from Immich. The phone is a
relay, not a store. This is what makes the aggressive delete-to-reclaim-space
loop safe.

- The relay tracks per-asset progress in a small Longhorn PVC
  (`photos-relay-state`, 1 Gi). An asset is marked **done** only after the phone
  reports a completed backup for the batch containing it.
- A file is only deleted from the phone after "Backup complete" is observed for
  its batch. Worst case (Photos silently rejected a file we marked done): the
  file is missing from Google Photos but still safe in Immich, and a periodic
  full reconciliation pass re-queues anything not confirmed. No original is ever
  at risk.
- The relay only ever deletes files **it pushed** (a dedicated name prefix /
  MediaStore query), never the handset's own camera roll.

## The loop

```
loop forever:
  ensure adb device is authorized (reuse the preserved adb key)
  assets = Immich API: list all asset IDs (paginated), minus trashed
  todo   = assets - state.done
  batch  = take from todo until BATCH_MAX_BYTES
  for asset in batch:
      stream GET /assets/{id}/original  (x-api-key)  ->  adb push to backup folder
  media-scan the pushed files
  wait until Google Photos reports "Backup complete" (bounded timeout)
  mark batch assets done in state
  delete the pushed local files (reclaim phone storage)
  if todo was empty: sleep IDLE_INTERVAL before the next reconciliation pass
```

The Google Photos backup state is read from the app's UI/notification surface via
`uiautomator` — the phone is not rooted and Photos here is the ReVanced build, so
there is no database oracle; the UI is the only control surface.

## Where it runs

A single-replica Deployment pinned to `k3s-worker-3` (`nodeSelector`), running as
root with a `hostPath` mount of the `/dev/bus/usb` **directory** (so device
re-enumeration doesn't orphan the mount). The container is a version-pinned
Alpine that installs `android-tools` (adb) + Python at start; the relay logic
lives in a ConfigMap-mounted script for full inspectability. A follow-up can bake
a proper image into the cluster registry once the logic settles.

## One-time human actions

1. Physically tap **Always allow** on the ADB authorization prompt (done).
2. Create the Immich API key and store it in SSM (the only morning action).
3. Confirm the phone's Google Photos backup folder scope covers the relay's
   push folder (the relay pushes into a folder Photos already backs up).

## Deliberately out of scope

Driving Google Photos' "Free up space" menu (the relay deletes its own pushed
files directly, which is simpler and never touches real photos); any Google
Photos API integration (would need OAuth and does not grant the unlimited/2 TB
semantics the on-device app does); fixing Immich's own Longhorn backup target
(separate, tracked work).
