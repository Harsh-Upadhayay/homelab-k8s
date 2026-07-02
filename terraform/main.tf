locals {
  common_tags = ["k3s", "terraform-managed"]
}

# ─── k3s-server-1 — control plane, tainted, embedded etcd lives here ───
resource "proxmox_virtual_environment_vm" "k3s_server_1" {
  name        = "k3s-server-1"
  description = "k3s control plane — embedded etcd. Tainted, no app workloads."
  node_name   = var.proxmox_node
  tags        = concat(local.common_tags, ["control-plane"])

  clone {
    vm_id = var.template_vm_id
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
    datastore_id = var.storage_pool
    interface    = "scsi0"
    size         = var.server_disk_size
  }

  network_device {
    bridge = var.network_bridge
  }

  initialization {
    datastore_id = var.storage_pool

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
  node_name   = var.proxmox_node
  tags        = concat(local.common_tags, ["worker"])

  clone {
    vm_id = var.template_vm_id
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

  disk {
    datastore_id = var.storage_pool
    interface    = "scsi0"
    size         = var.worker_disk_size
  }

  network_device {
    bridge = var.network_bridge
  }

  initialization {
    datastore_id = var.storage_pool

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
