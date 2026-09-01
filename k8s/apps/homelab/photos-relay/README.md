# photos-relay — Immich → Google Photos upload relay

Mirrors the cluster's entire Immich library to Google Photos by feeding originals
through a dedicated Android handset (Moto G13, Google One 2 TB) whose on-device
Google Photos app does the actual backup. Batches are pushed, confirmed in the
cloud, then deleted from the phone to reclaim space — forever.

Full rationale and the safety model are in
`docs/superpowers/specs/2026-09-02-immich-google-photos-relay-design.md`.
**One-line safety summary:** Immich is the source of truth and the phone is
disposable, so the reclaim-space deletes can never lose an original.

## Moving parts

| Layer | Where | What it does |
|---|---|---|
| USB passthrough | `terraform/proxmox` (`usb_devices` on `k3s-worker-3`) | maps the handset (bus-port `1-4`) into the worker |
| Device perms | `ansible/roles/photos_relay_udev` (`--tags photos_relay`) | relaxes the ADB node, keyed on interface class |
| Relay app | this directory, synced by `k8s/argocd/apps/photos-relay.yaml` | the batch loop, pinned to `k3s-worker-3` |
| Secrets | ESO ← SSM `/neovara/homeinfra/photos-relay/*` | Immich API key + the pre-authorized adb keypair |

The relay program is `relay/relay.py`; `manifests/configmap.yaml` embeds a copy of
it. **Regenerate the ConfigMap after editing the script** — see below.

## Bring-up (one-time)

Everything except step 1 is already done on-cluster; step 1 is the only human
action left.

1. **Store the secrets in SSM** (Terraform `terraform/aws` is the right long-term
   home; these commands are the quick path):

   ```sh
   # (a) Immich API key — create it in Immich UI: Account → API Keys,
   #     grant asset read + download, then:
   aws ssm put-parameter --type SecureString \
     --name /neovara/homeinfra/photos-relay/immich-api-key \
     --value '<the-immich-api-key>'

   # (b) the pre-authorized adb keypair (already generated + accepted by the
   #     handset). Store both halves so the pod reuses the identity and the
   #     phone never re-prompts:
   aws ssm put-parameter --type SecureString \
     --name /neovara/homeinfra/photos-relay/adbkey     --value "$(cat adbkey)"
   aws ssm put-parameter --type String \
     --name /neovara/homeinfra/photos-relay/adbkey-pub --value "$(cat adbkey.pub)"
   ```

2. **Force ESO to sync** (the store is `refreshInterval: "0"`):

   ```sh
   kubectl annotate externalsecret -n photos-relay photos-relay-immich \
     force-sync=$(date +%s) --overwrite
   kubectl annotate externalsecret -n photos-relay photos-relay-adb \
     force-sync=$(date +%s) --overwrite
   ```

3. Argo CD syncs the app automatically. Watch it work:

   ```sh
   kubectl logs -n photos-relay deploy/photos-relay -f
   ```

Until the Immich key exists the pod stays up and idle (it logs that it is waiting)
rather than crash-looping, so you can inspect it before the key lands.

## Editing the relay logic

The script is mounted from a ConfigMap. After changing `relay/relay.py`, rebuild
the ConfigMap so Git and the running pod agree:

```sh
python3 - <<'PY'
src = open("relay/relay.py").read()
ind = "".join("    "+l if l.strip() else l for l in src.splitlines(keepends=True))
head = open("manifests/configmap.yaml").read().split("relay.py: |\n",1)[0] + "relay.py: |\n"
open("manifests/configmap.yaml","w").write(head+ind)
PY
```

## Tunables (Deployment env)

| Var | Default | Meaning |
|---|---|---|
| `BATCH_MAX_BYTES` | 15 GiB | max bytes pushed before waiting for backup |
| `BATCH_MAX_FILES` | 300 | max files per batch |
| `BACKUP_TIMEOUT_S` | 6 h | give up waiting on a batch (it retries next pass) |
| `IDLE_INTERVAL_S` | 1 h | pause after a pass finds nothing new |
| `PUSH_DIR` | `/sdcard/DCIM/Camera` | a folder Google Photos already backs up |
| `RELAY_PREFIX` | `immich_` | marks relay files so deletes never touch real photos |

## Known follow-ups

- Replace the apk-install entrypoint with the baked image (`relay/Dockerfile`) to
  drop the runtime dependency on the Alpine CDN.
- Move the SSM parameters into `terraform/aws` for the same IaC coverage the rest
  of the secrets have.
