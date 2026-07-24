provider "proxmox" {
  endpoint = var.proxmox_cluster_endpoint

  # api_token is intentionally NOT set here — the provider reads it from the
  # PROXMOX_VE_API_TOKEN environment variable instead, so the one secret never
  # lands in a committed file. Everything else (endpoint, node, IPs, specs) is
  # non-secret and lives in the now-committed terraform.tfvars. Same
  # runtime-secret pattern as Ansible's K3S_TOKEN / tailscale_auth_key (ADR-0003).
  #   export PROXMOX_VE_API_TOKEN='terraform@pve!tf=<secret>'

  # Proxmox uses a self-signed cert by default in a homelab — fine to accept it here.
  insecure = true

  # Needed for operations the Proxmox API doesn't cover directly (e.g. file uploads, disk resizes).
  ssh {
    agent    = true
    username = "root"
  }
}
