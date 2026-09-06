# NVIDIA GPU passthrough into the k3s cluster — design

**Status:** Implemented on branch `feat/nvidia-gpu-passthrough`, pending review/merge.
**Date:** 2026-09-06

## Goal

Make the GeForce GTX 1660 SUPER physically installed in `pve-asrock` available to
Kubernetes as a schedulable `nvidia.com/gpu` resource on `k3s-worker-3`.

The scope ends at "a pod that requests a GPU gets one and `nvidia-smi` works
inside it." Wiring any *consumer* of the GPU — Immich hardware transcoding,
ollama, ML workloads — is deliberately separate follow-on work.

This closes a deferral the repo has carried since the bare-metal migration:
`docs/Migration Plan.md` records "GPU workloads deferred … this cluster is
Proxmox VMs with no GPU passthrough (yet). Anything that *requires* the GPU stays
off-cluster."

## Verified hardware facts

Everything below was confirmed live before designing, not assumed:

| Fact | Value | Why it matters |
|---|---|---|
| GPU | `01:00.0` NVIDIA TU116 [GTX 1660 SUPER] `10de:21c4` | Turing (SM 7.5) — still supported by current drivers |
| Host | `pve-asrock` (also runs `k3s-server-1`, the sole control plane) | any host reboot is a full-cluster outage |
| IOMMU | already active, 7 groups | **no GRUB/bootloader change needed** |
| GPU IOMMU group | group 1 = root port `00:01.0` + `01:00.0/.1/.2/.3` | all four endpoints are functions of the *same card*; bridges are ignored by VFIO — a cleanly passable group, no ACS override |
| Current GPU driver | `nouveau`, refcount 0 | nothing is using it |
| Other display adapter | **none** | host loses its Linux console (accepted) |
| Host kernel | `7.0.2-6-pve`, 3 APT holds intact | the patched-NIC constraint — see below |
| VM 103 machine type | unset ⇒ `i440fx` | `pcie=1` passthrough requires `q35` |
| VM 103 ballooning | `balloon: 0` | already correct; passthrough requires no ballooning |
| VM 103 console | `serial0: socket`, `vga: serial0` | a recovery path into the guest that survives a network break |
| Guest OS | Ubuntu 26.04 "resolute", kernel `7.0.0-29-generic` | driver availability had to be checked, not assumed |
| Guest netplan | matches by **MAC**, `set-name: eth0` | the `q35` switch will **not** rename the interface |
| k3s | `v1.36.2+k3s1`, containerd has only `runc` | needs the nvidia runtime added |
| RuntimeClass `nvidia` | **already exists** (k3s ships it) | nothing to author |
| Terraform | `bpg/proxmox` 0.111.1, plan clean before changes | any later diff is purely ours |

## The hard constraint this design must not break

`pve-asrock`'s Intel I219-V NIC works only through an **unsigned, ABI-specific
`e1000e` patch** built for `7.0.2-6-pve`. A host reboot is the one genuinely
dangerous step in this whole design: if that module fails to load, the host comes
back with no network, and it carries the control plane.

Three properties make the reboot acceptable:

1. **We never touch the kernel.** VFIO is in-tree; the NVIDIA driver lives in the
   *guest*, never on the hypervisor. The three APT holds stay untouched.
2. **The stock `e1000e` was deleted** during the original recovery, so module
   resolution has only the patched copy in
   `/lib/modules/7.0.2-6-pve/updates/nic-recovery/` to find.
3. **`update-initramfs` is already part of the documented, working procedure**
   (it appears in both the recovery and rollback runbooks), not a novel risk.

The design still gates the reboot behind explicit assertions rather than trusting
those properties — see *Safety gates* below.

## Where each layer's config lives

The work spans four layers. Each one lands in the place this repo already uses
for that kind of concern, so nothing here is a new pattern:

| Layer | Change | Owned by |
|---|---|---|
| Hypervisor | blacklist `nouveau`, bind `vfio-pci`, rebuild initramfs | Ansible `proxmox_host` role, new `gpu_passthrough.yml` task file |
| Which host has a GPU | the PCI IDs to bind | `ansible/host_vars/pve-asrock.yml` (host-topology data) |
| VM | `machine = q35` + `hostpci` attachment | Terraform `workers` map |
| Guest OS | NVIDIA driver + container toolkit, k3s restart | Ansible, new `nvidia_gpu_node` role |
| Kubernetes | device plugin, DCGM metrics, NFD | NVIDIA GPU Operator via Argo CD |

This follows ADR-0053's stated split precisely: **host-topology data drives a
generic role behind an explicit tag; machine-specific roles keep only real
hardware safeguards.** `proxmox_hw_asrock` therefore gains nothing — it stays a
pure assertion role, and we *reuse* it as the pre-reboot gate.

## Decisions, with the alternatives weighed

### Boot-time VFIO binding, not runtime rebinding

| Option | Verdict |
|---|---|
| **Blacklist `nouveau` + bind `vfio-pci` at boot** | **chosen** |
| Let Proxmox unbind `nouveau` when the VM starts | rejected |

Proxmox can rebind a device at VM start, which would preserve the host console.
But this GPU *is* the console device, so `nouveau` holds an active framebuffer —
exactly the case where runtime unbinding is unreliable. Deterministic boot-time
binding is worth the console, which the user explicitly accepted.

### `q35`, accepted as a real but recoverable change

`pcie=1` requires `q35`; VM 103 is currently `i440fx`. Changing the machine type
of a running production VM is the second-riskiest step here. It is taken because:

- The guest's netplan matches on **MAC address**, so the NIC keeps its name.
- `virtio-scsi-pci` works identically on both machine types, so the root disk is
  unaffected.
- Reverting is a single `qm set 103 -machine pc`.
- A serial console (`serial0`) reaches the guest even if networking breaks.

Passing the card as legacy PCI on `i440fx` would avoid the change, but PCIe is
the correct and recommended topology for a modern GPU, and doing it during
already-planned downtime is cheaper than revisiting it later.

### Driver and toolkit on the host; GPU Operator only for the Kubernetes layer

The GPU Operator is used (as chosen), but with `driver.enabled=false` and
`toolkit.enabled=false`:

| Option | Verdict |
|---|---|
| **Host-installed driver + toolkit; operator does device plugin / DCGM / NFD** | **chosen** |
| Operator driver containers (`driver.enabled=true`) | rejected |

Ubuntu 26.04 is very new and NVIDIA's driver *container* images lag new distro
releases; a generic driver container would compile modules against
`7.0.0-29-generic` inside a privileged pod, unattended, with no console. Ubuntu's
own packaged driver is built for exactly this kernel, pins cleanly per ADR-0002,
and — decisively — can be **verified working (`nvidia-smi`) before** any
Kubernetes component depends on it. The operator still delivers everything it was
chosen for: device plugin, DCGM/Prometheus metrics, node feature discovery, and
the validator.

The toolkit is likewise host-installed because **k3s auto-detects
`nvidia-container-runtime` at startup** and writes the containerd runtime itself.
Letting the operator's toolkit rewrite k3s's containerd config instead would mean
overriding `CONTAINERD_CONFIG`/`CONTAINERD_SOCKET` to k3s's non-standard paths —
more moving parts, for a job k3s already does natively.

### Driver branch: `nvidia-driver-580-server`

All of 580-server / 595 / 610 list `10de:21c4` as supported (verified against the
packages' `Modaliases`, not assumed — NVIDIA dropped Maxwell/Pascal/Volta in the
580 branch, so this needed checking). 580 is chosen as the most settled branch
that supports the card, and the `-server` variant avoids pulling X/Wayland onto a
headless node. The recommendation from `ubuntu-drivers devices` is checked once
the card is actually attached, and this pin revisited if it disagrees.

### The default container runtime stays `runc`

The operator's GPU-touching pods get the nvidia runtime via
`operator.runtimeClass=nvidia` (the k3s-provided RuntimeClass), rather than
making `nvidia` the node-wide default. `k3s-worker-3` also runs Argo CD,
cert-manager, cloudflared, Longhorn CSI and Immich ML — changing the runtime
under all of them for the benefit of a few pods is a wider blast radius than the
problem needs. If the operator's components turn out not to honour the runtime
class, the fallback is `default-runtime: nvidia` on that agent only, and that
fallback is recorded here rather than pre-emptively taken.

### GitOps: Helm first, Argo adopts on merge

The Argo CD root app syncs `k8s/argocd/apps/` from `main` with `prune` and
`selfHeal` on, so a child Application on a branch is inert until merge — but the
cluster has to be live *now*. This is exactly the situation ADR-0042/0044 already
solved for Traefik, Longhorn and cert-manager: **install with Helm at the pinned
version, commit the matching Application + values, and let the first sync adopt
the existing release as a tracking-annotation-only diff.** No new pattern.

## Safety gates

The host reboot proceeds only if all of these pass first:

1. `proxmox_hw_asrock` role assertions — running kernel is `7.0.2-6-pve`,
   `modinfo -F filename e1000e` resolves to the `nic-recovery` path, and that
   file's SHA-256 matches the physically tested artifact.
2. `apt-get -s dist-upgrade` reports **no kernel install or removal** (the
   runbook's own documented gate).
3. After writing the VFIO config and rebuilding the initramfs but **before
   rebooting**: the regenerated initramfs still contains `e1000e`, and module
   resolution still points at the patched copy.

Post-reboot, before going further: the NIC bypass message is in the kernel log,
`nic0` is up at 1000 Mb/s, both VMs are running, and `01:00.0` reports
`Kernel driver in use: vfio-pci`.

## Execution order and downtime

| Phase | Action | Impact |
|---|---|---|
| A | Host VFIO config + reboot `pve-asrock` | **full cluster outage** — control plane and worker-3 both live here |
| B | Terraform: `q35` + `hostpci` on VM 103 (stop → apply → start) | worker-3 workloads down; Immich library replica offline |
| C | Ansible: driver + toolkit in the guest, restart `k3s-agent` | worker-3 pods restart |
| D | Helm: GPU Operator | additive |

Phases B and C do not require another host reboot.

## What "done" looks like

1. `01:00.0` bound to `vfio-pci` on the host; `nic0` still up at 1 Gb/s.
2. `lspci` inside `k3s-worker-3` shows the TU116.
3. `nvidia-smi` in the guest reports the GTX 1660 SUPER.
4. `grep nvidia /var/lib/rancher/k3s/agent/etc/containerd/config.toml` matches.
5. `kubectl describe node k3s-worker-3` shows `nvidia.com/gpu: 1` allocatable.
6. A committed validation Job with `runtimeClassName: nvidia` and
   `nvidia.com/gpu: 1` runs `nvidia-smi` to completion inside the cluster.

## Deliberately out of scope

Immich hardware transcoding (the GPU's eventual first consumer, but a separate
change to a running app); ollama or any other GPU workload; MIG or time-slicing
(one card, one consumer at a time is fine for now); GPU passthrough on `pve-dell`
(its Meteor Lake iGPU is a different mechanism — VAAPI, not NVENC — and a
separate decision); moving `immich-server` onto `k3s-worker-3`.
