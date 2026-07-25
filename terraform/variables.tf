variable "proxmox_cluster_endpoint" {
  description = "API endpoint for one healthy member of the Proxmox cluster. Prefer a Tailscale MagicDNS name so applies work off-LAN; provider.tf's insecure=true covers the certificate mismatch."
  type        = string
}

# The Proxmox API token is NOT a Terraform variable — it's the one secret, so
# the provider reads it straight from PROXMOX_VE_API_TOKEN at runtime (see
# provider.tf). Create the token with pveum (GUIDE.md Phase 3).

variable "proxmox_dell_node" {
  description = "Proxmox node that hosts k3s-server-1, the consolidated k3s-worker-1, and the source template"
  type        = string
  default     = "pve-dell"
}

variable "proxmox_asrock_node" {
  description = "Proxmox node that hosts k3s-worker-3 and its dedicated managed Longhorn HDD datastore"
  type        = string
  default     = "pve-asrock"
}

variable "proxmox_dell_template_vm_id" {
  description = "Cluster-wide VM ID of the cloud-init template stored on pve-dell"
  type        = number
  default     = 9000
}

variable "proxmox_dell_storage_pool" {
  description = "Node-local pve-dell storage pool for existing VM disks; the internal NVMe remains strictly off-limits (ADR-0022)"
  type        = string
  default     = "local-lvm"
}

variable "proxmox_asrock_storage_pool" {
  description = "Node-local pve-asrock storage pool for the k3s-worker-3 OS and cloud-init disks"
  type        = string
  default     = "local-lvm"
}

variable "proxmox_asrock_longhorn_storage_pool" {
  description = "Node-local pve-asrock LVM-thin datastore backed only by the dedicated physical Longhorn HDD"
  type        = string
  default     = "longhorn-hdd"
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
# After retiring worker-2, worker-1 receives its compute allocation. CPU stays
# mildly overcommitted at 16 vCPU (server 4 + worker 12) on 14 threads. RAM
# remains 24GiB total (server 6 + worker 18), preserving host headroom. The
# enlarged worker-1 Longhorn disk replaces both former 280GB worker disks.

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

# --- consolidated Dell worker ---
variable "worker_ip" {
  description = "Static IP for k3s-worker-1"
  type        = string
  default     = "192.168.1.22"
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
  description = "GB — dedicated Longhorn data disk (scsi1) for the consolidated Dell worker, separate from its OS disk and thin-provisioned with host-pool headroom"
  type        = number
  default     = 280
}

# --- k3s-worker-3 (ASRock managed-HDD worker) ---

variable "worker3_ip" {
  description = "Static LAN IP for k3s-worker-3"
  type        = string
  default     = "192.168.1.24"
}

variable "worker3_cores" {
  description = "vCPU allocated to the ASRock worker independently of the consolidated Dell worker"
  type        = number
  default     = 6
}

variable "worker3_memory" {
  description = "MB allocated to k3s-worker-3 on pve-asrock"
  type        = number
  default     = 12288
}

variable "worker3_disk_size" {
  description = "GB allocated to the k3s-worker-3 OS disk on pve-asrock local-lvm"
  type        = number
  default     = 40
}

variable "worker3_data_disk_size" {
  description = "GB allocated to worker-3 from the dedicated ASRock Longhorn HDD datastore, leaving hypervisor and thin-pool headroom"
  type        = number
  default     = 1300
}
