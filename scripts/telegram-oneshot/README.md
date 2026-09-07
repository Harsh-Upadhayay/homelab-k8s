# Telegram → Google Photos one-shot backfill

A throwaway tool to move media out of a set of Telegram chats into Google Photos,
by piggy-backing on the **photos-relay** handset. It reuses the relay's proven path
— push to the phone's Google Photos folder, wait for the cloud to confirm, delete to
reclaim space — but sources from Telegram instead of Immich, and stops when the
backlog is done (no continuous watch).

It is deliberately **not a GitOps workload**. It runs as a detached process *inside*
the existing relay pod and leaves no permanent trace: a plain Secret, some files
under `/state/tg-oneshot/`, an optional temporary Tailscale Service, and a pod-local
`pip install telethon` — all removed at the end. This directory is the record of how
to set it up again; nothing here is applied automatically.

## Why it lives in the relay pod

Only one process can hold the handset's USB/adb claim at a time, so a second pod
would contend with the relay. The idle relay never touches the phone unless a new
Immich asset arrives, so the backfill coexists with it (it uses a separate
`uiautomator` dump file, `/sdcard/ui_tg.xml`, to avoid racing the relay's oracle).

## Files

| File | Purpose |
|---|---|
| `tg_oneshot.py` | the backfill — walks each chat, downloads media, pushes, confirms, reclaims; serves a live status page on `:8090`; resumable via a per-chat high-water mark |
| `tg_login.py` | interactive one-time login → prints a `StringSession` (full-account credential) |
| `tg_list_chats.py` | lists your groups/channels + numeric IDs + photo/video counts, to build the config |
| `config.example.json` | config template (copy to `config.json`, fill in, **never commit**) |
| `status-service.yaml` | temporary tailnet Service for the status page — `kubectl apply`/`delete` by hand |

## Prerequisites

- The **photos-relay** pod is running with the phone attached and authorized
  (`kubectl -n photos-relay get pod`), Google Photos signed in with **Backup ON**,
  the phone charging and on Wi-Fi.
- Telegram API credentials: https://my.telegram.org → *API development tools* →
  `api_id` + `api_hash`.
- A trusted machine with `python3` + `pip install telethon` to generate the session.

## Runbook

### 1. Generate a session (on your laptop)

```sh
pip install telethon
python3 tg_login.py      # prompts for api_id, api_hash, phone, login code, 2FA
```

Copy the printed `StringSession`. Treat it like a password.

### 2. Find the chat IDs

```sh
TG_API_ID=<id> TG_API_HASH=<hash> TG_SESSION='<session>' python3 tg_list_chats.py
```

Note the numeric IDs of the chats you want. You must already be a member of each.

### 3. Build `config.json`

Copy `config.example.json` → `config.json`, fill in `api_id`, `api_hash`, `session`,
and the `channels` list as ordered `[id, name]` pairs (put the smallest chat first so
you validate the whole pipeline before the big one).

### 4. (Optional) record the creds as a plain Secret

Per this repo's one-off convention (no ESO/SSM for a throwaway):

```sh
kubectl -n photos-relay create secret generic tg-oneshot \
  --from-literal=api_id=<id> --from-literal=api_hash=<hash> --from-literal=session='<session>'
```

The backfill itself reads `config.json` (below); the Secret is just the durable
record of the credential.

### 5. Stage into the relay pod

```sh
POD=$(kubectl -n photos-relay get pod -l app.kubernetes.io/name=photos-relay -o name | head -1 | cut -d/ -f2)
kubectl -n photos-relay exec "$POD" -- mkdir -p /state/tg-oneshot
kubectl -n photos-relay cp tg_oneshot.py "$POD":/state/tg-oneshot/tg_oneshot.py
kubectl -n photos-relay cp config.json   "$POD":/state/tg-oneshot/config.json
kubectl -n photos-relay exec "$POD" -- chmod 600 /state/tg-oneshot/config.json
kubectl -n photos-relay exec "$POD" -- pip install --quiet --root-user-action=ignore telethon
```

### 6. Launch (detached, survives your shell)

```sh
kubectl -n photos-relay exec "$POD" -- sh -c \
  'ADB=/opt/platform-tools/adb nohup python3 -u /state/tg-oneshot/tg_oneshot.py \
     > /state/tg-oneshot/run.log 2>&1 </dev/null &'
kubectl -n photos-relay exec "$POD" -- tail -f /state/tg-oneshot/run.log   # watch
```

### 7. (Optional) live status page

```sh
kubectl apply -f status-service.yaml   # ClusterIP + IngressRoute on the *.in front door
```

Open `https://photos-relay-tg.in.neovara.uk/` — shows per-channel progress, totals,
skips, and a heartbeat that goes **stale** if the process dies (your relaunch signal).
It routes through the shared `traefik-internal` device; do NOT use a
`loadBalancerClass: tailscale` Service (per-device, and it hangs on delete because
the operator's OAuth client can't delete tailnet devices).

### 8. Monitor & the one gotcha — relaunching after a pod restart

The backfill is a *detached process*, not a managed workload. If the relay pod
restarts (node event, OOM, redeploy), the process, its `pip`-installed Telethon, and
the status server all die — the status page goes down and the heartbeat goes stale.
**Nothing is lost** (state is on the PVC); just reinstall Telethon and relaunch — it
resumes from the per-chat high-water mark:

```sh
POD=$(kubectl -n photos-relay get pod -l app.kubernetes.io/name=photos-relay -o name | head -1 | cut -d/ -f2)
kubectl -n photos-relay exec "$POD" -- pip install --quiet --root-user-action=ignore telethon
# make sure none is already running before relaunching (see the matcher note below), then step 6.
```

### 9. Cleanup (when `run.log` says `ALL COMPLETE` and state has `done: true`)

```sh
kubectl -n photos-relay exec "$POD" -- /opt/platform-tools/adb shell 'rm -f /sdcard/DCIM/Camera/tg_* /sdcard/ui_tg.xml'
kubectl delete -f status-service.yaml
kubectl -n photos-relay delete secret tg-oneshot
kubectl -n photos-relay exec "$POD" -- sh -c 'rm -rf /state/tg-oneshot /tmp/tg_*'
kubectl -n photos-relay exec "$POD" -- pip uninstall -y telethon   # or just let a restart clear it
# and shred config.json / the session locally.
```

With the ClusterIP + IngressRoute page (step 7), `kubectl delete -f status-service.yaml`
removes it cleanly — no per-service tailnet device to chase in the admin console.

## Gotchas learned the hard way

- **`kubectl exec ... python3 - <<'PY'` does not feed stdin reliably** in this setup —
  the script runs but reads nothing. Always run scripts as *files* (`cp` then `exec
  python3 <path>`), never piped over exec stdin.
- **Checking whether the process is running:** match `tg_oneshot.py` in `/proc/*/cmdline`
  and **exclude your own checker's PID** — a naive `grep tg_oneshot` matches the check
  shell itself (its command line contains the string), a false positive that looks like
  a duplicate process. Never `kill` on that naive match; you'll SIGKILL your own shell.
- **Filenames with apostrophes** (e.g. `i'm-a-poor-guy.mp4`) broke the single-quoted
  `rm '<path>'` during reclaim, leaving files stranded on the phone though already
  backed up. `tg_oneshot.py` now sanitizes names to `[A-Za-z0-9._-]`; the fix is in.
- **Telethon appends an extension** when the download path has none and returns the
  real path — always use the returned path, not the one you passed.
- **Timeline dates:** mtime is set to the message date and adb preserves it, so Google
  Photos uses it when the file has no EXIF (compressed photos → message/post date).
  Files with embedded metadata (uncompressed "documents") keep their original capture
  date. The message date is the best signal available when EXIF is stripped.
- **Dedup:** the same clip in two chats is pushed twice; Google Photos' content-hash
  dedup collapses it. No local hashing.
- **Memory:** the backfill stays well under the relay pod's 512Mi limit (~150Mi
  observed) — it streams downloads — so it does not risk OOM-killing the relay.
