# Migration record — kiroku (first dev-owned app)

**Date:** 2026-07-09 · **Status:** ✅ migration complete — live in production on `kiroku.neovara.uk`.

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
- Exposed **internal-first** (`kiroku.in.neovara.uk`, Tailscale-only) for smoke-testing, then cut over
  to **public-only** at `kiroku.neovara.uk` (`web` entrypoint, cloudflared edge TLS) once verified — the
  internal route and the legacy `myanki.neovara.uk` alias were both retired (nothing else depends on
  either, since the old lab's DNS is decommissioned).

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
- Owner login confirmed working (real account, password reset — see incident below) and real vocab
  data confirmed synced correctly after the resource-sizing fix.
- Public cutover confirmed working at `kiroku.neovara.uk`.

## Incident: forgotten password, then an OOMKill on first real sync

**Password reset.** No self-service reset/change-password flow exists in kiroku (only register/login,
bcrypt cost 10 hashes in a `users` table). Reset by hand: scaled `kiroku-api` → 0, ran a throwaway
`alpine` pod mounting the live PVC, `sqlite3 UPDATE users SET password_hash=... WHERE email=...` with a
freshly bcrypt-hashed value, scaled back up. Real gap worth fixing in the app itself eventually (no
issue filed yet — low priority, single-operator homelab).

**OOMKilled on the first real login.** `kiroku-api`'s memory limit (512Mi, sized from a 3-day
Prometheus sample) was based on a near-empty test DB and only captured a small OCR burst — the first
real `sync/pull` against the actual ~14k-media-file library needed far more memory and got OOMKilled
(exit 137). Symptom in the browser was misleading: Safari reported the resulting connection drop as
"due to access control checks" (its generic wrapper for a network failure it can't detail), which read
like a CORS bug — direct `curl` with real Host/Origin headers proved routing was never broken.

Fix cascaded through three guardrails, each hit in turn once the previous was raised:
1. `kiroku-api` memory limit 512Mi → **1.5Gi** (real fix — the actual working-set need).
2. M0's per-namespace `LimitRange` `max.memory` 1Gi → **2Gi** (the 1.5Gi limit exceeded the old ceiling).
3. `ResourceQuota` `limits.memory` 2Gi → **3Gi** (default `RollingUpdate` briefly needs *both* the old
   and new pod's limits counted at once — 640Mi + 1536Mi = 2176Mi blew the old 2Gi cap, so the rollout
   was rejected on every retry until the quota itself was raised).

All three are permanent fixes (not migration-only artifacts) — see `docs/concepts/Kubernetes
Concepts.md` for the generalized lessons (QoS/eviction, the RollingUpdate⨯ResourceQuota surge collision,
the Safari network-error quirk).

## Rollback / safety

- Old Compose stack was **stopped, not deleted**, and `/storage/kiroku/data` was **never touched** by
  the migration (only read from) — `docker start kiroku kiroku-api` would restore the old instance if
  ever needed. Now that public cutover is verified working, the old stack is ready for decommission.
