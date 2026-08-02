terraform {
  backend "s3" {
    bucket       = "neovara-terraform-state"
    key          = "aws/terraform.tfstate" # per-stack key, distinct from proxmox/
    region       = "us-east-1"
    use_lockfile = true
    encrypt      = true
  }
}
