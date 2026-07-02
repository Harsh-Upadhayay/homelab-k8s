# ─── k3s workers — application workloads ───
# One resource block expanded over var.workers with for_each. Adding a
# worker = adding an entry to the map (terraform.tfvars) and re-applying:
# the plan shows "1 to add" and touches nothing else. Terraform reconciles
# the whole directory against one state — there is no "apply just this
# file"; the map is what makes workers repeatable.
#
# Each worker also needs a matching Ansible inventory entry under
# [k3s_agent] before site.yml can join it to the cluster.

resource "proxmox_virtual_environment_vm" "k3s_workers" {
  for_each = var.workers

  name        = each.key
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
    cores = each.value.cores
    type  = "x86-64-v2-AES"
  }

  memory {
    dedicated = each.value.memory
  }

  disk {
    datastore_id = var.storage_pool
    interface    = "scsi0"
    size         = each.value.disk_size
  }

  network_device {
    bridge = var.network_bridge
  }

  initialization {
    datastore_id = var.storage_pool

    ip_config {
      ipv4 {
        address = "${each.value.ip}${var.network_cidr_suffix}"
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
