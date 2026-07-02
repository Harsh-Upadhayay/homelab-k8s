provider "proxmox" {
  endpoint  = var.proxmox_endpoint
  api_token = var.proxmox_api_token

  # Proxmox uses a self-signed cert by default in a homelab — fine to accept it here.
  insecure = true

  # Needed for operations the Proxmox API doesn't cover directly (e.g. file uploads).
  ssh {
    agent    = true
    username = "root"
  }
}
