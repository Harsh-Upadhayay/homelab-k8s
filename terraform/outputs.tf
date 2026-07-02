output "k3s_server_1_ip" {
  value = var.server_ip
}

# One entry per worker, keyed by VM name — grows automatically as
# entries are added to var.workers.
output "worker_ips" {
  value = { for name, w in var.workers : name => w.ip }
}

output "vm_user" {
  value = var.vm_user
}
