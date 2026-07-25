output "k3s_server_1_ip" {
  value = var.server_ip
}

output "k3s_worker_ips" {
  value = {
    for name, worker in var.workers : name => worker.ip_address
  }
}

output "vm_user" {
  value = var.vm_user
}
