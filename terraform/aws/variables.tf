variable "region" {
  type    = string
  default = "us-east-1"
}

# --- Nextcloud DB credential (the one real secret migrated to SSM) ---
# Secret value is ephemeral: it never persists to state, supplied at apply via
# TF_VAR_nextcloud_db_password (see SECRETS.md). Paired with value_wo on the
# aws_ssm_parameter.
variable "nextcloud_db_password" {
  type      = string
  sensitive = true
  ephemeral = true
}

# Non-secret; safe to commit in tfvars.
variable "nextcloud_db_username" {
  type = string
}

# --- cert-manager Cloudflare API token (DNS-01 solver for both ClusterIssuers) ---
variable "cert_manager_cloudflare_api_token" {
  type      = string
  sensitive = true
  ephemeral = true
}

# --- cloudflared tunnel token (connector auth to Cloudflare's edge) ---
variable "cloudflared_tunnel_token" {
  type      = string
  sensitive = true
  ephemeral = true
}

# --- Immich DB (same shape as nextcloud-db: 1 secret + 2 non-secret keys) ---
variable "immich_db_password" {
  type      = string
  sensitive = true
  ephemeral = true
}
variable "immich_db_username" {
  type = string
}
variable "immich_db_database_name" {
  type = string
}

# --- Grafana admin (chart reads via existingSecret) ---
variable "grafana_admin_password" {
  type      = string
  sensitive = true
  ephemeral = true
}
variable "grafana_admin_user" {
  type = string
}

# --- Tailscale operator OAuth client (client_id is non-secret, client_secret is) ---
variable "tailscale_client_secret" {
  type      = string
  sensitive = true
  ephemeral = true
}
variable "tailscale_client_id" {
  type = string
}
