# Nextcloud DB credential in SSM Parameter Store. ESO (Phase 4) reads these and
# materializes them as the `nextcloud-db` Kubernetes Secret in the cluster.
# Path convention: /neovara/<tier>/<app>/<key>  (tier: dev | prod | homeinfra)

# The DB password — SecureString, encrypted with the default alias/aws/ssm key
# (no key_id set => AWS-managed key, per Decision D). value_wo keeps the value
# out of Terraform state; bump value_wo_version to force a rewrite on rotation.
resource "aws_ssm_parameter" "nextcloud_db_password" {
  name             = "/neovara/homeinfra/nextcloud/db-password"
  type             = "SecureString"
  value_wo         = var.nextcloud_db_password
  value_wo_version = 2 # bumped 1 -> 2 to rewrite with the live cluster value (v1 was seeded wrong)
}

# The DB username — non-secret, stored in plaintext (insecure_value) and safe to
# surface in plans/state.
resource "aws_ssm_parameter" "nextcloud_db_username" {
  name           = "/neovara/homeinfra/nextcloud/db-username"
  type           = "String"
  insecure_value = var.nextcloud_db_username
}

# ---------------------------------------------------------------------------
# Remaining imperative Secrets migrated to ESO+SSM. Same pattern as above:
# SecureString + value_wo for secrets (never in state), String + insecure_value
# for non-secrets. One SSM parameter per Secret key.
# ---------------------------------------------------------------------------

# cert-manager Cloudflare API token — consumed by both ClusterIssuers (DNS-01).
resource "aws_ssm_parameter" "cert_manager_cloudflare_api_token" {
  name             = "/neovara/homeinfra/cert-manager/cloudflare-api-token"
  type             = "SecureString"
  value_wo         = var.cert_manager_cloudflare_api_token
  value_wo_version = 1
}

# cloudflared tunnel token — the connector's auth to Cloudflare's edge.
resource "aws_ssm_parameter" "cloudflared_tunnel_token" {
  name             = "/neovara/homeinfra/cloudflared/tunnel-token"
  type             = "SecureString"
  value_wo         = var.cloudflared_tunnel_token
  value_wo_version = 1
}

# Immich DB credentials.
resource "aws_ssm_parameter" "immich_db_password" {
  name             = "/neovara/homeinfra/immich/db-password"
  type             = "SecureString"
  value_wo         = var.immich_db_password
  value_wo_version = 1
}
resource "aws_ssm_parameter" "immich_db_username" {
  name           = "/neovara/homeinfra/immich/db-username"
  type           = "String"
  insecure_value = var.immich_db_username
}
resource "aws_ssm_parameter" "immich_db_database_name" {
  name           = "/neovara/homeinfra/immich/db-database-name"
  type           = "String"
  insecure_value = var.immich_db_database_name
}

# Grafana admin credentials.
resource "aws_ssm_parameter" "grafana_admin_password" {
  name             = "/neovara/homeinfra/grafana/admin-password"
  type             = "SecureString"
  value_wo         = var.grafana_admin_password
  value_wo_version = 1
}
resource "aws_ssm_parameter" "grafana_admin_user" {
  name           = "/neovara/homeinfra/grafana/admin-user"
  type           = "String"
  insecure_value = var.grafana_admin_user
}

# Tailscale operator OAuth client.
resource "aws_ssm_parameter" "tailscale_client_secret" {
  name             = "/neovara/homeinfra/tailscale/client-secret"
  type             = "SecureString"
  value_wo         = var.tailscale_client_secret
  value_wo_version = 1
}
resource "aws_ssm_parameter" "tailscale_client_id" {
  name           = "/neovara/homeinfra/tailscale/client-id"
  type           = "String"
  insecure_value = var.tailscale_client_id
}

# Workbench devbox Tailscale auth key — bootstrap/recovery only. The devbox reads
# this Secret via the Kubernetes API (not a pod mount) for the one-time
# `tailscale up` after a state wipe; normal restarts resume from PVC state.
resource "aws_ssm_parameter" "workbench_tailscale_authkey" {
  name             = "/neovara/homeinfra/workbench/tailscale-authkey"
  type             = "SecureString"
  value_wo         = var.workbench_tailscale_authkey
  value_wo_version = 1
}

# ---------------------------------------------------------------------------
# Bootstrap secrets (tier: bootstrap). Host-side values consumed by
# Ansible/Terraform on the machines, not by pods. NOT referenced by any
# ExternalSecret — these exist in SSM only, as the durable source of truth
# replacing the local .env file. Read directly via `aws ssm get-parameter`.
# ---------------------------------------------------------------------------

# k3s cluster join token — consumed by the k3s_server/k3s_worker Ansible roles
# at node install time. Rotating it requires re-running the k3s roles.
resource "aws_ssm_parameter" "k3s_token" {
  name             = "/neovara/bootstrap/k3s-token"
  type             = "SecureString"
  value_wo         = var.k3s_token
  value_wo_version = 1
}

# Proxmox VE API token — consumed by the Terraform Proxmox provider to provision
# and manage the k3s VMs.
resource "aws_ssm_parameter" "proxmox_ve_api_token" {
  name             = "/neovara/bootstrap/proxmox-ve-api-token"
  type             = "SecureString"
  value_wo         = var.proxmox_ve_api_token
  value_wo_version = 1
}

# Immich OAuth/login secret — unreferenced orphan today (nothing consumes it in
# k8s/ansible/terraform), but preserved in SSM so it isn't lost when .env is
# retired. Wire consumption before relying on it.
resource "aws_ssm_parameter" "immich_login" {
  name             = "/neovara/bootstrap/immich-login"
  type             = "SecureString"
  value_wo         = var.immich_login
  value_wo_version = 1
}
