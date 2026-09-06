# NVIDIA GPU Passthrough Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the GTX 1660 SUPER in `pve-asrock` to Kubernetes as a schedulable `nvidia.com/gpu` resource on `k3s-worker-3`.

**Architecture:** Four layers, each landing in the place this repo already uses for that concern — hypervisor VFIO binding via the generic `proxmox_host` Ansible role driven by host-topology data (ADR-0053), VM device attachment via the Terraform `workers` map, guest driver/toolkit via a new tag-gated Ansible role, and the Kubernetes layer via the NVIDIA GPU Operator with `driver.enabled=false`/`toolkit.enabled=false`. The operator is Helm-installed at a pinned version now and adopted by Argo CD on merge (ADR-0042/0044).

**Tech Stack:** Proxmox VE 9.2.3 (kernel `7.0.2-6-pve`), `bpg/proxmox` Terraform provider 0.111.1, Ansible, Ubuntu 26.04 guest (kernel `7.0.0-29-generic`), k3s `v1.36.2+k3s1`, NVIDIA GPU Operator chart `v26.7.0`.

**Spec:** `docs/superpowers/specs/2026-09-06-nvidia-gpu-passthrough-design.md`

## Global Constraints

- **Never touch `pve-asrock`'s kernel.** The I219-V NIC depends on an unsigned, ABI-specific `e1000e` patch built for `7.0.2-6-pve`. The three APT holds (`proxmox-default-kernel`, `proxmox-kernel-7.0`, `proxmox-kernel-7.0.2-6-pve-signed`) stay in place.
- **Never edit `/etc/modprobe.d/e1000e-nvm-workaround.conf`.** New modprobe config goes in new, separate files.
- **Pin every version explicitly** (ADR-0002). No `latest`, no floating tags.
- GPU is at PCI `0000:01:00`, IOMMU group 1, functions `.0`/`.1`/`.2`/`.3` — pass the whole device, not one function.
- Host-topology data lives in `host_vars`; the generic role consumes it behind an explicit tag. Machine-specific roles keep only hardware safeguards (ADR-0053).
- Argo CD Applications use `syncOptions: [CreateNamespace=false]`; namespaces are separate committed manifests.
- Commit style: conventional commits (`feat(scope):`, `docs(scope):`).

---

### Task 1: Host VFIO configuration (code only, no execution)

**Files:**
- Modify: `ansible/host_vars/pve-asrock.yml`
- Create: `ansible/roles/proxmox_host/tasks/gpu_passthrough.yml`
- Modify: `ansible/roles/proxmox_host/tasks/main.yml`

**Interfaces:**
- Produces: `proxmox_vfio_passthrough` host var (keys: `pci_ids`, `blacklist_modules`, `console_loss_accepted`); the `gpu-passthrough` and `gpu-passthrough-reboot` Ansible tags.

- [ ] **Step 1: Confirm the real PCI IDs of all four functions** (do not assume TU116 defaults)

```bash
ssh -i ~/.ssh/proxmox_ed25519 root@pve-asrock.egret-pence.ts.net \
  "lspci -nn -s 01:00"
```
Expected: four lines, each ending `[10de:XXXX]`. Record all four.

- [ ] **Step 2: Add the topology data to `ansible/host_vars/pve-asrock.yml`**

Append (using the IDs from Step 1):

```yaml
# Host-topology data, not an ASRock hardware workaround — same split as
# proxmox_lvmthin_storage above. The generic proxmox_host role consumes this
# only with the explicit gpu-passthrough tag.
#
# The GeForce GTX 1660 SUPER (TU116) sits alone in IOMMU group 1 apart from its
# PCIe root port, so all four of its functions pass through together cleanly and
# no ACS override is needed. All four are listed because Proxmox passes the whole
# device (0000:01:00, no function suffix) and vfio-pci must own every function.
#
# CONSEQUENCE: this GPU is pve-asrock's ONLY display adapter. Once vfio-pci binds
# it at boot the host has no Linux console — BIOS and GRUB remain visible on a
# physical monitor, so rescue-boot recovery survives, but there is no login
# prompt. Accepted deliberately; see ADR-0067.
proxmox_vfio_passthrough:
  pci_ids:
    - "10de:21c4" # VGA
    - "10de:1aeb" # HDMI audio
    - "10de:1aec" # USB-C controller
    - "10de:1aed" # UCSI controller
  blacklist_modules:
    - nouveau
```

- [ ] **Step 3: Create `ansible/roles/proxmox_host/tasks/gpu_passthrough.yml`**

```yaml
---
# Generic host-side VFIO binding. Claims the PCI functions declared in host_vars
# for vfio-pci at boot so a guest can take the device exclusively.
#
# Boot-time binding (blacklist + vfio-pci) rather than letting Proxmox rebind at
# VM start: on this host the GPU is also the console framebuffer device, which is
# exactly the case where runtime unbinding is unreliable.
#
# This role NEVER touches the kernel or /etc/modprobe.d/e1000e-nvm-workaround.conf.
# The initramfs rebuild it performs is the same operation the I219-V recovery
# runbook already prescribes — but it is asserted afterwards, before any reboot,
# because on this host a wrong initramfs means a host with no network.

- name: Require the declared VFIO host identity
  ansible.builtin.assert:
    that:
      - proxmox_vfio_passthrough.pci_ids | length > 0
    fail_msg: proxmox_vfio_passthrough.pci_ids must declare at least one PCI ID.

- name: Blacklist the host drivers that would claim the passthrough device
  ansible.builtin.copy:
    dest: /etc/modprobe.d/vfio-passthrough-blacklist.conf
    owner: root
    group: root
    mode: "0644"
    content: |
      # Managed by ansible/roles/proxmox_host (gpu-passthrough tag) — do not edit by hand.
      {% for m in proxmox_vfio_passthrough.blacklist_modules %}
      blacklist {{ m }}
      options {{ m }} modeset=0
      {% endfor %}
  notify: Rebuild the Proxmox initramfs

- name: Bind the declared PCI functions to vfio-pci
  ansible.builtin.copy:
    dest: /etc/modprobe.d/vfio-passthrough.conf
    owner: root
    group: root
    mode: "0644"
    content: |
      # Managed by ansible/roles/proxmox_host (gpu-passthrough tag) — do not edit by hand.
      options vfio-pci ids={{ proxmox_vfio_passthrough.pci_ids | join(',') }} disable_vga=1
      {% for m in proxmox_vfio_passthrough.blacklist_modules %}
      softdep {{ m }} pre: vfio-pci
      {% endfor %}
  notify: Rebuild the Proxmox initramfs

- name: Load the vfio stack at boot
  ansible.builtin.copy:
    dest: /etc/modules-load.d/vfio.conf
    owner: root
    group: root
    mode: "0644"
    content: |
      # Managed by ansible/roles/proxmox_host (gpu-passthrough tag) — do not edit by hand.
      vfio
      vfio_iommu_type1
      vfio_pci
  notify: Rebuild the Proxmox initramfs

- name: Apply any pending initramfs rebuild before it is verified
  ansible.builtin.meta: flush_handlers

# --- Pre-reboot safety gate: the NIC must survive this host's next boot -------

- name: Read the e1000e module the running kernel resolves
  ansible.builtin.command: modinfo -F filename e1000e
  changed_when: false
  check_mode: false
  register: proxmox_vfio_e1000e_module

- name: Read the initramfs contents for the running kernel
  ansible.builtin.shell:
    cmd: set -o pipefail && lsinitramfs "/boot/initrd.img-{{ ansible_kernel }}" | grep -c 'e1000e\.ko'
    executable: /bin/bash
  changed_when: false
  check_mode: false
  failed_when: false
  register: proxmox_vfio_initramfs_e1000e

- name: Require the patched NIC driver to survive the rebuilt initramfs
  ansible.builtin.assert:
    that:
      - proxmox_vfio_e1000e_module.stdout == asrock_recovery_module_path
      - (proxmox_vfio_initramfs_e1000e.stdout | int) > 0
    fail_msg: >-
      Refusing to leave this host reboot-ready: e1000e resolves to
      {{ proxmox_vfio_e1000e_module.stdout }} (expected {{ asrock_recovery_module_path }})
      and the rebuilt initramfs contains {{ proxmox_vfio_initramfs_e1000e.stdout }}
      copies of e1000e.ko. Repair module precedence before rebooting.

- name: Reboot the hypervisor to bind the passthrough device
  ansible.builtin.reboot:
    reboot_timeout: 900
    post_reboot_delay: 30
  when: proxmox_vfio_reboot | default(false) | bool

- name: Read the driver now bound to the passthrough device
  ansible.builtin.shell:
    cmd: set -o pipefail && lspci -nnk -s 01:00.0 | awk -F': ' '/Kernel driver in use/ {print $2}'
    executable: /bin/bash
  changed_when: false
  check_mode: false
  register: proxmox_vfio_bound_driver
  when: proxmox_vfio_reboot | default(false) | bool

- name: Require the passthrough device to be owned by vfio-pci
  ansible.builtin.assert:
    that:
      - proxmox_vfio_bound_driver.stdout | trim == "vfio-pci"
    fail_msg: >-
      01:00.0 is bound to '{{ proxmox_vfio_bound_driver.stdout | trim }}',
      not vfio-pci. The guest cannot take the device.
  when: proxmox_vfio_reboot | default(false) | bool
```

- [ ] **Step 4: Create the handler** at `ansible/roles/proxmox_host/handlers/main.yml`

```yaml
---
- name: Rebuild the Proxmox initramfs
  ansible.builtin.command: "update-initramfs -u -k {{ ansible_kernel }}"
```

- [ ] **Step 5: Wire it into `ansible/roles/proxmox_host/tasks/main.yml`**

Add to the header comment block:
```
#   --tags gpu-passthrough   bind a host-declared PCI device to vfio-pci
```
and append the import:
```yaml
- name: Bind a declared PCI device to vfio-pci for guest passthrough
  ansible.builtin.import_tasks: gpu_passthrough.yml
  when: proxmox_vfio_passthrough is defined
  tags: [never, gpu-passthrough]
```

- [ ] **Step 6: Lint and commit**

```bash
cd ansible && ansible-playbook proxmox.yml --syntax-check
git add ansible/host_vars/pve-asrock.yml ansible/roles/proxmox_host/
git commit -m "feat(proxmox): bind a host-declared PCI device to vfio-pci"
```

---

### Task 2: Execute host VFIO binding and reboot `pve-asrock`

**Files:** none (execution of Task 1's code)

**Interfaces:**
- Consumes: `gpu-passthrough` tag, `proxmox_vfio_reboot` extra-var.
- Produces: `01:00.0` bound to `vfio-pci`; host rebooted; both VMs running.

> **This is the only step that can take the whole cluster down.** `pve-asrock` hosts `k3s-server-1` (sole control plane) and `k3s-worker-3`.

- [ ] **Step 1: Run the existing hardware assertion role as the pre-flight gate**

```bash
cd ansible && ansible-playbook proxmox.yml --tags asrock-hardware --limit pve-asrock
```
Expected: all assertions PASS (kernel `7.0.2-6-pve`, module path, SHA-256 match).

- [ ] **Step 2: Run the runbook's own kernel-change gate**

```bash
ssh -i ~/.ssh/proxmox_ed25519 root@pve-asrock.egret-pence.ts.net \
  "apt-get -s dist-upgrade | grep -Ei '^(Inst|Remv).*(proxmox-kernel-[0-9]|proxmox-default-kernel)' || echo no-kernel-change"
```
Expected: `no-kernel-change`. **If anything else prints, STOP.**

- [ ] **Step 3: Apply the VFIO config WITHOUT rebooting, letting the assertions gate it**

```bash
cd ansible && ansible-playbook proxmox.yml --tags gpu-passthrough --limit pve-asrock
```
Expected: config files written, initramfs rebuilt, and the "Require the patched NIC driver to survive the rebuilt initramfs" assertion PASSES.

- [ ] **Step 4: Reboot, gated on the same assertions re-running first**

```bash
cd ansible && ansible-playbook proxmox.yml --tags gpu-passthrough --limit pve-asrock \
  --extra-vars "proxmox_vfio_reboot=true"
```
Expected: host reboots, comes back, and `01:00.0` asserts as `vfio-pci`.

- [ ] **Step 5: Verify the NIC survived (the thing that could brick this host)**

```bash
ssh -i ~/.ssh/proxmox_ed25519 root@pve-asrock.egret-pence.ts.net \
  "journalctl -b -k | grep -i 'NVM checksum validation bypassed'; \
   ip -br link show nic0; \
   ethtool nic0 | grep -E 'Speed:|Link detected:'; \
   lspci -nnk -s 01:00.0 | grep -E 'Kernel driver|Kernel modules'; \
   qm list"
```
Expected: bypass message present, `nic0` UP, 1000Mb/s, `Kernel driver in use: vfio-pci`, VMs 100 and 103 `running`.

- [ ] **Step 6: Verify the cluster came back**

```bash
kubectl get nodes
```
Expected: all three nodes `Ready`.

---

### Task 3: Attach the GPU to VM 103 via Terraform

**Files:**
- Modify: `terraform/proxmox/variables.tf` (workers map object type)
- Modify: `terraform/proxmox/main.tf` (worker resource)
- Modify: `terraform/proxmox/terraform.tfvars` (k3s-worker-3 entry)

**Interfaces:**
- Consumes: `vfio-pci` binding from Task 2.
- Produces: VM 103 on `q35` with `hostpci0: 0000:01:00,pcie=1`.

- [ ] **Step 1: Extend the workers object type in `variables.tf`**

Add inside `map(object({ ... }))`, after `usb_devices`:

```hcl
    # Proxmox machine type. q35 is required for PCIe passthrough (pcie=1);
    # workers with no passthrough device stay on the i440fx default.
    machine = optional(string)

    # Physical PCI(e) devices mapped into the guest. Empty for every worker that
    # doesn't need one, so adding this stays a no-op for existing VMs.
    hostpci_devices = optional(list(object({
      device = string
      id     = string
      pcie   = optional(bool, true)
      rombar = optional(bool, true)
    })), [])
```

- [ ] **Step 2: Consume it in `main.tf`**

Add to `resource "proxmox_virtual_environment_vm" "k3s_worker"`, after the `memory` block:

```hcl
  # q35 only where a PCIe device is passed through; null leaves Proxmox's default.
  machine = each.value.machine
```

and after the `dynamic "usb"` block:

```hcl
  # Physical PCI(e) passthrough. `id` is the device address WITHOUT a function
  # suffix (0000:01:00, not 0000:01:00.0), which tells Proxmox to pass every
  # function of the card — required here because the GPU presents four (VGA,
  # audio, USB-C, UCSI) and vfio-pci owns them all as one IOMMU group.
  dynamic "hostpci" {
    for_each = each.value.hostpci_devices
    content {
      device = hostpci.value.device
      id     = hostpci.value.id
      pcie   = hostpci.value.pcie
      rombar = hostpci.value.rombar
    }
  }
```

- [ ] **Step 3: Declare the device on k3s-worker-3 in `terraform.tfvars`**

Add to the `k3s-worker-3` entry, after `usb_devices`:

```hcl
    # GeForce GTX 1660 SUPER passed through from pve-asrock (ADR-0067). q35 is
    # what makes pcie=1 legal; the guest's netplan matches on MAC address, so the
    # machine-type change does not rename its interface.
    machine = "q35"
    hostpci_devices = [
      { device = "hostpci0", id = "0000:01:00" },
    ]
```

- [ ] **Step 4: Plan, and HARD-GATE on "update in-place"**

```bash
cd terraform/proxmox
export PROXMOX_VE_API_TOKEN="$(grep '^PROXMOX_VE_API_TOKEN=' ../../.env | cut -d= -f2-)"
terraform plan -no-color | tee /tmp/gpu-plan.txt
grep -E "must be replaced|forces replacement|will be destroyed" /tmp/gpu-plan.txt && echo "!!! ABORT !!!" || echo "safe: in-place only"
```
Expected: `1 to change, 0 to add, 0 to destroy` and `safe: in-place only`.
**If it says the VM must be replaced, STOP — that would destroy VM 103 and its disks.**

- [ ] **Step 5: Stop VM 103, apply, start**

PCI passthrough cannot be hot-plugged, so the VM must be cold.

```bash
ssh -i ~/.ssh/proxmox_ed25519 root@pve-asrock.egret-pence.ts.net "qm shutdown 103 --timeout 120; sleep 5; qm status 103"
cd terraform/proxmox && terraform apply -auto-approve
ssh -i ~/.ssh/proxmox_ed25519 root@pve-asrock.egret-pence.ts.net "qm start 103; sleep 45; qm config 103 | grep -E 'machine|hostpci'"
```
Expected: `machine: q35` and `hostpci0: 0000:01:00,pcie=1`.

- [ ] **Step 6: Verify the guest booted and sees the card**

```bash
ssh -i ~/.ssh/id_ed25519 harsh@k3s-worker-3.egret-pence.ts.net "ip -br addr show eth0; lspci -nn | grep -i nvidia"
kubectl get node k3s-worker-3
```
Expected: `eth0` still holds `192.168.1.24`, `lspci` lists the TU116, node returns to `Ready`.

- [ ] **Step 7: Commit**

```bash
git add terraform/proxmox/
git commit -m "feat(terraform): pass the ASRock GPU through to k3s-worker-3"
```

---

### Task 4: Guest NVIDIA driver and container toolkit

**Files:**
- Create: `ansible/roles/nvidia_gpu_node/tasks/main.yml`
- Create: `ansible/roles/nvidia_gpu_node/handlers/main.yml`
- Modify: `ansible/group_vars/k3s_agent.yml` (pinned versions)
- Modify: `ansible/site.yml` (wire the role, tag-gated)

**Interfaces:**
- Consumes: the passed-through GPU from Task 3.
- Produces: working `nvidia-smi` in the guest; `nvidia` runtime in k3s's containerd config.

- [ ] **Step 1: Check what NVIDIA itself recommends now the card is attached**

```bash
ssh -i ~/.ssh/id_ed25519 harsh@k3s-worker-3.egret-pence.ts.net \
  "sudo apt-get install -y -qq ubuntu-drivers-common >/dev/null 2>&1; sudo ubuntu-drivers devices 2>&1 | grep -E 'vendor|driver'"
```
Expected: recommends an `nvidia-driver-*` branch. If it disagrees with `580-server`, use its recommendation and note it in the ADR.

- [ ] **Step 2: Pin the versions in `ansible/group_vars/k3s_agent.yml`**

```yaml
# NVIDIA GPU node (ADR-0067). Pinned deliberately, like k3s_version — the
# `-server` variant keeps X/Wayland off a headless node, and 580 is the most
# settled branch in Ubuntu 26.04 that still lists this Turing card (10de:21c4)
# in its Modaliases; NVIDIA dropped Maxwell/Pascal/Volta in this branch, so that
# was verified rather than assumed.
nvidia_driver_package: "nvidia-driver-580-server"
nvidia_container_toolkit_repo: "https://nvidia.github.io/libnvidia-container/stable/deb/$(ARCH) /"
nvidia_container_toolkit_key: "https://nvidia.github.io/libnvidia-container/gpgkey"
```

- [ ] **Step 3: Create `ansible/roles/nvidia_gpu_node/tasks/main.yml`**

```yaml
---
# NVIDIA driver + container runtime for a worker that has a GPU passed through.
#
# Scoped to k3s_agent but only meaningful on a worker that actually has the card;
# on any other worker the first assertion no-ops the role out. Gated behind the
# `nvidia_gpu` tag so it never runs on ordinary plays — same shape as
# photos_relay_udev.
#
# The driver is installed from Ubuntu's own archive rather than an NVIDIA driver
# container: the package is built for this exact kernel, pins per ADR-0002, and
# can be proven working with nvidia-smi BEFORE anything in Kubernetes depends on
# it. The container toolkit is installed here rather than by the GPU Operator
# because k3s auto-detects nvidia-container-runtime at startup and writes the
# containerd runtime itself.

- name: Look for a passed-through NVIDIA device
  ansible.builtin.shell:
    cmd: set -o pipefail && lspci -nn | grep -ci '\[10de:' || true
    executable: /bin/bash
  changed_when: false
  check_mode: false
  register: nvidia_gpu_present

- name: Skip every remaining task on workers with no NVIDIA device
  ansible.builtin.meta: end_host
  when: (nvidia_gpu_present.stdout | int) == 0

- name: Install the pinned NVIDIA driver
  ansible.builtin.apt:
    name: "{{ nvidia_driver_package }}"
    state: present
    update_cache: true
  notify: Reboot the GPU node

- name: Add the NVIDIA container toolkit signing key
  ansible.builtin.get_url:
    url: "{{ nvidia_container_toolkit_key }}"
    dest: /etc/apt/keyrings/nvidia-container-toolkit.asc
    mode: "0644"

- name: Add the NVIDIA container toolkit repository
  ansible.builtin.apt_repository:
    repo: "deb [signed-by=/etc/apt/keyrings/nvidia-container-toolkit.asc] {{ nvidia_container_toolkit_repo }}"
    filename: nvidia-container-toolkit
    state: present

- name: Install the NVIDIA container toolkit
  ansible.builtin.apt:
    name: nvidia-container-toolkit
    state: present
    update_cache: true
  notify: Restart k3s-agent

- name: Apply the driver reboot and runtime restart before verifying
  ansible.builtin.meta: flush_handlers

- name: Read the GPU the driver now sees
  ansible.builtin.command: nvidia-smi --query-gpu=name --format=csv,noheader
  changed_when: false
  check_mode: false
  register: nvidia_smi_gpu

- name: Read the container runtimes k3s wrote into containerd
  ansible.builtin.shell:
    cmd: set -o pipefail && grep -c 'nvidia' /var/lib/rancher/k3s/agent/etc/containerd/config.toml || true
    executable: /bin/bash
  changed_when: false
  check_mode: false
  register: nvidia_containerd_runtime

- name: Require a working driver and a registered nvidia container runtime
  ansible.builtin.assert:
    that:
      - nvidia_smi_gpu.stdout | length > 0
      - (nvidia_containerd_runtime.stdout | int) > 0
    fail_msg: >-
      nvidia-smi reported '{{ nvidia_smi_gpu.stdout }}' and containerd has
      {{ nvidia_containerd_runtime.stdout }} nvidia runtime references. k3s only
      writes the nvidia runtime if nvidia-container-runtime is on PATH when it
      starts — check the toolkit install, then restart k3s-agent.
```

- [ ] **Step 4: Create `ansible/roles/nvidia_gpu_node/handlers/main.yml`**

```yaml
---
# The driver's kernel module cannot load into a running kernel that already has
# nouveau's state, and k3s only rescans for alternative runtimes at startup.
- name: Reboot the GPU node
  ansible.builtin.reboot:
    reboot_timeout: 900
    post_reboot_delay: 30

- name: Restart k3s-agent
  ansible.builtin.systemd:
    name: k3s-agent
    state: restarted
```

- [ ] **Step 5: Wire into `ansible/site.yml`** — append to the `k3s_agent` play's roles:

```yaml
    # NVIDIA driver + container runtime for the GPU worker (ADR-0067).
    # Tag-gated, and no-ops on workers with no NVIDIA device.
    - role: nvidia_gpu_node
      tags: [never, nvidia_gpu]
```

- [ ] **Step 6: Execute**

```bash
cd ansible && ansible-playbook site.yml --tags nvidia_gpu --limit k3s-worker-3 \
  --extra-vars "k3s_token=${K3S_TOKEN}"
```
Expected: driver installs, node reboots, assertions pass.

- [ ] **Step 7: Verify independently**

```bash
ssh -i ~/.ssh/id_ed25519 harsh@k3s-worker-3.egret-pence.ts.net \
  "nvidia-smi; grep -A3 nvidia /var/lib/rancher/k3s/agent/etc/containerd/config.toml | head -20"
kubectl get node k3s-worker-3
```
Expected: `nvidia-smi` shows "NVIDIA GeForce GTX 1660 SUPER"; containerd config has an `nvidia` runtime; node `Ready`.

- [ ] **Step 8: Commit**

```bash
git add ansible/roles/nvidia_gpu_node/ ansible/group_vars/k3s_agent.yml ansible/site.yml
git commit -m "feat(ansible): NVIDIA driver and container runtime for the GPU worker"
```

---

### Task 5: NVIDIA GPU Operator

**Files:**
- Create: `k8s/gpu-operator/values.yaml`
- Create: `k8s/gpu-operator/manifests/namespace.yaml`
- Create: `k8s/argocd/apps/gpu-operator.yaml`
- Create: `k8s/gpu-operator/validation/gpu-smoke-test.yaml`

**Interfaces:**
- Consumes: host driver + `nvidia` containerd runtime from Task 4; the k3s-provided `nvidia` RuntimeClass.
- Produces: `nvidia.com/gpu: 1` allocatable on `k3s-worker-3`.

- [ ] **Step 1: Create `k8s/gpu-operator/manifests/namespace.yaml`**

```yaml
# Argo CD Applications in this repo all use CreateNamespace=false, so every
# namespace is an explicit committed object.
apiVersion: v1
kind: Namespace
metadata:
  name: gpu-operator
```

- [ ] **Step 2: Create `k8s/gpu-operator/values.yaml`**

```yaml
# NVIDIA GPU Operator values (ADR-0067).
#
# The operator is deliberately reduced to its Kubernetes-facing job. The driver
# and the container toolkit are installed on the node by the nvidia_gpu_node
# Ansible role instead, because:
#   - Ubuntu 26.04 is new enough that NVIDIA's driver *container* images lag it,
#     and a generic driver container would compile kernel modules unattended in a
#     privileged pod on a host with no console;
#   - the packaged driver is built for this exact kernel and pins per ADR-0002;
#   - it can be proven working (nvidia-smi) BEFORE anything in Kubernetes
#     depends on it;
#   - k3s auto-detects nvidia-container-runtime at startup and writes the
#     containerd runtime itself, so the operator's toolkit would only duplicate
#     that against k3s's non-standard containerd paths.
driver:
  enabled: false
toolkit:
  enabled: false

operator:
  # k3s ships a predefined `nvidia` RuntimeClass, so the operator's GPU-touching
  # pods opt into the nvidia runtime per-pod. The node's DEFAULT runtime stays
  # runc on purpose: k3s-worker-3 also runs Argo CD, cert-manager, cloudflared,
  # Longhorn CSI and Immich ML, and switching the runtime under all of them is a
  # wider blast radius than this needs.
  defaultRuntime: containerd
  runtimeClass: nvidia

# Node Feature Discovery labels nodes with their PCI vendors, which is how the
# device plugin lands only on the node that actually has the card.
nfd:
  enabled: true

# DCGM metrics — one of the reasons the operator was chosen over a bare device
# plugin. kube-prometheus-stack discovers ServiceMonitors cluster-wide (ADR-0039),
# so this is picked up without further wiring.
dcgmExporter:
  enabled: true
  serviceMonitor:
    enabled: true
```

- [ ] **Step 3: Create `k8s/argocd/apps/gpu-operator.yaml`**

```yaml
# Child Application: NVIDIA GPU Operator — exposes the GTX 1660 SUPER passed
# through to k3s-worker-3 as a schedulable nvidia.com/gpu resource (ADR-0067).
#
# Installed with Helm first at this exact chart version and adopted here, the
# same bootstrap-then-adopt path Traefik/Longhorn/cert-manager took (ADR-0042,
# ADR-0044) — the first sync should be a tracking-annotation-only diff.
#
# The driver and container toolkit are NOT managed here; they are node-level
# concerns owned by the nvidia_gpu_node Ansible role. See k8s/gpu-operator/values.yaml.
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: gpu-operator
  namespace: argocd
spec:
  project: default
  sources:
    - repoURL: https://helm.ngc.nvidia.com/nvidia
      chart: gpu-operator
      targetRevision: v26.7.0 # matches the version installed live; check NVIDIA's releases before bumping
      helm:
        valueFiles:
          - $values/k8s/gpu-operator/values.yaml
    - repoURL: https://github.com/Harsh-Upadhayay/homelab-k8s.git
      targetRevision: main
      ref: values
    - repoURL: https://github.com/Harsh-Upadhayay/homelab-k8s.git
      targetRevision: main
      path: k8s/gpu-operator/manifests
  destination:
    server: https://kubernetes.default.svc
    namespace: gpu-operator
  syncPolicy:
    syncOptions:
      - CreateNamespace=false
```

- [ ] **Step 4: Apply the namespace and Helm-install the pinned chart**

```bash
kubectl apply -f k8s/gpu-operator/manifests/namespace.yaml
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia && helm repo update nvidia
helm upgrade --install gpu-operator nvidia/gpu-operator \
  --version v26.7.0 -n gpu-operator -f k8s/gpu-operator/values.yaml --wait --timeout 15m
```
Expected: release deployed.

- [ ] **Step 5: Verify the GPU is schedulable**

```bash
kubectl -n gpu-operator get pods
kubectl describe node k3s-worker-3 | grep -A6 Allocatable
```
Expected: operator pods `Running`/`Completed`, and `nvidia.com/gpu: 1` under Allocatable.

- [ ] **Step 6: Create the smoke test at `k8s/gpu-operator/validation/gpu-smoke-test.yaml`**

Deliberately OUTSIDE the Argo Application's `manifests/` path — committed and reviewable, run on demand, never GitOps-managed (a completed Job would otherwise churn sync status).

```yaml
# On-demand proof that the passthrough chain works end to end:
#   pve-asrock vfio-pci -> VM 103 hostpci -> host driver -> nvidia runtime
#   -> device plugin -> a pod that asked for a GPU.
#
# NOT synced by Argo CD (this directory is outside the Application's path).
# Run with:  kubectl apply -f k8s/gpu-operator/validation/gpu-smoke-test.yaml
#            kubectl -n gpu-operator logs -f job/gpu-smoke-test
#            kubectl -n gpu-operator delete job gpu-smoke-test
apiVersion: batch/v1
kind: Job
metadata:
  name: gpu-smoke-test
  namespace: gpu-operator
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 3600
  template:
    spec:
      restartPolicy: Never
      runtimeClassName: nvidia
      containers:
        - name: nvidia-smi
          image: nvidia/cuda:12.6.2-base-ubuntu24.04
          command: ["nvidia-smi"]
          resources:
            limits:
              nvidia.com/gpu: 1
```

- [ ] **Step 7: Run the smoke test**

```bash
kubectl apply -f k8s/gpu-operator/validation/gpu-smoke-test.yaml
kubectl -n gpu-operator wait --for=condition=complete job/gpu-smoke-test --timeout=300s
kubectl -n gpu-operator logs job/gpu-smoke-test
kubectl -n gpu-operator delete job gpu-smoke-test
```
Expected: the `nvidia-smi` table naming "NVIDIA GeForce GTX 1660 SUPER".

- [ ] **Step 8: Commit**

```bash
git add k8s/gpu-operator/ k8s/argocd/apps/gpu-operator.yaml
git commit -m "feat(gpu-operator): expose the passed-through GPU as nvidia.com/gpu"
```

---

### Task 6: ADR-0067 and the PR

**Files:**
- Modify: `docs/adr/v2.0 - Operability.md` (append ADR-0067)
- Modify: `docs/adr/README.md` (Logs line + Index row)
- Modify: `docs/Migration Plan.md` (retire the "GPU workloads deferred" note)
- Modify: `ROADMAP.md`

**Interfaces:**
- Consumes: everything above, including whatever actually happened during execution.

- [ ] **Step 1: Append ADR-0067** to `docs/adr/v2.0 - Operability.md` in the house format (`## ADR-0067 — …` / `**Status:**` / `**Context:**` / `**Decision:**` / `**Consequences:**`), covering: GPU placement on worker-3 and why not the control-plane host; boot-time vfio-pci binding and the accepted loss of the host console; q35; host driver + toolkit with the operator reduced to the Kubernetes layer; the pinned driver branch; default runtime staying runc.

- [ ] **Step 2: Update `docs/adr/README.md`** — add `0067` to the `v2.0 - Operability` Logs line and a row to the Index table with status `Accepted`.

- [ ] **Step 3: Update the stale deferral notes** in `docs/Migration Plan.md` and `ROADMAP.md` to record that GPU passthrough has landed.

- [ ] **Step 4: Commit and open the PR**

```bash
git add docs/ ROADMAP.md
git commit -m "docs(adr): ADR-0067 — GPU passthrough into k3s-worker-3"
git push -u origin feat/nvidia-gpu-passthrough
gh pr create --title "feat: NVIDIA GPU passthrough into the k3s cluster" --body "..."
```

---

## Self-Review

**Spec coverage:** Boot-time VFIO → Task 1/2. q35 + hostpci → Task 3. Host driver/toolkit → Task 4. GPU Operator with driver/toolkit disabled → Task 5. Safety gates (assertion role, `dist-upgrade` check, pre-reboot initramfs assertion) → Task 2 Steps 1–3 plus the assertions inside Task 1's task file. Execution order/downtime → task order. "What done looks like" items 1–6 → Task 2 Step 5, Task 3 Step 6, Task 4 Step 7, Task 5 Steps 5 and 7. ADR → Task 6. No gaps.

**Placeholder scan:** The only deliberately unwritten prose is ADR-0067's body (Task 6 Step 1), which must be written against what actually happened during execution rather than predicted — its required sections and content are enumerated. The PR body is likewise written from the real outcome.

**Type consistency:** `proxmox_vfio_passthrough` (`pci_ids`, `blacklist_modules`) is defined in Task 1 Step 2 and consumed in Step 3. `proxmox_vfio_reboot` is defined in Task 1 Step 3 and used in Task 2 Step 4. `asrock_recovery_module_path` is pre-existing in `group_vars/proxmox_hw_asrock.yml`. `machine`/`hostpci_devices` are declared in Task 3 Step 1 and consumed in Steps 2–3. `nvidia_driver_package` and the toolkit repo vars are defined in Task 4 Step 2 and used in Step 3. Handler names match their `notify:` callers in both roles.
