# Homelab migration checklist

Source: `../homelab` (Docker Compose on the workstation). Initial inventory: 2026-07-09.
Last reconciled with Git and the live cluster: 2026-07-22 JST.

The old Compose stack was brought back up for final manual comparison. A running source container
does not mean it is still authoritative or required after the workstation rebuild; use the categories
below and the per-app runbooks.

## Migrated

- [x] kiroku — live on `kiroku.neovara.uk`. Old containers stopped, not yet removed.
- [x] audiobookshelf — Kubernetes 2.35.1 is live at `audiobookshelf.neovara.uk`. Exact state and
  all 1,105 media files are migrated; bulk media is on the one-replica Longhorn tier, with the
  former claim released after acceptance. Compose is intentionally still running as rollback but
  is no longer on the public route. Plan/runbook: `docs/migrations/audiobookshelf.md`.
- [x] nextcloud + nextcloud-db + nextcloud-redis — file storage/sync, real personal data.
- [x] immich — exact Compose state migrated to retained Longhorn PVCs, pgvecto.rs converted to
  VectorChord, upgraded from v2.7.3 to v3.0.3, and accepted through the internal route. The old
  Compose deployment was stopped after manual comparison on 2026-07-22 JST. The application is
  intentionally in GitOps maintenance while the workstation becomes a Proxmox host; preserve
  both HDD partitions and follow `docs/migrations/immich.md`. The normal recovery path reuses the
  existing PVC/Longhorn volume by reassociating the preserved Longhorn disk UUID on the new worker;
  it does not copy the 350 GiB library into a new PVC.

## Preserve data and migrate later

These workloads do not block installing Proxmox because they do not need to remain live during the
transition. Their source data is still important: keep the rollback/deferred-data partition intact
until each later Kubernetes deployment has restored and verified it.

- [ ] jobhunt — own project. `-django`, `-celery-beat`, `-celery-worker-1`, `-mysql`, `-redis`, `-frontend`, `-nginx`.
- [ ] mediaserver stack — gluetun, qbittorrent, flaresolverr, prowlarr, sonarr, radarr, jellyseerr,
  jellyfin. Preserve the media tree and per-service configuration; migrate later by hand.
- [ ] ollama + ollama-openai-gateway — preserve model/config data; GPU enablement is a separate
  later project.
- [ ] openclaw — preserve config/workspace; clarify the target runtime before migrating.

## Drop or already replaced

- [x] homepage — not required after old-host retirement; its host-Prometheus dependency is not being ported.
- [x] traefik + traefik-errorpages — replaced by the cluster's GitOps-managed Traefik.
- [x] cloudflared — replaced by the cluster Deployment and tunnel path.
- [x] authelia + lldap — intentionally removed; migrated apps use local login or tailnet reachability.
- [x] jenkins + dind — superseded by GitHub Actions.
- [x] openvscode-server — remote IDE not being migrated.
- [x] old monitoring stack — replaced by kube-prometheus-stack, Loki, and Alloy in Kubernetes.
- [x] watchtower — superseded by pinned versions plus GitOps reconciliation.
- [x] portfolio — remains on GitHub Pages.
- [x] remote-desktop — bare-metal xrdp/GNOME is removed when this workstation becomes a Proxmox host.

## Notes

- Immich machine learning is CPU-only. The workstation GPU (GTX 1660 SUPER) is not carried into
  the cluster as part of this migration.
- `gluetun` was still unhealthy in the source Compose stack on 2026-07-22; that predates its future
  Kubernetes migration and is not an Immich recovery blocker.
- Nextcloud and Kiroku currently use `Delete`-reclaim Longhorn claims. Their preserved source data is
  the rollback until a real backup/Retain policy is established; do not clean the source partition
  merely because the applications are live in Kubernetes.
