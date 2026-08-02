# Nextcloud — secrets (ESO-managed + one imperative)

Nextcloud uses two Kubernetes Secrets. `nextcloud-db` is reconciled by External
Secrets Operator from AWS SSM Parameter Store (the platform-wide pattern — see
CLAUDE.md, "Secrets management is live via ESO + AWS SSM"). `nextcloud-admin`
stays imperative by design (explained below). ArgoCD renders neither: the
ExternalSecret is a CRD ArgoCD applies but ESO owns the resulting Secret, and
the admin Secret is applied by hand, so `prune`/`selfHeal` can't touch either.
Documented here as **name + keys only, no values**.

## `nextcloud-db` (Secret, namespace `nextcloud`) — ESO-managed

Consumed by BOTH our own Postgres (`manifests/postgres.yaml`) and the chart's
`externalDatabase.existingSecret` (`values.yaml`). ESO creates and owns it
(`creationPolicy: Owner`); do **not** `kubectl create secret nextcloud-db` —
that would fight the ExternalSecret controller.

Defined by `manifests/external-secret.yaml`:

| Secret key | SSM remoteRef | Notes |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | `/neovara/homeinfra/nextcloud/db-password` | SecureString. Must equal the source install's DB password (already baked into the migrated `config.php`'s `dbpassword`) so the app authenticates unchanged after restore. |
| `db-username` | `/neovara/homeinfra/nextcloud/db-username` | Plaintext String. `nextcloud`. |

The SSM parameters themselves live in Terraform (`terraform/aws/ssm.tf`); the
apply/rotation flow (including the write-only `value_wo` inputs) is documented
in `terraform/aws/SECRETS.md`.

The ExternalSecret references the platform-wide `ClusterSecretStore` named
`aws-parameter-store` (namespace `external-secrets`), which is gated to
namespaces labeled `neovara-external-secrets=true` — the `nextcloud` namespace
carries that label. `refreshInterval: "0"` means ESO does **not** periodically
re-pull from SSM. Force a re-sync after rotating a value in AWS by annotating:

```
kubectl -n nextcloud annotate externalsecret nextcloud-db --overwrite \
  refreshed-at="$(date -Iseconds)"
```

## `nextcloud-admin` (Secret, namespace `nextcloud`) — imperative, by design

Referenced by the chart's `nextcloud.existingSecret`. This one is **deliberately
not** an ExternalSecret, because SSM Parameter Store rejects empty values and
the username key must be empty:

- An empty admin user makes the image skip first-run auto-install, so the
  migration (restore DB + inject config.php) is what establishes the instance,
  not a fresh install.
- The password is a stable dummy (never used for login; real accounts come from
  the DB restore, and the operator password is set via `occ` afterward).

Because both values are meaningless placeholders, there is also nothing to gain
from rotating them through SSM.

| Key | Value | Notes |
| --- | --- | --- |
| `nextcloud-username` | *(empty)* | Empty ⇒ no auto-install. Cannot live in SSM (empty values rejected). |
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
