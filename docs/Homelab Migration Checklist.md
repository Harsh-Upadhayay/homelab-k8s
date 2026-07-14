# Homelab migration checklist

Source: `../homelab` (docker compose, this workstation). Snapshot date: 2026-07-09.
Check off apps as they're migrated to k8s or eliminated outright.

## Migrated

- [x] kiroku — live on `kiroku.neovara.uk`. Old containers stopped, not yet removed.
- [x] audiobookshelf — Kubernetes 2.35.1 is live at `audiobookshelf.neovara.uk`. Exact state and
  all 1,105 media files are migrated; bulk media is on the one-replica Longhorn tier, with the
  former claim retained as rollback. Compose is intentionally still running but is no longer on
  the public route. Plan/runbook: `docs/migrations/audiobookshelf.md`.
- [x] nextcloud + nextcloud-db + nextcloud-redis — file storage/sync, real personal data.

## Deferred because of blockers

- [ ] immich — do not migrate in the current run. Keep Compose and `/storage/immich` intact; the
  321 GB library, storage placement/workstation rebuild, and v2.7.3 pgvecto-rs → v3 VectorChord
  transition need a separate plan.

## To preserve data, and migrate later, no active users so migration isnt' urgent, we can depricate this workstation without these apps live, but data is importatnt, whenever the k8s deployment is ready, the data should be restored exactly as it was on this host, and the k8s deployment should be able to pick up where this host left off.

- [ ] jobhunt — own project. `-django`, `-celery-beat`, `-celery-worker-1`, `-mysql`, `-redis`, `-frontend`, `-nginx`.

## To skip entirely
- [ ] Mediaserver stack: not required :-0
- [ ] homepage — serves root `neovara.uk`, reads host Prometheus. Needs Traefik cutover + rework.
- [ ] traefik + traefik-errorpages — old-lab ingress. Needed until every subdomain is cut over.
- [ ] cloudflared — public tunnel entrypoint. Needed until all public apps are migrated.
- [ ] authelia + lldap — auth/identity provider for old-lab apps.
- [ ] ollama + ollama-openai-gateway — GPU-dependent LLM inference.
- [ ] jenkins + dind — CI/CD.
- [ ] openvscode-server — remote dev IDE.
- [ ] monitoring stack — prometheus, grafana, cadvisor, node-exporter, dcgm-exporter (GPU metrics).
- [ ] watchtower — likely superseded by GitOps, not a real migration target.
- [ ] portfolio — personal site, check if it shares homepage's "reads host state" pattern.
- [ ] openclaw — permissions + gateway containers, purpose unclear, verify before deciding.
- [ ] remote-desktop — bare-metal xrdp/GNOME setup, not a container. Goes away if this host becomes a headless worker.

## Notes

- No GPU configuration anywhere in the current migration. Immich itself is deferred; the host GPU
  (GTX 1660 SUPER) is not carried into the cluster as part of this effort.
- `gluetun` reporting unhealthy as of this snapshot — worth checking independent of migration timing.
