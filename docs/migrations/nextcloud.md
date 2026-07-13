# Migration record — nextcloud (M5, first platform/off-the-shelf app)

**Date:** 2026-07-09 (migrated) → 2026-07-13 (upgraded to latest) · **Status:** ✅ **complete — live in
production on v34.0.1 (latest) at `nextcloud.in.neovara.uk`** (internal/Tailscale). ArgoCD app
`Synced/Healthy`; login + data + cron + external path all verified at v34.

This migration was executed **autonomously** (operator AFK) with a standing mandate: use managed/Helm
tooling as much as possible, deploy internally first, migrate all data, validate by logging in as the
real account (password forgotten → regenerated), then upgrade to the latest Helm-managed version. This
file is the decision log requested for that unattended run.

## The big decision: managed Helm chart, own Postgres/Redis

Instead of hand-writing every manifest, nextcloud is deployed via the **community `nextcloud/helm`
chart 9.2.0** (app Deployment + native CronJob + config/probes), in the same ArgoCD **multi-source**
shape as `k8s/traefik` (chart · `$values` · companion `manifests/`). Rationale:

- It matches the platform's own convention — every other platform app (traefik, longhorn, cert-manager,
  kube-prometheus-stack) is chart+values via ArgoCD; hand-rolled manifests (cloudflared) are the
  exception for things with no chart.
- It's the community standard for Nextcloud-on-k8s. No official operator exists; Nextcloud AIO is
  Docker-first and cannot cleanly import an existing non-AIO instance, so it's the wrong tool for a
  *migration*.

**But NOT the chart's bundled database/cache.** The chart's `postgresql`/`redis` subcharts pin
`bitnamilegacy/*` images — the dead Bitnami public catalog (Broadcom moved it to an unmaintained
"legacy" repo in Aug 2025; production images are now paid). So we run our **own** Postgres 16 + Redis 8
on **official images** (`manifests/postgres.yaml`, `manifests/redis.yaml`) and point the chart at them
with `externalDatabase` / `externalRedis`. This is the documented community migration path.

Layout (`k8s/apps/homelab/nextcloud/`): `values.yaml` (chart values) + `manifests/` (namespace, own
Postgres, own Redis, PVCs, Traefik IngressRoute/Middlewares) + `SECRETS.md`. Registered by
`k8s/argocd/apps/nextcloud.yaml` (project `homelab`, which now also allow-lists the nextcloud helm repo).

## Key design decisions

- **`strategy: Recreate`** on our Postgres (single-writer RWO — never two PG on one datadir).
- **image pinned to the SOURCE version (31.0.8)** for the migration; upgraded one major at a time
  afterward (Nextcloud forbids skipping a major).
- **No auto-install:** admin creds are supplied via an existing Secret with an **empty username**, so the
  image lays down the code but does not run the first-run installer. The instance is established by the
  restore instead.
- **`config.php` migrated verbatim** to preserve `secret` / `passwordsalt` / `instanceid` byte-exact
  (only `dbuser`/`dbpassword` were reconciled to the new role — see incidents).
- **`startupProbe` enabled** (chart default is off). The image copies ~1 GiB of code onto the replicated
  Longhorn PVC on first boot; the 40s liveness window was SIGKILLing it mid-copy in a crash loop. The
  startupProbe gates liveness for up to ~10 min — also covers the schema-migration pause on each upgrade.
- **Cron as a native CronJob** with `podAffinity` co-locating it onto the app pod's node (cron.php needs
  the same RWO PVCs; RWO binds to a node, not a pod, so same-node pods share it).
- **`longhorn-replicated`** (2 copies) for all three PVCs — authoritative personal data. Same
  reclaim-`Delete` caveat as kiroku: the old-lab `/storage` copy is the fallback until the
  workstation-as-node conversion; revisit `Retain`/`longhorn-static` before that teardown.

## Auth model: OIDC dropped → local account (as mandated)

The source logged in via **OIDC** (`user_oidc` → authelia). Authelia is retired
(Migration Plan: "public apps → their own built-in login"), so OIDC was dropped:

- The real user "Harsh" was an **OIDC-backend** user (uid `cbb605e9…64hex`, email
  `harshupadhayay906@gmail.com`, **admin** group) — *not* in `oc_users` (the local DB backend).
- Disabled `user_oidc`, then created a **local** account with the **same uid** so every migrated file,
  share, storage and account-property maps to it unchanged. `occ user:add` refuses when the home data
  already exists, so the `oc_users` row was inserted directly and the password set via
  `occ user:resetpassword`.
- Login verified over WebDAV (HTTP 207) with **both** the uid and the email.

## Data migration — what was done

Old instance quiesced (`occ maintenance:mode --on`) for a consistent snapshot, then:

1. **Postgres = logical restore.** `pg_dump` of the old DB (4.3 MB, 132 tables) → `psql` into the new PG
   pod. The DB is the source of truth; file blobs are meaningless without its `oc_*` index.
2. **Files = mover via `kubectl exec`.** The chart mounts one main PVC by **subPath** (`root/html/config/
   custom_apps/themes/tmp`) + a separate `data` PVC. Let the image lay down fresh v31.0.8 code, then
   injected only the stateful subset into the running pod's mounts: `config/config.php`, the three
   `custom_apps` (diary, drawio, user_oidc), and the full `data/` tree (`tar` streamed old→new).
   `chown 33:33`. **3074/3074 files, 1.4 G/1.4 G — exact match** (the bulk, 1.4 G, is `files_trashbin`;
   active `files/` is ~1.9 M / 188 items — `.ssh (1)`, `MODE`, `MyMind`, `Obsidian`, `Photos`).
3. **Reconcile:** `db:add-missing-indices/columns/primary-keys`, `maintenance:repair`,
   `maintenance:update:htaccess` (pretty URLs), trusted domain `nextcloud.in.neovara.uk`,
   `trusted_proxies=10.42.0.0/16` (Traefik pods), `overwrite*` for the internal host.

The **old Compose stack was only stopped/quiesced, never deleted**, and `/storage/nextcloud` was only
read — `docker compose up` + `occ maintenance:mode --off` restores the old instance if ever needed.

## Validation (v31.0.8)

- `status.php` over the **real tailnet path** (curl → `traefik-internal` 100.79.208.52, SNI
  `nextcloud.in.neovara.uk`): **HTTP 200**, valid `*.in.neovara.uk` Let's Encrypt cert,
  `installed:true, maintenance:false`.
- `/` → 302 → `/login` → 200. In-cluster Traefik routing → app → 200.
- Login via uid **and** email → WebDAV 207; real folders listed.

## Upgrade to latest (v34)

Path: **31.0.8 → 32.0.12 → 33.0.6 → 34.0.1** (chart 9.2.0 appVersion), one major per commit; each bump
triggers the image's automatic `occ upgrade` on boot (startupProbe covers the migration pause). All four
states verified `needsDbUpgrade:false` + `maintenance:false` before moving on; post-upgrade
`db:add-missing-*` and `maintenance:repair` ran at v34. **Final: v34.0.1, ArgoCD `Synced/Healthy`,
status.php + /login = HTTP 200 over the tailnet path.**

## Incidents (autonomous run)

1. **AppProject rejected the helm repo** at first (`InvalidSpecError`) — the app-of-projects hadn't yet
   synced the `homelab` AppProject's new `sourceRepos` entry. Hard-refreshed `appprojects`; cleared.
2. **Crash loop on first boot** — liveness SIGKILL during the ~1 GiB code copy. Fixed by enabling the
   `startupProbe` (now permanent).
3. **`config.php` DB user mismatch** — the migrated config connected as `oc_admin` (a role Nextcloud
   created at original install) which doesn't exist in the new PG. Reconciled `dbuser`→`nextcloud` /
   `dbpassword`→the new role's password (env config, not the sacred secret/salt/instanceid).
4. **Login 401 despite a valid hash** — traced to a stale password value passed via a temp file, not the
   account (a freshly `occ`-created probe user authenticated fine). Re-set the password inline; login OK.
5. **Mid-upgrade cluster outage:** during the 31→32 rollout all k3s nodes dropped off both Tailscale and
   the LAN simultaneously (Proxmox VMs / their network — unrelated to the migration; a pod upgrade cannot
   drop the hypervisor). All state was persisted (Longhorn PVCs, restored DB, pushed git incl. the 32.0.12
   bump). On recovery ArgoCD reconciled to 32.0.12 and the upgrade chain resumed and completed to v34.
6. **CronJob in Error → Argo `Degraded`:** the chart's CronJob container defaults to **root (uid 0)**,
   but Nextcloud refuses `cron.php` from any uid other than `config.php`'s owner (33). Fixed by setting
   `cronjob.cronjob.securityContext.runAsUser: 33` (the chart documents exactly this). Manual `create job
   --from=cronjob` then succeeded; app health returned to `Healthy`.

## Credential

The operator account password (regenerated, since the original was forgotten) is handed over in the chat
session, not stored here or in git. Login: `harshupadhayay906@gmail.com` (or the uid) at
`https://nextcloud.in.neovara.uk`.
