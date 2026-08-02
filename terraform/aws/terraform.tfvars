# Nextcloud non-secret SSM parameter value. The secret (db_password) is supplied
# at apply time via TF_VAR_nextcloud_db_password — never here.
nextcloud_db_username = "nextcloud"

# Non-secret values for the migrated Secrets. Each app's actual secret values
# (passwords/tokens) are supplied at apply via TF_VAR_* env vars — never here.
immich_db_username      = "postgres"
immich_db_database_name = "immich"
grafana_admin_user      = "admin"
tailscale_client_id     = "kC2f3x7i8721CNTRL"
