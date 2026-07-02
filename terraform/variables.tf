variable "proxmox_endpoint" {
  description = "Proxmox API endpoint, e.g. https://192.168.1.10:8006/"
  type        = string
}

variable "proxmox_api_token" {
  description = "Proxmox API token in the form user@realm!tokenid=secret. Create with pveum (see GUIDE.md Phase 3)."
  type        = string
  sensitive   = true
}

variable "proxmox_node" {
  description = "Name of the target Proxmox node (hostname shown in the web UI, often 'pve')"
  type        = string
  default     = "pve"
}

variable "template_vm_id" {
  description = "VM ID of the cloud-init template built in Phase 2"
  type        = number
  default     = 9000
}

variable "storage_pool" {
  description = "Proxmox storage pool for VM disks — MUST be backed by NVMe for k3s-server-1"
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
  description = "MB"
  type        = number
  default     = 8192
}

variable "server_disk_size" {
  description = "GB — must land on the NVMe-backed storage pool"
  type        = number
  default     = 60
}

# --- workers ---
# Map key = VM name (and Terraform state key). Adding a worker means adding
# an entry here (or overriding the whole map in terraform.tfvars) and
# re-applying — existing entries are untouched. Remember the matching
# Ansible inventory entry under [k3s_agent].
variable "workers" {
  description = "k3s worker VMs, keyed by name"
  type = map(object({
    ip        = string # static LAN IP (CIDR suffix appended from network_cidr_suffix)
    cores     = number
    memory    = number # MB
    disk_size = number # GB
  }))
  default = {
    "k3s-worker-1" = {
      ip        = "192.168.1.22"
      cores     = 4
      memory    = 16384
      disk_size = 150
    }
  }
}
