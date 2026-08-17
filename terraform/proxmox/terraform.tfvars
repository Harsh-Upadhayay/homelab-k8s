# Committed on purpose — this is the source of truth for THIS host, and holds
# NO secrets: the one secret (the Proxmox API token) is supplied at runtime via
# the PROXMOX_VE_API_TOKEN env var (see provider.tf), never here. Keep it that
# way — do not paste the token or any other credential into this file.
# Every node spec is passed explicitly here (defaults in variables.tf are
# fallback documentation).
#
# Sizing ledger — rewritten after the 2026-08-17 control-plane move (issue #49).
#
# pve-dell (14 threads / 31543MB / 816GiB thin pool) — now a worker-only host:
#   CPU  14 vCPU on 14 threads. No overcommit, and strictly less contention than
#        the previous 4+12=16, because the server VM left.
#   RAM  28672MB of 31543, leaving ~2.8GiB for the host. Measured host process
#        usage is ~2.4GiB, and this host keeps 8GiB of swap as a backstop.
#        The old ~5GiB reserve here existed because an OOM kill against the
#        *control plane* would have taken etcd and the whole cluster with it.
#        That VM now lives on ASRock, so the worst case here is a recoverable
#        worker OOM, and the headroom was deliberately spent on the worker.
#   DISK 60 worker OS + 650 worker data = 710G declared of the 816G pool.
#
# pve-asrock (motherboard SSD, 74.68GiB thin pool after the #49 extension;
#             separate 1.36TiB longhorn-hdd pool; 32027MB RAM):
#   CPU  4 server + 6 worker = 10 vCPU.
#   RAM  4096 server + 24576 worker = 28672MB of 32027, ~3.3GiB host reserve.
#        RAM is never overcommitted here: this host now runs the control plane,
#        so ADR-0020's original reasoning applies to *this* box instead. worker-3
#        was cut 28672->24576 to make room rather than shrinking the server VM,
#        which has the least slack and the worst failure mode.
#   DISK 60 server OS + 40 worker OS on the SSD pool; 1300G worker data on HDD.

proxmox_cluster_endpoint = "https://pve-dell.egret-pence.ts.net:8006/" # One healthy cluster member; MagicDNS keeps applies available off-LAN.
template_vm_id           = 9000
server_node_name         = "pve-asrock"
server_storage_pool      = "local-lvm"
network_bridge           = "vmbr0"

network_gateway     = "192.168.1.1"
network_cidr_suffix = "/24"
dns_servers         = ["1.1.1.1", "1.0.0.1"]

ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDovjTxah54f00yLsSXLlBZbZCavskkPi+gkoLP70Tjd homelab-admin"
vm_user        = "harsh"

# --- k3s-server-1 (control plane — tainted, etcd lives here) ---
server_ip        = "192.168.1.21"
server_cores     = 4
server_memory    = 4096
server_disk_size = 60

# Every worker uses the same Terraform resource. Host placement, capacity, and
# storage policy are parameters rather than separate implementations.
workers = {
  k3s-worker-1 = {
    node_name           = "pve-dell"
    clone_node_name     = null
    clone_datastore_id  = null
    ip_address          = "192.168.1.22"
    cores               = 14
    memory              = 28672
    os_datastore_id     = "local-lvm"
    os_disk_size        = 60
    os_disk_cache       = "writeback"
    data_datastore_id   = "local-lvm"
    data_disk_size      = 650
    data_disk_cache     = "writeback"
    data_disk_backup    = true
    data_disk_replicate = true
  }

  k3s-worker-3 = {
    node_name           = "pve-asrock"
    clone_node_name     = "pve-dell"
    clone_datastore_id  = "local-lvm"
    ip_address          = "192.168.1.24"
    cores               = 6
    memory              = 24576
    os_datastore_id     = "local-lvm"
    os_disk_size        = 40
    os_disk_cache       = "none"
    data_datastore_id   = "longhorn-hdd"
    data_disk_size      = 1300
    data_disk_cache     = "none"
    data_disk_backup    = false
    data_disk_replicate = false
  }
}
