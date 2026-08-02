# AWS Terraform — secrets at apply time

This stack provisions the AWS-side infra for ESO + SSM: the IAM user/role ESO
assumes, and the SSM parameters backing every cluster Secret consumed by pods.

## The migrated secrets

All cluster Secrets consumed by pods are backed by SSM parameters here. Secret
values are declared as **ephemeral** variables (`ephemeral = true`), so Terraform
never persists them to state or plan files. Paired with `value_wo` on the
`aws_ssm_parameter`, each flows env → Terraform memory → SSM and touches disk
nowhere. Non-secret values (usernames, db names, client IDs) use `insecure_value`
and live in `terraform.tfvars`.

| Secret (namespace) | SSM key(s) | type | TF var |
|---|---|---|---|
| `nextcloud-db` (nextcloud) | `…/nextcloud/db-password` | SecureString | `nextcloud_db_password` |
| | `…/nextcloud/db-username` | String | *(tfvars)* |
| `immich-db` (immich) | `…/immich/db-password` | SecureString | `immich_db_password` |
| | `…/immich/db-username` | String | *(tfvars)* |
| | `…/immich/db-database-name` | String | *(tfvars)* |
| `cloudflare-api-token` (cert-manager) | `…/cert-manager/cloudflare-api-token` | SecureString | `cert_manager_cloudflare_api_token` |
| `cloudflared-tunnel-token` (cloudflare) | `…/cloudflared/tunnel-token` | SecureString | `cloudflared_tunnel_token` |
| `grafana-admin-secret` (monitoring) | `…/grafana/admin-password` | SecureString | `grafana_admin_password` |
| | `…/grafana/admin-user` | String | *(tfvars)* |
| `operator-oauth` (tailscale) | `…/tailscale/client-secret` | SecureString | `tailscale_client_secret` |
| | `…/tailscale/client-id` | String | *(tfvars)* |
| `tailscale-auth` (workbench) | `…/workbench/tailscale-authkey` | SecureString | `workbench_tailscale_authkey` |

### Supply secrets at apply time

Each SecureString's value must be supplied via a `TF_VAR_<name>` env var. The
cleanest source is the cluster itself (the values already live there), so they
never appear in shell history or process args:

    export TF_VAR_nextcloud_db_password="$(kubectl -n nextcloud get secret nextcloud-db -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)"
    export TF_VAR_immich_db_password="$(kubectl -n immich get secret immich-db -o jsonpath='{.data.DB_PASSWORD}' | base64 -d)"
    export TF_VAR_cert_manager_cloudflare_api_token="$(kubectl -n cert-manager get secret cloudflare-api-token -o jsonpath='{.data.api-token}' | base64 -d)"
    export TF_VAR_cloudflared_tunnel_token="$(kubectl -n cloudflare get secret cloudflared-tunnel-token -o jsonpath='{.data.token}' | base64 -d)"
    export TF_VAR_grafana_admin_password="$(kubectl -n monitoring get secret grafana-admin-secret -o jsonpath='{.data.admin-password}' | base64 -d)"
    export TF_VAR_tailscale_client_secret="$(kubectl -n tailscale get secret operator-oauth -o jsonpath='{.data.client_secret}' | base64 -d)"
    export TF_VAR_workbench_tailscale_authkey="$(kubectl -n workbench get secret tailscale-auth -o jsonpath='{.data.TS_AUTHKEY}' | base64 -d)"
    terraform apply

`terraform plan` also requires the ephemeral vars to be set (they're required
inputs even though write-only values aren't diffable).

### Rotation

`value_wo` values aren't diffable, so to rotate: bump `value_wo_version` on the
parameter (e.g. 1 → 2), set the new `TF_VAR_<name>`, and apply. Then annotate the
cluster's ExternalSecret to force ESO to re-pull:

    kubectl annotate externalsecret <name> -n <ns> --overwrite \
      refreshed-at="$(date -Iseconds)"

## Bootstrap tier — host-side secrets, SSM only (no ESO)

Three `.env` values now live in SSM under `/neovara/bootstrap/` as the durable
source of truth, so the gitignored `.env` file is no longer the only copy:

| Key | SSM parameter | Consumed by |
|---|---|---|
| `K3S_TOKEN` | `/neovara/bootstrap/k3s-token` | k3s Ansible roles (node join) |
| `PROXMOX_VE_API_TOKEN` | `/neovara/bootstrap/proxmox-ve-api-token` | Terraform Proxmox provider |
| `IMMICH_LOGIN` | `/neovara/bootstrap/immich-login` | *(orphan — nothing consumes it yet)* |

These are **not** wired to ESO (no ExternalSecret): they are read by Ansible /
Terraform **on the machines**, not by pods, so there is no K8s Secret to
materialize. Read them directly with the tooling's own AWS credentials:

    aws ssm get-parameter --name /neovara/bootstrap/k3s-token --with-decryption \
      --query Parameter.Value --output text

Supply at apply time from `.env` (at the repo root — note the path):

    export TF_VAR_k3s_token="$(grep '^K3S_TOKEN=' ../../.env | cut -d= -f2-)"
    export TF_VAR_proxmox_ve_api_token="$(grep '^PROXMOX_VE_API_TOKEN=' ../../.env | cut -d= -f2-)"
    export TF_VAR_immich_login="$(grep '^IMMICH_LOGIN=' ../../.env | cut -d= -f2-)"

The ESO IAM role's policy is scoped to `/neovara/{dev,prod,homeinfra}/*` — it
deliberately excludes `bootstrap`, since ESO never reads these.

## The ESO IAM access key
`terraform apply` also creates the ESO access key (`aws_iam_access_key.eso`).
Its **secret** key IS stored in state — it's a generated output, not a supplied
input, so there's no write-only form. Acceptable because state lives in the
protected S3 backend. Retrieve it for the cluster bootstrap Secret:

    terraform -chdir=terraform/aws output -raw eso_access_key_secret
