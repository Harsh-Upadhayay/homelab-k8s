# Migration record — kiroku (first dev-owned app)

**Date:** 2026-07-09 · **Status:** deployed + data migrated (internal-only); **public cutover pending.**

kiroku = a custom two-tier app (Next.js frontend `kiroku` :3000 + Go API `kiroku-api` :8080,
SQLite/WAL store + tesseract OCR). Own public repo `github.com/Harsh-Upadhayay/kiroku`
(local checkout `~/myAnki`). First app migrated Compose → k8s, and the first exercise of the
Option E dev-owned pattern + the M0 host→PVC mover.

## Topology deployed

- **Manifests** live in the app repo under `deploy/` (Deployments, Services, PVC, IngressRoute,
  secure-headers Middleware); the cluster repo holds only pointer + governance Applications.
- **`kiroku-governance`** Application (default project, sync-wave 0) → `k8s/apps/personal/kiroku/`
  (namespace + ResourceQuota + LimitRange + deployer RBAC).
- **`kiroku`** Application (personal project, sync-wave 1) → the app repo's `deploy/`.
- Image pinned to immutable `sha-6c43b70` (CI publishes `type=sha,prefix=sha-`).
- Resources sized from 3 days of Prometheus data; CPU limits **kept** (personal-ns blast-radius
  policy, unlike trusted platform apps). Quota fits with wide headroom.
- Exposed **internal-first**: `kiroku.in.neovara.uk` (websecure, Tailscale-only). Public route deferred.

## Data migration — decisions

Source: `/storage/kiroku/data` on this workstation (which *is* the old-lab host). ~460M.

1. **Decouple deploy from data from cutover.** Deploy internal → migrate data → verify → *then* public
   DNS → decommission old. Nothing public moved during any of this.
2. **Quiesce the source before copying.** `kiroku.db` is SQLite in **WAL mode**; a live copy risks a
   torn DB. Stopped the old Compose stack (`docker stop kiroku kiroku-api`) so writes ceased and the
   file set was a consistent snapshot. (On the new api's first open, SQLite checkpointed the copied
   WAL into the main DB — verified: `-wal`/`-shm` disappeared, size 518M→452M.)
3. **Replace, not merge.** The PVC held a 49 KB smoke-test `kiroku.db` from an internal test login;
   removed it before copying so the real DB is authoritative.
4. **Skip the two `*.backup-*` files** (redundant point-in-time SQLite copies) — no need in the hot volume.
5. **Mover pattern (M0), local-source variant:** scale `kiroku-api` → 0 (release the RWO PVC) → throwaway
   `alpine` mover pod mounts the PVC (`fsGroup: 1000`) → `tar -C /storage/kiroku/data --exclude='*.backup-*'
   -cf - . | kubectl exec -i … -- tar -C /data -xf -` (data is local, streamed via exec — stdin-through-proxy
   confirmed working) → `chown -R 1000:1000` → delete mover → scale api → 1.
6. **Ownership uid/gid 1000** = the image's `appuser`; happens to match the host's file uid, so it lined up.
7. **⚠️ Reclaim caveat (open):** `kiroku-api-data` uses the `longhorn` StorageClass (`reclaimPolicy: Delete`).
   Once the old lab is decommissioned this PVC becomes the **sole** copy — a `kubectl delete ns kiroku`
   would then destroy it. Revisit `Retain`/`longhorn-static` (or a backup target) **before** old-lab teardown.

## Verification

- `kiroku-api` `1/1 Running`, logs show live `/api/sync/*` requests against the DB.
- `/app/data`: `kiroku.db` 84M, `db.json`, `media/` (13,912 files), `uploads/`, owned `appuser`.
- ResourceQuota `Used` matches the sized requests/limits.
- **Pending:** owner smoke-test at `kiroku.in.neovara.uk` with the real account (vocab decks present).

## Rollback / safety

- Old Compose stack **stopped, not deleted**; `/storage/kiroku/data` is **untouched** — `docker start
  kiroku kiroku-api` restores the old instance. Keep until public cutover is verified.

## Remaining (public cutover)

1. Add a **public IngressRoute** (`web` entrypoint, `kiroku.neovara.uk` + legacy `myanki.neovara.uk`).
2. Repoint Cloudflare DNS/tunnel for those hostnames from the old lab → this cluster's tunnel.
3. Smoke-test public, then decommission the old kiroku for good.
