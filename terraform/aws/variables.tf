variable "region" {
  type    = string
  default = "us-east-1"
}

# --- Nextcloud DB credential ---
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

# --- Workbench devbox Tailscale auth key (one-time bootstrap/recovery key) ---
# The devbox authenticates via persisted state on its PVC (ADR-0064), NOT via a
# mounted Secret. This key is only read via the Kubernetes API from inside the
# devbox (namespace-admin RBAC, ADR-0065) for the initial `tailscale up` or a
# state-wipe recovery — see docs/Workbench Runbook.md.
variable "workbench_tailscale_authkey" {
  type      = string
  sensitive = true
  ephemeral = true
}

# --- Bootstrap secrets (tier: bootstrap) ---
# Host-side values consumed by Ansible/Terraform ON THE MACHINES, not by pods.
# Stored in SSM as the durable source of truth so the local .env file is no
# longer the only copy. Deliberately NOT wired to any ExternalSecret — read
# directly from SSM by whichever tooling needs them. All three are secrets.
variable "k3s_token" {
  type      = string
  sensitive = true
  ephemeral = true
}
variable "proxmox_ve_api_token" {
  type      = string
  sensitive = true
  ephemeral = true
}
variable "immich_login" {
  type      = string
  sensitive = true
  ephemeral = true
}
