# AWS Terraform — secrets at apply time

This stack provisions the AWS-side infra for ESO + SSM: the IAM user/role ESO
assumes, and the SSM parameters holding the Nextcloud DB credential.

## The one secret: `nextcloud_db_password`

Declared as an **ephemeral** variable (`ephemeral = true`), so Terraform never
persists it to state or plan files. Paired with `value_wo` on the
`aws_ssm_parameter`, it flows env → Terraform memory → SSM and touches disk
nowhere.

### Supply it at apply time

The value **must be byte-identical** to the password Nextcloud already uses —
Postgres was initialized with it and it's baked into `config.php` as
`dbpassword`. Retrieve the current value:

    kubectl -n nextcloud get secret nextcloud-db -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d

Then apply with it on the environment:

    TF_VAR_nextcloud_db_password='<value from above>' terraform apply

`terraform plan` works without it (a write-only value isn't diffable anyway);
`apply` requires it.

### Rotation

`value_wo` values aren't diffable, so to rotate: bump `value_wo_version` on the
parameter (1 -> 2), set the new `TF_VAR_nextcloud_db_password`, and apply.

## The ESO IAM access key

`terraform apply` also creates the ESO access key (`aws_iam_access_key.eso`).
Its **secret** key IS stored in state — it's a generated output, not a supplied
input, so there's no write-only form. Acceptable because state lives in the
protected S3 backend. Retrieve it for the Phase 4 cluster bootstrap Secret:

    terraform -chdir=terraform/aws output -raw eso_access_key_secret
