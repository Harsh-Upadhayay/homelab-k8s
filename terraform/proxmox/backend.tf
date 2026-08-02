# Literal only, can't use var substitution as this file is parsed before the config loads. 
terraform {
  backend "s3" {
    bucket = "neovara-terraform-state"
    key = "proxmox/terraform.tfstate"
    region = "us-east-1"
    use_lockfile = true
    encrypt = true
  }
}