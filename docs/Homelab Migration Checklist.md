# Homelab migration checklist

Source: `../homelab` (docker compose, this workstation). Snapshot date: 2026-07-09.
Check off apps as they're migrated to k8s or eliminated outright.

## Migrated

- [x] kiroku — live on `kiroku.neovara.uk`. Old containers stopped, not yet removed.

## To migrate immediately, blockers to this worstation's deprecation
- [x] audiobookshelf — media server for audiobooks/podcasts.
- [x] immich — `_server`, `_machine_learning` (CPU-only — no GPU accel this migration), `_redis`, `_postgres`.
- [x] nextcloud + nextcloud-db + nextcloud-redis — file storage/sync, real personal data.

## To preserve data, and migrate later, no active users so migration isnt' urgent, we can depricate this workstation without these apps live, but data is importatnt, whenever the k8s deployment is ready, the data should be restored exactly as it was on this host, and the k8s deployment should be able to pick up where this host left off.

- [ ] jobhunt — own project. `-django`, `-celery-beat`, `-celery-worker-1`, `-mysql`, `-redis`, `-frontend`, `-nginx`.
- [ ] mediaserver stack — gluetun (⚠️ currently unhealthy), qbittorrent, flaresolverr, prowlarr, sonarr, radarr, jellyseerr, jellyfin.

## To skip entirely
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

- No GPU configuration anywhere for this migration (host or cluster). immich runs its ML in CPU-only mode;
  GPU acceleration is an optional perk deferred to later. The host GPU (GTX 1660 SUPER) is not carried into
  the cluster as part of this effort.
- `gluetun` reporting unhealthy as of this snapshot — worth checking independent of migration timing.
