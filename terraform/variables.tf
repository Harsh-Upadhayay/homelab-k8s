variable "proxmox_cluster_endpoint" {
  description = "API endpoint for one healthy member of the Proxmox cluster. Prefer a Tailscale MagicDNS name so applies work off-LAN; provider.tf's insecure=true covers the certificate mismatch."
  type        = string
}

# The Proxmox API token is NOT a Terraform variable — it's the one secret, so
# the provider reads it straight from PROXMOX_VE_API_TOKEN at runtime (see
# provider.tf). Create the token with pveum (GUIDE.md Phase 3).

variable "server_node_name" {
  description = "Proxmox node that hosts the k3s control-plane VM"
  type        = string
  default     = "pve-dell"
}

variable "template_vm_id" {
  description = "Cluster-wide VM ID of the cloud-init template used by all k3s VMs"
  type        = number
  default     = 9000
}

variable "server_storage_pool" {
  description = "Node-local datastore for the control-plane OS and cloud-init disks"
  type        = string
  default     = "local-lvm"
}

variable "network_bridge" {
  description = "Proxmox network bridge"
  type        = string
  default     = "vmbr0"
}

variable "network_gateway" {
  description = "LAN gateway IP"
  type        = string
  default     = "192.168.1.1"
}

variable "network_cidr_suffix" {
  description = "CIDR suffix for the LAN"
  type        = string
  default     = "/24"
}

variable "dns_servers" {
  description = "DNS servers for the VMs"
  type        = list(string)
  default     = ["1.1.1.1", "1.0.0.1"]
}

variable "ssh_public_key" {
  description = "Public key injected via cloud-init for the admin user on every VM"
  type        = string
}

variable "vm_user" {
  description = "Admin username created on each VM via cloud-init"
  type        = string
  default     = "harsh"
}

# Current pve-dell sizing: 16 vCPU on 14 threads is a deliberate mild CPU
# overcommit. RAM remains 24GiB total (server 6 + worker 18), preserving host
# headroom; the declared VM disks leave thin-pool safety headroom.

# --- k3s-server-1 ---
variable "server_ip" {
  description = "Static IP for k3s-server-1 (control plane)"
  type        = string
  default     = "192.168.1.21"
}

variable "server_cores" {
  type    = number
  default = 4
}

variable "server_memory" {
  description = "MB — tainted control plane, no app workloads; etcd wants disk latency, not RAM"
  type        = number
  default     = 6144
}

variable "server_disk_size" {
  description = "GB — etcd lives here; keep it on the fastest pool the ADR-0022 constraint allows"
  type        = number
  default     = 60
}

variable "workers" {
  description = "Worker VM parameters; every entry follows the same VM lifecycle and differs only by these values"
  type = map(object({
    node_name           = string
    clone_node_name     = optional(string)
    clone_datastore_id  = optional(string)
    ip_address          = string
    cores               = number
    memory              = number
    os_datastore_id     = string
    os_disk_size        = number
    os_disk_cache       = string
    data_datastore_id   = string
    data_disk_size      = number
    data_disk_cache     = string
    data_disk_backup    = bool
    data_disk_replicate = bool
  }))

  validation {
    condition = alltrue([
      for name, worker in var.workers :
      startswith(name, "k3s-worker-") &&
      worker.node_name != "" &&
      worker.ip_address != "" &&
      worker.os_datastore_id != "" &&
      worker.data_datastore_id != ""
    ])
    error_message = "Each worker key must start with k3s-worker- and declare its node, IP, OS datastore, and data datastore."
  }
}
