# Committed on purpose — this is the source of truth for THIS host, and holds
# NO secrets: the one secret (the Proxmox API token) is supplied at runtime via
# the PROXMOX_VE_API_TOKEN env var (see provider.tf), never here. Keep it that
# way — do not paste the token or any other credential into this file.
# Every node spec is passed explicitly here (defaults in variables.tf are
# fallback documentation).
#
# Sizing ledger for pve-dell (14 threads / 30GiB usable / 816GiB thin pool):
#   CPU  4+12 = 16 vCPU on 14 threads — the former worker-2 allocation moves
#        to worker-1; total CPU overcommit is unchanged.
#   RAM  6+18 = 24GiB of 30 — host headroom remains unchanged.
#   DISK 60 server + 60 worker OS + 650 worker data = 770G declared. This
#        replaces worker-2's 60G OS + 280G data disks and leaves ~46G of the
#        thin pool undeclared as the safety margin.

proxmox_cluster_endpoint = "https://pve-dell.egret-pence.ts.net:8006/" # One healthy cluster member; MagicDNS keeps applies available off-LAN.
proxmox_dell_node        = "pve-dell"

proxmox_dell_template_vm_id          = 9000
proxmox_dell_storage_pool            = "local-lvm"
proxmox_asrock_storage_pool          = "local-lvm"
proxmox_asrock_longhorn_storage_pool = "longhorn-hdd"
network_bridge                       = "vmbr0"

network_gateway     = "192.168.1.1"
network_cidr_suffix = "/24"
dns_servers         = ["1.1.1.1", "1.0.0.1"]

ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDovjTxah54f00yLsSXLlBZbZCavskkPi+gkoLP70Tjd homelab-admin"
vm_user        = "harsh"

# --- k3s-server-1 (control plane — tainted, etcd lives here) ---
server_ip        = "192.168.1.21"
server_cores     = 4
server_memory    = 6144
server_disk_size = 60

# --- consolidated Dell worker ---
worker_ip             = "192.168.1.22"
worker_cores          = 12
worker_memory         = 18432
worker_disk_size      = 60
worker_data_disk_size = 650

# --- k3s-worker-3 (ASRock managed-HDD worker) ---
proxmox_asrock_node    = "pve-asrock"
worker3_ip             = "192.168.1.24"
worker3_cores          = 6
worker3_memory         = 12288
worker3_disk_size      = 40
worker3_data_disk_size = 1300
