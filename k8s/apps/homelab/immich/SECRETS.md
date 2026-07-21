# Immich secrets

Secrets remain imperative and uncommitted as required by this repository's
current secret-management boundary.

The existing `immich/immich-db` Secret must contain exactly these keys:

- `DB_USERNAME`
- `DB_PASSWORD`
- `DB_DATABASE_NAME`

To recreate it after a cluster rebuild, supply the original restored-database
credentials at runtime:

```bash
kubectl -n immich create secret generic immich-db \
  --from-literal=DB_USERNAME='<existing username>' \
  --from-literal=DB_PASSWORD='<existing password>' \
  --from-literal=DB_DATABASE_NAME='<existing database>'
```

Never commit the literal values. The PostgreSQL companion and chart-managed
Immich server both read the same three keys.
