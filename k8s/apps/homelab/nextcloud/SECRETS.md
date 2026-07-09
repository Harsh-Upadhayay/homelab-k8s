# Nextcloud — imperative secrets (NOT committed)

Per the platform convention (Migration Plan → "Secrets = imperative `kubectl create
secret`, never committed"; ADR-0003 deferred-secrets), Nextcloud's secret material is
created by hand and referenced via `secretKeyRef` / the chart's `existingSecret`.
ArgoCD never renders it, so `prune`/`selfHeal` can't touch it. Documented here as
**name + keys only, no values**.

## `nextcloud-db` (Secret, namespace `nextcloud`)

Consumed by BOTH our own Postgres (`manifests/postgres.yaml`) and the chart's
`externalDatabase.existingSecret` (`values.yaml`).

| Key | Consumed by | Notes |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | Postgres container init **and** the app (chart `passwordKey`) | Must equal the source install's DB password (already baked into the migrated `config.php`'s `dbpassword`) so the app authenticates unchanged after restore. |
| `db-username` | app (chart `usernameKey`) | `nextcloud`. |

```
kubectl -n nextcloud create secret generic nextcloud-db \
  --from-literal=POSTGRES_PASSWORD='<NEXTCLOUD_DB_PASS from source ops/.env.local>' \
  --from-literal=db-username='nextcloud'
```

## `nextcloud-admin` (Secret, namespace `nextcloud`)

Referenced by the chart's `nextcloud.existingSecret`. The **username key is empty on
purpose** — an empty admin user makes the image skip first-run auto-install, so the
migration (restore DB + inject config.php) is what establishes the instance, not a
fresh install. The password is a stable dummy (never used for login; real accounts
come from the DB restore, and the operator password is set via `occ` afterward).

| Key | Value | Notes |
| --- | --- | --- |
| `nextcloud-username` | *(empty)* | Empty ⇒ no auto-install. |
| `nextcloud-password` | random | Unused; stable so ArgoCD sees no drift. |

```
kubectl -n nextcloud create secret generic nextcloud-admin \
  --from-literal=nextcloud-username='' \
  --from-literal=nextcloud-password="$(openssl rand -base64 18)"
```

## Deliberately NOT a k8s Secret

- **`secret`, `passwordsalt`, `instanceid`** — live inside `config.php`, migrated
  verbatim into the `nextcloud-main` PVC's `config/` subPath. Must survive byte-exact.
- **Redis auth** — none; the source and new Redis both run unauthenticated.
