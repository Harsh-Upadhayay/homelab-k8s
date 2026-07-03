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
  description = "Proxmox storage pool for VM disks. On pve-dell this is the thin pool on the external USB SSD — the internal NVMe is strictly off-limits (ADR-0022)."
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

# Sizing rationale (pve-dell: 14 threads / 30GiB usable RAM / 816GiB thin pool):
# CPU is mildly overcommitted (16 vCPU on 14 threads) — vCPUs are schedulable
# threads, not reservations, and k8s load is bursty. RAM is deliberately NOT
# overcommitted: 6+9+9=24GiB leaves ~5GiB for the host, because a host OOM
# kill against a VM is how etcd dies. Disks are thin-provisioned — blocks are
# consumed on write, so 680GB provisioned of 816GB costs nothing up front.

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

# --- workers (both sized identically) ---
variable "worker_ip" {
  description = "Static IP for k3s-worker-1"
  type        = string
  default     = "192.168.1.22"
}

variable "worker2_ip" {
  description = "Static IP for k3s-worker-2"
  type        = string
  default     = "192.168.1.23"
}

variable "worker_cores" {
  type    = number
  default = 6
}

variable "worker_memory" {
  description = "MB"
  type        = number
  default     = 9216
}

variable "worker_disk_size" {
  description = "GB — OS disk"
  type        = number
  default     = 60
}

variable "worker_data_disk_size" {
  description = "GB — dedicated data disk (scsi1) per worker, reserved for distributed storage (Longhorn later). Kept separate from the OS disk so storage I/O and OS I/O don't mix, and so future physical nodes arrive with the same symmetric layout. Thin-provisioned: sized to keep total declared ≈91% of the pool, leaving the crumple zone that stops the pool silently filling under its guests."
  type        = number
  default     = 280
}
