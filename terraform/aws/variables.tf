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
