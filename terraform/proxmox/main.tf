locals {
  common_tags = ["k3s", "terraform-managed"]
}

# ─── k3s-server-1 — control plane, tainted, embedded etcd lives here ───
resource "proxmox_virtual_environment_vm" "k3s_server_1" {
  name        = "k3s-server-1"
  description = "k3s control plane — embedded etcd. Tainted, no app workloads."
  node_name   = var.server_node_name
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
    datastore_id = var.server_storage_pool
    interface    = "scsi0"
    size         = var.server_disk_size

    cache = "writeback"
    # Thin-provisioned control-plane disk: let guest TRIM release freed blocks
    # back to the LVM-thin pool. Without this the host-side allocation only ever
    # grows, which is what inflated this disk to ~38GiB against ~9GiB of real data.
    discard = "on"
  }

  network_device {
    bridge = var.network_bridge
  }

  initialization {
    datastore_id = var.server_storage_pool

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

# ─── k3s workers — one lifecycle, host/storage differences are data ───
resource "proxmox_virtual_environment_vm" "k3s_worker" {
  for_each = var.workers

  name        = each.key
  description = "k3s agent — runs project workloads."
  node_name   = each.value.node_name
  tags        = concat(local.common_tags, ["worker"])

  clone {
    vm_id        = var.template_vm_id
    node_name    = each.value.clone_node_name
    datastore_id = each.value.clone_datastore_id
    full         = true
  }

  agent {
    enabled = true
  }
  stop_on_destroy = true

  cpu {
    cores = each.value.cores
    type  = "x86-64-v2-AES"
  }

  memory {
    dedicated = each.value.memory
  }

  # The template supplies scsi0; every worker grows it to its declared size.
  disk {
    datastore_id = each.value.os_datastore_id
    interface    = "scsi0"
    size         = each.value.os_disk_size
    cache        = each.value.os_disk_cache
  }

  # Proxmox owns the empty scsi1 allocation. The shared longhorn_node Ansible
  # role owns its guest filesystem and /var/lib/longhorn mount.
  disk {
    datastore_id = each.value.data_datastore_id
    interface    = "scsi1"
    size         = each.value.data_disk_size
    file_format  = "raw"
    cache        = each.value.data_disk_cache
    backup       = each.value.data_disk_backup
    replicate    = each.value.data_disk_replicate
  }

  network_device {
    bridge = var.network_bridge
  }

  initialization {
    datastore_id = each.value.os_datastore_id

    ip_config {
      ipv4 {
        address = "${each.value.ip_address}${var.network_cidr_suffix}"
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
