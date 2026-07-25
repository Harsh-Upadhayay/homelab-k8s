# Terraform Concepts

> Back to [[Homelab Learning Map]]

Phase 3 originally created `k3s-server-1`, `k3s-worker-1`, and `k3s-worker-2` on `pve-dell`.
ADR-0049 later brought `pve-asrock` into the same Proxmox cluster and Terraform added
`k3s-worker-3` there, initially with whole-HDD passthrough for recovery. Issue #48 retired `k3s-worker-2`, consolidated
its Dell allocation into `k3s-worker-1`. The current three resource blocks therefore model one
server and one worker on Dell plus one worker on ASRock, while one provider/token manages both
cluster members and each resource's `node_name` expresses physical placement.

**A provider is the API adapter; a resource is desired remote state.** `bpg/proxmox` translates Terraform's resource model into Proxmox API operations. Each `proxmox_virtual_environment_vm` block declares a VM that should exist with a specific clone source, CPU/RAM allocation, disks, NIC, cloud-init identity, and IP. Terraform owns that infrastructure shape; it does not configure Ubuntu or install k3s — that begins after SSH works and belongs to Ansible (ADR-0001).

**One clustered API does not mean shared storage or an HA endpoint.** A provider connected to one healthy Proxmox member can address resources on every cluster member, with `node_name` choosing placement, and the `terraform@pve` identity/ACLs replicated through `pmxcfs`. `local-lvm` still refers to node-local media, and a physical HDD remains pinned to its host. The configured endpoint is also still one hostname: before retiring that member, change `proxmox_endpoint` to a survivor and require a zero-destroy refresh/plan. During the accepted two-node interval, loss of either member makes cluster configuration read-only, so applies wait until quorum returns.

**A full clone creates independent VM storage from one reusable template.** Each resource's `clone { vm_id = var.template_vm_id; full = true }` starts from the manually prepared Ubuntu cloud-init template. Cloud-init injects the per-VM hostname/IP/SSH key on first boot, while the workers' second raw disks are deliberately blank so Ansible can format and mount them as Longhorn storage. Template construction is a one-time Proxmox bootstrap; cloning and sizing are repeatable code.

**Variables separate the reusable shape from this lab's concrete inputs.** `variables.tf` declares the contract and defaults, while `terraform.tfvars` supplies this environment's endpoint, node, storage, addresses, and sizing. The Proxmox API token is not a normal variable in that committed file: it is supplied at runtime through `PROXMOX_VE_API_TOKEN`, preserving the repository's no-secrets-in-Git rule.

**State is Terraform's binding between configuration and real objects.** The local state records that a particular resource address corresponds to a particular Proxmox VM ID. `plan` compares configuration, state, and the provider's live read to propose a diff; `apply` executes the reviewed diff and updates state. Losing or duplicating state does not automatically delete VMs, but it destroys Terraform's ownership map and can lead to attempted duplicate creation or manual imports. Treat state as sensitive operational data and preserve it securely; do not infer recoverability from Git alone.

**`plan` is the review boundary, not a promise that apply cannot fail.** The first real apply exposed two permissions absent from the original token design: allocating VM disks required `Datastore.AllocateSpace`, and attaching the NIC to `vmbr0` required `SDN.Use`. Those narrowly scoped ACLs are now automated by the Proxmox Ansible role (ADR-0023/0024). Provider-side authorization and concurrent live changes can still make an approved plan fail at apply time.

**Explicit worker resources trade repetition for clarity at the current scale.** A `for_each`
worker map was tried and rejected in ADR-0019 because a small number of concrete workers was easier
to read while the layout was still being learned. The two current workers deliberately differ:
worker 1 has a virtual Dell data disk while worker 3's data disk comes from the separate ASRock
`longhorn-hdd` datastore. Each still has a concrete Terraform resource and matching Ansible
inventory entry; joining a runtime node by hand would bypass the standards this repository exists
to enforce.
