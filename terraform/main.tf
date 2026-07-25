locals {
  common_tags = ["k3s", "terraform-managed"]
}

# ─── k3s-server-1 — control plane, tainted, embedded etcd lives here ───
resource "proxmox_virtual_environment_vm" "k3s_server_1" {
  name        = "k3s-server-1"
  description = "k3s control plane — embedded etcd. Tainted, no app workloads."
  node_name   = var.proxmox_dell_node
  tags        = concat(local.common_tags, ["control-plane"])

  clone {
    vm_id = var.proxmox_dell_template_vm_id
    full  = true
  }

  agent {
    enabled = true
  }
  stop_on_destroy = true

  cpu {
    cores = var.server_cores
    type  = "x86-64-v2-AES"
  }

  memory {
    dedicated = var.server_memory
  }

  disk {
    datastore_id = var.proxmox_dell_storage_pool
    interface    = "scsi0"
    size         = var.server_disk_size

    cache = "writeback"
  }

  network_device {
    bridge = var.network_bridge
  }

  initialization {
    datastore_id = var.proxmox_dell_storage_pool

    ip_config {
      ipv4 {
        address = "${var.server_ip}${var.network_cidr_suffix}"
        gateway = var.network_gateway
      }
    }

    dns {
      servers = var.dns_servers
    }

    user_account {
      username = var.vm_user
      keys     = [var.ssh_public_key]
    }
  }

  operating_system {
    type = "l26"
  }
}

# ─── k3s-worker-1 — application workloads ───
resource "proxmox_virtual_environment_vm" "k3s_worker_1" {
  name        = "k3s-worker-1"
  description = "k3s agent — runs project workloads."
  node_name   = var.proxmox_dell_node
  tags        = concat(local.common_tags, ["worker"])

  clone {
    vm_id = var.proxmox_dell_template_vm_id
    full  = true
  }

  agent {
    enabled = true
  }
  stop_on_destroy = true

  cpu {
    cores = var.worker_cores
    type  = "x86-64-v2-AES"
  }

  memory {
    dedicated = var.worker_memory
  }

  # OS disk — cloned from the template, grown to size
  disk {
    datastore_id = var.proxmox_dell_storage_pool
    interface    = "scsi0"
    size         = var.worker_disk_size
    cache        = "writeback"
  }

  # Dedicated data disk — reserved for distributed storage (Longhorn later).
  # Deliberately NOT part of the template clone: created empty, formatted and
  # mounted by the k3s_agent Ansible role. Thin-provisioned, so it consumes
  # real space only as written.
  disk {
    datastore_id = var.proxmox_dell_storage_pool
    interface    = "scsi1"
    size         = var.worker_data_disk_size
    file_format  = "raw"
    cache        = "writeback"
  }

  network_device {
    bridge = var.network_bridge
  }

  initialization {
    datastore_id = var.proxmox_dell_storage_pool

    ip_config {
      ipv4 {
        address = "${var.worker_ip}${var.network_cidr_suffix}"
        gateway = var.network_gateway
      }
    }

    dns {
      servers = var.dns_servers
    }

    user_account {
      username = var.vm_user
      keys     = [var.ssh_public_key]
    }
  }

  operating_system {
    type = "l26"
  }
}

# ─── k3s-worker-3 — ASRock managed-HDD worker ───
resource "proxmox_virtual_environment_vm" "k3s_worker_3" {
  name        = "k3s-worker-3"
  description = "k3s agent on pve-asrock — uses the dedicated Proxmox-managed Longhorn HDD datastore."
  node_name   = var.proxmox_asrock_node
  tags        = concat(local.common_tags, ["worker"])

  clone {
    vm_id        = var.proxmox_dell_template_vm_id
    node_name    = var.proxmox_dell_node
    datastore_id = var.proxmox_asrock_storage_pool
    full         = true
  }

  agent {
    enabled = true
  }
  stop_on_destroy = true

  cpu {
    cores = var.worker3_cores
    type  = "x86-64-v2-AES"
  }

  memory {
    dedicated = var.worker3_memory
  }

  disk {
    datastore_id = var.proxmox_asrock_storage_pool
    interface    = "scsi0"
    size         = var.worker3_disk_size
  }

  # Dedicated Longhorn data disk allocated from the separate physical-HDD
  # datastore. Proxmox owns the block allocation; Ansible owns the guest ext4
  # filesystem and mount, matching worker-1's lifecycle.
  disk {
    datastore_id = var.proxmox_asrock_longhorn_storage_pool
    interface    = "scsi1"
    size         = var.worker3_data_disk_size
    file_format  = "raw"
    cache        = "none"
    backup       = false
    replicate    = false
  }

  network_device {
    bridge = var.network_bridge
  }

  initialization {
    datastore_id = var.proxmox_asrock_storage_pool

    ip_config {
      ipv4 {
        address = "${var.worker3_ip}${var.network_cidr_suffix}"
        gateway = var.network_gateway
      }
    }

    dns {
      servers = var.dns_servers
    }

    user_account {
      username = var.vm_user
      keys     = [var.ssh_public_key]
    }
  }

  operating_system {
    type = "l26"
  }
}
