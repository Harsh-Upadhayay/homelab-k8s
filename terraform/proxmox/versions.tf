terraform {
  required_version = ">= 1.11" # bumped for S3 backend use_lockfile (Phase 2)
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.111"
    }
  }
}
