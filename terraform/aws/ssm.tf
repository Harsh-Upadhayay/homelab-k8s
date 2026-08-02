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
